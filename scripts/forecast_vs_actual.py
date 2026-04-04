"""scripts/forecast_vs_actual.py
---------------------------------
Utility script: compare solar forecast predictions against actual logged generation.

Run from the project root:
    python scripts/forecast_vs_actual.py

For each date/period that has telemetry data, prints:
  - ESB forecast kW (primary provider, county-level synthetic)
  - Quartz forecast kW (secondary provider, site-level actual calculation)
  - Calibrated/buffered forecast kW (ESB × power_multiplier from calibration file)
  - Actual average and peak kW measured by the inverter
  - Clipping events flagged in that window
  - Sample count (number of 15-min poll intervals)
  - Plain-language verdict explaining how accurate the forecasts were

Data file locations are read from config.py (TELEMETRY_LOG_PATH, CALIBRATION_LOG_PATH,
FORECAST_COMPARISONS_LOG_PATH).
"""

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Allow running from project root or from scripts/ sub-directory
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from config import (
    CALIBRATION_LOG_PATH,
    FORECAST_COMPARISONS_LOG_PATH,
    INVERTER_KW,
    TELEMETRY_LOG_PATH,
)

# Period hour boundaries — must stay in sync with telemetry_archive.py
_PERIOD_WINDOWS: dict[str, tuple[int, int]] = {
    "Morn": (7, 12),
    "Aftn": (12, 16),
    "Eve": (16, 20),
}


def period_for_hour(hour: int) -> str | None:
    """Return the forecast period name for a given local hour, or None if outside all windows.

    Args:
        hour: Hour of day in local time (0–23).

    Returns:
        One of 'Morn', 'Aftn', 'Eve', or None.
    """
    for name, (start, end) in _PERIOD_WINDOWS.items():
        if start <= hour < end:
            return name
    return None


def load_calibration(path: Path) -> dict[str, dict[str, float]]:
    """Load the calibration JSON and return per-period multiplier data.

    Args:
        path: Absolute path to forecast_calibration.json.

    Returns:
        Dict keyed by period name, each containing calibration fields including
        'power_multiplier'. Returns an empty dict if the file does not exist.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("periods", {})


def load_telemetry_rows(path: Path) -> list[dict[str, Any]]:
    """Read all records from the telemetry JSONL archive.

    Args:
        path: Absolute path to inverter_telemetry.jsonl.

    Returns:
        List of parsed record dicts. Returns an empty list if the file does not exist.
    """
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_forecast_comparisons(path: Path) -> list[dict[str, Any]]:
    """Read all records from the forecast comparison JSONL archive.

    Args:
        path: Absolute path to forecast_comparisons.jsonl.

    Returns:
        List of parsed comparison records. Returns an empty list if the file does not exist.
    """
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def classify_accuracy(ratio: float) -> str:
    """Return a short accuracy label for a raw forecast/actual ratio.

    Args:
        ratio: avg_actual_kw / forecast_kw.

    Returns:
        Human-readable label for how far off the forecast was.
    """
    if ratio < 0.5:
        return "way over-forecast"
    if ratio < 0.8:
        return "over-forecast"
    if ratio <= 1.25:
        return "on target"
    if ratio <= 2.0:
        return "under-forecast"
    return "severely under-forecast"


def describe_period(
    period: str,
    esb_kw: float | None,
    esb_pct: int | None,
    quartz_kw: float | None,
    quartz_pct: int | None,
    buffered_kw: float,
    avg_actual_kw: float,
    clipping_count: int,
    n: int,
) -> str:
    """Build a plain-language summary sentence for one date/period.

    Describes how ESB and Quartz forecasts compared to actuals, whether calibration
    helped, and whether clipping was observed.

    Args:
        period: Period name ('Morn', 'Aftn', 'Eve').
        esb_kw: ESB forecast in kW, or None if unavailable.
        esb_pct: ESB percentage difference (actual-forecast)/forecast*100, or None.
        quartz_kw: Quartz forecast in kW, or None if unavailable.
        quartz_pct: Quartz percentage difference, or None.
        buffered_kw: ESB forecast × power_multiplier, in kW.
        avg_actual_kw: Mean measured solar output across all samples, in kW.
        clipping_count: Number of samples flagged as likely clipping.
        n: Total telemetry samples in this period.

    Returns:
        Human-readable description string.
    """
    parts: list[str] = [f"{period}:"]

    # Compare ESB and Quartz to actual
    if esb_kw is not None and esb_pct is not None:
        esb_label = classify_accuracy(avg_actual_kw / esb_kw if esb_kw > 0 else 0.0)
        parts.append(f"ESB {esb_label} ({esb_pct:+d}%)")

    if quartz_kw is not None and quartz_pct is not None:
        quartz_label = classify_accuracy(avg_actual_kw / quartz_kw if quartz_kw > 0 else 0.0)
        parts.append(f"Quartz {quartz_label} ({quartz_pct:+d}%)")

    # Did calibration help?
    if esb_kw is not None:
        buf_ratio = avg_actual_kw / buffered_kw if buffered_kw > 0 else 0.0
        if buf_ratio <= 1.25:
            parts.append("Calibration brought ESB on target.")
        elif buf_ratio > 2.0:
            parts.append(
                f"Even with calibration, actual was {buf_ratio:.1f}×"
                f" the buffered figure — calibration needs more data."
            )

    # Clipping?
    if clipping_count > 0:
        clip_pct = round(100 * clipping_count / n)
        parts.append(
            f"Clipping in {clipping_count}/{n} ({clip_pct}%)"
            f" — inverter hit its {INVERTER_KW}kW ceiling."
        )

    return " ".join(parts)


def build_period_rows(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    """Group telemetry records by (date, period) and aggregate metrics.

    Args:
        records: All telemetry records from the archive.

    Returns:
        Dict keyed by (date, period). Each value contains:
          - actual_kw: list of per-sample solar kW readings
          - clipping_count: number of samples with likely_clipping=True
    """
    rows: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"actual_kw": [], "clipping_count": 0}
    )
    for rec in records:
        ts = rec.get("captured_at", "")
        if len(ts) < 13:
            continue
        hour = int(ts[11:13])
        period = period_for_hour(hour)
        if not period:
            continue
        date = ts[:10]
        key = (date, period)

        metrics = (rec.get("derived") or {}).get("extracted_metrics", {})
        solar_kw = metrics.get("solar_power_kw")
        if solar_kw is None:
            continue
        rows[key]["actual_kw"].append(solar_kw)

        if (rec.get("derived") or {}).get("likely_clipping"):
            rows[key]["clipping_count"] += 1

    return rows


def build_forecast_rows(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, float | None]]:
    """Extract ESB and Quartz forecasts by (date, period) from comparison archive.

    Args:
        records: All forecast comparison records from the archive.

    Returns:
        Dict keyed by (date, period). Each value contains:
          - esb_w: ESB forecast in watts (or None)
          - quartz_w: Quartz forecast in watts (or None)
    """
    rows: dict[tuple[str, str], dict[str, float | None]] = defaultdict(
        lambda: {"esb_w": None, "quartz_w": None}
    )
    for rec in records:
        ts = rec.get("captured_at", "")
        if len(ts) < 10:
            continue
        date = ts[:10]

        today_periods = (rec.get("today") or {}).get("periods", {})
        for period_name, period_data in today_periods.items():
            if period_name not in _PERIOD_WINDOWS:
                continue
            key = (date, period_name)

            primary = (period_data or {}).get("primary")
            if primary and primary.get("value_w") is not None:
                rows[key]["esb_w"] = primary.get("value_w")

            secondary = (period_data or {}).get("secondary")
            if secondary and secondary.get("value_w") is not None:
                rows[key]["quartz_w"] = secondary.get("value_w")

    return rows


def print_report(
    telemetry_rows: dict[tuple[str, str], dict[str, Any]],
    forecast_rows: dict[tuple[str, str], dict[str, float | None]],
    calibration: dict[str, dict[str, float]],
) -> None:
    """Print the forecast accuracy report to stdout.

    Outputs a numeric table followed by a plain-language summary section.

    Args:
        telemetry_rows: Aggregated period telemetry from build_period_rows().
        forecast_rows: ESB and Quartz forecasts from build_forecast_rows().
        calibration: Per-period calibration data from load_calibration().
    """
    print()
    print("=" * 130)
    print("  FORECAST ACCURACY REPORT")
    print("  ESB = county-level synthetic | Quartz = site-level | Buf = ESB × power_multiplier")
    print("  Percentages show (Actual - Forecast) / Forecast: negative=overestimated, positive=underestimated")
    print("=" * 130)
    header = (
        f"  {'Date':<12}  {'Period':<6}  "
        f"{'ESB kW':>16}  {'Quartz kW':>18}  {'Buf kW':>8}  "
        f"{'Avg Act kW':>10}  {'Clips':>5}  {'n':>5}"
    )
    print(header)
    print("-" * 130)

    verdicts: list[str] = []

    for (date, period), telem in sorted(telemetry_rows.items()):
        actuals = telem["actual_kw"]
        if not actuals:
            continue
        avg_actual = round(sum(actuals) / len(actuals), 2)
        n = len(actuals)
        clipping_count = telem["clipping_count"]

        forecast = forecast_rows.get((date, period), {})
        esb_w = forecast.get("esb_w")
        quartz_w = forecast.get("quartz_w")

        esb_kw = round(esb_w / 1000, 3) if esb_w is not None else None
        quartz_kw = round(quartz_w / 1000, 3) if quartz_w is not None else None

        # Calculate percentage differences
        esb_pct = None
        if esb_kw is not None and esb_kw > 0:
            esb_pct = round(((avg_actual - esb_kw) / esb_kw) * 100)

        quartz_pct = None
        if quartz_kw is not None and quartz_kw > 0:
            quartz_pct = round(((avg_actual - quartz_kw) / quartz_kw) * 100)

        cal = calibration.get(period, {})
        multiplier = cal.get("power_multiplier", 1.0)
        buffered_kw = round(esb_kw * multiplier, 3) if esb_kw is not None else None

        # Format columns with percentages
        esb_s = f"{esb_kw:.3f} ({esb_pct:+d}%)" if esb_kw is not None and esb_pct is not None else "N/A"
        quartz_s = f"{quartz_kw:.3f} ({quartz_pct:+d}%)" if quartz_kw is not None and quartz_pct is not None else "N/A"
        buf_s = f"{buffered_kw:.3f}" if buffered_kw is not None else "N/A"

        print(
            f"  {date:<12}  {period:<6}  "
            f"{esb_s:>16}  {quartz_s:>18}  {buf_s:>8}  "
            f"{avg_actual:>10.2f}  {clipping_count:>5}  {n:>5}"
        )

        if esb_kw is not None and buffered_kw is not None:
            verdicts.append(
                f"  {date} {period}: "
                + describe_period(
                    period=period,
                    esb_kw=esb_kw,
                    esb_pct=esb_pct,
                    quartz_kw=quartz_kw,
                    quartz_pct=quartz_pct,
                    buffered_kw=buffered_kw,
                    avg_actual_kw=avg_actual,
                    clipping_count=clipping_count,
                    n=n,
                )
            )

    print()
    print("=" * 130)
    print("  PLAIN LANGUAGE SUMMARY")
    print("=" * 130)
    if verdicts:
        for verdict in verdicts:
            print(verdict)
            print()
    else:
        print("  No data to summarise.")
    print()


def main() -> None:
    """Entry point: load data from config-specified paths and print the accuracy report."""
    telemetry_path = _ROOT / TELEMETRY_LOG_PATH
    calibration_path = _ROOT / CALIBRATION_LOG_PATH
    forecast_path = _ROOT / FORECAST_COMPARISONS_LOG_PATH

    telemetry_records = load_telemetry_rows(telemetry_path)
    if not telemetry_records:
        print(f"No telemetry records found at {telemetry_path}")
        sys.exit(1)

    calibration = load_calibration(calibration_path)
    forecast_records = load_forecast_comparisons(forecast_path)

    telemetry_rows = build_period_rows(telemetry_records)
    forecast_rows = build_forecast_rows(forecast_records)

    print_report(telemetry_rows, forecast_rows, calibration)


if __name__ == "__main__":
    main()

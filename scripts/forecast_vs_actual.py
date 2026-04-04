"""scripts/forecast_vs_actual.py
---------------------------------
Utility script: compare solar forecast predictions against actual logged generation.

Run from the project root:
    python scripts/forecast_vs_actual.py

For each date/period that has telemetry data, prints:
  - Raw forecast kW (as received from the weather provider)
  - Calibrated/buffered forecast kW (raw × power_multiplier from calibration file)
  - Actual average and peak kW measured by the inverter
  - Number of clipping events flagged in that window
  - Plain-language verdict explaining how accurate the forecast was

Data file locations are read from config.py (TELEMETRY_LOG_PATH, CALIBRATION_LOG_PATH).
"""

import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

# Allow running from project root or from scripts/ sub-directory
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from config import CALIBRATION_LOG_PATH, INVERTER_KW, TELEMETRY_LOG_PATH

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
    status: str,
    forecast_kw: float,
    buffered_kw: float,
    avg_actual_kw: float,
    max_actual_kw: float,
    clipping_count: int,
    n: int,
) -> str:
    """Build a plain-language summary sentence for one date/period.

    Describes how the raw forecast compared to actuals, whether calibration
    brought the estimate closer, and whether clipping was observed.

    Args:
        period: Period name ('Morn', 'Aftn', 'Eve').
        status: Forecast colour status ('Red', 'Amber', 'Green').
        forecast_kw: Raw forecast from the weather provider, in kW.
        buffered_kw: Calibration-adjusted forecast (raw × power_multiplier), in kW.
        avg_actual_kw: Mean measured solar output across all telemetry samples, in kW.
        max_actual_kw: Peak measured solar output in the period, in kW.
        clipping_count: Number of samples flagged as likely clipping.
        n: Total telemetry samples in this period.

    Returns:
        Human-readable description string.
    """
    raw_ratio = avg_actual_kw / forecast_kw if forecast_kw > 0 else 0.0
    buf_ratio = avg_actual_kw / buffered_kw if buffered_kw > 0 else 0.0
    raw_label = classify_accuracy(raw_ratio)
    buf_label = classify_accuracy(buf_ratio)

    parts: list[str] = [f"{period} was {status.lower()}."]

    if raw_label == "on target":
        parts.append("Raw forecast was accurate.")
    elif "under" in raw_label:
        parts.append(
            f"Raw forecast underestimated generation"
            f" (actual was {raw_ratio:.1f}× forecast)."
        )
    else:
        parts.append(
            f"Raw forecast overestimated generation"
            f" (actual was {raw_ratio:.1f}× forecast)."
        )

    if abs(buffered_kw - forecast_kw) > 0.01:
        if buf_label == "on target":
            parts.append("Calibration brought the estimate on target.")
        elif "under" in buf_label:
            parts.append(
                f"Even after calibration, actual was {buf_ratio:.1f}× the buffered"
                f" figure — calibration may need more data."
            )
        else:
            parts.append(
                f"Calibration overcorrected (actual was {buf_ratio:.1f}× buffered figure)."
            )

    if clipping_count > 0:
        clip_pct = round(100 * clipping_count / n)
        parts.append(
            f"Clipping detected in {clipping_count}/{n} samples ({clip_pct}%)"
            f" — the inverter hit its {INVERTER_KW} kW ceiling."
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
          - forecast_kw: raw forecast in kW (first seen per period)
          - forecast_status: colour label (first seen per period)
          - clipping_count: number of samples with likely_clipping=True
    """
    rows: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "actual_kw": [],
            "forecast_kw": None,
            "forecast_status": None,
            "clipping_count": 0,
        }
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

        if rows[key]["forecast_kw"] is None:
            fentry = (rec.get("forecast_today") or {}).get(period)
            if fentry:
                rows[key]["forecast_kw"] = round(fentry[0] / 1000, 3)
                rows[key]["forecast_status"] = fentry[1]

    return rows


def print_report(
    rows: dict[tuple[str, str], dict[str, Any]],
    calibration: dict[str, dict[str, float]],
) -> None:
    """Print the forecast accuracy report to stdout.

    Outputs a numeric table followed by a plain-language summary section.

    Args:
        rows: Aggregated period rows from build_period_rows().
        calibration: Per-period calibration data from load_calibration().
    """
    print()
    print("=" * 95)
    print("  FORECAST ACCURACY REPORT")
    print("  Fcst kW = raw provider forecast  |  Buf kW = calibration-adjusted forecast")
    print("=" * 95)
    header = (
        f"  {'Date':<12}  {'Period':<6}  {'Status':<7}"
        f"  {'Fcst kW':>8}  {'Buf kW':>8}  {'Avg Act kW':>10}"
        f"  {'Max Act kW':>10}  {'Ratio(raw)':>10}  {'Clips':>5}  {'n':>4}"
    )
    print(header)
    print("-" * 95)

    verdicts: list[str] = []

    for (date, period), v in sorted(rows.items()):
        actuals = v["actual_kw"]
        if not actuals:
            continue
        avg_actual = round(sum(actuals) / len(actuals), 2)
        max_actual = round(max(actuals), 2)
        forecast_kw = v["forecast_kw"]
        status = v["forecast_status"] or "N/A"
        n = len(actuals)
        clipping_count = v["clipping_count"]

        cal = calibration.get(period, {})
        multiplier = cal.get("power_multiplier", 1.0)
        buffered_kw = round(forecast_kw * multiplier, 3) if forecast_kw is not None else None

        raw_ratio = round(avg_actual / forecast_kw, 2) if forecast_kw else None
        fkw_s = f"{forecast_kw:.3f}" if forecast_kw is not None else "N/A"
        buf_s = f"{buffered_kw:.3f}" if buffered_kw is not None else "N/A"
        ratio_s = f"{raw_ratio:.2f}" if raw_ratio is not None else "N/A"

        print(
            f"  {date:<12}  {period:<6}  {status:<7}"
            f"  {fkw_s:>8}  {buf_s:>8}  {avg_actual:>10.2f}"
            f"  {max_actual:>10.2f}  {ratio_s:>10}  {clipping_count:>5}  {n:>4}"
        )

        if forecast_kw is not None and buffered_kw is not None:
            verdicts.append(
                f"  {date} {period}: "
                + describe_period(
                    period=period,
                    status=status,
                    forecast_kw=forecast_kw,
                    buffered_kw=buffered_kw,
                    avg_actual_kw=avg_actual,
                    max_actual_kw=max_actual,
                    clipping_count=clipping_count,
                    n=n,
                )
            )

    print()
    print("=" * 95)
    print("  PLAIN LANGUAGE SUMMARY")
    print("=" * 95)
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

    records = load_telemetry_rows(telemetry_path)
    if not records:
        print(f"No telemetry records found at {telemetry_path}")
        sys.exit(1)

    calibration = load_calibration(calibration_path)
    rows = build_period_rows(records)
    print_report(rows, calibration)


if __name__ == "__main__":
    main()

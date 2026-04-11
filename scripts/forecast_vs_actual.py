"""scripts/forecast_vs_actual.py
---------------------------------
Utility script: compare solar forecast predictions against actual logged generation.

Forecast data sources:
    - ESB: Solar forecast INDEX (not watts)
      - Red: 100 (low solar)
      - Amber: 300 (medium solar)
      - Green: 500 (high solar)
    - Quartz: Power forecast in WATTS (actual power estimate)

Run from the project root:
    python scripts/forecast_vs_actual.py

For each date/period that has telemetry data, prints:
    - ESB solar forecast index + status (primary provider, county-level synthetic)
    - Quartz power forecast in watts + status (secondary provider, site-level)
    - Forecast accuracy percentage versus measured period energy
    - Calibrated forecast equivalent (ESB index × fitted period multiplier)
    - Actual period energy (kWh) from sum of hourly averages
    - Average battery SOC (%) measured in that period
    - Actual classification with explicit basis (Array or Inverter)
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
from statistics import median
from typing import Any

# Allow running from project root or from scripts/ sub-directory
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from config.settings import (
    CALIBRATION_LOG_PATH,
    FORECAST_ANALYSIS_AFTERNOON_END_HOUR,
    FORECAST_ANALYSIS_AFTERNOON_START_HOUR,
    FORECAST_ANALYSIS_CLIPPING_PROMOTE_MIN_RATE,
    FORECAST_ANALYSIS_CLIPPING_PROMOTE_MIN_UTILIZATION,
    FORECAST_ANALYSIS_EVENING_END_HOUR,
    FORECAST_ANALYSIS_EVENING_START_HOUR,
    FORECAST_ANALYSIS_INVERTER_AMBER_UTILIZATION_MAX,
    FORECAST_ANALYSIS_INVERTER_RED_UTILIZATION_MAX,
    FORECAST_ANALYSIS_MORNING_END_HOUR,
    FORECAST_ANALYSIS_MORNING_START_HOUR,
    FORECAST_ANALYSIS_ON_TARGET_MAX_RATIO,
    FORECAST_ANALYSIS_OVER_FORECAST_MAX_RATIO,
    FORECAST_ANALYSIS_SOC_FULL_THRESHOLD_PERCENT,
    FORECAST_ANALYSIS_UNDER_FORECAST_MAX_RATIO,
    FORECAST_ANALYSIS_WAY_OVER_FORECAST_MAX_RATIO,
    FORECAST_COMPARISONS_LOG_PATH,
    INVERTER_KW,
    QUARTZ_GREEN_CAPACITY_FRACTION,
    QUARTZ_RED_CAPACITY_FRACTION,
    SOLAR_PV_KW,
    TELEMETRY_LOG_PATH,
)

# Period hour boundaries — must stay in sync with telemetry_archive.py
_PERIOD_WINDOWS: dict[str, tuple[int, int]] = {
    "Morn": (FORECAST_ANALYSIS_MORNING_START_HOUR, FORECAST_ANALYSIS_MORNING_END_HOUR),
    "Aftn": (FORECAST_ANALYSIS_AFTERNOON_START_HOUR, FORECAST_ANALYSIS_AFTERNOON_END_HOUR),
    "Eve": (FORECAST_ANALYSIS_EVENING_START_HOUR, FORECAST_ANALYSIS_EVENING_END_HOUR),
}

CLIPPING_LOOKAHEAD_SAMPLES = 2


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
    if ratio < FORECAST_ANALYSIS_WAY_OVER_FORECAST_MAX_RATIO:
        return "way over-forecast"
    if ratio < FORECAST_ANALYSIS_OVER_FORECAST_MAX_RATIO:
        return "over-forecast"
    if ratio <= FORECAST_ANALYSIS_ON_TARGET_MAX_RATIO:
        return "on target"
    if ratio <= FORECAST_ANALYSIS_UNDER_FORECAST_MAX_RATIO:
        return "under-forecast"
    return "severely under-forecast"


def derive_status_from_power(power_kw: float, capacity_kwp: float) -> str:
    """Derive Red/Amber/Green status from solar power and system capacity.

    Uses stricter capacity-based thresholds:
    - Red: < 20% of capacity
    - Amber: 20-40% of capacity
    - Green: > 40% of capacity

    Args:
        power_kw: Solar power in kW (forecast or actual).
        capacity_kwp: Array capacity in kW.

    Returns:
        Status string: 'Red', 'Amber', or 'Green'.
    """
    red_threshold = capacity_kwp * QUARTZ_RED_CAPACITY_FRACTION
    green_threshold = capacity_kwp * QUARTZ_GREEN_CAPACITY_FRACTION

    if power_kw < red_threshold:
        return "Red"
    if power_kw < green_threshold:
        return "Amber"
    return "Green"


def derive_actual_reading_status(
    avg_actual_kw: float,
    soc_full: bool,
    clipping_count: int,
    sample_count: int,
    inverter_kw: float,
    array_kw: float,
) -> tuple[str, str]:
    """Derive actual-status from inverter utilization with clipping awareness.

    This avoids over-trusting array-size thresholds when measured power is capped
    by inverter output and/or a full battery operating state.

    Args:
        avg_actual_kw: Average measured solar power in kW.
        soc_full: Whether the period should be treated as battery-full constrained.
        clipping_count: Number of likely clipping samples in the period.
        sample_count: Number of telemetry samples in the period.
        inverter_kw: Inverter AC ceiling in kW.
        array_kw: Solar array DC capacity in kW.

    Returns:
        Tuple of (status, basis), where basis is 'Inverter' or 'Array'.
    """
    basis = "Inverter" if soc_full else "Array"
    denominator_kw = inverter_kw if soc_full else array_kw

    utilization = (avg_actual_kw / denominator_kw) if denominator_kw > 0 else 0.0
    clipping_rate = (clipping_count / sample_count) if sample_count > 0 else 0.0

    # Use stricter bands when the inverter basis applies.
    if basis == "Inverter":
        if utilization < FORECAST_ANALYSIS_INVERTER_RED_UTILIZATION_MAX:
            status = "Red"
        elif utilization < FORECAST_ANALYSIS_INVERTER_AMBER_UTILIZATION_MAX:
            status = "Amber"
        else:
            status = "Green"
    else:
        if utilization < QUARTZ_RED_CAPACITY_FRACTION:
            status = "Red"
        elif utilization < QUARTZ_GREEN_CAPACITY_FRACTION:
            status = "Amber"
        else:
            status = "Green"

    # Frequent clipping implies available solar may be higher than measured.
    if (
        clipping_rate >= FORECAST_ANALYSIS_CLIPPING_PROMOTE_MIN_RATE
        and utilization >= FORECAST_ANALYSIS_CLIPPING_PROMOTE_MIN_UTILIZATION
    ):
        return "Green", basis
    return status, basis


def describe_period(
    period: str,
    esb_kw: float | None,
    esb_status: str | None,
    forecast_solar_kw: float | None,
    forecast_solar_pct: int | None,
    quartz_kw: float | None,
    quartz_pct: int | None,
    actual_status: str,
    buffered_kwh: float,
    actual_period_kwh: float,
    clipping_count: int,
    n: int,
) -> str:
    """Build a plain-language summary sentence for one date/period.

    Describes how ESB and Quartz forecasts compared to actuals, whether calibration
    helped, and whether clipping was observed.

    Args:
        period: Period name ('Morn', 'Aftn', 'Eve').
        esb_kw: ESB forecast in kW, or None if unavailable.
        esb_status: ESB Red/Amber/Green status, or None.
        quartz_kw: Quartz forecast in kW, or None if unavailable.
        quartz_pct: Quartz percentage of actual, or None.
        actual_status: Red/Amber/Green status derived from measured output.
        buffered_kwh: ESB forecast × power_multiplier converted to period kWh.
        actual_period_kwh: Actual period energy (kWh) from sum of hourly averages.
        clipping_count: Number of samples flagged as likely clipping.
        n: Total telemetry samples in this period.

    Returns:
        Human-readable description string.
    """
    parts: list[str] = []

    # Compare ESB and Quartz to actual
    if esb_kw is not None and esb_status is not None:
        parts.append(f"ESB forecast was {esb_status}; actual reading was {actual_status}")

    if forecast_solar_kw is not None and forecast_solar_pct is not None:
        if forecast_solar_pct < 50:
            fs_label = "severely underestimated"
        elif forecast_solar_pct < 80:
            fs_label = "underestimated"
        elif forecast_solar_pct <= 120:
            fs_label = "on target"
        elif forecast_solar_pct < 200:
            fs_label = "overestimated"
        else:
            fs_label = "severely overestimated"
        parts.append(f"Forecast.Solar {fs_label} ({forecast_solar_pct}%)")

    if quartz_kw is not None and quartz_pct is not None:
        if quartz_pct < 50:
            label = "severely underestimated"
        elif quartz_pct < 80:
            label = "underestimated"
        elif quartz_pct <= 120:
            label = "on target"
        elif quartz_pct < 200:
            label = "overestimated"
        else:
            label = "severely overestimated"
        parts.append(f"Quartz {label} ({quartz_pct}%)")

    # Did calibration help?
    if esb_kw is not None:
        buf_ratio = actual_period_kwh / buffered_kwh if buffered_kwh > 0 else 0.0
        if buf_ratio <= 1.25:
            parts.append("Calibration brought ESB on target.")
        elif buf_ratio > 2.0:
            parts.append(
                f"Even with calibration, actual was {buf_ratio:.1f}×"
                f" the calibrated figure — calibration needs more data."
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
                    - actual_kw: per-sample solar kW readings
                    - hourly_samples: per-sample tuples of (local_hour, solar_kW)
                    - soc_percent: per-sample battery SOC values
                    - clipping_count: samples with likely_clipping=True
    """
    rows: dict[tuple[str, str], dict[str, Any]] = defaultdict(
                lambda: {
                    "actual_kw": [],
                    "hourly_samples": [],
                    "soc_percent": [],
                    "clipping_count": 0,
                }
    )
    for rec in records:
        if not isinstance(rec, dict):
            continue
        ts = rec.get("captured_at", "")
        if len(ts) < 13:
            continue
        hour = int(ts[11:13])
        period = period_for_hour(hour)
        if not period:
            continue
        date = ts[:10]
        key = (date, period)

        derived = rec.get("derived")
        if not isinstance(derived, dict):
            derived = {}
        metrics = derived.get("extracted_metrics", {})
        if not isinstance(metrics, dict):
            metrics = {}
        solar_kw = metrics.get("solar_power_kw")
        if solar_kw is None:
            continue
        rows[key]["actual_kw"].append(solar_kw)
        rows[key]["hourly_samples"].append((hour, float(solar_kw)))

        soc_percent = metrics.get("battery_soc_percent")
        if isinstance(soc_percent, (int, float)):
            rows[key]["soc_percent"].append(float(soc_percent))

    for _, row in rows.items():
        row["clipping_count"] = count_confirmed_clipping_samples(
            row["actual_kw"],
            lookahead=CLIPPING_LOOKAHEAD_SAMPLES,
        )

    return rows


def period_energy_kwh_from_hourly_means(hourly_samples: list[tuple[int, float]]) -> float:
    """Compute period energy as the sum of hourly average kW values.

    Args:
        hourly_samples: Tuples of (local_hour, solar_kW) for one date/period.

    Returns:
        Energy in kWh computed by averaging kW within each hour bucket and
        summing those hourly averages across the period.
    """
    if not hourly_samples:
        return 0.0

    by_hour: dict[int, list[float]] = defaultdict(list)
    for hour, value_kw in hourly_samples:
        by_hour[hour].append(value_kw)

    return sum(sum(values) / len(values) for values in by_hour.values())


def build_daily_pv_totals(records: list[dict[str, Any]]) -> dict[str, float]:
    """Build per-day PV energy totals from telemetry `pvDayNrg` values.

    Args:
        records: All telemetry records from the archive.

    Returns:
        Dict of date string (YYYY-MM-DD) to max observed `pvDayNrg` value (kWh).
    """
    daily_totals: dict[str, float] = {}

    for rec in records:
        if not isinstance(rec, dict):
            continue
        ts = rec.get("captured_at", "")
        if len(ts) < 10:
            continue

        date = ts[:10]
        energy_flow = rec.get("energy_flow")
        if not isinstance(energy_flow, dict):
            continue
        pv_day_nrg = energy_flow.get("pvDayNrg")
        if not isinstance(pv_day_nrg, (int, float)):
            continue

        pv_day_nrg_f = float(pv_day_nrg)
        daily_totals[date] = max(daily_totals.get(date, 0.0), pv_day_nrg_f)

    return daily_totals


def count_confirmed_clipping_samples(actual_kw_samples: list[float], lookahead: int = 2) -> int:
    """Count confirmed clipping samples using strict ceiling and short lookahead checks.

    A sample is a clipping candidate only when it equals the inverter ceiling exactly.
    If any closely-following sample exceeds the inverter ceiling, the candidate is
    discarded because that pattern implies the earlier reading was not a true cap.

    Args:
        actual_kw_samples: Ordered per-period power samples in kW.
        lookahead: Number of subsequent samples to inspect.

    Returns:
        Number of samples considered confirmed clipping.
    """
    clipping_count = 0
    n = len(actual_kw_samples)

    for i, sample_kw in enumerate(actual_kw_samples):
        if sample_kw != INVERTER_KW:
            continue

        upper = min(n, i + 1 + max(0, lookahead))
        future_samples = actual_kw_samples[i + 1:upper]
        if any(next_kw > INVERTER_KW for next_kw in future_samples):
            continue

        clipping_count += 1

    return clipping_count


def build_forecast_rows(
    records: list[dict[str, Any]],
) -> dict[tuple[str, str], dict[str, float | str | None]]:
    """Extract ESB solar forecast indices and Quartz power forecasts by (date, period).

    ESB provides a solar forecast INDEX (not watts):
    - Red: 100 (low solar)
    - Amber: 300 (medium solar)
    - Green: 500 (high solar)

    Quartz provides power forecast in WATTS.

    Uses the EARLIEST record for each (date, period), which represents the forecast
    closest to the decision-making time. This allows comparison against the forecasts
    that actually drove scheduling decisions.

    Args:
        records: All forecast comparison records from the archive.

    Returns:
        Dict keyed by (date, period). Each value contains:
          - esb_w: ESB solar forecast index (or None). Values: 100 (Red), 300 (Amber), 500 (Green).
          - esb_status: ESB status string Red/Amber/Green (or None)
          - quartz_w: Quartz power forecast in watts (or None)
          - quartz_status: Quartz status string Red/Amber/Green (or None)
    """
    rows: dict[tuple[str, str], dict[str, float | str | None]] = defaultdict(
        lambda: {
            "esb_w": None,
            "esb_status": None,
            "forecast_solar_w": None,
            "forecast_solar_status": None,
            "quartz_w": None,
            "quartz_status": None,
            "captured_at": None,
        }
    )
    for rec in records:
        if not isinstance(rec, dict):
            continue
        ts = rec.get("captured_at", "")
        if len(ts) < 10:
            continue
        date = ts[:10]

        today_periods = (rec.get("today") or {}).get("periods", {})
        for period_name, period_data in today_periods.items():
            if period_name not in _PERIOD_WINDOWS:
                continue
            key = (date, period_name)

            # Only update if this is the first record for this period (earliest = closest to decision)
            if rows[key]["captured_at"] is None or ts < rows[key]["captured_at"]:
                rows[key]["captured_at"] = ts

                primary = (period_data or {}).get("primary")
                if primary and primary.get("value_w") is not None:
                    rows[key]["esb_w"] = primary.get("value_w")
                    rows[key]["esb_status"] = primary.get("status")

                secondary = (period_data or {}).get("secondary")
                if secondary and secondary.get("value_w") is not None:
                    secondary_name = str(rec.get("secondary_provider") or "quartz").strip().lower()
                    if secondary_name == "forecast_solar":
                        rows[key]["forecast_solar_w"] = secondary.get("value_w")
                        rows[key]["forecast_solar_status"] = secondary.get("status")
                    else:
                        rows[key]["quartz_w"] = secondary.get("value_w")
                        rows[key]["quartz_status"] = secondary.get("status")

                tertiary = (period_data or {}).get("tertiary")
                if tertiary and tertiary.get("value_w") is not None:
                    tertiary_name = str(rec.get("tertiary_provider") or "").strip().lower()
                    if tertiary_name == "quartz":
                        rows[key]["quartz_w"] = tertiary.get("value_w")
                        rows[key]["quartz_status"] = tertiary.get("status")
                    elif tertiary_name == "forecast_solar":
                        rows[key]["forecast_solar_w"] = tertiary.get("value_w")
                        rows[key]["forecast_solar_status"] = tertiary.get("status")

    return rows


def print_report(
    telemetry_rows: dict[tuple[str, str], dict[str, Any]],
    forecast_rows: dict[tuple[str, str], dict[str, float | str | None]],
    calibration: dict[str, dict[str, float]],
    daily_pv_totals: dict[str, float],
) -> None:
    """Print the forecast accuracy report to stdout.

    Outputs a numeric table followed by a plain-language summary section.

    Args:
        telemetry_rows: Aggregated period telemetry from build_period_rows().
        forecast_rows: ESB and Quartz forecasts from build_forecast_rows().
        calibration: Per-period calibration data from load_calibration().
        daily_pv_totals: Per-day max `pvDayNrg` values from telemetry (kWh).
    """
    # Fit a period-level multiplier directly from observed data so the report's
    # calibrated column reflects how ESB compares to measured output in practice.
    fitted_multipliers = compute_period_fit_multipliers(telemetry_rows, forecast_rows)

    print()
    print("=" * 176)
    print("  FORECAST ACCURACY REPORT")
    print("  ESB = county-level synthetic | Quartz = site-level | Calibrated = ESB × fitted period multiplier")
    print("  ESB/Quartz status use site-capacity thresholds; Actual Reading is SOC-aware (Array vs Inverter basis)")
    print("  Percentages show (Forecast / Actual) × 100: <100% means underestimated, >100% means overestimated")
    print("=" * 176)
    header = (
        f"  {'Date':<12}  {'Period':<6}  "
        f"{'ESB Forecast':>12}  {'ForecastSolar':>14}  {'FS Status':>10}  {'Quartz kW':>12}  {'Quartz Status':>13}  {'Calibrated':>12}  "
        f"{'Act kWh':>10}  {'Avg SOC %':>10}  {'Actual Basis':>12}  {'Actual Reading':>14}  {'Clips':>5}  {'n':>5}"
    )
    print(header)
    print("-" * 176)

    verdicts_by_date: dict[str, list[str]] = defaultdict(list)

    # Sort by date, then by period in time order (Morn, Aftn, Eve)
    period_order = {"Morn": 0, "Aftn": 1, "Eve": 2}
    sorted_items = sorted(
        telemetry_rows.items(),
        key=lambda x: (x[0][0], period_order.get(x[0][1], 999))
    )

    previous_date = None
    for (date, period), telem in sorted_items:
        # Add blank line between different days
        if previous_date is not None and date != previous_date:
            print()
        previous_date = date
        
        actuals = telem["actual_kw"]
        if not actuals:
            continue
        avg_actual = round(sum(actuals) / len(actuals), 2)
        actual_period_kwh = round(
            period_energy_kwh_from_hourly_means(telem.get("hourly_samples", [])),
            2,
        )
        n = len(actuals)
        clipping_count = telem["clipping_count"]
        soc_samples = telem.get("soc_percent", [])
        avg_soc = round(sum(soc_samples) / len(soc_samples), 1) if soc_samples else None
        max_soc = max(soc_samples) if soc_samples else None
        # Treat very high SOC as full to handle decimal rounding around 100%.
        soc_full = (
            max_soc is not None
            and max_soc >= FORECAST_ANALYSIS_SOC_FULL_THRESHOLD_PERCENT
        )

        forecast = forecast_rows.get((date, period), {})
        esb_w = forecast.get("esb_w")
        esb_status = forecast.get("esb_status")  # Use stored status from archive
        quartz_w = forecast.get("quartz_w")
        quartz_status = forecast.get("quartz_status")  # Use stored status from archive
        forecast_solar_w = forecast.get("forecast_solar_w")
        forecast_solar_status = forecast.get("forecast_solar_status")

        # Convert ESB forecast index to kW equivalent (for reporting purposes)
        # ESB indices: Red=100, Amber=300, Green=500
        # Quartz is already in watts, convert to kW
        esb_kw = round(esb_w / 1000, 3) if esb_w is not None else None
        forecast_solar_kw = round(forecast_solar_w / 1000, 3) if forecast_solar_w is not None else None
        quartz_kw = round(quartz_w / 1000, 3) if quartz_w is not None else None
        period_start_hour, period_end_hour = _PERIOD_WINDOWS[period]
        period_hours = max(0, period_end_hour - period_start_hour)
        esb_kwh = round(esb_kw * period_hours, 3) if esb_kw is not None else None
        forecast_solar_kwh = (
            round(forecast_solar_kw * period_hours, 3) if forecast_solar_kw is not None else None
        )
        quartz_kwh = round(quartz_kw * period_hours, 3) if quartz_kw is not None else None

        # Calculate percentage of actual (forecast as % of actual)
        quartz_pct = None
        if quartz_kwh is not None and actual_period_kwh > 0:
            quartz_pct = round((quartz_kwh / actual_period_kwh) * 100)
        forecast_solar_pct = None
        if forecast_solar_kwh is not None and actual_period_kwh > 0:
            forecast_solar_pct = round((forecast_solar_kwh / actual_period_kwh) * 100)

        cal = calibration.get(period, {})
        multiplier = fitted_multipliers.get(period, cal.get("power_multiplier", 1.0))
        buffered_kwh = round(esb_kwh * multiplier, 3) if esb_kwh is not None else None

        # Format columns with percentages
        esb_s = esb_status if esb_status is not None else "N/A"

        fs_status = forecast_solar_status if forecast_solar_status is not None else "N/A"
        fs_s = (
            f"{forecast_solar_kwh:.3f} ({forecast_solar_pct}%)"
            if forecast_solar_kwh is not None and forecast_solar_pct is not None
            else "N/A"
        )
        quartz_s_status = quartz_status if quartz_status is not None else "N/A"
        quartz_s = (
            f"{quartz_kwh:.3f} ({quartz_pct}%)"
            if quartz_kwh is not None and quartz_pct is not None
            else "N/A"
        )
        buf_s = f"{buffered_kwh:.3f}" if buffered_kwh is not None else "N/A"

        # Derive status from measured output using SOC-aware basis selection.
        derived_status, actual_basis = derive_actual_reading_status(
            avg_actual,
            soc_full,
            clipping_count,
            n,
            INVERTER_KW,
            SOLAR_PV_KW,
        )

        print(
            f"  {date:<12}  {period:<6}  "
            f"{esb_s:>12}  {fs_s:>14}  {fs_status:>10}  {quartz_s:>12}  {quartz_s_status:>13}  {buf_s:>12}  "
            f"{actual_period_kwh:>10.2f}  {f'{avg_soc:.1f}' if avg_soc is not None else 'N/A':>10}  "
            f"{actual_basis:>12}  {derived_status:>13}  {clipping_count:>5}  {n:>5}"
        )

        if esb_kwh is not None and buffered_kwh is not None:
            verdicts_by_date[date].append(
                f"  {date} {period}: "
                + describe_period(
                    period=period,
                    esb_kw=esb_kw,
                    esb_status=esb_status,
                    forecast_solar_kw=forecast_solar_kwh,
                    forecast_solar_pct=forecast_solar_pct,
                    quartz_kw=quartz_kwh,
                    quartz_pct=quartz_pct,
                    actual_status=derived_status,
                    buffered_kwh=buffered_kwh,
                    actual_period_kwh=actual_period_kwh,
                    clipping_count=clipping_count,
                    n=n,
                )
            )

    print()
    print("=" * 176)
    print("  PLAIN LANGUAGE SUMMARY")
    print("=" * 176)
    if verdicts_by_date:
        for index, date in enumerate(sorted(verdicts_by_date.keys())):
            for verdict in verdicts_by_date[date]:
                print(verdict)
            if date in daily_pv_totals:
                print(f"  {date} Day PV total (pvDayNrg): {daily_pv_totals[date]:.2f} kWh")
            if index < len(verdicts_by_date) - 1:
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
    daily_pv_totals = build_daily_pv_totals(telemetry_records)
    forecast_rows = build_forecast_rows(forecast_records)

    print_report(telemetry_rows, forecast_rows, calibration, daily_pv_totals)


def compute_period_fit_multipliers(
    telemetry_rows: dict[tuple[str, str], dict[str, Any]],
    forecast_rows: dict[tuple[str, str], dict[str, float | None]],
) -> dict[str, float]:
    """Compute median ESB-to-actual multipliers per period from observed rows.

    This report-focused fit is intentionally unbounded so calibrated values can be
    compared against actual measurements in a useful way.

    Args:
        telemetry_rows: Aggregated period telemetry keyed by (date, period).
        forecast_rows: Forecast rows keyed by (date, period).

    Returns:
        Dict of period -> median(actual_kw / esb_kw) for rows with both values.
    """
    ratios_by_period: dict[str, list[float]] = defaultdict(list)

    for (date, period), telem in telemetry_rows.items():
        actuals = telem.get("actual_kw", [])
        if not actuals:
            continue

        avg_actual = sum(actuals) / len(actuals)
        esb_w = (forecast_rows.get((date, period), {}) or {}).get("esb_w")
        if esb_w is None or esb_w <= 0:
            continue

        esb_kw = esb_w / 1000.0
        if esb_kw <= 0:
            continue

        ratios_by_period[period].append(avg_actual / esb_kw)

    return {
        period: round(median(ratios), 3)
        for period, ratios in ratios_by_period.items()
        if ratios
    }


if __name__ == "__main__":
    main()

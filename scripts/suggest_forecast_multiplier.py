"""scripts/suggest_forecast_multiplier.py
-----------------------------------------
Analyse recent forecast vs actual inverter telemetry for the configured
FORECAST_PROVIDER and print a clear per-period ratio breakdown with a
recommended multiplier setting.

For forecast_solar:
    Computes per-period ratios from raw forecast_solar_readings.jsonl against
    inverter telemetry, and recommends a new FORECAST_SOLAR_POWER_MULTIPLIER
    value for config/settings.py.

For esb_api / quartz:
    Computes per-period ratios from forecast_comparisons.jsonl against
    inverter telemetry, and recommends per-period power_multiplier values
    compared against the current forecast_calibration.json.

Usage:
    python scripts/suggest_forecast_multiplier.py
    python scripts/suggest_forecast_multiplier.py --days 30
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import median, mean, stdev
import sys
from typing import Any
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.constants import (
    FORECAST_CALIBRATION_PATH,
    FORECAST_COMPARISON_ARCHIVE_PATH,
    FORECAST_PROVIDER,
    FORECAST_SOLAR_ARCHIVE_PATH,
    INVERTER_TELEMETRY_ARCHIVE_PATH,
)
from config.settings import (
    FORECAST_ANALYSIS_AFTERNOON_END_HOUR,
    FORECAST_ANALYSIS_AFTERNOON_START_HOUR,
    FORECAST_ANALYSIS_EVENING_END_HOUR,
    FORECAST_ANALYSIS_EVENING_START_HOUR,
    FORECAST_ANALYSIS_MORNING_END_HOUR,
    FORECAST_ANALYSIS_MORNING_START_HOUR,
    FORECAST_SOLAR_POWER_MULTIPLIER,
    LOCAL_TIMEZONE,
)

PERIOD_WINDOWS: dict[str, tuple[int, int]] = {
    "Morn": (FORECAST_ANALYSIS_MORNING_START_HOUR, FORECAST_ANALYSIS_MORNING_END_HOUR),
    "Aftn": (FORECAST_ANALYSIS_AFTERNOON_START_HOUR, FORECAST_ANALYSIS_AFTERNOON_END_HOUR),
    "Eve": (FORECAST_ANALYSIS_EVENING_START_HOUR, FORECAST_ANALYSIS_EVENING_END_HOUR),
}
PERIOD_ORDER = ["Morn", "Aftn", "Eve"]

MIN_SAMPLES_FOR_RECOMMENDATION = 5


@dataclass
class PeriodStats:
    """Ratio observations for one period bucket.

    Args:
        ratios: List of (actual_kw / raw_forecast_kw) ratios across days.
        dates: Dates contributing to this period.
    """

    ratios: list[float] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)


def _parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed namespace with ``days`` attribute.
    """
    parser = argparse.ArgumentParser(description="Suggest forecast multiplier from recent telemetry.")
    parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Number of recent days to include in analysis (default: 30).",
    )
    return parser.parse_args()


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL file into a list of dicts.

    Args:
        path: Path to JSONL file.

    Returns:
        Parsed rows; empty list when file does not exist.
    """
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _period_for_local_hour(hour: int) -> str | None:
    """Return project period label for a local hour, or None if outside windows.

    Args:
        hour: Local wall-clock hour (0-23).

    Returns:
        Period label ('Morn', 'Aftn', 'Eve') or None.
    """
    for label, (start, end) in PERIOD_WINDOWS.items():
        if start <= hour < end:
            return label
    return None


def _build_telemetry_actuals(
    rows: list[dict[str, Any]],
    local_tz: ZoneInfo,
    min_date: date,
) -> dict[tuple[str, str], float]:
    """Aggregate telemetry pvPower into per-(date, period) average kW.

    Only includes samples from ``min_date`` onwards. Rows without valid
    ``captured_at`` or ``pvPower`` are skipped.

    Args:
        rows: Parsed inverter_telemetry.jsonl records.
        local_tz: Configured local timezone.
        min_date: Earliest local date to include.

    Returns:
        Dict mapping (date_iso, period) to average pvPower in kW.
    """
    grouped: dict[tuple[str, str], list[float]] = {}
    for row in rows:
        captured_at_str = row.get("captured_at")
        if not isinstance(captured_at_str, str):
            continue
        try:
            local_dt = datetime.fromisoformat(captured_at_str).astimezone(local_tz)
        except ValueError:
            continue

        if local_dt.date() < min_date:
            continue

        period = _period_for_local_hour(local_dt.hour)
        if period is None:
            continue

        energy_flow = row.get("energy_flow")
        if not isinstance(energy_flow, dict):
            continue
        pv_kw_raw = energy_flow.get("pvPower")
        if not isinstance(pv_kw_raw, (int, float)):
            continue

        key = (local_dt.date().isoformat(), period)
        grouped.setdefault(key, []).append(float(pv_kw_raw))

    return {key: sum(vals) / len(vals) for key, vals in grouped.items() if vals}


def _forecast_solar_period_estimates(
    readings_rows: list[dict[str, Any]],
    local_tz: ZoneInfo,
    min_date: date,
) -> dict[tuple[str, str], float]:
    """Build latest-before-period-start raw forecast kW per (date, period).

    Treats Forecast.Solar reading timestamp keys as local time (as archived),
    and picks the newest snapshot captured before each period window opens.

    Args:
        readings_rows: Parsed forecast_solar_readings.jsonl records.
        local_tz: Configured local timezone.
        min_date: Earliest local date to include.

    Returns:
        Dict mapping (date_iso, period) to average raw forecast kW
        (before any multiplier is applied).
    """
    # {(date_iso, period): [(captured_at_local, [kw_vals])]}
    candidates: dict[tuple[str, str], list[tuple[datetime, list[float]]]] = {}

    for row in readings_rows:
        captured_at_str = row.get("captured_at_utc") or row.get("captured_at")
        if not isinstance(captured_at_str, str):
            continue
        try:
            captured_at = datetime.fromisoformat(captured_at_str).astimezone(local_tz)
        except ValueError:
            continue

        readings = row.get("readings")
        if not isinstance(readings, dict):
            continue

        snap_grouped: dict[tuple[str, str], list[float]] = {}
        for ts_str, watts in readings.items():
            if not isinstance(watts, (int, float)):
                continue
            try:
                naive = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
                local_dt = naive.replace(tzinfo=local_tz)
            except ValueError:
                continue

            if local_dt.date() < min_date:
                continue

            period = _period_for_local_hour(local_dt.hour)
            if period is None:
                continue

            key = (local_dt.date().isoformat(), period)
            snap_grouped.setdefault(key, []).append(float(watts) / 1000.0)

        for (date_iso, period), kw_vals in snap_grouped.items():
            candidates.setdefault((date_iso, period), []).append((captured_at, kw_vals))

    result: dict[tuple[str, str], float] = {}
    for (date_iso, period), snapshot_list in candidates.items():
        target_date = datetime.fromisoformat(date_iso).date()
        start_hour, _ = PERIOD_WINDOWS[period]
        period_start = datetime(
            target_date.year, target_date.month, target_date.day,
            start_hour, tzinfo=local_tz,
        )

        best_captured_at: datetime | None = None
        best_kw_vals: list[float] | None = None
        for captured_at, kw_vals in snapshot_list:
            if captured_at > period_start:
                continue
            if best_captured_at is None or captured_at > best_captured_at:
                best_captured_at = captured_at
                best_kw_vals = kw_vals

        if best_kw_vals:
            result[(date_iso, period)] = sum(best_kw_vals) / len(best_kw_vals)

    return result


def _telemetry_embedded_forecasts(
    telemetry_rows: list[dict[str, Any]],
    local_tz: ZoneInfo,
    min_date: date,
) -> dict[tuple[str, str], list[float]]:
    """Extract per-tick forecast kW values from the ``forecast_today`` field.

    Each telemetry row may contain a ``forecast_today`` dict with the site-level
    forecast that was active at the time of that tick. These values are used by
    the calibration system and represent a reliable source for any provider,
    avoiding the issue where ESB comparison-archive values are synthetic
    categorical outputs (100 W / 300 W) rather than site-level power estimates.

    Returns a list of observed forecast kW values per (date, period) to allow
    median-based ratio computation against per-tick actual solar output.

    Args:
        telemetry_rows: Parsed inverter_telemetry.jsonl records.
        local_tz: Configured local timezone.
        min_date: Earliest local date to include.

    Returns:
        Dict mapping (date_iso, period) to list of forecast kW values.
    """
    result: dict[tuple[str, str], list[float]] = {}
    for row in telemetry_rows:
        captured_at_str = row.get("captured_at")
        if not isinstance(captured_at_str, str):
            continue
        try:
            local_dt = datetime.fromisoformat(captured_at_str).astimezone(local_tz)
        except ValueError:
            continue

        if local_dt.date() < min_date:
            continue

        period = _period_for_local_hour(local_dt.hour)
        if period is None:
            continue

        forecast_today = row.get("forecast_today")
        if not isinstance(forecast_today, dict):
            continue

        period_entry = forecast_today.get(period)
        if not isinstance(period_entry, (list, tuple)) or len(period_entry) < 1:
            continue

        try:
            forecast_kw = float(period_entry[0]) / 1000.0
        except (TypeError, ValueError):
            continue

        if forecast_kw <= 0:
            continue

        key = (local_dt.date().isoformat(), period)
        result.setdefault(key, []).append(forecast_kw)

    return result


def _comparison_period_forecasts(
    comparison_rows: list[dict[str, Any]],
    provider_name: str,
    local_tz: ZoneInfo,
    min_date: date,
) -> dict[tuple[str, str], float]:
    """Build latest-before-period-start forecast kW for the given provider.

    Scans the primary/secondary/tertiary slots in each comparison row for the
    matching provider name and picks the newest snapshot taken before each
    period window opens.

    Note: ESB comparison values are synthetic categoricals (100 W / 300 W), not
    site-level power estimates. Use ``_telemetry_embedded_forecasts`` for esb_api.

    Args:
        comparison_rows: Parsed forecast_comparisons.jsonl records.
        provider_name: Provider identifier (e.g. 'quartz').
        local_tz: Configured local timezone.
        min_date: Earliest local date to include.

    Returns:
        Dict mapping (date_iso, period) to forecast kW.
    """
    # {(date_iso, period): (captured_at_local, kw)} — keep only best
    best: dict[tuple[str, str], tuple[datetime, float]] = {}

    for row in comparison_rows:
        captured_at_str = row.get("captured_at")
        if not isinstance(captured_at_str, str):
            continue
        try:
            captured_at = datetime.fromisoformat(captured_at_str).astimezone(local_tz)
        except ValueError:
            continue

        # Find the slot whose provider label matches
        slot_name: str | None = None
        for candidate_slot in ("primary", "secondary", "tertiary"):
            if row.get(f"{candidate_slot}_provider") == provider_name:
                slot_name = candidate_slot
                break
        if slot_name is None:
            continue

        for day_offset, bucket_key in ((0, "today"), (1, "tomorrow")):
            target_date = captured_at.date() + timedelta(days=day_offset)
            if target_date < min_date:
                continue

            periods_data = (row.get(bucket_key) or {}).get("periods") or {}
            for period, period_entry in periods_data.items():
                if period not in PERIOD_WINDOWS or not isinstance(period_entry, dict):
                    continue

                slot_payload = period_entry.get(slot_name)
                if not isinstance(slot_payload, dict):
                    continue
                value_w = slot_payload.get("value_w")
                if not isinstance(value_w, (int, float)) or float(value_w) <= 0:
                    continue

                start_hour, _ = PERIOD_WINDOWS[period]
                period_start = datetime(
                    target_date.year, target_date.month, target_date.day,
                    start_hour, tzinfo=local_tz,
                )
                if captured_at > period_start:
                    continue

                key = (target_date.isoformat(), period)
                forecast_kw = float(value_w) / 1000.0
                prev = best.get(key)
                if prev is None or captured_at > prev[0]:
                    best[key] = (captured_at, forecast_kw)

    return {k: v[1] for k, v in best.items()}


def _compute_ratios(
    actuals: dict[tuple[str, str], float],
    forecasts: dict[tuple[str, str], float],
    min_forecast_kw: float = 0.05,
) -> dict[str, PeriodStats]:
    """Compute actual/forecast ratio observations grouped by period.

    Each entry in ``actuals`` is a period-averaged kW. Entries in ``forecasts``
    are matched by (date_iso, period) key. If no match, the period is skipped.

    Args:
        actuals: (date_iso, period) -> actual avg kW from telemetry.
        forecasts: (date_iso, period) -> forecast avg kW.
        min_forecast_kw: Skip entries where forecast is below this threshold.

    Returns:
        Dict mapping period label to PeriodStats with observed ratios.
    """
    stats: dict[str, PeriodStats] = {p: PeriodStats() for p in PERIOD_ORDER}

    for key, actual_kw in actuals.items():
        date_iso, period = key
        if period not in stats:
            continue
        forecast_kw = forecasts.get(key)
        if forecast_kw is None or forecast_kw < min_forecast_kw:
            continue

        ratio = actual_kw / forecast_kw
        stats[period].ratios.append(ratio)
        stats[period].dates.append(date_iso)

    return stats


def _compute_ratios_from_tick_lists(
    actuals: dict[tuple[str, str], float],
    forecast_lists: dict[tuple[str, str], list[float]],
    min_forecast_kw: float = 0.05,
) -> dict[str, PeriodStats]:
    """Compute actual/forecast ratios when forecasts are per-tick lists.

    Used for the ``esb_api`` path where the embedded ``forecast_today`` provides
    one forecast value per scheduler tick rather than a single period estimate.
    The median of each tick list is used as the period-level forecast value.

    Args:
        actuals: (date_iso, period) -> actual avg kW from telemetry.
        forecast_lists: (date_iso, period) -> list of forecast kW values.
        min_forecast_kw: Skip entries where median forecast is below this.

    Returns:
        Dict mapping period label to PeriodStats with observed ratios.
    """
    period_medians: dict[tuple[str, str], float] = {
        key: median(vals) for key, vals in forecast_lists.items() if vals
    }
    return _compute_ratios(actuals, period_medians, min_forecast_kw)


def _load_calibration(root: Path) -> dict[str, Any]:
    """Load current forecast_calibration.json, returning empty dict on failure.

    Args:
        root: Project root directory.

    Returns:
        Parsed calibration data or an empty dict.
    """
    path = root / FORECAST_CALIBRATION_PATH
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _print_ratio_table(stats: dict[str, PeriodStats]) -> None:
    """Print a compact per-period ratio summary table.

    Args:
        stats: Period -> PeriodStats, as returned by _compute_ratios.
    """
    print(f"  {'Period':<6}  {'n':>4}  {'median':>7}  {'mean':>7}  {'stdev':>7}  {'min':>7}  {'max':>7}")
    print(f"  {'-'*6}  {'-'*4}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*7}  {'-'*7}")
    for period in PERIOD_ORDER:
        s = stats[period]
        n = len(s.ratios)
        if n == 0:
            print(f"  {period:<6}  {'0':>4}  {'—':>7}  {'—':>7}  {'—':>7}  {'—':>7}  {'—':>7}")
            continue
        med = median(s.ratios)
        avg = mean(s.ratios)
        sd = stdev(s.ratios) if n > 1 else 0.0
        print(
            f"  {period:<6}  {n:>4}  {med:>7.3f}  {avg:>7.3f}  {sd:>7.3f}"
            f"  {min(s.ratios):>7.3f}  {max(s.ratios):>7.3f}"
        )


def _report_forecast_solar(
    stats: dict[str, PeriodStats],
    window_days: int,
) -> None:
    """Print multiplier recommendation for the forecast_solar provider.

    Args:
        stats: Per-period ratio stats.
        window_days: Analysis window size in days.
    """
    print(f"\n=== Forecast.Solar Multiplier Analysis (last {window_days} days) ===")
    print(f"Current FORECAST_SOLAR_POWER_MULTIPLIER = {FORECAST_SOLAR_POWER_MULTIPLIER:.3f}")
    print(f"Ratios are: actual_kW / raw_forecast_kW (before any multiplier)")
    print()
    _print_ratio_table(stats)

    all_ratios = [r for s in stats.values() for r in s.ratios]
    if not all_ratios:
        print("\n  No overlapping forecast+telemetry data found. Collect more data and retry.")
        return

    global_median = median(all_ratios)
    global_mean = mean(all_ratios)

    print()
    print(f"  Global across all periods: n={len(all_ratios)}, median={global_median:.3f}, mean={global_mean:.3f}")
    print()

    # Determine recommendation
    well_sampled_periods = [p for p in PERIOD_ORDER if len(stats[p].ratios) >= MIN_SAMPLES_FOR_RECOMMENDATION]
    if len(well_sampled_periods) >= 2:
        # Use median of per-period medians (equal weight per period)
        period_medians = [median(stats[p].ratios) for p in well_sampled_periods]
        recommended = round(median(period_medians), 2)
        basis = f"median of period medians ({', '.join(f'{p}={median(stats[p].ratios):.3f}' for p in well_sampled_periods)})"
    elif all_ratios:
        recommended = round(global_median, 2)
        basis = f"global median (limited per-period samples)"
    else:
        print("  Insufficient data for a recommendation.")
        return

    print(f"  Recommended FORECAST_SOLAR_POWER_MULTIPLIER = {recommended:.2f}  [{basis}]")
    print()

    delta = recommended - FORECAST_SOLAR_POWER_MULTIPLIER
    if abs(delta) < 0.03:
        print("  Current setting is already well-calibrated. No change needed.")
    elif delta > 0:
        print(f"  Action: Raise FORECAST_SOLAR_POWER_MULTIPLIER from {FORECAST_SOLAR_POWER_MULTIPLIER:.2f} → {recommended:.2f}")
        print(f"  (Forecast is consistently under-predicting actual output by ~{(recommended - 1) * 100:.0f}%)")
    else:
        print(f"  Action: Lower FORECAST_SOLAR_POWER_MULTIPLIER from {FORECAST_SOLAR_POWER_MULTIPLIER:.2f} → {recommended:.2f}")
        print(f"  (Forecast is consistently over-predicting actual output)")
    print()
    print(f"  In config/settings.py:")
    print(f"    FORECAST_SOLAR_POWER_MULTIPLIER = {recommended:.2f}")


def _report_comparison_provider(
    provider: str,
    stats: dict[str, PeriodStats],
    calibration: dict[str, Any],
    window_days: int,
) -> None:
    """Print per-period multiplier recommendations for esb_api or quartz.

    Args:
        provider: FORECAST_PROVIDER identifier.
        stats: Per-period ratio stats.
        calibration: Current forecast_calibration.json content.
        window_days: Analysis window size in days.
    """
    print(f"\n=== {provider} Per-Period Multiplier Analysis (last {window_days} days) ===")
    print(f"Ratios are: actual_kW / forecast_kW (before calibration multiplier)")
    print()
    _print_ratio_table(stats)

    all_ratios = [r for s in stats.values() for r in s.ratios]
    if not all_ratios:
        print("\n  No overlapping forecast+telemetry data found.")
        return

    print()
    print("  Per-period recommendations vs current calibration:")
    print(f"  {'Period':<6}  {'Current':>8}  {'Suggested':>9}  {'n':>4}  {'Action'}")
    print(f"  {'-'*6}  {'-'*8}  {'-'*9}  {'-'*4}  {'-'*30}")

    cal_periods = (calibration.get("periods") or {})
    any_recommendation = False
    for period in PERIOD_ORDER:
        s = stats[period]
        n = len(s.ratios)
        current_val = (cal_periods.get(period) or {}).get("power_multiplier", 1.0)

        if n < MIN_SAMPLES_FOR_RECOMMENDATION:
            note = f"insufficient data (n={n})"
            print(f"  {period:<6}  {current_val:>8.3f}  {'—':>9}  {n:>4}  {note}")
            continue

        suggested = round(median(s.ratios), 3)
        delta = suggested - current_val
        if abs(delta) < 0.03:
            note = "well-calibrated"
        elif delta > 0:
            note = f"↑ raise (under-predicting)"
            any_recommendation = True
        else:
            note = f"↓ lower (over-predicting)"
            any_recommendation = True
        print(f"  {period:<6}  {current_val:>8.3f}  {suggested:>9.3f}  {n:>4}  {note}")

    if any_recommendation:
        print()
        print("  Note: The scheduler auto-calibrates forecast_calibration.json daily.")
        print("  These suggestions reflect raw forecast accuracy before any calibration.")
        print("  Check data/forecast_calibration.json for live calibrated values.")
    else:
        print()
        print("  Current calibration is well-aligned with recent actuals.")


def main() -> None:
    """Run the appropriate multiplier analysis based on FORECAST_PROVIDER.

    Loads forecast and telemetry data, computes ratios by period, and prints
    a clear recommendation for updating the multiplier configuration.
    """
    args = _parse_args()
    window_days: int = args.days
    local_tz = ZoneInfo(LOCAL_TIMEZONE)
    min_date = datetime.now(local_tz).date() - timedelta(days=window_days)

    print(f"Provider: {FORECAST_PROVIDER}")
    print(f"Window:   last {window_days} days (from {min_date.isoformat()})")

    telemetry_rows = _load_jsonl(_ROOT / INVERTER_TELEMETRY_ARCHIVE_PATH)
    if not telemetry_rows:
        raise SystemExit(f"No telemetry found at {INVERTER_TELEMETRY_ARCHIVE_PATH}")

    actuals = _build_telemetry_actuals(telemetry_rows, local_tz, min_date)
    print(f"Telemetry: {len(telemetry_rows)} records → {len(actuals)} (date, period) actuals")

    if FORECAST_PROVIDER == "forecast_solar":
        readings_rows = _load_jsonl(_ROOT / FORECAST_SOLAR_ARCHIVE_PATH)
        if not readings_rows:
            raise SystemExit(f"No Forecast.Solar readings found at {FORECAST_SOLAR_ARCHIVE_PATH}")
        print(f"Forecast.Solar: {len(readings_rows)} snapshots")

        forecasts = _forecast_solar_period_estimates(readings_rows, local_tz, min_date)
        print(f"Forecast estimates: {len(forecasts)} (date, period) entries")

        stats = _compute_ratios(actuals, forecasts)
        _report_forecast_solar(stats, window_days)

    elif FORECAST_PROVIDER == "esb_api":
        # ESB comparison archive stores synthetic categorical values (100 W / 300 W),
        # not site-level power. Use the per-tick forecast_today embedded in telemetry.
        forecast_lists = _telemetry_embedded_forecasts(telemetry_rows, local_tz, min_date)
        print(f"Embedded forecast_today ticks: {sum(len(v) for v in forecast_lists.values())} samples")

        stats = _compute_ratios_from_tick_lists(actuals, forecast_lists)
        calibration = _load_calibration(_ROOT)
        _report_comparison_provider(FORECAST_PROVIDER, stats, calibration, window_days)

    elif FORECAST_PROVIDER == "quartz":
        comparison_rows = _load_jsonl(_ROOT / FORECAST_COMPARISON_ARCHIVE_PATH)
        if not comparison_rows:
            raise SystemExit(f"No comparison data found at {FORECAST_COMPARISON_ARCHIVE_PATH}")
        print(f"Comparison archive: {len(comparison_rows)} records")

        forecasts = _comparison_period_forecasts(comparison_rows, FORECAST_PROVIDER, local_tz, min_date)
        print(f"Forecast estimates: {len(forecasts)} (date, period) entries")

        stats = _compute_ratios(actuals, forecasts)
        calibration = _load_calibration(_ROOT)
        _report_comparison_provider(FORECAST_PROVIDER, stats, calibration, window_days)

    else:
        raise SystemExit(f"Unknown FORECAST_PROVIDER={FORECAST_PROVIDER!r}. Expected: esb_api, forecast_solar, quartz")


if __name__ == "__main__":
    main()

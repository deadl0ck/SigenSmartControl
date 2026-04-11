"""scripts/forecast_solar_vs_pvdaynrg.py
-----------------------------------------
Compare Forecast.Solar hourly averages against inverter hourly generation.

This script derives actual hourly generation from the inverter telemetry archive by
taking positive `pvDayNrg` deltas within each local-hour bucket. It compares those
actuals against the average Forecast.Solar snapshots captured in the same hour.

Run from the project root:
    python scripts/forecast_solar_vs_pvdaynrg.py
    python scripts/forecast_solar_vs_pvdaynrg.py --date 2026-04-10
    python scripts/forecast_solar_vs_pvdaynrg.py --all

For each daylight hour, prints:
    - Forecast.Solar average power for that hour (kW)
    - Forecast-equivalent hourly energy (kWh)
    - Inverter hourly energy from `pvDayNrg` deltas (kWh)
    - Difference between inverter and forecast (kWh)
    - Forecast snapshot count and telemetry delta count

Sunrise and sunset are fetched from the same public API used by the scheduler. If
that lookup fails, the script falls back to the earliest and latest non-zero
Forecast.Solar points recorded for the day.
"""

import argparse
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

# Allow running from project root or from scripts/ sub-directory.
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from config.constants import (
    FORECAST_SOLAR_ARCHIVE_PATH,
    INVERTER_TELEMETRY_ARCHIVE_PATH,
    LATITUDE,
    LONGITUDE,
)
from config.settings import LOCAL_TIMEZONE
from weather.sunrise_sunset import get_sunrise_sunset


@dataclass(frozen=True)
class HourlyComparisonRow:
    """Container for one local-hour comparison row.

    Args:
        hour_start: Start of the local-hour bucket.
        forecast_avg_kw: Average Forecast.Solar power captured during the hour.
        forecast_samples: Number of Forecast.Solar snapshots used.
        actual_kwh: Inverter hourly energy derived from `pvDayNrg` deltas.
        actual_deltas: Number of positive deltas contributing to `actual_kwh`.
    """

    hour_start: datetime
    forecast_avg_kw: float | None
    forecast_samples: int
    actual_kwh: float
    actual_deltas: int


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed argparse namespace.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Compare Forecast.Solar hourly averages against inverter hourly "
            "generation derived from pvDayNrg."
        )
    )
    parser.add_argument(
        "--date",
        dest="date_text",
        help="Local date to analyse in YYYY-MM-DD format. Defaults to latest common date.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Print all dates that have both telemetry and Forecast.Solar data.",
    )
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL archive file.

    Args:
        path: Path to a JSONL file.

    Returns:
        Parsed JSON objects. Returns an empty list when the file does not exist.
    """
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def parse_iso_datetime(value: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime string.

    Args:
        value: Timestamp string.

    Returns:
        Parsed datetime, or None when parsing fails.
    """
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_forecast_local_point(value: str, local_tz: ZoneInfo) -> datetime | None:
    """Parse a Forecast.Solar reading key as a local datetime.

    Args:
        value: Reading key in ``YYYY-MM-DD HH:MM:SS`` format.
        local_tz: Configured local timezone.

    Returns:
        Timezone-aware local datetime, or None when parsing fails.
    """
    try:
        naive = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return naive.replace(tzinfo=local_tz)


def available_telemetry_dates(
    telemetry_records: list[dict[str, Any]],
    local_tz: ZoneInfo,
) -> set[date]:
    """Return local dates that have telemetry records with `pvDayNrg`.

    Args:
        telemetry_records: Parsed telemetry archive rows.
        local_tz: Configured local timezone.

    Returns:
        Set of local dates present in telemetry.
    """
    dates: set[date] = set()
    for record in telemetry_records:
        captured_at = parse_iso_datetime(record.get("captured_at"))
        energy_flow = record.get("energy_flow")
        pv_day_nrg = energy_flow.get("pvDayNrg") if isinstance(energy_flow, dict) else None
        if captured_at is None or not isinstance(pv_day_nrg, (int, float)):
            continue
        dates.add(captured_at.astimezone(local_tz).date())
    return dates


def available_forecast_dates(
    forecast_records: list[dict[str, Any]],
    local_tz: ZoneInfo,
) -> set[date]:
    """Return local dates covered by Forecast.Solar reading points.

    Args:
        forecast_records: Parsed Forecast.Solar archive rows.
        local_tz: Configured local timezone.

    Returns:
        Set of local dates present in reading keys.
    """
    dates: set[date] = set()
    for record in forecast_records:
        readings = record.get("readings")
        if not isinstance(readings, dict):
            continue
        for key in readings:
            point_dt = parse_forecast_local_point(key, local_tz)
            if point_dt is not None:
                dates.add(point_dt.date())
    return dates


def default_target_dates(
    telemetry_records: list[dict[str, Any]],
    forecast_records: list[dict[str, Any]],
    local_tz: ZoneInfo,
) -> list[date]:
    """Determine the default date selection.

    Args:
        telemetry_records: Parsed telemetry archive rows.
        forecast_records: Parsed Forecast.Solar archive rows.
        local_tz: Configured local timezone.

    Returns:
        Sorted common dates present in both sources.
    """
    telemetry_dates = available_telemetry_dates(telemetry_records, local_tz)
    forecast_dates = available_forecast_dates(forecast_records, local_tz)
    return sorted(telemetry_dates & forecast_dates)


def infer_daylight_bounds_from_forecast(
    target_date: date,
    forecast_records: list[dict[str, Any]],
    local_tz: ZoneInfo,
) -> tuple[datetime, datetime]:
    """Infer daylight bounds from non-zero Forecast.Solar points.

    Args:
        target_date: Local date being analysed.
        forecast_records: Parsed Forecast.Solar archive rows.
        local_tz: Configured local timezone.

    Returns:
        Tuple of local sunrise-like and sunset-like datetimes.

    Raises:
        RuntimeError: If no forecast points can be found for the target date.
    """
    points: list[datetime] = []
    for record in forecast_records:
        readings = record.get("readings")
        if not isinstance(readings, dict):
            continue
        for key, value in readings.items():
            point_dt = parse_forecast_local_point(key, local_tz)
            if point_dt is None or point_dt.date() != target_date:
                continue
            if isinstance(value, (int, float)) and value > 0:
                points.append(point_dt)
    if not points:
        raise RuntimeError(f"No Forecast.Solar daylight points found for {target_date.isoformat()}")
    return min(points), max(points)


def resolve_daylight_bounds(
    target_date: date,
    forecast_records: list[dict[str, Any]],
    local_tz: ZoneInfo,
) -> tuple[datetime, datetime, str]:
    """Resolve daylight bounds for the target date.

    Args:
        target_date: Local date being analysed.
        forecast_records: Parsed Forecast.Solar archive rows.
        local_tz: Configured local timezone.

    Returns:
        Tuple of (sunrise_local, sunset_local, source_description).
    """
    try:
        sunrise_text, sunset_text = get_sunrise_sunset(
            LATITUDE,
            LONGITUDE,
            target_date.isoformat(),
        )
        sunrise_local = datetime.fromisoformat(sunrise_text).astimezone(local_tz)
        sunset_local = datetime.fromisoformat(sunset_text).astimezone(local_tz)
        return sunrise_local, sunset_local, "sunrise-sunset API"
    except Exception:
        sunrise_local, sunset_local = infer_daylight_bounds_from_forecast(
            target_date,
            forecast_records,
            local_tz,
        )
        return sunrise_local, sunset_local, "Forecast.Solar fallback"


def hour_bucket_starts(daylight_start: datetime, daylight_end: datetime) -> list[datetime]:
    """Return local-hour bucket starts that overlap the daylight window.

    Args:
        daylight_start: Local sunrise datetime.
        daylight_end: Local sunset datetime.

    Returns:
        Ordered list of local bucket-start datetimes.
    """
    current = daylight_start.replace(minute=0, second=0, microsecond=0)
    final_bucket = daylight_end.replace(minute=0, second=0, microsecond=0)
    buckets: list[datetime] = []
    while current <= final_bucket:
        bucket_end = current + timedelta(hours=1)
        if bucket_end > daylight_start and current < daylight_end:
            buckets.append(current)
        current += timedelta(hours=1)
    return buckets


def build_inverter_hourly_generation(
    telemetry_records: list[dict[str, Any]],
    target_date: date,
    daylight_start: datetime,
    daylight_end: datetime,
    local_tz: ZoneInfo,
) -> tuple[dict[int, float], dict[int, int]]:
    """Aggregate inverter hourly energy from `pvDayNrg` deltas.

    Args:
        telemetry_records: Parsed telemetry archive rows.
        target_date: Local date being analysed.
        daylight_start: Local sunrise datetime.
        daylight_end: Local sunset datetime.
        local_tz: Configured local timezone.

    Returns:
        Tuple of dictionaries keyed by local hour:
            - total hourly kWh from positive `pvDayNrg` deltas
            - positive delta count contributing to the hour
    """
    samples: list[tuple[datetime, float]] = []
    for record in telemetry_records:
        captured_at = parse_iso_datetime(record.get("captured_at"))
        energy_flow = record.get("energy_flow") or {}
        pv_day_nrg = energy_flow.get("pvDayNrg") if isinstance(energy_flow, dict) else None
        if captured_at is None or not isinstance(pv_day_nrg, (int, float)):
            continue
        local_dt = captured_at.astimezone(local_tz)
        if local_dt.date() != target_date:
            continue
        samples.append((local_dt, float(pv_day_nrg)))

    samples.sort(key=lambda item: item[0])
    by_hour_kwh: dict[int, float] = defaultdict(float)
    by_hour_deltas: dict[int, int] = defaultdict(int)

    previous_value: float | None = None
    for local_dt, pv_day_nrg in samples:
        if previous_value is None:
            previous_value = pv_day_nrg
            continue

        delta_kwh = pv_day_nrg - previous_value
        previous_value = pv_day_nrg

        if delta_kwh <= 0:
            continue
        if local_dt < daylight_start or local_dt > daylight_end:
            continue

        by_hour_kwh[local_dt.hour] += delta_kwh
        by_hour_deltas[local_dt.hour] += 1

    return dict(by_hour_kwh), dict(by_hour_deltas)


def build_forecast_hourly_averages(
    forecast_records: list[dict[str, Any]],
    target_date: date,
    target_hours: set[int],
    local_tz: ZoneInfo,
) -> tuple[dict[int, float], dict[int, int]]:
    """Average Forecast.Solar snapshots captured within each local hour.

    Args:
        forecast_records: Parsed Forecast.Solar archive rows.
        target_date: Local date being analysed.
        target_hours: Local hours that should appear in the output.
        local_tz: Configured local timezone.

    Returns:
        Tuple of dictionaries keyed by local hour:
            - average forecast power in kW
            - number of Forecast.Solar snapshots used for that hour
    """
    per_hour_values_w: dict[int, list[float]] = defaultdict(list)

    for record in forecast_records:
        captured_at_utc = parse_iso_datetime(record.get("captured_at_utc"))
        readings = record.get("readings")
        if captured_at_utc is None or not isinstance(readings, dict):
            continue

        captured_local = captured_at_utc.astimezone(local_tz)
        if captured_local.date() != target_date or captured_local.hour not in target_hours:
            continue

        target_hour = captured_local.hour
        row_values: list[float] = []
        for reading_key, reading_value in readings.items():
            point_dt = parse_forecast_local_point(reading_key, local_tz)
            if point_dt is None or point_dt.date() != target_date or point_dt.hour != target_hour:
                continue
            if isinstance(reading_value, (int, float)):
                row_values.append(float(reading_value))

        if row_values:
            per_hour_values_w[target_hour].append(sum(row_values) / len(row_values))

    averages_kw = {
        hour: (sum(values_w) / len(values_w)) / 1000.0
        for hour, values_w in per_hour_values_w.items()
        if values_w
    }
    sample_counts = {hour: len(values_w) for hour, values_w in per_hour_values_w.items()}
    return averages_kw, sample_counts


def build_hourly_rows(
    telemetry_records: list[dict[str, Any]],
    forecast_records: list[dict[str, Any]],
    target_date: date,
    local_tz: ZoneInfo,
) -> tuple[list[HourlyComparisonRow], datetime, datetime, str]:
    """Build ordered hourly comparison rows for one date.

    Args:
        telemetry_records: Parsed telemetry archive rows.
        forecast_records: Parsed Forecast.Solar archive rows.
        target_date: Local date being analysed.
        local_tz: Configured local timezone.

    Returns:
        Tuple of comparison rows, sunrise, sunset, and daylight-bound source.
    """
    sunrise_local, sunset_local, daylight_source = resolve_daylight_bounds(
        target_date,
        forecast_records,
        local_tz,
    )
    bucket_starts = hour_bucket_starts(sunrise_local, sunset_local)
    target_hours = {bucket.hour for bucket in bucket_starts}

    actual_kwh_by_hour, actual_delta_counts = build_inverter_hourly_generation(
        telemetry_records,
        target_date,
        sunrise_local,
        sunset_local,
        local_tz,
    )
    forecast_avg_kw_by_hour, forecast_sample_counts = build_forecast_hourly_averages(
        forecast_records,
        target_date,
        target_hours,
        local_tz,
    )

    rows = [
        HourlyComparisonRow(
            hour_start=bucket_start,
            forecast_avg_kw=forecast_avg_kw_by_hour.get(bucket_start.hour),
            forecast_samples=forecast_sample_counts.get(bucket_start.hour, 0),
            actual_kwh=round(actual_kwh_by_hour.get(bucket_start.hour, 0.0), 3),
            actual_deltas=actual_delta_counts.get(bucket_start.hour, 0),
        )
        for bucket_start in bucket_starts
    ]
    return rows, sunrise_local, sunset_local, daylight_source


def print_report_for_date(
    telemetry_records: list[dict[str, Any]],
    forecast_records: list[dict[str, Any]],
    target_date: date,
    local_tz: ZoneInfo,
) -> None:
    """Print the hourly comparison report for one date.

    Args:
        telemetry_records: Parsed telemetry archive rows.
        forecast_records: Parsed Forecast.Solar archive rows.
        target_date: Local date being analysed.
        local_tz: Configured local timezone.
    """
    rows, sunrise_local, sunset_local, daylight_source = build_hourly_rows(
        telemetry_records,
        forecast_records,
        target_date,
        local_tz,
    )

    print(f"Date: {target_date.isoformat()}")
    print(
        "Daylight: "
        f"{sunrise_local.strftime('%H:%M')} -> {sunset_local.strftime('%H:%M')} "
        f"({daylight_source})"
    )
    print(
        f"  {'Hour':<11}  {'FS Avg kW':>9}  {'FS Eqv kWh':>10}  {'Actual kWh':>10}  "
        f"{'Act-FS':>8}  {'FS n':>4}  {'deltas':>6}"
    )
    print("  " + "-" * 73)

    total_forecast_kwh = 0.0
    total_actual_kwh = 0.0
    total_forecast_samples = 0
    total_actual_deltas = 0

    for row in rows:
        forecast_kwh = row.forecast_avg_kw if row.forecast_avg_kw is not None else None
        delta_kwh = (
            row.actual_kwh - forecast_kwh
            if forecast_kwh is not None
            else None
        )

        total_forecast_kwh += forecast_kwh or 0.0
        total_actual_kwh += row.actual_kwh
        total_forecast_samples += row.forecast_samples
        total_actual_deltas += row.actual_deltas

        hour_label = (
            f"{row.hour_start.strftime('%H:%M')}"
            f"-{(row.hour_start + timedelta(hours=1)).strftime('%H:%M')}"
        )
        forecast_avg_text = (
            f"{row.forecast_avg_kw:>9.3f}"
            if row.forecast_avg_kw is not None
            else f"{'N/A':>9}"
        )
        forecast_kwh_text = (
            f"{forecast_kwh:>10.3f}"
            if forecast_kwh is not None
            else f"{'N/A':>10}"
        )
        delta_text = f"{delta_kwh:>8.3f}" if delta_kwh is not None else f"{'N/A':>8}"

        print(
            f"  {hour_label:<11}  {forecast_avg_text}  {forecast_kwh_text}  "
            f"{row.actual_kwh:>10.3f}  {delta_text}  {row.forecast_samples:>4}  "
            f"{row.actual_deltas:>6}"
        )

    print("  " + "-" * 73)
    print(
        f"  {'Total':<11}  {'':>9}  {total_forecast_kwh:>10.3f}  {total_actual_kwh:>10.3f}  "
        f"{(total_actual_kwh - total_forecast_kwh):>8.3f}  {total_forecast_samples:>4}  "
        f"{total_actual_deltas:>6}"
    )
    print()


def main() -> int:
    """Run the script entry point.

    Returns:
        Process exit code.
    """
    args = parse_args()
    local_tz = ZoneInfo(LOCAL_TIMEZONE)
    telemetry_records = load_jsonl(_ROOT / INVERTER_TELEMETRY_ARCHIVE_PATH)
    forecast_records = load_jsonl(_ROOT / FORECAST_SOLAR_ARCHIVE_PATH)

    if not telemetry_records:
        print("No inverter telemetry archive found.", file=sys.stderr)
        return 1
    if not forecast_records:
        print("No Forecast.Solar archive found.", file=sys.stderr)
        return 1

    common_dates = default_target_dates(telemetry_records, forecast_records, local_tz)
    if not common_dates:
        print("No common dates found in telemetry and Forecast.Solar archives.", file=sys.stderr)
        return 1

    if args.all:
        target_dates = common_dates
    elif args.date_text:
        try:
            requested_date = datetime.strptime(args.date_text, "%Y-%m-%d").date()
        except ValueError:
            print("--date must be in YYYY-MM-DD format.", file=sys.stderr)
            return 1
        if requested_date not in common_dates:
            print(
                f"No common telemetry/forecast data found for {requested_date.isoformat()}.",
                file=sys.stderr,
            )
            return 1
        target_dates = [requested_date]
    else:
        target_dates = [common_dates[-1]]

    for target_date in target_dates:
        print_report_for_date(telemetry_records, forecast_records, target_date, local_tz)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
"""Compare GREEN-GRID forecast accuracy against inverter telemetry.

This script evaluates GREEN-GRID hourly solar power forecasts against
actual inverter PV production. It aggregates GREEN-GRID's hourly values
into project periods (Morn/Aftn/Eve) and computes:
- Value MAE/MAPE versus actual average watts
- Suggested GREEN-GRID multiplier from observed bias (if needed)

GREEN-GRID forecasts are stored in data/greengrid_forecasts.jsonl
as captured from automated form submissions to the Shiny app.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from pathlib import Path
from statistics import median
import sys
from typing import Any
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import (
    LOCAL_TIMEZONE,
    QUARTZ_GREEN_CAPACITY_FRACTION,
    QUARTZ_RED_CAPACITY_FRACTION,
    SOLAR_PV_KW,
)


PERIOD_HOURS: dict[str, tuple[int, int]] = {
    "Morn": (7, 12),
    "Aftn": (12, 16),
    "Eve": (16, 20),
}


@dataclass
class ActualPeriod:
    """Actual observed period aggregates derived from telemetry."""

    avg_watts: float
    status: str
    samples: int


@dataclass
class GreenGridPeriodForecast:
    """GREEN-GRID forecast aggregated to one period."""

    target_day: str
    period: str
    captured_at: datetime
    avg_watts: float
    status: str


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    """Load a JSONL file into a list of dict rows."""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _status_from_actual_watts(avg_watts: float) -> str:
    """Classify actual average watts using configured capacity fractions."""
    avg_kw = avg_watts / 1000.0
    output_fraction = avg_kw / max(SOLAR_PV_KW, 0.1)
    if output_fraction < QUARTZ_RED_CAPACITY_FRACTION:
        return "Red"
    if output_fraction < QUARTZ_GREEN_CAPACITY_FRACTION:
        return "Amber"
    return "Green"


def _period_for_local_hour(hour_local: int) -> str | None:
    """Return project period label for a local hour."""
    for period, (start_hour, end_hour) in PERIOD_HOURS.items():
        if start_hour <= hour_local < end_hour:
            return period
    return None


def _build_actuals(telemetry_rows: list[dict[str, Any]]) -> dict[tuple[str, str], ActualPeriod]:
    """Aggregate telemetry PV power into day/period actuals."""
    grouped: dict[tuple[str, str], list[float]] = {}

    for row in telemetry_rows:
        if not isinstance(row, dict):
            continue
        captured_at = row.get("captured_at")
        if not isinstance(captured_at, str):
            continue

        try:
            local_dt = datetime.fromisoformat(captured_at)
        except ValueError:
            continue

        period = _period_for_local_hour(local_dt.hour)
        if period is None:
            continue

        energy_flow = row.get("energy_flow")
        if not isinstance(energy_flow, dict):
            continue

        pv_power_kw = energy_flow.get("pvPower")
        if not isinstance(pv_power_kw, (int, float)):
            continue

        key = (local_dt.date().isoformat(), period)
        grouped.setdefault(key, []).append(float(pv_power_kw) * 1000.0)

    actuals: dict[tuple[str, str], ActualPeriod] = {}
    for key, values in grouped.items():
        avg_watts = sum(values) / len(values)
        actuals[key] = ActualPeriod(
            avg_watts=avg_watts,
            status=_status_from_actual_watts(avg_watts),
            samples=len(values),
        )
    return actuals


def _build_greengrid_periods(
    forecast_rows: list[dict[str, Any]],
) -> dict[tuple[str, str], GreenGridPeriodForecast]:
    """Aggregate GREEN-GRID hourly forecasts into periods."""
    local_tz = ZoneInfo(LOCAL_TIMEZONE)
    grouped: dict[tuple[str, str], list[float]] = {}
    capture_times: dict[tuple[str, str], datetime] = {}

    for row in forecast_rows:
        if not isinstance(row, dict):
            continue

        captured_at_str = row.get("captured_at")
        if not isinstance(captured_at_str, str):
            continue

        try:
            captured_at = datetime.fromisoformat(captured_at_str)
        except ValueError:
            continue

        forecast_points = row.get("forecast_points")
        if not isinstance(forecast_points, list):
            continue

        for point in forecast_points:
            if not isinstance(point, dict):
                continue

            date_str = point.get("date")
            time_str = point.get("time")
            forecast_kwh = point.get("forecast_kwh")

            if not all(isinstance(x, str) for x in (date_str, time_str)):
                continue
            if not isinstance(forecast_kwh, (int, float)):
                continue

            try:
                # Parse date and time from forecast point
                point_dt_naive = datetime.strptime(
                    f"{date_str} {time_str}", "%Y-%m-%d %H:%M:%S"
                )
                # Assume forecast is in local timezone
                point_dt = point_dt_naive.replace(tzinfo=local_tz)
            except ValueError:
                continue

            period = _period_for_local_hour(point_dt.hour)
            if period is None:
                continue

            # Convert kWh to average watts (assuming point is 1-hour forecast)
            avg_watts = float(forecast_kwh) * 1000.0

            key = (point_dt.date().isoformat(), period)
            grouped.setdefault(key, []).append(avg_watts)
            capture_times.setdefault(key, captured_at)

    result: dict[tuple[str, str], GreenGridPeriodForecast] = {}
    for key, values in grouped.items():
        target_day, period = key
        avg_watts = sum(values) / len(values)
        result[key] = GreenGridPeriodForecast(
            target_day=target_day,
            period=period,
            captured_at=capture_times[key],
            avg_watts=avg_watts,
            status=_status_from_actual_watts(avg_watts),
        )
    return result


def _safe_mean(values: list[float]) -> float:
    """Return mean for non-empty values, otherwise NaN."""
    if not values:
        return float("nan")
    return sum(values) / len(values)


def main() -> None:
    """Run period-level GREEN-GRID accuracy comparison and print summary."""
    root = Path(__file__).resolve().parents[1]
    greengrid_rows = _load_jsonl(root / "data/greengrid_forecasts.jsonl")
    telemetry_rows = _load_jsonl(root / "data/inverter_telemetry.jsonl")

    if not greengrid_rows:
        print("No GREEN-GRID forecasts found in data/greengrid_forecasts.jsonl")
        print("To generate forecasts, use weather/greengrid_forecast.py with Playwright.")
        raise SystemExit(1)

    if not telemetry_rows:
        print("No telemetry found in data/inverter_telemetry.jsonl")
        raise SystemExit(1)

    actuals = _build_actuals(telemetry_rows)
    greengrid_periods = _build_greengrid_periods(greengrid_rows)

    if not greengrid_periods:
        print("No valid GREEN-GRID periods found in forecast data.")
        raise SystemExit(1)

    abs_errors_watts: list[float] = []
    ape_values: list[float] = []
    ratio_actual_over_forecast: list[float] = []
    matches: int = 0
    total: int = 0

    for (target_day, period), greengrid in greengrid_periods.items():
        actual = actuals.get((target_day, period))
        if actual is None:
            continue

        total += 1

        if greengrid.status == actual.status:
            matches += 1

        abs_error = abs(greengrid.avg_watts - actual.avg_watts)
        abs_errors_watts.append(abs_error)

        if actual.avg_watts > 0:
            ape_values.append(abs_error / actual.avg_watts)

        if greengrid.avg_watts > 0:
            ratio_actual_over_forecast.append(actual.avg_watts / greengrid.avg_watts)

    if total == 0:
        print("No matching GREEN-GRID forecasts and actual periods found.")
        raise SystemExit(1)

    status_accuracy = (matches / total * 100.0) if total else float("nan")
    mae_watts = _safe_mean(abs_errors_watts)
    mape = _safe_mean(ape_values) * 100.0
    median_ratio = median(ratio_actual_over_forecast) if ratio_actual_over_forecast else float("nan")
    mean_ratio = _safe_mean(ratio_actual_over_forecast)

    print("GREEN-GRID Forecast Accuracy Analysis")
    print("=" * 70)
    print("Scoring method: avgerage of forecast points within each period (Morn/Aftn/Eve).")
    print("Actual baseline: average inverter pvPower over period samples (telemetry).")
    print()
    print(
        f"GREEN-GRID   "
        f"n={total:3d} "
        f"status_acc={status_accuracy:5.1f}% "
        f"MAE={mae_watts:7.0f}W "
        f"MAPE={mape:5.1f}% "
        f"actual/forecast median={median_ratio:5.2f} mean={mean_ratio:5.2f}"
    )
    print()

    if ratio_actual_over_forecast and median_ratio != 1.0:
        print(
            f"GREEN-GRID multiplier candidates (to scale forecast watts toward actuals): "
            f"median x{median_ratio:.2f}, mean x{mean_ratio:.2f}"
        )
        if median_ratio > 1.2 or median_ratio < 0.8:
            print(f"Note: Significant bias detected. Consider applying multiplier adjustment.")
    else:
        print("GREEN-GRID forecasts show no significant bias.")


if __name__ == "__main__":
    main()

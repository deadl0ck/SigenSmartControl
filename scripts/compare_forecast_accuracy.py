"""Compare forecast provider accuracy against inverter telemetry.

This script evaluates ESB, Forecast.Solar, and Quartz period forecasts against
actual inverter PV production so far. It uses the latest forecast snapshot
captured before each period start (Morn/Aftn/Eve) and computes:
- Status accuracy (Red/Amber/Green)
- Value MAE/MAPE versus actual average watts
- Suggested Forecast.Solar multiplier from observed bias
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
class ForecastPick:
    """One chosen forecast for a provider/day/period."""

    provider: str
    target_day: str
    period: str
    generated_at: datetime
    status: str
    value_watts: float


@dataclass
class ProviderStats:
    """Aggregate error stats for one provider."""

    n: int = 0
    status_matches: int = 0
    abs_errors_watts: list[float] | None = None
    ape_values: list[float] | None = None
    ratio_actual_over_forecast: list[float] | None = None

    def __post_init__(self) -> None:
        self.abs_errors_watts = [] if self.abs_errors_watts is None else self.abs_errors_watts
        self.ape_values = [] if self.ape_values is None else self.ape_values
        self.ratio_actual_over_forecast = (
            [] if self.ratio_actual_over_forecast is None else self.ratio_actual_over_forecast
        )


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


def _iter_forecast_candidates(comparison_rows: list[dict[str, Any]]) -> list[ForecastPick]:
    """Expand comparison rows into provider/day/period forecast candidates."""
    candidates: list[ForecastPick] = []

    for row in comparison_rows:
        if not isinstance(row, dict):
            continue
        captured_at = row.get("captured_at")
        if not isinstance(captured_at, str):
            continue

        try:
            generated_at = datetime.fromisoformat(captured_at)
        except ValueError:
            continue

        generated_day = generated_at.date()
        provider_slots = [
            ("primary", row.get("primary_provider")),
            ("secondary", row.get("secondary_provider")),
            ("tertiary", row.get("tertiary_provider")),
        ]

        for bucket_name, day_offset in (("today", 0), ("tomorrow", 1)):
            target_day = (generated_day + timedelta(days=day_offset)).isoformat()
            periods = ((row.get(bucket_name) or {}).get("periods") or {})

            for period, period_entry in periods.items():
                if period not in PERIOD_HOURS or not isinstance(period_entry, dict):
                    continue

                for slot_name, provider_name in provider_slots:
                    if not isinstance(provider_name, str):
                        continue

                    slot_payload = period_entry.get(slot_name)
                    if not isinstance(slot_payload, dict):
                        continue

                    status = slot_payload.get("status")
                    value_watts = slot_payload.get("value_w")
                    if status not in {"Red", "Amber", "Green"}:
                        continue
                    if not isinstance(value_watts, (int, float)):
                        continue

                    candidates.append(
                        ForecastPick(
                            provider=provider_name,
                            target_day=target_day,
                            period=period,
                            generated_at=generated_at,
                            status=status,
                            value_watts=float(value_watts),
                        )
                    )

    return candidates


def _pick_latest_before_period_start(candidates: list[ForecastPick]) -> dict[tuple[str, str, str], ForecastPick]:
    """Select latest forecast for each provider/day/period before period start."""
    local_tz = ZoneInfo(LOCAL_TIMEZONE)
    chosen: dict[tuple[str, str, str], ForecastPick] = {}

    for candidate in candidates:
        target_date = datetime.fromisoformat(candidate.target_day).date()
        start_hour, _ = PERIOD_HOURS[candidate.period]
        period_start = datetime.combine(target_date, time(start_hour), tzinfo=local_tz)
        if candidate.generated_at > period_start:
            continue

        key = (candidate.provider, candidate.target_day, candidate.period)
        previous = chosen.get(key)
        if previous is None or candidate.generated_at > previous.generated_at:
            chosen[key] = candidate

    return chosen


def _score_providers(
    picks: dict[tuple[str, str, str], ForecastPick],
    actuals: dict[tuple[str, str], ActualPeriod],
) -> dict[str, ProviderStats]:
    """Compute provider-level accuracy stats."""
    stats: dict[str, ProviderStats] = {}

    for (_provider, day, period), pick in picks.items():
        actual = actuals.get((day, period))
        if actual is None:
            continue

        provider_stats = stats.setdefault(pick.provider, ProviderStats())
        provider_stats.n += 1

        if pick.status == actual.status:
            provider_stats.status_matches += 1

        abs_error = abs(pick.value_watts - actual.avg_watts)
        provider_stats.abs_errors_watts.append(abs_error)

        if actual.avg_watts > 0:
            provider_stats.ape_values.append(abs_error / actual.avg_watts)

        if pick.value_watts > 0:
            provider_stats.ratio_actual_over_forecast.append(actual.avg_watts / pick.value_watts)

    return stats


def _safe_mean(values: list[float]) -> float:
    """Return mean for non-empty values, otherwise NaN."""
    if not values:
        return float("nan")
    return sum(values) / len(values)


def main() -> None:
    """Run period-level provider accuracy comparison and print summary."""
    root = Path(__file__).resolve().parents[1]
    comparison_rows = _load_jsonl(root / "data/forecast_comparisons.jsonl")
    telemetry_rows = _load_jsonl(root / "data/inverter_telemetry.jsonl")

    if not comparison_rows:
        raise SystemExit("No forecast comparisons found in data/forecast_comparisons.jsonl")
    if not telemetry_rows:
        raise SystemExit("No telemetry found in data/inverter_telemetry.jsonl")

    actuals = _build_actuals(telemetry_rows)
    candidates = _iter_forecast_candidates(comparison_rows)
    picks = _pick_latest_before_period_start(candidates)
    stats = _score_providers(picks, actuals)

    print("Scoring method: latest forecast captured before each period start (Morn/Aftn/Eve).")
    print("Actual baseline: average inverter pvPower over period samples (telemetry).")
    print()

    for provider in sorted(stats):
        provider_stats = stats[provider]
        n = provider_stats.n
        status_accuracy = (provider_stats.status_matches / n * 100.0) if n else float("nan")
        mae_watts = _safe_mean(provider_stats.abs_errors_watts)
        mape = _safe_mean(provider_stats.ape_values) * 100.0
        median_ratio = median(provider_stats.ratio_actual_over_forecast)
        mean_ratio = _safe_mean(provider_stats.ratio_actual_over_forecast)

        print(
            f"{provider:14} n={n:3d} "
            f"status_acc={status_accuracy:5.1f}% "
            f"MAE={mae_watts:7.0f}W "
            f"MAPE={mape:5.1f}% "
            f"actual/forecast median={median_ratio:5.2f} mean={mean_ratio:5.2f}"
        )

    forecast_solar_stats = stats.get("forecast_solar")
    if forecast_solar_stats and forecast_solar_stats.ratio_actual_over_forecast:
        median_multiplier = median(forecast_solar_stats.ratio_actual_over_forecast)
        mean_multiplier = _safe_mean(forecast_solar_stats.ratio_actual_over_forecast)
        print()
        print(
            "Forecast.Solar multiplier candidates "
            f"(to scale forecast watts toward actuals): median x{median_multiplier:.2f}, "
            f"mean x{mean_multiplier:.2f}"
        )


if __name__ == "__main__":
    main()

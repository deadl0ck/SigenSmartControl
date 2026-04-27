"""Build bounded daily forecast calibration from archived inverter telemetry.

The scheduler keeps its existing rule structure but can consume a small daily
calibration artifact that adjusts numeric inputs conservatively based on recent
telemetry and observed clipping risk.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from config.settings import (
    CALIBRATION_CLIPPING_RATE_WEIGHT,
    CALIBRATION_DEFAULT_EXPORT_LEAD_BUFFER_MULTIPLIER,
    CALIBRATION_DEFAULT_POWER_MULTIPLIER,
    CALIBRATION_MIN_SOLAR_KW,
    CALIBRATION_MULTIPLIER_STEP_MAX,
    CALIBRATION_RATIO_MAX,
    CALIBRATION_RATIO_MIN,
    CALIBRATION_TARGET_LEAD_BUFFER_MAX,
    CALIBRATION_TARGET_MULTIPLIER_EXCESS_WEIGHT,
    CALIBRATION_TARGET_MULTIPLIER_MAX,
    CALIBRATION_TARGET_MULTIPLIER_MIN,
    CALIBRATION_WINDOW_DAYS,
    LOCAL_TIMEZONE,
)
from config.constants import FORECAST_CALIBRATION_PATH, INVERTER_TELEMETRY_ARCHIVE_PATH
from telemetry.telemetry_archive import derive_clipping_metrics


logger = logging.getLogger(__name__)

PERIODS = ("Morn", "Aftn", "Eve")


def _default_period_calibration() -> dict[str, float]:
    """Return baseline calibration values for one daytime period."""
    return {
        "power_multiplier": CALIBRATION_DEFAULT_POWER_MULTIPLIER,
        "export_lead_buffer_multiplier": CALIBRATION_DEFAULT_EXPORT_LEAD_BUFFER_MULTIPLIER,
        "telemetry_samples": 0,
        "ratios_used": 0,
        "clipping_observations": 0,
        "clipping_rate": 0.0,
    }


def default_forecast_calibration() -> dict[str, Any]:
    """Return the baseline calibration artifact structure."""
    return {
        "generated_at": None,
        "window_days": CALIBRATION_WINDOW_DAYS,
        "timezone": LOCAL_TIMEZONE,
        "periods": {period: _default_period_calibration() for period in PERIODS},
    }


def load_forecast_calibration() -> dict[str, Any]:
    """Load the most recent saved calibration, or defaults if unavailable."""
    path = Path(FORECAST_CALIBRATION_PATH)
    if not path.exists():
        return default_forecast_calibration()

    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default_forecast_calibration()

    calibration = default_forecast_calibration()
    loaded_periods = loaded.get("periods", {})
    for period in PERIODS:
        if isinstance(loaded_periods.get(period), dict):
            calibration["periods"][period].update(loaded_periods[period])
    calibration["generated_at"] = loaded.get("generated_at")
    calibration["window_days"] = loaded.get("window_days", CALIBRATION_WINDOW_DAYS)
    calibration["timezone"] = loaded.get("timezone", LOCAL_TIMEZONE)
    return calibration


def get_period_calibration(calibration: dict[str, Any], period: str) -> dict[str, float]:
    """Return one period's calibration values with baseline fallback."""
    return calibration.get("periods", {}).get(period, _default_period_calibration())


def _bounded_step(
    current: float,
    target: float,
    *,
    max_step: float,
    minimum: float,
    maximum: float,
) -> float:
    """Move toward a target with daily safety limits."""
    delta = max(-max_step, min(max_step, target - current))
    return max(minimum, min(maximum, current + delta))


def _infer_period_from_local_time(captured_at_local: datetime) -> str | None:
    """Map local wall-clock time into daytime forecast periods."""
    hour = captured_at_local.hour
    if 7 <= hour < 12:
        return "Morn"
    if 12 <= hour < 16:
        return "Aftn"
    if 16 <= hour < 20:
        return "Eve"
    return None


def _extract_forecast_value_w(forecast_for_day: Any, period: str) -> int | None:
    """Extract forecast watts for a period from archived scheduler forecast state."""
    if not isinstance(forecast_for_day, dict):
        return None

    value = forecast_for_day.get(period)
    if not isinstance(value, list) or len(value) < 2:
        return None

    try:
        return int(value[0])
    except Exception:
        return None


def _read_recent_telemetry(now_utc: datetime) -> list[dict[str, Any]]:
    """Read telemetry JSONL records inside the rolling analysis window.

    Streams the JSONL file line-by-line and uses a fast string pre-filter to
    skip lines whose ``captured_at`` date precedes the cutoff without calling
    ``json.loads()``.
    """
    path = Path(INVERTER_TELEMETRY_ARCHIVE_PATH)
    if not path.exists():
        return []

    cutoff_date = now_utc.astimezone(ZoneInfo(LOCAL_TIMEZONE)).date() - timedelta(
        days=CALIBRATION_WINDOW_DAYS
    )
    cutoff_str = cutoff_date.strftime("%Y-%m-%d")
    _DATE_LEN = 10  # "YYYY-MM-DD"
    _KEY = '"captured_at"'

    snapshots: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue

            # Fast pre-filter: extract the captured_at date without full JSON parse.
            key_idx = line.find(_KEY)
            if key_idx == -1:
                continue
            val_start = line.find('"', key_idx + len(_KEY) + 1)
            if val_start == -1:
                continue
            date_str = line[val_start + 1: val_start + 1 + _DATE_LEN]
            if date_str < cutoff_str:
                continue

            try:
                snapshot = json.loads(line)
                datetime.fromisoformat(snapshot["captured_at"])
            except Exception:
                continue

            snapshots.append(snapshot)

    return snapshots


def build_and_save_forecast_calibration(now_utc: datetime | None = None) -> dict[str, Any]:
    """Compute and persist a bounded daily calibration artifact.

    Calibration is derived from recent telemetry using three conservative knobs:
    1. `power_multiplier` inflates forecast watts by period when actual solar is
       regularly higher than forecast.
    2. `headroom_fraction` increases modestly when clipping is observed.
    3. `export_lead_buffer_multiplier` widens the export lead-time buffer under
       repeated clipping risk.
    """
    now_utc = now_utc or datetime.now(UTC)
    prior = load_forecast_calibration()
    calibration = default_forecast_calibration()
    snapshots = _read_recent_telemetry(now_utc)

    ratios_by_period: dict[str, list[float]] = {period: [] for period in PERIODS}
    clipping_by_period: dict[str, list[bool]] = {period: [] for period in PERIODS}

    for snapshot in snapshots:
        try:
            captured_at_local = datetime.fromisoformat(snapshot["captured_at"])
        except Exception:
            continue

        period = _infer_period_from_local_time(captured_at_local)
        if period is None:
            continue

        derived = snapshot.get("derived")
        if not isinstance(derived, dict):
            derived = derive_clipping_metrics(snapshot.get("energy_flow", {}))

        extracted_metrics = derived.get("extracted_metrics", {})
        solar_power_kw = extracted_metrics.get("solar_power_kw")
        forecast_w = _extract_forecast_value_w(snapshot.get("forecast_today"), period)
        if isinstance(solar_power_kw, (int, float)) and solar_power_kw >= CALIBRATION_MIN_SOLAR_KW:
            if forecast_w and forecast_w > 0:
                ratio = (float(solar_power_kw) * 1000.0) / float(forecast_w)
                ratios_by_period[period].append(
                    max(CALIBRATION_RATIO_MIN, min(CALIBRATION_RATIO_MAX, ratio))
                )
            clipping_by_period[period].append(bool(derived.get("likely_clipping", False)))

    for period in PERIODS:
        prior_period = get_period_calibration(prior, period)
        ratios = ratios_by_period[period]
        clipping_samples = clipping_by_period[period]
        clipping_rate = (
            sum(1 for value in clipping_samples if value) / len(clipping_samples)
            if clipping_samples else 0.0
        )

        if ratios:
            target_multiplier = max(
                CALIBRATION_TARGET_MULTIPLIER_MIN,
                min(CALIBRATION_TARGET_MULTIPLIER_MAX, median(ratios)),
            )
        else:
            target_multiplier = prior_period["power_multiplier"]

        target_lead_buffer = max(
            CALIBRATION_DEFAULT_EXPORT_LEAD_BUFFER_MULTIPLIER,
            min(
                CALIBRATION_TARGET_LEAD_BUFFER_MAX,
                CALIBRATION_DEFAULT_EXPORT_LEAD_BUFFER_MULTIPLIER
                + (CALIBRATION_CLIPPING_RATE_WEIGHT * clipping_rate)
                + (
                    CALIBRATION_TARGET_MULTIPLIER_EXCESS_WEIGHT
                    * max(0.0, target_multiplier - CALIBRATION_DEFAULT_POWER_MULTIPLIER)
                ),
            ),
        )

        calibration["periods"][period] = {
            "power_multiplier": round(
                _bounded_step(
                    float(prior_period["power_multiplier"]),
                    float(target_multiplier),
                    max_step=CALIBRATION_MULTIPLIER_STEP_MAX,
                    minimum=CALIBRATION_TARGET_MULTIPLIER_MIN,
                    maximum=CALIBRATION_TARGET_MULTIPLIER_MAX,
                ),
                3,
            ),
            "export_lead_buffer_multiplier": round(
                _bounded_step(
                    float(prior_period["export_lead_buffer_multiplier"]),
                    float(target_lead_buffer),
                    max_step=CALIBRATION_MULTIPLIER_STEP_MAX,
                    minimum=CALIBRATION_DEFAULT_EXPORT_LEAD_BUFFER_MULTIPLIER,
                    maximum=CALIBRATION_TARGET_LEAD_BUFFER_MAX,
                ),
                3,
            ),
            "telemetry_samples": len(clipping_samples),
            "ratios_used": len(ratios),
            "clipping_observations": sum(1 for value in clipping_samples if value),
            "clipping_rate": round(clipping_rate, 3),
        }

    calibration["generated_at"] = now_utc.astimezone(ZoneInfo(LOCAL_TIMEZONE)).isoformat()
    calibration["window_days"] = CALIBRATION_WINDOW_DAYS
    calibration["timezone"] = LOCAL_TIMEZONE

    path = Path(FORECAST_CALIBRATION_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(calibration, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    logger.info(f"[CALIBRATION] Saved forecast calibration to {path}")
    for period in PERIODS:
        period_cal = calibration["periods"][period]
        logger.info(
            "[CALIBRATION] "
            f"{period}: multiplier={period_cal['power_multiplier']:.3f}, "
            f"lead_buffer={period_cal['export_lead_buffer_multiplier']:.3f}, "
            f"samples={period_cal['telemetry_samples']}, "
            f"clipping_rate={period_cal['clipping_rate']:.3f}"
        )

    return calibration


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    build_and_save_forecast_calibration()
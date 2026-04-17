"""Persist inverter telemetry snapshots for later forecast analysis.

This module appends raw inverter telemetry to a JSONL file so forecast output
can be compared against what the system actually did over time.
"""

from __future__ import annotations

from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from config.settings import (
    CLIPPING_BATTERY_POWER_ABS_LOW_KW,
    CLIPPING_BATTERY_SOC_HIGH_PERCENT,
    INVERTER_KW,
    LOCAL_TIMEZONE,
)
from config.constants import INVERTER_TELEMETRY_ARCHIVE_PATH, MODE_CHANGE_EVENTS_ARCHIVE_PATH


logger = logging.getLogger(__name__)


def _collect_numeric_fields(value: Any, path: tuple[str, ...] = ()) -> list[tuple[tuple[str, ...], float]]:
    """Collect numeric leaf values from nested telemetry structures."""
    fields: list[tuple[tuple[str, ...], float]] = []
    if isinstance(value, dict):
        for key, item in value.items():
            fields.extend(_collect_numeric_fields(item, path + (str(key),)))
        return fields
    if isinstance(value, list):
        for index, item in enumerate(value):
            fields.extend(_collect_numeric_fields(item, path + (str(index),)))
        return fields
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        fields.append((path, float(value)))
    return fields


def _candidate_score(path: tuple[str, ...], candidates: tuple[str, ...]) -> int:
    """Score how closely a field path matches candidate telemetry keys."""
    joined = ".".join(part.lower() for part in path)
    leaf = path[-1].lower() if path else ""
    score = 0
    for candidate in candidates:
        candidate_lower = candidate.lower()
        compact_leaf = leaf.replace("_", "")
        compact_joined = joined.replace("_", "")
        compact_candidate = candidate_lower.replace("_", "")
        if compact_leaf == compact_candidate:
            score = max(score, 100)
        elif compact_candidate in compact_leaf:
            score = max(score, 80)
        elif compact_candidate in compact_joined:
            score = max(score, 60)
    return score


def _extract_numeric_metric(
    energy_flow: dict[str, Any],
    candidates: tuple[str, ...],
) -> tuple[str, float] | None:
    """Extract the best matching numeric field from raw telemetry."""
    ranked: list[tuple[int, tuple[str, ...], float]] = []
    for path, value in _collect_numeric_fields(energy_flow):
        score = _candidate_score(path, candidates)
        if score > 0:
            ranked.append((score, path, value))

    if not ranked:
        return None

    ranked.sort(key=lambda item: (-item[0], len(item[1])))
    _, path, value = ranked[0]
    return (".".join(path), value)


def _normalize_power_to_kw(value: float) -> float:
    """Normalize power values that may be reported in W or kW."""
    if abs(value) > 100:
        return value / 1000.0
    return value


def _split_grid_exchange_power_kw(value_kw: float | None) -> tuple[float | None, float | None]:
    """Split signed grid exchange power into export/import components.

    Positive values are treated as export to grid, and negative values are
    treated as import from grid.

    Args:
        value_kw: Signed net grid exchange in kW.

    Returns:
        Tuple of (grid_export_kw, grid_import_kw), each non-negative when present.
    """
    if value_kw is None:
        return None, None
    if value_kw >= 0:
        return value_kw, 0.0
    return 0.0, abs(value_kw)


def derive_clipping_metrics(energy_flow: dict[str, Any]) -> dict[str, Any]:
    """Infer likely clipping from raw inverter telemetry.

    A sample is considered likely clipping only when solar power equals the
    inverter AC ceiling exactly. Confidence is increased when corroborated by
    high SOC, low battery absorb power, and positive export telemetry.
    """
    solar_metric = _extract_numeric_metric(
        energy_flow,
        ("pvPower", "solarPower", "ppv", "pv", "solar"),
    )
    battery_soc_metric = _extract_numeric_metric(energy_flow, ("batterySoc", "soc"))
    battery_power_metric = _extract_numeric_metric(
        energy_flow,
        ("batteryPower", "batPower", "chargePower", "batteryChargePower"),
    )
    grid_exchange_metric = _extract_numeric_metric(
        energy_flow,
        (
            "buySellPower",
            "gridExportPower",
            "feedInPower",
            "exportPower",
            "netGridPower",
            "gridPower",
        ),
    )

    solar_kw = _normalize_power_to_kw(solar_metric[1]) if solar_metric is not None else None
    battery_soc = battery_soc_metric[1] if battery_soc_metric is not None else None
    battery_power_kw = (
        _normalize_power_to_kw(battery_power_metric[1]) if battery_power_metric is not None else None
    )
    grid_exchange_kw = (
        _normalize_power_to_kw(grid_exchange_metric[1]) if grid_exchange_metric is not None else None
    )
    grid_export_kw, grid_import_kw = _split_grid_exchange_power_kw(grid_exchange_kw)

    reasons: list[str] = []
    confidence = "low"
    likely_clipping = False
    high_battery_soc = (
        battery_soc is not None and battery_soc >= CLIPPING_BATTERY_SOC_HIGH_PERCENT
    )
    low_battery_absorb = (
        battery_power_kw is not None and abs(battery_power_kw) <= CLIPPING_BATTERY_POWER_ABS_LOW_KW
    )
    positive_export = grid_export_kw is not None and grid_export_kw > 0

    if solar_kw is not None and solar_kw == INVERTER_KW:
        likely_clipping = True
        confidence = "medium"
        reasons.append(
            f"solar power equals the inverter ceiling ({solar_kw:.2f} kW vs {INVERTER_KW:.1f} kW)"
        )

        if high_battery_soc:
            confidence = "high"
            reasons.append(f"battery SOC is high ({battery_soc:.1f}%)")

        if low_battery_absorb:
            confidence = "high"
            reasons.append(f"battery power is near zero ({battery_power_kw:.2f} kW)")

        if positive_export:
            reasons.append(f"grid export is positive ({grid_export_kw:.2f} kW)")

    return {
        "likely_clipping": likely_clipping,
        "clipping_confidence": confidence,
        "clipping_reasons": reasons,
        "extracted_metrics": {
            "solar_power_kw": solar_kw,
            "solar_power_source": solar_metric[0] if solar_metric is not None else None,
            "battery_soc_percent": battery_soc,
            "battery_soc_source": battery_soc_metric[0] if battery_soc_metric is not None else None,
            "battery_power_kw": battery_power_kw,
            "battery_power_source": battery_power_metric[0] if battery_power_metric is not None else None,
            "grid_exchange_kw": grid_exchange_kw,
            "grid_exchange_source": grid_exchange_metric[0] if grid_exchange_metric is not None else None,
            "grid_export_kw": grid_export_kw,
            "grid_import_kw": grid_import_kw,
            "grid_export_source": grid_exchange_metric[0] if grid_exchange_metric is not None else None,
        },
    }


def extract_live_solar_power_kw(energy_flow: dict[str, Any]) -> float | None:
    """Extract current solar generation power in kW from raw inverter telemetry.

    Args:
        energy_flow: Raw `get_energy_flow()` payload from the inverter API.

    Returns:
        Solar power in kW when available, otherwise None.
    """
    solar_metric = _extract_numeric_metric(
        energy_flow,
        ("pvPower", "solarPower", "ppv", "pv", "solar"),
    )
    if solar_metric is None:
        return None
    return _normalize_power_to_kw(solar_metric[1])


def extract_today_solar_generation_kwh(energy_flow: dict[str, Any]) -> float | None:
    """Extract today's cumulative solar generation from raw inverter telemetry.

    Args:
        energy_flow: Raw `get_energy_flow()` payload from the inverter API.

    Returns:
        Solar generation for the current day in kWh when available, otherwise None.
    """
    day_generation_metric = _extract_numeric_metric(
        energy_flow,
        (
            "pvDayNrg",
            "pvDayEnergy",
            "dayPvEnergy",
            "todayPvEnergy",
            "todaySolarEnergy",
            "solarDayNrg",
        ),
    )
    if day_generation_metric is None:
        return None

    value = float(day_generation_metric[1])
    # Some payloads report day energy in Wh; normalize large values to kWh.
    if value > 1000:
        value = value / 1000.0
    return value


def _json_safe(value: Any) -> Any:
    """Convert values into a JSON-safe structure.

    Args:
        value: Any nested Python value.

    Returns:
        A JSON-serializable version of the input.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return repr(value)


def append_inverter_telemetry_snapshot(
    *,
    energy_flow: dict[str, Any],
    operational_mode: Any,
    reason: str,
    scheduler_now_utc: datetime,
    forecast_today: dict[str, tuple[int, str]] | None = None,
    forecast_tomorrow: dict[str, tuple[int, str]] | None = None,
) -> None:
    """Append one inverter telemetry snapshot to the local archive.

    Args:
        energy_flow: Raw `get_energy_flow()` payload from the inverter API.
        operational_mode: Raw current mode payload or label.
        reason: Context for why the sample was captured.
        scheduler_now_utc: Current scheduler timestamp in UTC.
        forecast_today: Optional today's forecast state seen by the scheduler.
        forecast_tomorrow: Optional tomorrow's forecast state seen by the scheduler.
    """
    archive_path = Path(INVERTER_TELEMETRY_ARCHIVE_PATH)
    captured_at_local = scheduler_now_utc.astimezone(ZoneInfo(LOCAL_TIMEZONE))
    derived_metrics = derive_clipping_metrics(energy_flow)
    snapshot = {
        "captured_at": captured_at_local.isoformat(),
        "scheduler_now_utc": scheduler_now_utc.isoformat(),
        "timezone": LOCAL_TIMEZONE,
        "reason": reason,
        "operational_mode": _json_safe(operational_mode),
        "energy_flow": _json_safe(energy_flow),
        "derived": _json_safe(derived_metrics),
        "forecast_today": _json_safe(forecast_today),
        "forecast_tomorrow": _json_safe(forecast_tomorrow),
    }

    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with archive_path.open("a", encoding="utf-8") as archive_file:
            json.dump(snapshot, archive_file, sort_keys=True)
            archive_file.write("\n")
        logger.info(f"[TELEMETRY] Saved inverter snapshot to {archive_path}")
    except OSError as exc:
        logger.warning(f"[TELEMETRY] Failed to save inverter snapshot to {archive_path}: {exc}")


def append_mode_change_event(
    *,
    scheduler_now_utc: datetime,
    period: str,
    requested_mode: int,
    requested_mode_label: str,
    reason: str,
    simulated: bool,
    success: bool,
    current_mode: Any = None,
    response: Any = None,
    error: str | None = None,
) -> None:
    """Append one inverter mode-change event to the local archive.

    Args:
        scheduler_now_utc: Current scheduler timestamp in UTC.
        period: Human-readable period/context label.
        requested_mode: Target mode integer.
        requested_mode_label: Human-readable target mode label.
        reason: Decision reason supplied by scheduler.
        simulated: Whether the command ran in simulation mode.
        success: Whether set operation succeeded.
        current_mode: Optional current mode payload captured before set.
        response: Optional API/simulation response payload.
        error: Optional error string if set failed.
    """
    archive_path = Path(MODE_CHANGE_EVENTS_ARCHIVE_PATH)
    captured_at_local = scheduler_now_utc.astimezone(ZoneInfo(LOCAL_TIMEZONE))
    event = {
        "captured_at": captured_at_local.isoformat(),
        "scheduler_now_utc": scheduler_now_utc.isoformat(),
        "timezone": LOCAL_TIMEZONE,
        "period": period,
        "requested_mode": requested_mode,
        "requested_mode_label": requested_mode_label,
        "reason": reason,
        "simulated": simulated,
        "success": success,
        "current_mode": _json_safe(current_mode),
        "response": _json_safe(response),
        "error": error,
    }

    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with archive_path.open("a", encoding="utf-8") as archive_file:
            json.dump(event, archive_file, sort_keys=True)
            archive_file.write("\n")
        logger.info(f"[TELEMETRY] Saved mode-change event to {archive_path}")
    except OSError as exc:
        logger.warning(f"[TELEMETRY] Failed to save mode-change event to {archive_path}: {exc}")

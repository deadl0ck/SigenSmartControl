"""scenario_simulation.py
------------------------
Generate and evaluate read-only hourly scenario CSV rows for the scheduler.

This module provides deterministic helpers for building multi-day scenario sets
and for evaluating each row against the project's pure decision logic without
sending inverter commands or emails.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from config.settings import (
    BATTERY_KWH,
    BRIDGE_BATTERY_RESERVE_KWH,
    ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE,
    ESTIMATED_HOME_LOAD_KW,
    FORECAST_ANALYSIS_AFTERNOON_END_HOUR,
    FORECAST_ANALYSIS_AFTERNOON_START_HOUR,
    FORECAST_ANALYSIS_EVENING_END_HOUR,
    FORECAST_ANALYSIS_EVENING_START_HOUR,
    FORECAST_ANALYSIS_MORNING_END_HOUR,
    FORECAST_ANALYSIS_MORNING_START_HOUR,
    HEADROOM_TARGET_KWH,
    INVERTER_KW,
    SIGEN_MODES,
    SOLAR_PV_KW,
)
from logic.decision_logic import calc_headroom_kwh, decide_operational_mode
from logic.schedule_utils import get_hours_until_cheap_rate, get_schedule_period_for_time, is_cheap_rate_window


@dataclass(frozen=True)
class ScenarioTemplate:
    """Describe one 24-hour scenario set.

    Attributes:
        name: Human-readable scenario label.
        soc_by_hour: Hourly SOC values for a 24-hour block.
        forecast_by_period: Forecast status by daytime period.
        current_mode_by_hour: Current mode label by hour.
    """

    name: str
    soc_by_hour: list[int]
    forecast_by_period: dict[str, str]
    current_mode_by_hour: list[str]


def mode_name_from_value(mode_value: int) -> str:
    """Return the configured mode name for a numeric mode value.

    Args:
        mode_value: Numeric inverter mode value.

    Returns:
        Matching mode name when known, otherwise ``UNKNOWN``.
    """
    for name, value in SIGEN_MODES.items():
        if value == mode_value:
            return name
    return "UNKNOWN"


def parse_hour_text(hour_text: str) -> int:
    """Parse an ``HH:MM`` hour string.

    Args:
        hour_text: Local hour string, e.g. ``"07:00"``.

    Returns:
        Parsed hour number in the range 0-23.

    Raises:
        ValueError: When the input is not a valid ``HH:MM`` hour.
    """
    try:
        parsed = datetime.strptime(hour_text.strip(), "%H:%M")
    except ValueError as exc:
        raise ValueError(f"Invalid hour value: {hour_text!r}") from exc
    return parsed.hour


def normalize_forecast_label(forecast: str) -> str:
    """Normalize a forecast label to the values used by decision logic.

    Args:
        forecast: Raw CSV forecast value.

    Returns:
        Normalized uppercase forecast value.

    Raises:
        ValueError: When the forecast label is unsupported.
    """
    normalized = (forecast or "").strip().upper()
    if normalized not in {"GREEN", "AMBER", "RED"}:
        raise ValueError(f"Unsupported forecast value: {forecast!r}")
    return normalized


def normalize_mode_value(mode_text: str) -> int:
    """Normalize a current-mode CSV value to a numeric Sigen mode.

    Args:
        mode_text: Mode label or integer value from the CSV.

    Returns:
        Numeric Sigen mode value.

    Raises:
        ValueError: When the mode cannot be resolved.
    """
    normalized = (mode_text or "").strip().upper()
    if normalized.isdigit():
        return int(normalized)
    if normalized in SIGEN_MODES:
        return SIGEN_MODES[normalized]
    raise ValueError(f"Unsupported current mode value: {mode_text!r}")


def build_reference_utc(hour_text: str) -> datetime:
    """Build a fixed UTC datetime for an hourly scenario row.

    Args:
        hour_text: Local hour text in ``HH:MM`` format.

    Returns:
        Fixed UTC datetime on an arbitrary winter date.
    """
    hour = parse_hour_text(hour_text)
    return datetime(2026, 1, 15, hour, 0, tzinfo=timezone.utc)


def derive_period_for_hour(hour_text: str) -> str:
    """Map a local hour to a decision period label.

    Args:
        hour_text: Local hour text in ``HH:MM`` format.

    Returns:
        One of ``NIGHT``, ``Morn``, ``Aftn``, or ``Eve``.
    """
    when_utc = build_reference_utc(hour_text)
    if is_cheap_rate_window(when_utc):
        return "NIGHT"

    hour = parse_hour_text(hour_text)
    if FORECAST_ANALYSIS_MORNING_START_HOUR <= hour < FORECAST_ANALYSIS_MORNING_END_HOUR:
        return "Morn"
    if FORECAST_ANALYSIS_AFTERNOON_START_HOUR <= hour < FORECAST_ANALYSIS_AFTERNOON_END_HOUR:
        return "Aftn"
    if FORECAST_ANALYSIS_EVENING_START_HOUR <= hour < FORECAST_ANALYSIS_EVENING_END_HOUR:
        return "Eve"
    if hour < FORECAST_ANALYSIS_MORNING_START_HOUR:
        return "NIGHT"
    return "Eve"


def forecast_for_hour(hour_text: str, daytime_forecast_by_period: dict[str, str]) -> str:
    """Resolve the forecast label for a specific hour.

    Args:
        hour_text: Local hour text in ``HH:MM`` format.
        daytime_forecast_by_period: Forecast labels for Morn/Aftn/Eve.

    Returns:
        Forecast label for the hour. Cheap-rate hours always return ``RED``.
    """
    period = derive_period_for_hour(hour_text)
    if period == "NIGHT":
        return "RED"
    return normalize_forecast_label(daytime_forecast_by_period[period])


def _build_soc_profile(start_soc: int, midday_peak_soc: int, end_soc: int) -> list[int]:
    """Build a simple 24-hour SOC curve.

    Args:
        start_soc: Midnight SOC.
        midday_peak_soc: Peak daytime SOC.
        end_soc: Late-evening SOC.

    Returns:
        Deterministic list of 24 hourly SOC values.
    """
    first_leg = [start_soc, start_soc - 5, start_soc - 10, start_soc - 10, start_soc - 8, start_soc - 5]
    day_leg = [
        max(10, start_soc - 2),
        start_soc,
        min(100, start_soc + 8),
        min(100, start_soc + 16),
        min(100, midday_peak_soc - 6),
        midday_peak_soc,
        midday_peak_soc,
        max(10, midday_peak_soc - 4),
        max(10, midday_peak_soc - 8),
        max(10, midday_peak_soc - 12),
    ]
    evening_leg = [
        max(10, midday_peak_soc - 10),
        max(10, midday_peak_soc - 14),
        max(10, midday_peak_soc - 18),
        max(10, end_soc + 8),
        max(10, end_soc + 4),
        end_soc,
        end_soc,
        max(10, end_soc - 2),
    ]
    profile = first_leg + day_leg + evening_leg
    return [max(10, min(100, value)) for value in profile[:24]]


def _mode_block(night_mode: str, day_mode: str, evening_mode: str) -> list[str]:
    """Build a 24-hour current-mode pattern.

    Args:
        night_mode: Current mode label for cheap-rate hours.
        day_mode: Current mode label for morning and afternoon hours.
        evening_mode: Current mode label for evening shoulder hours.

    Returns:
        List of 24 mode labels.
    """
    return [
        night_mode,
        night_mode,
        night_mode,
        night_mode,
        night_mode,
        night_mode,
        night_mode,
        night_mode,
        day_mode,
        day_mode,
        day_mode,
        day_mode,
        day_mode,
        day_mode,
        day_mode,
        day_mode,
        evening_mode,
        evening_mode,
        evening_mode,
        evening_mode,
        evening_mode,
        evening_mode,
        evening_mode,
        night_mode,
    ]


def build_default_scenario_templates() -> list[ScenarioTemplate]:
    """Build the default 24-hour scenario set definitions.

    Returns:
        Deterministic list of scenario templates covering multiple forecast,
        SOC, and current-mode combinations.
    """
    return [
        ScenarioTemplate(
            name="high-soc-green-day",
            soc_by_hour=_build_soc_profile(72, 98, 74),
            forecast_by_period={"Morn": "GREEN", "Aftn": "GREEN", "Eve": "AMBER"},
            current_mode_by_hour=_mode_block("TOU", "SELF_POWERED", "SELF_POWERED"),
        ),
        ScenarioTemplate(
            name="medium-soc-mixed-day",
            soc_by_hour=_build_soc_profile(56, 82, 52),
            forecast_by_period={"Morn": "AMBER", "Aftn": "GREEN", "Eve": "RED"},
            current_mode_by_hour=_mode_block("TOU", "TOU", "SELF_POWERED"),
        ),
        ScenarioTemplate(
            name="low-soc-weak-day",
            soc_by_hour=_build_soc_profile(34, 54, 28),
            forecast_by_period={"Morn": "RED", "Aftn": "AMBER", "Eve": "RED"},
            current_mode_by_hour=_mode_block("SELF_POWERED", "GRID_EXPORT", "GRID_EXPORT"),
        ),
        ScenarioTemplate(
            name="late-solar-ramp",
            soc_by_hour=_build_soc_profile(48, 88, 60),
            forecast_by_period={"Morn": "RED", "Aftn": "AMBER", "Eve": "GREEN"},
            current_mode_by_hour=_mode_block("TOU", "AI", "SELF_POWERED"),
        ),
        ScenarioTemplate(
            name="full-battery-under-wrong-mode",
            soc_by_hour=_build_soc_profile(80, 100, 78),
            forecast_by_period={"Morn": "GREEN", "Aftn": "AMBER", "Eve": "AMBER"},
            current_mode_by_hour=_mode_block("SELF_POWERED", "GRID_EXPORT", "AI"),
        ),
        ScenarioTemplate(
            name="lean-battery-tou-day",
            soc_by_hour=_build_soc_profile(28, 62, 24),
            forecast_by_period={"Morn": "AMBER", "Aftn": "RED", "Eve": "RED"},
            current_mode_by_hour=_mode_block("TOU", "TOU", "TOU"),
        ),
        ScenarioTemplate(
            name="night-start-ai-gridexport-mismatch",
            soc_by_hour=[58, 56, 54, 52, 50, 48, 46, 44, 46, 50, 54, 58, 62, 66, 70, 72, 70, 68, 66, 64, 62, 60, 58, 56],
            forecast_by_period={"Morn": "AMBER", "Aftn": "GREEN", "Eve": "AMBER"},
            current_mode_by_hour=[
                "GRID_EXPORT", "GRID_EXPORT", "GRID_EXPORT", "GRID_EXPORT",
                "GRID_EXPORT", "GRID_EXPORT", "GRID_EXPORT", "GRID_EXPORT",
                "SELF_POWERED", "SELF_POWERED", "SELF_POWERED", "SELF_POWERED",
                "SELF_POWERED", "SELF_POWERED", "SELF_POWERED", "SELF_POWERED",
                "GRID_EXPORT", "GRID_EXPORT", "GRID_EXPORT", "GRID_EXPORT",
                "GRID_EXPORT", "GRID_EXPORT", "GRID_EXPORT", "AI",
            ],
        ),
        ScenarioTemplate(
            name="pre-morning-low-soc-wrong-night-mode",
            soc_by_hour=[35, 33, 31, 29, 27, 25, 20, 15, 18, 22, 28, 35, 42, 48, 52, 55, 54, 50, 46, 42, 38, 34, 30, 26],
            forecast_by_period={"Morn": "RED", "Aftn": "AMBER", "Eve": "RED"},
            current_mode_by_hour=[
                "SELF_POWERED", "SELF_POWERED", "SELF_POWERED", "SELF_POWERED",
                "SELF_POWERED", "SELF_POWERED", "GRID_EXPORT", "SELF_POWERED",
                "TOU", "TOU", "TOU", "TOU",
                "TOU", "TOU", "TOU", "TOU",
                "SELF_POWERED", "SELF_POWERED", "SELF_POWERED", "SELF_POWERED",
                "SELF_POWERED", "SELF_POWERED", "SELF_POWERED", "SELF_POWERED",
            ],
        ),
        ScenarioTemplate(
            name="pre-morning-high-soc-boundary-at-08",
            soc_by_hour=[96, 95, 94, 93, 92, 91, 95, 92, 90, 88, 90, 94, 97, 99, 100, 98, 95, 92, 90, 88, 86, 84, 82, 80],
            forecast_by_period={"Morn": "GREEN", "Aftn": "AMBER", "Eve": "AMBER"},
            current_mode_by_hour=[
                "SELF_POWERED", "SELF_POWERED", "SELF_POWERED", "SELF_POWERED",
                "SELF_POWERED", "SELF_POWERED", "AI", "AI",
                "TOU", "TOU", "TOU", "TOU",
                "GRID_EXPORT", "GRID_EXPORT", "GRID_EXPORT", "GRID_EXPORT",
                "AI", "AI", "AI", "AI",
                "AI", "AI", "AI", "SELF_POWERED",
            ],
        ),
        ScenarioTemplate(
            name="eve-low-usable-energy-opposite-bridge",
            soc_by_hour=[40, 38, 36, 34, 32, 30, 28, 26, 30, 36, 42, 48, 44, 38, 34, 30, 24, 20, 16, 14, 12, 9, 7, 12],
            forecast_by_period={"Morn": "RED", "Aftn": "AMBER", "Eve": "GREEN"},
            current_mode_by_hour=[
                "TOU", "TOU", "TOU", "TOU",
                "TOU", "TOU", "TOU", "TOU",
                "SELF_POWERED", "SELF_POWERED", "SELF_POWERED", "SELF_POWERED",
                "SELF_POWERED", "SELF_POWERED", "SELF_POWERED", "SELF_POWERED",
                "SELF_POWERED", "SELF_POWERED", "SELF_POWERED", "SELF_POWERED",
                "SELF_POWERED", "SELF_POWERED", "SELF_POWERED", "TOU",
            ],
        ),
    ]


def generate_scenario_rows() -> list[dict[str, str]]:
    """Generate CSV rows for all default scenario sets.

    Returns:
        List of CSV-compatible row dictionaries containing only the requested
        columns: ``Hour``, ``SOC``, ``Forecast``, and ``Current Mode``.
    """
    rows: list[dict[str, str]] = []
    hours = [f"{hour:02d}:00" for hour in range(24)]
    for template in build_default_scenario_templates():
        for index, hour_text in enumerate(hours):
            rows.append(
                {
                    "Hour": hour_text,
                    "SOC": str(template.soc_by_hour[index]),
                    "Forecast": forecast_for_hour(hour_text, template.forecast_by_period),
                    "Current Mode": template.current_mode_by_hour[index],
                }
            )
    return rows


def evaluate_scenario_row(
    hour_text: str,
    soc: float,
    forecast: str,
    current_mode: str,
) -> dict[str, Any]:
    """Evaluate a single scenario row against the pure decision engine.

    Args:
        hour_text: Local hour text in ``HH:MM`` format.
        soc: Battery state of charge percentage.
        forecast: Forecast label from the CSV.
        current_mode: Current mode label or integer string from the CSV.

    Returns:
        Annotated result dictionary including derived periods, target mode, and
        whether the scheduler would request a mode change.
    """
    when_utc = build_reference_utc(hour_text)
    normalized_forecast = normalize_forecast_label(forecast)
    current_mode_value = normalize_mode_value(current_mode)
    period = derive_period_for_hour(hour_text)
    schedule_period = get_schedule_period_for_time(when_utc)
    headroom_kwh = calc_headroom_kwh(BATTERY_KWH, soc)
    period_solar_kwh = 0.0 if period == "NIGHT" else min(SOLAR_PV_KW, INVERTER_KW) * 3.0
    target_mode_value, reason = decide_operational_mode(
        period=period,
        status=normalized_forecast,
        soc=soc,
        headroom_kwh=headroom_kwh,
        period_solar_kwh=period_solar_kwh,
        schedule_period=schedule_period,
        headroom_target_kwh=HEADROOM_TARGET_KWH,
        battery_kwh=BATTERY_KWH,
        hours_until_cheap_rate=get_hours_until_cheap_rate(when_utc),
        estimated_home_load_kw=ESTIMATED_HOME_LOAD_KW,
        bridge_battery_reserve_kwh=BRIDGE_BATTERY_RESERVE_KWH,
        enable_pre_cheap_rate_battery_bridge=ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE,
    )
    target_mode_name = mode_name_from_value(target_mode_value)
    current_mode_name = mode_name_from_value(current_mode_value)
    action = "KEEP_CURRENT_MODE" if current_mode_value == target_mode_value else "CHANGE_MODE"
    return {
        "Period": period,
        "Schedule Period": schedule_period,
        "Forecast": normalized_forecast,
        "Current Mode": current_mode_name,
        "Current Mode Value": current_mode_value,
        "Target Mode": target_mode_name,
        "Target Mode Value": target_mode_value,
        "Action": action,
        "Reason": reason,
    }


def annotate_scenario_rows(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Annotate a list of raw scenario rows with evaluation output.

    Args:
        rows: Raw CSV rows containing ``Hour``, ``SOC``, ``Forecast``, and
            ``Current Mode``.

    Returns:
        Output rows including the original columns plus scenario set number and
        evaluation details.
    """
    annotated_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        scenario_set = index // 24 + 1
        result = evaluate_scenario_row(
            hour_text=row["Hour"],
            soc=float(row["SOC"]),
            forecast=row["Forecast"],
            current_mode=row["Current Mode"],
        )
        annotated_rows.append(
            {
                "Scenario Set": scenario_set,
                "Hour": row["Hour"],
                "SOC": row["SOC"],
                "Forecast": result["Forecast"],
                "Current Mode": result["Current Mode"],
                "Period": result["Period"],
                "Schedule Period": result["Schedule Period"],
                "Target Mode": result["Target Mode"],
                "Target Mode Value": result["Target Mode Value"],
                "Action": result["Action"],
                "Reason": result["Reason"],
            }
        )
    return annotated_rows
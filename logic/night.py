"""
logic/night.py
--------------
Night window scheduler handler.

Manages PRE-DAWN and EVENING-NIGHT mode decisions, pre-cheap-rate export
planning, and optional night sleep scheduling.
"""

import logging
import math
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from config.settings import (
    BATTERY_KWH,
    CHEAP_RATE_START_HOUR,
    ENABLE_PRE_CHEAP_RATE_NIGHT_EXPORT,
    ENABLE_SUMMER_PRE_SUNRISE_DISCHARGE,
    HEADROOM_TARGET_KWH,
    MAX_TIMED_EXPORT_MINUTES,
    NIGHT_SLEEP_MODE_ENABLED,
    PERIOD_TO_MODE,
    POLL_INTERVAL_MINUTES,
    PRE_CHEAP_RATE_NIGHT_EXPORT_ASSUMED_DISCHARGE_KW,
    PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT,
    PRE_SUNRISE_DISCHARGE_LEAD_MINUTES,
    PRE_SUNRISE_DISCHARGE_MIN_SOC_PERCENT,
    PRE_SUNRISE_DISCHARGE_MONTHS,
    SIGEN_MODES,
)
from logic.schedule_utils import (
    LOCAL_TZ,
    get_hours_until_cheap_rate,
    is_pre_sunrise_discharge_window,
)

logger = logging.getLogger("sigen_control")

_POLL_INTERVAL_SECONDS = POLL_INTERVAL_MINUTES * 60


def _log_check(
    period: str,
    stage: str,
    *,
    mode_names: dict[int, str],
    now_utc: datetime,
    period_start_utc: datetime,
    solar_value: int,
    status: str,
    period_solar_kwh: float,
    soc: float | None,
    headroom_kwh: float | None,
    headroom_target_kwh: float,
    headroom_deficit_kwh: float,
    export_by_utc: datetime | None,
    solar_avg_kw_3: float | None = None,
    effective_battery_export_kw: float | None = None,
    lead_time_hours_adjusted: float | None = None,
    mode: int | None = None,
    reason: str = "",
    outcome: str = "",
) -> None:
    """Log a comprehensive decision checkpoint with all relevant state and parameters.

    Args:
        period: Human-readable period/context label.
        stage: Scheduling stage (NIGHT-BASE, etc.).
        mode_names: Mapping of mode integer to label for display.
        now_utc: Current time in UTC.
        period_start_utc: Period start time in UTC.
        solar_value: Forecasted power in watts.
        status: Forecast status string.
        period_solar_kwh: Estimated available solar energy in kWh.
        soc: Current battery SOC percentage, or None if unavailable.
        headroom_kwh: Current available battery headroom in kWh.
        headroom_target_kwh: Target headroom needed before period in kWh.
        headroom_deficit_kwh: Shortfall against target in kWh.
        export_by_utc: Deadline for pre-period export window.
        solar_avg_kw_3: Rolling average solar kW over latest three samples.
        effective_battery_export_kw: Estimated battery export kW after solar occupancy.
        lead_time_hours_adjusted: Lead-time computed from adjusted export denominator.
        mode: Target operational mode integer, or None.
        reason: Explanation of decision logic.
        outcome: Description of action taken.
    """
    mode_label = mode_names.get(mode, mode) if mode is not None else "N/A"
    export_by_label = export_by_utc.isoformat() if export_by_utc is not None else "N/A"
    base_period = period.split(" ", 1)[0].split("->")[-1]
    period_labels = {"Morn": "MORNING", "Aftn": "AFTERNOON", "Eve": "EVENING", "NIGHT": "NIGHT"}
    period_display = period_labels.get(base_period, base_period.upper())
    period_start_local = period_start_utc.astimezone(LOCAL_TZ).strftime("%H:%M")
    logger.info(f"[{period}] {stage} CHECK FOR {period_display} (Starts at {period_start_local}):")
    logger.info(f"[{period}]     -> now={now_utc.isoformat()}")
    logger.info(f"[{period}]     -> period_start={period_start_utc.isoformat()}")
    logger.info(f"[{period}]     -> forecast_w={solar_value}")
    logger.info(f"[{period}]     -> status={status}")
    logger.info(f"[{period}]     -> expected_solar_kwh={period_solar_kwh:.2f}")
    logger.info(f"[{period}]     -> soc={soc if soc is not None else 'N/A'}")
    logger.info(
        f"[{period}]     -> headroom_kwh={f'{headroom_kwh:.2f}' if headroom_kwh is not None else 'N/A'}"
    )
    logger.info(f"[{period}]     -> headroom_target_kwh={headroom_target_kwh:.2f}")
    logger.info(f"[{period}]     -> headroom_deficit_kwh={headroom_deficit_kwh:.2f}")
    logger.info(
        f"[{period}]     -> solar_avg_kw_3={f'{solar_avg_kw_3:.2f}' if solar_avg_kw_3 is not None else 'N/A'}"
    )
    logger.info(
        "[{}]     -> effective_battery_export_kw={}".format(
            period,
            f"{effective_battery_export_kw:.2f}" if effective_battery_export_kw is not None else "N/A",
        )
    )
    logger.info(
        "[{}]     -> lead_time_hours_adjusted={}".format(
            period,
            f"{lead_time_hours_adjusted:.2f}" if lead_time_hours_adjusted is not None else "N/A",
        )
    )
    logger.info(f"[{period}]     -> export_by={export_by_label}")
    logger.info(f"[{period}]     -> decision_mode={mode_label}")
    logger.info(f"[{period}]     -> outcome={outcome}")
    logger.info(f"[{period}]     -> reason={reason}")


def plan_pre_cheap_rate_night_export(
    *,
    soc: float | None,
    now_utc: datetime,
) -> tuple[int | None, str | None]:
    """Plan sunset-to-cheap-rate export duration bounded by SOC floor and time.

    Args:
        soc: Current battery SOC percentage.
        now_utc: Current scheduler timestamp in UTC.

    Returns:
        Tuple of (duration_minutes, reason) when export should begin, else (None, None).
    """
    if not ENABLE_PRE_CHEAP_RATE_NIGHT_EXPORT:
        return None, None
    if soc is None:
        return None, None
    if soc <= PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT:
        return None, None

    hours_until_cheap_rate = get_hours_until_cheap_rate(now_utc)
    if hours_until_cheap_rate <= 0:
        return None, None

    energy_above_floor_kwh = BATTERY_KWH * (
        (soc - PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT) / 100.0
    )
    if energy_above_floor_kwh <= 0:
        return None, None

    minutes_to_soc_floor = math.ceil(
        (energy_above_floor_kwh / PRE_CHEAP_RATE_NIGHT_EXPORT_ASSUMED_DISCHARGE_KW) * 60
    )
    minutes_to_cheap_rate = max(1, int(hours_until_cheap_rate * 60))
    duration_minutes = max(
        1,
        min(minutes_to_soc_floor, minutes_to_cheap_rate, MAX_TIMED_EXPORT_MINUTES),
    )

    reason = (
        "Pre-cheap-rate export strategy: discharge battery for arbitrage until "
        f"SOC floor {PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT:.1f}% or cheap-rate "
        f"window opens. SOC={soc:.1f}%, duration={duration_minutes} minutes."
    )
    return duration_minutes, reason


async def handle_night_window(
    *,
    now_utc: datetime,
    night_context: dict[str, Any],
    night_state: dict[str, Any],
    period_solar_kwh: float,
    fetch_soc: Callable[[str], Awaitable[float | None]],
    start_timed_grid_export: Callable[..., Awaitable[bool]],
    apply_mode_change: Callable[..., Awaitable[bool]],
    archive_inverter_telemetry: Callable[..., Awaitable[None]],
    sigen: Any,
    mode_names: dict[int, str],
) -> dict[str, Any]:
    """Handle active night-window behavior for a scheduler tick.

    Applies PRE-DAWN and EVENING-NIGHT mode logic, optionally starts timed
    export, and computes optional long sleep duration when night sleep is enabled.

    Args:
        now_utc: Current scheduler timestamp in UTC.
        night_context: Active night-window metadata from get_active_night_context.
        night_state: Mutable dict tracking night mode set key and sleep snapshot state.
        period_solar_kwh: Pre-computed estimated solar kWh for the target period.
        fetch_soc: Async callable returning current battery SOC or None.
        start_timed_grid_export: Async callable to begin a bounded timed export.
        apply_mode_change: Async callable to apply a mode change with tracking.
        archive_inverter_telemetry: Async callable to archive an inverter snapshot.
        sigen: Sigen API interaction instance.
        mode_names: Mapping of mode integer values to display labels.

    Returns:
        Dict with keys:
            sleep_seconds (int | None): Override sleep duration, or None for normal interval.
            refresh_auth_on_wake (bool): True if auth should be refreshed after waking.
    """
    night_period_name = f"Night->{night_context['target_period']}"
    night_headroom_target_kwh = HEADROOM_TARGET_KWH
    night_mode = PERIOD_TO_MODE["NIGHT"]
    night_mode_reason = "Night window active. Applying configured night mode."
    soc: float | None = None
    refresh_auth_on_wake = False
    sleep_seconds: int | None = None

    if (
        night_context["window_name"] == "PRE-DAWN"
        and is_pre_sunrise_discharge_window(
            now_utc,
            night_context["target_start"],
            enabled=ENABLE_SUMMER_PRE_SUNRISE_DISCHARGE,
            months_csv=PRE_SUNRISE_DISCHARGE_MONTHS,
            lead_minutes=PRE_SUNRISE_DISCHARGE_LEAD_MINUTES,
        )
    ):
        soc = await fetch_soc(night_period_name)
        if soc is not None and soc >= PRE_SUNRISE_DISCHARGE_MIN_SOC_PERCENT:
            night_mode = SIGEN_MODES["SELF_POWERED"]
            night_mode_reason = (
                "Summer pre-sunrise discharge window active. Switching to "
                "self-powered mode to create battery headroom before morning solar."
            )
        else:
            night_mode_reason = (
                "Summer pre-sunrise discharge window active, but SOC is below "
                f"minimum threshold {PRE_SUNRISE_DISCHARGE_MIN_SOC_PERCENT:.1f}%. "
                "Keeping configured night mode instead of discharging."
            )

    if night_context["window_name"] == "EVENING-NIGHT":
        hours_until_cheap_rate = get_hours_until_cheap_rate(now_utc)
        if hours_until_cheap_rate > 0:
            soc = await fetch_soc(night_period_name)
            export_minutes, export_reason = plan_pre_cheap_rate_night_export(
                soc=soc, now_utc=now_utc,
            )
            if export_minutes is not None and export_reason is not None:
                started = await start_timed_grid_export(
                    period=night_period_name,
                    reason=export_reason,
                    duration_minutes=export_minutes,
                    now_utc=now_utc,
                    battery_soc=soc,
                    export_soc_floor=PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT,
                )
                if started:
                    return {"sleep_seconds": None, "refresh_auth_on_wake": False}
            night_mode = SIGEN_MODES["SELF_POWERED"]
            if soc is not None and soc <= PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT:
                night_mode_reason = (
                    "Pre-cheap-rate export floor reached. Switching to self-powered "
                    f"at SOC floor {PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT:.1f}%."
                )
            else:
                night_mode_reason = (
                    "Pre-cheap-rate window active. Holding self-powered mode until "
                    "cheap-rate window opens."
                )

    mode_set_key = (night_context["target_date"], night_mode)
    if night_state["mode_set_key"] != mode_set_key:
        _log_check(
            night_period_name, "NIGHT-BASE",
            mode_names=mode_names, now_utc=now_utc,
            period_start_utc=night_context["target_start"],
            solar_value=night_context["solar_value"],
            status=night_context["status"],
            period_solar_kwh=period_solar_kwh,
            soc=None, headroom_kwh=None,
            headroom_target_kwh=night_headroom_target_kwh,
            headroom_deficit_kwh=0.0,
            export_by_utc=night_context["night_start"],
            mode=night_mode,
            reason=(
                f"Active {night_context['window_name']} window before "
                f"{night_context['target_period']}. {night_mode_reason}"
            ),
            outcome="night mode applied",
        )
        try:
            ok = await apply_mode_change(
                sigen=sigen,
                mode=night_mode,
                period=night_period_name,
                reason=night_mode_reason,
                mode_names=mode_names,
                battery_soc=soc,
            )
            if ok:
                night_state["mode_set_key"] = mode_set_key
        except Exception as e:
            logger.error(
                "[%s] Unexpected error applying base night mode: %s", night_period_name, e
            )

    if NIGHT_SLEEP_MODE_ENABLED:
        from config.settings import MAX_PRE_PERIOD_WINDOW_MINUTES  # avoid circular at module level
        pre_window_opens_at = night_context["target_start"] - timedelta(
            minutes=MAX_PRE_PERIOD_WINDOW_MINUTES
        )
        wake_at = pre_window_opens_at

        if night_context["window_name"] == "EVENING-NIGHT":
            cheap_rate_start_local = now_utc.astimezone(LOCAL_TZ).replace(
                hour=CHEAP_RATE_START_HOUR,
                minute=0,
                second=0,
                microsecond=0,
            )
            if now_utc.astimezone(LOCAL_TZ) >= cheap_rate_start_local:
                cheap_rate_start_local = cheap_rate_start_local + timedelta(days=1)
            cheap_rate_start_utc = cheap_rate_start_local.astimezone(timezone.utc)
            wake_at = min(wake_at, cheap_rate_start_utc)

        if night_context["window_name"] == "PRE-DAWN" and ENABLE_SUMMER_PRE_SUNRISE_DISCHARGE:
            pre_sunrise_wake_at = night_context["target_start"] - timedelta(
                minutes=PRE_SUNRISE_DISCHARGE_LEAD_MINUTES
            )
            wake_at = min(wake_at, pre_sunrise_wake_at)

        if now_utc < wake_at:
            candidate_sleep_seconds = max(1, int((wake_at - now_utc).total_seconds()))
            if candidate_sleep_seconds > _POLL_INTERVAL_SECONDS:
                local_date = now_utc.astimezone(LOCAL_TZ).date()
                if (
                    night_context["window_name"] == "EVENING-NIGHT"
                    and night_state.get("sleep_snapshot_for_date") != local_date
                ):
                    await archive_inverter_telemetry("night_sleep_start", now_utc)
                    night_state["sleep_snapshot_for_date"] = local_date
                    logger.info(
                        "[SCHEDULER] Captured end-of-day telemetry snapshot before night sleep."
                    )
                sleep_seconds = candidate_sleep_seconds
                refresh_auth_on_wake = True
                logger.info(
                    "[SCHEDULER] Night sleep mode active. Sleeping for %s minutes until %s (%s).",
                    sleep_seconds // 60,
                    wake_at.isoformat(),
                    "next critical night milestone",
                )

    return {"sleep_seconds": sleep_seconds, "refresh_auth_on_wake": refresh_auth_on_wake}

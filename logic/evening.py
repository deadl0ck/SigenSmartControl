"""
logic/evening.py
----------------
Evening period scheduler handler.

Manages pre-period export checks, period-start mode decisions,
mid-period clipping/high-SOC safety exports, and controlled evening
export planning for the Evening (Eve) period.
"""

import logging
import math
from datetime import datetime, timedelta

from config.settings import (
    BATTERY_KWH,
    BRIDGE_BATTERY_RESERVE_KWH,
    DAYTIME_TIMED_EXPORT_MIN_SOC_PERCENT,
    ENABLE_EVENING_CONTROLLED_EXPORT,
    ESTIMATED_HOME_LOAD_KW,
    EVENING_EXPORT_ASSUMED_DISCHARGE_KW,
    EVENING_EXPORT_MAX_DURATION_MINUTES,
    EVENING_EXPORT_MIN_EXCESS_KWH,
    EVENING_EXPORT_MIN_SOC_PERCENT,
    EVENING_EXPORT_TRIGGER_SOC_PERCENT,
    HEADROOM_TARGET_KWH,
    MAX_PRE_PERIOD_WINDOW_MINUTES,
    MID_PERIOD_SAFETY_SOLAR_TRIGGER_KW,
    MORNING_HIGH_SOC_PROTECTION_ENABLED,
    MORNING_HIGH_SOC_THRESHOLD_PERCENT,
    SIGEN_MODES,
)
from logic.decision_logic import (
    calc_headroom_kwh,
    is_live_clipping_period_enabled,
)
from logic.decision_logging import log_decision_checkpoint
from logic.period_handler_shared import PeriodHandlerContext, _evaluate_period_mode_decision
from logic.schedule_utils import (
    get_cheap_rate_end_utc,
    get_hours_until_cheap_rate,
    get_schedule_period_for_time,
    is_cheap_rate_window,
)

logger = logging.getLogger(__name__)

PERIOD = "Eve"


def plan_evening_controlled_export(
    *,
    period: str,
    soc: float | None,
    now_utc: datetime,
) -> tuple[int | None, str | None]:
    """Return bounded evening export duration and rationale when conditions allow.

    The planner is intentionally conservative: it only considers evening export,
    enforces an SOC floor, protects expected home usage until cheap rate starts,
    and requires minimum excess energy before enabling timed export.

    Args:
        period: Current scheduler period name.
        soc: Current battery state-of-charge percentage.
        now_utc: Current scheduler timestamp in UTC.

    Returns:
        Tuple of (duration_minutes, reason). Returns (None, None) when export
        should not be started.
    """
    if not ENABLE_EVENING_CONTROLLED_EXPORT:
        return None, None
    if (period or "").upper() != "EVE":
        return None, None
    if soc is None:
        return None, None
    if soc < EVENING_EXPORT_TRIGGER_SOC_PERCENT:
        return None, None

    if get_schedule_period_for_time(now_utc) == "PEAK":
        return None, None

    hours_until_cheap_rate = get_hours_until_cheap_rate(now_utc)
    if hours_until_cheap_rate <= 0:
        return None, None

    battery_energy_kwh = BATTERY_KWH * (soc / 100.0)
    soc_floor_kwh = BATTERY_KWH * (EVENING_EXPORT_MIN_SOC_PERCENT / 100.0)
    expected_load_until_cheap_kwh = hours_until_cheap_rate * ESTIMATED_HOME_LOAD_KW
    protected_kwh = max(
        soc_floor_kwh,
        expected_load_until_cheap_kwh + BRIDGE_BATTERY_RESERVE_KWH,
    )
    exportable_excess_kwh = max(0.0, battery_energy_kwh - protected_kwh)
    if exportable_excess_kwh < EVENING_EXPORT_MIN_EXCESS_KWH:
        return None, None

    required_minutes = math.ceil(
        (exportable_excess_kwh / EVENING_EXPORT_ASSUMED_DISCHARGE_KW) * 60
    )
    duration_minutes = max(1, min(required_minutes, EVENING_EXPORT_MAX_DURATION_MINUTES))
    reason = (
        f"Battery has spare charge ({exportable_excess_kwh:.2f} kWh above the protected reserve) "
        f"with {hours_until_cheap_rate:.1f} hours until cheap rate — exporting the surplus now. "
        f"Battery at {soc:.1f}%, running for up to {duration_minutes} minutes."
    )
    return duration_minutes, reason


async def handle_evening_period(ctx: PeriodHandlerContext) -> bool:
    """Run all Evening period checks for a single scheduler tick.

    Handles mid-period clipping export, high-SOC safety export, pre-period
    export, controlled evening export planning, and period-start mode decisions
    for the Evening period.

    Args:
        ctx: Shared handler context carrying all period parameters.

    Returns:
        True if the outer period loop should continue to the next period,
        False to allow further period checks this tick.
    """
    now_utc = ctx.now_utc
    period_start = ctx.period_start
    period_end_utc = ctx.period_end_utc
    timed_export_override = ctx.timed_export_override
    solar_value = ctx.solar_value
    status = ctx.status
    period_solar_kwh = ctx.period_solar_kwh
    period_calibration = ctx.period_calibration
    fetch_soc = ctx.fetch_soc
    get_live_solar_average_kw = ctx.get_live_solar_average_kw
    get_effective_battery_export_kw = ctx.get_effective_battery_export_kw
    start_timed_grid_export = ctx.start_timed_grid_export
    apply_mode_change = ctx.apply_mode_change
    sigen = ctx.sigen
    mode_names = ctx.mode_names
    s = ctx.period_state

    # --- Mid-period live clipping export check ---
    if (
        s["start_set"]
        and not s["clipping_export_set"]
        and not timed_export_override["active"]
        and now_utc >= period_start
        and (period_end_utc is None or now_utc < period_end_utc)
        and is_live_clipping_period_enabled(PERIOD)
    ):
        soc = await fetch_soc(PERIOD)
        decision_status = status
        if soc is not None:
            solar_avg_kw_3 = get_live_solar_average_kw()
            decision_data = _evaluate_period_mode_decision(
                period=PERIOD,
                status=status,
                soc=soc,
                period_solar_kwh=period_solar_kwh,
                now_utc=now_utc,
                schedule_time_utc=now_utc,
                solar_avg_kw_3=solar_avg_kw_3,
            )
            decision_status = str(decision_data["decision_status"])
        if soc is not None and decision_status != status:
            headroom_kwh = float(decision_data["headroom_kwh"])
            headroom_target_kwh = float(decision_data["headroom_target_kwh"])
            headroom_deficit = float(decision_data["headroom_deficit_kwh"])
            mode = int(decision_data["mode"])
            reason = str(decision_data["reason"])
            if mode == SIGEN_MODES["GRID_EXPORT"] and headroom_deficit > 0:
                effective_battery_export_kw = get_effective_battery_export_kw(solar_avg_kw_3)
                duration_minutes = math.ceil(
                    (headroom_deficit / effective_battery_export_kw) * 60
                )
                log_decision_checkpoint(
                    PERIOD, "MID-PERIOD-CLIPPING",
                    mode_names=mode_names, now_utc=now_utc,
                    period_start_utc=period_start, solar_value=solar_value,
                    status=decision_status, period_solar_kwh=period_solar_kwh,
                    soc=soc, headroom_kwh=headroom_kwh,
                    headroom_target_kwh=headroom_target_kwh,
                    headroom_deficit_kwh=headroom_deficit, export_by_utc=now_utc,
                    solar_avg_kw_3=solar_avg_kw_3,
                    effective_battery_export_kw=effective_battery_export_kw,
                    mode=mode, reason=reason,
                    outcome="mid-period clipping export triggered",
                )
                override_started = await start_timed_grid_export(
                    period=PERIOD, reason=reason, duration_minutes=duration_minutes,
                    now_utc=now_utc, battery_soc=soc, is_clipping_export=True,
                )
                if override_started:
                    s["clipping_export_set"] = True
                    return True
            s["clipping_export_set"] = True

    # --- Mid-period high-SOC safety export check ---
    if (
        s["start_set"]
        and not s["high_soc_export_set"]
        and not timed_export_override["active"]
        and now_utc >= period_start
        and (period_end_utc is None or now_utc < period_end_utc)
        and MORNING_HIGH_SOC_PROTECTION_ENABLED
    ):
        mid_period_solar_kw = get_live_solar_average_kw()
        mid_period_soc = await fetch_soc(PERIOD)
        if (
            mid_period_soc is not None
            and mid_period_soc >= MORNING_HIGH_SOC_THRESHOLD_PERCENT
            and mid_period_solar_kw is not None
            and mid_period_solar_kw >= MID_PERIOD_SAFETY_SOLAR_TRIGGER_KW
        ):
            mid_period_headroom_kwh = calc_headroom_kwh(BATTERY_KWH, mid_period_soc)
            mid_period_headroom_target_kwh = HEADROOM_TARGET_KWH
            mid_period_headroom_deficit = max(0.0, mid_period_headroom_target_kwh - mid_period_headroom_kwh)
            if mid_period_headroom_deficit > 0:
                mid_period_effective_battery_export_kw = get_effective_battery_export_kw(
                    mid_period_solar_kw
                )
                mid_period_duration_minutes = math.ceil(
                    (mid_period_headroom_deficit / mid_period_effective_battery_export_kw) * 60
                )
                mid_period_reason = (
                    f"Battery is high ({mid_period_soc:.1f}%) and solar is strong "
                    f"({mid_period_solar_kw:.1f} kW) but only {mid_period_headroom_kwh:.2f} kWh "
                    f"headroom remains (needs {mid_period_headroom_target_kwh:.2f} kWh) — "
                    "exporting to make room."
                )
                log_decision_checkpoint(
                    PERIOD, "MID-PERIOD-HIGH-SOC-SAFETY",
                    mode_names=mode_names, now_utc=now_utc,
                    period_start_utc=period_start, solar_value=solar_value,
                    status=status, period_solar_kwh=period_solar_kwh,
                    soc=mid_period_soc, headroom_kwh=mid_period_headroom_kwh,
                    headroom_target_kwh=mid_period_headroom_target_kwh,
                    headroom_deficit_kwh=mid_period_headroom_deficit, export_by_utc=now_utc,
                    solar_avg_kw_3=mid_period_solar_kw,
                    effective_battery_export_kw=mid_period_effective_battery_export_kw,
                    mode=SIGEN_MODES["GRID_EXPORT"], reason=mid_period_reason,
                    outcome="mid-period high-SOC safety export triggered",
                )
                mid_period_override_started = await start_timed_grid_export(
                    period=PERIOD, reason=mid_period_reason,
                    duration_minutes=mid_period_duration_minutes,
                    now_utc=now_utc, battery_soc=mid_period_soc, is_clipping_export=True,
                    export_soc_floor=DAYTIME_TIMED_EXPORT_MIN_SOC_PERCENT,
                )
                if mid_period_override_started:
                    s["high_soc_export_set"] = True
                    return True

    # --- Pre-period export check ---
    _cheap_rate_end = get_cheap_rate_end_utc(now_utc)
    _pre_period_target = (
        max(period_start, _cheap_rate_end)
        if _cheap_rate_end is not None and _cheap_rate_end > period_start
        else period_start
    )
    if not s["pre_set"] and _pre_period_target - timedelta(minutes=MAX_PRE_PERIOD_WINDOW_MINUTES) <= now_utc < _pre_period_target:
        soc = await fetch_soc(PERIOD)
        if soc is not None:
            solar_avg_kw_3 = get_live_solar_average_kw()
            decision_data = _evaluate_period_mode_decision(
                period=PERIOD,
                status=status,
                soc=soc,
                period_solar_kwh=period_solar_kwh,
                now_utc=now_utc,
                schedule_time_utc=period_start,
                solar_avg_kw_3=solar_avg_kw_3,
            )
            decision_status = str(decision_data["decision_status"])
            headroom_kwh = float(decision_data["headroom_kwh"])
            headroom_target_kwh = float(decision_data["headroom_target_kwh"])
            headroom_deficit = float(decision_data["headroom_deficit_kwh"])
            mode = int(decision_data["mode"])
            reason = str(decision_data["reason"])
            effective_battery_export_kw = get_effective_battery_export_kw(solar_avg_kw_3)
            lead_time_hours_adjusted = 0.0
            if headroom_deficit > 0:
                lead_time_hours_adjusted = (
                    headroom_deficit * period_calibration["export_lead_buffer_multiplier"]
                ) / effective_battery_export_kw
                export_by = _pre_period_target - timedelta(hours=lead_time_hours_adjusted)
            else:
                export_by = _pre_period_target

            if now_utc >= export_by:
                pre_check_complete = False
                if mode == SIGEN_MODES["GRID_EXPORT"]:
                    duration_minutes = max(
                        1, math.ceil((_pre_period_target - now_utc).total_seconds() / 60)
                    )
                    log_decision_checkpoint(
                        PERIOD, "PRE-PERIOD",
                        mode_names=mode_names, now_utc=now_utc,
                        period_start_utc=period_start, solar_value=solar_value,
                        status=decision_status, period_solar_kwh=period_solar_kwh,
                        soc=soc, headroom_kwh=headroom_kwh,
                        headroom_target_kwh=headroom_target_kwh,
                        headroom_deficit_kwh=headroom_deficit, export_by_utc=export_by,
                        solar_avg_kw_3=solar_avg_kw_3,
                        effective_battery_export_kw=effective_battery_export_kw,
                        lead_time_hours_adjusted=lead_time_hours_adjusted,
                        mode=mode, reason=reason,
                        outcome="pre-period export triggered",
                    )
                    override_started = await start_timed_grid_export(
                        period=PERIOD, reason=reason, duration_minutes=duration_minutes,
                        now_utc=now_utc, battery_soc=soc,
                        export_soc_floor=DAYTIME_TIMED_EXPORT_MIN_SOC_PERCENT,
                    )
                    if not override_started:
                        logger.warning(
                            "[%s] Timed export activation did not start; leaving pre-period "
                            "check eligible for retry on next tick.",
                            PERIOD,
                        )
                        return True
                    pre_check_complete = True
                else:
                    log_decision_checkpoint(
                        PERIOD, "PRE-PERIOD",
                        mode_names=mode_names, now_utc=now_utc,
                        period_start_utc=period_start, solar_value=solar_value,
                        status=decision_status, period_solar_kwh=period_solar_kwh,
                        soc=soc, headroom_kwh=headroom_kwh,
                        headroom_target_kwh=headroom_target_kwh,
                        headroom_deficit_kwh=headroom_deficit, export_by_utc=export_by,
                        solar_avg_kw_3=solar_avg_kw_3,
                        effective_battery_export_kw=effective_battery_export_kw,
                        lead_time_hours_adjusted=lead_time_hours_adjusted,
                        mode=mode, reason=reason,
                        outcome="pre-period check concluded no export needed",
                    )
                    if headroom_deficit <= 0:
                        pre_check_complete = True
                    else:
                        logger.info(
                            "[%s] Retrying pre-period check next tick: headroom deficit "
                            "%.2f kWh remains and mode=%s.",
                            PERIOD,
                            headroom_deficit,
                            mode_names.get(mode, mode),
                        )

                if pre_check_complete:
                    s["pre_set"] = True
            else:
                log_decision_checkpoint(
                    PERIOD, "PRE-PERIOD",
                    mode_names=mode_names, now_utc=now_utc,
                    period_start_utc=period_start, solar_value=solar_value,
                    status=decision_status, period_solar_kwh=period_solar_kwh,
                    soc=soc, headroom_kwh=headroom_kwh,
                    headroom_target_kwh=headroom_target_kwh,
                    headroom_deficit_kwh=headroom_deficit, export_by_utc=export_by,
                    solar_avg_kw_3=solar_avg_kw_3,
                    effective_battery_export_kw=effective_battery_export_kw,
                    lead_time_hours_adjusted=lead_time_hours_adjusted,
                    mode=mode, reason=reason,
                    outcome="waiting until export window opens",
                )
                if now_utc >= period_start:
                    return True

    # --- Period-start: set the definitive mode ---
    if not s["start_set"] and now_utc >= period_start:
        soc = await fetch_soc(PERIOD)
        if soc is not None:
            solar_avg_kw_3 = get_live_solar_average_kw()
            decision_data = _evaluate_period_mode_decision(
                period=PERIOD,
                status=status,
                soc=soc,
                period_solar_kwh=period_solar_kwh,
                now_utc=now_utc,
                schedule_time_utc=period_start,
                solar_avg_kw_3=solar_avg_kw_3,
            )
            decision_status = str(decision_data["decision_status"])
            headroom_kwh = float(decision_data["headroom_kwh"])
            headroom_target_kwh = float(decision_data["headroom_target_kwh"])
            headroom_deficit = float(decision_data["headroom_deficit_kwh"])
            mode = int(decision_data["mode"])
            reason = str(decision_data["reason"])

            # Evening-specific: attempt controlled timed export before standard mode.
            evening_export_minutes, evening_export_reason = plan_evening_controlled_export(
                period=PERIOD, soc=soc, now_utc=now_utc,
            )
            if evening_export_minutes is not None and evening_export_reason is not None:
                log_decision_checkpoint(
                    PERIOD, "PERIOD-START",
                    mode_names=mode_names, now_utc=now_utc,
                    period_start_utc=period_start, solar_value=solar_value,
                    status=decision_status, period_solar_kwh=period_solar_kwh,
                    soc=soc, headroom_kwh=headroom_kwh,
                    headroom_target_kwh=headroom_target_kwh,
                    headroom_deficit_kwh=headroom_deficit, export_by_utc=period_start,
                    mode=SIGEN_MODES["GRID_EXPORT"], reason=evening_export_reason,
                    outcome="controlled evening timed export started",
                )
                override_started = await start_timed_grid_export(
                    period=PERIOD, reason=evening_export_reason,
                    duration_minutes=evening_export_minutes,
                    now_utc=now_utc, battery_soc=soc,
                    export_soc_floor=EVENING_EXPORT_MIN_SOC_PERCENT,
                )
                if override_started:
                    s["start_set"] = True
                    s["pre_set"] = True
                    return True
                logger.warning(
                    "[%s] Controlled evening export was eligible but failed to start. "
                    "Falling back to standard period-start mode.",
                    PERIOD,
                )

            if mode == SIGEN_MODES["GRID_EXPORT"]:
                effective_battery_export_kw = get_effective_battery_export_kw(solar_avg_kw_3)
                duration_minutes = max(
                    1, math.ceil((headroom_deficit / effective_battery_export_kw) * 60)
                )
                is_clipping_export = (
                    (status or "").upper() == "AMBER"
                    and (decision_status or "").upper() == "GREEN"
                )
                log_decision_checkpoint(
                    PERIOD, "PERIOD-START",
                    mode_names=mode_names, now_utc=now_utc,
                    period_start_utc=period_start, solar_value=solar_value,
                    status=decision_status, period_solar_kwh=period_solar_kwh,
                    soc=soc, headroom_kwh=headroom_kwh,
                    headroom_target_kwh=headroom_target_kwh,
                    headroom_deficit_kwh=headroom_deficit, export_by_utc=period_start,
                    solar_avg_kw_3=solar_avg_kw_3,
                    effective_battery_export_kw=effective_battery_export_kw,
                    mode=mode, reason=reason,
                    outcome="period-start timed export started",
                )
                override_started = await start_timed_grid_export(
                    period=PERIOD, reason=reason, duration_minutes=duration_minutes,
                    now_utc=now_utc, battery_soc=soc, is_clipping_export=is_clipping_export,
                    export_soc_floor=DAYTIME_TIMED_EXPORT_MIN_SOC_PERCENT,
                )
                if override_started:
                    s["start_set"] = True
                    s["pre_set"] = True
                    return True

                logger.warning(
                    "[%s] Period-start GRID_EXPORT decision could not start timed export. "
                    "Skipping direct mode set to avoid unbounded export and retrying next tick.",
                    PERIOD,
                )
                return True

            log_decision_checkpoint(
                PERIOD, "PERIOD-START",
                mode_names=mode_names, now_utc=now_utc,
                period_start_utc=period_start, solar_value=solar_value,
                status=decision_status, period_solar_kwh=period_solar_kwh,
                soc=soc, headroom_kwh=headroom_kwh,
                headroom_target_kwh=headroom_target_kwh,
                headroom_deficit_kwh=headroom_deficit, export_by_utc=period_start,
                mode=mode, reason=reason,
                outcome="period start mode applied",
            )
            if is_cheap_rate_window(now_utc):
                logger.info(
                    "[%s] Deferring period-start mode override — cheap-rate window active. "
                    "Will retry on next tick after cheap-rate window ends.",
                    PERIOD,
                )
            else:
                ok = await apply_mode_change(
                    sigen=sigen,
                    mode=mode,
                    period=f"{PERIOD} (period-start)",
                    reason=reason,
                    mode_names=mode_names,
                    battery_soc=soc,
                )
                if ok:
                    s["start_set"] = True
                    s["pre_set"] = True

    return False

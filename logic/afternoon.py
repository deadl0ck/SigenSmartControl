"""
logic/afternoon.py
------------------
Afternoon period scheduler handler.

Manages pre-period export checks, period-start mode decisions, and
mid-period clipping/high-SOC safety exports for the Afternoon (Aftn) period.
"""

import logging
import math
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from config.settings import (
    BATTERY_KWH,
    BRIDGE_BATTERY_RESERVE_KWH,
    DAYTIME_TIMED_EXPORT_MIN_SOC_PERCENT,
    ESTIMATED_HOME_LOAD_KW,
    HEADROOM_TARGET_KWH,
    LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT,
    LIVE_CLIPPING_RISK_SOLAR_TRIGGER_KW,
    MAX_PRE_PERIOD_WINDOW_MINUTES,
    MID_PERIOD_SAFETY_SOLAR_TRIGGER_KW,
    MORNING_HIGH_SOC_PROTECTION_ENABLED,
    MORNING_HIGH_SOC_THRESHOLD_PERCENT,
    SIGEN_MODES,
)
from logic.decision_logic import (
    DecisionContext,
    calc_headroom_kwh,
    decide_operational_mode,
    is_live_clipping_period_enabled,
)
from logic.decision_logging import log_decision_checkpoint
from logic.schedule_utils import (
    LOCAL_TZ,
    get_hours_until_cheap_rate,
    get_schedule_period_for_time,
    is_cheap_rate_window,
)

logger = logging.getLogger("sigen_control")

PERIOD = "Aftn"


def _promote_status_for_live_clipping_risk(
    period: str,
    status: str,
    soc: float | None,
    avg_live_solar_kw: float | None,
) -> tuple[str, str | None]:
    """Promote Amber forecast status to Green when live clipping risk is high.

    Args:
        period: Current period name (e.g., Morn/Aftn/Eve).
        status: Forecast status for the period.
        soc: Current battery SOC percentage.
        avg_live_solar_kw: Rolling live solar average in kW.

    Returns:
        Tuple of (effective_status, override_reason). override_reason is None
        when no promotion is applied.
    """
    status_key = (status or "").upper()

    if not is_live_clipping_period_enabled(period):
        return status, None
    if status_key != "AMBER":
        return status, None
    if soc is None or soc < LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT:
        return status, None
    if avg_live_solar_kw is None:
        return status, None

    trigger_kw = LIVE_CLIPPING_RISK_SOLAR_TRIGGER_KW
    if avg_live_solar_kw < trigger_kw:
        return status, None

    reason = (
        "Live clipping-risk override: promoting AMBER to GREEN because "
        f"SOC={soc:.1f}% and avg live solar={avg_live_solar_kw:.2f} kW is near "
        f"or above configured trigger ({trigger_kw:.1f} kW)."
    )
    return "Green", reason


def _evaluate_period_mode_decision(
    *,
    period: str,
    status: str,
    soc: float,
    period_solar_kwh: float,
    now_utc: datetime,
    schedule_time_utc: datetime,
    solar_avg_kw_3: float | None,
) -> dict[str, Any]:
    """Evaluate mode and headroom metrics for a period decision point.

    Args:
        period: Scheduler period label (e.g., Morn/Aftn/Eve).
        status: Forecast status before live clipping-risk promotion.
        soc: Current battery state-of-charge percentage.
        period_solar_kwh: Estimated period solar energy in kWh.
        now_utc: Current scheduler tick timestamp in UTC.
        schedule_time_utc: Timestamp used to derive tariff schedule period.
        solar_avg_kw_3: Rolling average live solar generation in kW.

    Returns:
        Dict with decision_status, reason, mode, headroom_kwh,
        headroom_target_kwh, headroom_deficit_kwh, and status_override_reason.
    """
    decision_status, status_override_reason = _promote_status_for_live_clipping_risk(
        period, status, soc, solar_avg_kw_3
    )
    headroom_kwh = calc_headroom_kwh(BATTERY_KWH, soc)
    headroom_target_kwh = HEADROOM_TARGET_KWH
    headroom_deficit_kwh = max(0.0, headroom_target_kwh - headroom_kwh)
    mode, reason = decide_operational_mode(
        DecisionContext(
            period=period,
            status=decision_status,
            soc=soc,
            headroom_kwh=headroom_kwh,
            headroom_target_kwh=HEADROOM_TARGET_KWH,
            live_solar_kw=solar_avg_kw_3,
            hours_until_cheap_rate=get_hours_until_cheap_rate(now_utc),
            estimated_home_load_kw=ESTIMATED_HOME_LOAD_KW,
            bridge_battery_reserve_kwh=BRIDGE_BATTERY_RESERVE_KWH,
            tariff=get_schedule_period_for_time(schedule_time_utc),
        )
    )
    if status_override_reason is not None:
        reason = f"{status_override_reason} {reason}"

    return {
        "decision_status": decision_status,
        "status_override_reason": status_override_reason,
        "headroom_kwh": headroom_kwh,
        "headroom_target_kwh": headroom_target_kwh,
        "headroom_deficit_kwh": headroom_deficit_kwh,
        "mode": mode,
        "reason": reason,
    }


async def handle_afternoon_period(
    *,
    now_utc: datetime,
    period_start: datetime,
    period_end_utc: datetime | None,
    period_state: dict[str, Any],
    timed_export_override: dict[str, Any],
    solar_value: int,
    status: str,
    period_solar_kwh: float,
    period_calibration: dict[str, Any],
    fetch_soc: Callable[[str], Awaitable[float | None]],
    get_live_solar_average_kw: Callable[[], float | None],
    get_effective_battery_export_kw: Callable[[float | None], float],
    start_timed_grid_export: Callable[..., Awaitable[bool]],
    apply_mode_change: Callable[..., Awaitable[bool]],
    sigen: Any,
    mode_names: dict[int, str],
) -> bool:
    """Run all Afternoon period checks for a single scheduler tick.

    Handles mid-period clipping export, high-SOC safety export, pre-period
    export, and period-start mode decisions for the Afternoon period.

    Args:
        now_utc: Current scheduler tick time in UTC.
        period_start: Scheduled start time for the Afternoon period in UTC.
        period_end_utc: End time of this period (start of Evening) in UTC, or None.
        period_state: Mutable state dict for this period (pre_set, start_set, clipping_export_set).
        timed_export_override: Shared mutable timed export state dict.
        solar_value: Forecasted solar power in watts.
        status: Forecast status string (Green/Amber/Red).
        period_solar_kwh: Estimated solar energy for the period in kWh.
        period_calibration: Calibration multipliers for this period.
        fetch_soc: Async callable returning current battery SOC or None.
        get_live_solar_average_kw: Returns rolling live solar average in kW.
        get_effective_battery_export_kw: Returns effective battery export kW.
        start_timed_grid_export: Async callable to begin a bounded timed export.
        apply_mode_change: Async callable to apply a mode change with tracking.
        sigen: Sigen API interaction instance.
        mode_names: Mapping of mode integer values to display labels.

    Returns:
        True if the outer period loop should continue to the next period,
        False to allow further period checks this tick.
    """
    s = period_state

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
        and not timed_export_override["active"]
        and now_utc >= period_start
        and (period_end_utc is None or now_utc < period_end_utc)
        and MORNING_HIGH_SOC_PROTECTION_ENABLED
    ):
        solar_avg_kw_3_safety = get_live_solar_average_kw()
        soc_safety = await fetch_soc(PERIOD)
        if (
            soc_safety is not None
            and soc_safety >= MORNING_HIGH_SOC_THRESHOLD_PERCENT
            and solar_avg_kw_3_safety is not None
            and solar_avg_kw_3_safety >= MID_PERIOD_SAFETY_SOLAR_TRIGGER_KW
        ):
            headroom_kwh_safety = calc_headroom_kwh(BATTERY_KWH, soc_safety)
            headroom_target_kwh_safety = HEADROOM_TARGET_KWH
            headroom_deficit_safety = max(0.0, headroom_target_kwh_safety - headroom_kwh_safety)
            if headroom_deficit_safety > 0:
                effective_battery_export_kw_safety = get_effective_battery_export_kw(
                    solar_avg_kw_3_safety
                )
                duration_minutes_safety = math.ceil(
                    (headroom_deficit_safety / effective_battery_export_kw_safety) * 60
                )
                reason_safety = (
                    f"High-SOC safety export: SOC {soc_safety:.1f}% >= "
                    f"{MORNING_HIGH_SOC_THRESHOLD_PERCENT:.0f}% threshold, "
                    f"solar {solar_avg_kw_3_safety:.1f} kW >= "
                    f"{MID_PERIOD_SAFETY_SOLAR_TRIGGER_KW:.1f} kW trigger, "
                    f"headroom {headroom_kwh_safety:.2f} kWh < target "
                    f"{headroom_target_kwh_safety:.2f} kWh"
                )
                log_decision_checkpoint(
                    PERIOD, "MID-PERIOD-HIGH-SOC-SAFETY",
                    mode_names=mode_names, now_utc=now_utc,
                    period_start_utc=period_start, solar_value=solar_value,
                    status=status, period_solar_kwh=period_solar_kwh,
                    soc=soc_safety, headroom_kwh=headroom_kwh_safety,
                    headroom_target_kwh=headroom_target_kwh_safety,
                    headroom_deficit_kwh=headroom_deficit_safety, export_by_utc=now_utc,
                    solar_avg_kw_3=solar_avg_kw_3_safety,
                    effective_battery_export_kw=effective_battery_export_kw_safety,
                    mode=SIGEN_MODES["GRID_EXPORT"], reason=reason_safety,
                    outcome="mid-period high-SOC safety export triggered",
                )
                override_started_safety = await start_timed_grid_export(
                    period=PERIOD, reason=reason_safety,
                    duration_minutes=duration_minutes_safety,
                    now_utc=now_utc, battery_soc=soc_safety, is_clipping_export=True,
                )
                if override_started_safety:
                    return True

    # --- Pre-period export check ---
    if not s["pre_set"] and period_start - timedelta(minutes=MAX_PRE_PERIOD_WINDOW_MINUTES) <= now_utc < period_start:
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
                export_by = period_start - timedelta(hours=lead_time_hours_adjusted)
            else:
                export_by = period_start

            if now_utc >= export_by:
                pre_check_complete = False
                if mode == SIGEN_MODES["GRID_EXPORT"]:
                    duration_minutes = max(
                        1, math.ceil((period_start - now_utc).total_seconds() / 60)
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

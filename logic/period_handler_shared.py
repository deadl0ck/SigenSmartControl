"""
logic/period_handler_shared.py
-------------------------------
Shared helper functions used across daytime period handlers.

All three daytime period handlers (morning, afternoon, evening) share identical
implementations of live-clipping-risk promotion and mode-decision evaluation.
This module holds those helpers in a single place to eliminate duplication and
keep period handler files focused on their own control-flow logic.

Functions defined here must NOT be modified to add period-specific behaviour.
If a future need arises for period-specific logic inside these helpers, move the
diverging copy back into the relevant handler file and leave the others here.
"""

from datetime import datetime
from typing import Any

from config.settings import (
    BATTERY_KWH,
    BRIDGE_BATTERY_RESERVE_KWH,
    ESTIMATED_HOME_LOAD_KW,
    HEADROOM_TARGET_KWH,
    LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT,
    LIVE_CLIPPING_RISK_SOLAR_TRIGGER_KW,
)
from logic.decision_logic import (
    DecisionContext,
    calc_headroom_kwh,
    decide_operational_mode,
    is_live_clipping_period_enabled,
)
from logic.schedule_utils import (
    get_hours_until_cheap_rate,
    get_schedule_period_for_time,
)


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

"""immersion_control.py
-----------------------
Controls the immersion water heater via a SwitchBot switch.

Triggers a boost when solar generation is strong and the battery is well-charged,
consuming surplus renewable energy locally rather than exporting it. The immersion
heater's built-in timer handles the one-hour cutoff automatically — this code only
needs to send the turn-on signal.

State is a plain dict on SchedulerState tracking today's boost count so the daily
limit is respected. No persistence to disk is needed — a cold start simply
re-evaluates conditions on the next qualifying tick.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from config.constants import (
    SWITCHBOT_IMMERSION_DEVICE_ID,
    SWITCHBOT_SECRET,
    SWITCHBOT_TOKEN,
)
from config.settings import (
    FULL_SIMULATION_MODE,
    SWITCHBOT_IMMERSION_ENABLED,
    SWITCHBOT_IMMERSION_MAX_BOOSTS_PER_DAY,
    SWITCHBOT_IMMERSION_MIN_SOC_PERCENT,
    SWITCHBOT_IMMERSION_SOLAR_TRIGGER_KW,
    SWITCHBOT_IMMERSION_VALID_PERIODS,
)
from integrations.switchbot_interaction import turn_on


async def check_immersion_boost(
    *,
    immersion_state: dict[str, Any],
    now_utc: datetime,
    today_local: date,
    soc_percent: float | None,
    live_solar_avg_kw: float | None,
    active_period: str | None,
    logger: logging.Logger,
) -> None:
    """Trigger an immersion boost if conditions are met and the daily limit allows.

    The heater's built-in timer cuts power after one hour — no turn-off command
    is needed. This function only decides whether to send the turn-on signal.

    Args:
        immersion_state: Mutable dict tracking boosts_today and last_boost_date.
        now_utc: Current UTC timestamp.
        today_local: Current local date, used to reset the daily boost counter.
        soc_percent: Battery SOC percentage, or None if unavailable this tick.
        live_solar_avg_kw: Rolling average live solar in kW, or None if unavailable.
        active_period: Name of the currently active period (e.g. 'Morn'), or None.
        logger: Logger instance.
    """
    if not SWITCHBOT_IMMERSION_ENABLED:
        return
    if not SWITCHBOT_IMMERSION_DEVICE_ID:
        logger.debug("[IMMERSION] Device ID not configured — skipping.")
        return
    if not SWITCHBOT_TOKEN or not SWITCHBOT_SECRET:
        logger.warning("[IMMERSION] SwitchBot credentials not set — skipping.")
        return

    # Reset daily counter on day rollover
    if immersion_state.get("last_boost_date") != today_local:
        immersion_state["boosts_today"] = 0
        immersion_state["last_boost_date"] = today_local

    if immersion_state.get("boosts_today", 0) >= SWITCHBOT_IMMERSION_MAX_BOOSTS_PER_DAY:
        return

    if active_period not in SWITCHBOT_IMMERSION_VALID_PERIODS:
        return

    if soc_percent is None or soc_percent < SWITCHBOT_IMMERSION_MIN_SOC_PERCENT:
        return

    if live_solar_avg_kw is None or live_solar_avg_kw < SWITCHBOT_IMMERSION_SOLAR_TRIGGER_KW:
        return

    logger.info(
        "[IMMERSION] Conditions met — SOC=%.1f%% (≥%.1f%%), solar=%.2f kW (≥%.2f kW), "
        "period=%s. Triggering boost.",
        soc_percent,
        SWITCHBOT_IMMERSION_MIN_SOC_PERCENT,
        live_solar_avg_kw,
        SWITCHBOT_IMMERSION_SOLAR_TRIGGER_KW,
        active_period,
    )

    if FULL_SIMULATION_MODE:
        logger.info("[IMMERSION] SIMULATION: would turn immersion ON via SwitchBot.")
    else:
        try:
            result = await turn_on(SWITCHBOT_IMMERSION_DEVICE_ID, SWITCHBOT_TOKEN, SWITCHBOT_SECRET)
            logger.info("[IMMERSION] Boost triggered. Response: %s", result)
        except Exception as exc:
            logger.error("[IMMERSION] Failed to trigger boost: %s", exc)
            return

    immersion_state["boosts_today"] = immersion_state.get("boosts_today", 0) + 1

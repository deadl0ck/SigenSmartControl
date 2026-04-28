"""immersion_control.py
-----------------------
Controls the immersion water heater via a SwitchBot switch.

Triggers a timed boost when solar generation is strong and the battery is
well-charged, using renewable energy that would otherwise be exported. Respects
a per-day boost limit and only fires during configured daytime periods.

State is stored in a plain dict (immersion_state) on SchedulerState so it
survives across ticks but does not need to be persisted to disk — a cold start
simply re-evaluates conditions on the next tick.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any

from config.constants import (
    SWITCHBOT_IMMERSION_DEVICE_ID,
    SWITCHBOT_SECRET,
    SWITCHBOT_TOKEN,
)
from config.settings import (
    FULL_SIMULATION_MODE,
    SWITCHBOT_IMMERSION_BOOST_DURATION_MINUTES,
    SWITCHBOT_IMMERSION_ENABLED,
    SWITCHBOT_IMMERSION_MAX_BOOSTS_PER_DAY,
    SWITCHBOT_IMMERSION_MIN_SOC_PERCENT,
    SWITCHBOT_IMMERSION_SOLAR_TRIGGER_KW,
    SWITCHBOT_IMMERSION_VALID_PERIODS,
)
from integrations.switchbot_interaction import turn_off, turn_on

BOOST_DURATION = timedelta(minutes=SWITCHBOT_IMMERSION_BOOST_DURATION_MINUTES)


def make_immersion_state() -> dict[str, Any]:
    """Return the initial (empty) immersion state dict.

    Returns:
        Fresh immersion state with no active boost and zero daily count.
    """
    return {
        "active": False,
        "activated_at": None,
        "boosts_today": 0,
        "last_boost_date": None,
    }


async def _issue_turn_on(logger: logging.Logger) -> bool:
    """Call SwitchBot turnOn and return True on success.

    Args:
        logger: Logger instance.

    Returns:
        True if the command succeeded or was simulated.
    """
    if FULL_SIMULATION_MODE:
        logger.info("[IMMERSION] SIMULATION: would turn immersion ON via SwitchBot.")
        return True
    try:
        result = await turn_on(SWITCHBOT_IMMERSION_DEVICE_ID, SWITCHBOT_TOKEN, SWITCHBOT_SECRET)
        logger.info("[IMMERSION] Turned ON. Response: %s", result)
        return True
    except Exception as exc:
        logger.error("[IMMERSION] Failed to turn ON: %s", exc)
        return False


async def _issue_turn_off(logger: logging.Logger) -> bool:
    """Call SwitchBot turnOff and return True on success.

    Args:
        logger: Logger instance.

    Returns:
        True if the command succeeded or was simulated.
    """
    if FULL_SIMULATION_MODE:
        logger.info("[IMMERSION] SIMULATION: would turn immersion OFF via SwitchBot.")
        return True
    try:
        result = await turn_off(SWITCHBOT_IMMERSION_DEVICE_ID, SWITCHBOT_TOKEN, SWITCHBOT_SECRET)
        logger.info("[IMMERSION] Turned OFF. Response: %s", result)
        return True
    except Exception as exc:
        logger.error("[IMMERSION] Failed to turn OFF: %s", exc)
        return False


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
    """Evaluate whether to start or stop an immersion boost this tick.

    Called every scheduler tick. Handles both the turn-on decision (when
    conditions are met) and the timed turn-off (when boost duration expires).

    Args:
        immersion_state: Mutable dict tracking boost state across ticks.
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

    # Handle active boost: check if duration exceeded and turn off
    if immersion_state.get("active"):
        activated_at: datetime | None = immersion_state.get("activated_at")
        if activated_at is not None and now_utc >= activated_at + BOOST_DURATION:
            logger.info(
                "[IMMERSION] %d-minute boost complete — turning off.",
                SWITCHBOT_IMMERSION_BOOST_DURATION_MINUTES,
            )
            if await _issue_turn_off(logger):
                immersion_state["active"] = False
                immersion_state["activated_at"] = None
        else:
            remaining_minutes = (
                int(((activated_at + BOOST_DURATION) - now_utc).total_seconds() / 60)
                if activated_at else 0
            )
            logger.info("[IMMERSION] Boost active — %d min remaining.", remaining_minutes)
        return

    # Guard: daily limit
    if immersion_state.get("boosts_today", 0) >= SWITCHBOT_IMMERSION_MAX_BOOSTS_PER_DAY:
        return

    # Guard: must be in a valid period
    if active_period not in SWITCHBOT_IMMERSION_VALID_PERIODS:
        return

    # Guard: SOC must be above threshold
    if soc_percent is None or soc_percent < SWITCHBOT_IMMERSION_MIN_SOC_PERCENT:
        return

    # Guard: live solar must be above trigger
    if live_solar_avg_kw is None or live_solar_avg_kw < SWITCHBOT_IMMERSION_SOLAR_TRIGGER_KW:
        return

    logger.info(
        "[IMMERSION] Conditions met — SOC=%.1f%% (≥%.1f%%), solar=%.2f kW (≥%.2f kW), "
        "period=%s. Starting %d-minute boost.",
        soc_percent,
        SWITCHBOT_IMMERSION_MIN_SOC_PERCENT,
        live_solar_avg_kw,
        SWITCHBOT_IMMERSION_SOLAR_TRIGGER_KW,
        active_period,
        SWITCHBOT_IMMERSION_BOOST_DURATION_MINUTES,
    )
    if await _issue_turn_on(logger):
        immersion_state["active"] = True
        immersion_state["activated_at"] = now_utc
        immersion_state["boosts_today"] = immersion_state.get("boosts_today", 0) + 1

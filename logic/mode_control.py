"""
mode_control.py
---------------
Inverter operational mode management and decision logic.

Handles mode detection, mode matching from various API response formats,
and coordinating mode changes with idempotency checks.
"""

import logging
from typing import Any

from integrations.sigen_interaction import SigenInteraction
from config.settings import (
    ENABLE_EVENING_AI_MODE_TRANSITION,
    EVENING_AI_MODE_START_HOUR,
    SIGEN_MODE_LABEL_TO_VALUE,
)
from logic.schedule_utils import LOCAL_TZ

logger = logging.getLogger("sigen_control")
ACTION_DIVIDER = "=" * 100


def _normalize_mode_label(label: str) -> str:
    """Normalize a mode label for tolerant matching.

    Args:
        label: Raw mode label text from API or configuration.

    Returns:
        Lower-cased and whitespace-normalized label string.
    """
    return " ".join(label.strip().lower().split())


_NORMALIZED_LABEL_MODE_MAP: dict[str, int] = {
    _normalize_mode_label(label): value
    for label, value in SIGEN_MODE_LABEL_TO_VALUE.items()
}


def should_use_ai_mode_for_evening(period: str, now_utc: Any) -> tuple[bool, str]:
    """Determine whether to switch to AI Mode for evening period profit-max optimization.
    
    AI Mode is recommended for evening when:
    - Evening period is active (user configured via EVENING_AI_MODE_START_HOUR)
    - Enables automatic battery arbitrage: discharge at day rates, recharge at cheap night rates
    
    Args:
        period: Current period name (typically 'Eve' for evening).
        now_utc: Current time in UTC.
        
    Returns:
        Tuple of (should_use_ai_mode: bool, reason: str)
    """
    if not ENABLE_EVENING_AI_MODE_TRANSITION:
        return False, ""
    
    if period.upper() != "EVE":
        return False, ""
    
    local_hour = now_utc.astimezone(LOCAL_TZ).hour
    if local_hour < EVENING_AI_MODE_START_HOUR:
        return False, f"Local hour {local_hour} is before EVENING_AI_MODE_START_HOUR ({EVENING_AI_MODE_START_HOUR})"
    
    return True, (
        f"Using AI Mode for Evening period: triggered at local hour {local_hour} "
        f"(>= {EVENING_AI_MODE_START_HOUR}). "
        f"This allows automatic profit-max battery arbitrage before cheap-rate window opens."
    )


def extract_mode_value(raw_mode: Any) -> int | None:
    """Extract numeric mode value from various response formats returned by the API.
    
    Handles multiple formats: raw integers, stringified numbers, dicts with mode keys, etc.
    
    Args:
        raw_mode: Mode value in any format (int, string, dict, etc.) as returned by
                  the Sigen API.
        
    Returns:
        Integer mode value (one of the SIGEN_MODES values), or None if extraction fails
        or the response does not contain a numeric mode.
    """
    if isinstance(raw_mode, int):
        return raw_mode
    if isinstance(raw_mode, str):
        if raw_mode.isdigit():
            return int(raw_mode)
        normalized = _normalize_mode_label(raw_mode)
        mapped = _NORMALIZED_LABEL_MODE_MAP.get(normalized)
        if mapped is not None:
            return mapped
        return None
    if isinstance(raw_mode, dict):
        for key in ("mode", "operationalMode", "operational_mode", "value"):
            value = raw_mode.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
            if isinstance(value, str):
                normalized = _normalize_mode_label(value)
                mapped = _NORMALIZED_LABEL_MODE_MAP.get(normalized)
                if mapped is not None:
                    return mapped
        for key in ("label", "name"):
            value = raw_mode.get(key)
            if isinstance(value, str):
                normalized = _normalize_mode_label(value)
                mapped = _NORMALIZED_LABEL_MODE_MAP.get(normalized)
                if mapped is not None:
                    return mapped
    return None


def mode_matches_target(raw_mode: Any, target_mode: int, mode_names: dict[int, str]) -> bool:
    """Check whether a raw mode response already represents the target mode.
    
    Handles multiple response formats: numeric values, string labels, and dict structures.
    Uses numeric comparison first, then falls back to human-readable label matching
    (e.g., 'Sigen AI Mode' contains 'AI' which matches AI mode).
    
    Args:
        raw_mode: Current mode from API (can be int, string, or dict).
        target_mode: Numeric mode value we want to match against.
        mode_names: Mapping from numeric mode to human-readable label (e.g., {1: 'AI'}).
        
    Returns:
        True if raw_mode already represents target_mode (avoiding redundant API calls),
        False otherwise.
    """
    current_mode = extract_mode_value(raw_mode)
    if current_mode is not None:
        return current_mode == target_mode

    target_label = mode_names.get(target_mode)
    if not target_label:
        return False

    target_norm = target_label.strip().lower()
    if isinstance(raw_mode, str):
        value_norm = raw_mode.strip().lower()
        return value_norm == target_norm or target_norm in value_norm

    if isinstance(raw_mode, dict):
        label = raw_mode.get("label")
        if isinstance(label, str):
            value_norm = label.strip().lower()
            return value_norm == target_norm or target_norm in value_norm

    return False


async def log_current_mode_on_startup(sigen: SigenInteraction, mode_names: dict[int, str]) -> None:
    """Log the inverter's current operational mode during startup.
    
    Args:
        sigen: SigenInteraction instance for API calls.
        mode_names: Mapping from numeric mode to human-readable label.
    """
    try:
        current_mode_raw = await sigen.get_operational_mode()
        current_mode = extract_mode_value(current_mode_raw)
        logger.info(ACTION_DIVIDER)
        logger.info("STARTUP CHECK: fetched current inverter mode")
        if current_mode is not None:
            logger.info(
                f"Current mode is {mode_names.get(current_mode, current_mode)} (value={current_mode})"
            )
        else:
            logger.info(f"Current mode response (unparsed): {current_mode_raw}")
        logger.info(ACTION_DIVIDER)
    except Exception as e:
        logger.error(f"Failed to fetch current inverter mode on startup: {e}")


async def apply_mode_change(
    *,
    sigen: SigenInteraction | None,
    mode: int,
    period: str,
    reason: str,
    mode_names: dict[int, str],
) -> bool:
    """Attempt to change the inverter operational mode with idempotency checks.
    
    Reads the current mode before writing; if already at target mode, logs and returns True
    without calling the API. Falls back to set attempt if read fails.
    
    Args:
        sigen: SigenInteraction instance, or None in dry-run mode.
        mode: Target numeric mode value.
        period: Human-readable period/context label for logging.
        reason: Explanation of why this mode change is being made.
        mode_names: Mapping from numeric mode to human-readable label.
        
    Returns:
        True if mode was set or already at target, False if set operation failed.
    """
    mode_label = mode_names.get(mode, mode)
    if sigen is None:
        logger.error(f"Cannot set mode for {period}: Sigen interaction is unavailable.")
        return False

    try:
        current_mode_raw = await sigen.get_operational_mode()
        if mode_matches_target(current_mode_raw, mode, mode_names):
            logger.info(ACTION_DIVIDER)
            logger.info("Skipping inverter set_operational_mode (already at target mode)")
            logger.info(f"Target period/context: {period}")
            logger.info(f"Target mode: {mode_label} (value={mode})")
            logger.info(f"Decision reason: {reason}")
            logger.info(ACTION_DIVIDER)
            return True
    except Exception as e:
        logger.warning(
            f"Could not read current inverter mode before setting {mode_label} for {period}: {e}. "
            "Proceeding with mode set attempt."
        )

    logger.info(ACTION_DIVIDER)
    logger.info("Calling inverter set_operational_mode")
    logger.info(f"Target period/context: {period}")
    logger.info(f"Target mode: {mode_label} (value={mode})")
    logger.info(f"Decision reason: {reason}")
    logger.info(ACTION_DIVIDER)

    try:
        response = await sigen.set_operational_mode(mode)
        logger.info(f"Set mode response for {period}: {response}")
        return True
    except Exception as e:
        logger.error(f"Failed to set mode for {period}: {e}")
        return False



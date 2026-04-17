"""
mode_control.py
---------------
Inverter operational mode management and decision logic.

Handles mode detection, mode matching from various API response formats,
and coordinating mode changes with idempotency checks.
"""

import logging
from typing import Any

from config.settings import (
    SIGEN_MODE_LABEL_TO_VALUE,
)

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



"""mode_logging.py
-----------------
Helpers for standardized inverter mode logging.

This module centralizes human-readable logging of raw operational mode payloads
so scheduler and helper modules can emit consistent mode-status lines.
"""

import logging
from typing import Any

from logic.mode_control import extract_mode_value


logger = logging.getLogger(__name__)


def log_mode_status(context: str, current_mode_raw: Any, mode_names: dict[int, str]) -> None:
    """Log pulled inverter mode state in a standardized format.

    Args:
        context: Human-readable context for where mode status was pulled.
        current_mode_raw: Raw mode payload returned by inverter API.
        mode_names: Mapping of numeric mode value to mode label.
    """
    current_mode = extract_mode_value(current_mode_raw)
    if current_mode is not None:
        logger.info(
            "[MODE STATUS] %s -> %s (value=%s), raw=%s",
            context,
            mode_names.get(current_mode, current_mode),
            current_mode,
            current_mode_raw,
        )
        return
    logger.info("[MODE STATUS] %s -> unparsed raw=%s", context, current_mode_raw)
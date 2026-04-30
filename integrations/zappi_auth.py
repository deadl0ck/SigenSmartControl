"""Singleton accessor for the ZappiInteraction instance.

Returns None when credentials are absent so the feature is a complete
no-op for users who have not configured myenergi credentials.
"""

from __future__ import annotations

import logging
import os

from integrations.zappi_interaction import ZappiInteraction

logger = logging.getLogger(__name__)

_zappi_instance: ZappiInteraction | None = None
_zappi_init_attempted: bool = False


def get_zappi_interaction() -> ZappiInteraction | None:
    """Return the singleton ZappiInteraction, or None if credentials are absent.

    Logs once on first call. Returns the cached instance on subsequent calls.
    """
    global _zappi_instance, _zappi_init_attempted
    if _zappi_init_attempted:
        return _zappi_instance
    _zappi_init_attempted = True

    if not os.getenv("MYENERGI_HUB_SERIAL", "").strip():
        logger.info(
            "[ZAPPI] MYENERGI_HUB_SERIAL not set — Zappi integration disabled."
        )
        return None

    try:
        _zappi_instance = ZappiInteraction.create_from_env()
        logger.info("[ZAPPI] Zappi integration enabled.")
    except RuntimeError as exc:
        logger.warning("[ZAPPI] Zappi integration disabled: %s", exc)
        _zappi_instance = None

    return _zappi_instance


def reset_zappi_instance() -> None:
    """Clear the cached singleton (used in tests)."""
    global _zappi_instance, _zappi_init_attempted
    _zappi_instance = None
    _zappi_init_attempted = False

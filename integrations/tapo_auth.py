"""Singleton accessor for the TapoPlug instance.

Returns None when credentials are absent so the feature is a complete
no-op for users without a Tapo P110 plug.
"""

from __future__ import annotations

import logging
import os

from integrations.tapo_client import TapoPlug

logger = logging.getLogger(__name__)

_tapo_instance: TapoPlug | None = None
_tapo_init_attempted: bool = False


def get_tapo_plug() -> TapoPlug | None:
    """Return the singleton TapoPlug, or None if TAPO_HOST is unset or incomplete."""
    global _tapo_instance, _tapo_init_attempted
    if _tapo_init_attempted:
        return _tapo_instance
    _tapo_init_attempted = True

    if not os.getenv("TAPO_HOST", "").strip():
        logger.info("[TAPO] TAPO_HOST not set — Tapo plug integration disabled.")
        return None

    try:
        _tapo_instance = TapoPlug.create_from_env()
        logger.info("[TAPO] Tapo plug integration enabled.")
    except RuntimeError as exc:
        logger.warning("[TAPO] Tapo plug integration disabled: %s", exc)
        _tapo_instance = None

    return _tapo_instance


def reset_tapo_instance() -> None:
    """Clear the cached singleton (used in tests)."""
    global _tapo_instance, _tapo_init_attempted
    _tapo_instance = None
    _tapo_init_attempted = False

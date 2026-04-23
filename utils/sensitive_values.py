"""Helpers for masking sensitive values before logging.

This module centralizes redaction logic so callers can safely log configuration
and environment-derived values without leaking secrets.
"""

from typing import Any


def mask_sensitive_value(value: Any, key: str | None = None) -> Any:
    """Mask sensitive values while preserving non-sensitive data.

    Args:
        value: Value to inspect and potentially mask.
        key: Optional key name associated with the value.

    Returns:
        Redacted value when sensitive, otherwise the original value.
    """
    if key and key.upper() in ("SIGEN_PASSWORD",):
        return "***MASKED***"
    if not isinstance(value, str):
        return value
    if any(token in value.upper() for token in ("PASS", "SECRET", "TOKEN")):
        return value[:2] + "***MASKED***" + value[-2:]
    return value

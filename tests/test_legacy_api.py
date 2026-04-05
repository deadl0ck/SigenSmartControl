"""Unit tests for the legacy API diagnostic script.

These tests cover the lightweight normalization and helper behavior used by the
read-only legacy diagnostics CLI.
"""

from typing import Any

from scripts.test_legacy_api import get_async_method, normalize_payload


def test_normalize_payload_parses_legacy_signals_blob() -> None:
    """Legacy signal fragments should be normalized into structured JSON."""
    payload = (
        '"success","data":[{"signalId":2008,"signalValue":"100.0","unit":"%"},'
        '{"signalId":2941,"signalKey":"di_power_mode","signalValue":"0","unit":""}]}'
    )

    normalized = normalize_payload(payload)

    assert normalized["status"] == "success"
    assert isinstance(normalized["data"], list)
    assert normalized["data"][0]["signalId"] == 2008
    assert normalized["data"][0]["signalValue"] == "100.0"
    assert normalized["data"][1]["signalKey"] == "di_power_mode"


def test_get_async_method_returns_callable_only_when_present() -> None:
    """Async helper lookup should return the bound method or None when missing."""

    class DummyClient:
        """Minimal client for method lookup testing."""

        async def get_signals(self) -> dict[str, Any]:
            """Return a dummy signal payload."""
            return {"ok": True}

    client = DummyClient()

    assert get_async_method(client, "get_signals") is not None
    assert get_async_method(client, "missing_method") is None
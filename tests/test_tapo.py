"""Tests for the Tapo P110 granny-charger plug integration."""

import asyncio
from unittest.mock import AsyncMock, patch

from integrations import tapo_auth
from integrations.tapo_client import TapoPlug
from notifications.notification_email_helpers import _build_tapo_email_sections


def test_accessor_disabled_without_host(monkeypatch):
    monkeypatch.delenv("TAPO_HOST", raising=False)
    tapo_auth.reset_tapo_instance()
    assert tapo_auth.get_tapo_plug() is None
    tapo_auth.reset_tapo_instance()


def test_accessor_enabled_with_env(monkeypatch):
    monkeypatch.setenv("TAPO_HOST", "1.2.3.4")
    monkeypatch.setenv("TAPO_USERNAME", "a@b.c")
    monkeypatch.setenv("TAPO_PASSWORD", "pw")
    tapo_auth.reset_tapo_instance()
    assert tapo_auth.get_tapo_plug() is not None
    tapo_auth.reset_tapo_instance()


def test_get_live_status_returns_none_on_error():
    plug = TapoPlug("1.2.3.4", "u", "p")
    with patch.object(plug, "_read", AsyncMock(side_effect=OSError("down"))):
        assert asyncio.run(plug.get_live_status()) is None


def test_email_section_empty_when_unavailable():
    assert _build_tapo_email_sections(None) == ("", "")


def test_email_section_contains_values():
    text, html = _build_tapo_email_sections(
        {"name": "Granny Charger", "is_on": True, "power_w": 2300.0, "today_kwh": 4.5, "month_kwh": 80.0}
    )
    assert "2.30 kW" in text and "4.50 kWh" in text and "On" in text
    assert "Tapo P110" in html

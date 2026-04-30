"""Unit tests for Zappi EV charger integration modules."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock

import pytest

import integrations.zappi_auth as zappi_auth_module
from integrations.zappi_client import ZappiClient
from integrations.zappi_interaction import ZappiInteraction


# ---------------------------------------------------------------------------
# ZappiClient tests
# ---------------------------------------------------------------------------


def test_create_from_env_raises_when_hub_serial_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MYENERGI_HUB_SERIAL", raising=False)
    monkeypatch.delenv("MYENERGI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MYENERGI_HUB_SERIAL"):
        ZappiClient.create_from_env()


def test_create_from_env_raises_when_api_key_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYENERGI_HUB_SERIAL", "12345678")
    monkeypatch.delenv("MYENERGI_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="MYENERGI_API_KEY"):
        ZappiClient.create_from_env()


def test_create_from_env_succeeds_with_hub_and_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MYENERGI_HUB_SERIAL", "12345678")
    monkeypatch.setenv("MYENERGI_API_KEY", "testkey")
    client = ZappiClient.create_from_env()
    assert isinstance(client, ZappiClient)
    assert client._hub_serial == "12345678"
    assert client._api_key == "testkey"
    assert client._zappi_serial is None  # discovered lazily on first status call


@pytest.mark.asyncio
async def test_get_server_caches_after_discovery() -> None:
    client = ZappiClient("hub1", "key1")
    client._discover_server = AsyncMock(return_value=("s18.myenergi.net", []))

    server1 = await client._get_server()
    server2 = await client._get_server()

    assert server1 == "s18.myenergi.net"
    assert server2 == "s18.myenergi.net"
    client._discover_server.assert_called_once()


@pytest.mark.asyncio
async def test_get_live_status_uses_correct_url_and_caches_serial() -> None:
    client = ZappiClient("hub1", "key1")
    client._server = "s18.myenergi.net"

    mock_response = MagicMock()
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(
        return_value={"zappi": [{"sno": "12345678", "pst": "C1", "che": 5.2}]}
    )

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(return_value=mock_response)

    client._make_session = MagicMock(return_value=mock_session)
    result = await client.get_live_status()

    assert result == [{"sno": "12345678", "pst": "C1", "che": 5.2}]
    called_url = mock_session.get.call_args[0][0]
    assert "s18.myenergi.net" in called_url
    assert "cgi-jstatus-Z" in called_url
    assert client._zappi_serial == "12345678"  # auto-discovered from response


@pytest.mark.asyncio
async def test_get_daily_history_builds_correct_url() -> None:
    client = ZappiClient("hub1", "key1")
    client._server = "s18.myenergi.net"
    client._zappi_serial = "99999999"  # pre-set as if already discovered
    target = date(2026, 4, 15)

    mock_response = MagicMock()
    mock_response.__aenter__ = AsyncMock(return_value=mock_response)
    mock_response.__aexit__ = AsyncMock(return_value=False)
    mock_response.raise_for_status = MagicMock()
    mock_response.json = AsyncMock(return_value={"U99999999": [{"imp": 500, "h1d": 200}]})

    mock_session = MagicMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.get = MagicMock(return_value=mock_response)

    client._make_session = MagicMock(return_value=mock_session)
    result = await client.get_daily_history(target)

    assert result == [{"imp": 500, "h1d": 200}]
    called_url = mock_session.get.call_args[0][0]
    assert "2026-4-15" in called_url
    assert "99999999" in called_url


@pytest.mark.asyncio
async def test_get_daily_history_raises_without_serial() -> None:
    client = ZappiClient("hub1", "key1")
    with pytest.raises(RuntimeError, match="serial not yet discovered"):
        await client.get_daily_history(date(2026, 4, 15))


# ---------------------------------------------------------------------------
# ZappiInteraction tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_live_status_returns_normalized_dict() -> None:
    client = ZappiClient("hub1", "key1")
    client.get_live_status = AsyncMock(
        return_value=[{"sno": "12345678", "pst": "C1", "zmo": 2, "che": 7.3, "div": 3200}]
    )
    interaction = ZappiInteraction(client)
    result = await interaction.get_live_status()

    assert result is not None
    assert result["status_text"] == "Charging"
    assert result["is_charging"] is True
    assert result["mode_text"] == "Eco"
    assert result["session_energy_kwh"] == pytest.approx(7.3)
    assert result["charge_power_w"] == 3200


@pytest.mark.asyncio
async def test_get_live_status_returns_none_for_empty_list() -> None:
    client = ZappiClient("hub1", "key1")
    client.get_live_status = AsyncMock(return_value=[])
    interaction = ZappiInteraction(client)
    result = await interaction.get_live_status()
    assert result is None


@pytest.mark.asyncio
async def test_get_live_status_ev_disconnected() -> None:
    client = ZappiClient("hub1", "key1")
    client.get_live_status = AsyncMock(
        return_value=[{"sno": "12345678", "pst": "A", "zmo": 1, "che": 0.0, "div": 0}]
    )
    interaction = ZappiInteraction(client)
    result = await interaction.get_live_status()
    assert result is not None
    assert result["status_text"] == "EV Disconnected"
    assert result["is_charging"] is False
    assert result["mode_text"] == "Fast"


@pytest.mark.asyncio
async def test_get_daily_totals_sums_wh_records() -> None:
    client = ZappiClient("hub1", "key1")
    # Values are in joules; 3,600,000 J = 1 kWh.
    # h1d = solar diverted to EV; h1b = grid boosted to EV. imp is site import, not used.
    client.get_daily_history = AsyncMock(
        return_value=[
            {"imp": 99_999_999, "h1d": 1_800_000, "h1b": 3_600_000},  # 0.5 kWh div, 1.0 kWh boost
            {"imp": 99_999_999, "h1d": 5_400_000, "h1b": 7_200_000},  # 1.5 kWh div, 2.0 kWh boost
            {"imp": 99_999_999, "h1d": 2_880_000, "h1b": 0},          # 0.8 kWh div, 0.0 kWh boost
        ]
    )
    interaction = ZappiInteraction(client)
    result = await interaction.get_daily_totals(date(2026, 4, 15))
    assert result is not None
    assert result["diverted_kwh"] == pytest.approx(2.8)
    assert result["boosted_kwh"] == pytest.approx(3.0)
    assert result["total_kwh"] == pytest.approx(5.8)


@pytest.mark.asyncio
async def test_get_daily_totals_empty_history() -> None:
    client = ZappiClient("hub1", "key1")
    client.get_daily_history = AsyncMock(return_value=[])
    interaction = ZappiInteraction(client)
    result = await interaction.get_daily_totals(date(2026, 4, 15))
    assert result == {"total_kwh": 0.0, "diverted_kwh": 0.0, "boosted_kwh": 0.0}


# ---------------------------------------------------------------------------
# ZappiAuth (singleton) tests
# ---------------------------------------------------------------------------


def test_get_zappi_interaction_returns_none_when_no_hub_serial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zappi_auth_module.reset_zappi_instance()
    monkeypatch.delenv("MYENERGI_HUB_SERIAL", raising=False)
    result = zappi_auth_module.get_zappi_interaction()
    assert result is None
    zappi_auth_module.reset_zappi_instance()


def test_get_zappi_interaction_returns_none_for_partial_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zappi_auth_module.reset_zappi_instance()
    monkeypatch.setenv("MYENERGI_HUB_SERIAL", "12345678")
    monkeypatch.delenv("MYENERGI_API_KEY", raising=False)
    result = zappi_auth_module.get_zappi_interaction()
    assert result is None
    zappi_auth_module.reset_zappi_instance()


def test_get_zappi_interaction_returns_instance_when_credentials_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zappi_auth_module.reset_zappi_instance()
    monkeypatch.setenv("MYENERGI_HUB_SERIAL", "12345678")
    monkeypatch.setenv("MYENERGI_API_KEY", "testkey")
    result = zappi_auth_module.get_zappi_interaction()
    assert isinstance(result, ZappiInteraction)
    zappi_auth_module.reset_zappi_instance()


def test_get_zappi_interaction_caches_instance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    zappi_auth_module.reset_zappi_instance()
    monkeypatch.setenv("MYENERGI_HUB_SERIAL", "12345678")
    monkeypatch.setenv("MYENERGI_API_KEY", "testkey")
    first = zappi_auth_module.get_zappi_interaction()
    second = zappi_auth_module.get_zappi_interaction()
    assert first is second
    zappi_auth_module.reset_zappi_instance()

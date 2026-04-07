"""Unit tests for official Sigen OpenAPI client implementation.

All tests mock network behavior and never call live endpoints.
"""

import pytest
import base64

from integrations.sigen_official import (
    OFFICIAL_MODE_ENUM_TO_INT,
    OFFICIAL_MODE_INT_TO_ENUM,
    SigenOfficial,
)


def test_official_mode_maps_are_consistent() -> None:
    """Ensure int<->enum mode mappings are stable."""
    assert OFFICIAL_MODE_INT_TO_ENUM[0] == "MSC"
    assert OFFICIAL_MODE_INT_TO_ENUM[5] == "FFG"
    assert OFFICIAL_MODE_INT_TO_ENUM[8] == "NBI"

    assert OFFICIAL_MODE_ENUM_TO_INT["MSC"] == 0
    assert OFFICIAL_MODE_ENUM_TO_INT["FFG"] == 5
    assert OFFICIAL_MODE_ENUM_TO_INT["NBI"] == 8


@pytest.mark.asyncio
async def test_get_operational_mode_parses_mode_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Query mode should parse integer and map to official enum label."""
    client = SigenOfficial(username="u", password="p", system_id="SYS-1")
    client.access_token = "token"

    async def fake_request(*, method, path, payload, include_bearer=True, use_form_urlencoded=False):
        del method, path, payload, include_bearer, use_form_urlencoded
        return {"code": 0, "msg": "success", "data": {"energyStorageOperationMode": 5}}

    monkeypatch.setattr(client, "_request", fake_request)

    response = await client.get_operational_mode()
    assert response["mode"] == 5
    assert response["label"] == "Fully Feed-in to Grid"


@pytest.mark.asyncio
async def test_get_operational_mode_maps_observed_ai_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Official account query should map observed mode 1 to AI label."""
    client = SigenOfficial(username="u", password="p", system_id="SYS-1")
    client.access_token = "token"

    async def fake_request(*, method, path, payload, include_bearer=True, use_form_urlencoded=False):
        del method, path, payload, include_bearer, use_form_urlencoded
        return {"code": 0, "msg": "success", "data": '{"energyStorageOperationMode":"1"}'}

    monkeypatch.setattr(client, "_request", fake_request)

    response = await client.get_operational_mode()
    assert response["mode"] == 1
    assert response["label"] == "Sigen AI Mode"


@pytest.mark.asyncio
async def test_set_operational_mode_maps_int_to_enum(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mode set should convert integer mode to official enum payload."""
    client = SigenOfficial(username="u", password="p", system_id="SYS-1")
    client.access_token = "token"

    captured: dict[str, object] = {}

    async def fake_request(*, method, path, payload, include_bearer=True, use_form_urlencoded=False):
        captured["method"] = method
        captured["path"] = path
        captured["payload"] = payload
        captured["include_bearer"] = include_bearer
        captured["use_form_urlencoded"] = use_form_urlencoded
        return {"code": 0, "msg": "success", "data": True}

    monkeypatch.setattr(client, "_request", fake_request)

    response = await client.set_operational_mode(0)
    assert response["ok"] is True
    assert response["mode"] == 0
    assert response["mode_enum"] == "MSC"

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["systemId"] == "SYS-1"
    assert payload["energyStorageOperationMode"] == 0
    assert captured["use_form_urlencoded"] is False


@pytest.mark.asyncio
async def test_set_operational_mode_rejects_unsupported_mode() -> None:
    """Unsupported official mode integers should fail early."""
    client = SigenOfficial(username="u", password="p", system_id="SYS-1")

    with pytest.raises(ValueError):
        await client.set_operational_mode(1)


@pytest.mark.asyncio
async def test_get_system_summary_uses_official_monitoring_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """System summary should be queried via the configured official monitoring path."""
    client = SigenOfficial(username="u", password="p", system_id="SYS-1")
    client.access_token = "token"

    captured: dict[str, object] = {}

    async def fake_request(*, method, path, payload, include_bearer=True, use_form_urlencoded=False):
        captured["method"] = method
        captured["path"] = path
        del payload, include_bearer, use_form_urlencoded
        return {"code": 0, "msg": "success", "data": {"dailyPowerGeneration": 12.3}}

    monkeypatch.setattr(client, "_request", fake_request)

    response = await client.get_system_summary()
    assert response["dailyPowerGeneration"] == 12.3
    assert captured["method"] == "GET"
    assert captured["path"] == "/openapi/systems/SYS-1/summary"


@pytest.mark.asyncio
async def test_get_energy_flow_uses_official_monitoring_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Energy flow should be queried via the configured official monitoring path."""
    client = SigenOfficial(username="u", password="p", system_id="SYS-1")
    client.access_token = "token"

    captured: dict[str, object] = {}

    async def fake_request(*, method, path, payload, include_bearer=True, use_form_urlencoded=False):
        captured["method"] = method
        captured["path"] = path
        del payload, include_bearer, use_form_urlencoded
        return {"code": 0, "msg": "success", "data": {"pvPower": 4.2, "batterySoc": 77.0}}

    monkeypatch.setattr(client, "_request", fake_request)

    response = await client.get_energy_flow()
    assert response["pvPower"] == 4.2
    assert response["batterySoc"] == 77.0
    assert captured["method"] == "GET"
    assert captured["path"] == "/openapi/systems/SYS-1/energyFlow"


@pytest.mark.asyncio
async def test_get_device_realtime_uses_serial_query_param(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Device realtime should query configured path with serialNumber parameter."""
    client = SigenOfficial(username="u", password="p", system_id="SYS-1")
    client.access_token = "token"

    captured: dict[str, object] = {}

    async def fake_request(
        *,
        method,
        path,
        payload,
        include_bearer=True,
        use_form_urlencoded=False,
        query_params=None,
    ):
        captured["method"] = method
        captured["path"] = path
        captured["query_params"] = query_params
        del payload, include_bearer, use_form_urlencoded
        return {
            "code": 0,
            "msg": "success",
            "data": {
                "serialNumber": "INV-1",
                "realTimeInfo": {"pv1Voltage": 420.0, "pv1Current": 4.2},
            },
        }

    monkeypatch.setattr(client, "_request", fake_request)

    response = await client.get_device_realtime("INV-1")
    assert response["serialNumber"] == "INV-1"
    assert captured["method"] == "GET"
    assert captured["path"] == "/openapi/systems/SYS-1/device/realtime"
    assert captured["query_params"] == {"serialNumber": "INV-1"}


@pytest.mark.asyncio
async def test_get_device_realtime_requires_serial_number() -> None:
    """Device realtime should fail fast when serial number is empty."""
    client = SigenOfficial(username="u", password="p", system_id="SYS-1")

    with pytest.raises(ValueError, match="serial_number is required"):
        await client.get_device_realtime("   ")


@pytest.mark.asyncio
async def test_async_initialize_fetches_system_id_when_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Initialization should resolve system_id from system list when not preconfigured."""
    client = SigenOfficial(username="u", password="p", system_id=None)

    async def fake_authenticate() -> None:
        client.access_token = "token"

    async def fake_get_system_list():
        return [{"systemId": "AUTO-SYS-1"}]

    monkeypatch.setattr(client, "authenticate", fake_authenticate)
    monkeypatch.setattr(client, "get_system_list", fake_get_system_list)

    await client.async_initialize()
    assert client.system_id == "AUTO-SYS-1"


@pytest.mark.asyncio
async def test_authenticate_with_account_parses_json_string_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Account auth should parse API responses where data is a JSON-encoded string."""
    client = SigenOfficial(username="u", password="p", auth_mode="account", system_id="SYS-1")

    async def fake_request(*, method, path, payload, include_bearer=False, use_form_urlencoded=False):
        del method, path, payload, include_bearer, use_form_urlencoded
        return {
            "code": 0,
            "msg": "success",
            "data": '{"accessToken": "abc123", "expiresIn": 43199, "tokenType": "Bearer"}',
        }

    monkeypatch.setattr(client, "_request", fake_request)

    await client._authenticate_with_account()
    assert client.access_token == "abc123"


@pytest.mark.asyncio
async def test_authenticate_with_key_uses_base64_encoded_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Key auth should send base64(AppKey:AppSecret) in payload."""
    client = SigenOfficial(
        app_key="my-app-key",
        app_secret="my-app-secret",
        auth_mode="key",
        system_id="SYS-1",
    )

    captured: dict[str, object] = {}

    async def fake_request(*, method, path, payload, include_bearer=False, use_form_urlencoded=False):
        captured["payload"] = payload
        captured["use_form_urlencoded"] = use_form_urlencoded
        del method, path, include_bearer
        return {
            "code": 0,
            "msg": "success",
            "data": '{"accessToken": "keytoken"}',
        }

    monkeypatch.setattr(client, "_request", fake_request)

    await client._authenticate_with_key()
    assert client.access_token == "keytoken"

    payload = captured["payload"]
    assert isinstance(payload, dict)
    expected = base64.b64encode(b"my-app-key:my-app-secret").decode("utf-8")
    assert payload["key"] == expected


@pytest.mark.asyncio
async def test_strict_official_mode_does_not_use_legacy_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict official mode should fail after official attempts without fallback."""
    client = SigenOfficial(
        username="u",
        password="p",
        auth_mode="account",
        strict_official_only=True,
        system_id="SYS-1",
    )

    calls = {"count": 0}

    async def failing_request(*, method, path, payload, include_bearer=False, use_form_urlencoded=False):
        del method, path, payload, include_bearer, use_form_urlencoded
        calls["count"] += 1
        raise RuntimeError("forced auth fail")

    monkeypatch.setattr(client, "_request", failing_request)

    with pytest.raises(RuntimeError, match="Official-only mode is enabled"):
        await client._authenticate_with_account()

    # Official chain has 3 attempts before fallback would normally occur.
    assert calls["count"] == 3
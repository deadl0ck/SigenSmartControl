"""Unit tests for client selection in sigen_auth module."""

import pytest

import integrations.sigen_auth as sigen_auth


@pytest.mark.asyncio
async def test_get_sigen_instance_uses_official_client_when_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SIGEN_CLIENT_IMPL=official should use official client factory."""
    monkeypatch.setenv("SIGEN_CLIENT_IMPL", "official")

    async def fake_create_from_env():
        return {"client": "official"}

    monkeypatch.setattr(sigen_auth, "_sigen_instance", None)
    monkeypatch.setattr(sigen_auth.SigenOfficial, "create_from_env", fake_create_from_env)

    client = await sigen_auth.get_sigen_instance()
    assert client == {"client": "official"}


@pytest.mark.asyncio
async def test_get_sigen_instance_uses_legacy_client_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default path should instantiate legacy Sigen class."""
    monkeypatch.delenv("SIGEN_CLIENT_IMPL", raising=False)
    monkeypatch.setenv("SIGEN_USERNAME", "u")
    monkeypatch.setenv("SIGEN_PASSWORD", "p")

    class FakeSigen:
        def __init__(self, username: str, password: str) -> None:
            self.username = username
            self.password = password
            self.initialized = False

        async def async_initialize(self) -> None:
            self.initialized = True

    monkeypatch.setattr(sigen_auth, "_sigen_instance", None)
    monkeypatch.setattr(sigen_auth, "Sigen", FakeSigen)

    client = await sigen_auth.get_sigen_instance()
    assert isinstance(client, FakeSigen)
    assert client.initialized is True


@pytest.mark.asyncio
async def test_refresh_sigen_instance_forces_new_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refresh_sigen_instance should clear cache and rebuild client."""
    created: list[dict[str, str]] = []

    async def fake_get_sigen_instance() -> dict[str, str]:
        client = {"client": f"n{len(created) + 1}"}
        created.append(client)
        sigen_auth._sigen_instance = client
        return client

    monkeypatch.setattr(sigen_auth, "_sigen_instance", {"client": "cached"})
    monkeypatch.setattr(sigen_auth, "get_sigen_instance", fake_get_sigen_instance)

    refreshed = await sigen_auth.refresh_sigen_instance()
    assert refreshed == {"client": "n1"}
    assert sigen_auth._sigen_instance == {"client": "n1"}
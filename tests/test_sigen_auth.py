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
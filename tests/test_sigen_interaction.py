import pytest

"""Unit tests for the Sigen inverter interaction layer (sigen_interaction.py).

Tests mode setting, simulation mode, and API wrapper behavior.
"""

from integrations.sigen_interaction import SigenInteraction


class DummyClient:
    def __init__(self):
        self.set_calls = []

    async def get_operational_mode(self):
        return {"mode": 1}

    async def set_operational_mode(self, mode: int):
        self.set_calls.append(mode)
        return {"ok": True, "mode": mode}

    async def get_energy_flow(self):
        return {"batterySoc": 81}

    async def get_operational_modes(self):
        return [{"label": "AI", "value": 1}]


class FlakyAuthClient(DummyClient):
    def __init__(self, error_message: str = "Invalid grant"):
        super().__init__()
        self.calls = 0
        self.error_message = error_message

    async def get_energy_flow(self):
        self.calls += 1
        raise RuntimeError(self.error_message)


@pytest.mark.asyncio
async def test_sigen_interaction_from_client_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch FULL_SIMULATION_MODE to False to test pass-through behavior
    import integrations.sigen_interaction as sigen_interaction
    monkeypatch.setattr(sigen_interaction, "FULL_SIMULATION_MODE", False)
    
    dummy = DummyClient()
    interaction = SigenInteraction.from_client(dummy)

    assert await interaction.get_operational_mode() == {"mode": 1}
    assert await interaction.get_energy_flow() == {"batterySoc": 81}
    assert await interaction.get_operational_modes() == [{"label": "AI", "value": 1}]

    set_resp = await interaction.set_operational_mode(2)
    assert set_resp == {"ok": True, "mode": 2}
    assert dummy.set_calls == [2]


@pytest.mark.asyncio
async def test_sigen_interaction_create_uses_auth_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummyClient()

    async def fake_get_sigen_instance():
        return dummy

    monkeypatch.setattr("integrations.sigen_interaction.get_sigen_instance", fake_get_sigen_instance)

    interaction = await SigenInteraction.create()
    assert await interaction.get_operational_mode() == {"mode": 1}


@pytest.mark.asyncio
async def test_sigen_interaction_set_operational_mode_respects_simulation_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Test that set_operational_mode respects FULL_SIMULATION_MODE
    import integrations.sigen_interaction as sigen_interaction
    monkeypatch.setattr(sigen_interaction, "FULL_SIMULATION_MODE", True)
    
    dummy = DummyClient()
    interaction = SigenInteraction.from_client(dummy)

    # In simulation mode, should return simulated response and NOT call the client
    set_resp = await interaction.set_operational_mode(2)
    assert set_resp == {"simulated": True, "mode": 2}
    # Verify the dummy client was NOT called
    assert dummy.set_calls == []


@pytest.mark.asyncio
async def test_sigen_interaction_reauth_retries_once_on_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import integrations.sigen_interaction as sigen_interaction

    first_client = FlakyAuthClient("Failed to refresh access token: Invalid grant")
    second_client = DummyClient()

    async def fake_refresh_sigen_instance():
        return second_client

    monkeypatch.setattr(sigen_interaction, "refresh_sigen_instance", fake_refresh_sigen_instance)

    interaction = SigenInteraction.from_client(first_client)
    result = await interaction.get_energy_flow()

    assert first_client.calls == 1
    assert result == {"batterySoc": 81}


@pytest.mark.asyncio
async def test_sigen_interaction_reauth_failure_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import integrations.sigen_interaction as sigen_interaction

    first_client = FlakyAuthClient("Invalid grant")
    second_client = FlakyAuthClient("Invalid grant")

    async def fake_refresh_sigen_instance():
        return second_client

    monkeypatch.setattr(sigen_interaction, "refresh_sigen_instance", fake_refresh_sigen_instance)

    interaction = SigenInteraction.from_client(first_client)

    with pytest.raises(RuntimeError, match="Invalid grant"):
        await interaction.get_energy_flow()

    assert first_client.calls == 1
    assert second_client.calls == 1

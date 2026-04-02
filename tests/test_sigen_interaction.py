import pytest

from sigen_interaction import SigenInteraction


class DummyClient:
    def __init__(self):
        self.set_calls = []

    async def get_operational_mode(self):
        return {"mode": 1}

    async def set_operational_mode(self, mode: int, profile_id: int = -1):
        self.set_calls.append((mode, profile_id))
        return {"ok": True, "mode": mode, "profile_id": profile_id}

    async def get_energy_flow(self):
        return {"batterySoc": 81}

    async def get_operational_modes(self):
        return [{"label": "AI", "value": 1}]


@pytest.mark.asyncio
async def test_sigen_interaction_from_client_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch FULL_SIMULATION_MODE to False to test pass-through behavior
    import sigen_interaction
    monkeypatch.setattr(sigen_interaction, "FULL_SIMULATION_MODE", False)
    
    dummy = DummyClient()
    interaction = SigenInteraction.from_client(dummy)

    assert await interaction.get_operational_mode() == {"mode": 1}
    assert await interaction.get_energy_flow() == {"batterySoc": 81}
    assert await interaction.get_operational_modes() == [{"label": "AI", "value": 1}]

    set_resp = await interaction.set_operational_mode(2, -1)
    assert set_resp == {"ok": True, "mode": 2, "profile_id": -1}
    assert dummy.set_calls == [(2, -1)]


@pytest.mark.asyncio
async def test_sigen_interaction_create_uses_auth_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummyClient()

    async def fake_get_sigen_instance():
        return dummy

    monkeypatch.setattr("sigen_interaction.get_sigen_instance", fake_get_sigen_instance)

    interaction = await SigenInteraction.create()
    assert await interaction.get_operational_mode() == {"mode": 1}


@pytest.mark.asyncio
async def test_sigen_interaction_set_operational_mode_respects_simulation_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Test that set_operational_mode respects FULL_SIMULATION_MODE
    import sigen_interaction
    monkeypatch.setattr(sigen_interaction, "FULL_SIMULATION_MODE", True)
    
    dummy = DummyClient()
    interaction = SigenInteraction.from_client(dummy)

    # In simulation mode, should return simulated response and NOT call the client
    set_resp = await interaction.set_operational_mode(2, -1)
    assert set_resp == {"simulated": True, "mode": 2, "profile_id": -1}
    # Verify the dummy client was NOT called
    assert dummy.set_calls == []

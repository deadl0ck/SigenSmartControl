from datetime import datetime, timedelta, timezone

import pytest

import main


def test_suppress_elapsed_periods_except_latest_marks_only_stale_periods() -> None:
    now = datetime(2026, 4, 2, 18, 0, tzinfo=timezone.utc)
    period_windows = {
        "Morn": now - timedelta(hours=9),
        "Aftn": now - timedelta(hours=5),
        "Eve": now - timedelta(hours=1),
    }
    day_state = {
        "Morn": {"pre_set": False, "start_set": False},
        "Aftn": {"pre_set": False, "start_set": False},
        "Eve": {"pre_set": False, "start_set": False},
    }

    suppressed = main.suppress_elapsed_periods_except_latest(now, period_windows, day_state)

    assert suppressed == ["Morn", "Aftn"]
    assert day_state["Morn"] == {"pre_set": True, "start_set": True}
    assert day_state["Aftn"] == {"pre_set": True, "start_set": True}
    assert day_state["Eve"] == {"pre_set": False, "start_set": False}


def test_suppress_elapsed_periods_except_latest_noop_when_single_or_none_elapsed() -> None:
    now = datetime(2026, 4, 2, 6, 0, tzinfo=timezone.utc)
    period_windows = {
        "Morn": now + timedelta(minutes=30),
        "Aftn": now + timedelta(hours=4),
        "Eve": now + timedelta(hours=8),
    }
    day_state = {
        "Morn": {"pre_set": False, "start_set": False},
        "Aftn": {"pre_set": False, "start_set": False},
        "Eve": {"pre_set": False, "start_set": False},
    }

    suppressed = main.suppress_elapsed_periods_except_latest(now, period_windows, day_state)

    assert suppressed == []
    assert all(not state["pre_set"] and not state["start_set"] for state in day_state.values())


@pytest.mark.asyncio
async def test_create_scheduler_interaction_returns_none_in_full_sim_on_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create():
        raise Exception("auth failed")

    monkeypatch.setattr(main, "FULL_SIMULATION_MODE", True)
    monkeypatch.setattr(main.SigenInteraction, "create", fake_create)

    result = await main.create_scheduler_interaction({})
    assert result is None


@pytest.mark.asyncio
async def test_create_scheduler_interaction_raises_when_not_in_simulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_create():
        raise Exception("auth failed")

    monkeypatch.setattr(main, "FULL_SIMULATION_MODE", False)
    monkeypatch.setattr(main.SigenInteraction, "create", fake_create)

    with pytest.raises(Exception, match="auth failed"):
        await main.create_scheduler_interaction({})


@pytest.mark.asyncio
async def test_create_scheduler_interaction_success_logs_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    interaction = object()
    called = {"startup_log": False}

    async def fake_create():
        return interaction

    async def fake_log_current_mode_on_startup(sigen, mode_names):
        called["startup_log"] = True
        assert sigen is interaction
        assert mode_names == {1: "AI"}

    monkeypatch.setattr(main.SigenInteraction, "create", fake_create)
    monkeypatch.setattr(main, "log_current_mode_on_startup", fake_log_current_mode_on_startup)

    result = await main.create_scheduler_interaction({1: "AI"})
    assert result is interaction
    assert called["startup_log"] is True


def test_get_hours_until_cheap_rate_returns_zero_when_already_cheap_window() -> None:
    now_utc = datetime(2026, 1, 15, 23, 30, tzinfo=timezone.utc)
    assert main.get_hours_until_cheap_rate(now_utc) == 0.0


def test_get_hours_until_cheap_rate_counts_down_before_cheap_window() -> None:
    now_utc = datetime(2026, 1, 15, 21, 0, tzinfo=timezone.utc)
    hours = main.get_hours_until_cheap_rate(now_utc)
    assert abs(hours - 2.0) < 0.01


class DummyModeInteraction:
    def __init__(self, current_mode: int):
        self.current_mode = current_mode
        self.set_calls: list[tuple[int, int]] = []

    async def get_operational_mode(self):
        return {"mode": self.current_mode}

    async def set_operational_mode(self, mode: int, profile_id: int):
        self.set_calls.append((mode, profile_id))
        self.current_mode = mode
        return {"ok": True, "mode": mode, "profile_id": profile_id}


@pytest.mark.asyncio
async def test_apply_mode_change_skips_when_already_target_mode() -> None:
    sigen = DummyModeInteraction(current_mode=1)

    ok = await main.apply_mode_change(
        sigen=sigen,
        mode=1,
        period="Eve (period-start)",
        reason="Already AI for evening arbitrage.",
        mode_names={1: "AI"},
    )

    assert ok is True
    assert sigen.set_calls == []


@pytest.mark.asyncio
async def test_apply_mode_change_sets_when_target_differs() -> None:
    sigen = DummyModeInteraction(current_mode=0)

    ok = await main.apply_mode_change(
        sigen=sigen,
        mode=1,
        period="Eve (period-start)",
        reason="Switching to AI for evening arbitrage.",
        mode_names={0: "SELF_POWERED", 1: "AI"},
    )

    assert ok is True
    assert sigen.set_calls == [(1, -1)]

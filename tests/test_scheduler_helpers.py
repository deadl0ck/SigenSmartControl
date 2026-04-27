"""Unit tests for scheduler helper functions from main.py.

Tests period suppression, scheduler initialization, and mode application logic.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

import logging

import main
import logic.mode_change as mode_change_module
import logic.timed_export as timed_export_module
from logic.mode_change import apply_mode_change
from logic.schedule_utils import is_pre_sunrise_discharge_window

_test_logger = logging.getLogger("test")


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


def test_suppress_elapsed_periods_except_latest_is_idempotent() -> None:
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

    first = main.suppress_elapsed_periods_except_latest(now, period_windows, day_state)
    second = main.suppress_elapsed_periods_except_latest(now, period_windows, day_state)

    assert first == ["Morn", "Aftn"]
    assert second == []
    assert day_state["Morn"] == {"pre_set": True, "start_set": True}
    assert day_state["Aftn"] == {"pre_set": True, "start_set": True}
    assert day_state["Eve"] == {"pre_set": False, "start_set": False}


def test_mid_period_window_end_excludes_morning_after_afternoon_starts() -> None:
    now = datetime(2026, 4, 2, 15, 32, tzinfo=timezone.utc)
    period_windows = {
        "Morn": datetime(2026, 4, 2, 8, 0, tzinfo=timezone.utc),
        "Aftn": datetime(2026, 4, 2, 12, 0, tzinfo=timezone.utc),
        "Eve": datetime(2026, 4, 2, 16, 0, tzinfo=timezone.utc),
    }
    ordered = sorted(period_windows.items(), key=lambda item: item[1])

    morn_index, (_, morn_start) = next(
        (index, item) for index, item in enumerate(ordered) if item[0] == "Morn"
    )
    aftn_index, (_, aftn_start) = next(
        (index, item) for index, item in enumerate(ordered) if item[0] == "Aftn"
    )

    morn_end = ordered[morn_index + 1][1]
    aftn_end = ordered[aftn_index + 1][1]

    assert not (now >= morn_start and now < morn_end)
    assert now >= aftn_start and now < aftn_end


def test_persist_and_load_timed_export_override(tmp_path) -> None:
    state_path = tmp_path / "timed_export_state.json"
    override = {
        "active": True,
        "started_at": datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
        "restore_at": datetime(2026, 4, 17, 13, 0, tzinfo=timezone.utc),
        "restore_mode": 0,
        "restore_mode_label": "SELF_POWERED",
        "trigger_period": "Morn",
        "duration_minutes": 60,
        "is_clipping_export": True,
        "clipping_soc_floor": 65.0,
        "export_soc_floor": None,
    }

    timed_export_module.persist_timed_export_override(override, logger=_test_logger, path=state_path)
    loaded = timed_export_module.load_timed_export_override(logger=_test_logger, path=state_path)

    assert state_path.exists()
    assert loaded["active"] is True
    assert loaded["restore_mode"] == 0
    assert loaded["trigger_period"] == "Morn"
    assert loaded["started_at"] == override["started_at"]
    assert loaded["restore_at"] == override["restore_at"]


def test_persist_timed_export_override_clears_inactive_file(tmp_path) -> None:
    state_path = tmp_path / "timed_export_state.json"

    timed_export_module.persist_timed_export_override(
        {
            "active": True,
            "started_at": datetime(2026, 4, 17, 12, 0, tzinfo=timezone.utc),
            "restore_at": datetime(2026, 4, 17, 13, 0, tzinfo=timezone.utc),
            "restore_mode": 0,
            "restore_mode_label": "SELF_POWERED",
            "trigger_period": "Morn",
            "duration_minutes": 60,
            "is_clipping_export": False,
            "clipping_soc_floor": None,
            "export_soc_floor": None,
        },
        logger=_test_logger,
        path=state_path,
    )
    assert state_path.exists()

    timed_export_module.persist_timed_export_override(
        timed_export_module._empty_timed_export_override(),
        logger=_test_logger,
        path=state_path,
    )

    assert not state_path.exists()


@pytest.mark.asyncio
async def test_create_scheduler_interaction_exits_after_retries_in_full_sim_on_auth_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    async def fake_create():
        calls["count"] += 1
        raise Exception("auth failed")

    monkeypatch.setattr(main, "FULL_SIMULATION_MODE", True)
    monkeypatch.setattr(main.SigenInteraction, "create", fake_create)

    with pytest.raises(SystemExit) as exc:
        await main.create_scheduler_interaction({})

    assert exc.value.code == 1
    assert calls["count"] == 3


@pytest.mark.asyncio
async def test_create_scheduler_interaction_exits_after_retries_when_not_in_simulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"count": 0}

    async def fake_create():
        calls["count"] += 1
        raise Exception("auth failed")

    monkeypatch.setattr(main, "FULL_SIMULATION_MODE", False)
    monkeypatch.setattr(main.SigenInteraction, "create", fake_create)

    with pytest.raises(SystemExit) as exc:
        await main.create_scheduler_interaction({})

    assert exc.value.code == 1
    assert calls["count"] == 3


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
        return None, None, None

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


def test_order_daytime_periods_enforces_morn_aftn_eve_order() -> None:
    period_forecast = {
        "Eve": (3000, "Green"),
        "Morn": (1200, "Amber"),
        "Aftn": (2200, "Green"),
        "Night": (0, "Red"),
    }

    assert main.order_daytime_periods(period_forecast) == ["Morn", "Aftn", "Eve"]


def test_order_daytime_periods_appends_unknown_daytime_periods() -> None:
    period_forecast = {
        "Shoulder": (700, "Amber"),
        "Eve": (2100, "Green"),
        "Morn": (800, "Amber"),
        "Night": (0, "Red"),
    }

    assert main.order_daytime_periods(period_forecast) == ["Morn", "Eve", "Shoulder"]


class DummyModeInteraction:
    def __init__(self, current_mode):
        self.current_mode = current_mode
        self.set_calls: list[int] = []

    async def get_operational_mode(self):
        return self.current_mode

    async def set_operational_mode(self, mode: int):
        self.set_calls.append(mode)
        self.current_mode = {"mode": mode}
        return {"ok": True, "mode": mode}


@pytest.mark.asyncio
async def test_apply_mode_change_skips_when_already_target_mode() -> None:
    sigen = DummyModeInteraction(current_mode={"mode": 1})

    ok = await apply_mode_change(
        sigen=sigen,
        mode=1,
        period="Eve (period-start)",
        reason="Already at target mode — no change needed.",
        mode_names={1: "AI"},
        logger=_test_logger,
    )

    assert ok is True
    assert sigen.set_calls == []


@pytest.mark.asyncio
async def test_apply_mode_change_sets_when_target_differs() -> None:
    sigen = DummyModeInteraction(current_mode=0)

    ok = await apply_mode_change(
        sigen=sigen,
        mode=1,
        period="Eve (period-start)",
        reason="Switching to target mode.",
        mode_names={0: "SELF_POWERED", 1: "AI"},
        logger=_test_logger,
    )

    assert ok is True
    assert sigen.set_calls == [1]


@pytest.mark.asyncio
async def test_apply_mode_change_does_not_archive_during_pytest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sigen = DummyModeInteraction(current_mode=0)
    called = {"append": False}

    def fake_append_mode_change_event(**kwargs: Any) -> None:
        called["append"] = True

    monkeypatch.setattr(mode_change_module, "append_mode_change_event", fake_append_mode_change_event)

    ok = await apply_mode_change(
        sigen=sigen,
        mode=1,
        period="Eve (period-start)",
        reason="Switching to target mode.",
        mode_names={0: "SELF_POWERED", 1: "AI"},
        logger=_test_logger,
    )

    assert ok is True
    assert called["append"] is False


@pytest.mark.asyncio
async def test_apply_mode_change_simulation_triggers_email_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: dict[str, Any] = {"email": False}

    async def fake_notify(logger: Any, **kwargs: Any) -> None:
        called["email"] = True
        assert kwargs["success"] is True
        assert kwargs["period"] == "Night->Morn"
        assert kwargs["requested_mode"] == 1

    monkeypatch.setattr(mode_change_module, "FULL_SIMULATION_MODE", True)
    monkeypatch.setattr(mode_change_module, "_notify_mode_change_email", fake_notify)

    ok = await apply_mode_change(
        sigen=None,
        mode=1,
        period="Night->Morn",
        reason="Simulation email notification test.",
        mode_names={1: "AI"},
        logger=_test_logger,
    )

    assert ok is True
    assert called["email"] is True


def test_is_pre_sunrise_discharge_window_true_in_configured_month_and_window() -> None:
    now_utc = datetime(2026, 6, 15, 4, 45, tzinfo=timezone.utc)
    sunrise_utc = datetime(2026, 6, 15, 5, 30, tzinfo=timezone.utc)

    assert is_pre_sunrise_discharge_window(
        now_utc,
        sunrise_utc,
        enabled=True,
        months_csv="4,5,6,7,8,9",
        lead_minutes=60,
    ) is True


def test_is_pre_sunrise_discharge_window_false_outside_months() -> None:
    now_utc = datetime(2026, 1, 15, 6, 30, tzinfo=timezone.utc)
    sunrise_utc = datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)

    assert is_pre_sunrise_discharge_window(
        now_utc,
        sunrise_utc,
        enabled=True,
        months_csv="4,5,6,7,8,9",
        lead_minutes=60,
    ) is False

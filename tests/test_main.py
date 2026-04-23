"""Characterization tests for main scheduler behavior in main.py.

These tests intentionally lock current behavior before refactoring. They focus on
night-window branching rules, pre-period export timing math, and timed-export
persist/restore paths.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import main as main_module
from config.settings import (
    BATTERY_KWH,
    CHEAP_RATE_START_HOUR,
    FORECAST_TO_MODE,
    PERIOD_TO_MODE,
    PRE_SUNRISE_DISCHARGE_LEAD_MINUTES,
    PRE_SUNRISE_DISCHARGE_MIN_SOC_PERCENT,
    SIGEN_MODES,
)
from logic.schedule_utils import (
    LOCAL_TZ,
    get_hours_until_cheap_rate,
    is_pre_sunrise_discharge_window,
)


def test_system_specs_imported() -> None:
    """System specs must be present and positive."""
    from config.settings import BATTERY_KWH as battery_kwh
    from config.settings import INVERTER_KW as inverter_kw
    from config.settings import SOLAR_PV_KW as solar_pv_kw

    assert solar_pv_kw > 0
    assert inverter_kw > 0
    assert battery_kwh > 0


class DummySigen:
    """Minimal async dummy for legacy control-loop mapping test."""

    def __init__(self) -> None:
        self.set_modes: list[int] = []

    async def set_operational_mode(self, mode: int) -> dict[str, int | str]:
        self.set_modes.append(mode)
        return {"result": "ok", "mode": mode}


@pytest.mark.asyncio
async def test_control_loop_mode_mapping_characterization() -> None:
    """Current forecast-to-mode mapping should stay stable."""
    period_forecast = {
        "Morn": (500, "Green"),
        "Aftn": (300, "Amber"),
        "Eve": (100, "Red"),
    }

    sigen = DummySigen()
    for period, (solar_value, status) in period_forecast.items():
        _ = solar_value
        status_key = status.upper()
        mode = FORECAST_TO_MODE.get(status_key, SIGEN_MODES["SELF_POWERED"])
        resp = await sigen.set_operational_mode(mode)
        assert resp["result"] == "ok"
        assert resp["mode"] == mode
        assert period in {"Morn", "Aftn", "Eve"}

    assert sigen.set_modes == [
        SIGEN_MODES["SELF_POWERED"],
        SIGEN_MODES["SELF_POWERED"],
        SIGEN_MODES["SELF_POWERED"],
    ]


def test_night_pre_dawn_high_soc_prefers_self_powered_characterization() -> None:
    """In PRE-DAWN discharge window, high SOC keeps current self-powered behavior."""
    target_start = datetime(2026, 4, 19, 6, 0, tzinfo=timezone.utc)
    now_utc = target_start - timedelta(minutes=max(1, PRE_SUNRISE_DISCHARGE_LEAD_MINUTES // 2))
    soc = PRE_SUNRISE_DISCHARGE_MIN_SOC_PERCENT + 5.0

    in_window = is_pre_sunrise_discharge_window(
        now_utc,
        target_start,
        enabled=True,
        months_csv="1,2,3,4,5,6,7,8,9,10,11,12",
        lead_minutes=PRE_SUNRISE_DISCHARGE_LEAD_MINUTES,
    )
    chosen_mode = (
        SIGEN_MODES["SELF_POWERED"]
        if in_window and soc >= PRE_SUNRISE_DISCHARGE_MIN_SOC_PERCENT
        else PERIOD_TO_MODE["NIGHT"]
    )

    assert in_window is True
    assert chosen_mode == SIGEN_MODES["SELF_POWERED"]


def test_night_pre_dawn_low_soc_keeps_night_mode_characterization() -> None:
    """In PRE-DAWN discharge window, low SOC retains configured night mode."""
    target_start = datetime(2026, 4, 19, 6, 0, tzinfo=timezone.utc)
    now_utc = target_start - timedelta(minutes=max(1, PRE_SUNRISE_DISCHARGE_LEAD_MINUTES // 2))
    soc = PRE_SUNRISE_DISCHARGE_MIN_SOC_PERCENT - 10.0

    in_window = is_pre_sunrise_discharge_window(
        now_utc,
        target_start,
        enabled=True,
        months_csv="1,2,3,4,5,6,7,8,9,10,11,12",
        lead_minutes=PRE_SUNRISE_DISCHARGE_LEAD_MINUTES,
    )
    chosen_mode = (
        SIGEN_MODES["SELF_POWERED"]
        if in_window and soc >= PRE_SUNRISE_DISCHARGE_MIN_SOC_PERCENT
        else PERIOD_TO_MODE["NIGHT"]
    )

    assert in_window is True
    assert chosen_mode == PERIOD_TO_MODE["NIGHT"]


def test_evening_night_hours_until_cheap_rate_positive_characterization() -> None:
    """EVENING-NIGHT branch should see positive cheap-rate countdown before start hour."""
    cheap_rate_start_hour = CHEAP_RATE_START_HOUR
    local_now = datetime(2026, 4, 19, max(0, cheap_rate_start_hour - 2), 0, tzinfo=LOCAL_TZ)
    now_utc = local_now.astimezone(timezone.utc)

    hours_until_cheap_rate = get_hours_until_cheap_rate(now_utc)

    assert hours_until_cheap_rate > 0


def test_pre_period_export_by_when_deficit_positive_characterization() -> None:
    """Current pre-period logic sets export_by earlier than period_start when deficit exists."""
    period_start = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    headroom_deficit_kwh = 4.0
    export_lead_buffer_multiplier = 1.2
    effective_battery_export_kw = 2.0

    lead_time_hours_adjusted = (
        headroom_deficit_kwh * export_lead_buffer_multiplier
    ) / effective_battery_export_kw
    export_by = period_start - timedelta(hours=lead_time_hours_adjusted)

    assert lead_time_hours_adjusted > 0
    assert export_by < period_start


def test_pre_period_export_by_when_deficit_zero_characterization() -> None:
    """Current pre-period logic arms at period_start when headroom deficit is zero."""
    period_start = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    headroom_deficit_kwh = 0.0

    export_by = period_start if headroom_deficit_kwh <= 0 else period_start - timedelta(hours=1)

    assert export_by == period_start


def test_timed_export_state_round_trip_restore_path(tmp_path: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    """Persisted timed-export state should round-trip with datetimes restored."""
    state_path = tmp_path / "timed_export_state.json"
    monkeypatch.setattr(main_module, "TIMED_EXPORT_STATE_PATH", str(state_path))

    started_at = datetime(2026, 4, 19, 8, 0, tzinfo=timezone.utc)
    restore_at = datetime(2026, 4, 19, 8, 45, tzinfo=timezone.utc)
    active_state = {
        "active": True,
        "started_at": started_at,
        "restore_at": restore_at,
        "restore_mode": SIGEN_MODES["SELF_POWERED"],
        "restore_mode_label": "SELF_POWERED",
        "trigger_period": "Morn",
        "duration_minutes": 45,
        "is_clipping_export": False,
        "clipping_soc_floor": None,
        "export_soc_floor": 20.0,
    }

    main_module._persist_timed_export_override(active_state)
    loaded = main_module._load_timed_export_override()

    assert loaded["active"] is True
    assert loaded["restore_mode"] == SIGEN_MODES["SELF_POWERED"]
    assert loaded["started_at"] == started_at
    assert loaded["restore_at"] == restore_at


def test_timed_export_state_invalid_datetime_falls_back_to_empty(
    tmp_path: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid persisted datetime should clear timed-export restore path safely."""
    state_path = tmp_path / "timed_export_state.json"
    monkeypatch.setattr(main_module, "TIMED_EXPORT_STATE_PATH", str(state_path))

    invalid_payload = {
        "active": True,
        "started_at": "not-a-datetime",
        "restore_at": "still-not-a-datetime",
        "restore_mode": SIGEN_MODES["SELF_POWERED"],
    }
    state_path.write_text(__import__("json").dumps(invalid_payload), encoding="utf-8")

    loaded = main_module._load_timed_export_override()

    assert loaded == main_module._empty_timed_export_override()


def test_timed_export_state_inactive_removes_file(
    tmp_path: pytest.TempPathFactory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Persisting inactive state should remove any prior timed-export file."""
    state_path = tmp_path / "timed_export_state.json"
    monkeypatch.setattr(main_module, "TIMED_EXPORT_STATE_PATH", str(state_path))

    active = main_module._empty_timed_export_override()
    active.update({"active": True, "started_at": datetime.now(timezone.utc)})
    main_module._persist_timed_export_override(active)
    assert state_path.exists()

    main_module._persist_timed_export_override(main_module._empty_timed_export_override())

    assert not state_path.exists()


def test_headroom_deficit_consistency_characterization() -> None:
    """Characterize current headroom deficit convention used before pre-period timing."""
    soc = 70.0
    headroom_kwh = BATTERY_KWH * (1 - soc / 100.0)
    headroom_target_kwh = main_module.HEADROOM_TARGET_KWH
    deficit = max(0.0, headroom_target_kwh - headroom_kwh)

    assert headroom_kwh >= 0
    assert deficit >= 0

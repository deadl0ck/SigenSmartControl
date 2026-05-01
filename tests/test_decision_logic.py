"""Unit tests for the decision logic engine (decision_logic.py).

Tests mode selection algorithms under various battery, solar, and tariff conditions.
"""

from config.settings import SIGEN_MODES
from logic.decision_logic import DecisionContext, decide_operational_mode
from logic.schedule_utils import is_cheap_rate_window
from datetime import datetime, timezone


def test_cheap_rate_window_is_true_during_cheap_tariff_hours():
    # 2026-01-15 23:30 Europe/Dublin = 23:30 UTC in winter.
    now_utc = datetime(2026, 1, 15, 23, 30, tzinfo=timezone.utc)
    assert is_cheap_rate_window(now_utc) is True


def test_cheap_rate_window_is_false_during_evening_shoulder_hours():
    # 2026-01-15 21:30 Europe/Dublin = 21:30 UTC in winter.
    now_utc = datetime(2026, 1, 15, 21, 30, tzinfo=timezone.utc)
    assert is_cheap_rate_window(now_utc) is False


def test_peak_tariff_overrides_default_forecast_mode_to_self_powered():
    mode, reason = decide_operational_mode(
        DecisionContext(
            period="Aftn",
            status="Amber",
            soc=45,
            headroom_kwh=8.0,
            headroom_target_kwh=10.2,
            live_solar_kw=None,
            hours_until_cheap_rate=None,
            estimated_home_load_kw=None,
            bridge_battery_reserve_kwh=None,
            tariff="PEAK",
        )
    )

    assert mode == SIGEN_MODES["SELF_POWERED"]
    assert "Peak electricity tariff is active" in reason


def test_peak_tariff_does_not_override_grid_export_rule():
    mode, reason = decide_operational_mode(
        DecisionContext(
            period="Aftn",
            status="Green",
            soc=98,
            headroom_kwh=0.1,
            headroom_target_kwh=10.2,
            live_solar_kw=None,
            hours_until_cheap_rate=None,
            estimated_home_load_kw=None,
            bridge_battery_reserve_kwh=None,
            tariff="PEAK",
        )
    )

    assert mode == SIGEN_MODES["GRID_EXPORT"]
    assert "export" in reason.lower()


def test_evening_red_uses_self_powered_when_battery_can_bridge_to_cheap_rate():
    mode, reason = decide_operational_mode(
        DecisionContext(
            period="Eve",
            status="Red",
            soc=70,
            headroom_kwh=6.0,
            headroom_target_kwh=12.0,
            live_solar_kw=None,
            hours_until_cheap_rate=4.0,
            estimated_home_load_kw=0.8,
            bridge_battery_reserve_kwh=1.0,
            tariff="DAY",
        )
    )

    assert mode == SIGEN_MODES["SELF_POWERED"]
    assert "staying in self-powered mode" in reason


def test_evening_red_falls_back_to_ai_when_bridge_energy_is_insufficient():
    mode, reason = decide_operational_mode(
        DecisionContext(
            period="Eve",
            status="Red",
            soc=20,
            headroom_kwh=6.0,
            headroom_target_kwh=12.0,
            live_solar_kw=None,
            hours_until_cheap_rate=4.0,
            estimated_home_load_kw=1.2,
            bridge_battery_reserve_kwh=1.5,
            tariff="DAY",
        )
    )

    assert mode == SIGEN_MODES["SELF_POWERED"]
    assert "Forecast is Red" in reason


def test_morning_high_soc_protection_does_not_export_for_amber_when_headroom_is_low():
    # Amber forecast: high SOC should not trigger protective export — moderate solar
    # is unlikely to cause significant clipping, and exporting stored energy wastes more.
    mode, reason = decide_operational_mode(
        DecisionContext(
            period="Morn",
            status="Amber",
            soc=97.0,
            headroom_kwh=0.6,
            headroom_target_kwh=10.2,
            live_solar_kw=None,
            hours_until_cheap_rate=None,
            estimated_home_load_kw=None,
            bridge_battery_reserve_kwh=None,
            tariff="DAY",
        )
    )

    assert mode == SIGEN_MODES["SELF_POWERED"]
    assert "Forecast is Amber" in reason


def test_morning_high_soc_protection_does_not_trigger_below_threshold():
    mode, reason = decide_operational_mode(
        DecisionContext(
            period="Morn",
            status="Amber",
            soc=45.0,
            headroom_kwh=0.6,
            headroom_target_kwh=10.2,
            live_solar_kw=None,
            hours_until_cheap_rate=None,
            estimated_home_load_kw=None,
            bridge_battery_reserve_kwh=None,
            tariff="DAY",
        )
    )

    assert mode == SIGEN_MODES["SELF_POWERED"]
    assert "Forecast is Amber" in reason
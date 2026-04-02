from config import SIGEN_MODES, TARIFF_TO_MODE
from decision_logic import decide_night_preparation_mode, decide_operational_mode
from datetime import datetime, timezone
from main import is_cheap_rate_window


def test_night_preparation_uses_grid_export_when_headroom_is_insufficient():
    mode, reason = decide_night_preparation_mode(
        target_period="Morn",
        status="Green",
        soc=90,
        headroom_kwh=0.2,
        period_solar_kwh=3.0,
        headroom_frac=0.25,
        soc_high_threshold=95,
    )

    assert mode == SIGEN_MODES["GRID_EXPORT"]
    assert "Next-day preparation" in reason


def test_night_preparation_uses_night_mode_when_export_not_required():
    mode, reason = decide_night_preparation_mode(
        target_period="Morn",
        status="Amber",
        soc=40,
        headroom_kwh=10.0,
        period_solar_kwh=1.0,
        headroom_frac=0.25,
        soc_high_threshold=95,
    )

    assert mode == TARIFF_TO_MODE["NIGHT"]
    assert "export is not required" in reason


def test_night_preparation_falls_back_to_night_mode_without_forecast():
    mode, reason = decide_night_preparation_mode(
        target_period="",
        status="",
        soc=None,
        headroom_kwh=None,
        period_solar_kwh=0.0,
    )

    assert mode == TARIFF_TO_MODE["NIGHT"]
    assert "No next-day forecast available" in reason


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
        period="Aftn",
        status="Amber",
        soc=60,
        headroom_kwh=8.0,
        period_solar_kwh=1.0,
        tariff_period="PEAK",
    )

    assert mode == SIGEN_MODES["SELF_POWERED"]
    assert "Tariff period is Peak" in reason


def test_peak_tariff_does_not_override_grid_export_rule():
    mode, reason = decide_operational_mode(
        period="Aftn",
        status="Green",
        soc=98,
        headroom_kwh=0.1,
        period_solar_kwh=3.0,
        tariff_period="PEAK",
    )

    assert mode == SIGEN_MODES["GRID_EXPORT"]
    assert "export" in reason.lower()
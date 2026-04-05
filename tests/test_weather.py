
"""
test_weather.py
Unit tests for weather.py (SolarForecast) and config-based mode mapping.
Uses pytest and logging for output.
"""

import json
import sys
import logging
import pytest
import weather.forecast as weather_module
from weather.forecast import ComparingSolarForecastProvider, QuartzSolarForecast, SolarForecast
from config.settings import FORECAST_TO_MODE, SIGEN_MODES
import logging
logger = logging.getLogger(__name__)
import os

def mask(val):
    if isinstance(val, str) and ("PASS" in val or "SECRET" in val or "TOKEN" in val):
        return val[:2] + "***MASKED***" + val[-2:]
    return val

from datetime import datetime

# Force logging to stdout for pytest
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger("test")

class DummyLogger:
    def info(self, msg):
        logger.info(msg)
    def error(self, msg):
        logger.error(msg)
    def debug(self, msg):
        logger.debug(msg)
    def warning(self, msg):
        logger.warning(msg)

@pytest.fixture
def dummy_forecast():
    # Patch SolarForecast to avoid network calls and always use today's day
    today_day = datetime.now().strftime("%a")  # e.g., 'Tue'
    today_day = today_day.capitalize()  # match SolarForecast __get_today()
    logger.info(f"[TEST] Using test data for day: {today_day}")
    test_data = [
        (today_day, "Morn", 500, "Green"),
        (today_day, "Aftn", 300, "Amber"),
        (today_day, "Eve", 100, "Red"),
    ]
    logger.info(f"[TEST] Test table_data: {test_data}")
    class DummyForecast(SolarForecast):
        def __init__(self, logger):
            self.logger = logger
            self.table_data = test_data
    return DummyForecast(DummyLogger())

def test_period_forecast(dummy_forecast):
    pf = dummy_forecast.get_todays_period_forecast()
    logger.info(f"[TEST] Period forecast returned: {pf}")
    assert pf["Morn"] == (500, "Green")
    assert pf["Aftn"] == (300, "Amber")
    assert pf["Eve"] == (100, "Red")
    logger.info("[RESULT] test_period_forecast: PASSED - For test day: Morning=GREEN (500W), Afternoon=AMBER (300W), Evening=RED (100W). Forecast matches expected values.")
    logger.info(f"[TEST] Period forecast assertions passed.")

def test_mode_mapping():
    # Test all forecast-to-mode mappings
    logger.info(f"[TEST] SIGEN_MODES: {SIGEN_MODES}")
    logger.info(f"[TEST] FORECAST_TO_MODE: {FORECAST_TO_MODE}")
    mode_names = {v: k for k, v in SIGEN_MODES.items()}
    for status, expected_mode in [("GREEN", SIGEN_MODES["SELF_POWERED"]),
                                  ("AMBER", SIGEN_MODES["AI"]),
                                  ("RED", SIGEN_MODES["AI"])]:
        mode = FORECAST_TO_MODE[status]
        logger.info(f"[TEST] Forecast {status} maps to mode {mode} ({mode_names.get(mode, 'UNKNOWN')}), expected {expected_mode} ({mode_names.get(expected_mode, 'UNKNOWN')})")
        assert mode == expected_mode
        logger.info(f"[RESULT] test_mode_mapping: PASSED - Testing {status} solar forecast: Sigen inverter should switch to {mode_names.get(mode, 'UNKNOWN')} mode.")
    logger.info(f"[TEST] Mode mapping assertions passed.")

def test_integration(dummy_forecast):
    pf = dummy_forecast.get_todays_period_forecast()
    logger.info(f"[TEST] Integration test period forecast: {pf}")
    mode_names = {v: k for k, v in SIGEN_MODES.items()}
    for period, (value, status) in pf.items():
        status_key = status.upper()
        mode = FORECAST_TO_MODE[status_key]
        logger.info(f"[TEST] Period {period}: value {value}, status {status} -> mode {mode} ({mode_names.get(mode, 'UNKNOWN')})")
        assert mode in SIGEN_MODES.values()
        logger.info(f"[RESULT] test_integration: PASSED - Testing {status} solar forecast for {period} ({value}W): Sigen inverter should switch to {mode_names.get(mode, 'UNKNOWN')} mode.")
    logger.info(f"[TEST] Integration assertions passed.")


def test_comparison_snapshot_is_written(tmp_path, monkeypatch):
    """Persist one JSONL snapshot per provider refresh for later analysis."""
    archive_path = tmp_path / "forecast_comparisons.jsonl"
    monkeypatch.setattr(weather_module, "FORECAST_COMPARISON_ARCHIVE_PATH", str(archive_path))

    class StaticProvider:
        def __init__(self, today, tomorrow):
            self._today = today
            self._tomorrow = tomorrow

        def get_todays_period_forecast(self):
            return self._today

        def get_tomorrows_period_forecast(self):
            return self._tomorrow

        def get_todays_solar_values(self):
            return []

        def get_simple_inverter_plan(self):
            return {}

        def is_good_day(self):
            return False

    primary = StaticProvider(
        {"Morn": (100, "Red"), "Aftn": (300, "Amber")},
        {"Morn": (500, "Green")},
    )
    secondary = StaticProvider(
        {"Morn": (1200, "Amber"), "Aftn": (2600, "Green")},
        {"Morn": (4000, "Green")},
    )

    ComparingSolarForecastProvider(
        DummyLogger(),
        primary,
        secondary,
        primary_name="esb_api",
        secondary_name="quartz",
    )

    assert archive_path.exists()
    snapshot_lines = archive_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(snapshot_lines) == 1

    snapshot = json.loads(snapshot_lines[0])
    assert snapshot["primary_provider"] == "esb_api"
    assert snapshot["secondary_provider"] == "quartz"
    assert snapshot["today"]["periods"]["Morn"]["primary"]["status"] == "Red"
    assert snapshot["today"]["periods"]["Morn"]["secondary"]["status"] == "Amber"
    assert snapshot["today"]["summary"]["mismatches"] == 2


def test_comparison_provider_uses_primary_status_with_secondary_watts():
    """Scheduler-facing forecast should preserve ESB status but use Quartz watts."""

    class StaticProvider:
        def __init__(self, today, tomorrow):
            self._today = today
            self._tomorrow = tomorrow

        def get_todays_period_forecast(self):
            return self._today

        def get_tomorrows_period_forecast(self):
            return self._tomorrow

        def get_todays_solar_values(self):
            return []

        def get_simple_inverter_plan(self):
            return {}

        def is_good_day(self):
            return False

    provider = ComparingSolarForecastProvider(
        DummyLogger(),
        StaticProvider({"Aftn": (300, "Amber")}, {"Morn": (100, "Red")}),
        StaticProvider({"Aftn": (2118, "Amber")}, {"Morn": (1139, "Red")}),
        primary_name="esb_api",
        secondary_name="quartz",
    )

    assert provider.get_todays_period_forecast() == {"Aftn": (2118, "Amber")}
    assert provider.get_tomorrows_period_forecast() == {"Morn": (1139, "Red")}


def test_quartz_status_normalization_uses_20_40_capacity_thresholds(monkeypatch):
    """Quartz status should map to Red/Amber/Green at 20% and 40% capacity bands."""
    monkeypatch.setattr(weather_module, "QUARTZ_SITE_CAPACITY_KWP", 10.0)

    assert QuartzSolarForecast._status_from_avg_kw(1.99) == "Red"
    assert QuartzSolarForecast._status_from_avg_kw(2.0) == "Amber"
    assert QuartzSolarForecast._status_from_avg_kw(3.99) == "Amber"
    assert QuartzSolarForecast._status_from_avg_kw(4.0) == "Green"


"""
test_weather.py
Unit tests for weather.py (SolarForecast) and config-based mode mapping.
Uses pytest and logging for output.
"""

import sys
import logging
import pytest
from weather import SolarForecast
from config import FORECAST_TO_MODE, SIGEN_MODES
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
                                  ("RED", SIGEN_MODES["TOU"])]:
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

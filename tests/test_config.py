def test_system_specs():
    logger.info(f"[TEST] SOLAR_PV_KW in config: {getattr(config, 'SOLAR_PV_KW', None)}")
    logger.info(f"[TEST] INVERTER_KW in config: {getattr(config, 'INVERTER_KW', None)}")
    logger.info(f"[TEST] BATTERY_KWH in config: {getattr(config, 'BATTERY_KWH', None)}")
    assert hasattr(config, "SOLAR_PV_KW")
    assert hasattr(config, "INVERTER_KW")
    assert hasattr(config, "BATTERY_KWH")
    assert config.SOLAR_PV_KW > 0
    assert config.INVERTER_KW > 0
    assert config.BATTERY_KWH > 0
    logger.info(f"[RESULT] test_system_specs: PASSED - System specs present and positive: Solar PV = {config.SOLAR_PV_KW} kW, Inverter = {config.INVERTER_KW} kW, Battery = {config.BATTERY_KWH} kWh.")

"""
test_config.py
Unit tests for config.py mappings and logging level.
Uses pytest and logging for output.
"""

import sys
import logging
"""Unit tests for system configuration (config.py).

Validates that all required configuration constants are present,
positive, and structured correctly.
"""


import config.settings as config
import logging
logger = logging.getLogger(__name__)

def mask(val):
    if isinstance(val, str) and ("PASS" in val or "SECRET" in val or "TOKEN" in val):
        return val[:2] + "***MASKED***" + val[-2:]
    return val


# Force logging to stdout for pytest
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    stream=sys.stdout,
    force=True
)
logger = logging.getLogger("test")

def test_log_level():
    logger.info(f"[TEST] LOG_LEVEL in config: {getattr(config, 'LOG_LEVEL', None)}")
    assert hasattr(config, "LOG_LEVEL")
    logger.info(f"[TEST] LOG_LEVEL is set to {config.LOG_LEVEL}")
    assert config.LOG_LEVEL in ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
    logger.info(f"[TEST] LOG_LEVEL assertion passed.")
    logger.info("[RESULT] test_log_level: PASSED - LOG_LEVEL is present in config and set correctly.")

def test_sigen_modes():
    # All required modes should be present
    logger.info(f"[TEST] SIGEN_MODES: {config.SIGEN_MODES}")
    for key in ["AI", "SELF_POWERED", "TOU", "GRID_EXPORT", "REMOTE_EMS", "CUSTOM"]:
        logger.info(f"[TEST] Checking SIGEN_MODES contains {key}")
        assert key in config.SIGEN_MODES
        logger.info(f"[TEST] SIGEN_MODES contains {key} -> {config.SIGEN_MODES[key]}")
    logger.info(f"[TEST] SIGEN_MODES assertions passed.")
    logger.info("[RESULT] test_sigen_modes: PASSED - All expected Sigen modes are present in SIGEN_MODES.")

def test_forecast_to_mode():
    # All forecast statuses should map to a valid mode
    logger.info(f"[TEST] FORECAST_TO_MODE: {config.FORECAST_TO_MODE}")
    for status in ["GREEN", "AMBER", "RED"]:
        logger.info(f"[TEST] Checking FORECAST_TO_MODE contains {status}")
        assert status in config.FORECAST_TO_MODE
        mode = config.FORECAST_TO_MODE[status]
        logger.info(f"[TEST] Forecast {status} maps to mode {mode}")
        assert mode in config.SIGEN_MODES.values()
    logger.info(f"[TEST] FORECAST_TO_MODE assertions passed.")
    logger.info("[RESULT] test_forecast_to_mode: PASSED - All expected forecast keys are present in FORECAST_TO_MODE.")

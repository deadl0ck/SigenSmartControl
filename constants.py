"""
constants.py
-------------
Application-wide constants for Sigen inverter control system.

Loads environment variables for location (latitude/longitude), defines forecast
scoring thresholds, and API endpoints for ESB Networks solar forecast data.
"""

import os
from dotenv import load_dotenv
load_dotenv()
try:
    from typing import Final
except ImportError:
    from typing_extensions import Final

import logging
logger = logging.getLogger(__name__)

def _get_env_float_required(var: str) -> float:
    """Load a required floating-point environment variable.
    
    Args:
        var: Environment variable name.
        
    Returns:
        Parsed float value.
        
    Raises:
        RuntimeError: If variable is not set or cannot be parsed as float.
    """
    val = os.getenv(var)
    if val is None:
        logger.error(f"Required environment variable '{var}' is not set. Application cannot start.")
        raise RuntimeError(f"Required environment variable '{var}' is not set.")
    try:
        return float(val)
    except Exception:
        logger.error(f"Environment variable '{var}' must be a valid float, got: {val}")
        raise

LATITUDE: Final[float] = _get_env_float_required("SIGEN_LATITUDE")
LONGITUDE: Final[float] = _get_env_float_required("SIGEN_LONGITUDE")


# County used when filtering the ESB forecast data.
COUNTY: Final[str] = "WESTMEATH"

# Public CSV endpoint with renewable forecast values.
MET_IE_FORECAST_CUR: Final[str] = "https://www.esbnetworks.ie/docs/default-source/dso/dso-renewableforecast-wind-solar.csv"

# Numeric weights used to score whether the day is considered good for solar.
RED_VAL: Final[int] = 0
AMBER_VAL: Final[float] = 0.5
GREEN_VAL: Final[int] = 1
GOOD_DAY_THRESHOLD: Final[float] = 1.5

# Public API endpoint for sunrise/sunset times (see sunrise_sunset.py)
SUNRISE_SUNSET_API_URL: Final[str] = "https://api.sunrise-sunset.org/json"

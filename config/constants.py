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


# Forecast provider selection.
# Supported values:
# - "esb_api": ESB Networks county forecast API (default)
# - "quartz": Open Quartz site-level forecast API (optional provider)
FORECAST_PROVIDER: Final[str] = os.getenv("FORECAST_PROVIDER", "esb_api").strip().lower()

# County used for the ESB county-level forecast API.
# Any of the keys listed in ESB_COUNTY_ID_MAP is accepted (case-insensitive).
ESB_FORECAST_COUNTY: Final[str] = os.getenv("ESB_FORECAST_COUNTY", "Westmeath").strip()

# ESB county IDs (from ESB renewable forecast web app mapping):
# Carlow, Cavan, Clare, Cork, Donegal, Dublin, Galway, Kerry, Kildare,
# Kilkenny, Laois, Leitrim, Limerick, Longford, Louth, Mayo, Meath,
# Monaghan, Offaly, Roscommon, Sligo, Tipperary, Waterford, Westmeath,
# Wexford, Wicklow.
ESB_COUNTY_ID_MAP: Final[dict[str, int]] = {
    "Carlow": 1,
    "Cavan": 2,
    "Clare": 3,
    "Cork": 4,
    "Donegal": 5,
    "Dublin": 6,
    "Galway": 7,
    "Kerry": 8,
    "Kildare": 9,
    "Kilkenny": 10,
    "Laois": 11,
    "Leitrim": 12,
    "Limerick": 13,
    "Longford": 14,
    "Louth": 15,
    "Mayo": 16,
    "Meath": 17,
    "Monaghan": 18,
    "Offaly": 19,
    "Roscommon": 20,
    "Sligo": 21,
    "Tipperary": 22,
    "Waterford": 23,
    "Westmeath": 24,
    "Wexford": 25,
    "Wicklow": 26,
}

# ESB forecast API endpoint details used by the county selector on esbnetworks.ie.
ESB_FORECAST_API_BASE_URL: Final[str] = os.getenv(
    "ESB_FORECAST_API_BASE_URL",
    "https://api.esb.ie/esbn/dmso-toolkit/v1.0",
).strip()
ESB_FORECAST_API_ENDPOINT: Final[str] = "/forecast-data-calculation"

# Public subscription key currently exposed by the ESB forecast page JS config.
# You can override this with ESB_FORECAST_API_SUBSCRIPTION_KEY in your environment.
ESB_FORECAST_API_SUBSCRIPTION_KEY: Final[str] = os.getenv(
    "ESB_FORECAST_API_SUBSCRIPTION_KEY",
    "86f282ece0d44857bda0e9b085b4aeea",
).strip()

_county_lookup = {name.lower(): county_id for name, county_id in ESB_COUNTY_ID_MAP.items()}
ESB_FORECAST_COUNTY_ID: Final[int] = _county_lookup.get(
    ESB_FORECAST_COUNTY.lower(),
    ESB_COUNTY_ID_MAP["Westmeath"],
)
# URL for the configured ESB forecast county (derived from ESB_COUNTY_ID_MAP).
ESB_FORECAST_API_URL: Final[str] = (
    f"{ESB_FORECAST_API_BASE_URL.rstrip('/')}"
    f"{ESB_FORECAST_API_ENDPOINT}/{ESB_FORECAST_COUNTY_ID}"
)

# Legacy CSV endpoint (kept for reference/backward compatibility only).
MET_IE_FORECAST_CUR: Final[str] = "https://www.esbnetworks.ie/docs/default-source/dso/dso-renewableforecast-wind-solar.csv"

# Backward-compatible alias used in existing code.
COUNTY: Final[str] = ESB_FORECAST_COUNTY

# Optional Open Quartz provider settings.
QUARTZ_FORECAST_API_URL: Final[str] = os.getenv(
    "QUARTZ_FORECAST_API_URL",
    "https://open.quartz.solar/forecast/",
).strip()
QUARTZ_SITE_CAPACITY_KWP: Final[float] = float(os.getenv("QUARTZ_SITE_CAPACITY_KWP", "8.9"))

# Local archive file for side-by-side ESB vs Quartz comparison snapshots.
FORECAST_COMPARISON_ARCHIVE_PATH: Final[str] = os.getenv(
    "FORECAST_COMPARISON_ARCHIVE_PATH",
    "data/forecast_comparisons.jsonl",
).strip()

# Local archive file for raw inverter telemetry snapshots.
INVERTER_TELEMETRY_ARCHIVE_PATH: Final[str] = os.getenv(
    "INVERTER_TELEMETRY_ARCHIVE_PATH",
    "data/inverter_telemetry.jsonl",
).strip()

# Local archive file for inverter mode-change events (set attempts/results).
MODE_CHANGE_EVENTS_ARCHIVE_PATH: Final[str] = os.getenv(
    "MODE_CHANGE_EVENTS_ARCHIVE_PATH",
    "data/mode_change_events.jsonl",
).strip()

# Local artifact storing bounded daily forecast calibration derived from telemetry.
FORECAST_CALIBRATION_PATH: Final[str] = os.getenv(
    "FORECAST_CALIBRATION_PATH",
    "data/forecast_calibration.json",
).strip()

# Numeric weights used to score whether the day is considered good for solar.
RED_VAL: Final[int] = 0
AMBER_VAL: Final[float] = 0.5
GREEN_VAL: Final[int] = 1
GOOD_DAY_THRESHOLD: Final[float] = 1.5

# Public API endpoint for sunrise/sunset times (see sunrise_sunset.py)
SUNRISE_SUNSET_API_URL: Final[str] = "https://api.sunrise-sunset.org/json"

"""
test_sunrise_sunset.py
Unit tests for sunrise_sunset.py (fetching sunrise/sunset times).
"""

import sys
import logging
"""Unit tests for sunrise/sunset API integration (sunrise_sunset.py).

Tests time parsing and API response handling.
"""

from unittest.mock import patch
from weather.sunrise_sunset import get_sunrise_sunset
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

@patch("weather.sunrise_sunset.requests.get")
def test_get_sunrise_sunset(mock_get):
    # Mock API response
    mock_get.return_value.status_code = 200
    mock_get.return_value.json.return_value = {
        "status": "OK",
        "results": {
            "sunrise": "2026-03-31T06:45:00+00:00",
            "sunset": "2026-03-31T19:45:00+00:00"
        }
    }
    lat, lng = 53.5, -7.3  # Example: Westmeath, Ireland
    sunrise, sunset = get_sunrise_sunset(lat, lng)
    logger.info(f"[TEST] Sunrise: {sunrise}, Sunset: {sunset}")
    assert sunrise == "2026-03-31T06:45:00+00:00"
    assert sunset == "2026-03-31T19:45:00+00:00"
    logger.info("[RESULT] test_get_sunrise_sunset: PASSED - Correct sunrise and sunset times parsed from mocked API response for Westmeath, Ireland.")

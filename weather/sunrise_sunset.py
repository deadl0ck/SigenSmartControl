"""
sunrise_sunset.py
-----------------
Fetches sunrise and sunset times for a given latitude and longitude using the sunrise-sunset.org API.
"""

import requests
from datetime import datetime
from typing import Tuple
from config.constants import SUNRISE_SUNSET_API_URL
from config.settings import SUNRISE_SUNSET_API_TIMEOUT_SECONDS
import logging

logger = logging.getLogger("sunrise_sunset")

def get_sunrise_sunset(lat: float, lng: float, date: str = "today") -> Tuple[str, str]:
    """
    Fetch sunrise and sunset times for the given latitude and longitude.
    Args:
        lat (float): Latitude of the location.
        lng (float): Longitude of the location.
        date (str): Date string ("today", "YYYY-MM-DD", etc.)
    Returns:
        Tuple[str, str]: Sunrise and sunset times in ISO 8601 format (UTC).
    Raises:
        Exception: If the API call fails or returns invalid data.
    """
    params = {
        "lat": lat,
        "lng": lng,
        "date": date,
        "formatted": 0  # ISO 8601 format
    }
    logger.info(f"Requesting sunrise/sunset for lat={lat}, lng={lng}, date={date}")
    response = requests.get(
        SUNRISE_SUNSET_API_URL,
        params=params,
        timeout=SUNRISE_SUNSET_API_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    if data["status"] != "OK":
        raise Exception(f"Sunrise-sunset API error: {data}")
    sunrise = data["results"]["sunrise"]
    sunset = data["results"]["sunset"]
    logger.info(f"Sunrise: {sunrise}, Sunset: {sunset}")
    return sunrise, sunset

if __name__ == "__main__":
    import sys
    import constants
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', stream=sys.stdout, force=True)
    logger = logging.getLogger("sunrise_sunset")
    lat = constants.LATITUDE
    lng = constants.LONGITUDE
    logger.info(f"[STANDALONE] Using lat={lat}, lng={lng} from environment variables.")
    try:
        sunrise, sunset = get_sunrise_sunset(lat, lng)
        logger.info(f"[STANDALONE] Sunrise: {sunrise}, Sunset: {sunset}")
        sunrise_dt = datetime.fromisoformat(sunrise)
        sunset_dt = datetime.fromisoformat(sunset)
        sunrise_str = sunrise_dt.strftime('%Y-%m-%d %H:%M:%S %Z')
        sunset_str = sunset_dt.strftime('%Y-%m-%d %H:%M:%S %Z')
        logger.info(f"[STANDALONE] Sunrise: {sunrise_str}, Sunset: {sunset_str}")
        print(f"Sunrise: {sunrise_str}\nSunset: {sunset_str}")
    except Exception as e:
        logger.error(f"[STANDALONE] Error fetching sunrise/sunset: {e}")
        sys.exit(1)

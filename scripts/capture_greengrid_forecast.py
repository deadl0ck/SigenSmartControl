"""Capture GREEN-GRID solar forecast and append to archive.

This script queries the GREEN-GRID Shiny app for a 1-day solar forecast
using your installation parameters and saves it to data/greengrid_forecasts.jsonl
for later accuracy comparison against actual inverter telemetry.

Usage:
    python scripts/capture_greengrid_forecast.py

Requires:
    - Playwright installed: pip install playwright
    - Chromium browser: playwright install chromium
    - Configured solar parameters in config/constants.py (EIRCODE, panel direction, pitch, count)

Example .env parameters:
    GREENGRID_EIRCODE=N91 F752
    GREENGRID_DIRECTION=SE
    GREENGRID_ROOF_PITCH_DEGREES=27
    GREENGRID_NUM_PANELS=20
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import LOCAL_TIMEZONE
from weather.greengrid_forecast import GreenGridForecast


logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def get_solar_parameters() -> tuple[str, str, int, int]:
    """Load solar parameters from environment or config.

    Returns:
        (eircode, direction, roof_pitch_degrees, num_panels)

    Raises:
        ValueError: If required parameters are missing or invalid.
    """
    # Try environment variables first
    eircode = os.getenv("GREENGRID_EIRCODE", "").strip()
    direction = os.getenv("GREENGRID_DIRECTION", "").strip()
    roof_pitch_str = os.getenv("GREENGRID_ROOF_PITCH_DEGREES", "").strip()
    num_panels_str = os.getenv("GREENGRID_NUM_PANELS", "").strip()

    # If not in env, try to load from config/constants.py
    if not all([eircode, direction, roof_pitch_str, num_panels_str]):
        logger.info(
            "Solar parameters not fully set in environment. "
            "Set via environment variables:\n"
            "  GREENGRID_EIRCODE=<eircode>\n"
            "  GREENGRID_DIRECTION=<direction>\n"
            "  GREENGRID_ROOF_PITCH_DEGREES=<degrees>\n"
            "  GREENGRID_NUM_PANELS=<count>"
        )

        # Attempt fallback from config (if available)
        try:
            from config.constants import LATITUDE, LONGITUDE
            logger.info(f"Fallback: Using LATITUDE={LATITUDE}, LONGITUDE={LONGITUDE} from config")
            # These are just lat/lon; we still need the other params
        except ImportError:
            pass

        missing = []
        if not eircode:
            missing.append("GREENGRID_EIRCODE")
        if not direction:
            missing.append("GREENGRID_DIRECTION")
        if not roof_pitch_str:
            missing.append("GREENGRID_ROOF_PITCH_DEGREES")
        if not num_panels_str:
            missing.append("GREENGRID_NUM_PANELS")

        raise ValueError(
            f"Missing required parameters: {', '.join(missing)}\n"
            "Set these as environment variables before running this script."
        )

    try:
        roof_pitch_degrees = int(roof_pitch_str)
        num_panels = int(num_panels_str)
    except ValueError as exc:
        raise ValueError(
            f"Invalid numeric parameters: "
            f"GREENGRID_ROOF_PITCH_DEGREES={roof_pitch_str}, "
            f"GREENGRID_NUM_PANELS={num_panels_str}"
        ) from exc

    if not (15 <= roof_pitch_degrees <= 80):
        raise ValueError(
            f"Roof pitch must be 15-80 degrees, got {roof_pitch_degrees}"
        )

    if not (4 <= num_panels <= 300):
        raise ValueError(
            f"Panel count must be 4-300, got {num_panels}"
        )

    return eircode, direction, roof_pitch_degrees, num_panels


async def main() -> None:
    """Capture GREEN-GRID forecast and append to archive."""
    try:
        eircode, direction, roof_pitch_degrees, num_panels = get_solar_parameters()
    except ValueError as exc:
        logger.error(str(exc))
        raise SystemExit(1)

    logger.info(f"Capturing GREEN-GRID forecast for:")
    logger.info(f"  Eircode: {eircode}")
    logger.info(f"  Direction: {direction}")
    logger.info(f"  Roof Pitch: {roof_pitch_degrees}°")
    logger.info(f"  Panels: {num_panels}")

    provider = GreenGridForecast()

    if not provider.playwright_installed:
        logger.error(
            "Playwright not installed. Install with:\n"
            "  pip install playwright\n"
            "  playwright install chromium"
        )
        raise SystemExit(1)

    logger.info("Querying GREEN-GRID app (this may take 5-10 seconds)...")
    forecast = await provider.fetch_forecast(
        eircode=eircode,
        direction=direction,
        roof_pitch_degrees=roof_pitch_degrees,
        num_panels=num_panels,
    )

    if not forecast:
        logger.error("Failed to retrieve forecast from GREEN-GRID app")
        raise SystemExit(1)

    # Append to archive
    root = Path(__file__).resolve().parents[1]
    archive_path = root / "data" / "greengrid_forecasts.jsonl"

    try:
        archive_path.parent.mkdir(parents=True, exist_ok=True)

        with open(archive_path, "a", encoding="utf-8") as f:
            json.dump(forecast, f)
            f.write("\n")

        logger.info(f"✓ Saved forecast to {archive_path}")
        logger.info(f"  Total forecast: {forecast.get('total_forecast_kwh', 0):.2f} kWh")
        logger.info(f"  Hourly points: {len(forecast.get('forecast_points', []))}")
        logger.info(f"  Captured at: {forecast.get('captured_at', 'unknown')}")

        # Print next steps
        logger.info("")
        logger.info("Next steps:")
        logger.info("1. Collect multiple days of forecasts")
        logger.info("2. Run: python scripts/compare_greengrid_vs_actuals.py")
        logger.info("3. Compare GREEN-GRID vs actual inverter production")

    except OSError as exc:
        logger.error(f"Failed to write to {archive_path}: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())

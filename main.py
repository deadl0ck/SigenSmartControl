
import logging
import asyncio
from typing import Any

from weather import SolarForecast
from sigen_auth import get_sigen_instance
from config import SIGEN_MODES, FORECAST_TO_MODE, TARIFF_TO_MODE

# --- Logging configuration ---
LOG_LEVEL = getattr(logging, __import__('config').LOG_LEVEL, logging.INFO)
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("sigen_control")



async def main() -> None:
    """
    Main control loop for Sigen inverter automation.
    - Fetches today's solar forecast
    - Determines the best operational mode for each period (Morn/Aftn/Eve)
    - Sets the inverter mode accordingly
    - All actions are logged at the configured level
    """

    import os

    def mask(val, key=None):
        if key and key.upper() in ("SIGEN_PASSWORD",):
            return "***MASKED***"
        if not isinstance(val, str):
            return val
        if any(s in val.upper() for s in ("PASS", "SECRET", "TOKEN")):
            return val[:2] + "***MASKED***" + val[-2:]
        return val

    # Only log relevant env vars used in code
    relevant_env_vars = [
        "SIGEN_USERNAME", "SIGEN_PASSWORD", "SIGEN_LATITUDE", "SIGEN_LONGITUDE"
    ]
    logger.info("[RUN] Loaded relevant environment variables:")
    for k in relevant_env_vars:
        v = os.getenv(k)
        if v is None:
            logger.info(f"[RUN] ENV {k} = [NOT SET]")
        else:
            logger.info(f"[RUN] ENV {k} = {mask(v, k)}")

    from config import SOLAR_PV_KW, INVERTER_KW, BATTERY_KWH
    logger.info("Starting Sigen inverter control loop...")
    logger.info(f"System Specs: Solar PV = {SOLAR_PV_KW} kW, Inverter = {INVERTER_KW} kW, Battery = {BATTERY_KWH} kWh")

    # Configurable: how much headroom (fraction) to keep free before high solar periods
    HEADROOM_FRAC = 0.25  # e.g., keep at least 25% of battery free before high solar
    PRE_PERIOD_MINUTES = 45  # how many minutes before period to check/export

    # Helper to estimate headroom (kWh)
    def calc_headroom(soc: float) -> float:
        return BATTERY_KWH * (1 - soc / 100)

    # Helper to estimate max possible solar input for a period (kWh)
    def estimate_period_solar(solar_value: int, period_hours: float = 3.0) -> float:
        # solar_value is forecast W for the period; scale by PV size
        # Assume forecast is average W for period
        kw = (solar_value / 1000.0)
        kw = min(kw, SOLAR_PV_KW, INVERTER_KW)  # can't exceed hardware
        return kw * period_hours

    # Fetch sunrise/sunset if needed for dynamic period mapping (not shown here)
    sigen = await get_sigen_instance()
    forecast = SolarForecast(logger)
    period_forecast = forecast.get_todays_period_forecast()

    # Example: Simulate tariff period (could be dynamic in future)
    # For now, just use 'DAY' for all periods
    tariff_period = "DAY"


    SOC_HIGH_THRESHOLD = 95  # %
    mode_names = {v: k for k, v in SIGEN_MODES.items()}


    for period, (solar_value, status) in period_forecast.items():
        logger.info(f"Period: {period}, Solar Value: {solar_value}, Status: {status}")

        # Fetch battery SOC for this period
        try:
            energy_flow: dict[str, Any] = await sigen.get_energy_flow()
            soc = energy_flow.get("batterySoc")
            logger.info(f"Battery SOC for {period}: {soc}%")
        except Exception as e:
            logger.error(f"Failed to fetch SOC for {period}: {e}")
            soc = None

        # Estimate headroom and solar for this period
        headroom_kwh = calc_headroom(soc) if soc is not None else None
        period_solar_kwh = estimate_period_solar(solar_value)
        logger.info(f"Estimated battery headroom before {period}: {headroom_kwh:.2f} kWh")
        logger.info(f"Estimated max solar input for {period}: {period_solar_kwh:.2f} kWh")

        # Pre-period export logic: if headroom is less than needed, export before period
        if (
            soc is not None
            and status.upper() == "GREEN"
            and headroom_kwh is not None
            and headroom_kwh < period_solar_kwh * HEADROOM_FRAC
        ):
            mode = SIGEN_MODES["GRID_EXPORT"]
            logger.info(f"[PRE-PERIOD] Headroom ({headroom_kwh:.2f} kWh) < {HEADROOM_FRAC*100:.0f}% of expected solar ({period_solar_kwh:.2f} kWh). Preemptively exporting to grid to create space.")
        # Night handling (if period is Night, use TARIFF_TO_MODE['NIGHT'])
        elif period.upper() == "NIGHT":
            mode = TARIFF_TO_MODE["NIGHT"]
            logger.info(f"Night period detected. Using TARIFF_TO_MODE['NIGHT']: {mode_names.get(mode, mode)} (value={mode})")
        # If SOC is high and forecast is Green, export to grid
        elif soc is not None and soc >= SOC_HIGH_THRESHOLD and status.upper() == "GREEN":
            mode = SIGEN_MODES["GRID_EXPORT"]
            logger.info(f"SOC >= {SOC_HIGH_THRESHOLD}% and forecast is Green. Using GRID_EXPORT mode: {mode_names.get(mode, mode)} (value={mode})")
        else:
            # Default mapping
            status_key = status.upper()
            mode = FORECAST_TO_MODE.get(status_key, SIGEN_MODES["AI"])
            logger.info(f"Selected mode for {period}: {mode_names.get(mode, mode)} (value={mode})")

        # Set the inverter mode (simulate or actually set)
        try:
            response = await sigen.set_operational_mode(mode, -1)
            logger.info(f"Set mode response for {period}: {response}")
        except Exception as e:
            logger.error(f"Failed to set mode for {period}: {e}")

    # --- Future improvement: fetch sunrise/sunset for dynamic period mapping ---
    # You can use an API like sunrise-sunset.org or OpenWeatherMap to get today's
    # sunrise and sunset times for your location. Use these to define when
    # 'Morning', 'Afternoon', and 'Evening' start/end dynamically.

    logger.info("Control loop complete.")

    # logger.info("\nFetching current operational mode...")
    # current_mode = await sigen.get_operational_mode()
    # logger.info(f"Current Operational Mode: {current_mode}")


if __name__ == "__main__":
    asyncio.run(main())
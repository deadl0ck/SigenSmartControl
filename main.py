
import logging
import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any

from weather import SolarForecast
from sigen_auth import get_sigen_instance
from config import (
    SIGEN_MODES,
    POLL_INTERVAL_MINUTES,
    MAX_PRE_PERIOD_WINDOW_MINUTES,
    HEADROOM_FRAC,
    SOC_HIGH_THRESHOLD,
)
from decision_logic import decide_operational_mode, calc_headroom_kwh
from sunrise_sunset import get_sunrise_sunset
from constants import LATITUDE, LONGITUDE

# --- Logging configuration ---
LOG_LEVEL = getattr(logging, __import__('config').LOG_LEVEL, logging.INFO)
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("sigen_control")

# How often the scheduler wakes up to re-evaluate each period.
POLL_INTERVAL_SECONDS = POLL_INTERVAL_MINUTES * 60
# How far ahead of a period start we begin monitoring SOC for a potential pre-export.
MAX_PRE_PERIOD_WINDOW = timedelta(minutes=MAX_PRE_PERIOD_WINDOW_MINUTES)


def _parse_utc(iso_str: str) -> datetime:
    """Parse an ISO 8601 timestamp, ensuring it carries UTC tzinfo."""
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def derive_period_windows(
    sunrise_utc: datetime,
    sunset_utc: datetime,
    period_names: list[str],
) -> dict[str, datetime]:
    """
    Divide the solar day into equal windows and return each period's start time (UTC).
    With the default three periods (Morn/Aftn/Eve) the solar day is split into thirds
    starting at sunrise.
    """
    solar_day = sunset_utc - sunrise_utc
    n = len(period_names)
    return {
        name: sunrise_utc + solar_day * (i / n)
        for i, name in enumerate(period_names)
    }


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

    # Helper to estimate max possible solar input for a period (kWh)
    def estimate_period_solar(solar_value: int, period_hours: float = 3.0) -> float:
        # solar_value is forecast W for the period; scale by PV size
        # Assume forecast is average W for period
        kw = (solar_value / 1000.0)
        kw = min(kw, SOLAR_PV_KW, INVERTER_KW)  # can't exceed hardware
        return kw * period_hours

    # Legacy one-shot run path (scheduler mode is run_scheduler).
    sigen = await get_sigen_instance()
    forecast = SolarForecast(logger)
    period_forecast = forecast.get_todays_period_forecast()

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
        headroom_kwh = calc_headroom_kwh(BATTERY_KWH, soc) if soc is not None else None
        period_solar_kwh = estimate_period_solar(solar_value)
        logger.info(f"Estimated battery headroom before {period}: {headroom_kwh:.2f} kWh")
        logger.info(f"Estimated max solar input for {period}: {period_solar_kwh:.2f} kWh")

        mode, decision_reason = decide_operational_mode(
            period=period,
            status=status,
            soc=soc,
            headroom_kwh=headroom_kwh,
            period_solar_kwh=period_solar_kwh,
            headroom_frac=HEADROOM_FRAC,
            soc_high_threshold=SOC_HIGH_THRESHOLD,
        )
        logger.info(
            f"Selected mode for {period}: {mode_names.get(mode, mode)} (value={mode}). Reason: {decision_reason}"
        )

        # Set the inverter mode (simulate or actually set)
        try:
            response = await sigen.set_operational_mode(mode, -1)
            logger.info(f"Set mode response for {period}: {response}")
        except Exception as e:
            logger.error(f"Failed to set mode for {period}: {e}")

    logger.info("Control loop complete.")


async def run_scheduler() -> None:
    """
    Self-contained 15-minute scheduling loop for production use.

    On each tick:
      1. Refreshes solar forecast and sunrise/sunset times at the start of each day,
         then derives equal-width period start times across the solar day.
      2. For each daytime period, begins monitoring SOC when within MAX_PRE_PERIOD_WINDOW
         of the period start.
      3. Calculates the dynamic lead time needed to export enough battery headroom:
               lead_time = (headroom_deficit_kWh * 1.1) / inverter_kw
         and triggers GRID_EXPORT as soon as that window opens.
      4. At each period start, re-evaluates SOC and sets the definitive mode.
      5. Every action (pre-export and period-start) is performed at most once per
         period per day to avoid redundant inverter commands.
    """
    import os
    from config import SOLAR_PV_KW, INVERTER_KW, BATTERY_KWH

    def mask(val, key=None):
        if key and key.upper() in ("SIGEN_PASSWORD",):
            return "***MASKED***"
        if not isinstance(val, str):
            return val
        if any(s in val.upper() for s in ("PASS", "SECRET", "TOKEN")):
            return val[:2] + "***MASKED***" + val[-2:]
        return val

    relevant_env_vars = ["SIGEN_USERNAME", "SIGEN_PASSWORD", "SIGEN_LATITUDE", "SIGEN_LONGITUDE"]
    logger.info("[SCHEDULER] Environment:")
    for k in relevant_env_vars:
        v = os.getenv(k)
        logger.info(f"[SCHEDULER]   {k} = {mask(v, k) if v else '[NOT SET]'}")
    logger.info(
        f"[SCHEDULER] System specs: Solar PV={SOLAR_PV_KW} kW, "
        f"Inverter={INVERTER_KW} kW, Battery={BATTERY_KWH} kWh"
    )

    mode_names = {v: k for k, v in SIGEN_MODES.items()}

    sigen = await get_sigen_instance()
    current_date = None
    period_windows: dict[str, datetime] = {}
    period_forecast: dict[str, tuple[int, str]] = {}
    # Tracks which actions have been taken for each period today.
    # state[period] = {"pre_set": bool, "start_set": bool}
    state: dict[str, dict[str, bool]] = {}

    async def refresh_daily_data() -> None:
        nonlocal period_windows, period_forecast, state
        logger.info("[SCHEDULER] Refreshing daily forecast and sunrise/sunset data.")
        forecast_obj = SolarForecast(logger)
        period_forecast = forecast_obj.get_todays_period_forecast()
        logger.info(f"[SCHEDULER] Today's forecast: {period_forecast}")

        sunrise_str, sunset_str = get_sunrise_sunset(LATITUDE, LONGITUDE)
        sunrise_utc = _parse_utc(sunrise_str)
        sunset_utc = _parse_utc(sunset_str)
        logger.info(
            f"[SCHEDULER] Sunrise: {sunrise_utc.isoformat()}  Sunset: {sunset_utc.isoformat()}"
        )

        daytime_periods = [p for p in period_forecast if p.upper() != "NIGHT"]
        period_windows = derive_period_windows(sunrise_utc, sunset_utc, daytime_periods)
        for period, start in period_windows.items():
            logger.info(f"[SCHEDULER] Period '{period}' starts at {start.isoformat()} UTC")

        state = {p: {"pre_set": False, "start_set": False} for p in daytime_periods}

    async def fetch_soc(period: str) -> float | None:
        try:
            energy_flow: dict[str, Any] = await sigen.get_energy_flow()
            soc = energy_flow.get("batterySoc")
            logger.info(f"[{period}] SOC: {soc}%")
            return soc
        except Exception as e:
            logger.error(f"[{period}] Failed to fetch SOC: {e}")
            return None

    def estimate_solar(solar_value: int) -> float:
        kw = min(solar_value / 1000.0, SOLAR_PV_KW, INVERTER_KW)
        return kw * 3.0  # assume 3-hour period

    def log_check(
        period: str,
        stage: str,
        *,
        now_utc: datetime,
        period_start_utc: datetime,
        solar_value: int,
        status: str,
        period_solar_kwh: float,
        soc: float | None,
        headroom_kwh: float | None,
        headroom_target_kwh: float,
        headroom_deficit_kwh: float,
        export_by_utc: datetime | None,
        mode: int | None,
        reason: str,
        outcome: str,
    ) -> None:
        mode_label = mode_names.get(mode, mode) if mode is not None else "N/A"
        export_by_label = export_by_utc.isoformat() if export_by_utc is not None else "N/A"
        logger.info(
            f"[{period}] {stage} CHECK | now={now_utc.isoformat()} | "
            f"period_start={period_start_utc.isoformat()} | forecast_w={solar_value} | "
            f"status={status} | expected_solar_kwh={period_solar_kwh:.2f} | "
            f"soc={soc if soc is not None else 'N/A'} | "
            f"headroom_kwh={f'{headroom_kwh:.2f}' if headroom_kwh is not None else 'N/A'} | "
            f"headroom_target_kwh={headroom_target_kwh:.2f} | "
            f"headroom_deficit_kwh={headroom_deficit_kwh:.2f} | "
            f"export_by={export_by_label} | decision_mode={mode_label} | "
            f"outcome={outcome} | reason={reason}"
        )

    logger.info(
        f"[SCHEDULER] Starting. Will poll every {POLL_INTERVAL_MINUTES} minutes. "
        f"Max pre-period window: {MAX_PRE_PERIOD_WINDOW_MINUTES} minutes. "
        f"Headroom fraction: {HEADROOM_FRAC:.2f}. SOC export threshold: {SOC_HIGH_THRESHOLD}%."
    )

    while True:
        now = datetime.now(timezone.utc)
        today = now.date()

        # Refresh forecast and period windows once per calendar day.
        if today != current_date:
            current_date = today
            try:
                await refresh_daily_data()
            except Exception as e:
                logger.error(
                    f"[SCHEDULER] Failed to refresh daily data: {e}. Retrying next tick."
                )
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

        for period, period_start in period_windows.items():
            s = state[period]
            solar_value, status = period_forecast[period]
            period_solar_kwh = estimate_solar(solar_value)

            # --- Pre-period export check ---
            # Active when within MAX_PRE_PERIOD_WINDOW of the period start.
            if not s["pre_set"] and period_start - MAX_PRE_PERIOD_WINDOW <= now < period_start:
                soc = await fetch_soc(period)
                if soc is not None:
                    headroom_kwh = calc_headroom_kwh(BATTERY_KWH, soc)
                    headroom_target_kwh = period_solar_kwh * HEADROOM_FRAC
                    headroom_deficit = max(0.0, headroom_target_kwh - headroom_kwh)
                    if headroom_deficit > 0:
                        # Time needed = deficit (kWh) / inverter export capacity (kW), +10% buffer.
                        lead_time = timedelta(hours=(headroom_deficit * 1.1) / INVERTER_KW)
                        export_by = period_start - lead_time
                    else:
                        export_by = period_start  # No export needed; arm at period start.

                    mode, reason = decide_operational_mode(
                        period=period,
                        status=status,
                        soc=soc,
                        headroom_kwh=headroom_kwh,
                        period_solar_kwh=period_solar_kwh,
                        headroom_frac=HEADROOM_FRAC,
                        soc_high_threshold=SOC_HIGH_THRESHOLD,
                    )

                    if now >= export_by:
                        outcome = "pre-period export triggered"
                        if mode == SIGEN_MODES["GRID_EXPORT"]:
                            log_check(
                                period,
                                "PRE-PERIOD",
                                now_utc=now,
                                period_start_utc=period_start,
                                solar_value=solar_value,
                                status=status,
                                period_solar_kwh=period_solar_kwh,
                                soc=soc,
                                headroom_kwh=headroom_kwh,
                                headroom_target_kwh=headroom_target_kwh,
                                headroom_deficit_kwh=headroom_deficit,
                                export_by_utc=export_by,
                                mode=mode,
                                reason=reason,
                                outcome=outcome,
                            )
                            try:
                                await sigen.set_operational_mode(mode, -1)
                            except Exception as e:
                                logger.error(f"[{period}] Failed to set pre-period mode: {e}")
                        else:
                            log_check(
                                period,
                                "PRE-PERIOD",
                                now_utc=now,
                                period_start_utc=period_start,
                                solar_value=solar_value,
                                status=status,
                                period_solar_kwh=period_solar_kwh,
                                soc=soc,
                                headroom_kwh=headroom_kwh,
                                headroom_target_kwh=headroom_target_kwh,
                                headroom_deficit_kwh=headroom_deficit,
                                export_by_utc=export_by,
                                mode=mode,
                                reason=reason,
                                outcome="pre-period check concluded no export needed",
                            )
                        s["pre_set"] = True
                    else:
                        log_check(
                            period,
                            "PRE-PERIOD",
                            now_utc=now,
                            period_start_utc=period_start,
                            solar_value=solar_value,
                            status=status,
                            period_solar_kwh=period_solar_kwh,
                            soc=soc,
                            headroom_kwh=headroom_kwh,
                            headroom_target_kwh=headroom_target_kwh,
                            headroom_deficit_kwh=headroom_deficit,
                            export_by_utc=export_by,
                            mode=mode,
                            reason=reason,
                            outcome="waiting until export window opens",
                        )

            # --- Period start: set the definitive mode ---
            if not s["start_set"] and now >= period_start:
                soc = await fetch_soc(period)
                if soc is not None:
                    headroom_kwh = calc_headroom_kwh(BATTERY_KWH, soc)
                    headroom_target_kwh = period_solar_kwh * HEADROOM_FRAC
                    headroom_deficit = max(0.0, headroom_target_kwh - headroom_kwh)
                    mode, reason = decide_operational_mode(
                        period=period,
                        status=status,
                        soc=soc,
                        headroom_kwh=headroom_kwh,
                        period_solar_kwh=period_solar_kwh,
                        headroom_frac=HEADROOM_FRAC,
                        soc_high_threshold=SOC_HIGH_THRESHOLD,
                    )
                    log_check(
                        period,
                        "PERIOD-START",
                        now_utc=now,
                        period_start_utc=period_start,
                        solar_value=solar_value,
                        status=status,
                        period_solar_kwh=period_solar_kwh,
                        soc=soc,
                        headroom_kwh=headroom_kwh,
                        headroom_target_kwh=headroom_target_kwh,
                        headroom_deficit_kwh=headroom_deficit,
                        export_by_utc=period_start,
                        mode=mode,
                        reason=reason,
                        outcome="period start mode applied",
                    )
                    try:
                        await sigen.set_operational_mode(mode, -1)
                        s["start_set"] = True
                        s["pre_set"] = True  # Suppress further pre-period checks.
                    except Exception as e:
                        logger.error(f"[{period}] Failed to set period-start mode: {e}")

        logger.info(
            f"[SCHEDULER] Tick at {now.isoformat()} UTC complete. "
            f"Next check in {POLL_INTERVAL_SECONDS // 60} minutes."
        )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_scheduler())
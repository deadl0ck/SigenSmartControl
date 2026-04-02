
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from weather import SolarForecast
from sigen_interaction import SigenInteraction
from config import (
    SIGEN_MODES,
    TARIFF_TO_MODE,
    SHOULDER_NIGHT_MODE,
    FULL_SIMULATION_MODE,
    POLL_INTERVAL_MINUTES,
    MAX_PRE_PERIOD_WINDOW_MINUTES,
    NIGHT_MODE_ENABLED,
    NEXT_DAY_PRECHECK_ENABLED,
    NIGHT_PRECHECK_DELAY_MINUTES,
    LOCAL_TIMEZONE,
    DAY_RATE_MORNING_START_HOUR,
    DAY_RATE_MORNING_END_HOUR,
    PEAK_RATE_START_HOUR,
    PEAK_RATE_END_HOUR,
    DAY_RATE_EVENING_START_HOUR,
    DAY_RATE_EVENING_END_HOUR,
    CHEAP_RATE_START_HOUR,
    CHEAP_RATE_END_HOUR,
    HEADROOM_FRAC,
    SOC_HIGH_THRESHOLD,
    ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE,
    ESTIMATED_HOME_LOAD_KW,
    BRIDGE_BATTERY_RESERVE_KWH,
    ENABLE_EVENING_AI_MODE_TRANSITION,
    EVENING_AI_MODE_START_HOUR,
    SOLAR_PV_KW,
    INVERTER_KW,
    BATTERY_KWH,
)
from decision_logic import (
    decide_operational_mode,
    decide_night_preparation_mode,
    calc_headroom_kwh,
)
from tariff_utils import (
    _parse_utc,
    derive_period_windows,
    get_first_period_info,
    is_cheap_rate_window,
    get_night_tariff_mode,
    get_hours_until_cheap_rate,
    get_tariff_period_for_time,
    suppress_elapsed_periods_except_latest,
    LOCAL_TZ,
)
from mode_control import (
    should_use_ai_mode_for_evening,
    extract_mode_value,
    mode_matches_target,
    ACTION_DIVIDER,
)
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


# --- Scheduler interaction and mode control ---

async def log_current_mode_on_startup(sigen: SigenInteraction, mode_names: dict[int, str]) -> None:
    """Log the inverter's current operational mode during startup.
    
    Args:
        sigen: SigenInteraction instance for API calls.
        mode_names: Mapping from numeric mode to human-readable label.
    """
    try:
        current_mode_raw = await sigen.get_operational_mode()
        current_mode = extract_mode_value(current_mode_raw)
        logger.info(ACTION_DIVIDER)
        logger.info("STARTUP CHECK: fetched current inverter mode")
        if current_mode is not None:
            logger.info(
                f"Current mode is {mode_names.get(current_mode, current_mode)} (value={current_mode})"
            )
        else:
            logger.info(f"Current mode response (unparsed): {current_mode_raw}")
        logger.info(ACTION_DIVIDER)
    except Exception as e:
        logger.error(f"Failed to fetch current inverter mode on startup: {e}")


async def apply_mode_change(
    *,
    sigen: SigenInteraction | None,
    mode: int,
    period: str,
    reason: str,
    mode_names: dict[int, str],
) -> bool:
    """Attempt to change the inverter operational mode with idempotency checks.
    
    Reads the current mode before writing; if already at target mode, logs and returns True
    without calling the API. Falls back to set attempt if read fails.
    
    Args:
        sigen: SigenInteraction instance, or None in dry-run mode.
        mode: Target numeric mode value.
        period: Human-readable period/context label for logging.
        reason: Explanation of why this mode change is being made.
        mode_names: Mapping from numeric mode to human-readable label.
        
    Returns:
        True if mode was set or already at target, False if set operation failed.
    """
    mode_label = mode_names.get(mode, mode)
    if sigen is None:
        logger.error(f"Cannot set mode for {period}: Sigen interaction is unavailable.")
        return False

    try:
        current_mode_raw = await sigen.get_operational_mode()
        if mode_matches_target(current_mode_raw, mode, mode_names):
            logger.info(ACTION_DIVIDER)
            logger.info("Skipping inverter set_operational_mode (already at target mode)")
            logger.info(f"Target period/context: {period}")
            logger.info(f"Target mode: {mode_label} (value={mode})")
            logger.info(f"Decision reason: {reason}")
            logger.info(ACTION_DIVIDER)
            return True
    except Exception as e:
        logger.warning(
            f"Could not read current inverter mode before setting {mode_label} for {period}: {e}. "
            "Proceeding with mode set attempt."
        )

    logger.info(ACTION_DIVIDER)
    logger.info("Calling inverter set_operational_mode")
    logger.info(f"Target period/context: {period}")
    logger.info(f"Target mode: {mode_label} (value={mode})")
    logger.info(f"Decision reason: {reason}")
    logger.info(ACTION_DIVIDER)

    try:
        response = await sigen.set_operational_mode(mode)
        logger.info(f"Set mode response for {period}: {response}")
        return True
    except Exception as e:
        logger.error(f"Failed to set mode for {period}: {e}")
        return False


async def create_scheduler_interaction(mode_names: dict[int, str]) -> SigenInteraction | None:
    """Create and validate the Sigen API interaction wrapper.
    
    Attempts to initialize API connection and logs current inverter mode on startup.
    If authentication fails but FULL_SIMULATION_MODE is enabled, returns None for
    dry-run operation; otherwise re-raises the exception.
    
    Args:
        mode_names: Mapping from numeric mode to human-readable label.
        
    Returns:
        SigenInteraction instance if successful, or None in simulation mode if auth fails.
        
    Raises:
        Exception: If API initialization fails and not in simulation mode.
    """
    try:
        sigen = await SigenInteraction.create()
        await log_current_mode_on_startup(sigen, mode_names)
        return sigen
    except Exception as e:
        if FULL_SIMULATION_MODE:
            logger.warning(
                "[SCHEDULER] Inverter authentication failed, but FULL_SIMULATION_MODE is enabled. "
                "Continuing in offline dry-run mode with simulated SOC values. "
                f"Reason: {e}"
            )
            return None
        raise


async def main() -> None:
    """
    Main control loop for Sigen inverter automation.
    - Fetches today's solar forecast
    - Determines the best operational mode for each period (Morn/Aftn/Eve)
    - Sets the inverter mode accordingly
    - All actions are logged at the configured level
    """

    def mask(val, key=None):
        """Mask sensitive environment variable values in logs.
        
        Args:
            val: Value to check for masking.
            key: Optional environment variable name.
            
        Returns:
            Masked string for sensitive values, original value otherwise.
        """
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

    logger.info("Starting Sigen inverter control loop...")
    logger.info(f"System Specs: Solar PV = {SOLAR_PV_KW} kW, Inverter = {INVERTER_KW} kW, Battery = {BATTERY_KWH} kWh")

    # Helper to estimate max possible solar input for a period (kWh)
    def estimate_period_solar(solar_value: int, period_hours: float = 3.0) -> float:
        """Estimate total solar energy available during a period.
        
        Args:
            solar_value: Forecasted power in watts (typically average for period).
            period_hours: Duration of the period in hours (default 3.0).
            
        Returns:
            Estimated energy in kWh, capped by system limits (PV size, inverter capacity).
        """
        # solar_value is forecast W for the period; scale by PV size
        # Assume forecast is average W for period
        kw = (solar_value / 1000.0)
        kw = min(kw, SOLAR_PV_KW, INVERTER_KW)  # can't exceed hardware
        return kw * period_hours

    # Legacy one-shot run path (scheduler mode is run_scheduler).
    sigen = await SigenInteraction.create()
    mode_names = {v: k for k, v in SIGEN_MODES.items()}
    await log_current_mode_on_startup(sigen, mode_names)
    forecast = SolarForecast(logger)
    period_forecast = forecast.get_todays_period_forecast()


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
            tariff_period=get_tariff_period_for_time(datetime.now(timezone.utc)),
            headroom_frac=HEADROOM_FRAC,
            soc_high_threshold=SOC_HIGH_THRESHOLD,
            battery_kwh=BATTERY_KWH,
            hours_until_cheap_rate=get_hours_until_cheap_rate(datetime.now(timezone.utc)),
            estimated_home_load_kw=ESTIMATED_HOME_LOAD_KW,
            bridge_battery_reserve_kwh=BRIDGE_BATTERY_RESERVE_KWH,
            enable_pre_cheap_rate_battery_bridge=ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE,
        )
        logger.info(
            f"Selected mode for {period}: {mode_names.get(mode, mode)} (value={mode}). Reason: {decision_reason}"
        )

        await apply_mode_change(
            sigen=sigen,
            mode=mode,
            period=period,
            reason=decision_reason,
            mode_names=mode_names,
        )

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

    def mask(val, key=None):
        """Mask sensitive environment variable values in logs.
        
        Args:
            val: Value to check for masking.
            key: Optional environment variable name.
            
        Returns:
            Masked string for sensitive values, original value otherwise.
        """
        if key and key.upper() in ("SIGEN_PASSWORD",):
            return "***MASKED***"
        if not isinstance(val, str):
            return val
        if any(s in val.upper() for s in ("PASS", "SECRET", "TOKEN")):
            return val[:2] + "***MASKED***" + val[-2:]
        return val

    relevant_env_vars = [
        "SIGEN_USERNAME",
        "SIGEN_PASSWORD",
        "SIGEN_LATITUDE",
        "SIGEN_LONGITUDE",
        "SIMULATED_SOC_PERCENT",
    ]
    logger.info("[SCHEDULER] Environment:")
    for k in relevant_env_vars:
        v = os.getenv(k)
        logger.info(f"[SCHEDULER]   {k} = {mask(v, k) if v else '[NOT SET]'}")
    logger.info(
        f"[SCHEDULER] System specs: Solar PV={SOLAR_PV_KW} kW, "
        f"Inverter={INVERTER_KW} kW, Battery={BATTERY_KWH} kWh"
    )

    simulated_soc_raw = os.getenv("SIMULATED_SOC_PERCENT", "80")
    try:
        simulated_soc_percent = float(simulated_soc_raw)
    except ValueError:
        logger.warning(
            f"[SCHEDULER] Invalid SIMULATED_SOC_PERCENT='{simulated_soc_raw}'. Falling back to 80%."
        )
        simulated_soc_percent = 80.0

    mode_names = {v: k for k, v in SIGEN_MODES.items()}

    sigen = await create_scheduler_interaction(mode_names)
    current_date = None
    today_period_windows: dict[str, datetime] = {}
    tomorrow_period_windows: dict[str, datetime] = {}
    today_period_forecast: dict[str, tuple[int, str]] = {}
    tomorrow_period_forecast: dict[str, tuple[int, str]] = {}
    today_sunrise_utc: datetime | None = None
    today_sunset_utc: datetime | None = None
    tomorrow_sunrise_utc: datetime | None = None
    # Tracks which actions have been taken for each period today.
    # day_state[period] = {"pre_set": bool, "start_set": bool}
    day_state: dict[str, dict[str, bool]] = {}
    night_state: dict[str, Any] = {
        "mode_set_for": None,
        "mode_phase": None,
        "prep_set_for": None,
    }

    async def refresh_daily_data() -> None:
        """Fetch and cache solar forecast and sunrise/sunset times for today and tomorrow.
        
        Called once per calendar day to initialize/refresh period windows, forecasts,
        and sunrise/sunset times used throughout the day's scheduling loop.
        """
        nonlocal today_period_windows, tomorrow_period_windows
        nonlocal today_period_forecast, tomorrow_period_forecast
        nonlocal today_sunrise_utc, today_sunset_utc, tomorrow_sunrise_utc, day_state
        logger.info("[SCHEDULER] Refreshing daily forecast and sunrise/sunset data.")
        forecast_obj = SolarForecast(logger)
        today_period_forecast = forecast_obj.get_todays_period_forecast()
        tomorrow_period_forecast = forecast_obj.get_tomorrows_period_forecast()
        logger.info(f"[SCHEDULER] Today's forecast: {today_period_forecast}")
        logger.info(f"[SCHEDULER] Tomorrow's forecast: {tomorrow_period_forecast}")

        if current_date is None:
            raise RuntimeError("Current scheduler date was not initialized before refresh.")

        tomorrow_date = current_date + timedelta(days=1)

        sunrise_str, sunset_str = get_sunrise_sunset(LATITUDE, LONGITUDE, current_date.isoformat())
        tomorrow_sunrise_str, tomorrow_sunset_str = get_sunrise_sunset(
            LATITUDE,
            LONGITUDE,
            tomorrow_date.isoformat(),
        )
        sunrise_utc = _parse_utc(sunrise_str)
        sunset_utc = _parse_utc(sunset_str)
        tomorrow_sunrise = _parse_utc(tomorrow_sunrise_str)
        tomorrow_sunset = _parse_utc(tomorrow_sunset_str)
        today_sunrise_utc = sunrise_utc
        today_sunset_utc = sunset_utc
        tomorrow_sunrise_utc = tomorrow_sunrise
        logger.info(
            f"[SCHEDULER] Sunrise: {sunrise_utc.isoformat()}  Sunset: {sunset_utc.isoformat()}"
        )
        logger.info(f"[SCHEDULER] Tomorrow sunrise: {tomorrow_sunrise.isoformat()}")

        daytime_periods = [p for p in today_period_forecast if p.upper() != "NIGHT"]
        tomorrow_daytime_periods = [p for p in tomorrow_period_forecast if p.upper() != "NIGHT"]
        today_period_windows = derive_period_windows(sunrise_utc, sunset_utc, daytime_periods)
        tomorrow_period_windows = derive_period_windows(
            tomorrow_sunrise,
            tomorrow_sunset,
            tomorrow_daytime_periods,
        )
        for period, start in today_period_windows.items():
            logger.info(f"[SCHEDULER] Period '{period}' starts at {start.isoformat()} UTC")
        for period, start in tomorrow_period_windows.items():
            logger.info(f"[SCHEDULER] Tomorrow period '{period}' starts at {start.isoformat()} UTC")

        day_state = {p: {"pre_set": False, "start_set": False} for p in daytime_periods}

    async def fetch_soc(period: str) -> float | None:
        """Fetch current battery state-of-charge from inverter or use simulated value.
        
        Args:
            period: Human-readable period/context label for logging.
            
        Returns:
            Battery SOC percentage (0-100), or None if fetch fails.
        """
        if sigen is None:
            logger.info(
                f"[{period}] SOC: {simulated_soc_percent}% (simulated; inverter unavailable in dry-run mode)"
            )
            return simulated_soc_percent
        try:
            energy_flow: dict[str, Any] = await sigen.get_energy_flow()
            soc = energy_flow.get("batterySoc")
            logger.info(f"[{period}] SOC: {soc}%")
            return soc
        except Exception as e:
            logger.error(f"[{period}] Failed to fetch SOC: {e}")
            return None

    def estimate_solar(solar_value: int) -> float:
        """Estimate total solar energy available during a period.
        
        Args:
            solar_value: Forecasted power in watts (typically average for period).
            
        Returns:
            Estimated energy in kWh assuming 3-hour period, capped by system limits.
        """
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
        """Log a comprehensive decision checkpoint with all relevant state and parameters.
        
        Args:
            period: Human-readable period/context label.
            stage: Scheduling stage (PRE-PERIOD, PERIOD-START, NIGHT-BASE, etc.).
            now_utc: Current time in UTC.
            period_start_utc: Period start time in UTC.
            solar_value: Forecasted power in watts.
            status: Forecast status string (e.g., 'GREEN', 'YELLOW').
            period_solar_kwh: Estimated available solar energy.
            soc: Current battery SOC percentage, or None if unavailable.
            headroom_kwh: Current available battery headroom.
            headroom_target_kwh: Target headroom needed before period.
            headroom_deficit_kwh: Shortfall (if any) against target.
            export_by_utc: Deadline for pre-period export window.
            mode: Target operational mode, or None.
            reason: Explanation of decision logic.
            outcome: Description of action taken.
        """
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

    def get_active_night_context(now_utc: datetime) -> dict[str, Any] | None:
        """Determine whether a night window is currently active and return scheduling context.
        
        Returns active night context during two windows:
        - PRE-DAWN: Before the first daytime period of today
        - EVENING-NIGHT: After today's sunset until tomorrow's first daytime period
        
        Args:
            now_utc: Current time in UTC.
            
        Returns:
            Dict with keys {window_name, night_start, target_period, target_start, solar_value,
            status, target_date} if in a night window, or None if in daytime.
        """
        today_first_period = get_first_period_info(today_period_windows, today_period_forecast)
        tomorrow_first_period = get_first_period_info(tomorrow_period_windows, tomorrow_period_forecast)

        if today_first_period is not None and now_utc < today_first_period[1]:
            period, period_start, solar_value, status = today_first_period
            return {
                "window_name": "PRE-DAWN",
                "night_start": None,
                "target_period": period,
                "target_start": period_start,
                "solar_value": solar_value,
                "status": status,
                "target_date": period_start.date(),
            }

        if (
            today_sunset_utc is not None
            and tomorrow_first_period is not None
            and now_utc >= today_sunset_utc
        ):
            period, period_start, solar_value, status = tomorrow_first_period
            return {
                "window_name": "EVENING-NIGHT",
                "night_start": today_sunset_utc,
                "target_period": period,
                "target_start": period_start,
                "solar_value": solar_value,
                "status": status,
                "target_date": period_start.date(),
            }

        return None

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
                suppressed_periods = suppress_elapsed_periods_except_latest(
                    now,
                    today_period_windows,
                    day_state,
                )
                if suppressed_periods:
                    elapsed_periods = [
                        period
                        for period, period_start in sorted(today_period_windows.items(), key=lambda item: item[1])
                        if now >= period_start
                    ]
                    latest_elapsed_period = elapsed_periods[-1]
                    logger.info(
                        "[SCHEDULER] Suppressing stale elapsed daytime periods on startup/day refresh: "
                        f"{', '.join(suppressed_periods)}. "
                        f"Keeping only the latest elapsed period actionable: {latest_elapsed_period}."
                    )
            except Exception as e:
                logger.error(
                    f"[SCHEDULER] Failed to refresh daily data: {e}. Retrying next tick."
                )
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

        if night_state["mode_set_for"] is not None and night_state["mode_set_for"] < today:
            night_state["mode_set_for"] = None
            night_state["mode_phase"] = None
        if night_state["prep_set_for"] is not None and night_state["prep_set_for"] < today:
            night_state["prep_set_for"] = None

        night_context = get_active_night_context(now)
        if NIGHT_MODE_ENABLED and night_context is not None:
            night_period_name = f"Night->{night_context['target_period']}"
            night_period_solar_kwh = estimate_solar(night_context["solar_value"])
            night_headroom_target_kwh = night_period_solar_kwh * HEADROOM_FRAC
            night_mode, night_phase, night_mode_reason = get_night_tariff_mode(now)

            if (
                night_state["mode_set_for"] != night_context["target_date"]
                or night_state["mode_phase"] != night_phase
            ):
                log_check(
                    night_period_name,
                    "NIGHT-BASE",
                    now_utc=now,
                    period_start_utc=night_context["target_start"],
                    solar_value=night_context["solar_value"],
                    status=night_context["status"],
                    period_solar_kwh=night_period_solar_kwh,
                    soc=None,
                    headroom_kwh=None,
                    headroom_target_kwh=night_headroom_target_kwh,
                    headroom_deficit_kwh=0.0,
                    export_by_utc=night_context["night_start"],
                    mode=night_mode,
                    reason=(
                        f"Active {night_context['window_name']} window before "
                        f"{night_context['target_period']}. {night_mode_reason}"
                    ),
                    outcome=f"night {night_phase} mode applied",
                )
                try:
                    ok = await apply_mode_change(
                        sigen=sigen,
                        mode=night_mode,
                        period=night_period_name,
                        reason=night_mode_reason,
                        mode_names=mode_names,
                    )
                    if ok:
                        night_state["mode_set_for"] = night_context["target_date"]
                        night_state["mode_phase"] = night_phase
                except Exception as e:
                    logger.error(f"[{night_period_name}] Unexpected error applying base night mode: {e}")

            if NEXT_DAY_PRECHECK_ENABLED and night_state["prep_set_for"] != night_context["target_date"]:
                precheck_opens_at = (
                    night_context["night_start"] + timedelta(minutes=NIGHT_PRECHECK_DELAY_MINUTES)
                    if night_context["night_start"] is not None
                    else now
                )
                if now >= precheck_opens_at:
                    soc = await fetch_soc(night_period_name)
                    if soc is not None:
                        headroom_kwh = calc_headroom_kwh(BATTERY_KWH, soc)
                        headroom_deficit = max(0.0, night_headroom_target_kwh - headroom_kwh)
                        mode, reason = decide_night_preparation_mode(
                            target_period=night_context["target_period"],
                            status=night_context["status"],
                            soc=soc,
                            headroom_kwh=headroom_kwh,
                            period_solar_kwh=night_period_solar_kwh,
                            headroom_frac=HEADROOM_FRAC,
                            soc_high_threshold=SOC_HIGH_THRESHOLD,
                        )
                        if mode == TARIFF_TO_MODE["NIGHT"] and not is_cheap_rate_window(now):
                            mode = SHOULDER_NIGHT_MODE
                            reason = (
                                f"{reason} Cheap-rate window has not opened yet, so using shoulder mode "
                                "instead of charge-oriented night mode."
                            )
                        log_check(
                            night_period_name,
                            "NIGHT-PREP",
                            now_utc=now,
                            period_start_utc=night_context["target_start"],
                            solar_value=night_context["solar_value"],
                            status=night_context["status"],
                            period_solar_kwh=night_period_solar_kwh,
                            soc=soc,
                            headroom_kwh=headroom_kwh,
                            headroom_target_kwh=night_headroom_target_kwh,
                            headroom_deficit_kwh=headroom_deficit,
                            export_by_utc=precheck_opens_at,
                            mode=mode,
                            reason=reason,
                            outcome="night pre-check action applied",
                        )
                        ok = await apply_mode_change(
                            sigen=sigen,
                            mode=mode,
                            period=night_period_name,
                            reason=reason,
                            mode_names=mode_names,
                        )
                        if ok:
                            night_state["prep_set_for"] = night_context["target_date"]
                else:
                    log_check(
                        night_period_name,
                        "NIGHT-PREP",
                        now_utc=now,
                        period_start_utc=night_context["target_start"],
                        solar_value=night_context["solar_value"],
                        status=night_context["status"],
                        period_solar_kwh=night_period_solar_kwh,
                        soc=None,
                        headroom_kwh=None,
                        headroom_target_kwh=night_headroom_target_kwh,
                        headroom_deficit_kwh=0.0,
                        export_by_utc=precheck_opens_at,
                        mode=night_mode,
                        reason=(
                            "Waiting until configured night pre-check delay has elapsed. "
                            f"Current local time {now.astimezone(LOCAL_TZ).strftime('%H:%M')} is still in "
                            f"the {night_phase} tariff phase."
                        ),
                        outcome="night pre-check not yet due",
                    )

        for period, period_start in today_period_windows.items():
            s = day_state[period]
            solar_value, status = today_period_forecast[period]
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
                        tariff_period=get_tariff_period_for_time(period_start),
                        headroom_frac=HEADROOM_FRAC,
                        soc_high_threshold=SOC_HIGH_THRESHOLD,
                        battery_kwh=BATTERY_KWH,
                        hours_until_cheap_rate=get_hours_until_cheap_rate(now),
                        estimated_home_load_kw=ESTIMATED_HOME_LOAD_KW,
                        bridge_battery_reserve_kwh=BRIDGE_BATTERY_RESERVE_KWH,
                        enable_pre_cheap_rate_battery_bridge=ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE,
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
                            await apply_mode_change(
                                sigen=sigen,
                                mode=mode,
                                period=f"{period} (pre-period)",
                                reason=reason,
                                mode_names=mode_names,
                            )
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
                        tariff_period=get_tariff_period_for_time(period_start),
                        headroom_frac=HEADROOM_FRAC,
                        soc_high_threshold=SOC_HIGH_THRESHOLD,
                        battery_kwh=BATTERY_KWH,
                        hours_until_cheap_rate=get_hours_until_cheap_rate(now),
                        estimated_home_load_kw=ESTIMATED_HOME_LOAD_KW,
                        bridge_battery_reserve_kwh=BRIDGE_BATTERY_RESERVE_KWH,
                        enable_pre_cheap_rate_battery_bridge=ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE,
                    )
                    
                    # Check if Evening period should use AI Mode for profit-max arbitrage
                    use_ai_mode, ai_mode_reason = should_use_ai_mode_for_evening(period, now)
                    if use_ai_mode:
                        mode = SIGEN_MODES["AI"]
                        reason = ai_mode_reason
                    
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
                    ok = await apply_mode_change(
                        sigen=sigen,
                        mode=mode,
                        period=f"{period} (period-start)",
                        reason=reason,
                        mode_names=mode_names,
                    )
                    if ok:
                        s["start_set"] = True
                        s["pre_set"] = True  # Suppress further pre-period checks.

        logger.info(
            f"[SCHEDULER] Tick at {now.isoformat()} UTC complete. "
            f"Next check in {POLL_INTERVAL_SECONDS // 60} minutes."
        )
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    asyncio.run(run_scheduler())
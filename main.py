
"""Main scheduler loop for coordinating Sigen inverter mode decisions.

The scheduler continuously monitors solar forecasts, battery state, and tariff windows,
making operational mode decisions that optimize between self-powered generation,
grid arbitrage, and cost-minimization based on real-time conditions.
"""

import asyncio
from collections import deque
import math
import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Any

from weather.forecast import (
    SolarForecastProvider,
    archive_forecast_solar_snapshot,
    create_solar_forecast_provider,
)
from integrations.sigen_interaction import SigenInteraction
from integrations.sigen_auth import refresh_sigen_instance
from config.settings import (
    LOG_LEVEL as CONFIG_LOG_LEVEL,
    SIGEN_MODES,
    FULL_SIMULATION_MODE,
    POLL_INTERVAL_MINUTES,
    FORECAST_REFRESH_INTERVAL_MINUTES,
    FORECAST_SOLAR_ARCHIVE_ENABLED,
    FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES,
    FORECAST_SOLAR_RATE_LIMIT_COOLDOWN_MINUTES,
    MAX_PRE_PERIOD_WINDOW_MINUTES,
    NIGHT_MODE_ENABLED,
    HEADROOM_TARGET_KWH,
    SOLAR_PV_KW,
    INVERTER_KW,
    BATTERY_KWH,
    LIVE_SOLAR_AVERAGE_SAMPLE_COUNT,
    MIN_EFFECTIVE_BATTERY_EXPORT_KW,
    DEFAULT_SIMULATED_SOC_PERCENT,
)
from logic.morning import handle_morning_period
from logic.afternoon import handle_afternoon_period
from logic.evening import handle_evening_period
from logic.night import handle_night_window
from logic.schedule_utils import (
    _parse_utc,
    derive_period_windows,
    get_hours_until_cheap_rate,
    is_cheap_rate_window,
    get_active_night_context as schedule_utils_get_active_night_context,
    order_daytime_periods,
    suppress_elapsed_periods_except_latest,
)
from logic.mode_control import (
    ACTION_DIVIDER,
)
from logic.inverter_control import (
    apply_mode_change as apply_mode_change_control,
    get_effective_battery_export_kw as get_effective_battery_export_kw_control,
    get_live_solar_average_kw as get_live_solar_average_kw_control,
    sample_live_solar_power as sample_live_solar_power_control,
)
from logic.mode_logging import log_mode_status
from weather.sunrise_sunset import get_sunrise_sunset
from config.constants import (
    LATITUDE,
    LONGITUDE,
    TIMED_EXPORT_STATE_PATH,
)
import logic.timed_export as timed_export_module
from logic.timed_export import (
    load_timed_export_override,
    maybe_restore_timed_grid_export as maybe_restore_timed_grid_export_helper,
    persist_timed_export_override,
    start_timed_grid_export as start_timed_grid_export_helper,
)
from telemetry.forecast_calibration import build_and_save_forecast_calibration, get_period_calibration
from telemetry.telemetry_archive import (
    append_inverter_telemetry_snapshot,
    append_mode_change_event,
    extract_today_solar_generation_kwh,
)
from notifications.notification_email_helpers import (
    notify_mode_change_email,
    notify_startup_email,
)
from utils.logging_formatters import LevelColorFormatter
from utils.terminal_formatting import (
    ANSI_BRIGHT_RED,
    colorize_text,
)
from utils.payload_tree import log_payload_tree
from utils.sensitive_values import mask_sensitive_value


# --- Logging configuration ---
LOG_LEVEL = getattr(logging, CONFIG_LOG_LEVEL, logging.INFO)
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(
    LevelColorFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
logging.basicConfig(level=LOG_LEVEL, handlers=[_log_handler], force=True)
logger = logging.getLogger("sigen_control")


def _empty_timed_export_override() -> dict[str, Any]:
    """Backward-compatible wrapper for timed-export default state."""
    return timed_export_module._empty_timed_export_override()


def _persist_timed_export_override(state: dict[str, Any]) -> None:
    """Backward-compatible wrapper for timed-export persistence."""
    original_path = timed_export_module.TIMED_EXPORT_STATE_PATH
    timed_export_module.TIMED_EXPORT_STATE_PATH = TIMED_EXPORT_STATE_PATH
    try:
        timed_export_module.persist_timed_export_override(state, logger=logger)
    finally:
        timed_export_module.TIMED_EXPORT_STATE_PATH = original_path


def _load_timed_export_override() -> dict[str, Any]:
    """Backward-compatible wrapper for timed-export load."""
    original_path = timed_export_module.TIMED_EXPORT_STATE_PATH
    timed_export_module.TIMED_EXPORT_STATE_PATH = TIMED_EXPORT_STATE_PATH
    try:
        return timed_export_module.load_timed_export_override(logger=logger)
    finally:
        timed_export_module.TIMED_EXPORT_STATE_PATH = original_path


def _is_truthy_env(name: str) -> bool:
    """Return True when an environment variable is set to a truthy value."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _describe_payload_shape(payload: Any) -> str:
    """Return a compact payload shape description for warning logs.

    Args:
        payload: Arbitrary payload returned by an API call.

    Returns:
        Human-readable payload shape summary.
    """
    if isinstance(payload, dict):
        keys = list(payload.keys())
        preview = ", ".join(str(key) for key in keys[:8])
        suffix = ", ..." if len(keys) > 8 else ""
        return f"dict keys=[{preview}{suffix}]"
    if isinstance(payload, list):
        return f"list len={len(payload)}"
    return f"type={type(payload).__name__}"


# How often the scheduler wakes up to re-evaluate each period.
POLL_INTERVAL_SECONDS = POLL_INTERVAL_MINUTES * 60
FORECAST_REFRESH_INTERVAL_SECONDS = FORECAST_REFRESH_INTERVAL_MINUTES * 60
FORECAST_SOLAR_ARCHIVE_INTERVAL_SECONDS = FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES * 60
FORECAST_SOLAR_RATE_LIMIT_COOLDOWN_SECONDS = FORECAST_SOLAR_RATE_LIMIT_COOLDOWN_MINUTES * 60
# How far ahead of a period start we begin monitoring SOC for a potential pre-export.
MAX_PRE_PERIOD_WINDOW = timedelta(minutes=MAX_PRE_PERIOD_WINDOW_MINUTES)


# --- Scheduler interaction and mode control ---


def _should_archive_mode_change_events() -> bool:
    """Return whether mode-change events should be written to the live archive.

    Returns:
        True during normal runtime. False during pytest unless explicitly enabled.
    """
    running_under_pytest = bool(os.getenv("PYTEST_CURRENT_TEST"))
    allow_pytest_archives = _is_truthy_env("SIGEN_ALLOW_MODE_CHANGE_ARCHIVE_IN_TESTS")
    return not running_under_pytest or allow_pytest_archives


async def _notify_startup_email(
    *,
    current_mode_raw: Any,
    battery_soc: float | None,
    solar_generated_today_kwh: float | None,
    today_period_forecast: dict[str, tuple[int, str]] | None,
    mode_names: dict[int, str],
    event_time_utc: datetime,
) -> None:
    """Backward-compatible wrapper for startup notification helper.

    Args:
        current_mode_raw: Current mode payload returned at startup.
        battery_soc: Battery state-of-charge percentage, when available.
        solar_generated_today_kwh: Current day's cumulative solar generation in kWh.
        today_period_forecast: Daytime period forecast snapshot for today.
        mode_names: Mapping from mode value to human-readable mode label.
        event_time_utc: Startup timestamp in UTC.
    """
    await notify_startup_email(
        current_mode_raw=current_mode_raw,
        battery_soc=battery_soc,
        solar_generated_today_kwh=solar_generated_today_kwh,
        today_period_forecast=today_period_forecast,
        mode_names=mode_names,
        event_time_utc=event_time_utc,
        logger=logger,
    )


async def _notify_mode_change_email(
    *,
    success: bool,
    period: str,
    reason: str,
    requested_mode: int,
    requested_mode_label: str,
    current_mode_raw: Any,
    mode_names: dict[int, str],
    event_time_utc: datetime,
    battery_soc: float | None = None,
    solar_generated_today_kwh: float | None = None,
    today_period_forecast: dict[str, tuple[int, str]] | None = None,
    response: Any | None = None,
    error: str | None = None,
) -> None:
    """Backward-compatible wrapper for mode-change notification helper.

    Args:
        success: True when the mode command call succeeded.
        period: Scheduler period/context label.
        reason: Decision reason for the command.
        requested_mode: Numeric target mode value.
        requested_mode_label: Human-readable target mode label.
        current_mode_raw: Current mode payload before command.
        mode_names: Mapping from mode value to label.
        event_time_utc: Timestamp for this command attempt.
        battery_soc: Battery state of charge at the time of command, when known.
        solar_generated_today_kwh: Current day's cumulative solar generation in kWh.
        today_period_forecast: Daytime period forecast snapshot for today.
        response: Optional API response payload on success.
        error: Optional error message on failure.
    """
    await notify_mode_change_email(
        success=success,
        period=period,
        reason=reason,
        requested_mode=requested_mode,
        requested_mode_label=requested_mode_label,
        current_mode_raw=current_mode_raw,
        mode_names=mode_names,
        event_time_utc=event_time_utc,
        battery_soc=battery_soc,
        solar_generated_today_kwh=solar_generated_today_kwh,
        today_period_forecast=today_period_forecast,
        response=response,
        error=error,
        logger=logger,
    )

async def log_current_mode_on_startup(
    sigen: SigenInteraction,
    mode_names: dict[int, str],
) -> tuple[Any, float | None, float | None]:
    """Log retrievable startup data and return current mode/SOC snapshot.

    Args:
        sigen: SigenInteraction instance for API calls.
        mode_names: Mapping from numeric mode to human-readable label.

    Returns:
        Tuple of (current_mode_raw, battery_soc, solar_generated_today_kwh).
    """
    logger.info(ACTION_DIVIDER)
    logger.info("STARTUP CHECK: fetching retrievable inverter data")

    current_mode_raw: Any = None
    battery_soc: float | None = None
    solar_generated_today_kwh: float | None = None

    try:
        current_mode_raw = await sigen.get_operational_mode()
        log_mode_status("startup pull", current_mode_raw, mode_names)
    except Exception as e:
        logger.error("Failed to fetch current inverter mode on startup: %s", e)

    try:
        energy_flow = await sigen.get_energy_flow()
        if isinstance(energy_flow, dict):
            soc_value = energy_flow.get("batterySoc")
            if isinstance(soc_value, (int, float)):
                battery_soc = float(soc_value)
            solar_generated_today_kwh = extract_today_solar_generation_kwh(energy_flow)
        log_payload_tree(logger, "Startup energy flow payload", energy_flow)
    except Exception as e:
        logger.error("Failed to fetch energy flow payload on startup: %s", e)

    try:
        operational_modes = await sigen.get_operational_modes()
        log_payload_tree(
            logger,
            "Startup supported operational modes payload",
            operational_modes,
        )
    except Exception as e:
        logger.error("Failed to fetch supported operational modes on startup: %s", e)

    logger.info(ACTION_DIVIDER)
    return current_mode_raw, battery_soc, solar_generated_today_kwh


async def apply_mode_change(
    *,
    sigen: SigenInteraction | None,
    mode: int,
    period: str,
    reason: str,
    mode_names: dict[int, str],
    export_duration_minutes: int | None = None,
    battery_soc: float | None = None,
    today_period_forecast: dict[str, tuple[int, str]] | None = None,
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
        export_duration_minutes: Optional override window when forcing GRID_EXPORT.
        battery_soc: Battery state of charge at the time of the command, when known.
        today_period_forecast: Daytime period forecast snapshot for today.
        
    Returns:
        True if mode was set or already at target, False if set operation failed.
    """
    return await apply_mode_change_control(
        sigen=sigen,
        mode=mode,
        period=period,
        reason=reason,
        mode_names=mode_names,
        logger=logger,
        notify_mode_change_email=_notify_mode_change_email,
        should_archive_mode_change_events=_should_archive_mode_change_events,
        append_mode_change_event=append_mode_change_event,
        full_simulation_mode=FULL_SIMULATION_MODE,
        export_duration_minutes=export_duration_minutes,
        battery_soc=battery_soc,
        today_period_forecast=today_period_forecast,
    )


async def create_scheduler_interaction(mode_names: dict[int, str]) -> SigenInteraction | None:
    """Create and validate the Sigen API interaction wrapper.
    
    Attempts to initialize API connection and logs current inverter mode on startup.
    Retries authentication twice on failure (three total attempts). If all attempts
    fail, exits the process with a non-zero status.
    
    Args:
        mode_names: Mapping from numeric mode to human-readable label.
        
    Returns:
        SigenInteraction instance if successful.

    Raises:
        SystemExit: If authentication fails after all retry attempts.
    """
    max_attempts = 3  # initial attempt + two retries
    retry_delay_seconds = 2

    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(
                "[SCHEDULER] Initializing inverter interaction (attempt %s/%s).",
                attempt,
                max_attempts,
            )
            sigen = await SigenInteraction.create()
            logger.info(
                "[SCHEDULER] Inverter interaction created successfully: %s",
                type(sigen).__name__,
            )
            startup_today_period_forecast: dict[str, tuple[int, str]] | None = None
            try:
                startup_forecast_provider = create_solar_forecast_provider(logger)
                startup_today_period_forecast = startup_forecast_provider.get_todays_period_forecast()
            except Exception as exc:
                logger.warning(
                    "[SCHEDULER] Could not fetch today's forecast for startup email: %s",
                    exc,
                )
            startup_mode_raw, startup_soc, startup_solar_today_kwh = await log_current_mode_on_startup(sigen, mode_names)
            await _notify_startup_email(
                current_mode_raw=startup_mode_raw,
                battery_soc=startup_soc,
                solar_generated_today_kwh=startup_solar_today_kwh,
                today_period_forecast=startup_today_period_forecast,
                mode_names=mode_names,
                event_time_utc=datetime.now(timezone.utc),
            )
            return sigen
        except Exception as e:
            logger.warning(
                "[SCHEDULER] Inverter authentication/initialization failed on attempt %s/%s. "
                "FULL_SIMULATION_MODE=%s. Reason: %r",
                attempt,
                max_attempts,
                FULL_SIMULATION_MODE,
                e,
            )
            if attempt < max_attempts:
                logger.warning(
                    "[SCHEDULER] Retrying inverter authentication in %s seconds...",
                    retry_delay_seconds,
                )
                await asyncio.sleep(retry_delay_seconds)

    logger.error(
        "[SCHEDULER] Unable to authenticate with inverter after %s attempts. "
        "Exiting process.",
        max_attempts,
    )
    raise SystemExit(1)


async def run_scheduler() -> None:
    """
    Self-contained 5-minute scheduling loop for production use.

    On each tick:
      1. Refreshes solar forecast and sunrise/sunset times at the start of each day,
         then derives equal-width period start times across the solar day.
      2. For each daytime period, begins monitoring SOC when within MAX_PRE_PERIOD_WINDOW
         of the period start.
    3. Calculates dynamic lead time needed to export enough battery headroom using
       a live-solar-adjusted discharge denominator:
           lead_time = (headroom_deficit_kWh * lead_buffer) / effective_battery_export_kw
       where effective_battery_export_kw = inverter_kw - avg(live_solar_kw over last 3 ticks).
         and triggers GRID_EXPORT as soon as that window opens.
      4. At each period start, re-evaluates SOC and sets the definitive mode.
      5. Every action (pre-export and period-start) is performed at most once per
         period per day to avoid redundant inverter commands.
    """

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
        logger.info(
            f"[SCHEDULER]   {k} = {mask_sensitive_value(v, k) if v else '[NOT SET]'}"
        )
    logger.info(
        f"[SCHEDULER] System specs: Solar PV={SOLAR_PV_KW} kW, "
        f"Inverter={INVERTER_KW} kW, Battery={BATTERY_KWH} kWh"
    )
    logger.info(
        "[SCHEDULER] Telemetry grid exchange parsing: prefer buySellPower, then "
        "gridExportPower/feedInPower/exportPower/netGridPower/gridPower. "
        "Sign convention: positive=export, negative=import."
    )

    simulated_soc_raw = os.getenv("SIMULATED_SOC_PERCENT", str(DEFAULT_SIMULATED_SOC_PERCENT))
    try:
        simulated_soc_percent = float(simulated_soc_raw)
    except ValueError:
        logger.warning(
            "[SCHEDULER] Invalid SIMULATED_SOC_PERCENT='%s'. Falling back to %.1f%%.",
            simulated_soc_raw,
            DEFAULT_SIMULATED_SOC_PERCENT,
        )
        simulated_soc_percent = DEFAULT_SIMULATED_SOC_PERCENT

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
    forecast_calibration: dict[str, Any] = build_and_save_forecast_calibration()
    # Tracks which actions have been taken for each period today.
    # day_state[period] = {"pre_set": bool, "start_set": bool}
    day_state: dict[str, dict[str, bool]] = {}
    night_state: dict[str, Any] = {
        "mode_set_key": None,
        "sleep_snapshot_for_date": None,
    }
    sleep_override_seconds: int | None = None
    refresh_auth_on_wake = False
    auth_refreshed_for_date = None
    last_forecast_refresh_utc: datetime | None = None
    last_forecast_solar_archive_utc: datetime | None = None
    forecast_solar_archive_cooldown_until_utc: datetime | None = None
    timed_export_override: dict[str, Any] = load_timed_export_override(logger=logger)
    live_solar_kw_samples: deque[float] = deque(maxlen=LIVE_SOLAR_AVERAGE_SAMPLE_COUNT)
    tick_mode_change_attempts = 0
    tick_mode_change_successes = 0
    tick_mode_change_failures = 0

    async def _apply_mode_change_tracked(**kwargs: Any) -> bool:
        """Apply mode change and record per-tick mode-change counters."""
        nonlocal tick_mode_change_attempts, tick_mode_change_successes, tick_mode_change_failures
        kwargs.setdefault("today_period_forecast", today_period_forecast)
        tick_mode_change_attempts += 1
        ok = await apply_mode_change(**kwargs)
        if ok:
            tick_mode_change_successes += 1
        else:
            tick_mode_change_failures += 1
        return ok

    def _update_timed_export_override(new_state: dict[str, Any]) -> None:
        """Update in-memory timed export override and persist it to disk."""
        nonlocal timed_export_override
        timed_export_override = new_state
        persist_timed_export_override(new_state, logger=logger)

    async def start_timed_grid_export(
        *,
        period: str,
        reason: str,
        duration_minutes: int,
        now_utc: datetime,
        battery_soc: float | None = None,
        is_clipping_export: bool = False,
        export_soc_floor: float | None = None,
    ) -> bool:
        """Delegate timed GRID_EXPORT activation to the timed-export module."""
        return await start_timed_grid_export_helper(
            timed_export_override=timed_export_override,
            set_timed_export_override=_update_timed_export_override,
            period=period,
            reason=reason,
            duration_minutes=duration_minutes,
            now_utc=now_utc,
            battery_soc=battery_soc,
            is_clipping_export=is_clipping_export,
            export_soc_floor=export_soc_floor,
            sigen=sigen,
            mode_names=mode_names,
            apply_mode_change=_apply_mode_change_tracked,
            logger=logger,
            log_mode_status=log_mode_status,
        )

    async def maybe_restore_timed_grid_export(now_utc: datetime) -> str:
        """Delegate timed-export restore handling to the timed-export module."""
        return await maybe_restore_timed_grid_export_helper(
            timed_export_override=timed_export_override,
            set_timed_export_override=_update_timed_export_override,
            now_utc=now_utc,
            fetch_soc=fetch_soc,
            sigen=sigen,
            mode_names=mode_names,
            apply_mode_change=_apply_mode_change_tracked,
            logger=logger,
        )

    async def refresh_daily_data(*, reset_day_state: bool = True) -> None:
        """Fetch and cache solar forecast and sunrise/sunset times for today and tomorrow.
        
        Called at day start and optionally intra-day to refresh period windows,
        forecasts, and sunrise/sunset times used throughout the scheduling loop.

        Args:
            reset_day_state: When True, resets per-period pre/start action flags for a
                new day. When False, preserves existing period action state.
        """
        nonlocal today_period_windows, tomorrow_period_windows
        nonlocal today_period_forecast, tomorrow_period_forecast
        nonlocal today_sunrise_utc, today_sunset_utc, tomorrow_sunrise_utc, day_state
        nonlocal forecast_calibration, last_forecast_refresh_utc
        logger.info("[SCHEDULER] Refreshing daily forecast and sunrise/sunset data.")
        forecast_calibration = build_and_save_forecast_calibration()
        forecast_obj: SolarForecastProvider = create_solar_forecast_provider(logger)
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

        daytime_periods = order_daytime_periods(today_period_forecast)
        tomorrow_daytime_periods = order_daytime_periods(tomorrow_period_forecast)
        today_period_windows = derive_period_windows(sunrise_utc, sunset_utc, daytime_periods)
        tomorrow_period_windows = derive_period_windows(
            tomorrow_sunrise,
            tomorrow_sunset,
            tomorrow_daytime_periods,
        )
        logger.info("[SCHEDULER] Ordered daytime periods today: %s", daytime_periods)
        logger.info("[SCHEDULER] Ordered daytime periods tomorrow: %s", tomorrow_daytime_periods)
        for period, start in today_period_windows.items():
            logger.info(f"[SCHEDULER] Period '{period}' starts at {start.isoformat()} UTC")
        for period, start in tomorrow_period_windows.items():
            logger.info(f"[SCHEDULER] Tomorrow period '{period}' starts at {start.isoformat()} UTC")

        if reset_day_state:
            day_state = {p: {"pre_set": False, "start_set": False, "clipping_export_set": False} for p in daytime_periods}
        else:
            for period in daytime_periods:
                day_state.setdefault(period, {"pre_set": False, "start_set": False, "clipping_export_set": False})

        last_forecast_refresh_utc = datetime.now(timezone.utc)

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
            try:
                raw = await sigen.get_energy_flow()
                diagnostic = f"[{period}] Raw energy_flow response for diagnosis: {raw}"
                logger.error(
                    "%s",
                    colorize_text(diagnostic, ANSI_BRIGHT_RED),
                )
            except Exception as e2:
                logger.error(f"[{period}] Could not re-fetch energy_flow for diagnosis: {e2}")
            return None

    async def archive_inverter_telemetry(reason: str, now_utc: datetime) -> None:
        """Persist one raw inverter telemetry sample for later analysis.

        Args:
            reason: Context label for why the snapshot is being captured.
            now_utc: Current scheduler timestamp in UTC.
        """
        if sigen is None:
            return

        try:
            energy_flow = await sigen.get_energy_flow()
        except KeyError as exc:
            logger.warning(
                "[TELEMETRY] get_energy_flow payload missing key %r. "
                "Snapshot skipped for this tick.",
                exc,
            )
            return
        except Exception as exc:
            logger.warning(
                "[TELEMETRY] get_energy_flow failed: %s. Snapshot skipped for this tick.",
                exc,
            )
            return

        if not isinstance(energy_flow, dict):
            logger.warning(
                "[TELEMETRY] get_energy_flow returned unexpected payload shape (%s). "
                "Snapshot skipped for this tick.",
                _describe_payload_shape(energy_flow),
            )
            return

        try:
            operational_mode = await sigen.get_operational_mode()
        except Exception as exc:
            logger.warning(
                "[TELEMETRY] get_operational_mode failed: %s. "
                "Snapshot skipped for this tick.",
                exc,
            )
            return

        try:
            append_inverter_telemetry_snapshot(
                energy_flow=energy_flow,
                operational_mode=operational_mode,
                reason=reason,
                scheduler_now_utc=now_utc,
                forecast_today=today_period_forecast,
                forecast_tomorrow=tomorrow_period_forecast,
            )
        except Exception as exc:
            logger.warning(
                "[TELEMETRY] Failed to write inverter snapshot: %s | energy_flow=%s | "
                "operational_mode_shape=%s",
                exc,
                _describe_payload_shape(energy_flow),
                _describe_payload_shape(operational_mode),
            )

    async def sample_live_solar_power(now_utc: datetime) -> None:
        """Capture one live solar reading via inverter-control helpers."""
        await sample_live_solar_power_control(
            now_utc=now_utc,
            sigen=sigen,
            live_solar_kw_samples=live_solar_kw_samples,
            live_solar_average_sample_count=LIVE_SOLAR_AVERAGE_SAMPLE_COUNT,
            logger=logger,
        )

    def get_live_solar_average_kw() -> float | None:
        """Return rolling average live solar generation via inverter-control helpers."""
        return get_live_solar_average_kw_control(live_solar_kw_samples)

    def get_effective_battery_export_kw(avg_live_solar_kw: float | None) -> float:
        """Estimate effective export power via inverter-control helpers."""
        return get_effective_battery_export_kw_control(
            avg_live_solar_kw,
            inverter_kw=INVERTER_KW,
            min_effective_battery_export_kw=MIN_EFFECTIVE_BATTERY_EXPORT_KW,
        )

    def estimate_solar(period: str, solar_value: int) -> float:
        """Estimate total solar energy available during a period.
        
        Args:
            solar_value: Forecasted power in watts (typically average for period).
            
        Returns:
            Estimated energy in kWh assuming 3-hour period, capped by system limits.
        """
        period_calibration = get_period_calibration(forecast_calibration, period)
        adjusted_watts = solar_value * period_calibration["power_multiplier"]
        kw = min(adjusted_watts / 1000.0, SOLAR_PV_KW)
        return kw * 3.0  # assume 3-hour period





    def get_active_night_context(now_utc: datetime) -> dict[str, Any] | None:
        """Wrapper delegating to schedule_utils.get_active_night_context with scheduler state.
        
        This maintains backward compatibility for test monkeypatching while keeping
        the core timing logic in the logic module.
        
        Args:
            now_utc: Current time in UTC.
            
        Returns:
            Dict with night window context if active, or None if in daytime.
        """
        return schedule_utils_get_active_night_context(
            now_utc,
            today_period_windows,
            today_period_forecast,
            tomorrow_period_windows,
            tomorrow_period_forecast,
            today_sunset_utc,
            MAX_PRE_PERIOD_WINDOW,
        )


    logger.info(
        f"[SCHEDULER] Starting. Will poll every {POLL_INTERVAL_MINUTES} minutes. "
        f"Max pre-period window: {MAX_PRE_PERIOD_WINDOW_MINUTES} minutes. "
        f"Headroom target: {HEADROOM_TARGET_KWH:.1f} kWh (surplus capacity × 3 h)."
    )
    if FORECAST_REFRESH_INTERVAL_SECONDS > 0:
        logger.info(
            "[SCHEDULER] Intra-day forecast refresh enabled every %s minutes.",
            FORECAST_REFRESH_INTERVAL_MINUTES,
        )
    else:
        logger.info("[SCHEDULER] Intra-day forecast refresh disabled.")

    if FORECAST_SOLAR_ARCHIVE_ENABLED and FORECAST_SOLAR_ARCHIVE_INTERVAL_SECONDS > 0:
        logger.info(
            "[SCHEDULER] Forecast.Solar raw archiving enabled every %s minutes.",
            FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES,
        )
    else:
        logger.info("[SCHEDULER] Forecast.Solar raw archiving disabled.")

    while True:
        now = datetime.now(timezone.utc)
        today = now.date()
        sleep_override_seconds = None
        tick_mode_change_attempts = 0
        tick_mode_change_successes = 0
        tick_mode_change_failures = 0

        if refresh_auth_on_wake and auth_refreshed_for_date != today and sigen is not None:
            try:
                logger.info("[SCHEDULER] Wake-time auth refresh: forcing full re-authentication.")
                refreshed_client = await refresh_sigen_instance()
                sigen = SigenInteraction.from_client(refreshed_client)
                auth_refreshed_for_date = today
                logger.info("[SCHEDULER] Wake-time auth refresh completed.")
            except Exception as exc:
                logger.warning("[SCHEDULER] Wake-time auth refresh failed: %s", exc)
            finally:
                refresh_auth_on_wake = False

        # Refresh forecast and period windows once per calendar day.
        if today != current_date:
            current_date = today
            try:
                await refresh_daily_data(reset_day_state=True)
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

        elif (
            FORECAST_REFRESH_INTERVAL_SECONDS > 0
            and last_forecast_refresh_utc is not None
            and (now - last_forecast_refresh_utc).total_seconds() >= FORECAST_REFRESH_INTERVAL_SECONDS
        ):
            try:
                logger.info(
                    "[SCHEDULER] Running intra-day forecast refresh (interval=%s minutes).",
                    FORECAST_REFRESH_INTERVAL_MINUTES,
                )
                await refresh_daily_data(reset_day_state=False)
            except Exception as exc:
                logger.warning(
                    "[SCHEDULER] Intra-day forecast refresh failed: %s. Will retry next tick.",
                    exc,
                )

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
            logger.warning(
                "[SCHEDULER] Suppressing stale elapsed daytime periods on live tick: %s. "
                "Only the latest elapsed period remains actionable: %s.",
                ", ".join(suppressed_periods),
                latest_elapsed_period,
            )

        if FORECAST_SOLAR_ARCHIVE_ENABLED and FORECAST_SOLAR_ARCHIVE_INTERVAL_SECONDS > 0:
            should_archive = (
                (forecast_solar_archive_cooldown_until_utc is None or now >= forecast_solar_archive_cooldown_until_utc)
                and (
                last_forecast_solar_archive_utc is None
                or (now - last_forecast_solar_archive_utc).total_seconds()
                >= FORECAST_SOLAR_ARCHIVE_INTERVAL_SECONDS
                )
            )
            if should_archive:
                try:
                    archive_forecast_solar_snapshot(logger, now)
                    last_forecast_solar_archive_utc = now
                    forecast_solar_archive_cooldown_until_utc = None
                except Exception as exc:
                    if "429" in str(exc):
                        cooldown_seconds = max(0, FORECAST_SOLAR_RATE_LIMIT_COOLDOWN_SECONDS)
                        forecast_solar_archive_cooldown_until_utc = now + timedelta(
                            seconds=cooldown_seconds
                        )
                        last_forecast_solar_archive_utc = now
                        logger.warning(
                            "[SCHEDULER] Forecast.Solar rate-limited (429). Cooling down until %s.",
                            forecast_solar_archive_cooldown_until_utc.isoformat(),
                        )
                    else:
                        logger.warning(
                            "[SCHEDULER] Forecast.Solar raw archive pull failed: %s",
                            exc,
                        )

        await sample_live_solar_power(now)

        timed_export_status = await maybe_restore_timed_grid_export(now)
        if timed_export_status != "inactive":
            if timed_export_status == "active":
                logger.info(
                    "[TIMED EXPORT] Override active until %s; skipping normal mode decisions this tick.",
                    timed_export_override["restore_at"],
                )
            else:
                logger.info(
                    "[TIMED EXPORT] Restore completed this tick; skipping normal mode decisions until next tick."
                )
            logger.info(
                "[SCHEDULER] Tick mode-change summary: attempted=%s successful=%s failed=%s",
                tick_mode_change_attempts,
                tick_mode_change_successes,
                tick_mode_change_failures,
            )
            logger.info(
                f"[SCHEDULER] Tick at {now.isoformat()} UTC complete. "
                f"Next check in {POLL_INTERVAL_SECONDS // 60} minutes."
            )
            await archive_inverter_telemetry("scheduler_tick", now)
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue

        if (
            night_state["mode_set_key"] is not None
            and night_state["mode_set_key"][0] < today
        ):
            night_state["mode_set_key"] = None

        night_context = get_active_night_context(now)
        night_tick_consumed = False
        if NIGHT_MODE_ENABLED and night_context is not None:
            night_period_solar_kwh = estimate_solar(
                night_context["target_period"], night_context["solar_value"]
            )
            night_result = await handle_night_window(
                now_utc=now,
                night_context=night_context,
                night_state=night_state,
                period_solar_kwh=night_period_solar_kwh,
                fetch_soc=fetch_soc,
                start_timed_grid_export=start_timed_grid_export,
                apply_mode_change=_apply_mode_change_tracked,
                archive_inverter_telemetry=archive_inverter_telemetry,
                sigen=sigen,
                mode_names=mode_names,
            )
            if night_result["sleep_seconds"] is not None:
                sleep_override_seconds = night_result["sleep_seconds"]
            if night_result["refresh_auth_on_wake"]:
                refresh_auth_on_wake = True
            night_tick_consumed = True
            logger.info("[SCHEDULER] Night window active; skipping daytime period evaluation this tick.")

        if not night_tick_consumed:
            ordered_period_windows = sorted(today_period_windows.items(), key=lambda item: item[1])
            for period_index, (period, period_start) in enumerate(ordered_period_windows):
                s = day_state[period]
                solar_value, status = today_period_forecast[period]
                period_solar_kwh = estimate_solar(period, solar_value)
                period_calibration = get_period_calibration(forecast_calibration, period)
                if period_index + 1 < len(ordered_period_windows):
                    period_end_utc = ordered_period_windows[period_index + 1][1]
                else:
                    period_end_utc = today_sunset_utc

                if period == "Morn":
                    await handle_morning_period(
                        now_utc=now,
                        period_start=period_start,
                        period_end_utc=period_end_utc,
                        period_state=s,
                        timed_export_override=timed_export_override,
                        solar_value=solar_value,
                        status=status,
                        period_solar_kwh=period_solar_kwh,
                        period_calibration=period_calibration,
                        fetch_soc=fetch_soc,
                        get_live_solar_average_kw=get_live_solar_average_kw,
                        get_effective_battery_export_kw=get_effective_battery_export_kw,
                        start_timed_grid_export=start_timed_grid_export,
                        apply_mode_change=_apply_mode_change_tracked,
                        sigen=sigen,
                        mode_names=mode_names,
                    )
                elif period == "Aftn":
                    await handle_afternoon_period(
                        now_utc=now,
                        period_start=period_start,
                        period_end_utc=period_end_utc,
                        period_state=s,
                        timed_export_override=timed_export_override,
                        solar_value=solar_value,
                        status=status,
                        period_solar_kwh=period_solar_kwh,
                        period_calibration=period_calibration,
                        fetch_soc=fetch_soc,
                        get_live_solar_average_kw=get_live_solar_average_kw,
                        get_effective_battery_export_kw=get_effective_battery_export_kw,
                        start_timed_grid_export=start_timed_grid_export,
                        apply_mode_change=_apply_mode_change_tracked,
                        sigen=sigen,
                        mode_names=mode_names,
                    )
                elif period == "Eve":
                    await handle_evening_period(
                        now_utc=now,
                        period_start=period_start,
                        period_end_utc=period_end_utc,
                        period_state=s,
                        timed_export_override=timed_export_override,
                        solar_value=solar_value,
                        status=status,
                        period_solar_kwh=period_solar_kwh,
                        period_calibration=period_calibration,
                        fetch_soc=fetch_soc,
                        get_live_solar_average_kw=get_live_solar_average_kw,
                        get_effective_battery_export_kw=get_effective_battery_export_kw,
                        start_timed_grid_export=start_timed_grid_export,
                        apply_mode_change=_apply_mode_change_tracked,
                        sigen=sigen,
                        mode_names=mode_names,
                    )

        logger.info(
            "[SCHEDULER] Tick mode-change summary: attempted=%s successful=%s failed=%s",
            tick_mode_change_attempts,
            tick_mode_change_successes,
            tick_mode_change_failures,
        )
        next_sleep_seconds = sleep_override_seconds or POLL_INTERVAL_SECONDS
        logger.info(
            f"[SCHEDULER] Tick at {now.isoformat()} UTC complete. "
            f"Next check in {next_sleep_seconds // 60} minutes."
        )
        await archive_inverter_telemetry("scheduler_tick", now)
        await asyncio.sleep(next_sleep_seconds)


if __name__ == "__main__":
    asyncio.run(run_scheduler())
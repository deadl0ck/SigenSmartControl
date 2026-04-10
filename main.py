
"""Main scheduler loop for coordinating Sigen inverter mode decisions.

The scheduler continuously monitors solar forecasts, battery state, and tariff windows,
making operational mode decisions that optimize between self-powered generation,
grid arbitrage, and cost-minimization based on real-time conditions.
"""

import asyncio
from collections import deque
import importlib.util
import math
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
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
    PERIOD_TO_MODE,
    FULL_SIMULATION_MODE,
    POLL_INTERVAL_MINUTES,
    FORECAST_REFRESH_INTERVAL_MINUTES,
    FORECAST_SOLAR_ARCHIVE_ENABLED,
    FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES,
    FORECAST_SOLAR_RATE_LIMIT_COOLDOWN_MINUTES,
    MAX_PRE_PERIOD_WINDOW_MINUTES,
    NIGHT_MODE_ENABLED,
    NIGHT_SLEEP_MODE_ENABLED,
    HEADROOM_TARGET_KWH,
    ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE,
    ESTIMATED_HOME_LOAD_KW,
    BRIDGE_BATTERY_RESERVE_KWH,
    SOLAR_PV_KW,
    INVERTER_KW,
    BATTERY_KWH,
    LIVE_SOLAR_AVERAGE_SAMPLE_COUNT,
    MIN_EFFECTIVE_BATTERY_EXPORT_KW,
    DEFAULT_SIMULATED_SOC_PERCENT,
    CLIPPING_BATTERY_SOC_HIGH_PERCENT,
    CLIPPING_SECONDARY_NEAR_CEILING_MARGIN_KW,
    MAX_TIMED_EXPORT_MINUTES,
)
from logic.decision_logic import (
    decide_operational_mode,
    calc_headroom_kwh,
)
from logic.schedule_utils import (
    _parse_utc,
    derive_period_windows,
    get_first_period_info,
    get_hours_until_cheap_rate,
    get_schedule_period_for_time,
    suppress_elapsed_periods_except_latest,
    LOCAL_TZ,
)
from logic.mode_control import (
    should_use_ai_mode_for_evening,
    extract_mode_value,
    mode_matches_target,
    ACTION_DIVIDER,
)
from weather.sunrise_sunset import get_sunrise_sunset
from config.constants import LATITUDE, LONGITUDE
from telemetry.forecast_calibration import build_and_save_forecast_calibration, get_period_calibration
from telemetry.telemetry_archive import (
    append_inverter_telemetry_snapshot,
    append_mode_change_event,
    extract_live_solar_power_kw,
)


class LevelColorFormatter(logging.Formatter):
    """Apply ANSI colors to warning/error levels for terminal readability."""

    _RESET = "\033[0m"
    _ORANGE = "\033[38;5;214m"
    _RED = "\033[31m"

    def __init__(self, fmt: str) -> None:
        """Initialize formatter with optional color support.

        Args:
            fmt: Base logging format string.
        """
        super().__init__(fmt=fmt)
        self._use_color = bool(getattr(os.sys.stderr, "isatty", lambda: False)()) and not os.getenv(
            "NO_COLOR"
        )

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record, colorizing WARNING and ERROR/CRITICAL levels.

        Args:
            record: Standard logging record.

        Returns:
            Formatted log line.
        """
        if not self._use_color:
            return super().format(record)

        original_levelname = record.levelname
        try:
            if record.levelno == logging.WARNING:
                record.levelname = f"{self._ORANGE}{record.levelname}{self._RESET}"
            elif record.levelno >= logging.ERROR:
                record.levelname = f"{self._RED}{record.levelname}{self._RESET}"
            return super().format(record)
        finally:
            record.levelname = original_levelname


# --- Logging configuration ---
LOG_LEVEL = getattr(logging, CONFIG_LOG_LEVEL, logging.INFO)
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(
    LevelColorFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
logging.basicConfig(level=LOG_LEVEL, handlers=[_log_handler])
logger = logging.getLogger("sigen_control")

_EMAIL_SENDER_ADDRESS = os.getenv("EMAIL_SENDER", "").strip()
_EMAIL_RECEIVER_ADDRESS = os.getenv("EMAIL_RECEIVER", "").strip()
_EMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()
_EMAIL_SENDER_INSTANCE: Any | None = None
_EMAIL_CONFIG_LOGGED = False


def _is_truthy_env(name: str) -> bool:
    """Return True when an environment variable is set to a truthy value."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}

# How often the scheduler wakes up to re-evaluate each period.
POLL_INTERVAL_SECONDS = POLL_INTERVAL_MINUTES * 60
FORECAST_REFRESH_INTERVAL_SECONDS = FORECAST_REFRESH_INTERVAL_MINUTES * 60
FORECAST_SOLAR_ARCHIVE_INTERVAL_SECONDS = FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES * 60
FORECAST_SOLAR_RATE_LIMIT_COOLDOWN_SECONDS = FORECAST_SOLAR_RATE_LIMIT_COOLDOWN_MINUTES * 60
# How far ahead of a period start we begin monitoring SOC for a potential pre-export.
MAX_PRE_PERIOD_WINDOW = timedelta(minutes=MAX_PRE_PERIOD_WINDOW_MINUTES)
_CANONICAL_DAYTIME_PERIOD_ORDER: tuple[str, ...] = ("Morn", "Aftn", "Eve")


def order_daytime_periods(period_forecast: dict[str, tuple[int, str]]) -> list[str]:
    """Return daytime periods in deterministic scheduler order.

    Args:
        period_forecast: Mapping of period labels to forecast tuples.

    Returns:
        Ordered daytime period list. Known periods are returned as Morn, Aftn,
        Eve when present. Any additional daytime labels are appended in
        alphabetical order for deterministic behavior.
    """
    known = [period for period in _CANONICAL_DAYTIME_PERIOD_ORDER if period in period_forecast]
    extras = sorted(
        period
        for period in period_forecast
        if period.upper() != "NIGHT" and period not in _CANONICAL_DAYTIME_PERIOD_ORDER
    )
    return known + extras


# --- Scheduler interaction and mode control ---

def _format_tree_leaf(value: Any) -> str:
    """Format a scalar value for tree logging.

    Args:
        value: Scalar value to format.

    Returns:
        String-safe representation suitable for log output.
    """
    return repr(value)


def _iter_tree_lines(payload: Any, prefix: str = "") -> list[str]:
    """Convert nested dict/list payloads into ASCII tree lines.

    Args:
        payload: Value to render, usually dict/list from API responses.
        prefix: Internal indentation prefix used during recursion.

    Returns:
        List of formatted tree lines.
    """
    lines: list[str] = []

    if isinstance(payload, dict):
        items = list(payload.items())
        for index, (key, value) in enumerate(items):
            is_last = index == len(items) - 1
            branch = "`- " if is_last else "|- "
            child_prefix = prefix + ("   " if is_last else "|  ")

            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{branch}{key}:")
                lines.extend(_iter_tree_lines(value, child_prefix))
            else:
                lines.append(f"{prefix}{branch}{key}: {_format_tree_leaf(value)}")
        return lines

    if isinstance(payload, list):
        for index, value in enumerate(payload):
            is_last = index == len(payload) - 1
            branch = "`- " if is_last else "|- "
            child_prefix = prefix + ("   " if is_last else "|  ")
            label = f"[{index}]"

            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{branch}{label}:")
                lines.extend(_iter_tree_lines(value, child_prefix))
            else:
                lines.append(f"{prefix}{branch}{label}: {_format_tree_leaf(value)}")
        return lines

    lines.append(f"{prefix}`- {_format_tree_leaf(payload)}")
    return lines


def log_payload_tree(title: str, payload: Any) -> None:
    """Log nested payload data as a readable multi-line tree.

    Args:
        title: Human-readable section title for this payload.
        payload: Structured payload value from the inverter API.
    """
    logger.info("%s:", title)
    for line in _iter_tree_lines(payload):
        logger.info("  %s", line)


def _load_email_sender_class() -> type | None:
    """Load EmailSender class from email/email_sender.py without shadowing stdlib email package.

    Returns:
        EmailSender class when available, otherwise None.
    """
    sender_path = Path(__file__).resolve().parent / "email" / "email_sender.py"
    if not sender_path.exists():
        return None

    spec = importlib.util.spec_from_file_location("sigen_email_sender", sender_path)
    if spec is None or spec.loader is None:
        return None

    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "EmailSender", None)


def _get_email_sender_instance() -> Any | None:
    """Build and cache the email sender helper when env vars are configured.

    Returns:
        Initialized EmailSender instance, or None when email notifications are disabled.
    """
    global _EMAIL_SENDER_INSTANCE
    global _EMAIL_CONFIG_LOGGED

    if _EMAIL_SENDER_INSTANCE is not None:
        return _EMAIL_SENDER_INSTANCE

    # Safety guard: test runs should not trigger real email sends unless explicitly enabled.
    if _is_truthy_env("SIGEN_DISABLE_MODE_CHANGE_EMAILS"):
        if not _EMAIL_CONFIG_LOGGED:
            logger.info(
                "[EMAIL] Mode-change email notifications disabled by "
                "SIGEN_DISABLE_MODE_CHANGE_EMAILS."
            )
            _EMAIL_CONFIG_LOGGED = True
        return None

    running_under_pytest = bool(os.getenv("PYTEST_CURRENT_TEST"))
    allow_pytest_emails = _is_truthy_env("SIGEN_ALLOW_EMAIL_NOTIFICATIONS_IN_TESTS")
    if running_under_pytest and not allow_pytest_emails:
        if not _EMAIL_CONFIG_LOGGED:
            logger.info(
                "[EMAIL] Mode-change email notifications disabled during pytest run. "
                "Set SIGEN_ALLOW_EMAIL_NOTIFICATIONS_IN_TESTS=true to override."
            )
            _EMAIL_CONFIG_LOGGED = True
        return None

    if not (_EMAIL_SENDER_ADDRESS and _EMAIL_RECEIVER_ADDRESS and _EMAIL_APP_PASSWORD):
        if not _EMAIL_CONFIG_LOGGED:
            logger.info(
                "[EMAIL] Mode-change email notifications disabled (missing EMAIL_SENDER, "
                "EMAIL_RECEIVER, or GMAIL_APP_PASSWORD)."
            )
            _EMAIL_CONFIG_LOGGED = True
        return None

    email_sender_cls = _load_email_sender_class()
    if email_sender_cls is None:
        logger.warning("[EMAIL] Could not load EmailSender class from email/email_sender.py.")
        return None

    try:
        _EMAIL_SENDER_INSTANCE = email_sender_cls(_EMAIL_SENDER_ADDRESS, _EMAIL_APP_PASSWORD)
        logger.info("[EMAIL] Mode-change email notifications enabled.")
        return _EMAIL_SENDER_INSTANCE
    except Exception as exc:
        logger.error("[EMAIL] Failed to initialize EmailSender: %s", exc)
        return None


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
    response: Any | None = None,
    error: str | None = None,
) -> None:
    """Send a best-effort email describing an inverter mode command attempt.

    Args:
        success: True when the mode command call succeeded.
        period: Scheduler period/context label.
        reason: Decision reason for the command.
        requested_mode: Numeric target mode value.
        requested_mode_label: Human-readable target mode label.
        current_mode_raw: Current mode payload before command.
        mode_names: Mapping from mode value to label.
        event_time_utc: Timestamp for this command attempt.
        response: Optional API response payload on success.
        error: Optional error message on failure.
    """
    sender = _get_email_sender_instance()
    if sender is None:
        logger.info(
            "[EMAIL] Skipping mode-change notification (sender unavailable). "
            "period=%s target=%s(%s)",
            period,
            requested_mode_label,
            requested_mode,
        )
        return

    current_mode_value = extract_mode_value(current_mode_raw)
    if current_mode_value is not None:
        previous_mode_label = str(mode_names.get(current_mode_value, current_mode_value))
        previous_mode_value = str(current_mode_value)
    else:
        previous_mode_label = "Unknown"
        previous_mode_value = str(current_mode_raw)

    status = "SUCCESS" if success else "FAILED"
    local_time = event_time_utc.astimezone(LOCAL_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    subject = f"[Sigen] Mode command {status}: {requested_mode_label} ({period})"
    body = (
        f"Mode command status: {status}\n"
        f"Local time: {local_time}\n"
        f"UTC time: {event_time_utc.isoformat()}\n"
        f"Context/period: {period}\n"
        f"Previous mode: {previous_mode_label} (raw={previous_mode_value})\n"
        f"Requested mode: {requested_mode_label} (value={requested_mode})\n"
        f"Reason: {reason}\n"
        f"Error: {error if error else 'None'}\n"
        f"Response: {response if response is not None else 'None'}\n"
    )

    try:
        logger.info(
            "[EMAIL] Sending mode-change notification: status=%s period=%s target=%s(%s).",
            status,
            period,
            requested_mode_label,
            requested_mode,
        )
        await asyncio.to_thread(sender.send, _EMAIL_RECEIVER_ADDRESS, subject, body)
        logger.info("[EMAIL] Sent mode-change notification for %s.", period)
    except Exception as exc:
        logger.error("[EMAIL] Failed to send mode-change notification: %s", exc)

async def log_current_mode_on_startup(sigen: SigenInteraction, mode_names: dict[int, str]) -> None:
    """Log all retrievable inverter startup data.
    
    Args:
        sigen: SigenInteraction instance for API calls.
        mode_names: Mapping from numeric mode to human-readable label.
    """
    logger.info(ACTION_DIVIDER)
    logger.info("STARTUP CHECK: fetching retrievable inverter data")

    try:
        current_mode_raw = await sigen.get_operational_mode()
        current_mode = extract_mode_value(current_mode_raw)
        if current_mode is not None:
            logger.info(
                "Startup current mode: %s (value=%s)",
                mode_names.get(current_mode, current_mode),
                current_mode,
            )
        else:
            logger.info("Startup current mode response (unparsed): %s", current_mode_raw)
    except Exception as e:
        logger.error("Failed to fetch current inverter mode on startup: %s", e)

    try:
        energy_flow = await sigen.get_energy_flow()
        log_payload_tree("Startup energy flow payload", energy_flow)
    except Exception as e:
        logger.error("Failed to fetch energy flow payload on startup: %s", e)

    try:
        operational_modes = await sigen.get_operational_modes()
        log_payload_tree(
            "Startup supported operational modes payload",
            operational_modes,
        )
    except Exception as e:
        logger.error("Failed to fetch supported operational modes on startup: %s", e)

    logger.info(ACTION_DIVIDER)


async def apply_mode_change(
    *,
    sigen: SigenInteraction | None,
    mode: int,
    period: str,
    reason: str,
    mode_names: dict[int, str],
    export_duration_minutes: int | None = None,
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
        
    Returns:
        True if mode was set or already at target, False if set operation failed.
    """
    mode_label = mode_names.get(mode, mode)
    current_mode_raw: Any = None
    if sigen is None:
        if FULL_SIMULATION_MODE:
            event_time = datetime.now(timezone.utc)
            simulated_response = {
                "simulated": True,
                "mode": mode,
                "note": "Sigen interaction unavailable; simulated fallback path.",
            }
            logger.info(ACTION_DIVIDER)
            logger.info(ACTION_DIVIDER)
            logger.info(
                f"[SIMULATION] set_operational_mode(mode={mode_label}, value={mode}) "
                "- command suppressed in simulation mode"
            )
            logger.info("[SIMULATION] Context=%s | reason=%s", period, reason)
            logger.info(ACTION_DIVIDER)
            logger.info(ACTION_DIVIDER)
            append_mode_change_event(
                scheduler_now_utc=event_time,
                period=period,
                requested_mode=mode,
                requested_mode_label=str(mode_label),
                reason=reason,
                simulated=True,
                success=True,
                current_mode=None,
                response=simulated_response,
            )
            await _notify_mode_change_email(
                success=True,
                period=period,
                reason=reason,
                requested_mode=mode,
                requested_mode_label=str(mode_label),
                current_mode_raw=None,
                mode_names=mode_names,
                event_time_utc=event_time,
                response=simulated_response,
            )
            return True

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

    event_time = datetime.now(timezone.utc)
    try:
        if mode == SIGEN_MODES["GRID_EXPORT"] and export_duration_minutes is not None:
            response = await sigen.export_to_grid(export_duration_minutes)
        else:
            response = await sigen.set_operational_mode(mode)
        logger.info(f"Set mode response for {period}: {response}")
        append_mode_change_event(
            scheduler_now_utc=event_time,
            period=period,
            requested_mode=mode,
            requested_mode_label=str(mode_label),
            reason=reason,
            simulated=FULL_SIMULATION_MODE,
            success=True,
            current_mode=current_mode_raw,
            response=response,
        )
        logger.info(
            "[EMAIL] Queueing mode-change notification: status=SUCCESS period=%s target=%s(%s) "
            "simulated=%s",
            period,
            mode_label,
            mode,
            FULL_SIMULATION_MODE,
        )
        await _notify_mode_change_email(
            success=True,
            period=period,
            reason=reason,
            requested_mode=mode,
            requested_mode_label=str(mode_label),
            current_mode_raw=current_mode_raw,
            mode_names=mode_names,
            event_time_utc=event_time,
            response=response,
        )
        return True
    except Exception as e:
        logger.error(f"Failed to set mode for {period}: {e}")
        append_mode_change_event(
            scheduler_now_utc=event_time,
            period=period,
            requested_mode=mode,
            requested_mode_label=str(mode_label),
            reason=reason,
            simulated=FULL_SIMULATION_MODE,
            success=False,
            current_mode=current_mode_raw,
            error=str(e),
        )
        logger.info(
            "[EMAIL] Queueing mode-change notification: status=FAILED period=%s target=%s(%s) "
            "simulated=%s",
            period,
            mode_label,
            mode,
            FULL_SIMULATION_MODE,
        )
        await _notify_mode_change_email(
            success=False,
            period=period,
            reason=reason,
            requested_mode=mode,
            requested_mode_label=str(mode_label),
            current_mode_raw=current_mode_raw,
            mode_names=mode_names,
            event_time_utc=event_time,
            error=str(e),
        )
        return False


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
            await log_current_mode_on_startup(sigen, mode_names)
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
        "mode_set_for": None,
        "sleep_snapshot_for_date": None,
    }
    sleep_override_seconds: int | None = None
    refresh_auth_on_wake = False
    auth_refreshed_for_date = None
    last_forecast_refresh_utc: datetime | None = None
    last_forecast_solar_archive_utc: datetime | None = None
    forecast_solar_archive_cooldown_until_utc: datetime | None = None
    timed_export_override: dict[str, Any] = {
        "active": False,
        "started_at": None,
        "restore_at": None,
        "restore_mode": None,
        "restore_mode_label": None,
        "trigger_period": None,
        "duration_minutes": None,
    }
    live_solar_kw_samples: deque[float] = deque(maxlen=LIVE_SOLAR_AVERAGE_SAMPLE_COUNT)
    tick_mode_change_attempts = 0
    tick_mode_change_successes = 0
    tick_mode_change_failures = 0

    async def _apply_mode_change_tracked(**kwargs: Any) -> bool:
        """Apply mode change and record per-tick mode-change counters."""
        nonlocal tick_mode_change_attempts, tick_mode_change_successes, tick_mode_change_failures
        tick_mode_change_attempts += 1
        ok = await apply_mode_change(**kwargs)
        if ok:
            tick_mode_change_successes += 1
        else:
            tick_mode_change_failures += 1
        return ok

    async def start_timed_grid_export(
        *,
        period: str,
        reason: str,
        duration_minutes: int,
        now_utc: datetime,
    ) -> bool:
        """Switch to GRID_EXPORT for a bounded duration, then restore prior mode later.

        Args:
            period: Human-readable period label that triggered export.
            reason: Decision explanation for audit logs.
            duration_minutes: Requested export duration in minutes.
            now_utc: Current scheduler timestamp in UTC.

        Returns:
            True when timed export is activated, False otherwise.
        """
        nonlocal timed_export_override
        if timed_export_override["active"]:
            logger.info(
                "[TIMED EXPORT] Requested by %s but override already active until %s. "
                "Keeping current override and skipping new request.",
                period,
                timed_export_override["restore_at"],
            )
            return False

        requested_minutes = max(1, duration_minutes)
        clamped_minutes = min(requested_minutes, MAX_TIMED_EXPORT_MINUTES)
        if clamped_minutes < requested_minutes:
            logger.warning(
                "[TIMED EXPORT] Requested duration %s minutes exceeds safety cap of %s minutes. "
                "Clamping to %s minutes.",
                requested_minutes,
                MAX_TIMED_EXPORT_MINUTES,
                clamped_minutes,
            )
        restore_at = now_utc + timedelta(minutes=clamped_minutes)

        restore_mode: int | None = None
        restore_label = "UNKNOWN"
        if sigen is not None:
            try:
                current_mode_raw = await sigen.get_operational_mode()
                restore_mode = extract_mode_value(current_mode_raw)
                if restore_mode is None:
                    logger.warning(
                        "[TIMED EXPORT] Could not parse current mode before timed export; "
                        "refusing override to avoid unsafe restore target. raw=%s",
                        current_mode_raw,
                    )
                    return False
                restore_label = str(mode_names.get(restore_mode, restore_mode))
            except Exception as exc:
                logger.warning(
                    "[TIMED EXPORT] Failed to read current mode before timed export: %s",
                    exc,
                )
                return False

        logger.info(ACTION_DIVIDER)
        logger.info(
            "[TIMED EXPORT] Switching to GRID_EXPORT now. Trigger period=%s, duration=%s min, "
            "active_until=%s, will_restore_to=%s",
            period,
            clamped_minutes,
            restore_at.isoformat(),
            restore_label,
        )
        logger.info(ACTION_DIVIDER)

        apply_reason = (
            f"{reason} Timed export override active for {clamped_minutes} minutes "
            f"(until {restore_at.isoformat()}) before restoring previous mode {restore_label}."
        )
        ok = await _apply_mode_change_tracked(
            sigen=sigen,
            mode=SIGEN_MODES["GRID_EXPORT"],
            period=f"{period} (timed-export-start)",
            reason=apply_reason,
            mode_names=mode_names,
            export_duration_minutes=clamped_minutes,
        )
        if not ok:
            return False

        timed_export_override = {
            "active": True,
            "started_at": now_utc,
            "restore_at": restore_at,
            "restore_mode": restore_mode,
            "restore_mode_label": restore_label,
            "trigger_period": period,
            "duration_minutes": clamped_minutes,
        }
        return True

    async def maybe_restore_timed_grid_export(now_utc: datetime) -> bool:
        """Restore pre-export mode when active timed export window has elapsed.

        Args:
            now_utc: Current scheduler timestamp in UTC.

        Returns:
            True when an override is active and normal scheduler decisions should be skipped.
        """
        nonlocal timed_export_override
        if not timed_export_override["active"]:
            return False

        restore_at = timed_export_override["restore_at"]
        if restore_at is None:
            logger.warning("[TIMED EXPORT] Override state missing restore_at; clearing state.")
            timed_export_override = {
                "active": False,
                "started_at": None,
                "restore_at": None,
                "restore_mode": None,
                "restore_mode_label": None,
                "trigger_period": None,
                "duration_minutes": None,
            }
            return False

        if now_utc < restore_at:
            return True

        restore_mode = timed_export_override["restore_mode"]
        restore_label = timed_export_override["restore_mode_label"]
        trigger_period = timed_export_override["trigger_period"]
        duration_minutes = timed_export_override["duration_minutes"]
        if restore_mode is None:
            logger.warning(
                "[TIMED EXPORT] Restore mode unavailable after timed export window from %s. "
                "Leaving scheduler control enabled without automated restore.",
                trigger_period,
            )
            timed_export_override = {
                "active": False,
                "started_at": None,
                "restore_at": None,
                "restore_mode": None,
                "restore_mode_label": None,
                "trigger_period": None,
                "duration_minutes": None,
            }
            return False

        logger.info(ACTION_DIVIDER)
        logger.info(
            "[TIMED EXPORT] Export window completed. Restoring prior mode %s now. "
            "Triggered_by=%s, configured_duration=%s min, restore_due_at=%s",
            restore_label,
            trigger_period,
            duration_minutes,
            restore_at.isoformat(),
        )
        logger.info(ACTION_DIVIDER)

        restore_ok = await _apply_mode_change_tracked(
            sigen=sigen,
            mode=restore_mode,
            period=f"{trigger_period} (timed-export-restore)",
            reason=(
                "Timed grid export window complete; restoring mode active before override "
                f"({restore_label})."
            ),
            mode_names=mode_names,
        )
        if restore_ok:
            timed_export_override = {
                "active": False,
                "started_at": None,
                "restore_at": None,
                "restore_mode": None,
                "restore_mode_label": None,
                "trigger_period": None,
                "duration_minutes": None,
            }
            return False

        logger.warning("[TIMED EXPORT] Restore attempt failed; will retry next scheduler tick.")
        return True

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
            day_state = {p: {"pre_set": False, "start_set": False} for p in daytime_periods}
        else:
            for period in daytime_periods:
                day_state.setdefault(period, {"pre_set": False, "start_set": False})

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
            operational_mode = await sigen.get_operational_mode()
            append_inverter_telemetry_snapshot(
                energy_flow=energy_flow,
                operational_mode=operational_mode,
                reason=reason,
                scheduler_now_utc=now_utc,
                forecast_today=today_period_forecast,
                forecast_tomorrow=tomorrow_period_forecast,
            )
        except Exception as exc:
            logger.warning(f"[TELEMETRY] Failed to capture inverter snapshot: {exc}")

    async def sample_live_solar_power(now_utc: datetime) -> None:
        """Capture one live solar reading for rolling export-capacity calculations.

        Args:
            now_utc: Current scheduler timestamp in UTC.
        """
        if sigen is None:
            return
        try:
            energy_flow = await sigen.get_energy_flow()
            solar_kw = extract_live_solar_power_kw(energy_flow)
            if solar_kw is not None:
                live_solar_kw_samples.append(max(0.0, solar_kw))
                logger.info(
                    f"[SCHEDULER] Live solar sample: {solar_kw:.2f} kW "
                    f"({len(live_solar_kw_samples)}/{LIVE_SOLAR_AVERAGE_SAMPLE_COUNT} samples)"
                )
        except Exception as exc:
            logger.warning(f"[SCHEDULER] Failed to sample live solar power: {exc}")

    def get_live_solar_average_kw() -> float | None:
        """Return rolling average live solar generation across recent configured samples."""
        if not live_solar_kw_samples:
            return None
        return sum(live_solar_kw_samples) / len(live_solar_kw_samples)

    def get_effective_battery_export_kw(avg_live_solar_kw: float | None) -> float:
        """Estimate available battery discharge/export power after live solar occupancy.

        Args:
            avg_live_solar_kw: Rolling average live solar generation in kW.

        Returns:
            Effective kW available for battery-driven export/discharge.
        """
        if avg_live_solar_kw is None:
            return INVERTER_KW
        available_kw = INVERTER_KW - max(0.0, avg_live_solar_kw)
        return min(INVERTER_KW, max(MIN_EFFECTIVE_BATTERY_EXPORT_KW, available_kw))

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

    def promote_status_for_live_clipping_risk(
        period: str,
        status: str,
        soc: float | None,
        avg_live_solar_kw: float | None,
    ) -> tuple[str, str | None]:
        """Promote Amber forecast status to Green when live clipping risk is high.

        This runtime correction handles cases where forecast underestimates irradiance.
        If live solar is already near inverter ceiling and battery SOC is high, we
        treat the period as Green for decision purposes so headroom export logic can
        run preemptively.

        Args:
            period: Current period name (e.g., Morn/Aftn/Eve).
            status: Forecast status for the period.
            soc: Current battery SOC percentage.
            avg_live_solar_kw: Rolling live solar average in kW.

        Returns:
            Tuple of (effective_status, override_reason). override_reason is None
            when no promotion is applied.
        """
        status_key = (status or "").upper()
        period_key = (period or "").upper()

        if period_key not in {"MORN", "AFTN"}:
            return status, None
        if status_key != "AMBER":
            return status, None
        if soc is None or soc < CLIPPING_BATTERY_SOC_HIGH_PERCENT:
            return status, None
        if avg_live_solar_kw is None:
            return status, None

        trigger_kw = INVERTER_KW - CLIPPING_SECONDARY_NEAR_CEILING_MARGIN_KW
        if avg_live_solar_kw < trigger_kw:
            return status, None

        reason = (
            "Live clipping-risk override: promoting AMBER to GREEN because "
            f"SOC={soc:.1f}% and avg live solar={avg_live_solar_kw:.2f} kW is near "
            f"inverter ceiling ({INVERTER_KW:.1f} kW)."
        )
        return "Green", reason

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
        solar_avg_kw_3: float | None = None,
        effective_battery_export_kw: float | None = None,
        lead_time_hours_adjusted: float | None = None,
        mode: int | None = None,
        reason: str = "",
        outcome: str = "",
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
            solar_avg_kw_3: Rolling average solar kW over latest three samples.
            effective_battery_export_kw: Estimated battery export kW available after solar occupancy.
            lead_time_hours_adjusted: Lead-time computed from adjusted export denominator.
            mode: Target operational mode, or None.
            reason: Explanation of decision logic.
            outcome: Description of action taken.
        """
        mode_label = mode_names.get(mode, mode) if mode is not None else "N/A"
        export_by_label = export_by_utc.isoformat() if export_by_utc is not None else "N/A"
        base_period = period.split(" ", 1)[0]
        base_period = base_period.split("->")[-1]
        period_labels = {
            "Morn": "MORNING",
            "Aftn": "AFTERNOON",
            "Eve": "EVENING",
            "NIGHT": "NIGHT",
        }
        period_display = period_labels.get(base_period, base_period.upper())
        period_start_local = period_start_utc.astimezone(LOCAL_TZ).strftime("%H:%M")
        logger.info(
            f"[{period}] {stage} CHECK FOR {period_display} (Starts at {period_start_local}):"
        )
        logger.info(f"[{period}]     -> now={now_utc.isoformat()}")
        logger.info(f"[{period}]     -> period_start={period_start_utc.isoformat()}")
        logger.info(f"[{period}]     -> forecast_w={solar_value}")
        logger.info(f"[{period}]     -> status={status}")
        logger.info(f"[{period}]     -> expected_solar_kwh={period_solar_kwh:.2f}")
        logger.info(f"[{period}]     -> soc={soc if soc is not None else 'N/A'}")
        logger.info(
            f"[{period}]     -> headroom_kwh={f'{headroom_kwh:.2f}' if headroom_kwh is not None else 'N/A'}"
        )
        logger.info(f"[{period}]     -> headroom_target_kwh={headroom_target_kwh:.2f}")
        logger.info(f"[{period}]     -> headroom_deficit_kwh={headroom_deficit_kwh:.2f}")
        logger.info(
            f"[{period}]     -> solar_avg_kw_3={f'{solar_avg_kw_3:.2f}' if solar_avg_kw_3 is not None else 'N/A'}"
        )
        logger.info(
            "[{}]     -> effective_battery_export_kw={}".format(
                period,
                f"{effective_battery_export_kw:.2f}"
                if effective_battery_export_kw is not None
                else "N/A",
            )
        )
        logger.info(
            "[{}]     -> lead_time_hours_adjusted={}".format(
                period,
                f"{lead_time_hours_adjusted:.2f}" if lead_time_hours_adjusted is not None else "N/A",
            )
        )
        logger.info(f"[{period}]     -> export_by={export_by_label}")
        logger.info(f"[{period}]     -> decision_mode={mode_label}")
        logger.info(f"[{period}]     -> outcome={outcome}")
        logger.info(f"[{period}]     -> reason={reason}")

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

        override_active = await maybe_restore_timed_grid_export(now)
        if override_active:
            logger.info(
                "[TIMED EXPORT] Override active until %s; skipping normal mode decisions this tick.",
                timed_export_override["restore_at"],
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

        if night_state["mode_set_for"] is not None and night_state["mode_set_for"] < today:
            night_state["mode_set_for"] = None

        night_context = get_active_night_context(now)
        if NIGHT_MODE_ENABLED and night_context is not None:
            night_period_name = f"Night->{night_context['target_period']}"
            night_period_solar_kwh = estimate_solar(
                night_context["target_period"],
                night_context["solar_value"],
            )
            night_headroom_target_kwh = HEADROOM_TARGET_KWH
            night_mode = PERIOD_TO_MODE["NIGHT"]
            night_mode_reason = "Night window active. Forcing AI mode overnight."

            if night_state["mode_set_for"] != night_context["target_date"]:
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
                    outcome="night mode applied",
                )
                try:
                    ok = await _apply_mode_change_tracked(
                        sigen=sigen,
                        mode=night_mode,
                        period=night_period_name,
                        reason=night_mode_reason,
                        mode_names=mode_names,
                    )
                    if ok:
                        night_state["mode_set_for"] = night_context["target_date"]
                except Exception as e:
                    logger.error(f"[{night_period_name}] Unexpected error applying base night mode: {e}")

            if NIGHT_SLEEP_MODE_ENABLED:
                pre_window_opens_at = night_context["target_start"] - MAX_PRE_PERIOD_WINDOW

                if now < pre_window_opens_at:
                    sleep_seconds = max(1, int((pre_window_opens_at - now).total_seconds()))
                    if sleep_seconds > POLL_INTERVAL_SECONDS:
                        local_date = now.astimezone(LOCAL_TZ).date()
                        if (
                            night_context["window_name"] == "EVENING-NIGHT"
                            and night_state.get("sleep_snapshot_for_date") != local_date
                        ):
                            await archive_inverter_telemetry("night_sleep_start", now)
                            night_state["sleep_snapshot_for_date"] = local_date
                            logger.info(
                                "[SCHEDULER] Captured end-of-day telemetry snapshot before night sleep."
                            )
                        sleep_override_seconds = sleep_seconds
                        refresh_auth_on_wake = True
                        logger.info(
                            "[SCHEDULER] Night sleep mode active. Sleeping for %s minutes "
                            "until %s (%s).",
                            sleep_seconds // 60,
                            pre_window_opens_at.isoformat(),
                            "morning pre-period window",
                        )

        for period, period_start in today_period_windows.items():
            s = day_state[period]
            solar_value, status = today_period_forecast[period]
            period_solar_kwh = estimate_solar(period, solar_value)
            period_calibration = get_period_calibration(forecast_calibration, period)

            # --- Pre-period export check ---
            # Active when within MAX_PRE_PERIOD_WINDOW of the period start.
            if not s["pre_set"] and period_start - MAX_PRE_PERIOD_WINDOW <= now < period_start:
                soc = await fetch_soc(period)
                if soc is not None:
                    headroom_kwh = calc_headroom_kwh(BATTERY_KWH, soc)
                    headroom_target_kwh = HEADROOM_TARGET_KWH
                    headroom_deficit = max(0.0, headroom_target_kwh - headroom_kwh)
                    solar_avg_kw_3 = get_live_solar_average_kw()
                    decision_status, status_override_reason = promote_status_for_live_clipping_risk(
                        period,
                        status,
                        soc,
                        solar_avg_kw_3,
                    )
                    effective_battery_export_kw = get_effective_battery_export_kw(solar_avg_kw_3)
                    lead_time_hours_adjusted = 0.0
                    if headroom_deficit > 0:
                        # Time needed = deficit (kWh) / effective battery export capacity (kW),
                        # with calibration buffer multiplier.
                        lead_time_hours_adjusted = (
                            headroom_deficit
                            * period_calibration["export_lead_buffer_multiplier"]
                        ) / effective_battery_export_kw
                        lead_time = timedelta(
                            hours=lead_time_hours_adjusted
                        )
                        export_by = period_start - lead_time
                    else:
                        export_by = period_start  # No export needed; arm at period start.

                    mode, reason = decide_operational_mode(
                        period=period,
                        status=decision_status,
                        soc=soc,
                        headroom_kwh=headroom_kwh,
                        period_solar_kwh=period_solar_kwh,
                        schedule_period=get_schedule_period_for_time(period_start),
                        headroom_target_kwh=HEADROOM_TARGET_KWH,
                        battery_kwh=BATTERY_KWH,
                        hours_until_cheap_rate=get_hours_until_cheap_rate(now),
                        estimated_home_load_kw=ESTIMATED_HOME_LOAD_KW,
                        bridge_battery_reserve_kwh=BRIDGE_BATTERY_RESERVE_KWH,
                        enable_pre_cheap_rate_battery_bridge=ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE,
                    )
                    if status_override_reason is not None:
                        reason = f"{status_override_reason} {reason}"

                    if now >= export_by:
                        outcome = "pre-period export triggered"
                        pre_check_complete = False
                        if mode == SIGEN_MODES["GRID_EXPORT"]:
                            duration_minutes = max(
                                1,
                                math.ceil((period_start - now).total_seconds() / 60),
                            )
                            log_check(
                                period,
                                "PRE-PERIOD",
                                now_utc=now,
                                period_start_utc=period_start,
                                solar_value=solar_value,
                                status=decision_status,
                                period_solar_kwh=period_solar_kwh,
                                soc=soc,
                                headroom_kwh=headroom_kwh,
                                headroom_target_kwh=headroom_target_kwh,
                                headroom_deficit_kwh=headroom_deficit,
                                export_by_utc=export_by,
                                solar_avg_kw_3=solar_avg_kw_3,
                                effective_battery_export_kw=effective_battery_export_kw,
                                lead_time_hours_adjusted=lead_time_hours_adjusted,
                                mode=mode,
                                reason=reason,
                                outcome=outcome,
                            )
                            override_started = await start_timed_grid_export(
                                period=period,
                                reason=reason,
                                duration_minutes=duration_minutes,
                                now_utc=now,
                            )
                            if not override_started:
                                logger.warning(
                                    "[%s] Timed export activation did not start; leaving pre-period "
                                    "check eligible for retry on next tick.",
                                    period,
                                )
                                continue
                            pre_check_complete = True
                        else:
                            log_check(
                                period,
                                "PRE-PERIOD",
                                now_utc=now,
                                period_start_utc=period_start,
                                solar_value=solar_value,
                                status=decision_status,
                                period_solar_kwh=period_solar_kwh,
                                soc=soc,
                                headroom_kwh=headroom_kwh,
                                headroom_target_kwh=headroom_target_kwh,
                                headroom_deficit_kwh=headroom_deficit,
                                export_by_utc=export_by,
                                solar_avg_kw_3=solar_avg_kw_3,
                                effective_battery_export_kw=effective_battery_export_kw,
                                lead_time_hours_adjusted=lead_time_hours_adjusted,
                                mode=mode,
                                reason=reason,
                                outcome="pre-period check concluded no export needed",
                            )

                            # Keep checking until headroom deficit clears or a GRID_EXPORT
                            # override starts so rising live solar can still trigger pre-export.
                            if headroom_deficit <= 0:
                                pre_check_complete = True
                            else:
                                logger.info(
                                    "[%s] Retrying pre-period check next tick: headroom deficit "
                                    "%.2f kWh remains and mode=%s.",
                                    period,
                                    headroom_deficit,
                                    mode_names.get(mode, mode),
                                )

                        if pre_check_complete:
                            s["pre_set"] = True
                    else:
                        log_check(
                            period,
                            "PRE-PERIOD",
                            now_utc=now,
                            period_start_utc=period_start,
                            solar_value=solar_value,
                            status=decision_status,
                            period_solar_kwh=period_solar_kwh,
                            soc=soc,
                            headroom_kwh=headroom_kwh,
                            headroom_target_kwh=headroom_target_kwh,
                            headroom_deficit_kwh=headroom_deficit,
                            export_by_utc=export_by,
                            solar_avg_kw_3=solar_avg_kw_3,
                            effective_battery_export_kw=effective_battery_export_kw,
                            lead_time_hours_adjusted=lead_time_hours_adjusted,
                            mode=mode,
                            reason=reason,
                            outcome="waiting until export window opens",
                        )

            # --- Period start: set the definitive mode ---
            if not s["start_set"] and now >= period_start:
                soc = await fetch_soc(period)
                if soc is not None:
                    solar_avg_kw_3 = get_live_solar_average_kw()
                    decision_status, status_override_reason = promote_status_for_live_clipping_risk(
                        period,
                        status,
                        soc,
                        solar_avg_kw_3,
                    )
                    headroom_kwh = calc_headroom_kwh(BATTERY_KWH, soc)
                    headroom_target_kwh = HEADROOM_TARGET_KWH
                    headroom_deficit = max(0.0, headroom_target_kwh - headroom_kwh)
                    mode, reason = decide_operational_mode(
                        period=period,
                        status=decision_status,
                        soc=soc,
                        headroom_kwh=headroom_kwh,
                        period_solar_kwh=period_solar_kwh,
                        schedule_period=get_schedule_period_for_time(period_start),
                        headroom_target_kwh=HEADROOM_TARGET_KWH,
                        battery_kwh=BATTERY_KWH,
                        hours_until_cheap_rate=get_hours_until_cheap_rate(now),
                        estimated_home_load_kw=ESTIMATED_HOME_LOAD_KW,
                        bridge_battery_reserve_kwh=BRIDGE_BATTERY_RESERVE_KWH,
                        enable_pre_cheap_rate_battery_bridge=ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE,
                    )
                    if status_override_reason is not None:
                        reason = f"{status_override_reason} {reason}"
                    
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
                        status=decision_status,
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
                    ok = await _apply_mode_change_tracked(
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
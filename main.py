
"""Main scheduler loop for coordinating Sigen inverter mode decisions.

The scheduler continuously monitors solar forecasts, battery state, and tariff windows,
making operational mode decisions that optimize between self-powered generation,
grid arbitrage, and cost-minimization based on real-time conditions.
"""

import asyncio
from collections import deque
from html import escape
import importlib.util
import json
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
    ENABLE_SUMMER_PRE_SUNRISE_DISCHARGE,
    PRE_SUNRISE_DISCHARGE_MONTHS,
    PRE_SUNRISE_DISCHARGE_LEAD_MINUTES,
    PRE_SUNRISE_DISCHARGE_MIN_SOC_PERCENT,
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
    LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT,
    LIVE_CLIPPING_RISK_SOLAR_TRIGGER_KW,
    LIVE_CLIPPING_EXPORT_SOC_FLOOR_PERCENT,
    DAYTIME_TIMED_EXPORT_MIN_SOC_PERCENT,
    MAX_TIMED_EXPORT_MINUTES,
    ENABLE_EVENING_CONTROLLED_EXPORT,
    EVENING_EXPORT_MIN_SOC_PERCENT,
    EVENING_EXPORT_TRIGGER_SOC_PERCENT,
    EVENING_EXPORT_MIN_EXCESS_KWH,
    EVENING_EXPORT_ASSUMED_DISCHARGE_KW,
    EVENING_EXPORT_MAX_DURATION_MINUTES,
    ENABLE_PRE_CHEAP_RATE_NIGHT_EXPORT,
    PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT,
    PRE_CHEAP_RATE_NIGHT_EXPORT_ASSUMED_DISCHARGE_KW,
    CHEAP_RATE_START_HOUR,
    MORNING_HIGH_SOC_PROTECTION_ENABLED,
    MORNING_HIGH_SOC_THRESHOLD_PERCENT,
    MID_PERIOD_SAFETY_SOLAR_TRIGGER_KW,
)
from logic.decision_logic import (
    decide_operational_mode,
    calc_headroom_kwh,
    is_live_clipping_period_enabled,
)
from logic.schedule_utils import (
    _parse_utc,
    derive_period_windows,
    get_first_period_info,
    get_hours_until_cheap_rate,
    is_cheap_rate_window,
    is_pre_sunrise_discharge_window,
    get_schedule_period_for_time,
    suppress_elapsed_periods_except_latest,
    LOCAL_TZ,
)
from logic.mode_control import (
    extract_mode_value,
    mode_matches_target,
    ACTION_DIVIDER,
)
from weather.sunrise_sunset import get_sunrise_sunset
from config.constants import (
    LATITUDE,
    LONGITUDE,
    MODE_CHANGE_EVENTS_ARCHIVE_PATH,
    TIMED_EXPORT_STATE_PATH,
)
from telemetry.forecast_calibration import build_and_save_forecast_calibration, get_period_calibration
from telemetry.telemetry_archive import (
    append_inverter_telemetry_snapshot,
    append_mode_change_event,
    extract_live_solar_power_kw,
    extract_today_solar_generation_kwh,
)


class LevelColorFormatter(logging.Formatter):
    """Apply ANSI colors to warning/error levels for terminal readability."""

    _RESET = "\033[0m"
    _GREEN = "\033[32m"
    _ORANGE = "\033[38;5;214m"
    _RED = "\033[31m"

    def __init__(self, fmt: str) -> None:
        """Initialize formatter with optional color support.

        Args:
            fmt: Base logging format string.
        """
        super().__init__(fmt=fmt)
        force_color = os.getenv("FORCE_COLOR", "").strip().lower() in {"1", "true", "yes", "on"}
        is_tty = bool(getattr(os.sys.stderr, "isatty", lambda: False)())
        self._use_color = (is_tty or force_color) and not os.getenv("NO_COLOR")

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record, colorizing WARNING and ERROR/CRITICAL levels.

        Args:
            record: Standard logging record.

        Returns:
            Formatted log line.
        """
        if not self._use_color:
            return super().format(record)

        rendered = super().format(record)
        if record.levelno == logging.INFO and "[MODE STATUS]" in rendered:
            return f"{self._GREEN}{rendered}{self._RESET}"
        if record.levelno == logging.WARNING:
            return f"{self._ORANGE}{rendered}{self._RESET}"
        if record.levelno >= logging.ERROR:
            return f"{self._RED}{rendered}{self._RESET}"
        return rendered


# --- Logging configuration ---
LOG_LEVEL = getattr(logging, CONFIG_LOG_LEVEL, logging.INFO)
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(
    LevelColorFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
logging.basicConfig(level=LOG_LEVEL, handlers=[_log_handler], force=True)
logger = logging.getLogger("sigen_control")

_EMAIL_SENDER_ADDRESS = os.getenv("EMAIL_SENDER", "").strip()
_EMAIL_RECEIVER_ADDRESS = os.getenv("EMAIL_RECEIVER", "").strip()
_EMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()
_EMAIL_SENDER_INSTANCE: Any | None = None
_EMAIL_CONFIG_LOGGED = False
_EMAIL_MODE_LABELS = {
    "TOU": "Time of Use",
    "SELF_POWERED": "Self Consumption",
    "GRID_EXPORT": "Feed to Grid",
    "AI": "Sigen AI Mode",
    "SIGEN_AI_MODE": "Sigen AI Mode",
    "NORTH_BOUND": "North Bound",
    "REMOTE_EMS_MODE": "Remote EMS Mode",
    "CUSTOM_OPERATION_MODE": "Custom Operation Mode",
    "MAXIMUM_SELF_CONSUMPTION": "Self Consumption",
}


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


def _format_email_mode_label(mode_label: Any) -> str:
    """Return a user-friendly mode label for email notifications."""
    label = str(mode_label or "Unknown").strip()
    if not label:
        return "Unknown"
    normalized = label.upper().replace(" ", "_").replace("-", "_")
    return _EMAIL_MODE_LABELS.get(normalized, label.replace("_", " ").title())


def _format_email_local_timestamp(event_time_utc: datetime) -> str:
    """Return local timestamp formatted as dd-mm-yyyy HH:MM:SS."""
    return event_time_utc.astimezone(LOCAL_TZ).strftime("%d-%m-%Y %H:%M:%S")


def _format_email_period_label(period: str) -> str:
    """Return a more human-readable scheduler context label for email content."""
    label = (period or "").strip()
    if not label:
        return "Scheduler update"

    period_name_map = {
        "Morn": "Morning",
        "Aftn": "Afternoon",
        "Eve": "Evening",
    }
    for short_name, full_name in period_name_map.items():
        label = label.replace(short_name, full_name)

    if "->" in label:
        from_period, to_period = [part.strip() for part in label.split("->", 1)]
        if from_period and to_period:
            return f"Transitioning from {from_period} to {to_period}"

    return label


def _format_email_payload(value: Any) -> str:
    """Return a readable string form for email payload fields."""
    if value is None:
        return "None"
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, indent=2, default=str)
        except TypeError:
            return repr(value)
    return str(value)


def _load_recent_transitions(since_local: datetime) -> list[dict[str, Any]]:
    """Load mode-change events from the archive since a given local datetime.

    Args:
        since_local: Only return events at or after this local datetime.

    Returns:
        List of event dicts in chronological order.
    """
    path = Path(MODE_CHANGE_EVENTS_ARCHIVE_PATH)
    if not path.exists():
        return []
    results: list[dict[str, Any]] = []
    try:
        with path.open(encoding="utf-8") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    event = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                captured_at_str = event.get("captured_at")
                if not captured_at_str:
                    continue
                try:
                    captured_at = datetime.fromisoformat(captured_at_str).astimezone(LOCAL_TZ)
                except ValueError:
                    continue
                if captured_at >= since_local and not event.get("simulated", False):
                    event["_captured_local"] = captured_at
                    results.append(event)
    except OSError:
        pass
    return results


def _build_today_forecast_email_sections(
    today_period_forecast: dict[str, tuple[int, str]] | None,
) -> tuple[str, str]:
    """Build plain-text and HTML sections for today's forecast summary.

    Args:
        today_period_forecast: Mapping of period name to (watts, status).

    Returns:
        Tuple of (plain_text_section, html_section).
    """
    if not today_period_forecast:
        plain_text = "Today's Solar Forecast\n----------------------\n- Unavailable\n"
        html = (
            '<div style="margin-top:12px;padding:10px 12px;background:#f8fafc;'
            'border:1px solid #e4ebf3;border-radius:10px;">'
            '<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;'
            'color:#5b6b82;margin-bottom:6px;">Today\'s Solar Forecast</div>'
            '<div style="font-size:12px;color:#5b6b82;">Unavailable</div>'
            '</div>'
        )
        return plain_text, html

    rows_text: list[str] = []
    rows_html = ""
    for period in order_daytime_periods(today_period_forecast):
        watts, status = today_period_forecast[period]
        period_label = _format_email_period_label(period)
        status_text = str(status).capitalize()
        rows_text.append(f"- {period_label}: {status_text} ({int(watts)}W)")
        rows_html += (
            f'<tr>'
            f'<td style="padding:4px 8px 4px 0;font-size:12px;color:#5b6b82;white-space:nowrap;">'
            f'{escape(period_label)}</td>'
            f'<td style="padding:4px 8px;font-size:12px;font-weight:600;color:#172033;">'
            f'{escape(status_text)}</td>'
            f'<td style="padding:4px 0;font-size:12px;color:#172033;">{int(watts)}W</td>'
            f'</tr>'
        )

    plain_text = (
        "Today's Solar Forecast\n"
        "----------------------\n"
        + "\n".join(rows_text)
        + "\n"
    )
    html = (
        '<div style="margin-top:12px;padding:10px 12px;background:#f8fafc;'
        'border:1px solid #e4ebf3;border-radius:10px;">'
        '<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;'
        'color:#5b6b82;margin-bottom:6px;">Today\'s Solar Forecast</div>'
        f'<table role="presentation" style="width:100%;border-collapse:collapse;">{rows_html}</table>'
        '</div>'
    )
    return plain_text, html


def _empty_timed_export_override() -> dict[str, Any]:
    """Return the default inactive timed export override structure."""
    return {
        "active": False,
        "started_at": None,
        "restore_at": None,
        "restore_mode": None,
        "restore_mode_label": None,
        "trigger_period": None,
        "duration_minutes": None,
        "is_clipping_export": False,
        "clipping_soc_floor": None,
        "export_soc_floor": None,
    }


def _persist_timed_export_override(state: dict[str, Any]) -> None:
    """Persist active timed export override state to disk or clear it.

    Args:
        state: Timed export override state dict.
    """
    state_path = Path(TIMED_EXPORT_STATE_PATH)
    if not state.get("active"):
        try:
            state_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("[TIMED EXPORT] Failed to remove persisted state: %s", exc)
        return

    payload = dict(state)
    for field_name in ("started_at", "restore_at"):
        value = payload.get(field_name)
        if isinstance(value, datetime):
            payload[field_name] = value.isoformat()

    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    except OSError as exc:
        logger.warning("[TIMED EXPORT] Failed to persist override state: %s", exc)


def _load_timed_export_override() -> dict[str, Any]:
    """Load persisted timed export override state from disk when available.

    Returns:
        Restored timed export state, or an inactive default state when unavailable.
    """
    state_path = Path(TIMED_EXPORT_STATE_PATH)
    if not state_path.exists():
        return _empty_timed_export_override()

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[TIMED EXPORT] Failed to load persisted override state: %s", exc)
        return _empty_timed_export_override()

    restored = _empty_timed_export_override()
    restored.update(payload)
    for field_name in ("started_at", "restore_at"):
        value = restored.get(field_name)
        if isinstance(value, str):
            try:
                restored[field_name] = datetime.fromisoformat(value)
            except ValueError:
                logger.warning(
                    "[TIMED EXPORT] Invalid datetime %s in persisted override state.",
                    field_name,
                )
                return _empty_timed_export_override()

    if restored.get("active"):
        logger.warning(
            "[TIMED EXPORT] Restored active override from disk: trigger_period=%s restore_at=%s",
            restored.get("trigger_period"),
            restored.get("restore_at"),
        )
    return restored


def log_mode_status(context: str, current_mode_raw: Any, mode_names: dict[int, str]) -> None:
    """Log pulled inverter mode state in a standardized format.

    Args:
        context: Human-readable context for where mode status was pulled.
        current_mode_raw: Raw mode payload returned by inverter API.
        mode_names: Mapping of numeric mode value to mode label.
    """
    current_mode = extract_mode_value(current_mode_raw)
    if current_mode is not None:
        logger.info(
            "[MODE STATUS] %s -> %s (value=%s), raw=%s",
            context,
            mode_names.get(current_mode, current_mode),
            current_mode,
            current_mode_raw,
        )
        return
    logger.info("[MODE STATUS] %s -> unparsed raw=%s", context, current_mode_raw)

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
    """Send a startup email with current mode, SOC, and recent transition summary.

    Args:
        current_mode_raw: Current mode payload returned at startup.
        battery_soc: Battery state-of-charge percentage, when available.
        solar_generated_today_kwh: Current day's cumulative solar generation in kWh.
        today_period_forecast: Daytime period forecast snapshot for today.
        mode_names: Mapping from mode value to human-readable mode label.
        event_time_utc: Startup timestamp in UTC.
    """
    sender = _get_email_sender_instance()
    if sender is None:
        logger.info("[EMAIL] Skipping startup notification (sender unavailable).")
        return

    current_mode_value = extract_mode_value(current_mode_raw)
    if current_mode_value is not None:
        current_mode_label = _format_email_mode_label(mode_names.get(current_mode_value, current_mode_value))
    else:
        current_mode_label = "Unknown"

    local_time = _format_email_local_timestamp(event_time_utc)
    soc_text = f"{battery_soc:.1f}%" if battery_soc is not None else "Unknown"
    today_solar_text = (
        f"{solar_generated_today_kwh:.2f} kWh"
        if solar_generated_today_kwh is not None
        else "Unknown"
    )
    subject = (
        f"Solar Update • Startup • {current_mode_label} • "
        f"SOC {soc_text} • Solar Today {today_solar_text}"
    )
    forecast_text, forecast_html = _build_today_forecast_email_sections(today_period_forecast)

    now_local = event_time_utc.astimezone(LOCAL_TZ)
    previous_2230 = (now_local - timedelta(days=1)).replace(hour=22, minute=30, second=0, microsecond=0)
    recent_events = _load_recent_transitions(previous_2230)
    recent_events = recent_events[-12:]

    if recent_events:
        timeline_lines = []
        timeline_rows = ""
        for ev in recent_events:
            ev_local: datetime = ev["_captured_local"]
            ev_time_str = ev_local.strftime("%H:%M")
            ev_mode = _format_email_mode_label(ev.get("requested_mode_label", ""))
            ev_period = _format_email_period_label(ev.get("period", ""))
            ev_ok = "OK" if ev.get("success", True) else "FAIL"
            timeline_lines.append(f"- {ev_time_str} | {ev_mode} | {ev_period} | {ev_ok}")
            dot_color = "#1f6f43" if ev.get("success", True) else "#b42318"
            timeline_rows += (
                f'<tr>'
                f'<td style="padding:4px 8px 4px 0;font-size:12px;color:#5b6b82;white-space:nowrap;">{escape(ev_time_str)}</td>'
                f'<td style="padding:4px 8px;font-size:12px;font-weight:600;color:#172033;">{escape(ev_mode)}</td>'
                f'<td style="padding:4px 0;font-size:11px;color:#5b6b82;">{escape(ev_period)}</td>'
                f'<td style="padding:4px 0 4px 8px;font-size:11px;color:{dot_color};font-weight:700;">{ev_ok}</td>'
                f'</tr>'
            )
        timeline_text = "\n".join(timeline_lines)
        timeline_html = (
            f'<div style="margin-top:12px;padding:10px 12px;background:#f8fafc;border:1px solid #e4ebf3;border-radius:10px;">'
            f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;color:#5b6b82;margin-bottom:6px;">Transitions since 10:30 PM</div>'
            f'<table role="presentation" style="width:100%;border-collapse:collapse;">{timeline_rows}</table>'
            f'</div>'
        )
    else:
        timeline_text = "- No mode transitions recorded since 10:30 PM"
        timeline_html = (
            '<div style="margin-top:12px;padding:10px 12px;background:#f8fafc;border:1px solid #e4ebf3;border-radius:10px;font-size:12px;color:#5b6b82;">'
            'No mode transitions recorded since 10:30 PM.'
            '</div>'
        )

    body = (
        "Sigen Inverter Startup\n"
        "=====================\n\n"
        f"Local Time: {local_time}\n"
        f"Current Mode: {current_mode_label}\n"
        f"Battery SOC: {soc_text}\n\n"
        f"Solar Produced Today: {today_solar_text}\n\n"
        f"{forecast_text}\n"
        "Transitions Since 10:30 PM\n"
        "---------------------------\n"
        f"{timeline_text}\n"
    )

    html_body = f"""<!DOCTYPE html>
<html lang="en">
    <body style="margin:0;padding:12px;background:#f4f7fb;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#172033;">
        <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #d8e1ec;border-radius:14px;overflow:hidden;box-shadow:0 4px 16px rgba(23,32,51,0.08);">
            <div style="padding:14px 18px;background:linear-gradient(135deg,#143a52 0%,#1e5f74 100%);color:#ffffff;">
                <div style="font-size:11px;letter-spacing:0.10em;text-transform:uppercase;opacity:0.75;">Solar Update</div>
                <div style="margin-top:3px;font-size:18px;font-weight:700;line-height:1.2;">System startup</div>
                <div style="margin-top:6px;">
                    <span style="font-size:12px;opacity:0.85;">{escape(local_time)}</span>
                    <span style="margin-left:10px;padding:2px 9px;border-radius:999px;background:#e8f7ee;color:#1f6f43;font-size:11px;font-weight:700;">ONLINE</span>
                </div>
            </div>
            <div style="padding:14px 18px;">
                <table role="presentation" style="width:100%;border-collapse:collapse;margin-bottom:12px;">
                    <tr>
                        <td style="width:50%;padding:0 6px 0 0;vertical-align:top;">
                            <div style="padding:10px 12px;background:#f8fafc;border:1px solid #e4ebf3;border-radius:10px;">
                                <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;color:#5b6b82;margin-bottom:3px;">Current Mode</div>
                                <div style="font-size:14px;font-weight:700;color:#172033;">{escape(current_mode_label)}</div>
                            </div>
                        </td>
                        <td style="width:50%;padding:0 0 0 6px;vertical-align:top;">
                            <div style="padding:10px 12px;background:#f8fafc;border:1px solid #e4ebf3;border-radius:10px;">
                                <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;color:#5b6b82;margin-bottom:3px;">Battery SOC</div>
                                <div style="font-size:14px;font-weight:700;color:#172033;">{escape(soc_text)}</div>
                            </div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:12px 6px 0 0;vertical-align:top;" colspan="2">
                            <div style="padding:10px 12px;background:#f8fafc;border:1px solid #e4ebf3;border-radius:10px;">
                                <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;color:#5b6b82;margin-bottom:3px;">Solar Produced Today</div>
                                <div style="font-size:14px;font-weight:700;color:#172033;">{escape(today_solar_text)}</div>
                            </div>
                        </td>
                    </tr>
                </table>
                {forecast_html}
                {timeline_html}
            </div>
        </div>
    </body>
</html>"""

    try:
        logger.info("[EMAIL] Sending startup notification.")
        await asyncio.to_thread(sender.send, _EMAIL_RECEIVER_ADDRESS, subject, body, html_body)
        logger.info("[EMAIL] Sent startup notification.")
    except Exception as exc:
        logger.error("[EMAIL] Failed to send startup notification: %s", exc)


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
        battery_soc: Battery state of charge at the time of command, when known.
        solar_generated_today_kwh: Current day's cumulative solar generation in kWh.
        today_period_forecast: Daytime period forecast snapshot for today.
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
        previous_mode_label = _format_email_mode_label(
            mode_names.get(current_mode_value, current_mode_value)
        )
        previous_mode_value = str(current_mode_value)
    else:
        previous_mode_label = "Unknown"
        previous_mode_value = str(current_mode_raw)

    status = "SUCCESS" if success else "FAILED"
    local_time = _format_email_local_timestamp(event_time_utc)
    requested_mode_friendly = _format_email_mode_label(requested_mode_label)
    friendly_period = _format_email_period_label(period)
    soc_text = f"{battery_soc:.1f}%" if battery_soc is not None else "Unknown"
    today_solar_text = (
        f"{solar_generated_today_kwh:.2f} kWh"
        if solar_generated_today_kwh is not None
        else "Unknown"
    )
    soc_subject = f" • SOC {soc_text}" if battery_soc is not None else ""
    solar_subject = (
        f" • Solar Today {today_solar_text}"
        if solar_generated_today_kwh is not None
        else ""
    )
    subject = (
        f"Solar Update • {status.title()} • "
        f"{previous_mode_label} → {requested_mode_friendly} • {friendly_period}{soc_subject}{solar_subject}"
    )
    forecast_text, forecast_html = _build_today_forecast_email_sections(today_period_forecast)
    response_text = _format_email_payload(response)
    error_text = error if error else "None"
    body = (
        "Sigen Inverter Mode Change\n"
        "==========================\n\n"
        f"Status: {status}\n"
        f"Local Time: {local_time}\n"
        f"Context / Period: {friendly_period}\n"
        f"Battery State of Charge (SOC): {soc_text}\n\n"
        f"Solar Produced Today: {today_solar_text}\n\n"
        f"{forecast_text}\n"
        "Mode Transition\n"
        "---------------\n"
        f"Previous Mode: {previous_mode_label} (raw={previous_mode_value})\n"
        f"Requested Mode: {requested_mode_friendly} (value={requested_mode})\n\n"
        "Decision Reason\n"
        "---------------\n"
        f"{reason}\n\n"
        "Command Details\n"
        "---------------\n"
        f"Error: {error_text}\n"
        f"Response: {response_text}\n"
    )
    status_bg = "#e8f7ee" if success else "#fdecea"
    status_fg = "#1f6f43" if success else "#b42318"

    # Build the transitions-since-last-night summary.
    now_local = event_time_utc.astimezone(LOCAL_TZ)
    previous_2230 = (now_local - timedelta(days=1)).replace(hour=22, minute=30, second=0, microsecond=0)
    recent_events = _load_recent_transitions(previous_2230)
    if recent_events:
        timeline_rows = ""
        for ev in recent_events:
            ev_local: datetime = ev["_captured_local"]
            ev_time_str = ev_local.strftime("%H:%M")
            ev_mode = _format_email_mode_label(ev.get("requested_mode_label", ""))
            ev_period = _format_email_period_label(ev.get("period", ""))
            ev_ok = ev.get("success", True)
            ev_dot_color = "#1f6f43" if ev_ok else "#b42318"
            ev_sim = ev.get("simulated", False)
            sim_tag = ' <span style="opacity:0.55;font-size:10px;">(sim)</span>' if ev_sim else ""
            timeline_rows += (
                f'<tr>'
                f'<td style="padding:4px 8px 4px 0;font-size:12px;color:#5b6b82;white-space:nowrap;">'
                f'{escape(ev_time_str)}</td>'
                f'<td style="padding:4px 8px;font-size:12px;font-weight:600;color:#172033;">{escape(ev_mode)}{sim_tag}</td>'
                f'<td style="padding:4px 0;font-size:11px;color:#5b6b82;">{escape(ev_period)}</td>'
                f'<td style="padding:4px 0 4px 8px;font-size:14px;color:{ev_dot_color};">&#9679;</td>'
                f'</tr>'
            )
        timeline_section = (
            f'<div style="margin-top:12px;padding:10px 12px;background:#f8fafc;border:1px solid #e4ebf3;border-radius:10px;">'
            f'<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;color:#5b6b82;margin-bottom:6px;">'
            f'Transitions since 10:30 PM</div>'
            f'<table role="presentation" style="width:100%;border-collapse:collapse;">{timeline_rows}</table>'
            f'</div>'
        )
    else:
        timeline_section = ""

    html_body = f"""<!DOCTYPE html>
<html lang="en">
    <body style="margin:0;padding:12px;background:#f4f7fb;font-family:Segoe UI,Helvetica,Arial,sans-serif;color:#172033;">
        <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #d8e1ec;border-radius:14px;overflow:hidden;box-shadow:0 4px 16px rgba(23,32,51,0.08);">
            <div style="padding:14px 18px;background:linear-gradient(135deg,#143a52 0%,#1e5f74 100%);color:#ffffff;">
                <div style="font-size:11px;letter-spacing:0.10em;text-transform:uppercase;opacity:0.75;">Solar Update</div>
                <div style="margin-top:3px;font-size:18px;font-weight:700;line-height:1.2;">{escape(previous_mode_label)} &#8594; {escape(requested_mode_friendly)}</div>
                <div style="margin-top:6px;">
                    <span style="font-size:12px;opacity:0.85;">{escape(friendly_period)}</span>
                    <span style="margin-left:10px;padding:2px 9px;border-radius:999px;background:{status_bg};color:{status_fg};font-size:11px;font-weight:700;">{escape(status)}</span>
                </div>
            </div>
            <div style="padding:14px 18px;">
                <table role="presentation" style="width:100%;border-collapse:collapse;margin-bottom:12px;">
                    <tr>
                        <td style="width:50%;padding:0 6px 0 0;vertical-align:top;">
                            <div style="padding:10px 12px;background:#f8fafc;border:1px solid #e4ebf3;border-radius:10px;">
                                <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;color:#5b6b82;margin-bottom:3px;">Time</div>
                                <div style="font-size:14px;font-weight:700;color:#172033;">{escape(local_time)}</div>
                            </div>
                        </td>
                        <td style="width:50%;padding:0 0 0 6px;vertical-align:top;">
                            <div style="padding:10px 12px;background:#f8fafc;border:1px solid #e4ebf3;border-radius:10px;">
                                <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;color:#5b6b82;margin-bottom:3px;">Battery SOC</div>
                                <div style="font-size:14px;font-weight:700;color:#172033;">{escape(soc_text)}</div>
                            </div>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding:12px 6px 0 0;vertical-align:top;" colspan="2">
                            <div style="padding:10px 12px;background:#f8fafc;border:1px solid #e4ebf3;border-radius:10px;">
                                <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;color:#5b6b82;margin-bottom:3px;">Solar Produced Today</div>
                                <div style="font-size:14px;font-weight:700;color:#172033;">{escape(today_solar_text)}</div>
                            </div>
                        </td>
                    </tr>
                </table>
                {forecast_html}
                <div style="margin-bottom:12px;padding:10px 12px;background:#f8fafc;border:1px solid #e4ebf3;border-radius:10px;font-size:13px;line-height:1.5;color:#26354d;">
                    <div style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;color:#5b6b82;margin-bottom:4px;">Reason</div>
                    {escape(reason)}
                </div>
                <details style="margin-bottom:0;">
                    <summary style="cursor:pointer;font-size:12px;color:#5b6b82;padding:6px 0;">Command details</summary>
                    <div style="margin-top:8px;padding:10px 12px;background:#0f172a;border-radius:10px;color:#e5eef8;font-size:12px;">
                        <div style="margin-bottom:6px;"><strong style="color:#ffffff;">Error:</strong> {escape(error_text)}</div>
                        <div><strong style="color:#ffffff;">Response:</strong></div>
                        <pre style="margin:6px 0 0 0;white-space:pre-wrap;word-break:break-word;font-size:11px;line-height:1.4;color:#d6e3f3;">{escape(response_text)}</pre>
                    </div>
                </details>
                {timeline_section}
            </div>
        </div>
    </body>
</html>"""

    try:
        logger.info(
            "[EMAIL] Sending mode-change notification: status=%s period=%s target=%s(%s).",
            status,
            period,
            requested_mode_label,
            requested_mode,
        )
        await asyncio.to_thread(sender.send, _EMAIL_RECEIVER_ADDRESS, subject, body, html_body)
        logger.info("[EMAIL] Sent mode-change notification for %s.", period)
    except Exception as exc:
        logger.error("[EMAIL] Failed to send mode-change notification: %s", exc)

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
    mode_label = mode_names.get(mode, mode)
    current_mode_raw: Any = None
    solar_generated_today_kwh: float | None = None
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
            if _should_archive_mode_change_events():
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
                battery_soc=battery_soc,
                solar_generated_today_kwh=solar_generated_today_kwh,
                today_period_forecast=today_period_forecast,
                response=simulated_response,
            )
            return True

        logger.error(f"Cannot set mode for {period}: Sigen interaction is unavailable.")
        return False

    try:
        current_mode_raw = await sigen.get_operational_mode()
        log_mode_status(f"pre-change pull ({period})", current_mode_raw, mode_names)
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

    try:
        energy_flow_for_email = await sigen.get_energy_flow()
        if isinstance(energy_flow_for_email, dict):
            if battery_soc is None:
                soc_value = energy_flow_for_email.get("batterySoc")
                if isinstance(soc_value, (int, float)):
                    battery_soc = float(soc_value)
            solar_generated_today_kwh = extract_today_solar_generation_kwh(energy_flow_for_email)
    except Exception as e:
        logger.debug(
            "Could not read energy flow before mode-change email for %s: %s",
            period,
            e,
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
        if _should_archive_mode_change_events():
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
            battery_soc=battery_soc,
            solar_generated_today_kwh=solar_generated_today_kwh,
            today_period_forecast=today_period_forecast,
            response=response,
        )
        return True
    except Exception as e:
        logger.error(f"Failed to set mode for {period}: {e}")
        if _should_archive_mode_change_events():
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
            battery_soc=battery_soc,
            solar_generated_today_kwh=solar_generated_today_kwh,
            today_period_forecast=today_period_forecast,
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
        "mode_set_key": None,
        "sleep_snapshot_for_date": None,
    }
    sleep_override_seconds: int | None = None
    refresh_auth_on_wake = False
    auth_refreshed_for_date = None
    last_forecast_refresh_utc: datetime | None = None
    last_forecast_solar_archive_utc: datetime | None = None
    forecast_solar_archive_cooldown_until_utc: datetime | None = None
    timed_export_override: dict[str, Any] = _load_timed_export_override()
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
        _persist_timed_export_override(new_state)

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
        """Switch to GRID_EXPORT for a bounded duration, then restore prior mode later.

        Args:
            period: Human-readable period label that triggered export.
            reason: Decision explanation for audit logs.
            duration_minutes: Requested export duration in minutes.
            now_utc: Current scheduler timestamp in UTC.
            battery_soc: Battery SOC at trigger time when available.
            is_clipping_export: If True, apply SOC floor check during export window.
            export_soc_floor: Optional SOC floor that triggers early restore.

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
                log_mode_status(
                    f"pre-timed-export pull ({period})",
                    current_mode_raw,
                    mode_names,
                )
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
            battery_soc=battery_soc,
        )
        if not ok:
            return False

        _update_timed_export_override({
            "active": True,
            "started_at": now_utc,
            "restore_at": restore_at,
            "restore_mode": restore_mode,
            "restore_mode_label": restore_label,
            "trigger_period": period,
            "duration_minutes": clamped_minutes,
            "is_clipping_export": is_clipping_export,
            "clipping_soc_floor": LIVE_CLIPPING_EXPORT_SOC_FLOOR_PERCENT if is_clipping_export else None,
            "export_soc_floor": export_soc_floor,
        })
        return True

    async def maybe_restore_timed_grid_export(now_utc: datetime) -> str:
        """Restore pre-export mode when active timed export window has elapsed.

        Args:
            now_utc: Current scheduler timestamp in UTC.

        Returns:
            One of:
            - "active": timed export remains active and normal scheduler decisions should be skipped.
            - "restored": timed export was restored this tick and normal scheduler decisions
              should be skipped until the next tick to avoid immediate re-entry.
            - "inactive": no timed export override is active.
        """
        nonlocal timed_export_override
        if not timed_export_override["active"]:
            return "inactive"

        restore_at = timed_export_override["restore_at"]
        is_clipping = timed_export_override.get("is_clipping_export", False)
        clipping_soc_floor = timed_export_override.get("clipping_soc_floor")
        export_soc_floor = timed_export_override.get("export_soc_floor")
        
        if export_soc_floor is not None:
            current_soc = await fetch_soc("timed-export-soc-check")
            if current_soc is not None and current_soc <= export_soc_floor:
                restore_mode = timed_export_override["restore_mode"]
                restore_label = timed_export_override["restore_mode_label"]
                trigger_period = timed_export_override["trigger_period"]
                if restore_mode is not None:
                    logger.info(ACTION_DIVIDER)
                    logger.info(
                        "[TIMED EXPORT] Export SOC floor reached (%.1f%% <= %.1f%%). Restoring %s early.",
                        current_soc,
                        export_soc_floor,
                        restore_label,
                    )
                    logger.info(ACTION_DIVIDER)
                    restore_ok = await _apply_mode_change_tracked(
                        sigen=sigen,
                        mode=restore_mode,
                        period=f"{trigger_period} (timed-export-soc-floor)",
                        reason=(
                            f"Timed export SOC floor reached at {current_soc:.1f}%. "
                            f"Restoring {restore_label}."
                        ),
                        mode_names=mode_names,
                        battery_soc=current_soc,
                    )
                    if restore_ok:
                        _update_timed_export_override(_empty_timed_export_override())
                        return "restored"

        # Check if clipping export has hit SOC floor early
        if is_clipping and clipping_soc_floor is not None:
            current_soc = await fetch_soc("clipping-soc-check")
            if current_soc is not None and current_soc <= clipping_soc_floor:
                restore_mode = timed_export_override["restore_mode"]
                restore_label = timed_export_override["restore_mode_label"]
                trigger_period = timed_export_override["trigger_period"]
                if restore_mode is not None:
                    logger.info(ACTION_DIVIDER)
                    logger.info(
                        "[TIMED EXPORT] Clipping export SOC floor reached (%.1f%% <= %.1f%%). "
                        "Restoring %s early.",
                        current_soc,
                        clipping_soc_floor,
                        restore_label,
                    )
                    logger.info(ACTION_DIVIDER)
                    restore_ok = await _apply_mode_change_tracked(
                        sigen=sigen,
                        mode=restore_mode,
                        period=f"{trigger_period} (clipping-export-soc-floor)",
                        reason=(
                            f"Clipping export SOC floor reached at {current_soc:.1f}%. "
                            f"Restoring {restore_label}."
                        ),
                        mode_names=mode_names,
                        battery_soc=current_soc,
                    )
                    if restore_ok:
                        _update_timed_export_override(_empty_timed_export_override())
                        return "restored"
        
        if restore_at is None:
            logger.warning("[TIMED EXPORT] Override state missing restore_at; clearing state.")
            _update_timed_export_override(_empty_timed_export_override())
            return "inactive"

        if now_utc < restore_at:
            return "active"

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
            _update_timed_export_override(_empty_timed_export_override())
            return "inactive"

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

        restore_soc = await fetch_soc("timed-export-restore")

        restore_ok = await _apply_mode_change_tracked(
            sigen=sigen,
            mode=restore_mode,
            period=f"{trigger_period} (timed-export-restore)",
            reason=(
                "Timed grid export window complete; restoring mode active before override "
                f"({restore_label})."
            ),
            mode_names=mode_names,
            battery_soc=restore_soc,
        )
        if restore_ok:
            _update_timed_export_override(_empty_timed_export_override())
            return "restored"

        logger.warning("[TIMED EXPORT] Restore attempt failed; will retry next scheduler tick.")
        return "active"

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
                logger.error(
                    "\033[91m[%s] Raw energy_flow response for diagnosis: %s\033[0m",
                    period,
                    raw,
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

    def plan_evening_controlled_export(
        *,
        period: str,
        soc: float | None,
        now_utc: datetime,
    ) -> tuple[int | None, str | None]:
        """Return bounded evening export duration and rationale when conditions allow.

        The planner is intentionally conservative: it only considers evening export,
        enforces an SOC floor, protects expected home usage until cheap rate starts,
        and requires minimum excess energy before enabling timed export.

        Args:
            period: Current scheduler period name.
            soc: Current battery state-of-charge percentage.
            now_utc: Current scheduler timestamp in UTC.

        Returns:
            Tuple of (duration_minutes, reason). Returns (None, None) when export
            should not be started.
        """
        if not ENABLE_EVENING_CONTROLLED_EXPORT:
            return None, None
        if (period or "").upper() != "EVE":
            return None, None
        if soc is None:
            return None, None
        if soc < EVENING_EXPORT_TRIGGER_SOC_PERCENT:
            return None, None

        # Never begin controlled evening export during peak tariff hours.
        if get_schedule_period_for_time(now_utc) == "PEAK":
            return None, None

        hours_until_cheap_rate = get_hours_until_cheap_rate(now_utc)
        if hours_until_cheap_rate <= 0:
            return None, None

        battery_energy_kwh = BATTERY_KWH * (soc / 100.0)
        soc_floor_kwh = BATTERY_KWH * (EVENING_EXPORT_MIN_SOC_PERCENT / 100.0)
        expected_load_until_cheap_kwh = hours_until_cheap_rate * ESTIMATED_HOME_LOAD_KW
        protected_kwh = max(
            soc_floor_kwh,
            expected_load_until_cheap_kwh + BRIDGE_BATTERY_RESERVE_KWH,
        )
        exportable_excess_kwh = max(0.0, battery_energy_kwh - protected_kwh)
        if exportable_excess_kwh < EVENING_EXPORT_MIN_EXCESS_KWH:
            return None, None

        required_minutes = math.ceil(
            (exportable_excess_kwh / EVENING_EXPORT_ASSUMED_DISCHARGE_KW) * 60
        )
        duration_minutes = max(
            1,
            min(required_minutes, EVENING_EXPORT_MAX_DURATION_MINUTES),
        )
        reason = (
            "Controlled evening export: surplus battery energy available above protected "
            f"reserve. SOC={soc:.1f}%, exportable_excess={exportable_excess_kwh:.2f} kWh, "
            f"hours_until_cheap_rate={hours_until_cheap_rate:.2f}, "
            f"duration={duration_minutes} minutes."
        )
        return duration_minutes, reason

    def plan_pre_cheap_rate_night_export(
        *,
        soc: float | None,
        now_utc: datetime,
    ) -> tuple[int | None, str | None]:
        """Plan sunset-to-cheap-rate export duration bounded by SOC floor and time.

        Args:
            soc: Current battery SOC percentage.
            now_utc: Current scheduler timestamp in UTC.

        Returns:
            Tuple of (duration_minutes, reason) when export should begin, else (None, None).
        """
        if not ENABLE_PRE_CHEAP_RATE_NIGHT_EXPORT:
            return None, None
        if soc is None:
            return None, None
        if soc <= PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT:
            return None, None

        hours_until_cheap_rate = get_hours_until_cheap_rate(now_utc)
        if hours_until_cheap_rate <= 0:
            return None, None

        energy_above_floor_kwh = BATTERY_KWH * (
            (soc - PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT) / 100.0
        )
        if energy_above_floor_kwh <= 0:
            return None, None

        minutes_to_soc_floor = math.ceil(
            (energy_above_floor_kwh / PRE_CHEAP_RATE_NIGHT_EXPORT_ASSUMED_DISCHARGE_KW) * 60
        )
        minutes_to_cheap_rate = max(1, int(hours_until_cheap_rate * 60))
        duration_minutes = max(
            1,
            min(minutes_to_soc_floor, minutes_to_cheap_rate, MAX_TIMED_EXPORT_MINUTES),
        )

        reason = (
            "Pre-cheap-rate export strategy: discharge battery for arbitrage until "
            f"SOC floor {PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT:.1f}% or cheap-rate "
            f"window opens. SOC={soc:.1f}%, duration={duration_minutes} minutes."
        )
        return duration_minutes, reason

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

        if not is_live_clipping_period_enabled(period):
            return status, None
        if status_key != "AMBER":
            return status, None
        if soc is None or soc < LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT:
            return status, None
        if avg_live_solar_kw is None:
            return status, None

        trigger_kw = LIVE_CLIPPING_RISK_SOLAR_TRIGGER_KW
        if avg_live_solar_kw < trigger_kw:
            return status, None

        reason = (
            "\033[93mLive clipping-risk override: promoting AMBER to GREEN because "
            f"SOC={soc:.1f}% and avg live solar={avg_live_solar_kw:.2f} kW is near "
            f"or above configured trigger ({trigger_kw:.1f} kW).\033[0m"
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
        if NIGHT_MODE_ENABLED and night_context is not None:
            night_period_name = f"Night->{night_context['target_period']}"
            night_period_solar_kwh = estimate_solar(
                night_context["target_period"],
                night_context["solar_value"],
            )
            night_headroom_target_kwh = HEADROOM_TARGET_KWH
            night_mode = PERIOD_TO_MODE["NIGHT"]
            night_mode_reason = "Night window active. Applying configured night mode."
            soc: float | None = None

            if (
                night_context["window_name"] == "PRE-DAWN"
                and is_pre_sunrise_discharge_window(
                    now,
                    night_context["target_start"],
                    enabled=ENABLE_SUMMER_PRE_SUNRISE_DISCHARGE,
                    months_csv=PRE_SUNRISE_DISCHARGE_MONTHS,
                    lead_minutes=PRE_SUNRISE_DISCHARGE_LEAD_MINUTES,
                )
            ):
                soc = await fetch_soc(night_period_name)
                if soc is not None and soc >= PRE_SUNRISE_DISCHARGE_MIN_SOC_PERCENT:
                    night_mode = SIGEN_MODES["SELF_POWERED"]
                    night_mode_reason = (
                        "Summer pre-sunrise discharge window active. Switching to "
                        "self-powered mode to create battery headroom before morning solar."
                    )
                else:
                    night_mode_reason = (
                        "Summer pre-sunrise discharge window active, but SOC is below "
                        f"minimum threshold {PRE_SUNRISE_DISCHARGE_MIN_SOC_PERCENT:.1f}%. "
                        "Keeping configured night mode instead of discharging."
                    )

            if night_context["window_name"] == "EVENING-NIGHT":
                hours_until_cheap_rate = get_hours_until_cheap_rate(now)
                if hours_until_cheap_rate > 0:
                    soc = await fetch_soc(night_period_name)
                    export_minutes, export_reason = plan_pre_cheap_rate_night_export(
                        soc=soc,
                        now_utc=now,
                    )
                    if export_minutes is not None and export_reason is not None:
                        started = await start_timed_grid_export(
                            period=night_period_name,
                            reason=export_reason,
                            duration_minutes=export_minutes,
                            now_utc=now,
                            battery_soc=soc,
                            export_soc_floor=PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT,
                        )
                        if started:
                            continue
                    night_mode = SIGEN_MODES["SELF_POWERED"]
                    if soc is not None and soc <= PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT:
                        night_mode_reason = (
                            "Pre-cheap-rate export floor reached. Switching to self-powered "
                            f"at SOC floor {PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT:.1f}%."
                        )
                    else:
                        night_mode_reason = (
                            "Pre-cheap-rate window active. Holding self-powered mode until "
                            "cheap-rate window opens."
                        )

            mode_set_key = (night_context["target_date"], night_mode)
            if night_state["mode_set_key"] != mode_set_key:
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
                        battery_soc=soc,
                    )
                    if ok:
                        night_state["mode_set_key"] = mode_set_key
                except Exception as e:
                    logger.error(f"[{night_period_name}] Unexpected error applying base night mode: {e}")

            if NIGHT_SLEEP_MODE_ENABLED:
                pre_window_opens_at = night_context["target_start"] - MAX_PRE_PERIOD_WINDOW
                wake_at = pre_window_opens_at

                if night_context["window_name"] == "EVENING-NIGHT":
                    cheap_rate_start_local = now.astimezone(LOCAL_TZ).replace(
                        hour=CHEAP_RATE_START_HOUR,
                        minute=0,
                        second=0,
                        microsecond=0,
                    )
                    if now.astimezone(LOCAL_TZ) >= cheap_rate_start_local:
                        cheap_rate_start_local = cheap_rate_start_local + timedelta(days=1)
                    cheap_rate_start_utc = cheap_rate_start_local.astimezone(timezone.utc)
                    wake_at = min(wake_at, cheap_rate_start_utc)

                if (
                    night_context["window_name"] == "PRE-DAWN"
                    and ENABLE_SUMMER_PRE_SUNRISE_DISCHARGE
                ):
                    pre_sunrise_wake_at = night_context["target_start"] - timedelta(
                        minutes=PRE_SUNRISE_DISCHARGE_LEAD_MINUTES
                    )
                    wake_at = min(wake_at, pre_sunrise_wake_at)

                if now < wake_at:
                    sleep_seconds = max(1, int((wake_at - now).total_seconds()))
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
                            wake_at.isoformat(),
                            "next critical night milestone",
                        )

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

            # --- Mid-period live clipping export check ---
            # When the period is already active but the forecast was Amber, check whether
            # live conditions (high SOC + strong solar) warrant an immediate export to
            # create headroom now rather than waiting for the next pre-period window.
            if (
                s["start_set"]
                and not s["clipping_export_set"]
                and not timed_export_override["active"]
                and now >= period_start
                and (period_end_utc is None or now < period_end_utc)
                and is_live_clipping_period_enabled(period)
            ):
                soc = await fetch_soc(period)
                solar_avg_kw_3 = get_live_solar_average_kw()
                decision_status, status_override_reason = promote_status_for_live_clipping_risk(
                    period, status, soc, solar_avg_kw_3
                )
                if decision_status != status and soc is not None:
                    headroom_kwh = calc_headroom_kwh(BATTERY_KWH, soc)
                    headroom_target_kwh = HEADROOM_TARGET_KWH
                    headroom_deficit = max(0.0, headroom_target_kwh - headroom_kwh)
                    mode, reason = decide_operational_mode(
                        period=period,
                        status=decision_status,
                        soc=soc,
                        headroom_kwh=headroom_kwh,
                        period_solar_kwh=period_solar_kwh,
                        schedule_period=get_schedule_period_for_time(now),
                        headroom_target_kwh=HEADROOM_TARGET_KWH,
                        battery_kwh=BATTERY_KWH,
                        hours_until_cheap_rate=get_hours_until_cheap_rate(now),
                        estimated_home_load_kw=ESTIMATED_HOME_LOAD_KW,
                        bridge_battery_reserve_kwh=BRIDGE_BATTERY_RESERVE_KWH,
                        enable_pre_cheap_rate_battery_bridge=ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE,
                    )
                    if status_override_reason is not None:
                        reason = f"{status_override_reason} {reason}"
                    if mode == SIGEN_MODES["GRID_EXPORT"] and headroom_deficit > 0:
                        # Estimate export duration: time to clear headroom deficit at effective rate,
                        # bounded by time remaining until period end or MAX_TIMED_EXPORT_MINUTES.
                        effective_battery_export_kw = get_effective_battery_export_kw(solar_avg_kw_3)
                        duration_minutes = math.ceil(
                            (headroom_deficit / effective_battery_export_kw) * 60
                        )
                        log_check(
                            period,
                            "MID-PERIOD-CLIPPING",
                            now_utc=now,
                            period_start_utc=period_start,
                            solar_value=solar_value,
                            status=decision_status,
                            period_solar_kwh=period_solar_kwh,
                            soc=soc,
                            headroom_kwh=headroom_kwh,
                            headroom_target_kwh=headroom_target_kwh,
                            headroom_deficit_kwh=headroom_deficit,
                            export_by_utc=now,
                            solar_avg_kw_3=solar_avg_kw_3,
                            effective_battery_export_kw=effective_battery_export_kw,
                            mode=mode,
                            reason=reason,
                            outcome="mid-period clipping export triggered",
                        )
                        override_started = await start_timed_grid_export(
                            period=period,
                            reason=reason,
                            duration_minutes=duration_minutes,
                            now_utc=now,
                            battery_soc=soc,
                            is_clipping_export=True,
                        )
                        if override_started:
                            s["clipping_export_set"] = True
                            continue
                    s["clipping_export_set"] = True

            # --- Mid-period high-SOC safety export check ---
            # Fires every tick when the period is active and no timed export is running.
            # Catches the case where forecast is already Green (so the clipping path above
            # never promotes status and clipping_export_set is consumed without action),
            # but the battery is charging toward 100% with strong solar.
            # Independent of clipping_export_set so it re-evaluates each tick.
            if (
                s["start_set"]
                and not timed_export_override["active"]
                and now >= period_start
                and (period_end_utc is None or now < period_end_utc)
                and MORNING_HIGH_SOC_PROTECTION_ENABLED
            ):
                solar_avg_kw_3_safety = get_live_solar_average_kw()
                soc_safety = await fetch_soc(period)
                if (
                    soc_safety is not None
                    and soc_safety >= MORNING_HIGH_SOC_THRESHOLD_PERCENT
                    and solar_avg_kw_3_safety >= MID_PERIOD_SAFETY_SOLAR_TRIGGER_KW
                ):
                    headroom_kwh_safety = calc_headroom_kwh(BATTERY_KWH, soc_safety)
                    headroom_target_kwh_safety = HEADROOM_TARGET_KWH
                    headroom_deficit_safety = max(0.0, headroom_target_kwh_safety - headroom_kwh_safety)
                    if headroom_deficit_safety > 0:
                        effective_battery_export_kw_safety = get_effective_battery_export_kw(solar_avg_kw_3_safety)
                        duration_minutes_safety = math.ceil(
                            (headroom_deficit_safety / effective_battery_export_kw_safety) * 60
                        )
                        reason_safety = (
                            f"High-SOC safety export: SOC {soc_safety:.1f}% >= "
                            f"{MORNING_HIGH_SOC_THRESHOLD_PERCENT:.0f}% threshold, "
                            f"solar {solar_avg_kw_3_safety:.1f} kW >= {MID_PERIOD_SAFETY_SOLAR_TRIGGER_KW:.1f} kW trigger, "
                            f"headroom {headroom_kwh_safety:.2f} kWh < target {headroom_target_kwh_safety:.2f} kWh"
                        )
                        log_check(
                            period,
                            "MID-PERIOD-HIGH-SOC-SAFETY",
                            now_utc=now,
                            period_start_utc=period_start,
                            solar_value=solar_value,
                            status=status,
                            period_solar_kwh=period_solar_kwh,
                            soc=soc_safety,
                            headroom_kwh=headroom_kwh_safety,
                            headroom_target_kwh=headroom_target_kwh_safety,
                            headroom_deficit_kwh=headroom_deficit_safety,
                            export_by_utc=now,
                            solar_avg_kw_3=solar_avg_kw_3_safety,
                            effective_battery_export_kw=effective_battery_export_kw_safety,
                            mode=SIGEN_MODES["GRID_EXPORT"],
                            reason=reason_safety,
                            outcome="mid-period high-SOC safety export triggered",
                        )
                        override_started_safety = await start_timed_grid_export(
                            period=period,
                            reason=reason_safety,
                            duration_minutes=duration_minutes_safety,
                            now_utc=now,
                            battery_soc=soc_safety,
                            is_clipping_export=True,
                        )
                        if override_started_safety:
                            continue

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
                                battery_soc=soc,
                                export_soc_floor=DAYTIME_TIMED_EXPORT_MIN_SOC_PERCENT,
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
                    
                    evening_export_minutes, evening_export_reason = plan_evening_controlled_export(
                        period=period,
                        soc=soc,
                        now_utc=now,
                    )
                    if evening_export_minutes is not None and evening_export_reason is not None:
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
                            mode=SIGEN_MODES["GRID_EXPORT"],
                            reason=evening_export_reason,
                            outcome="controlled evening timed export started",
                        )
                        override_started = await start_timed_grid_export(
                            period=period,
                            reason=evening_export_reason,
                            duration_minutes=evening_export_minutes,
                            now_utc=now,
                            battery_soc=soc,
                            export_soc_floor=EVENING_EXPORT_MIN_SOC_PERCENT,
                        )
                        if override_started:
                            s["start_set"] = True
                            s["pre_set"] = True
                            continue
                        logger.warning(
                            "[%s] Controlled evening export was eligible but failed to start. "
                            "Falling back to standard period-start mode.",
                            period,
                        )

                    if mode == SIGEN_MODES["GRID_EXPORT"]:
                        effective_battery_export_kw = get_effective_battery_export_kw(solar_avg_kw_3)
                        duration_minutes = max(
                            1,
                            math.ceil((headroom_deficit / effective_battery_export_kw) * 60),
                        )
                        is_clipping_export = (
                            (status or "").upper() == "AMBER"
                            and (decision_status or "").upper() == "GREEN"
                        )
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
                            solar_avg_kw_3=solar_avg_kw_3,
                            effective_battery_export_kw=effective_battery_export_kw,
                            mode=mode,
                            reason=reason,
                            outcome="period-start timed export started",
                        )
                        override_started = await start_timed_grid_export(
                            period=period,
                            reason=reason,
                            duration_minutes=duration_minutes,
                            now_utc=now,
                            battery_soc=soc,
                            is_clipping_export=is_clipping_export,
                            export_soc_floor=DAYTIME_TIMED_EXPORT_MIN_SOC_PERCENT,
                        )
                        if override_started:
                            s["start_set"] = True
                            s["pre_set"] = True
                            continue

                        logger.warning(
                            "[%s] Period-start GRID_EXPORT decision could not start timed export. "
                            "Skipping direct mode set to avoid unbounded export and retrying next tick.",
                            period,
                        )
                        continue
                    
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
                    if is_cheap_rate_window(now):
                        logger.info(
                            "[%s] Deferring period-start mode override — cheap-rate window active. "
                            "Will retry on next tick after cheap-rate window ends.",
                            period,
                        )
                    else:
                        ok = await _apply_mode_change_tracked(
                            sigen=sigen,
                            mode=mode,
                            period=f"{period} (period-start)",
                            reason=reason,
                            mode_names=mode_names,
                            battery_soc=soc,
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
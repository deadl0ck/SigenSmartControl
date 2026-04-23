"""Email configuration and formatting helpers for scheduler notifications.

This module owns email environment/config resolution and all notification
formatting helpers so scheduler code can remain focused on control flow.
"""

from datetime import datetime
import json
import logging
import os
from pathlib import Path
import importlib.util
from typing import Any, Tuple

from html import escape

from logic.schedule_utils import LOCAL_TZ


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


def get_email_receiver_address() -> str:
    """Return configured receiver email address.

    Returns:
        The EMAIL_RECEIVER environment value, or an empty string when unset.
    """
    return _EMAIL_RECEIVER_ADDRESS


def _is_truthy_env(name: str) -> bool:
    """Return whether an environment variable has a truthy value."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}

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

def _build_today_forecast_email_sections(
    today_period_forecast: dict[str, tuple[int, str]] | None,
) -> Tuple[str, str]:
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
    for period in ("Morn", "Aftn", "Eve"):
        if period not in today_period_forecast:
            continue
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

    for period, (watts, status) in today_period_forecast.items():
        if period in {"Morn", "Aftn", "Eve"}:
            continue
        if period.upper() == "NIGHT":
            continue
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

def _load_email_sender_class() -> type | None:
    """Load EmailSender class from email/email_sender.py without shadowing stdlib email package.
    Returns:
        EmailSender class when available, otherwise None.
    """
    sender_path = Path(__file__).resolve().parent.parent / "email" / "email_sender.py"
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
        if not _EMAIL_CONFIG_LOGGED:
            logger.warning("[EMAIL] EmailSender class not found in email/email_sender.py.")
            _EMAIL_CONFIG_LOGGED = True
        return None
    try:
        _EMAIL_SENDER_INSTANCE = email_sender_cls(_EMAIL_SENDER_ADDRESS, _EMAIL_APP_PASSWORD)
        logger.info("[EMAIL] Mode-change email notifications enabled.")
        return _EMAIL_SENDER_INSTANCE
    except Exception as exc:
        logger.error("[EMAIL] Failed to initialize EmailSender: %s", exc)
        return None

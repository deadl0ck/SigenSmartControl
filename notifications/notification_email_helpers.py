"""Email notification helper flows used by the scheduler.

This module owns the higher-level startup and mode-change notification
composition/sending logic so the scheduler loop can focus on control flow.
"""

import asyncio
from datetime import datetime, timedelta
from html import escape
import json
import logging
from pathlib import Path
from typing import Any

from config.constants import MODE_CHANGE_EVENTS_ARCHIVE_PATH
from logic.mode_control import extract_mode_value
from logic.schedule_utils import LOCAL_TZ
from notifications.email_notifications import (
    _build_today_forecast_email_sections,
    _format_email_local_timestamp,
    _format_email_mode_label,
    _format_email_payload,
    _format_email_period_label,
    _get_email_sender_instance,
    get_email_receiver_address,
)


def load_recent_transitions(since_local: datetime) -> list[dict[str, Any]]:
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


async def notify_startup_email(
    *,
    current_mode_raw: Any,
    battery_soc: float | None,
    solar_generated_today_kwh: float | None,
    today_period_forecast: dict[str, tuple[int, str]] | None,
    mode_names: dict[int, str],
    event_time_utc: datetime,
    logger: logging.Logger,
) -> None:
    """Send a startup email with current mode, SOC, and recent transition summary.

    Args:
        current_mode_raw: Current mode payload returned at startup.
        battery_soc: Battery state-of-charge percentage, when available.
        solar_generated_today_kwh: Current day's cumulative solar generation in kWh.
        today_period_forecast: Daytime period forecast snapshot for today.
        mode_names: Mapping from mode value to human-readable mode label.
        event_time_utc: Startup timestamp in UTC.
        logger: Logger instance used for status/error output.
    """
    sender = _get_email_sender_instance()
    if sender is None:
        logger.info("[EMAIL] Skipping startup notification (sender unavailable).")
        return

    current_mode_value = extract_mode_value(current_mode_raw)
    if current_mode_value is not None:
        current_mode_label = _format_email_mode_label(
            mode_names.get(current_mode_value, current_mode_value)
        )
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
    previous_2230 = (now_local - timedelta(days=1)).replace(
        hour=22,
        minute=30,
        second=0,
        microsecond=0,
    )
    recent_events = load_recent_transitions(previous_2230)
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
        await asyncio.to_thread(
            sender.send,
            get_email_receiver_address(),
            subject,
            body,
            html_body,
        )
        logger.info("[EMAIL] Sent startup notification.")
    except Exception as exc:
        logger.error("[EMAIL] Failed to send startup notification: %s", exc)


def _build_zappi_email_sections(
    zappi_status: dict[str, Any] | None,
    zappi_daily: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Build plain-text and HTML sections for Zappi EV charger status.

    Args:
        zappi_status: Normalized Zappi live-status dict, or None when unavailable.
        zappi_daily: Today's Zappi daily charge totals, or None when unavailable.

    Returns:
        Tuple of (plain_text_section, html_section). Both empty strings when
        both zappi_status and zappi_daily are None.
    """
    if not zappi_status and not zappi_daily:
        return "", ""

    rows_text: list[str] = []
    rows_html = ""

    if zappi_status:
        status_text = zappi_status.get("status_text", "Unknown")
        charge_w = zappi_status.get("charge_power_w", 0)
        session_kwh = zappi_status.get("session_energy_kwh", 0.0)
        mode_text = zappi_status.get("mode_text", "Unknown")
        charge_kw = charge_w / 1000.0
        rows_text += [
            f"Status: {status_text} | Mode: {mode_text}",
            f"Charge Power: {charge_kw:.1f} kW",
            f"Session Energy: {session_kwh:.2f} kWh",
        ]
        rows_html += (
            f'<tr><td style="padding:4px 8px 4px 0;font-size:12px;color:#5b6b82;white-space:nowrap;">Status</td>'
            f'<td style="padding:4px 0;font-size:12px;font-weight:600;color:#172033;">'
            f'{escape(status_text)} &mdash; {escape(mode_text)}</td></tr>'
            f'<tr><td style="padding:4px 8px 4px 0;font-size:12px;color:#5b6b82;white-space:nowrap;">Charge Power</td>'
            f'<td style="padding:4px 0;font-size:12px;color:#172033;">{charge_kw:.1f} kW</td></tr>'
            f'<tr><td style="padding:4px 8px 4px 0;font-size:12px;color:#5b6b82;white-space:nowrap;">Session Energy</td>'
            f'<td style="padding:4px 0;font-size:12px;color:#172033;">{session_kwh:.2f} kWh</td></tr>'
        )

    if zappi_daily:
        total_kwh = zappi_daily.get("total_kwh", 0.0)
        diverted_kwh = zappi_daily.get("diverted_kwh", 0.0)
        boosted_kwh = zappi_daily.get("boosted_kwh", 0.0)
        rows_text += [
            f"Today's Total to EV: {total_kwh:.2f} kWh  "
            f"(Solar: {diverted_kwh:.2f} kWh | Grid: {boosted_kwh:.2f} kWh)",
        ]
        rows_html += (
            f'<tr><td style="padding:4px 8px 4px 0;font-size:12px;color:#5b6b82;white-space:nowrap;">Today — Total to EV</td>'
            f'<td style="padding:4px 0;font-size:12px;font-weight:600;color:#172033;">{total_kwh:.2f} kWh</td></tr>'
            f'<tr><td style="padding:4px 8px 4px 0;font-size:12px;color:#5b6b82;white-space:nowrap;">&nbsp;&nbsp;Solar diverted</td>'
            f'<td style="padding:4px 0;font-size:12px;color:#172033;">{diverted_kwh:.2f} kWh</td></tr>'
            f'<tr><td style="padding:4px 8px 4px 0;font-size:12px;color:#5b6b82;white-space:nowrap;">&nbsp;&nbsp;Grid boost</td>'
            f'<td style="padding:4px 0;font-size:12px;color:#172033;">{boosted_kwh:.2f} kWh</td></tr>'
        )

    plain_text = (
        "EV Charger (Zappi)\n"
        "------------------\n"
        + "\n".join(rows_text)
        + "\n"
    )
    html = (
        '<div style="margin-top:12px;padding:10px 12px;background:#f8fafc;'
        'border:1px solid #e4ebf3;border-radius:10px;">'
        '<div style="font-size:11px;text-transform:uppercase;letter-spacing:0.07em;'
        'color:#5b6b82;margin-bottom:6px;">EV Charger (Zappi)</div>'
        '<table role="presentation" style="width:100%;border-collapse:collapse;">'
        + rows_html
        + '</table>'
        '</div>'
    )
    return plain_text, html


async def notify_mode_change_email(
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
    zappi_status: dict[str, Any] | None = None,
    zappi_daily: dict[str, Any] | None = None,
    response: Any | None = None,
    error: str | None = None,
    logger: logging.Logger | None = None,
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
        zappi_status: Most recent Zappi live-status snapshot, or None when unavailable.
        zappi_daily: Today's Zappi daily charge totals, or None when unavailable.
        response: Optional API response payload on success.
        error: Optional error message on failure.
        logger: Logger instance used for status/error output.
    """
    if logger is None:
        logger = logging.getLogger(__name__)

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
    zappi_text, zappi_html = _build_zappi_email_sections(zappi_status, zappi_daily)
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
        + (f"{zappi_text}\n" if zappi_text else "")
        + "Mode Transition\n"
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

    now_local = event_time_utc.astimezone(LOCAL_TZ)
    previous_2230 = (now_local - timedelta(days=1)).replace(
        hour=22,
        minute=30,
        second=0,
        microsecond=0,
    )
    recent_events = load_recent_transitions(previous_2230)
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
                {zappi_html}
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
        await asyncio.to_thread(
            sender.send,
            get_email_receiver_address(),
            subject,
            body,
            html_body,
        )
        logger.info("[EMAIL] Sent mode-change notification for %s.", period)
    except Exception as exc:
        logger.error("[EMAIL] Failed to send mode-change notification: %s", exc)

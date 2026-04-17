"""Print today's mode changes and compare with live current mode.

Usage:
    python scripts/mode_sanity_check.py
"""

from __future__ import annotations

from datetime import datetime
import asyncio
import json
from pathlib import Path
import sys
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from config.constants import MODE_CHANGE_EVENTS_ARCHIVE_PATH
from config.settings import LOCAL_TIMEZONE, SIGEN_MODES
from integrations.sigen_interaction import SigenInteraction
from logic.mode_control import extract_mode_value


TEST_ONLY_REASONS = {
    "Simulation email notification test.",
}

GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


def _build_mode_name_map() -> dict[int, str]:
    """Return reverse map from mode value to label."""
    return {value: key for key, value in SIGEN_MODES.items()}


def _parse_event_datetime(value: str) -> datetime | None:
    """Parse event datetime string into a timezone-aware datetime."""
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            return dt.replace(tzinfo=ZoneInfo(LOCAL_TIMEZONE))
        return dt
    except Exception:
        return None


def _format_mode(event: dict[str, Any], mode_names: dict[int, str]) -> str:
    """Format requested mode label/value for display."""
    requested_label = event.get("requested_mode_label")
    requested_mode = event.get("requested_mode")
    if requested_label is not None and requested_mode is not None:
        return f"{requested_label} ({requested_mode})"
    if requested_label is not None:
        return str(requested_label)
    if requested_mode is not None:
        return str(requested_mode)
    return "N/A"


def _load_todays_events(
    local_tz: ZoneInfo,
    *,
    include_test_events: bool,
) -> tuple[list[dict[str, Any]], int]:
    """Load mode-change events for today in local timezone.

    Args:
        local_tz: Local timezone for date filtering.
        include_test_events: When False, excludes known test-generated reasons.

    Returns:
        Tuple of (events, excluded_test_event_count).
    """
    path = Path(MODE_CHANGE_EVENTS_ARCHIVE_PATH)
    if not path.exists():
        return []

    today = datetime.now(local_tz).date()
    events: list[dict[str, Any]] = []
    excluded_test_events = 0

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        captured_at = event.get("captured_at")
        if not isinstance(captured_at, str):
            continue
        parsed = _parse_event_datetime(captured_at)
        if parsed is None:
            continue
        if parsed.astimezone(local_tz).date() != today:
            continue

        if not include_test_events:
            reason = event.get("reason")
            if isinstance(reason, str) and reason in TEST_ONLY_REASONS:
                excluded_test_events += 1
                continue

        event["_parsed_captured_at"] = parsed
        events.append(event)

    events.sort(key=lambda item: item.get("_parsed_captured_at", datetime.min.replace(tzinfo=local_tz)))
    return events, excluded_test_events


def _print_todays_events(events: list[dict[str, Any]], mode_names: dict[int, str]) -> None:
    """Print today's events in chronological order."""
    print("\n=== Today's Mode Changes ===")
    if not events:
        print("No mode-change events recorded today.")
        return

    for event in events:
        dt = event["_parsed_captured_at"].astimezone(ZoneInfo(LOCAL_TIMEZONE))
        time_label = dt.strftime("%H:%M:%S")
        period = event.get("period", "N/A")
        mode_text = _format_mode(event, mode_names)
        success = event.get("success")
        simulated = event.get("simulated")
        print(
            f"- {time_label} | {period:<28} | target={mode_text:<24} "
            f"| success={success} simulated={simulated}"
        )


def _event_mode_value(event: dict[str, Any]) -> int | None:
    """Extract requested mode value from event payload."""
    requested_mode = event.get("requested_mode")
    if isinstance(requested_mode, int):
        return requested_mode
    if isinstance(requested_mode, str) and requested_mode.isdigit():
        return int(requested_mode)
    return None


async def _fetch_current_mode() -> tuple[int | None, Any]:
    """Fetch current operational mode and return normalized value plus raw payload."""
    sigen = await SigenInteraction.create()
    raw_mode = await sigen.get_operational_mode()
    mode_value = extract_mode_value(raw_mode)
    return mode_value, raw_mode


def _mode_display(mode_value: int | None, mode_names: dict[int, str]) -> str:
    """Return friendly mode display string."""
    if mode_value is None:
        return "N/A"
    return f"{mode_names.get(mode_value, str(mode_value))} ({mode_value})"


def _last_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return latest event if available."""
    if not events:
        return None
    return events[-1]


def _last_live_success_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return latest successful non-simulated event if available."""
    for event in reversed(events):
        if event.get("success") is True and event.get("simulated") is False:
            return event
    return None


def _print_match_check(
    *,
    title: str,
    event: dict[str, Any] | None,
    current_mode_value: int | None,
    mode_names: dict[int, str],
) -> None:
    """Print whether current mode matches a reference event's requested mode."""
    print(f"\n=== {title} ===")
    if event is None:
        print("No reference event available.")
        return

    event_mode = _event_mode_value(event)
    event_mode_text = _mode_display(event_mode, mode_names)
    current_mode_text = _mode_display(current_mode_value, mode_names)
    match = event_mode is not None and current_mode_value is not None and event_mode == current_mode_value
    match_text = f"{GREEN}True{RESET}" if match else f"{RED}False{RESET}"

    print(f"Reference event period: {event.get('period', 'N/A')}")
    print(f"Reference event mode:   {event_mode_text}")
    print(f"Current live mode:      {current_mode_text}")
    print(f"Match:                  {match_text}")


async def main() -> int:
    """Run mode sanity checks and print result."""
    import argparse

    parser = argparse.ArgumentParser(description="Sanity-check today mode-change events against live mode.")
    parser.add_argument(
        "--include-test-events",
        action="store_true",
        help="Include known test-generated events in today's event timeline.",
    )
    args = parser.parse_args()

    local_tz = ZoneInfo(LOCAL_TIMEZONE)
    mode_names = _build_mode_name_map()

    events, excluded_test_events = _load_todays_events(
        local_tz,
        include_test_events=args.include_test_events,
    )
    if excluded_test_events > 0 and not args.include_test_events:
        print(
            f"Filtered out {excluded_test_events} known test-generated event(s). "
            "Use --include-test-events to show all raw entries."
        )

    _print_todays_events(events, mode_names)

    print("\n=== Current Mode (Live) ===")
    try:
        current_mode_value, current_mode_raw = await _fetch_current_mode()
        print(f"Current mode: {_mode_display(current_mode_value, mode_names)}")
        print(f"Raw mode payload: {current_mode_raw}")
    except Exception as exc:
        print(f"Failed to fetch current mode: {exc}")
        return 1

    _print_match_check(
        title="Match Against Latest Event (Any)",
        event=_last_event(events),
        current_mode_value=current_mode_value,
        mode_names=mode_names,
    )
    _print_match_check(
        title="Match Against Latest Successful Live Event",
        event=_last_live_success_event(events),
        current_mode_value=current_mode_value,
        mode_names=mode_names,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

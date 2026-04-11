"""scripts/forecast_command_summary.py
----------------------------------------
Summarize forecast-driven inverter commands from the mode-change archive.

This script reads mode-change events and prints the commands that would have been
sent by forecast-driven scheduler logic, including:
    - local timestamp
    - period/context
    - requested mode label/value
    - success/failure
    - decision reason

By default, only daytime forecast-driven events are shown, where the period is
derived from each event timestamp (Morn/Aftn/Eve). Use ``--include-all-events``
to include all archived commands (e.g., night baseline and manual test events).

Run from the project root:
    python scripts/forecast_command_summary.py
    python scripts/forecast_command_summary.py --date 2026-04-10
    python scripts/forecast_command_summary.py --all
    python scripts/forecast_command_summary.py --all --include-all-events
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Allow running from project root or from scripts/ sub-directory.
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from config.constants import MODE_CHANGE_EVENTS_ARCHIVE_PATH
from config.settings import (
    FORECAST_ANALYSIS_AFTERNOON_END_HOUR,
    FORECAST_ANALYSIS_AFTERNOON_START_HOUR,
    FORECAST_ANALYSIS_EVENING_END_HOUR,
    FORECAST_ANALYSIS_EVENING_START_HOUR,
    FORECAST_ANALYSIS_MORNING_END_HOUR,
    FORECAST_ANALYSIS_MORNING_START_HOUR,
)

_FORECAST_PERIOD_PREFIXES = ("Morn", "Aftn", "Eve")


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments.

    Returns:
        Parsed argparse namespace.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Summarize forecast-driven inverter command events from "
            "mode_change_events.jsonl."
        )
    )
    parser.add_argument(
        "--date",
        dest="date_text",
        help="Local date to analyse in YYYY-MM-DD format. Defaults to latest date.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Print summaries for all available dates.",
    )
    parser.add_argument(
        "--include-all-events",
        action="store_true",
        help="Include non-forecast events (night baseline, manual test, etc.).",
    )
    return parser.parse_args()


def load_mode_events(path: Path) -> list[dict[str, Any]]:
    """Load mode-change events from a JSONL archive.

    Args:
        path: Archive path.

    Returns:
        List of parsed event dictionaries.
    """
    if not path.exists():
        return []

    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            events.append(payload)
    return events


def parse_event_time(event: dict[str, Any]) -> datetime | None:
    """Parse event timestamp.

    Args:
        event: Mode-change event record.

    Returns:
        Parsed datetime when available; otherwise None.
    """
    captured_at = event.get("captured_at")
    if not isinstance(captured_at, str) or not captured_at.strip():
        return None
    try:
        return datetime.fromisoformat(captured_at)
    except ValueError:
        return None


def derive_period_from_timestamp(event_time: datetime) -> str:
    """Derive Morn/Aftn/Eve bucket from event timestamp.

    Args:
        event_time: Parsed event timestamp.

    Returns:
        Derived period label: Morn, Aftn, Eve, or Outside.
    """
    hour = event_time.hour

    if FORECAST_ANALYSIS_MORNING_START_HOUR <= hour < FORECAST_ANALYSIS_MORNING_END_HOUR:
        return "Morn"
    if FORECAST_ANALYSIS_AFTERNOON_START_HOUR <= hour < FORECAST_ANALYSIS_AFTERNOON_END_HOUR:
        return "Aftn"
    if FORECAST_ANALYSIS_EVENING_START_HOUR <= hour < FORECAST_ANALYSIS_EVENING_END_HOUR:
        return "Eve"
    return "Outside"


def is_forecast_driven_event(event_time: datetime) -> bool:
    """Return whether a timestamp falls into forecast daytime periods.

    Args:
        event_time: Parsed event timestamp.

    Returns:
        True when the timestamp maps to Morn/Aftn/Eve.
    """
    return derive_period_from_timestamp(event_time) in _FORECAST_PERIOD_PREFIXES


def filter_events(
    events: list[dict[str, Any]],
    include_all_events: bool,
) -> list[dict[str, Any]]:
    """Filter events according to CLI options.

    Args:
        events: Raw mode-change events.
        include_all_events: Whether to include non-forecast records.

    Returns:
        Filtered events with parseable timestamps, sorted by timestamp.
    """
    kept: list[tuple[datetime, dict[str, Any]]] = []
    for event in events:
        event_time = parse_event_time(event)
        if event_time is None:
            continue
        if not include_all_events and not is_forecast_driven_event(event_time):
            continue
        kept.append((event_time, event))

    kept.sort(key=lambda row: row[0])
    return [event for _, event in kept]


def available_dates(events: list[dict[str, Any]]) -> list[date]:
    """List unique local dates present in the filtered events.

    Args:
        events: Filtered events.

    Returns:
        Sorted date list.
    """
    dates: set[date] = set()
    for event in events:
        event_time = parse_event_time(event)
        if event_time is not None:
            dates.add(event_time.date())
    return sorted(dates)


def select_target_dates(
    dates: list[date],
    date_text: str | None,
    include_all_dates: bool,
) -> list[date]:
    """Resolve target report dates from CLI arguments.

    Args:
        dates: Available dates.
        date_text: Optional ``YYYY-MM-DD`` date string.
        include_all_dates: Whether to include all dates.

    Returns:
        Selected date list.

    Raises:
        ValueError: When date input is invalid or unavailable.
    """
    if not dates:
        raise ValueError("No matching events found.")

    if include_all_dates:
        return dates

    if date_text:
        try:
            requested = datetime.strptime(date_text, "%Y-%m-%d").date()
        except ValueError as exc:
            raise ValueError("--date must be in YYYY-MM-DD format.") from exc
        if requested not in dates:
            raise ValueError(f"No matching events found for {requested.isoformat()}.")
        return [requested]

    return [dates[-1]]


def summarize_mode_counts(events: list[dict[str, Any]]) -> dict[str, int]:
    """Count commands by requested mode label/value.

    Args:
        events: Events for one date.

    Returns:
        Mode label/value counts in descending frequency order.
    """
    counts: Counter[str] = Counter()
    for event in events:
        label = event.get("requested_mode_label")
        value = event.get("requested_mode")
        if isinstance(label, str) and label.strip():
            key = f"{label} ({value})"
        else:
            key = f"mode={value}"
        counts[key] += 1

    return dict(counts.most_common())


def format_outcome(event: dict[str, Any]) -> str:
    """Format event outcome text.

    Args:
        event: Mode-change event record.

    Returns:
        Compact outcome string.
    """
    success = event.get("success")
    simulated = event.get("simulated")

    if success is True and simulated is True:
        return "OK(sim)"
    if success is True:
        return "OK"
    if success is False:
        return "FAILED"
    return "UNKNOWN"


def print_date_report(target_date: date, events: list[dict[str, Any]]) -> None:
    """Print one date's event timeline and summary.

    Args:
        target_date: Date to print.
        events: Events for this date.
    """
    print(f"Date: {target_date.isoformat()}")
    print(
        f"  {'Time':<8}  {'Derived':<7}  {'Logged Context':<24}  {'Command':<18}  {'Outcome':<8}  Reason"
    )
    print("  " + "-" * 108)

    for event in events:
        event_time = parse_event_time(event)
        if event_time is None:
            continue

        derived_period = derive_period_from_timestamp(event_time)
        period = str(event.get("period", "N/A"))
        label = event.get("requested_mode_label")
        value = event.get("requested_mode")
        command = f"{label} ({value})" if isinstance(label, str) else f"mode={value}"
        reason = str(event.get("reason", "")).strip() or "N/A"

        print(
            f"  {event_time.strftime('%H:%M:%S'):<8}  "
            f"{derived_period:<7}  {period:<24}  {command:<18}  {format_outcome(event):<8}  {reason}"
        )

    success_count = sum(1 for event in events if event.get("success") is True)
    failure_count = sum(1 for event in events if event.get("success") is False)
    counts = summarize_mode_counts(events)

    print("  " + "-" * 108)
    print(
        f"  Total commands: {len(events)} | successful: {success_count} | failed: {failure_count}"
    )
    if counts:
        counts_text = ", ".join(f"{mode} x{count}" for mode, count in counts.items())
        print(f"  By mode: {counts_text}")
    print()


def main() -> int:
    """Run script entry point.

    Returns:
        Process exit code.
    """
    args = parse_args()
    archive_path = _ROOT / MODE_CHANGE_EVENTS_ARCHIVE_PATH
    all_events = load_mode_events(archive_path)

    if not all_events:
        print(f"No mode-change archive found at {archive_path}.", file=sys.stderr)
        return 1

    filtered = filter_events(all_events, include_all_events=args.include_all_events)
    if not filtered:
        mode_text = "all events" if args.include_all_events else "forecast-driven events"
        print(f"No {mode_text} found in archive.", file=sys.stderr)
        return 1

    grouped: dict[date, list[dict[str, Any]]] = defaultdict(list)
    for event in filtered:
        event_time = parse_event_time(event)
        if event_time is not None:
            grouped[event_time.date()].append(event)

    dates = sorted(grouped.keys())
    try:
        targets = select_target_dates(dates, args.date_text, args.all)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    scope_text = "all commands" if args.include_all_events else "forecast-driven commands"
    print(f"Summary scope: {scope_text}")
    print()

    for target in targets:
        print_date_report(target, grouped[target])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

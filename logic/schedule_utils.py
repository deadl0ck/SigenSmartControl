"""schedule_utils.py
-----------------
Time-based schedule period detection and cheap-rate window calculations.

Provides helper functions for determining cheap-rate windows, schedule periods,
hours until the cheap-rate window opens, and dividing solar days into scheduling periods.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from config.settings import (
    CHEAP_RATE_START_HOUR,
    CHEAP_RATE_END_HOUR,
    EVENING_END_HOUR,
    EVENING_START_HOUR,
    MORNING_END_HOUR,
    MORNING_START_HOUR,
    PEAK_END_HOUR,
    PEAK_START_HOUR,
    LOCAL_TIMEZONE,
)

LOCAL_TZ = ZoneInfo(LOCAL_TIMEZONE)


def _parse_utc(iso_str: str) -> datetime:
    """Parse an ISO 8601 timestamp, ensuring it carries UTC tzinfo.

    Args:
        iso_str: ISO 8601 formatted timestamp string.

    Returns:
        Timezone-aware datetime with UTC tzinfo.
    """
    dt = datetime.fromisoformat(iso_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def derive_period_windows(
    sunrise_utc: datetime,
    sunset_utc: datetime,
    period_names: list[str],
) -> dict[str, datetime]:
    """Divide the solar day into equal windows and return each period's start time (UTC).

    With the default three periods (Morn/Aftn/Eve) the solar day is split into thirds
    starting at sunrise.

    Args:
        sunrise_utc: Sunrise time in UTC.
        sunset_utc: Sunset time in UTC.
        period_names: List of period names to create (e.g., ['Morn', 'Aftn', 'Eve']).

    Returns:
        Dict mapping period names to their UTC start times.
    """
    solar_day = sunset_utc - sunrise_utc
    n = len(period_names)
    return {
        name: sunrise_utc + solar_day * (i / n)
        for i, name in enumerate(period_names)
    }


def get_first_period_info(
    period_windows: dict[str, datetime],
    period_forecast: dict[str, tuple[int, str]],
) -> tuple[str, datetime, int, str] | None:
    """Retrieve the earliest period from a collection of periods.

    Args:
        period_windows: Mapping of period names to their start times (UTC).
        period_forecast: Mapping of period names to (solar_watts, status) tuples.

    Returns:
        Tuple of (period_name, start_time, solar_value, status) for the earliest period,
        or None if no periods are available.
    """
    available_periods = [
        (period, start, *period_forecast[period])
        for period, start in period_windows.items()
        if period in period_forecast
    ]
    if not available_periods:
        return None
    return min(available_periods, key=lambda item: item[1])


def is_cheap_rate_window(now_utc: datetime) -> bool:
    """Determine whether the current time falls within the cheap-rate window.

    Args:
        now_utc: Current time in UTC.

    Returns:
        True if now_utc is within the configured cheap-rate window (CHEAP_RATE_START_HOUR
        to CHEAP_RATE_END_HOUR in local timezone), False otherwise.
    """
    local_hour = now_utc.astimezone(LOCAL_TZ).hour
    if CHEAP_RATE_START_HOUR < CHEAP_RATE_END_HOUR:
        return CHEAP_RATE_START_HOUR <= local_hour < CHEAP_RATE_END_HOUR
    return local_hour >= CHEAP_RATE_START_HOUR or local_hour < CHEAP_RATE_END_HOUR


def get_hours_until_cheap_rate(now_utc: datetime) -> float:
    """Calculate hours until the next cheap-rate window opens in local timezone.

    Args:
        now_utc: Current time in UTC.

    Returns:
        Hours until the next cheap-rate window starts. Returns 0.0 if already within
        the cheap-rate window. Accounts for daily cycle wrap-around.
    """
    if is_cheap_rate_window(now_utc):
        return 0.0

    local_now = now_utc.astimezone(LOCAL_TZ)
    cheap_start_local = local_now.replace(
        hour=CHEAP_RATE_START_HOUR,
        minute=0,
        second=0,
        microsecond=0,
    )
    if local_now >= cheap_start_local:
        cheap_start_local += timedelta(days=1)

    return (cheap_start_local - local_now).total_seconds() / 3600.0


def get_schedule_period_for_time(when_utc: datetime) -> str:
    """Determine which schedule period (NIGHT, PEAK, or DAY) applies at a given time.

    Args:
        when_utc: Time to check, in UTC.

    Returns:
        String representing the schedule period: 'NIGHT' (cheap-rate window),
        'PEAK' (peak hours), or 'DAY' (standard daytime).
    """
    local_hour = when_utc.astimezone(LOCAL_TZ).hour

    if is_cheap_rate_window(when_utc):
        return "NIGHT"

    if PEAK_START_HOUR <= local_hour < PEAK_END_HOUR:
        return "PEAK"

    if MORNING_START_HOUR <= local_hour < MORNING_END_HOUR:
        return "DAY"

    if EVENING_START_HOUR <= local_hour < EVENING_END_HOUR:
        return "DAY"

    return "DAY"


def suppress_elapsed_periods_except_latest(
    now_utc: datetime,
    period_windows: dict[str, datetime],
    day_state: dict[str, dict[str, bool]],
) -> list[str]:
    """Mark all elapsed periods as 'done' except the latest, allowing recovery if missed.

    When multiple periods have started before the scheduler catches up, suppresses
    pre_set and start_set on all but the latest elapsed period so only the current
    period can trigger actions.

    Args:
        now_utc: Current time in UTC.
        period_windows: Mapping of period names to start times (UTC).
        day_state: Mutable dict tracking pre_set and start_set status per period.

    Returns:
        List of suppressed period names for logging.
    """
    elapsed_periods = [
        period
        for period, period_start in sorted(period_windows.items(), key=lambda item: item[1])
        if now_utc >= period_start
    ]
    if len(elapsed_periods) <= 1:
        return []

    suppressed_periods: list[str] = []
    for period in elapsed_periods[:-1]:
        state = day_state.get(period)
        if state is None:
            continue
        state["pre_set"] = True
        state["start_set"] = True
        suppressed_periods.append(period)
    return suppressed_periods


def parse_month_list(months_csv: str) -> set[int]:
    """Parse comma-separated month numbers and return valid month values.

    Args:
        months_csv: Comma-separated month numbers (1-12), e.g. ``"4,5,6,7,8,9"``.

    Returns:
        Set of valid month integers. Invalid tokens are ignored.
    """
    valid_months: set[int] = set()
    for token in (months_csv or "").split(","):
        value = token.strip()
        if not value:
            continue
        if not value.isdigit():
            continue
        month = int(value)
        if 1 <= month <= 12:
            valid_months.add(month)
    return valid_months


def is_pre_sunrise_discharge_window(
    now_utc: datetime,
    sunrise_utc: datetime,
    *,
    enabled: bool,
    months_csv: str,
    lead_minutes: int,
) -> bool:
    """Return whether pre-sunrise discharge should be active for current time.

    Args:
        now_utc: Current scheduler time in UTC.
        sunrise_utc: Target sunrise time in UTC.
        enabled: Feature flag controlling whether this behavior is active.
        months_csv: Comma-separated local months where discharge is allowed.
        lead_minutes: Minutes before sunrise to begin discharge.

    Returns:
        True when now is in the configured pre-sunrise lead window for an enabled
        month, otherwise False.
    """
    if not enabled or lead_minutes <= 0:
        return False

    active_months = parse_month_list(months_csv)
    if not active_months:
        return False

    local_now = now_utc.astimezone(LOCAL_TZ)
    if local_now.month not in active_months:
        return False

    seconds_until_sunrise = (sunrise_utc - now_utc).total_seconds()
    if seconds_until_sunrise <= 0:
        return False

    return seconds_until_sunrise <= lead_minutes * 60

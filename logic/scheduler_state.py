"""Encapsulates all mutable state for the scheduler loop.

The SchedulerState dataclass centralizes all stateful variables tracked across
ticks, providing a single source of truth for forecast data, period windows,
auth state, and mode-change tracking.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from config.settings import LIVE_SOLAR_AVERAGE_SAMPLE_COUNT


@dataclass
class SchedulerState:
    """Centralized mutable state for the scheduler loop.

    Attributes:
        today_period_windows: Mapping of period names to their start/end times for today.
        ordered_period_windows: today_period_windows sorted ascending by start time; updated
            whenever today_period_windows is refreshed.
        tomorrow_period_windows: Mapping of period names to their start/end times for tomorrow.
        today_period_forecast: Mapping of period names to (confidence, category) tuples for today.
        tomorrow_period_forecast: Mapping of period names to (confidence, category) tuples for tomorrow.
        today_sunrise_utc: Sunrise time for today in UTC, or None if not yet calculated.
        today_sunset_utc: Sunset time for today in UTC, or None if not yet calculated.
        tomorrow_sunrise_utc: Sunrise time for tomorrow in UTC, or None if not yet calculated.
        day_state: Tracks which actions (pre_set, start_set) have been taken for each period today.
        night_state: Tracks night mode state including mode_set_key and sleep_snapshot_for_date.
        current_date: Current local date (date part only), or None if not yet set.
        auth_refreshed_for_date: Last date on which Sigen auth was refreshed, or None.
        refresh_auth_on_wake: Flag indicating auth refresh should be performed on next wake.
        live_solar_kw_samples: Rolling deque of live solar generation samples in kW,
            bounded to LIVE_SOLAR_AVERAGE_SAMPLE_COUNT (3) entries.
        forecast_calibration: Learned multipliers per period, keyed by period name.
        last_forecast_refresh_utc: Timestamp of last successful forecast refresh, or None.
        last_forecast_solar_archive_utc: Timestamp of last successful forecast.solar archive, or None.
        forecast_solar_archive_cooldown_until_utc: Rate-limit cooldown expiry for forecast.solar API, or None.
        timed_export_override: Configuration for timed export override feature.
        tick_mode_change_attempts: Count of mode-change attempts in the current tick.
        tick_mode_change_successes: Count of successful mode-changes in the current tick.
        tick_mode_change_failures: Count of failed mode-changes in the current tick.
        sleep_override_seconds: Override sleep duration in seconds, or None for normal polling.
    """

    today_period_windows: dict[str, datetime] = field(default_factory=dict)
    ordered_period_windows: list = field(default_factory=list)
    tomorrow_period_windows: dict[str, datetime] = field(default_factory=dict)
    today_period_forecast: dict[str, tuple[int, str]] = field(default_factory=dict)
    tomorrow_period_forecast: dict[str, tuple[int, str]] = field(default_factory=dict)
    today_sunrise_utc: datetime | None = None
    today_sunset_utc: datetime | None = None
    tomorrow_sunrise_utc: datetime | None = None
    day_state: dict[str, dict[str, bool]] = field(default_factory=dict)
    night_state: dict[str, Any] = field(default_factory=lambda: {
        "mode_set_key": None,
        "sleep_snapshot_for_date": None,
    })
    current_date: datetime | None = None
    auth_refreshed_for_date: datetime | None = None
    refresh_auth_on_wake: bool = False
    live_solar_kw_samples: deque[float] = field(
        default_factory=lambda: deque(maxlen=LIVE_SOLAR_AVERAGE_SAMPLE_COUNT)
    )
    forecast_calibration: dict[str, Any] = field(default_factory=dict)
    last_forecast_refresh_utc: datetime | None = None
    last_forecast_solar_archive_utc: datetime | None = None
    forecast_solar_archive_cooldown_until_utc: datetime | None = None
    timed_export_override: dict[str, Any] = field(default_factory=dict)
    tick_mode_change_attempts: int = 0
    tick_mode_change_successes: int = 0
    tick_mode_change_failures: int = 0
    sleep_override_seconds: int | None = None

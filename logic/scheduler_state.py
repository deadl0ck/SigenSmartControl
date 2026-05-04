"""Encapsulates all mutable state for the scheduler loop.

The SchedulerState dataclass centralizes all stateful variables tracked across
ticks, providing a single source of truth for forecast data, period windows,
auth state, and mode-change tracking.
"""

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, TypedDict

from config.settings import LIVE_SOLAR_AVERAGE_SAMPLE_COUNT


class DayStateEntry(TypedDict):
    """Per-period action flags tracking which scheduler events have fired today.

    Attributes:
        pre_set: True once the pre-period headroom/export check has been applied.
        start_set: True once the period-start mode decision has been applied.
        clipping_export_set: True once a live clipping-risk export has been started.
        high_soc_export_set: True once a mid-period high-SOC safety export has fired.
            Prevents repeated exports within the same period after solar recharges
            the battery above the trigger threshold.
    """

    pre_set: bool
    start_set: bool
    clipping_export_set: bool
    high_soc_export_set: bool


class NightState(TypedDict):
    """Mutable night-window state threaded through the scheduler and night handler.

    Attributes:
        mode_set_key: (target_date, mode_int) tuple for the last night mode applied,
            or None if no night mode has been set yet this cycle.
        sleep_snapshot_for_date: Local date for which the end-of-day telemetry
            snapshot has been captured, or None if not yet captured.
    """

    mode_set_key: tuple[date, int] | None
    sleep_snapshot_for_date: date | None


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
        current_date: Current local date (date part only).
        auth_refreshed_for_date: Last date on which Sigen auth was refreshed, or None.
        refresh_auth_on_wake: Flag indicating auth refresh should be performed on next wake.
        live_solar_kw_samples: Rolling deque of live solar generation samples in kW,
            bounded to LIVE_SOLAR_AVERAGE_SAMPLE_COUNT (3) entries.
        forecast_calibration: Learned multipliers per period, keyed by period name.
        last_forecast_refresh_utc: Timestamp of last successful forecast refresh, or None.
        last_forecast_solar_archive_utc: Timestamp of last successful forecast.solar archive, or None.
        forecast_solar_archive_cooldown_until_utc: Rate-limit cooldown expiry for forecast.solar API, or None.
        timed_export_override: Configuration for timed export override feature.
        last_export_restore_at: UTC timestamp of the most recent timed export restore,
            or None if no restore has occurred yet. Used to enforce the restore cooldown.
        tick_mode_change_attempts: Count of mode-change attempts in the current tick.
        tick_mode_change_successes: Count of successful mode-changes in the current tick.
        tick_mode_change_failures: Count of failed mode-changes in the current tick.
        sleep_override_seconds: Override sleep duration in seconds, or None for normal polling.
        last_known_soc: Most recent battery SOC percentage fetched this session, or None.
        immersion_state: Daily boost counter for the SwitchBot immersion heater (boosts_today, last_boost_date).
        latest_zappi_status: Most recent Zappi live-status snapshot, or None when
            Zappi is not configured or the last fetch failed.
        latest_zappi_daily: Today's Zappi daily charge totals, or None when
            Zappi is not configured or the last fetch failed.
    """

    current_date: date
    today_period_windows: dict[str, datetime] = field(default_factory=dict)
    ordered_period_windows: list[tuple[str, datetime]] = field(default_factory=list)
    tomorrow_period_windows: dict[str, datetime] = field(default_factory=dict)
    today_period_forecast: dict[str, tuple[int, str]] = field(default_factory=dict)
    tomorrow_period_forecast: dict[str, tuple[int, str]] = field(default_factory=dict)
    today_sunrise_utc: datetime | None = None
    today_sunset_utc: datetime | None = None
    tomorrow_sunrise_utc: datetime | None = None
    day_state: dict[str, DayStateEntry] = field(default_factory=dict)
    night_state: NightState = field(default_factory=lambda: NightState(
        mode_set_key=None,
        sleep_snapshot_for_date=None,
    ))
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
    last_export_restore_at: datetime | None = None
    tick_mode_change_attempts: int = 0
    tick_mode_change_successes: int = 0
    tick_mode_change_failures: int = 0
    sleep_override_seconds: int | None = None
    last_known_soc: float | None = None
    immersion_state: dict[str, Any] = field(default_factory=lambda: {
        "boosts_today": 0,
        "last_boost_date": None,
    })
    latest_zappi_status: dict[str, Any] | None = None
    latest_zappi_daily: dict[str, Any] | None = None

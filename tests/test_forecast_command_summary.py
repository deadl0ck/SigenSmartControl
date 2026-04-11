"""Tests for forecast command summary script helpers."""

from datetime import date, datetime

from scripts.forecast_command_summary import (
    derive_period_from_timestamp,
    filter_events,
    is_forecast_driven_event,
    select_target_dates,
    summarize_mode_counts,
)


def test_derive_period_from_timestamp_uses_hour_buckets() -> None:
    """Derived period should come from event timestamp hour buckets."""
    assert derive_period_from_timestamp(datetime.fromisoformat("2026-04-10T08:30:00+01:00")) == "Morn"
    assert derive_period_from_timestamp(datetime.fromisoformat("2026-04-10T13:30:00+01:00")) == "Aftn"
    assert derive_period_from_timestamp(datetime.fromisoformat("2026-04-10T18:30:00+01:00")) == "Eve"
    assert derive_period_from_timestamp(datetime.fromisoformat("2026-04-10T22:30:00+01:00")) == "Outside"


def test_is_forecast_driven_event_uses_derived_period() -> None:
    """Forecast-driven classification should rely on timestamp-derived period."""
    assert is_forecast_driven_event(datetime.fromisoformat("2026-04-10T11:00:00+01:00")) is True
    assert is_forecast_driven_event(datetime.fromisoformat("2026-04-10T21:00:00+01:00")) is False


def test_filter_events_excludes_non_forecast_by_default() -> None:
    """Filtering should keep only daytime events by derived timestamp period."""
    events = [
        {"captured_at": "2026-04-10T08:00:00+01:00", "period": "Eve (period-start)"},
        {"captured_at": "2026-04-10T22:00:00+01:00", "period": "Morn (period-start)"},
    ]

    filtered = filter_events(events, include_all_events=False)

    assert len(filtered) == 1
    assert filtered[0]["captured_at"] == "2026-04-10T08:00:00+01:00"


def test_select_target_dates_defaults_to_latest_date() -> None:
    """Date selection should default to the latest available date."""
    dates = [date(2026, 4, 9), date(2026, 4, 10)]

    selected = select_target_dates(dates, date_text=None, include_all_dates=False)

    assert selected == [date(2026, 4, 10)]


def test_summarize_mode_counts_groups_by_label_and_value() -> None:
    """Mode summary should aggregate repeated requested mode labels."""
    events = [
        {"requested_mode_label": "AI", "requested_mode": 1},
        {"requested_mode_label": "AI", "requested_mode": 1},
        {"requested_mode_label": "GRID_EXPORT", "requested_mode": 5},
    ]

    counts = summarize_mode_counts(events)

    assert counts["AI (1)"] == 2
    assert counts["GRID_EXPORT (5)"] == 1

"""Tests for the hourly Forecast.Solar vs inverter readings comparison script."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from config.settings import LOCAL_TIMEZONE
from scripts.forecast_solar_vs_inverter_readings import (
    build_forecast_hourly_averages,
    build_inverter_hourly_generation,
)


def test_build_inverter_hourly_generation_sums_positive_pvdaynrg_deltas() -> None:
    """Hourly actuals should be the sum of positive cumulative-energy deltas."""
    local_tz = ZoneInfo(LOCAL_TIMEZONE)
    target_date = date(2026, 4, 10)
    telemetry_records = [
        {
            "captured_at": "2026-04-10T07:50:00+01:00",
            "energy_flow": {"pvDayNrg": 1.0},
        },
        {
            "captured_at": "2026-04-10T08:10:00+01:00",
            "energy_flow": {"pvDayNrg": 1.4},
        },
        {
            "captured_at": "2026-04-10T08:40:00+01:00",
            "energy_flow": {"pvDayNrg": 2.1},
        },
        {
            "captured_at": "2026-04-10T09:10:00+01:00",
            "energy_flow": {"pvDayNrg": 2.7},
        },
    ]

    hourly_kwh, hourly_counts = build_inverter_hourly_generation(
        telemetry_records,
        target_date,
        datetime(2026, 4, 10, 8, 0, tzinfo=local_tz),
        datetime(2026, 4, 10, 10, 0, tzinfo=local_tz),
        local_tz,
    )

    assert hourly_kwh[8] == pytest.approx(1.1)
    assert hourly_kwh[9] == pytest.approx(0.6)
    assert hourly_counts == {8: 2, 9: 1}


def test_build_forecast_hourly_averages_uses_latest_snapshot_only() -> None:
    """Forecast averages should use only the most recent snapshot with target points."""
    local_tz = ZoneInfo(LOCAL_TIMEZONE)
    target_date = date(2026, 4, 10)
    forecast_records = [
        {
            "captured_at_utc": "2026-04-10T09:05:00+00:00",
            "readings": {
                "2026-04-10 10:00:00": 1000.0,
                "2026-04-10 10:30:00": 1200.0,
            },
        },
        {
            "captured_at_utc": "2026-04-10T09:35:00+00:00",
            "readings": {
                "2026-04-10 10:00:00": 1400.0,
                "2026-04-10 10:30:00": 1600.0,
            },
        },
        {
            "captured_at_utc": "2026-04-10T10:05:00+00:00",
            "readings": {
                "2026-04-10 11:00:00": 2000.0,
            },
        },
    ]

    averages_kw, sample_counts = build_forecast_hourly_averages(
        forecast_records,
        target_date,
        {10, 11},
        local_tz,
    )

    assert averages_kw == {11: 2.0}
    assert sample_counts == {11: 1}
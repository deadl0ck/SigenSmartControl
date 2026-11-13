"""Night sleep must not start before the cheap-rate window opens (evening)."""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from logic import night


def _run(now_local):
    now_utc = now_local.astimezone(timezone.utc)
    ctx = {
        "target_date": "2026-11-14", "target_period": "Morn",
        "target_start": datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc),
        "window_name": "EVENING-NIGHT", "night_start": now_utc - timedelta(hours=1),
        "solar_value": 5, "status": "Red",
    }
    state = {"mode_set_key": None, "sleep_snapshot_for_date": None}
    archive = AsyncMock()
    res = asyncio.run(night.handle_night_window(
        now_utc=now_utc, night_context=ctx, night_state=state, period_solar_kwh=0.0,
        fetch_soc=AsyncMock(return_value=50.0),
        start_timed_grid_export=AsyncMock(return_value=False),
        apply_mode_change=AsyncMock(return_value=True),
        archive_inverter_telemetry=archive, sigen=object(), mode_names={},
    ))
    return res, archive


def test_no_sleep_at_peak_before_cheap_rate(monkeypatch):
    monkeypatch.setattr(night, "NIGHT_SLEEP_MODE_ENABLED", True)
    res, archive = _run(datetime(2026, 11, 13, 17, 30, tzinfo=ZoneInfo("Europe/Dublin")))
    assert res["sleep_seconds"] is None
    archive.assert_not_called()


def test_sleeps_once_cheap_rate_starts(monkeypatch):
    monkeypatch.setattr(night, "NIGHT_SLEEP_MODE_ENABLED", True)
    res, archive = _run(datetime(2026, 11, 13, 23, 0, tzinfo=ZoneInfo("Europe/Dublin")))
    assert res["sleep_seconds"] and res["sleep_seconds"] > 3600
    archive.assert_called_once()

"""Tests for the seasonal Morn headroom-export gate in start_timed_grid_export."""

import asyncio
import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from logic.timed_export import start_timed_grid_export

LOGGER = logging.getLogger("test")


def _start(period, month, is_clipping_export=False):
    sigen = MagicMock()
    sigen.get_operational_mode = AsyncMock(return_value=0)
    apply = AsyncMock(return_value=True)
    override = {"active": False}
    ok = asyncio.run(
        start_timed_grid_export(
            timed_export_override=override,
            set_timed_export_override=MagicMock(),
            period=period,
            reason="test",
            duration_minutes=30,
            now_utc=datetime(2026, month, 15, 5, 30, tzinfo=timezone.utc),
            battery_soc=95.0,
            is_clipping_export=is_clipping_export,
            export_soc_floor=40.0,
            sigen=sigen,
            mode_names={0: "x", 1: "y", 2: "z", 3: "w"},
            apply_mode_change=apply,
            logger=LOGGER,
        )
    )
    return ok, sigen, apply


def test_morn_headroom_export_blocked_in_disabled_month():
    ok, sigen, apply = _start("Morn", 10)
    assert ok is False
    sigen.get_operational_mode.assert_not_called()
    apply.assert_not_called()


def test_morn_headroom_export_allowed_in_summer():
    _, sigen, _ = _start("Morn", 6)
    sigen.get_operational_mode.assert_called()


def test_morn_clipping_export_still_allowed_in_disabled_month():
    _, sigen, _ = _start("Morn", 10, is_clipping_export=True)
    sigen.get_operational_mode.assert_called()


def test_other_periods_unaffected_in_disabled_month():
    _, sigen, _ = _start("Aftn", 10)
    sigen.get_operational_mode.assert_called()

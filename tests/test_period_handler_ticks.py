"""Unit tests for period handler state mutations in handle_morning_period().

Verifies that pre_set, start_set, and clipping_export_set flags are set
correctly under various tick conditions, and that guard flags prevent
double-triggering of timed exports and mode changes.
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

from config.settings import (
    MORNING_HIGH_SOC_THRESHOLD_PERCENT,
    MID_PERIOD_SAFETY_SOLAR_TRIGGER_KW,
    HEADROOM_TARGET_KWH,
    SIGEN_MODES,
    SIGEN_MODE_NAMES,
)
from logic.morning import handle_morning_period
from logic.period_handler_shared import PeriodHandlerContext
from logic.scheduler_state import DayStateEntry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

UTC = timezone.utc

# A daytime UTC timestamp that is clearly within the DAY tariff window
# (08:00–17:00 Europe/Dublin, UTC in summer ≈ UTC-1 so 09:00–18:00 UTC).
# Using 10:00 UTC on a summer date keeps us safely in DAY tariff.
_BASE_DATE = datetime(2026, 6, 15, tzinfo=UTC)
_PERIOD_START = _BASE_DATE.replace(hour=9, minute=0)   # 09:00 UTC ~ 10:00 local
_PERIOD_END   = _BASE_DATE.replace(hour=13, minute=0)  # 13:00 UTC ~ 14:00 local


def _make_period_state(
    pre_set: bool = False,
    start_set: bool = False,
    clipping_export_set: bool = False,
) -> DayStateEntry:
    """Return a fresh DayStateEntry with the given flag values."""
    return DayStateEntry(
        pre_set=pre_set,
        start_set=start_set,
        clipping_export_set=clipping_export_set,
    )


def _make_timed_export_override(active: bool = False) -> dict[str, Any]:
    """Return a minimal timed export override dict."""
    return {
        "active": active,
        "restore_at": None,
        "restore_mode": None,
        "restore_mode_label": None,
        "trigger_period": None,
        "duration_minutes": None,
        "started_at": None,
    }


def _make_calibration() -> dict[str, Any]:
    """Return default calibration dict used in pre-period lead-time calc."""
    return {"export_lead_buffer_multiplier": 1.1}


def _make_handler_kwargs(
    *,
    now_utc: datetime,
    period_start: datetime = _PERIOD_START,
    period_state: DayStateEntry | None = None,
    timed_export_override: dict[str, Any] | None = None,
    soc_return: float | None = 30.0,
    solar_avg_kw: float | None = 1.0,
    effective_export_kw: float = 3.0,
    start_timed_export_return: bool = True,
    apply_mode_change_return: bool = True,
    status: str = "Green",
    period_solar_kwh: float = 20.0,
) -> dict[str, Any]:
    """Build keyword arguments for handle_morning_period() with sensible defaults.

    Args:
        now_utc: Timestamp for this scheduler tick.
        period_start: Morning period start time (default 09:00 UTC).
        period_state: Pre-built DayStateEntry; defaults to all-False if None.
        timed_export_override: Override dict; defaults to inactive if None.
        soc_return: Value that fetch_soc() returns.
        solar_avg_kw: Value that get_live_solar_average_kw() returns.
        effective_export_kw: Value that get_effective_battery_export_kw() returns.
        start_timed_export_return: Bool returned by start_timed_grid_export().
        apply_mode_change_return: Bool returned by apply_mode_change().
        status: Forecast status string passed to handler.
        period_solar_kwh: Estimated period solar energy in kWh.

    Returns:
        Dict of keyword-only arguments ready for ``await handle_morning_period(**kwargs)``.
    """
    if period_state is None:
        period_state = _make_period_state()
    if timed_export_override is None:
        timed_export_override = _make_timed_export_override()

    fetch_soc = AsyncMock(return_value=soc_return)
    start_timed_grid_export = AsyncMock(return_value=start_timed_export_return)
    apply_mode_change = AsyncMock(return_value=apply_mode_change_return)
    get_live_solar_average_kw = MagicMock(return_value=solar_avg_kw)
    get_effective_battery_export_kw = MagicMock(return_value=effective_export_kw)

    return dict(
        now_utc=now_utc,
        period_start=period_start,
        period_end_utc=_PERIOD_END,
        period_state=period_state,
        timed_export_override=timed_export_override,
        solar_value=5000,
        status=status,
        period_solar_kwh=period_solar_kwh,
        period_calibration=_make_calibration(),
        fetch_soc=fetch_soc,
        get_live_solar_average_kw=get_live_solar_average_kw,
        get_effective_battery_export_kw=get_effective_battery_export_kw,
        start_timed_grid_export=start_timed_grid_export,
        apply_mode_change=apply_mode_change,
        sigen=MagicMock(),
        mode_names=SIGEN_MODE_NAMES,
        # Keep references for assertion access
        _fetch_soc=fetch_soc,
        _start_timed_grid_export=start_timed_grid_export,
        _apply_mode_change=apply_mode_change,
    )


def _strip_private(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Remove private helper keys (prefixed with '_') before passing to the handler."""
    return {k: v for k, v in kwargs.items() if not k.startswith("_")}


def _make_ctx(kwargs: dict[str, Any]) -> PeriodHandlerContext:
    """Build a PeriodHandlerContext from the helper-kwargs dict."""
    public = _strip_private(kwargs)
    return PeriodHandlerContext(**public)


# ---------------------------------------------------------------------------
# Initial-state tests (no async needed)
# ---------------------------------------------------------------------------

class TestDayStateInitialValues:
    """Fresh DayStateEntry must have all flags False."""

    def test_pre_set_false_initially(self):
        """A freshly created DayStateEntry has pre_set=False."""
        entry = _make_period_state()
        assert entry["pre_set"] is False

    def test_start_set_false_initially(self):
        """A freshly created DayStateEntry has start_set=False."""
        entry = _make_period_state()
        assert entry["start_set"] is False

    def test_clipping_export_set_false_initially(self):
        """A freshly created DayStateEntry has clipping_export_set=False."""
        entry = _make_period_state()
        assert entry["clipping_export_set"] is False


# ---------------------------------------------------------------------------
# Pre-period tests
# ---------------------------------------------------------------------------

class TestPrePeriodBranch:
    """Tests for the PRE-PERIOD export check (120–180 min before period_start)."""

    @pytest.mark.asyncio
    async def test_pre_period_does_not_set_pre_set_before_export_window_opens(self):
        """pre_set stays False when the export window has not yet opened.

        When headroom_deficit=0, export_by equals period_start.  The pre-period
        branch guards with ``now_utc < period_start``, so ``now_utc >= export_by``
        is always False in this window.  The handler logs "waiting until export
        window opens" and leaves pre_set unchanged.
        """
        period_start = _PERIOD_START
        # 60 min before period start — within the 180-min pre-period window
        now_utc = period_start - timedelta(minutes=60)
        period_state = _make_period_state()

        kwargs = _make_handler_kwargs(
            now_utc=now_utc,
            period_start=period_start,
            period_state=period_state,
            soc_return=30.0,          # headroom = 16.8 kWh > 12 kWh → no deficit
            solar_avg_kw=0.5,
            status="Green",
        )
        await handle_morning_period(_make_ctx(kwargs))

        # Export window has not opened yet so pre_set must still be False
        assert period_state["pre_set"] is False

    @pytest.mark.asyncio
    async def test_pre_period_triggers_export_when_headroom_deficit_exists(self):
        """start_timed_grid_export is called when headroom deficit exists in pre-period.

        SOC=90% → headroom=2.4 kWh << 12 kWh target → large deficit.
        Status Green + high SOC means decision returns GRID_EXPORT.
        """
        period_start = _PERIOD_START
        now_utc = period_start - timedelta(minutes=60)
        period_state = _make_period_state()

        kwargs = _make_handler_kwargs(
            now_utc=now_utc,
            period_start=period_start,
            period_state=period_state,
            soc_return=90.0,          # headroom = 24 * 0.10 = 2.4 kWh < 12 kWh target
            solar_avg_kw=1.0,
            status="Green",
            start_timed_export_return=True,
        )
        await handle_morning_period(_make_ctx(kwargs))

        kwargs["_start_timed_grid_export"].assert_called_once()
        assert period_state["pre_set"] is True

    @pytest.mark.asyncio
    async def test_pre_set_prevents_double_trigger(self):
        """start_timed_grid_export is called at most once; pre_set guards the second tick.

        The first call enters the pre-period branch and sets pre_set=True.
        The second call skips the pre-period branch entirely because pre_set is now True.
        """
        period_start = _PERIOD_START
        now_utc = period_start - timedelta(minutes=60)
        period_state = _make_period_state()

        kwargs = _make_handler_kwargs(
            now_utc=now_utc,
            period_start=period_start,
            period_state=period_state,
            soc_return=90.0,
            solar_avg_kw=1.0,
            status="Green",
            start_timed_export_return=True,
        )

        # First tick — pre-period branch fires
        await handle_morning_period(_make_ctx(kwargs))
        assert period_state["pre_set"] is True

        # Second tick at same time — pre_set=True should block re-entry
        await handle_morning_period(_make_ctx(kwargs))

        assert kwargs["_start_timed_grid_export"].call_count == 1


# ---------------------------------------------------------------------------
# Period-start tests
# ---------------------------------------------------------------------------

class TestPeriodStartBranch:
    """Tests for the PERIOD-START mode decision (now_utc >= period_start)."""

    @pytest.mark.asyncio
    async def test_period_start_sets_start_set_and_calls_apply_mode_change(self):
        """At period start, start_set is set to True and apply_mode_change is called.

        SOC=30% → no headroom deficit → SELF_POWERED mode → apply_mode_change path.
        """
        period_start = _PERIOD_START
        # Exactly at period start
        now_utc = period_start
        period_state = _make_period_state()

        kwargs = _make_handler_kwargs(
            now_utc=now_utc,
            period_start=period_start,
            period_state=period_state,
            soc_return=30.0,    # headroom = 16.8 kWh > 12 kWh → SELF_POWERED
            solar_avg_kw=1.0,
            status="Green",
            apply_mode_change_return=True,
        )
        await handle_morning_period(_make_ctx(kwargs))

        assert period_state["start_set"] is True
        kwargs["_apply_mode_change"].assert_called_once()

    @pytest.mark.asyncio
    async def test_start_set_prevents_double_trigger(self):
        """apply_mode_change is called at most once; start_set guards the second tick."""
        period_start = _PERIOD_START
        now_utc = period_start + timedelta(minutes=5)
        period_state = _make_period_state()

        kwargs = _make_handler_kwargs(
            now_utc=now_utc,
            period_start=period_start,
            period_state=period_state,
            soc_return=30.0,
            solar_avg_kw=1.0,
            status="Green",
            apply_mode_change_return=True,
        )

        # First tick — period-start branch fires
        await handle_morning_period(_make_ctx(kwargs))
        assert period_state["start_set"] is True
        assert kwargs["_apply_mode_change"].call_count == 1

        # Second tick — start_set=True prevents re-entry of period-start block
        await handle_morning_period(_make_ctx(kwargs))
        assert kwargs["_apply_mode_change"].call_count == 1

    @pytest.mark.asyncio
    async def test_period_start_triggers_timed_export_when_headroom_deficit_exists(self):
        """At period start with large headroom deficit, start_timed_grid_export is called.

        SOC=92% → headroom=1.92 kWh → deficit≈10.08 kWh → GRID_EXPORT path.
        """
        period_start = _PERIOD_START
        now_utc = period_start
        period_state = _make_period_state()

        kwargs = _make_handler_kwargs(
            now_utc=now_utc,
            period_start=period_start,
            period_state=period_state,
            soc_return=92.0,
            solar_avg_kw=1.0,
            status="Green",
            start_timed_export_return=True,
        )
        await handle_morning_period(_make_ctx(kwargs))

        kwargs["_start_timed_grid_export"].assert_called_once()
        assert period_state["start_set"] is True
        assert period_state["pre_set"] is True


# ---------------------------------------------------------------------------
# Mid-period clipping export (clipping_export_set)
# ---------------------------------------------------------------------------

class TestMidPeriodClippingExport:
    """Tests for the mid-period live clipping export branch."""

    @pytest.mark.asyncio
    async def test_clipping_export_set_on_amber_promoted_to_green_with_high_soc_and_solar(self):
        """clipping_export_set is set to True when Amber is promoted to Green mid-period.

        Conditions:
          - start_set=True (mid-period is active)
          - timed_export_override inactive
          - now_utc >= period_start and < period_end_utc
          - is_live_clipping_period_enabled("Morn") is True (default setting "M,A")
          - status="Amber"
          - SOC >= LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT (50%)
          - solar >= LIVE_CLIPPING_RISK_SOLAR_TRIGGER_KW (3.2 kW)
        When all these hold, _promote_status_for_live_clipping_risk promotes Amber→Green,
        so decision_status != status and the clipping branch proceeds.
        With SOC=90% the headroom deficit is large → mode=GRID_EXPORT and
        start_timed_grid_export is called, then clipping_export_set=True.
        """
        period_start = _PERIOD_START
        now_utc = period_start + timedelta(minutes=30)   # mid-period
        period_state = _make_period_state(start_set=True)  # already at period start

        kwargs = _make_handler_kwargs(
            now_utc=now_utc,
            period_start=period_start,
            period_state=period_state,
            soc_return=90.0,          # >= 50% threshold → promotion eligible; large deficit
            solar_avg_kw=4.0,         # >= 3.2 kW trigger → promotion proceeds
            status="Amber",
            start_timed_export_return=True,
        )
        await handle_morning_period(_make_ctx(kwargs))

        assert period_state["clipping_export_set"] is True

    @pytest.mark.asyncio
    async def test_low_soc_blocks_clipping_export(self):
        """clipping_export_set stays False when SOC is below the promotion threshold.

        SOC=20% < LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT (50%) so Amber is NOT
        promoted to Green. decision_status == status, so the clipping branch body
        is skipped and clipping_export_set is NOT set.
        """
        period_start = _PERIOD_START
        now_utc = period_start + timedelta(minutes=30)
        period_state = _make_period_state(start_set=True)

        kwargs = _make_handler_kwargs(
            now_utc=now_utc,
            period_start=period_start,
            period_state=period_state,
            soc_return=20.0,          # below 50% threshold → no promotion
            solar_avg_kw=4.0,
            status="Amber",
        )
        await handle_morning_period(_make_ctx(kwargs))

        assert period_state["clipping_export_set"] is False

    @pytest.mark.asyncio
    async def test_low_solar_blocks_clipping_export(self):
        """clipping_export_set stays False when live solar is below the trigger threshold.

        Solar=1.0 kW < LIVE_CLIPPING_RISK_SOLAR_TRIGGER_KW (3.2 kW) → no Amber→Green
        promotion, so the clipping export body is not reached.
        """
        period_start = _PERIOD_START
        now_utc = period_start + timedelta(minutes=30)
        period_state = _make_period_state(start_set=True)

        kwargs = _make_handler_kwargs(
            now_utc=now_utc,
            period_start=period_start,
            period_state=period_state,
            soc_return=80.0,
            solar_avg_kw=1.0,         # below 3.2 kW trigger
            status="Amber",
        )
        await handle_morning_period(_make_ctx(kwargs))

        assert period_state["clipping_export_set"] is False

    @pytest.mark.asyncio
    async def test_clipping_export_not_set_before_period_start(self):
        """Mid-period clipping check is skipped when now_utc < period_start.

        Even with start_set=True (hypothetically), the guard `now_utc >= period_start`
        prevents the clipping block from running before the period has started.
        """
        period_start = _PERIOD_START
        now_utc = period_start - timedelta(minutes=10)   # before period start
        period_state = _make_period_state(start_set=True)

        kwargs = _make_handler_kwargs(
            now_utc=now_utc,
            period_start=period_start,
            period_state=period_state,
            soc_return=90.0,
            solar_avg_kw=4.0,
            status="Amber",
        )
        await handle_morning_period(_make_ctx(kwargs))

        assert period_state["clipping_export_set"] is False

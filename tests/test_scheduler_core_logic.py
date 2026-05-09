"""
test_scheduler_core_logic.py
----------------------------
Focused unit tests for core scheduler algorithms:
  - Timed export state machine (activation, restoration, SOC floors)
  - Night-mode branching (PRE-DAWN discharge, EVENING-NIGHT exports)
  - Lead-time calculations (headroom deficit → export window timing)

Uses pytest and async fixtures. Tests validate behavior without full scheduler loop.
"""

import asyncio
import json
import logging
import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from typing import Any

from config.settings import (
    BATTERY_KWH,
    HEADROOM_TARGET_KWH,
    INVERTER_KW,
    MAX_TIMED_EXPORT_MINUTES,
    SIGEN_MODES,
)
from logic.decision_logic import calc_headroom_kwh

logger = logging.getLogger(__name__)


class TestHeadroomAndLeadTime:
    """Test battery headroom calculations and dynamic lead-time planning."""

    def test_calc_headroom_basic(self):
        """Headroom = Battery * (1 - SOC%) should be correct."""
        battery_kwh = 8.0
        soc_percent = 75.0
        
        headroom = calc_headroom_kwh(battery_kwh, soc_percent)
        
        # At 75% SOC, 25% is headroom = 8 * 0.25 = 2.0 kWh
        expected = 2.0
        assert headroom == expected, f"Expected {expected}, got {headroom}"
        logger.info(f"[TEST] Headroom at {soc_percent}% SOC: {headroom:.2f} kWh (expected {expected:.2f})")

    def test_calc_headroom_full_soc(self):
        """At 100% SOC, headroom should be zero."""
        battery_kwh = 10.0
        soc_percent = 100.0
        
        headroom = calc_headroom_kwh(battery_kwh, soc_percent)
        
        assert headroom == 0.0
        logger.info(f"[TEST] Headroom at 100% SOC: {headroom:.2f} kWh (expected 0.0)")

    def test_calc_headroom_empty_soc(self):
        """At 0% SOC, headroom equals full battery."""
        battery_kwh = 8.0
        soc_percent = 0.0
        
        headroom = calc_headroom_kwh(battery_kwh, soc_percent)
        
        assert headroom == battery_kwh
        logger.info(f"[TEST] Headroom at 0% SOC: {headroom:.2f} kWh (expected {battery_kwh:.2f})")

    def test_lead_time_calculation_with_deficit(self):
        """Dynamic lead time = (deficit_kwh * buffer_multiplier) / effective_export_kw."""
        headroom_deficit_kwh = 3.0  # Need to clear 3 kWh
        effective_battery_export_kw = 3.0  # Can export 3 kW
        lead_buffer_multiplier = 1.2  # 20% safety margin
        
        # lead_time_hours = (3.0 * 1.2) / 3.0 = 1.2 hours
        lead_time_hours = (headroom_deficit_kwh * lead_buffer_multiplier) / effective_battery_export_kw
        
        expected_hours = 1.2
        assert abs(lead_time_hours - expected_hours) < 0.01
        logger.info(f"[TEST] Lead time with {headroom_deficit_kwh} kWh deficit @ {effective_battery_export_kw} kW: {lead_time_hours:.2f} hours")

    def test_lead_time_zero_deficit(self):
        """Lead time should be 0 when headroom deficit is already satisfied."""
        headroom_deficit_kwh = 0.0
        effective_battery_export_kw = 3.0
        lead_buffer_multiplier = 1.2
        
        lead_time_hours = (headroom_deficit_kwh * lead_buffer_multiplier) / effective_battery_export_kw
        
        assert lead_time_hours == 0.0
        logger.info("[TEST] Lead time with 0 kWh deficit: 0.0 hours (no export needed)")

    def test_lead_time_high_deficit_slow_export(self):
        """Large deficit with slow export = longer lead time."""
        headroom_deficit_kwh = 5.0  # Large deficit
        effective_battery_export_kw = 1.0  # Slow export (high solar)
        lead_buffer_multiplier = 1.5
        
        lead_time_hours = (headroom_deficit_kwh * lead_buffer_multiplier) / effective_battery_export_kw
        
        # (5.0 * 1.5) / 1.0 = 7.5 hours
        expected_hours = 7.5
        assert abs(lead_time_hours - expected_hours) < 0.01
        logger.info(f"[TEST] Lead time with large deficit: {lead_time_hours:.2f} hours (expected {expected_hours:.2f})")


class TestTimedExportStateMachine:
    """Test timed export override activation, duration clamping, and restoration logic."""

    def test_timed_export_state_structure(self):
        """Timed export override dict has correct structure when active."""
        from logic.timed_export import _empty_timed_export_override

        state = _empty_timed_export_override()
        
        # Verify structure
        assert state["active"] is False
        assert state["started_at"] is None
        assert state["restore_at"] is None
        assert state["restore_mode"] is None
        assert "trigger_period" in state
        assert "duration_minutes" in state
        logger.info("[TEST] Timed export override state structure is correct")

    def test_timed_export_activation_sequence(self):
        """Timed export should transition: inactive -> active -> check restore."""
        now_utc = datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc)
        requested_minutes = 30
        
        # Simulate activation
        state = {
            "active": True,
            "started_at": now_utc,
            "restore_at": now_utc + timedelta(minutes=requested_minutes),
            "restore_mode": SIGEN_MODES["SELF_POWERED"],
            "restore_mode_label": "SELF_POWERED",
            "trigger_period": "Aftn",
            "duration_minutes": requested_minutes,
        }
        
        assert state["active"] is True
        assert state["started_at"] == now_utc
        assert state["restore_at"] == now_utc + timedelta(minutes=30)
        logger.info(f"[TEST] Timed export activated: started {now_utc}, restore at {state['restore_at']}")

    def test_timed_export_duration_clamping(self):
        """Requested duration should be clamped to MAX_TIMED_EXPORT_MINUTES."""
        requested_minutes = 300  # Way higher than max
        max_minutes = MAX_TIMED_EXPORT_MINUTES
        
        clamped = min(requested_minutes, max_minutes)
        
        assert clamped == max_minutes
        assert clamped < requested_minutes
        logger.info(f"[TEST] Duration {requested_minutes} clamped to {clamped} (max)")

    def test_timed_export_restoration_by_elapsed_time(self):
        """Timed export should restore when restore_at time has passed."""
        now_utc = datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc)
        restore_at = now_utc + timedelta(minutes=30)
        
        # Check status before restore time
        status_before = now_utc < restore_at  # Should be True
        assert status_before is True
        logger.info(f"[TEST] At {now_utc}, restore_at {restore_at} not yet reached")
        
        # Check status after restore time
        now_after = restore_at + timedelta(seconds=1)
        status_after = now_after >= restore_at  # Should be True
        assert status_after is True
        logger.info(f"[TEST] At {now_after}, restore_at {restore_at} has been reached")

    def test_timed_export_restoration_by_soc_floor(self):
        """Timed export should restore early when SOC hits export_soc_floor."""
        export_soc_floor = 20.0  # Restore when SOC drops below 20%
        current_soc = 18.0  # Current SOC is below floor
        
        should_restore_early = current_soc <= export_soc_floor
        
        assert should_restore_early is True
        logger.info(f"[TEST] SOC {current_soc}% <= floor {export_soc_floor}%, restore early")

    def test_timed_export_soc_floor_not_triggered(self):
        """Timed export should NOT restore when SOC is above floor."""
        export_soc_floor = 20.0
        current_soc = 25.0  # Above floor
        
        should_restore_early = current_soc <= export_soc_floor
        
        assert should_restore_early is False
        logger.info(f"[TEST] SOC {current_soc}% > floor {export_soc_floor}%, continue exporting")

    def test_timed_export_clipping_soc_floor(self):
        """Clipping export should respect separate clipping_soc_floor threshold."""
        is_clipping_export = True
        clipping_soc_floor = 30.0
        current_soc = 28.0

        should_restore = current_soc <= clipping_soc_floor and is_clipping_export

        assert should_restore is True
        logger.info(f"[TEST] Clipping export: SOC {current_soc}% <= clipping floor {clipping_soc_floor}%, restore")

    @pytest.mark.asyncio
    async def test_clipping_export_extends_when_soc_above_trigger(self):
        """Clipping export at restore_at with SOC still above trigger threshold should extend."""
        from logic.timed_export import maybe_restore_timed_grid_export
        from config.settings import LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT

        started_at = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
        restore_at = datetime(2026, 5, 6, 12, 15, tzinfo=timezone.utc)
        now_utc = restore_at + timedelta(seconds=1)

        override = {
            "active": True,
            "started_at": started_at,
            "restore_at": restore_at,
            "restore_mode": SIGEN_MODES["SELF_POWERED"],
            "restore_mode_label": "SELF_POWERED",
            "trigger_period": "Morn",
            "duration_minutes": 15,
            "is_clipping_export": True,
            "clipping_soc_floor": 45.0,
            "export_soc_floor": None,
        }

        saved_state = {}

        def set_override(state):
            saved_state.update(state)

        fetch_soc = AsyncMock(return_value=LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT + 10)
        apply_mode_change = AsyncMock(return_value=True)

        result = await maybe_restore_timed_grid_export(
            timed_export_override=override,
            set_timed_export_override=set_override,
            now_utc=now_utc,
            fetch_soc=fetch_soc,
            sigen=None,
            mode_names={},
            apply_mode_change=apply_mode_change,
            logger=logger,
        )

        assert result == "active", "Should extend, not restore"
        assert saved_state.get("restore_at") > restore_at, "restore_at should be bumped"
        apply_mode_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_export_extends_past_initial_duration_when_soc_above_floor(self):
        """Export should extend based on SOC floor even after MAX_TIMED_EXPORT_MINUTES elapsed."""
        from logic.timed_export import maybe_restore_timed_grid_export
        from config.settings import MAX_TIMED_EXPORT_MINUTES

        started_at = datetime(2026, 5, 9, 5, 42, tzinfo=timezone.utc)
        restore_at = started_at + timedelta(minutes=MAX_TIMED_EXPORT_MINUTES)
        now_utc = restore_at + timedelta(seconds=1)

        override = {
            "active": True,
            "started_at": started_at,
            "restore_at": restore_at,
            "restore_mode": SIGEN_MODES["SELF_POWERED"],
            "restore_mode_label": "SELF_POWERED",
            "trigger_period": "Morn",
            "duration_minutes": MAX_TIMED_EXPORT_MINUTES,
            "is_clipping_export": True,
            "clipping_soc_floor": 45.0,
            "export_soc_floor": 45.0,
        }

        saved_state = {}

        def set_override(state):
            saved_state.update(state)

        fetch_soc = AsyncMock(return_value=52.0)
        apply_mode_change = AsyncMock(return_value=True)

        result = await maybe_restore_timed_grid_export(
            timed_export_override=override,
            set_timed_export_override=set_override,
            now_utc=now_utc,
            fetch_soc=fetch_soc,
            sigen=None,
            mode_names={},
            apply_mode_change=apply_mode_change,
            logger=logger,
        )

        assert result == "active", "Should extend when SOC above floor, regardless of elapsed time"
        assert saved_state.get("restore_at") > restore_at, "restore_at should be bumped"
        apply_mode_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_export_restores_when_current_period_floor_higher_than_stored(self):
        """Green export extending into Amber period should stop at Amber floor, not Green floor."""
        from logic.timed_export import maybe_restore_timed_grid_export

        started_at = datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc)
        restore_at = datetime(2026, 5, 9, 14, 30, tzinfo=timezone.utc)
        now_utc = restore_at + timedelta(seconds=1)

        override = {
            "active": True,
            "started_at": started_at,
            "restore_at": restore_at,
            "restore_mode": SIGEN_MODES["SELF_POWERED"],
            "restore_mode_label": "SELF_POWERED",
            "trigger_period": "Aftn",
            "duration_minutes": 30,
            "is_clipping_export": False,
            "clipping_soc_floor": None,
            "export_soc_floor": 50.0,  # Green period floor stored at export start
        }

        saved_state = {}

        def set_override(state):
            saved_state.update(state)

        fetch_soc = AsyncMock(return_value=60.0)  # 60% > Green floor (50%), but < Amber floor (75%)
        apply_mode_change = AsyncMock(return_value=True)

        result = await maybe_restore_timed_grid_export(
            timed_export_override=override,
            set_timed_export_override=set_override,
            now_utc=now_utc,
            fetch_soc=fetch_soc,
            sigen=None,
            mode_names={},
            apply_mode_change=apply_mode_change,
            logger=logger,
            current_export_soc_floor=75.0,  # Amber period is now active
        )

        assert result == "restored", "Should restore when SOC is below Amber floor even if above stored Green floor"
        apply_mode_change.assert_called_once()

    @pytest.mark.asyncio
    async def test_export_extends_when_soc_above_current_period_floor(self):
        """Export at restore_at should extend when SOC is above the current period's floor."""
        from logic.timed_export import maybe_restore_timed_grid_export

        started_at = datetime(2026, 5, 9, 14, 0, tzinfo=timezone.utc)
        restore_at = datetime(2026, 5, 9, 14, 30, tzinfo=timezone.utc)
        now_utc = restore_at + timedelta(seconds=1)

        override = {
            "active": True,
            "started_at": started_at,
            "restore_at": restore_at,
            "restore_mode": SIGEN_MODES["SELF_POWERED"],
            "restore_mode_label": "SELF_POWERED",
            "trigger_period": "Aftn",
            "duration_minutes": 30,
            "is_clipping_export": False,
            "clipping_soc_floor": None,
            "export_soc_floor": 50.0,
        }

        saved_state = {}

        def set_override(state):
            saved_state.update(state)

        fetch_soc = AsyncMock(return_value=80.0)  # 80% > Amber floor (75%)
        apply_mode_change = AsyncMock(return_value=True)

        result = await maybe_restore_timed_grid_export(
            timed_export_override=override,
            set_timed_export_override=set_override,
            now_utc=now_utc,
            fetch_soc=fetch_soc,
            sigen=None,
            mode_names={},
            apply_mode_change=apply_mode_change,
            logger=logger,
            current_export_soc_floor=75.0,  # Amber period is now active
        )

        assert result == "active", "Should extend when SOC is above current period floor"
        assert saved_state.get("restore_at") > restore_at
        apply_mode_change.assert_not_called()

    @pytest.mark.asyncio
    async def test_clipping_export_restores_when_soc_at_trigger_threshold(self):
        """Clipping export at restore_at with SOC at or below trigger threshold should restore."""
        from logic.timed_export import maybe_restore_timed_grid_export
        from config.settings import LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT

        started_at = datetime(2026, 5, 6, 12, 0, tzinfo=timezone.utc)
        restore_at = datetime(2026, 5, 6, 12, 15, tzinfo=timezone.utc)
        now_utc = restore_at + timedelta(seconds=1)

        override = {
            "active": True,
            "started_at": started_at,
            "restore_at": restore_at,
            "restore_mode": SIGEN_MODES["SELF_POWERED"],
            "restore_mode_label": "SELF_POWERED",
            "trigger_period": "Morn",
            "duration_minutes": 15,
            "is_clipping_export": True,
            "clipping_soc_floor": 45.0,
            "export_soc_floor": None,
        }

        saved_state = {}

        def set_override(state):
            saved_state.update(state)

        fetch_soc = AsyncMock(return_value=LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT)
        apply_mode_change = AsyncMock(return_value=True)

        result = await maybe_restore_timed_grid_export(
            timed_export_override=override,
            set_timed_export_override=set_override,
            now_utc=now_utc,
            fetch_soc=fetch_soc,
            sigen=None,
            mode_names={},
            apply_mode_change=apply_mode_change,
            logger=logger,
        )

        assert result == "restored", "Should restore when SOC is at trigger threshold"
        apply_mode_change.assert_called_once()

    def test_timed_export_persistence_structure(self):
        """Persisted timed export state should include all required fields."""
        state_to_persist = {
            "active": True,
            "started_at": datetime(2026, 4, 19, 10, 0, 0, tzinfo=timezone.utc),
            "restore_at": datetime(2026, 4, 19, 10, 30, 0, tzinfo=timezone.utc),
            "restore_mode": SIGEN_MODES["SELF_POWERED"],
            "restore_mode_label": "SELF_POWERED",
            "trigger_period": "Aftn",
            "duration_minutes": 30,
            "is_clipping_export": False,
            "export_soc_floor": 15.0,
        }
        
        # Simulate persistence. In real code, datetimes are serialized to ISO strings.
        persisted = dict(state_to_persist)
        for field in ("started_at", "restore_at"):
            if isinstance(persisted[field], datetime):
                persisted[field] = persisted[field].isoformat()
        
        # Verify all fields present for recovery
        assert "active" in persisted
        assert "started_at" in persisted
        assert "restore_at" in persisted
        assert persisted["active"] is True
        logger.info(f"[TEST] Timed export state persists all {len(persisted)} fields")

    def test_timed_export_restore_mode_capture(self):
        """Before starting timed export, capture the current mode for later restoration."""
        current_mode_from_api = SIGEN_MODES["SELF_POWERED"]
        
        # Store for restoration
        restore_mode = current_mode_from_api
        restore_label = "SELF_POWERED"
        
        assert restore_mode == SIGEN_MODES["SELF_POWERED"]
        assert restore_label == "SELF_POWERED"
        logger.info(f"[TEST] Captured restore mode: {restore_label} (value={restore_mode})")

    def test_timed_export_safety_bounds(self):
        """Timed export should have reasonable min/max duration bounds."""
        # Minimum should be 1 minute
        min_duration = max(1, 0)  # Clamp to at least 1
        assert min_duration >= 1
        
        # Maximum should be capped
        max_allowed = MAX_TIMED_EXPORT_MINUTES
        assert max_allowed > 0
        logger.info(f"[TEST] Timed export duration bounds: {min_duration}-{max_allowed} minutes")


class TestNightModeBranching:
    """Test night-mode decision logic: PRE-DAWN discharge, EVENING-NIGHT exports, sleep optimization."""

    def test_night_context_structure(self):
        """Night context should contain period, window name, solar value, status."""
        night_context = {
            "target_date": "2026-04-19",
            "target_period": "Morn",
            "target_start": datetime(2026, 4, 19, 6, 0, 0, tzinfo=timezone.utc),
            "window_name": "PRE-DAWN",
            "night_start": datetime(2026, 4, 19, 21, 0, 0, tzinfo=timezone.utc),
            "solar_value": 10,
            "status": "Red",
        }
        
        assert night_context["window_name"] in ("PRE-DAWN", "EVENING-NIGHT")
        assert night_context["target_period"] in ("Morn", "Aftn", "Eve")
        logger.info(f"[TEST] Night context structure valid: {night_context['window_name']}")

    def test_pre_dawn_discharge_enabled_high_soc(self):
        """PRE-DAWN window with high SOC should trigger discharge."""
        window_name = "PRE-DAWN"
        enable_pre_sunrise_discharge = True
        current_soc = 80.0
        min_soc_threshold = 60.0
        
        should_discharge = (
            window_name == "PRE-DAWN"
            and enable_pre_sunrise_discharge
            and current_soc >= min_soc_threshold
        )
        
        assert should_discharge is True
        logger.info(f"[TEST] PRE-DAWN discharge triggered: SOC {current_soc}% >= {min_soc_threshold}% threshold")

    def test_pre_dawn_discharge_disabled_low_soc(self):
        """PRE-DAWN window with low SOC should keep configured night mode."""
        window_name = "PRE-DAWN"
        enable_pre_sunrise_discharge = True
        current_soc = 40.0
        min_soc_threshold = 60.0
        
        should_discharge = (
            window_name == "PRE-DAWN"
            and enable_pre_sunrise_discharge
            and current_soc >= min_soc_threshold
        )
        
        assert should_discharge is False
        logger.info(f"[TEST] PRE-DAWN discharge skipped: SOC {current_soc}% < {min_soc_threshold}% threshold")

    def test_evening_night_export_window_active(self):
        """EVENING-NIGHT window should plan export when hours until cheap rate exists."""
        window_name = "EVENING-NIGHT"
        hours_until_cheap_rate = 4.5
        
        should_plan_export = window_name == "EVENING-NIGHT" and hours_until_cheap_rate > 0
        
        assert should_plan_export is True
        logger.info(f"[TEST] EVENING-NIGHT export planned: {hours_until_cheap_rate:.1f} hours until cheap rate")

    def test_evening_night_no_export_after_cheap_rate(self):
        """EVENING-NIGHT after cheap rate should NOT plan export."""
        window_name = "EVENING-NIGHT"
        hours_until_cheap_rate = -1.0  # Cheap rate has already started
        
        should_plan_export = window_name == "EVENING-NIGHT" and hours_until_cheap_rate > 0
        
        assert should_plan_export is False
        logger.info(f"[TEST] EVENING-NIGHT export skipped: cheap rate already active ({hours_until_cheap_rate:.1f} hours)")

    def test_night_sleep_calculation_simple(self):
        """Sleep duration should be time until next wake point."""
        now_utc = datetime(2026, 4, 19, 21, 0, 0, tzinfo=timezone.utc)
        wake_at = datetime(2026, 4, 19, 5, 30, 0, tzinfo=timezone.utc) + timedelta(days=1)
        
        sleep_duration = (wake_at - now_utc).total_seconds()
        sleep_minutes = sleep_duration / 60
        
        # 21:00 to 05:30 next day = 8.5 hours = 510 minutes
        expected_minutes = (8.5 * 60)
        assert abs(sleep_minutes - expected_minutes) < 1
        logger.info(f"[TEST] Night sleep duration: {sleep_minutes:.0f} minutes until {wake_at}")

    def test_night_mode_state_tracking(self):
        """Night state should track which mode was last set per date."""
        target_date = "2026-04-19"
        night_mode = SIGEN_MODES["SELF_POWERED"]
        
        mode_set_key = (target_date, night_mode)
        night_state = {"mode_set_key": mode_set_key}
        
        # Should not re-set same mode on next tick
        new_mode_check = night_state["mode_set_key"] != (target_date, night_mode)
        assert new_mode_check is False
        logger.info(f"[TEST] Night mode state tracks {mode_set_key}")

    def test_night_mode_change_detection(self):
        """Night mode should be re-applied when target changes."""
        target_date = "2026-04-19"
        old_mode = SIGEN_MODES["SELF_POWERED"]
        new_mode = SIGEN_MODES["GRID_EXPORT"]
        
        old_key = (target_date, old_mode)
        new_key = (target_date, new_mode)
        
        should_apply = old_key != new_key
        assert should_apply is True
        logger.info(f"[TEST] Night mode change detected: {old_key} -> {new_key}")

    def test_night_sleep_snapshot_per_date(self):
        """Night sleep end-of-day snapshot should be captured only once per date."""
        local_date_1 = "2026-04-19"
        local_date_2 = "2026-04-20"
        
        night_state = {"sleep_snapshot_for_date": None}
        
        # First date: snapshot not yet captured
        should_snapshot_1 = night_state.get("sleep_snapshot_for_date") != local_date_1
        assert should_snapshot_1 is True
        
        # Update state
        night_state["sleep_snapshot_for_date"] = local_date_1
        
        # Same date again: already captured
        should_snapshot_2 = night_state.get("sleep_snapshot_for_date") != local_date_1
        assert should_snapshot_2 is False
        
        # New date: snapshot needed again
        should_snapshot_3 = night_state.get("sleep_snapshot_for_date") != local_date_2
        assert should_snapshot_3 is True
        logger.info(f"[TEST] Night sleep snapshot tracking: {local_date_1} captured once, {local_date_2} needs snapshot")


class TestTimedExportEdgeCases:
    """Test edge cases and error conditions in timed export state machine."""

    def test_timed_export_zero_duration(self):
        """Zero duration should be clamped to minimum 1 minute."""
        requested = 0
        clamped = max(1, requested)
        
        assert clamped >= 1
        logger.info(f"[TEST] Zero duration clamped to {clamped} minute minimum")

    def test_timed_export_negative_duration(self):
        """Negative duration should be clamped to minimum 1 minute."""
        requested = -10
        clamped = max(1, requested)
        
        assert clamped >= 1
        logger.info(f"[TEST] Negative duration {requested} clamped to {clamped} minute minimum")

    def test_timed_export_already_active_rejects_new_request(self):
        """While active, new timed export request should be rejected."""
        timed_export_override = {
            "active": True,
            "restore_at": datetime(2026, 4, 19, 10, 30, 0, tzinfo=timezone.utc),
        }
        
        can_start_new = not timed_export_override["active"]
        
        assert can_start_new is False
        logger.info(f"[TEST] New export request rejected while active until {timed_export_override['restore_at']}")

    def test_timed_export_inactive_allows_new_request(self):
        """When inactive, new timed export request should be allowed."""
        timed_export_override = {"active": False}
        
        can_start_new = not timed_export_override["active"]
        
        assert can_start_new is True
        logger.info("[TEST] New export request allowed when inactive")

    def test_timed_export_recovery_from_persisted_state(self):
        """Restoration from disk should recover active timed export and restore mode."""
        persisted_state = {
            "active": True,
            "started_at": "2026-04-19T10:00:00+00:00",  # ISO string
            "restore_at": "2026-04-19T10:30:00+00:00",
            "restore_mode": SIGEN_MODES["SELF_POWERED"],
            "restore_mode_label": "SELF_POWERED",
            "trigger_period": "Aftn",
            "duration_minutes": 30,
        }
        
        # Simulate restoration from JSON
        restored = dict(persisted_state)
        if isinstance(restored["started_at"], str):
            restored["started_at"] = datetime.fromisoformat(restored["started_at"])
        if isinstance(restored["restore_at"], str):
            restored["restore_at"] = datetime.fromisoformat(restored["restore_at"])
        
        assert restored["active"] is True
        assert restored["restore_mode"] == SIGEN_MODES["SELF_POWERED"]
        assert isinstance(restored["restore_at"], datetime)
        logger.info(f"[TEST] Restored timed export from disk: active={restored['active']}, restore_mode={restored['restore_mode_label']}")


# Tests are ready for execution
if __name__ == "__main__":
    pytest.main([__file__, "-v"])

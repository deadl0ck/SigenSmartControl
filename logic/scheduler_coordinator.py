"""Coordinator for the main scheduling loop.

Orchestrates the periodic evaluation of period-based mode decisions,
handling state transitions, forecast refreshes, and inverter mode commands.
"""

import asyncio
import logging
from datetime import date, datetime, timezone, timedelta
from typing import Callable

from logic.scheduler_state import SchedulerState
from integrations.sigen_interaction import SigenInteraction
from logic.morning import handle_morning_period
from logic.afternoon import handle_afternoon_period
from logic.evening import handle_evening_period
from logic.period_handler_shared import PeriodHandlerContext
from logic.night import handle_night_window
from logic.scheduler_operations import (
    refresh_daily_data,
    fetch_soc,
    sample_live_solar_power,
    get_live_solar_average_kw,
    get_effective_battery_export_kw,
    estimate_solar,
    archive_inverter_telemetry,
)
from logic.schedule_utils import (
    suppress_elapsed_periods_except_latest,
    get_active_night_context,
)
from weather.providers.forecast_solar import archive_forecast_solar_snapshot
from telemetry.forecast_calibration import get_period_calibration
from config.enums import Period
from config.settings import SWITCHBOT_IMMERSION_ENABLED
from logic.immersion_control import check_immersion_boost
from integrations.zappi_auth import get_zappi_interaction
from telemetry.telemetry_archive import append_zappi_telemetry_snapshot
from config.settings import (
    POLL_INTERVAL_MINUTES,
    FORECAST_REFRESH_INTERVAL_MINUTES,
    FORECAST_SOLAR_ARCHIVE_ENABLED,
    FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES,
    FORECAST_SOLAR_RATE_LIMIT_COOLDOWN_MINUTES,
    MAX_PRE_PERIOD_WINDOW_MINUTES,
    NIGHT_MODE_ENABLED,
)


POLL_INTERVAL_SECONDS = POLL_INTERVAL_MINUTES * 60
FORECAST_REFRESH_INTERVAL_SECONDS = FORECAST_REFRESH_INTERVAL_MINUTES * 60
FORECAST_SOLAR_ARCHIVE_INTERVAL_SECONDS = FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES * 60
FORECAST_SOLAR_RATE_LIMIT_COOLDOWN_SECONDS = FORECAST_SOLAR_RATE_LIMIT_COOLDOWN_MINUTES * 60
MAX_PRE_PERIOD_WINDOW = timedelta(minutes=MAX_PRE_PERIOD_WINDOW_MINUTES)


class SchedulerCoordinator:
    """Orchestrates the main scheduling loop.

    Manages periodic state evaluation, forecast refreshes, and mode decisions
    across all daytime and night-time periods.
    """

    def __init__(
        self,
        state: SchedulerState,
        sigen: SigenInteraction | None,
        mode_names: dict[int, str],
        logger: logging.Logger,
    ):
        """Initialize the scheduler coordinator.

        Args:
            state: Mutable scheduler state tracking forecasts, windows, and decisions.
            sigen: Sigen inverter interaction handler, or None in simulation mode.
            mode_names: Mapping from mode values to human-readable labels.
            logger: Logger instance for diagnostic output.
        """
        self.state = state
        self.sigen = sigen
        self.mode_names = mode_names
        self.logger = logger
        # Populated by run_main_loop; valid only for its lifetime.
        self._sim_soc: float = 0.0
        self._apply_mode_change: Callable | None = None
        self._start_timed_export: Callable | None = None
        self._maybe_restore_export: Callable | None = None

    # --- Suppression helpers ---

    def _log_suppressed_periods(
        self, suppressed_periods: list[str], now: datetime, level: str = "info"
    ) -> None:
        """Log a suppression notice for stale elapsed daytime periods.

        Finds all elapsed periods from the ordered state, then emits one log line
        describing which were suppressed and which remains actionable.

        Args:
            suppressed_periods: Period names that were suppressed.
            now: Current tick time in UTC.
            level: Log level to use — ``"info"`` or ``"warning"``.
        """
        elapsed_periods = [
            p for p, p_start in self.state.ordered_period_windows if now >= p_start
        ]
        log_fn = getattr(self.logger, level)
        log_fn(
            "[SCHEDULER] Suppressing stale elapsed daytime periods%s: %s. "
            "Only the latest elapsed period remains actionable: %s.",
            " on startup/day refresh" if level == "info" else " on live tick",
            ", ".join(suppressed_periods),
            elapsed_periods[-1],
        )

    # --- Per-tick context wrappers ---

    async def _fetch_soc(self, period: str) -> float | None:
        """Delegate to module-level fetch_soc with scheduler context."""
        return await fetch_soc(self.state, period, self.sigen, self.logger, self._sim_soc)

    def _get_live_solar_avg(self) -> float | None:
        """Delegate to module-level get_live_solar_average_kw."""
        return get_live_solar_average_kw(self.state)

    def _get_effective_export(self, avg_live_solar_kw: float | None) -> float:
        """Delegate to module-level get_effective_battery_export_kw."""
        return get_effective_battery_export_kw(self.state, avg_live_solar_kw)

    async def _archive_telemetry(self, reason: str, now_utc: datetime) -> None:
        """Delegate to module-level archive_inverter_telemetry."""
        await archive_inverter_telemetry(self.state, reason, now_utc, self.sigen, self.logger)

    # --- Tick-level handlers ---

    async def _handle_auth_refresh(self, now: datetime, today: date) -> None:
        """Perform wake-time auth refresh if flagged and not yet done today."""
        if not (
            self.state.refresh_auth_on_wake
            and self.state.auth_refreshed_for_date != today
            and self.sigen is not None
        ):
            return
        try:
            from integrations.sigen_auth import refresh_sigen_instance
            self.logger.info("[SCHEDULER] Wake-time auth refresh: forcing full re-authentication.")
            refreshed_client = await refresh_sigen_instance()
            self.sigen = SigenInteraction.from_client(refreshed_client)
            self.state.auth_refreshed_for_date = today
            self.logger.info("[SCHEDULER] Wake-time auth refresh completed.")
        except Exception as exc:
            self.logger.warning("[SCHEDULER] Wake-time auth refresh failed: %s", exc)
        finally:
            self.state.refresh_auth_on_wake = False

    async def _handle_forecast_refresh(self, now: datetime, today: date) -> bool:
        """Refresh forecast data if needed. Returns True if the tick should be skipped."""
        if today != self.state.current_date:
            self.state.current_date = today
            try:
                await refresh_daily_data(self.state, self.logger, reset_day_state=True)
                suppressed_periods = suppress_elapsed_periods_except_latest(
                    now, self.state.today_period_windows, self.state.day_state,
                )
                if suppressed_periods:
                    self._log_suppressed_periods(suppressed_periods, now, level="info")
            except Exception as e:
                self.logger.error(f"[SCHEDULER] Failed to refresh daily data: {e}. Retrying next tick.")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                return True
        elif FORECAST_REFRESH_INTERVAL_SECONDS > 0 and (
            self.state.last_forecast_refresh_utc is None
            or (now - self.state.last_forecast_refresh_utc).total_seconds() >= FORECAST_REFRESH_INTERVAL_SECONDS
        ):
            try:
                self.logger.info(
                    "[SCHEDULER] Running intra-day forecast refresh (interval=%s minutes).",
                    FORECAST_REFRESH_INTERVAL_MINUTES,
                )
                await refresh_daily_data(self.state, self.logger, reset_day_state=False)
            except Exception as exc:
                self.logger.warning(
                    "[SCHEDULER] Intra-day forecast refresh failed: %s. Will retry next tick.", exc,
                )

        suppressed_periods = suppress_elapsed_periods_except_latest(
            now, self.state.today_period_windows, self.state.day_state,
        )
        if suppressed_periods:
            self._log_suppressed_periods(suppressed_periods, now, level="warning")
        return False

    def _handle_archive(self, now: datetime) -> None:
        """Archive a Forecast.Solar snapshot if the interval and rate-limit cooldown allow it."""
        if not (FORECAST_SOLAR_ARCHIVE_ENABLED and FORECAST_SOLAR_ARCHIVE_INTERVAL_SECONDS > 0):
            return
        should_archive = (
            (
                self.state.forecast_solar_archive_cooldown_until_utc is None
                or now >= self.state.forecast_solar_archive_cooldown_until_utc
            )
            and (
                self.state.last_forecast_solar_archive_utc is None
                or (now - self.state.last_forecast_solar_archive_utc).total_seconds()
                >= FORECAST_SOLAR_ARCHIVE_INTERVAL_SECONDS
            )
        )
        if not should_archive:
            return
        try:
            archive_forecast_solar_snapshot(self.logger, now)
            self.state.last_forecast_solar_archive_utc = now
            self.state.forecast_solar_archive_cooldown_until_utc = None
        except Exception as exc:
            if "429" in str(exc):
                cooldown_seconds = max(0, FORECAST_SOLAR_RATE_LIMIT_COOLDOWN_SECONDS)
                self.state.forecast_solar_archive_cooldown_until_utc = now + timedelta(seconds=cooldown_seconds)
                self.state.last_forecast_solar_archive_utc = now
                self.logger.warning(
                    "[SCHEDULER] Forecast.Solar rate-limited (429). Cooling down until %s.",
                    self.state.forecast_solar_archive_cooldown_until_utc.isoformat(),
                )
            else:
                self.logger.warning("[SCHEDULER] Forecast.Solar raw archive pull failed: %s", exc)

    async def _check_timed_export_active(self, now: datetime) -> bool:
        """Check timed-export state and finish the tick if an override is active.

        Logs, archives telemetry, and sleeps before returning True when the normal
        dispatch should be skipped this tick.
        """
        timed_export_status = await self._maybe_restore_export(now, self._get_active_period(now))
        if timed_export_status == "inactive":
            return False
        if timed_export_status == "active":
            self.logger.info(
                "[TIMED EXPORT] Override active until %s; skipping normal mode decisions this tick.",
                self.state.timed_export_override["restore_at"],
            )
        else:
            self.state.last_export_restore_at = now
            self.logger.info(
                "[TIMED EXPORT] Restore completed this tick; skipping normal mode decisions until next tick."
            )
        self.logger.info(
            "[SCHEDULER] Tick mode-change summary: attempted=%s successful=%s failed=%s",
            self.state.tick_mode_change_attempts,
            self.state.tick_mode_change_successes,
            self.state.tick_mode_change_failures,
        )
        self.logger.info(
            f"[SCHEDULER] Tick at {now.isoformat()} UTC complete. "
            f"Next check in {POLL_INTERVAL_SECONDS // 60} minutes."
        )
        await self._archive_telemetry("scheduler_tick", now)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
        return True

    def _get_active_period(self, now: datetime) -> str | None:
        """Return the name of the currently active daytime period, or None.

        Walks ordered_period_windows in reverse and returns the first period
        whose start time has elapsed.
        """
        active = None
        for period, start in self.state.ordered_period_windows:
            if now >= start:
                active = period
        return active

    async def _fetch_zappi_status(self, now: datetime) -> None:
        """Fetch Zappi live status and today's daily totals; archive a snapshot."""
        zappi = get_zappi_interaction()
        if zappi is None:
            return
        try:
            status = await zappi.get_live_status()
            if status is not None:
                self.state.latest_zappi_status = status
                append_zappi_telemetry_snapshot(
                    live_status=status,
                    scheduler_now_utc=now,
                )
        except Exception as exc:
            self.logger.warning("[ZAPPI] Failed to fetch Zappi live status: %s", exc)
        try:
            from datetime import timezone as _tz
            from zoneinfo import ZoneInfo
            from config.settings import LOCAL_TIMEZONE
            local_today = now.astimezone(ZoneInfo(LOCAL_TIMEZONE)).date()
            daily = await zappi.get_daily_totals(local_today)
            if daily is not None:
                self.state.latest_zappi_daily = daily
        except Exception as exc:
            self.logger.warning("[ZAPPI] Failed to fetch Zappi daily totals: %s", exc)

    async def _check_immersion_boost(self, now: datetime, today: date) -> None:
        """Evaluate immersion heater boost conditions and act if appropriate."""
        if not SWITCHBOT_IMMERSION_ENABLED:
            return
        from zoneinfo import ZoneInfo
        from config.settings import LOCAL_TIMEZONE
        today_local = now.astimezone(ZoneInfo(LOCAL_TIMEZONE)).date()
        await check_immersion_boost(
            immersion_state=self.state.immersion_state,
            now_utc=now,
            today_local=today_local,
            soc_percent=self.state.last_known_soc,
            live_solar_avg_kw=self._get_live_solar_avg(),
            active_period=self._get_active_period(now),
            logger=self.logger,
        )

    async def _process_period_windows(self, now: datetime, today: date) -> None:
        """Evaluate the night window or each daytime period handler for this tick."""
        if (
            self.state.night_state["mode_set_key"] is not None
            and self.state.night_state["mode_set_key"][0] < today
        ):
            self.state.night_state["mode_set_key"] = None

        night_context = get_active_night_context(
            now,
            self.state.today_period_windows,
            self.state.today_period_forecast,
            self.state.tomorrow_period_windows,
            self.state.tomorrow_period_forecast,
            self.state.today_sunset_utc,
            MAX_PRE_PERIOD_WINDOW,
        )
        night_tick_consumed = False
        if NIGHT_MODE_ENABLED and night_context is not None:
            night_period_solar_kwh = estimate_solar(
                self.state, night_context["target_period"], night_context["solar_value"]
            )
            night_result = await handle_night_window(
                now_utc=now,
                night_context=night_context,
                night_state=self.state.night_state,
                period_solar_kwh=night_period_solar_kwh,
                fetch_soc=self._fetch_soc,
                start_timed_grid_export=self._start_timed_export,
                apply_mode_change=self._apply_mode_change,
                archive_inverter_telemetry=self._archive_telemetry,
                sigen=self.sigen,
                mode_names=self.mode_names,
            )
            if night_result["sleep_seconds"] is not None:
                self.state.sleep_override_seconds = night_result["sleep_seconds"]
            if night_result["refresh_auth_on_wake"]:
                self.state.refresh_auth_on_wake = True
            night_tick_consumed = True
            self.logger.info("[SCHEDULER] Night window active; skipping daytime period evaluation this tick.")

        if not night_tick_consumed:
            for period_index, (period, period_start) in enumerate(self.state.ordered_period_windows):
                solar_value, status = self.state.today_period_forecast[period]
                period_end_utc = (
                    self.state.ordered_period_windows[period_index + 1][1]
                    if period_index + 1 < len(self.state.ordered_period_windows)
                    else self.state.today_sunset_utc
                )
                ctx = PeriodHandlerContext(
                    now_utc=now,
                    period_start=period_start,
                    period_end_utc=period_end_utc,
                    period_state=self.state.day_state[period],
                    timed_export_override=self.state.timed_export_override,
                    solar_value=solar_value,
                    status=status,
                    period_solar_kwh=estimate_solar(self.state, period, solar_value),
                    period_calibration=get_period_calibration(self.state.forecast_calibration, period),
                    fetch_soc=self._fetch_soc,
                    get_live_solar_average_kw=self._get_live_solar_avg,
                    get_effective_battery_export_kw=self._get_effective_export,
                    start_timed_grid_export=self._start_timed_export,
                    apply_mode_change=self._apply_mode_change,
                    sigen=self.sigen,
                    mode_names=self.mode_names,
                )
                if period == Period.MORN:
                    await handle_morning_period(ctx)
                elif period == Period.AFTN:
                    await handle_afternoon_period(ctx)
                elif period == Period.EVE:
                    await handle_evening_period(ctx)

    async def run_main_loop(
        self,
        simulated_soc_percent: float,
        _apply_mode_change_tracked: Callable,
        start_timed_grid_export: Callable,
        maybe_restore_timed_grid_export: Callable,
    ) -> None:
        """Main scheduling loop (while True).

        Runs continuously on each POLL_INTERVAL_MINUTES tick, refreshing
        forecasts daily, sampling solar power, and making mode decisions
        for each active period.

        Args:
            simulated_soc_percent: Simulated battery SOC when not using real inverter.
            _apply_mode_change_tracked: Callback to apply and track mode changes.
            start_timed_grid_export: Callback to initiate timed export.
            maybe_restore_timed_grid_export: Callback to restore/check timed export state.
        """
        self._sim_soc = simulated_soc_percent
        self._apply_mode_change = _apply_mode_change_tracked
        self._start_timed_export = start_timed_grid_export
        self._maybe_restore_export = maybe_restore_timed_grid_export

        while True:
            now = datetime.now(timezone.utc)
            today = now.date()
            self.state.sleep_override_seconds = None
            self.state.tick_mode_change_attempts = 0
            self.state.tick_mode_change_successes = 0
            self.state.tick_mode_change_failures = 0

            await self._handle_auth_refresh(now, today)

            if await self._handle_forecast_refresh(now, today):
                continue

            self._handle_archive(now)
            await sample_live_solar_power(self.state, now, self.sigen, self.logger)
            await self._fetch_zappi_status(now)

            if await self._check_timed_export_active(now):
                await self._check_immersion_boost(now, today)
                continue

            await self._process_period_windows(now, today)
            await self._check_immersion_boost(now, today)

            self.logger.info(
                "[SCHEDULER] Tick mode-change summary: attempted=%s successful=%s failed=%s",
                self.state.tick_mode_change_attempts,
                self.state.tick_mode_change_successes,
                self.state.tick_mode_change_failures,
            )
            next_sleep_seconds = self.state.sleep_override_seconds or POLL_INTERVAL_SECONDS
            self.logger.info(
                f"[SCHEDULER] Tick at {now.isoformat()} UTC complete. "
                f"Next check in {next_sleep_seconds // 60} minutes."
            )
            await self._archive_telemetry("scheduler_tick", now)
            await asyncio.sleep(next_sleep_seconds)

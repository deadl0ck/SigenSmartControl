
"""Main scheduler loop for coordinating Sigen inverter mode decisions.

The scheduler continuously monitors solar forecasts, battery state, and tariff windows,
making operational mode decisions that optimize between self-powered generation,
grid arbitrage, and cost-minimization based on real-time conditions.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any

from config.settings import (
    LOG_LEVEL as CONFIG_LOG_LEVEL,
    SIGEN_MODE_NAMES,
    SIGEN_MODES,
    FULL_SIMULATION_MODE,
    POLL_INTERVAL_MINUTES,
    FORECAST_REFRESH_INTERVAL_MINUTES,
    FORECAST_SOLAR_ARCHIVE_ENABLED,
    FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES,
    FORECAST_SOLAR_RATE_LIMIT_COOLDOWN_MINUTES,
    MAX_PRE_PERIOD_WINDOW_MINUTES,
    HEADROOM_TARGET_KWH,
    SOLAR_PV_KW,
    INVERTER_KW,
    BATTERY_KWH,
    DEFAULT_SIMULATED_SOC_PERCENT,
    TIMED_EXPORT_RESTORE_COOLDOWN_MINUTES,
)
from integrations.sigen_interaction import SigenInteraction, SigenPayloadError
from integrations.sigen_auth import refresh_sigen_instance
from logic.scheduler_state import SchedulerState
from logic.scheduler_coordinator import SchedulerCoordinator
from logic.mode_change import apply_mode_change as _apply_mode_change_core
from logic.mode_control import ACTION_DIVIDER
from logic.mode_logging import log_mode_status
from logic.timed_export import (
    load_timed_export_override,
    persist_timed_export_override,
    start_timed_grid_export as start_timed_grid_export_helper,
    maybe_restore_timed_grid_export as maybe_restore_timed_grid_export_helper,
)
from logic.schedule_utils import (
    suppress_elapsed_periods_except_latest,
    get_hours_until_cheap_rate,
    order_daytime_periods,
)
from config.constants import (
    TIMED_EXPORT_STATE_PATH,
)
from telemetry.forecast_calibration import build_and_save_forecast_calibration
from notifications.notification_email_helpers import (
    notify_startup_email,
)
from utils.logging_formatters import LevelColorFormatter
from utils.payload_tree import log_payload_tree
from utils.sensitive_values import mask_sensitive_value


# --- Logging configuration ---
LOG_LEVEL = getattr(logging, CONFIG_LOG_LEVEL, logging.INFO)
_log_handler = logging.StreamHandler()
_log_handler.setFormatter(
    LevelColorFormatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
logging.basicConfig(level=LOG_LEVEL, handlers=[_log_handler], force=True)
logger = logging.getLogger("sigen_control")


POLL_INTERVAL_SECONDS = POLL_INTERVAL_MINUTES * 60


async def log_current_mode_on_startup(
    sigen: SigenInteraction,
    mode_names: dict[int, str],
) -> tuple[Any, float | None, float | None]:
    """Log retrievable startup data and return current mode/SOC snapshot.

    Args:
        sigen: SigenInteraction instance for API calls.
        mode_names: Mapping from numeric mode to human-readable label.

    Returns:
        Tuple of (current_mode_raw, battery_soc, solar_generated_today_kwh).
    """
    from telemetry.telemetry_archive import extract_today_solar_generation_kwh

    logger.info(ACTION_DIVIDER)
    logger.info("STARTUP CHECK: fetching retrievable inverter data")

    current_mode_raw: Any = None
    battery_soc: float | None = None
    solar_generated_today_kwh: float | None = None

    try:
        current_mode_raw = await sigen.get_operational_mode()
        log_mode_status("startup pull", current_mode_raw, mode_names)
    except Exception as e:
        logger.error("Failed to fetch current inverter mode on startup: %s", e)

    try:
        energy_flow = await sigen.get_energy_flow()
        if isinstance(energy_flow, dict):
            soc_value = energy_flow.get("batterySoc")
            if isinstance(soc_value, (int, float)):
                battery_soc = float(soc_value)
            solar_generated_today_kwh = extract_today_solar_generation_kwh(energy_flow)
        log_payload_tree(logger, "Startup energy flow payload", energy_flow)
    except SigenPayloadError as e:
        logger.error("Inverter returned unexpected payload on startup energy flow fetch: %s", e)
    except Exception as e:
        logger.error("Failed to fetch energy flow payload on startup: %s", e)

    try:
        operational_modes = await sigen.get_operational_modes()
        log_payload_tree(
            logger,
            "Startup supported operational modes payload",
            operational_modes,
        )
    except Exception as e:
        logger.error("Failed to fetch supported operational modes on startup: %s", e)

    logger.info(ACTION_DIVIDER)
    return current_mode_raw, battery_soc, solar_generated_today_kwh


async def create_scheduler_interaction(mode_names: dict[int, str]) -> SigenInteraction | None:
    """Create and validate the Sigen API interaction wrapper.

    Attempts to initialize API connection and logs current inverter mode on startup.
    Retries authentication twice on failure (three total attempts). If all attempts
    fail, exits the process with a non-zero status.

    Args:
        mode_names: Mapping from numeric mode to human-readable label.

    Returns:
        SigenInteraction instance if successful.

    Raises:
        SystemExit: If authentication fails after all retry attempts.
    """
    from weather.forecast import create_solar_forecast_provider

    max_attempts = 3  # initial attempt + two retries
    retry_delay_seconds = 2

    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(
                "[SCHEDULER] Initializing inverter interaction (attempt %s/%s).",
                attempt,
                max_attempts,
            )
            sigen = await SigenInteraction.create()
            logger.info(
                "[SCHEDULER] Inverter interaction created successfully: %s",
                type(sigen).__name__,
            )
            startup_today_period_forecast: dict[str, tuple[int, str]] | None = None
            try:
                startup_forecast_provider = create_solar_forecast_provider(logger)
                startup_today_period_forecast = startup_forecast_provider.get_todays_period_forecast()
            except Exception as exc:
                logger.warning(
                    "[SCHEDULER] Could not fetch today's forecast for startup email: %s",
                    exc,
                )
            startup_mode_raw, startup_soc, startup_solar_today_kwh = await log_current_mode_on_startup(sigen, mode_names)
            await notify_startup_email(
                current_mode_raw=startup_mode_raw,
                battery_soc=startup_soc,
                solar_generated_today_kwh=startup_solar_today_kwh,
                today_period_forecast=startup_today_period_forecast,
                mode_names=mode_names,
                event_time_utc=datetime.now(timezone.utc),
                logger=logger,
            )
            return sigen
        except Exception as e:
            logger.warning(
                "[SCHEDULER] Inverter authentication/initialization failed on attempt %s/%s. "
                "FULL_SIMULATION_MODE=%s. Reason: %r",
                attempt,
                max_attempts,
                FULL_SIMULATION_MODE,
                e,
            )
            if attempt < max_attempts:
                logger.warning(
                    "[SCHEDULER] Retrying inverter authentication in %s seconds...",
                    retry_delay_seconds,
                )
                await asyncio.sleep(retry_delay_seconds)

    logger.error(
        "[SCHEDULER] Unable to authenticate with inverter after %s attempts. "
        "Exiting process.",
        max_attempts,
    )
    raise SystemExit(1)


async def run_scheduler() -> None:
    """Entry point for the scheduler loop.

    Initializes all scheduler components and delegates to SchedulerCoordinator.run_main_loop().
    """
    relevant_env_vars = [
        "SIGEN_USERNAME",
        "SIGEN_PASSWORD",
        "SIGEN_LATITUDE",
        "SIGEN_LONGITUDE",
        "SIMULATED_SOC_PERCENT",
    ]
    logger.info("[SCHEDULER] Environment:")
    for k in relevant_env_vars:
        v = os.getenv(k)
        logger.info(
            f"[SCHEDULER]   {k} = {mask_sensitive_value(v, k) if v else '[NOT SET]'}"
        )
    logger.info(
        f"[SCHEDULER] System specs: Solar PV={SOLAR_PV_KW} kW, "
        f"Inverter={INVERTER_KW} kW, Battery={BATTERY_KWH} kWh"
    )
    logger.info(
        "[SCHEDULER] Telemetry grid exchange parsing: prefer buySellPower, then "
        "gridExportPower/feedInPower/exportPower/netGridPower/gridPower. "
        "Sign convention: positive=export, negative=import."
    )

    simulated_soc_raw = os.getenv("SIMULATED_SOC_PERCENT", str(DEFAULT_SIMULATED_SOC_PERCENT))
    try:
        simulated_soc_percent = float(simulated_soc_raw)
    except ValueError:
        logger.warning(
            "[SCHEDULER] Invalid SIMULATED_SOC_PERCENT='%s'. Falling back to %.1f%%.",
            simulated_soc_raw,
            DEFAULT_SIMULATED_SOC_PERCENT,
        )
        simulated_soc_percent = DEFAULT_SIMULATED_SOC_PERCENT

    mode_names = SIGEN_MODE_NAMES

    sigen = await create_scheduler_interaction(mode_names)
    now = datetime.now(timezone.utc)
    state = SchedulerState(
        current_date=now.date(),
        forecast_calibration=build_and_save_forecast_calibration(),
        timed_export_override=load_timed_export_override(logger=logger),
    )

    async def _apply_mode_change_tracked(
        *,
        sigen: SigenInteraction | None,
        mode: int,
        period: str,
        reason: str,
        mode_names: dict[int, str],
        export_duration_minutes: int | None = None,
        battery_soc: float | None = None,
        today_period_forecast: dict[str, tuple[int, str]] | None = None,
    ) -> bool:
        """Apply mode change and record per-tick counters.

        Wraps apply_mode_change to track successful and failed mode-change attempts
        per scheduler tick. Defaults today_period_forecast from state when not supplied.

        Args:
            sigen: SigenInteraction instance, or None in dry-run mode.
            mode: Target numeric mode value.
            period: Human-readable period/context label for logging.
            reason: Explanation of why this mode change is being made.
            mode_names: Mapping from numeric mode to human-readable label.
            export_duration_minutes: Optional override window when forcing GRID_EXPORT.
            battery_soc: Battery state of charge at the time of the command, when known.
            today_period_forecast: Daytime period forecast snapshot for today.

        Returns:
            True if mode was set or already at target, False if set operation failed.
        """
        resolved_forecast = today_period_forecast or state.today_period_forecast
        state.tick_mode_change_attempts += 1
        ok = await _apply_mode_change_core(
            sigen=sigen,
            mode=mode,
            period=period,
            reason=reason,
            mode_names=mode_names,
            logger=logger,
            export_duration_minutes=export_duration_minutes,
            battery_soc=battery_soc,
            today_period_forecast=resolved_forecast,
            zappi_status=state.latest_zappi_status,
            zappi_daily=state.latest_zappi_daily,
        )
        if ok:
            state.tick_mode_change_successes += 1
        else:
            state.tick_mode_change_failures += 1
        return ok

    def _update_timed_export_override(new_state: dict[str, Any]) -> None:
        """Update in-memory timed export override and persist it to disk."""
        state.timed_export_override = new_state
        persist_timed_export_override(new_state, logger=logger)

    async def start_timed_grid_export(
        *,
        period: str,
        reason: str,
        duration_minutes: int,
        now_utc: datetime,
        battery_soc: float | None = None,
        is_clipping_export: bool = False,
        export_soc_floor: float | None = None,
    ) -> bool:
        """Delegate timed GRID_EXPORT activation to the timed-export module."""
        return await start_timed_grid_export_helper(
            timed_export_override=state.timed_export_override,
            set_timed_export_override=_update_timed_export_override,
            period=period,
            reason=reason,
            duration_minutes=duration_minutes,
            now_utc=now_utc,
            battery_soc=battery_soc,
            is_clipping_export=is_clipping_export,
            export_soc_floor=export_soc_floor,
            last_export_restore_at=state.last_export_restore_at,
            restore_cooldown_minutes=TIMED_EXPORT_RESTORE_COOLDOWN_MINUTES,
            sigen=sigen,
            mode_names=mode_names,
            apply_mode_change=_apply_mode_change_tracked,
            logger=logger,
            log_mode_status=log_mode_status,
        )

    forecast_refresh_interval_seconds = FORECAST_REFRESH_INTERVAL_MINUTES * 60

    logger.info(
        f"[SCHEDULER] Starting. Will poll every {POLL_INTERVAL_MINUTES} minutes. "
        f"Max pre-period window: {MAX_PRE_PERIOD_WINDOW_MINUTES} minutes. "
        f"Headroom target: {HEADROOM_TARGET_KWH:.1f} kWh (surplus capacity × 3 h)."
    )
    if forecast_refresh_interval_seconds > 0:
        logger.info(
            "[SCHEDULER] Intra-day forecast refresh enabled every %s minutes.",
            FORECAST_REFRESH_INTERVAL_MINUTES,
        )
    else:
        logger.info("[SCHEDULER] Intra-day forecast refresh disabled.")

    if FORECAST_SOLAR_ARCHIVE_ENABLED and (FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES * 60) > 0:
        logger.info(
            "[SCHEDULER] Forecast.Solar raw archiving enabled every %s minutes.",
            FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES,
        )
    else:
        logger.info("[SCHEDULER] Forecast.Solar raw archiving disabled.")

    coordinator = SchedulerCoordinator(state, sigen, mode_names, logger)

    async def maybe_restore_timed_grid_export(now_utc: datetime) -> str:
        """Delegate timed-export restore handling to the timed-export module."""
        return await maybe_restore_timed_grid_export_helper(
            timed_export_override=state.timed_export_override,
            set_timed_export_override=_update_timed_export_override,
            now_utc=now_utc,
            fetch_soc=coordinator._fetch_soc,
            sigen=sigen,
            mode_names=mode_names,
            apply_mode_change=_apply_mode_change_tracked,
            logger=logger,
        )

    await coordinator.run_main_loop(
        simulated_soc_percent=simulated_soc_percent,
        _apply_mode_change_tracked=_apply_mode_change_tracked,
        start_timed_grid_export=start_timed_grid_export,
        maybe_restore_timed_grid_export=maybe_restore_timed_grid_export,
    )


if __name__ == "__main__":
    asyncio.run(run_scheduler())

"""inverter_control.py
---------------------
Inverter interaction control helpers used by the scheduler.

This module centralizes command/write behavior and live-solar sampling helpers
that interact with inverter payloads, keeping main.py focused on orchestration.
"""

from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
import logging
from typing import Any

from config.settings import SIGEN_MODES
from logic.mode_control import ACTION_DIVIDER, mode_matches_target
from logic.mode_logging import log_mode_status
from telemetry.telemetry_archive import (
    extract_live_solar_power_kw,
    extract_today_solar_generation_kwh,
)


ModeChangeNotifier = Callable[..., Awaitable[None]]
ArchiveDecision = Callable[[], bool]
ModeChangeArchiveAppender = Callable[..., None]


async def apply_mode_change(
    *,
    sigen: Any | None,
    mode: int,
    period: str,
    reason: str,
    mode_names: dict[int, str],
    logger: logging.Logger,
    notify_mode_change_email: ModeChangeNotifier,
    should_archive_mode_change_events: ArchiveDecision,
    append_mode_change_event: ModeChangeArchiveAppender,
    full_simulation_mode: bool,
    export_duration_minutes: int | None = None,
    battery_soc: float | None = None,
    today_period_forecast: dict[str, tuple[int, str]] | None = None,
) -> bool:
    """Attempt to change inverter mode with idempotency and side effects.

    Reads current mode before writing; skips write if already at target.
    On write attempts, archives mode-change events and emits notification emails.

    Args:
        sigen: Sigen interaction instance, or None in dry-run paths.
        mode: Target numeric mode value.
        period: Human-readable period/context label for logging.
        reason: Explanation of why this mode change is being made.
        mode_names: Mapping from numeric mode to human-readable label.
        logger: Logger used for control-path messages.
        notify_mode_change_email: Async callback for notification dispatch.
        should_archive_mode_change_events: Callback indicating archive policy.
        append_mode_change_event: Callback that writes mode-change events.
        full_simulation_mode: Whether simulation mode is active.
        export_duration_minutes: Optional override duration for GRID_EXPORT mode.
        battery_soc: Battery state of charge at command time, when known.
        today_period_forecast: Daytime period forecast snapshot for today.

    Returns:
        True if mode was set or already at target, False otherwise.
    """
    mode_label = mode_names.get(mode, mode)
    current_mode_raw: Any = None
    solar_generated_today_kwh: float | None = None

    if sigen is None:
        if full_simulation_mode:
            event_time = datetime.now(timezone.utc)
            simulated_response = {
                "simulated": True,
                "mode": mode,
                "note": "Sigen interaction unavailable; simulated fallback path.",
            }
            logger.info(ACTION_DIVIDER)
            logger.info(ACTION_DIVIDER)
            logger.info(
                "[SIMULATION] set_operational_mode(mode=%s, value=%s) "
                "- command suppressed in simulation mode",
                mode_label,
                mode,
            )
            logger.info("[SIMULATION] Context=%s | reason=%s", period, reason)
            logger.info(ACTION_DIVIDER)
            logger.info(ACTION_DIVIDER)
            if should_archive_mode_change_events():
                append_mode_change_event(
                    scheduler_now_utc=event_time,
                    period=period,
                    requested_mode=mode,
                    requested_mode_label=str(mode_label),
                    reason=reason,
                    simulated=True,
                    success=True,
                    current_mode=None,
                    response=simulated_response,
                )
            await notify_mode_change_email(
                success=True,
                period=period,
                reason=reason,
                requested_mode=mode,
                requested_mode_label=str(mode_label),
                current_mode_raw=None,
                mode_names=mode_names,
                event_time_utc=event_time,
                battery_soc=battery_soc,
                solar_generated_today_kwh=solar_generated_today_kwh,
                today_period_forecast=today_period_forecast,
                response=simulated_response,
            )
            return True

        logger.error("Cannot set mode for %s: Sigen interaction is unavailable.", period)
        return False

    try:
        current_mode_raw = await sigen.get_operational_mode()
        log_mode_status(f"pre-change pull ({period})", current_mode_raw, mode_names)
        if mode_matches_target(current_mode_raw, mode, mode_names):
            logger.info(ACTION_DIVIDER)
            logger.info("Skipping inverter set_operational_mode (already at target mode)")
            logger.info("Target period/context: %s", period)
            logger.info("Target mode: %s (value=%s)", mode_label, mode)
            logger.info("Decision reason: %s", reason)
            logger.info(ACTION_DIVIDER)
            return True
    except Exception as exc:
        logger.warning(
            "Could not read current inverter mode before setting %s for %s: %s. "
            "Proceeding with mode set attempt.",
            mode_label,
            period,
            exc,
        )

    try:
        energy_flow_for_email = await sigen.get_energy_flow()
        if isinstance(energy_flow_for_email, dict):
            if battery_soc is None:
                soc_value = energy_flow_for_email.get("batterySoc")
                if isinstance(soc_value, (int, float)):
                    battery_soc = float(soc_value)
            solar_generated_today_kwh = extract_today_solar_generation_kwh(energy_flow_for_email)
    except Exception as exc:
        logger.debug(
            "Could not read energy flow before mode-change email for %s: %s",
            period,
            exc,
        )

    logger.info(ACTION_DIVIDER)
    logger.info("Calling inverter set_operational_mode")
    logger.info("Target period/context: %s", period)
    logger.info("Target mode: %s (value=%s)", mode_label, mode)
    logger.info("Decision reason: %s", reason)
    logger.info(ACTION_DIVIDER)

    event_time = datetime.now(timezone.utc)
    try:
        if mode == SIGEN_MODES["GRID_EXPORT"] and export_duration_minutes is not None:
            response = await sigen.export_to_grid(export_duration_minutes)
        else:
            response = await sigen.set_operational_mode(mode)

        logger.info("Set mode response for %s: %s", period, response)
        if should_archive_mode_change_events():
            append_mode_change_event(
                scheduler_now_utc=event_time,
                period=period,
                requested_mode=mode,
                requested_mode_label=str(mode_label),
                reason=reason,
                simulated=full_simulation_mode,
                success=True,
                current_mode=current_mode_raw,
                response=response,
            )

        logger.info(
            "[EMAIL] Queueing mode-change notification: status=SUCCESS period=%s target=%s(%s) "
            "simulated=%s",
            period,
            mode_label,
            mode,
            full_simulation_mode,
        )
        await notify_mode_change_email(
            success=True,
            period=period,
            reason=reason,
            requested_mode=mode,
            requested_mode_label=str(mode_label),
            current_mode_raw=current_mode_raw,
            mode_names=mode_names,
            event_time_utc=event_time,
            battery_soc=battery_soc,
            solar_generated_today_kwh=solar_generated_today_kwh,
            today_period_forecast=today_period_forecast,
            response=response,
        )
        return True
    except Exception as exc:
        logger.error("Failed to set mode for %s: %s", period, exc)
        if should_archive_mode_change_events():
            append_mode_change_event(
                scheduler_now_utc=event_time,
                period=period,
                requested_mode=mode,
                requested_mode_label=str(mode_label),
                reason=reason,
                simulated=full_simulation_mode,
                success=False,
                current_mode=current_mode_raw,
                error=str(exc),
            )
        logger.info(
            "[EMAIL] Queueing mode-change notification: status=FAILED period=%s target=%s(%s) "
            "simulated=%s",
            period,
            mode_label,
            mode,
            full_simulation_mode,
        )
        await notify_mode_change_email(
            success=False,
            period=period,
            reason=reason,
            requested_mode=mode,
            requested_mode_label=str(mode_label),
            current_mode_raw=current_mode_raw,
            mode_names=mode_names,
            event_time_utc=event_time,
            battery_soc=battery_soc,
            solar_generated_today_kwh=solar_generated_today_kwh,
            today_period_forecast=today_period_forecast,
            error=str(exc),
        )
        return False


async def sample_live_solar_power(
    *,
    now_utc: datetime,
    sigen: Any | None,
    live_solar_kw_samples: deque[float],
    live_solar_average_sample_count: int,
    logger: logging.Logger,
) -> None:
    """Capture one live solar reading for rolling export-capacity calculations.

    Args:
        now_utc: Current scheduler timestamp in UTC.
        sigen: Sigen interaction instance, or None in dry-run mode.
        live_solar_kw_samples: Rolling deque for live solar samples in kW.
        live_solar_average_sample_count: Configured sample count for logging context.
        logger: Logger for status messages.
    """
    _ = now_utc
    if sigen is None:
        return
    try:
        energy_flow = await sigen.get_energy_flow()
        solar_kw = extract_live_solar_power_kw(energy_flow)
        if solar_kw is not None:
            live_solar_kw_samples.append(max(0.0, solar_kw))
            logger.info(
                "[SCHEDULER] Live solar sample: %.2f kW (%s/%s samples)",
                solar_kw,
                len(live_solar_kw_samples),
                live_solar_average_sample_count,
            )
    except Exception as exc:
        logger.warning("[SCHEDULER] Failed to sample live solar power: %s", exc)


def get_live_solar_average_kw(live_solar_kw_samples: deque[float]) -> float | None:
    """Return rolling average live solar generation across recent samples.

    Args:
        live_solar_kw_samples: Rolling deque of sampled live solar kW values.

    Returns:
        Average kW when at least one sample is present, else None.
    """
    if not live_solar_kw_samples:
        return None
    return sum(live_solar_kw_samples) / len(live_solar_kw_samples)


def get_effective_battery_export_kw(
    avg_live_solar_kw: float | None,
    *,
    inverter_kw: float,
    min_effective_battery_export_kw: float,
) -> float:
    """Estimate battery export capacity after accounting for live solar occupancy.

    Args:
        avg_live_solar_kw: Rolling average live solar generation in kW.
        inverter_kw: Inverter export capacity limit in kW.
        min_effective_battery_export_kw: Lower bound for effective export kW.

    Returns:
        Effective kW available for battery-driven export/discharge.
    """
    if avg_live_solar_kw is None:
        return inverter_kw
    available_kw = inverter_kw - max(0.0, avg_live_solar_kw)
    return min(inverter_kw, max(min_effective_battery_export_kw, available_kw))
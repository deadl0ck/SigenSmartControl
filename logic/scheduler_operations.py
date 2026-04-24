"""Core scheduler operations: forecast refresh, sunrise/sunset updates, and period derivation.

Handles periodic refresh of solar forecasts and sunrise/sunset times, updating
the scheduler state with derived period windows and forecast confidence data.
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from config.constants import LATITUDE, LONGITUDE
from config.settings import (
    SOLAR_PV_KW,
    INVERTER_KW,
    MIN_EFFECTIVE_BATTERY_EXPORT_KW,
    LIVE_SOLAR_AVERAGE_SAMPLE_COUNT,
)
from weather.forecast import (
    SolarForecastProvider,
    create_solar_forecast_provider,
)
from weather.sunrise_sunset import get_sunrise_sunset
from integrations.sigen_interaction import SigenInteraction, SigenPayloadError
from logic.schedule_utils import (
    _parse_utc,
    derive_period_windows,
    order_daytime_periods,
)
from logic.inverter_control import (
    sample_live_solar_power as sample_live_solar_power_control,
    get_live_solar_average_kw as get_live_solar_average_kw_control,
    get_effective_battery_export_kw as get_effective_battery_export_kw_control,
)
from telemetry.forecast_calibration import (
    build_and_save_forecast_calibration,
    get_period_calibration,
)
from telemetry.telemetry_archive import append_inverter_telemetry_snapshot
from logic.scheduler_state import SchedulerState


async def refresh_daily_data(
    state: SchedulerState,
    logger: logging.Logger,
    *,
    reset_day_state: bool = True,
) -> None:
    """Fetch and cache solar forecast and sunrise/sunset times for today and tomorrow.

    Called at day start and optionally intra-day to refresh period windows,
    forecasts, and sunrise/sunset times used throughout the scheduling loop.

    Args:
        state: Scheduler state object to update with refreshed data.
        logger: Logger instance for diagnostic output.
        reset_day_state: When True, resets per-period pre/start action flags for a
            new day. When False, preserves existing period action state.
    """
    logger.info("[SCHEDULER] Refreshing daily forecast and sunrise/sunset data.")
    state.forecast_calibration = build_and_save_forecast_calibration()
    forecast_obj: SolarForecastProvider = create_solar_forecast_provider(logger)
    state.today_period_forecast = forecast_obj.get_todays_period_forecast()
    state.tomorrow_period_forecast = forecast_obj.get_tomorrows_period_forecast()
    logger.info(f"[SCHEDULER] Today's forecast: {state.today_period_forecast}")
    logger.info(f"[SCHEDULER] Tomorrow's forecast: {state.tomorrow_period_forecast}")

    if state.current_date is None:
        raise RuntimeError("Current scheduler date was not initialized before refresh.")

    tomorrow_date = state.current_date + timedelta(days=1)

    sunrise_str, sunset_str = get_sunrise_sunset(
        LATITUDE, LONGITUDE, state.current_date.isoformat()
    )
    tomorrow_sunrise_str, tomorrow_sunset_str = get_sunrise_sunset(
        LATITUDE,
        LONGITUDE,
        tomorrow_date.isoformat(),
    )
    sunrise_utc = _parse_utc(sunrise_str)
    sunset_utc = _parse_utc(sunset_str)
    tomorrow_sunrise = _parse_utc(tomorrow_sunrise_str)
    tomorrow_sunset = _parse_utc(tomorrow_sunset_str)
    state.today_sunrise_utc = sunrise_utc
    state.today_sunset_utc = sunset_utc
    state.tomorrow_sunrise_utc = tomorrow_sunrise
    logger.info(
        f"[SCHEDULER] Sunrise: {sunrise_utc.isoformat()}  Sunset: {sunset_utc.isoformat()}"
    )
    logger.info(f"[SCHEDULER] Tomorrow sunrise: {tomorrow_sunrise.isoformat()}")

    daytime_periods = order_daytime_periods(state.today_period_forecast)
    tomorrow_daytime_periods = order_daytime_periods(state.tomorrow_period_forecast)
    state.today_period_windows = derive_period_windows(sunrise_utc, sunset_utc, daytime_periods)
    state.tomorrow_period_windows = derive_period_windows(
        tomorrow_sunrise,
        tomorrow_sunset,
        tomorrow_daytime_periods,
    )
    logger.info("[SCHEDULER] Ordered daytime periods today: %s", daytime_periods)
    logger.info("[SCHEDULER] Ordered daytime periods tomorrow: %s", tomorrow_daytime_periods)
    for period, start in state.today_period_windows.items():
        logger.info(f"[SCHEDULER] Period '{period}' starts at {start.isoformat()} UTC")
    for period, start in state.tomorrow_period_windows.items():
        logger.info(f"[SCHEDULER] Tomorrow period '{period}' starts at {start.isoformat()} UTC")

    if reset_day_state:
        state.day_state = {
            p: {"pre_set": False, "start_set": False, "clipping_export_set": False}
            for p in daytime_periods
        }
    else:
        for period in daytime_periods:
            state.day_state.setdefault(
                period, {"pre_set": False, "start_set": False, "clipping_export_set": False}
            )

    state.last_forecast_refresh_utc = datetime.now(timezone.utc)


async def fetch_soc(
    state: SchedulerState,
    period: str,
    sigen: SigenInteraction | None,
    logger: logging.Logger,
    simulated_soc_percent: float,
) -> float | None:
    """Fetch current battery state-of-charge from inverter or use simulated value.

    Args:
        state: Scheduler state object (unused, kept for consistency).
        period: Human-readable period/context label for logging.
        sigen: Sigen inverter interaction client, or None in dry-run mode.
        logger: Logger instance for diagnostic output.
        simulated_soc_percent: Simulated SOC percentage to use when inverter unavailable.

    Returns:
        Battery SOC percentage (0-100), or None if fetch fails.
    """
    if sigen is None:
        logger.info(
            f"[{period}] SOC: {simulated_soc_percent}% (simulated; inverter unavailable in dry-run mode)"
        )
        return simulated_soc_percent
    try:
        energy_flow: dict[str, Any] = await sigen.get_energy_flow()
        soc = energy_flow.get("batterySoc")
        logger.info(f"[{period}] SOC: {soc}%")
        return soc
    except SigenPayloadError as exc:
        logger.error("[%s] Inverter payload error fetching SOC — skipping tick: %s", period, exc)
        return None
    except Exception as exc:
        logger.error("[%s] Failed to fetch SOC: %s", period, exc)
        return None


async def sample_live_solar_power(
    state: SchedulerState,
    now_utc: datetime,
    sigen: SigenInteraction | None,
    logger: logging.Logger,
) -> None:
    """Capture one live solar reading via inverter-control helpers.

    Args:
        state: Scheduler state object containing live solar samples buffer.
        now_utc: Current time in UTC.
        sigen: Sigen inverter interaction client, or None in dry-run mode.
        logger: Logger instance for diagnostic output.
    """
    await sample_live_solar_power_control(
        now_utc=now_utc,
        sigen=sigen,
        live_solar_kw_samples=state.live_solar_kw_samples,
        live_solar_average_sample_count=LIVE_SOLAR_AVERAGE_SAMPLE_COUNT,
        logger=logger,
    )


def get_live_solar_average_kw(state: SchedulerState) -> float | None:
    """Return rolling average live solar generation via inverter-control helpers.

    Args:
        state: Scheduler state object containing live solar samples.

    Returns:
        Rolling average solar power in kW, or None if insufficient samples.
    """
    return get_live_solar_average_kw_control(state.live_solar_kw_samples)


def get_effective_battery_export_kw(
    state: SchedulerState,
    avg_live_solar_kw: float | None,
) -> float:
    """Estimate effective export power via inverter-control helpers.

    Args:
        state: Scheduler state object (unused, kept for consistency).
        avg_live_solar_kw: Rolling average solar power in kW, or None.

    Returns:
        Effective battery export power in kW.
    """
    return get_effective_battery_export_kw_control(
        avg_live_solar_kw,
        inverter_kw=INVERTER_KW,
        min_effective_battery_export_kw=MIN_EFFECTIVE_BATTERY_EXPORT_KW,
    )


def estimate_solar(
    state: SchedulerState,
    period: str,
    solar_value: int,
) -> float:
    """Estimate total solar energy available during a period.

    Args:
        state: Scheduler state object containing forecast calibration multipliers.
        period: Period name (e.g., 'Morn', 'Aftn', 'Eve').
        solar_value: Forecasted power in watts (typically average for period).

    Returns:
        Estimated energy in kWh assuming 3-hour period, capped by system limits.
    """
    period_calibration = get_period_calibration(state.forecast_calibration, period)
    adjusted_watts = solar_value * period_calibration["power_multiplier"]
    kw = min(adjusted_watts / 1000.0, SOLAR_PV_KW)
    return kw * 3.0  # assume 3-hour period


def _describe_payload_shape(payload: Any) -> str:
    """Return a compact payload shape description for warning logs.

    Args:
        payload: Arbitrary payload returned by an API call.

    Returns:
        Human-readable payload shape summary.
    """
    if isinstance(payload, dict):
        keys = list(payload.keys())
        preview = ", ".join(str(key) for key in keys[:8])
        suffix = ", ..." if len(keys) > 8 else ""
        return f"dict keys=[{preview}{suffix}]"
    if isinstance(payload, list):
        return f"list len={len(payload)}"
    return f"type={type(payload).__name__}"


async def archive_inverter_telemetry(
    state: SchedulerState,
    reason: str,
    now_utc: datetime,
    sigen: SigenInteraction | None,
    logger: logging.Logger,
) -> None:
    """Persist one raw inverter telemetry sample for later analysis.

    Args:
        state: Scheduler state object containing forecast data.
        reason: Context label for why the snapshot is being captured.
        now_utc: Current scheduler timestamp in UTC.
        sigen: Sigen inverter interaction client, or None in dry-run mode.
        logger: Logger instance for diagnostic output.
    """
    if sigen is None:
        return

    try:
        energy_flow = await sigen.get_energy_flow()
    except SigenPayloadError as exc:
        logger.warning(
            "[TELEMETRY] get_energy_flow payload error — snapshot skipped for this tick: %s",
            exc,
        )
        return
    except Exception as exc:
        logger.warning(
            "[TELEMETRY] get_energy_flow failed: %s. Snapshot skipped for this tick.",
            exc,
        )
        return

    if not isinstance(energy_flow, dict):
        logger.warning(
            "[TELEMETRY] get_energy_flow returned unexpected payload shape (%s). "
            "Snapshot skipped for this tick.",
            _describe_payload_shape(energy_flow),
        )
        return

    try:
        operational_mode = await sigen.get_operational_mode()
    except Exception as exc:
        logger.warning(
            "[TELEMETRY] get_operational_mode failed: %s. "
            "Snapshot skipped for this tick.",
            exc,
        )
        return

    try:
        append_inverter_telemetry_snapshot(
            energy_flow=energy_flow,
            operational_mode=operational_mode,
            reason=reason,
            scheduler_now_utc=now_utc,
            forecast_today=state.today_period_forecast,
            forecast_tomorrow=state.tomorrow_period_forecast,
        )
    except Exception as exc:
        logger.warning(
            "[TELEMETRY] Failed to write inverter snapshot: %s | energy_flow=%s | "
            "operational_mode_shape=%s",
            exc,
            _describe_payload_shape(energy_flow),
            _describe_payload_shape(operational_mode),
        )



"""
logic/decision_logging.py
--------------------------
Shared decision-checkpoint logging helper.

Provides a single canonical implementation of the per-tick decision log
that is called from all period handler modules (morning, afternoon, evening,
night).  Centralising this avoids maintaining four identical copies of the
same ~40-line logging block.
"""

import logging
from datetime import datetime

from logic.schedule_utils import LOCAL_TZ

logger = logging.getLogger(__name__)


def log_decision_checkpoint(
    period: str,
    stage: str,
    *,
    mode_names: dict[int, str],
    now_utc: datetime,
    period_start_utc: datetime,
    solar_value: int,
    status: str,
    period_solar_kwh: float,
    soc: float | None,
    headroom_kwh: float | None,
    headroom_target_kwh: float,
    headroom_deficit_kwh: float,
    export_by_utc: datetime | None,
    solar_avg_kw_3: float | None = None,
    effective_battery_export_kw: float | None = None,
    lead_time_hours_adjusted: float | None = None,
    mode: int | None = None,
    reason: str = "",
    outcome: str = "",
) -> None:
    """Log a comprehensive decision checkpoint with all relevant state and parameters.

    Emits a structured block of INFO-level log lines that capture every input
    used when evaluating a period mode decision.  The block is headed by a
    human-readable period/stage label and followed by one line per field so
    that the output is grep-friendly.

    Args:
        period: Human-readable period/context label (e.g. ``Morn``, ``Aftn->Eve``).
        stage: Scheduling stage identifier (e.g. ``PRE-PERIOD``, ``PERIOD-START``,
            ``NIGHT-BASE``, ``MID-PERIOD-CLIPPING``).
        mode_names: Mapping of mode integer to display label.
        now_utc: Current scheduler tick timestamp in UTC.
        period_start_utc: Scheduled start time of the target period in UTC.
        solar_value: Forecasted solar power in watts.
        status: Forecast status string (e.g. ``Green``, ``Amber``, ``Red``).
        period_solar_kwh: Estimated available solar energy for the period in kWh.
        soc: Current battery SOC percentage, or ``None`` if unavailable.
        headroom_kwh: Current available battery headroom in kWh, or ``None``.
        headroom_target_kwh: Target headroom required before the period in kWh.
        headroom_deficit_kwh: Shortfall against the target in kWh.
        export_by_utc: Deadline for the pre-period export window, or ``None``.
        solar_avg_kw_3: Rolling average live solar generation over the latest
            three samples in kW, or ``None`` if not yet available.
        effective_battery_export_kw: Estimated net battery export rate in kW
            after solar generation occupancy, or ``None``.
        lead_time_hours_adjusted: Lead-time in hours derived from the adjusted
            export denominator, or ``None``.
        mode: Target operational mode integer, or ``None`` when no mode decision
            has been reached.
        reason: Human-readable explanation of the decision logic applied.
        outcome: Short description of the action taken (e.g. ``"pre-period export
            triggered"``).
    """
    mode_label = mode_names.get(mode, mode) if mode is not None else "N/A"
    export_by_label = export_by_utc.isoformat() if export_by_utc is not None else "N/A"
    base_period = period.split(" ", 1)[0].split("->")[-1]
    period_labels = {"Morn": "MORNING", "Aftn": "AFTERNOON", "Eve": "EVENING", "NIGHT": "NIGHT"}
    period_display = period_labels.get(base_period, base_period.upper())
    period_start_local = period_start_utc.astimezone(LOCAL_TZ).strftime("%H:%M")
    logger.info(f"[{period}] {stage} CHECK FOR {period_display} (Starts at {period_start_local}):")
    logger.info(f"[{period}]     -> now={now_utc.isoformat()}")
    logger.info(f"[{period}]     -> period_start={period_start_utc.isoformat()}")
    logger.info(f"[{period}]     -> forecast_w={solar_value}")
    logger.info(f"[{period}]     -> status={status}")
    logger.info(f"[{period}]     -> expected_solar_kwh={period_solar_kwh:.2f}")
    logger.info(f"[{period}]     -> soc={soc if soc is not None else 'N/A'}")
    logger.info(
        f"[{period}]     -> headroom_kwh={f'{headroom_kwh:.2f}' if headroom_kwh is not None else 'N/A'}"
    )
    logger.info(f"[{period}]     -> headroom_target_kwh={headroom_target_kwh:.2f}")
    logger.info(f"[{period}]     -> headroom_deficit_kwh={headroom_deficit_kwh:.2f}")
    logger.info(
        f"[{period}]     -> solar_avg_kw_3={f'{solar_avg_kw_3:.2f}' if solar_avg_kw_3 is not None else 'N/A'}"
    )
    logger.info(
        "[{}]     -> effective_battery_export_kw={}".format(
            period,
            f"{effective_battery_export_kw:.2f}" if effective_battery_export_kw is not None else "N/A",
        )
    )
    logger.info(
        "[{}]     -> lead_time_hours_adjusted={}".format(
            period,
            f"{lead_time_hours_adjusted:.2f}" if lead_time_hours_adjusted is not None else "N/A",
        )
    )
    logger.info(f"[{period}]     -> export_by={export_by_label}")
    logger.info(f"[{period}]     -> decision_mode={mode_label}")
    logger.info(f"[{period}]     -> outcome={outcome}")
    logger.info(f"[{period}]     -> reason={reason}")

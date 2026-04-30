"""logic/mode_change.py
---------------------
High-level mode-change orchestration for the scheduler.

Wraps logic.inverter_control.apply_mode_change with concrete dependency bindings:
notification email dispatch, event archiving, and simulation-mode detection.
All module-level names are resolved via global lookup at call time so tests can
monkeypatch them via monkeypatch.setattr(mode_change_module, ...).
"""

import logging
import os
from typing import Any

from logic.inverter_control import apply_mode_change as _apply_mode_change_control
from notifications.notification_email_helpers import (
    notify_mode_change_email as _notify_email_raw,
)
from telemetry.telemetry_archive import append_mode_change_event
from config.settings import FULL_SIMULATION_MODE


def _should_archive_mode_change_events() -> bool:
    """Return whether mode-change events should be written to the live archive.

    Returns:
        True during normal runtime. False during pytest unless explicitly enabled.
    """
    running_under_pytest = bool(os.getenv("PYTEST_CURRENT_TEST"))
    allow_pytest_archives = (
        os.getenv("SIGEN_ALLOW_MODE_CHANGE_ARCHIVE_IN_TESTS", "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    return not running_under_pytest or allow_pytest_archives


async def _notify_mode_change_email(logger: logging.Logger, **kwargs: Any) -> None:
    """Dispatch a mode-change notification email.

    Args:
        logger: Logger instance to pass to the email helper.
        **kwargs: All keyword arguments forwarded to notify_mode_change_email.
    """
    await _notify_email_raw(**kwargs, logger=logger)


async def apply_mode_change(
    *,
    sigen: Any | None,
    mode: int,
    period: str,
    reason: str,
    mode_names: dict[int, str],
    logger: logging.Logger,
    export_duration_minutes: int | None = None,
    battery_soc: float | None = None,
    today_period_forecast: dict[str, tuple[int, str]] | None = None,
    zappi_status: dict[str, Any] | None = None,
    zappi_daily: dict[str, Any] | None = None,
) -> bool:
    """Attempt to change the inverter operational mode with idempotency checks.

    Reads the current mode before writing; if already at target mode, logs and returns True
    without calling the API. Falls back to set attempt if read fails.

    All dependency names (_notify_mode_change_email, _should_archive_mode_change_events,
    append_mode_change_event, FULL_SIMULATION_MODE) are read from the module globals at
    call time so that tests can monkeypatch them via
    monkeypatch.setattr(mode_change_module, ...).

    Args:
        sigen: SigenInteraction instance, or None in dry-run mode.
        mode: Target numeric mode value.
        period: Human-readable period/context label for logging.
        reason: Explanation of why this mode change is being made.
        mode_names: Mapping from numeric mode to human-readable label.
        logger: Logger instance for diagnostic output.
        export_duration_minutes: Optional override window when forcing GRID_EXPORT.
        battery_soc: Battery state of charge at the time of the command, when known.
        today_period_forecast: Daytime period forecast snapshot for today.
        zappi_status: Most recent Zappi live-status snapshot, or None when unavailable.
        zappi_daily: Today's Zappi daily charge totals, or None when unavailable.

    Returns:
        True if mode was set or already at target, False if set operation failed.
    """
    _bound_logger = logger

    async def _notify(**kwargs: Any) -> None:
        # Global lookup: reads _notify_mode_change_email from this module's globals.
        await _notify_mode_change_email(_bound_logger, **kwargs)

    return await _apply_mode_change_control(
        sigen=sigen,
        mode=mode,
        period=period,
        reason=reason,
        mode_names=mode_names,
        logger=logger,
        notify_mode_change_email=_notify,
        # Global lookups — resolved at call time so monkeypatching works.
        should_archive_mode_change_events=_should_archive_mode_change_events,
        append_mode_change_event=append_mode_change_event,
        full_simulation_mode=FULL_SIMULATION_MODE,
        export_duration_minutes=export_duration_minutes,
        battery_soc=battery_soc,
        today_period_forecast=today_period_forecast,
        zappi_status=zappi_status,
        zappi_daily=zappi_daily,
    )

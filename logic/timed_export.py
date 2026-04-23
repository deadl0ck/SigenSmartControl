"""Timed grid export state and orchestration helpers.

This module centralizes timed export state persistence and restore behavior.
It keeps scheduler code in main.py focused on high-level loop flow while this
module handles the start/restore lifecycle for timed GRID_EXPORT windows.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable

from config.constants import TIMED_EXPORT_STATE_PATH
from config.settings import (
    LIVE_CLIPPING_EXPORT_SOC_FLOOR_PERCENT,
    MAX_TIMED_EXPORT_MINUTES,
    SIGEN_MODES,
)
from logic.mode_control import ACTION_DIVIDER, extract_mode_value


ModeChangeApplier = Callable[..., Awaitable[bool]]
SocFetcher = Callable[[str], Awaitable[float | None]]
ModeStatusLogger = Callable[[str, Any, dict[int, str]], None]
StateUpdater = Callable[[dict[str, Any]], None]


def _empty_timed_export_override() -> dict[str, Any]:
    """Return the default inactive timed export override structure."""
    return {
        "active": False,
        "started_at": None,
        "restore_at": None,
        "restore_mode": None,
        "restore_mode_label": None,
        "trigger_period": None,
        "duration_minutes": None,
        "is_clipping_export": False,
        "clipping_soc_floor": None,
        "export_soc_floor": None,
    }


def persist_timed_export_override(
    state: dict[str, Any],
    *,
    logger: logging.Logger,
) -> None:
    """Persist active timed export override state to disk or clear it.

    Args:
        state: Timed export override state dict.
        logger: Scheduler logger used for warning output.
    """
    state_path = Path(TIMED_EXPORT_STATE_PATH)
    if not state.get("active"):
        try:
            state_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("[TIMED EXPORT] Failed to remove persisted state: %s", exc)
        return

    payload = dict(state)
    for field_name in ("started_at", "restore_at"):
        value = payload.get(field_name)
        if isinstance(value, datetime):
            payload[field_name] = value.isoformat()

    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(payload, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("[TIMED EXPORT] Failed to persist override state: %s", exc)


def load_timed_export_override(*, logger: logging.Logger) -> dict[str, Any]:
    """Load persisted timed export override state from disk when available.

    Args:
        logger: Scheduler logger used for warning output.

    Returns:
        Restored timed export state, or an inactive default state when unavailable.
    """
    state_path = Path(TIMED_EXPORT_STATE_PATH)
    if not state_path.exists():
        return _empty_timed_export_override()

    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[TIMED EXPORT] Failed to load persisted override state: %s", exc)
        return _empty_timed_export_override()

    restored = _empty_timed_export_override()
    restored.update(payload)
    for field_name in ("started_at", "restore_at"):
        value = restored.get(field_name)
        if isinstance(value, str):
            try:
                restored[field_name] = datetime.fromisoformat(value)
            except ValueError:
                logger.warning(
                    "[TIMED EXPORT] Invalid datetime %s in persisted override state.",
                    field_name,
                )
                return _empty_timed_export_override()

    if restored.get("active"):
        logger.warning(
            "[TIMED EXPORT] Restored active override from disk: trigger_period=%s restore_at=%s",
            restored.get("trigger_period"),
            restored.get("restore_at"),
        )
    return restored


async def start_timed_grid_export(
    *,
    timed_export_override: dict[str, Any],
    set_timed_export_override: StateUpdater,
    period: str,
    reason: str,
    duration_minutes: int,
    now_utc: datetime,
    battery_soc: float | None,
    is_clipping_export: bool,
    export_soc_floor: float | None,
    sigen: Any,
    mode_names: dict[int, str],
    apply_mode_change: ModeChangeApplier,
    logger: logging.Logger,
    log_mode_status: ModeStatusLogger | None = None,
) -> bool:
    """Switch to GRID_EXPORT for a bounded duration and schedule mode restore.

    Args:
        timed_export_override: Current in-memory timed export state.
        set_timed_export_override: Callback that updates and persists state.
        period: Human-readable period label that triggered export.
        reason: Decision explanation for audit logs.
        duration_minutes: Requested export duration in minutes.
        now_utc: Current scheduler timestamp in UTC.
        battery_soc: Battery SOC at trigger time when available.
        is_clipping_export: If True, apply clipping SOC floor checks.
        export_soc_floor: Optional SOC floor that triggers early restore.
        sigen: Sigen interaction instance or None.
        mode_names: Mapping of mode value to user-facing mode name.
        apply_mode_change: Callback that performs mode change and accounting.
        logger: Scheduler logger.
        log_mode_status: Optional callback for standardized pre-change mode logs.

    Returns:
        True when timed export is activated, False otherwise.
    """
    if timed_export_override["active"]:
        logger.info(
            "[TIMED EXPORT] Requested by %s but override already active until %s. "
            "Keeping current override and skipping new request.",
            period,
            timed_export_override["restore_at"],
        )
        return False

    requested_minutes = max(1, duration_minutes)
    clamped_minutes = min(requested_minutes, MAX_TIMED_EXPORT_MINUTES)
    if clamped_minutes < requested_minutes:
        logger.warning(
            "[TIMED EXPORT] Requested duration %s minutes exceeds safety cap of %s minutes. "
            "Clamping to %s minutes.",
            requested_minutes,
            MAX_TIMED_EXPORT_MINUTES,
            clamped_minutes,
        )
    restore_at = now_utc + timedelta(minutes=clamped_minutes)

    restore_mode: int | None = None
    restore_label = "UNKNOWN"
    if sigen is not None:
        try:
            current_mode_raw = await sigen.get_operational_mode()
            if log_mode_status is not None:
                log_mode_status(
                    f"pre-timed-export pull ({period})",
                    current_mode_raw,
                    mode_names,
                )
            restore_mode = extract_mode_value(current_mode_raw)
            if restore_mode is None:
                logger.warning(
                    "[TIMED EXPORT] Could not parse current mode before timed export; "
                    "refusing override to avoid unsafe restore target. raw=%s",
                    current_mode_raw,
                )
                return False
            restore_label = str(mode_names.get(restore_mode, restore_mode))
        except Exception as exc:
            logger.warning(
                "[TIMED EXPORT] Failed to read current mode before timed export: %s",
                exc,
            )
            return False

    logger.info(ACTION_DIVIDER)
    logger.info(
        "[TIMED EXPORT] Switching to GRID_EXPORT now. Trigger period=%s, duration=%s min, "
        "active_until=%s, will_restore_to=%s",
        period,
        clamped_minutes,
        restore_at.isoformat(),
        restore_label,
    )
    logger.info(ACTION_DIVIDER)

    apply_reason = (
        f"{reason} Timed export override active for {clamped_minutes} minutes "
        f"(until {restore_at.isoformat()}) before restoring previous mode {restore_label}."
    )
    ok = await apply_mode_change(
        sigen=sigen,
        mode=SIGEN_MODES["GRID_EXPORT"],
        period=f"{period} (timed-export-start)",
        reason=apply_reason,
        mode_names=mode_names,
        export_duration_minutes=clamped_minutes,
        battery_soc=battery_soc,
    )
    if not ok:
        return False

    set_timed_export_override(
        {
            "active": True,
            "started_at": now_utc,
            "restore_at": restore_at,
            "restore_mode": restore_mode,
            "restore_mode_label": restore_label,
            "trigger_period": period,
            "duration_minutes": clamped_minutes,
            "is_clipping_export": is_clipping_export,
            "clipping_soc_floor": (
                LIVE_CLIPPING_EXPORT_SOC_FLOOR_PERCENT if is_clipping_export else None
            ),
            "export_soc_floor": export_soc_floor,
        }
    )
    return True


async def maybe_restore_timed_grid_export(
    *,
    timed_export_override: dict[str, Any],
    set_timed_export_override: StateUpdater,
    now_utc: datetime,
    fetch_soc: SocFetcher,
    sigen: Any,
    mode_names: dict[int, str],
    apply_mode_change: ModeChangeApplier,
    logger: logging.Logger,
) -> str:
    """Restore pre-export mode when active timed export window has elapsed.

    Args:
        timed_export_override: Current in-memory timed export state.
        set_timed_export_override: Callback that updates and persists state.
        now_utc: Current scheduler timestamp in UTC.
        fetch_soc: Callback for obtaining battery SOC.
        sigen: Sigen interaction instance or None.
        mode_names: Mapping of mode value to user-facing mode name.
        apply_mode_change: Callback that performs mode change and accounting.
        logger: Scheduler logger.

    Returns:
        One of: "active", "restored", or "inactive".
    """
    if not timed_export_override["active"]:
        return "inactive"

    restore_at = timed_export_override["restore_at"]
    is_clipping = timed_export_override.get("is_clipping_export", False)
    clipping_soc_floor = timed_export_override.get("clipping_soc_floor")
    export_soc_floor = timed_export_override.get("export_soc_floor")

    if export_soc_floor is not None:
        current_soc = await fetch_soc("timed-export-soc-check")
        if current_soc is not None and current_soc <= export_soc_floor:
            restore_mode = timed_export_override["restore_mode"]
            restore_label = timed_export_override["restore_mode_label"]
            trigger_period = timed_export_override["trigger_period"]
            if restore_mode is not None:
                logger.info(ACTION_DIVIDER)
                logger.info(
                    "[TIMED EXPORT] Export SOC floor reached (%.1f%% <= %.1f%%). "
                    "Restoring %s early.",
                    current_soc,
                    export_soc_floor,
                    restore_label,
                )
                logger.info(ACTION_DIVIDER)
                restore_ok = await apply_mode_change(
                    sigen=sigen,
                    mode=restore_mode,
                    period=f"{trigger_period} (timed-export-soc-floor)",
                    reason=(
                        f"Timed export SOC floor reached at {current_soc:.1f}%. "
                        f"Restoring {restore_label}."
                    ),
                    mode_names=mode_names,
                    battery_soc=current_soc,
                )
                if restore_ok:
                    set_timed_export_override(_empty_timed_export_override())
                    return "restored"

    if is_clipping and clipping_soc_floor is not None:
        current_soc = await fetch_soc("clipping-soc-check")
        if current_soc is not None and current_soc <= clipping_soc_floor:
            restore_mode = timed_export_override["restore_mode"]
            restore_label = timed_export_override["restore_mode_label"]
            trigger_period = timed_export_override["trigger_period"]
            if restore_mode is not None:
                logger.info(ACTION_DIVIDER)
                logger.info(
                    "[TIMED EXPORT] Clipping export SOC floor reached (%.1f%% <= %.1f%%). "
                    "Restoring %s early.",
                    current_soc,
                    clipping_soc_floor,
                    restore_label,
                )
                logger.info(ACTION_DIVIDER)
                restore_ok = await apply_mode_change(
                    sigen=sigen,
                    mode=restore_mode,
                    period=f"{trigger_period} (clipping-export-soc-floor)",
                    reason=(
                        f"Clipping export SOC floor reached at {current_soc:.1f}%. "
                        f"Restoring {restore_label}."
                    ),
                    mode_names=mode_names,
                    battery_soc=current_soc,
                )
                if restore_ok:
                    set_timed_export_override(_empty_timed_export_override())
                    return "restored"

    if restore_at is None:
        logger.warning("[TIMED EXPORT] Override state missing restore_at; clearing state.")
        set_timed_export_override(_empty_timed_export_override())
        return "inactive"

    if now_utc < restore_at:
        return "active"

    restore_mode = timed_export_override["restore_mode"]
    restore_label = timed_export_override["restore_mode_label"]
    trigger_period = timed_export_override["trigger_period"]
    duration_minutes = timed_export_override["duration_minutes"]
    if restore_mode is None:
        logger.warning(
            "[TIMED EXPORT] Restore mode unavailable after timed export window from %s. "
            "Leaving scheduler control enabled without automated restore.",
            trigger_period,
        )
        set_timed_export_override(_empty_timed_export_override())
        return "inactive"

    logger.info(ACTION_DIVIDER)
    logger.info(
        "[TIMED EXPORT] Export window completed. Restoring prior mode %s now. "
        "Triggered_by=%s, configured_duration=%s min, restore_due_at=%s",
        restore_label,
        trigger_period,
        duration_minutes,
        restore_at.isoformat(),
    )
    logger.info(ACTION_DIVIDER)

    restore_soc = await fetch_soc("timed-export-restore")

    restore_ok = await apply_mode_change(
        sigen=sigen,
        mode=restore_mode,
        period=f"{trigger_period} (timed-export-restore)",
        reason=(
            "Timed grid export window complete; restoring mode active before override "
            f"({restore_label})."
        ),
        mode_names=mode_names,
        battery_soc=restore_soc,
    )
    if restore_ok:
        set_timed_export_override(_empty_timed_export_override())
        return "restored"

    logger.warning("[TIMED EXPORT] Restore attempt failed; will retry next scheduler tick.")
    return "active"

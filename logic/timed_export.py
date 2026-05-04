"""Timed grid export state and orchestration helpers.

This module centralizes timed export state persistence and restore behavior.
It keeps scheduler code in main.py focused on high-level loop flow while this
module handles the start/restore lifecycle for timed GRID_EXPORT windows.

## State machine

The timed export override follows a three-state lifecycle:

    inactive ──► active ──► restored ──► inactive (next tick)

                    ▲                │
                    └── extended ────┘  (stays active, restore_at bumped)

**inactive**: No override is in force.  ``timed_export_override["active"]``
    is False.  ``maybe_restore_timed_grid_export`` returns ``"inactive"`` and
    the scheduler proceeds with its normal per-period decisions.

**active**: A GRID_EXPORT window is running.  ``timed_export_override["active"]``
    is True and ``now_utc < restore_at``.  The scheduler skips all normal mode
    decisions until the window ends or an SOC floor fires.

**extended**: ``restore_at`` was reached but SOC is still above ``export_soc_floor``,
    meaning solar kept refilling the battery faster than the export drained it.
    ``restore_at`` is bumped forward by the original ``duration_minutes`` (capped at
    the ``MAX_TIMED_EXPORT_MINUTES`` wall from ``started_at``) and the state remains
    *active*.  This prevents the stop/cooldown/restart cycle that would otherwise
    add a 15-minute gap before export resumes.  Only applies when ``export_soc_floor``
    is set (headroom-based exports); clipping exports are not extended.

**restored**: The export window ended (or an SOC floor fired) and the previous
    mode was reinstated this tick.  ``maybe_restore_timed_grid_export`` returns
    ``"restored"``.  The scheduler skips normal decisions for the *remainder of
    this tick only* so that the fresh restore is not immediately overwritten;
    normal decisions resume on the next tick.

## Transitions

* **inactive → active**: ``start_timed_grid_export()`` succeeds — the inverter
  accepted the GRID_EXPORT command and state is written to disk.

* **active → active** (extended): ``restore_at`` is reached, ``export_soc_floor``
  is set, and current SOC is still above that floor.  ``restore_at`` is advanced
  and the override remains active.  Capped at ``started_at + MAX_TIMED_EXPORT_MINUTES``.

* **active → restored** (normal): ``restore_at`` is reached and either no
  ``export_soc_floor`` is configured, the cap has been reached, or SOC has
  dropped to or below the floor.  The previous mode is restored and the state
  file is deleted.

* **active → restored** (early, export SOC floor): While active, the battery SOC
  drops to or below ``export_soc_floor``.  Checked first on every tick.

* **active → restored** (early, clipping SOC floor): While active *and*
  ``is_clipping_export`` is True, the battery SOC drops to or below
  ``clipping_soc_floor``.  Checked after the export SOC floor.

* **restored → inactive**: The caller discards the ``"restored"`` signal and the
  state is already cleared; the *next* call to ``maybe_restore_timed_grid_export``
  finds ``active=False`` and returns ``"inactive"``.

## SOC floor precedence

``export_soc_floor`` is checked before ``clipping_soc_floor`` so that an
explicit caller-supplied floor always wins over the clipping-specific default.
Both floors trigger the same early-restore action; the first one that fires
short-circuits the rest of the checks.
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

from config.constants import TIMED_EXPORT_STATE_PATH
from config.settings import (
    LIVE_CLIPPING_EXPORT_SOC_FLOOR_PERCENT,
    LOCAL_TIMEZONE,
    MAX_TIMED_EXPORT_MINUTES,
    SIGEN_MODES,
    TIMED_EXPORT_RESTORE_COOLDOWN_MINUTES,
)
from logic.mode_control import ACTION_DIVIDER, extract_mode_value
from logic.schedule_utils import is_cheap_rate_window


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
    path: Path | None = None,
) -> None:
    """Persist active timed export override state to disk or clear it.

    Args:
        state: Timed export override state dict.
        logger: Scheduler logger used for warning output.
        path: Override path for the state file. Defaults to TIMED_EXPORT_STATE_PATH.
    """
    state_path = path if path is not None else Path(TIMED_EXPORT_STATE_PATH)
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


def load_timed_export_override(
    *,
    logger: logging.Logger,
    path: Path | None = None,
) -> dict[str, Any]:
    """Load persisted timed export override state from disk when available.

    Args:
        logger: Scheduler logger used for warning output.
        path: Override path for the state file. Defaults to TIMED_EXPORT_STATE_PATH.

    Returns:
        Restored timed export state, or an inactive default state when unavailable.
    """
    state_path = path if path is not None else Path(TIMED_EXPORT_STATE_PATH)
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
    last_export_restore_at: datetime | None = None,
    restore_cooldown_minutes: int = 0,
    sigen: Any = None,
    mode_names: dict[int, str] | None = None,
    apply_mode_change: ModeChangeApplier | None = None,
    logger: logging.Logger | None = None,
    log_mode_status: ModeStatusLogger | None = None,
) -> bool:
    """Switch to GRID_EXPORT for a bounded duration and schedule mode restore.

    On success the state machine transitions from *inactive* to *active*: the
    inverter is placed in GRID_EXPORT, the previous mode is recorded as the
    restore target, and the state is persisted to disk.  The caller should skip
    all remaining normal mode decisions for the current tick after a True return.

    Args:
        timed_export_override: Current in-memory timed export state.
        set_timed_export_override: Callback that updates and persists state.
        period: Human-readable period label that triggered export.
        reason: Decision explanation for audit logs.
        duration_minutes: Requested export duration in minutes.
        now_utc: Current scheduler timestamp in UTC.
        battery_soc: Battery SOC at trigger time when available.
        is_clipping_export: If True, apply clipping SOC floor checks during
            the window in addition to any explicit export_soc_floor.
        export_soc_floor: Optional SOC % at which the export is cut short early,
            regardless of whether the window has elapsed.
        last_export_restore_at: UTC timestamp of the most recent restore, or None.
            When set and restore_cooldown_minutes > 0, blocks new exports until the
            cooldown window expires.
        restore_cooldown_minutes: Minutes to suppress new exports after a restore.
            Defaults to 0 (no cooldown). Typically sourced from
            TIMED_EXPORT_RESTORE_COOLDOWN_MINUTES.
        sigen: Sigen interaction instance or None.
        mode_names: Mapping of mode value to user-facing mode name.
        apply_mode_change: Callback that performs mode change and accounting.
        logger: Scheduler logger.
        log_mode_status: Optional callback for standardized pre-change mode logs.

    Returns:
        True when the state machine moved to *active* (inverter accepted the
        command).  False when the override was already active, the current mode
        could not be read, or the mode-change command failed — in all False cases
        the state is unchanged.
    """
    if timed_export_override["active"]:
        logger.info(
            "[TIMED EXPORT] Requested by %s but override already active until %s. "
            "Keeping current override and skipping new request.",
            period,
            timed_export_override["restore_at"],
        )
        return False

    if last_export_restore_at is not None and restore_cooldown_minutes > 0:
        cooldown_expires = last_export_restore_at + timedelta(minutes=restore_cooldown_minutes)
        if now_utc < cooldown_expires:
            remaining = int((cooldown_expires - now_utc).total_seconds() / 60)
            logger.info(
                "[TIMED EXPORT] Skipping new export — restore cooldown active for %s more minute(s) "
                "(last restore: %s).",
                remaining,
                last_export_restore_at.isoformat(),
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
            # Neither TOU nor GRID_EXPORT should ever be the restore target:
            #   - TOU is a night-only charging mode; restoring to it during or
            #     after cheap-rate would either recharge the battery from the
            #     grid (undoing headroom) or leave the inverter in the wrong
            #     daytime mode.
            #   - GRID_EXPORT as a restore target means the inverter was already
            #     in export mode when this export started (e.g. a stale state or
            #     duplicate scheduler instance). Restoring to GRID_EXPORT would
            #     leave the inverter exporting indefinitely with no SOC floor
            #     protection once the timed window ends.
            # In both cases, fall back to SELF_POWERED.
            if restore_mode in (SIGEN_MODES["TOU"], SIGEN_MODES["GRID_EXPORT"]):
                logger.info(
                    "[TIMED EXPORT] Current mode is %s — "
                    "will restore to SELF_POWERED instead.",
                    restore_label,
                )
                restore_mode = SIGEN_MODES["SELF_POWERED"]
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

    restore_at_local = restore_at.astimezone(ZoneInfo(LOCAL_TIMEZONE))
    restore_at_str = restore_at_local.strftime("%-d %b %Y %H:%M")
    apply_reason = (
        f"{reason} Exporting to grid for {clamped_minutes} minutes "
        f"(until {restore_at_str}), then returning to {restore_label}."
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
        ``"inactive"`` — no override is currently active; the caller should
            proceed with its normal per-period mode decisions this tick.
        ``"active"`` — an override window is still running; the caller should
            skip all normal mode decisions and wait for the window to expire.
        ``"restored"`` — the export window just ended (time-based or SOC floor)
            and the previous mode was reinstated this tick; the caller should
            skip normal decisions for the remainder of *this* tick only, then
            resume normally on the next tick.
    """
    if not timed_export_override["active"]:
        return "inactive"

    restore_at = timed_export_override["restore_at"]
    is_clipping = timed_export_override.get("is_clipping_export", False)
    clipping_soc_floor = timed_export_override.get("clipping_soc_floor")
    export_soc_floor = timed_export_override.get("export_soc_floor")

    # SOC floor checks: export_soc_floor is evaluated first because it is an
    # explicit caller-supplied threshold (e.g. pre-period headroom protection)
    # and should always win over the clipping-specific default.  The first floor
    # that fires triggers an early restore and short-circuits the second check.
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
                        f"Battery reached the minimum level ({current_soc:.1f}%) — "
                        f"stopping export and returning to {restore_label}."
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
                        f"Battery reached the minimum level during clipping export ({current_soc:.1f}%) — "
                        f"stopping export and returning to {restore_label}."
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

    # Auto-extension: before restoring, check whether headroom has actually been
    # recovered. When solar keeps refilling the battery faster than the export drains
    # it, SOC stays above the floor even after the planned window. In that case, bump
    # restore_at forward by the original duration (capped at the MAX_TIMED_EXPORT_MINUTES
    # wall from started_at) and stay active — this avoids the stop/cooldown/restart gap.
    # Only headroom-based exports set export_soc_floor; clipping exports are not extended.
    if export_soc_floor is not None:
        started_at = timed_export_override.get("started_at")
        original_duration = timed_export_override.get("duration_minutes") or 0
        if started_at is not None:
            max_end_utc = started_at + timedelta(minutes=MAX_TIMED_EXPORT_MINUTES)
            if now_utc < max_end_utc:
                extend_soc = await fetch_soc("timed-export-extend-check")
                if extend_soc is not None and extend_soc > export_soc_floor:
                    extension_end = now_utc + timedelta(minutes=max(original_duration, 1))
                    new_restore_at = min(extension_end, max_end_utc)
                    ext_minutes = int((new_restore_at - now_utc).total_seconds() / 60)
                    logger.info(
                        "[TIMED EXPORT] Window expired but SOC (%.1f%%) is still above the "
                        "%.1f%% floor — extending export by %s minute(s) "
                        "(until %s, cap %s).",
                        extend_soc,
                        export_soc_floor,
                        ext_minutes,
                        new_restore_at.isoformat(),
                        max_end_utc.isoformat(),
                    )
                    extended_state = dict(timed_export_override)
                    extended_state["restore_at"] = new_restore_at
                    set_timed_export_override(extended_state)
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
            f"Export window finished — returning to {restore_label}."
        ),
        mode_names=mode_names,
        battery_soc=restore_soc,
    )
    if restore_ok:
        set_timed_export_override(_empty_timed_export_override())
        return "restored"

    logger.warning("[TIMED EXPORT] Restore attempt failed; will retry next scheduler tick.")
    return "active"

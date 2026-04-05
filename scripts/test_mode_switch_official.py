"""Test official Sigen mode APIs with safe defaults.

This script defaults to read-only operations. It will only send a mode-change
command when both a mode argument and --apply are provided.

Usage:
  python scripts/test_mode_switch_official.py
  python scripts/test_mode_switch_official.py --list
  python scripts/test_mode_switch_official.py 0
  python scripts/test_mode_switch_official.py 5 --apply
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from typing import Any
from pathlib import Path

# Allow execution from project root or scripts/ directory.
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from integrations.sigen_official import OFFICIAL_MODE_INT_TO_ENUM, SigenOfficial


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("test_mode_switch_official")


def _format_tree_leaf(value: Any) -> str:
    """Format a scalar value for tree logging."""
    return repr(value)


def _iter_tree_lines(payload: Any, prefix: str = "") -> list[str]:
    """Convert nested dict/list payloads into ASCII tree lines."""
    lines: list[str] = []

    if isinstance(payload, dict):
        items = list(payload.items())
        for index, (key, value) in enumerate(items):
            is_last = index == len(items) - 1
            branch = "`- " if is_last else "|- "
            child_prefix = prefix + ("   " if is_last else "|  ")

            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{branch}{key}:")
                lines.extend(_iter_tree_lines(value, child_prefix))
            else:
                lines.append(f"{prefix}{branch}{key}: {_format_tree_leaf(value)}")
        return lines

    if isinstance(payload, list):
        for index, value in enumerate(payload):
            is_last = index == len(payload) - 1
            branch = "`- " if is_last else "|- "
            child_prefix = prefix + ("   " if is_last else "|  ")
            label = f"[{index}]"

            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{branch}{label}:")
                lines.extend(_iter_tree_lines(value, child_prefix))
            else:
                lines.append(f"{prefix}{branch}{label}: {_format_tree_leaf(value)}")
        return lines

    lines.append(f"{prefix}`- {_format_tree_leaf(payload)}")
    return lines


def log_payload_tree(title: str, payload: Any) -> None:
    """Log nested payload data as a readable multi-line tree."""
    logger.info("%s:", title)
    for line in _iter_tree_lines(payload):
        logger.info("  %s", line)


async def show_current_mode(client: SigenOfficial) -> None:
    """Fetch and print current operating mode."""
    try:
        current = await client.get_operational_mode()
    except RuntimeError as exc:
        logger.warning("Official current-mode query unavailable: %s", exc)
        return

    logger.info("Current mode response:\n%s", json.dumps(current, indent=2, default=str))


async def show_monitoring_payloads(client: SigenOfficial) -> None:
    """Fetch and print monitoring payloads exposed by the official app."""
    try:
        systems = await client.get_system_list()
    except RuntimeError as exc:
        logger.warning("Official system list unavailable: %s", exc)
    else:
        log_payload_tree("Official system list payload", systems)

    try:
        summary = await client.get_system_summary()
    except RuntimeError as exc:
        logger.warning("Official system summary unavailable: %s", exc)
    else:
        log_payload_tree("Official system summary payload", summary)

    try:
        energy_flow = await client.get_energy_flow()
    except RuntimeError as exc:
        logger.warning("Official energy flow unavailable: %s", exc)
    else:
        log_payload_tree("Official energy flow payload", energy_flow)


async def show_supported_modes(client: SigenOfficial) -> None:
    """Fetch and print supported mode list."""
    modes = await client.get_operational_modes()
    logger.info("Supported official modes:\n%s", json.dumps(modes, indent=2, default=str))


async def maybe_apply_mode(client: SigenOfficial, mode: int, apply: bool) -> None:
    """Apply mode only when explicitly requested.

    Args:
        client: Initialized official client.
        mode: Target mode integer.
        apply: Whether to send write command.
    """
    if mode not in OFFICIAL_MODE_INT_TO_ENUM:
        supported = ", ".join(str(v) for v in sorted(OFFICIAL_MODE_INT_TO_ENUM))
        raise ValueError(f"Unsupported official mode {mode}. Supported: {supported}")

    mode_enum = OFFICIAL_MODE_INT_TO_ENUM[mode]
    if not apply:
        logger.info(
            "Dry-run: would set mode %s (%s). Re-run with --apply to send command.",
            mode,
            mode_enum,
        )
        return

    logger.info("Applying mode %s (%s)...", mode, mode_enum)
    response = await client.set_operational_mode(mode)
    logger.info("Set mode response:\n%s", json.dumps(response, indent=2, default=str))


async def main() -> None:
    """Parse args and run official mode checks/set."""
    parser = argparse.ArgumentParser(
        description="Test official Sigen mode query/set with safe defaults.",
    )
    parser.add_argument("mode", nargs="?", type=int, help="Official mode integer (0, 5, 8).")
    parser.add_argument("--apply", action="store_true", help="Actually send mode change command.")
    parser.add_argument("--list", action="store_true", help="Show supported mode list.")
    args = parser.parse_args()

    # This script is intended for official-only validation.
    os.environ["SIGEN_OFFICIAL_STRICT_ONLY"] = "true"

    logger.info("Initializing official Sigen client...")
    client = await SigenOfficial.create_from_env()
    logger.info("Official client initialized.")

    await show_monitoring_payloads(client)
    await show_current_mode(client)

    if args.list or args.mode is None:
        await show_supported_modes(client)

    if args.mode is not None:
        await maybe_apply_mode(client, args.mode, args.apply)


if __name__ == "__main__":
    asyncio.run(main())
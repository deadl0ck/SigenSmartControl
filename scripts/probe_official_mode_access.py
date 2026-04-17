"""Probe official Sigen API mode-switch access.

This script is designed as a quick fallback diagnostic for official API support.
It verifies:
1) Official authentication succeeds.
2) Current operating mode can be queried.
3) A safe mode-set call can be sent (optional) and response captured.

Usage examples:
    python scripts/probe_official_mode_access.py
    python scripts/probe_official_mode_access.py --mode 0 --apply
    python scripts/probe_official_mode_access.py --mode 5 --apply --auth-mode key

Notes:
- Official mode-switch endpoint only supports 0, 5, 8.
- By default this runs read-only unless --apply is provided.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from integrations.sigen_official import OFFICIAL_MODE_INT_TO_ENUM, SigenOfficial


def _print_json(title: str, payload: Any) -> None:
    """Print a JSON payload with a clear title."""
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2, default=str))


async def run_probe(*, mode: int, apply: bool) -> int:
    """Run official API mode access probe.

    Args:
        mode: Target official mode integer (0, 5, or 8).
        apply: Whether to actually send mode change command.

    Returns:
        Process exit code (0 for success, non-zero for failure).
    """
    if mode not in OFFICIAL_MODE_INT_TO_ENUM:
        supported = ", ".join(str(v) for v in sorted(OFFICIAL_MODE_INT_TO_ENUM))
        print(f"ERROR: Unsupported official mode '{mode}'. Supported: {supported}")
        return 2

    mode_enum = OFFICIAL_MODE_INT_TO_ENUM[mode]
    print("Initializing official client...")
    try:
        client = await SigenOfficial.create_from_env()
    except Exception as exc:
        print(f"ERROR: Official client initialization failed: {exc}")
        return 3

    print("Official client initialized.")

    # Step 1: read mode
    try:
        current_mode = await client.get_operational_mode()
        _print_json("Current Mode", current_mode)
    except Exception as exc:
        print(f"ERROR: get_operational_mode failed: {exc}")
        return 4

    # Step 2: optional write test
    if not apply:
        print(
            f"DRY-RUN: Official auth/read succeeded. Would set mode {mode} ({mode_enum}) with --apply."
        )
        return 0

    print(f"Applying mode {mode} ({mode_enum})...")
    try:
        set_response = await client.set_operational_mode(mode)
        _print_json("Set Mode Response", set_response)
    except Exception as exc:
        print(f"ERROR: set_operational_mode failed: {exc}")
        return 5

    # Step 3: verify post-set mode read
    try:
        after_mode = await client.get_operational_mode()
        _print_json("Mode After Set", after_mode)
    except Exception as exc:
        print(f"WARNING: Mode set call succeeded but follow-up read failed: {exc}")
        return 6

    print("SUCCESS: Official mode-set access is working.")
    return 0


async def main() -> int:
    """Parse args and execute probe."""
    parser = argparse.ArgumentParser(description="Probe official mode-switch access.")
    parser.add_argument(
        "--mode",
        type=int,
        default=0,
        help="Official mode integer to test (0, 5, or 8). Default: 0",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually send set_operational_mode request. Otherwise dry-run read-only.",
    )
    parser.add_argument(
        "--auth-mode",
        choices=["account", "key"],
        help="Override SIGEN_OFFICIAL_AUTH_MODE for this run.",
    )
    parser.add_argument(
        "--strict-official-only",
        action="store_true",
        help="Set SIGEN_OFFICIAL_STRICT_ONLY=true for this run.",
    )
    args = parser.parse_args()

    if args.auth_mode:
        os.environ["SIGEN_OFFICIAL_AUTH_MODE"] = args.auth_mode

    if args.strict_official_only:
        os.environ["SIGEN_OFFICIAL_STRICT_ONLY"] = "true"

    return await run_probe(mode=args.mode, apply=args.apply)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

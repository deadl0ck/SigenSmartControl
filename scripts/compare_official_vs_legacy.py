"""Compare legacy vs official Sigen API payloads.

This script performs read-only calls against both clients and prints:
- Current mode payloads
- Energy flow payloads
- Supported mode lists
- Field-path differences for dict payloads

Usage:
    python scripts/compare_official_vs_legacy.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from sigen import Sigen

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from integrations.sigen_official import SigenOfficial


def _print_json(title: str, payload: Any) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2, default=str))


def _flatten_paths(payload: Any, prefix: str = "") -> set[str]:
    paths: set[str] = set()

    if isinstance(payload, dict):
        for key, value in payload.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            paths.add(path)
            paths.update(_flatten_paths(value, path))
        return paths

    if isinstance(payload, list):
        # Keep list structure coarse to avoid noisy index diffs.
        list_path = f"{prefix}[]" if prefix else "[]"
        paths.add(list_path)
        for item in payload[:5]:
            paths.update(_flatten_paths(item, list_path))
        return paths

    return paths


def _print_path_diff(name: str, legacy_payload: Any, official_payload: Any) -> None:
    legacy_paths = _flatten_paths(legacy_payload)
    official_paths = _flatten_paths(official_payload)

    only_legacy = sorted(legacy_paths - official_paths)
    only_official = sorted(official_paths - legacy_paths)
    common = sorted(legacy_paths & official_paths)

    print(f"\n=== Field Diff: {name} ===")
    print(f"Common paths: {len(common)}")
    print(f"Legacy-only paths: {len(only_legacy)}")
    for path in only_legacy[:40]:
        print(f"  - {path}")
    if len(only_legacy) > 40:
        print(f"  ... ({len(only_legacy) - 40} more)")

    print(f"Official-only paths: {len(only_official)}")
    for path in only_official[:40]:
        print(f"  + {path}")
    if len(only_official) > 40:
        print(f"  ... ({len(only_official) - 40} more)")


async def _build_legacy_client() -> Sigen:
    load_dotenv()
    username = os.getenv("SIGEN_USERNAME")
    password = os.getenv("SIGEN_PASSWORD")
    if not username or not password:
        raise RuntimeError("SIGEN_USERNAME and SIGEN_PASSWORD must be set in .env")
    legacy = Sigen(username=username, password=password)
    await legacy.async_initialize()
    return legacy


async def main() -> int:
    load_dotenv()

    print("Initializing legacy client...")
    legacy = await _build_legacy_client()
    print("Legacy client initialized.")

    print("Initializing official client...")
    official = await SigenOfficial.create_from_env()
    print("Official client initialized.")

    legacy_mode = await legacy.get_operational_mode()
    official_mode = await official.get_operational_mode()
    _print_json("Legacy Current Mode", legacy_mode)
    _print_json("Official Current Mode", official_mode)
    _print_path_diff("Current Mode", legacy_mode, official_mode)

    legacy_energy = await legacy.get_energy_flow()
    official_energy = await official.get_energy_flow()
    _print_json("Legacy Energy Flow", legacy_energy)
    _print_json("Official Energy Flow", official_energy)
    _print_path_diff("Energy Flow", legacy_energy, official_energy)

    legacy_modes = await legacy.get_operational_modes()
    official_modes = await official.get_operational_modes()
    _print_json("Legacy Supported Modes", legacy_modes)
    _print_json("Official Supported Modes", official_modes)
    _print_path_diff("Supported Modes", legacy_modes, official_modes)

    try:
        official_summary = await official.get_system_summary()
        _print_json("Official System Summary", official_summary)
        print("\nNote: legacy client in this project does not expose system summary endpoint.")
    except Exception as exc:
        print(f"\nOfficial system summary unavailable: {exc}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

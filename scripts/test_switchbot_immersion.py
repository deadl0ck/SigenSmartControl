"""Test SwitchBot immersion heater switch by sending a turn-on command.

Reads credentials and device ID from .env and config. Use this to verify
your SWITCHBOT_TOKEN, SWITCHBOT_SECRET, and SWITCHBOT_IMMERSION_DEVICE_ID
are correct before enabling the feature in settings.py.

Usage:
    python scripts/test_switchbot_immersion.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from config.constants import SWITCHBOT_IMMERSION_DEVICE_ID, SWITCHBOT_SECRET, SWITCHBOT_TOKEN
from integrations.switchbot_interaction import get_device_status, turn_on


async def main() -> int:
    """Turn on the SwitchBot immersion switch and print the response."""
    if not SWITCHBOT_TOKEN or not SWITCHBOT_SECRET:
        print("ERROR: SWITCHBOT_TOKEN and SWITCHBOT_SECRET must be set in .env")
        return 1
    if not SWITCHBOT_IMMERSION_DEVICE_ID:
        print("ERROR: SWITCHBOT_IMMERSION_DEVICE_ID must be set in .env")
        return 1

    print(f"Device ID : {SWITCHBOT_IMMERSION_DEVICE_ID}")

    print("Fetching current status...")
    try:
        status = await get_device_status(SWITCHBOT_IMMERSION_DEVICE_ID, SWITCHBOT_TOKEN, SWITCHBOT_SECRET)
        power = status.get("body", {}).get("power", "unknown")
        print(f"Current state: {power}")
    except Exception as exc:
        print(f"WARNING: Could not fetch status: {exc}")

    print("Sending turn-on command...")
    try:
        result = await turn_on(SWITCHBOT_IMMERSION_DEVICE_ID, SWITCHBOT_TOKEN, SWITCHBOT_SECRET)
        code = result.get("statusCode")
        msg = result.get("message", "")
        print(f"Response: statusCode={code} message={msg!r}")
        if code == 100:
            print("SUCCESS — switch turned on.")
        else:
            print(f"UNEXPECTED response code {code}. Full response: {result}")
            return 1
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

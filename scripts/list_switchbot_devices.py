"""List all SwitchBot devices on the account.

Use this to find the device ID for your immersion heater switch before
setting SWITCHBOT_IMMERSION_DEVICE_ID in .env.

Usage:
    python scripts/list_switchbot_devices.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from config.constants import SWITCHBOT_SECRET, SWITCHBOT_TOKEN
from integrations.switchbot_interaction import SWITCHBOT_API_BASE_URL, _build_headers
from utils.terminal_formatting import render_table

import aiohttp


async def main() -> int:
    """Fetch and display all SwitchBot devices on the account."""
    if not SWITCHBOT_TOKEN or not SWITCHBOT_SECRET:
        print("ERROR: SWITCHBOT_TOKEN and SWITCHBOT_SECRET must be set in .env")
        return 1

    url = f"{SWITCHBOT_API_BASE_URL}/devices"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url,
            headers=_build_headers(SWITCHBOT_TOKEN, SWITCHBOT_SECRET),
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

    devices = data.get("body", {}).get("deviceList", [])
    if not devices:
        print("No devices found on this account.")
        return 0

    rows = [
        [d.get("deviceId", ""), d.get("deviceName", ""), d.get("deviceType", ""), str(d.get("hubDeviceId", ""))]
        for d in devices
    ]
    print()
    print(render_table(["Device ID", "Name", "Type", "Hub ID"], rows, title="SwitchBot Devices"))
    print()
    print("Set SWITCHBOT_IMMERSION_DEVICE_ID in .env to the Device ID of your immersion switch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

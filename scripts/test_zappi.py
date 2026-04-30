"""Test Zappi integration — fetches live status and today's charge totals.

Reads credentials from .env. Use this to verify your MYENERGI_HUB_SERIAL
and MYENERGI_API_KEY are correct before enabling the feature in the scheduler.

Usage:
    python scripts/test_zappi.py
    python scripts/test_zappi.py --raw      # also print raw API responses
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv()

from integrations.zappi_client import ZappiClient
from integrations.zappi_interaction import ZappiInteraction


def _fmt_kw(watts: int) -> str:
    return f"{watts / 1000:.2f} kW" if watts >= 1000 else f"{watts} W"


async def main(show_raw: bool) -> int:
    print("─" * 60)
    print("  myenergi Zappi — connection test")
    print("─" * 60)

    try:
        client = ZappiClient.create_from_env()
    except RuntimeError as exc:
        print(f"\nERROR: {exc}")
        print("\nAdd the missing entries to your .env file:")
        print("  MYENERGI_HUB_SERIAL=your_hub_serial")
        print("  MYENERGI_API_KEY=your_api_key")
        return 1

    print(f"\nHub serial : {client._hub_serial}")

    # --- Server discovery ---
    print("\n[1/3] Discovering API server...")
    try:
        server = await client._get_server()
        print(f"      API server  : {server}")
        print(f"      Zappi serial: {client._zappi_serial} (auto-discovered)")
    except Exception as exc:
        print(f"      FAILED: {exc}")
        return 1

    interaction = ZappiInteraction(client)
    today = date.today()

    # --- Live status (also discovers Zappi serial) ---
    print("\n[2/3] Fetching live status...")
    try:
        raw_status = await client.get_live_status()
        if show_raw:
            print(f"      Raw response: {raw_status}")
        if client._zappi_serial:
            print(f"      Zappi serial  : {client._zappi_serial} (auto-discovered)")
        status = await interaction.get_live_status()
        if status is None:
            print("      WARNING: No Zappi devices found on this hub.")
        else:
            print(f"      Status        : {status['status_text']}")
            print(f"      Mode          : {status['mode_text']}")
            print(f"      Charge power  : {_fmt_kw(status['charge_power_w'])}")
            print(f"      Diverted power: {_fmt_kw(status['diverted_power_w'])}")
            print(f"      Session energy: {status['session_energy_kwh']:.2f} kWh")
    except Exception as exc:
        print(f"      FAILED: {exc}")
        return 1

    # --- Today's totals ---
    print(f"\n[3/3] Fetching today's charge totals ({today})...")
    try:
        raw_history = await client.get_daily_history(today)
        if show_raw:
            print(f"      Raw response ({len(raw_history)} hourly records): {raw_history[:3]}{'...' if len(raw_history) > 3 else ''}")
        totals = await interaction.get_daily_totals(today)
        if totals is None:
            print("      WARNING: No history data returned.")
        else:
            print(f"      Total to EV   : {totals['total_kwh']:.2f} kWh")
            print(f"      Solar diverted: {totals['diverted_kwh']:.2f} kWh")
            print(f"      Grid boost    : {totals['boosted_kwh']:.2f} kWh")
    except Exception as exc:
        print(f"      FAILED: {exc}")
        return 1

    print("\n" + "─" * 60)
    print("  All checks passed — Zappi integration is working.")
    print("─" * 60)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test myenergi Zappi connection")
    parser.add_argument("--raw", action="store_true", help="Print raw API responses")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(args.raw)))

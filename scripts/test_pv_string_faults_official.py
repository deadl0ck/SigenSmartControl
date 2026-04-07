"""Check per-string PV balance using the official Sigen device realtime API.

This script is read-only and is intended as a quick diagnostic for PV string
imbalance (for example shading, soiling, or weak string behavior). It prints a
single snapshot and warns when an active string is significantly below the
active-string average power.

Usage:
  python scripts/test_pv_string_faults_official.py --serial <SERIAL>
  python scripts/test_pv_string_faults_official.py --serial <SERIAL> --warn-pct 30

You can also set SIGEN_INVERTER_SERIAL in .env and omit --serial.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Allow running from project root or scripts/ directory.
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from integrations.sigen_official import SigenOfficial


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("test_pv_string_faults_official")


def _as_float(value: Any) -> float | None:
    """Convert mixed API scalar values to float when possible.

    Args:
        value: Raw scalar value from official API payload.

    Returns:
        Float value when conversion succeeds, otherwise None.
    """
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _collect_string_rows(real_time_info: dict[str, Any]) -> list[dict[str, float | int]]:
    """Build PV string rows from realtime payload keys.

    Args:
        real_time_info: Device realtime info dictionary.

    Returns:
        A list of rows containing string index, voltage, current, and power.
    """
    rows: list[dict[str, float | int]] = []
    for index in range(1, 5):
        voltage = _as_float(real_time_info.get(f"pv{index}Voltage"))
        current = _as_float(real_time_info.get(f"pv{index}Current"))

        if voltage is None and current is None:
            continue

        power_kw = 0.0
        if voltage is not None and current is not None:
            power_kw = (voltage * current) / 1000.0

        rows.append(
            {
                "string": index,
                "voltage_v": voltage if voltage is not None else 0.0,
                "current_a": current if current is not None else 0.0,
                "power_kw": power_kw,
            }
        )
    return rows


def _imbalance_findings(rows: list[dict[str, float | int]], warn_pct: float) -> list[str]:
    """Generate imbalance findings relative to active-string average power.

    Args:
        rows: Per-string computed rows.
        warn_pct: Warning threshold in percent below average.

    Returns:
        Human-readable findings.
    """
    active_rows = [row for row in rows if float(row["power_kw"]) > 0.05]
    if len(active_rows) < 2:
        return ["Not enough active strings for imbalance analysis."]

    average_kw = sum(float(row["power_kw"]) for row in active_rows) / len(active_rows)
    if average_kw <= 0:
        return ["Average active-string power is zero; cannot compute imbalance."]

    findings: list[str] = []
    for row in active_rows:
        power_kw = float(row["power_kw"])
        deficit_pct = (average_kw - power_kw) / average_kw * 100.0
        if deficit_pct >= warn_pct:
            findings.append(
                "String "
                f"{int(row['string'])} is {deficit_pct:.1f}% below active average "
                f"({power_kw:.2f} kW vs avg {average_kw:.2f} kW)."
            )

    if not findings:
        findings.append("No major string imbalance detected among active strings.")
    return findings


async def main() -> None:
    """Run one official API realtime query and print PV string diagnostics."""
    parser = argparse.ArgumentParser(
        description=(
            "Read per-string PV telemetry from official API and flag possible string imbalance."
        )
    )
    parser.add_argument(
        "--serial",
        default=os.getenv("SIGEN_INVERTER_SERIAL", "").strip(),
        help="Inverter/AIO serial number (defaults to SIGEN_INVERTER_SERIAL).",
    )
    parser.add_argument(
        "--warn-pct",
        type=float,
        default=30.0,
        help="Warn when a string is at least this percent below active-string average.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print only the raw device realtime payload as JSON and exit.",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help="List available device serials for this system and exit.",
    )
    args = parser.parse_args()

    os.environ["SIGEN_OFFICIAL_STRICT_ONLY"] = "true"

    logger.info("Initializing official Sigen client...")
    client = await SigenOfficial.create_from_env()
    logger.info("Official client initialized.")

    if args.list_devices:
        try:
            devices = await client.get_device_list()
        except RuntimeError as exc:
            raise SystemExit(f"Device list query failed: {exc}") from exc

        if not devices:
            print("No devices returned by official inventory endpoint for this account/system.")
            return

        print("Available devices")
        print("=================")
        for device in devices:
            serial = device.get("serialNumber") or device.get("snCode") or "UNKNOWN"
            device_type = device.get("deviceType", "UNKNOWN")
            status = device.get("status", "UNKNOWN")
            print(f"- serial={serial} type={device_type} status={status}")
        return

    serial = args.serial.strip()
    if not serial:
        raise SystemExit(
            "Missing serial number. Provide --serial or set SIGEN_INVERTER_SERIAL in .env."
        )

    if serial == str(client.system_id):
        raise SystemExit(
            "The --serial value matches systemId. Device realtime requires a device serialNumber "
            "(for example inverter/AIO SN), not systemId."
        )

    try:
        device_payload = await client.get_device_realtime(serial)
    except RuntimeError as exc:
        discovered_devices: list[dict[str, Any]] | None = None
        try:
            discovered_devices = await client.get_device_list()
        except Exception:
            discovered_devices = None

        discovery_hint = ""
        if discovered_devices:
            lines = []
            for device in discovered_devices:
                serial_hint = device.get("serialNumber") or device.get("snCode") or "UNKNOWN"
                type_hint = device.get("deviceType", "UNKNOWN")
                lines.append(f"  - serial={serial_hint} type={type_hint}")
            discovery_hint = "\nDiscovered device serials for this account/system:\n" + "\n".join(lines)

        raise SystemExit(
            f"Device realtime query failed: {exc}\n"
            "Tip: confirm --serial is a device serialNumber from the Sigen app, not systemId."
            f"{discovery_hint}"
        ) from exc

    if args.json:
        print(json.dumps(device_payload, indent=2, default=str))
        return

    real_time_info = device_payload.get("realTimeInfo", {})
    if not isinstance(real_time_info, dict):
        real_time_info = {}

    rows = _collect_string_rows(real_time_info)
    pv_power_kw = _as_float(real_time_info.get("pvPower"))
    pv_total_power_kw = _as_float(real_time_info.get("pvTotalPower"))

    print()
    print("PV String Fault Check (Official API)")
    print("===================================")
    print(f"System ID: {client.system_id}")
    print(f"Serial:    {serial}")
    print(
        "pvPower:   "
        + (f"{pv_power_kw:.3f} kW" if pv_power_kw is not None else "N/A")
    )
    print(
        "pvTotal:   "
        + (f"{pv_total_power_kw:.3f} kW" if pv_total_power_kw is not None else "N/A")
    )
    print()

    if not rows:
        print("No pvNVoltage/pvNCurrent fields were returned for this device.")
        print("Use --json to inspect full payload and confirm device/serial type.")
        return

    print("String  Voltage(V)  Current(A)  Power(kW)")
    print("------  ----------  ----------  ---------")
    for row in rows:
        print(
            f"{int(row['string']):>6}  "
            f"{float(row['voltage_v']):>10.1f}  "
            f"{float(row['current_a']):>10.2f}  "
            f"{float(row['power_kw']):>9.3f}"
        )

    print()
    print("Findings")
    print("========")
    for finding in _imbalance_findings(rows, args.warn_pct):
        print(f"- {finding}")


if __name__ == "__main__":
    asyncio.run(main())

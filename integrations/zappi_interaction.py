"""Higher-level wrapper for Zappi API responses.

Extracts the fields used by the scheduler and email notifications from raw
myenergi API payloads, and exposes two main async methods for live status
and daily energy totals.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from integrations.zappi_client import ZappiClient


_ZMO_LABELS = {1: "Fast", 2: "Eco", 3: "Eco+"}
_PST_LABELS = {
    "A": "EV Disconnected",
    "B1": "EV Connected",
    "B2": "Waiting for EV",
    "C1": "Charging",
    "C2": "Charging",
    "F": "Fault",
}


def _mode_text(zmo: Any) -> str:
    try:
        return _ZMO_LABELS.get(int(zmo), f"Mode {zmo}")
    except (TypeError, ValueError):
        return "Unknown"


def _status_text(pst: Any) -> str:
    return _PST_LABELS.get(str(pst), str(pst))


def _is_charging(pst: Any) -> bool:
    return str(pst) in {"C1", "C2"}


class ZappiInteraction:
    """Higher-level interface for Zappi live status and daily history."""

    def __init__(self, client: ZappiClient) -> None:
        self._client = client

    @classmethod
    def create_from_env(cls) -> "ZappiInteraction":
        """Create a ZappiInteraction from environment variables.

        Raises:
            RuntimeError: If any required credential is missing.
        """
        return cls(ZappiClient.create_from_env())

    async def get_live_status(self) -> dict[str, Any] | None:
        """Return a normalized live-status snapshot.

        Returns:
            Dict with keys: status_text, charge_power_w, diverted_power_w,
            session_energy_kwh, mode_text, is_charging. Returns None on error.
        """
        records = await self._client.get_live_status()
        entry = records[0] if records else None
        if entry is None:
            return None

        pst = entry.get("pst")
        zmo = entry.get("zmo")
        che = entry.get("che", 0.0)
        div = entry.get("div", 0)

        ct_powers = [
            entry.get(f"ectp{i}", 0) or 0
            for i in range(1, 7)
            if f"ectp{i}" in entry
        ]
        charge_power_w = abs(int(div)) if div else (abs(ct_powers[0]) if ct_powers else 0)

        return {
            "status_text": _status_text(pst),
            "charge_power_w": int(charge_power_w),
            "diverted_power_w": int(div) if div is not None else 0,
            "session_energy_kwh": float(che) if che is not None else 0.0,
            "mode_text": _mode_text(zmo),
            "is_charging": _is_charging(pst),
        }

    async def get_daily_totals(self, target_date: date) -> dict[str, Any] | None:
        """Return aggregated daily energy totals for the given date.

        Args:
            target_date: Local date for which to fetch hourly history.

        Returns:
            Dict with keys: total_kwh, diverted_kwh, imported_kwh. Returns None on error.
        """
        records = await self._client.get_daily_history(target_date)
        if not records:
            return {"total_kwh": 0.0, "diverted_kwh": 0.0, "boosted_kwh": 0.0}

        # myenergi history API returns energy values in joules.
        # Divide by 3,600,000 to convert to kWh.
        # h1d = solar energy diverted to EV; h1b = grid energy boosted to EV.
        # imp = total site grid import — NOT EV-specific, excluded here.
        total_div_j = 0
        total_boost_j = 0
        for record in records:
            total_div_j += int(record.get("h1d", 0) or 0)
            total_boost_j += int(record.get("h1b", 0) or 0)

        diverted_kwh = total_div_j / 3_600_000.0
        boosted_kwh = total_boost_j / 3_600_000.0
        total_kwh = diverted_kwh + boosted_kwh

        return {
            "total_kwh": round(total_kwh, 3),
            "diverted_kwh": round(diverted_kwh, 3),
            "boosted_kwh": round(boosted_kwh, 3),
        }

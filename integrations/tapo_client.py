"""Tapo P110 smart-plug client (EV granny charger) using python-kasa (KLAP)."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

TAPO_TIMEOUT_SECONDS = 15.0


class TapoPlug:
    """Reads live power and energy totals from a Tapo P110 plug."""

    def __init__(self, host: str, username: str, password: str, name: str = "Granny Charger") -> None:
        self._host = host
        self._username = username
        self._password = password
        self._name = name

    @classmethod
    def create_from_env(cls) -> "TapoPlug":
        """Build from TAPO_HOST / TAPO_USERNAME / TAPO_PASSWORD (+ optional TAPO_NAME)."""
        host = os.getenv("TAPO_HOST", "").strip()
        username = os.getenv("TAPO_USERNAME", "").strip()
        password = os.getenv("TAPO_PASSWORD", "").strip()
        if not (host and username and password):
            raise RuntimeError("TAPO_HOST, TAPO_USERNAME and TAPO_PASSWORD must all be set")
        return cls(host, username, password, os.getenv("TAPO_NAME", "Granny Charger").strip() or "Granny Charger")

    async def _read(self) -> dict[str, Any]:
        from kasa import Credentials, Discover

        dev = await Discover.discover_single(
            self._host,
            credentials=Credentials(self._username, self._password),
            timeout=10,
        )
        try:
            await dev.update()
            energy = dev.modules.get("Energy")
            return {
                "name": self._name,
                "is_on": bool(dev.is_on),
                "power_w": round(float(energy.current_consumption), 1) if energy else None,
                "today_kwh": round(float(energy.consumption_today), 3) if energy else None,
                "month_kwh": round(float(energy.consumption_this_month), 3) if energy else None,
                "rssi": dev.rssi,
            }
        finally:
            await dev.disconnect()

    async def get_live_status(self) -> dict[str, Any] | None:
        """Return a normalized status dict, or None when the plug is unreachable."""
        try:
            return await asyncio.wait_for(self._read(), TAPO_TIMEOUT_SECONDS)
        except Exception as exc:
            logger.warning("[TAPO] Could not read plug at %s: %r", self._host, exc)
            return None

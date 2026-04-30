"""Thin async HTTP client for the myenergi Zappi EV charger API.

Uses HTTP Digest authentication with hub serial and API key. Performs
director-based server discovery on first call and caches the result.
The Zappi device serial is auto-discovered from the first status call
and cached for use in history endpoint URLs.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import aiohttp


_DIRECTOR_URL = "https://director.myenergi.net/cgi-jstatus-Z"
_SERVER_HEADER = "x_myenergi-asn"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=10)


class ZappiClient:
    """Async HTTP client for myenergi Zappi API with director-based discovery."""

    def __init__(self, hub_serial: str, api_key: str) -> None:
        self._hub_serial = hub_serial
        self._api_key = api_key
        self._server: str | None = None
        self._zappi_serial: str | None = None

    @classmethod
    def create_from_env(cls) -> "ZappiClient":
        """Create a ZappiClient from environment variables.

        Raises:
            RuntimeError: If any required credential is missing.
        """
        hub_serial = os.getenv("MYENERGI_HUB_SERIAL", "").strip()
        api_key = os.getenv("MYENERGI_API_KEY", "").strip()
        missing = [
            name
            for name, val in (
                ("MYENERGI_HUB_SERIAL", hub_serial),
                ("MYENERGI_API_KEY", api_key),
            )
            if not val
        ]
        if missing:
            raise RuntimeError(
                f"Missing required myenergi environment variables: {', '.join(missing)}"
            )
        return cls(hub_serial, api_key)

    def _make_session(self) -> aiohttp.ClientSession:
        auth = aiohttp.DigestAuthMiddleware(self._hub_serial, self._api_key)
        return aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT, middlewares=(auth,))

    async def _discover_server(self) -> tuple[str, list[dict[str, Any]]]:
        """Call the director endpoint, return the API server hostname and initial device list.

        The director response both carries the assigned-server header and contains
        a fresh Zappi status payload, so we parse both in one round-trip.
        """
        async with self._make_session() as session:
            async with session.get(_DIRECTOR_URL) as resp:
                resp.raise_for_status()
                server = resp.headers.get(_SERVER_HEADER, "").strip()
                if not server:
                    raise RuntimeError(
                        f"myenergi director response missing '{_SERVER_HEADER}' header"
                    )
                data = await resp.json(content_type=None)
                devices = data.get("zappi", [])
                return server, devices

    async def _get_server(self) -> str:
        if self._server is None:
            self._server, devices = await self._discover_server()
            if devices and self._zappi_serial is None:
                self._zappi_serial = str(devices[0].get("sno", ""))
        return self._server

    async def get_live_status(self) -> list[dict[str, Any]]:
        """Fetch live status for all Zappi devices on this hub.

        Also caches the first Zappi serial found for use in history calls.

        Returns:
            Raw list of Zappi status objects from the API response.
        """
        server = await self._get_server()
        url = f"https://{server}/cgi-jstatus-Z"
        async with self._make_session() as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                devices = data.get("zappi", [])
                if devices and self._zappi_serial is None:
                    self._zappi_serial = str(devices[0].get("sno", ""))
                return devices

    async def get_daily_history(self, target_date: date) -> list[dict[str, Any]]:
        """Fetch hourly Zappi history for the given date.

        Requires the Zappi serial, which is auto-discovered on the first
        get_live_status() call. Call get_live_status() at least once first.

        Args:
            target_date: The local date for which to retrieve hourly records.

        Returns:
            Raw list of hourly history objects from the API response.
        """
        if not self._zappi_serial:
            raise RuntimeError(
                "Zappi serial not yet discovered — call get_live_status() first."
            )
        server = await self._get_server()
        year = target_date.year
        month = target_date.month
        day = target_date.day
        url = (
            f"https://{server}/cgi-jdayhour-Z{self._zappi_serial}"
            f"-{year}-{month}-{day}"
        )
        async with self._make_session() as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
                return data.get(f"U{self._zappi_serial}", [])

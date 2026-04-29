"""switchbot_interaction.py
--------------------------
SwitchBot Cloud API v1.1 client for controlling plug/switch devices.

Auth uses HMAC-SHA256 over (token + timestamp_ms + nonce) signed with the secret.
All credentials are injected at call time so this module has no global state.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
import uuid
from typing import Any

import aiohttp

from config.settings import SWITCHBOT_API_TIMEOUT_SECONDS


SWITCHBOT_API_BASE_URL = "https://api.switch-bot.com/v1.1"
_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=SWITCHBOT_API_TIMEOUT_SECONDS)


def _build_headers(token: str, secret: str) -> dict[str, str]:
    """Build SwitchBot v1.1 HMAC-signed request headers.

    Args:
        token: SwitchBot API token from the app.
        secret: SwitchBot API secret from the app.

    Returns:
        Dict of headers required for every authenticated request.
    """
    t = str(int(round(time.time() * 1000)))
    nonce = str(uuid.uuid4())
    string_to_sign = f"{token}{t}{nonce}"
    sign = base64.b64encode(
        hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode("utf-8")
    return {
        "Authorization": token,
        "sign": sign,
        "nonce": nonce,
        "t": t,
        "Content-Type": "application/json",
    }


async def get_device_status(device_id: str, token: str, secret: str) -> dict[str, Any]:
    """Return the current status payload for a SwitchBot device.

    Args:
        device_id: SwitchBot device ID (from GET /v1.1/devices).
        token: SwitchBot API token.
        secret: SwitchBot API secret.

    Returns:
        Parsed JSON response dict from the SwitchBot API.

    Raises:
        aiohttp.ClientResponseError: On non-2xx HTTP status.
    """
    url = f"{SWITCHBOT_API_BASE_URL}/devices/{device_id}/status"
    async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
        async with session.get(url, headers=_build_headers(token, secret)) as resp:
            resp.raise_for_status()
            return await resp.json()


async def _send_command(device_id: str, command: str, token: str, secret: str) -> dict[str, Any]:
    """Send a named command to a SwitchBot device.

    Args:
        device_id: SwitchBot device ID.
        command: Command string (e.g. 'turnOn', 'turnOff').
        token: SwitchBot API token.
        secret: SwitchBot API secret.

    Returns:
        Parsed JSON response dict.

    Raises:
        aiohttp.ClientResponseError: On non-2xx HTTP status.
    """
    url = f"{SWITCHBOT_API_BASE_URL}/devices/{device_id}/commands"
    payload = {"command": command, "parameter": "default", "commandType": "command"}
    async with aiohttp.ClientSession(timeout=_REQUEST_TIMEOUT) as session:
        async with session.post(url, headers=_build_headers(token, secret), json=payload) as resp:
            resp.raise_for_status()
            return await resp.json()


async def turn_on(device_id: str, token: str, secret: str) -> dict[str, Any]:
    """Turn a SwitchBot switch on.

    Args:
        device_id: SwitchBot device ID.
        token: SwitchBot API token.
        secret: SwitchBot API secret.

    Returns:
        Parsed JSON response dict.
    """
    return await _send_command(device_id, "turnOn", token, secret)


async def turn_off(device_id: str, token: str, secret: str) -> dict[str, Any]:
    """Turn a SwitchBot switch off.

    Args:
        device_id: SwitchBot device ID.
        token: SwitchBot API token.
        secret: SwitchBot API secret.

    Returns:
        Parsed JSON response dict.
    """
    return await _send_command(device_id, "turnOff", token, secret)

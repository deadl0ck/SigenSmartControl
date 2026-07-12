"""
sigen_auth.py
---------------
Reusable authentication for Sigen inverter API.
Implements a singleton pattern for efficient long-running use.
Handles .env loading, logging, and type hints.
"""

import os
import logging
from typing import Optional
import aiohttp
from dotenv import load_dotenv
from sigen import Sigen
from integrations.sigen_official import SigenOfficial

# Configure module-level logger
logger = logging.getLogger(__name__)

# Singleton instance cache
_sigen_instance: Optional[object] = None

# Sigen's CloudFront WAF blocks requests carrying aiohttp's default User-Agent
# (e.g. "Python/3.13 aiohttp/3.13.5") with a 403 "Request blocked" response,
# even with valid credentials. The legacy `sigen` package creates a fresh
# aiohttp.ClientSession() with no custom headers on every API call (auth,
# refresh, mode get/set, energy flow), so we patch the default headers here
# to unblock it without forking the vendored package.
_BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_original_client_session_init = aiohttp.ClientSession.__init__


def _client_session_init_with_browser_ua(self, *args, headers=None, **kwargs):
    merged_headers = dict(headers) if headers else {}
    merged_headers.setdefault("User-Agent", _BROWSER_USER_AGENT)
    _original_client_session_init(self, *args, headers=merged_headers, **kwargs)


aiohttp.ClientSession.__init__ = _client_session_init_with_browser_ua

async def get_sigen_instance() -> object:
    """
    Loads credentials from .env and returns a singleton initialized client.
    Supports both legacy and official client implementations.

    Selection is controlled by SIGEN_CLIENT_IMPL:
      - legacy (default): use third-party sigen package client
      - official: use OpenAPI client in integrations.sigen_official

    Raises RuntimeError if required credentials are missing.

    Returns:
        Authenticated client object implementing required Sigen protocol methods.
    """
    global _sigen_instance
    if _sigen_instance is not None:
        logger.debug("Returning cached Sigen instance.")
        return _sigen_instance

    load_dotenv()
    client_impl = os.getenv("SIGEN_CLIENT_IMPL", "legacy").strip().lower()

    if client_impl == "official":
        logger.info("Authenticating with official Sigen OpenAPI client...")
        official = await SigenOfficial.create_from_env()
        logger.info("Official Sigen authentication successful.")
        _sigen_instance = official
        return official

    username: Optional[str] = os.getenv("SIGEN_USERNAME")
    password: Optional[str] = os.getenv("SIGEN_PASSWORD")
    if not username or not password:
        logger.error("SIGEN_USERNAME and SIGEN_PASSWORD must be set in .env")
        raise RuntimeError("SIGEN_USERNAME and SIGEN_PASSWORD must be set in .env")

    logger.info("Authenticating with legacy Sigen API client...")
    sigen = Sigen(username=username, password=password)
    await sigen.async_initialize()
    logger.info("Legacy Sigen authentication successful.")
    _sigen_instance = sigen
    return sigen


async def refresh_sigen_instance() -> object:
    """Force full re-authentication and return a newly initialized client.

    This helper clears the cached singleton and performs a fresh auth flow,
    which is used as a fallback when token refresh fails at runtime.

    Returns:
        Newly authenticated client instance.
    """
    global _sigen_instance
    _sigen_instance = None
    return await get_sigen_instance()

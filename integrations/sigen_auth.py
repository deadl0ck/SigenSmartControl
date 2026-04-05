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
from dotenv import load_dotenv
from sigen import Sigen
from integrations.sigen_official import SigenOfficial

# Configure module-level logger
logger = logging.getLogger(__name__)

# Singleton instance cache
_sigen_instance: Optional[object] = None

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

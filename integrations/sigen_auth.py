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

# Configure module-level logger
logger = logging.getLogger(__name__)

# Singleton instance cache
_sigen_instance: Optional[Sigen] = None

async def get_sigen_instance() -> Sigen:
    """
    Loads credentials from .env and returns a singleton, initialized Sigen instance.
    Only authenticates once per process. Handles token refresh automatically.
    Raises RuntimeError if credentials are missing.
    Returns:
        Sigen: An authenticated and initialized Sigen API client.
    """
    global _sigen_instance
    if _sigen_instance is not None:
        logger.debug("Returning cached Sigen instance.")
        return _sigen_instance

    load_dotenv()
    username: Optional[str] = os.getenv("SIGEN_USERNAME")
    password: Optional[str] = os.getenv("SIGEN_PASSWORD")
    if not username or not password:
        logger.error("SIGEN_USERNAME and SIGEN_PASSWORD must be set in .env")
        raise RuntimeError("SIGEN_USERNAME and SIGEN_PASSWORD must be set in .env")

    logger.info("Authenticating with Sigen API...")
    sigen = Sigen(username=username, password=password)
    await sigen.async_initialize()
    logger.info("Sigen authentication successful.")
    _sigen_instance = sigen
    return sigen

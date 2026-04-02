"""
check_modes.py
--------------
Script to print all available Sigen inverter operational modes (labels and values).
Uses singleton authentication from sigen_auth.py.
Safe to run: does not change any inverter settings.
"""

import asyncio
import logging
from typing import Any
from sigen_interaction import SigenInteraction

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def main() -> None:
    """
    Connects to Sigen inverter and prints all available operational mode labels and values.
    """
    sigen = await SigenInteraction.create()
    operational_modes: list[dict[str, Any]] = await sigen.get_operational_modes()
    logger.info("Available Sigen operational modes:")
    for mode in operational_modes:
        logger.info(f"  Label: {mode['label']}, Value: {mode['value']}")

if __name__ == "__main__":
    asyncio.run(main())

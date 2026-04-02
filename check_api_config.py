"""
check_api_config.py
-------------------
Diagnostic script to query Sigen API for current mode configuration,
including optimization settings like profit-max.
"""

import asyncio
import json
import logging
from sigen_interaction import SigenInteraction

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


async def main() -> None:
    """Query Sigen API for current mode and configuration."""
    try:
        sigen = await SigenInteraction.create()
        
        # Get current operational mode
        logger.info("Fetching current operational mode...")
        current_mode = await sigen.get_operational_mode()
        logger.info(f"Current operational mode response:\n{json.dumps(current_mode, indent=2, default=str)}")
        
        # Get available modes
        logger.info("\nFetching available operational modes...")
        modes = await sigen.get_operational_modes()
        logger.info(f"Available modes:\n{json.dumps(modes, indent=2, default=str)}")
        
        # Get energy flow (includes current status)
        logger.info("\nFetching energy flow data...")
        energy_flow = await sigen.get_energy_flow()
        logger.info(f"Energy flow response:\n{json.dumps(energy_flow, indent=2, default=str)}")
        
    except Exception as e:
        logger.error(f"Error querying Sigen API: {e}", exc_info=True)


if __name__ == "__main__":
    asyncio.run(main())

"""
set_mode_now.py
---------------
One-off script to switch the inverter to a specific mode immediately.
Usage: python scripts/set_mode_now.py <mode_value>
Example: python scripts/set_mode_now.py 1   (AI Mode)
         python scripts/set_mode_now.py 0   (Self-Powered)
"""

import asyncio
import logging
import sys

from integrations.sigen_interaction import SigenInteraction
from config.settings import SIGEN_MODES

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

MODE_LABELS = {v: k for k, v in SIGEN_MODES.items()}


async def main() -> None:
    """Read target mode from args, switch inverter, and confirm."""
    if len(sys.argv) < 2:
        print("Usage: python scripts/set_mode_now.py <mode_value>")
        for v, k in sorted(MODE_LABELS.items()):
            print(f"  {v} = {k}")
        sys.exit(1)

    try:
        target_mode = int(sys.argv[1])
    except ValueError:
        logger.error("mode_value must be an integer")
        sys.exit(1)

    label = MODE_LABELS.get(target_mode, f"UNKNOWN({target_mode})")
    logger.info("Connecting to Sigen API...")
    sigen = await SigenInteraction.create()

    logger.info("Current mode before change:")
    current = await sigen.get_operational_mode()
    logger.info("  %s", current)

    logger.info("Sending mode change → %s (value=%s)", label, target_mode)
    response = await sigen.set_operational_mode(target_mode)
    logger.info("Response: %s", response)

    logger.info("Mode after change:")
    after = await sigen.get_operational_mode()
    logger.info("  %s", after)


if __name__ == "__main__":
    asyncio.run(main())

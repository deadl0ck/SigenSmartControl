"""
test_mode_switch.py
-------------------
Test script to explore and switch Sigen operational modes.
Lists available modes from the actual API, or switches to a specified mode.

Usage:
  python scripts/test_mode_switch.py           # List available modes
  python scripts/test_mode_switch.py --list    # List available modes
  python scripts/test_mode_switch.py <mode_id> # Switch to mode (e.g., 1 for AI)
"""

import asyncio
import argparse
import json
import sys
import logging
from pathlib import Path
from typing import Any

# Allow running from project root or from scripts/ sub-directory.
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from integrations.sigen_interaction import SigenInteraction

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("test_mode_switch")


async def list_available_modes(sigen: SigenInteraction) -> None:
    """Fetch and display all available operational modes from the inverter."""
    logger.info("=" * 80)
    logger.info("AVAILABLE OPERATIONAL MODES (from actual Sigen API)")
    logger.info("=" * 80)
    
    try:
        modes = await sigen.get_operational_modes()
        logger.info(f"Raw modes response: {modes}")
        if not modes:
            logger.warning("No modes returned from API")
            return
        
        logger.info(f"Total modes available: {len(modes)}\n")
        for mode in modes:
            logger.info(f"  Label: {mode.get('label', 'N/A'):20s} | Value: {mode.get('value', 'N/A')}")
        
        logger.info("\n" + "=" * 80)
        logger.info("These are the modes supported by the API being used (sigen library)")
        logger.info("=" * 80 + "\n")
    except Exception as e:
        logger.error(f"Failed to fetch operational modes: {e}", exc_info=True)
        sys.exit(1)


async def get_current_mode(sigen: SigenInteraction) -> Any:
    """Fetch and display current operational mode."""
    try:
        current = await sigen.get_operational_mode()
        logger.info("Current operational mode response:")
        logger.info(json.dumps(current, indent=2, default=str))
        return current
    except Exception as e:
        logger.error(f"Failed to fetch current mode: {e}", exc_info=True)
        return None


async def switch_mode(sigen: SigenInteraction, mode_id: int) -> None:
    """Switch to the specified operational mode."""
    logger.info("=" * 80)
    logger.info(f"SWITCHING TO MODE: {mode_id}")
    logger.info("=" * 80)
    
    try:
        logger.info(f"\nBefore switch - fetching current mode...")
        await get_current_mode(sigen)
        logger.info(f"\nSwitching to mode {mode_id}...")
        response = await sigen.set_operational_mode(mode_id)
        
        logger.info("\n" + "-" * 80)
        logger.info("Mode switch response:")
        logger.info("-" * 80)
        logger.info(json.dumps(response, indent=2, default=str))
        
        logger.info(f"\nAfter switch - fetching new mode...")
        await get_current_mode(sigen)
        
        logger.info("\n" + "=" * 80)
        logger.info("SWITCH COMPLETE")
        logger.info("=" * 80 + "\n")
    except Exception as e:
        logger.error(f"Failed to switch mode: {e}", exc_info=True)
        sys.exit(1)


async def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Test Sigen operational modes. Lists available modes or switches to a specified mode.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python scripts/test_mode_switch.py              # List available modes
  python scripts/test_mode_switch.py --list       # List available modes
  python scripts/test_mode_switch.py 1            # Switch to mode 1 (AI)
  python scripts/test_mode_switch.py 5            # Switch to mode 5 (GRID_EXPORT/FFG)
  python scripts/test_mode_switch.py 0            # Switch to mode 0 (SELF_POWERED/MSC)
        """
    )
    parser.add_argument(
        "mode",
        nargs="?",
        type=int,
        help="Operational mode ID to switch to (optional)"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List available modes (default if no mode specified)"
    )
    
    args = parser.parse_args()
    
    try:
        logger.info("Authenticating with Sigen API...")
        sigen = await SigenInteraction.create()
        logger.info("Authentication successful.\n")
        
        # List modes if requested or no mode specified
        if args.list or (args.mode is None):
            logger.info("Fetching current operational mode...")
            await get_current_mode(sigen)
            logger.info("")
            await list_available_modes(sigen)
        
        # Switch to a specific mode if requested
        if args.mode is not None:
            await switch_mode(sigen, args.mode)
            
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

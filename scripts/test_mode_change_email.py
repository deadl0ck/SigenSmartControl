"""Send a test mode-change notification email through the scheduler path.

This script uses apply_mode_change() with a simulated interaction so it exercises
exactly the same email notification path as runtime mode-change events.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
import logic.mode_change as _mode_change_module
from logic.mode_change import apply_mode_change

_script_logger = logging.getLogger("test_mode_change_email")


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments for the email notification test script.

    Returns:
        Parsed argparse namespace.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Send a test mode-change email notification via scheduler logic. "
            "Requires EMAIL_SENDER, EMAIL_RECEIVER, and GMAIL_APP_PASSWORD in .env."
        )
    )
    parser.add_argument(
        "--mode",
        type=int,
        default=1,
        help="Mode value to include in the test notification (default: 1 / AI).",
    )
    parser.add_argument(
        "--period",
        default="ManualTest",
        help="Context/period label to include in the test notification.",
    )
    parser.add_argument(
        "--reason",
        default="Manual test of mode-change email notification path.",
        help="Reason text to include in the test notification.",
    )
    parser.add_argument(
        "--soc",
        type=float,
        default=78.0,
        help="Battery SOC percentage to include in the test notification.",
    )
    return parser.parse_args()


def validate_email_env() -> tuple[bool, list[str]]:
    """Validate required email environment variables are configured.

    Returns:
        Tuple of (is_valid, missing_keys).
    """
    required_keys = ["EMAIL_SENDER", "EMAIL_RECEIVER", "GMAIL_APP_PASSWORD"]
    missing = [key for key in required_keys if not os.getenv(key)]
    return len(missing) == 0, missing


async def run_test(mode: int, period: str, reason: str, soc: float) -> bool:
    """Run a simulated mode-change call that should trigger an email.

    Args:
        mode: Mode value to pass to apply_mode_change.
        period: Context/period label for notification content.
        reason: Decision reason text for notification content.
        soc: Battery SOC percentage for notification content.

    Returns:
        True when simulated apply_mode_change completed successfully.
    """
    mode_names = {value: name for name, value in main.SIGEN_MODES.items()}
    previous_full_sim = _mode_change_module.FULL_SIMULATION_MODE

    try:
        # Force simulation path so the test does not send inverter commands.
        _mode_change_module.FULL_SIMULATION_MODE = True
        return await apply_mode_change(
            sigen=None,
            mode=mode,
            period=period,
            reason=reason,
            mode_names=mode_names,
            battery_soc=soc,
            logger=_script_logger,
        )
    finally:
        _mode_change_module.FULL_SIMULATION_MODE = previous_full_sim


def main_cli() -> None:
    """CLI entry point for sending a test mode-change notification email."""
    args = parse_args()
    valid, missing = validate_email_env()
    if not valid:
        print("Missing required email env vars:", ", ".join(missing))
        print("Please set them in .env and retry.")
        raise SystemExit(1)

    ok = asyncio.run(run_test(args.mode, args.period, args.reason, args.soc))
    if ok:
        print("Test mode-change command recorded and notification send attempted.")
        print("Check logs and your inbox for the notification email.")
        return

    print("Test failed: apply_mode_change returned False.")
    raise SystemExit(1)


if __name__ == "__main__":
    main_cli()

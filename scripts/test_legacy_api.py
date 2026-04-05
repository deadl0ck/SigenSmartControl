"""Read-only diagnostic for the legacy Sigen API client.

This script forces the project to use the older third-party ``sigen`` client and
queries the legacy read endpoints to confirm they still work. It never sends any
write commands.

Usage:
  python scripts/test_legacy_api.py
  python scripts/test_legacy_api.py --json
  python scripts/test_legacy_api.py --skip-signals
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable


ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from integrations.sigen_auth import get_sigen_instance


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("test_legacy_api")

ClientMethod = Callable[[], Awaitable[Any]]


def strip_wrapping_quotes(value: str) -> str:
    """Remove one layer of matching outer quotes from a string.

    Args:
        value: String that may be wrapped in matching single or double quotes.

    Returns:
        The unwrapped string when matching outer quotes are present.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _format_tree_leaf(value: Any) -> str:
    """Format a scalar value for ASCII tree logging.

    Args:
        value: Scalar value to render.

    Returns:
        A string representation safe for logs.
    """
    return repr(value)


def _iter_tree_lines(payload: Any, prefix: str = "") -> list[str]:
    """Convert nested payloads into a readable ASCII tree.

    Args:
        payload: Dict, list, or scalar payload.
        prefix: Current tree indentation prefix.

    Returns:
        A list of formatted log lines.
    """
    lines: list[str] = []

    if isinstance(payload, dict):
        items = list(payload.items())
        for index, (key, value) in enumerate(items):
            is_last = index == len(items) - 1
            branch = "`- " if is_last else "|- "
            child_prefix = prefix + ("   " if is_last else "|  ")

            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{branch}{key}:")
                lines.extend(_iter_tree_lines(value, child_prefix))
            else:
                lines.append(f"{prefix}{branch}{key}: {_format_tree_leaf(value)}")
        return lines

    if isinstance(payload, list):
        for index, value in enumerate(payload):
            is_last = index == len(payload) - 1
            branch = "`- " if is_last else "|- "
            child_prefix = prefix + ("   " if is_last else "|  ")
            label = f"[{index}]"

            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{branch}{label}:")
                lines.extend(_iter_tree_lines(value, child_prefix))
            else:
                lines.append(f"{prefix}{branch}{label}: {_format_tree_leaf(value)}")
        return lines

    lines.append(f"{prefix}`- {_format_tree_leaf(payload)}")
    return lines


def normalize_payload(payload: Any) -> Any:
    """Decode JSON-like string payloads returned by the legacy client.

    Args:
        payload: Raw payload from the legacy client.

    Returns:
        A normalized payload with nested JSON strings decoded when possible.
    """
    if isinstance(payload, dict):
        return {key: normalize_payload(value) for key, value in payload.items()}

    if isinstance(payload, list):
        return [normalize_payload(value) for value in payload]

    if not isinstance(payload, str):
        return payload

    stripped = strip_wrapping_quotes(payload.strip())
    if stripped.startswith('"success","data":'):
        data_fragment = stripped[len('"success","data":') :]
        candidates = [data_fragment]
        while candidates[-1] and candidates[-1][-1] in {"'", "}"}:
            candidates.append(candidates[-1][:-1])
        for candidate in candidates:
            try:
                return {
                    "status": "success",
                    "data": normalize_payload(json.loads(candidate)),
                }
            except json.JSONDecodeError:
                continue
        return payload

    candidates: list[str] = []
    if stripped.startswith(("{", "[")):
        candidates.append(stripped)
    if stripped.startswith('"code":'):
        candidates.insert(0, "{" + stripped + "}")

    for candidate in candidates:
        try:
            decoded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        return normalize_payload(decoded)

    return stripped


def log_payload(title: str, payload: Any, as_json: bool) -> None:
    """Log a payload as JSON or ASCII tree.

    Args:
        title: Human-readable payload title.
        payload: Payload to log.
        as_json: Whether to render as formatted JSON.
    """
    normalized = normalize_payload(payload)

    logger.info("%s:", title)
    if as_json:
        logger.info("%s", json.dumps(normalized, indent=2, default=str))
        return

    for line in _iter_tree_lines(normalized):
        logger.info("  %s", line)


async def run_read(name: str, method: ClientMethod, as_json: bool) -> bool:
    """Run a single read-only legacy client call and log the result.

    Args:
        name: Display name for the call.
        method: Awaitable client method.
        as_json: Whether to render payloads as JSON.

    Returns:
        True when the call succeeds, else False.
    """
    try:
        payload = await method()
    except Exception as exc:
        logger.warning("%s failed: %s", name, exc)
        return False

    log_payload(name, payload, as_json)
    return True


def get_async_method(client: object, method_name: str) -> ClientMethod | None:
    """Return an async legacy client method when available.

    Args:
        client: Authenticated legacy client instance.
        method_name: Method to retrieve.

    Returns:
        The bound async method, or None when unavailable.
    """
    method = getattr(client, method_name, None)
    return method if callable(method) else None


async def main() -> None:
    """Authenticate with the legacy client and query read-only endpoints."""
    parser = argparse.ArgumentParser(
        description="Read-only legacy Sigen API diagnostic.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Render payloads as formatted JSON instead of ASCII trees.",
    )
    parser.add_argument(
        "--skip-signals",
        action="store_true",
        help="Skip the legacy get_signals() call if you only want core diagnostics.",
    )
    args = parser.parse_args()

    os.environ["SIGEN_CLIENT_IMPL"] = "legacy"

    logger.info("Initializing legacy Sigen client...")
    client = await get_sigen_instance()
    logger.info("Legacy client initialized.")

    operations: list[tuple[str, str]] = [
        ("Legacy station info", "fetch_station_info"),
        ("Legacy current mode", "get_operational_mode"),
        ("Legacy supported modes", "get_operational_modes"),
        ("Legacy energy flow", "get_energy_flow"),
    ]
    if not args.skip_signals:
        operations.append(("Legacy signals", "get_signals"))

    succeeded = 0
    attempted = 0

    for title, method_name in operations:
        method = get_async_method(client, method_name)
        if method is None:
            logger.warning("%s unavailable: client has no %s() method", title, method_name)
            continue

        attempted += 1
        if await run_read(title, method, args.json):
            succeeded += 1

    logger.info(
        "Legacy API diagnostic complete: %s/%s read calls succeeded.",
        succeeded,
        attempted,
    )


if __name__ == "__main__":
    asyncio.run(main())
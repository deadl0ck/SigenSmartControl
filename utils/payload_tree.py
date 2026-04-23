"""payload_tree.py
-----------------
Generic helpers for rendering nested payloads as ASCII tree lines.

These utilities are shared by runtime and diagnostics scripts to keep tree
formatting behavior consistent across the project.
"""

import logging
from typing import Any


def format_tree_leaf(value: Any) -> str:
    """Format a scalar value for tree logging.

    Args:
        value: Scalar value to format.

    Returns:
        String-safe representation suitable for log output.
    """
    return repr(value)


def iter_tree_lines(payload: Any, prefix: str = "") -> list[str]:
    """Convert nested dict/list payloads into ASCII tree lines.

    Args:
        payload: Value to render, usually dict/list from API responses.
        prefix: Internal indentation prefix used during recursion.

    Returns:
        List of formatted tree lines.
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
                lines.extend(iter_tree_lines(value, child_prefix))
            else:
                lines.append(f"{prefix}{branch}{key}: {format_tree_leaf(value)}")
        return lines

    if isinstance(payload, list):
        for index, value in enumerate(payload):
            is_last = index == len(payload) - 1
            branch = "`- " if is_last else "|- "
            child_prefix = prefix + ("   " if is_last else "|  ")
            label = f"[{index}]"

            if isinstance(value, (dict, list)):
                lines.append(f"{prefix}{branch}{label}:")
                lines.extend(iter_tree_lines(value, child_prefix))
            else:
                lines.append(f"{prefix}{branch}{label}: {format_tree_leaf(value)}")
        return lines

    lines.append(f"{prefix}`- {format_tree_leaf(payload)}")
    return lines


def log_payload_tree(logger: logging.Logger, title: str, payload: Any) -> None:
    """Log nested payload data as a readable multi-line tree.

    Args:
        logger: Logger instance receiving the output lines.
        title: Human-readable section title for this payload.
        payload: Structured payload value.
    """
    logger.info("%s:", title)
    for line in iter_tree_lines(payload):
        logger.info("  %s", line)
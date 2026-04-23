"""terminal_formatting.py
-----------------------
Shared ANSI terminal formatting helpers used across runtime modules and scripts.

Provides a single source of truth for deciding when ANSI colors are enabled and
for wrapping text with ANSI color codes.
"""

import os
import sys
from typing import TextIO

ANSI_RESET = "\033[0m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_ORANGE = "\033[38;5;214m"
ANSI_PURPLE = "\033[95m"
ANSI_BRIGHT_RED = "\033[91m"

_TRUTHY_VALUES = {"1", "true", "yes", "on"}


def should_use_ansi_color(stream: TextIO | None = None) -> bool:
    """Return whether ANSI color output should be enabled for terminal text.

    Honors FORCE_COLOR and NO_COLOR conventions and defaults to sys.stderr.

    Args:
        stream: Output stream to evaluate for TTY support. Defaults to sys.stderr.

    Returns:
        True when ANSI coloring should be applied, False otherwise.
    """
    output_stream = stream or sys.stderr
    force_color = os.getenv("FORCE_COLOR", "").strip().lower() in _TRUTHY_VALUES
    is_tty = bool(getattr(output_stream, "isatty", lambda: False)())
    return (is_tty or force_color) and not os.getenv("NO_COLOR")


def colorize_text(
    text: str,
    color_code: str,
    *,
    enabled: bool | None = None,
    stream: TextIO | None = None,
) -> str:
    """Wrap text with ANSI color codes when color output is enabled.

    Args:
        text: Plain text to optionally colorize.
        color_code: ANSI color code prefix (for example ANSI_RED).
        enabled: Optional explicit color toggle. When None, auto-detects.
        stream: Stream used for auto-detection when enabled is None.

    Returns:
        Colorized text when enabled; otherwise original text.
    """
    use_color = should_use_ansi_color(stream=stream) if enabled is None else enabled
    if not use_color:
        return text
    return f"{color_code}{text}{ANSI_RESET}"
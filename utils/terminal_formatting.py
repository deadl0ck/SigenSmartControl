"""terminal_formatting.py
-----------------------
Shared ANSI terminal formatting helpers used across runtime modules and scripts.

Provides a single source of truth for deciding when ANSI colors are enabled,
for wrapping text with ANSI color codes, and for rendering box-drawing tables.
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


def render_table(
    headers: list[str],
    rows: list[list[str]],
    title: str | None = None,
    divider_after: set[int] | None = None,
) -> str:
    """Render a box-drawing table as a string.

    Args:
        headers: Column header labels.
        rows: Data rows; each inner list must have the same length as headers.
        title: Optional title rendered in a full-width banner above the headers.
        divider_after: Set of row indices after which to insert a mid-table divider.

    Returns:
        Multi-line string ready for printing.
    """
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    # If the title is wider than the columns, expand the last column to fit.
    if title:
        col_total = sum(w + 3 for w in col_widths) + 1
        title_needed = len(title) + 4  # "│ <title> │"
        if title_needed > col_total:
            col_widths[-1] += title_needed - col_total

    def _row_line(cells: list[str], left: str, sep: str, right: str) -> str:
        return left + sep.join(f" {c:<{col_widths[i]}} " for i, c in enumerate(cells)) + right

    def _rule(left: str, mid: str, right: str, h: str) -> str:
        return left + mid.join(h * (w + 2) for w in col_widths) + right

    total_width = sum(w + 3 for w in col_widths) + 1
    lines: list[str] = []

    if title:
        inner = total_width - 2
        lines.append("┌" + "─" * inner + "┐")
        lines.append("│ " + title.ljust(inner - 2) + " │")
        lines.append(_rule("├", "┬", "┤", "─"))
    else:
        lines.append(_rule("┌", "┬", "┐", "─"))

    lines.append(_row_line(headers, "│", "│", "│"))
    lines.append(_rule("├", "┼", "┤", "─"))
    for i, row in enumerate(rows):
        lines.append(_row_line(row, "│", "│", "│"))
        if divider_after and i in divider_after and i < len(rows) - 1:
            lines.append(_rule("├", "┼", "┤", "─"))
    lines.append(_rule("└", "┴", "┘", "─"))

    return "\n".join(lines)


def render_section_header(title: str) -> str:
    """Render a single-line box-drawing section header.

    Args:
        title: Section title text.

    Returns:
        Multi-line string with a boxed header.
    """
    inner = len(title) + 2
    return "\n".join([
        "┌" + "─" * inner + "┐",
        "│ " + title + " │",
        "└" + "─" * inner + "┘",
    ])
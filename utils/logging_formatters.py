"""logging_formatters.py
----------------------
Shared logging formatter implementations for terminal output.

Provides a configurable ANSI-aware formatter that supports per-level colors,
with defaults aligned to the scheduler's current log styling.
"""

import logging
from collections.abc import Mapping

from utils.terminal_formatting import (
    ANSI_GREEN,
    ANSI_ORANGE,
    ANSI_RED,
    colorize_text,
    should_use_ansi_color,
)


class LevelColorFormatter(logging.Formatter):
    """Format logs with optional ANSI coloring based on level and message keywords.

    Default behavior matches current scheduler styling:
    - INFO lines containing ``[MODE STATUS]`` are green
    - WARNING lines are orange
    - ERROR and CRITICAL lines are red
    """

    def __init__(
        self,
        fmt: str,
        *,
        level_colors: Mapping[int, str] | None = None,
        info_substring_colors: Mapping[str, str] | None = None,
        use_color: bool | None = None,
    ) -> None:
        """Initialize a formatter with optional color customizations.

        Args:
            fmt: Base logging format string.
            level_colors: Mapping of logging level to ANSI color code.
                Defaults to WARNING=orange, ERROR=red, CRITICAL=red.
            info_substring_colors: Mapping of substring to color for INFO lines.
                Defaults to ``{"[MODE STATUS]": ANSI_GREEN}``.
            use_color: Explicit ANSI toggle. When None, auto-detects from
                terminal/NO_COLOR/FORCE_COLOR conventions.
        """
        super().__init__(fmt=fmt)
        self._use_color = should_use_ansi_color() if use_color is None else use_color
        self._level_colors: dict[int, str] = {
            logging.WARNING: ANSI_ORANGE,
            logging.ERROR: ANSI_RED,
            logging.CRITICAL: ANSI_RED,
        }
        if level_colors is not None:
            self._level_colors.update(dict(level_colors))

        self._info_substring_colors: dict[str, str] = {"[MODE STATUS]": ANSI_GREEN}
        if info_substring_colors is not None:
            self._info_substring_colors.update(dict(info_substring_colors))

    def format(self, record: logging.LogRecord) -> str:
        """Format and optionally colorize the rendered log line.

        Args:
            record: Standard logging record.

        Returns:
            Formatted log line, colorized when enabled.
        """
        rendered = super().format(record)
        if not self._use_color:
            return rendered

        if record.levelno == logging.INFO:
            for marker, color_code in self._info_substring_colors.items():
                if marker in rendered:
                    return colorize_text(rendered, color_code, enabled=True)
            return rendered

        color_code = self._color_for_level(record.levelno)
        if color_code is None:
            return rendered
        return colorize_text(rendered, color_code, enabled=True)

    def _color_for_level(self, levelno: int) -> str | None:
        """Resolve ANSI color code for a logging level.

        Args:
            levelno: Numeric logging level from a record.

        Returns:
            ANSI color code for the level when configured, otherwise None.
        """
        if levelno >= logging.CRITICAL and logging.CRITICAL in self._level_colors:
            return self._level_colors[logging.CRITICAL]
        if levelno >= logging.ERROR and logging.ERROR in self._level_colors:
            return self._level_colors[logging.ERROR]
        return self._level_colors.get(levelno)
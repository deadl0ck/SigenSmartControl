"""Print today's and tomorrow's ESB solar forecast.

Run from project root:
    python scripts/todays_forecast_both.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from weather.forecast import SolarForecast
from utils.terminal_formatting import render_table

_PERIOD_ORDER: tuple[str, ...] = ("Morn", "Aftn", "Eve", "Night")


def _fmt_cell(value_status: tuple[int, str] | None) -> str:
    if value_status is None:
        return "N/A"
    watts, status = value_status
    return f"{watts}W ({status})"


def _print_forecast(title: str, forecast: dict[str, tuple[int, str]]) -> None:
    periods = sorted(
        forecast,
        key=lambda p: _PERIOD_ORDER.index(p) if p in _PERIOD_ORDER else len(_PERIOD_ORDER),
    )
    rows = [[period, *_fmt_cell(forecast.get(period)).split(" (", 1)] for period in periods]
    # Split "2500W (Amber)" into "2500W" and "Amber" for separate columns
    rows = []
    for period in periods:
        cell = _fmt_cell(forecast.get(period))
        if " (" in cell:
            watts, rest = cell.split(" (", 1)
            status = rest.rstrip(")")
        else:
            watts, status = cell, ""
        rows.append([period, status, watts])
    print(render_table(["Period", "Status", "Watts"], rows, title=title))


def main() -> None:
    """Fetch and print today's and tomorrow's ESB period forecasts."""
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
    logger = logging.getLogger("forecast_esb")

    try:
        provider = SolarForecast(logger)
        today = provider.get_todays_period_forecast()
        tomorrow = provider.get_tomorrows_period_forecast()
    except Exception as exc:
        print(f"ESB fetch failed: {exc}")
        return

    if not today and not tomorrow:
        print("No forecast data available.")
        return

    print()
    if today:
        _print_forecast("TODAY — ESB Forecast", today)
    print()
    if tomorrow:
        _print_forecast("TOMORROW — ESB Forecast", tomorrow)
    print()


if __name__ == "__main__":
    main()

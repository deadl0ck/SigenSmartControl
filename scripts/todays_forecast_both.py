"""Print today's ESB and Quartz forecasts side-by-side.

Run from project root:
    python scripts/todays_forecast_both.py

This utility fetches forecasts directly from both providers regardless of the
active FORECAST_PROVIDER setting and prints one table for today's periods.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

# Allow running from project root or from scripts/ sub-directory.
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from weather.forecast import QuartzSolarForecast, SolarForecast

_PERIOD_ORDER: tuple[str, ...] = ("Morn", "Aftn", "Eve", "NIGHT")


def _fmt_forecast_cell(value_status: tuple[int, str] | None) -> str:
    """Format one provider's period tuple for table output.

    Args:
        value_status: Optional tuple of (watts, status).

    Returns:
        Human-readable compact representation.
    """
    if value_status is None:
        return "N/A"

    watts, status = value_status
    return f"{watts}W ({status})"


def main() -> None:
    """Fetch and print today's period forecasts from ESB and Quartz."""
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s")
    logger = logging.getLogger("forecast_both")

    print()
    print("=" * 84)
    print("  TODAY'S FORECAST (ESB vs Quartz)")
    print("=" * 84)

    try:
        esb_provider = SolarForecast(logger)
        esb_today = esb_provider.get_todays_period_forecast()
    except Exception as exc:
        esb_today = {}
        print(f"ESB fetch failed: {exc}")

    try:
        quartz_provider = QuartzSolarForecast(logger)
        quartz_today = quartz_provider.get_todays_period_forecast()
    except Exception as exc:
        quartz_today = {}
        print(f"Quartz fetch failed: {exc}")

    if not esb_today and not quartz_today:
        print("No forecast data available from either provider.")
        print()
        return

    header = f"  {'Period':<8}  {'ESB':<30}  {'Quartz':<30}"
    print(header)
    print("-" * 84)

    periods = sorted(
        set(esb_today) | set(quartz_today),
        key=lambda period: _PERIOD_ORDER.index(period) if period in _PERIOD_ORDER else len(_PERIOD_ORDER),
    )

    for period in periods:
        esb_cell = _fmt_forecast_cell(esb_today.get(period))
        quartz_cell = _fmt_forecast_cell(quartz_today.get(period))
        print(f"  {period:<8}  {esb_cell:<30}  {quartz_cell:<30}")

    print("=" * 84)
    print()


if __name__ == "__main__":
    main()

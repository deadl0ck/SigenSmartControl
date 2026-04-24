"""Shared forecast provider interfaces, types, and base behavior."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
from typing import Protocol, TypeAlias

from config.constants import AMBER_VAL, GOOD_DAY_THRESHOLD, GREEN_VAL


DIVIDER = "+" + "-" * 11 + "+" + "-" * 10 + "+" + "-" * 13 + "+" + "-" * 8 + "+"
TableRow: TypeAlias = tuple[str, str, int, str]
PeriodForecast: TypeAlias = dict[str, tuple[int, str]]
InverterPlan: TypeAlias = dict[str, str]


class SolarForecastProvider(Protocol):
    """Stable interface for all forecast provider implementations."""

    def get_todays_period_forecast(self) -> PeriodForecast:
        """Return today's daytime period forecast."""

    def get_tomorrows_period_forecast(self) -> PeriodForecast:
        """Return tomorrow's daytime period forecast."""

    def get_todays_solar_values(self) -> list[str]:
        """Return today's compact status values used by good-day scoring."""

    def get_simple_inverter_plan(self) -> InverterPlan:
        """Return a simple text plan by daytime period."""

    def is_good_day(self) -> bool:
        """Return whether today meets configured good-day threshold."""


class BaseSolarForecast:
    """Shared behavior for provider implementations backed by normalized table rows."""

    def __init__(self, logger: logging.Logger, provider_label: str) -> None:
        self.logger = logger
        self.provider_label = provider_label
        self.table_data: list[TableRow] = []

    @staticmethod
    def _get_today() -> str:
        """Return today's three-letter day label (e.g., Mon, Tue)."""
        return BaseSolarForecast._get_day(0)

    @staticmethod
    def _get_day(offset_days: int = 0) -> str:
        """Return a three-letter day label with optional offset from today."""
        day = (datetime.now() + timedelta(days=offset_days)).strftime("%A").upper()
        return day[:3].capitalize()

    @staticmethod
    def _value_from_status(status: str) -> int:
        """Map status to synthetic watts for downstream headroom calculations.

        Values chosen as representative midpoints of each band relative to the
        8.9 kW array capacity (thresholds: Red < 20% = 1.78 kW, Green >= 40% = 3.56 kW):
          Red   ~890W midpoint of 0–1780W band  → 1000W (rounded)
          Amber ~2670W midpoint of 1780–3560W   → 2500W
          Green typical good-day average ~55%   → 5000W
        Starting close to observed actuals means the calibration multiplier
        converges near 1.0 rather than compensating for a large systematic offset.
        """
        normalized = status.strip().capitalize()
        if normalized == "Green":
            return 5000
        if normalized == "Amber":
            return 2500
        return 1000

    def _log_table(self) -> None:
        """Print normalized table rows in an ASCII table."""
        header = f"| {'Day':^9} | {'Period':^8} | {'Solar Value':^11} | {'Status':^6} |"
        self.logger.info("[FORECAST-TABLE] %s", self.provider_label)
        self.logger.info(DIVIDER)
        self.logger.info(header)
        self.logger.info(DIVIDER)

        previous_day: str | None = None
        for day, period, value, status in self.table_data:
            if previous_day and day != previous_day:
                self.logger.info(DIVIDER)
            self.logger.info(f"| {day:^9} | {period:^8} | {value:^11} | {status:^6} |")
            previous_day = day

        self.logger.info(DIVIDER)

    def get_todays_solar_values(self) -> list[str]:
        """Return today's daytime statuses as compact codes (R/A/G)."""
        today_day = self._get_today()
        values: list[str] = []

        self.logger.info(
            "Solar Values for today (%s) [%s]:",
            today_day,
            self.provider_label,
        )
        self.logger.info(DIVIDER)
        for day, period, value, status in self.table_data:
            if day == today_day and period != "NIGHT":
                self.logger.info(f"| {day:^9} | {period:^8} | {value:^11} | {status:^6} |")
                values.append("G" if status == "Green" else "A" if status == "Amber" else "R")
        self.logger.info(DIVIDER)
        return values

    def get_todays_period_forecast(self) -> PeriodForecast:
        """Return today's daytime forecast values and statuses by period."""
        return self.get_period_forecast_for_day(self._get_today())

    def get_period_forecast_for_day(
        self,
        day_label: str,
        *,
        include_night: bool = False,
    ) -> PeriodForecast:
        """Return forecast values and statuses for a specific day label."""
        period_forecast: PeriodForecast = {}
        for day, period, value, status in self.table_data:
            if day != day_label:
                continue
            if not include_night and period == "NIGHT":
                continue
            period_forecast[period] = (value, status)
        return period_forecast

    def get_tomorrows_period_forecast(self) -> PeriodForecast:
        """Return tomorrow's daytime forecast values and statuses by period."""
        return self.get_period_forecast_for_day(self._get_day(1))

    def get_simple_inverter_plan(self) -> InverterPlan:
        """Create a simple planning hint for charge/discharge by daytime period."""
        forecast = self.get_todays_period_forecast()
        plan: InverterPlan = {}

        for period in ("Morn", "Aftn", "Eve"):
            value_status = forecast.get(period)
            if value_status is None:
                plan[period] = "No forecast available"
                continue

            _, status = value_status
            if status == "Green":
                plan[period] = "Prefer discharge first to create battery headroom and reduce clipping risk"
            elif status == "Amber":
                plan[period] = "Use balanced mode with light charging/discharging"
            else:
                plan[period] = "Avoid discharge where possible; keep battery energy for home use"

        return plan

    def is_good_day(self) -> bool:
        """Determine whether today's aggregate forecast meets the good-day threshold."""
        today = self.get_todays_solar_values()
        good_periods = sum(GREEN_VAL for value in today if value == "G")
        good_periods += sum(AMBER_VAL for value in today if value == "A")
        return good_periods >= GOOD_DAY_THRESHOLD

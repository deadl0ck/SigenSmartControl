"""Solar forecast providers and normalization helpers.

This module exposes a stable forecast provider interface used by scheduler logic.
The default implementation reads ESB's county API, and an optional Quartz
implementation can be selected without changing consumer code.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import logging
from typing import Protocol, TypeAlias

import requests

from constants import (
    AMBER_VAL,
    COUNTY,
    ESB_COUNTY_ID_MAP,
    ESB_FORECAST_API_BASE_URL,
    ESB_FORECAST_API_ENDPOINT,
    ESB_FORECAST_API_SUBSCRIPTION_KEY,
    FORECAST_PROVIDER,
    GOOD_DAY_THRESHOLD,
    GREEN_VAL,
    LATITUDE,
    LONGITUDE,
    QUARTZ_FORECAST_API_URL,
    QUARTZ_SITE_CAPACITY_KWP,
)

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


class _BaseSolarForecast:
    """Shared behavior for provider implementations backed by normalized table rows."""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger
        self.table_data: list[TableRow] = []

    @staticmethod
    def _get_today() -> str:
        """Return today's three-letter day label (e.g., Mon, Tue)."""
        return _BaseSolarForecast._get_day(0)

    @staticmethod
    def _get_day(offset_days: int = 0) -> str:
        """Return a three-letter day label with optional offset from today."""
        day = (datetime.now() + timedelta(days=offset_days)).strftime("%A").upper()
        return day[:3].capitalize()

    @staticmethod
    def _status_from_value(value: int) -> str:
        """Convert synthetic/forecast watts to Red/Amber/Green status."""
        if value < 200:
            return "Red"
        if value <= 400:
            return "Amber"
        return "Green"

    @staticmethod
    def _value_from_status(status: str) -> int:
        """Map status to synthetic watts for downstream headroom calculations."""
        normalized = status.strip().capitalize()
        if normalized == "Green":
            return 500
        if normalized == "Amber":
            return 300
        return 100

    def _log_table(self) -> None:
        """Print normalized table rows in an ASCII table."""
        header = f"| {'Day':^9} | {'Period':^8} | {'Solar Value':^11} | {'Status':^6} |"
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

        self.logger.info(f"Solar Values for today ({today_day}):")
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


class SolarForecast(_BaseSolarForecast):
    """ESB county API-backed forecast provider.

    This class keeps the historical `SolarForecast` name for backward compatibility.
    """

    _period_map = {
        "Morning": "Morn",
        "Afternoon": "Aftn",
        "Evening": "Eve",
        "Night": "NIGHT",
    }

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize provider and load county forecast rows from ESB API."""
        super().__init__(logger)
        self._load_esb_api_table(COUNTY)

    @staticmethod
    def _normalize_county_name(county: str) -> str:
        """Return canonical county key matching ESB_COUNTY_ID_MAP."""
        lower_lookup = {name.lower(): name for name in ESB_COUNTY_ID_MAP}
        key = county.strip().lower()
        if key not in lower_lookup:
            raise ValueError(f"County '{county}' is not supported by ESB county mapping")
        return lower_lookup[key]

    def _load_esb_api_table(self, county: str) -> None:
        """Load and normalize county forecast rows from ESB's JSON endpoint."""
        county_key = self._normalize_county_name(county)
        county_id = ESB_COUNTY_ID_MAP[county_key]
        url = f"{ESB_FORECAST_API_BASE_URL.rstrip('/')}{ESB_FORECAST_API_ENDPOINT}/{county_id}"

        headers = {"Content-Type": "application/json"}
        if ESB_FORECAST_API_SUBSCRIPTION_KEY:
            headers["API-Subscription-Key"] = ESB_FORECAST_API_SUBSCRIPTION_KEY

        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            raise ValueError("ESB forecast API returned empty or invalid data")

        normalized_rows: list[TableRow] = []
        for row in rows:
            date_str = str(row.get("date", "")).strip()
            period_label = str(row.get("period", "")).strip()
            status = str(row.get("status", "")).strip().capitalize()
            period = self._period_map.get(period_label)
            if period is None or status not in {"Green", "Amber", "Red"}:
                continue

            try:
                day_label = datetime.fromisoformat(date_str).strftime("%a").capitalize()
            except Exception:
                day_label = self._get_today()
            value = self._value_from_status(status)
            normalized_rows.append((day_label, period, value, status))

        if not normalized_rows:
            raise ValueError("ESB forecast API did not contain usable period/status rows")

        self.table_data = normalized_rows
        self._log_table()


class QuartzSolarForecast(_BaseSolarForecast):
    """Optional Open Quartz provider using site latitude/longitude and capacity."""

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize provider and load forecasts from Open Quartz API."""
        super().__init__(logger)
        self._load_quartz_table()

    @staticmethod
    def _period_from_hour(hour_utc: int) -> str:
        """Map UTC hour to project period labels."""
        if 7 <= hour_utc < 12:
            return "Morn"
        if 12 <= hour_utc < 16:
            return "Aftn"
        if 16 <= hour_utc < 20:
            return "Eve"
        return "NIGHT"

    def _load_quartz_table(self) -> None:
        """Load and normalize Quartz forecast output into project period rows."""
        payload = {
            "site": {
                "latitude": LATITUDE,
                "longitude": LONGITUDE,
                "capacity_kwp": QUARTZ_SITE_CAPACITY_KWP,
            }
        }

        response = requests.post(QUARTZ_FORECAST_API_URL, json=payload, timeout=30)
        response.raise_for_status()
        body = response.json()
        power_by_timestamp = body.get("predictions", {}).get("power_kw", {})
        if not isinstance(power_by_timestamp, dict) or not power_by_timestamp:
            raise ValueError("Quartz forecast response did not include predictions.power_kw")

        grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
        for timestamp, kw_value in power_by_timestamp.items():
            try:
                dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                period = self._period_from_hour(dt.hour)
                day_label = dt.strftime("%a").capitalize()
                grouped[(day_label, period)].append(float(kw_value))
            except Exception:
                continue

        normalized_rows: list[TableRow] = []
        for (day_label, period), kw_values in grouped.items():
            avg_kw = sum(kw_values) / len(kw_values)
            value = int(round(avg_kw * 1000.0))
            status = self._status_from_value(value)
            normalized_rows.append((day_label, period, value, status))

        if not normalized_rows:
            raise ValueError("Quartz forecast produced no usable period rows")

        # Keep days/periods in deterministic order.
        period_order = {"Morn": 0, "Aftn": 1, "Eve": 2, "NIGHT": 3}
        normalized_rows.sort(key=lambda row: (row[0], period_order.get(row[1], 99)))
        self.table_data = normalized_rows
        self._log_table()


class ComparingSolarForecastProvider:
    """Wrap primary and secondary providers, using primary for decisions.

    This provider logs side-by-side comparisons at creation time while exposing
    the primary provider's outputs to scheduler/control consumers.
    """

    _period_order = {"Morn": 0, "Aftn": 1, "Eve": 2, "NIGHT": 3}

    def __init__(
        self,
        logger: logging.Logger,
        primary: SolarForecastProvider,
        secondary: SolarForecastProvider,
        *,
        primary_name: str,
        secondary_name: str,
    ) -> None:
        self.logger = logger
        self._primary = primary
        self._secondary = secondary
        self._primary_name = primary_name
        self._secondary_name = secondary_name
        self._log_comparison()

    def _ordered_periods(
        self,
        left: PeriodForecast,
        right: PeriodForecast,
    ) -> list[str]:
        periods = set(left) | set(right)
        return sorted(periods, key=lambda period: self._period_order.get(period, 99))

    def _log_day_comparison(
        self,
        day_label: str,
        left: PeriodForecast,
        right: PeriodForecast,
    ) -> tuple[int, int, int]:
        match_count = 0
        mismatch_count = 0
        missing_count = 0

        self.logger.info(
            f"[FORECAST-COMPARE] {day_label}: {self._primary_name} (decision source) vs "
            f"{self._secondary_name} (secondary)"
        )

        for period in self._ordered_periods(left, right):
            left_val = left.get(period)
            right_val = right.get(period)

            if left_val is None or right_val is None:
                missing_count += 1
                self.logger.info(
                    f"[FORECAST-COMPARE]   {period}: incomplete data | "
                    f"{self._primary_name}={left_val} | {self._secondary_name}={right_val}"
                )
                continue

            left_w, left_status = left_val
            right_w, right_status = right_val
            same_status = left_status == right_status
            if same_status:
                match_count += 1
                verdict = "MATCH"
            else:
                mismatch_count += 1
                verdict = "DIFF"

            self.logger.info(
                f"[FORECAST-COMPARE]   {period}: {verdict} | "
                f"{self._primary_name}={left_status} ({left_w}W) | "
                f"{self._secondary_name}={right_status} ({right_w}W)"
            )

        return match_count, mismatch_count, missing_count

    def _log_comparison(self) -> None:
        today_primary = self._primary.get_todays_period_forecast()
        today_secondary = self._secondary.get_todays_period_forecast()
        tomorrow_primary = self._primary.get_tomorrows_period_forecast()
        tomorrow_secondary = self._secondary.get_tomorrows_period_forecast()

        self.logger.info(
            f"[FORECAST-COMPARE] Decision provider is {self._primary_name}. "
            f"{self._secondary_name} is comparison-only and does not drive inverter decisions."
        )

        today_counts = self._log_day_comparison("Today", today_primary, today_secondary)
        tomorrow_counts = self._log_day_comparison("Tomorrow", tomorrow_primary, tomorrow_secondary)

        total_matches = today_counts[0] + tomorrow_counts[0]
        total_mismatches = today_counts[1] + tomorrow_counts[1]
        total_missing = today_counts[2] + tomorrow_counts[2]
        self.logger.info(
            "[FORECAST-COMPARE] Summary: "
            f"matches={total_matches}, mismatches={total_mismatches}, missing={total_missing}. "
            "Use mismatch trends to evaluate whether Quartz should replace or supplement ESB later."
        )

    def get_todays_period_forecast(self) -> PeriodForecast:
        return self._primary.get_todays_period_forecast()

    def get_tomorrows_period_forecast(self) -> PeriodForecast:
        return self._primary.get_tomorrows_period_forecast()

    def get_todays_solar_values(self) -> list[str]:
        return self._primary.get_todays_solar_values()

    def get_simple_inverter_plan(self) -> InverterPlan:
        return self._primary.get_simple_inverter_plan()

    def is_good_day(self) -> bool:
        return self._primary.is_good_day()


def create_solar_forecast_provider(logger: logging.Logger) -> SolarForecastProvider:
    """Create the active forecast provider based on constants configuration."""
    if FORECAST_PROVIDER == "esb_api":
        primary = SolarForecast(logger)
        try:
            secondary = QuartzSolarForecast(logger)
            return ComparingSolarForecastProvider(
                logger,
                primary,
                secondary,
                primary_name="esb_api",
                secondary_name="quartz",
            )
        except Exception as exc:
            logger.warning(
                "[FORECAST-COMPARE] Quartz comparison provider unavailable. "
                f"Continuing with ESB-only decisions. Reason: {exc}"
            )
            return primary
    if FORECAST_PROVIDER == "quartz":
        return QuartzSolarForecast(logger)
    raise ValueError(
        f"Unsupported FORECAST_PROVIDER='{FORECAST_PROVIDER}'. "
        "Use 'esb_api' or 'quartz'."
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    log = logging.getLogger(__name__)
    solar_forecast = create_solar_forecast_provider(log)
    todays_values = solar_forecast.get_todays_solar_values()
    todays_periods = solar_forecast.get_todays_period_forecast()
    inverter_plan = solar_forecast.get_simple_inverter_plan()
    log.info(f"Today's forecast: {todays_values}")
    log.info(f"Today's period forecast: {todays_periods}")
    log.info(f"Simple inverter plan: {inverter_plan}")
    log.info(f"A good day? {solar_forecast.is_good_day()}")

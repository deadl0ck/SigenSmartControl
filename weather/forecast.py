"""Solar forecast providers and normalization helpers.

This module exposes a stable forecast provider interface used by scheduler logic.
The default implementation reads ESB's county API, and an optional Quartz
implementation can be selected without changing consumer code.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
from typing import Any, Protocol, TypeAlias
from zoneinfo import ZoneInfo

import requests

from config.settings import (
    ESB_API_TIMEOUT_SECONDS,
    FORECAST_SOLAR_API_TIMEOUT_SECONDS,
    FORECAST_SOLAR_POWER_MULTIPLIER,
    LOCAL_TIMEZONE,
    QUARTZ_API_TIMEOUT_SECONDS,
    QUARTZ_GREEN_CAPACITY_FRACTION,
    QUARTZ_RED_CAPACITY_FRACTION,
)
from config.constants import (
    AMBER_VAL,
    COUNTY,
    ESB_COUNTY_ID_MAP,
    ESB_FORECAST_API_BASE_URL,
    ESB_FORECAST_API_ENDPOINT,
    ESB_FORECAST_API_SUBSCRIPTION_KEY,
    FORECAST_SOLAR_API_BASE_URL,
    FORECAST_SOLAR_API_KEY,
    FORECAST_SOLAR_ARCHIVE_PATH,
    FORECAST_SOLAR_PLANE_AZIMUTH,
    FORECAST_SOLAR_PLANE_DECLINATION,
    FORECAST_SOLAR_SITE_KWP,
    FORECAST_COMPARISON_ARCHIVE_PATH,
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

    def __init__(self, logger: logging.Logger, provider_label: str) -> None:
        self.logger = logger
        self.provider_label = provider_label
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
        super().__init__(logger, "ESB county forecast")
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

        response = requests.get(url, headers=headers, timeout=ESB_API_TIMEOUT_SECONDS)
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
        super().__init__(logger, "Quartz site-level forecast")
        self._load_quartz_table()

    @staticmethod
    def _period_from_hour(hour_local: int) -> str:
        """Map local hour to project period labels."""
        if 7 <= hour_local < 12:
            return "Morn"
        if 12 <= hour_local < 16:
            return "Aftn"
        if 16 <= hour_local < 20:
            return "Eve"
        return "NIGHT"

    @staticmethod
    def _local_timezone() -> ZoneInfo:
        """Return the configured local timezone for period bucketing."""
        return ZoneInfo(LOCAL_TIMEZONE)

    @classmethod
    def _status_from_avg_kw(cls, avg_kw: float) -> str:
        """Map Quartz period-average output to Red/Amber/Green.

        Quartz returns site-level power in kW, so status is normalized against
        configured array size (QUARTZ_SITE_CAPACITY_KWP):
        - Red: < 20% of capacity
        - Amber: 20% to < 40% of capacity
        - Green: >= 40% of capacity
        """
        capacity_kw = max(QUARTZ_SITE_CAPACITY_KWP, 0.1)
        output_fraction = avg_kw / capacity_kw
        if output_fraction < QUARTZ_RED_CAPACITY_FRACTION:
            return "Red"
        if output_fraction < QUARTZ_GREEN_CAPACITY_FRACTION:
            return "Amber"
        return "Green"

    def _load_quartz_table(self) -> None:
        """Load and normalize Quartz forecast output into project period rows."""
        payload = {
            "site": {
                "latitude": LATITUDE,
                "longitude": LONGITUDE,
                "capacity_kwp": QUARTZ_SITE_CAPACITY_KWP,
            }
        }

        response = requests.post(
            QUARTZ_FORECAST_API_URL,
            json=payload,
            timeout=QUARTZ_API_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        power_by_timestamp = body.get("predictions", {}).get("power_kw", {})
        if not isinstance(power_by_timestamp, dict) or not power_by_timestamp:
            raise ValueError("Quartz forecast response did not include predictions.power_kw")

        grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
        local_tz = self._local_timezone()
        for timestamp, kw_value in power_by_timestamp.items():
            try:
                dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                local_dt = dt.astimezone(local_tz)
                period = self._period_from_hour(local_dt.hour)
                day_label = local_dt.strftime("%a").capitalize()
                grouped[(day_label, period)].append(float(kw_value))
            except Exception:
                continue

        normalized_rows: list[TableRow] = []
        for (day_label, period), kw_values in grouped.items():
            avg_kw = sum(kw_values) / len(kw_values)
            value = int(round(avg_kw * 1000.0))
            status = self._status_from_avg_kw(avg_kw)
            normalized_rows.append((day_label, period, value, status))

        if not normalized_rows:
            raise ValueError("Quartz forecast produced no usable period rows")

        # Keep days/periods in deterministic order.
        period_order = {"Morn": 0, "Aftn": 1, "Eve": 2, "NIGHT": 3}
        normalized_rows.sort(key=lambda row: (row[0], period_order.get(row[1], 99)))
        self.table_data = normalized_rows
        self._log_table()


def _build_forecast_solar_endpoint() -> str:
    """Build Forecast.Solar watts endpoint based on account mode."""
    lat = f"{LATITUDE:.4f}"
    lon = f"{LONGITUDE:.4f}"
    dec = str(FORECAST_SOLAR_PLANE_DECLINATION)
    az = str(FORECAST_SOLAR_PLANE_AZIMUTH)
    kwp = f"{FORECAST_SOLAR_SITE_KWP:.3f}".rstrip("0").rstrip(".")

    if FORECAST_SOLAR_API_KEY:
        return (
            f"{FORECAST_SOLAR_API_BASE_URL}/{FORECAST_SOLAR_API_KEY}"
            f"/estimate/watts/{lat}/{lon}/{dec}/{az}/{kwp}"
        )
    return f"{FORECAST_SOLAR_API_BASE_URL}/estimate/watts/{lat}/{lon}/{dec}/{az}/{kwp}"


def _extract_forecast_solar_watts_map(payload: Any) -> tuple[dict[str, float], str]:
    """Extract Forecast.Solar point map from supported response shapes.

    Args:
        payload: Decoded JSON response from Forecast.Solar.

    Returns:
        Tuple of (timestamp->watts map, source format label).
    """
    if not isinstance(payload, dict):
        return {}, "missing"

    result = payload.get("result")
    if not isinstance(result, dict):
        return {}, "missing"

    # Historical format: result.watts holds the timestamp-value map.
    watts_nested = result.get("watts")
    if isinstance(watts_nested, dict) and watts_nested:
        nested_normalized = {
            str(ts): float(value)
            for ts, value in watts_nested.items()
            if isinstance(value, (int, float))
        }
        if nested_normalized:
            return nested_normalized, "result.watts"

    # Current observed format: result itself is the timestamp-value map.
    direct_normalized = {
        str(ts): float(value)
        for ts, value in result.items()
        if isinstance(value, (int, float))
    }
    if direct_normalized:
        return direct_normalized, "result"

    return {}, "missing"


def archive_forecast_solar_snapshot(logger: logging.Logger, now_utc: datetime) -> None:
    """Pull and persist one raw Forecast.Solar reading snapshot.

    Args:
        logger: Project logger.
        now_utc: Scheduler tick timestamp in UTC.
    """
    endpoint = _build_forecast_solar_endpoint()
    response = requests.get(endpoint, timeout=FORECAST_SOLAR_API_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    readings, source_format = _extract_forecast_solar_watts_map(body)
    if not readings:
        raise ValueError("Forecast.Solar response did not include usable watts data")

    archive_path = Path(FORECAST_SOLAR_ARCHIVE_PATH)
    snapshot = {
        "captured_at_utc": now_utc.isoformat(),
        "source": "forecast_solar",
        "endpoint": endpoint,
        "source_format": source_format,
        "point_count": len(readings),
        "readings": readings,
    }

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with archive_path.open("a", encoding="utf-8") as archive_file:
        json.dump(snapshot, archive_file, sort_keys=True)
        archive_file.write("\n")

    logger.info(
        "[FORECAST-SOLAR] Archived %s raw points to %s",
        len(readings),
        archive_path,
    )


class ForecastSolarForecast(_BaseSolarForecast):
    """Forecast.Solar provider using public or API-key estimate endpoints."""

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize provider and load forecasts from Forecast.Solar API."""
        super().__init__(logger, "Forecast.Solar site-level forecast")
        self._load_forecast_solar_table()

    @staticmethod
    def _period_from_hour(hour_local: int) -> str:
        """Map local hour to project period labels."""
        if 7 <= hour_local < 12:
            return "Morn"
        if 12 <= hour_local < 16:
            return "Aftn"
        if 16 <= hour_local < 20:
            return "Eve"
        return "NIGHT"

    @staticmethod
    def _local_timezone() -> ZoneInfo:
        """Return the configured local timezone for period bucketing."""
        return ZoneInfo(LOCAL_TIMEZONE)

    @classmethod
    def _status_from_avg_kw(cls, avg_kw: float) -> str:
        """Map Forecast.Solar period-average output to Red/Amber/Green."""
        capacity_kw = max(FORECAST_SOLAR_SITE_KWP, 0.1)
        output_fraction = avg_kw / capacity_kw
        if output_fraction < QUARTZ_RED_CAPACITY_FRACTION:
            return "Red"
        if output_fraction < QUARTZ_GREEN_CAPACITY_FRACTION:
            return "Amber"
        return "Green"

    def _build_endpoint(self) -> str:
        """Build Forecast.Solar watts endpoint based on account mode."""
        return _build_forecast_solar_endpoint()

    def _load_forecast_solar_table(self) -> None:
        """Load and normalize Forecast.Solar output into project period rows."""
        endpoint = self._build_endpoint()
        self.logger.info(
            "[FORECAST-SOLAR] Applying configured power multiplier x%.2f",
            FORECAST_SOLAR_POWER_MULTIPLIER,
        )
        response = requests.get(endpoint, timeout=FORECAST_SOLAR_API_TIMEOUT_SECONDS)
        response.raise_for_status()
        body = response.json()

        watts_by_timestamp, _source_name = _extract_forecast_solar_watts_map(body)
        if not watts_by_timestamp:
            raise ValueError("Forecast.Solar response did not include usable watts data")

        info = body.get("message", {}).get("info", {}) if isinstance(body, dict) else {}
        provider_tz_name = info.get("timezone") if isinstance(info, dict) else None
        local_tz = (
            ZoneInfo(provider_tz_name)
            if isinstance(provider_tz_name, str)
            else self._local_timezone()
        )

        grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
        for timestamp, watts_value in watts_by_timestamp.items():
            try:
                dt = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=local_tz)
                local_dt = dt.astimezone(self._local_timezone())
                period = self._period_from_hour(local_dt.hour)
                day_label = local_dt.strftime("%a").capitalize()
                grouped[(day_label, period)].append(float(watts_value) / 1000.0)
            except Exception:
                continue

        normalized_rows: list[TableRow] = []
        for (day_label, period), kw_values in grouped.items():
            avg_kw = (sum(kw_values) / len(kw_values)) * FORECAST_SOLAR_POWER_MULTIPLIER
            value = int(round(avg_kw * 1000.0))
            status = self._status_from_avg_kw(avg_kw)
            normalized_rows.append((day_label, period, value, status))

        if not normalized_rows:
            raise ValueError("Forecast.Solar forecast produced no usable period rows")

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
        tertiary: SolarForecastProvider | None = None,
        tertiary_name: str | None = None,
    ) -> None:
        self.logger = logger
        self._primary = primary
        self._secondary = secondary
        self._tertiary = tertiary
        self._primary_name = primary_name
        self._secondary_name = secondary_name
        self._tertiary_name = tertiary_name
        self._log_comparison()

    def _ordered_periods(
        self,
        left: PeriodForecast,
        right: PeriodForecast,
    ) -> list[str]:
        periods = set(left) | set(right)
        return sorted(periods, key=lambda period: self._period_order.get(period, 99))

    def _merge_primary_status_with_secondary_values(
        self,
        primary: PeriodForecast,
        secondary: PeriodForecast,
        tertiary: PeriodForecast | None,
    ) -> PeriodForecast:
        """Keep primary statuses while borrowing secondary watts when available.

        This preserves ESB as the decision/status source while letting the
        scheduler use site-level numeric forecasts for headroom and clipping
        calculations. If the secondary provider is missing a period, fall back
        to the primary tuple unchanged.
        """
        merged: PeriodForecast = {}
        for period, (primary_value, primary_status) in primary.items():
            secondary_value = secondary.get(period)
            if secondary_value is not None:
                merged[period] = (secondary_value[0], primary_status)
                continue

            tertiary_value = tertiary.get(period) if tertiary is not None else None
            if tertiary_value is not None:
                merged[period] = (tertiary_value[0], primary_status)
                continue

            merged[period] = (primary_value, primary_status)

        return merged

    @staticmethod
    def _serialize_period_value(value_status: tuple[int, str] | None) -> dict[str, int | str] | None:
        """Convert a forecast tuple into JSON-serializable structure."""
        if value_status is None:
            return None

        value_w, status = value_status
        return {"value_w": value_w, "status": status}

    @staticmethod
    def _format_period_value(provider_name: str, value: int, status: str) -> str:
        """Format comparison output so synthetic ESB values are clearly labelled."""
        if provider_name == "esb_api":
            return f"{status} (county status, synthetic={value}W)"

        if provider_name == "quartz":
            capacity_kw = max(QUARTZ_SITE_CAPACITY_KWP, 0.1)
            avg_kw = value / 1000.0
            pct_capacity = int(round((avg_kw / capacity_kw) * 100.0))
            return f"{status} (site avg={value}W, {pct_capacity}% of {capacity_kw:g}kWp)"

        return f"{status} ({value}W)"

    def _build_day_snapshot(self, left: PeriodForecast, right: PeriodForecast) -> dict[str, object]:
        """Build a serializable comparison payload for one forecast day."""
        periods: dict[str, object] = {}
        match_count = 0
        mismatch_count = 0
        missing_count = 0

        for period in self._ordered_periods(left, right):
            left_val = left.get(period)
            right_val = right.get(period)
            match: bool | None
            if left_val is None or right_val is None:
                missing_count += 1
                match = None
            else:
                match = left_val[1] == right_val[1]
                if match:
                    match_count += 1
                else:
                    mismatch_count += 1

            periods[period] = {
                "primary": self._serialize_period_value(left_val),
                "secondary": self._serialize_period_value(right_val),
                "status_match": match,
            }

        return {
            "periods": periods,
            "summary": {
                "matches": match_count,
                "mismatches": mismatch_count,
                "missing": missing_count,
            },
        }

    def _build_day_snapshot_with_optional_tertiary(
        self,
        left: PeriodForecast,
        secondary: PeriodForecast,
        tertiary: PeriodForecast | None,
    ) -> dict[str, object]:
        """Build serializable comparison payload for one day with optional tertiary source."""
        base_snapshot = self._build_day_snapshot(left, secondary)
        if tertiary is None:
            return base_snapshot

        periods = base_snapshot.get("periods", {})
        for period in self._ordered_periods(left, tertiary):
            period_entry = periods.get(period)
            if not isinstance(period_entry, dict):
                period_entry = {
                    "primary": self._serialize_period_value(left.get(period)),
                    "secondary": self._serialize_period_value(secondary.get(period)),
                    "status_match": None,
                }
                periods[period] = period_entry

            tertiary_val = tertiary.get(period)
            primary_val = left.get(period)
            period_entry["tertiary"] = self._serialize_period_value(tertiary_val)
            if primary_val is None or tertiary_val is None:
                period_entry["status_match_tertiary"] = None
            else:
                period_entry["status_match_tertiary"] = primary_val[1] == tertiary_val[1]

        base_snapshot["periods"] = periods
        return base_snapshot

    def _persist_comparison_snapshot(
        self,
        today_primary: PeriodForecast,
        today_secondary: PeriodForecast,
        tomorrow_primary: PeriodForecast,
        tomorrow_secondary: PeriodForecast,
        today_tertiary: PeriodForecast | None,
        tomorrow_tertiary: PeriodForecast | None,
    ) -> None:
        """Append one normalized comparison snapshot to the local JSONL archive."""
        archive_path = Path(FORECAST_COMPARISON_ARCHIVE_PATH)
        snapshot = {
            "captured_at": datetime.now(ZoneInfo(LOCAL_TIMEZONE)).isoformat(),
            "timezone": LOCAL_TIMEZONE,
            "primary_provider": self._primary_name,
            "secondary_provider": self._secondary_name,
            "tertiary_provider": self._tertiary_name,
            "county": COUNTY,
            "latitude": LATITUDE,
            "longitude": LONGITUDE,
            "quartz_site_capacity_kwp": QUARTZ_SITE_CAPACITY_KWP,
            "forecast_solar_site_kwp": FORECAST_SOLAR_SITE_KWP,
            "normalization": {
                "quartz_period_timezone": LOCAL_TIMEZONE,
                "quartz_red_lt_fraction": QUARTZ_RED_CAPACITY_FRACTION,
                "quartz_amber_lt_fraction": QUARTZ_GREEN_CAPACITY_FRACTION,
                "forecast_solar_power_multiplier": FORECAST_SOLAR_POWER_MULTIPLIER,
                "esb_values_are_synthetic": True,
            },
            "today": self._build_day_snapshot_with_optional_tertiary(
                today_primary,
                today_secondary,
                today_tertiary,
            ),
            "tomorrow": self._build_day_snapshot_with_optional_tertiary(
                tomorrow_primary,
                tomorrow_secondary,
                tomorrow_tertiary,
            ),
        }

        try:
            archive_path.parent.mkdir(parents=True, exist_ok=True)
            with archive_path.open("a", encoding="utf-8") as archive_file:
                json.dump(snapshot, archive_file, sort_keys=True)
                archive_file.write("\n")
            self.logger.info(
                f"[FORECAST-COMPARE] Saved comparison snapshot to {archive_path}"
            )
        except OSError as exc:
            self.logger.warning(
                f"[FORECAST-COMPARE] Failed to save comparison snapshot to {archive_path}: {exc}"
            )

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
                f"{self._primary_name}={self._format_period_value(self._primary_name, left_w, left_status)} | "
                f"{self._secondary_name}={self._format_period_value(self._secondary_name, right_w, right_status)}"
            )

        return match_count, mismatch_count, missing_count

    def _log_comparison(self) -> None:
        today_primary = self._primary.get_todays_period_forecast()
        today_secondary = self._secondary.get_todays_period_forecast()
        tomorrow_primary = self._primary.get_tomorrows_period_forecast()
        tomorrow_secondary = self._secondary.get_tomorrows_period_forecast()
        today_tertiary = (
            self._tertiary.get_todays_period_forecast() if self._tertiary is not None else None
        )
        tomorrow_tertiary = (
            self._tertiary.get_tomorrows_period_forecast() if self._tertiary is not None else None
        )

        self.logger.info(
            f"[FORECAST-COMPARE] Decision provider is {self._primary_name}. "
            f"{self._secondary_name} is comparison-only and does not drive inverter decisions."
        )
        if self._tertiary_name is not None:
            self.logger.info(
                f"[FORECAST-COMPARE] Backup order for numeric watts: "
                f"{self._secondary_name} first, {self._tertiary_name} second."
            )
        self.logger.info(
            f"[FORECAST-COMPARE] Scheduler calculations keep {self._primary_name} statuses "
            f"but use {self._secondary_name} site-level watts when available to estimate clipping risk."
        )
        self.logger.info(
            "[FORECAST-COMPARE] Quartz normalization uses local period bucketing "
            f"({LOCAL_TIMEZONE}) and capacity-based thresholds: "
            f"Red < {int(QUARTZ_RED_CAPACITY_FRACTION * 100)}%, "
            f"Amber < {int(QUARTZ_GREEN_CAPACITY_FRACTION * 100)}%, "
            "Green >= that share of configured array output."
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
        self._persist_comparison_snapshot(
            today_primary,
            today_secondary,
            tomorrow_primary,
            tomorrow_secondary,
            today_tertiary,
            tomorrow_tertiary,
        )

    def get_todays_period_forecast(self) -> PeriodForecast:
        return self._merge_primary_status_with_secondary_values(
            self._primary.get_todays_period_forecast(),
            self._secondary.get_todays_period_forecast(),
            self._tertiary.get_todays_period_forecast() if self._tertiary is not None else None,
        )

    def get_tomorrows_period_forecast(self) -> PeriodForecast:
        return self._merge_primary_status_with_secondary_values(
            self._primary.get_tomorrows_period_forecast(),
            self._secondary.get_tomorrows_period_forecast(),
            self._tertiary.get_tomorrows_period_forecast() if self._tertiary is not None else None,
        )

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
        forecast_solar_provider: ForecastSolarForecast | None = None
        quartz_provider: QuartzSolarForecast | None = None

        try:
            forecast_solar_provider = ForecastSolarForecast(logger)
        except Exception as exc:
            logger.warning(
                "[FORECAST-COMPARE] Forecast.Solar backup provider unavailable. Reason: %s",
                exc,
            )

        try:
            quartz_provider = QuartzSolarForecast(logger)
        except Exception as exc:
            logger.warning(
                "[FORECAST-COMPARE] Quartz backup provider unavailable. Reason: %s",
                exc,
            )

        if forecast_solar_provider is not None:
            return ComparingSolarForecastProvider(
                logger,
                primary,
                forecast_solar_provider,
                primary_name="esb_api",
                secondary_name="forecast_solar",
                tertiary=quartz_provider,
                tertiary_name="quartz" if quartz_provider is not None else None,
            )

        if quartz_provider is not None:
            return ComparingSolarForecastProvider(
                logger,
                primary,
                quartz_provider,
                primary_name="esb_api",
                secondary_name="quartz",
            )

        logger.warning(
            "[FORECAST-COMPARE] No backup forecast providers available. Continuing with ESB-only decisions."
        )
        return primary

    if FORECAST_PROVIDER == "forecast_solar":
        return ForecastSolarForecast(logger)

    if FORECAST_PROVIDER == "quartz":
        return QuartzSolarForecast(logger)

    raise ValueError(
        f"Unsupported FORECAST_PROVIDER='{FORECAST_PROVIDER}'. "
        "Use 'esb_api', 'forecast_solar' or 'quartz'."
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

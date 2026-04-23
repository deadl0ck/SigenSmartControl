"""Forecast.Solar provider implementation and archive helpers."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import json
import logging
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from config.settings import (
    FORECAST_SOLAR_API_TIMEOUT_SECONDS,
    FORECAST_SOLAR_POWER_MULTIPLIER,
    LOCAL_TIMEZONE,
    QUARTZ_GREEN_CAPACITY_FRACTION,
    QUARTZ_RED_CAPACITY_FRACTION,
)
from config.constants import (
    FORECAST_SOLAR_API_BASE_URL,
    FORECAST_SOLAR_API_KEY,
    FORECAST_SOLAR_ARCHIVE_PATH,
    FORECAST_SOLAR_PLANE_AZIMUTH,
    FORECAST_SOLAR_PLANE_DECLINATION,
    FORECAST_SOLAR_SITE_KWP,
    LATITUDE,
    LONGITUDE,
)
from weather.providers.common import BaseSolarForecast, TableRow


def build_forecast_solar_endpoint() -> str:
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


def extract_forecast_solar_watts_map(payload: Any) -> tuple[dict[str, float], str]:
    """Extract Forecast.Solar point map from supported response shapes."""
    if not isinstance(payload, dict):
        return {}, "missing"

    result = payload.get("result")
    if not isinstance(result, dict):
        return {}, "missing"

    watts_nested = result.get("watts")
    if isinstance(watts_nested, dict) and watts_nested:
        nested_normalized = {
            str(ts): float(value)
            for ts, value in watts_nested.items()
            if isinstance(value, (int, float))
        }
        if nested_normalized:
            return nested_normalized, "result.watts"

    direct_normalized = {
        str(ts): float(value)
        for ts, value in result.items()
        if isinstance(value, (int, float))
    }
    if direct_normalized:
        return direct_normalized, "result"

    return {}, "missing"


def archive_forecast_solar_snapshot(logger: logging.Logger, now_utc: datetime) -> None:
    """Pull and persist one raw Forecast.Solar reading snapshot."""
    endpoint = build_forecast_solar_endpoint()
    response = requests.get(endpoint, timeout=FORECAST_SOLAR_API_TIMEOUT_SECONDS)
    response.raise_for_status()
    body = response.json()
    readings, source_format = extract_forecast_solar_watts_map(body)
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


class ForecastSolarForecast(BaseSolarForecast):
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
        return build_forecast_solar_endpoint()

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

        watts_by_timestamp, _source_name = extract_forecast_solar_watts_map(body)
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

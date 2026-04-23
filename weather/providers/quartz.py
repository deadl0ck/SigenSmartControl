"""Open Quartz forecast provider implementation."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import logging
from typing import TypeAlias
from zoneinfo import ZoneInfo

import requests

from config.settings import (
    LOCAL_TIMEZONE,
    QUARTZ_API_TIMEOUT_SECONDS,
    QUARTZ_GREEN_CAPACITY_FRACTION,
    QUARTZ_RED_CAPACITY_FRACTION,
)
from config.constants import LATITUDE, LONGITUDE, QUARTZ_FORECAST_API_URL, QUARTZ_SITE_CAPACITY_KWP
from weather.providers.common import BaseSolarForecast, TableRow


GroupedKw: TypeAlias = dict[tuple[str, str], list[float]]


class QuartzSolarForecast(BaseSolarForecast):
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
        """Map Quartz period-average output to Red/Amber/Green."""
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

        grouped: GroupedKw = defaultdict(list)
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

        period_order = {"Morn": 0, "Aftn": 1, "Eve": 2, "NIGHT": 3}
        normalized_rows.sort(key=lambda row: (row[0], period_order.get(row[1], 99)))
        self.table_data = normalized_rows
        self._log_table()

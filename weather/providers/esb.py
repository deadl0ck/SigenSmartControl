"""ESB county API forecast provider implementation."""

from __future__ import annotations

from datetime import datetime
import logging

import requests

from config.settings import ESB_API_TIMEOUT_SECONDS
from config.constants import (
    COUNTY,
    ESB_COUNTY_ID_MAP,
    ESB_FORECAST_API_BASE_URL,
    ESB_FORECAST_API_ENDPOINT,
    ESB_FORECAST_API_SUBSCRIPTION_KEY,
)
from weather.providers.common import BaseSolarForecast, TableRow


class EsbSolarForecast(BaseSolarForecast):
    """ESB county API-backed forecast provider."""

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

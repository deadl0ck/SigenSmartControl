"""Solcast rooftop forecast provider with rate-limit-safe disk caching."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

from config.settings import (
    FORECAST_ANALYSIS_AFTERNOON_END_HOUR,
    FORECAST_ANALYSIS_AFTERNOON_START_HOUR,
    FORECAST_ANALYSIS_EVENING_END_HOUR,
    FORECAST_ANALYSIS_EVENING_START_HOUR,
    FORECAST_ANALYSIS_MORNING_END_HOUR,
    FORECAST_ANALYSIS_MORNING_START_HOUR,
    LOCAL_TIMEZONE,
    QUARTZ_GREEN_CAPACITY_FRACTION,
    QUARTZ_RED_CAPACITY_FRACTION,
    SOLCAST_API_TIMEOUT_SECONDS,
    SOLCAST_FETCH_WINDOW_END_HOUR,
    SOLCAST_FETCH_WINDOW_START_HOUR,
    SOLCAST_MIN_FETCH_INTERVAL_MINUTES,
)
from config.constants import (
    FORECAST_SOLAR_SITE_KWP,
    SOLCAST_API_KEY,
    SOLCAST_ARCHIVE_PATH,
    SOLCAST_ROOFTOP_URL,
)
from weather.providers.common import BaseSolarForecast, TableRow


class SolcastForecast(BaseSolarForecast):
    """Solcast rooftop-site forecast provider with disk-cached responses.

    Solcast free tier allows 10 API calls/day. Responses are archived to JSONL
    and reused until SOLCAST_MIN_FETCH_INTERVAL_MINUTES elapses, so the
    scheduler can run freely without burning the daily quota.
    """

    def __init__(self, logger: logging.Logger) -> None:
        super().__init__(logger, "Solcast")
        self._load_solcast_table()

    @staticmethod
    def _period_from_hour(hour_local: int) -> str:
        if FORECAST_ANALYSIS_MORNING_START_HOUR <= hour_local < FORECAST_ANALYSIS_MORNING_END_HOUR:
            return "Morn"
        if FORECAST_ANALYSIS_AFTERNOON_START_HOUR <= hour_local < FORECAST_ANALYSIS_AFTERNOON_END_HOUR:
            return "Aftn"
        if FORECAST_ANALYSIS_EVENING_START_HOUR <= hour_local < FORECAST_ANALYSIS_EVENING_END_HOUR:
            return "Eve"
        return "Night"

    @classmethod
    def _status_from_avg_kw(cls, avg_kw: float) -> str:
        capacity_kw = max(FORECAST_SOLAR_SITE_KWP, 0.1)
        if (avg_kw / capacity_kw) < QUARTZ_RED_CAPACITY_FRACTION:
            return "Red"
        if (avg_kw / capacity_kw) < QUARTZ_GREEN_CAPACITY_FRACTION:
            return "Amber"
        return "Green"

    def _load_cached(
        self, archive_path: Path, now_utc: datetime, ignore_age: bool = False
    ) -> list[dict[str, Any]] | None:
        """Return cached forecasts if present and still fresh (or if ignore_age is True)."""
        if not archive_path.exists():
            return None
        last: dict[str, Any] | None = None
        with archive_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line)
                    except json.JSONDecodeError:
                        continue
        if last is None:
            return None
        try:
            captured = datetime.fromisoformat(last["captured_at_utc"])
            age_minutes = (now_utc - captured).total_seconds() / 60
            if ignore_age:
                self.logger.info(
                    "[SOLCAST] Using cached forecast (%.0f min old, age check suppressed outside fetch window)",
                    age_minutes,
                )
                return last.get("forecasts", [])
            if age_minutes < SOLCAST_MIN_FETCH_INTERVAL_MINUTES:
                self.logger.info(
                    "[SOLCAST] Using cached forecast (%.0f min old, refresh after %d min)",
                    age_minutes,
                    SOLCAST_MIN_FETCH_INTERVAL_MINUTES,
                )
                return last.get("forecasts", [])
        except Exception:
            pass
        return None

    def _fetch_and_archive(self, archive_path: Path, now_utc: datetime) -> list[dict[str, Any]]:
        """Fetch from Solcast API and append raw response to the archive."""
        headers = {"Authorization": f"Bearer {SOLCAST_API_KEY}"} if SOLCAST_API_KEY else {}
        response = requests.get(
            SOLCAST_ROOFTOP_URL, headers=headers, timeout=SOLCAST_API_TIMEOUT_SECONDS
        )
        response.raise_for_status()
        body = response.json()
        forecasts: list[dict[str, Any]] = body.get("forecasts", [])
        if not forecasts:
            raise ValueError("Solcast response contained no forecast entries")

        snapshot = {
            "captured_at_utc": now_utc.isoformat(),
            "source": "solcast",
            "url": SOLCAST_ROOFTOP_URL,
            "point_count": len(forecasts),
            "forecasts": forecasts,
        }
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        with archive_path.open("a", encoding="utf-8") as f:
            json.dump(snapshot, f, sort_keys=True)
            f.write("\n")

        self.logger.info("[SOLCAST] Fetched and archived %d forecast points", len(forecasts))
        return forecasts

    def _load_solcast_table(self) -> None:
        now_utc = datetime.now(timezone.utc)
        archive_path = Path(SOLCAST_ARCHIVE_PATH)
        local_tz = ZoneInfo(LOCAL_TIMEZONE)

        local_hour = datetime.now(local_tz).hour
        in_fetch_window = SOLCAST_FETCH_WINDOW_START_HOUR <= local_hour < SOLCAST_FETCH_WINDOW_END_HOUR

        if not in_fetch_window:
            self.logger.info(
                "[SOLCAST] Outside fetch window (%02d:00–%02d:00 local) — serving cache.",
                SOLCAST_FETCH_WINDOW_START_HOUR,
                SOLCAST_FETCH_WINDOW_END_HOUR,
            )
            forecasts = self._load_cached(archive_path, now_utc, ignore_age=True)
            if forecasts is None:
                forecasts = self._fetch_and_archive(archive_path, now_utc)
        else:
            forecasts = self._load_cached(archive_path, now_utc)
            if forecasts is None:
                forecasts = self._fetch_and_archive(archive_path, now_utc)

        grouped: dict[tuple[str, str], list[float]] = defaultdict(list)
        for entry in forecasts:
            try:
                dt = datetime.fromisoformat(entry["period_end"].replace("Z", "+00:00"))
                local_dt = dt.astimezone(local_tz)
                period = self._period_from_hour(local_dt.hour)
                day_label = local_dt.strftime("%a").capitalize()
                grouped[(day_label, period)].append(float(entry["pv_estimate"]))
            except Exception:
                continue

        normalized_rows: list[TableRow] = []
        for (day_label, period), kw_values in grouped.items():
            avg_kw = sum(kw_values) / len(kw_values)
            value = int(round(avg_kw * 1000.0))
            status = self._status_from_avg_kw(avg_kw)
            normalized_rows.append((day_label, period, value, status))

        if not normalized_rows:
            raise ValueError("Solcast forecast produced no usable period rows")

        period_order = {"Morn": 0, "Aftn": 1, "Eve": 2, "Night": 3}
        normalized_rows.sort(key=lambda row: (row[0], period_order.get(row[1], 99)))
        self.table_data = normalized_rows
        self._log_table()

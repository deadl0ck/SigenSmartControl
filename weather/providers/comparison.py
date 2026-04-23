"""Comparison provider that logs and archives multi-provider forecast snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from weather.providers.common import InverterPlan, PeriodForecast, SolarForecastProvider


@dataclass(frozen=True)
class ComparisonConfig:
    """Static configuration used by comparison logging and archive snapshots."""

    archive_path: str
    local_timezone: str
    county: str
    latitude: float
    longitude: float
    quartz_site_capacity_kwp: float
    forecast_solar_site_kwp: float
    quartz_red_fraction: float
    quartz_green_fraction: float
    forecast_solar_power_multiplier: float


class ForecastComparisonProvider:
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
        config: ComparisonConfig,
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
        self._config = config
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
        """Keep primary statuses while borrowing secondary watts when available."""
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

    def _format_period_value(self, provider_name: str, value: int, status: str) -> str:
        """Format comparison output so synthetic ESB values are clearly labelled."""
        if provider_name == "esb_api":
            return f"{status} (county status, synthetic={value}W)"

        if provider_name == "quartz":
            capacity_kw = max(self._config.quartz_site_capacity_kwp, 0.1)
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
        archive_path = Path(self._config.archive_path)
        snapshot = {
            "captured_at": datetime.now(ZoneInfo(self._config.local_timezone)).isoformat(),
            "timezone": self._config.local_timezone,
            "primary_provider": self._primary_name,
            "secondary_provider": self._secondary_name,
            "tertiary_provider": self._tertiary_name,
            "county": self._config.county,
            "latitude": self._config.latitude,
            "longitude": self._config.longitude,
            "quartz_site_capacity_kwp": self._config.quartz_site_capacity_kwp,
            "forecast_solar_site_kwp": self._config.forecast_solar_site_kwp,
            "normalization": {
                "quartz_period_timezone": self._config.local_timezone,
                "quartz_red_lt_fraction": self._config.quartz_red_fraction,
                "quartz_amber_lt_fraction": self._config.quartz_green_fraction,
                "forecast_solar_power_multiplier": self._config.forecast_solar_power_multiplier,
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
            f"[FORECAST-COMPARE] {day_label}: {self._primary_name} "
            f"(decision source) vs {self._secondary_name} (secondary)"
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
                f"{self._primary_name}="
                f"{self._format_period_value(self._primary_name, left_w, left_status)} | "
                f"{self._secondary_name}="
                f"{self._format_period_value(self._secondary_name, right_w, right_status)}"
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
            f"({self._config.local_timezone}) and capacity-based thresholds: "
            f"Red < {int(self._config.quartz_red_fraction * 100)}%, "
            f"Amber < {int(self._config.quartz_green_fraction * 100)}%, "
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

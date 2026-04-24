"""Solar forecast facade and provider selection utilities.

This module keeps the public weather.forecast API stable while delegating
provider-specific implementations to dedicated submodules.
"""

from __future__ import annotations

from datetime import datetime
import logging

from config.settings import (
    FORECAST_SOLAR_POWER_MULTIPLIER,
    LOCAL_TIMEZONE,
    QUARTZ_GREEN_CAPACITY_FRACTION,
    QUARTZ_RED_CAPACITY_FRACTION,
)
from config.constants import (
    COUNTY,
    FORECAST_COMPARISON_ARCHIVE_PATH,
    FORECAST_PROVIDER,
    FORECAST_SOLAR_SITE_KWP,
    LATITUDE,
    LONGITUDE,
    QUARTZ_SITE_CAPACITY_KWP,
)
from weather.providers.common import SolarForecastProvider
from weather.providers.comparison import ComparisonConfig, ForecastComparisonProvider
from weather.providers.esb import EsbSolarForecast
from weather.providers.forecast_solar import (
    ForecastSolarForecast as _ForecastSolarForecast,
    archive_forecast_solar_snapshot as _archive_forecast_solar_snapshot,
)
from weather.providers.quartz import QuartzSolarForecast as _QuartzSolarForecast


class SolarForecast(EsbSolarForecast):
    """Backwards-compatible ESB provider class name."""


class QuartzSolarForecast(_QuartzSolarForecast):
    """Backwards-compatible Quartz provider with module-level threshold overrides."""

    @classmethod
    def _status_from_avg_kw(cls, avg_kw: float) -> str:
        """Map Quartz average kW to Red/Amber/Green status."""
        capacity_kw = max(QUARTZ_SITE_CAPACITY_KWP, 0.1)
        output_fraction = avg_kw / capacity_kw
        if output_fraction < QUARTZ_RED_CAPACITY_FRACTION:
            return "Red"
        if output_fraction < QUARTZ_GREEN_CAPACITY_FRACTION:
            return "Amber"
        return "Green"


class ForecastSolarForecast(_ForecastSolarForecast):
    """Backwards-compatible Forecast.Solar provider class name."""


def archive_forecast_solar_snapshot(logger: logging.Logger, now_utc: datetime) -> None:
    """Archive one Forecast.Solar raw snapshot."""
    _archive_forecast_solar_snapshot(logger, now_utc)


class ComparingSolarForecastProvider(ForecastComparisonProvider):
    """Backwards-compatible comparison provider facade."""

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
        super().__init__(
            logger,
            primary,
            secondary,
            primary_name=primary_name,
            secondary_name=secondary_name,
            tertiary=tertiary,
            tertiary_name=tertiary_name,
            config=ComparisonConfig(
                archive_path=FORECAST_COMPARISON_ARCHIVE_PATH,
                local_timezone=LOCAL_TIMEZONE,
                county=COUNTY,
                latitude=LATITUDE,
                longitude=LONGITUDE,
                quartz_site_capacity_kwp=QUARTZ_SITE_CAPACITY_KWP,
                forecast_solar_site_kwp=FORECAST_SOLAR_SITE_KWP,
                quartz_red_fraction=QUARTZ_RED_CAPACITY_FRACTION,
                quartz_green_fraction=QUARTZ_GREEN_CAPACITY_FRACTION,
                forecast_solar_power_multiplier=FORECAST_SOLAR_POWER_MULTIPLIER,
            ),
        )


def create_solar_forecast_provider(logger: logging.Logger) -> SolarForecastProvider:
    """Create the active forecast provider based on constants configuration."""
    if FORECAST_PROVIDER == "esb_api":
        primary = SolarForecast(logger)

        provider_classes = [
            (ForecastSolarForecast, "forecast.solar"),
            (QuartzSolarForecast, "quartz"),
        ]
        providers: list[ForecastSolarForecast | QuartzSolarForecast] = []
        for cls, name in provider_classes:
            try:
                providers.append(cls(logger))
            except Exception as exc:
                logger.warning("[FORECAST-COMPARE] %s unavailable: %s", name, exc)

        forecast_solar_provider = next(
            (p for p in providers if isinstance(p, ForecastSolarForecast)), None
        )
        quartz_provider = next(
            (p for p in providers if isinstance(p, QuartzSolarForecast)), None
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
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    log = logging.getLogger(__name__)
    solar_forecast = create_solar_forecast_provider(log)
    todays_values = solar_forecast.get_todays_solar_values()
    todays_periods = solar_forecast.get_todays_period_forecast()
    inverter_plan = solar_forecast.get_simple_inverter_plan()
    log.info("Today's forecast: %s", todays_values)
    log.info("Today's period forecast: %s", todays_periods)
    log.info("Simple inverter plan: %s", inverter_plan)
    log.info("A good day? %s", solar_forecast.is_good_day())

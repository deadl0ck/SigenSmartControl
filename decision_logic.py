from config import SIGEN_MODES, FORECAST_TO_MODE, TARIFF_TO_MODE


def calc_headroom_kwh(battery_kwh: float, soc: float) -> float:
    return battery_kwh * (1 - soc / 100)


def decide_operational_mode(
    period: str,
    status: str,
    soc: float | None,
    headroom_kwh: float | None,
    period_solar_kwh: float,
    *,
    tariff_period: str | None = None,
    headroom_frac: float = 0.25,
    soc_high_threshold: float = 95,
) -> tuple[int, str]:
    status_key = (status or "").upper()
    period_key = (period or "").upper()
    tariff_key = (tariff_period or "").upper()

    if (
        soc is not None
        and status_key == "GREEN"
        and headroom_kwh is not None
        and headroom_kwh < period_solar_kwh * headroom_frac
    ):
        mode = SIGEN_MODES["GRID_EXPORT"]
        reason = (
            f"Headroom ({headroom_kwh:.2f} kWh) < {headroom_frac*100:.0f}% of expected solar "
            f"({period_solar_kwh:.2f} kWh). Preemptively exporting to grid."
        )
        return mode, reason

    if period_key == "NIGHT":
        mode = TARIFF_TO_MODE["NIGHT"]
        reason = "Night period detected. Using tariff-based mode."
        return mode, reason

    if soc is not None and soc >= soc_high_threshold and status_key == "GREEN":
        mode = SIGEN_MODES["GRID_EXPORT"]
        reason = f"SOC >= {soc_high_threshold}% and forecast is Green. Exporting to grid."
        return mode, reason

    mode = FORECAST_TO_MODE.get(status_key, SIGEN_MODES["AI"])
    reason = f"Default mapping for {status}."

    # During expensive peak tariff windows, prioritize self-powered operation
    # unless one of the explicit export-to-grid rules already triggered above.
    if tariff_key == "PEAK" and mode != SIGEN_MODES["GRID_EXPORT"]:
        mode = TARIFF_TO_MODE["PEAK"]
        reason = (
            f"{reason} Tariff period is Peak, so prioritizing self-powered mode "
            "to reduce expensive grid import."
        )

    return mode, reason


def decide_night_preparation_mode(
    target_period: str,
    status: str,
    soc: float | None,
    headroom_kwh: float | None,
    period_solar_kwh: float,
    *,
    headroom_frac: float = 0.25,
    soc_high_threshold: float = 95,
) -> tuple[int, str]:
    if not target_period or not status:
        mode = TARIFF_TO_MODE["NIGHT"]
        return mode, "No next-day forecast available. Using tariff-based night mode."

    mode, reason = decide_operational_mode(
        period=target_period,
        status=status,
        soc=soc,
        headroom_kwh=headroom_kwh,
        period_solar_kwh=period_solar_kwh,
        headroom_frac=headroom_frac,
        soc_high_threshold=soc_high_threshold,
    )
    if mode == SIGEN_MODES["GRID_EXPORT"]:
        return mode, f"Next-day preparation for {target_period}: {reason}"

    mode = TARIFF_TO_MODE["NIGHT"]
    return mode, (
        f"Next-day preparation for {target_period}: export is not required. "
        "Using tariff-based night mode."
    )

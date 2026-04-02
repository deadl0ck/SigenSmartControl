# This module provides a function to simulate the Sigen logic for the web API.
# It reuses your config and mapping logic, but does not require hardware or real API calls.

from config import SIGEN_MODES
from decision_logic import (
    decide_operational_mode,
    decide_night_preparation_mode,
    calc_headroom_kwh,
)

def simulate_sigen_decision(
    inverter_kw: float,
    battery_kwh: float,
    solar_pv_kw: float,
    soc: float,
    forecast_morn: str,
    forecast_aftn: str,
    forecast_eve: str,
    custom_var: str = None
):
    """
    Simulate the Sigen control logic for the web API.
    Returns a dict with the mode decision for each period.
    """
    HEADROOM_FRAC = 0.25
    SOC_HIGH_THRESHOLD = 95
    periods = [
        ("Morn", forecast_morn),
        ("Aftn", forecast_aftn),
        ("Eve", forecast_eve),
        # Night mode itself is tariff-driven; we use next morning's forecast context.
        ("NIGHT", forecast_morn),
    ]
    results = {}
    for period, status in periods:
        # Simulate headroom and solar (simplified)
        headroom_kwh = calc_headroom_kwh(battery_kwh, soc)
        period_solar_kwh = 0.0 if period == "NIGHT" else min(solar_pv_kw, inverter_kw) * 3.0
        mode, reason = decide_operational_mode(
            period=period,
            status=status,
            soc=soc,
            headroom_kwh=headroom_kwh,
            period_solar_kwh=period_solar_kwh,
            headroom_frac=HEADROOM_FRAC,
            soc_high_threshold=SOC_HIGH_THRESHOLD,
        )
        results[period] = {
            "mode": mode,
            "mode_name": [k for k, v in SIGEN_MODES.items() if v == mode][0],
            "reason": reason,
        }

    # Simulate optional overnight prep decision for next morning.
    next_morn_solar_kwh = min(solar_pv_kw, inverter_kw) * 3.0
    prep_mode, prep_reason = decide_night_preparation_mode(
        target_period="Morn",
        status=forecast_morn,
        soc=soc,
        headroom_kwh=calc_headroom_kwh(battery_kwh, soc),
        period_solar_kwh=next_morn_solar_kwh,
        headroom_frac=HEADROOM_FRAC,
        soc_high_threshold=SOC_HIGH_THRESHOLD,
    )
    results["NightPrep"] = {
        "mode": prep_mode,
        "mode_name": [k for k, v in SIGEN_MODES.items() if v == prep_mode][0],
        "reason": prep_reason,
    }

    return results

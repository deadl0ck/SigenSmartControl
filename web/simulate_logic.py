# This module provides a function to simulate the Sigen logic for the web API.
# It reuses your config and mapping logic, but does not require hardware or real API calls.

from config import SIGEN_MODES, FORECAST_TO_MODE, TARIFF_TO_MODE

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
    ]
    results = {}
    for period, status in periods:
        # Simulate headroom and solar (simplified)
        headroom_kwh = battery_kwh * (1 - soc / 100)
        period_solar_kwh = min(solar_pv_kw, inverter_kw) * 3.0  # Assume 3h period
        # Pre-period export logic
        if (
            status.upper() == "GREEN"
            and headroom_kwh < period_solar_kwh * HEADROOM_FRAC
        ):
            mode = SIGEN_MODES["GRID_EXPORT"]
            reason = f"Headroom ({headroom_kwh:.2f} kWh) < {HEADROOM_FRAC*100:.0f}% of expected solar ({period_solar_kwh:.2f} kWh). Preemptively exporting to grid."
        elif soc >= SOC_HIGH_THRESHOLD and status.upper() == "GREEN":
            mode = SIGEN_MODES["GRID_EXPORT"]
            reason = f"SOC >= {SOC_HIGH_THRESHOLD}% and forecast is Green. Exporting to grid."
        else:
            mode = FORECAST_TO_MODE.get(status.upper(), SIGEN_MODES["AI"])
            reason = f"Default mapping for {status}."
        results[period] = {
            "mode": mode,
            "mode_name": [k for k, v in SIGEN_MODES.items() if v == mode][0],
            "reason": reason,
        }
    return results

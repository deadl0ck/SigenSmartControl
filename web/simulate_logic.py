"""
simulate_logic.py
-----------------
Web API simulation module for testing Sigen control logic without hardware.

Reuses the core decision_logic engine but accepts parameters via the web API rather than
fetching from the real Sigen inverter. Allows users to explore different forecast scenarios,
battery states, and system configurations without live hardware.
"""

from config.settings import SIGEN_MODES, HEADROOM_TARGET_KWH
from logic.decision_logic import (
    decide_operational_mode,
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
) -> dict[str, dict[str, int | str]]:
    """Simulate the Sigen control logic for the web API.
    
    Evaluates operational mode decisions for each solar period based on provided
    forecasts and system parameters. Uses simplified headroom/solar estimates.
    
    Args:
        inverter_kw: Inverter capacity in kW.
        battery_kwh: Total battery capacity in kWh.
        solar_pv_kw: Solar PV system capacity in kW.
        soc: Current battery state-of-charge (0-100).
        forecast_morn: Morning period forecast status ('Green', 'Amber', or 'Red').
        forecast_aftn: Afternoon period forecast status.
        forecast_eve: Evening period forecast status.
        
    Returns:
        Dict with keys for each period ('Morn', 'Aftn', 'Eve', 'NIGHT') mapping to
        mode details: {'mode': int, 'mode_name': str, 'reason': str, 'forecast': str}.
    """
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
            headroom_target_kwh=HEADROOM_TARGET_KWH,
        )
        results[period] = {
            "mode": mode,
            "mode_name": [k for k, v in SIGEN_MODES.items() if v == mode][0],
            "reason": reason,
        }

    return results

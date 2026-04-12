"""
decision_logic.py
-----------------
Core decision engine for operational mode selection based on solar forecast, battery state,
and tariff periods. Used by both the scheduler (main.py) and web simulator.

Implements a hierarchical decision tree:
1. Headroom-based export rule (battery space below physics-derived target)
2. Night period detection
3. Evening battery bridge rule (prevent premature charging)
4. Forecast-to-mode mapping with tariff overrides
5. Peak tariff self-powered override
"""

from config.settings import (
    FORECAST_TO_MODE,
    LIVE_CLIPPING_RISK_VALID_PERIODS,
    MORNING_HIGH_SOC_PROTECTION_ENABLED,
    MORNING_HIGH_SOC_THRESHOLD_PERCENT,
    PERIOD_TO_MODE,
    SIGEN_MODES,
)


def _parse_period_codes(codes_str: str) -> set[str]:
    """Parse a comma-separated period code string into a validated set.

    Args:
        codes_str: Comma-separated period codes (e.g., ``"M,A"``). Supported
            codes are M (Morning), A (Afternoon), and E (Evening).

    Returns:
        Validated set of period codes. Falls back to ``{"M", "A"}`` when the
        string is empty or contains no recognised codes.
    """
    raw = {token.strip().upper() for token in codes_str.split(",") if token.strip()}
    valid = raw & {"M", "A", "E"}
    return valid or {"M", "A"}


def is_live_clipping_period_enabled(period: str) -> bool:
    """Return whether live clipping-risk Amber→Green promotion is enabled for a period.

    Reads ``LIVE_CLIPPING_RISK_VALID_PERIODS`` from settings. This controls only
    the scheduler's intra-tick signal that promotes an Amber forecast to Green when
    live solar generation and battery SOC both exceed their configured thresholds.

    Args:
        period: Scheduler period name (e.g., Morn, Aftn, Eve).

    Returns:
        True when the period is covered by ``LIVE_CLIPPING_RISK_VALID_PERIODS``.
    """
    period_to_code = {"MORN": "M", "AFTN": "A", "EVE": "E"}
    period_code = period_to_code.get((period or "").upper())
    if period_code is None:
        return False
    return period_code in _parse_period_codes(LIVE_CLIPPING_RISK_VALID_PERIODS)


def calc_headroom_kwh(battery_kwh: float, soc: float) -> float:
    """Calculate available battery headroom (reserved capacity for charging).
    
    Args:
        battery_kwh: Total battery capacity in kWh.
        soc: Current state-of-charge as a percentage (0-100).
        
    Returns:
        Available headroom in kWh (capacity × (1 - SOC/100)).
    """
    return battery_kwh * (1 - soc / 100)


def decide_operational_mode(
    period: str,
    status: str,
    soc: float | None,
    headroom_kwh: float | None,
    period_solar_kwh: float,
    *,
    schedule_period: str | None = None,
    headroom_target_kwh: float = 0.0,
    battery_kwh: float | None = None,
    hours_until_cheap_rate: float | None = None,
    estimated_home_load_kw: float | None = None,
    bridge_battery_reserve_kwh: float = 0.0,
    enable_pre_cheap_rate_battery_bridge: bool = False,
) -> tuple[int, str]:
    """Determine the optimal inverter operational mode for current conditions.
    
    Implements a hierarchical decision tree:
    1. Export if headroom is below the physics-derived target before a Green period
    2. Daytime high-SOC protection for Amber/Green morning and afternoon periods
    3. Use tariff mode if night period
    4. Evening bridge: use self-powered if battery can cover load until cheap rate
    5. Map forecast status to default mode (Green→self-powered, Amber→AI, Red→TOU)
    6. Peak tariff override: prioritize self-powered during expensive hours
    
    Args:
        period: Current period name (e.g., 'Morn', 'Aftn', 'Eve', 'Night').
        status: Solar forecast status ('GREEN', 'AMBER', 'RED').
        soc: Current battery state-of-charge (0-100), or None if unavailable.
        headroom_kwh: Available battery headroom for charging, or None if SOC unavailable.
        period_solar_kwh: Estimated solar energy available in this period (used in reason text).
        schedule_period: Current schedule period ('NIGHT', 'PEAK', 'DAY'), or None.
        headroom_target_kwh: Required free headroom in kWh before a Green period. Derived
            from hardware surplus capacity: (SOLAR_PV_KW - INVERTER_KW) × period_hours.
        battery_kwh: Total battery capacity, needed for evening bridge rule.
        hours_until_cheap_rate: Hours until cheap-rate tariff starts, needed for bridge rule.
        estimated_home_load_kw: Average household load in kW, needed for bridge rule.
        bridge_battery_reserve_kwh: Minimum battery reserve to maintain, used in bridge calc.
        enable_pre_cheap_rate_battery_bridge: Enable evening battery preservation rule.
        
    Returns:
        Tuple of (mode_value: int, reason: str) explaining the mode choice.
    """
    status_key = (status or "").upper()
    period_key = (period or "").upper()
    schedule_key = (schedule_period or "").upper()

    if (
        soc is not None
        and status_key == "GREEN"
        and headroom_kwh is not None
        and headroom_kwh < headroom_target_kwh
    ):
        mode = SIGEN_MODES["GRID_EXPORT"]
        reason = (
            f"Headroom ({headroom_kwh:.2f} kWh) < target ({headroom_target_kwh:.2f} kWh). "
            "Preemptively exporting to grid."
        )
        return mode, reason

    if (
        MORNING_HIGH_SOC_PROTECTION_ENABLED
        and status_key in {"AMBER", "GREEN"}
        and soc is not None
        and soc >= MORNING_HIGH_SOC_THRESHOLD_PERCENT
        and headroom_kwh is not None
        and headroom_kwh < headroom_target_kwh
    ):
        mode = SIGEN_MODES["GRID_EXPORT"]
        period_label = {
            "MORN": "Morning",
            "AFTN": "Afternoon",
            "EVE": "Evening",
        }.get(period_key, period)
        reason = (
            f"{period_label} high-SOC protection: "
            f"SOC ({soc:.1f}%) >= {MORNING_HIGH_SOC_THRESHOLD_PERCENT:.1f}% and "
            f"headroom ({headroom_kwh:.2f} kWh) < target ({headroom_target_kwh:.2f} kWh). "
            "Preemptively exporting to grid."
        )
        return mode, reason

    if period_key == "NIGHT":
        mode = PERIOD_TO_MODE["NIGHT"]
        reason = "Night period detected. Applying configured night mode."
        return mode, reason

    # Before cheap-rate starts, prefer battery usage over charge-oriented behavior
    # when the battery can safely cover expected load until cheap-rate begins.
    if (
        enable_pre_cheap_rate_battery_bridge
        and period_key == "EVE"
        and soc is not None
        and battery_kwh is not None
        and hours_until_cheap_rate is not None
        and estimated_home_load_kw is not None
        and hours_until_cheap_rate > 0
    ):
        available_kwh = max(0.0, battery_kwh * (soc / 100.0) - bridge_battery_reserve_kwh)
        required_kwh = max(0.0, hours_until_cheap_rate * estimated_home_load_kw)
        if available_kwh >= required_kwh:
            mode = SIGEN_MODES["SELF_POWERED"]
            reason = (
                "Evening bridge rule: battery has enough usable energy "
                f"({available_kwh:.2f} kWh) to cover expected load until cheap-rate "
                f"starts ({required_kwh:.2f} kWh required). Prioritizing self-powered mode."
            )
            return mode, reason

    mode = FORECAST_TO_MODE.get(status_key, SIGEN_MODES["AI"])
    reason = f"Default mapping for {status}."

    # During expensive peak tariff windows, prioritize self-powered operation
    # unless one of the explicit export-to-grid rules already triggered above.
    if schedule_key == "PEAK" and mode != SIGEN_MODES["GRID_EXPORT"]:
        mode = PERIOD_TO_MODE["PEAK"]
        reason = (
            f"{reason} Schedule period is Peak, so prioritizing self-powered mode "
            "to reduce grid import."
        )

    return mode, reason



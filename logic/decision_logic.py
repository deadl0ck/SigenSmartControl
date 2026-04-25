"""
decision_logic.py
-----------------
Core decision engine for operational mode selection based on solar forecast, battery state,
and tariff periods. Used by both the scheduler (main.py) and web simulator.

Implements a hierarchical decision tree:
1. Headroom-based export rule (battery space below physics-derived target)
2. Night period detection
3. Evening battery bridge rule (prevent premature charging)
4. Forecast-to-mode mapping with deterministic fallback and tariff overrides
5. Peak tariff self-powered override
"""

from dataclasses import dataclass

from config.enums import ForecastStatus, Period
from config.settings import (
    BATTERY_KWH,
    ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE,
    FORECAST_TO_MODE,
    LIVE_CLIPPING_RISK_VALID_PERIODS,
    MORNING_HIGH_SOC_PROTECTION_ENABLED,
    MORNING_HIGH_SOC_THRESHOLD_PERCENT,
    PERIOD_TO_MODE,
    SIGEN_MODES,
)


@dataclass
class DecisionContext:
    """All inputs needed by decide_operational_mode() to select an inverter mode.

    Attributes:
        period: Current period name (e.g., 'Morn', 'Aftn', 'Eve', 'Night').
        status: Solar forecast status ('GREEN', 'AMBER', 'RED'), or None.
        soc: Current battery state-of-charge (0-100), or None if unavailable.
        headroom_kwh: Available battery headroom for charging, or None if SOC unavailable.
        headroom_target_kwh: Required free headroom in kWh before a Green period.
        live_solar_kw: Current live solar generation in kW, or None if unavailable.
        hours_until_cheap_rate: Hours until cheap-rate tariff starts, or None.
        estimated_home_load_kw: Average household load in kW, or None.
        bridge_battery_reserve_kwh: Minimum battery reserve to maintain in bridge calc, or None.
        tariff: Current schedule period ('NIGHT', 'PEAK', 'DAY'), or None.
    """

    period: str
    status: str | None
    soc: float | None
    headroom_kwh: float | None
    headroom_target_kwh: float
    live_solar_kw: float | None
    hours_until_cheap_rate: float | None
    estimated_home_load_kw: float | None
    bridge_battery_reserve_kwh: float | None
    tariff: str | None


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


def decide_operational_mode(ctx: DecisionContext) -> tuple[int, str]:
    """Determine the optimal inverter operational mode for current conditions.

    Implements a hierarchical decision tree:
    1. Export if headroom is below the physics-derived target before a Green period
    2. Daytime high-SOC protection for Amber/Green morning and afternoon periods
    3. Use tariff mode if night period
    4. Evening bridge: use self-powered if battery can cover load until cheap rate
    5. Map forecast status to default mode (deterministic, no AI fallback)
    6. Peak tariff override: prioritize self-powered during expensive hours

    Args:
        ctx: DecisionContext containing all inputs required for mode selection.

    Returns:
        Tuple of (mode_value: int, reason: str) explaining the mode choice.
    """
    status_key = (ctx.status or "").upper()
    period_key = ctx.period or ""
    schedule_key = (ctx.tariff or "").upper()

    if (
        ctx.soc is not None
        and status_key == ForecastStatus.GREEN
        and ctx.headroom_kwh is not None
        and ctx.headroom_kwh < ctx.headroom_target_kwh
    ):
        mode = SIGEN_MODES["GRID_EXPORT"]
        reason = (
            f"Battery has {ctx.headroom_kwh:.2f} kWh headroom but needs {ctx.headroom_target_kwh:.2f} kWh — "
            "exporting to make room for incoming solar."
        )
        return mode, reason

    if (
        MORNING_HIGH_SOC_PROTECTION_ENABLED
        and status_key in {ForecastStatus.AMBER, ForecastStatus.GREEN}
        and ctx.soc is not None
        and ctx.soc >= MORNING_HIGH_SOC_THRESHOLD_PERCENT
        and ctx.headroom_kwh is not None
        and ctx.headroom_kwh < ctx.headroom_target_kwh
    ):
        mode = SIGEN_MODES["GRID_EXPORT"]
        period_label = {
            Period.MORN: "Morning",
            Period.AFTN: "Afternoon",
            Period.EVE: "Evening",
        }.get(period_key, ctx.period)
        reason = (
            f"Battery is high ({ctx.soc:.1f}%) with only {ctx.headroom_kwh:.2f} kWh headroom "
            f"(needs {ctx.headroom_target_kwh:.2f} kWh) — exporting to make room for incoming solar."
        )
        return mode, reason

    if period_key == Period.NIGHT:
        mode = PERIOD_TO_MODE[Period.NIGHT]
        reason = "Night window active — applying configured night mode."
        return mode, reason

    # Before cheap-rate starts, prefer battery usage over charge-oriented behavior
    # when the battery can safely cover expected load until cheap-rate begins.
    if (
        ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE
        and period_key == Period.EVE
        and ctx.soc is not None
        and ctx.hours_until_cheap_rate is not None
        and ctx.estimated_home_load_kw is not None
        and ctx.hours_until_cheap_rate > 0
    ):
        reserve = ctx.bridge_battery_reserve_kwh or 0.0
        available_kwh = max(0.0, BATTERY_KWH * (ctx.soc / 100.0) - reserve)
        required_kwh = max(0.0, ctx.hours_until_cheap_rate * ctx.estimated_home_load_kw)
        if available_kwh >= required_kwh:
            mode = SIGEN_MODES["SELF_POWERED"]
            reason = (
                f"Battery has enough charge ({available_kwh:.2f} kWh) to power the home "
                f"until cheap-rate electricity starts ({required_kwh:.2f} kWh needed) — "
                "staying in self-powered mode."
            )
            return mode, reason

    mode = FORECAST_TO_MODE.get(status_key, SIGEN_MODES["SELF_POWERED"])
    reason = f"Forecast is {ctx.status} — applying standard mode."

    # During expensive peak tariff windows, prioritize self-powered operation
    # unless one of the explicit export-to-grid rules already triggered above.
    if schedule_key == "PEAK" and mode != SIGEN_MODES["GRID_EXPORT"]:
        mode = PERIOD_TO_MODE["PEAK"]
        reason = (
            f"{reason} Peak electricity tariff is active — "
            "using battery to avoid expensive grid imports."
        )

    return mode, reason



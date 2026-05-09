"""
config.py

Configuration file for Sigen inverter operational mode mappings and forecast-to-mode associations.

Edit this file to adjust how forecast statuses and tariff periods map to Sigen operational modes.
"""

from config.enums import ForecastStatus, Period

# ==============================
# System Specifications
# ==============================
# Solar PV array size in kW (DC rating)
SOLAR_PV_KW = 8.9
# Inverter maximum output in kW (AC rating)
INVERTER_KW = 5.5
# Battery usable capacity in kWh
BATTERY_KWH = 24

# LOG_LEVEL controls the verbosity of logging throughout the system.
# Set to one of: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'.
LOG_LEVEL = "INFO"  # Change to 'DEBUG' for more detailed logs

# ==============================
# Runtime / Scheduler Settings
# ==============================
# Scheduler cadence.
# How often the self-contained scheduler wakes up to re-check forecast windows.
# 5 minutes balances API rate limits against decision latency; finer cadence adds no value given inverter mode-change overhead.
POLL_INTERVAL_MINUTES = 5
# How often to refresh forecast data during the day (0 disables intra-day refresh).
FORECAST_REFRESH_INTERVAL_MINUTES = 30

# Forecast.Solar archive controls.
# Whether to pull and archive raw Forecast.Solar readings during scheduler ticks.
FORECAST_SOLAR_ARCHIVE_ENABLED = True
# Minimum minutes between raw Forecast.Solar archive pulls. Public Forecast.Solar
# plans expose 1-hour resolution, so 30 minutes is a practical default that limits
# redundant polling while still checking for forecast refreshes.
FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES = 30
# Cooldown minutes applied after Forecast.Solar responds with HTTP 429.
FORECAST_SOLAR_RATE_LIMIT_COOLDOWN_MINUTES = 60

# Live solar and pre-period export calculations.
# How far ahead of a period start we begin monitoring SOC for a possible export.
# Increased to start proactive headroom checks earlier on fast-ramp solar mornings.
# 180 min (3 h) covers the full morning ramp: at 3.4 kW surplus × 3 h = ~10 kWh, matching HEADROOM_TARGET_KWH.
MAX_PRE_PERIOD_WINDOW_MINUTES = 180
# Number of live-solar samples used in rolling average calculations.
# 3 samples at 5-min intervals = 15-min average; smooths cloud transients without lagging behind genuine ramps.
LIVE_SOLAR_AVERAGE_SAMPLE_COUNT = 3
# Lower bound for effective battery export capacity in pre-period calculations.
# 0.2 kW is the minimum measurable net export after inverter standby losses; values below this are indistinguishable from idle.
MIN_EFFECTIVE_BATTERY_EXPORT_KW = 0.2

# Simulation and safety controls.
# Default fallback SOC used in full simulation mode when env var parsing fails.
DEFAULT_SIMULATED_SOC_PERCENT = 80.0
# Full simulation mode: reads data and logs intended actions but never sends
# inverter mode-change commands.
FULL_SIMULATION_MODE = False
# Maximum allowed duration for timed grid export override (minutes) — prevents accidental
# over-discharge or excessive grid arbitrage cycles.
MAX_TIMED_EXPORT_MINUTES = 240
# Mode-change retry behaviour.
# Number of additional attempts after an initial failure (0 = no retries).
MODE_CHANGE_RETRY_ATTEMPTS = 3
# Seconds to wait between retry attempts.
MODE_CHANGE_RETRY_DELAY_SECONDS = 120
# Minutes to block a new timed export after a restore completes, preventing
# the inverter from oscillating between self-consumption and grid-export on
# consecutive ticks immediately after a restore.
TIMED_EXPORT_RESTORE_COOLDOWN_MINUTES = 15

# Night and timezone behavior.
# Whether the scheduler should explicitly apply the configured night mode.
NIGHT_MODE_ENABLED = True
# Whether scheduler should sleep through inactive night periods instead of polling every tick.
NIGHT_SLEEP_MODE_ENABLED = True
# Summer pre-sunrise discharge behavior.
# When enabled, the scheduler can switch from night charging mode to self-powered
# shortly before sunrise in selected months to create battery headroom for morning solar.
ENABLE_SUMMER_PRE_SUNRISE_DISCHARGE = True
# Comma-separated local month numbers where pre-sunrise discharge is allowed.
# Example: "4,5,6,7,8,9" for Apr-Sep.
PRE_SUNRISE_DISCHARGE_MONTHS = "4,5,6,7,8,9"
# Minutes before sunrise to begin pre-sunrise discharge in enabled months.
# 120 min (2 h) gives enough time at ~2 kW discharge to clear ~4 kWh, creating meaningful headroom before solar ramps up.
PRE_SUNRISE_DISCHARGE_LEAD_MINUTES = 120
# Minimum SOC required before pre-sunrise discharge is allowed to switch away
# from night charging behavior. Below this threshold, the scheduler keeps the
# configured night mode instead of discharging into the morning.
# 65% (~15.6 kWh) ensures enough overnight reserve remains after 2 h discharge to cover typical morning household load if solar underperforms.
PRE_SUNRISE_DISCHARGE_MIN_SOC_PERCENT = 65.0
# Local timezone used for schedule windows.
LOCAL_TIMEZONE = "Europe/Dublin"

# Forecast provider configuration (site-specific settings).
# Keep these in settings.py so site geometry can be tuned without .env edits.
QUARTZ_SITE_CAPACITY_KWP = SOLAR_PV_KW
FORECAST_SOLAR_SITE_KWP = SOLAR_PV_KW
# Roof pitch / tilt angle in degrees for Forecast.Solar.
FORECAST_SOLAR_PLANE_DECLINATION = 27
# Panel azimuth in degrees (0=south, negative=east, positive=west).
FORECAST_SOLAR_PLANE_AZIMUTH = -40
# Multiplier applied to Forecast.Solar site-level power (kW) before period
# normalization and downstream scheduler calculations. Use this to correct
# persistent under/over-forecast bias against local inverter telemetry.
FORECAST_SOLAR_POWER_MULTIPLIER = 1.53

# Provider request timeouts.
# HTTP timeout for ESB county forecast API requests.
ESB_API_TIMEOUT_SECONDS = 30
# HTTP timeout for Quartz forecast API requests.
QUARTZ_API_TIMEOUT_SECONDS = 30
# HTTP timeout for Forecast.Solar API requests.
FORECAST_SOLAR_API_TIMEOUT_SECONDS = 30
# HTTP timeout for Solcast API requests.
SOLCAST_API_TIMEOUT_SECONDS = 30
# Minimum minutes between live Solcast API fetches (free tier: 10 calls/day).
# Fetches are gated to SOLCAST_FETCH_WINDOW_START_HOUR–SOLCAST_FETCH_WINDOW_END_HOUR
# (local time). Outside that window the cache is always served without an age check,
# concentrating all 10 daily calls in the hours that matter for decisions.
# 100 minutes across a 3AM–8PM window (17 hours) ≈ 10 calls/day.
SOLCAST_MIN_FETCH_INTERVAL_MINUTES = 100
# Local hours defining when live Solcast fetches are permitted.
# Before START: no solar generation and tomorrow's forecast won't change meaningfully.
# After END: evening analysis window has closed; today's forecast is no longer needed.
# 3AM gives a one-hour buffer before the pre-period export window opens at 4AM
# (MAX_PRE_PERIOD_WINDOW_MINUTES before FORECAST_ANALYSIS_MORNING_START_HOUR).
SOLCAST_FETCH_WINDOW_START_HOUR = 3
SOLCAST_FETCH_WINDOW_END_HOUR = 20
# HTTP timeout for sunrise/sunset API requests.
SUNRISE_SUNSET_API_TIMEOUT_SECONDS = 10
# SMTP connection timeout for outbound email notifications.
EMAIL_SMTP_TIMEOUT_SECONDS = 12

# Quartz normalization thresholds.
# Quartz period status normalization thresholds as fractions of configured array capacity.
# Red: output_fraction < QUARTZ_RED_CAPACITY_FRACTION
# Amber: QUARTZ_RED_CAPACITY_FRACTION <= output_fraction < QUARTZ_GREEN_CAPACITY_FRACTION
# Green: output_fraction >= QUARTZ_GREEN_CAPACITY_FRACTION
# 20% of 8.9 kW ≈ 1.8 kW: below this the inverter cannot meaningfully charge the battery; treat as Red.
QUARTZ_RED_CAPACITY_FRACTION = 0.20
# 40% of 8.9 kW ≈ 3.6 kW: above this, surplus exceeds ESTIMATED_HOME_LOAD_KW and meaningful storage is likely; treat as Green.
QUARTZ_GREEN_CAPACITY_FRACTION = 0.40

# Telemetry clipping heuristics.
# Battery SOC threshold considered "high" for clipping confidence.
CLIPPING_BATTERY_SOC_HIGH_PERCENT = 95.0
# Absolute battery power threshold considered near-zero battery absorb/discharge.
CLIPPING_BATTERY_POWER_ABS_LOW_KW = 0.2

# Calibration controls.
# Forecast calibration bounds and step controls.
CALIBRATION_WINDOW_DAYS = 7
CALIBRATION_DEFAULT_POWER_MULTIPLIER = 1.0
CALIBRATION_DEFAULT_EXPORT_LEAD_BUFFER_MULTIPLIER = 1.1
CALIBRATION_RATIO_MIN = 0.5
CALIBRATION_RATIO_MAX = 2.0
CALIBRATION_TARGET_MULTIPLIER_MIN = 0.70
CALIBRATION_TARGET_MULTIPLIER_MAX = 1.75
CALIBRATION_MIN_SOLAR_KW = 0.1  # exclude near-zero readings from ratio calculation
CALIBRATION_TARGET_LEAD_BUFFER_MAX = 1.6
CALIBRATION_MULTIPLIER_STEP_MAX = 0.08
CALIBRATION_CLIPPING_RATE_WEIGHT = 0.25
CALIBRATION_TARGET_MULTIPLIER_EXCESS_WEIGHT = 0.15

# Forecast analysis windows.
# Forecast analysis script period windows (local hour ranges, end-exclusive).
FORECAST_ANALYSIS_MORNING_START_HOUR = 7
FORECAST_ANALYSIS_MORNING_END_HOUR = 12
FORECAST_ANALYSIS_AFTERNOON_START_HOUR = 12
FORECAST_ANALYSIS_AFTERNOON_END_HOUR = 16
FORECAST_ANALYSIS_EVENING_START_HOUR = 16
FORECAST_ANALYSIS_EVENING_END_HOUR = 20

# Forecast analysis classification thresholds.
# Forecast analysis classification thresholds.
FORECAST_ANALYSIS_WAY_OVER_FORECAST_MAX_RATIO = 0.5
FORECAST_ANALYSIS_OVER_FORECAST_MAX_RATIO = 0.8
FORECAST_ANALYSIS_ON_TARGET_MAX_RATIO = 1.25
FORECAST_ANALYSIS_UNDER_FORECAST_MAX_RATIO = 2.0
FORECAST_ANALYSIS_SOC_FULL_THRESHOLD_PERCENT = 99.5
FORECAST_ANALYSIS_INVERTER_RED_UTILIZATION_MAX = 0.30
FORECAST_ANALYSIS_INVERTER_AMBER_UTILIZATION_MAX = 0.60
FORECAST_ANALYSIS_CLIPPING_PROMOTE_MIN_RATE = 0.2
FORECAST_ANALYSIS_CLIPPING_PROMOTE_MIN_UTILIZATION = 0.55
# ==============================
# Schedule Windows
# ==============================
# Hour boundaries (local time) used for schedule period detection in schedule_utils.py.
MORNING_START_HOUR = 8
MORNING_END_HOUR = 17
PEAK_START_HOUR = 17
PEAK_END_HOUR = 19
EVENING_START_HOUR = 19
EVENING_END_HOUR = 23
# Cheap-rate window start hour in local time.
CHEAP_RATE_START_HOUR = 23
# Cheap-rate window end hour in local time.
CHEAP_RATE_END_HOUR = 8
# ==============================
# Decision Thresholds
# ==============================
# Target free battery headroom before a Green period: 50% of total battery capacity.
HEADROOM_TARGET_KWH = BATTERY_KWH * 0.5  # e.g. 24 × 0.5 = 12.0 kWh
# Alternative physics-based formula: (SOLAR_PV_KW - INVERTER_KW) * 3 = surplus kW × 3 h reserve.
# For this system that gives 10.2 kWh. BATTERY_KWH * 0.5 adds extra margin; adjust to taste.
# Target free battery headroom before an Amber period: 25% of total battery capacity.
# Amber days carry moderate solar risk — less than Green but enough to warrant partial headroom.
# Set to 0.0 to disable Amber headroom entirely (reverts to pre-2026-05 behaviour).
AMBER_HEADROOM_FRACTION = 0.25
AMBER_HEADROOM_TARGET_KWH = BATTERY_KWH * AMBER_HEADROOM_FRACTION  # e.g. 24 × 0.25 = 6.0 kWh
# Enable bridge-to-cheap-rate rule: before cheap-rate starts, prefer self-powered
# if current battery energy is sufficient to cover expected household demand.
ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE = True
# Estimated average household load (kW) used to calculate battery energy needed
# to reach cheap-rate start time.
ESTIMATED_HOME_LOAD_KW = 0.8
# Safety reserve kept in battery when evaluating bridge-to-cheap-rate sufficiency.
BRIDGE_BATTERY_RESERVE_KWH = 1.0
# Pre-cheap-rate night export: discharge battery toward a SOC floor before the cheap-rate
# window opens, so the battery has room to charge on cheap-rate electricity.
ENABLE_PRE_CHEAP_RATE_NIGHT_EXPORT = True
# SOC floor below which no further pre-cheap-rate export is triggered.
PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT = 15.0
# Conservative assumed net battery discharge power used to size the export window.
PRE_CHEAP_RATE_NIGHT_EXPORT_ASSUMED_DISCHARGE_KW = round(INVERTER_KW * 0.36, 2)  # ~36% of inverter capacity
# High-SOC protection: force GRID_EXPORT for Green or Amber periods when battery is very
# full and headroom is below the status-appropriate target, preventing wasted solar.
# Green uses HEADROOM_TARGET_KWH (50%); Amber uses AMBER_HEADROOM_TARGET_KWH (25%).
MORNING_HIGH_SOC_PROTECTION_ENABLED = True
# SOC threshold for the mid-period high-SOC safety export check (combined with solar).
# When SOC >= this AND solar >= MID_PERIOD_SAFETY_SOLAR_TRIGGER_KW, export is triggered.
# 55% gives a 10% gap above the 45% SOC floor (DAYTIME_TIMED_EXPORT_MIN_SOC_PERCENT),
# ensuring each export creates meaningful headroom before stopping.
MORNING_HIGH_SOC_THRESHOLD_PERCENT = 55.0
# Solar generation threshold (kW) for mid-period high-SOC safety export.
# Only triggers when live solar is this strong and SOC >= MORNING_HIGH_SOC_THRESHOLD_PERCENT.
MID_PERIOD_SAFETY_SOLAR_TRIGGER_KW = round(SOLAR_PV_KW * 0.39, 2)  # ~39% of array capacity
# Live clipping-risk promotion: promote Amber to Green during a scheduler tick when
# live solar generation and battery SOC both exceed configured thresholds, allowing
# the headroom export rule to trigger early even on an underforecast day.
# Comma-separated period codes where live clipping-risk promotion is allowed.
# Codes: M=Morning, A=Afternoon, E=Evening. Example: "M,A" enables morning and afternoon.
LIVE_CLIPPING_RISK_VALID_PERIODS = "M,A"
# SOC threshold for live clipping-risk Amber→Green promotion.
# Lowered to trigger protective export before the battery is almost full.
LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT = 50.0
# Rolling live-solar kW threshold for live clipping-risk promotion.
# Raised to require a sustained high-irradiance run before promoting Amber→Green.
# 4.5 kW ≈ 51% of array capacity; avoids triggering on brief cloud-break spikes on Amber days.
LIVE_CLIPPING_RISK_SOLAR_TRIGGER_KW = 4.5
# SOC floor for mid-period clipping export: if timed export started by clipping-risk
# promotion drops SOC to this floor, cancel the export and restore prior mode.
# Set to 5% below the promotion threshold to avoid yo-yo behavior.
LIVE_CLIPPING_EXPORT_SOC_FLOOR_PERCENT = 45.0
# SOC floor for daytime headroom timed exports (pre-period / period-start) where
# the scheduler proactively exports to create battery room for forecasted solar.
DAYTIME_TIMED_EXPORT_MIN_SOC_PERCENT = 45.0
# Controlled evening export settings.
# Enables bounded battery export in evening to create headroom, while preserving
# enough energy to avoid avoidable grid import before cheap-rate charging.
ENABLE_EVENING_CONTROLLED_EXPORT = True
# Minimum SOC floor that controlled evening export must preserve.
EVENING_EXPORT_MIN_SOC_PERCENT = 45.0
# SOC threshold to consider starting controlled evening export.
EVENING_EXPORT_TRIGGER_SOC_PERCENT = 75.0
# Additional surplus above protected energy required before exporting.
EVENING_EXPORT_MIN_EXCESS_KWH = 1.0
# Conservative assumed net battery discharge power used to size export window.
EVENING_EXPORT_ASSUMED_DISCHARGE_KW = round(INVERTER_KW * 0.36, 2)  # ~36% of inverter capacity
# Safety cap for one evening controlled export window.
EVENING_EXPORT_MAX_DURATION_MINUTES = 120

# ==============================
# Sigen Modes
# ==============================
# Sigen operational mode values (from check_modes.py)
SIGEN_MODES = {
    # Let Sigen AI optimize for savings and self-consumption
    "AI": 1,  # Sigen AI Mode
    # Maximize use of solar and battery, minimize grid import
    "SELF_POWERED": 0,  # Maximum Self-Powered
    # Use time-of-use tariff schedule for charging/discharging
    "TOU": 2,  # TOU (Time-of-Use)
    # Export all generated energy to the grid
    "GRID_EXPORT": 5,  # Fully Fed to Grid
    # Allow remote/advanced automation control
    "REMOTE_EMS": 7,  # Remote EMS Mode
    # User-defined custom operation logic
    "CUSTOM": 9,  # Custom Operation Mode
}

SIGEN_MODE_NAMES: dict[int, str] = {v: k for k, v in SIGEN_MODES.items()}

# Mapping of API label responses to numeric mode values.
# This is used when get_operational_mode returns a text label rather than an integer.
# Keys are matched case-insensitively after basic normalization.
SIGEN_MODE_LABEL_TO_VALUE = {
    "Sigen AI Mode": SIGEN_MODES["AI"],
    "Signe AI Mode": SIGEN_MODES["AI"],
    "AI Mode": SIGEN_MODES["AI"],
    "Maximum Self-Powered": SIGEN_MODES["SELF_POWERED"],
    "Self-Powered": SIGEN_MODES["SELF_POWERED"],
    "TOU": SIGEN_MODES["TOU"],
    "Fully Fed to Grid": SIGEN_MODES["GRID_EXPORT"],
    "Grid Export": SIGEN_MODES["GRID_EXPORT"],
    "Remote EMS Mode": SIGEN_MODES["REMOTE_EMS"],
    "Custom Operation Mode": SIGEN_MODES["CUSTOM"],
}

# ==============================
# Forecast-to-Mode Mapping
# ==============================
# Map forecast status (Red/Amber/Green) to Sigen operational mode
# Adjust these mappings to change automation behavior
FORECAST_TO_MODE = {
    # Green: Good solar forecast, maximize self-consumption
    ForecastStatus.GREEN: SIGEN_MODES["SELF_POWERED"],
    # Amber: Moderate solar, stay in deterministic self-consumption mode
    ForecastStatus.AMBER: SIGEN_MODES["SELF_POWERED"],
    # Red: Poor solar, stay in deterministic self-consumption mode
    ForecastStatus.RED: SIGEN_MODES["SELF_POWERED"],
}

# ==============================
# Period-to-Mode Mapping
# ==============================
# Map schedule period (NIGHT/DAY/PEAK) to Sigen operational mode.
PERIOD_TO_MODE = {
    # Night: cheap-rate window behavior (charge-oriented when TOU charge windows are configured)
    Period.NIGHT: SIGEN_MODES["TOU"],
    # Day: deterministic self-consumption behavior
    "DAY": SIGEN_MODES["SELF_POWERED"],
    # Peak: high-demand hours, maximize self-consumption
    "PEAK": SIGEN_MODES["SELF_POWERED"],
}

# You can import these mappings in your main control logic:
# from config.settings import SIGEN_MODES, FORECAST_TO_MODE, PERIOD_TO_MODE

# ==============================
# SwitchBot Immersion Heater
# ==============================
# Master enable for the immersion heater boost feature.
# Requires SWITCHBOT_TOKEN, SWITCHBOT_SECRET, and SWITCHBOT_IMMERSION_DEVICE_ID in .env.
SWITCHBOT_IMMERSION_ENABLED = False

# Battery SOC must be at or above this percentage before a boost is triggered.
# Set high enough that the boost doesn't leave the battery short for evening self-consumption.
SWITCHBOT_IMMERSION_MIN_SOC_PERCENT = 80.0

# Rolling average live solar must be at or above this kW to trigger a boost.
# Ensures the panels are genuinely generating surplus before heating water.
SWITCHBOT_IMMERSION_SOLAR_TRIGGER_KW = 3.0

# Maximum number of boost cycles per calendar day. One is usually enough.
SWITCHBOT_IMMERSION_MAX_BOOSTS_PER_DAY = 1

# Periods during which a boost may be triggered. Avoids late-day triggers that
# would heat water without enough cheap-rate overnight recharge to benefit from.
SWITCHBOT_IMMERSION_VALID_PERIODS: set[str] = {"Morn", "Aftn"}
# HTTP timeout for SwitchBot Cloud API requests.
SWITCHBOT_API_TIMEOUT_SECONDS = 10

# ==============================
# Data File Paths
# ==============================
# Paths are relative to the project root directory.
# Telemetry archive: one JSONL record per scheduler poll cycle.
TELEMETRY_LOG_PATH = "data/inverter_telemetry.jsonl"
# Calibration artifact: learned per-period multipliers used to adjust raw forecasts.
CALIBRATION_LOG_PATH = "data/forecast_calibration.json"
# Forecast comparison log: historical forecast-vs-actual entries written by calibration.
FORECAST_COMPARISONS_LOG_PATH = "data/forecast_comparisons.jsonl"

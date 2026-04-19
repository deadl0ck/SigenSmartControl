"""
config.py

Configuration file for Sigen inverter operational mode mappings and forecast-to-mode associations.

Edit this file to adjust how forecast statuses and tariff periods map to Sigen operational modes.
"""

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
MAX_PRE_PERIOD_WINDOW_MINUTES = 180
# Number of live-solar samples used in rolling average calculations.
LIVE_SOLAR_AVERAGE_SAMPLE_COUNT = 3
# Lower bound for effective battery export capacity in pre-period calculations.
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
PRE_SUNRISE_DISCHARGE_LEAD_MINUTES = 120
# Minimum SOC required before pre-sunrise discharge is allowed to switch away
# from night charging behavior. Below this threshold, the scheduler keeps the
# configured night mode instead of discharging into the morning.
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
# HTTP timeout for sunrise/sunset API requests.
SUNRISE_SUNSET_API_TIMEOUT_SECONDS = 10

# Quartz normalization thresholds.
# Quartz period status normalization thresholds as fractions of configured array capacity.
# Red: output_fraction < QUARTZ_RED_CAPACITY_FRACTION
# Amber: QUARTZ_RED_CAPACITY_FRACTION <= output_fraction < QUARTZ_GREEN_CAPACITY_FRACTION
# Green: output_fraction >= QUARTZ_GREEN_CAPACITY_FRACTION
QUARTZ_RED_CAPACITY_FRACTION = 0.20
QUARTZ_GREEN_CAPACITY_FRACTION = 0.40

# Telemetry clipping heuristics.
# Telemetry clipping heuristics.
# Primary clipping trigger when solar power is within this margin of inverter ceiling.
CLIPPING_PRIMARY_NEAR_CEILING_MARGIN_KW = 0.1
# Secondary clipping trigger margin requiring corroborating signals.
CLIPPING_SECONDARY_NEAR_CEILING_MARGIN_KW = 0.3
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
CALIBRATION_TARGET_MULTIPLIER_MIN = 0.85
CALIBRATION_TARGET_MULTIPLIER_MAX = 1.5
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
# Surplus solar capacity that cannot be stored when the inverter is at its AC ceiling.
# This is the energy that would be clipped if the battery were already full.
SURPLUS_CAPACITY_KW = SOLAR_PV_KW - INVERTER_KW  # e.g. 8.9 - 5.5 = 3.4 kW
# Target free battery headroom before a Green period, sized to absorb the full
# surplus output across one 3-hour period.
HEADROOM_TARGET_KWH = SURPLUS_CAPACITY_KW * 3.0  # e.g. 3.4 × 3 = 10.2 kWh
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
PRE_CHEAP_RATE_NIGHT_EXPORT_ASSUMED_DISCHARGE_KW = 2.0
# High-SOC protection: force GRID_EXPORT for Amber/Green periods when battery is very
# full and headroom is below target, preventing wasted solar due to a full battery.
MORNING_HIGH_SOC_PROTECTION_ENABLED = True
# SOC threshold for the mid-period high-SOC safety export check (combined with solar).
# When SOC >= this AND solar >= MID_PERIOD_SAFETY_SOLAR_TRIGGER_KW, export is triggered.
MORNING_HIGH_SOC_THRESHOLD_PERCENT = 50.0
# Solar generation threshold (kW) for mid-period high-SOC safety export.
# Only triggers when live solar is this strong and SOC >= MORNING_HIGH_SOC_THRESHOLD_PERCENT.
MID_PERIOD_SAFETY_SOLAR_TRIGGER_KW = 3.5
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
# Lowered so underforecast high-irradiance ramps are caught earlier.
LIVE_CLIPPING_RISK_SOLAR_TRIGGER_KW = 3.2
# SOC floor for mid-period clipping export: if timed export started by clipping-risk
# promotion drops SOC to this floor, cancel the export and restore prior mode.
# Set to 5% below the promotion threshold to avoid yo-yo behavior.
LIVE_CLIPPING_EXPORT_SOC_FLOOR_PERCENT = 45.0
# SOC floor for daytime headroom timed exports (pre-period / period-start) where
# the scheduler proactively exports to create battery room for forecasted solar.
DAYTIME_TIMED_EXPORT_MIN_SOC_PERCENT = 50.0
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
EVENING_EXPORT_ASSUMED_DISCHARGE_KW = 2.0
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
    "GREEN": SIGEN_MODES["SELF_POWERED"],
    # Amber: Moderate solar, stay in deterministic self-consumption mode
    "AMBER": SIGEN_MODES["SELF_POWERED"],
    # Red: Poor solar, stay in deterministic self-consumption mode
    "RED": SIGEN_MODES["SELF_POWERED"],
}

# ==============================
# Period-to-Mode Mapping
# ==============================
# Map schedule period (NIGHT/DAY/PEAK) to Sigen operational mode.
PERIOD_TO_MODE = {
    # Night: cheap-rate window behavior (charge-oriented when TOU charge windows are configured)
    "NIGHT": SIGEN_MODES["TOU"],
    # Day: deterministic self-consumption behavior
    "DAY": SIGEN_MODES["SELF_POWERED"],
    # Peak: high-demand hours, maximize self-consumption
    "PEAK": SIGEN_MODES["SELF_POWERED"],
}

# You can import these mappings in your main control logic:
# from config.settings import SIGEN_MODES, FORECAST_TO_MODE, PERIOD_TO_MODE

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

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
# How often the self-contained scheduler wakes up to re-check forecast windows.
POLL_INTERVAL_MINUTES = 5
# How far ahead of a period start we begin monitoring SOC for a possible export.
MAX_PRE_PERIOD_WINDOW_MINUTES = 120
# Number of live-solar samples used in rolling average calculations.
LIVE_SOLAR_AVERAGE_SAMPLE_COUNT = 3
# Lower bound for effective battery export capacity in pre-period calculations.
MIN_EFFECTIVE_BATTERY_EXPORT_KW = 0.2
# Default fallback SOC used in full simulation mode when env var parsing fails.
DEFAULT_SIMULATED_SOC_PERCENT = 80.0
# Full simulation mode: reads data and logs intended actions but never sends
# inverter mode-change commands.
FULL_SIMULATION_MODE = True
# Maximum allowed duration for timed grid export override (minutes) — prevents accidental
# over-discharge or excessive grid arbitrage cycles.
MAX_TIMED_EXPORT_MINUTES = 240
# Whether the scheduler should explicitly apply the configured night mode.
NIGHT_MODE_ENABLED = True
# Whether scheduler should sleep through inactive night periods instead of polling every tick.
NIGHT_SLEEP_MODE_ENABLED = True
# Local timezone used for schedule windows.
LOCAL_TIMEZONE = "Europe/Dublin"

# HTTP timeout for ESB county forecast API requests.
ESB_API_TIMEOUT_SECONDS = 30
# HTTP timeout for Quartz forecast API requests.
QUARTZ_API_TIMEOUT_SECONDS = 30
# HTTP timeout for sunrise/sunset API requests.
SUNRISE_SUNSET_API_TIMEOUT_SECONDS = 10

# Quartz period status normalization thresholds as fractions of configured array capacity.
# Red: output_fraction < QUARTZ_RED_CAPACITY_FRACTION
# Amber: QUARTZ_RED_CAPACITY_FRACTION <= output_fraction < QUARTZ_GREEN_CAPACITY_FRACTION
# Green: output_fraction >= QUARTZ_GREEN_CAPACITY_FRACTION
QUARTZ_RED_CAPACITY_FRACTION = 0.20
QUARTZ_GREEN_CAPACITY_FRACTION = 0.40

# Telemetry clipping heuristics.
# Primary clipping trigger when solar power is within this margin of inverter ceiling.
CLIPPING_PRIMARY_NEAR_CEILING_MARGIN_KW = 0.1
# Secondary clipping trigger margin requiring corroborating signals.
CLIPPING_SECONDARY_NEAR_CEILING_MARGIN_KW = 0.3
# Battery SOC threshold considered "high" for clipping confidence.
CLIPPING_BATTERY_SOC_HIGH_PERCENT = 95.0
# Absolute battery power threshold considered near-zero battery absorb/discharge.
CLIPPING_BATTERY_POWER_ABS_LOW_KW = 0.2

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

# Forecast analysis script period windows (local hour ranges, end-exclusive).
FORECAST_ANALYSIS_MORNING_START_HOUR = 7
FORECAST_ANALYSIS_MORNING_END_HOUR = 12
FORECAST_ANALYSIS_AFTERNOON_START_HOUR = 12
FORECAST_ANALYSIS_AFTERNOON_END_HOUR = 16
FORECAST_ANALYSIS_EVENING_START_HOUR = 16
FORECAST_ANALYSIS_EVENING_END_HOUR = 20

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
# Morning clipping protection: allow export for Amber/Green mornings when battery is very full.
MORNING_HIGH_SOC_PROTECTION_ENABLED = True
MORNING_HIGH_SOC_THRESHOLD_PERCENT = 95.0
# Enable AI Mode transition for evening periods approaching cheap-rate window.
# When enabled, the Evening period will switch to AI Mode (with profit-max configured
# in mySigen app) to allow automatic battery arbitrage: discharge at day/peak rates,
# then recharge at cheap night rates.
ENABLE_EVENING_AI_MODE_TRANSITION = True
# Hour (local time) after which Evening period should use AI Mode for profit-max optimization.
# E.g., if set to 17, Evening mode will switch to AI after 17:00 (5 PM).
EVENING_AI_MODE_START_HOUR = 17

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

# ==============================
# Forecast-to-Mode Mapping
# ==============================
# Map forecast status (Red/Amber/Green) to Sigen operational mode
# Adjust these mappings to change automation behavior
FORECAST_TO_MODE = {
    # Green: Good solar forecast, maximize self-consumption
    "GREEN": SIGEN_MODES["SELF_POWERED"],
    # Amber: Moderate solar, let AI optimize
    "AMBER": SIGEN_MODES["AI"],
    # Red: Poor solar, let AI handle low-generation periods
    "RED": SIGEN_MODES["AI"],
}

# ==============================
# Period-to-Mode Mapping
# ==============================
# Map schedule period (NIGHT/DAY/PEAK) to Sigen operational mode.
PERIOD_TO_MODE = {
    # Night: cheap-rate window behavior (set to AI since no TOU profiles are defined)
    "NIGHT": SIGEN_MODES["AI"],
    # Day: normal solar hours, let AI or self-powered logic decide
    "DAY": SIGEN_MODES["AI"],
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

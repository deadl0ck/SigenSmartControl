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
POLL_INTERVAL_MINUTES = 15
# How far ahead of a period start we begin monitoring SOC for a possible export.
MAX_PRE_PERIOD_WINDOW_MINUTES = 120
# Full simulation mode: reads data and logs intended actions but never sends
# inverter mode-change commands.
FULL_SIMULATION_MODE = True
# Whether the scheduler should explicitly apply the configured night mode.
NIGHT_MODE_ENABLED = True
# Whether the scheduler should perform a night-before pre-check for the next morning.
NEXT_DAY_PRECHECK_ENABLED = True
# How long after the night window starts before running the next-day pre-check.
NIGHT_PRECHECK_DELAY_MINUTES = 30
# Local timezone used for tariff windows.
LOCAL_TIMEZONE = "Europe/Dublin"
# ==============================
# Tariff Configuration
# ==============================
# Tariff rates in cents per kWh.
DAY_RATE_CENTS_PER_KWH = 26.596
PEAK_RATE_CENTS_PER_KWH = 32.591
NIGHT_RATE_CENTS_PER_KWH = 13.462
SELL_RATE_CENTS_PER_KWH = 18.5
# Tariff time windows in local time.
DAY_RATE_MORNING_START_HOUR = 8
DAY_RATE_MORNING_END_HOUR = 17
PEAK_RATE_START_HOUR = 17
PEAK_RATE_END_HOUR = 19
DAY_RATE_EVENING_START_HOUR = 19
DAY_RATE_EVENING_END_HOUR = 23
# Cheap-rate tariff window start hour in local time.
CHEAP_RATE_START_HOUR = 23
# Cheap-rate tariff window end hour in local time.
CHEAP_RATE_END_HOUR = 8
# ==============================
# Decision Thresholds
# ==============================
# Fraction of expected solar energy to keep free in the battery as headroom.
HEADROOM_FRAC = 0.25
# If SOC is already above this threshold during a Green forecast, export to grid.
SOC_HIGH_THRESHOLD = 95
# Enable bridge-to-cheap-rate rule: before cheap-rate starts, prefer self-powered
# if current battery energy is sufficient to cover expected household demand.
ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE = True
# Estimated average household load (kW) used to calculate battery energy needed
# to reach cheap-rate start time.
ESTIMATED_HOME_LOAD_KW = 0.8
# Safety reserve kept in battery when evaluating bridge-to-cheap-rate sufficiency.
BRIDGE_BATTERY_RESERVE_KWH = 1.0
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
    # Red: Poor solar, use TOU to optimize for tariffs
    "RED": SIGEN_MODES["TOU"],
}

# ==============================
# Tariff-to-Mode Mapping
# ==============================
# Example: Map tariff period to Sigen mode (optional, for advanced logic)
TARIFF_TO_MODE = {
    # Night: Cheap grid, charge battery if needed
    "NIGHT": SIGEN_MODES["TOU"],
    # Day: Normal operation, let AI or self-powered logic decide
    "DAY": SIGEN_MODES["AI"],
    # Peak: Expensive grid, maximize self-consumption
    "PEAK": SIGEN_MODES["SELF_POWERED"],
}

# Mode used before cheap-rate starts overnight.
# This prevents the system from moving into charge-oriented TOU mode too early.
PRE_CHEAP_RATE_MODE = SIGEN_MODES["AI"]

# You can import these mappings in your main control logic:
# from config.settings import SIGEN_MODES, FORECAST_TO_MODE, TARIFF_TO_MODE

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

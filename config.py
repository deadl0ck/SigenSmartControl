# --- System Specifications (edit to match your hardware) ---
# Solar PV array size in kW (DC rating)
SOLAR_PV_KW = 8.9
# Inverter maximum output in kW (AC rating)
INVERTER_KW = 5.5
# Battery usable capacity in kWh
BATTERY_KWH = 24

"""
config.py

Configuration file for Sigen inverter operational mode mappings and forecast-to-mode associations.

Edit this file to adjust how forecast statuses and tariff periods map to Sigen operational modes.
"""

"""
LOG_LEVEL controls the verbosity of logging throughout the system.
Set to one of: 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'.
"""
LOG_LEVEL = "INFO"  # Change to 'DEBUG' for more detailed logs

# Scheduler runtime settings.
# How often the self-contained scheduler wakes up to re-check forecast windows.
POLL_INTERVAL_MINUTES = 15
# How far ahead of a period start we begin monitoring SOC for a possible export.
MAX_PRE_PERIOD_WINDOW_MINUTES = 120
# Fraction of expected solar energy to keep free in the battery as headroom.
HEADROOM_FRAC = 0.25
# If SOC is already above this threshold during a Green forecast, export to grid.
SOC_HIGH_THRESHOLD = 95

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

# Example: Map tariff period to Sigen mode (optional, for advanced logic)
TARIFF_TO_MODE = {
    # Night: Cheap grid, charge battery if needed
    "NIGHT": SIGEN_MODES["TOU"],
    # Day: Normal operation, let AI or self-powered logic decide
    "DAY": SIGEN_MODES["AI"],
    # Peak: Expensive grid, maximize self-consumption
    "PEAK": SIGEN_MODES["SELF_POWERED"],
}

# You can import these mappings in your main control logic:
# from config import SIGEN_MODES, FORECAST_TO_MODE, TARIFF_TO_MODE

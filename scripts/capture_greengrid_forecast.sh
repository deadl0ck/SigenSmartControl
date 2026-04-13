#!/bin/bash

# GREEN-GRID Forecast Capture Script
# Queries the GREEN-GRID Shiny app and saves the forecast to data/greengrid_forecasts.jsonl
#
# Usage:
#   ./scripts/capture_greengrid_forecast.sh
#   or
#   bash scripts/capture_greengrid_forecast.sh
#
# IMPORTANT: Set these parameters before running, or set them as environment variables.
# Do NOT commit actual location data to the repository.

# Your Irish Eircode (MUST be set - replace placeholder)
export GREENGRID_EIRCODE="${GREENGRID_EIRCODE:-YOUR_EIRCODE_HERE}"

# Panel direction (N/S/E/W/NE/SE/SW/NW)
export GREENGRID_DIRECTION="${GREENGRID_DIRECTION:-SE}"

# Roof pitch in degrees (15-80)
export GREENGRID_ROOF_PITCH_DEGREES="${GREENGRID_ROOF_PITCH_DEGREES:-27}"

# Number of solar panels (4-300)
export GREENGRID_NUM_PANELS="${GREENGRID_NUM_PANELS:-20}"

# Activate virtual environment
source .venv/bin/activate

# Validate that EIRCODE was set
if [[ "$GREENGRID_EIRCODE" == "YOUR_EIRCODE_HERE" ]]; then
    echo "ERROR: GREENGRID_EIRCODE not set. Please set it before running:"
    echo "  export GREENGRID_EIRCODE='your_eircode_here'"
    echo "  ./scripts/capture_greengrid_forecast.sh"
    exit 1
fi

# Run the capture script
python scripts/capture_greengrid_forecast.py

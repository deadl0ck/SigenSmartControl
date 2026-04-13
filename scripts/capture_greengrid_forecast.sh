#!/bin/bash

# GREEN-GRID Forecast Capture Script
# Queries the GREEN-GRID Shiny app and saves the forecast to data/greengrid_forecasts.jsonl
#
# Usage:
#   ./scripts/capture_greengrid_forecast.sh
#   or
#   bash scripts/capture_greengrid_forecast.sh
#
# Edit the parameters below to match your solar installation:

# Your Irish Eircode
export GREENGRID_EIRCODE="N91 F752"

# Panel direction (N/S/E/W/NE/SE/SW/NW)
export GREENGRID_DIRECTION="SE"

# Roof pitch in degrees (15-80)
export GREENGRID_ROOF_PITCH_DEGREES="27"

# Number of solar panels (4-300)
export GREENGRID_NUM_PANELS="20"

# Activate virtual environment
source .venv/bin/activate

# Run the capture script
python scripts/capture_greengrid_forecast.py

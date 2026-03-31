# Sigen Inverter Smart Control System

## Table of Contents
1. [Overview](#overview)
2. [Features](#features)
3. [Folder Structure](#folder-structure)
4. [Setup Instructions](#setup-instructions)
5. [Usage](#usage)
6. [How It Works](#how-it-works)
7. [Customizing Control Logic](#customizing-control-logic)
8. [Troubleshooting](#troubleshooting)

---

## Overview
This project provides a smart, locally forecast-driven control system for Sigen inverters. It integrates:
- Met Éireann solar forecasts
- Your electricity tariff schedule
- Sigen inverter operational modes
- Automated mode switching for maximum savings and solar self-consumption

## Features
- Reads and parses Met Éireann solar forecasts for your county
- Maps forecast periods (Morning, Afternoon, Evening) to inverter control actions
- Integrates your day/night/peak tariff schedule
- Selects and sets the best Sigen operational mode automatically
- Modular authentication and logging
- Safe mode-checking utility

## Folder Structure
```
/requirements.txt         # Python dependencies
/weather.py              # Solar forecast logic and planning
/sigen_auth.py           # Singleton Sigen authentication
/check_modes.py          # Utility to print available inverter modes
/main.py                 # (To be extended) Main control loop
/constants.py            # Project-wide constants
/.env                    # Local credentials (not committed)
```

## Setup Instructions
1. **Clone the repository** and open in VS Code.
2. **Install Python 3.10+** (recommended: 3.11 or 3.12).
3. **Create a virtual environment:**
   ```sh
   python3 -m venv .venv
   source .venv/bin/activate
   ```
4. **Install dependencies:**
   ```sh
   pip install -r requirements.txt
   ```
5. **Create a `.env` file** in the project root:
   ```ini
   SIGEN_USERNAME=your_sigen_email
   SIGEN_PASSWORD=your_sigen_password
   ```
6. **Edit `constants.py`** to set your county if needed.

7. **Edit `config.py`** to set your system specifications:
  - `SOLAR_PV_KW`: Your total solar PV array size in kW (e.g., 8.9)
  - `INVERTER_KW`: Your inverter's maximum output in kW (e.g., 5.5)
  - `BATTERY_KWH`: Your battery's usable capacity in kWh (e.g., 24)

  Example:
  ```python
  SOLAR_PV_KW = 8.9
  INVERTER_KW = 5.5
  BATTERY_KWH = 24
  ```

  These values are used by the control logic to optimize charging, discharging, and export decisions for your specific hardware.

## Usage
  ```sh
  python check_modes.py
  ```
  ```sh
  python weather.py
  ```
  ```sh
  python main.py
  ```
## Usage

- **Check available inverter modes:**
  ```sh
  python check_modes.py
  ```
- **Run the solar forecast and planning logic:**
  ```sh
  python weather.py
  ```
- **Run the main control loop:**
  ```sh
  python main.py
  ```

## Control Loop & Logging

The main control loop (`main.py`) fetches today's solar forecast, determines the best Sigen operational mode for each period (Morning, Afternoon, Evening), and sets the inverter mode accordingly. All actions are logged.

- **Logging Level:**
  - Configurable in `config.py` via the `LOG_LEVEL` variable.
  - Set to `'DEBUG'` for detailed logs, `'INFO'` for normal operation, or `'WARNING'`/`'ERROR'` for less output.
  - Example:
    ```python
    LOG_LEVEL = "DEBUG"
    ```

## Test Cases: Forecast-to-Mode Mapping

Below are test scenarios for each possible forecast combination. For each period (Morn, Aftn, Eve), the forecast can be Green, Amber, or Red. The expected operational mode is determined by the mapping in `config.py`.

| Period  | Forecast | Expected Mode           | Mode Value | Description                                      |
|---------|----------|------------------------|------------|--------------------------------------------------|
| Morn    | Green    | SELF_POWERED           | 0          | Maximize self-consumption                        |
| Morn    | Amber    | AI                     | 1          | Let Sigen AI optimize                            |
| Morn    | Red      | TOU                    | 2          | Use TOU/tariff-based mode                        |
| Aftn    | Green    | SELF_POWERED           | 0          | Maximize self-consumption                        |
| Aftn    | Amber    | AI                     | 1          | Let Sigen AI optimize                            |
| Aftn    | Red      | TOU                    | 2          | Use TOU/tariff-based mode                        |
| Eve     | Green    | SELF_POWERED           | 0          | Maximize self-consumption                        |
| Eve     | Amber    | AI                     | 1          | Let Sigen AI optimize                            |
| Eve     | Red      | TOU                    | 2          | Use TOU/tariff-based mode                        |

**How to test:**
- You can simulate different forecasts by modifying the mapping in `config.py` or by mocking the forecast in `weather.py`.
- Run `python main.py` and observe the logs to verify the correct mode is selected and set for each period.


**Example log output:**
```
2026-03-31 20:00:00,000 - sigen_control - INFO - Period: Morn, Solar Value: 450, Status: Green
2026-03-31 20:00:00,001 - sigen_control - INFO - Selected mode for Morn: SELF_POWERED (value=0)
2026-03-31 20:00:00,002 - sigen_control - INFO - Set mode response for Morn: {...}
```



## Sunrise/Sunset Integration

The system can fetch sunrise and sunset times for your location using the [sunrise-sunset.org](https://sunrise-sunset.org/api) API. This allows you to dynamically determine when 'Morning', 'Afternoon', and 'Evening' start/end, rather than using fixed periods.

- The API endpoint is stored in `constants.py` as `SUNRISE_SUNSET_API_URL`.
- The logic is implemented in `sunrise_sunset.py`.
- Example usage:
  ```python
  from sunrise_sunset import get_sunrise_sunset
  sunrise, sunset = get_sunrise_sunset(lat=53.5, lng=-7.3)  # Westmeath, Ireland
  print(f"Sunrise: {sunrise}, Sunset: {sunset}")
  ```
- You can use these times to adjust your control logic for solar periods.


This project is developed and tested with **Python 3.14** (see output below for your environment). It is strongly recommended to use a virtual environment for all development and testing.

### Setting up your environment

1. **Create and activate a virtual environment:**
  ```sh
  python3 -m venv .venv
  source .venv/bin/activate
  ```
2. **Install dependencies:**
  ```sh
  pip install -r requirements.txt
  # If you want to run tests, also install pytest and pytest-asyncio:
  pip install pytest pytest-asyncio
  ```
  Or, to install everything at once:
  ```sh
  pip install -r requirements.txt pytest pytest-asyncio
  ```
3. **Check your Python version:**
  ```sh
  python --version
  # Should print Python 3.14.x (or your compatible version)
  ```

### Troubleshooting
- If you see missing package errors (e.g., `No module named 'openpyxl'`), ensure you are using the correct virtual environment and have installed all dependencies.
- If you want to use a different Python version, ensure all dependencies are compatible.

## Automated Test Suite


Unit tests are provided for all core logic:
- Forecast parsing and period mapping (`test_weather.py`)
- Config mappings and logging level (`test_config.py`)
- Main control loop logic (`test_main.py`)
- Sunrise/sunset API integration (`test_sunrise_sunset.py`)

All tests use Python logging for output and are written with `pytest`.

### Running the tests

1. Install pytest if not already installed:
  ```sh
  pip install pytest
  ```
2. Run all tests:
  ```sh
  pytest
  ```
3. To see detailed log output, set `LOG_LEVEL = "DEBUG"` in `config.py` before running tests.

### What is covered
- Forecast-to-mode mapping for all combinations
- Config integrity and logging level
- Control loop logic (mode selection and API call simulation)

Tests are safe to run and do not require a real inverter or network connection.

## How It Works
- The system fetches the latest solar forecast for your county.
- It divides the day into Morning, Afternoon, and Evening periods.
- For each period, it determines the solar status (Green/Amber/Red).
- It combines this with your battery SOC and tariff period (Night/Day/Peak).
- It selects the best Sigen operational mode (Self-Consumption, AI, Time-based Control, etc.) and sets it via the API.
- All authentication is handled via a singleton pattern for efficiency.

## Customizing Control Logic
- Edit `weather.py` to adjust how forecast values are mapped to statuses.
- Edit `main.py` to change how SOC, tariff, and forecast are combined for mode selection.
- You can add your own custom Sigen modes if needed.

## Troubleshooting
- If you see auth errors, check your `.env` file and credentials.
- If you see missing modes, run `python check_modes.py` to verify available options.
- For API or network errors, check your internet connection and Sigen cloud status.

---

**For questions or improvements, open an issue or contact the maintainer.**

---

## Test and Development Environment

**Important:** This project uses a Python 3.14 virtual environment located at `.venv`.

- Before running tests or scripts, always activate the virtual environment:

    source .venv/bin/activate

- Then run tests with:

    pytest -v

Or use the provided script:

    ./scripts/test.sh

This ensures all dependencies (including `openpyxl`, `python-dotenv`, etc.) are available to your code and tests.

If you see errors about missing packages, double-check that your virtual environment is activated and all dependencies are installed:

    pip install -r requirements.txt

---

## Interactive Web Simulator

A modern web interface is included for interactive simulation of the Sigen control logic. You can edit inverter, battery, solar, SOC, and forecast values, then simulate what the system would do for each period.

### How to Use the Web Simulator

1. **Install Flask (if not already installed):**
   ```sh
   pip install flask
   ```
2. **Start the web server:**
   ```sh
   python web/app.py
   ```
3. **Open your browser to:**
   [http://localhost:5000](http://localhost:5000)

- The web page is pre-filled with your config values (editable).
- Set solar forecast for each part of the day and SOC.
- Click "Simulate" to see the system's decision for each period in a color-coded table.
- No sensitive data is exposed.

---

## Repository Description
A privacy-first, auditable automation and simulation platform for Sigen inverters, batteries, and solar. Includes robust logging, scenario-specific tests, and a modern web UI for interactive system simulation.

---

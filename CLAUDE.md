# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

Locally-running home battery inverter control system for a Sigen inverter. Monitors solar forecasts, fetches real-time battery state-of-charge, and makes autonomous inverter mode decisions every 5 minutes. Optimizes between self-consumption, grid export, and TOU (time-of-use) charging.

## Commands

```bash
# Setup
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# Run all tests
python -m pytest -q

# Run a single test module
python -m pytest -q tests/test_decision_logic.py

# Coverage
python -m pytest -q --cov=. --cov-report=term-missing

# Start/stop scheduler in background
./start_monitor.sh
./stop_monitor.sh
./restart_monitor.sh
tail -f monitor.log

# Run in foreground (debug)
python main.py
```

## Simulation Mode

Before testing live changes, set `FULL_SIMULATION_MODE = True` in `config/settings.py`. The scheduler runs all logic but sends no mode-change commands to the inverter.

## Architecture

### Main Loop (`main.py`)

Self-contained async scheduler loop running every `POLL_INTERVAL_MINUTES` (5 min). State machine with these branches:

1. **Day Boundary**: Refreshes sunrise/sunset and forecasts once per day
2. **Timed Export Override**: Skips normal decisions if a pre-period or clipping-risk export is already active
3. **Night Window**: Night mode, summer pre-sunrise discharge, or evening export
4. **Daytime Evaluation** (per period: Morning/Afternoon/Evening):
   - **PRE-PERIOD** (120 min before): Checks headroom deficit, triggers pre-export if needed
   - **PERIOD-START** (at period start): Detects live clipping risk, applies final mode decision

### Decision Hierarchy (`logic/decision_logic.py`)

For daytime periods (highest priority first):
1. **Export if headroom insufficient**: Forecast = Green AND headroom < target → `GRID_EXPORT`
2. **High-SOC protection**: (Amber or Green) AND SOC ≥ 50% AND live solar ≥ 3.5 kW → `GRID_EXPORT`
3. **Evening bridge rule**: Evening AND battery can cover load until cheap-rate → `SELF_POWERED`
4. **Forecast mapping**: Apply `FORECAST_TO_MODE` (all map to `SELF_POWERED` by default)
5. **Peak tariff override**: If tariff = Peak → `SELF_POWERED`

### Key Configuration (`config/settings.py`)

All tunable thresholds live here. Key physics-based values:
- `HEADROOM_TARGET_KWH` = `BATTERY_KWH × 0.5` (12 kWh from 24 kWh battery)
- Derived from: (Solar PV kW − Inverter kW) × 3 hours = (8.9 − 5.5) × 3 = 10.2 kWh
- Hard-coded tariff windows: Night 23:00–08:00, Day 08:00–17:00, Peak 17:00–19:00, Evening 19:00–23:00
- Period names: `Morn`, `Aftn`, `Eve`, `NIGHT`

### Forecast Providers (`weather/forecast.py`)

Protocol-based, tried in order:
1. **ESB county API** (primary): Returns categorical Red/Amber/Green
2. **Forecast.Solar** (backup): kW → normalized to Red/Amber/Green via capacity thresholds
3. **Quartz** (fallback): kW → normalized similarly

Normalization: Red < 20% of `SOLAR_PV_KW`, Amber 20–40%, Green ≥ 40%.

### API Layer (`integrations/`)

- `sigen_interaction.py`: All Sigen API calls — centralizes mode get/set, handles simulation mode, one-time auth recovery
- `sigen_auth.py`: Lazy-loaded singleton Sigen client from `.env` credentials
- `sigen_official.py`: Official OpenAPI client implementation (alternative auth with app key/secret)

### Telemetry (`telemetry/`)

- `telemetry_archive.py`: Appends one inverter snapshot per tick to `data/inverter_telemetry.jsonl` and mode-change events to `data/mode_change_events.jsonl`
- `forecast_calibration.py`: Generates per-period learned multipliers in `data/forecast_calibration.json`. Changes are bounded to ±0.08 per day to prevent instability.

## Official API Reference

For any work involving the Sigen API, consult `.github/reference/Sigen API/API Documentation/` first. Treat those files as source of truth for auth flows, endpoints, modes, and command semantics. Also see `.github/reference/Sigen API/User Manual/` for workflow context.

## Log Analysis

- Live logs in `data/` are the current source of truth.
- Historical snapshot logs in `data/imported-local-2026-04-18/` are background context only.
- If they conflict, prioritize live logs and call out the difference explicitly.

## Code Standards

- **File length**: Max 500 lines per module; split earlier at 400+ lines
- **Functions**: 50–100 lines max; break into smaller testable units
- **Type hints**: Required on all parameters and return types
- **Docstrings**: Google-style required on every function and module
- **Imports**: Grouped (stdlib → third-party → local), separated by blank lines, all at top of file
- **Formatting**: 4-space indent, lines under 100 chars, PEP 8

## `.env` File (Required)

```ini
SIGEN_USERNAME=your_sigen_email
SIGEN_PASSWORD=your_sigen_password
SIGEN_LATITUDE=53.3498
SIGEN_LONGITUDE=-6.2603
EMAIL_SENDER=your_sender@gmail.com
EMAIL_RECEIVER=your_receiver@gmail.com
GMAIL_APP_PASSWORD=your_gmail_app_password
```

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

### Main Loop (`main.py` → `logic/scheduler_coordinator.py`)

`main.py` initialises state, wires up callbacks, and delegates the entire loop to
`SchedulerCoordinator` in `logic/scheduler_coordinator.py`. Each tick runs these steps in order:

1. **Auth refresh** (`_handle_auth_refresh`): Re-authenticates if flagged and not yet done today.
2. **Daily forecast/sunrise refresh** (`_handle_forecast_refresh` → `scheduler_operations.refresh_daily_data`): Refreshes sunrise/sunset and period forecasts on day boundary or intra-day interval.
3. **Live solar sampling**: Appends one live solar reading to the rolling average buffer.
4. **Timed export check** (`_check_timed_export_active` → `logic/timed_export.py`): Runs the inactive → active → restored state machine; skips normal decisions if an export window is in force. When a window expires but SOC is still above the headroom floor (solar kept refilling the battery), the window is automatically extended rather than restored — this avoids the stop/cooldown/restart gap.
5. **Period dispatch** (`_process_period_windows`): Calls `handle_morning/afternoon/evening_period` via shared helpers in `logic/period_handler_shared.py` for each daytime period.
6. **Night window** (`handle_night_window` in `logic/night.py`): Night mode, summer pre-sunrise discharge, or pre-cheap-rate export — evaluated before daytime periods and consumes the tick if active.

### Decision Hierarchy (`logic/decision_logic.py`)

For daytime periods (highest priority first):
1. **Mid-period live clipping risk** (checked every tick while period is active): SOC ≥ threshold AND live solar ≥ trigger → promote Amber→Green → start timed `GRID_EXPORT`.
2. **High-SOC safety export** (mid-period): SOC ≥ 50% AND live solar ≥ 3.5 kW → `GRID_EXPORT`.
3. **Export if headroom insufficient** (pre-period and period-start): Forecast = Green AND headroom < target → `GRID_EXPORT`.
4. **High-SOC protection** (period-start): (Amber or Green) AND SOC ≥ 50% AND live solar ≥ 3.5 kW → `GRID_EXPORT`.
5. **Evening bridge rule**: Evening AND battery can cover load until cheap-rate → `SELF_POWERED`.
6. **Forecast mapping**: Apply `FORECAST_TO_MODE` (all map to `SELF_POWERED` by default).
7. **Peak tariff override**: If tariff = Peak → `SELF_POWERED`.

### Key Configuration (`config/settings.py`)

All tunable thresholds live here. Key physics-based values:
- `HEADROOM_TARGET_KWH` = `BATTERY_KWH × 0.5` (12 kWh from 24 kWh battery)
- Derived from: (Solar PV kW − Inverter kW) × 3 hours = (8.9 − 5.5) × 3 = 10.2 kWh
- Hard-coded tariff windows: Night 23:00–08:00, Day 08:00–17:00, Peak 17:00–19:00, Evening 19:00–23:00
- Period names: `Morn`, `Aftn`, `Eve`, `NIGHT`
- `ENABLE_SUMMER_PRE_SUNRISE_DISCHARGE`: Discharges battery to grid in summer before sunrise to make room for solar. Active in months defined by `PRE_SUNRISE_DISCHARGE_MONTHS`.
- `ENABLE_PRE_CHEAP_RATE_NIGHT_EXPORT`: Exports to grid in the window between sunset and cheap-rate start (23:00) when SOC is above `PRE_CHEAP_RATE_NIGHT_EXPORT_MIN_SOC_PERCENT`.

### Forecast Providers (`weather/forecast.py`)

All three providers are instantiated simultaneously via `create_solar_forecast_provider()` and
wrapped in a `ComparingSolarForecastProvider`. ESB is always the decision-maker; Forecast.Solar
and Quartz results are fetched in parallel for comparison and logged to
`data/forecast_comparison_archive.jsonl` for calibration, but do not override ESB decisions.

- **ESB county API** (primary/decision-maker): Returns categorical Red/Amber/Green
- **Forecast.Solar** (comparison): kW → normalized to Red/Amber/Green via capacity thresholds
- **Quartz** (comparison): kW → normalized similarly

Normalization: Red < 20% of `SOLAR_PV_KW`, Amber 20–40%, Green ≥ 40%.

### API Layer (`integrations/`)

- `sigen_interaction.py`: All Sigen API calls — centralizes mode get/set, handles simulation mode, one-time auth recovery
- `sigen_auth.py`: Lazy-loaded singleton Sigen client from `.env` credentials
- `sigen_official.py`: Official OpenAPI client implementation (alternative auth with app key/secret)

### Telemetry (`telemetry/`)

- `telemetry_archive.py`: Appends one inverter snapshot per tick to `data/inverter_telemetry.jsonl` and mode-change events to `data/mode_change_events.jsonl`
- `forecast_calibration.py`: Generates per-period learned multipliers in `data/forecast_calibration.json`. Changes are bounded to ±0.08 per day to prevent instability.

### Key Modules

| Module | Description |
|---|---|
| `logic/scheduler_coordinator.py` | Main loop orchestration — delegates to per-tick handlers |
| `logic/scheduler_state.py` | Centralised mutable state dataclass (`SchedulerState`, `DayStateEntry`, `NightState`) |
| `logic/scheduler_operations.py` | Forecast/sunrise refresh, live solar sampling, telemetry archiving |
| `logic/timed_export.py` | Timed grid export state machine (inactive → active → extended/restored) |
| `logic/period_handler_shared.py` | Shared helpers used by morning/afternoon/evening handlers |
| `logic/decision_logging.py` | Canonical decision checkpoint logging function |
| `logic/mode_change.py` | `apply_mode_change` function (notification, archiving, simulation-mode guard) |
| `config/enums.py` | `Period` and `ForecastStatus` enums |

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

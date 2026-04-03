# Sigen Inverter Smart Control System

## Table of Contents

1. [Overview](#overview)
2. [Plain English Summary](#plain-english-summary)
3. [Key Files](#key-files)
4. [Setup](#setup)
5. [Configuration](#configuration)
6. [How It Works](#how-it-works)
7. [Scheduler Behavior](#scheduler-behavior)
8. [Logging](#logging)
9. [Web Simulator](#web-simulator)
10. [Tests](#tests)
11. [Notes](#notes)

## Overview

This project provides a locally run control system for a Sigen inverter using:

- ESB county API forecast data (primary decision source)
- Optional Open Quartz forecast data (secondary comparison source)
- Battery state of charge from the Sigen API
- Configurable operational mode mappings
- A self-contained scheduler that evaluates conditions throughout the day

The system can also be exercised through an interactive web simulator under `web/`.

## Plain English Summary

This system acts like an automatic energy assistant for your home battery and inverter.

It checks the expected solar generation for morning, afternoon, and evening, compares that
with how full your battery is right now, and then decides which inverter mode makes the most sense.

If the battery is likely to run out of space before strong solar arrives, it can export sooner
to create headroom and reduce wasted solar. If the battery is already very full and the forecast
is good, it can also choose export mode to avoid clipping. Otherwise it follows your normal
forecast-to-mode mapping rules.

This happens automatically on a timed loop, with detailed logs written on every check so you can
see exactly what values were used and why each decision was made.

## Key Files

```text
config.py             Runtime configuration and mode mappings
constants.py          Environment-backed location constants
decision_logic.py     Shared decision logic used by runtime and web simulator
sigen_auth.py         Authentication and singleton creation for Sigen API client
sigen_interaction.py  SigenInteraction wrapper for all Sigen API calls
main.py               Self-contained scheduler and runtime control loop
weather.py            Solar forecast parsing
sunrise_sunset.py     Sunrise/sunset lookup used to derive period windows
web/app.py            Flask web simulator backend
web/simulate_logic.py Web simulator wrapper around shared decision logic
web/static/           Simulator UI
tests/                Test suite
```

## Setup

1. Create and activate a virtual environment.

```sh
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies.

```sh
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-cov
```

3. Create a `.env` file in the project root.

```ini
SIGEN_USERNAME=your_sigen_email
SIGEN_PASSWORD=your_sigen_password
SIGEN_LATITUDE=53.3498
SIGEN_LONGITUDE=-6.2603
```

4. Edit `config.py` for your hardware and scheduler settings.

## Configuration

The core runtime settings live in `config.py`.

### Hardware

```python
SOLAR_PV_KW = 8.9
INVERTER_KW = 5.5
BATTERY_KWH = 24
```

### Scheduler and decision thresholds

```python
POLL_INTERVAL_MINUTES = 15
MAX_PRE_PERIOD_WINDOW_MINUTES = 120
FULL_SIMULATION_MODE = True
NIGHT_MODE_ENABLED = True
NEXT_DAY_PRECHECK_ENABLED = True
NIGHT_PRECHECK_DELAY_MINUTES = 30
LOCAL_TIMEZONE = "Europe/Dublin"
DAY_RATE_CENTS_PER_KWH = 26.596
PEAK_RATE_CENTS_PER_KWH = 32.591
NIGHT_RATE_CENTS_PER_KWH = 13.462
CHEAP_RATE_START_HOUR = 23
CHEAP_RATE_END_HOUR = 8
SELL_RATE_CENTS_PER_KWH = 18.5
HEADROOM_FRAC = 0.25
SOC_HIGH_THRESHOLD = 95
ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE = True
ESTIMATED_HOME_LOAD_KW = 0.8
BRIDGE_BATTERY_RESERVE_KWH = 1.0
ENABLE_EVENING_AI_MODE_TRANSITION = True
EVENING_AI_MODE_START_HOUR = 17
```

Meaning:

- `POLL_INTERVAL_MINUTES`: how often the scheduler wakes up to evaluate each period
- `MAX_PRE_PERIOD_WINDOW_MINUTES`: how far ahead of a period start the scheduler begins checking SOC for possible export
- `FULL_SIMULATION_MODE`: when `True`, run full logic and logging but do not send inverter mode-change commands
- `NIGHT_MODE_ENABLED`: whether the scheduler explicitly applies the configured night mode overnight
- `NEXT_DAY_PRECHECK_ENABLED`: whether the scheduler evaluates the next morning's forecast during the night window
- `NIGHT_PRECHECK_DELAY_MINUTES`: how long after the night window starts before the next-day pre-check runs
- `LOCAL_TIMEZONE`: timezone used when evaluating tariff windows
- `DAY_RATE_CENTS_PER_KWH`: day unit rate for 08:00-17:00 and 19:00-23:00
- `PEAK_RATE_CENTS_PER_KWH`: peak unit rate for 17:00-19:00
- `NIGHT_RATE_CENTS_PER_KWH`: night unit rate for 23:00-08:00
- `SELL_RATE_CENTS_PER_KWH`: export unit rate used for arbitrage analysis and simulation notes
- `CHEAP_RATE_START_HOUR`: local-hour start of cheap night rates
- `CHEAP_RATE_END_HOUR`: local-hour end of cheap night rates
- `FORECAST_PROVIDER`: active provider (`esb_api` or `quartz`)
- `ESB_FORECAST_COUNTY`: county name used for ESB county API lookup (e.g., `Westmeath`)
- `ESB_FORECAST_API_URL`: derived ESB endpoint for selected county id
- `QUARTZ_FORECAST_API_URL`: Open Quartz endpoint (used for comparison or as active provider)
- `QUARTZ_SITE_CAPACITY_KWP`: site capacity sent to Quartz when used
- `HEADROOM_FRAC`: required free battery headroom as a fraction of expected solar energy for that period
- `SOC_HIGH_THRESHOLD`: if forecast is Green and SOC is at or above this threshold, export to grid
- `ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE`: when enabled, Evening decisions avoid charge-oriented behavior before cheap-rate starts if battery can bridge the expected load
- `ESTIMATED_HOME_LOAD_KW`: average household load estimate used to calculate whether current battery energy can cover consumption until cheap-rate begins
- `BRIDGE_BATTERY_RESERVE_KWH`: safety buffer to keep in battery when evaluating bridge sufficiency
- `ENABLE_EVENING_AI_MODE_TRANSITION`: when enabled, Evening period-start decisions can switch to AI mode so mySigen profit-max handles export/recharge optimization
- `EVENING_AI_MODE_START_HOUR`: local hour after which Evening can transition to AI mode

### Tariff schedule currently configured

The tariff schedule currently captured in `config.py` is:

- `08:00-17:00`: Day at `26.596 c/kWh`
- `17:00-19:00`: Peak at `32.591 c/kWh`
- `19:00-23:00`: Day at `26.596 c/kWh`
- `23:00-08:00`: Night at `13.462 c/kWh`
- **Sell rate**: `18.5 c/kWh` (used when exporting to grid; enables arbitrage between sell and cheap-rate recharge)

### Forecast providers (ESB primary, Quartz secondary)

Forecast ingestion is abstracted behind a stable provider interface in `weather.py`.

- Default runtime mode (`FORECAST_PROVIDER=esb_api`) uses ESB county API data for decisions.
- In ESB mode, the app also pulls Quartz and logs a period-by-period comparison summary each refresh.
- Quartz is comparison-only in this mode; inverter decisions still follow ESB-derived forecasts.
- If you set `FORECAST_PROVIDER=quartz`, Quartz becomes the decision source.

Why keep Quartz as secondary while ESB is primary:

- ESB county statuses align with the public county forecast users already see.
- Quartz provides independent site-level predictions, useful for validating trends and potential future migration.
- Running both lets you quantify match/mismatch over time before changing decision source.

### Mode mappings

`SIGEN_MODES`, `FORECAST_TO_MODE`, and `TARIFF_TO_MODE` are all defined in `config.py`.

### Mode mappings in plain English

The easiest way to read this is:

- `SIGEN_MODES` = the list of available inverter modes.
- `FORECAST_TO_MODE` = your default weather rules.
- `TARIFF_TO_MODE` = your default price-period rules.

Current defaults in this project:

- Green -> `SELF_POWERED`
- Amber -> `AI`
- Red -> `TOU`
- Night tariff -> `TOU`
- Peak tariff -> `SELF_POWERED`

Mode descriptions:

| Mode | Main idea | How it behaves day-to-day | When to use it |
| --- | --- | --- | --- |
| **Sigen AI Mode** | The inverter uses smart algorithms and schedule data to decide when to charge, discharge, or export to minimize your electricity cost. | Automatically switches between self-consumption, TOU-like scheduling, and grid export based on predicted solar, load, and tariff. It is currently the recommended primary mode in the commissioning guide. | Best if you are on a variable tariff, have dynamic pricing, or just want the system to manage everything without manual time-based rules. |
| **Self-consumption Mode** | The system prioritizes using solar and battery for your own loads instead of sending power to the grid. | Solar first powers the house; surplus charges the battery; only what remains after that is exported. The battery discharges to cover home loads when solar is low, aiming to reduce grid import as much as possible. | Good if your main goal is to maximize self-use of solar, reduce your bills, and you do not care much about exporting to the grid. |
| **Fully Fed to Grid Mode** | The system's priority is to push PV generation to the grid rather than store it in the battery. | Solar is sent to the grid first; the battery is only used if specifically scheduled or forced (for example, backup). Self-consumption is minimised unless overridden by other settings. | Mainly for setups where export is the main goal (for example, certain feed-in tariff systems, or where battery storage is secondary). |
| **Time-based Control (TOU) Mode** | The system follows a schedule you define for charging, discharging, and self-consumption during different time windows. | You set up charge windows (for example, `00:00-05:00`) to charge the battery from grid when electricity is cheap, and discharge windows (for example, `17:00-22:00`) to use stored energy when prices are high. Other times can be set to self-consumption or grid-only. | Ideal if you are on a time-of-use tariff (cheaper at night, expensive during peak hours) and want to arbitrage between cheap-charge and expensive-use periods. |
| **Remote EMS / Time-based EMS** | The system is controlled by a remote-scheduling service (for example, via the mySigen app or installer-defined schedules). | Charge/discharge times and SOC limits are pushed from the cloud or installer view, overriding or layering on top of local modes. TOU rules can be set remotely without touching the local-mode table. | Used when an installer or platform wants centrally managed scheduling (for example, for multiple homes, grid-support schemes, or dynamic market signals). |

### Final decision order (very important)

The runtime does not just apply one mapping directly. It uses this order:

1. Export rules first (highest priority):
If forecast is Green and battery headroom is too low, or SOC is already very high, use `GRID_EXPORT`.
2. Evening bridge rule second:
If period is Evening and battery can safely cover expected household demand until cheap-rate starts, use `SELF_POWERED`.
3. Peak-price rule third:
If export is not needed and tariff is Peak, use `SELF_POWERED`.
4. Default mapping last:
If neither of the above applies, use `FORECAST_TO_MODE` (Green/Amber/Red).
5. Evening AI transition at period-start (optional final override):
If `ENABLE_EVENING_AI_MODE_TRANSITION` is enabled and local time is after `EVENING_AI_MODE_START_HOUR`, set `AI` for Evening so mySigen profit-max can run arbitrage.

### Plain “if/then” version

For daytime periods, read it like this:

1. If Green forecast AND battery space is too small for expected solar -> `GRID_EXPORT`.
2. Else if Green forecast AND SOC already above threshold -> `GRID_EXPORT`.
3. Else if period is Evening and battery can cover expected load until cheap-rate starts -> `SELF_POWERED`.
4. Else if tariff is Peak -> `SELF_POWERED`.
5. Else -> use forecast mapping (Green/Amber/Red).
6. At Evening period-start, if Evening AI transition is enabled and local time is past the configured threshold -> force `AI`.

For night:

1. Use night tariff behavior (`TARIFF_TO_MODE["NIGHT"]`) during cheap-rate hours.
2. Before cheap-rate starts, hold `PRE_CHEAP_RATE_MODE` to avoid early charge behavior.
3. Optional next-day pre-check can prepare for tomorrow morning, but still respects pre-cheap-rate protection.

### Quick examples

- Green + high SOC -> `GRID_EXPORT` (headroom protection wins).
- Amber + Peak tariff -> `SELF_POWERED` (peak override wins).
- Red + normal Day tariff -> `TOU` (default forecast mapping).

## How It Works

### Shared decision logic

The export and mode-selection logic is centralized in `decision_logic.py`.

All direct Sigen API calls are centralized in `sigen_interaction.py` via `SigenInteraction`.

Both of these use the same shared code path:

- `main.py` runtime scheduler
- `web/simulate_logic.py` web simulator

That ensures the simulator and the live runtime cannot drift apart.

### Battery headroom calculation

Battery headroom is the free storage space remaining in the battery:

$$
\text{headroom\_kwh} = \text{battery\_kwh} \times \left(1 - \frac{\text{soc}}{100}\right)
$$

Example:

- battery size = `24 kWh`
- SOC = `80%`

$$
24 \times (1 - 0.80) = 4.8 \text{ kWh}
$$

### Expected solar energy calculation

For the web simulator, expected solar for a period is:

$$
\text{period\_solar\_kwh} = \min(\text{solar\_pv\_kw}, \text{inverter\_kw}) \times 3.0
$$

For the runtime scheduler, the period forecast value is read in watts and converted to kWh over an assumed 3-hour period:

$$
\text{period\_solar\_kwh} = \min\left(\frac{\text{forecast\_watts}}{1000}, \text{solar\_pv\_kw}, \text{inverter\_kw}\right) \times 3.0
$$

### Export-to-grid rules

The system exports to grid under either of these conditions.

#### Rule 1: Insufficient headroom before a Green period

The target free headroom is:

$$
\text{headroom\_target\_kwh} = \text{period\_solar\_kwh} \times \text{HEADROOM\_FRAC}
$$

If:

$$
#### Why AI is preferred for Evening in this project

Evening can still have some usable solar, but in most real-world days it is lower and less reliable than daytime generation. The bigger financial lever near cheap-rate start is battery state, not late solar peak.

\text{headroom\_kwh} < \text{headroom\_target\_kwh}
$$

then the system selects `GRID_EXPORT` to create battery space ahead of the solar period.

#### Rule 2: High SOC on a Green forecast

If:

$$
\text{soc} \ge \text{SOC\_HIGH\_THRESHOLD}
$$

and the forecast status is `Green`, the system selects `GRID_EXPORT`.

### Day and peak tariff influence

In addition to forecast status and SOC/headroom, the scheduler also considers the
tariff period for the target time of the period action:

- `DAY` during 08:00-17:00 and 19:00-23:00
- `PEAK` during 17:00-19:00
- `NIGHT` during 23:00-08:00

Decision precedence for daytime periods is:

1. Export-to-grid safety/space rules (headroom shortfall or high SOC with Green forecast)
2. Peak tariff override: if tariff is `PEAK` and export was not selected, force self-powered mode to minimize expensive imports
3. Otherwise use the forecast mapping (Green/Amber/Red)

This means peak pricing can actively change the daytime mode choice, not just night windows.

### Dynamic export lead time

If more battery headroom is needed before the upcoming period, the scheduler estimates how early export should begin.

Headroom deficit:

$$
\text{headroom\_deficit\_kwh} = \max(0, \text{headroom\_target\_kwh} - \text{headroom\_kwh})
$$

Lead time before the period:

$$
\text{lead\_time\_hours} = \frac{\text{headroom\_deficit\_kwh} \times 1.1}{\text{inverter\_kw}}
$$

The `1.1` factor adds a 10% buffer.

The scheduler then calculates:

$$
\text{export\_by} = \text{period\_start} - \text{lead\_time}
$$

When current time is at or after `export_by`, it can trigger the pre-period export decision.

### Night behavior

The scheduler now has an explicit night window.

- Before the first daytime period starts, the system treats that as a pre-dawn night window.
- After sunset, the system treats that as the evening/night window for the upcoming day.

During the active night window it can do two separate things:

1. Apply either a pre-cheap-rate mode or the configured night mode depending on local tariff time.
2. Optionally run a next-day pre-check for the next morning forecast after `NIGHT_PRECHECK_DELAY_MINUTES`.

For example, with cheap rates from 11pm to 8am:

- after sunset but before 11pm, the system stays in pre-cheap-rate mode so it does not start charge-oriented night behavior too early
- from 11pm to 8am local time, it can use `TARIFF_TO_MODE["NIGHT"]`
- after 8am, if the first daytime period has not started yet, it falls back out of the cheap-rate night mode again

The next-day pre-check uses the upcoming first daytime period, normally `Morn`.

If the next morning looks strong enough that headroom must be created, it can choose `GRID_EXPORT` overnight.
Otherwise it stays in the appropriate pre-cheap-rate or cheap-rate night mode for the current local tariff phase.

### AI Mode profit-max and arbitrage (battery sell-discharge-recharge)

When using **Sigen AI Mode** with **profit-max** enabled in the mySigen app, the system can optimize battery charging by performing energy arbitrage: selling excess battery at higher rates and then recharging from the grid at cheap-rate times.

#### How it works

The arbitrage opportunity exists when:

- **Sell rate** (18.5 c/kWh during day/peak) is higher than **night charge rate** (13.462 c/kWh)
- Battery is at high SOC as evening approaches
- Cheap-rate window (23:00-08:00) is approaching

AI mode with profit-max will:

1. **Discharge excess battery before cheap rates** (typically around 17:00-23:00) into `GRID_EXPORT` mode at day/peak rates (18.5-32.6 c/kWh)
2. **Recharge from grid during cheap window** (23:00-08:00) at night rates (13.462 c/kWh)
3. *Net gain*: ~5 c/kWh per cycle ((18.5 - 13.462) × kWh discharged → recharged)

#### Configuration requirements

For AI Mode profit-max to work correctly, you must:

1. **Set tariffs in mySigen app**: Configure day, peak, night, and sell rates in the device settings
2. **Enable profit-max mode**: In mySigen app settings, activate "Profit Max" or "Export Optimization" mode
3. **Set sell rate in config.py**: Document the sell rate for reference and simulation
4. **Verify discharge cut-off SOC**: Ensure the discharge cut-off in mySigen app is low enough (typically 10-20%) to allow significant discharge before cheap rates start

#### Why this scheduler uses TOU → AI transition at night

This scheduler can be tuned to:

- Run the day in forecast-based modes (Green/Amber/Red)
- Switch to **AI Mode** as evening approaches
- Let AI mode handle the sell-discharge-recharge optimization automatically

This approach:

- Avoids hard-coded discharge logic in the Python scheduler
- Leverages Sigen's native profit-max optimization
- Simplifies debugging: all tariff/export behavior is centralized on the device
- Remains flexible: profit-max parameters can be tuned in mySigen without code changes

#### Why AI is preferred for Evening in this project

Evening can still have usable solar, but it is usually less predictable and lower than daytime production. Near cheap-rate start, the larger economic lever is often battery state rather than late-day solar capture.

With your tariff setup (`sell = 18.5 c/kWh`, `night = 13.462 c/kWh`), the arbitrage spread is positive. Because of that, the default preference is to let AI profit-max decide whether to discharge/export before cheap rates and then refill overnight.

In practical terms:

- if evening solar is still worthwhile, AI can still choose self-use/export behavior dynamically
- if SOC is high before cheap-rate, AI can create battery headroom by discharging/exporting
- during cheap-rate hours, AI can recharge at lower unit cost

That is why the scheduler supports an Evening AI transition instead of hard-coding one fixed Evening policy for all days.

### Full simulation mode (dry run)

Set `FULL_SIMULATION_MODE = True` in `config.py` to run safely without changing inverter state.

When enabled:

- the scheduler still runs all calculations and timing checks
- forecast and SOC are still fetched normally
- startup still fetches and logs current inverter mode
- every would-be mode change is logged clearly with a full separator banner and action details
- no `set_operational_mode(...)` command is sent to the inverter

This is intended for realistic test runs where you want full observability without altering the live system state.

## Scheduler Behavior

Running:

```sh
python main.py
```

starts a self-contained scheduler.

The scheduler:

1. Wakes every `POLL_INTERVAL_MINUTES`
2. Refreshes forecast and sunrise/sunset data once per day
3. Divides the daylight window from sunrise to sunset into equal period start times for `Morn`, `Aftn`, and `Eve`
4. Explicitly applies night mode during the night window when enabled
5. Optionally checks the next morning forecast during the night window and can prepare with export if needed
6. Begins monitoring each daytime period when inside the `MAX_PRE_PERIOD_WINDOW_MINUTES` window before that period starts
7. Fetches live SOC and evaluates export, forecast, and tariff-period rules for each period
8. Applies pre-period export at most once per period per day
9. Applies the definitive period-start mode at most once per period per day

## Logging

Logging is controlled by `LOG_LEVEL` in `config.py`.

Recommended values:

- `INFO` for normal operation
- `DEBUG` for detailed troubleshooting

### Check logging

Each scheduler evaluation writes an info log containing the values used and the conclusion reached.

For each check, the log includes:

- current UTC time
- period start time
- forecast watts
- forecast status
- expected solar kWh
- SOC
- battery headroom kWh
- target headroom kWh
- headroom deficit kWh
- calculated `export_by` time
- selected decision mode
- outcome
- reason

Example structure:

```text
[Morn] PRE-PERIOD CHECK | now=... | period_start=... | forecast_w=500 | status=Green |
expected_solar_kwh=1.50 | soc=82.0 | headroom_kwh=4.32 | headroom_target_kwh=0.38 |
headroom_deficit_kwh=0.00 | export_by=... | decision_mode=SELF_POWERED |
outcome=waiting until export window opens | reason=Default mapping for Green.
```

## Web Simulator

Start the web UI with:

```sh
python web/app.py
```

Then open:

```text
http://127.0.0.1:5000/
```

The simulator:

- preloads inverter, battery, and solar config values
- defaults SOC to `80%` while keeping it editable
- lets you simulate Morning, Afternoon, and Evening forecasts
- treats Night as tariff-driven (not a manual solar status)
- derives overnight prep decisions from the next morning forecast
- shows both the selected mode and a human-readable explanation
- uses the same shared decision logic as the live runtime

## Tests

Run all tests with:

```sh
source .venv/bin/activate
python -m pytest -q
```

Focused checks used during development:

```sh
python -m pytest -q web/test_app_simulate.py tests/test_main.py -rA
```

Coverage run:

```sh
python -m pytest -q --cov=. --cov-report=term-missing
```

## Notes

- The runtime scheduler is self-contained and does not require cron.
- The decision logic is centralized so runtime and simulator stay aligned.
- Sunrise/sunset times are used to derive dynamic daytime period boundaries.

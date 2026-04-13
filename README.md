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
9. [Mode Test Utility](#mode-test-utility)
10. [Email Notifications](#email-notifications)
11. [Forecast Accuracy Report](#forecast-accuracy-report)
12. [Web Simulator](#web-simulator)
13. [Tests](#tests)
14. [Notes](#notes)

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
.
├── main.py                           # Self-contained scheduler and runtime control loop
├── config/
│   ├── settings.py                   # Runtime configuration and mode mappings
│   └── constants.py                  # Environment-backed location constants
├── logic/
│   └── decision_logic.py             # Shared decision logic used by runtime and web simulator
├── integrations/
│   ├── sigen_auth.py                 # Authentication and singleton creation for Sigen API client
│   ├── sigen_official.py             # Official OpenAPI client (account/key auth, endpoint overrides)
│   ├── sigen_interaction.py          # SigenInteraction wrapper for Sigen API calls
│   └── tools/                        # Sigen diagnostics scripts (mode listing and API config checks)
├── weather/
│   ├── forecast.py                   # Solar forecast parsing
│   ├── greengrid_forecast.py         # GREEN-GRID Shiny app browser automation (optional)
│   └── sunrise_sunset.py             # Sunrise/sunset lookup used to derive period windows
├── telemetry/
│   └── forecast_calibration.py       # Daily bounded calibration generation from telemetry
├── scripts/
│   ├── compare_forecast_accuracy.py  # ESB/Forecast.Solar/Quartz vs inverter telemetry analysis
│   ├── compare_greengrid_vs_actuals.py # GREEN-GRID vs inverter telemetry analysis
│   ├── forecast_vs_actual.py         # Forecast-vs-actual reporting and status analysis
│   ├── test_mode_switch.py           # Quick mode listing/switch test utility for live API checks
│   └── test_mode_change_email.py     # Sends a test mode-change email via scheduler simulation path
├── web/
│   ├── app.py                        # Flask web simulator backend
│   ├── simulate_logic.py             # Web simulator wrapper around shared decision logic
│   └── static/                       # Simulator UI
└── tests/                            # Test suite
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
EMAIL_SENDER=your_sender@gmail.com
EMAIL_RECEIVER=your_receiver@gmail.com
GMAIL_APP_PASSWORD=your_gmail_app_password
```

4. Edit `config/settings.py` for your hardware and scheduler settings.

## Configuration

The core runtime settings live in `config/settings.py`.

### Hardware

```python
SOLAR_PV_KW = 8.9
INVERTER_KW = 5.5
BATTERY_KWH = 24
```

### Scheduler and decision thresholds

```python
POLL_INTERVAL_MINUTES = 5
FORECAST_REFRESH_INTERVAL_MINUTES = 30
FORECAST_SOLAR_ARCHIVE_ENABLED = True
FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES = 30
MAX_PRE_PERIOD_WINDOW_MINUTES = 120
FULL_SIMULATION_MODE = True
NIGHT_MODE_ENABLED = True
LOCAL_TIMEZONE = "Europe/Dublin"
QUARTZ_RED_CAPACITY_FRACTION = 0.20
QUARTZ_GREEN_CAPACITY_FRACTION = 0.40
FORECAST_SOLAR_POWER_MULTIPLIER = 1.53
CHEAP_RATE_START_HOUR = 23
CHEAP_RATE_END_HOUR = 8
HEADROOM_TARGET_KWH = 10.2
ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE = True
ESTIMATED_HOME_LOAD_KW = 0.8
BRIDGE_BATTERY_RESERVE_KWH = 1.0
MORNING_HIGH_SOC_PROTECTION_ENABLED = True
MORNING_HIGH_SOC_THRESHOLD_PERCENT = 95.0
LIVE_CLIPPING_RISK_VALID_PERIODS = "M,A"
LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT = 90.0
LIVE_CLIPPING_RISK_SOLAR_TRIGGER_KW = 4.0
ENABLE_EVENING_AI_MODE_TRANSITION = True
EVENING_AI_MODE_START_HOUR = 17
```

Meaning:

- `POLL_INTERVAL_MINUTES`: how often the scheduler wakes up to evaluate each period
- `FORECAST_REFRESH_INTERVAL_MINUTES`: how often forecast data refreshes during the day (`0` disables intra-day refresh)
- `FORECAST_SOLAR_ARCHIVE_ENABLED`: enables per-tick raw Forecast.Solar pulls to local archive
- `FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES`: minimum minutes between raw Forecast.Solar archive pulls; `30` is a sensible default for the public tier because its forecast resolution is hourly
- `MAX_PRE_PERIOD_WINDOW_MINUTES`: how far ahead of a period start the scheduler begins checking SOC for possible export
- `FULL_SIMULATION_MODE`: when `True`, run full logic and logging but do not send inverter mode-change commands
- `NIGHT_MODE_ENABLED`: whether the scheduler explicitly applies the configured night mode overnight
- `LOCAL_TIMEZONE`: timezone used when evaluating tariff windows
- `CHEAP_RATE_START_HOUR`: local-hour start of cheap night rates
- `CHEAP_RATE_END_HOUR`: local-hour end of cheap night rates
- `FORECAST_PROVIDER`: active provider (`esb_api`, `forecast_solar`, or `quartz`)
- `ESB_FORECAST_COUNTY`: county name used for ESB county API lookup (e.g., `Westmeath`)
- `ESB_FORECAST_API_URL`: derived ESB endpoint for selected county id
- `QUARTZ_FORECAST_API_URL`: Open Quartz endpoint (used for comparison or as active provider)
- `QUARTZ_SITE_CAPACITY_KWP`: site capacity sent to Quartz when used
- `QUARTZ_RED_CAPACITY_FRACTION`: lower Quartz status threshold as a fraction of configured array capacity
- `QUARTZ_GREEN_CAPACITY_FRACTION`: upper Quartz status threshold as a fraction of configured array capacity
- `FORECAST_SOLAR_POWER_MULTIPLIER`: scalar applied to Forecast.Solar watts before period status/value normalization; use this to correct persistent local bias (for example, historical under-forecasting)
```python
HEADROOM_TARGET_KWH = 10.2
```

Meaning:

- `HEADROOM_TARGET_KWH`: fixed battery headroom target (10.2 kWh = surplus capacity × 3 hours)
- `ENABLE_PRE_CHEAP_RATE_BATTERY_BRIDGE`: when enabled, Evening decisions avoid charge-oriented behavior before cheap-rate starts if battery can bridge the expected load
- `ESTIMATED_HOME_LOAD_KW`: average household load estimate used to calculate whether current battery energy can cover consumption until cheap-rate begins
- `BRIDGE_BATTERY_RESERVE_KWH`: safety buffer to keep in battery when evaluating bridge sufficiency
- `MORNING_HIGH_SOC_PROTECTION_ENABLED`: enables high-SOC export protection rule for selected daytime periods
- `MORNING_HIGH_SOC_THRESHOLD_PERCENT`: SOC threshold for the high-SOC export protection rule
- `LIVE_CLIPPING_RISK_VALID_PERIODS`: comma-separated period codes where live clipping-risk Amber→Green promotion is active (`M`=Morning, `A`=Afternoon, `E`=Evening). Only applies to the intra-tick live solar check.
- `LIVE_CLIPPING_RISK_SOC_THRESHOLD_PERCENT`: SOC threshold for live clipping-risk Amber→Green promotion
- `LIVE_CLIPPING_RISK_SOLAR_TRIGGER_KW`: rolling live-solar kW threshold for live clipping-risk promotion
- `ENABLE_EVENING_AI_MODE_TRANSITION`: when enabled, Evening period-start decisions can switch to AI mode so mySigen profit-max handles export/recharge optimization
- `EVENING_AI_MODE_START_HOUR`: local hour after which Evening can transition to AI mode

### Tariff schedule windows

The tariff time windows in `config/settings.py` define when each tariff period is active. These drive period detection and cheap-rate window checks used by the scheduler:

- `08:00-17:00`: Day period
- `17:00-19:00`: Peak period
- `19:00-23:00`: Day period (evening)
- `23:00-08:00`: Night / cheap-rate window

The scheduler uses these windows to determine whether to use self-powered, TOU, or AI modes at each transition. Actual electricity rates (c/kWh) are not stored in the config — the system makes mode decisions purely on forecast quality and tariff period, not on cost arithmetic.

### Forecast providers (ESB primary, Forecast.Solar backup, Quartz fallback)

Forecast ingestion is abstracted behind a stable provider interface in `weather/forecast.py`.

- Default runtime mode (`FORECAST_PROVIDER=esb_api`) uses ESB county API data for decisions.
- In ESB mode, the app pulls Forecast.Solar first and Quartz second and logs a period-by-period comparison summary each refresh.
- Forecast.Solar and Quartz are comparison-only in this mode; inverter decisions still follow ESB-derived statuses.
- For numeric watts used in headroom/clipping calculations, backup priority is Forecast.Solar first, then Quartz.
- If you set `FORECAST_PROVIDER=forecast_solar`, Forecast.Solar becomes the decision source.
- If you set `FORECAST_PROVIDER=quartz`, Quartz becomes the decision source.

Why keep Forecast.Solar/Quartz as secondary sources while ESB is primary:

- ESB county statuses align with the public county forecast users already see.
- Forecast.Solar and Quartz provide independent site-level predictions, useful for validating trends and potential future migration.
- Running both lets you quantify match/mismatch over time before changing decision source.

How the comparison is normalized:

- ESB and Quartz are not naturally comparable. ESB is county-level and categorical (`Red`/`Amber`/`Green`), while Quartz is site-level and numeric (predicted power by timestamp).
- Quartz timestamps are first converted into the local tariff timezone (`Europe/Dublin`) before grouping into project periods (`Morn`, `Aftn`, `Eve`, `NIGHT`). This avoids false mismatches caused by comparing UTC buckets to local scheduler windows.
- Quartz period status is derived from average predicted output as a share of configured array size (`QUARTZ_SITE_CAPACITY_KWP`). Current normalization is:
	- `Red`: less than `QUARTZ_RED_CAPACITY_FRACTION` (default 20%)
	- `Amber`: from `QUARTZ_RED_CAPACITY_FRACTION` up to `QUARTZ_GREEN_CAPACITY_FRACTION` (default 20% to <40%)
	- `Green`: `QUARTZ_GREEN_CAPACITY_FRACTION` or more (default >=40%)
- ESB still exposes synthetic placeholder forecast values (`Red=100`, `Amber=300`, `Green=500`) internally so the rest of the control logic keeps working unchanged. In comparison logs these are explicitly labelled as synthetic and not as measured site power.

Why the normalization exists:

- Without local-time bucketing, Quartz can look wrong simply because its morning energy lands in the wrong scheduler period.
- Without capacity-based thresholds, Quartz will look unrealistically optimistic for a larger array because even modest output easily exceeds tiny fixed watt cutoffs.
- Without labelling ESB values as synthetic, the logs can make county statuses look like real measured watts, which is misleading.
- The goal is not to force perfect agreement. The goal is to make ESB-vs-Quartz differences interpretable enough to judge whether Quartz is a credible future decision source.

Actual inverter telemetry is also archived locally for later analysis:

- The scheduler appends one raw inverter snapshot per tick to `data/inverter_telemetry.jsonl` when the inverter API is reachable.
- Each record includes the raw `get_energy_flow()` payload, current operational mode, scheduler timestamp, and the forecast state seen by the scheduler at that time.
- When night sleep mode is active, the scheduler also writes a dedicated `night_sleep_start` snapshot before entering the long evening sleep window so the latest daily totals (including `pvDayNrg`) are preserved.
- Each record also includes derived clipping heuristics. A sample is flagged as likely clipping only when inverter-side solar equals the `5.5 kW` ceiling.
- This file is intended for post-run analysis so you can compare forecasted periods against what the inverter and battery actually did.

Daily bounded calibration is also applied from that telemetry:

- On daily forecast refresh, the scheduler reads recent telemetry from `data/inverter_telemetry.jsonl` and writes a bounded calibration artifact to `data/forecast_calibration.json`.
- The calibration adjusts two numeric inputs per daytime period (`Morn`, `Aftn`, `Eve`):
	- `power_multiplier`: inflates forecast watts when recent actual PV has been consistently higher than forecast
	- `export_lead_buffer_multiplier`: starts pre-export slightly earlier when clipping risk has been recurring
- Changes are deliberately bounded per day so the system cannot swing too far overnight.
- The rule structure does not self-rewrite. It keeps the existing decision logic with fixed hardware-based headroom targets.
- Manual rebuild is also available with `python telemetry/forecast_calibration.py`.

### Mode mappings

`SIGEN_MODES`, `FORECAST_TO_MODE`, and `PERIOD_TO_MODE` are all defined in `config/settings.py`.

### Mode mappings in plain English

The easiest way to read this is:

- `SIGEN_MODES` = the list of available inverter modes.
- `FORECAST_TO_MODE` = your default weather rules.
- `PERIOD_TO_MODE` = your default price-period rules.

Current defaults in this project:

- Green -> `SELF_POWERED`
- Amber -> `AI`
- Red -> `AI`
- Night tariff -> `AI`
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
If forecast is Green and battery headroom is too low, use `GRID_EXPORT`.
Live clipping-risk can promote Amber to Green when configured period, SOC, and live-solar triggers are met.
High-SOC export protection can also force export in configured periods when SOC is high and headroom is below target.
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
2. Else if period is Evening and battery can cover expected load until cheap-rate starts -> `SELF_POWERED`.
3. Else if tariff is Peak -> `SELF_POWERED`.
4. Else -> use forecast mapping (Green/Amber/Red).
5. At Evening period-start, if Evening AI transition is enabled and local time is past the configured threshold -> force `AI`.

For night:

1. Apply `PERIOD_TO_MODE["NIGHT"]` throughout the active night window.
2. Keep night behavior simple and deterministic (AI-only in the default config).

### Quick examples

- Green + headroom < 10.2 kWh -> `GRID_EXPORT` (insufficient space for incoming solar).
- Green + headroom >= 10.2 kWh -> Follow forecast mode (battery can absorb the solar).
- Amber + Peak tariff -> `SELF_POWERED` (peak override wins).
- Red + normal Day tariff -> `AI` (default forecast mapping).

## How It Works

### Shared decision logic

The export and mode-selection logic is centralized in `logic/decision_logic.py`.

All direct Sigen API calls are centralized in `integrations/sigen_interaction.py` via `SigenInteraction`.

### Official OpenAPI integration (`integrations/sigen_official.py`)

The project includes an official API client implementation that can be used by the
interaction layer when running against OpenAPI endpoints:

- Supports both account auth and app key/secret auth
- Auto-discovers `system_id` from the system list when not explicitly configured
- Allows endpoint path overrides via environment variables so path changes can be
	handled without code edits
- Keeps a compatibility mode for account auth payload variants observed across
	different tenants/regions

Important constraints:

- We do not currently have access to every official API capability in all environments.
- Some endpoints documented in the official markdown may be unavailable or permission-
	restricted depending on tenant, app type, and account scopes.
- Observed mode values can include legacy/account values beyond the small public enum
	subset, so the client preserves both officially documented and observed values.

Operational recommendation:

- Treat `.github/reference/Sigen API/API Documentation/` as source of truth for
	endpoint semantics.
- Validate endpoint availability against your own account credentials before assuming
	a documented endpoint can be used in production.

Both of these use the same shared code path:

- `main.py` runtime scheduler
- `web/simulate_logic.py` web simulator

That ensures the simulator and the live runtime cannot drift apart.

### Battery headroom calculation

Battery headroom is the free storage space remaining in the battery:

$$
	ext{headroom} = \text{battery capacity} \times \left(1 - \frac{\text{SOC}}{100}\right)
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
	ext{period solar} = \min(\text{solar PV kW}, \text{inverter kW}) \times 3.0
$$

For the runtime scheduler, the period forecast value is read in watts and converted to kWh over an assumed 3-hour period:

$$
	ext{period solar} = \min\left(\frac{\text{forecast watts}}{1000}, \text{solar PV kW}, \text{inverter kW}\right) \times 3.0
$$

### Export-to-grid rules

The system exports to grid under these conditions.

#### Rule 1: Insufficient headroom before a Green period

The target free headroom is derived from hardware surplus capacity multiplied by a 3-hour reserve window:

$$
	ext{headroom target} = (\text{solar PV kW} - \text{inverter kW}) \times 3.0 = (8.9 - 5.5) \times 3.0 = 10.2 \text{ kWh}
$$

This fixed target ensures the battery can absorb 3 hours of maximum surplus generation (3.4 kW) without clipping, independent of forecast quality or period.

If:
$$
	ext{headroom} < \text{headroom target}
$$

then the system selects `GRID_EXPORT` to create battery space ahead of the solar period.

**Why this model is superior:** The old fraction-based model (0.25 × forecast) was fundamentally broken because it relied on ESB synthetic forecast categories (500W "Green" label) rather than real solar predictions. This led to ultra-small targets (0.375 kWh at SOC=100%) and essentially no early lead time. The physics model instead grounds the target in actual hardware constraints: *how much surplus power can the inverter not distribute?* This is deterministic, testable, and produces realistic lead times (~2 hours at SOC=100% for Green periods).

### Day and peak tariff influence

In addition to forecast status and SOC/headroom, the scheduler also considers the
tariff period for the target time of the period action:

- `DAY` during 08:00-17:00 and 19:00-23:00
- `PEAK` during 17:00-19:00
- `NIGHT` during 23:00-08:00

Decision precedence for daytime periods is:

1. Export-to-grid safety/space rules (headroom shortfall before a Green period)
2. Peak tariff override: if tariff is `PEAK` and export was not selected, force self-powered mode to minimize expensive imports
3. Otherwise use the forecast mapping (Green/Amber/Red)

This means peak pricing can actively change the daytime mode choice, not just night windows.

### Dynamic export lead time

If more battery headroom is needed before the upcoming period, the scheduler estimates how early export should begin.

Headroom deficit:

$$
	ext{headroom deficit} = \max(0, \text{headroom target} - \text{headroom})
$$

Lead time before the period:

$$
	ext{solar avg kW (latest 3)} = \text{average of latest 3 live solar readings}
$$

$$
	ext{effective battery export kW} = \max(0.2, \text{inverter kW} - \text{solar avg kW (latest 3)})
$$

$$
	ext{lead time hours} = \frac{\text{headroom deficit} \times \text{export lead buffer multiplier}}{\text{effective battery export kW}}
$$

This causes earlier export start when live solar is already high (because less inverter headroom remains for battery discharge).

The scheduler then calculates:

$$
	ext{export by} = \text{period start} - \text{lead time}
$$

When current time is at or after `export_by`, it can trigger the pre-period export decision.

### Night behavior

The scheduler now has an explicit night window.

- Before the first daytime period starts, the system treats that as a pre-dawn night window.
- After sunset, the system treats that as the evening/night window for the upcoming day.

During the active night window it can do two separate things:

1. Apply the configured night mode (`PERIOD_TO_MODE["NIGHT"]`).
2. Optionally sleep between checks to reduce polling until the morning pre-period window opens.

For example, with cheap rates from 11pm to 8am:

- after sunset and before the first daytime period, the scheduler holds the configured night mode
- if night sleep mode is enabled, it can sleep and wake near the morning pre-period window

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
3. **Set sell rate in config/settings.py**: Document the sell rate for reference and simulation
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

Set `FULL_SIMULATION_MODE = True` in `config/settings.py` to run safely without changing inverter state.

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
3. Optionally refreshes forecast data intra-day every `FORECAST_REFRESH_INTERVAL_MINUTES` when configured above `0`
4. Optionally pulls and archives raw Forecast.Solar readings every `FORECAST_SOLAR_ARCHIVE_INTERVAL_MINUTES`
5. Divides the daylight window from sunrise to sunset into equal period start times for `Morn`, `Aftn`, and `Eve`
6. Explicitly applies night mode during the night window when enabled
7. Optionally checks the next morning forecast during the night window and can prepare with export if needed
8. Begins monitoring each daytime period when inside the `MAX_PRE_PERIOD_WINDOW_MINUTES` window before that period starts
9. Fetches live SOC and evaluates export, forecast, and tariff-period rules for each period
10. Applies pre-period export at most once per period per day
11. Applies the definitive period-start mode at most once per period per day

## Logging

Logging is controlled by `LOG_LEVEL` in `config/settings.py`.

Terminal output is level-colorized to improve readability:

- `WARNING` lines are shown in orange
- `ERROR` and `CRITICAL` lines are shown in red

Color is auto-enabled when stderr is a TTY. You can also control behavior with environment variables:

- `FORCE_COLOR=1` to force ANSI colors in environments that do not report a TTY
- `NO_COLOR=1` to disable ANSI colors

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
- rolling 3-sample live solar average (kW)
- effective battery export capacity after live solar occupancy (kW)
- adjusted lead-time hours
- calculated `export_by` time
- selected decision mode
- outcome
- reason

Example structure:

```text
[Morn] PRE-PERIOD CHECK | now=... | period_start=... | forecast_w=500 | status=Green |
expected_solar_kwh=1.50 | soc=82.0 | headroom_kwh=4.32 | headroom_target_kwh=10.20 |
headroom_deficit_kwh=5.88 | solar_avg_kw_3=2.80 | effective_battery_export_kw=2.70 |
lead_time_hours_adjusted=2.40 | export_by=... | decision_mode=GRID_EXPORT |
outcome=waiting until export window opens | reason=Default mapping for Green.
```

### Mode-change event archive

Every set-operational-mode command attempt (including simulation-mode set attempts) is appended to:

- `data/mode_change_events.jsonl`

Each event includes timestamp, period/context, requested mode, reason, prior mode payload (if readable), response payload, and success/failure. This allows direct correlation with inverter telemetry snapshots in `data/inverter_telemetry.jsonl`.

## Mode Test Utility

Run this script to inspect current mode, list all available operational modes returned by the active API client, and optionally switch to a selected mode value:

```sh
python scripts/test_mode_switch.py
```

Optional usage:

```sh
python scripts/test_mode_switch.py --list
python scripts/test_mode_switch.py 1
python scripts/test_mode_switch.py 5
```

Behavior:

- With no mode parameter (or `--list`), it prints:
	- the current operational mode
	- the full available modes list (label + integer value)
- With a mode value, it prints:
	- current mode before switching
	- mode set response payload
	- current mode after switching

Important:

- `FULL_SIMULATION_MODE = True` means mode writes are suppressed (read-only calls still run).
- Set `FULL_SIMULATION_MODE = False` to send real mode-change commands.

## Scripts Reference

All files under `scripts/` are documented below.

- `scripts/compare_forecast_accuracy.py`
	- Period-level accuracy analysis for ESB, Forecast.Solar, and Quartz against inverter telemetry.
	- Reports status accuracy %, MAE, MAPE, and bias ratio; suggests Forecast.Solar multiplier.
	- Run: `python scripts/compare_forecast_accuracy.py`

- `scripts/compare_greengrid_vs_actuals.py`
	- Period-level accuracy analysis for GREEN-GRID forecasts against inverter telemetry.
	- Requires GREEN-GRID forecast data captured via `weather/greengrid_forecast.py` (Playwright required).
	- Aggregates hourly GREEN-GRID forecasts into periods and compares status accuracy, MAE, MAPE, ratio.
	- Suggests a GREEN-GRID multiplier if bias is detected.
	- Run: `python scripts/compare_greengrid_vs_actuals.py`

- `scripts/forecast_vs_actual.py`
	- Convenience wrapper to run tests from shell.
	- Run: `bash scripts/test.sh`

- `scripts/test_legacy_api.py`
	- Read-only diagnostic for the legacy third-party Sigen client.
	- Run: `python scripts/test_legacy_api.py`
	- Optional: `python scripts/test_legacy_api.py --json --skip-signals`

- `scripts/test_mode_change_email.py`
	- Sends a test mode-change notification email through scheduler email path.
	- Run: `python scripts/test_mode_change_email.py`
	- Optional: `python scripts/test_mode_change_email.py --mode 1 --period "ManualTest" --reason "Testing email path"`

- `scripts/test_mode_switch.py`
	- Legacy/active-client mode check and mode switch helper.
	- Run: `python scripts/test_mode_switch.py`
	- Optional: `python scripts/test_mode_switch.py --list` and `python scripts/test_mode_switch.py <mode_id>`

- `scripts/test_mode_switch_official.py`
	- Official API mode diagnostics and optional official mode switch (`--apply`).
	- Run: `python scripts/test_mode_switch_official.py`
	- Optional: `python scripts/test_mode_switch_official.py --list` and `python scripts/test_mode_switch_official.py 5 --apply`

- `scripts/test_pv_string_faults_official.py`
	- Official API per-string PV realtime snapshot and imbalance warning check.
	- Requires inverter/AIO device serial number (`--serial` or `SIGEN_INVERTER_SERIAL`), not systemId.
	- Run: `python scripts/test_pv_string_faults_official.py --serial <SERIAL>`
	- Optional: `python scripts/test_pv_string_faults_official.py --serial <SERIAL> --warn-pct 30`
	- Optional raw payload: `python scripts/test_pv_string_faults_official.py --serial <SERIAL> --json`

- `scripts/todays_forecast_both.py`
	- Prints today's ESB and Quartz forecasts side-by-side.
	- Run: `python scripts/todays_forecast_both.py`

## Email Notifications

When the scheduler issues a mode-change command, it sends an email notification with:

- success or failure status
- period/context
- previous mode and requested mode
- decision reason
- local and UTC timestamps
- response payload or error text

Required `.env` variables:

- `EMAIL_SENDER`: Gmail address used to send notifications
- `EMAIL_RECEIVER`: destination email address
- `GMAIL_APP_PASSWORD`: Gmail app password for `EMAIL_SENDER`

For official per-device PV string diagnostics, set one of:

- `SIGEN_INVERTER_SERIAL`: inverter/AIO serial for `test_pv_string_faults_official.py`
- or pass `--serial` directly when running the script

Notes:

- Notifications are sent for real inverter mode-change commands.
- Notifications are also sent in simulation mode command paths.
- In simulation mode, the interaction layer tracks the last simulated mode command so
	follow-up change-back transitions are detected consistently and can generate their own
	simulated command log entries and notification emails.
- If required email env vars are missing, notifications are skipped and scheduler continues.

### Test email notifications

Run this script to trigger a simulated mode-change command and send a test email through the same runtime notification path:

```sh
python scripts/test_mode_change_email.py
```

Optional arguments:

```sh
python scripts/test_mode_change_email.py --mode 1 --period "ManualTest" --reason "Testing email path"
```

## Forecast Accuracy Report

Run:

```sh
python scripts/forecast_vs_actual.py
```

This report compares ESB, Forecast.Solar, Quartz, and measured inverter telemetry by daytime period.

Columns include:

- `ESB Forecast`: ESB period status only (`Red`/`Amber`/`Green`)
- `ForecastSolar`: Forecast.Solar period forecast in kW with `(Forecast / Actual) %` (when available)
- `FS Status`: Forecast.Solar status derived from configured capacity thresholds
- `Quartz kW`: Quartz period forecast in kW with `(Forecast / Actual) %`
- `Quartz Status`: Quartz status derived from configured capacity thresholds
- `Calibrated kW`: ESB kW adjusted by a fitted multiplier from observed telemetry
- `Avg Act kW`: average measured solar for that date/period
- `Actual Basis`: denominator used for measured classification (`Array` or `Inverter`)
- `Actual Reading`: measured status (`Red`/`Amber`/`Green`)
- Per-day summary line: `Day PV total (pvDayNrg): <kWh>` from telemetry

### Status rules used in the report

`ESB Forecast`, `FS Status`, and `Quartz Status` use site-capacity thresholds:

- `Red`: <20% of `SOLAR_PV_KW`
- `Amber`: 20% to <40% of `SOLAR_PV_KW`
- `Green`: >=40% of `SOLAR_PV_KW`

`Actual Reading` is SOC-aware and can switch basis:

- If period max SOC >=99.5%, basis is `Inverter` (`INVERTER_KW`)
- Otherwise basis is `Array` (`SOLAR_PV_KW`)

Measured thresholds by basis:

- `Inverter` basis: `Red` <30%, `Amber` 30% to <60%, `Green` >=60%
- `Array` basis: `Red` <20%, `Amber` 20% to <40%, `Green` >=40%

Clipping-aware promotion:

- If clipping rate >=20% and utilization >=55%, measured status is promoted to `Green`
- Clipping candidates are strict (`sample == 5.5 kW`) and de-noised: if a nearby following sample exceeds `5.5 kW`, the earlier candidate is discarded as non-clipping

### Calibrated kW note

`Calibrated kW` in this report is:

- ESB period kW multiplied by a fitted period multiplier
- fitted multiplier = median of observed `(Avg Actual kW / ESB kW)` for matching periods

This report-fit calibration is intentionally analysis-oriented so you can see whether ESB can be
brought closer to observed generation in each period.

### Provider-vs-actual accuracy comparison

Run:

```sh
python scripts/compare_forecast_accuracy.py
```

What it does:

- Compares ESB, Forecast.Solar, and Quartz against measured inverter telemetry so far
- Uses the latest forecast captured before each period start (`Morn`, `Aftn`, `Eve`)
- Reports status accuracy, MAE, MAPE, and `actual/forecast` bias ratio
- Prints suggested Forecast.Solar multiplier candidates (median and mean)

Notes:

- ESB period watts are synthetic status placeholders, so ESB watt MAE/MAPE should be interpreted cautiously; ESB status accuracy is the more meaningful metric.
- After updating `FORECAST_SOLAR_POWER_MULTIPLIER`, only new scheduler/provider refreshes will reflect the change in future archive snapshots.

### GREEN-GRID forecast comparison (optional)

GREEN-GRID is an alternative solar forecast provider available at https://greengrid.shinyapps.io/greengrid_energy_app/. It uses advanced climate-corrected modeling to produce hourly solar power estimates for Irish locations.

**Setup (Playwright browser automation required):**

```sh
pip install playwright
playwright install chromium
```

**Capturing GREEN-GRID forecasts:**

The `weather/greengrid_forecast.py` module provides a `GreenGridForecast` class that automates the browser interaction to query the app:

```python
import asyncio
from weather.greengrid_forecast import GreenGridForecast

async def main():
    provider = GreenGridForecast()
    forecast = await provider.fetch_forecast(
        eircode="N91 F752",
        direction="SE",
        roof_pitch_degrees=27,
        num_panels=20,
    )
    if forecast:
        print(f"Total kWh forecast: {forecast['total_forecast_kwh']}")

asyncio.run(main())
```

Forecast data is automatically appended to `data/greengrid_forecasts.jsonl` for later analysis. Each entry includes:
- `captured_at`: timestamp of the forecast query
- `forecast_points`: list of `{date, time, forecast_kwh}` hourly values
- `total_forecast_kwh`: sum across all hours
- `inputs`: the query parameters (eircode, direction, roof pitch, panel count)

**Comparing GREEN-GRID vs actuals:**

Run:

```sh
python scripts/compare_greengrid_vs_actuals.py
```

This script:
- Aggregates GREEN-GRID hourly forecasts into project periods (Morn/Aftn/Eve)
- Compares against measured inverter telemetry for the same periods
- Reports status accuracy, MAE, MAPE, and bias ratio
- Suggests a multiplier if significant bias is detected

Example output:

```text
GREEN-GRID   n= 12 status_acc= 75.0% MAE=  850W MAPE= 35.2% actual/forecast median=1.12 mean=1.15
GREEN-GRID multiplier candidates: median x1.12, mean x1.15
```

**Notes:**

- Requires an active inverter telemetry archive (`data/inverter_telemetry.jsonl`).
- Forecast resolution is 1 hour; periods are aggregated as simple averages.
- Status accuracy reflects the Red/Amber/Green classification match.
- If you plan to integrate GREEN-GRID as an active decision provider, capture a few weeks of forecasts first to validate local accuracy and determine if a multiplier is needed.

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
- The official client in `integrations/sigen_official.py` includes fallback auth
	behavior because endpoint and payload acceptance can vary by environment.
- Not all official API endpoints are currently accessible in every tenant/account;
	docs may describe capabilities that your credentials cannot invoke.

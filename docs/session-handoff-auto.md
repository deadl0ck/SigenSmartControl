# Session Handoff (Auto)

_Last updated: 2026-04-23 18:19:48 IST_

## Snapshot

- Branch: ClaudeV1
- HEAD: da67537 Fix crash when SOC is None in mid-period clipping check (23 hours ago)

## Working Tree

- Status: dirty

### Changed files

-  M .github/copilot-instructions.md
-  M data/forecast_solar_readings.jsonl
-  M data/mode_change_events.jsonl
-  M integrations/sigen_interaction.py
-  M logic/schedule_utils.py
-  M main.py
-  D requirements.txt
-  M scripts/mode_sanity_check.py
-  M scripts/test_legacy_api.py
-  M scripts/test_mode_switch_official.py
-  M tests/test_main.py
-  M weather/forecast.py
- ?? .github/prompts/
- ?? .github/skills/
- ?? .monitor.pid
- ?? CLAUDE.md
- ?? docs/session-handoff-auto.md
- ?? docs/session-handoff-auto.md.tmp.i4l9US
- ?? docs/session-handoff.md
- ?? logic/afternoon.py
- ?? logic/evening.py
- ?? logic/inverter_control.py
- ?? logic/mode_logging.py
- ?? logic/morning.py
- ?? logic/night.py
- ?? logic/timed_export.py
- ?? monitor.log
- ?? notifications/
- ?? resume_last_session.txt
- ?? scripts/install_handoff_timer.sh
- ?? scripts/update_handoff_snapshot.sh
- ?? start_venv.sh
- ?? utils/
- ?? weather/providers/

## Recent commits

- da67537 Fix crash when SOC is None in mid-period clipping check
- c6664f5 Add restart_monitor.sh and document monitor scripts in README
- bfca327 Set headroom target to 50% battery capacity (12 kWh)
- 23eaedc Lower clipping/high-SOC export trigger to 50 percent
- 85cf679 Add robust monitor start/stop scripts

## Suggested resume command

```bash
cd /home/martin/git/SigenSmartControl
git status
cat docs/session-handoff.md
cat docs/session-handoff-auto.md
```

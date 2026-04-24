# Modularization Refactoring Plan

Goal: Extract functionality from `main.py` (currently 1,079 lines) into focused modules to hit the 500-line limit and improve testability.

## Overview

The refactoring moves 12+ nested functions and ~15 state variables from `run_scheduler()` in `main.py` into:
1. **`logic/scheduler_state.py`** — Centralized state management (SchedulerState dataclass)
2. **`logic/scheduler_operations.py`** — Core operations (fetch_soc, archive_telemetry, sample_solar, etc.)
3. **`logic/scheduler_coordinator.py`** — Main loop orchestration (the while loop logic)
4. **`main.py`** — Thin entry point (~100 lines)

## Sessions & Checkpoints

### ✅ Session 1: Create SchedulerState dataclass
**Filename:** `logic/scheduler_state.py`  
**Goal:** Move all mutable state variables into one dataclass for clarity.

**What to do:**
1. Create `logic/scheduler_state.py` with a `SchedulerState` dataclass containing:
   - Forecast state: `today_period_windows`, `tomorrow_period_windows`, `today_period_forecast`, `tomorrow_period_forecast`
   - Sunrise/sunset: `today_sunrise_utc`, `today_sunset_utc`, `tomorrow_sunrise_utc`
   - Daily tracking: `day_state`, `night_state`, `current_date`
   - Auth state: `auth_refreshed_for_date`, `refresh_auth_on_wake`
   - Solar sampling: `live_solar_kw_samples` (deque)
   - Forecasts: `forecast_calibration`, `last_forecast_refresh_utc`
   - Archive cooldown: `forecast_solar_archive_cooldown_until_utc`, `last_forecast_solar_archive_utc`
   - Timed export: `timed_export_override`
   - Tick counters: `tick_mode_change_attempts`, `tick_mode_change_successes`, `tick_mode_change_failures`
   - Sleep override: `sleep_override_seconds`

2. Add type hints and docstrings (Google style).

**Acceptance criteria:**
- File created at `logic/scheduler_state.py`
- SchedulerState class has all 15+ fields with type hints
- All fields documented
- Can run tests: `python -m pytest tests/` (should still pass)

**References:**
- Current state variables: `main.py` lines 487–513

---

### ✅ Session 2: Extract `_refresh_daily_data()`
**Filename:** `logic/scheduler_operations.py` (new file)  
**Goal:** Move forecast/sunrise refresh logic out of main loop.

**What to do:**
1. Create `logic/scheduler_operations.py`
2. Extract `refresh_daily_data()` from `main.py` (lines 574–640) into module-level async function:
   ```python
   async def refresh_daily_data(
       state: SchedulerState,
       sigen: SigenInteraction | None,
       logger: logging.Logger,
   ) -> None:
       """Fetch and cache solar forecast and sunrise/sunset times..."""
   ```
3. Replace all `nonlocal` assignments with `state.field = value`
4. Add imports at top of file.

**Acceptance criteria:**
- Function moved to `logic/scheduler_operations.py`
- Takes `state` object instead of using nonlocal
- All existing unit tests still pass
- Docstring intact and references are clear

**References:**
- Current function: `main.py` lines 574–640
- Uses: `LATITUDE`, `LONGITUDE`, `current_date` tracking

---

### ✅ Session 3: Extract `fetch_soc()`, `sample_live_solar_power()`, `get_live_solar_average_kw()`
**Filename:** `logic/scheduler_operations.py` (additions)  
**Goal:** Add battery/solar telemetry operations to module.

**What to do:**
1. Add to `logic/scheduler_operations.py`:
   - `fetch_soc()` (lines 642–672 from main.py)
   - `sample_live_solar_power()` (lines 736–744)
   - `get_live_solar_average_kw()` (lines 746–748)
   - `get_effective_battery_export_kw()` (lines 750–756)
   - `estimate_solar()` (lines 758–770)

2. Adjust signatures to accept state and use it instead of nonlocal:
   ```python
   async def fetch_soc(state: SchedulerState, period: str, ...) -> float | None:
   async def sample_live_solar_power(state: SchedulerState, now_utc: datetime, ...) -> None:
   def get_live_solar_average_kw(state: SchedulerState) -> float | None:
   ```

3. Move helper imports needed for these functions.

**Acceptance criteria:**
- All 5 functions moved to `logic/scheduler_operations.py`
- Signatures updated to pass `state` instead of using nonlocal
- Tests still pass
- No functionality changes (same logic, just different parameter passing)

**References:**
- `fetch_soc`: `main.py` lines 642–672
- `sample_live_solar_power`: `main.py` lines 736–744
- Solar helpers: `main.py` lines 746–770

---

### ✅ Session 4: Extract `archive_inverter_telemetry()`
**Filename:** `logic/scheduler_operations.py` (additions)  
**Goal:** Move telemetry archiving logic.

**What to do:**
1. Add `archive_inverter_telemetry()` to `logic/scheduler_operations.py` (from main.py lines 674–734)
2. Signature:
   ```python
   async def archive_inverter_telemetry(
       state: SchedulerState,
       reason: str,
       now_utc: datetime,
       sigen: SigenInteraction | None,
       logger: logging.Logger,
   ) -> None:
   ```

**Acceptance criteria:**
- Function moved and refactored to use state parameter
- All imports present
- Tests pass
- Docstring intact

**References:**
- Current function: `main.py` lines 674–734

---

### ✅ Session 5: Extract mode-change tracking wrapper
**Filename:** `logic/scheduler_operations.py` (additions)  
**Goal:** Add telemetry counters for mode changes.

**What to do:**
1. Add to `logic/scheduler_operations.py`:
   ```python
   async def apply_mode_change_tracked(
       state: SchedulerState,
       sigen: SigenInteraction,
       today_period_forecast: dict[str, tuple[int, str]],
       **kwargs
   ) -> bool:
       """Apply mode change and record per-tick counters."""
   ```
2. Wraps existing `apply_mode_change()` from main.py (lines 311–355)
3. Updates `state.tick_mode_change_*` counters

**Acceptance criteria:**
- Function added to `logic/scheduler_operations.py`
- Calls `apply_mode_change()` from `main.py`
- Increments state counters correctly
- Tests pass

**References:**
- Current code: `main.py` lines 515–525
- Calls: `apply_mode_change()` at line 311

---

### ✅ Session 6: Create timed-export wrapper helpers
**Filename:** `logic/scheduler_operations.py` (additions)  
**Goal:** Simplify timed-export delegation.

**What to do:**
1. Add to `logic/scheduler_operations.py`:
   ```python
   async def start_timed_grid_export_wrapper(
       state: SchedulerState,
       sigen: SigenInteraction,
       mode_names: dict[int, str],
       apply_mode_change_fn,
       logger: logging.Logger,
       **kwargs
   ) -> bool:
       """Delegate to timed_export.start_timed_grid_export."""

   async def maybe_restore_timed_grid_export_wrapper(
       state: SchedulerState,
       now_utc: datetime,
       sigen: SigenInteraction,
       mode_names: dict[int, str],
       fetch_soc_fn,
       apply_mode_change_fn,
       logger: logging.Logger,
   ) -> str:
       """Delegate to timed_export.maybe_restore."""
   ```
2. These replace lines 533–572 in main.py (the nested functions)

**Acceptance criteria:**
- Both wrapper functions added
- Signatures take state instead of using nonlocal
- Pass through to existing `logic/timed_export.py` helpers
- Tests pass

**References:**
- Current nested functions: `main.py` lines 533–572

---

### ✅ Session 7: Create SchedulerCoordinator class
**Filename:** `logic/scheduler_coordinator.py` (new file)  
**Goal:** Orchestrate the main loop.

**What to do:**
1. Create `logic/scheduler_coordinator.py`
2. Add class:
   ```python
   class SchedulerCoordinator:
       def __init__(
           self,
           state: SchedulerState,
           sigen: SigenInteraction | None,
           mode_names: dict[int, str],
           logger: logging.Logger,
       ):
           self.state = state
           self.sigen = sigen
           self.mode_names = mode_names
           self.logger = logger

       async def run_main_loop(self) -> None:
           """Main scheduling loop (while True)."""
           # Copy the entire while True: block from main.py (lines 820–end)
   ```
3. Move the entire `while True:` loop (lines 820 onwards in main.py) into this method
4. Replace all state accesses like `today_period_windows` with `self.state.today_period_windows`
5. Replace nested function calls with calls to imported functions from `logic.scheduler_operations`

**Acceptance criteria:**
- Class created with `run_main_loop()` method
- All loop logic preserved (no behavioral changes)
- References to operations module are correct
- Tests pass

**References:**
- Loop logic: `main.py` lines 820–1078 (essentially rest of file)

---

### ✅ Session 8: Refactor main.py to thin entry point
**Filename:** `main.py`  
**Goal:** Reduce main.py to ~100 lines as entry point.

**What to do:**
1. Delete all the old nested function definitions (lines 108–797)
2. Delete the old `run_scheduler()` function body (lines 431–1078)
3. Keep only:
   - Module docstring (lines 1–7)
   - Imports (lines 9–95)
   - Logging configuration (lines 98–106)
   - New `run_scheduler()` that just:
     ```python
     async def run_scheduler() -> None:
         """Entry point for scheduler."""
         # Initialize SchedulerState
         state = SchedulerState(...)
         
         # Initialize sigen client
         sigen = await create_scheduler_interaction(mode_names)
         
         # Create coordinator and run
         coordinator = SchedulerCoordinator(state, sigen, mode_names, logger)
         await coordinator.run_main_loop()
     ```
   - `if __name__ == "__main__":` block (end of file)

4. Update imports to include new modules:
   ```python
   from logic.scheduler_state import SchedulerState
   from logic.scheduler_coordinator import SchedulerCoordinator
   ```

**Acceptance criteria:**
- main.py is < 200 lines (closer to 100)
- All tests pass
- Scheduler still runs (manual test with `python main.py`)
- No functional behavior changed

**References:**
- Current main.py: all of it

---

## Testing Strategy

After each session:
1. Run: `python -m pytest -q` (all tests must pass)
2. Run: `python main.py` for 1 tick in foreground (dry-run mode) to verify no crashes
3. If test failures occur, roll back and ask for help in next session

## Order & Dependencies

Sessions are ordered so each depends on prior work:
1. SchedulerState (no deps)
2. refresh_daily_data (uses SchedulerState)
3. fetch_soc, sample_solar, etc. (use SchedulerState)
4. archive_telemetry (uses SchedulerState)
5. mode_change_tracking (uses SchedulerState)
6. timed_export wrappers (use SchedulerState)
7. SchedulerCoordinator (uses all of above)
8. main.py refactor (uses SchedulerCoordinator)

**Can skip ahead if desired**, but previous work must exist for later sessions to work.

## Tips for Each Session

- Copy/paste the function signature and docstring exactly as is (don't rewrite)
- Use `state.field_name` instead of any `nonlocal` references
- Keep `logger` and `sigen` as parameters rather than accessing via state
- When in doubt about whether something should go in state vs passed as param, ask for clarification

---

## Rollback

If something breaks in a session:
1. `git diff` to see changes
2. If confident, revert with `git checkout <file>`
3. Save error message and ask for help in next session

Good luck! Start with Session 1 whenever ready.

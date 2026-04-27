# Code Review Task List

Work through these one at a time in a fresh Claude session each time.
Each task lists only the files you need to read — keep context lean.
After each task: run `python -m pytest -q` to verify nothing broke.
Check off tasks with `[x]` as you complete them.

---

## Task 1 — Standardise `*_safety` variable naming in period handlers

**Why:** `morning.py`, `afternoon.py`, `evening.py` use `soc_safety`,
`solar_avg_kw_3_safety`, `headroom_kwh_safety` etc. for the mid-period safety
check, while pre-period and period-start checks use plain names. The `_safety`
suffix is ambiguous — it is neither a type qualifier nor a conventional Python
naming pattern, and it obscures which branch of logic the variable belongs to.

**Files to read:**
- `logic/morning.py`
- `logic/afternoon.py`
- `logic/evening.py`

**What to do:**
Rename all `*_safety` variables to `mid_period_*` across all three files, e.g.:
- `soc_safety` → `mid_period_soc`
- `solar_avg_kw_3_safety` → `mid_period_solar_kw`
- `headroom_kwh_safety` → `mid_period_headroom_kwh`

Search each file for any remaining `_safety`-suffixed names and rename them
consistently.

**Acceptance criteria:**
- No `*_safety` variable names remain in any period handler file
- `python -m pytest -q` passes

- [x] Done

---

## Task 2 — Bundle period handler parameters into a dataclass

**Why:** `handle_morning_period()`, `handle_afternoon_period()`, and
`handle_evening_period()` each accept 15+ keyword arguments. Call sites in
`scheduler_coordinator.py` construct a `shared_kwargs` dict just to pass them all.
Adding a new parameter requires touching four files, and forgetting one silently
falls back to a default or raises a `TypeError` at runtime.

**Files to read:**
- `logic/morning.py`
- `logic/afternoon.py`
- `logic/evening.py`
- `logic/period_handler_shared.py`
- `logic/scheduler_coordinator.py`

**What to do:**
1. Define a `PeriodHandlerContext` dataclass in `logic/period_handler_shared.py`
   containing all shared kwargs:
   `now_utc`, `period_start`, `period_end_utc`, `period_state`,
   `timed_export_override`, `solar_value`, `status`, `period_solar_kwh`,
   `period_calibration`, `fetch_soc`, `get_live_solar_average_kw`,
   `get_effective_battery_export_kw`, `start_timed_grid_export`,
   `apply_mode_change`, `sigen`, `mode_names`.
2. Change `handle_morning_period`, `handle_afternoon_period`, and
   `handle_evening_period` to accept a single `ctx: PeriodHandlerContext`
   parameter instead of individual kwargs.
3. Update the `_process_period_windows` call site in `scheduler_coordinator.py`
   to construct one `PeriodHandlerContext` and pass it.

**Acceptance criteria:**
- Each handler function signature is `async def handle_*_period(ctx: PeriodHandlerContext) -> bool`
- `shared_kwargs` dict is gone from `scheduler_coordinator.py`
- `python -m pytest -q` passes

- [x] Done

---

## Task 3 — Use `__name__` for logger names instead of hardcoded `"sigen_control"`

**Why:** Every module does `logging.getLogger("sigen_control")`, which routes all
log output through a single root logger. This prevents per-module log level control
and makes it impossible to selectively silence or capture logs from a single module
in tests. The standard Python convention is `logging.getLogger(__name__)`, which
gives each module its own logger in the hierarchy (e.g. `logic.morning`,
`logic.timed_export`).

**Files to read:**
- Run `grep -rn 'getLogger("sigen_control")' .` to find all affected files.
- `main.py` (should keep `"sigen_control"` as the application root logger name)

**What to do:**
Replace `logging.getLogger("sigen_control")` with `logging.getLogger(__name__)`
in every module-level logger assignment **except `main.py`**. `main.py` should
keep the `"sigen_control"` name because it is the root logger that logging
configuration targets.

**Acceptance criteria:**
- No module outside `main.py` uses `"sigen_control"` as a logger name
- `python -m pytest -q` passes

- [x] Done

---

## Task 4 — Fix inconsistent `Period` enum casing

**Why:** `Period.NIGHT = "NIGHT"` (all-caps) but `Period.MORN = "Morn"`,
`Period.AFTN = "Aftn"`, `Period.EVE = "Eve"` (title case). The inconsistency
means any code that switches on `period.value` or compares against a string
literal must know which casing to use for each member. Dict keys in settings
(e.g. `FORECAST_TO_MODE`) may also be affected.

**Files to read:**
- `config/enums.py`
- `config/settings.py` (search for `"NIGHT"` as a dict key)
- Run `grep -rn '"NIGHT"' .` to find all hardcoded usages

**What to do:**
1. Decide on title case (`"Night"`) to match the other three values.
2. Change `Period.NIGHT = "NIGHT"` → `Period.NIGHT = "Night"` in `config/enums.py`.
3. Update every occurrence of the bare string `"NIGHT"` used as a period name
   (dict keys in settings, comparisons in logic files) to `"Night"` or
   `Period.NIGHT`.
4. Check `FORECAST_TO_MODE` and `PERIOD_TO_MODE` dicts in `config/settings.py`
   for `"NIGHT"` keys and update them.

**Acceptance criteria:**
- All `Period` enum values use title-case strings
- `grep -rn '"NIGHT"' .` returns no results outside comments/docs
- `python -m pytest -q` passes

- [x] Done

---

## Task 5 — Add tests for `_candidate_score()` and `_collect_numeric_fields()` edge cases

**Why:** `telemetry_archive.py` has complex field-name scoring logic
(`_candidate_score`) and recursive field collection (`_collect_numeric_fields`)
with a `max_depth` guard. These are the critical path for extracting live solar
power and battery SOC from inverter payloads. If the inverter returns an
unexpected payload shape the fallback scoring logic is entirely untested.

**Files to read:**
- `telemetry/telemetry_archive.py` (the three private functions and `find_best_metric_value`)
- `tests/conftest.py` (existing fixture patterns)

**What to do:**
Create `tests/test_telemetry_archive.py` with tests covering:
- `_candidate_score`: exact leaf match returns 100
- `_candidate_score`: partial leaf match returns 80
- `_candidate_score`: match only in joined path returns 60
- `_candidate_score`: no match returns 0
- `_candidate_score`: underscore normalisation works
  (e.g. `"pv_power"` candidate matches `"pvPower"` leaf)
- `_collect_numeric_fields`: basic nested dict extraction returns correct paths and values
- `_collect_numeric_fields`: `max_depth` guard stops recursion and returns empty at depth > 10
- `_collect_numeric_fields`: non-numeric values (strings, bools) are excluded
- Full `_extract_numeric_metric` integration with a realistic energy_flow payload shape
  that has `pvPower` nested under a `data` key

**Acceptance criteria:**
- `tests/test_telemetry_archive.py` has ≥ 8 test cases
- `python -m pytest -q` passes

- [x] Done

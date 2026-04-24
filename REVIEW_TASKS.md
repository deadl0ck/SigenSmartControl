# Code Review Task List

Work through these one at a time in a fresh Claude session each time.
Each task lists only the files you need to read — keep context lean.
After each task: run `python -m pytest -q` to verify nothing broke.
Check off tasks with `[x]` as you complete them.

---

## Task 1 — Fix unbounded deque in SchedulerState

**Why:** `SchedulerState.live_solar_kw_samples` is declared with `deque(maxlen=None)` (unlimited),
but `main.py` immediately reinitialises it with `maxlen=LIVE_SOLAR_AVERAGE_SAMPLE_COUNT` (3).
The dataclass default is dead code and will cause silent memory growth in any code path that
skips the main.py reinitialisation — including tests.

**Files to read:**
- `logic/scheduler_state.py` (line ~61)
- `main.py` (search for `live_solar_kw_samples`)
- `config/settings.py` (for `LIVE_SOLAR_AVERAGE_SAMPLE_COUNT`)

**What to do:**
1. In `scheduler_state.py`, import `LIVE_SOLAR_AVERAGE_SAMPLE_COUNT` from `config.settings`.
2. Change the default factory from `deque(maxlen=None)` to
   `deque(maxlen=LIVE_SOLAR_AVERAGE_SAMPLE_COUNT)`.
3. Remove the redundant reinitialisation line in `main.py`.
4. Update the docstring to document the fixed capacity.

**Acceptance criteria:**
- `SchedulerState()` produces a bounded deque without any extra initialisation in `main.py`
- `python -m pytest -q` passes

- [ ] Done

---

## Task 2 — Consolidate shared `_log_check()` across period handler files

**Why:** `_log_check()` is defined identically in `morning.py`, `afternoon.py`, `evening.py`, and
`night.py` — roughly 250 lines of duplicated logging code. Improving the format or adding a field
requires changes in four places.

**Files to read:**
- `logic/morning.py` (find `_log_check`)
- `logic/afternoon.py` (same)
- `logic/evening.py` (same)
- `logic/night.py` (same)

**What to do:**
1. Create `logic/decision_logging.py` with a single `log_decision_checkpoint()` function whose
   body is taken verbatim from one of the four copies (they are identical).
2. Delete the four `_log_check()` definitions and replace every call site with
   `log_decision_checkpoint(...)`.
3. Adjust the parameter name from `_log_check`-style to match the new function signature.

**Acceptance criteria:**
- `_log_check` does not appear anywhere in the codebase
- `logic/decision_logging.py` is the single source of the logging logic
- Log output unchanged
- `python -m pytest -q` passes

- [ ] Done

---

## Task 3 — Extract shared period-handler logic to eliminate duplication across morning/afternoon/evening

**Why:** `logic/morning.py` (588 lines), `logic/afternoon.py` (588 lines), and
`logic/evening.py` (689 lines) are structurally near-identical. They each duplicate:
- `_promote_status_for_live_clipping_risk()`
- `_evaluate_period_mode_decision()`
- The same pre-period / period-start control flow

A bug fix or feature addition must be replicated across all three files. Evening has small
additional logic (pre-cheap-rate bridge) but the core flow is the same.

**Files to read:**
- `logic/morning.py`
- `logic/afternoon.py`
- `logic/evening.py`
- `logic/scheduler_coordinator.py` (where handlers are called)

**What to do:**
1. Create `logic/period_handler_shared.py` containing:
   - `promote_status_for_live_clipping_risk()` — taken from morning.py
   - `evaluate_period_mode_decision()` — taken from morning.py
   - A generic `handle_daytime_period()` async function that encapsulates the shared
     pre-period / period-start control flow, taking `period: str` as a parameter.
2. Refactor `morning.py` and `afternoon.py` to thin wrapper modules (~20 lines each) that
   call `handle_daytime_period("Morn")` / `handle_daytime_period("Aftn")`.
3. Refactor `evening.py` similarly, keeping its additional evening-specific bridge logic as
   a post-hook or extra branch inside `handle_daytime_period()` guarded by `period == "Eve"`.
4. Update imports in `scheduler_coordinator.py` if needed.

**Acceptance criteria:**
- `morning.py` and `afternoon.py` are each ≤ 40 lines
- `evening.py` is ≤ 100 lines (evening-specific logic only)
- `period_handler_shared.py` is ≤ 400 lines
- No duplicated helper functions remain across the three period files
- `python -m pytest -q` passes

- [ ] Done

---

## Task 4 — Tighten type hints on `SchedulerState` fields

**Why:** Several `SchedulerState` fields use imprecise types:
- `ordered_period_windows: list` — should be `list[tuple[str, datetime]]`
- `day_state: dict[str, dict[str, bool]]` — inner dict shape (`pre_set`, `start_set`,
  `clipping_export_set`) is undocumented
- `night_state: dict[str, Any]` — accepts any dict; callers access known keys without safety

Static analysis and IDE autocomplete cannot help developers catch misuse.

**Files to read:**
- `logic/scheduler_state.py`
- `logic/scheduler_coordinator.py` (to see how day_state and night_state are accessed)

**What to do:**
1. Define `TypedDict` classes near the top of `scheduler_state.py`:
   ```python
   class DayStateEntry(TypedDict):
       pre_set: bool
       start_set: bool
       clipping_export_set: bool

   class NightState(TypedDict, total=False):
       mode_set_key: tuple[date, int] | None
       sleep_snapshot_for_date: date | None
   ```
2. Change field types:
   - `ordered_period_windows: list` → `list[tuple[str, datetime]]`
   - `day_state: dict[str, dict[str, bool]]` → `dict[str, DayStateEntry]`
   - `night_state: dict[str, Any]` → `NightState`
3. Fix any mypy errors at call sites.

**Acceptance criteria:**
- All `SchedulerState` fields have specific non-`Any` types
- No bare `dict[str, Any]` for structured state
- `python -m pytest -q` passes

- [ ] Done

---

## Task 5 — Make `current_date` a required field on `SchedulerState`

**Why:** `SchedulerState.current_date` is declared `datetime | None = None`, but
`scheduler_operations.refresh_daily_data()` immediately checks `if state.current_date is None:
raise RuntimeError()`. The None check is a runtime guard against incorrect usage, not a genuine
optional state. Making it a required positional field removes an impossible error path and
documents the precondition at the call site.

**Files to read:**
- `logic/scheduler_state.py`
- `logic/scheduler_operations.py` (search for `current_date`)
- `main.py` (where `SchedulerState` is instantiated)

**What to do:**
1. In `scheduler_state.py`, remove `= None` from `current_date` making it a required field.
   Move it before any fields that have defaults (dataclass rules require this).
2. In `main.py`, pass `current_date=now.date()` when constructing `SchedulerState`.
3. Remove the `if state.current_date is None: raise RuntimeError()` guard in
   `refresh_daily_data()` — it is now unreachable.
4. Update the docstring on `SchedulerState` to document the field.

**Acceptance criteria:**
- `SchedulerState()` without `current_date` raises a `TypeError` (missing required arg)
- `RuntimeError` guard in `refresh_daily_data()` deleted
- `python -m pytest -q` passes

- [ ] Done

---

## Task 6 — Add explicit Protocol types for `apply_mode_change` callbacks

**Why:** `logic/inverter_control.py` defines `ModeChangeNotifier = Callable[..., Awaitable[None]]`.
The ellipsis hides which keyword arguments are actually passed (success, period, reason,
requested_mode, etc.). Callers and test mocks must read the implementation to learn the contract.
A wrong keyword in a mock silently passes at definition time.

**Files to read:**
- `logic/inverter_control.py` (lines ~25–50 and the apply_mode_change call sites inside)
- `logic/mode_change.py`
- `main.py` (where the callback is wired up)

**What to do:**
1. In `logic/inverter_control.py`, define an explicit `Protocol`:
   ```python
   class ModeChangeNotifier(Protocol):
       async def __call__(
           self,
           *,
           success: bool,
           period: str,
           reason: str,
           requested_mode: int,
           requested_mode_label: str,
           current_mode_raw: Any,
           mode_names: dict[int, str],
           event_time_utc: datetime,
           battery_soc: float | None,
           solar_generated_today_kwh: float | None,
           today_period_forecast: dict[str, tuple[int, str]] | None,
           response: Any | None = None,
           error: str | None = None,
       ) -> None: ...
   ```
2. Replace the `Callable[..., Awaitable[None]]` alias with the Protocol.
3. Add `-> bool` return type annotation to `apply_mode_change`.
4. Fix any call sites that don't match the Protocol signature.

**Acceptance criteria:**
- `ModeChangeNotifier` is a `Protocol` with explicit keyword arguments
- `apply_mode_change` has a `-> bool` return type
- `python -m pytest -q` passes

- [ ] Done

---

## Task 7 — Document the timed export state machine

**Why:** `logic/timed_export.py` implements a multi-state override machine (inactive → active →
restored → inactive) that is central to the scheduler's export decisions. The state transitions,
return value semantics of `maybe_restore_timed_grid_export()` ("inactive" / "active" /
"restored"), and the precedence of the two SOC-floor checks are not documented anywhere. A
developer maintaining this file must reverse-engineer the state machine from the code.

**Files to read:**
- `logic/timed_export.py` (entire file)
- `main.py` (usage of `maybe_restore_timed_grid_export`)

**What to do:**
1. Add a module-level docstring to `timed_export.py` that documents:
   - The three states and transitions
   - What triggers each transition (time elapsed, SOC floor reached)
   - Precedence of `export_soc_floor` vs `clipping_soc_floor`
2. Expand the docstring of `maybe_restore_timed_grid_export()` to document each return value:
   - `"inactive"` — no override, proceed normally
   - `"active"` — override in force, caller should skip normal decisions
   - `"restored"` — override just ended, caller should skip this tick then resume
3. Add an inline comment above the SOC-floor checks clarifying precedence.

**Acceptance criteria:**
- Module-level docstring describes all states and transitions
- Return value semantics are documented on `maybe_restore_timed_grid_export()`
- A new developer can understand the state machine from documentation alone
- No values or logic changed
- `python -m pytest -q` passes

- [ ] Done

---

## Task 8 — Add tests for period handler state mutations across multiple ticks

**Why:** `handle_morning_period()`, `handle_afternoon_period()`, `handle_evening_period()` contain
complex async control flow that mutates `state.day_state[period]` flags
(`pre_set`, `start_set`, `clipping_export_set`) and `state.timed_export_override`. None of this
mutation logic is covered by tests. A subtle ordering bug (e.g., `start_set` set before
`pre_set`) would only surface at runtime over days.

**Files to read:**
- `logic/morning.py` (or `period_handler_shared.py` after Task 3)
- `logic/scheduler_state.py`
- `tests/conftest.py` (existing fixture patterns)
- `tests/test_scheduler_core_logic.py` (existing integration test style)

**What to do:**
Create `tests/test_period_handler_ticks.py` with at minimum:
- Test that `pre_set` is False initially, True after the pre-period branch runs
- Test that `start_set` is False initially, True after the period-start branch runs
- Test that `clipping_export_set` is True only when SOC and solar thresholds are met
- Test that state is NOT mutated when conditions block the action (e.g., low SOC)
- Test that `start_timed_grid_export` is called at most once per period (guarded by `pre_set`)
- Test that `apply_mode_change` is called at most once per period-start (guarded by `start_set`)

Use mocked async callables for `fetch_soc`, `get_live_solar_average_kw`,
`start_timed_grid_export`, and `apply_mode_change`.

**Acceptance criteria:**
- `tests/test_period_handler_ticks.py` has ≥ 8 test cases
- All named state flags are tested for correct set/unset behaviour
- `python -m pytest -q` passes

- [ ] Done

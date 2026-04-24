# Code Review Task List

Work through these one at a time in a fresh Claude session each time.
Each task lists only the files you need to read — keep context lean.
After each task: run `python -m pytest -q` to verify nothing broke.
Check off tasks with `[x]` as you complete them.

---

## Task 1 — Split `run_main_loop()` into focused handler methods

**Why:** `run_main_loop()` in `scheduler_coordinator.py` is ~310 lines and handles auth refresh,
forecast refresh, archive management, timed-export state, period dispatch, and sleep. It should be
broken into private methods so each responsibility is individually readable and testable.

**Files to read:**
- `logic/scheduler_coordinator.py` (387 lines)

**What to do:**
Extract the following private methods from `run_main_loop()`:
- `_handle_auth_refresh(self, now)` — lines around the auth token refresh block
- `_handle_forecast_refresh(self, now)` — lines around the daily forecast/sunrise refresh
- `_handle_archive(self, now)` — lines around forecast-solar archive with rate-limit logic
- `_check_timed_export_active(self, now) -> bool` — lines checking timed-export override state,
  returns True if a timed export is active (so the caller can skip normal dispatch)
- `_process_period_windows(self, now)` — the per-period pre-period / period-start dispatch loop

`run_main_loop()` should become ~50 lines that calls these methods in sequence.

**Acceptance criteria:**
- Each extracted method is ≤80 lines
- `run_main_loop()` is ≤60 lines
- `python -m pytest -q` passes
- No behaviour change — only structural refactor

- [ ] Done

---

## Task 2 — Remove wrapper functions from `scheduler_operations.py` and fix circular import

**Why:** Three wrapper functions at the bottom of `scheduler_operations.py` (lines 328–486) are
thin pass-throughs that exist only to inject `state` and `logger`. They also contain a late import
`from main import apply_mode_change` to work around a circular dependency. Both problems should be
fixed together.

**Files to read:**
- `logic/scheduler_operations.py` (486 lines)
- `main.py` (511 lines)
- `logic/scheduler_coordinator.py` (387 lines)

**What to do:**

Step A — Fix the circular import:
`apply_mode_change` in `main.py` should move to a new module `logic/mode_change.py`. Both
`main.py` and `scheduler_operations.py` can then import from there without circularity.

Step B — Remove the three wrappers:
`create_apply_mode_change_tracked()`, `start_timed_grid_export_wrapper()`,
`maybe_restore_timed_grid_export_wrapper()` should be deleted. Any call sites in
`scheduler_coordinator.py` should call the underlying functions directly, passing `state` and
`logger` explicitly.

Step C — Check `scheduler_operations.py` length. With the wrappers removed it should be well
under 400 lines.

**Acceptance criteria:**
- No late imports anywhere in the codebase
- `scheduler_operations.py` has no wrapper functions
- `python -m pytest -q` passes

- [ ] Done

---

## Task 3 — Create `DecisionContext` dataclass for `decide_operational_mode()`

**Why:** `decide_operational_mode()` takes 8 optional parameters. Callers need deep knowledge of
the internal rules to call it correctly. Grouping inputs into a dataclass makes the interface
self-documenting and makes tests easier to write.

**Files to read:**
- `logic/decision_logic.py` (197 lines)
- Any test file that calls `decide_operational_mode` (check `tests/`)

**What to do:**
1. Define `DecisionContext` as a `@dataclass` near the top of `decision_logic.py`:
   ```python
   @dataclass
   class DecisionContext:
       period: str
       status: str | None
       soc: float | None
       headroom_kwh: float | None
       headroom_target_kwh: float
       live_solar_kw: float | None
       hours_until_cheap_rate: float | None
       estimated_home_load_kw: float | None
       bridge_battery_reserve_kwh: float | None
       tariff: str | None
   ```
2. Change `decide_operational_mode()` to accept a single `ctx: DecisionContext` argument.
3. Update all call sites to construct and pass a `DecisionContext`.
4. Update tests accordingly.

**Acceptance criteria:**
- `decide_operational_mode()` has exactly one parameter (`ctx: DecisionContext`)
- All call sites updated
- `python -m pytest -q` passes

- [ ] Done

---

## Task 4 — Fix silent failure in `sigen_interaction.py`

**Why:** `get_energy_flow()` returns `{}` on `KeyError` after a retry. Callers cannot distinguish
"empty payload" from "fetch failed", which masks real upstream problems silently.

**Files to read:**
- `integrations/sigen_interaction.py` (242 lines)
- Any callers of `get_energy_flow` (grep for it in `logic/` and `main.py`)

**What to do:**
1. Define a custom exception in `sigen_interaction.py`:
   ```python
   class SigenPayloadError(Exception):
       """Raised when the Sigen API returns a structurally unexpected payload."""
   ```
2. In `get_energy_flow()`, replace `return {}` with `raise SigenPayloadError(...)` on the retry
   failure path.
3. Update callers to catch `SigenPayloadError` explicitly and handle it (log + skip tick, rather
   than silently receiving `{}`).
4. Also: the `_is_missing_data_key_error` check uses `"data" in str(part).lower()` — replace with
   a proper key membership check against the known schema.

**Acceptance criteria:**
- `get_energy_flow()` never returns `{}`
- `SigenPayloadError` is imported and caught at call sites
- `python -m pytest -q` passes

- [ ] Done

---

## Task 5 — Add `Enum` classes for period names and forecast statuses

**Why:** `"MORN"`, `"AFTN"`, `"EVE"`, `"GREEN"`, `"AMBER"`, `"RED"` appear as bare strings
across multiple files. Typos fail silently at runtime.

**Files to read:**
- `config/settings.py` (312 lines)
- `logic/decision_logic.py` (197 lines)
- `logic/scheduler_coordinator.py` (387 lines)

**What to do:**
1. Add to `config/settings.py` (or a new `config/enums.py`):
   ```python
   from enum import Enum

   class Period(str, Enum):
       MORN = "Morn"
       AFTN = "Aftn"
       EVE = "Eve"
       NIGHT = "NIGHT"

   class ForecastStatus(str, Enum):
       RED = "RED"
       AMBER = "AMBER"
       GREEN = "GREEN"
   ```
   Using `str, Enum` means existing string comparisons and dict keys still work during transition.
2. Replace bare string literals in `decision_logic.py` and `scheduler_coordinator.py` with enum
   members.
3. Update `FORECAST_TO_MODE` and other dicts in `settings.py` to use enum keys.

**Acceptance criteria:**
- No bare `"MORN"` / `"AFTN"` / `"EVE"` / `"GREEN"` / `"AMBER"` / `"RED"` string literals
  in decision_logic.py or scheduler_coordinator.py
- `python -m pytest -q` passes

- [ ] Done

---

## Task 6 — Stream JSONL in `forecast_calibration.py` instead of loading all at once

**Why:** `_read_recent_telemetry()` calls `path.read_text().splitlines()`, loading the entire
archive file into memory before filtering. The archive grows unboundedly; this will become slow.

**Files to read:**
- `telemetry/forecast_calibration.py` (279 lines)

**What to do:**
Replace this pattern:
```python
for line in path.read_text(encoding="utf-8").splitlines():
```
with streaming:
```python
with path.open(encoding="utf-8") as f:
    for line in f:
```
The rest of the logic (JSON parse, date filter, append) stays the same.

Also: move the `cutoff_date` check to skip JSON parsing for lines whose prefix timestamp is too
old. The JSONL lines start with `"captured_at"` as the first key — you can check the first ~30
characters of the line before calling `json.loads()` to skip obviously old records cheaply.

**Acceptance criteria:**
- `path.read_text()` is gone from this file
- `python -m pytest -q` passes

- [ ] Done

---

## Task 7 — Deduplicate candidate tuples and mode-name inversion

**Why:** Two copies of the same data exist independently and will silently diverge if one is
updated.

**Files to read:**
- `telemetry/telemetry_archive.py` (358 lines)
- `integrations/sigen_interaction.py` (242 lines)
- `config/settings.py` (312 lines)
- `main.py` — search for `mode_names` (~line 423)

**What to do:**

Part A — Solar metric candidate tuples:
In `telemetry_archive.py`, the candidate tuple `("pvPower", "solarPower", "ppv", "pv", "solar")`
appears at ~line 115 and ~line 204. Extract as module-level constants:
```python
_SOLAR_POWER_CANDIDATES = ("pvPower", "solarPower", "ppv", "pv", "solar")
_SOLAR_GENERATION_CANDIDATES = ("pvDayNrg", "pvDayEnergy", ...)  # full list from line 222
_BATTERY_SOC_CANDIDATES = (...)  # equivalent for SOC metric
```
Replace both occurrences with the constants.

Part B — Mode name inversion:
`{v: k for k, v in SIGEN_MODES.items()}` is computed in both `main.py` (~line 423) and
`sigen_interaction.py` (~line 16). Add `SIGEN_MODE_NAMES = {v: k for k, v in SIGEN_MODES.items()}`
to `config/settings.py` and import it in both places.

**Acceptance criteria:**
- No repeated inline candidate tuples in `telemetry_archive.py`
- `SIGEN_MODE_NAMES` defined once in `config/settings.py`
- `python -m pytest -q` passes

- [ ] Done

---

## Task 8 — Document magic numbers in `settings.py` with physical/economic basis

**Why:** Tuning parameters have no comments explaining why they are set to their current values.
When thresholds need adjusting, there's no baseline reasoning to work from.

**Files to read:**
- `config/settings.py` (312 lines)
- `CLAUDE.md` (for the existing documented rationale of `HEADROOM_TARGET_KWH`)

**What to do:**
Add a one-line comment above each of the following constants explaining the physical or economic
reasoning. Do not change the values — only add comments.

Constants that need documenting:
- `POLL_INTERVAL_MINUTES` — why 5 minutes?
- `MAX_PRE_PERIOD_WINDOW_MINUTES` — why 180?
- `LIVE_SOLAR_AVERAGE_SAMPLE_COUNT` — why 3 samples?
- `MIN_EFFECTIVE_BATTERY_EXPORT_KW` — what does 0.2 kW represent physically?
- `PRE_SUNRISE_DISCHARGE_LEAD_MINUTES` — why 120 minutes before sunrise?
- `PRE_SUNRISE_DISCHARGE_MIN_SOC_PERCENT` — why 65%?
- `QUARTZ_RED_CAPACITY_FRACTION` / `QUARTZ_GREEN_CAPACITY_FRACTION` — basis for 20%/40% thresholds
- `MORNING_HIGH_SOC_THRESHOLD_PERCENT` — why 50%?
- `LIVE_SOLAR_CLIPPING_THRESHOLD_KW` — basis for the clipping risk threshold

If you are not sure of the exact reasoning, use a comment that records what is known and flags the
uncertainty, e.g.:
```python
# 3 samples at 5-min intervals = 15-min average; smooths cloud transients
LIVE_SOLAR_AVERAGE_SAMPLE_COUNT = 3
```

**Acceptance criteria:**
- Every listed constant has a comment
- No values changed
- `python -m pytest -q` passes

- [ ] Done

---

## Task 9 — Optimise `_candidate_score()` in `telemetry_archive.py`

**Why:** String normalization (`replace("_", "")`) is recomputed inside the inner loop for every
candidate. For a hot path called on every telemetry tick, this is unnecessary work.

**Files to read:**
- `telemetry/telemetry_archive.py` (358 lines) — focus on `_candidate_score()` and callers

**What to do:**
In `_candidate_score()`, move `leaf.replace("_", "")` and `joined.replace("_", "")` outside the
`for candidate in candidates` loop. Add `return score` early inside the loop when `score == 100`
(max score — no need to check remaining candidates).

Also in `_collect_numeric_fields()`: the path tuple is extended via `path + (str(key),)` on every
recursion, creating intermediate tuple objects. Refactor to pass a mutable list and convert to
tuple only at the leaf. Add a `max_depth: int = 10` guard parameter and raise or return early if
depth is exceeded.

**Acceptance criteria:**
- Pre-computed strings outside inner loop
- Early exit when score reaches 100
- `max_depth` guard on `_collect_numeric_fields()`
- `python -m pytest -q` passes

- [ ] Done

---

## Task 10 — Simplify duplicate suppression logic in `scheduler_coordinator.py`

**Why:** Near-identical code blocks appear at ~lines 156–172 and ~197–214. They sort
`today_period_windows`, find elapsed periods, and log suppression — differing only in log level.

**Files to read:**
- `logic/scheduler_coordinator.py` (387 lines)

**What to do:**
1. Extract into a single helper method:
   ```python
   def _log_suppressed_periods(
       self, suppressed_periods: list[str], now: datetime, level: str = "info"
   ) -> None:
   ```
2. Replace both duplicate blocks with calls to this method.
3. Also: the `sorted(self.state.today_period_windows.items(), key=lambda item: item[1])` call
   appears 3+ times per loop. Store `ordered_period_windows` as a list on `SchedulerState`,
   populated once when `today_period_windows` is set, and reference it directly.

**Acceptance criteria:**
- Zero duplicate suppression blocks
- Sort computed once and stored in state
- `python -m pytest -q` passes

- [ ] Done

---

## Task 11 — Deduplicate forecast provider construction in `weather/forecast.py`

**Why:** `create_solar_forecast_provider()` has identical `try/except` blocks for each provider
constructor. Adding a new provider requires copy-pasting the same pattern.

**Files to read:**
- `weather/forecast.py` (175 lines)

**What to do:**
Replace the repeated exception-handling blocks with a loop:
```python
provider_classes = [
    (ForecastSolarForecast, "forecast.solar"),
    (QuartzSolarForecast, "quartz"),
]
providers = []
for cls, name in provider_classes:
    try:
        providers.append(cls(logger))
    except Exception as exc:
        logger.warning("[FORECAST-COMPARE] %s unavailable: %s", name, exc)
```
Then wire up the returned list as before.

**Acceptance criteria:**
- No repeated `try/except` blocks for provider construction
- Behaviour identical — unavailable providers are skipped with a warning
- `python -m pytest -q` passes

- [ ] Done

---

## Task 12 — Replace module-state mutation wrappers in `main.py`

**Why:** `_persist_timed_export_override()` and `_load_timed_export_override()` in `main.py`
temporarily reassign `timed_export_module.TIMED_EXPORT_STATE_PATH` to inject a different path.
This is not thread-safe and is confusing to read.

**Files to read:**
- `main.py` (511 lines) — lines 87–109 and their call sites
- `logic/timed_export.py` (if it exists) or wherever `persist_timed_export_override` is defined

**What to do:**
Check whether `persist_timed_export_override` and `load_timed_export_override` accept a `path`
parameter. If they do, pass the path directly and delete the three wrappers. If they do not, add
a `path` parameter to those functions and then delete the wrappers.

**Acceptance criteria:**
- No module-level variable mutation in `main.py`
- Three wrapper functions deleted
- `python -m pytest -q` passes

- [ ] Done

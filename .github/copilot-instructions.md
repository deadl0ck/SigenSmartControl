---
name: "Sigen Inverter Control System — Code Standards"
description: "Use when: writing or refactoring Python code for the Sigen inverter control system. Enforces file organization, documentation, type hints, and maintainability standards."
---

# Sigen Project Code Standards

This document defines the coding practices and preferences for the Sigen inverter control system project. All code contributions should follow these conventions.

## Official API Reference Priority

- For any question or implementation work involving the Sigen official API, consult the markdown files under `.github/reference/Sigen API/API Documentation/` first.
- Treat those files as the source of truth for authentication flows, endpoints, payload/response fields, enums, modes, telemetry signals, and command semantics.
- If code assumptions conflict with the official API docs, call out the mismatch clearly and align implementation with the official docs unless explicitly told otherwise.
- When relevant, also consult `.github/reference/Sigen API/User Manual/` for onboarding and workflow context.

## File Organization & Structure

### File Length
- **Keep Python modules under 500 lines** to maintain readability and navigation speed.
- **Functions should be 50-100 lines max**. Break up longer functions into smaller, testable units.
- **Create separate modules** when a file approaches 400+ lines:
  - Group related functions logically (e.g., tariff calculations, mode control, scheduler logic)
  - Use descriptive module names reflecting their purpose
  - Example: `tariff_utils.py`, `mode_control.py`, `scheduler_loop.py`

### Imports
- **All imports must be at the top of the file**, no exceptions.
- **Group imports in this order:**
  1. Standard library (os, sys, asyncio, datetime, etc.)
  2. Third-party packages (flask, requests, openpyxl, etc.)
  3. Local project imports (from config, from decision_logic, etc.)
- **Separate groups with a blank line**.
- **Do not scatter imports throughout the code** — consolidate mid-module imports at the top.

Example:
```python
import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Any

from flask import Flask
from openpyxl import load_workbook

from config import SOLAR_PV_KW
from decision_logic import decide_operational_mode
```

## Documentation

### Function & Method Documentation
- **Every function must have a docstring** (no exceptions).
- **Use Google-style docstrings:**
  ```python
  def calculate_headroom(battery_kwh: float, soc: float) -> float:
      """Calculate available battery headroom (reserved capacity for charging).
      
      Args:
          battery_kwh: Total battery capacity in kWh.
          soc: Current state-of-charge as a percentage (0-100).
          
      Returns:
          Available headroom in kWh.
      """
  ```
- **Document parameters, return values, and exceptions.**
- **Keep docstrings clear and concise** — imagine explaining this to a colleague.
- **For complex functions, include practical examples** in the docstring.

### Module Documentation
- **Add a module-level docstring at the top of every .py file.**
- **Describe the module's purpose in 2-3 sentences.**
  ```python
  """
  tariff_utils.py
  ---------------
  Time-based tariff calculations and rate window detection.
  Provides helper functions for determining cheap-rate windows,
  tariff periods, and sunrise/sunset-based scheduling.
  """
  ```

### README & User Docs
- **Keep README.md up-to-date** whenever core logic changes.
- **Use simple, clear English** — avoid jargon where possible.
- **Organize into sections:** Installation, Configuration, How It Works, Decision Logic, Examples, Troubleshooting.
- **Include concrete examples** (e.g., "When forecast is Green → device switches to self-powered mode").
- **Document why decisions are made**, not just what they do (e.g., "Evening AI Mode enables profit-max battery arbitrage: sell at 18.5c/kWh, recharge at 13.462c/kWh").

## Type Hints

### Required Type Hints
- **All function parameters** must have type hints.
- **All function return types** must be specified.
- **Use `Optional[T]` or `T | None`** for nullable values.
- **Use `Union[T1, T2]` or `T1 | T2`** for multiple possible types.
- **Import types from `typing`** when needed: `Any`, `Dict`, `List`, `Tuple`, `Protocol`, etc.

Examples:
```python
async def fetch_data(timeout_seconds: int) -> dict[str, Any]:
    """..."""

def parse_mode(raw_mode: Any) -> int | None:
    """..."""

async def process_periods(
    periods: list[str],
    forecasts: dict[str, tuple[int, str]],
) -> dict[str, int]:
    """..."""
```

### Avoid Bare `Any`
- Use `Any` sparingly — only when the type is truly unknown or dynamic.
- Prefer more specific types: `dict[str, Any]`, `str | int`, etc.

## Code Quality Targets

### Simplicity First (Project Preference)
- **Prefer the simplest code that works today.**
- **Do not add speculative parameters or abstractions** for future use.
- If a parameter is not actively used by current behavior and call sites, **remove it**.
- Add complexity only when there is a present, tested requirement.

### Testing
- **Maintain test coverage** — all public functions should have corresponding tests.
- **Run tests frequently:** `python -m pytest -q` before committing.
- **Keep tests passing** — no broken tests in main branch.

### Git Commits
- **Write clear commit messages** describing what changed and why.
- **Reference relevant decision logic or configuration changes** in the message.
- **Commit after completing logical units** — not every 5 lines.

### Linting & Formatting
- **Follow PEP 8** conventions (implicit via type hints and docstrings).
- **Consistent indentation** (4 spaces, no tabs).
- **Line length**: Keep lines under 100 characters when possible (readability on standard terminals).

## Module Responsibilities

When refactoring large files, use this guidance for module separation:

| Module | Responsibility | Example Functions |
|--------|-----------------|-------------------|
| `schedule_utils.py` | Time-based schedule windows, period detection | `is_cheap_rate_window()`, `get_schedule_period_for_time()`, `get_hours_until_cheap_rate()` |
| `mode_control.py` | Mode decision logic, API interaction, mode matching | `apply_mode_change()`, `mode_matches_target()`, `extract_mode_value()` |
| `scheduler_loop.py` | Main scheduler loop, nested helpers | `run_scheduler()` and its internal async helpers |
| `config.py` | All configuration values and environment setup | System specs, tariffs, thresholds, feature flags |
| `decision_logic.py` | Mode decision algorithms | `decide_operational_mode()` |
| `main.py` | Entry points only | `async def run_scheduler()`, `if __name__ == "__main__"` |

## Common Patterns

### Nested Async Helpers
Instead of:
```python
async def run_scheduler():
    async def refresh_daily_data():
        # 100 lines of code
    
    async def fetch_soc():
        # 50 lines of code
    
    # 400+ lines of scheduler loop
    while True:
        ...
```

Consider:
```python
# scheduler_helpers.py
async def refresh_daily_data(...):
    """Refresh forecast and sunrise/sunset data."""

async def fetch_soc(...):
    """Fetch current battery SOC."""

# scheduler_loop.py
from scheduler_helpers import refresh_daily_data, fetch_soc

async def run_scheduler():
    while True:
        await refresh_daily_data(...)
        await fetch_soc(...)
```

This keeps files small and functions testable.

## Enforcement Checklist

Before committing any code:
- [ ] File is ≤ 500 lines
- [ ] All imports are at the top
- [ ] Every function has a docstring
- [ ] Every function has type hints (parameters + return)
- [ ] Tests pass: `pytest -q`
- [ ] README updated if logic changed
- [ ] Commit message explains the change

## Questions or Deviations?

If you need to deviate from these standards (e.g., for performance reasons or external constraints), document the exception clearly in a comment and open a discussion.

## Log analysis policy:
- Always analyze both live logs in data and historical snapshot logs in data/imported-local-2026-04-18.

- Treat snapshot logs as background context only.

- Treat live logs as the current source of truth.

- If they conflict, prioritize live logs and explicitly call out the difference.

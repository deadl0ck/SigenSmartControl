---
mode: ask
description: "Use when: quickly resume this project after VS Code restart, reconnect, or power cut using handoff files and current git state."
---

Resume this project from saved state.

Steps:
1. Read `README.md`, `docs/session-handoff.md`, and `docs/session-handoff-auto.md`.
2. Run `git status --short --branch` and `git log --oneline -5`.
3. Read baseline runtime files: `main.py`, `logic/decision_logic.py`, `logic/mode_control.py`, and `config/settings.py`.
4. Read up to the top 10 relevant changed files from `git status`.
5. If handoff references specific files/symbols, read those targeted files next.
6. Return:
   - Resume state (short paragraph)
   - Top 3 next actions
   - First command or edit to execute now
   - Assumptions or uncertainties

Rules:
- Do not use destructive git commands.
- Do not discard or overwrite uncommitted changes.
- Keep output concise and action-oriented.

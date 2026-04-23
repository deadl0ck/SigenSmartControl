---
name: restart-resume
description: "Use when: resuming this project after VS Code restart, SSH reconnect, or power cut. Reads handoff files, checks git state, and rebuilds coding context before continuing work."
---

# Restart Resume

## Purpose

Restore working context quickly and safely after interruptions.

## Run Triggers

Use this skill when the user asks to:

- resume where we left off
- continue after restart/reconnect
- recover after power loss
- reload project context from handoff

## Required Inputs

- Manual handoff: `docs/session-handoff.md`
- Auto handoff: `docs/session-handoff-auto.md`
- Project overview: `README.md`

## Workflow

1. Verify workspace root and read `README.md`, then read both handoff files.
2. Run a quick repository state check:
   - `git status --short --branch`
   - `git log --oneline -5`
3. Do a baseline code scan of core runtime files:
   - `main.py`
   - `logic/decision_logic.py`
   - `logic/mode_control.py`
   - `config/settings.py`
4. Parse handoff notes for:
   - current objective
   - next actions
   - blockers/risks
5. Read changed files from `git status` (up to the top 10 relevant files) to confirm current in-progress work.
6. If handoff references additional files or symbols, read those targeted files next.
7. Return a concise resume brief:
   - what is complete
   - what is next
   - the first concrete action to execute now

## Output Contract

Always produce:

- `Resume state`: one paragraph summary
- `Top 3 next actions`: numbered list
- `First command`: one command or file edit to start immediately
- `Assumptions`: any uncertainty due to missing handoff details

## Guardrails

- Do not run destructive git commands.
- Do not assume unfinished work is safe to discard.
- Perform the required baseline scan first, then prefer targeted reads.
- Keep startup/recovery responses short and action-oriented.

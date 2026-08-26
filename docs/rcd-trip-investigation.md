# Main Breaker / RCD Trip Investigation

Date recorded: 2026-08-19

## Context

Two EV chargers are in use, both scheduled to charge 23:00–08:00 (cheap-rate window):

- **Zappi** (myenergi) — charges the Tesla. Integration in this repo reads live status only (`integrations/zappi_interaction.py`); never controls it.
- **Granny charger** (portable 3-pin lead, plugged into a socket in the garage) — charges the wife's BYD Seal-U DMi. Not integrated with this project at all; it draws directly from the house mains and only shows up indirectly as extra household load in `data/inverter_telemetry.jsonl` (`loadPower` / `grid_import_kw`), since the Sigen inverter sits downstream of both the granny charger's circuit and the Zappi (per `docs/inverter-install-facts.md`, the Zappi itself is upstream of the inverter's own metering and never appears in `loadPower` at all).

## The device that trips

Identified from board photos:

```
Hager
NBN263A
B63
10kA/15kA
EN 60947-2
```

- Double-pole, **B-curve**, **63A** rated.
- Built to **EN 60947-2** (general/industrial circuit-breaker standard, which has provision for breakers with integrated residual-current protection), not EN 60898-1 (the plain domestic overcurrent-only MCB standard).
- Requires manual physical reset after tripping (no auto-reclose) — located outside, must be walked out to and flipped back by hand.
- This is the **main incomer breaker for the whole board** — not a plain isolator (a true isolator switch would not carry a B/C/D curve designation, since it has no automatic trip mechanism to describe).
- The EN 60947-2 marking + the fact it trips for two distinct kinds of events (see below) both point toward this being a combined overcurrent + integrated-RCD device (essentially the main RCBO for the whole property), rather than a pure overcurrent-only breaker. Not confirmed against the datasheet — worth an electrician verifying directly.

## Observed trip scenarios

**1. Both chargers starting simultaneously at 23:00.**
Combined earth-leakage from both chargers' EMI filters, plus simultaneous inrush from both contactors closing at the same instant, can tip a shared/main earth-leakage device over its threshold even though neither charger alone would.

**2. Stopping the granny charger while the Zappi is still charging.**
Confirmed: the granny charger was stopped via the BYD's own app/screen (the correct, graceful method — not by pulling the plug live), and the trip happened at that moment. Two live theories:
   - The granny charger's own in-cable relay/contactor isn't a zero-crossing type, so even a commanded stop produces a small internal arc/transient — would trip regardless of what else is running.
   - Lack of **discrimination** between the garage's own local RCD (confirmed to exist, feeding a sub-board that connects back to the house via a sub-main) and this main incomer's own earth-leakage sensing, if it has one. Ordinary domestic-grade RCDs have no deliberate time delay, so a downstream transient can trip either device essentially at random depending on manufacturing tolerance.
   - Not yet isolated which of these two it is — the test is to stop the granny charger on its own, with the Zappi off, during the day, and see if it still trips.

**3. Shower + EV charging overlapping (e.g. early-morning shower while a car is still on its charge schedule).**
Most likely a genuine, correct **overcurrent** trip on the 63A main breaker, not a fault. 63A × 230V ≈ 14.5kW total board capacity. An Irish electric shower alone is typically 9.5–10.5kW; add either EV charger and 14.5kW is easily exceeded. This is a supply-capacity question, not a wiring fault.

## Why this project's telemetry couldn't fully pin down trip timing

`data/inverter_telemetry.jsonl` and `data/zappi_telemetry.jsonl` are both written by the scheduler on its poll cycle, but the scheduler deliberately sleeps for long stretches overnight once night/TOU mode is applied (see `logic/night.py` — logged as `"Night sleep mode active. Sleeping for N minutes until <next critical milestone>"`). On the nights checked, this leaves multi-hour gaps (e.g. 23:00 → ~03:08–03:16 local) with no samples at all, so an exact trip timestamp can't be recovered from this data — only bounded before/after the gap. The monitor's own wake-up behavior after a gap (forced re-auth) is routine and is not, by itself, evidence of a power interruption.

## Recommended next steps (for an electrician)

1. Confirm whether the Hager NBN263A actually has integrated residual-current (earth-leakage) protection, or is overcurrent-only.
2. If it does: check discrimination/selectivity between this main breaker and the garage sub-board's own RCD. Standard fix is normally a time-delayed/selective ("S-type") device upstream, so downstream faults clear locally first.
3. Isolation test (can be done without an electrician): stop the granny charger on its own, Zappi off, during the day. Trips regardless → granny charger's own relay. Only trips when Zappi is also running → points at discrimination/shared-neutral between the two circuits.
4. For the shower/charging overlap: either avoid scheduling a shower during 23:00–08:00, or ask about upgrading main supply capacity now that two EV chargers are in regular use.
5. Do **not** add a generic plug-in RCD to "fix" the granny charger — it sits in the same current path as the suspected fault, won't address either root cause, and (being a plain 30mA Type AC device with no DC-leakage immunity) is not compliant EV-charging protection under I.S. 10101 anyway.

## Change log

- 2026-08-19: Initial record created from RCD/breaker-tripping investigation.

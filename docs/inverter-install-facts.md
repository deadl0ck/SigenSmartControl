# Inverter Install Facts (Confirmed)

Date recorded: 2026-04-04

## Confirmed site setup

- Export to grid is enabled for this installation.
- CT direction is correct.
- Meter/CT wiring orientation is correct.

## Power and tariff interpretation

- The inverter AC output limit is defined by `INVERTER_KW` in `config.py`.
- With low house load and no battery charging, export can approach the inverter AC ceiling.
- The sell/export tariff assumption used by this project is `SELL_RATE_CENTS_PER_KWH` in `config.py`.

## Telemetry interpretation used in this repo

- Raw net grid exchange comes from `energy_flow.buySellPower`.
- Sign convention used in this repo:
  - Positive = export to grid
  - Negative = import from grid
- Derived fields in telemetry processing now include:
  - `grid_exchange_kw`
  - `grid_export_kw`
  - `grid_import_kw`

## Why this note exists

This file captures physical-install facts that should be treated as true unless hardware or installer settings change.

## Change log

- 2026-04-04: Initial record created.
- 2026-04-04: Confirmed export enabled, CT direction correct, and meter/CT wiring orientation correct.
- 2026-04-04: Confirmed tariff assumption uses `SELL_RATE_CENTS_PER_KWH` in `config.py`.
- 2026-04-04: Confirmed telemetry grid exchange field is `energy_flow.buySellPower` (positive export, negative import).

## Update template

When anything changes in hardware, installer settings, or interpretation, append one line in this format:

- YYYY-MM-DD: What changed, why it changed, and who confirmed it.

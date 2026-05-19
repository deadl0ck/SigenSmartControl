# SigenStor Battery — Specification & Warranty Notes

## Hardware

**3 × SigenStor BAT 8.0** (total system: 24.18 kWh capacity, 23.4 kWh usable)

### Performance Specification (BAT 8.0)

| Parameter | Value |
|---|---|
| Battery chemistry | **LiFePO4 (LFP)** |
| Total energy capacity | 8.06 kWh |
| Usable energy capacity | 7.8 kWh |
| Max charge / discharge power | 4,000 W |
| Peak charge / discharge power (10 s) | 6,000 W |
| Operating temperature range | −20 °C to 55 °C |
| Recommended operating temperature | 15 °C to 30 °C |
| Ingress protection | IP66 |
| Cooling | Natural convection |
| Weight | 70 kg per unit |
| Dimensions (W/H/D) | 767 / 270 / 260 mm |

Test conditions (for rated figures): 100% depth of discharge, 0.2C rate charge/discharge, 25 °C.

### 3-Unit Stack (installed configuration)

| Units | Usable capacity | Max charge/discharge |
|---|---|---|
| 3 × BAT 8.0 | 23.4 kWh | 12 kW |

---

## Factory Limited Warranty (Europe)

Source: Sigenergy Technology Co., Ltd. — Factory Limited Warranty for SigenStor (Europe), dated 14 May 2024.

### Product warranty period

| Product | Warranty period |
|---|---|
| Sigen Battery | 10 years |
| Sigen Energy Controller | 10 years |
| Sigen EV DC Charging Module | 3 years |

Warranty commences on the earlier of: (i) installation/activation/registration date, or (ii) date of retailer's invoice or delivery note. If neither is determinable, 6 months after manufacture date.

### Performance warranty — the critical constraint

Sigenergy warrants the battery retains **70% of usable energy** for **10 years OR until the Minimum Through Output Energy is reached — whichever comes first**.

| Product | Usable energy | **Minimum Through Output Energy (warranty cap)** |
|---|---|---|
| Sigen Battery 5 kWh | 5.2 kWh | 15.85 MWh per unit |
| **Sigen Battery 8 kWh** | **7.8 kWh** | **23.77 MWh per unit** |

**For our 3-unit installation: 3 × 23.77 = 71.31 MWh total throughput cap.**

Once cumulative discharge reaches this figure the performance warranty expires, regardless of age.

### What this means for daily cycling

To stay within the 10-year warranty window the battery must average no more than:

```
71,310 kWh ÷ (10 × 365 days) = 19.5 kWh/day discharge (all 3 units combined)
                              =  6.5 kWh/day per unit
                              =  0.83 equivalent full cycles/day per unit
```

The warranty cap is equivalent to **3,047 full discharge cycles per unit** over the warranty period.

### Key warranty conditions

- Battery must be operated within the temperature and humidity ranges in the spec sheet.
- Installation must be by a skilled/trained installer following the installation guide.
- Remote firmware updates via internet connection are required; disconnection for extended periods may affect the full 10-year warranty (a 5-year floor still applies regardless).
- Warranty is transferable only if the equipment remains at the original installation address.

### What is NOT covered

- Damage from improper installation or use
- Force majeure (lightning, flooding, fire, etc.)
- Cosmetic wear and tear
- Installations within 500 m of the coastline or affected by sea winds
- Damage from third-party software or components not supplied by Sigenergy

---

## Implications for this controller

### Chemistry — good news

LFP (LiFePO4) is specifically designed for high-cycle daily use. It is far more tolerant of:
- Daily full charge/discharge cycles
- Being held at high SOC (less damaging than NMC chemistry)
- Shallow depth-of-discharge cycles

The 100% DoD test condition in the spec sheet confirms the manufacturer rates and tests at full depth — the controller's 40% SOC floor means real usage is shallower than the test, which is easier on the cells.

### Throughput cap — monitor it

The 23.77 MWh/unit cap is the binding warranty constraint, not cycle count per se. The controller currently performs:
- Overnight grid charge to ~100% SOC (TOU mode)
- Pre-period headroom exports (discharge to ~40% SOC floor on Green days)
- Evening self-consumption discharge
- Optional pre-cheap-rate export discharge

On an active summer day this can drive 1.0–1.5+ equivalent cycles. Ireland's mixed weather (many Red/Amber/overcast days) averages the annual figure down considerably.

Run `python scripts/battery_throughput.py` to calculate the actual discharge rate from telemetry and project against the warranty cap.

### Measured throughput (Apr–May 2026, summer baseline)

| Metric | Value |
|---|---|
| Average daily discharge | 24.81 kWh/day (all 3 units) |
| Average per unit | 8.27 kWh/day (vs 7.8 kWh usable — >1 cycle/day) |
| Equivalent cycles/day | 1.06 per unit |
| Warranty daily budget | 19.5 kWh/day to hit cap exactly at year 10 |
| Summer overage vs budget | +27% above budget |

High-discharge days (30+ kWh) show the battery cycling from **~5–16% SOC up to 100%** — effectively full depth-of-discharge cycles. The deep minimum SOC comes from self-powered mode naturally depleting the battery to cover home load overnight (the controller's 40% floor only applies to timed GRID_EXPORT windows, not self-powered operation).

**Projection at measured rates:**
- At summer rate only: warranty cap in **7.9 years** (misses 10-year window)
- Blended estimate (winter = 40% of summer): **11.3 years** ✓
- Blended estimate (winter = 60% of summer, more realistic without solar recharge): **~9.9 years** ⚠

The winter assumption is the key uncertainty. In winter, solar recharging is absent so the battery can only do one full charge-discharge cycle per day (overnight TOU charge → daytime/evening self-powered discharge), but that single cycle is still substantial (~15–18 kWh) because the inverter will deplete the battery covering home load throughout the day.

### If throughput becomes a concern

The single largest lever is the **pre-cheap-rate evening export** — it adds a discharge cycle that isn't strictly necessary for solar headroom. Disabling or reducing it on days when morning forecast is not strongly Green would be the first thing to adjust.

The **overnight grid charge to 100%** followed immediately by a **pre-period headroom export** is also an avoidable cycle: if the morning forecast is Green, charging to a lower SOC overnight (e.g. 60%) would eliminate the export needed to create headroom. This would require a TOU schedule change in the Sigen app rather than controller code.

"""
Estimate daily battery discharge throughput from inverter telemetry and compare
against the SigenStor BAT 8.0 warranty throughput cap (23.77 MWh per unit).

batteryPower sign convention: negative = discharging, positive = charging.
Discharge energy per tick = abs(min(0, battery_power_kw)) * interval_hours.
"""

import json
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

TELEMETRY_PATH = Path("data/inverter_telemetry.jsonl")

# SigenStor BAT 8.0 warranty cap per unit (MWh)
WARRANTY_MWH_PER_UNIT = 23.77
NUM_UNITS = 3
TOTAL_WARRANTY_MWH = WARRANTY_MWH_PER_UNIT * NUM_UNITS
WARRANTY_YEARS = 10


def load_records() -> list[dict]:
    records = []
    with TELEMETRY_PATH.open() as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def parse_ts(ts_str: str) -> datetime:
    return datetime.fromisoformat(ts_str)


def analyse(records: list[dict]) -> None:
    # Group by calendar date (local time from captured_at)
    by_date: dict[date, list[dict]] = defaultdict(list)
    for r in records:
        d = parse_ts(r["captured_at"]).date()
        by_date[d].append(r)

    # Sort dates
    dates = sorted(by_date.keys())

    daily_discharge: dict[date, float] = {}
    daily_charge: dict[date, float] = {}

    for d in dates:
        day_records = sorted(by_date[d], key=lambda r: parse_ts(r["captured_at"]))
        discharge_kwh = 0.0
        charge_kwh = 0.0

        for i, rec in enumerate(day_records):
            batt_kw = rec.get("derived", {}).get("extracted_metrics", {}).get("battery_power_kw")
            if batt_kw is None:
                batt_kw = rec.get("energy_flow", {}).get("batteryPower")
            if batt_kw is None:
                continue

            # Interval: use gap to next record, or 5 min for the last one
            if i < len(day_records) - 1:
                t0 = parse_ts(day_records[i]["captured_at"])
                t1 = parse_ts(day_records[i + 1]["captured_at"])
                interval_h = (t1 - t0).total_seconds() / 3600.0
                # Clamp to avoid gaps from service restarts distorting the total
                interval_h = min(interval_h, 10 / 60)
            else:
                interval_h = 5 / 60

            if batt_kw < 0:
                discharge_kwh += abs(batt_kw) * interval_h
            else:
                charge_kwh += batt_kw * interval_h

        daily_discharge[d] = discharge_kwh
        daily_charge[d] = charge_kwh

    # ── Summary table ───────────────────────────────────────────────────────
    print(f"\n{'Date':<12} {'Discharge kWh':>14} {'Charge kWh':>11} {'Net kWh':>9}")
    print("-" * 52)
    for d in dates:
        dis = daily_discharge[d]
        chg = daily_charge[d]
        net = chg - dis
        print(f"{d!s:<12} {dis:>14.2f} {chg:>11.2f} {net:>+9.2f}")

    total_dis = sum(daily_discharge.values())
    total_chg = sum(daily_charge.values())
    n_days = len(dates)
    avg_dis = total_dis / n_days if n_days else 0
    avg_chg = total_chg / n_days if n_days else 0

    print("-" * 52)
    print(f"{'Average':<12} {avg_dis:>14.2f} {avg_chg:>11.2f}")
    print(f"{'Total':<12} {total_dis:>14.2f} {total_chg:>11.2f}")

    # ── Warranty projection ─────────────────────────────────────────────────
    print(f"\n── Warranty throughput projection ({'summer baseline — expect lower annual average':}) ──")
    print(f"  Data window          : {dates[0]} → {dates[-1]} ({n_days} days)")
    print(f"  Avg daily discharge  : {avg_dis:.2f} kWh/day (all 3 units combined)")
    print(f"  Avg per unit         : {avg_dis / NUM_UNITS:.2f} kWh/day  (usable capacity: 7.8 kWh/unit)")
    print(f"  Equivalent cycles/day: {avg_dis / NUM_UNITS / 7.8:.3f} per unit")
    print()
    print(f"  Warranty cap         : {WARRANTY_MWH_PER_UNIT:.2f} MWh/unit × {NUM_UNITS} = {TOTAL_WARRANTY_MWH:.2f} MWh total")
    budget_per_day = (TOTAL_WARRANTY_MWH * 1000) / (WARRANTY_YEARS * 365)
    print(f"  10-yr daily budget   : {budget_per_day:.1f} kWh/day to hit cap exactly at year 10")
    print()

    # Project at current summer rate
    days_at_summer = (TOTAL_WARRANTY_MWH * 1000) / avg_dis if avg_dis > 0 else float("inf")
    years_at_summer = days_at_summer / 365
    print(f"  At current summer rate ({avg_dis:.1f} kWh/day):")
    print(f"    → warranty cap in {years_at_summer:.1f} years")

    # Rough annual estimate: assume winter avg is ~40% of summer
    winter_fraction = 0.40
    winter_avg = avg_dis * winter_fraction
    annual_avg = (avg_dis * 180 + winter_avg * 185) / 365
    days_annual = (TOTAL_WARRANTY_MWH * 1000) / annual_avg if annual_avg > 0 else float("inf")
    years_annual = days_annual / 365
    print(f"  Rough annual estimate (summer × 1.0, winter × {winter_fraction}):")
    print(f"    Winter avg           : {winter_avg:.1f} kWh/day")
    print(f"    Blended annual avg   : {annual_avg:.1f} kWh/day")
    print(f"    → warranty cap in    : {years_annual:.1f} years")
    print()
    if years_annual >= WARRANTY_YEARS:
        print(f"  ✓ Within 10-year warranty period on blended estimate.")
    else:
        print(f"  ⚠  Blended estimate exceeds warranty cap before 10 years.")
    print()


if __name__ == "__main__":
    records = load_records()
    print(f"Loaded {len(records)} telemetry records.")
    analyse(records)

"""Compare Solcast half-hourly forecasts against actual inverter PV output.

For each completed 30-minute slot on each available day, finds the latest
Solcast snapshot captured before that slot's period_end and compares
pv_estimate against the average pvPower from inverter telemetry.

Run from project root:
    python3 scripts/solcast_vs_actual.py           # all available days
    python3 scripts/solcast_vs_actual.py --today   # today only
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.settings import LOCAL_TIMEZONE

_TZ = ZoneInfo(LOCAL_TIMEZONE)
_UTC = timezone.utc

_DATA = _ROOT / "data"
_SOLCAST_FILE = _DATA / "solcast_readings.jsonl"
_TELEMETRY_FILE = _DATA / "inverter_telemetry.jsonl"


def _load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _slot_end_utc(ts_utc: datetime) -> datetime:
    minutes = ts_utc.minute
    if minutes < 30:
        return ts_utc.replace(minute=30, second=0, microsecond=0)
    return ts_utc.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)


def _build_actuals(telemetry: list[dict], target_date: date) -> dict[datetime, float]:
    slot_readings: dict[datetime, list[float]] = {}
    for row in telemetry:
        ts = _parse_dt(row["captured_at"]).astimezone(_TZ)
        if ts.date() != target_date:
            continue
        pv = row.get("energy_flow", {}).get("pvPower")
        if not isinstance(pv, (int, float)):
            continue
        slot_end = _slot_end_utc(ts.astimezone(_UTC))
        slot_readings.setdefault(slot_end, []).append(float(pv))
    return {slot: mean(vals) for slot, vals in slot_readings.items() if vals}


def _build_snapshot_index(
    snapshots: list[dict],
) -> list[tuple[datetime, dict[datetime, dict]]]:
    index: list[tuple[datetime, dict[datetime, dict]]] = []
    for snap in sorted(snapshots, key=lambda r: r["captured_at_utc"]):
        cap = _parse_dt(snap["captured_at_utc"])
        slot_map = {_parse_dt(f["period_end"]): f for f in snap.get("forecasts", [])}
        index.append((cap, slot_map))
    return index


def _latest_forecast_for(
    slot_end: datetime, index: list[tuple[datetime, dict[datetime, dict]]]
) -> dict | None:
    for cap, slot_map in reversed(index):
        if cap <= slot_end and slot_end in slot_map:
            return slot_map[slot_end]
    return None


def _analyse_day(
    target_date: date,
    actuals: dict[datetime, float],
    snapshot_index: list[tuple[datetime, dict[datetime, dict]]],
    now_utc: datetime,
) -> list[tuple]:
    rows = []
    for slot_end_utc, actual_kw in sorted(actuals.items()):
        slot_end_local = slot_end_utc.astimezone(_TZ)
        if slot_end_local.date() != target_date:
            continue
        if slot_end_utc > now_utc:
            continue
        local_hour = slot_end_local.hour
        if local_hour < 6 or local_hour > 21:
            continue
        fc = _latest_forecast_for(slot_end_utc, snapshot_index)
        if fc is None:
            continue
        est = fc["pv_estimate"]
        p10 = fc.get("pv_estimate10", "")
        p90 = fc.get("pv_estimate90", "")
        if actual_kw == 0.0 and est == 0.0:
            continue
        rows.append((slot_end_local, est, p10, p90, actual_kw, est - actual_kw))
    return rows


def _print_day(target_date: date, rows: list[tuple]) -> tuple[int, float, float, float, int, int] | None:
    header = f"{'Time':>6}  {'Forecast':>9}  {'P10–P90':>13}  {'Actual':>8}  {'Error':>8}  {'%Err':>7}"
    width = len(header)
    print(f"\nSolcast vs Actual — {target_date.strftime('%-d %b %Y')}")
    print("=" * width)
    print(header)
    print("-" * width)

    if not rows:
        print("  (no matched slots)")
        print("=" * width)
        return None

    errors: list[float] = []
    ape_values: list[float] = []

    for slot_end_local, est, p10, p90, actual_kw, err in rows:
        time_str = slot_end_local.strftime("%H:%M")
        p_range = f"{p10:.2f}–{p90:.2f}" if isinstance(p10, float) else "    n/a    "
        pct = f"{err / actual_kw * 100:+.0f}%" if actual_kw > 0 else "   n/a"
        print(
            f"{time_str:>6}  {est:>7.2f}kW  {p_range:>13}  {actual_kw:>6.2f}kW"
            f"  {err:>+7.2f}kW  {pct:>7}"
        )
        errors.append(err)
        if actual_kw > 0:
            ape_values.append(abs(err) / actual_kw)

    print("=" * width)

    mae = mean(abs(e) for e in errors)
    bias = mean(errors)
    mape = mean(ape_values) * 100 if ape_values else float("nan")
    over = sum(1 for e in errors if e > 0)
    under = sum(1 for e in errors if e < 0)
    bias_dir = "over" if bias > 0 else "under"
    print(f"  Slots {len(errors):>2}  |  MAE {mae:.3f} kW  |  MAPE {mape:.1f}%  |  Bias {bias:+.3f} kW ({bias_dir})  |  {over}↑ {under}↓")

    return len(errors), mae, mape, bias, over, under


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", action="store_true", help="Show today only")
    args = parser.parse_args()

    now_local = datetime.now(_TZ)
    now_utc = now_local.astimezone(_UTC)
    today = now_local.date()

    telemetry = _load_jsonl(_TELEMETRY_FILE)
    snapshots = _load_jsonl(_SOLCAST_FILE)

    if not snapshots:
        print("No Solcast snapshots found.")
        return
    if not telemetry:
        print("No inverter telemetry found.")
        return

    snapshot_index = _build_snapshot_index(snapshots)

    # Dates covered by snapshots (local time of capture)
    snap_dates = {
        _parse_dt(s["captured_at_utc"]).astimezone(_TZ).date() for s in snapshots
    }
    # Dates covered by telemetry
    tel_dates = {
        _parse_dt(r["captured_at"]).astimezone(_TZ).date() for r in telemetry
    }

    candidate_dates = sorted(snap_dates & tel_dates)
    if args.today:
        candidate_dates = [d for d in candidate_dates if d == today]

    if not candidate_dates:
        print("No dates with both Solcast snapshots and inverter telemetry.")
        return

    # Aggregate stats
    all_errors: list[float] = []
    all_ape: list[float] = []
    day_summaries: list[tuple] = []

    for d in candidate_dates:
        actuals = _build_actuals(telemetry, d)
        rows = _analyse_day(d, actuals, snapshot_index, now_utc)
        result = _print_day(d, rows)
        if result:
            n, mae, mape, bias, over, under = result
            all_errors.extend(e for _, _, _, _, _, e in rows)
            all_ape.extend(
                abs(e) / a for _, _, _, _, a, e in rows if a > 0
            )
            day_summaries.append((d, n, mae, mape, bias))

    # Latest snapshot timestamp
    if snapshot_index:
        latest_cap = snapshot_index[-1][0].astimezone(_TZ)
        print(f"\nLatest snapshot: {latest_cap.strftime('%-d %b %H:%M %Z')}")

    if len(day_summaries) > 1:
        print("\n── Aggregate across all days ──────────────────────────────────")
        total_slots = sum(n for _, n, *_ in day_summaries)
        agg_mae = mean(abs(e) for e in all_errors)
        agg_mape = mean(all_ape) * 100 if all_ape else float("nan")
        agg_bias = mean(all_errors)
        agg_over = sum(1 for e in all_errors if e > 0)
        agg_under = sum(1 for e in all_errors if e < 0)
        bias_dir = "over" if agg_bias > 0 else "under"
        print(f"  Days {len(day_summaries)}  |  Slots {total_slots}  |  MAE {agg_mae:.3f} kW  |  MAPE {agg_mape:.1f}%  |  Bias {agg_bias:+.3f} kW ({bias_dir})  |  {agg_over}↑ {agg_under}↓")


if __name__ == "__main__":
    main()

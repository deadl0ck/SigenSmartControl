"""Show today's Solcast forecast vs actual solar output by scheduler period.

Uses the same sunrise-derived period windows the scheduler uses (Morn/Aftn/Eve),
not fixed clock hours.  Periods still in progress show a partial actual.

Run from project root:
    python scripts/todays_solcast_accuracy.py
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config.constants import LATITUDE, LONGITUDE
from config.settings import LOCAL_TIMEZONE, FORECAST_SOLAR_SITE_KWP, QUARTZ_RED_CAPACITY_FRACTION, QUARTZ_GREEN_CAPACITY_FRACTION
from logic.schedule_utils import derive_period_windows
from weather.sunrise_sunset import get_sunrise_sunset
from zoneinfo import ZoneInfo

_TZ = ZoneInfo(LOCAL_TIMEZONE)
_UTC = timezone.utc
_DATA = _ROOT / "data"


def _parse_dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _status(avg_kw: float) -> str:
    cap = max(FORECAST_SOLAR_SITE_KWP, 0.1)
    frac = avg_kw / cap
    if frac < QUARTZ_RED_CAPACITY_FRACTION:
        return "Red"
    if frac < QUARTZ_GREEN_CAPACITY_FRACTION:
        return "Amber"
    return "Green"


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    log = logging.getLogger(__name__)

    now_local = datetime.now(_TZ)
    now_utc = now_local.astimezone(_UTC)
    today = now_local.date()

    # --- Period windows from sunrise/sunset (same as scheduler) ---
    sunrise_str, sunset_str = get_sunrise_sunset(LATITUDE, LONGITUDE, today.isoformat())
    sunrise_utc = _parse_dt(sunrise_str)
    sunset_utc  = _parse_dt(sunset_str)
    period_windows = derive_period_windows(sunrise_utc, sunset_utc, ["Morn", "Aftn", "Eve"])
    ordered = sorted(period_windows.items(), key=lambda x: x[1])

    # Build period end times
    period_ends: dict[str, datetime] = {}
    for i, (period, start) in enumerate(ordered):
        period_ends[period] = ordered[i + 1][1] if i + 1 < len(ordered) else sunset_utc

    # --- Solcast forecast: use latest snapshot captured before each period start ---
    solcast_file = _DATA / "solcast_readings.jsonl"
    snapshots: list[dict] = []
    if solcast_file.exists():
        for line in solcast_file.read_text().splitlines():
            if line.strip():
                snapshots.append(json.loads(line))
    snapshots.sort(key=lambda s: s["captured_at_utc"])

    def forecast_for_period(period_start: datetime, period_end: datetime) -> float | None:
        """Average pv_estimate over slots within the period from the last snapshot before period_start."""
        best_snap = None
        for snap in reversed(snapshots):
            if _parse_dt(snap["captured_at_utc"]) <= period_start:
                best_snap = snap
                break
        if best_snap is None:
            best_snap = snapshots[0] if snapshots else None
        if best_snap is None:
            return None
        vals = []
        for entry in best_snap["forecasts"]:
            slot_end = _parse_dt(entry["period_end"])
            if period_start < slot_end <= period_end:
                vals.append(float(entry["pv_estimate"]))
        return mean(vals) if vals else None

    # --- Actual solar from telemetry ---
    telemetry_file = _DATA / "inverter_telemetry.jsonl"
    actuals_by_period: dict[str, list[float]] = {p: [] for p in period_windows}

    if telemetry_file.exists():
        for line in telemetry_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
                ts = _parse_dt(row["captured_at"]).astimezone(_UTC)
                if ts.date() != today:
                    continue
                solar = row["derived"]["extracted_metrics"]["solar_power_kw"]
                for period, start in period_windows.items():
                    end = period_ends[period]
                    if start <= ts < end:
                        actuals_by_period[period].append(float(solar))
            except Exception:
                continue

    # --- Print ---
    print(f"\nToday's Solcast forecast vs actual — {today.strftime('%-d %b %Y')}")
    print(f"Sunrise {sunrise_utc.astimezone(_TZ).strftime('%H:%M')} BST  "
          f"Sunset {sunset_utc.astimezone(_TZ).strftime('%H:%M')} BST\n")

    header = f"{'Period':<6}  {'Window (BST)':<15}  {'Forecast':>9}  {'Status':>6}  {'Actual':>9}  {'Diff':>8}  {'Note'}"
    print(header)
    print("-" * len(header))

    for period, start_utc in ordered:
        end_utc = period_ends[period]
        start_bst = start_utc.astimezone(_TZ).strftime("%H:%M")
        end_bst   = end_utc.astimezone(_TZ).strftime("%H:%M")
        window    = f"{start_bst}–{end_bst}"

        fc_kw = forecast_for_period(start_utc, end_utc)
        vals  = actuals_by_period[period]
        act_kw = mean(vals) if vals else None

        fc_str  = f"{fc_kw * 1000:.0f}W"  if fc_kw  is not None else "     n/a"
        fc_stat = _status(fc_kw)           if fc_kw  is not None else "   -"
        act_str = f"{act_kw * 1000:.0f}W" if act_kw is not None else "     n/a"
        diff_str = f"{(act_kw - fc_kw) / fc_kw * 100:+.1f}%" if fc_kw and act_kw else "       -"

        if now_utc < start_utc:
            note = "upcoming"
        elif now_utc < end_utc:
            note = f"in progress ({len(vals)} samples)"
        else:
            note = f"complete ({len(vals)} samples)"

        print(f"{period:<6}  {window:<15}  {fc_str:>9}  {fc_stat:>6}  {act_str:>9}  {diff_str:>8}  {note}")

    print()


if __name__ == "__main__":
    main()

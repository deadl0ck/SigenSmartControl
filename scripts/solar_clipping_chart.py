"""Generate a stacked bar chart of daily solar production per period with ESB forecast and clipping.

Per-day data sources:
- Production per period (Morn/Aftn/Eve): pvDayNrg deltas at period boundaries
  (hours 7, 12, 16, 20 local time matching FORECAST_ANALYSIS_* settings).
- ESB forecast per period: earliest 'today' entry in forecast_comparisons.jsonl.
- Promotions (Amber→Green): mode_change_events.jsonl 'promoting AMBER to GREEN' entries.
- Clipping duration: ticks with likely_clipping=True × 5 min on secondary axis.

Run from project root:
    python scripts/solar_clipping_chart.py
"""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional
import zoneinfo

import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as ticker

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))

from config.settings import (
    CHEAP_RATE_END_HOUR,
    CHEAP_RATE_START_HOUR,
    FORECAST_ANALYSIS_MORNING_START_HOUR,
    FORECAST_ANALYSIS_MORNING_END_HOUR,
    FORECAST_ANALYSIS_AFTERNOON_END_HOUR,
    FORECAST_ANALYSIS_EVENING_END_HOUR,
    LOCAL_TIMEZONE,
)

TELEMETRY_PATH = _ROOT / "data" / "inverter_telemetry.jsonl"
FORECAST_PATH = _ROOT / "data" / "forecast_comparisons.jsonl"
EVENTS_PATH = _ROOT / "data" / "mode_change_events.jsonl"

LOCAL_TZ = zoneinfo.ZoneInfo(LOCAL_TIMEZONE)
TICK_MINUTES = 5
PERIODS = ("Morn", "Aftn", "Eve")

# pvDayNrg boundary hours in local time: start-of-Morn, Morn/Aftn split, Aftn/Eve split, end-of-Eve
PERIOD_BOUNDARY_HOURS = (
    FORECAST_ANALYSIS_MORNING_START_HOUR,
    FORECAST_ANALYSIS_MORNING_END_HOUR,
    FORECAST_ANALYSIS_AFTERNOON_END_HOUR,
    FORECAST_ANALYSIS_EVENING_END_HOUR,
)

PROD_COLORS = {"Morn": "#f9c74f", "Aftn": "#f3722c", "Eve": "#577590"}
FORECAST_COLORS = {"Green": "#2a9d8f", "Amber": "#e9c46a", "Red": "#e63946"}

# Hours considered daytime for grid import — excludes cheap-rate window (CHEAP_RATE_START_HOUR–CHEAP_RATE_END_HOUR)
DAYTIME_HOURS = set(range(CHEAP_RATE_END_HOUR, CHEAP_RATE_START_HOUR))
TICK_HOURS = TICK_MINUTES / 60


def load_jsonl(path: Path) -> list[dict]:
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def local_date(ts_str: str) -> date:
    return datetime.fromisoformat(ts_str).astimezone(LOCAL_TZ).date()


def local_hour(ts_str: str) -> int:
    return datetime.fromisoformat(ts_str).astimezone(LOCAL_TZ).hour


def pvnrg_at_boundary(records_for_day: list[tuple[int, float]], boundary_hour: int) -> float:
    """Return the pvDayNrg value from the record closest to boundary_hour."""
    candidates = [(abs(h - boundary_hour), nrg) for h, nrg in records_for_day]
    return min(candidates, key=lambda x: x[0])[1] if candidates else 0.0


def compute_production(telemetry: list[dict]) -> dict[date, dict[str, float]]:
    """Split daily pvDayNrg into per-period production via boundary-hour deltas."""
    by_day: dict[date, list[tuple[int, float]]] = defaultdict(list)
    for r in telemetry:
        nrg = r["energy_flow"].get("pvDayNrg")
        if nrg is not None:
            d = local_date(r["captured_at"])
            h = local_hour(r["captured_at"])
            by_day[d].append((h, float(nrg)))

    result: dict[date, dict[str, float]] = {}
    h0, h1, h2, h3 = PERIOD_BOUNDARY_HOURS
    for d, recs in by_day.items():
        n0 = pvnrg_at_boundary(recs, h0)
        n1 = pvnrg_at_boundary(recs, h1)
        n2 = pvnrg_at_boundary(recs, h2)
        n3 = pvnrg_at_boundary(recs, h3)
        result[d] = {
            "Morn": max(0.0, n1 - n0),
            "Aftn": max(0.0, n2 - n1),
            "Eve": max(0.0, n3 - n2),
            "total": max(recs, key=lambda x: x[1])[1],
        }
    return result


def compute_clipping(telemetry: list[dict]) -> dict[date, int]:
    """Count clipping ticks per day."""
    ticks: dict[date, int] = defaultdict(int)
    for r in telemetry:
        if r["derived"].get("likely_clipping"):
            ticks[local_date(r["captured_at"])] += 1
    return ticks


def compute_esb_forecast(comparisons: list[dict]) -> dict[date, dict[str, str]]:
    """Return earliest-captured ESB period forecast for each day."""
    earliest: dict[date, tuple[datetime, dict]] = {}
    for r in comparisons:
        if r.get("primary_provider") != "esb_api":
            continue
        ts = datetime.fromisoformat(r["captured_at"])
        d = local_date(r["captured_at"])
        if d not in earliest or ts < earliest[d][0]:
            earliest[d] = (ts, r.get("today", {}).get("periods", {}))

    result: dict[date, dict[str, str]] = {}
    for d, (_, periods) in earliest.items():
        result[d] = {
            period: data["primary"]["status"]
            for period, data in periods.items()
            if "primary" in data
        }
    return result


def compute_grid_flows(telemetry: list[dict]) -> dict[date, dict[str, float]]:
    """Return daily grid import, daytime solar export, and night battery export in kWh.

    Import is restricted to DAYTIME_HOURS (08:00–22:59) to exclude cheap-rate charging.
    Export is split: daytime (solar-driven) vs night (battery arbitrage, cheap-rate window).
    """
    flows: dict[date, dict[str, float]] = defaultdict(
        lambda: {"import": 0.0, "export_day": 0.0, "export_night": 0.0}
    )
    for r in telemetry:
        power = r["energy_flow"].get("buySellPower")
        if power is None:
            continue
        power = float(power)
        d = local_date(r["captured_at"])
        h = local_hour(r["captured_at"])
        if power < 0 and h in DAYTIME_HOURS:
            flows[d]["import"] += abs(power) * TICK_HOURS
        elif power > 0:
            if h in DAYTIME_HOURS:
                flows[d]["export_day"] += power * TICK_HOURS
            else:
                flows[d]["export_night"] += power * TICK_HOURS
    return dict(flows)


def compute_promotions(events: list[dict]) -> dict[date, set[str]]:
    """Return set of promoted period names per day."""
    promoted: dict[date, set[str]] = defaultdict(set)
    for r in events:
        if "promoting AMBER to GREEN" in r.get("reason", ""):
            d = local_date(r["captured_at"])
            period = r.get("period", "").split("(")[0].strip()
            if period in PERIODS:
                promoted[d].add(period)
    return promoted


def main() -> None:
    telemetry = load_jsonl(TELEMETRY_PATH)
    comparisons = load_jsonl(FORECAST_PATH)
    events = load_jsonl(EVENTS_PATH)

    production = compute_production(telemetry)
    clipping = compute_clipping(telemetry)
    forecasts = compute_esb_forecast(comparisons)
    promotions = compute_promotions(events)
    grid_flows = compute_grid_flows(telemetry)

    today = max(production.keys())
    days = [today - timedelta(days=6 - i) for i in range(7)]
    labels = [d.strftime("%a\n%d %b") for d in days]

    # Layout: two sub-bars per day — production (wide) + forecast strip (narrow)
    prod_width = 0.45
    fcast_width = 0.12
    gap = 0.04  # gap between production bar and forecast strip
    STRIP_SECTION_H = 20.0  # fixed kWh height per forecast section (3 sections = 60 kWh total)
    n = len(days)

    fig, ax1 = plt.subplots(figsize=(12, 6))
    fig.patch.set_facecolor("#1a1a2e")
    ax1.set_facecolor("#16213e")
    ax2 = ax1.twinx()

    prod_centers = [i - (fcast_width + gap) / 2 for i in range(n)]
    fcast_centers = [c + prod_width / 2 + gap + fcast_width / 2 for c in prod_centers]

    for i, d in enumerate(days):
        prod = production.get(d, {"Morn": 0, "Aftn": 0, "Eve": 0})
        fcast = forecasts.get(d, {})
        promoted_today = promotions.get(d, set())

        # Stacked production bars
        bottom = 0.0
        for period in PERIODS:
            val = prod.get(period, 0.0)
            ax1.bar(prod_centers[i], val, width=prod_width, bottom=bottom,
                    color=PROD_COLORS[period], zorder=3)
            if val > 1.0:
                ax1.text(prod_centers[i], bottom + val / 2,
                         f"{val:.1f}", ha="center", va="center",
                         fontsize=7, color="white", fontweight="bold", zorder=4)
            bottom += val

        # Total label above production bar
        total = prod.get("total", bottom)
        if total > 0:
            ax1.text(prod_centers[i], total + 0.5, f"{total:.1f}",
                     ha="center", va="bottom", fontsize=8, color="#e0e0e0", zorder=4)

        # Forecast strip: fixed equal-height sections so low-production days remain readable
        fcast_bottom = 0.0
        for period in PERIODS:
            status = fcast.get(period)
            color = FORECAST_COLORS.get(status, "#555577") if status else "#333355"
            ax1.bar(fcast_centers[i], STRIP_SECTION_H, width=fcast_width,
                    bottom=fcast_bottom, color=color, zorder=3, alpha=0.9)
            if period in promoted_today:
                ax1.text(fcast_centers[i], fcast_bottom + STRIP_SECTION_H / 2,
                         "P", ha="center", va="center",
                         fontsize=7, color="white", fontweight="bold", zorder=5)
            fcast_bottom += STRIP_SECTION_H

    # Grid import and split export lines on primary axis
    import_kwh = [grid_flows.get(d, {}).get("import", 0.0) for d in days]
    export_day_kwh = [grid_flows.get(d, {}).get("export_day", 0.0) for d in days]
    export_night_kwh = [grid_flows.get(d, {}).get("export_night", 0.0) for d in days]
    ax1.plot(prod_centers, import_kwh, color="#ef476f", linewidth=1.5,
             linestyle="--", marker="o", markersize=5, zorder=6, label="Grid import daytime (kWh)")
    ax1.plot(prod_centers, export_day_kwh, color="#06d6a0", linewidth=1.5,
             linestyle="--", marker="o", markersize=5, zorder=6, label="Solar export daytime (kWh)")
    ax1.plot(prod_centers, export_night_kwh, color="#a8dadc", linewidth=1.5,
             linestyle=":", marker="s", markersize=5, zorder=6, label="Battery export night (kWh)")
    for i, (imp, exp_d, exp_n) in enumerate(zip(import_kwh, export_day_kwh, export_night_kwh)):
        if imp > 0.1:
            ax1.text(prod_centers[i], imp + 0.3, f"{imp:.1f}",
                     ha="center", va="bottom", fontsize=7, color="#ef476f", zorder=7)
        if exp_d > 0.1:
            ax1.text(prod_centers[i], exp_d + 0.3, f"{exp_d:.1f}",
                     ha="center", va="bottom", fontsize=7, color="#06d6a0", zorder=7)
        if exp_n > 0.1:
            ax1.text(prod_centers[i], exp_n + 0.3, f"{exp_n:.1f}",
                     ha="center", va="bottom", fontsize=7, color="#a8dadc", zorder=7)

    # Clipping on secondary axis as scatter markers
    clip_mins = [clipping.get(d, 0) * TICK_MINUTES for d in days]
    ax2.scatter(range(n), clip_mins, color="#f77f00", s=60, zorder=5, label="Clipping (min)")
    for i, cm in enumerate(clip_mins):
        if cm > 0:
            ax2.text(i, cm + 0.3, f"{cm}m", ha="center", va="bottom",
                     fontsize=7, color="#ffd166", zorder=5)

    # Axes styling
    ax1.set_xticks(range(n))
    ax1.set_xticklabels(labels, color="#e0e0e0", fontsize=9)
    ax1.set_xlim(-0.6, n - 0.4)
    ax1.set_ylabel("Production (kWh)", color="#e0e0e0")
    ax1.tick_params(colors="#e0e0e0")
    ax1.spines[:].set_color("#444466")
    ax1.grid(axis="y", color="#2a2a4a", linewidth=0.8, zorder=0)
    ax1.yaxis.label.set_color("#e0e0e0")

    ax2.set_ylabel("Clipping detected (minutes at ceiling)", color="#f77f00")
    ax2.tick_params(axis="y", colors="#f77f00")
    ax2.spines[:].set_color("#444466")
    ax2.set_ylim(bottom=0)

    ax1.set_title("Daily Solar Production by Period — Last 7 Days\n"
                  "Bars: actual kWh (Morn/Aftn/Eve) | Strip: ESB forecast (R/A/G) 'P'=promoted | "
                  "Lines: grid import, solar export, battery arbitrage export | Dots: clipping minutes",
                  color="#ffffff", fontsize=11, pad=12)

    # Legend
    prod_patches = [mpatches.Patch(color=PROD_COLORS[p], label=f"{p} production") for p in PERIODS]
    fcast_patches = [
        mpatches.Patch(color=FORECAST_COLORS["Green"], label="ESB: Green"),
        mpatches.Patch(color=FORECAST_COLORS["Amber"], label="ESB: Amber"),
        mpatches.Patch(color=FORECAST_COLORS["Red"], label="ESB: Red"),
    ]
    import_line = plt.Line2D([0], [0], color="#ef476f", linewidth=1.5, linestyle="--",
                             marker="o", markersize=5, label="Grid import daytime (kWh)")
    export_day_line = plt.Line2D([0], [0], color="#06d6a0", linewidth=1.5, linestyle="--",
                                 marker="o", markersize=5, label="Solar export daytime (kWh)")
    export_night_line = plt.Line2D([0], [0], color="#a8dadc", linewidth=1.5, linestyle=":",
                                   marker="s", markersize=5, label="Battery export night (kWh)")
    clip_dot = plt.scatter([], [], color="#f77f00", s=60, label="Clipping (min)")
    ax1.legend(
        handles=prod_patches + fcast_patches + [import_line, export_day_line, export_night_line, clip_dot],
        facecolor="#16213e", edgecolor="#444466", labelcolor="#e0e0e0",
        fontsize=8, loc="upper left",
    )

    out_path = _ROOT / "data" / "solar_clipping_last7days.png"
    plt.tight_layout()
    plt.savefig(out_path, dpi=150, facecolor=fig.get_facecolor())
    print(f"Saved: {out_path}")
    plt.show()


if __name__ == "__main__":
    main()

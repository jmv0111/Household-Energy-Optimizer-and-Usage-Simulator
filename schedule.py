"""
Step 7 of the methodology: turn the optimal hours into a daily schedule a
household can follow.

Meralco's residential rate is the same at every hour (TOU is out of scope),
so time of day does not change the cost. The clock times only make the plan
practical. Each appliance's hours go to the hours of the day when Metro
Manila / CALABARZON / Central Luzon households in HECS 2011 most often used
that appliance (data/time_profiles.csv). Appliances used only some days a
week are spread across the week.
"""

import csv
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROFILES_CSV = ROOT / "data" / "time_profiles.csv"

# Fallback preference when an appliance has no survey profile:
# evening first, then night, then morning, then afternoon.
DEFAULT_ORDER = [19, 20, 18, 21, 22, 17, 23, 6, 7, 5, 8, 0, 1, 2, 3, 4,
                 9, 10, 11, 12, 13, 14, 15, 16]

WEEK_DAYS = {1: ["Sat"], 2: ["Wed", "Sat"], 3: ["Mon", "Wed", "Sat"],
             4: ["Mon", "Wed", "Fri", "Sun"],
             5: ["Mon", "Tue", "Thu", "Fri", "Sat"],
             6: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]}

_profiles = None


def load_profiles(path=PROFILES_CSV):
    global _profiles
    if _profiles is None:
        _profiles = {}
        if Path(path).exists():
            with open(path, newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    _profiles[row["name"]] = [float(row[f"h{h:02d}"]) for h in range(24)]
    return _profiles


def hour_preference(appliance):
    """Clock hours ordered from most to least typical for this appliance."""
    shares = load_profiles().get(appliance.catalogue or appliance.name)
    if not shares or max(shares) <= 0:
        return DEFAULT_ORDER
    # Tie-break by the default (evening-first) order.
    rank = {h: i for i, h in enumerate(DEFAULT_ORDER)}
    return sorted(range(24), key=lambda h: (-shares[h], rank[h]))


def time_blocks(appliance, hours):
    """Clock-time blocks totalling `hours` (e.g. 3.5 -> ['18:00-21:30'])."""
    if hours <= 0:
        return []
    if hours >= 24 - 1e-9:
        return ["00:00-24:00"]
    n_full = math.ceil(hours - 1e-9)
    chosen = sorted(hour_preference(appliance)[:n_full])
    # Merge consecutive hours (wrapping past midnight) into blocks.
    blocks, start, prev = [], chosen[0], chosen[0]
    for h in chosen[1:]:
        if h == prev + 1:
            prev = h
            continue
        blocks.append([start, prev + 1])
        start = prev = h
    blocks.append([start, prev + 1])
    if len(blocks) > 1 and blocks[0][0] == 0 and blocks[-1][1] == 24:
        blocks[0][0] = blocks[-1][0] - 24
        blocks.pop()
    # Trim the fractional part from the end of the longest block.
    frac = n_full - hours
    if frac > 1e-9:
        longest = max(blocks, key=lambda b: b[1] - b[0])
        longest[1] -= frac
    return [f"{_clock(s)}-{_clock(e)}" for s, e in sorted(blocks, key=lambda b: b[0] % 24)]


def _clock(h):
    minutes = round((h % 24) * 60)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def use_days(appliance):
    d = appliance.days_per_week
    if d >= 7:
        return "daily"
    n = max(1, round(d))
    return "/".join(WEEK_DAYS.get(n, ["Sat"]))


def daily_schedule(plan):
    """Rows: appliance, priority, when, daily hours, time blocks, status."""
    rows = []
    for alloc in plan.allocations:
        a = alloc.appliance
        rows.append({
            "appliance": a.name,
            "priority": "-" if a.is_fixed else a.priority,
            "type": a.type,
            "when": use_days(a),
            "hours_per_use_day": alloc.hours,
            "avg_hours_per_day": alloc.avg_hours_per_day,
            "monthly_hours": alloc.monthly_hours(plan.days_in_month),
            "monthly_kwh": alloc.monthly_kwh(plan.days_in_month),
            "time_blocks": time_blocks(a, alloc.hours),
            "status": alloc.status,
        })
    return rows


def hourly_load_kw(plan):
    """Average household load (kW) for each clock hour of a typical day,
    weighting partial-week appliances by days_per_week / 7."""
    load = [0.0] * 24
    for alloc in plan.allocations:
        a = alloc.appliance
        if alloc.hours <= 0:
            continue
        kw = a.watts * a.quantity / 1000 * a.days_per_week / 7
        if alloc.hours >= 24:
            hours_used = range(24)
        else:
            hours_used = sorted(hour_preference(a)[:math.ceil(alloc.hours - 1e-9)])
        remaining = alloc.hours
        for h in hours_used:
            portion = min(1.0, remaining)
            load[h] += kw * portion
            remaining -= portion
    return load

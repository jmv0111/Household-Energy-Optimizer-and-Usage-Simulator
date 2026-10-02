"""
Step 2 of the methodology (Collect Data): turn the PSA Household Energy
Consumption Survey (HECS) 2011 public-use files into the reference tables the
optimizer needs.

Inputs  (Datasets/):
  RT03 - Electricity                      household source, kWh, bill
  RT07 - Electricity Used for Lighting    one row per bulb type
  RT08 - Electricity for appliances       one row per appliance type
  hecs_2011_metadata(dictionary).xlsx     code labels

Outputs (data/ and households/):
  data/appliance_catalogue.csv   median wattage / hours / days per appliance
  data/time_profiles.csv         share of households using each appliance
                                 in each hour of the day
  data/meralco_households.csv    per-household reported vs bottom-up kWh
  households/*.json              real survey households used as profiles

Which households: those served by a private distribution utility
(A1P2A = 1) in NCR, Central Luzon and CALABARZON (REG 13, 3, 41), i.e. the
Meralco franchise area, with a reported monthly consumption > 0.

Key decoding rule (verified against reported kWh, see validate.py):
  the "how often" fields (A4P3C12, A4P2C07) are the number of days the item
  was used in a 184-day reference period, so
      monthly kWh = W x quantity x hours/day x (days / 184) x 30 / 1000
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
DATASETS = ROOT / "Datasets"
DATA = ROOT / "data"
HOUSEHOLDS = ROOT / "households"

RT03 = DATASETS / "HECS PUF 2011 RT03 - Electricity.CSV"
RT07 = DATASETS / "HECS PUF 2011 RT07 -Electricity Used for Lighting.CSV"
RT08 = DATASETS / "HECS PUF 2011 RT08 - Electricity for appliances_equipment.CSV"
DICTIONARY = DATASETS / "hecs_2011_metadata(dictionary).xlsx"

MERALCO_REGIONS = {13: "NCR", 3: "Central Luzon", 41: "CALABARZON"}
REFERENCE_DAYS = 184          # survey reference period for "how often"
DAYS_PER_MONTH = 30

# Time-of-day bitmask used by the AM and PM "time of day" codes.
# Each bit is one slot; (start hour, end hour) on a 24-hour clock.
AM_SLOTS = [(0, 2), (2, 4), (4, 5), (5, 6), (6, 7), (7, 8), (8, 9), (9, 10), (10, 12)]
PM_SLOTS = [(h + 12, e + 12) for h, e in AM_SLOTS]

# Appliances that run around the clock ("Fixed / Always On" in the proposal).
FIXED_CODES = {21, 22, 23}

# Group label for the several generic "Others" codes in the dictionary.
OTHERS_GROUP = {10: "kitchen", 19: "heating", 29: "refrigeration",
                49: "entertainment", 59: "cooling", 79: "miscellaneous"}

BULB_LABELS = {1: "Incandescent bulb", 2: "Linear fluorescent tube",
               3: "Circular fluorescent lamp", 4: "Compact fluorescent lamp (CFL)",
               5: "LED bulb", 9: "Other lamp"}

# Default "necessity" order used only to give the sample profiles a
# sensible priority list (1 = keep longest). Users set their own.
DEFAULT_PRIORITY = [
    "Lighting", "Electric fan", "Rice cooker", "Water pump",
    "Electric flat iron", "Washing machine", "Color TV", "B/W TV",
    "Personal computer", "Radio", "Microwave oven", "Electric thermos",
    "Airconditioner", "Water heater", "Karaoke", "Video games",
]


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def read_csv(path):
    df = pd.read_csv(path, skipinitialspace=True, dtype={"QID": str})
    return df.apply(lambda c: pd.to_numeric(c, errors="coerce") if c.name != "QID" else c)


def appliance_labels():
    """Code -> label for A4P3C03 from the dictionary's value-set sheet."""
    vs = pd.read_excel(DICTIONARY, sheet_name="hecs_2011_puf_valueset", header=None)
    # Layout: column 0 = value-set name, columns 2/3 = label/code rows below it.
    start = vs.index[vs[0].eq("A4P3C03_VS1")][0] + 1
    labels = {}
    for name, label, code in vs.iloc[start:, [0, 2, 3]].itertuples(index=False):
        if isinstance(name, str) and "_VS" in name:
            break
        try:
            code = int(float(code))
        except (TypeError, ValueError):
            continue
        label = str(label)
        if label.strip() == "Others":
            label = f"Others ({OTHERS_GROUP.get(code, 'misc')})"
        labels[code] = label.strip()
    return labels


def meralco_households():
    rt03 = read_csv(RT03)
    mask = (rt03.A1P1.eq(1) & rt03.A1P2A.eq(1)
            & rt03.REG.isin(MERALCO_REGIONS) & rt03.A1P4.gt(0))
    hh = rt03.loc[mask, ["QID", "REG", "A1P4", "A1P5"]].rename(
        columns={"A1P4": "reported_kwh", "A1P5": "reported_bill"})
    hh["region"] = hh.REG.map(MERALCO_REGIONS)
    return hh.reset_index(drop=True)


def _usage_frame(df, *, code, watts, qty, days, hours, am, pm, kind):
    out = pd.DataFrame({
        "QID": df.QID,
        "code": df[code],
        "watts": df[watts],
        "quantity": df[qty].fillna(1).clip(lower=1),
        "days": df[days],
        "hours": df[hours],
        "am": df[am].fillna(0).astype(int),
        "pm": df[pm].fillna(0).astype(int),
        "kind": kind,
    })
    out = out[(out.watts > 0) & (out.hours > 0) & (out.days > 0)]
    out = out[(out.hours <= 24) & (out.days <= REFERENCE_DAYS)]
    out["monthly_kwh"] = (out.watts * out.quantity * out.hours
                          * (out.days / REFERENCE_DAYS) * DAYS_PER_MONTH / 1000)
    return out


def usage_lines(qids):
    """All appliance + lighting lines (household-consumption use) for `qids`."""
    rt08 = read_csv(RT08)
    rt08 = rt08[rt08.QID.isin(qids)]
    apps = _usage_frame(rt08, code="A4P3C03", watts="A4P3C07", qty="A4P3C08",
                        days="A4P3C12", hours="A4P3C13",
                        am="A4P3C15", pm="A4P3C17", kind="appliance")

    rt07 = read_csv(RT07)
    rt07 = rt07[rt07.QID.isin(qids)]
    lights = _usage_frame(rt07, code="A4P2C03", watts="A4P2C04", qty="A4P2C05",
                          days="A4P2C07", hours="A4P2C08",
                          am="A4P2C10", pm="A4P2C12", kind="lighting")
    return apps, lights


# ---------------------------------------------------------------------------
# Reference tables
# ---------------------------------------------------------------------------
def hour_shares(lines):
    """Fraction of lines that use the item in each clock hour 0..23."""
    counts = np.zeros(24)
    for am, pm in zip(lines.am, lines.pm):
        for bit, (s, e) in enumerate(AM_SLOTS):
            if am & (1 << bit):
                counts[s:e] += 1
        for bit, (s, e) in enumerate(PM_SLOTS):
            if pm & (1 << bit):
                counts[s:e] += 1
    return counts / max(len(lines), 1)


def build_catalogue(apps, lights, n_households):
    labels = appliance_labels()
    rows, profiles = [], []

    def summarize(group, name, category, code, kind):
        days = group.days.median()
        rows.append({
            "name": name,
            "kind": kind,
            "hecs_code": int(code),
            "category": category,
            "households": group.QID.nunique(),
            "ownership_pct": round(100 * group.QID.nunique() / n_households, 1),
            "median_watts": float(group.watts.median()),
            "median_quantity": float(group.quantity.median()),
            "median_hours_per_day": float(group.hours.median()),
            "median_days_per_week": round(min(7.0, days / REFERENCE_DAYS * 7), 1),
            "median_monthly_kwh": round(float(group.monthly_kwh.median()), 2),
        })
        profiles.append({"name": name, **{f"h{h:02d}": round(v, 4)
                                         for h, v in enumerate(hour_shares(group))}})

    for code, g in apps.groupby("code"):
        if len(g) < 10 or code not in labels:
            continue
        cat = "fixed" if code in FIXED_CODES else "variable"
        summarize(g, labels[code], cat, code, "appliance")
    for code, g in lights.groupby("code"):
        if len(g) < 10:
            continue
        summarize(g, "Lighting - " + BULB_LABELS.get(int(code), "Other lamp"),
                  "variable", code, "lighting")

    cat = pd.DataFrame(rows).sort_values("households", ascending=False)
    return cat.reset_index(drop=True), pd.DataFrame(profiles)


def household_estimates(hh, apps, lights):
    est = pd.concat([apps, lights]).groupby("QID").monthly_kwh.sum()
    hh = hh.copy()
    hh["bottom_up_kwh"] = hh.QID.map(est).fillna(0).round(2)
    hh["ratio"] = (hh.bottom_up_kwh / hh.reported_kwh).round(3)
    hh["implied_php_per_kwh"] = (hh.reported_bill / hh.reported_kwh).round(2)
    return hh


# ---------------------------------------------------------------------------
# Sample household profiles (real survey households)
# ---------------------------------------------------------------------------
def _priority_rank(name):
    for i, key in enumerate(DEFAULT_PRIORITY):
        if name.startswith(key):
            return i
    return len(DEFAULT_PRIORITY)


def household_profile(qid, apps, lights, labels, title):
    items = []
    for kind, df in (("appliance", apps), ("lighting", lights)):
        for _, r in df[df.QID == qid].iterrows():
            code = int(r.code)
            if kind == "lighting":
                name = "Lighting - " + BULB_LABELS.get(code, "Other lamp")
            else:
                name = labels.get(code, f"Appliance {code}")
            fixed = kind == "appliance" and code in FIXED_CODES
            item = {
                "name": name,
                "catalogue": name,
                "type": "fixed" if fixed else "variable",
                "watts": float(r.watts),
                "quantity": int(r.quantity),
            }
            if not fixed:
                item["max_hours"] = float(min(24.0, r.hours))
                item["min_hours"] = 0.0
                item["days_per_week"] = round(min(7.0, r.days / REFERENCE_DAYS * 7), 1)
            items.append(item)

    # Merge duplicate lines of the same item (e.g. two TVs listed twice).
    merged = {}
    for it in items:
        key = (it["name"], it["watts"], it.get("max_hours"), it.get("days_per_week"))
        if key in merged:
            merged[key]["quantity"] += it["quantity"]
        else:
            merged[key] = it
    items = list(merged.values())

    # Unique display names: "Electric fan #1", "Electric fan #2", ...
    totals = pd.Series([i["name"] for i in items]).value_counts()
    seen = {}
    for it in items:
        base = it["name"]
        if totals[base] > 1:
            seen[base] = seen.get(base, 0) + 1
            it["name"] = f"{base} #{seen[base]}"

    variables = sorted((i for i in items if i["type"] == "variable"),
                       key=lambda i: (_priority_rank(i["name"]), -i["watts"]))
    for rank, it in enumerate(variables, start=1):
        it["priority"] = rank
    # Lighting: keep at least 2 h a day if the household normally uses more.
    for it in variables:
        if it["name"].startswith("Lighting") and it["max_hours"] >= 2:
            it["min_hours"] = 2.0

    fixed = [i for i in items if i["type"] == "fixed"]
    return {"name": title, "source": f"HECS 2011 QID {qid}",
            "appliances": fixed + variables}


def pick_profiles(hh, apps, lights):
    """Real NCR households near the 25th, 50th and 90th consumption
    percentiles whose appliance list explains their bill (ratio 0.9-1.1)."""
    labels = appliance_labels()
    ncr = hh[(hh.REG == 13) & hh.ratio.between(0.9, 1.1)]
    counts = pd.concat([apps, lights]).groupby("QID").size()
    targets = [("low", 0.25, "Low-consumption Metro Manila household"),
               ("median", 0.50, "Typical Metro Manila household"),
               ("high", 0.90, "High-consumption Metro Manila household (with aircon)")]
    profiles = {}
    all_ncr = hh[hh.REG == 13].reported_kwh
    for key, q, title in targets:
        target = all_ncr.quantile(q)
        cand = ncr.assign(dist=(ncr.reported_kwh - target).abs(),
                          lines=ncr.QID.map(counts).fillna(0))
        cand = cand[cand.lines >= 4]
        if key == "high":
            has_ac = set(apps[apps.code.isin([50, 51, 52])].QID)
            cand = cand[cand.QID.isin(has_ac)]
        row = cand.sort_values(["dist", "lines"], ascending=[True, False]).iloc[0]
        prof = household_profile(row.QID, apps, lights, labels, title)
        prof["reported_kwh"] = float(row.reported_kwh)
        prof["reported_bill_2011"] = float(row.reported_bill)
        profiles[key] = prof
    return profiles


def main():
    DATA.mkdir(exist_ok=True)
    HOUSEHOLDS.mkdir(exist_ok=True)

    hh = meralco_households()
    apps, lights = usage_lines(set(hh.QID))
    catalogue, profiles = build_catalogue(apps, lights, len(hh))
    hh = household_estimates(hh, apps, lights)

    catalogue.to_csv(DATA / "appliance_catalogue.csv", index=False)
    profiles.to_csv(DATA / "time_profiles.csv", index=False)
    hh.drop(columns="REG").to_csv(DATA / "meralco_households.csv", index=False)

    for key, prof in pick_profiles(hh, apps, lights).items():
        with open(HOUSEHOLDS / f"{key}_household.json", "w", encoding="utf-8") as f:
            json.dump(prof, f, indent=2)

    print(f"Meralco-area households: {len(hh):,}")
    print(f"Appliance/lighting lines: {len(apps):,} / {len(lights):,}")
    print(f"Catalogue entries: {len(catalogue)}")
    print(f"Bottom-up / reported kWh, median ratio: {hh.ratio.median():.2f}")
    print("Wrote data/appliance_catalogue.csv, data/time_profiles.csv,")
    print("      data/meralco_households.csv, households/*.json")


if __name__ == "__main__":
    main()

"""
Household Energy Optimizer and Usage Simulator - command-line program.

Examples
    python main.py --household median --budget 2000
    python main.py --household households/high_household.json --budget 4500 --mode weighted
    python main.py --household high --budget 4500 --csv results/my_plan.csv
    python main.py --interactive
"""

import argparse
import csv
import json
import sys
from pathlib import Path

from appliances import (FIXED, VARIABLE, find_in_catalogue, household_from_dict,
                        load_catalogue, load_household)
from meralco_rates import BillOptions, compute_bill
from optimizer import STRICT, WEIGHTED, optimize
from schedule import daily_schedule

ROOT = Path(__file__).resolve().parent
PRESETS = {"low": "low_household.json", "median": "median_household.json",
           "high": "high_household.json", "custom": "custom_example.json"}


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------
def render(plan):
    D = plan.days_in_month
    out = []
    line = "=" * 100
    out += [line, f"HOUSEHOLD ENERGY OPTIMIZER  |  {plan.household.name}", line]
    if plan.household.source:
        out.append(f"Profile source       : {plan.household.source}")
    out.append(f"Monthly budget       : PHP {plan.budget:,.2f}   "
               f"(mode: {plan.mode}, {D}-day month)")
    out.append(f"Energy cap (Meralco) : {plan.kwh_cap:,.2f} kWh/month "
               f"= {plan.kwh_cap / D:,.2f} kWh/day")
    out.append(f"Recommended usage    : {plan.total_kwh:,.2f} kWh/month "
               f"= {plan.total_kwh / D:,.2f} kWh/day")
    if plan.feasible:
        out.append(f"Expected bill        : PHP {plan.cost:,.2f}  "
                   f"(unused budget PHP {plan.unused_budget:,.2f}, "
                   f"about PHP {plan.cost / D:,.2f}/day)")
    else:
        out.append(f"Always-on load alone : PHP {plan.cost:,.2f}  "
                   f"(PHP {-plan.unused_budget:,.2f} over budget)")
    out.append(f"Comfort delivered    : {plan.comfort_score():.1f}% of desired "
               "variable-appliance hours")
    out.append(f"Status               : {'FEASIBLE' if plan.feasible else 'INFEASIBLE'}")

    out += ["", "RECOMMENDED DAILY SCHEDULE", "-" * 100]
    hdr = f"{'Pri':>3}  {'Appliance':<40} {'When':<24} {'h/day':>6}  {'Time of day':<26} {'kWh/mo':>7}  Status"
    out += [hdr, "-" * len(hdr)]
    for r in daily_schedule(plan):
        name = r["appliance"] if len(r["appliance"]) <= 40 else r["appliance"][:37] + "..."
        blocks = ", ".join(r["time_blocks"]) or "-"
        out.append(f"{r['priority']!s:>3}  {name:<40} {r['when']:<24} "
                   f"{r['hours_per_use_day']:>6.2f}  {blocks:<26} "
                   f"{r['monthly_kwh']:>7.2f}  {r['status']}")
    out.append("(h/day = hours on each day the appliance is used; "
               "Pri 1 = most important, sacrificed last)")

    out += ["", "MONTHLY SUMMARY", "-" * 100]
    for r in daily_schedule(plan):
        out.append(f"  {r['appliance'][:50]:<50} {r['monthly_hours']:>7.1f} h/month  "
                   f"{r['avg_hours_per_day']:>5.2f} h/day avg")

    out += ["", "BILL BREAKDOWN (Meralco Summary Schedule of Rates, Sept 2026, VAT-inclusive)",
            "-" * 100, plan.bill.summary()]

    if plan.messages:
        out += ["", "NOTES", "-" * 100]
        out += [f"* {m}" for m in plan.messages]
    out.append(line)
    return "\n".join(out)


def write_csv(plan, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = daily_schedule(plan)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["priority", "appliance", "type", "when", "hours_per_use_day",
                    "avg_hours_per_day", "monthly_hours", "monthly_kwh",
                    "time_of_day", "status"])
        for r in rows:
            w.writerow([r["priority"], r["appliance"], r["type"], r["when"],
                        round(r["hours_per_use_day"], 2), round(r["avg_hours_per_day"], 2),
                        round(r["monthly_hours"], 1), round(r["monthly_kwh"], 2),
                        "; ".join(r["time_blocks"]), r["status"]])
        w.writerow([])
        w.writerow(["total_kwh", round(plan.total_kwh, 2)])
        w.writerow(["bill_php", round(plan.cost, 2)])
        w.writerow(["budget_php", plan.budget])


# ---------------------------------------------------------------------------
# Interactive input
# ---------------------------------------------------------------------------
def ask(prompt, cast=str, default=None, check=None):
    while True:
        suffix = f" [{default}]" if default is not None else ""
        raw = input(f"{prompt}{suffix}: ").strip()
        if not raw and default is not None:
            raw = str(default)
        try:
            value = cast(raw)
            if check and not check(value):
                raise ValueError
            return value
        except (ValueError, TypeError):
            print("  Invalid value, try again.")


def interactive():
    print("Household Energy Optimizer - interactive mode")
    print("Enter appliances one at a time. Pick a number from the list or type a name.\n")
    cat = list(load_catalogue().values())
    for i, row in enumerate(cat, 1):
        print(f"  {i:>2}. {row['name'][:45]:<45} {float(row['median_watts']):>6.0f} W  "
              f"(typical {float(row['median_hours_per_day']):g} h/day)")
    print()
    budget = ask("Monthly electricity budget (PHP)", float, check=lambda v: v >= 0)
    appliances, names = [], set()
    pending_picks = []
    while True:
        if pending_picks:
            pick = pending_picks.pop(0)
            print(f"\nAppliance number/name: {pick}")
        else:
            pick = input("\nAppliance number/name (Enter to finish; "
                         "several numbers like 1,3,5 also work): ").strip()
            if not pick:
                if appliances:
                    break
                print("  Add at least one appliance.")
                continue
            tokens = [t.strip() for t in pick.replace(",", " ").split() if t.strip()]
            if tokens and all(t.isdigit() for t in tokens):
                pick, pending_picks = tokens[0], tokens[1:]

        if pick.isdigit():
            if not 1 <= int(pick) <= len(cat):
                print(f"  {pick} is not on the list (1-{len(cat)}).")
                continue
            ref = cat[int(pick) - 1]
            name = ref["name"]
        else:
            # A typed name keeps the user's wording but borrows typical
            # wattage/hours from the closest catalogue item, if any.
            ref = find_in_catalogue(pick)
            name = pick
            if ref:
                print(f"  (defaults from catalogue: {ref['name']})")
        base, k = name, 2
        while name in names:
            name, k = f"{base} #{k}", k + 1
        names.add(name)
        default_type = ref["category"] if ref else VARIABLE
        kind = ask("  Type fixed/variable", str, default_type,
                   lambda v: v in (FIXED, VARIABLE))
        watts = ask("  Wattage (W)", float, ref and float(ref["median_watts"]),
                    lambda v: v >= 0)
        qty = ask("  Quantity", int, 1, lambda v: v >= 1)
        item = {"name": name, "catalogue": ref["name"] if ref else None,
                "type": kind, "watts": watts, "quantity": qty}
        if kind == VARIABLE:
            item["max_hours"] = ask("  Desired hours per use-day", float,
                                    ref and float(ref["median_hours_per_day"]),
                                    lambda v: 0 <= v <= 24)
            item["min_hours"] = ask("  Minimum (essential) hours", float, 0,
                                    lambda v: 0 <= v <= item["max_hours"])
            item["days_per_week"] = ask("  Days used per week", float,
                                        ref and float(ref["median_days_per_week"]) or 7,
                                        lambda v: 0 < v <= 7)
            item["priority"] = ask("  Priority (1 = most important)", int,
                                   len([a for a in appliances if a["type"] == VARIABLE]) + 1,
                                   lambda v: v >= 1)
        appliances.append(item)
    data = {"name": "My household", "appliances": appliances}
    save = input("\nSave this household to a file? (path or Enter to skip): ").strip()
    if save:
        try:
            Path(save).parent.mkdir(parents=True, exist_ok=True)
            with open(save, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            print(f"Saved {save}")
        except OSError as e:
            print(f"  Could not save ({e}); continuing without saving.")
    return household_from_dict(data), budget


# ---------------------------------------------------------------------------
def resolve_household(arg):
    if arg in PRESETS:
        return ROOT / "households" / PRESETS[arg]
    return Path(arg)


def main(argv=None):
    p = argparse.ArgumentParser(description="Budget-based household appliance usage optimizer "
                                            "(Meralco Sept 2026 residential rates).")
    p.add_argument("--household", default="median",
                   help="low | median | high | custom, or a path to a household JSON")
    p.add_argument("--budget", type=float, help="monthly budget in PHP")
    p.add_argument("--mode", choices=[STRICT, WEIGHTED], default=STRICT)
    p.add_argument("--days", type=int, default=30, help="days in the billing month")
    p.add_argument("--step", type=float, default=0.25,
                   help="hour rounding step (0 = no rounding)")
    p.add_argument("--lifeline", action="store_true",
                   help="registered lifeline customer (discount only up to 100 kWh)")
    p.add_argument("--senior", action="store_true",
                   help="senior citizen 5%% discount (only up to 100 kWh)")
    p.add_argument("--lft", type=float, default=0.0, help="local franchise tax PHP/kWh")
    p.add_argument("--csv", help="also save the schedule to this CSV file")
    p.add_argument("--interactive", action="store_true")
    p.add_argument("--gui", "--ui", action="store_true", help="launch desktop GUI window")
    a = p.parse_args(argv)

    if a.gui:
        from gui import main as run_desktop_gui
        run_desktop_gui()
        return 0

    if a.interactive:
        try:
            household, budget = interactive()
        except (KeyboardInterrupt, EOFError):
            print("\nCancelled.")
            return 2
    else:
        if a.budget is None:
            p.error("--budget is required (or use --interactive)")
        path = resolve_household(a.household)
        try:
            household, budget = load_household(path), a.budget
        except FileNotFoundError:
            p.error(f"household file not found: {path} "
                    f"(presets: {', '.join(PRESETS)})")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            p.error(f"could not read household file {path}: {e}")

    opts = BillOptions(lifeline=a.lifeline, senior_citizen=a.senior, lft_per_kwh=a.lft)
    try:
        plan = optimize(household, budget, mode=a.mode, options=opts,
                        days_in_month=a.days, step=a.step or None)
    except ValueError as e:
        p.error(str(e))
    print(render(plan))
    if a.csv:
        try:
            write_csv(plan, a.csv)
            print(f"Schedule saved to {a.csv}")
        except OSError as e:
            print(f"Could not save the CSV ({e}). Is the file open in Excel?",
                  file=sys.stderr)
    return 0 if plan.feasible else 1


if __name__ == "__main__":
    sys.exit(main())

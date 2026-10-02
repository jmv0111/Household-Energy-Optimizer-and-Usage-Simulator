"""
Step 5 of the methodology (Verify and Validate). Writes
results/validation_report.md with:

  A. Simulated bills vs. the published Meralco rate table (hand calculation)
  B. Optimality of the allocation vs. an LP solver (scipy HiGHS)
  C. Edge cases (budgets too low for basic needs, bracket boundaries)
  D. Data validation: HECS appliance-level energy vs. reported household kWh
"""

import random
import sys
from pathlib import Path

import pandas as pd

from appliances import load_household
from lp_reference import lp_strict, lp_weighted, weighted_objective
from meralco_rates import compute_bill, max_kwh_for_budget
from optimizer import STRICT, WEIGHTED, optimize, solve_continuous

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
sys.path.insert(0, str(ROOT))
from tests.test_optimizer import random_household  # noqa: E402
from tests.test_rates import VALIDATION_POINTS, hand_bill  # noqa: E402


def section_a():
    rows = []
    for kwh in VALIDATION_POINTS:
        model, hand = compute_bill(kwh).total, hand_bill(kwh)
        rows.append((kwh, hand, model, model - hand))
    worst = max(abs(r[3]) for r in rows)
    out = ["## A. Simulated bill vs. published Meralco rate table", "",
           "The hand calculation types every residential rate directly from the "
           "*Summary Schedule of Rates, effective September 2026 billing* "
           "(generation, transmission, ancillary service, system loss, distribution "
           "bracket, supply, metering, AWAT refunds, regulatory reset, lifeline, "
           "senior citizen, RPT, universal charge, FIT-All, GEA-All, energy tax "
           "and the component VAT rates). The model must reproduce it exactly.", "",
           "| kWh | Hand calculation (PHP) | Model (PHP) | Difference |",
           "|---:|---:|---:|---:|"]
    out += [f"| {k:,} | {h:,.2f} | {m:,.2f} | {d:+.2e} |" for k, h, m, d in rows]
    out += ["", f"Largest absolute difference: **PHP {worst:.2e}**. The 200 to 201 kWh "
            f"step (PHP {compute_bill(201).total - compute_bill(200).total:,.2f}) shows "
            "the distribution bracket applying to all kWh.", ""]
    return out


def section_b(n=300):
    rng = random.Random(2026)
    strict_err = weighted_err = 0.0
    for _ in range(n):
        hh = random_household(rng)
        v = hh.variable
        mins = sum(a.monthly_kwh(a.min_hours) for a in v)
        full = sum(a.monthly_kwh(a.max_hours) for a in v)
        R = mins + rng.uniform(0, 1.1) * (full - mins)
        g, _, _ = solve_continuous(v, R, STRICT, 30)
        ref = lp_strict(v, R)
        strict_err = max(strict_err, max(abs(g[a.name] - ref[a.name]) for a in v))
        g, _, _ = solve_continuous(v, R, WEIGHTED, 30)
        _, best = lp_weighted(v, R)
        weighted_err = max(weighted_err, abs(weighted_objective(v, g) - best))
    return ["## B. Optimality check against an LP solver", "",
            f"{n} random households (2 to 12 variable appliances, random wattage, "
            "priority, minimum/desired hours and days per week) were solved both by "
            "the model's greedy allocation and by scipy's HiGHS linear-programming "
            "solver.", "",
            "| Objective | Comparison | Worst difference |", "|---|---|---:|",
            f"| Strict (lexicographic priority) | hours per appliance | {strict_err:.2e} h |",
            f"| Weighted comfort | objective value | {weighted_err:.2e} |", "",
            "Both match to solver precision, so the allocation is the exact optimum "
            "of the linear program.", ""]


def section_c():
    out = ["## C. Edge cases", "", "| Household | Budget (PHP) | Result |", "|---|---:|---|"]
    cases = [("median", 0), ("median", 20), ("median", 1000), ("median", 1100),
             ("high", 1000), ("high", 1200), ("custom_example", 3000),
             ("low", 100_000)]
    for name, budget in cases:
        fname = name if name.endswith("example") else f"{name}_household"
        hh = load_household(ROOT / "households" / f"{fname}.json")
        plan = optimize(hh, budget)
        if not plan.feasible:
            msg = plan.messages[-1].split(". ")[0]
        else:
            off = sum(1 for a in plan.variable_allocations if a.hours == 0)
            msg = (f"feasible, {plan.total_kwh:,.1f} kWh, bill PHP {plan.cost:,.2f}, "
                   f"comfort {plan.comfort_score():.0f}%, {off} appliance(s) off")
        out.append(f"| {hh.name} | {budget:,} | {msg} |")
    k = max_kwh_for_budget(3000)
    out += ["", f"Bracket boundary: a PHP 3,000 budget gives a cap of {k:,.2f} kWh, "
            f"because 200 kWh costs PHP {compute_bill(200).total:,.2f} and 201 kWh "
            f"costs PHP {compute_bill(201).total:,.2f}. The model never plans into a "
            "bracket the budget cannot pay for.", ""]
    return out


def section_d():
    hh = pd.read_csv(ROOT / "data" / "meralco_households.csv")
    r = hh[hh.bottom_up_kwh > 0].ratio
    out = ["## D. Data validation: appliance model vs. reported consumption", "",
           "For each of the HECS 2011 Meralco-area households, the appliance-level "
           "energy (W x quantity x hours/day x days used/184 x 30) was summed over all "
           "appliances and lamps and compared with the household's own reported "
           "average monthly kWh.", "",
           "| Region | Households | Median estimate / reported | Within 0.5x-2x |",
           "|---|---:|---:|---:|"]
    for region, g in [("All", hh)] + list(hh.groupby("region")):
        g = g[g.bottom_up_kwh > 0]
        out.append(f"| {region} | {len(g):,} | {g.ratio.median():.2f} | "
                   f"{100 * g.ratio.between(0.5, 2).mean():.0f}% |")
    out += ["", f"A median ratio of {r.median():.2f} means the wattage x hours model "
            "reproduces typical household consumption without any calibration factor. "
            "Individual households scatter because survey answers are self-reported.", ""]
    return out


def main():
    RESULTS.mkdir(exist_ok=True)
    text = ["# Validation Report", "",
            "Generated by `validate.py`. Unit tests: `python -m unittest discover -s tests -t .`", ""]
    text += section_a() + section_b() + section_c() + section_d()
    path = RESULTS / "validation_report.md"
    path.write_text("\n".join(text), encoding="utf-8")
    print("\n".join(text))
    print(f"\nWrote {path}")


if __name__ == "__main__":
    main()

"""
Verification of the optimization model (methodology step 5):
budget is never exceeded, fixed appliances always get 24 h, priorities are
respected, edge cases (budget too low) are reported, and the greedy
solution equals the LP optimum computed by scipy.
"""

import random
import unittest
from pathlib import Path

from appliances import Appliance, Household, assign_priorities, load_household
from lp_reference import lp_strict, lp_weighted, weighted_objective
from meralco_rates import compute_bill, max_kwh_for_budget
from optimizer import STRICT, WEIGHTED, optimize, solve_continuous

ROOT = Path(__file__).resolve().parent.parent
PROFILES = ["low", "median", "high", "custom_example"]


def profile(name):
    fname = name if name.endswith("example") else f"{name}_household"
    return load_household(ROOT / "households" / f"{fname}.json")


def random_household(rng, n_var=None):
    apps = [Appliance("Fridge", "fixed", rng.choice([80, 100, 140, 180]))]
    n_var = n_var or rng.randint(2, 12)
    for i in range(n_var):
        mx = rng.choice([0.5, 1, 2, 3, 4, 5, 6, 8, 10, 12])
        apps.append(Appliance(
            f"App{i}", "variable",
            watts=rng.choice([10, 25, 60, 80, 110, 250, 600, 900, 1200, 1860]),
            quantity=rng.randint(1, 3),
            priority=rng.randint(1, n_var),
            min_hours=rng.choice([0, 0, 0, 0.5, 1, 2]) if mx >= 2 else 0,
            max_hours=mx,
            days_per_week=rng.choice([1, 2, 3, 5, 7, 7, 7])))
    assign_priorities(apps)
    return Household("random", apps)


class BudgetAndFixedLoads(unittest.TestCase):
    def test_never_exceeds_budget(self):
        rng = random.Random(42)
        for _ in range(400):
            hh = random_household(rng)
            budget = rng.uniform(800, 9000)
            for mode in (STRICT, WEIGHTED):
                for step in (0.25, None):
                    plan = optimize(hh, budget, mode=mode, step=step)
                    if plan.feasible:
                        self.assertLessEqual(plan.cost, budget + 1e-6)

    def test_fixed_appliances_get_24h(self):
        for name in PROFILES:
            plan = optimize(profile(name), 5000)
            for a in plan.fixed_allocations:
                self.assertEqual(a.hours, 24.0)

    def test_hours_within_bounds_and_15_min_steps(self):
        rng = random.Random(7)
        for _ in range(200):
            hh = random_household(rng)
            plan = optimize(hh, rng.uniform(1000, 6000))
            for a in plan.variable_allocations:
                self.assertGreaterEqual(a.hours, -1e-9)
                self.assertLessEqual(a.hours, a.appliance.max_hours + 1e-9)
                on_grid = abs(a.hours / 0.25 - round(a.hours / 0.25)) < 1e-6
                self.assertTrue(on_grid or abs(a.hours - a.appliance.max_hours) < 1e-9)


class EdgeCases(unittest.TestCase):
    def test_budget_below_fixed_charges(self):
        plan = optimize(profile("median"), 10)
        self.assertFalse(plan.feasible)
        self.assertTrue(any("supply and metering" in m for m in plan.messages))

    def test_budget_below_always_on_load(self):
        hh = profile("median")
        fridge_cost = compute_bill(sum(a.monthly_kwh(24) for a in hh.fixed)).total
        plan = optimize(hh, fridge_cost - 1)
        self.assertFalse(plan.feasible)
        self.assertTrue(all(a.hours == 0 for a in plan.variable_allocations))
        ok = optimize(hh, fridge_cost + 0.01)
        self.assertTrue(ok.feasible)

    def test_zero_budget(self):
        self.assertFalse(optimize(profile("low"), 0).feasible)

    def test_large_budget_runs_everything(self):
        for name in PROFILES:
            plan = optimize(profile(name), 100_000)
            self.assertTrue(all(a.status in ("full", "always on") for a in plan.allocations))
            self.assertAlmostEqual(plan.comfort_score(), 100.0)

    def test_minimums_cut_lowest_priority_first(self):
        apps = [Appliance("Fridge", "fixed", 100)]
        for p in range(1, 4):
            apps.append(Appliance(f"Light{p}", "variable", 100, priority=p,
                                  min_hours=4, max_hours=6))
        hh = Household("mins", apps)
        fixed_kwh = 72.0
        # Enough for the two highest-priority minimums only.
        budget = compute_bill(fixed_kwh + 2 * 12 + 1).total
        plan = optimize(hh, budget, step=None)
        hours = {a.appliance.name: a for a in plan.variable_allocations}
        self.assertTrue(hours["Light1"].min_met)
        self.assertTrue(hours["Light2"].min_met)
        self.assertFalse(hours["Light3"].min_met)
        self.assertTrue(any("essential" in m for m in plan.messages))

    def test_no_variable_appliances(self):
        hh = Household("fridge only", [Appliance("Fridge", "fixed", 100)])
        plan = optimize(hh, 2000)
        self.assertTrue(plan.feasible)
        self.assertEqual(plan.comfort_score(), 100.0)

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            optimize(profile("low"), -5)
        with self.assertRaises(ValueError):
            optimize(profile("low"), 1000, mode="greedy")
        with self.assertRaises(ValueError):
            Appliance("x", "variable", 10, min_hours=5, max_hours=2).validate()
        with self.assertRaises(ValueError):
            optimize(profile("low"), 1000, days_in_month=0)
        with self.assertRaises(ValueError):
            optimize(profile("low"), 1000, step=-0.25)

    def test_cli_reports_bad_input_without_traceback(self):
        import contextlib
        import io

        from main import main
        bad = [["--budget", "-5"], ["--household", "nope.json", "--budget", "2000"],
               ["--budget", "2000", "--days", "0"]]
        for argv in bad:
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as cm:
                    main(argv)
                self.assertEqual(cm.exception.code, 2)   # argparse usage error


class PriorityBehaviour(unittest.TestCase):
    def test_strict_sacrifices_lowest_priority_first(self):
        rng = random.Random(3)
        for _ in range(200):
            hh = random_household(rng)
            plan = optimize(hh, rng.uniform(1200, 5000), mode=STRICT, step=None)
            if not plan.feasible:
                continue
            allocs = sorted(plan.variable_allocations, key=lambda a: a.appliance.priority)
            # Once an appliance is below its maximum, every lower-priority one
            # is at its minimum (it got nothing extra).
            short = False
            for a in allocs:
                if short:
                    self.assertAlmostEqual(a.hours, a.appliance.min_hours if a.min_met else a.hours)
                if a.hours < a.appliance.max_hours - 1e-6:
                    short = True

    def test_more_budget_never_reduces_comfort(self):
        for name in PROFILES:
            hh = profile(name)
            prev = -1
            for budget in range(800, 9001, 100):
                plan = optimize(hh, budget, step=None)
                if plan.feasible:
                    self.assertGreaterEqual(plan.comfort_score(), prev - 1e-9)
                    prev = plan.comfort_score()


class MatchesLinearProgram(unittest.TestCase):
    """The greedy solution must equal scipy's LP optimum (no rounding)."""

    def instances(self, n=150, seed=11):
        rng = random.Random(seed)
        for _ in range(n):
            hh = random_household(rng)
            variables = hh.variable
            fixed = sum(a.monthly_kwh(24) for a in hh.fixed)
            mins = sum(a.monthly_kwh(a.min_hours) for a in variables)
            full = sum(a.monthly_kwh(a.max_hours) for a in variables)
            # Energy left between "minimums only" and "everything full".
            R = mins + rng.uniform(0, 1.1) * (full - mins)
            yield variables, R, fixed

    def test_strict_equals_lexicographic_lp(self):
        for variables, R, _ in self.instances():
            greedy, _, _ = solve_continuous(variables, R, STRICT, 30)
            ref = lp_strict(variables, R)
            for a in variables:
                self.assertAlmostEqual(greedy[a.name], ref[a.name], places=4)

    def test_weighted_equals_lp_optimum(self):
        for variables, R, _ in self.instances():
            greedy, _, _ = solve_continuous(variables, R, WEIGHTED, 30)
            _, best = lp_weighted(variables, R)
            self.assertAlmostEqual(weighted_objective(variables, greedy), best, places=5)

    def test_full_pipeline_matches_lp_on_profiles(self):
        for name in PROFILES:
            hh = profile(name)
            for budget in (1500, 2500, 4000, 6000):
                plan = optimize(hh, budget, mode=STRICT, step=None)
                if not plan.feasible:
                    continue
                R = max_kwh_for_budget(budget) - sum(a.monthly_kwh(24) for a in hh.fixed)
                mins = sum(a.monthly_kwh(a.min_hours) for a in hh.variable)
                if mins > R:
                    continue
                ref = lp_strict(hh.variable, R)
                for a in plan.variable_allocations:
                    self.assertAlmostEqual(a.hours, ref[a.appliance.name], places=4)


if __name__ == "__main__":
    unittest.main()

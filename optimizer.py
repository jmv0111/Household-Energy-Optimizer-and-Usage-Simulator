"""
Steps 3-4 of the methodology: the budget-to-usage optimization model.

Mathematical model
------------------
Inputs: monthly budget B (PHP); fixed appliances F; variable appliances V,
each with priority rank r_i (1 = most important), bounds
0 <= m_i <= M_i <= 24 (hours per use-day), and energy coefficient
e_i = W_i * q_i / 1000 * (d_i / 7) * D (kWh per month per hour/use-day).

1. Budget -> kWh cap. The Meralco bill C(E) is non-decreasing in monthly
   energy E, with step increases at the distribution brackets
   (200 / 300 / 400 kWh). So
       C(E) <= B   <=>   E <= K*,   K* = max{E : C(E) <= B},
   which `meralco_rates.max_kwh_for_budget` finds by bisection. This maps
   the tiered tariff to one linear energy cap, so the problem stays an LP.

2. Fixed appliances get 24 h:  E_F = sum_{f in F} 24 * e_f.
   If E_F > K* the budget cannot run the always-on load (infeasible).

3. Variable appliances, decision variables h_i (hours per use-day):
       maximize    U(h)
       subject to  sum_i e_i h_i <= R = K* - E_F
                   m_i <= h_i <= M_i
   Two objectives are offered:
     * strict   (default): lexicographic. Maximize h_1, then h_2 given h_1,
                ... The lowest-priority appliance is always sacrificed first,
                as the proposal's scope requires.
     * weighted: maximize sum_i w_i h_i / M_i, w_i = N - r_i + 1
                (priority-weighted share of the desired hours). It may keep
                a cheap low-priority appliance (fan) over an expensive
                high-priority one (aircon) if that gives more total comfort.
   Both are LPs with a single knapsack constraint plus box bounds, so the
   greedy solution (fill in priority order / by w_i/(M_i e_i)) is exactly
   optimal (fractional knapsack). validate.py and the tests confirm this
   against scipy's LP solver.

   Minimum hours are treated as essentials. They are satisfied first, in
   priority order. If R cannot cover them all, the lowest-priority minimums
   are the ones cut.

4. Practical schedule: hours are rounded down to 15-minute steps and the
   leftover energy is redistributed in the same order, so the final plan
   never exceeds the budget.
"""

import math
from dataclasses import dataclass, field

from appliances import Household
from meralco_rates import (BillOptions, DISTRIBUTION_BRACKETS, compute_bill,
                           max_kwh_for_budget)

STRICT = "strict"
WEIGHTED = "weighted"
EPS = 1e-9


@dataclass
class Allocation:
    appliance: object
    hours: float                      # per use-day
    min_met: bool = True

    @property
    def avg_hours_per_day(self):
        return self.hours * self.appliance.days_per_week / 7

    def monthly_hours(self, days_in_month):
        return self.avg_hours_per_day * days_in_month

    def monthly_kwh(self, days_in_month):
        return self.appliance.monthly_kwh(self.hours, days_in_month)

    @property
    def status(self):
        a = self.appliance
        if a.is_fixed:
            return "always on"
        if self.hours <= EPS:
            return "OFF (sacrificed)"
        if self.hours >= a.max_hours - EPS:
            return "full"
        if not self.min_met:
            return "below minimum"
        if self.hours <= a.min_hours + EPS and a.min_hours > 0:
            return "minimum only"
        return "reduced"


@dataclass
class Plan:
    household: Household
    budget: float
    mode: str
    days_in_month: int
    options: BillOptions
    kwh_cap: float
    allocations: list
    feasible: bool
    messages: list = field(default_factory=list)

    @property
    def total_kwh(self):
        return sum(a.monthly_kwh(self.days_in_month) for a in self.allocations)

    @property
    def bill(self):
        return compute_bill(self.total_kwh, self.options)

    @property
    def cost(self):
        return self.bill.total

    @property
    def unused_budget(self):
        return self.budget - self.cost

    @property
    def fixed_allocations(self):
        return [a for a in self.allocations if a.appliance.is_fixed]

    @property
    def variable_allocations(self):
        return [a for a in self.allocations if not a.appliance.is_fixed]

    def comfort_score(self):
        """Share of desired variable-appliance hours delivered (0-100%)."""
        want = sum(a.appliance.max_hours for a in self.variable_allocations)
        got = sum(a.hours for a in self.variable_allocations)
        return 100.0 * got / want if want else 100.0


# ---------------------------------------------------------------------------
# Core LP solvers (continuous)
# ---------------------------------------------------------------------------
def fill_order(variables, mode, days_in_month):
    """Order in which extra energy is given to appliances (phase 2)."""
    if mode == STRICT:
        return sorted(variables, key=lambda a: a.priority)
    if mode == WEIGHTED:
        n = len(variables)

        def value_per_kwh(a):
            e = a.kwh_per_hour(days_in_month)
            w = n - a.priority + 1
            if e <= 0 or a.max_hours <= 0:
                return math.inf
            return w / (a.max_hours * e)
        return sorted(variables, key=lambda a: (-value_per_kwh(a), a.priority))
    raise ValueError(f"unknown mode {mode!r}; use 'strict' or 'weighted'")


def solve_continuous(variables, energy_left, mode, days_in_month):
    """Optimal h_i (no rounding). Returns (hours dict, min_met dict, R left)."""
    hours = {a.name: 0.0 for a in variables}
    min_met = {a.name: True for a in variables}
    R = energy_left

    # Phase 1: essentials (minimum hours), highest priority first.
    for a in sorted(variables, key=lambda a: a.priority):
        if a.min_hours <= 0:
            continue
        e = a.kwh_per_hour(days_in_month)
        need = e * a.min_hours
        if need <= R + EPS:
            hours[a.name] = a.min_hours
            R -= need
        else:
            hours[a.name] = R / e if e > 0 else a.min_hours
            R -= e * hours[a.name]
            min_met[a.name] = False

    # Phase 2: extra hours up to the desired maximum.
    for a in fill_order(variables, mode, days_in_month):
        e = a.kwh_per_hour(days_in_month)
        room = a.max_hours - hours[a.name]
        if room <= EPS:
            continue
        add = room if e <= 0 else min(room, max(R, 0.0) / e)
        hours[a.name] += add
        R -= e * add
    return hours, min_met, R


def round_down_and_refill(variables, hours, energy_left, mode, days_in_month,
                          step):
    """Floor to `step` hours, then add steps back in fill order while the
    energy cap allows. Keeps the plan within budget."""
    rounded = {}
    for a in variables:
        h = math.floor(hours[a.name] / step + 1e-6) * step
        rounded[a.name] = min(h, a.max_hours)
    used = sum(a.kwh_per_hour(days_in_month) * rounded[a.name] for a in variables)
    R = energy_left - used

    def next_step(a, limit):
        # A full step, or the last partial piece up to `limit` (e.g. a
        # toaster wanted for only 0.1 h a day).
        return min(step, limit - rounded[a.name])

    # Restore essentials (minimum hours) first, highest priority first.
    for a in sorted(variables, key=lambda a: a.priority):
        e = a.kwh_per_hour(days_in_month)
        while rounded[a.name] + EPS < a.min_hours:
            s = next_step(a, a.min_hours)
            if e * s > R + EPS:
                break
            rounded[a.name] += s
            R -= e * s

    # Then extra steps in the mode's fill order (greedy, same as the LP).
    order = fill_order(variables, mode, days_in_month)
    progress = True
    while progress:
        progress = False
        for a in order:
            e = a.kwh_per_hour(days_in_month)
            s = next_step(a, a.max_hours)
            if s > EPS and e * s <= R + EPS:
                rounded[a.name] += s
                R -= e * s
                progress = True
                break
    return rounded


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def optimize(household, budget, mode=STRICT, options=None, days_in_month=30,
             step=0.25):
    """Recommended usage plan for `household` under a monthly `budget`.

    step: rounding granularity in hours (0.25 = 15 min). None = continuous.
    """
    opts = options or BillOptions()
    if mode not in (STRICT, WEIGHTED):
        raise ValueError(f"unknown mode {mode!r}; use 'strict' or 'weighted'")
    if budget < 0:
        raise ValueError("budget cannot be negative")
    if days_in_month <= 0:
        raise ValueError("days in the billing month must be at least 1")
    if step is not None and step <= 0:
        raise ValueError("rounding step must be positive (or None for no rounding)")

    fixed = household.fixed
    variables = household.variable
    messages = []

    base_bill = compute_bill(0, opts).total
    e_fixed = sum(a.monthly_kwh(24, days_in_month) for a in fixed)
    fixed_cost = compute_bill(e_fixed, opts).total

    allocations = [Allocation(a, 24.0) for a in fixed]

    if budget < base_bill:
        kwh_cap = 0.0
    else:
        kwh_cap = max_kwh_for_budget(budget, opts)

    if budget < fixed_cost:
        allocations += [Allocation(a, 0.0, min_met=a.min_hours <= 0)
                        for a in variables]
        if budget < base_bill:
            messages.append(
                f"INFEASIBLE: PHP {budget:,.2f} does not even cover the fixed "
                f"monthly supply and metering charges (PHP {base_bill:,.2f}).")
        messages.append(
            f"INFEASIBLE: the always-on appliances alone use {e_fixed:,.1f} kWh "
            f"and cost PHP {fixed_cost:,.2f} a month, more than the "
            f"PHP {budget:,.2f} budget. Raise the budget by at least "
            f"PHP {fixed_cost - budget:,.2f}, or replace/unplug an always-on "
            "appliance. Every variable appliance is set to OFF.")
        return Plan(household, budget, mode, days_in_month, opts, kwh_cap,
                    allocations, feasible=False, messages=messages)

    energy_left = kwh_cap - e_fixed
    hours, min_met, _ = solve_continuous(variables, energy_left, mode,
                                         days_in_month)
    if step:
        hours = round_down_and_refill(variables, hours, energy_left, mode,
                                      days_in_month, step)
        for a in variables:
            min_met[a.name] = hours[a.name] >= a.min_hours - EPS

    allocations += [Allocation(a, hours[a.name], min_met[a.name])
                    for a in variables]
    plan = Plan(household, budget, mode, days_in_month, opts, kwh_cap,
                allocations, feasible=True, messages=messages)
    _add_advice(plan)
    return plan


# ---------------------------------------------------------------------------
# Plain-language advice
# ---------------------------------------------------------------------------
def _add_advice(plan):
    msgs = plan.messages
    D = plan.days_in_month
    unmet = [a.appliance.name for a in plan.variable_allocations if not a.min_met]
    if unmet:
        need = sum(a.appliance.monthly_kwh(a.appliance.min_hours, D)
                   for a in plan.allocations)
        msgs.append(
            "Budget too low for all essential (minimum) hours. Not guaranteed: "
            + ", ".join(unmet) + ". Budget needed for every minimum: "
            f"PHP {compute_bill(need, plan.options).total:,.2f}.")

    off = [a.appliance.name for a in plan.variable_allocations
           if a.hours <= EPS and a.appliance.max_hours > 0]
    if off:
        msgs.append("Sacrificed (OFF this month): " + ", ".join(off) + ".")

    full_kwh = sum(a.appliance.monthly_kwh(a.appliance.max_hours, D)
                   for a in plan.allocations)
    full_cost = compute_bill(full_kwh, plan.options).total
    if all(a.status in ("full", "always on") for a in plan.allocations):
        msgs.append(
            f"The budget covers every appliance at its desired hours: "
            f"expected bill PHP {plan.cost:,.2f}, PHP {plan.unused_budget:,.2f} "
            "below budget.")
    else:
        msgs.append(
            f"Running everything at the desired hours would use "
            f"{full_kwh:,.1f} kWh and cost PHP {full_cost:,.2f} "
            f"(PHP {full_cost - plan.budget:,.2f} over budget).")

    # Distribution bracket advice: the bracket rate applies to ALL kWh, so
    # crossing 200/300/400 kWh by a little is expensive.
    E = plan.total_kwh
    edges = [u for u, _ in DISTRIBUTION_BRACKETS if math.isfinite(u)]
    lower = [b for b in edges if b < E]
    if lower:
        b = max(lower)
        cut = E - b
        saving = plan.cost - compute_bill(b, plan.options).total
        if cut <= 0.10 * E:
            msgs.append(
                f"Bracket tip: you are {cut:,.1f} kWh above the {b:.0f} kWh "
                f"distribution bracket. Trimming that much saves "
                f"PHP {saving:,.2f}, about PHP {saving / cut:,.2f} per kWh cut, "
                "because the lower distribution rate would apply to every kWh.")

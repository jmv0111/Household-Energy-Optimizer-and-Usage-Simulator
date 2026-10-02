"""
Reference solutions from a general-purpose LP solver (scipy HiGHS), used
only to verify that optimizer.py's greedy allocation is the exact optimum
of the model's linear program. Not needed to run the optimizer itself.
"""

import numpy as np
from scipy.optimize import linprog


def _bounds(variables):
    return [(a.min_hours, a.max_hours) for a in variables]


def lp_weighted(variables, energy_left, days_in_month=30):
    """max sum w_i h_i / M_i  s.t. sum e_i h_i <= R, m_i <= h_i <= M_i."""
    n = len(variables)
    c = np.array([-(n - a.priority + 1) / a.max_hours if a.max_hours > 0 else 0.0
                  for a in variables])
    e = np.array([[a.kwh_per_hour(days_in_month) for a in variables]])
    res = linprog(c, A_ub=e, b_ub=[energy_left], bounds=_bounds(variables),
                  method="highs")
    if not res.success:
        raise RuntimeError(res.message)
    return {a.name: h for a, h in zip(variables, res.x)}, -res.fun


def lp_strict(variables, energy_left, days_in_month=30):
    """Lexicographic LP: maximize h of priority 1, fix it, then priority 2..."""
    order = sorted(variables, key=lambda a: a.priority)
    e = np.array([[a.kwh_per_hour(days_in_month) for a in order]])
    bounds = _bounds(order)
    for k in range(len(order)):
        c = np.zeros(len(order))
        c[k] = -1.0
        res = linprog(c, A_ub=e, b_ub=[energy_left], bounds=bounds, method="highs")
        if not res.success:
            raise RuntimeError(res.message)
        best = min(max(res.x[k], bounds[k][0]), bounds[k][1])
        bounds[k] = (best, best)          # fix exactly before the next level
    return {a.name: b[1] for a, b in zip(order, bounds)}


def weighted_objective(variables, hours):
    n = len(variables)
    return sum((n - a.priority + 1) * hours[a.name] / a.max_hours
               for a in variables if a.max_hours > 0)

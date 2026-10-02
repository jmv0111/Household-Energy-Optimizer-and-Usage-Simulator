"""
Step 6 of the methodology (Analyze Results): run the household profiles
across a range of budgets and both objectives, and show how the
recommendations shift.

Writes to results/:
  scenarios.csv              one row per (profile, budget, mode, appliance)
  scenario_summary.csv       one row per (profile, budget, mode)
  scenario_summary.md        key thresholds and tables used in the report
  fig1_bill_curve.png        Meralco bill vs kWh (bracket jumps)
  fig2_hecs_kwh.png          HECS 2011 Meralco-area consumption distribution
  fig3_hours_<profile>.png   recommended hours per appliance vs budget
  fig4_kwh_<profile>.png     monthly kWh by appliance vs budget (stacked)
  fig5_comfort.png           comfort score vs budget, all profiles, both modes
  fig6_hourly_load.png       typical-day load profile at three budgets
  fig7_strict_vs_weighted.png  custom household, strict vs weighted
"""

import re
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from appliances import load_household  # noqa: E402
from meralco_rates import compute_bill  # noqa: E402
from optimizer import STRICT, WEIGHTED, optimize  # noqa: E402
from schedule import hourly_load_kw  # noqa: E402

ROOT = Path(__file__).resolve().parent
RESULTS = ROOT / "results"
PROFILES = {"low": "low_household", "median": "median_household",
            "high": "high_household", "custom": "custom_example"}
BUDGETS = list(range(500, 8001, 100))
BRACKETS = [200, 300, 400]


def short(name, n=30):
    """Shorter legend label for long HECS appliance names."""
    name = name.replace("Lighting - ", "").replace("Compact fluorescent lamp (CFL)", "CFL")
    name = re.sub(r"\s*\(.*?\)", "", name) if len(name) > n else name
    return name if len(name) <= n else name[:n - 1] + "…"


def load(profile):
    return load_household(ROOT / "households" / f"{PROFILES[profile]}.json")


def run_grid():
    detail, summary = [], []
    for profile in PROFILES:
        hh = load(profile)
        for mode in (STRICT, WEIGHTED):
            for budget in BUDGETS:
                plan = optimize(hh, budget, mode=mode)
                summary.append({
                    "profile": profile, "mode": mode, "budget": budget,
                    "feasible": plan.feasible, "kwh_cap": round(plan.kwh_cap, 2),
                    "planned_kwh": round(plan.total_kwh, 2) if plan.feasible else None,
                    "bill": round(plan.cost, 2) if plan.feasible else None,
                    "comfort_pct": round(plan.comfort_score(), 1) if plan.feasible else None,
                    "appliances_off": sum(a.hours == 0 for a in plan.variable_allocations)
                    if plan.feasible else None})
                if not plan.feasible:
                    continue
                for a in plan.allocations:
                    detail.append({
                        "profile": profile, "mode": mode, "budget": budget,
                        "appliance": a.appliance.name, "type": a.appliance.type,
                        "priority": a.appliance.priority,
                        "hours_per_use_day": a.hours,
                        "desired_hours": 24 if a.appliance.is_fixed else a.appliance.max_hours,
                        "monthly_kwh": round(a.monthly_kwh(30), 3),
                        "status": a.status})
    return pd.DataFrame(detail), pd.DataFrame(summary)


def thresholds(summary):
    """Minimum feasible budget and budget for 100% comfort, per profile."""
    rows = []
    for profile in PROFILES:
        hh = load(profile)
        fixed_kwh = sum(a.monthly_kwh(24) for a in hh.fixed)
        min_kwh = fixed_kwh + sum(a.monthly_kwh(a.min_hours) for a in hh.variable)
        full_kwh = fixed_kwh + sum(a.monthly_kwh(a.max_hours) for a in hh.variable)
        rows.append({
            "profile": profile, "household": hh.name,
            "fixed_kwh": fixed_kwh, "min_budget": compute_bill(fixed_kwh).total,
            "essentials_kwh": min_kwh, "essentials_budget": compute_bill(min_kwh).total,
            "full_kwh": full_kwh, "full_budget": compute_bill(full_kwh).total})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Figures
# ---------------------------------------------------------------------------
def fig_bill_curve():
    kwh = [i / 2 for i in range(0, 1201)]
    bill = [compute_bill(k).total for k in kwh]
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.plot(kwh, bill, color="tab:blue")
    for b in BRACKETS:
        ax.axvline(b, color="grey", ls="--", lw=0.8)
        jump = compute_bill(b + 1).total - compute_bill(b).total
        ax.annotate(f"{b} to {b + 1} kWh\n+PHP {jump:,.0f}", (b, compute_bill(b).total),
                    xytext=(-60, 25), textcoords="offset points", fontsize=8)
    ax.set(xlabel="Monthly consumption (kWh)", ylabel="Monthly bill (PHP)",
           title="Meralco residential bill, September 2026 rates")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS / "fig1_bill_curve.png", dpi=150)
    plt.close(fig)


def fig_hecs_kwh():
    hh = pd.read_csv(ROOT / "data" / "meralco_households.csv")
    fig, ax = plt.subplots(figsize=(8, 4.5))
    ax.hist(hh.reported_kwh.clip(upper=800), bins=80, color="tab:green", alpha=0.8)
    for b in BRACKETS:
        ax.axvline(b, color="grey", ls="--", lw=0.8)
    for q, label in [(0.25, "P25"), (0.5, "median"), (0.9, "P90")]:
        v = hh.reported_kwh.quantile(q)
        ax.axvline(v, color="tab:red", lw=1)
        ax.text(v, ax.get_ylim()[1] * 0.92, f" {label}\n {v:.0f}", fontsize=8, color="tab:red")
    ax.set(xlabel="Reported average monthly consumption (kWh, capped at 800)",
           ylabel="Households",
           title=f"HECS 2011, Meralco-area households (n = {len(hh):,})")
    fig.tight_layout()
    fig.savefig(RESULTS / "fig2_hecs_kwh.png", dpi=150)
    plt.close(fig)


def _variable_pivot(detail, profile, value, mode=STRICT):
    d = detail[(detail.profile == profile) & (detail["mode"] == mode)]
    d = d[d.type == "variable"]
    order = d.drop_duplicates("appliance").sort_values("priority").appliance
    return d.pivot(index="budget", columns="appliance", values=value)[list(order)]


def fig_hours(detail, profile):
    p = _variable_pivot(detail, profile, "hours_per_use_day")
    fig, ax = plt.subplots(figsize=(9, 7))
    cmap = plt.get_cmap("tab20")
    for i, col in enumerate(p.columns):
        ax.plot(p.index, p[col], label=short(col), color=cmap(i % 20), lw=1.5)
    ax.set(xlabel="Monthly budget (PHP)", ylabel="Recommended hours per use-day",
           title=f"Recommended hours vs budget: {profile} household (strict priority)")
    ax.legend(fontsize=7, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.13))
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS / f"fig3_hours_{profile}.png", dpi=150)
    plt.close(fig)


def fig_kwh(detail, profile):
    d = detail[(detail.profile == profile) & (detail["mode"] == STRICT)]
    order = d.drop_duplicates("appliance").sort_values(["type", "priority"]).appliance
    p = d.pivot(index="budget", columns="appliance", values="monthly_kwh")[list(order)]
    fig, ax = plt.subplots(figsize=(9, 7))
    cmap = plt.get_cmap("tab20")
    ax.stackplot(p.index, p.T.values, labels=[short(c) for c in p.columns],
                 colors=[cmap(i % 20) for i in range(len(p.columns))])
    for b in BRACKETS:
        ax.axhline(b, color="grey", ls="--", lw=0.8)
    ax.set(xlabel="Monthly budget (PHP)", ylabel="Planned monthly kWh",
           title=f"Energy allocation by appliance: {profile} household")
    ax.legend(fontsize=7, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.13))
    fig.tight_layout()
    fig.savefig(RESULTS / f"fig4_kwh_{profile}.png", dpi=150)
    plt.close(fig)


def fig_comfort(summary):
    fig, ax = plt.subplots(figsize=(8, 4.5))
    colors = dict(zip(PROFILES, ["tab:green", "tab:blue", "tab:red", "tab:purple"]))
    for (profile, mode), g in summary[summary.feasible].groupby(["profile", "mode"]):
        ax.plot(g.budget, g.comfort_pct, color=colors[profile],
                ls="-" if mode == STRICT else ":", label=f"{profile} ({mode})")
    ax.set(xlabel="Monthly budget (PHP)", ylabel="Comfort score (% of desired hours)",
           title="Comfort vs budget", ylim=(0, 105))
    ax.legend(fontsize=8, ncol=2)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS / "fig5_comfort.png", dpi=150)
    plt.close(fig)


def fig_hourly_load(profile="median", budgets=(1200, 1500, 2100)):
    hh = load(profile)
    fig, ax = plt.subplots(figsize=(8, 4.5))
    for b in budgets:
        plan = optimize(hh, b)
        ax.step(range(24), hourly_load_kw(plan), where="post",
                label=f"PHP {b:,} ({plan.total_kwh:.0f} kWh)")
    ax.set(xlabel="Hour of day", ylabel="Average load (kW)", xticks=range(0, 24, 2),
           title=f"Typical-day load of the recommended schedule: {profile} household")
    ax.legend()
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(RESULTS / "fig6_hourly_load.png", dpi=150)
    plt.close(fig)


def fig_strict_vs_weighted(detail, profile="custom", budget=3000):
    d = detail[(detail.profile == profile) & (detail.budget == budget)
               & (detail.type == "variable")]
    p = d.pivot(index="appliance", columns="mode", values="hours_per_use_day")
    p = p.loc[d.drop_duplicates("appliance").sort_values("priority").appliance]
    want = d.drop_duplicates("appliance").set_index("appliance").desired_hours[p.index]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    x = range(len(p))
    ax.bar([i - 0.27 for i in x], want, 0.27, color="lightgrey", label="desired")
    ax.bar(list(x), p[STRICT], 0.27, label="strict")
    ax.bar([i + 0.27 for i in x], p[WEIGHTED], 0.27, label="weighted")
    ax.set_xticks(list(x), [f"{n}\n(P{i + 1})" for i, n in enumerate(p.index)],
                  fontsize=7)
    ax.set(ylabel="Hours per use-day",
           title=f"Strict vs weighted objective: {profile} household, PHP {budget:,}")
    ax.legend()
    fig.tight_layout()
    fig.savefig(RESULTS / "fig7_strict_vs_weighted.png", dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------------------
def markdown_summary(detail, summary, thr):
    out = ["# Scenario Analysis Summary", "", "Generated by `scenarios.py`.", "",
           "## Budget thresholds per household", "",
           "| Profile | Always-on load | Minimum budget | Budget for essentials | "
           "Budget for every appliance at desired hours |", "|---|---:|---:|---:|---:|"]
    for r in thr.itertuples():
        out.append(f"| {r.household} | {r.fixed_kwh:.1f} kWh | PHP {r.min_budget:,.2f} | "
                   f"PHP {r.essentials_budget:,.2f} ({r.essentials_kwh:.1f} kWh) | "
                   f"PHP {r.full_budget:,.2f} ({r.full_kwh:.1f} kWh) |")

    pick = [1500, 2000, 3000, 4000, 6000]
    out += ["", "## Comfort score (strict / weighted) at selected budgets", "",
            "| Profile | " + " | ".join(f"PHP {b:,}" for b in pick) + " |",
            "|---|" + "---:|" * len(pick)]
    s = summary.set_index(["profile", "mode", "budget"])
    for profile in PROFILES:
        cells = []
        for b in pick:
            st, wt = s.loc[(profile, STRICT, b)], s.loc[(profile, WEIGHTED, b)]
            cells.append("infeasible" if not st.feasible
                         else f"{st.comfort_pct:.0f}% / {wt.comfort_pct:.0f}%")
        out.append(f"| {profile} | " + " | ".join(cells) + " |")

    out += ["", "## Order in which appliances are switched off (strict mode)", ""]
    for profile in PROFILES:
        d = detail[(detail.profile == profile) & (detail["mode"] == STRICT)
                   & (detail.type == "variable")]
        # Highest budget at which each appliance is still OFF.
        off = d[d.hours_per_use_day == 0].groupby("appliance").budget.max()
        if off.empty:
            continue
        seq = ", ".join(f"{a} (off up to PHP {b:,})"
                        for a, b in off.sort_values(ascending=False).items())
        out.append(f"- **{profile}**: {seq}")
    return out


def main():
    RESULTS.mkdir(exist_ok=True)
    detail, summary = run_grid()
    thr = thresholds(summary)
    detail.to_csv(RESULTS / "scenarios.csv", index=False)
    summary.to_csv(RESULTS / "scenario_summary.csv", index=False)

    fig_bill_curve()
    fig_hecs_kwh()
    for profile in PROFILES:
        fig_hours(detail, profile)
        fig_kwh(detail, profile)
    fig_comfort(summary)
    fig_hourly_load()
    fig_strict_vs_weighted(detail)

    text = markdown_summary(detail, summary, thr)
    (RESULTS / "scenario_summary.md").write_text("\n".join(text), encoding="utf-8")
    print("\n".join(text))
    print(f"\nWrote {len(detail):,} rows to results/scenarios.csv and the figures in results/")


if __name__ == "__main__":
    main()

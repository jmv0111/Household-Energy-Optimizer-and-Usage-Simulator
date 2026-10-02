"""
Meralco residential tariff model, September 2026 billing.

Source: Meralco "Summary Schedule of Rates, Effective September 2026 Billing"
(09-2026_rate_schedule.pdf). Only the Residential rows are modelled, because
the project scope is a single household.

All rates in the schedule are VAT-exclusive. VAT is applied per component
using the effective VAT rates printed in the schedule's "VAT RATES" table.

Assumptions (see README / report, change here if the panel says otherwise):
  * The distribution charge bracket is chosen by TOTAL monthly kWh and that
    rate applies to every kWh (Meralco does not bill distribution
    incrementally per bracket).
  * Energy tax (BP 36) IS incremental: first 650 kWh free, next 350 at 0.10,
    next 500 at 0.20, excess at 0.35.
  * Lifeline discount only applies to registered lifeline customers
    (RA 11552 / ERC Res. 02-2026), so it is OFF by default. When on, the
    discount % is applied to generation, transmission, ancillary service,
    system loss, distribution, supply and metering charges, and the customer
    no longer pays the lifeline subsidy.
  * Senior citizen discount (5%) is OFF by default and, when on, is applied
    to the charges listed in footnote 22.
  * Local franchise tax (LFT) varies per LGU, so it is a parameter (default 0).
  * TOU GCA only applies to Time-of-Use customers and is not modelled.
"""

from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Per-kWh charges common to every residential bracket (PHP/kWh, VAT-exclusive)
# ---------------------------------------------------------------------------
GENERATION = 9.7032
TRANSMISSION = 0.5485
ANCILLARY_SERVICE = 0.7341
SYSTEM_LOSS = 0.8898
SUPPLY_PER_KWH = 0.4979
METERING_PER_KWH = 0.3350

# Fixed monthly charges (PHP per customer per month)
SUPPLY_FIXED = 16.38
METERING_FIXED = 5.00

# Refunds and adjustments (negative = refund to the customer)
AWAT_REFUND_1 = -0.4278
AWAT_REFUND_2 = -0.5861
REGULATORY_RESET_ADJ = -0.0023
LIFELINE_RATE_ADJ = -0.0001      # applies to non-lifeline customers
SENIOR_CITIZEN_SUBSIDY = 0.0001
RPT_CHARGE = 0.0067
RPT_ADJ = 0.0015

# Universal charge components
UC_ME_NPC_SPUG = 0.2662
UC_ME_RED_CI = 0.0101
UC_EC = 0.0025
UC_SD = 0.0428
UNIVERSAL_CHARGE = UC_ME_NPC_SPUG + UC_ME_RED_CI + UC_EC + UC_SD

FIT_ALL = 0.3359
GEA_ALL = 0.0000                 # suspended (footnote 16)
LIFELINE_SUBSIDY = 0.0100        # paid by non-lifeline customers

# Distribution charge by monthly consumption bracket: (upper kWh, PHP/kWh)
DISTRIBUTION_BRACKETS = [
    (200, 0.9803),
    (300, 1.2908),
    (400, 1.5837),
    (float("inf"), 2.0941),
]

# Lifeline discount by consumption bracket: (upper kWh, discount fraction)
LIFELINE_DISCOUNT = [
    (50, 1.00),
    (70, 0.35),
    (100, 0.20),
]

# Energy tax, incremental blocks: (block size kWh, PHP/kWh)
ENERGY_TAX_BLOCKS = [
    (650, 0.00),
    (350, 0.10),
    (500, 0.20),
    (float("inf"), 0.35),
]

# Effective VAT rates from the schedule's "VAT RATES" table
VAT_GENERATION = 0.0921
VAT_TRANSMISSION = 0.1199
VAT_ANCILLARY = 0.0983
VAT_SYSTEM_LOSS = 0.0938
VAT_OTHER = 0.12

SENIOR_CITIZEN_DISCOUNT = 0.05


def distribution_rate(kwh):
    for upper, rate in DISTRIBUTION_BRACKETS:
        if kwh <= upper:
            return rate
    return DISTRIBUTION_BRACKETS[-1][1]


def lifeline_discount_rate(kwh):
    for upper, pct in LIFELINE_DISCOUNT:
        if kwh <= upper:
            return pct
    return 0.0


def energy_tax(kwh):
    tax, remaining = 0.0, kwh
    for size, rate in ENERGY_TAX_BLOCKS:
        used = min(remaining, size)
        tax += used * rate
        remaining -= used
        if remaining <= 0:
            break
    return tax


@dataclass
class BillOptions:
    lifeline: bool = False
    senior_citizen: bool = False
    lft_per_kwh: float = 0.0     # local franchise tax, varies per LGU


@dataclass
class Bill:
    kwh: float
    lines: dict = field(default_factory=dict)

    @property
    def total(self):
        return sum(self.lines.values())

    def summary(self):
        width = max(len(k) for k in self.lines)
        rows = [f"Consumption: {self.kwh:,.2f} kWh"]
        rows += [f"  {k:<{width}}  {v:>12,.2f}" for k, v in self.lines.items()]
        rows.append(f"  {'TOTAL':<{width}}  {self.total:>12,.2f}")
        if self.kwh > 0:
            rows.append(f"  Effective rate: {self.total / self.kwh:,.4f} PHP/kWh")
        return "\n".join(rows)


def compute_bill(kwh, options=None):
    """Monthly Meralco residential bill (PHP, VAT-inclusive) for `kwh`."""
    opts = options or BillOptions()
    if kwh < 0:
        raise ValueError("kWh cannot be negative")

    generation = GENERATION * kwh
    transmission = TRANSMISSION * kwh
    ancillary = ANCILLARY_SERVICE * kwh
    system_loss = SYSTEM_LOSS * kwh
    distribution = distribution_rate(kwh) * kwh
    supply = SUPPLY_PER_KWH * kwh + SUPPLY_FIXED
    metering = METERING_PER_KWH * kwh + METERING_FIXED

    lines = {
        "Generation": generation,
        "Transmission": transmission,
        "Ancillary Service": ancillary,
        "System Loss": system_loss,
        "Distribution": distribution,
        "Supply": supply,
        "Metering": metering,
    }

    if opts.lifeline:
        pct = lifeline_discount_rate(kwh)
        base = (generation + transmission + ancillary + system_loss
                + distribution + supply + metering)
        lines["Lifeline Discount"] = -pct * base
    else:
        lines["Lifeline Subsidy"] = LIFELINE_SUBSIDY * kwh
        lines["Lifeline Rate Adj"] = LIFELINE_RATE_ADJ * kwh

    if opts.senior_citizen:
        base = (generation + transmission + ancillary + system_loss
                + distribution + supply + metering)
        lines["Senior Citizen Discount"] = -SENIOR_CITIZEN_DISCOUNT * base
    else:
        lines["Senior Citizen Subsidy"] = SENIOR_CITIZEN_SUBSIDY * kwh

    awat = (AWAT_REFUND_1 + AWAT_REFUND_2) * kwh
    reg_reset = REGULATORY_RESET_ADJ * kwh
    # Charges in the "Other Charges" VAT class that are not in the schedule's
    # "not subject to VAT" list (Lifeline Rate Adj, Senior Citizen Subsidy).
    other_vatable_adj = (lines.get("Lifeline Rate Adj", 0.0)
                         + lines.get("Senior Citizen Subsidy", 0.0))
    lines["AWAT Refunds"] = awat
    lines["Regulatory Reset Adj"] = reg_reset

    lines["Universal Charge"] = UNIVERSAL_CHARGE * kwh
    lines["FIT-All"] = FIT_ALL * kwh
    lines["GEA-All"] = GEA_ALL * kwh
    lines["RPT Charge + Adj"] = (RPT_CHARGE + RPT_ADJ) * kwh
    lines["Local Franchise Tax"] = opts.lft_per_kwh * kwh
    lines["Energy Tax"] = energy_tax(kwh)

    # VAT: discounts reduce the VAT base proportionally for each component
    keep = 1.0
    if opts.lifeline:
        keep -= lifeline_discount_rate(kwh)
    if opts.senior_citizen:
        keep -= SENIOR_CITIZEN_DISCOUNT
    keep = max(keep, 0.0)
    other_base = ((distribution + supply + metering) * keep + awat + reg_reset
                  + other_vatable_adj)
    lines["VAT"] = (
        VAT_GENERATION * generation * keep
        + VAT_TRANSMISSION * transmission * keep
        + VAT_ANCILLARY * ancillary * keep
        + VAT_SYSTEM_LOSS * system_loss * keep
        + VAT_OTHER * other_base
    )

    return Bill(kwh=kwh, lines=lines)


def max_kwh_for_budget(budget, options=None, tol=1e-4):
    """Largest monthly kWh whose bill does not exceed `budget` (PHP).

    The bill is non-decreasing in kWh (bracket jumps only push it up), so a
    binary search is valid. Returns 0.0 if even the fixed charges exceed the
    budget; callers should check `compute_bill(0).total` for that case.
    """
    if compute_bill(0, options).total > budget:
        return 0.0
    lo, hi = 0.0, 1.0
    while compute_bill(hi, options).total <= budget:
        hi *= 2
    while hi - lo > tol:
        mid = (lo + hi) / 2
        if compute_bill(mid, options).total <= budget:
            lo = mid
        else:
            hi = mid
    return lo


if __name__ == "__main__":
    for kwh in (50, 200, 201, 300, 450):
        print(compute_bill(kwh).summary(), end="\n\n")
    for budget in (1500, 3000, 5000):
        kwh = max_kwh_for_budget(budget)
        print(f"Budget PHP {budget:,}: up to {kwh:,.2f} kWh/month "
              f"(bill PHP {compute_bill(kwh).total:,.2f})")

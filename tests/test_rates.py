"""
Validation of the tariff model against the published Meralco Summary
Schedule of Rates (September 2026 billing, residential rows).

hand_bill() re-types every rate straight from the PDF instead of importing
meralco_rates, so it is an independent check of the module's arithmetic.
"""

import unittest

from meralco_rates import BillOptions, compute_bill, energy_tax, max_kwh_for_budget


def hand_bill(kwh):
    """Non-lifeline, non-senior residential bill typed from the PDF."""
    if kwh <= 200:
        dist = 0.9803
    elif kwh <= 300:
        dist = 1.2908
    elif kwh <= 400:
        dist = 1.5837
    else:
        dist = 2.0941
    gen, trans, anc, sysloss = 9.7032 * kwh, 0.5485 * kwh, 0.7341 * kwh, 0.8898 * kwh
    distribution = dist * kwh
    supply = 0.4979 * kwh + 16.38
    metering = 0.3350 * kwh + 5.00
    awat = (-0.4278 - 0.5861) * kwh
    reg_reset = -0.0023 * kwh
    lifeline_adj = -0.0001 * kwh
    senior_sub = 0.0001 * kwh
    not_vatable = (0.0100 * kwh                       # lifeline subsidy
                   + (0.0067 + 0.0015) * kwh            # RPT charge + adj
                   + (0.2662 + 0.0101 + 0.0025 + 0.0428) * kwh   # universal charge
                   + 0.3359 * kwh                       # FIT-All
                   + 0.0000 * kwh)                      # GEA-All (suspended)
    # Energy tax (BP 36): first 650 free, next 350 @0.10, next 500 @0.20, rest @0.35
    et = (max(0, min(kwh, 1000) - 650) * 0.10 + max(0, min(kwh, 1500) - 1000) * 0.20
          + max(0, kwh - 1500) * 0.35)
    vat = (0.0921 * gen + 0.1199 * trans + 0.0983 * anc + 0.0938 * sysloss
           + 0.12 * (distribution + supply + metering + awat + reg_reset
                     + lifeline_adj + senior_sub))
    return (gen + trans + anc + sysloss + distribution + supply + metering + awat
            + reg_reset + lifeline_adj + senior_sub + not_vatable + et + vat)


VALIDATION_POINTS = [0, 25, 50, 100, 150, 200, 201, 250, 300, 301, 350, 400,
                     401, 500, 650, 700, 1000, 1200, 1500, 2000]


class TariffAgainstSchedule(unittest.TestCase):
    def test_matches_hand_calculation(self):
        for kwh in VALIDATION_POINTS:
            with self.subTest(kwh=kwh):
                self.assertAlmostEqual(compute_bill(kwh).total, hand_bill(kwh), places=6)

    def test_fixed_charges_only_at_zero(self):
        # Supply 16.38 + metering 5.00 per month, plus 12% VAT.
        self.assertAlmostEqual(compute_bill(0).total, (16.38 + 5.00) * 1.12, places=6)

    def test_distribution_bracket_applies_to_all_kwh(self):
        jump = compute_bill(201).total - compute_bill(200).total
        # 201 kWh at 1.2908 instead of 200 at 0.9803, plus VAT, plus one more kWh.
        self.assertGreater(jump, 80)
        self.assertAlmostEqual(compute_bill(201).lines["Distribution"], 201 * 1.2908)

    def test_energy_tax_blocks(self):
        self.assertEqual(energy_tax(650), 0)
        self.assertAlmostEqual(energy_tax(1000), 35.0)
        self.assertAlmostEqual(energy_tax(1500), 135.0)
        self.assertAlmostEqual(energy_tax(1600), 170.0)

    def test_bill_is_non_decreasing(self):
        prev = -1
        for i in range(0, 3001):
            total = compute_bill(i / 2).total
            self.assertGreaterEqual(total, prev - 1e-9)
            prev = total

    def test_budget_inversion(self):
        for budget in [24, 100, 500, 1500, 2935.74, 2936, 3000, 3020.20, 4500, 8000]:
            with self.subTest(budget=budget):
                k = max_kwh_for_budget(budget)
                self.assertLessEqual(compute_bill(k).total, budget + 1e-6)
                self.assertGreater(compute_bill(k + 0.01).total, budget)

    def test_lifeline_50kwh_is_fully_discounted(self):
        b = compute_bill(50, BillOptions(lifeline=True))
        self.assertNotIn("Lifeline Subsidy", b.lines)
        disc = b.lines["Lifeline Discount"]
        base = sum(b.lines[k] for k in ("Generation", "Transmission", "Ancillary Service",
                                        "System Loss", "Distribution", "Supply", "Metering"))
        self.assertAlmostEqual(disc, -base)

    def test_senior_discount_is_five_percent(self):
        plain = compute_bill(90)
        sc = compute_bill(90, BillOptions(senior_citizen=True))
        base = sum(plain.lines[k] for k in ("Generation", "Transmission", "Ancillary Service",
                                            "System Loss", "Distribution", "Supply", "Metering"))
        self.assertAlmostEqual(sc.lines["Senior Citizen Discount"], -0.05 * base)
        self.assertLess(sc.total, plain.total)

    def test_discounts_stop_above_100_kwh(self):
        # RA 9994 (senior) and RA 11552 (lifeline) both stop at 100 kWh/month;
        # above that the customer is billed like everyone else.
        for opts in (BillOptions(senior_citizen=True), BillOptions(lifeline=True)):
            with self.subTest(opts=opts):
                self.assertAlmostEqual(compute_bill(150, opts).total, compute_bill(150).total)
                self.assertLess(compute_bill(100, opts).total, compute_bill(100).total)

    def test_budget_inversion_with_discounts(self):
        # With a 100% lifeline discount (0-50 kWh) only the per-kWh refunds
        # and pass-through charges remain, so the bill dips slightly below
        # zero there. The bisection still needs every kWh up to the cap to be
        # affordable and the next step above it not to be.
        for opts in (BillOptions(lifeline=True), BillOptions(senior_citizen=True),
                     BillOptions(lifeline=True, senior_citizen=True, lft_per_kwh=0.05)):
            for budget in (30, 400, 1200, 1500, 3000):
                with self.subTest(opts=opts, budget=budget):
                    k = max_kwh_for_budget(budget, opts)
                    grid = [k * i / 400 for i in range(401)]
                    self.assertTrue(all(compute_bill(e, opts).total <= budget + 1e-6
                                        for e in grid))
                    self.assertGreater(compute_bill(k + 0.01, opts).total, budget)
            prev = -1e9   # above the 50-kWh full-discount tier it never decreases
            for i in range(201, 4001):
                total = compute_bill(i / 4, opts).total
                self.assertGreaterEqual(total, prev - 1e-9)
                prev = total

    def test_negative_kwh_rejected(self):
        with self.assertRaises(ValueError):
            compute_bill(-1)


if __name__ == "__main__":
    unittest.main()

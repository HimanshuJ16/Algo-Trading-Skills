import math
import unittest

from horizon_risk_allocator import (
    ALLOCATION_TOLERANCE_PCT,
    MAX_PLAUSIBLE_ANNUALIZED_VOL,
    VALID_STATUSES,
    HorizonAllocation,
    RiskBudgetAllocationEngine,
    TimeHorizonBucket,
)


def four_horizon_book():
    """A 100%-allocated book: 20 / 30 / 30 / 20 across four holding periods."""
    return [
        TimeHorizonBucket("INTRADAY", 1, 20.0, 0.10, 2.0),
        TimeHorizonBucket("SHORT_TERM", 5, 30.0, 0.12, 5.0),
        TimeHorizonBucket("MEDIUM_TERM", 20, 30.0, 0.15, 8.0),
        TimeHorizonBucket("LONG_TERM", 60, 20.0, 0.18, 10.0),
    ]


class TestValidAllocation(unittest.TestCase):

    def setUp(self):
        self.engine = RiskBudgetAllocationEngine(total_portfolio_vol_target=0.15)

    def test_fully_allocated_book_is_valid(self):
        report = self.engine.allocate_risk_budget(four_horizon_book())
        self.assertEqual(report.status, "RISK_BUDGET_VALID")
        self.assertIn(report.status, VALID_STATUSES)
        self.assertFalse(report.over_allocated)
        self.assertFalse(report.under_allocated)
        self.assertEqual(report.total_risk_budget_pct, 100.0)
        self.assertEqual(report.unallocated_risk_pct, 0.0)
        self.assertEqual(report.total_horizons, 4)
        self.assertEqual(report.portfolio_vol_target, 0.15)

    def test_budget_implied_vol_targets_are_derived_from_the_budget(self):
        # Independent arithmetic: 20% of a 15% portfolio budget is 3.0% annualized vol.
        report = self.engine.allocate_risk_budget(four_horizon_book())
        expected = [0.03, 0.045, 0.045, 0.03]
        for allocation, want in zip(report.horizon_allocations, expected):
            self.assertAlmostEqual(allocation.budget_implied_vol_target, want, places=12)

    def test_position_size_scalar_is_inverse_to_sleeve_volatility(self):
        # Regression: the scalar must depend on the risk budget AND scale down a
        # volatile sleeve. The pre-2.0 formula was base_vol / portfolio_vol, which
        # ignored the budget entirely and scaled volatile sleeves *up*.
        report = self.engine.allocate_risk_budget(four_horizon_book())
        # 0.20 * 0.15 / 0.10 = 0.30 ; 0.30 * 0.15 / 0.12 = 0.375
        # 0.30 * 0.15 / 0.15 = 0.30 ; 0.20 * 0.15 / 0.18 = 1/6
        expected = [0.30, 0.375, 0.30, 1.0 / 6.0]
        for allocation, want in zip(report.horizon_allocations, expected):
            self.assertAlmostEqual(allocation.position_size_scalar, want, places=12)

        intraday, short_term = report.horizon_allocations[0], report.horizon_allocations[1]
        long_term = report.horizon_allocations[3]
        # Same 20% budget, higher sleeve vol (18% vs 10%) => smaller scalar.
        self.assertEqual(intraday.risk_budget_pct, long_term.risk_budget_pct)
        self.assertLess(long_term.position_size_scalar, intraday.position_size_scalar)
        # Same 30% budget as MEDIUM_TERM but lower sleeve vol => larger scalar.
        self.assertGreater(short_term.position_size_scalar,
                           report.horizon_allocations[2].position_size_scalar)

    def test_budget_is_not_ignored_by_the_scalar(self):
        # Two sleeves, identical volatility, budgets 4:1. The scalars must be 4:1.
        engine = RiskBudgetAllocationEngine(total_portfolio_vol_target=0.20)
        report = engine.allocate_risk_budget([
            TimeHorizonBucket("BIG", 5, 80.0, 0.25, 10.0),
            TimeHorizonBucket("SMALL", 5, 20.0, 0.25, 10.0),
        ])
        big, small = report.horizon_allocations
        self.assertAlmostEqual(big.position_size_scalar / small.position_size_scalar, 4.0,
                               places=12)

    def test_scaled_sleeve_volatilities_sum_to_the_portfolio_budget(self):
        # The comonotonic invariant: with budgets totalling 100%, the sum of scaled
        # sleeve volatilities equals the portfolio volatility budget exactly.
        report = self.engine.allocate_risk_budget(four_horizon_book())
        total = math.fsum(
            a.position_size_scalar * a.base_annualized_vol for a in report.horizon_allocations
        )
        self.assertAlmostEqual(total, 0.15, places=12)

    def test_scalar_is_unbounded_above_but_the_budget_invariant_holds(self):
        # A near-zero base volatility needs an enormous multiplier to reach its budget.
        # The multiplier is arithmetically correct - the sleeve still lands on exactly
        # its budgeted volatility - but bounding the leverage it implies is out of scope.
        report = self.engine.allocate_risk_budget([
            TimeHorizonBucket("CASH_LIKE", 1, 20.0, 1e-6, 2.0),
        ])
        allocation = report.horizon_allocations[0]
        self.assertAlmostEqual(allocation.position_size_scalar, 30000.0, places=6)
        self.assertAlmostEqual(
            allocation.position_size_scalar * allocation.base_annualized_vol, 0.03, places=12
        )

    def test_scalar_is_not_prematurely_rounded(self):
        # 1/6 is not representable at 4 decimal places; the pre-2.0 engine rounded the
        # sizing multiplier to 4dp inside the engine.
        report = self.engine.allocate_risk_budget(four_horizon_book())
        self.assertNotEqual(report.horizon_allocations[3].position_size_scalar, 0.1667)

    def test_holding_period_volatility_uses_square_root_of_time(self):
        report = self.engine.allocate_risk_budget(four_horizon_book())
        intraday, long_term = report.horizon_allocations[0], report.horizon_allocations[3]
        # 3% annualized over 1 of 252 days: 0.03 / sqrt(252) = 0.001889822...
        self.assertAlmostEqual(intraday.holding_period_vol, 0.03 / math.sqrt(252.0), places=12)
        # 3% annualized over 60 of 252 days: 0.03 * sqrt(60/252) = 0.014638501...
        self.assertAlmostEqual(long_term.holding_period_vol, 0.014638501094227997, places=12)

    def test_trading_days_per_year_changes_the_holding_period_scaling(self):
        engine = RiskBudgetAllocationEngine(
            total_portfolio_vol_target=0.15, trading_days_per_year=365
        )
        report = engine.allocate_risk_budget([
            TimeHorizonBucket("DAILY", 1, 100.0, 0.20, 5.0),
        ])
        self.assertAlmostEqual(
            report.horizon_allocations[0].holding_period_vol, 0.15 / math.sqrt(365.0), places=12
        )

    def test_report_preserves_input_order(self):
        book = list(reversed(four_horizon_book()))
        report = self.engine.allocate_risk_budget(book)
        self.assertEqual(
            [a.horizon_label for a in report.horizon_allocations],
            ["LONG_TERM", "MEDIUM_TERM", "SHORT_TERM", "INTRADAY"],
        )

    def test_verdict_is_independent_of_bucket_order(self):
        forward = self.engine.allocate_risk_budget(four_horizon_book())
        reverse = self.engine.allocate_risk_budget(list(reversed(four_horizon_book())))
        self.assertEqual(forward.total_risk_budget_pct, reverse.total_risk_budget_pct)
        self.assertEqual(forward.status, reverse.status)


class TestOverAndUnderAllocation(unittest.TestCase):

    def setUp(self):
        self.engine = RiskBudgetAllocationEngine(total_portfolio_vol_target=0.15)

    def test_over_allocated_budget_flagged(self):
        report = self.engine.allocate_risk_budget([
            TimeHorizonBucket("INTRADAY", 1, 50.0, 0.10, 2.0),
            TimeHorizonBucket("SHORT_TERM", 5, 60.0, 0.12, 5.0),
        ])
        self.assertEqual(report.status, "RISK_BUDGET_OVER_ALLOCATED")
        self.assertTrue(report.over_allocated)
        self.assertFalse(report.under_allocated)
        self.assertEqual(report.total_risk_budget_pct, 110.0)
        self.assertEqual(report.unallocated_risk_pct, 0.0)

    def test_under_allocation_is_reported_but_is_not_a_breach(self):
        report = self.engine.allocate_risk_budget([
            TimeHorizonBucket("INTRADAY", 1, 20.0, 0.10, 2.0),
            TimeHorizonBucket("SHORT_TERM", 5, 30.0, 0.12, 5.0),
        ])
        self.assertEqual(report.status, "RISK_BUDGET_VALID")
        self.assertFalse(report.over_allocated)
        self.assertTrue(report.under_allocated)
        self.assertAlmostEqual(report.unallocated_risk_pct, 50.0, places=12)
        # Only half the portfolio volatility budget is deployed.
        deployed = math.fsum(
            a.position_size_scalar * a.base_annualized_vol for a in report.horizon_allocations
        )
        self.assertAlmostEqual(deployed, 0.075, places=12)

    def test_float_accumulation_does_not_fabricate_a_breach(self):
        # Regression: these four two-decimal percentages accumulate to
        # 100.00000000000001 with a running float total, which the pre-2.0 engine
        # reported as over-allocated while its own rounded total still read 100.0.
        book = [
            TimeHorizonBucket("A", 1, 23.35, 0.10, 2.0),
            TimeHorizonBucket("B", 5, 65.80, 0.12, 2.0),
            TimeHorizonBucket("C", 20, 9.18, 0.15, 2.0),
            TimeHorizonBucket("D", 60, 1.67, 0.18, 2.0),
        ]
        naive_total = 0.0
        for bucket in book:
            naive_total += bucket.allocated_risk_pct
        self.assertGreater(naive_total, 100.0)  # the defect this test guards against

        report = self.engine.allocate_risk_budget(book)
        self.assertFalse(report.over_allocated)
        self.assertFalse(report.under_allocated)
        self.assertEqual(report.status, "RISK_BUDGET_VALID")

    def test_reported_total_never_contradicts_the_breach_flag(self):
        # Regression: round(100.004, 2) == 100.0, so the pre-2.0 report showed a total
        # of 100.0 alongside over_allocated=True.
        report = self.engine.allocate_risk_budget([
            TimeHorizonBucket("A", 1, 50.0, 0.10, 2.0),
            TimeHorizonBucket("B", 5, 50.004, 0.12, 2.0),
        ])
        self.assertTrue(report.over_allocated)
        self.assertGreater(report.total_risk_budget_pct, 100.0)
        self.assertIn("100.004", report.audit_notes)

    def test_exact_hundred_percent_is_not_a_breach(self):
        report = self.engine.allocate_risk_budget([
            TimeHorizonBucket("ONLY", 1, 100.0, 0.15, 5.0),
        ])
        self.assertFalse(report.over_allocated)
        self.assertFalse(report.under_allocated)

    def test_breach_just_above_tolerance_is_flagged(self):
        report = self.engine.allocate_risk_budget([
            TimeHorizonBucket("A", 1, 100.0, 0.10, 2.0),
            TimeHorizonBucket("B", 5, 10.0 * ALLOCATION_TOLERANCE_PCT, 0.12, 2.0),
        ])
        self.assertTrue(report.over_allocated)


class TestDrawdownBudget(unittest.TestCase):

    def test_no_portfolio_limit_means_the_check_did_not_run(self):
        engine = RiskBudgetAllocationEngine(total_portfolio_vol_target=0.15)
        report = engine.allocate_risk_budget(four_horizon_book())
        self.assertFalse(report.drawdown_over_allocated)
        self.assertEqual(report.total_drawdown_limit_pct, 25.0)
        for allocation in report.horizon_allocations:
            self.assertIsNone(allocation.is_within_limits)
        self.assertIn("drawdown check not run", report.audit_notes)

    def test_summed_horizon_drawdowns_exceeding_the_portfolio_limit_are_flagged(self):
        engine = RiskBudgetAllocationEngine(
            total_portfolio_vol_target=0.15, portfolio_max_drawdown_limit_pct=10.0
        )
        report = engine.allocate_risk_budget([
            TimeHorizonBucket("A", 1, 50.0, 0.10, 6.0),
            TimeHorizonBucket("B", 5, 50.0, 0.12, 6.0),
        ])
        self.assertEqual(report.status, "DRAWDOWN_BUDGET_OVER_ALLOCATED")
        self.assertTrue(report.drawdown_over_allocated)
        self.assertFalse(report.over_allocated)
        self.assertEqual(report.total_drawdown_limit_pct, 12.0)
        # Neither horizon can breach the portfolio limit alone.
        self.assertTrue(all(a.is_within_limits for a in report.horizon_allocations))

    def test_single_horizon_exceeding_the_portfolio_limit_alone(self):
        engine = RiskBudgetAllocationEngine(
            total_portfolio_vol_target=0.15, portfolio_max_drawdown_limit_pct=10.0
        )
        report = engine.allocate_risk_budget([
            TimeHorizonBucket("HOG", 1, 60.0, 0.10, 12.0),
            TimeHorizonBucket("REST", 5, 40.0, 0.12, 1.0),
        ])
        self.assertFalse(report.horizon_allocations[0].is_within_limits)
        self.assertTrue(report.horizon_allocations[1].is_within_limits)
        self.assertTrue(report.drawdown_over_allocated)

    def test_drawdown_limits_within_the_portfolio_limit_pass(self):
        engine = RiskBudgetAllocationEngine(
            total_portfolio_vol_target=0.15, portfolio_max_drawdown_limit_pct=30.0
        )
        report = engine.allocate_risk_budget(four_horizon_book())
        self.assertEqual(report.total_drawdown_limit_pct, 25.0)
        self.assertEqual(report.status, "RISK_BUDGET_VALID")
        self.assertFalse(report.drawdown_over_allocated)

    def test_both_breaches_combine_into_one_status(self):
        engine = RiskBudgetAllocationEngine(
            total_portfolio_vol_target=0.15, portfolio_max_drawdown_limit_pct=5.0
        )
        report = engine.allocate_risk_budget([
            TimeHorizonBucket("A", 1, 70.0, 0.10, 6.0),
            TimeHorizonBucket("B", 5, 70.0, 0.12, 6.0),
        ])
        self.assertEqual(report.status, "RISK_AND_DRAWDOWN_OVER_ALLOCATED")
        self.assertTrue(report.over_allocated)
        self.assertTrue(report.drawdown_over_allocated)

    def test_drawdown_limit_inside_one_holding_period_sigma_is_flagged(self):
        # A 100% budget on a 15% portfolio target held for a full year has a
        # holding-period sigma of 15%. A 2% drawdown limit sits well inside routine
        # noise and will fire on ordinary P&L rather than on a risk event.
        engine = RiskBudgetAllocationEngine(total_portfolio_vol_target=0.15)
        report = engine.allocate_risk_budget([
            TimeHorizonBucket("YEARLONG", 252, 100.0, 0.15, 2.0),
        ])
        allocation = report.horizon_allocations[0]
        self.assertAlmostEqual(allocation.holding_period_vol, 0.15, places=12)
        self.assertTrue(allocation.drawdown_limit_below_one_sigma)

    def test_generous_drawdown_limit_is_not_flagged_as_inside_one_sigma(self):
        engine = RiskBudgetAllocationEngine(total_portfolio_vol_target=0.15)
        report = engine.allocate_risk_budget([
            TimeHorizonBucket("YEARLONG", 252, 100.0, 0.15, 25.0),
        ])
        self.assertFalse(report.horizon_allocations[0].drawdown_limit_below_one_sigma)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = RiskBudgetAllocationEngine(total_portfolio_vol_target=0.15)

    def test_nan_allocation_raises(self):
        # Regression: NaN > 100.0 is False, so the pre-2.0 engine returned
        # RISK_BUDGET_VALID for a budget whose total was NaN.
        with self.assertRaises(ValueError):
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("A", 1, 20.0, 0.10, 2.0),
                TimeHorizonBucket("B", 5, float("nan"), 0.12, 5.0),
            ])

    def test_infinite_allocation_raises(self):
        with self.assertRaises(ValueError):
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("A", 1, float("inf"), 0.10, 2.0),
            ])

    def test_negative_allocation_raises(self):
        # Regression: 150 + (-60) = 90 passed the pre-2.0 cap as valid.
        with self.assertRaises(ValueError):
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("A", 1, 150.0, 0.10, 2.0),
                TimeHorizonBucket("B", 5, -60.0, 0.12, 5.0),
            ])

    def test_zero_allocation_raises(self):
        with self.assertRaises(ValueError):
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("A", 1, 0.0, 0.10, 2.0),
            ])

    def test_allocation_above_one_hundred_percent_raises(self):
        with self.assertRaises(ValueError):
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("A", 1, 100.01, 0.10, 2.0),
            ])

    def test_zero_sleeve_volatility_raises(self):
        with self.assertRaises(ValueError):
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("A", 1, 100.0, 0.0, 2.0),
            ])

    def test_negative_sleeve_volatility_raises(self):
        with self.assertRaises(ValueError):
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("A", 1, 100.0, -0.10, 2.0),
            ])

    def test_percent_shaped_volatility_raises_with_a_units_hint(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("A", 1, 100.0, 15.0, 2.0),
            ])
        self.assertIn("fractions, not percentages", str(ctx.exception))

    def test_volatility_at_the_sanity_bound_is_accepted(self):
        report = self.engine.allocate_risk_budget([
            TimeHorizonBucket("CRYPTO", 1, 100.0, MAX_PLAUSIBLE_ANNUALIZED_VOL, 20.0),
        ])
        self.assertEqual(report.status, "RISK_BUDGET_VALID")

    def test_duplicate_horizon_labels_raise(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("INTRADAY", 1, 40.0, 0.10, 2.0),
                TimeHorizonBucket("INTRADAY", 1, 40.0, 0.10, 2.0),
            ])
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_blank_horizon_label_raises(self):
        with self.assertRaises(ValueError):
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("   ", 1, 100.0, 0.10, 2.0),
            ])

    def test_empty_bucket_list_raises(self):
        with self.assertRaises(ValueError):
            self.engine.allocate_risk_budget([])

    def test_none_bucket_list_raises(self):
        with self.assertRaises(ValueError):
            self.engine.allocate_risk_budget(None)

    def test_non_bucket_entry_raises(self):
        with self.assertRaises(TypeError):
            self.engine.allocate_risk_budget([{"horizon_label": "A"}])

    def test_zero_holding_period_raises(self):
        with self.assertRaises(ValueError):
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("A", 0, 100.0, 0.10, 2.0),
            ])

    def test_boolean_holding_period_raises(self):
        # bool is a subclass of int; True would silently become a 1-day holding period.
        with self.assertRaises(TypeError):
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("A", True, 100.0, 0.10, 2.0),
            ])

    def test_string_allocation_raises(self):
        with self.assertRaises(TypeError):
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("A", 1, "100", 0.10, 2.0),
            ])

    def test_zero_drawdown_limit_raises(self):
        with self.assertRaises(ValueError):
            self.engine.allocate_risk_budget([
                TimeHorizonBucket("A", 1, 100.0, 0.10, 0.0),
            ])


class TestEngineConfiguration(unittest.TestCase):

    def test_zero_portfolio_vol_target_raises(self):
        # Regression: the pre-2.0 engine silently fell back to a scalar of 1.0, sizing
        # every horizon at base with no risk basis at all.
        with self.assertRaises(ValueError):
            RiskBudgetAllocationEngine(total_portfolio_vol_target=0.0)

    def test_negative_portfolio_vol_target_raises(self):
        with self.assertRaises(ValueError):
            RiskBudgetAllocationEngine(total_portfolio_vol_target=-0.15)

    def test_nan_portfolio_vol_target_raises(self):
        with self.assertRaises(ValueError):
            RiskBudgetAllocationEngine(total_portfolio_vol_target=float("nan"))

    def test_percent_shaped_portfolio_vol_target_raises(self):
        with self.assertRaises(ValueError):
            RiskBudgetAllocationEngine(total_portfolio_vol_target=15.0)

    def test_invalid_portfolio_drawdown_limit_raises(self):
        with self.assertRaises(ValueError):
            RiskBudgetAllocationEngine(portfolio_max_drawdown_limit_pct=0.0)
        with self.assertRaises(ValueError):
            RiskBudgetAllocationEngine(portfolio_max_drawdown_limit_pct=101.0)

    def test_invalid_trading_days_per_year_raises(self):
        with self.assertRaises(ValueError):
            RiskBudgetAllocationEngine(trading_days_per_year=0)
        with self.assertRaises(TypeError):
            RiskBudgetAllocationEngine(trading_days_per_year=252.0)

    def test_defaults(self):
        engine = RiskBudgetAllocationEngine()
        self.assertEqual(engine.total_portfolio_vol_target, 0.15)
        self.assertIsNone(engine.portfolio_max_drawdown_limit_pct)
        self.assertEqual(engine.trading_days_per_year, 252)


class TestReportShape(unittest.TestCase):

    def test_report_fields_are_populated(self):
        engine = RiskBudgetAllocationEngine(
            total_portfolio_vol_target=0.15, portfolio_max_drawdown_limit_pct=30.0
        )
        report = engine.allocate_risk_budget(four_horizon_book())
        self.assertEqual(len(report.horizon_allocations), 4)
        for allocation in report.horizon_allocations:
            self.assertIsInstance(allocation, HorizonAllocation)
            self.assertGreater(allocation.position_size_scalar, 0.0)
            self.assertGreater(allocation.budget_implied_vol_target, 0.0)
            self.assertGreater(allocation.holding_period_vol, 0.0)
            self.assertGreaterEqual(allocation.holding_period_days, 1)
        self.assertIn("RISK BUDGET ALLOCATION", report.audit_notes)
        self.assertIn(report.status, report.audit_notes)


if __name__ == '__main__':
    unittest.main()

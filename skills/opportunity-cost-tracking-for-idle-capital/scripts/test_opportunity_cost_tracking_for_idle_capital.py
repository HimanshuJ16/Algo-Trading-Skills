"""Unit tests for opportunity-cost-tracking-for-idle-capital.

Expected values are derived independently of the implementation (hand-computed
accruals, closed-form breakevens, published day-count conventions) rather than by
re-running the engine's own formula.
"""
import logging
import unittest

from opportunity_cost_tracking_for_idle_capital import (
    AccrualMethod,
    DayCount,
    OpportunityCostReport,
    OpportunityCostTrackerEngine,
    PortfolioCapitalState,
    REASON_BELOW_MIN_SWEEP_THRESHOLD,
    REASON_BELOW_THRESHOLD_AND_UNECONOMIC,
    REASON_NO_YIELD_ADVANTAGE,
    REASON_SWEEP_COST_EXCEEDS_YIELD,
    SweepConfig,
    accrue,
)

# The engine logs an audit line per call; silence it so test output stays readable.
logging.getLogger("opportunity_cost_tracking_for_idle_capital").setLevel(logging.CRITICAL)


def state(total, cash, rate, days=30.0, cash_yield=0.0):
    """Builds a reconciling snapshot: allocated = total - cash."""
    return PortfolioCapitalState(
        total_capital=total,
        allocated_capital=total - cash,
        unallocated_cash=cash,
        benchmark_rate_pct=rate,
        holding_period_days=days,
        cash_yield_pct=cash_yield,
    )


class TestAccrual(unittest.TestCase):
    """Day-count and accrual arithmetic, checked against hand-computed values."""

    def test_act_360_is_the_default_money_market_basis(self):
        # ACT/360 is the convention SOFR, the SOFR Averages/Index, and T-bills use.
        self.assertEqual(DayCount.ACT_360.basis, 360.0)
        self.assertEqual(DayCount.ACT_365F.basis, 365.0)
        self.assertIs(SweepConfig().day_count, DayCount.ACT_360)

    def test_simple_accrual_act_360_hand_computed(self):
        # 5.25% over 30 days ACT/360 = 0.0525 * 30/360 = 0.004375 exactly.
        self.assertAlmostEqual(accrue(5.25, 30.0, DayCount.ACT_360), 0.004375, places=12)

    def test_simple_accrual_act_365_hand_computed(self):
        # 5.25% over 30 days ACT/365F = 0.0525 * 30/365.
        self.assertAlmostEqual(accrue(5.25, 30.0, DayCount.ACT_365F), 0.0525 * 30.0 / 365.0,
                               places=12)

    def test_act_360_exceeds_act_365_by_exactly_365_over_360(self):
        # The whole point of the convention fix: 365/360 - 1 = 1.3888...% more accrual.
        a360 = accrue(4.00, 90.0, DayCount.ACT_360)
        a365 = accrue(4.00, 90.0, DayCount.ACT_365F)
        self.assertAlmostEqual(a360 / a365, 365.0 / 360.0, places=12)

    def test_daily_compounding_exceeds_simple_and_matches_closed_form(self):
        # (1 + 0.05/360)^365 - 1, computed independently here.
        expected = (1.0 + 0.05 / 360.0) ** 365 - 1.0
        got = accrue(5.0, 365.0, DayCount.ACT_360, AccrualMethod.DAILY_COMPOUNDED)
        self.assertAlmostEqual(got, expected, places=12)
        self.assertGreater(got, accrue(5.0, 365.0, DayCount.ACT_360))

    def test_zero_days_accrues_nothing_under_both_methods(self):
        self.assertEqual(accrue(5.25, 0.0, DayCount.ACT_360), 0.0)
        self.assertAlmostEqual(
            accrue(5.25, 0.0, DayCount.ACT_360, AccrualMethod.DAILY_COMPOUNDED), 0.0, places=15
        )

    def test_negative_rate_accrues_negative(self):
        self.assertLess(accrue(-0.75, 30.0, DayCount.ACT_360), 0.0)

    def test_compounding_overflow_surfaces_as_value_error(self):
        # math.pow raises OverflowError, which callers guarding on ValueError miss.
        with self.assertRaises(ValueError):
            accrue(5.25, 1e9, DayCount.ACT_360, AccrualMethod.DAILY_COMPOUNDED)

    def test_compounding_undefined_for_deeply_negative_rate(self):
        with self.assertRaises(ValueError):
            accrue(-40000.0, 30.0, DayCount.ACT_360, AccrualMethod.DAILY_COMPOUNDED)

    def test_invalid_accrual_inputs_rejected(self):
        with self.assertRaises(ValueError):
            accrue(5.25, -1.0, DayCount.ACT_360)
        with self.assertRaises(ValueError):
            accrue(float("nan"), 30.0, DayCount.ACT_360)
        with self.assertRaises(ValueError):
            accrue(5.25, float("inf"), DayCount.ACT_360)
        with self.assertRaises(TypeError):
            accrue("5.25", 30.0, DayCount.ACT_360)


class TestOpportunityCostTrackerEngine(unittest.TestCase):
    def setUp(self):
        self.engine = OpportunityCostTrackerEngine()

    # ------------------------------------------------------------------
    # Core drag calculation
    # ------------------------------------------------------------------
    def test_drag_and_sweep_recommendation_act_360(self):
        # $10M total, $2M idle (20% idle ratio), 5.25% benchmark, 30 days, ACT/360.
        # Period yield  = 0.0525 * 30/360        = 0.004375
        # Gross drag    = 2,000,000 * 0.004375   = $8,750.00  (ACT/365 gave $8,630.14)
        # Period bps    = 8,750 / 10,000,000 * 1e4 = 8.75 bps
        # Annualized bps= 0.20 * 0.0525 * 1e4      = 105.00 bps
        report = self.engine.analyze_opportunity_cost(state(10_000_000.0, 2_000_000.0, 5.25))

        self.assertEqual(report.status, "IDLE_CAPITAL_RATIO_EXCEEDED")
        self.assertEqual(report.idle_capital_ratio_pct, 20.0)
        self.assertAlmostEqual(report.gross_opportunity_cost_usd, 8750.00, places=2)
        self.assertAlmostEqual(report.drag_basis_points, 8.75, places=2)
        self.assertAlmostEqual(report.annualized_drag_basis_points, 105.00, places=2)
        self.assertEqual(report.recommendation, "SWEEP_TO_YIELD_BENCHMARK")
        self.assertIsNone(report.sweep_blocked_reason)
        self.assertAlmostEqual(report.net_yield_gain_usd, 8750.00 - 50.0, places=2)
        self.assertEqual(report.day_count, "ACT/360")

    def test_act_365_basis_reproduces_the_365_day_figure(self):
        """The old ACT/365 number is still reachable, but only by asking for it."""
        engine = OpportunityCostTrackerEngine(SweepConfig(day_count=DayCount.ACT_365F))
        report = engine.analyze_opportunity_cost(state(10_000_000.0, 2_000_000.0, 5.25))
        self.assertAlmostEqual(report.gross_opportunity_cost_usd, 8630.14, places=2)
        self.assertEqual(report.day_count, "ACT/365F")

    def test_annualized_bps_is_independent_of_holding_period(self):
        """Period drag scales with days; the annualized figure must not."""
        short = self.engine.analyze_opportunity_cost(state(10_000_000.0, 2_000_000.0, 5.25, days=7))
        long = self.engine.analyze_opportunity_cost(state(10_000_000.0, 2_000_000.0, 5.25, days=180))
        self.assertAlmostEqual(short.annualized_drag_basis_points, 105.00, places=2)
        self.assertAlmostEqual(long.annualized_drag_basis_points, 105.00, places=2)
        self.assertLess(short.drag_basis_points, long.drag_basis_points)

    # ------------------------------------------------------------------
    # Opportunity cost is net of the yield the cash already earns
    # ------------------------------------------------------------------
    def test_existing_cash_yield_is_netted_out_of_the_drag(self):
        # Cash already earning 4.80% against a 5.25% benchmark: the foregone yield is
        # the 45 bp spread, not the full 525 bp.
        # Period yield = 0.0045 * 30/360 = 0.000375 -> $750 on $2M.
        report = self.engine.analyze_opportunity_cost(
            state(10_000_000.0, 2_000_000.0, 5.25, cash_yield=4.80)
        )
        self.assertAlmostEqual(report.net_benchmark_spread_pct, 0.45, places=6)
        self.assertAlmostEqual(report.gross_opportunity_cost_usd, 750.00, places=2)
        self.assertAlmostEqual(report.annualized_drag_basis_points, 9.00, places=2)
        # Still worth sweeping: $750 recovered against a $50 round trip.
        self.assertEqual(report.recommendation, "SWEEP_TO_YIELD_BENCHMARK")

    def test_zero_default_cash_yield_reproduces_full_benchmark_drag(self):
        explicit = self.engine.analyze_opportunity_cost(
            state(10_000_000.0, 2_000_000.0, 5.25, cash_yield=0.0)
        )
        implied = self.engine.analyze_opportunity_cost(state(10_000_000.0, 2_000_000.0, 5.25))
        self.assertEqual(explicit.gross_opportunity_cost_usd, implied.gross_opportunity_cost_usd)

    def test_cash_already_beating_the_benchmark_blocks_the_sweep(self):
        report = self.engine.analyze_opportunity_cost(
            state(10_000_000.0, 2_000_000.0, 5.25, cash_yield=5.50)
        )
        self.assertEqual(report.recommendation, "MAINTAIN_IDLE_CASH")
        self.assertEqual(report.sweep_blocked_reason, REASON_NO_YIELD_ADVANTAGE)
        self.assertLess(report.gross_opportunity_cost_usd, 0.0)
        self.assertIsNone(report.breakeven_sweep_notional_usd)

    def test_cash_yield_exactly_equal_to_benchmark_is_no_advantage(self):
        report = self.engine.analyze_opportunity_cost(
            state(10_000_000.0, 2_000_000.0, 5.25, cash_yield=5.25)
        )
        self.assertEqual(report.gross_opportunity_cost_usd, 0.0)
        self.assertEqual(report.sweep_blocked_reason, REASON_NO_YIELD_ADVANTAGE)

    # ------------------------------------------------------------------
    # Operational buffer
    # ------------------------------------------------------------------
    def test_operational_buffer_is_excluded_from_sweepable_cash(self):
        # $500k idle, $450k reserved for margin/settlement -> only $50k sweepable,
        # which is below the $100k threshold even though the raw balance is not.
        engine = OpportunityCostTrackerEngine(SweepConfig(operational_buffer_usd=450_000.0))
        report = engine.analyze_opportunity_cost(state(10_000_000.0, 500_000.0, 5.25))
        self.assertEqual(report.sweepable_cash_usd, 50_000.0)
        self.assertEqual(report.recommendation, "MAINTAIN_IDLE_CASH")
        self.assertEqual(report.sweep_blocked_reason, REASON_BELOW_MIN_SWEEP_THRESHOLD)
        # Gross drag is still reported on the FULL balance -- the buffer costs yield too.
        self.assertAlmostEqual(report.gross_opportunity_cost_usd, 500_000.0 * 0.004375, places=2)
        self.assertAlmostEqual(report.recoverable_yield_usd, 50_000.0 * 0.004375, places=2)

    def test_buffer_larger_than_cash_yields_zero_sweepable_not_negative(self):
        engine = OpportunityCostTrackerEngine(SweepConfig(operational_buffer_usd=1_000_000.0))
        report = engine.analyze_opportunity_cost(state(10_000_000.0, 200_000.0, 5.25))
        self.assertEqual(report.sweepable_cash_usd, 0.0)
        self.assertEqual(report.recoverable_yield_usd, 0.0)
        self.assertEqual(report.recommendation, "MAINTAIN_IDLE_CASH")

    def test_zero_buffer_default_preserves_full_balance_as_sweepable(self):
        report = self.engine.analyze_opportunity_cost(state(10_000_000.0, 2_000_000.0, 5.25))
        self.assertEqual(report.sweepable_cash_usd, report.unallocated_cash_usd)
        self.assertEqual(report.operational_buffer_usd, 0.0)

    # ------------------------------------------------------------------
    # Sweep economics
    # ------------------------------------------------------------------
    def test_breakeven_notional_is_the_exact_indifference_point(self):
        # Breakeven = cost / period_yield = 50 / 0.004375 = $11,428.5714...
        report = self.engine.analyze_opportunity_cost(state(10_000_000.0, 2_000_000.0, 5.25))
        self.assertAlmostEqual(report.breakeven_sweep_notional_usd, 50.0 / 0.004375, places=2)

    def test_at_breakeven_notional_net_gain_is_zero_and_sweep_is_blocked(self):
        breakeven = 50.0 / 0.004375
        engine = OpportunityCostTrackerEngine(SweepConfig(min_sweep_threshold_usd=0.0))
        report = engine.analyze_opportunity_cost(state(10_000_000.0, breakeven, 5.25))
        self.assertAlmostEqual(report.net_yield_gain_usd, 0.0, places=2)
        # Strictly-greater-than gate: exactly breaking even is not a reason to trade.
        self.assertEqual(report.recommendation, "MAINTAIN_IDLE_CASH")
        self.assertEqual(report.sweep_blocked_reason, REASON_SWEEP_COST_EXCEEDS_YIELD)

    def test_just_above_breakeven_triggers_the_sweep(self):
        engine = OpportunityCostTrackerEngine(SweepConfig(min_sweep_threshold_usd=0.0))
        report = engine.analyze_opportunity_cost(state(10_000_000.0, 50.0 / 0.004375 + 1.0, 5.25))
        self.assertEqual(report.recommendation, "SWEEP_TO_YIELD_BENCHMARK")

    def test_large_balance_below_threshold_but_economic(self):
        # $99,999 clears the $50 cost easily but sits below the $100k policy threshold.
        report = self.engine.analyze_opportunity_cost(state(10_000_000.0, 99_999.0, 5.25))
        self.assertGreater(report.net_yield_gain_usd, 0.0)
        self.assertEqual(report.recommendation, "MAINTAIN_IDLE_CASH")
        self.assertEqual(report.sweep_blocked_reason, REASON_BELOW_MIN_SWEEP_THRESHOLD)

    def test_exactly_at_min_sweep_threshold_is_inclusive(self):
        report = self.engine.analyze_opportunity_cost(state(10_000_000.0, 100_000.0, 5.25))
        self.assertEqual(report.recommendation, "SWEEP_TO_YIELD_BENCHMARK")

    def test_small_cash_is_both_below_threshold_and_uneconomic(self):
        # $10k over 1 day: 10,000 * 0.0525 * 1/360 = $1.46 against a $50 round trip.
        report = self.engine.analyze_opportunity_cost(state(1_000_000.0, 10_000.0, 5.25, days=1.0))
        self.assertEqual(report.status, "IDLE_RATIO_HEALTHY")
        self.assertEqual(report.idle_capital_ratio_pct, 1.0)
        self.assertEqual(report.recommendation, "MAINTAIN_IDLE_CASH")
        self.assertEqual(report.sweep_blocked_reason, REASON_BELOW_THRESHOLD_AND_UNECONOMIC)
        self.assertLess(report.net_yield_gain_usd, 0.0)

    # ------------------------------------------------------------------
    # Idle ratio threshold
    # ------------------------------------------------------------------
    def test_idle_ratio_exactly_at_max_is_healthy(self):
        # Strict '>' comparison: 5.00% against a 5% cap is not a breach.
        report = self.engine.analyze_opportunity_cost(state(10_000_000.0, 500_000.0, 5.25))
        self.assertEqual(report.idle_capital_ratio_pct, 5.0)
        self.assertEqual(report.status, "IDLE_RATIO_HEALTHY")

    def test_idle_ratio_just_above_max_breaches(self):
        report = self.engine.analyze_opportunity_cost(state(10_000_000.0, 500_001.0, 5.25))
        self.assertEqual(report.status, "IDLE_CAPITAL_RATIO_EXCEEDED")

    def test_zero_idle_cash_is_healthy_with_no_drag(self):
        report = self.engine.analyze_opportunity_cost(state(10_000_000.0, 0.0, 5.25))
        self.assertEqual(report.idle_capital_ratio_pct, 0.0)
        self.assertEqual(report.gross_opportunity_cost_usd, 0.0)
        self.assertEqual(report.status, "IDLE_RATIO_HEALTHY")
        self.assertEqual(report.recommendation, "MAINTAIN_IDLE_CASH")

    # ------------------------------------------------------------------
    # Input validation -- each of these previously returned a confident report
    # ------------------------------------------------------------------
    def test_negative_cash_is_a_margin_debit_and_must_raise(self):
        # Previously: -20% idle ratio, negative drag, status 'IDLE_RATIO_HEALTHY'.
        with self.assertRaises(ValueError) as ctx:
            self.engine.analyze_opportunity_cost(
                PortfolioCapitalState(10_000_000.0, 12_000_000.0, -2_000_000.0, 5.25)
            )
        self.assertIn("margin debit", str(ctx.exception))

    def test_non_finite_benchmark_rate_must_raise(self):
        # Previously: NaN drag, silently recommended MAINTAIN_IDLE_CASH.
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(rate=bad):
                with self.assertRaises(ValueError):
                    self.engine.analyze_opportunity_cost(state(10_000_000.0, 2_000_000.0, bad))

    def test_non_finite_cash_yield_must_raise(self):
        with self.assertRaises(ValueError):
            self.engine.analyze_opportunity_cost(
                state(10_000_000.0, 2_000_000.0, 5.25, cash_yield=float("nan"))
            )

    def test_non_positive_holding_period_must_raise(self):
        # Previously: -30 days produced a NEGATIVE drag that read as a yield surplus.
        for bad_days in (-30.0, 0.0):
            with self.subTest(days=bad_days):
                with self.assertRaises(ValueError):
                    self.engine.analyze_opportunity_cost(
                        state(10_000_000.0, 2_000_000.0, 5.25, days=bad_days)
                    )

    def test_unreconciled_capital_must_raise(self):
        # Previously: allocated_capital was accepted and never used, so cash could
        # exceed total capital and report a 200% idle ratio as a sweep signal.
        with self.assertRaises(ValueError) as ctx:
            self.engine.analyze_opportunity_cost(
                PortfolioCapitalState(1_000_000.0, 8_000_000.0, 2_000_000.0, 5.25)
            )
        self.assertIn("does not reconcile", str(ctx.exception))

    def test_reconciliation_tolerance_absorbs_rounding_drift(self):
        # 50 cents of rounding drift is within the $1 default tolerance.
        report = self.engine.analyze_opportunity_cost(
            PortfolioCapitalState(10_000_000.0, 8_000_000.50, 2_000_000.0, 5.25)
        )
        self.assertEqual(report.idle_capital_ratio_pct, 20.0)

    def test_non_positive_total_capital_must_raise(self):
        for bad_total in (0.0, -1_000_000.0):
            with self.subTest(total=bad_total):
                with self.assertRaises(ValueError):
                    self.engine.analyze_opportunity_cost(
                        PortfolioCapitalState(bad_total, 0.0, 0.0, 5.25)
                    )

    def test_non_finite_total_capital_must_raise(self):
        with self.assertRaises(ValueError):
            self.engine.analyze_opportunity_cost(
                PortfolioCapitalState(float("nan"), 0.0, 0.0, 5.25)
            )

    def test_benchmark_rate_has_no_default(self):
        """A hardcoded rate is the pitfall this skill exists to prevent."""
        with self.assertRaises(TypeError):
            PortfolioCapitalState(10_000_000.0, 8_000_000.0, 2_000_000.0)  # noqa

    def test_bool_is_rejected_as_a_rate(self):
        with self.assertRaises(TypeError):
            self.engine.analyze_opportunity_cost(state(10_000_000.0, 2_000_000.0, True))

    # ------------------------------------------------------------------
    # Config validation
    # ------------------------------------------------------------------
    def test_idle_ratio_max_must_be_a_fraction(self):
        # Passing 5 to mean '5%' would make every ratio look healthy.
        with self.assertRaises(ValueError):
            SweepConfig(target_idle_ratio_max=5.0)
        with self.assertRaises(ValueError):
            SweepConfig(target_idle_ratio_max=-0.01)

    def test_negative_config_amounts_rejected(self):
        with self.assertRaises(ValueError):
            SweepConfig(sweep_transaction_cost_usd=-50.0)
        with self.assertRaises(ValueError):
            SweepConfig(min_sweep_threshold_usd=-1.0)
        with self.assertRaises(ValueError):
            SweepConfig(operational_buffer_usd=-1.0)

    def test_non_finite_config_amounts_rejected(self):
        with self.assertRaises(ValueError):
            SweepConfig(sweep_transaction_cost_usd=float("inf"))

    def test_config_accepts_string_enum_values_from_serialized_config(self):
        cfg = SweepConfig(day_count="ACT/365F", accrual_method="DAILY_COMPOUNDED")
        self.assertIs(cfg.day_count, DayCount.ACT_365F)
        self.assertIs(cfg.accrual_method, AccrualMethod.DAILY_COMPOUNDED)

    def test_unknown_day_count_string_rejected(self):
        with self.assertRaises(ValueError):
            SweepConfig(day_count="30/360")

    # ------------------------------------------------------------------
    # Report contract
    # ------------------------------------------------------------------
    def test_report_type_and_audit_note_content(self):
        report = self.engine.analyze_opportunity_cost(state(10_000_000.0, 2_000_000.0, 5.25))
        self.assertIsInstance(report, OpportunityCostReport)
        self.assertIn("ACT/360", report.audit_notes)
        self.assertIn("IDLE_CAPITAL_RATIO_EXCEEDED", report.audit_notes)
        self.assertIn("SWEEP_TO_YIELD_BENCHMARK", report.audit_notes)

    def test_daily_compounding_reports_a_larger_drag_than_simple(self):
        compounded = OpportunityCostTrackerEngine(
            SweepConfig(accrual_method=AccrualMethod.DAILY_COMPOUNDED)
        ).analyze_opportunity_cost(state(10_000_000.0, 2_000_000.0, 5.25, days=365.0))
        simple = self.engine.analyze_opportunity_cost(
            state(10_000_000.0, 2_000_000.0, 5.25, days=365.0)
        )
        self.assertGreater(compounded.gross_opportunity_cost_usd,
                           simple.gross_opportunity_cost_usd)
        self.assertEqual(compounded.accrual_method, "DAILY_COMPOUNDED")


if __name__ == '__main__':
    unittest.main()

"""Unit tests for incremental-capital-deployment-for-new-strategies."""
import logging
import unittest

from incremental_capital_deployment_for_new_strategies import (
    DEFAULT_MAINTENANCE_MAX_DRAWDOWN_PCT,
    IncrementalCapitalDeploymentEngine,
    IncrementalDeploymentReport,
    StrategyDeploymentState,
    TierPromotionGate,
    annualized_sharpe_standard_error,
    required_observations_for_sharpe_precision,
)

# The engine logs every demotion at CRITICAL/WARNING by design; keep that out of the
# test runner's output without lowering the module's own log level.
_module_logger = logging.getLogger("incremental_capital_deployment_for_new_strategies")
_module_logger.addHandler(logging.NullHandler())
_module_logger.propagate = False


def make_state(**overrides) -> StrategyDeploymentState:
    """A healthy Tier 1 strategy that clears every gate, unless overridden."""
    base = dict(
        strategy_id="STAT_ARB_01",
        current_tier=1,
        days_in_tier=35,
        realized_sharpe=1.4,
        realized_max_drawdown_pct=3.2,
        slippage_vs_backtest_ratio=1.1,
        target_full_capital_usd=1_000_000.0,
    )
    base.update(overrides)
    return StrategyDeploymentState(**base)


class TestSharpeStandardError(unittest.TestCase):
    """
    Independently verified against Lo (2002), "The Statistics of Sharpe Ratios",
    Financial Analysts Journal 58(4). Expected values are the paper's own published
    numbers, not a re-run of this module's formula.
    """

    def test_reproduces_lo_2002_table_1_per_period_values(self):
        # Lo's worked Table 1 examples: at T = 60, SE is 0.188 for a true SR of 1.50
        # and 0.303 for a true SR of 3.00. periods_per_year=1 gives the per-period form.
        self.assertAlmostEqual(
            annualized_sharpe_standard_error(1.50, 60, periods_per_year=1), 0.188, places=3)
        self.assertAlmostEqual(
            annualized_sharpe_standard_error(3.00, 60, periods_per_year=1), 0.303, places=3)

    def test_larger_sharpe_implies_larger_standard_error(self):
        # Lo: "for any given sample size T, larger Sharpe ratios imply larger standard errors."
        errors = [annualized_sharpe_standard_error(sr, 60, periods_per_year=1)
                  for sr in (0.5, 1.0, 1.5, 2.0, 3.0)]
        self.assertEqual(errors, sorted(errors))

    def test_standard_error_at_the_tier_1_gate_is_larger_than_the_threshold(self):
        # 30 daily observations, annualized: SE ~ 2.90 against a threshold of 1.0.
        se = annualized_sharpe_standard_error(1.0, 30)
        self.assertAlmostEqual(se, 2.9012, places=3)
        self.assertGreater(se, 1.0)

    def test_standard_error_shrinks_with_sqrt_of_sample_size(self):
        se_30 = annualized_sharpe_standard_error(1.0, 30)
        se_120 = annualized_sharpe_standard_error(1.0, 120)
        self.assertAlmostEqual(se_30 / se_120, 2.0, places=6)

    def test_required_observations_round_trips_with_standard_error(self):
        n = required_observations_for_sharpe_precision(0.5, 1.0)
        self.assertEqual(n, 1010)  # (252 + 0.5) / 0.25, rounded up ~ 4 years of daily data
        self.assertLessEqual(annualized_sharpe_standard_error(1.0, n), 0.5)
        self.assertGreater(annualized_sharpe_standard_error(1.0, n - 1), 0.5)

    def test_rejects_invalid_arguments(self):
        with self.assertRaises(ValueError):
            annualized_sharpe_standard_error(float("nan"), 30)
        with self.assertRaises(ValueError):
            annualized_sharpe_standard_error(1.0, 0)
        with self.assertRaises(ValueError):
            annualized_sharpe_standard_error(1.0, 30, periods_per_year=0)
        with self.assertRaises(ValueError):
            required_observations_for_sharpe_precision(0.0, 1.0)
        with self.assertRaises(ValueError):
            required_observations_for_sharpe_precision(0.5, float("inf"))


class TestStateValidation(unittest.TestCase):
    """
    Regression tests for input states that previously produced silently unsafe
    allocations rather than an error.
    """

    def test_nan_drawdown_is_rejected(self):
        # Regression: NaN >= emergency_limit is False, so a NaN drawdown used to skip
        # the emergency demotion entirely and retain the strategy at full allocation.
        with self.assertRaises(ValueError) as ctx:
            make_state(realized_max_drawdown_pct=float("nan"))
        self.assertIn("finite", str(ctx.exception))

    def test_infinite_and_nan_fields_are_rejected(self):
        for fieldname in ("realized_sharpe", "realized_max_drawdown_pct",
                          "slippage_vs_backtest_ratio", "target_full_capital_usd"):
            for bad in (float("nan"), float("inf"), float("-inf")):
                with self.subTest(field=fieldname, value=bad):
                    with self.assertRaises(ValueError):
                        make_state(**{fieldname: bad})

    def test_signed_negative_drawdown_is_rejected(self):
        # Regression: a caller using the signed convention (-14.5 for a 14.5% drawdown)
        # used to pass every "<= limit" promotion gate AND fail the ">= limit" emergency
        # gate, so a strategy in a 14.5% drawdown was promoted to 100% capital.
        with self.assertRaises(ValueError) as ctx:
            make_state(realized_max_drawdown_pct=-14.5)
        self.assertIn("positive magnitude", str(ctx.exception))

    def test_drawdown_above_100_percent_is_rejected(self):
        with self.assertRaises(ValueError):
            make_state(realized_max_drawdown_pct=150.0)

    def test_out_of_range_tier_raises_value_error_not_key_error(self):
        # Regression: current_tier=4 used to escape every branch and raise KeyError
        # from the allocation lookup.
        for bad_tier in (-1, 4, 99):
            with self.subTest(tier=bad_tier):
                with self.assertRaises(ValueError):
                    make_state(current_tier=bad_tier)

    def test_negative_capital_is_rejected(self):
        # Regression: a negative target used to produce a negative allocation.
        with self.assertRaises(ValueError):
            make_state(target_full_capital_usd=-1_000_000.0)

    def test_negative_days_and_error_counts_are_rejected(self):
        with self.assertRaises(ValueError):
            make_state(days_in_tier=-50)
        with self.assertRaises(ValueError):
            make_state(execution_errors_in_tier=-1)

    def test_non_positive_slippage_ratio_is_rejected(self):
        for bad in (0.0, -1.1):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    make_state(slippage_vs_backtest_ratio=bad)

    def test_blank_strategy_id_is_rejected(self):
        for bad in ("", "   "):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    make_state(strategy_id=bad)

    def test_zero_capital_is_allowed(self):
        state = make_state(target_full_capital_usd=0.0)
        report = IncrementalCapitalDeploymentEngine().evaluate_strategy_deployment(state)
        self.assertEqual(report.allocated_capital_usd, 0.0)


class TestEngineConstruction(unittest.TestCase):

    def test_rejects_maintenance_limit_at_or_above_emergency_limit(self):
        # A maintenance limit >= the emergency limit is unreachable dead code, because
        # the emergency branch resolves first.
        with self.assertRaises(ValueError):
            IncrementalCapitalDeploymentEngine(
                emergency_max_drawdown_limit_pct=12.0,
                maintenance_max_drawdown_pct={1: 12.0, 2: 12.0, 3: 12.0},
            )

    def test_rejects_invalid_emergency_limit(self):
        for bad in (0.0, -5.0, 101.0, float("nan")):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    IncrementalCapitalDeploymentEngine(emergency_max_drawdown_limit_pct=bad)

    def test_rejects_promotion_gate_above_tier_3(self):
        with self.assertRaises(ValueError):
            IncrementalCapitalDeploymentEngine(
                promotion_gates={3: TierPromotionGate(from_tier=3, min_days_in_tier=1)})

    def test_rejects_non_state_argument(self):
        with self.assertRaises(TypeError):
            IncrementalCapitalDeploymentEngine().evaluate_strategy_deployment({"tier": 1})

    def test_tier_mutated_after_construction_is_rejected_not_a_key_error(self):
        # StrategyDeploymentState is a mutable dataclass, so __post_init__ validation
        # can be bypassed by assigning after construction.
        state = make_state()
        state.current_tier = 7
        with self.assertRaises(ValueError):
            IncrementalCapitalDeploymentEngine().evaluate_strategy_deployment(state)

    def test_non_numeric_field_raises_value_error_not_type_error(self):
        for bad in (None, "not-a-number", object()):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    make_state(realized_max_drawdown_pct=bad)


class TestPromotion(unittest.TestCase):

    def setUp(self):
        self.engine = IncrementalCapitalDeploymentEngine(emergency_max_drawdown_limit_pct=12.0)

    def test_promotion_from_tier_1_to_tier_2(self):
        # 35 days, Sharpe 1.4, Max DD 3.2%, Slippage 1.1x -> Tier 2 (50% = $500k).
        report = self.engine.evaluate_strategy_deployment(make_state())
        self.assertEqual(report.promotion_status, "PROMOTED")
        self.assertEqual(report.new_tier, 2)
        self.assertEqual(report.capital_allocation_pct, 0.50)
        self.assertEqual(report.allocated_capital_usd, 500_000.0)
        self.assertEqual(report.failed_gates, ())

    def test_retained_in_tier_when_gates_not_met(self):
        report = self.engine.evaluate_strategy_deployment(make_state(days_in_tier=15))
        self.assertEqual(report.promotion_status, "RETAINED_CURRENT_TIER")
        self.assertEqual(report.new_tier, 1)
        self.assertEqual(report.allocated_capital_usd, 100_000.0)

    def test_failed_gates_name_every_blocking_condition(self):
        report = self.engine.evaluate_strategy_deployment(
            make_state(days_in_tier=15, realized_sharpe=0.4,
                       realized_max_drawdown_pct=6.0, slippage_vs_backtest_ratio=1.8))
        joined = " | ".join(report.failed_gates)
        self.assertIn("min_days_in_tier", joined)
        self.assertIn("min_realized_sharpe", joined)
        self.assertIn("max_drawdown_pct", joined)
        self.assertIn("max_slippage_ratio", joined)
        self.assertIn("Blocking:", report.audit_notes)

    def test_tier_2_to_tier_3_is_blocked_by_excess_slippage(self):
        # Regression: the 50% -> 100% step, the largest single capital increase in the
        # ladder, previously had no slippage gate at all and promoted at 5.0x slippage.
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=2, days_in_tier=60, realized_sharpe=1.3,
                       realized_max_drawdown_pct=4.0, slippage_vs_backtest_ratio=1.9))
        self.assertEqual(report.promotion_status, "RETAINED_CURRENT_TIER")
        self.assertEqual(report.new_tier, 2)
        self.assertTrue(any("max_slippage_ratio" in g for g in report.failed_gates))

    def test_tier_2_to_tier_3_promotes_when_slippage_is_acceptable(self):
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=2, days_in_tier=60, realized_sharpe=1.3,
                       realized_max_drawdown_pct=4.0, slippage_vs_backtest_ratio=1.4))
        self.assertEqual(report.promotion_status, "PROMOTED")
        self.assertEqual(report.new_tier, 3)
        self.assertEqual(report.allocated_capital_usd, 1_000_000.0)

    def test_tier_0_to_tier_1_blocked_by_execution_errors(self):
        # SKILL.md always documented "0 execution crashes" for this gate; it is now
        # actually enforced rather than only described.
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=0, days_in_tier=20, realized_max_drawdown_pct=1.0,
                       execution_errors_in_tier=1))
        self.assertEqual(report.promotion_status, "RETAINED_CURRENT_TIER")
        self.assertEqual(report.new_tier, 0)
        self.assertEqual(report.allocated_capital_usd, 0.0)
        self.assertTrue(any("max_execution_errors" in g for g in report.failed_gates))

    def test_tier_0_to_tier_1_blocked_by_paper_drawdown(self):
        # The 0% -> 10% step is the first commitment of real capital; a paper record
        # that already breached the seed drawdown tolerance does not earn it.
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=0, days_in_tier=20, realized_max_drawdown_pct=6.0))
        self.assertEqual(report.new_tier, 0)
        self.assertTrue(any("max_drawdown_pct" in g for g in report.failed_gates))

    def test_tier_0_to_tier_1_promotes_on_a_clean_paper_record(self):
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=0, days_in_tier=14, realized_sharpe=-3.0,
                       realized_max_drawdown_pct=1.0))
        self.assertEqual(report.promotion_status, "PROMOTED")
        self.assertEqual(report.new_tier, 1)
        self.assertEqual(report.allocated_capital_usd, 100_000.0)
        # Paper Sharpe is deliberately not gated at this transition.
        self.assertIsNone(report.sharpe_standard_error)

    def test_promotion_is_capped_at_one_tier_per_evaluation(self):
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=0, days_in_tier=5_000, realized_sharpe=5.0,
                       realized_max_drawdown_pct=0.5))
        self.assertEqual(report.new_tier, 1)

    def test_tier_3_is_terminal(self):
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=3, days_in_tier=500, realized_sharpe=3.0,
                       realized_max_drawdown_pct=1.0))
        self.assertEqual(report.promotion_status, "RETAINED_CURRENT_TIER")
        self.assertEqual(report.new_tier, 3)
        self.assertEqual(report.failed_gates, ())
        self.assertIn("at maximum tier", report.audit_notes)

    def test_days_in_tier_boundary_is_inclusive(self):
        self.assertEqual(
            self.engine.evaluate_strategy_deployment(make_state(days_in_tier=30)).new_tier, 2)
        self.assertEqual(
            self.engine.evaluate_strategy_deployment(make_state(days_in_tier=29)).new_tier, 1)

    def test_sharpe_and_drawdown_gate_boundaries_are_inclusive(self):
        # Sharpe exactly at 1.0 and drawdown exactly at 5.0 both pass.
        report = self.engine.evaluate_strategy_deployment(
            make_state(realized_sharpe=1.0, realized_max_drawdown_pct=5.0,
                       slippage_vs_backtest_ratio=1.5))
        self.assertEqual(report.new_tier, 2)
        # A hair beyond each does not.
        self.assertEqual(self.engine.evaluate_strategy_deployment(
            make_state(realized_sharpe=0.99)).new_tier, 1)
        self.assertEqual(self.engine.evaluate_strategy_deployment(
            make_state(realized_max_drawdown_pct=5.01)).new_tier, 1)
        self.assertEqual(self.engine.evaluate_strategy_deployment(
            make_state(slippage_vs_backtest_ratio=1.51)).new_tier, 1)


class TestEmergencyDeactivation(unittest.TestCase):

    def setUp(self):
        self.engine = IncrementalCapitalDeploymentEngine(emergency_max_drawdown_limit_pct=12.0)

    def test_emergency_deactivation_on_drawdown_breach(self):
        report = self.engine.evaluate_strategy_deployment(
            make_state(strategy_id="MOMENTUM_02", current_tier=2, days_in_tier=45,
                       realized_sharpe=0.5, realized_max_drawdown_pct=14.5,
                       slippage_vs_backtest_ratio=2.0))
        self.assertEqual(report.promotion_status, "DEMOTED_DRAWDOWN_BREACH")
        self.assertEqual(report.new_tier, 0)
        self.assertEqual(report.allocated_capital_usd, 0.0)
        self.assertEqual(report.tier_name, "EMERGENCY_DEACTIVATED")

    def test_emergency_limit_boundary_is_inclusive(self):
        at_limit = self.engine.evaluate_strategy_deployment(
            make_state(realized_max_drawdown_pct=12.0))
        self.assertEqual(at_limit.promotion_status, "DEMOTED_DRAWDOWN_BREACH")
        self.assertEqual(at_limit.new_tier, 0)

    def test_emergency_outranks_promotion_and_maintenance(self):
        # Every promotion gate is satisfied except drawdown; emergency still wins.
        report = self.engine.evaluate_strategy_deployment(
            make_state(days_in_tier=400, realized_sharpe=4.0,
                       realized_max_drawdown_pct=13.0, slippage_vs_backtest_ratio=1.0))
        self.assertEqual(report.promotion_status, "DEMOTED_DRAWDOWN_BREACH")
        self.assertEqual(report.new_tier, 0)

    def test_emergency_applies_from_tier_0(self):
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=0, days_in_tier=30, realized_max_drawdown_pct=20.0))
        self.assertEqual(report.promotion_status, "DEMOTED_DRAWDOWN_BREACH")
        self.assertEqual(report.new_tier, 0)


class TestMaintenanceDemotion(unittest.TestCase):
    """
    Regression tests: previously the only downside path was the 12% emergency cliff, so
    a Tier 3 strategy at 11.9% drawdown kept 100% of capital until it fell off it.
    """

    def setUp(self):
        self.engine = IncrementalCapitalDeploymentEngine(emergency_max_drawdown_limit_pct=12.0)

    def test_drawdown_between_maintenance_and_emergency_steps_down_one_tier(self):
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=3, days_in_tier=100,
                       realized_max_drawdown_pct=11.0, realized_sharpe=0.2))
        self.assertEqual(report.promotion_status, "DEMOTED_MAINTENANCE_BREACH")
        self.assertEqual(report.new_tier, 2)
        self.assertEqual(report.allocated_capital_usd, 500_000.0)
        self.assertIn("maintenance drawdown", report.audit_notes)

    def test_demotion_steps_down_exactly_one_tier(self):
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=2, days_in_tier=70, realized_max_drawdown_pct=11.0))
        self.assertEqual(report.new_tier, 1)
        self.assertEqual(report.allocated_capital_usd, 100_000.0)

    def test_excess_slippage_steps_down_one_tier(self):
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=2, days_in_tier=70, realized_max_drawdown_pct=2.0,
                       slippage_vs_backtest_ratio=2.5))
        self.assertEqual(report.promotion_status, "DEMOTED_MAINTENANCE_BREACH")
        self.assertEqual(report.new_tier, 1)
        self.assertIn("maintenance slippage", report.audit_notes)

    def test_maintenance_boundary_is_exclusive(self):
        limit = DEFAULT_MAINTENANCE_MAX_DRAWDOWN_PCT[3]
        at_limit = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=3, days_in_tier=100, realized_max_drawdown_pct=limit))
        self.assertEqual(at_limit.promotion_status, "RETAINED_CURRENT_TIER")
        just_over = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=3, days_in_tier=100,
                       realized_max_drawdown_pct=limit + 0.01))
        self.assertEqual(just_over.promotion_status, "DEMOTED_MAINTENANCE_BREACH")

    def test_poor_sharpe_alone_never_demotes(self):
        # Deliberate: at 30-60 daily observations the Sharpe standard error exceeds the
        # thresholds themselves (Lo 2002), so demoting on Sharpe would thrash capital
        # on noise. Only realized drawdown and realized slippage de-risk.
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=3, days_in_tier=100, realized_sharpe=-5.0,
                       realized_max_drawdown_pct=2.0))
        self.assertEqual(report.promotion_status, "RETAINED_CURRENT_TIER")
        self.assertEqual(report.new_tier, 3)

    def test_maintenance_band_gives_hysteresis_above_the_entry_gate(self):
        # 6% drawdown fails the 5% entry gate for Tier 2 but does not breach Tier 1's
        # 8% maintenance limit, so the strategy holds rather than oscillating.
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=1, realized_max_drawdown_pct=6.0))
        self.assertEqual(report.promotion_status, "RETAINED_CURRENT_TIER")
        self.assertEqual(report.new_tier, 1)

    def test_maintenance_demotion_can_be_disabled(self):
        engine = IncrementalCapitalDeploymentEngine(enable_maintenance_demotion=False)
        report = engine.evaluate_strategy_deployment(
            make_state(current_tier=3, days_in_tier=100, realized_max_drawdown_pct=11.0))
        self.assertEqual(report.promotion_status, "RETAINED_CURRENT_TIER")
        self.assertEqual(report.new_tier, 3)


class TestDaysInTierReset(unittest.TestCase):
    """
    Regression: nothing told the caller to reset ``days_in_tier`` on a tier change, so a
    70-day *paper* record satisfied the 30-day *live* gate and the strategy jumped
    Tier 0 -> 1 -> 2 in two evaluations without a single live day at Tier 1.
    """

    def setUp(self):
        self.engine = IncrementalCapitalDeploymentEngine()

    def test_next_days_in_tier_is_zero_on_promotion(self):
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=0, days_in_tier=70, realized_max_drawdown_pct=1.0))
        self.assertEqual(report.new_tier, 1)
        self.assertEqual(report.next_days_in_tier, 0)

    def test_next_days_in_tier_is_zero_on_demotion(self):
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=3, days_in_tier=100, realized_max_drawdown_pct=11.0))
        self.assertEqual(report.next_days_in_tier, 0)
        emergency = self.engine.evaluate_strategy_deployment(
            make_state(realized_max_drawdown_pct=30.0))
        self.assertEqual(emergency.next_days_in_tier, 0)

    def test_next_days_in_tier_is_unchanged_on_retention(self):
        report = self.engine.evaluate_strategy_deployment(make_state(days_in_tier=15))
        self.assertEqual(report.next_days_in_tier, 15)

    def test_following_next_days_in_tier_prevents_the_double_promotion(self):
        first = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=0, days_in_tier=70, realized_max_drawdown_pct=1.0))
        self.assertEqual(first.new_tier, 1)
        # The caller persists next_days_in_tier, as the report instructs.
        second = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=first.new_tier, days_in_tier=first.next_days_in_tier,
                       realized_max_drawdown_pct=1.0))
        self.assertEqual(second.promotion_status, "RETAINED_CURRENT_TIER")
        self.assertEqual(second.new_tier, 1)


class TestSharpeConfidenceReporting(unittest.TestCase):

    def setUp(self):
        self.engine = IncrementalCapitalDeploymentEngine()

    def test_tier_1_promotion_is_reported_as_not_statistically_decisive(self):
        report = self.engine.evaluate_strategy_deployment(make_state(days_in_tier=35))
        self.assertEqual(report.promotion_status, "PROMOTED")
        self.assertIsNotNone(report.sharpe_standard_error)
        self.assertGreater(report.sharpe_standard_error, 1.0)
        self.assertFalse(report.sharpe_gate_conclusive)
        self.assertIn("NOT statistically decisive", report.audit_notes)

    def test_a_long_enough_track_record_is_reported_as_decisive(self):
        # ~28 years of daily data at Sharpe 1.4 against a 1.0 threshold. The size of
        # this number is the point: the gate is a floor, not evidence of edge.
        report = self.engine.evaluate_strategy_deployment(make_state(days_in_tier=7_000))
        self.assertTrue(report.sharpe_gate_conclusive)
        self.assertIn("decisive", report.audit_notes)

    def test_standard_error_is_reported_on_retention_too(self):
        report = self.engine.evaluate_strategy_deployment(make_state(days_in_tier=15))
        self.assertIsNotNone(report.sharpe_standard_error)
        self.assertAlmostEqual(
            report.sharpe_standard_error,
            annualized_sharpe_standard_error(1.4, 15), places=12)


class TestAllocationArithmetic(unittest.TestCase):

    def setUp(self):
        self.engine = IncrementalCapitalDeploymentEngine()

    def test_each_tier_allocates_its_documented_percentage(self):
        expected = {0: (0.0, 0.0), 1: (0.10, 100_000.0),
                    2: (0.50, 500_000.0), 3: (1.00, 1_000_000.0)}
        for tier, (pct, usd) in expected.items():
            with self.subTest(tier=tier):
                # days_in_tier=0 blocks every promotion, isolating the allocation maths.
                report = self.engine.evaluate_strategy_deployment(
                    make_state(current_tier=tier, days_in_tier=0,
                               realized_max_drawdown_pct=1.0))
                self.assertEqual(report.new_tier, tier)
                self.assertEqual(report.capital_allocation_pct, pct)
                self.assertEqual(report.allocated_capital_usd, usd)

    def test_allocation_is_rounded_to_cents(self):
        report = self.engine.evaluate_strategy_deployment(
            make_state(current_tier=1, days_in_tier=0, target_full_capital_usd=333_333.339))
        self.assertEqual(report.allocated_capital_usd, 33_333.33)

    def test_allocation_never_exceeds_target_capital(self):
        for tier in (0, 1, 2, 3):
            with self.subTest(tier=tier):
                report = self.engine.evaluate_strategy_deployment(
                    make_state(current_tier=tier, days_in_tier=0,
                               realized_max_drawdown_pct=1.0))
                self.assertLessEqual(report.allocated_capital_usd, 1_000_000.0)
                self.assertGreaterEqual(report.allocated_capital_usd, 0.0)


class TestDeterminism(unittest.TestCase):

    def test_repeated_evaluation_of_the_same_state_is_identical(self):
        engine = IncrementalCapitalDeploymentEngine()
        first = engine.evaluate_strategy_deployment(make_state())
        second = engine.evaluate_strategy_deployment(make_state())
        self.assertEqual(first, second)

    def test_engine_holds_no_per_strategy_state(self):
        engine = IncrementalCapitalDeploymentEngine()
        engine.evaluate_strategy_deployment(
            make_state(strategy_id="A", realized_max_drawdown_pct=30.0))
        clean = engine.evaluate_strategy_deployment(make_state(strategy_id="B"))
        self.assertEqual(clean.promotion_status, "PROMOTED")
        self.assertEqual(clean.new_tier, 2)

    def test_report_is_a_plain_dataclass(self):
        report = IncrementalCapitalDeploymentEngine().evaluate_strategy_deployment(make_state())
        self.assertIsInstance(report, IncrementalDeploymentReport)
        self.assertEqual(report.previous_tier, 1)


if __name__ == '__main__':
    unittest.main()

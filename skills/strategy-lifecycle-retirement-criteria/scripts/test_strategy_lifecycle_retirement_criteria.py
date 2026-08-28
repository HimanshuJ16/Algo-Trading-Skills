"""
Unit tests for strategy-lifecycle-retirement-criteria.

Expected values are derived independently of the implementation. The drift
figures in particular are computed by hand in the docstring of each test so a
copied formula cannot make a broken engine look correct.
"""
import logging
import unittest

from strategy_lifecycle_retirement_criteria import (
    DEFAULT_MIN_IC_T_STAT,
    DEFAULT_MIN_LIVE_INFORMATION_RATIO,
    RetirementDecision,
    StrategyLifecycleRetirementCriteria,
    StrategyLifecycleRetirementCriteriaConfig,
    StrategyLifecycleRetirementEngine,
    StrategyPerformanceMetrics,
    StrategyRetirementReport,
)

# The engine logs an ERROR on every retirement; silence it so a passing run is
# not buried in expected output.
logging.getLogger("strategy_lifecycle_retirement_criteria").setLevel(logging.CRITICAL)


def make_metrics(**overrides) -> StrategyPerformanceMetrics:
    """A healthy baseline payload; override one field per test."""
    base = dict(
        strategy_id="BASELINE",
        backtest_sharpe=2.0,
        backtest_max_drawdown_pct=10.0,
        live_sharpe=1.8,
        live_max_drawdown_pct=11.0,       # 11.0 <= 15.0 (1.5 x 10.0)
        live_information_ratio=1.2,       # 1.2 >= 0.50
        live_ic_t_stat=2.5,               # 2.5 >= 1.96
        live_realized_annual_return_pct=18.0,
        backtest_annual_return_pct=20.0,  # drift = -10% > -40%
    )
    base.update(overrides)
    return StrategyPerformanceMetrics(**base)


class TestStrategyLifecycleLegacy(unittest.TestCase):
    """The legacy shim is part of the public API and must keep working."""

    def test_execute_true(self):
        config = StrategyLifecycleRetirementCriteriaConfig(enabled=True)
        engine = StrategyLifecycleRetirementCriteria(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = StrategyLifecycleRetirementCriteriaConfig(enabled=False)
        engine = StrategyLifecycleRetirementCriteria(config)
        self.assertFalse(engine.execute())


class TestDecisionLadder(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyLifecycleRetirementEngine(
            min_live_information_ratio=0.50,
            max_drawdown_multiplier=1.50,
            min_ic_t_stat=1.96,
            max_allowed_performance_drift_pct=-40.0,
        )

    def test_active_healthy_strategy(self):
        report = self.engine.evaluate_strategy(make_metrics(strategy_id="STAT_ARB_HEALTHY"))
        self.assertEqual(report.decision, RetirementDecision.ACTIVE_HEALTHY)
        self.assertFalse(report.is_retired)
        self.assertEqual(report.breached_criteria, [])
        self.assertEqual(report.skipped_criteria, [])
        self.assertEqual(report.evaluated_criteria_count, 4)
        # (18 - 20) / 20 * 100 = -10.0
        self.assertAlmostEqual(report.performance_drift_pct, -10.0, places=6)
        self.assertAlmostEqual(report.return_gap_pct_points, -2.0, places=6)
        self.assertIsNone(report.escalation_reason)

    def test_one_breach_is_needs_review(self):
        """Only the IC t-stat fails: 1.50 < 1.96."""
        report = self.engine.evaluate_strategy(make_metrics(live_ic_t_stat=1.50))
        self.assertEqual(report.decision, RetirementDecision.NEEDS_REVIEW)
        self.assertFalse(report.is_retired)
        self.assertEqual(len(report.breached_criteria), 1)
        self.assertIn("IC_STATISTICAL_DECAY", report.breached_criteria[0])

    def test_two_breaches_with_positive_ir_is_reduce_allocation(self):
        """
        IC t-stat and drawdown fail; IR stays positive so the escalation
        override must NOT fire. Live DD 16.0 > 15.0 allowed.
        """
        report = self.engine.evaluate_strategy(
            make_metrics(live_ic_t_stat=1.0, live_max_drawdown_pct=16.0)
        )
        self.assertEqual(report.decision, RetirementDecision.REDUCE_ALLOCATION)
        self.assertFalse(report.is_retired)
        self.assertEqual(len(report.breached_criteria), 2)
        self.assertIsNone(report.escalation_reason)

    def test_mandatory_retirement_on_multiple_breaches(self):
        """
        All four criteria fail. Allowed DD = 8.0 x 1.5 = 12.0 < 20.0.
        drift = (-5 - 25) / 25 * 100 = -120.0 < -40.0.
        """
        metrics = make_metrics(
            strategy_id="FAILED_ALPHA_STRAT",
            backtest_sharpe=2.5,
            backtest_max_drawdown_pct=8.0,
            live_sharpe=-0.5,
            live_max_drawdown_pct=20.0,
            live_information_ratio=-0.2,
            live_ic_t_stat=0.4,
            live_realized_annual_return_pct=-5.0,
            backtest_annual_return_pct=25.0,
        )
        report = self.engine.evaluate_strategy(metrics)
        self.assertEqual(report.decision, RetirementDecision.MANDATORY_RETIREMENT)
        self.assertTrue(report.is_retired)
        self.assertEqual(len(report.breached_criteria), 4)
        self.assertAlmostEqual(report.performance_drift_pct, -120.0, places=6)

    def test_escalation_override_is_reported_not_silent(self):
        """
        Regression: the engine retires on TWO breaches when a drawdown breach
        coincides with a negative live IR. The old build did this silently while
        SKILL.md documented 2 breaches as REDUCE_ALLOCATION. The override must
        still fire, and must now name itself in ``escalation_reason``.
        """
        report = self.engine.evaluate_strategy(
            make_metrics(live_max_drawdown_pct=20.0, live_information_ratio=-0.2)
        )
        self.assertEqual(report.decision, RetirementDecision.MANDATORY_RETIREMENT)
        self.assertTrue(report.is_retired)
        self.assertEqual(len(report.breached_criteria), 2)
        self.assertIsNotNone(report.escalation_reason)
        self.assertIn("OVERRIDE_DD_AND_NEGATIVE_IR", report.escalation_reason)
        self.assertIn("ESCALATION", report.audit_notes)

    def test_escalation_override_can_be_disabled(self):
        engine = StrategyLifecycleRetirementEngine(
            escalate_on_negative_ir_with_drawdown_breach=False
        )
        report = engine.evaluate_strategy(
            make_metrics(live_max_drawdown_pct=20.0, live_information_ratio=-0.2)
        )
        self.assertEqual(report.decision, RetirementDecision.REDUCE_ALLOCATION)
        self.assertFalse(report.is_retired)
        self.assertIsNone(report.escalation_reason)

    def test_escalation_needs_an_actual_drawdown_breach(self):
        """A negative IR alone is one breach, not a retirement trigger."""
        report = self.engine.evaluate_strategy(make_metrics(live_information_ratio=-0.2))
        self.assertEqual(report.decision, RetirementDecision.NEEDS_REVIEW)
        self.assertIsNone(report.escalation_reason)

    def test_thresholds_are_echoed_for_reproducibility(self):
        report = self.engine.evaluate_strategy(make_metrics())
        self.assertEqual(report.thresholds_applied["min_live_information_ratio"], 0.50)
        self.assertEqual(report.thresholds_applied["max_drawdown_multiplier"], 1.50)
        self.assertEqual(report.thresholds_applied["min_ic_t_stat"], 1.96)
        self.assertEqual(
            report.thresholds_applied["max_allowed_performance_drift_pct"], -40.0
        )

    def test_report_is_the_documented_type(self):
        self.assertIsInstance(
            self.engine.evaluate_strategy(make_metrics()), StrategyRetirementReport
        )


class TestBoundaryBehaviour(unittest.TestCase):
    """Comparisons are strict: exactly on the threshold is not a breach."""

    def setUp(self):
        self.engine = StrategyLifecycleRetirementEngine()

    def test_ir_exactly_at_threshold_passes(self):
        report = self.engine.evaluate_strategy(
            make_metrics(live_information_ratio=DEFAULT_MIN_LIVE_INFORMATION_RATIO)
        )
        self.assertEqual(report.decision, RetirementDecision.ACTIVE_HEALTHY)

    def test_ir_one_tick_below_threshold_breaches(self):
        report = self.engine.evaluate_strategy(
            make_metrics(live_information_ratio=DEFAULT_MIN_LIVE_INFORMATION_RATIO - 1e-9)
        )
        self.assertEqual(report.decision, RetirementDecision.NEEDS_REVIEW)

    def test_ic_t_stat_exactly_at_threshold_passes(self):
        report = self.engine.evaluate_strategy(
            make_metrics(live_ic_t_stat=DEFAULT_MIN_IC_T_STAT)
        )
        self.assertEqual(report.decision, RetirementDecision.ACTIVE_HEALTHY)

    def test_drawdown_exactly_at_limit_passes(self):
        """Allowed = 10.0 x 1.5 = 15.0; exactly 15.0 is not '>' the limit."""
        report = self.engine.evaluate_strategy(make_metrics(live_max_drawdown_pct=15.0))
        self.assertEqual(report.decision, RetirementDecision.ACTIVE_HEALTHY)

    def test_drawdown_a_hair_over_limit_breaches(self):
        report = self.engine.evaluate_strategy(
            make_metrics(live_max_drawdown_pct=15.000001)
        )
        self.assertEqual(report.decision, RetirementDecision.NEEDS_REVIEW)
        self.assertIn("DRAWDOWN_BREACH", report.breached_criteria[0])

    def test_drift_exactly_at_limit_passes(self):
        """live 12.0 vs backtest 20.0 -> (12-20)/20*100 = -40.0, not < -40.0."""
        report = self.engine.evaluate_strategy(
            make_metrics(live_realized_annual_return_pct=12.0)
        )
        self.assertAlmostEqual(report.performance_drift_pct, -40.0, places=6)
        self.assertEqual(report.decision, RetirementDecision.ACTIVE_HEALTHY)

    def test_drift_below_limit_breaches(self):
        """live 11.0 vs backtest 20.0 -> -45.0 < -40.0."""
        report = self.engine.evaluate_strategy(
            make_metrics(live_realized_annual_return_pct=11.0)
        )
        self.assertAlmostEqual(report.performance_drift_pct, -45.0, places=6)
        self.assertEqual(report.decision, RetirementDecision.NEEDS_REVIEW)
        self.assertIn("PERFORMANCE_DRIFT", report.breached_criteria[0])

    def test_live_outperformance_gives_positive_drift_and_no_breach(self):
        """live 30.0 vs backtest 20.0 -> +50.0."""
        report = self.engine.evaluate_strategy(
            make_metrics(live_realized_annual_return_pct=30.0)
        )
        self.assertAlmostEqual(report.performance_drift_pct, 50.0, places=6)
        self.assertEqual(report.decision, RetirementDecision.ACTIVE_HEALTHY)


class TestDrawdownSignConvention(unittest.TestCase):
    """
    Regression for the highest-severity defect: under the negative drawdown
    convention the old build compared -30.0 > -15.0 as False and certified a
    strategy drawing down three times its backtested worst case as healthy.
    """

    def setUp(self):
        self.engine = StrategyLifecycleRetirementEngine()

    def test_negative_live_drawdown_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_strategy(make_metrics(live_max_drawdown_pct=-30.0))
        self.assertIn("positive magnitudes", str(ctx.exception))

    def test_negative_backtest_drawdown_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_strategy(
                make_metrics(backtest_max_drawdown_pct=-10.0, live_max_drawdown_pct=-30.0)
            )

    def test_zero_backtest_drawdown_raises_rather_than_gating_on_a_blank_field(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_strategy(make_metrics(backtest_max_drawdown_pct=0.0))
        self.assertIn("unpopulated", str(ctx.exception))

    def test_zero_live_drawdown_is_allowed(self):
        report = self.engine.evaluate_strategy(make_metrics(live_max_drawdown_pct=0.0))
        self.assertEqual(report.decision, RetirementDecision.ACTIVE_HEALTHY)


class TestNonFiniteInputRejection(unittest.TestCase):
    """
    Regression: NaN fails every ``<`` and ``>`` comparison, so the old build
    scored a fully corrupt payload as ACTIVE_HEALTHY with zero breaches.
    """

    def setUp(self):
        self.engine = StrategyLifecycleRetirementEngine()

    def test_nan_in_each_numeric_field_raises(self):
        nan = float("nan")
        for name in (
            "backtest_sharpe",
            "backtest_max_drawdown_pct",
            "live_sharpe",
            "live_max_drawdown_pct",
            "live_information_ratio",
            "live_ic_t_stat",
            "live_realized_annual_return_pct",
            "backtest_annual_return_pct",
        ):
            with self.subTest(field=name):
                with self.assertRaises(ValueError):
                    self.engine.evaluate_strategy(make_metrics(**{name: nan}))

    def test_infinity_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_strategy(
                make_metrics(live_information_ratio=float("inf"))
            )
        with self.assertRaises(ValueError):
            self.engine.evaluate_strategy(
                make_metrics(live_ic_t_stat=float("-inf"))
            )

    def test_non_numeric_metric_raises_type_error(self):
        with self.assertRaises(TypeError):
            self.engine.evaluate_strategy(make_metrics(live_information_ratio="1.2"))

    def test_empty_strategy_id_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_strategy(make_metrics(strategy_id="   "))


class TestDriftNotMeasurable(unittest.TestCase):
    """
    Regression: the old build returned drift = 0.0 whenever the backtested
    return was non-positive. A strategy that backtested at -5% and delivered
    -50% live scored zero breaches and ACTIVE_HEALTHY on a fabricated 0.0.
    """

    def setUp(self):
        self.engine = StrategyLifecycleRetirementEngine()

    def test_negative_backtest_return_skips_the_criterion_and_says_so(self):
        report = self.engine.evaluate_strategy(
            make_metrics(
                backtest_annual_return_pct=-5.0,
                live_realized_annual_return_pct=-50.0,
            )
        )
        self.assertIsNone(report.performance_drift_pct)
        self.assertEqual(len(report.skipped_criteria), 1)
        self.assertIn("PERFORMANCE_DRIFT", report.skipped_criteria[0])
        self.assertEqual(report.evaluated_criteria_count, 3)
        self.assertIn("NOT EVALUATED", report.audit_notes)
        # The gap is always available even when the ratio is not.
        self.assertAlmostEqual(report.return_gap_pct_points, -45.0, places=6)

    def test_zero_backtest_return_skips_the_criterion(self):
        report = self.engine.evaluate_strategy(
            make_metrics(backtest_annual_return_pct=0.0)
        )
        self.assertIsNone(report.performance_drift_pct)
        self.assertEqual(report.evaluated_criteria_count, 3)

    def test_unevaluated_criterion_blocks_the_active_healthy_label(self):
        """
        A clean-looking payload whose drift criterion never ran must not be
        certified ACTIVE_HEALTHY -- a dashboard or agent reading only
        ``decision`` would be told all four guardrails passed on the strength
        of three. Here live trails backtest by 37 percentage points.
        """
        report = self.engine.evaluate_strategy(
            make_metrics(
                backtest_annual_return_pct=-3.0,
                live_realized_annual_return_pct=-40.0,
            )
        )
        self.assertEqual(report.breached_criteria, [])
        self.assertEqual(report.decision, RetirementDecision.NEEDS_REVIEW)
        self.assertFalse(report.is_retired)
        self.assertIn("could not be evaluated", report.recommended_action)
        self.assertAlmostEqual(report.return_gap_pct_points, -37.0, places=6)

    def test_near_zero_backtest_return_does_not_manufacture_a_breach(self):
        """
        backtest 0.1% vs live 0.05% is a 5 bp miss but a -50% ratio. The old
        build flagged it NEEDS_REVIEW; the ratio is meaningless at that scale.
        """
        report = self.engine.evaluate_strategy(
            make_metrics(
                backtest_annual_return_pct=0.1,
                live_realized_annual_return_pct=0.05,
            )
        )
        self.assertIsNone(report.performance_drift_pct)
        self.assertEqual(report.breached_criteria, [])
        self.assertIn("numerically meaningless", report.skipped_criteria[0])
        self.assertAlmostEqual(report.return_gap_pct_points, -0.05, places=6)

    def test_backtest_return_at_the_floor_is_still_evaluated(self):
        """Exactly 1.0% is not below the 1.0% floor. live 0.5 -> -50% drift."""
        report = self.engine.evaluate_strategy(
            make_metrics(
                backtest_annual_return_pct=1.0,
                live_realized_annual_return_pct=0.5,
            )
        )
        self.assertAlmostEqual(report.performance_drift_pct, -50.0, places=6)
        self.assertEqual(report.evaluated_criteria_count, 4)
        self.assertEqual(report.decision, RetirementDecision.NEEDS_REVIEW)

    def test_skipped_criterion_cannot_reach_mandatory_retirement_alone(self):
        """
        With drift unevaluable, three breaches must come from the other three
        criteria -- the skipped one is never counted either way.
        """
        report = self.engine.evaluate_strategy(
            make_metrics(
                backtest_annual_return_pct=-5.0,
                live_information_ratio=-0.4,
                live_ic_t_stat=0.1,
                live_max_drawdown_pct=20.0,
            )
        )
        self.assertEqual(report.evaluated_criteria_count, 3)
        self.assertEqual(len(report.breached_criteria), 3)
        self.assertEqual(report.decision, RetirementDecision.MANDATORY_RETIREMENT)


class TestSampleSizeGate(unittest.TestCase):
    """A strategy cannot be retired on evidence that does not exist yet."""

    def test_gate_is_off_by_default(self):
        engine = StrategyLifecycleRetirementEngine()
        report = engine.evaluate_strategy(make_metrics(live_observation_count=3))
        self.assertEqual(report.decision, RetirementDecision.ACTIVE_HEALTHY)

    def test_short_track_record_blocks_retirement(self):
        engine = StrategyLifecycleRetirementEngine(min_live_observations=126)
        report = engine.evaluate_strategy(
            make_metrics(
                live_observation_count=15,
                live_information_ratio=-0.5,
                live_ic_t_stat=0.1,
                live_max_drawdown_pct=25.0,
                live_realized_annual_return_pct=-30.0,
            )
        )
        self.assertEqual(report.decision, RetirementDecision.INSUFFICIENT_LIVE_HISTORY)
        self.assertFalse(report.is_retired)
        # Breaches are still reported, for information only.
        self.assertEqual(len(report.breached_criteria), 4)
        self.assertIn("EXTEND_OBSERVATION", report.recommended_action)
        self.assertIsNone(report.escalation_reason)

    def test_sufficient_track_record_runs_the_ladder(self):
        engine = StrategyLifecycleRetirementEngine(min_live_observations=126)
        report = engine.evaluate_strategy(
            make_metrics(
                live_observation_count=126,
                live_information_ratio=-0.5,
                live_ic_t_stat=0.1,
                live_max_drawdown_pct=25.0,
                live_realized_annual_return_pct=-30.0,
            )
        )
        self.assertEqual(report.decision, RetirementDecision.MANDATORY_RETIREMENT)
        self.assertTrue(report.is_retired)

    def test_gate_does_not_fire_when_count_is_not_supplied(self):
        """
        Documented limitation: an unsupplied count cannot be checked, so the
        ladder runs. The report must not imply the gate was satisfied.
        """
        engine = StrategyLifecycleRetirementEngine(min_live_observations=126)
        report = engine.evaluate_strategy(make_metrics(live_observation_count=None))
        self.assertEqual(report.decision, RetirementDecision.ACTIVE_HEALTHY)

    def test_bad_observation_count_raises(self):
        engine = StrategyLifecycleRetirementEngine()
        with self.assertRaises(ValueError):
            engine.evaluate_strategy(make_metrics(live_observation_count=-1))
        with self.assertRaises(ValueError):
            engine.evaluate_strategy(make_metrics(live_observation_count=12.5))


class TestConstructorValidation(unittest.TestCase):
    """
    Regression: a negative drawdown multiplier used to invert the drawdown gate
    silently (allowed DD of -50% flagged every strategy, including healthy ones).
    """

    def test_non_positive_drawdown_multiplier_raises(self):
        for bad in (0.0, -5.0):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    StrategyLifecycleRetirementEngine(max_drawdown_multiplier=bad)

    def test_non_finite_thresholds_raise(self):
        with self.assertRaises(ValueError):
            StrategyLifecycleRetirementEngine(
                min_live_information_ratio=float("nan")
            )
        with self.assertRaises(ValueError):
            StrategyLifecycleRetirementEngine(min_ic_t_stat=float("inf"))

    def test_breach_count_out_of_range_raises(self):
        for bad in (0, 5, 2.5):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    StrategyLifecycleRetirementEngine(
                        mandatory_retirement_breach_count=bad
                    )

    def test_non_positive_drift_floor_raises(self):
        with self.assertRaises(ValueError):
            StrategyLifecycleRetirementEngine(min_backtest_return_for_drift_pct=0.0)

    def test_bad_min_live_observations_raises(self):
        for bad in (0, -3, 2.5):
            with self.subTest(value=bad):
                with self.assertRaises(ValueError):
                    StrategyLifecycleRetirementEngine(min_live_observations=bad)

    def test_breach_count_of_four_leaves_only_the_override_when_drift_is_skipped(self):
        """
        Configuration foot-gun, documented in references/standards.md: with the
        ladder set to 4 and only three criteria evaluable, no breach count can
        reach it. The escalation override must still be able to retire.
        """
        engine = StrategyLifecycleRetirementEngine(mandatory_retirement_breach_count=4)
        report = engine.evaluate_strategy(
            make_metrics(
                backtest_annual_return_pct=-2.0,
                live_information_ratio=-0.9,
                live_ic_t_stat=0.1,
                live_max_drawdown_pct=30.0,
            )
        )
        self.assertEqual(report.evaluated_criteria_count, 3)
        self.assertEqual(len(report.breached_criteria), 3)
        self.assertEqual(report.decision, RetirementDecision.MANDATORY_RETIREMENT)
        self.assertIsNotNone(report.escalation_reason)

    def test_breach_count_of_two_retires_earlier(self):
        engine = StrategyLifecycleRetirementEngine(mandatory_retirement_breach_count=2)
        report = engine.evaluate_strategy(
            make_metrics(live_ic_t_stat=1.0, live_max_drawdown_pct=16.0)
        )
        self.assertEqual(report.decision, RetirementDecision.MANDATORY_RETIREMENT)
        # Reached by the ladder, not the override, so no escalation note.
        self.assertIsNone(report.escalation_reason)


if __name__ == "__main__":
    unittest.main()

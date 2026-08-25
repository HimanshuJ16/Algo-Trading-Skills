"""
Tests for execution-algo-parameter-optimization-via-backtest.

Expected values for the impact model are taken from the published Table 3 of
Almgren, Thum, Hauptmann & Li (2005), "Direct Estimation of Equity Market Impact" --
not recomputed from this module's own code -- so the tests fail if the coefficients,
the exponent, or the functional form drift.

Expected values for the shortfall arithmetic are hand-derived on paths where impact
is switched off, so the Perold (1988) decomposition is checked in isolation from the
impact model.
"""
import logging
import math
import unittest

from execution_algo_parameter_optimization_via_backtest import (
    ATHL_TEMPORARY_EXPONENT,
    AlgoOptimizationAuditReport,
    AlgoParameterConfig,
    ExecutionAlgoOptimizerEngine,
    HistoricalTradeSample,
    ImpactModelCoefficients,
    almgren_chriss_kappa_t,
    athl_permanent_impact_fraction,
    athl_temporary_impact_fraction,
    _ac_inventory_fraction,
)

# Repo convention: silence the module logger so the suite output stays readable.
logging.getLogger("execution_algo_parameter_optimization_via_backtest").setLevel(logging.CRITICAL)

#: Impact switched off, so shortfall arithmetic can be checked exactly.
NO_IMPACT = ImpactModelCoefficients(permanent_gamma=0.0, temporary_eta=0.0)


def frictionless_engine(**kwargs) -> ExecutionAlgoOptimizerEngine:
    kwargs.setdefault("impact_coefficients", NO_IMPACT)
    return ExecutionAlgoOptimizerEngine(**kwargs)


class TestATHLImpactModel(unittest.TestCase):
    """Reproduces the published Table 3 of Almgren, Thum, Hauptmann & Li (2005)."""

    def test_permanent_impact_matches_published_table_3(self):
        # IBM: sigma = 1.57%, X/V = 0.1, Theta/V = 263 -> paper prints 20 bp.
        ibm_bp = athl_permanent_impact_fraction(0.0157, 100_000, 1_000_000, 263_000_000) * 1e4
        self.assertAlmostEqual(ibm_bp, 20.0, delta=0.5)
        # DRI: sigma = 2.26%, X/V = 0.1, Theta/V = 87 -> paper prints 22 bp.
        dri_bp = athl_permanent_impact_fraction(0.0226, 100_000, 1_000_000, 87_000_000) * 1e4
        self.assertAlmostEqual(dri_bp, 22.0, delta=0.5)

    def test_temporary_impact_matches_published_table_3(self):
        # Paper prints IBM 22 / 15 / 8 bp and DRI 32 / 21 / 12 bp at T = 0.1 / 0.2 / 0.5
        # days for X/V = 0.1, i.e. participation rates 1.0 / 0.5 / 0.2.
        expectations = [
            # participation, IBM bp (sigma 1.57%), DRI bp (sigma 2.26%)
            (1.0, 22.0, 32.0),
            (0.5, 15.0, 21.0),
            (0.2, 8.0, 12.0),
        ]
        for participation, ibm_bp, dri_bp in expectations:
            with self.subTest(participation=participation):
                self.assertAlmostEqual(
                    athl_temporary_impact_fraction(0.0157, participation) * 1e4, ibm_bp, delta=0.5)
                self.assertAlmostEqual(
                    athl_temporary_impact_fraction(0.0226, participation) * 1e4, dri_bp, delta=0.5)

    def test_realised_cost_is_half_permanent_plus_temporary(self):
        # Paper prints J = 32 bp for IBM at T = 0.1 days.
        permanent = athl_permanent_impact_fraction(0.0157, 100_000, 1_000_000, 263_000_000)
        temporary = athl_temporary_impact_fraction(0.0157, 1.0)
        self.assertAlmostEqual((permanent / 2.0 + temporary) * 1e4, 32.0, delta=0.5)

    def test_exponent_is_three_fifths_not_square_root(self):
        # ATHL reject beta = 1/2 at the 95% level in favour of 3/5. A square-root
        # model would give 0.5 here; the fitted model gives 2^-0.6.
        self.assertAlmostEqual(ATHL_TEMPORARY_EXPONENT, 0.600, places=3)
        ratio = athl_temporary_impact_fraction(0.02, 0.5) / athl_temporary_impact_fraction(0.02, 1.0)
        self.assertAlmostEqual(ratio, 0.5 ** 0.600, places=6)
        self.assertNotAlmostEqual(ratio, math.sqrt(0.5), places=3)

    def test_zero_participation_costs_nothing(self):
        self.assertEqual(athl_temporary_impact_fraction(0.02, 0.0), 0.0)

    def test_negative_participation_rejected(self):
        with self.assertRaises(ValueError):
            athl_temporary_impact_fraction(0.02, -0.1)

    def test_coefficient_validation(self):
        with self.assertRaises(ValueError):
            ImpactModelCoefficients(temporary_eta=float("nan"))
        with self.assertRaises(ValueError):
            ImpactModelCoefficients(permanent_gamma=-0.1)
        with self.assertRaises(ValueError):
            ImpactModelCoefficients(temporary_beta=0.0)


class TestAlmgrenChrissTrajectory(unittest.TestCase):
    """Almgren & Chriss (2000) Eq. (17): x_j/X = sinh(k(T-t))/sinh(kT)."""

    def test_risk_neutral_limit_is_linear(self):
        for u in (0.0, 0.25, 0.5, 0.75, 1.0):
            self.assertAlmostEqual(_ac_inventory_fraction(0.0, u), u, places=12)

    def test_matches_direct_sinh_evaluation(self):
        # kappa*T = 2, halfway through: sinh(1)/sinh(2).
        expected = math.sinh(1.0) / math.sinh(2.0)
        self.assertAlmostEqual(_ac_inventory_fraction(2.0, 0.5), expected, places=12)
        self.assertAlmostEqual(expected, 0.3240271368, places=9)

    def test_boundary_conditions_x0_equals_X_and_xN_equals_zero(self):
        for kappa_t in (0.0, 0.5, 2.0, 50.0, 1e4):
            self.assertAlmostEqual(_ac_inventory_fraction(kappa_t, 1.0), 1.0, places=12)
            self.assertAlmostEqual(_ac_inventory_fraction(kappa_t, 0.0), 0.0, places=12)

    def test_large_kappa_does_not_overflow(self):
        # sinh(1e4) overflows a float; the expm1 formulation must not.
        value = _ac_inventory_fraction(1e4, 0.5)
        self.assertTrue(math.isfinite(value))
        self.assertGreaterEqual(value, 0.0)
        self.assertLess(value, 1e-6)

    def test_trajectory_is_monotone_and_front_loaded_with_urgency(self):
        # Higher urgency leaves strictly less inventory outstanding at every interior point.
        for u in (0.25, 0.5, 0.75):
            self.assertLess(_ac_inventory_fraction(5.0, u), _ac_inventory_fraction(1.0, u))
            self.assertLess(_ac_inventory_fraction(1.0, u), _ac_inventory_fraction(0.0, u))

    def test_kappa_increases_with_risk_aversion(self):
        sample = HistoricalTradeSample(
            "T1", "AAPL", 10_000, 185.0, 1_000_000.0, 0.015, [185.0] * 4)
        low = almgren_chriss_kappa_t(
            AlgoParameterConfig(0.10, 1e-5, 0), sample)
        high = almgren_chriss_kappa_t(
            AlgoParameterConfig(0.10, 1e-4, 0), sample)
        self.assertGreater(high, low)
        self.assertTrue(math.isfinite(high))
        # AC Eq. (19): kappa scales with sqrt(lambda), so a 10x lambda is a sqrt(10)x kappa.
        self.assertAlmostEqual(high / low, math.sqrt(10.0), places=6)

    def test_risk_neutral_lambda_gives_zero_kappa(self):
        sample = HistoricalTradeSample("T1", "AAPL", 10_000, 185.0, 1_000_000.0, 0.015, [185.0] * 4)
        self.assertEqual(almgren_chriss_kappa_t(AlgoParameterConfig(0.10, 0.0, 0), sample), 0.0)
        flat = HistoricalTradeSample("T2", "AAPL", 10_000, 185.0, 1_000_000.0, 0.0, [185.0] * 4)
        self.assertEqual(almgren_chriss_kappa_t(AlgoParameterConfig(0.10, 1e-4, 0), flat), 0.0)


class TestImplementationShortfall(unittest.TestCase):
    """Perold (1988) decomposition, checked with impact switched off."""

    def test_flat_path_full_fill_costs_exactly_the_peg_concession(self):
        engine = frictionless_engine()
        sample = HistoricalTradeSample(
            "T1", "XYZ", 1_000, 100.0, 1_000_000.0, 0.02, [100.0, 100.0], tick_size=0.01)
        result = engine.simulate_single_execution(AlgoParameterConfig(0.25, 0.0, 1), sample)
        self.assertAlmostEqual(result.fill_completion_rate, 1.0, places=12)
        # One tick of 0.01 on a 100.00 arrival price is exactly 1 bp.
        self.assertAlmostEqual(result.implementation_shortfall_bps, 1.0, places=9)

    def test_mid_peg_on_a_flat_path_is_costless(self):
        engine = frictionless_engine()
        sample = HistoricalTradeSample("T1", "XYZ", 1_000, 100.0, 1_000_000.0, 0.02, [100.0, 100.0])
        result = engine.simulate_single_execution(AlgoParameterConfig(0.25, 0.0, 0), sample)
        self.assertAlmostEqual(result.implementation_shortfall_bps, 0.0, places=12)

    def test_unfilled_quantity_is_charged_opportunity_cost(self):
        # Half the order fills at the arrival price; the market then runs 10% away.
        # Perold: IS = 0.5 * 0 bp + 0.5 * 1000 bp = 500 bp.
        engine = frictionless_engine()
        sample = HistoricalTradeSample(
            "T1", "XYZ", 1_000, 100.0, 1_000_000.0, 0.02, [100.0, 110.0],
            interval_volumes=[500.0, 0.0])
        result = engine.simulate_single_execution(AlgoParameterConfig(1.0, 0.0, 0), sample)
        self.assertAlmostEqual(result.fill_completion_rate, 0.5, places=12)
        self.assertAlmostEqual(result.execution_cost_bps, 0.0, places=9)
        self.assertAlmostEqual(result.opportunity_cost_bps, 1000.0, places=9)
        self.assertAlmostEqual(result.implementation_shortfall_bps, 500.0, places=9)

    def test_sell_side_is_the_mirror_image_of_buy_side(self):
        engine = frictionless_engine()
        buy = HistoricalTradeSample(
            "B", "XYZ", 1_000, 100.0, 1_000_000.0, 0.02, [100.0, 110.0],
            side="BUY", interval_volumes=[500.0, 0.0])
        sell = HistoricalTradeSample(
            "S", "XYZ", 1_000, 100.0, 1_000_000.0, 0.02, [100.0, 90.0],
            side="SELL", interval_volumes=[500.0, 0.0])
        config = AlgoParameterConfig(1.0, 0.0, 0)
        self.assertAlmostEqual(
            engine.simulate_single_execution(config, buy).implementation_shortfall_bps,
            engine.simulate_single_execution(config, sell).implementation_shortfall_bps,
            places=9)

    def test_a_favourable_path_produces_negative_shortfall(self):
        engine = frictionless_engine()
        sample = HistoricalTradeSample(
            "T1", "XYZ", 1_000, 100.0, 1_000_000.0, 0.02, [99.0, 99.0])
        result = engine.simulate_single_execution(AlgoParameterConfig(0.25, 0.0, 0), sample)
        self.assertAlmostEqual(result.implementation_shortfall_bps, -100.0, places=9)

    def test_fill_is_capped_by_observed_interval_volume(self):
        # 5% of 50,000 shares per interval over two intervals can absorb 5,000 of a
        # 10,000-share order and no more, whatever the schedule wants.
        engine = frictionless_engine()
        sample = HistoricalTradeSample(
            "T1", "XYZ", 10_000, 100.0, 100_000.0, 0.02, [100.0, 100.0])
        result = engine.simulate_single_execution(AlgoParameterConfig(0.05, 0.0, 0), sample)
        self.assertAlmostEqual(result.filled_qty, 5_000.0, places=9)
        self.assertAlmostEqual(result.fill_completion_rate, 0.5, places=12)

    def test_permanent_impact_only_applied_when_shares_outstanding_supplied(self):
        engine = ExecutionAlgoOptimizerEngine()
        without = HistoricalTradeSample("A", "XYZ", 1_000, 100.0, 1_000_000.0, 0.02, [100.0])
        with_theta = HistoricalTradeSample(
            "B", "XYZ", 1_000, 100.0, 1_000_000.0, 0.02, [100.0], shares_outstanding=2.6e8)
        r_without = engine.simulate_single_execution(AlgoParameterConfig(0.25, 0.0, 0), without)
        r_with = engine.simulate_single_execution(AlgoParameterConfig(0.25, 0.0, 0), with_theta)
        self.assertFalse(r_without.permanent_impact_applied)
        self.assertTrue(r_with.permanent_impact_applied)
        self.assertGreater(r_with.implementation_shortfall_bps, r_without.implementation_shortfall_bps)


class TestHistoricalDataActuallyDrivesTheResult(unittest.TestCase):
    """
    Regression guard. An earlier implementation computed shortfall from the parameter
    config alone and never read arrival_price or the price path, so every one of these
    assertions failed: the "backtest" was insensitive to its own historical inputs.
    """

    def setUp(self):
        self.engine = frictionless_engine()
        self.config = AlgoParameterConfig(1.0, 0.0, 0)

    def _is_bps(self, prices, arrival=100.0, volumes=None):
        sample = HistoricalTradeSample(
            "T", "XYZ", 1_000, arrival, 1_000_000.0, 0.02, prices, interval_volumes=volumes)
        return self.engine.simulate_single_execution(self.config, sample).implementation_shortfall_bps

    def test_price_path_changes_the_shortfall(self):
        rising = self._is_bps([100.0, 101.0, 102.0])
        flat = self._is_bps([100.0, 100.0, 100.0])
        falling = self._is_bps([100.0, 99.0, 98.0])
        self.assertGreater(rising, flat)
        self.assertGreater(flat, falling)

    def test_arrival_price_changes_the_shortfall(self):
        self.assertNotAlmostEqual(
            self._is_bps([100.0, 100.0], arrival=100.0),
            self._is_bps([100.0, 100.0], arrival=99.0),
            places=6)

    def test_two_samples_differing_only_in_path_score_differently(self):
        quiet = HistoricalTradeSample("Q", "XYZ", 1_000, 100.0, 1_000_000.0, 0.02, [100.0, 100.0])
        volatile = HistoricalTradeSample("V", "XYZ", 1_000, 100.0, 1_000_000.0, 0.02, [100.0, 105.0])
        a = self.engine.simulate_single_execution(self.config, quiet)
        b = self.engine.simulate_single_execution(self.config, volatile)
        self.assertNotAlmostEqual(
            a.implementation_shortfall_bps, b.implementation_shortfall_bps, places=6)


class TestUrgencyTradeOff(unittest.TestCase):
    """Higher risk aversion front-loads the schedule: better fills, more impact."""

    def _sample(self):
        # Liquidity collapses after the first interval, so only a front-loaded
        # schedule can complete the order.
        return HistoricalTradeSample(
            "T1", "XYZ", 1_000, 100.0, 1_000_000.0, 0.02, [100.0, 100.0, 100.0],
            interval_volumes=[1_000.0, 100.0, 100.0])

    def test_urgency_raises_fill_completion(self):
        engine = ExecutionAlgoOptimizerEngine()
        patient = engine.simulate_single_execution(
            AlgoParameterConfig(1.0, 0.0, 0), self._sample())
        urgent = engine.simulate_single_execution(
            AlgoParameterConfig(1.0, 1e-2, 0), self._sample())
        self.assertGreater(urgent.kappa_t, patient.kappa_t)
        self.assertGreater(urgent.fill_completion_rate, patient.fill_completion_rate)
        self.assertAlmostEqual(urgent.fill_completion_rate, 1.0, places=9)

    def test_urgency_costs_more_impact_on_an_unconstrained_path(self):
        # Same order, ample liquidity throughout: the only difference urgency makes is
        # concentrating the trade rate, which the ATHL temporary term charges for.
        engine = ExecutionAlgoOptimizerEngine()
        sample = lambda: HistoricalTradeSample(  # noqa: E731 - terse fixture
            "T1", "XYZ", 10_000, 100.0, 1_000_000.0, 0.02, [100.0] * 4)
        patient = engine.simulate_single_execution(AlgoParameterConfig(1.0, 0.0, 0), sample())
        urgent = engine.simulate_single_execution(AlgoParameterConfig(1.0, 1.0, 0), sample())
        self.assertAlmostEqual(patient.fill_completion_rate, 1.0, places=9)
        self.assertAlmostEqual(urgent.fill_completion_rate, 1.0, places=9)
        self.assertGreater(
            urgent.implementation_shortfall_bps, patient.implementation_shortfall_bps)


class TestGridSearchOptimization(unittest.TestCase):

    def setUp(self):
        self.engine = ExecutionAlgoOptimizerEngine(
            is_volatility_penalty_weight=0.5, incomplete_fill_penalty_weight=50.0)
        self.grid = [
            AlgoParameterConfig(0.05, 1e-5, 0),
            AlgoParameterConfig(0.15, 1e-5, 1),
            AlgoParameterConfig(0.25, 1e-4, 2),
        ]
        self.samples = [
            HistoricalTradeSample("TR_01", "AAPL", 10_000, 185.00, 1_000_000.0, 0.015,
                                  [185.00, 185.10, 185.05, 185.20], shares_outstanding=1.5e10),
            HistoricalTradeSample("TR_02", "AAPL", 20_000, 185.20, 1_000_000.0, 0.018,
                                  [185.20, 185.35, 185.15, 185.40], shares_outstanding=1.5e10),
            HistoricalTradeSample("TR_03", "AAPL", 15_000, 184.90, 1_000_000.0, 0.014,
                                  [184.90, 185.05, 184.95, 185.10], shares_outstanding=1.5e10),
        ]

    def test_report_shape_and_selection(self):
        report = self.engine.optimize_algo_parameters(
            "IS_ALMGREN_CHRISS", "AAPL", self.grid, self.samples)
        self.assertIsInstance(report, AlgoOptimizationAuditReport)
        self.assertEqual(report.total_grid_candidates_evaluated, 3)
        self.assertEqual(report.total_trade_samples_tested, 3)
        self.assertEqual(len(report.all_candidate_results), 3)
        # The winner really is the minimum, not merely the head of a sorted list.
        self.assertEqual(
            report.optimal_utility_score,
            min(r.utility_score for r in report.all_candidate_results))
        self.assertEqual(report.optimal_config, report.all_candidate_results[0].config)

    def test_every_candidate_records_per_sample_diagnostics(self):
        report = self.engine.optimize_algo_parameters("A", "AAPL", self.grid, self.samples)
        for result in report.all_candidate_results:
            self.assertEqual(result.samples_evaluated, 3)
            self.assertGreaterEqual(
                result.worst_implementation_shortfall_bps,
                result.mean_implementation_shortfall_bps)
            self.assertLessEqual(
                result.min_fill_completion_rate, result.avg_fill_completion_rate)

    def test_ties_resolve_to_the_first_candidate_in_grid_order(self):
        # Two configs that are identical in every respect that can affect the score.
        duplicate = [AlgoParameterConfig(0.10, 1e-5, 0), AlgoParameterConfig(0.10, 1e-5, 0)]
        first = self.engine.optimize_algo_parameters("A", "AAPL", duplicate, self.samples)
        second = self.engine.optimize_algo_parameters("A", "AAPL", duplicate, self.samples)
        self.assertEqual(first.optimal_config, second.optimal_config)
        self.assertEqual(first.optimal_utility_score, second.optimal_utility_score)

    def test_result_is_deterministic_across_runs(self):
        a = self.engine.optimize_algo_parameters("A", "AAPL", self.grid, self.samples)
        b = self.engine.optimize_algo_parameters("A", "AAPL", self.grid, self.samples)
        self.assertEqual(a.optimal_config, b.optimal_config)
        self.assertEqual(
            [r.utility_score for r in a.all_candidate_results],
            [r.utility_score for r in b.all_candidate_results])

    def test_incomplete_fill_penalty_shifts_the_winner_towards_participation(self):
        # Thin liquidity: a 5% ceiling cannot finish the order. With no fill penalty
        # the cheapest-impact candidate wins; with a large one it cannot.
        thin = [
            HistoricalTradeSample("T", "XYZ", 100_000, 100.0, 200_000.0, 0.02,
                                  [100.0, 100.0, 100.0, 100.0])
        ]
        grid = [AlgoParameterConfig(0.05, 1e-5, 0), AlgoParameterConfig(0.25, 1e-5, 0)]
        lenient = ExecutionAlgoOptimizerEngine(incomplete_fill_penalty_weight=0.0)
        strict = ExecutionAlgoOptimizerEngine(incomplete_fill_penalty_weight=10_000.0)
        self.assertEqual(
            lenient.optimize_algo_parameters("A", "XYZ", grid, thin).optimal_config
            .max_participation_rate, 0.05)
        self.assertEqual(
            strict.optimize_algo_parameters("A", "XYZ", grid, thin).optimal_config
            .max_participation_rate, 0.25)


class TestParticipationCeilingGuard(unittest.TestCase):

    def setUp(self):
        self.samples = [
            HistoricalTradeSample("T", "XYZ", 1_000, 100.0, 1_000_000.0, 0.02, [100.0, 100.0])
        ]

    def test_candidate_above_the_limit_is_excluded_and_recorded(self):
        engine = ExecutionAlgoOptimizerEngine(max_allowed_participation_rate=0.25)
        grid = [AlgoParameterConfig(0.10, 1e-5, 0), AlgoParameterConfig(0.40, 1e-5, 0)]
        report = engine.optimize_algo_parameters("A", "XYZ", grid, self.samples)
        self.assertEqual(report.total_grid_candidates_evaluated, 1)
        self.assertEqual(len(report.rejected_configs), 1)
        self.assertEqual(report.rejected_configs[0][0].max_participation_rate, 0.40)
        self.assertIn("participation", report.rejected_configs[0][1])
        self.assertNotEqual(report.optimal_config.max_participation_rate, 0.40)

    def test_exactly_at_the_limit_is_allowed(self):
        engine = ExecutionAlgoOptimizerEngine(max_allowed_participation_rate=0.25)
        report = engine.optimize_algo_parameters(
            "A", "XYZ", [AlgoParameterConfig(0.25, 1e-5, 0)], self.samples)
        self.assertEqual(report.total_grid_candidates_evaluated, 1)
        self.assertEqual(report.rejected_configs, [])

    def test_an_entirely_ineligible_grid_raises_rather_than_guessing(self):
        engine = ExecutionAlgoOptimizerEngine(max_allowed_participation_rate=0.10)
        grid = [AlgoParameterConfig(0.40, 1e-5, 0), AlgoParameterConfig(0.50, 1e-5, 0)]
        with self.assertRaises(ValueError) as ctx:
            engine.optimize_algo_parameters("A", "XYZ", grid, self.samples)
        self.assertIn("participation limit", str(ctx.exception))

    def test_limit_is_configurable_for_desks_with_a_different_policy(self):
        engine = ExecutionAlgoOptimizerEngine(max_allowed_participation_rate=0.50)
        report = engine.optimize_algo_parameters(
            "A", "XYZ", [AlgoParameterConfig(0.40, 1e-5, 0)], self.samples)
        self.assertEqual(report.rejected_configs, [])


class TestOutOfSampleValidation(unittest.TestCase):

    def setUp(self):
        self.engine = ExecutionAlgoOptimizerEngine(overfit_degradation_threshold_bps=5.0)
        self.grid = [AlgoParameterConfig(0.10, 1e-5, 0), AlgoParameterConfig(0.20, 1e-5, 0)]

    def _samples(self, terminal_price):
        return [HistoricalTradeSample(
            f"T{terminal_price}", "XYZ", 5_000, 100.0, 1_000_000.0, 0.02,
            [100.0, terminal_price], shares_outstanding=1e9)]

    def test_missing_holdout_is_flagged_not_silently_accepted(self):
        report = self.engine.optimize_algo_parameters(
            "A", "XYZ", self.grid, self._samples(100.0))
        self.assertFalse(report.holdout_evaluated)
        self.assertIsNone(report.holdout_mean_is_bps)
        self.assertTrue(any("NO HOLDOUT SUPPLIED" in w for w in report.warnings))

    def test_holdout_is_evaluated_and_degradation_reported(self):
        report = self.engine.optimize_algo_parameters(
            "A", "XYZ", self.grid, self._samples(100.0),
            holdout_samples=self._samples(101.0))
        self.assertTrue(report.holdout_evaluated)
        self.assertIsNotNone(report.holdout_mean_is_bps)
        self.assertIsNotNone(report.holdout_fill_completion_rate)
        self.assertAlmostEqual(
            report.holdout_is_degradation_bps,
            report.holdout_mean_is_bps - report.optimal_mean_is_bps, places=4)
        self.assertFalse(any("NO HOLDOUT SUPPLIED" in w for w in report.warnings))

    def test_material_degradation_raises_an_overfitting_warning(self):
        report = self.engine.optimize_algo_parameters(
            "A", "XYZ", self.grid, self._samples(100.0),
            holdout_samples=self._samples(110.0))
        self.assertGreater(report.holdout_is_degradation_bps, 5.0)
        self.assertTrue(any("OUT-OF-SAMPLE DEGRADATION" in w for w in report.warnings))

    def test_missing_shares_outstanding_warns_that_cost_is_understated(self):
        samples = [HistoricalTradeSample("T", "XYZ", 5_000, 100.0, 1e6, 0.02, [100.0, 100.0])]
        report = self.engine.optimize_algo_parameters("A", "XYZ", self.grid, samples)
        self.assertTrue(any("PERMANENT IMPACT OMITTED" in w for w in report.warnings))


class TestSelectionSeparation(unittest.TestCase):
    """
    A grid ranking is only a result if the winner is distinguishable from the runner-up.
    Implementation Shortfall across real paths has a standard deviation in the tens of
    basis points, so on a small sample the ranking is frequently pure sampling noise.
    """

    def _samples(self, n, spread):
        # n must be a multiple of 8: the same 8-point terminal-price distribution is
        # repeated, so the population dispersion is held fixed while n grows and the
        # standard error is free to shrink as 1/sqrt(n).
        assert n % 8 == 0, "sample count must be a multiple of 8 to hold dispersion fixed"
        return [
            HistoricalTradeSample(
                f"T{i}", "XYZ", 1_000, 100.0, 1_000_000.0, 0.02,
                [100.0, 100.0 + spread * ((i % 8) - 3.5)], shares_outstanding=1e9)
            for i in range(n)
        ]

    def test_standard_error_is_reported_and_shrinks_with_sample_size(self):
        engine = ExecutionAlgoOptimizerEngine()
        config = AlgoParameterConfig(0.10, 1e-5, 0)
        small = engine.evaluate_candidate(config, self._samples(8, 1.0))
        large = engine.evaluate_candidate(config, self._samples(64, 1.0))
        for result in (small, large):
            self.assertAlmostEqual(
                result.mean_is_standard_error_bps,
                result.std_implementation_shortfall_bps / math.sqrt(result.samples_evaluated),
                places=3)
        # Same 8-point distribution, 8x the samples. The naive expectation is sqrt(8),
        # but statistics.stdev applies Bessel's correction, so with sum-of-squares SS:
        #   SE(8)  = sqrt(SS/7)/sqrt(8)        SE(64) = sqrt(8*SS/63)/8
        #   ratio  = sqrt(8) * sqrt(63/56)     = 3 exactly.
        self.assertAlmostEqual(
            small.mean_is_standard_error_bps / large.mean_is_standard_error_bps,
            3.0, places=6)

    def test_single_candidate_grid_has_no_margin_to_report(self):
        engine = ExecutionAlgoOptimizerEngine()
        report = engine.optimize_algo_parameters(
            "A", "XYZ", [AlgoParameterConfig(0.10, 1e-5, 0)], self._samples(8, 1.0))
        self.assertIsNone(report.selection_margin_score)
        self.assertIsNone(report.selection_is_separated)

    def test_a_ranking_inside_the_noise_is_flagged_not_presented_as_a_result(self):
        engine = ExecutionAlgoOptimizerEngine()
        # Two near-identical ceilings on widely dispersed paths: whichever wins, it
        # wins by far less than the standard error on either mean.
        grid = [AlgoParameterConfig(0.10, 1e-5, 0), AlgoParameterConfig(0.11, 1e-5, 0)]
        report = engine.optimize_algo_parameters("A", "XYZ", grid, self._samples(8, 5.0))
        self.assertGreater(report.all_candidate_results[0].mean_is_standard_error_bps, 0.0)
        self.assertFalse(report.selection_is_separated)
        self.assertTrue(any("SELECTION NOT SEPARATED" in w for w in report.warnings))
        self.assertIn("INSIDE NOISE", report.audit_notes)

    def test_a_decisive_ranking_is_reported_as_separated(self):
        # A crushing fill penalty against a ceiling that cannot finish the order makes
        # the gap enormous relative to the shortfall noise.
        engine = ExecutionAlgoOptimizerEngine(incomplete_fill_penalty_weight=100_000.0)
        thin = [HistoricalTradeSample("T", "XYZ", 100_000, 100.0, 200_000.0, 0.02,
                                      [100.0, 100.0], shares_outstanding=1e9)]
        grid = [AlgoParameterConfig(0.02, 1e-5, 0), AlgoParameterConfig(0.25, 1e-5, 0)]
        report = engine.optimize_algo_parameters("A", "XYZ", grid, thin)
        self.assertTrue(report.selection_is_separated)
        self.assertFalse(any("SELECTION NOT SEPARATED" in w for w in report.warnings))
        self.assertIn("separated", report.audit_notes)


class TestInputValidation(unittest.TestCase):

    def test_config_rejects_out_of_range_parameters(self):
        for kwargs in (
            dict(max_participation_rate=0.0, risk_aversion_lambda=1e-5, peg_offset_ticks=0),
            dict(max_participation_rate=1.5, risk_aversion_lambda=1e-5, peg_offset_ticks=0),
            dict(max_participation_rate=-0.1, risk_aversion_lambda=1e-5, peg_offset_ticks=0),
            dict(max_participation_rate=float("nan"), risk_aversion_lambda=1e-5, peg_offset_ticks=0),
            dict(max_participation_rate=0.1, risk_aversion_lambda=-1e-5, peg_offset_ticks=0),
            dict(max_participation_rate=0.1, risk_aversion_lambda=float("inf"), peg_offset_ticks=0),
            dict(max_participation_rate=0.1, risk_aversion_lambda=1e-5, peg_offset_ticks=-1),
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    AlgoParameterConfig(**kwargs)

    def test_sample_rejects_degenerate_orders(self):
        base = dict(trade_id="T", symbol="XYZ", order_qty=1_000, arrival_price=100.0,
                    market_adv_shares=1e6, volatility_daily_pct=0.02,
                    historical_execution_prices=[100.0])
        for override in (
            dict(order_qty=0),
            dict(order_qty=-5),
            dict(arrival_price=0.0),
            dict(arrival_price=-1.0),
            dict(arrival_price=float("nan")),
            dict(market_adv_shares=0.0),
            dict(volatility_daily_pct=-0.01),
            dict(volatility_daily_pct=float("inf")),
            dict(historical_execution_prices=[]),
            dict(historical_execution_prices=[100.0, float("nan")]),
            dict(historical_execution_prices=[100.0, -5.0]),
            dict(side="HOLD"),
            dict(execution_horizon_days=0.0),
            dict(tick_size=-0.01),
            dict(shares_outstanding=0.0),
            dict(interval_volumes=[1.0, 2.0]),          # length mismatch
            dict(historical_execution_prices=[100.0, 100.0], interval_volumes=[1.0, -2.0]),
        ):
            with self.subTest(**override):
                with self.assertRaises(ValueError):
                    HistoricalTradeSample(**{**base, **override})

    def test_zero_quantity_order_raises_instead_of_dividing_by_zero(self):
        with self.assertRaises(ValueError):
            HistoricalTradeSample("T", "XYZ", 0, 100.0, 1e6, 0.02, [100.0])

    def test_engine_rejects_invalid_weights(self):
        for kwargs in (
            dict(is_volatility_penalty_weight=-1.0),
            dict(incomplete_fill_penalty_weight=-1.0),
            dict(incomplete_fill_penalty_weight=float("nan")),
            dict(max_allowed_participation_rate=0.0),
            dict(max_allowed_participation_rate=1.5),
            dict(overfit_degradation_threshold_bps=-1.0),
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    ExecutionAlgoOptimizerEngine(**kwargs)

    def test_empty_grid_or_empty_samples_raise(self):
        engine = ExecutionAlgoOptimizerEngine()
        samples = [HistoricalTradeSample("T", "XYZ", 1_000, 100.0, 1e6, 0.02, [100.0])]
        with self.assertRaises(ValueError):
            engine.optimize_algo_parameters("A", "XYZ", [], samples)
        with self.assertRaises(ValueError):
            engine.optimize_algo_parameters("A", "XYZ", [AlgoParameterConfig(0.1, 1e-5, 0)], [])

    def test_side_is_normalised_case_insensitively(self):
        sample = HistoricalTradeSample("T", "XYZ", 1_000, 100.0, 1e6, 0.02, [100.0], side="sell")
        self.assertEqual(sample.side, "SELL")
        self.assertEqual(sample.side_sign, -1)

    def test_default_interval_volumes_split_adv_across_the_horizon(self):
        sample = HistoricalTradeSample(
            "T", "XYZ", 1_000, 100.0, 100_000.0, 0.02, [100.0] * 4, execution_horizon_days=0.5)
        self.assertEqual(len(sample.interval_volumes), 4)
        # 100,000 ADV over half a day, split across four intervals.
        self.assertAlmostEqual(sum(sample.interval_volumes), 50_000.0, places=6)
        self.assertAlmostEqual(sample.interval_volumes[0], 12_500.0, places=6)


if __name__ == "__main__":
    unittest.main()

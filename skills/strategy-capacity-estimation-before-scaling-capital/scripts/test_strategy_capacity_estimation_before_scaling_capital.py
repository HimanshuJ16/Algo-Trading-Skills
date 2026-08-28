"""
Unit tests for strategy-capacity-estimation-before-scaling-capital.

Expected values in the quantitative tests are derived by hand from the stated
inputs, not by re-running the engine's own expressions. The derivation for the
$25M reference point is spelled out in ``TestCapacityQuantitativeCorrectness``.

Tests named ``test_regression_*`` each fail against the pre-audit engine and pass
against the current one.
"""
import math
import unittest

from strategy_capacity_estimation_before_scaling_capital import (
    LIMITING_FACTORS,
    MAX_GRID_POINTS,
    AUMCapacityPoint,
    StrategyCapacityEstimationBeforeScalingCapital,
    StrategyCapacityEstimationBeforeScalingCapitalConfig,
    StrategyCapacityEstimatorEngine,
    StrategyCapacityReport,
    StrategyParameters,
)


def _base_params(**overrides) -> StrategyParameters:
    """Reference strategy: 25% gross, 15% vol, 10% daily turnover against $50M ADV."""
    defaults = dict(
        strategy_id="STAT_ARB_US_LARGE_CAP",
        annual_gross_return_pct=0.25,
        annual_volatility_pct=0.15,
        daily_turnover_pct=0.10,
        avg_daily_volume_usd=50_000_000.0,
        avg_daily_volatility_pct=0.015,
        half_spread_bps=1.0,
        max_participation_rate_pct=5.0,
        min_acceptable_sharpe=1.0,
    )
    defaults.update(overrides)
    return StrategyParameters(**defaults)


class TestStrategyCapacityLegacy(unittest.TestCase):
    def test_execute_true(self):
        config = StrategyCapacityEstimationBeforeScalingCapitalConfig(enabled=True)
        engine = StrategyCapacityEstimationBeforeScalingCapital(config)
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        config = StrategyCapacityEstimationBeforeScalingCapitalConfig(enabled=False)
        engine = StrategyCapacityEstimationBeforeScalingCapital(config)
        self.assertFalse(engine.execute())


class TestCapacityQuantitativeCorrectness(unittest.TestCase):
    """
    Hand-derivation at AUM = $25,000,000 with the reference parameters:

        daily one-way notional Q = 25,000,000 * 0.10          = $2,500,000
        participation           = 2,500,000 / 50,000,000      = 5.00% of ADV
        annual spread cost      = 2,500,000 * 252 * 1e-4      = $63,000
        impact I(Q) = 0.5 * 0.015 * sqrt(0.05)                = 0.001677050983...
        annual impact cost      = 2,500,000 * 252 * I(Q)      = $1,056,542.119...
        gross PnL               = 25,000,000 * 0.25           = $6,250,000
        net PnL                                               = $5,130,457.881...
        net return              = 5,130,457.881 / 25,000,000  = 0.20521831522...
        net Sharpe (rf = 0)     = 0.20521831522 / 0.15        = 1.36812210150...
    """

    def setUp(self):
        self.engine = StrategyCapacityEstimatorEngine(impact_gamma=0.5)
        self.params = _base_params()

    def _point_at(self, report: StrategyCapacityReport, aum: float) -> AUMCapacityPoint:
        for point in report.capacity_curve:
            if math.isclose(point.aum_usd, aum, rel_tol=1e-12):
                return point
        self.fail(f"No grid point at AUM {aum}.")

    def test_reference_point_costs_match_hand_derivation(self):
        report = self.engine.estimate_capacity(self.params, 1_000_000.0, 100_000_000.0)
        point = self._point_at(report, 25_000_000.0)
        self.assertAlmostEqual(point.spread_cost_usd, 63_000.00, places=2)
        self.assertAlmostEqual(point.market_impact_cost_usd, 1_056_542.12, places=2)
        self.assertAlmostEqual(point.net_pnl_usd, 5_130_457.88, places=2)
        self.assertAlmostEqual(point.net_sharpe_ratio_exact, 1.3681221015, places=9)
        self.assertAlmostEqual(point.adv_participation_pct_exact, 5.0, places=12)

    def test_frictionless_sharpe_with_zero_risk_free_rate(self):
        report = self.engine.estimate_capacity(self.params, 1_000_000.0, 100_000_000.0)
        # 0.25 / 0.15 = 1.6667 -> 1.67
        self.assertEqual(report.frictionless_sharpe_ratio, 1.67)

    def test_regression_sharpe_is_an_excess_return_ratio(self):
        """
        Sharpe (1994) is a ratio of *excess* return. The pre-audit engine divided
        the raw return by volatility, so a 4% risk-free rate was silently credited
        to the strategy: (0.25 - 0.04) / 0.15 = 1.40, not 1.67.
        """
        report = self.engine.estimate_capacity(
            _base_params(risk_free_rate_pct=0.04), 1_000_000.0, 100_000_000.0
        )
        self.assertEqual(report.frictionless_sharpe_ratio, 1.40)
        self.assertEqual(report.risk_free_rate_pct, 0.04)
        # And the rate flows into every point on the curve, not just the headline.
        point = self._point_at(report, 25_000_000.0)
        self.assertAlmostEqual(
            point.net_sharpe_ratio_exact, 1.3681221015 - 0.04 / 0.15, places=9
        )

    def test_impact_follows_the_square_root_law(self):
        """Quadrupling traded notional must exactly double impact, not quadruple it."""
        small = self.engine._estimate_daily_market_impact_pct(1_000_000.0, 50_000_000.0, 0.015)
        large = self.engine._estimate_daily_market_impact_pct(4_000_000.0, 50_000_000.0, 0.015)
        self.assertAlmostEqual(large / small, 2.0, places=12)

    def test_impact_is_linear_in_the_calibrated_prefactor(self):
        """Capacity depends on an uncalibrated Y, so the dependence must be explicit."""
        cheap = StrategyCapacityEstimatorEngine(impact_gamma=0.5)
        dear = StrategyCapacityEstimatorEngine(impact_gamma=1.0)
        q, adv, vol = 2_500_000.0, 50_000_000.0, 0.015
        self.assertAlmostEqual(
            dear._estimate_daily_market_impact_pct(q, adv, vol),
            2.0 * cheap._estimate_daily_market_impact_pct(q, adv, vol),
            places=12,
        )

    def test_capacity_scales_as_inverse_square_of_the_impact_prefactor(self):
        """
        Where the Sharpe gate binds, net return is R - spread - c*Y*sqrt(AUM), so the
        breaking AUM goes as Y^-2: doubling Y must cut capacity roughly fourfold.
        This is the documented reason an uncalibrated Y is not a small error.
        """
        params = _base_params(max_participation_rate_pct=1_000.0)
        low = StrategyCapacityEstimatorEngine(0.5).estimate_capacity(params, 1_000_000.0, 300_000_000.0)
        high = StrategyCapacityEstimatorEngine(1.0).estimate_capacity(params, 1_000_000.0, 300_000_000.0)
        self.assertEqual(low.limiting_factor, "MIN_SHARPE_BREACH")
        self.assertEqual(high.limiting_factor, "MIN_SHARPE_BREACH")
        ratio = low.max_capacity_aum_usd / high.max_capacity_aum_usd
        self.assertAlmostEqual(ratio, 4.0, delta=0.1)

    def test_capacity_is_independent_of_the_prefactor_when_participation_binds(self):
        """The calibration only matters where the Sharpe gate is the binding one."""
        params = _base_params()
        low = StrategyCapacityEstimatorEngine(0.5).estimate_capacity(params, 1_000_000.0, 100_000_000.0)
        high = StrategyCapacityEstimatorEngine(1.0).estimate_capacity(params, 1_000_000.0, 100_000_000.0)
        self.assertEqual(low.limiting_factor, "ADV_PARTICIPATION_LIMIT")
        self.assertEqual(high.limiting_factor, "ADV_PARTICIPATION_LIMIT")
        self.assertEqual(low.max_capacity_aum_usd, high.max_capacity_aum_usd)

    def test_sharpe_decays_monotonically_as_aum_scales(self):
        report = self.engine.estimate_capacity(self.params, 1_000_000.0, 20_000_000.0)
        sharpes = [p.net_sharpe_ratio_exact for p in report.capacity_curve]
        self.assertEqual(len(sharpes), 20)
        for earlier, later in zip(sharpes, sharpes[1:]):
            self.assertGreater(earlier, later)


class TestCapacityLimitSemantics(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyCapacityEstimatorEngine(impact_gamma=0.5)

    def test_participation_cap_binds_at_the_exact_boundary(self):
        """
        The 5% cap is inclusive. 10% turnover against $50M ADV puts participation at
        exactly 5.0% when AUM = $25M, so $25M must be feasible and $26M must not.
        """
        report = self.engine.estimate_capacity(_base_params(), 1_000_000.0, 100_000_000.0)
        self.assertEqual(report.max_capacity_aum_usd, 25_000_000.0)
        self.assertEqual(report.limiting_factor, "ADV_PARTICIPATION_LIMIT")
        at_cap = report.capacity_curve[24]
        just_over = report.capacity_curve[25]
        self.assertEqual(at_cap.aum_usd, 25_000_000.0)
        self.assertFalse(at_cap.is_capacity_exceeded)
        self.assertTrue(just_over.is_participation_breached)
        self.assertFalse(just_over.is_sharpe_breached)

    def test_regression_feasible_optimum_never_exceeds_capacity(self):
        """
        The pre-audit engine maximised net dollar PnL over the whole grid, breached
        points included. On these reference parameters net PnL keeps climbing past
        the ADV cap, so it returned the $100M search ceiling as the 'optimal' AUM
        against a true capacity of $25M -- a 4x over-allocation.
        """
        report = self.engine.estimate_capacity(_base_params(), 1_000_000.0, 100_000_000.0)
        self.assertLessEqual(
            report.optimal_sharpe_capacity_aum_usd, report.max_capacity_aum_usd
        )
        self.assertEqual(report.optimal_sharpe_capacity_aum_usd, 25_000_000.0)
        # The unconstrained peak is still reported, but only as a diagnostic.
        self.assertEqual(report.unconstrained_max_pnl_aum_usd, 100_000_000.0)
        self.assertIn("unconstrained net-PnL peak", report.audit_notes)

    def test_regression_censored_search_is_not_reported_as_unlimited(self):
        """
        The pre-audit engine returned limiting_factor 'UNLIMITED' and a capacity
        equal to max_search_aum_usd whenever no gate breached inside the range --
        i.e. it reported a loop bound as an allocation limit.
        """
        report = self.engine.estimate_capacity(_base_params(), 1_000_000.0, 5_000_000.0)
        self.assertEqual(report.limiting_factor, "SEARCH_RANGE_EXHAUSTED")
        self.assertTrue(report.search_range_exhausted)
        self.assertIn("search ceiling", report.audit_notes)

    def test_min_sharpe_binds_when_participation_is_unconstrained(self):
        """
        Lift the participation cap and impact drag alone must eventually break the
        Sharpe gate. Verified as a boundary, not against a re-derived AUM: the
        point at capacity clears 1.0 and the next point does not.
        """
        report = self.engine.estimate_capacity(
            _base_params(max_participation_rate_pct=1_000.0),
            1_000_000.0,
            200_000_000.0,
        )
        self.assertEqual(report.limiting_factor, "MIN_SHARPE_BREACH")
        self.assertFalse(report.search_range_exhausted)
        idx = int(report.max_capacity_aum_usd / 1_000_000.0) - 1
        self.assertGreaterEqual(report.capacity_curve[idx].net_sharpe_ratio_exact, 1.0)
        self.assertLess(report.capacity_curve[idx + 1].net_sharpe_ratio_exact, 1.0)
        self.assertTrue(report.capacity_curve[idx + 1].is_sharpe_breached)

    def test_strategy_below_the_gate_at_every_size_is_reported_distinctly(self):
        """
        A 0.67-Sharpe strategy is not capacity-constrained; it never clears a 1.0
        gate at any size. Reporting that as MIN_SHARPE_BREACH implies a limit that
        more liquidity or less turnover could relieve.
        """
        report = self.engine.estimate_capacity(
            _base_params(annual_gross_return_pct=0.10, daily_turnover_pct=0.0001),
            1_000_000.0,
            10_000_000.0,
        )
        self.assertEqual(report.limiting_factor, "BELOW_MIN_SHARPE_AT_ALL_SIZES")
        self.assertEqual(report.max_capacity_aum_usd, 0.0)
        self.assertEqual(report.optimal_sharpe_capacity_aum_usd, 0.0)

    def test_every_point_below_reported_capacity_is_feasible(self):
        """Capacity is an unbroken feasible run, not merely the last feasible point."""
        report = self.engine.estimate_capacity(_base_params(), 1_000_000.0, 100_000_000.0)
        for point in report.capacity_curve:
            if point.aum_usd <= report.max_capacity_aum_usd:
                self.assertFalse(point.is_capacity_exceeded, f"AUM {point.aum_usd}")

    def test_limiting_factor_is_always_a_declared_value(self):
        for params, ceiling in (
            (_base_params(), 100_000_000.0),
            (_base_params(max_participation_rate_pct=1_000.0), 200_000_000.0),
            (_base_params(), 5_000_000.0),
            (_base_params(annual_gross_return_pct=0.10, daily_turnover_pct=0.0001), 10_000_000.0),
        ):
            report = self.engine.estimate_capacity(params, 1_000_000.0, ceiling)
            self.assertIn(report.limiting_factor, LIMITING_FACTORS)

    def test_capacity_resolution_is_reported(self):
        report = self.engine.estimate_capacity(_base_params(), 5_000_000.0, 100_000_000.0)
        self.assertEqual(report.capacity_resolution_usd, 5_000_000.0)
        # A coarser grid can only under-report capacity, never over-report it.
        self.assertEqual(report.max_capacity_aum_usd, 25_000_000.0)
        self.assertGreaterEqual(report.max_capacity_net_sharpe, 1.0)

    def test_grid_is_index_derived_not_accumulated(self):
        """
        A step that is not exactly representable must still yield floor(range/step)
        points with no drift in the final one.
        """
        step = 1_000_000.0 / 3.0
        report = self.engine.estimate_capacity(_base_params(), step, 10_000_000.0)
        self.assertEqual(len(report.capacity_curve), 30)
        self.assertAlmostEqual(report.capacity_curve[-1].aum_usd, 30 * step, places=6)
        self.assertLessEqual(report.capacity_curve[-1].aum_usd, 10_000_000.0)


class TestCapacityInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = StrategyCapacityEstimatorEngine()

    def test_regression_non_finite_inputs_are_rejected(self):
        """
        The pre-audit engine let a NaN reach the Sharpe gate, where every comparison
        is False, and then reported a confident capacity for an unpriced strategy.
        """
        for field_name in (
            "annual_gross_return_pct",
            "annual_volatility_pct",
            "avg_daily_volatility_pct",
            "half_spread_bps",
            "risk_free_rate_pct",
        ):
            for bad in (float("nan"), float("inf")):
                with self.subTest(field=field_name, value=bad):
                    with self.assertRaises(ValueError):
                        self.engine.estimate_capacity(_base_params(**{field_name: bad}))

    def test_zero_volatility_is_rejected_not_divided_by(self):
        with self.assertRaises(ValueError):
            self.engine.estimate_capacity(_base_params(annual_volatility_pct=0.0))

    def test_zero_or_negative_adv_is_rejected_not_divided_by(self):
        for adv in (0.0, -1.0):
            with self.subTest(adv=adv):
                with self.assertRaises(ValueError):
                    self.engine.estimate_capacity(_base_params(avg_daily_volume_usd=adv))

    def test_regression_negative_turnover_is_rejected(self):
        """
        Negative turnover produced negative impact and spread costs -- a rebate for
        trading -- and the pre-audit engine then reported capacity limited by nothing.
        """
        with self.assertRaises(ValueError):
            self.engine.estimate_capacity(_base_params(daily_turnover_pct=-0.10))

    def test_negative_spread_and_participation_cap_are_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.estimate_capacity(_base_params(half_spread_bps=-1.0))
        with self.assertRaises(ValueError):
            self.engine.estimate_capacity(_base_params(max_participation_rate_pct=0.0))

    def test_blank_strategy_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.estimate_capacity(_base_params(strategy_id="   "))

    def test_booleans_are_not_accepted_as_numbers(self):
        with self.assertRaises(TypeError):
            self.engine.estimate_capacity(_base_params(annual_gross_return_pct=True))

    def test_regression_non_positive_step_is_rejected_instead_of_looping_forever(self):
        """
        The pre-audit loop was `while aum <= max_search_aum_usd: ... aum += step`.
        A negative step walked away from the ceiling and never terminated; a zero
        step divided by an AUM of zero. Neither is a usable failure mode.
        """
        for step in (0.0, -1_000_000.0):
            with self.subTest(step=step):
                with self.assertRaises(ValueError):
                    self.engine.estimate_capacity(_base_params(), step, 100_000_000.0)

    def test_regression_empty_grid_is_rejected(self):
        """
        A step larger than the search range produced an empty curve, a capacity of
        0.0, and limiting_factor 'UNLIMITED' -- three mutually contradictory claims.
        """
        with self.assertRaises(ValueError):
            self.engine.estimate_capacity(_base_params(), 500_000_000.0, 100_000_000.0)

    def test_oversized_grid_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.estimate_capacity(
                _base_params(), 1.0, float(MAX_GRID_POINTS + 1)
            )

    def test_negative_impact_prefactor_is_rejected(self):
        with self.assertRaises(ValueError):
            StrategyCapacityEstimatorEngine(impact_gamma=-0.5)
        with self.assertRaises(ValueError):
            StrategyCapacityEstimatorEngine(impact_gamma=float("nan"))


if __name__ == "__main__":
    unittest.main()

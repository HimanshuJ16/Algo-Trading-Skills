"""
Unit tests for implementation-shortfall-minimization.

Expected values are derived independently of the implementation: the Almgren-Chriss
schedule is cross-checked against ``math.sinh`` applied directly to Eq. (17) (a
different numerical path from the module's overflow-safe ``exp``/``expm1``
rewrite), ``kappa`` against the closed form ``arccosh(3)``, and the shortfall
figures by hand from Perold's identity.

Tests whose name ends in ``_regression`` reproduce a defect that the previous
implementation exhibited; each fails against that implementation.
"""
import math
import unittest

from implementation_shortfall_minimization import (
    ImplementationShortfallEngine,
    ExecutedTradeFill,
    ImpactParameters,
    ShortfallForecast,
    almgren_chriss_kappa,
    forecast_shortfall,
    median_mid_arrival_price,
    _sinh_ratio,
    STATUS_SUCCESS,
    STATUS_NO_FILLS,
)


def reference_trajectory(total_qty, n_intervals, kappa):
    """
    Almgren-Chriss (2000) Eq. 17 evaluated directly with ``math.sinh``, differenced
    into a trade list. Independent numerical path from the module's ``_sinh_ratio``.
    """
    horizon = kappa * n_intervals
    held = [total_qty]
    for j in range(1, n_intervals):
        fraction = math.sinh(kappa * (n_intervals - j)) / math.sinh(horizon)
        held.append(int(round(total_qty * fraction)))
    held.append(0)
    return [held[j] - held[j + 1] for j in range(n_intervals)]


def reference_float_trade_list(total_qty, n_intervals, kappa):
    """
    Eq. (17) differenced into an *unrounded* trade list, on an independent
    numerical path: ``math.exp`` applied to the analytic identity

        sinh(a)/sinh(b) = exp(a-b) * (1 - exp(-2a)) / (1 - exp(-2b))

    rather than the module's ``expm1``-based helper. Used where the reference
    value must be the exact optimum rather than its whole-share rounding.
    """
    def ratio(a, b):
        if a <= 0.0:
            return 0.0
        return math.exp(a - b) * (1.0 - math.exp(-2.0 * a)) / (1.0 - math.exp(-2.0 * b))

    horizon = float(n_intervals)
    remaining = [
        total_qty * ratio(kappa * (horizon - t), kappa * horizon)
        for t in range(n_intervals + 1)
    ]
    return [remaining[t] - remaining[t + 1] for t in range(n_intervals)]


def lambda_for_kappa(kappa):
    """
    Risk aversion that yields exactly ``kappa`` at the dimensionless defaults
    (sigma = eta = tau = 1, gamma = 0): inverting
    ``kappa = arccosh(1 + lambda/2)`` gives ``lambda = 2*(cosh(kappa) - 1)``.
    """
    return 2.0 * (math.cosh(kappa) - 1.0)


class TestAlmgrenChrissKappa(unittest.TestCase):

    def test_kappa_matches_closed_form(self):
        # lambda=4, sigma=1, eta=1, gamma=0, tau=1 -> kappa_tilde^2 = 4
        # -> kappa = arccosh(1 + 4/2) = arccosh(3).
        self.assertAlmostEqual(almgren_chriss_kappa(4.0), math.acosh(3.0), places=12)

    def test_kappa_depends_on_sigma_and_eta_regression(self):
        # kappa_tilde^2 = lambda*sigma^2/eta = 4*4/8 = 2 -> kappa = arccosh(2).
        # The previous implementation hard-coded kappa = sqrt(lambda) = 2.0 and
        # ignored sigma and eta entirely.
        self.assertAlmostEqual(almgren_chriss_kappa(4.0, 2.0, 8.0), math.acosh(2.0), places=12)
        self.assertNotAlmostEqual(almgren_chriss_kappa(4.0, 2.0, 8.0), math.sqrt(4.0), places=3)

    def test_kappa_uses_exact_root_not_small_tau_approximation(self):
        # Eq. (19), kappa ~ sqrt(lambda*sigma^2/eta), is the tau -> 0 limit. On a
        # coarse grid the exact Eq. (16) root is materially smaller.
        exact = almgren_chriss_kappa(4.0)
        approximation = math.sqrt(4.0)
        self.assertAlmostEqual(exact, math.acosh(3.0), places=12)
        self.assertLess(exact, approximation)
        self.assertGreater(abs(exact - approximation) / approximation, 0.10)

    def test_permanent_impact_enters_via_eta_tilde(self):
        # eta_tilde = eta - gamma*tau/2 = 10 - 4*1/2 = 8; kappa_tilde^2 = 1*16/8 = 2.
        self.assertAlmostEqual(
            almgren_chriss_kappa(1.0, 4.0, 10.0, 4.0, 1.0), math.acosh(2.0), places=12
        )

    def test_zero_risk_aversion_is_risk_neutral(self):
        self.assertEqual(almgren_chriss_kappa(0.0), 0.0)

    def test_negative_risk_aversion_raises_regression(self):
        # Previously floored to kappa=0 and silently returned a TWAP schedule.
        with self.assertRaises(ValueError):
            almgren_chriss_kappa(-1.0)

    def test_degenerate_eta_tilde_raises(self):
        # gamma*tau/2 >= eta leaves Eq. 16 without a real decay root.
        with self.assertRaises(ValueError):
            almgren_chriss_kappa(1.0, 1.0, temporary_impact_eta=1.0,
                                 permanent_impact_gamma=2.0, interval_length=1.0)

    def test_invalid_model_parameters_raise(self):
        with self.assertRaises(ValueError):
            almgren_chriss_kappa(1.0, volatility_per_sqrt_time=0.0)
        with self.assertRaises(ValueError):
            almgren_chriss_kappa(1.0, temporary_impact_eta=-1.0)
        with self.assertRaises(ValueError):
            almgren_chriss_kappa(1.0, interval_length=0.0)
        with self.assertRaises(ValueError):
            almgren_chriss_kappa(float("nan"))
        with self.assertRaises(TypeError):
            almgren_chriss_kappa("1.0")

    def test_sinh_ratio_matches_math_sinh(self):
        for a, b in [(1.0, 5.0), (4.0, 5.0), (0.5, 0.5), (1e-6, 5e-6), (12.0, 40.0)]:
            self.assertAlmostEqual(_sinh_ratio(a, b), math.sinh(a) / math.sinh(b), places=10)


class TestAlmgrenChrissTrajectory(unittest.TestCase):

    def setUp(self):
        self.engine = ImplementationShortfallEngine()

    def test_risk_neutral_schedule_is_exact_twap(self):
        traj = self.engine.calculate_almgren_chriss_trajectory(
            10_000, n_intervals=5, risk_aversion_lambda=0.0)
        self.assertEqual(traj, [2000, 2000, 2000, 2000, 2000])
        self.assertEqual(sum(traj), 10_000)

    def test_schedule_matches_equation_17_reference(self):
        # Cross-check against Eq. 17 evaluated with math.sinh at the same kappa.
        kappa = almgren_chriss_kappa(0.01)
        traj = self.engine.calculate_almgren_chriss_trajectory(
            10_000, n_intervals=5, risk_aversion_lambda=0.01)
        self.assertEqual(traj, reference_trajectory(10_000, 5, kappa))

    def test_schedule_is_front_loaded(self):
        traj = self.engine.calculate_almgren_chriss_trajectory(
            10_000, n_intervals=8, risk_aversion_lambda=0.05)
        self.assertGreater(traj[0], traj[-1])

    def test_adjacent_slices_never_invert_by_more_than_one_share(self):
        # The real-valued Almgren-Chriss trade list is strictly decreasing for
        # kappa > 0; whole-share rounding can swap two adjacent slices by at most
        # one share on a nearly flat schedule. Anything larger is a shape defect.
        for risk_aversion in (1e-8, 1e-4, 0.05, 0.5, 4.0):
            for intervals in (2, 3, 5, 9, 17):
                for qty in (7, 32, 101, 999, 10_000):
                    traj = self.engine.calculate_almgren_chriss_trajectory(
                        qty, intervals, risk_aversion)
                    for earlier, later in zip(traj, traj[1:]):
                        self.assertLessEqual(
                            later - earlier, 1, (risk_aversion, intervals, qty, traj))

    def test_higher_risk_aversion_front_loads_more(self):
        patient = self.engine.calculate_almgren_chriss_trajectory(10_000, 5, 0.01)
        urgent = self.engine.calculate_almgren_chriss_trajectory(10_000, 5, 1.00)
        self.assertGreater(urgent[0], patient[0])

    def test_no_negative_slice_regression(self):
        # The previous implementation rounded each slice independently and plugged
        # the residual into the last interval, returning [28, 4, 1, 0, -1] here --
        # a reversing trade. Almgren-Chriss (2000) Sec. 3: n_j > 0 for all j.
        traj = self.engine.calculate_almgren_chriss_trajectory(32, 5, 4.0)
        self.assertEqual(sum(traj), 32)
        self.assertTrue(all(share >= 0 for share in traj), traj)

    def test_no_negative_slice_across_parameter_sweep_regression(self):
        for risk_aversion in (0.0, 1e-4, 0.5, 4.0, 50.0):
            for intervals in (1, 3, 5, 9):
                for qty in (1, 7, 32, 101, 999):
                    traj = self.engine.calculate_almgren_chriss_trajectory(
                        qty, intervals, risk_aversion)
                    self.assertEqual(len(traj), intervals)
                    self.assertEqual(sum(traj), qty)
                    self.assertTrue(min(traj) >= 0, (risk_aversion, intervals, qty, traj))

    def test_extreme_risk_aversion_does_not_overflow_regression(self):
        # math.sinh(kappa * N) previously raised OverflowError for large lambda.
        traj = self.engine.calculate_almgren_chriss_trajectory(10_000, 5, 1e7)
        self.assertEqual(sum(traj), 10_000)
        self.assertEqual(traj[0], 10_000)  # Everything in the first interval.
        self.assertEqual(self.engine.calculate_almgren_chriss_trajectory(10_000, 5, 1e300)[0], 10_000)

    def test_single_interval_returns_whole_order(self):
        self.assertEqual(self.engine.calculate_almgren_chriss_trajectory(500, 1, 0.5), [500])

    def test_invalid_quantities_raise(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_almgren_chriss_trajectory(0, 5, 0.01)
        with self.assertRaises(ValueError):
            self.engine.calculate_almgren_chriss_trajectory(-100, 5, 0.01)
        with self.assertRaises(ValueError):
            self.engine.calculate_almgren_chriss_trajectory(1000, 0, 0.01)
        with self.assertRaises(TypeError):
            self.engine.calculate_almgren_chriss_trajectory(1000.5, 5, 0.01)
        with self.assertRaises(TypeError):
            self.engine.calculate_almgren_chriss_trajectory(True, 5, 0.01)


class TestPeroldShortfallDecomposition(unittest.TestCase):

    def setUp(self):
        self.engine = ImplementationShortfallEngine()
        # Buy 10,000 @ P0 = 100.00. 8,000 fill (4,000 @ 100.20 + 4,000 @ 100.30),
        # 2,000 unfilled marked at 101.00, $20 fees.
        #   execution   = 4000*0.20 + 4000*0.30      = $2,000
        #   opportunity = 2000*(101.00 - 100.00)     = $2,000
        #   fees                                     = $   20
        #   total                                    = $4,020
        #   bps         = 4020 / (10,000*100) * 1e4  =  40.20
        self.fills = [
            ExecutedTradeFill("F1", 4000, 100.20, 10.0, 1000),
            ExecutedTradeFill("F2", 4000, 100.30, 10.0, 1001),
        ]

    def _evaluate(self, **overrides):
        kwargs = dict(
            symbol="AAPL", side="BUY", total_order_qty=10_000, decision_price_p0=100.00,
            executed_fills=self.fills, final_market_price=101.00,
        )
        kwargs.update(overrides)
        return self.engine.evaluate_implementation_shortfall(**kwargs)

    def test_four_component_decomposition(self):
        report = self._evaluate()
        self.assertEqual(report.status, STATUS_SUCCESS)
        self.assertEqual(report.execution_cost_usd, 2000.0)
        self.assertEqual(report.opportunity_cost_usd, 2000.0)
        self.assertEqual(report.explicit_fees_usd, 20.0)
        self.assertEqual(report.total_implementation_shortfall_usd, 4020.0)
        self.assertEqual(report.total_implementation_shortfall_bps, 40.20)
        self.assertEqual(report.executed_qty, 8000)
        self.assertEqual(report.unfilled_qty, 2000)
        self.assertEqual(report.fill_ratio, 0.8)
        self.assertAlmostEqual(report.volume_weighted_executed_price, 100.25, places=6)

    def test_components_sum_to_total(self):
        report = self._evaluate()
        self.assertAlmostEqual(
            report.execution_cost_usd + report.opportunity_cost_usd + report.explicit_fees_usd,
            report.total_implementation_shortfall_usd, places=2)

    def test_arrival_price_splits_execution_cost_exactly(self):
        # delay  = 8000 * (100.10 - 100.00)                          = $  800
        # impact = 4000*(100.20-100.10) + 4000*(100.30-100.10)       = $1,200
        report = self._evaluate(arrival_price=100.10)
        self.assertEqual(report.delay_cost_usd, 800.0)
        self.assertEqual(report.market_impact_cost_usd, 1200.0)
        self.assertAlmostEqual(
            report.delay_cost_usd + report.market_impact_cost_usd,
            report.execution_cost_usd, places=2)
        # The split must not change the four-component total.
        self.assertEqual(report.total_implementation_shortfall_usd, 4020.0)

    def test_split_is_none_without_arrival_price(self):
        # Reporting the combined executed-leg cost under the name "market impact"
        # is what this None guards against: it is delay + impact + market drift.
        report = self._evaluate()
        self.assertIsNone(report.delay_cost_usd)
        self.assertIsNone(report.market_impact_cost_usd)
        self.assertIsNone(report.arrival_price)

    def test_sell_side_mirrors_buy_side(self):
        # Sell 10,000 @ P0=100. Fills 4,000 @ 99.80 + 4,000 @ 99.70; 2,000 unfilled
        # marked at 99.00. Every component mirrors the buy case exactly.
        report = self.engine.evaluate_implementation_shortfall(
            symbol="AAPL", side="SELL", total_order_qty=10_000, decision_price_p0=100.00,
            executed_fills=[
                ExecutedTradeFill("S1", 4000, 99.80, 10.0, 1),
                ExecutedTradeFill("S2", 4000, 99.70, 10.0, 2),
            ],
            final_market_price=99.00)
        self.assertEqual(report.execution_cost_usd, 2000.0)
        self.assertEqual(report.opportunity_cost_usd, 2000.0)
        self.assertEqual(report.total_implementation_shortfall_bps, 40.20)

    def test_favourable_execution_reports_negative_cost(self):
        # Sell fully filled 50c above the decision price: a $5,000 gain, -50 bps.
        report = self.engine.evaluate_implementation_shortfall(
            symbol="AAPL", side="SELL", total_order_qty=10_000, decision_price_p0=100.00,
            executed_fills=[ExecutedTradeFill("S1", 10_000, 100.50, 0.0, 1)],
            final_market_price=100.00)
        self.assertEqual(report.execution_cost_usd, -5000.0)
        self.assertEqual(report.total_implementation_shortfall_bps, -50.0)

    def test_maker_rebate_is_a_negative_fee(self):
        report = self._evaluate(
            executed_fills=[ExecutedTradeFill("F1", 8000, 100.25, -15.0, 1)])
        self.assertEqual(report.explicit_fees_usd, -15.0)
        self.assertEqual(report.total_implementation_shortfall_usd, 3985.0)

    def test_zero_fill_order_reports_no_fill_status_regression(self):
        # Previously reported volume_weighted_executed_price = decision price on an
        # order that never traded, implying a fill at P0.
        report = self.engine.evaluate_implementation_shortfall(
            symbol="AAPL", side="BUY", total_order_qty=1_000, decision_price_p0=100.00,
            executed_fills=[], final_market_price=105.00)
        self.assertEqual(report.status, STATUS_NO_FILLS)
        self.assertIsNone(report.volume_weighted_executed_price)
        self.assertEqual(report.executed_qty, 0)
        self.assertEqual(report.fill_ratio, 0.0)
        self.assertEqual(report.opportunity_cost_usd, 5000.0)   # 1000 * 5.00
        self.assertEqual(report.total_implementation_shortfall_bps, 500.0)

    def test_full_fill_leaves_no_opportunity_cost(self):
        report = self._evaluate(
            executed_fills=[ExecutedTradeFill("F1", 10_000, 100.10, 0.0, 1)],
            final_market_price=140.00)
        self.assertEqual(report.unfilled_qty, 0)
        self.assertEqual(report.opportunity_cost_usd, 0.0)
        self.assertEqual(report.total_implementation_shortfall_usd, 1000.0)

    def test_fill_order_does_not_change_result(self):
        forward = self._evaluate()
        reversed_report = self._evaluate(executed_fills=list(reversed(self.fills)))
        self.assertEqual(forward.total_implementation_shortfall_usd,
                         reversed_report.total_implementation_shortfall_usd)

    def test_bps_survives_sub_cent_shortfall_regression(self):
        # One share of a 0.0001-priced asset filled 10% through: a real 1,000 bps.
        # Deriving bps from the cent-rounded total collapsed this to 0.00 bps.
        report = self.engine.evaluate_implementation_shortfall(
            symbol="TOKEN", side="BUY", total_order_qty=1, decision_price_p0=0.0001,
            executed_fills=[ExecutedTradeFill("F1", 1, 0.00011, 0.0, 1)],
            final_market_price=0.0001)
        self.assertAlmostEqual(report.total_implementation_shortfall_bps, 1000.0, places=2)

    def test_zero_cost_sell_does_not_report_negative_zero(self):
        report = self.engine.evaluate_implementation_shortfall(
            symbol="AAPL", side="SELL", total_order_qty=1_000, decision_price_p0=100.00,
            executed_fills=[ExecutedTradeFill("S1", 1_000, 100.00, 0.0, 1)],
            final_market_price=100.00)
        for value in (report.execution_cost_usd, report.opportunity_cost_usd,
                      report.total_implementation_shortfall_usd,
                      report.total_implementation_shortfall_bps):
            self.assertEqual(value, 0.0)
            self.assertFalse(math.copysign(1.0, value) < 0, f"{value!r} is negative zero")

    def test_bps_denominator_is_intended_notional(self):
        # A 10% fill at the decision price with a 10% adverse move must show the
        # full opportunity cost on the intended notional, not on what filled.
        report = self.engine.evaluate_implementation_shortfall(
            symbol="AAPL", side="BUY", total_order_qty=10_000, decision_price_p0=100.00,
            executed_fills=[ExecutedTradeFill("F1", 1_000, 100.00, 0.0, 1)],
            final_market_price=110.00)
        self.assertEqual(report.opportunity_cost_usd, 90_000.0)   # 9,000 * 10.00
        self.assertEqual(report.total_implementation_shortfall_bps, 900.0)


class TestShortfallInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = ImplementationShortfallEngine()

    def _evaluate(self, **overrides):
        kwargs = dict(
            symbol="AAPL", side="BUY", total_order_qty=1_000, decision_price_p0=100.00,
            executed_fills=[ExecutedTradeFill("F1", 500, 100.10, 1.0, 1)],
            final_market_price=101.00,
        )
        kwargs.update(overrides)
        return self.engine.evaluate_implementation_shortfall(**kwargs)

    def test_non_finite_fill_price_raises_regression(self):
        # Previously produced a NaN shortfall reported as IS_EVALUATION_SUCCESS.
        with self.assertRaises(ValueError):
            self._evaluate(executed_fills=[ExecutedTradeFill("F1", 500, float("nan"), 1.0, 1)])
        with self.assertRaises(ValueError):
            self._evaluate(executed_fills=[ExecutedTradeFill("F1", 500, float("inf"), 1.0, 1)])

    def test_non_finite_fee_raises_regression(self):
        with self.assertRaises(ValueError):
            self._evaluate(executed_fills=[ExecutedTradeFill("F1", 500, 100.10, float("nan"), 1)])

    def test_non_positive_fill_price_raises(self):
        with self.assertRaises(ValueError):
            self._evaluate(executed_fills=[ExecutedTradeFill("F1", 500, 0.0, 1.0, 1)])
        with self.assertRaises(ValueError):
            self._evaluate(executed_fills=[ExecutedTradeFill("F1", 500, -100.0, 1.0, 1)])

    def test_non_positive_fill_quantity_raises_regression(self):
        # Previously accepted, yielding a negative executed_qty.
        with self.assertRaises(ValueError):
            self._evaluate(executed_fills=[ExecutedTradeFill("F1", -500, 100.10, 1.0, 1)])
        with self.assertRaises(ValueError):
            self._evaluate(executed_fills=[ExecutedTradeFill("F1", 0, 100.10, 1.0, 1)])

    def test_over_execution_raises_regression(self):
        # Previously clamped unfilled_qty to 0 and reported a clean shortfall on an
        # order that filled twice over.
        with self.assertRaises(ValueError):
            self._evaluate(executed_fills=[
                ExecutedTradeFill("F1", 1_000, 100.10, 1.0, 1),
                ExecutedTradeFill("F2", 1_000, 100.10, 1.0, 2),
            ])

    def test_duplicate_fill_id_raises_regression(self):
        with self.assertRaises(ValueError):
            self._evaluate(executed_fills=[
                ExecutedTradeFill("F1", 300, 100.10, 1.0, 1),
                ExecutedTradeFill("F1", 300, 100.10, 1.0, 2),
            ])

    def test_exact_full_fill_is_not_over_execution(self):
        report = self._evaluate(
            executed_fills=[ExecutedTradeFill("F1", 1_000, 100.10, 1.0, 1)])
        self.assertEqual(report.unfilled_qty, 0)

    def test_invalid_final_market_price_raises_when_unfilled(self):
        with self.assertRaises(ValueError):
            self._evaluate(final_market_price=float("nan"))
        with self.assertRaises(ValueError):
            self._evaluate(final_market_price=0.0)
        with self.assertRaises(ValueError):
            self._evaluate(final_market_price=-5.0)

    def test_invalid_side_raises(self):
        with self.assertRaises(ValueError):
            self._evaluate(side="SHORT")
        with self.assertRaises(TypeError):
            self._evaluate(side=None)

    def test_side_is_case_and_whitespace_insensitive(self):
        self.assertEqual(self._evaluate(side=" buy ").side, "BUY")

    def test_invalid_order_quantity_or_decision_price_raises(self):
        with self.assertRaises(ValueError):
            self._evaluate(total_order_qty=0)
        with self.assertRaises(ValueError):
            self._evaluate(decision_price_p0=0.0)
        with self.assertRaises(ValueError):
            self._evaluate(decision_price_p0=float("nan"))
        with self.assertRaises(TypeError):
            self._evaluate(total_order_qty=1000.5)

    def test_invalid_arrival_price_raises(self):
        with self.assertRaises(ValueError):
            self._evaluate(arrival_price=0.0)
        with self.assertRaises(ValueError):
            self._evaluate(arrival_price=float("nan"))

    def test_non_fill_object_raises(self):
        with self.assertRaises(TypeError):
            self._evaluate(executed_fills=[{"quantity": 500, "fill_price": 100.10}])


# ---------------------------------------------------------------------- #
# Arrival price benchmark (folded in from implementation-shortfall-minimization)
# ---------------------------------------------------------------------- #
class TestArrivalPriceBenchmark(unittest.TestCase):
    """The benchmark is the median mid over the one-second submission window."""

    def test_median_of_an_odd_window(self):
        # Mids: 100.00, 100.02, 100.04 -> median 100.02.
        quotes = [(99.98, 100.02), (100.00, 100.04), (100.02, 100.06)]
        self.assertAlmostEqual(median_mid_arrival_price(quotes), 100.02, places=12)

    def test_even_window_averages_the_two_central_mids(self):
        # Mids: 100.00, 100.02, 100.04, 100.10 -> (100.02 + 100.04)/2 = 100.03.
        quotes = [(99.98, 100.02), (100.00, 100.04), (100.02, 100.06), (100.08, 100.12)]
        self.assertAlmostEqual(median_mid_arrival_price(quotes), 100.03, places=12)

    def test_median_is_robust_to_a_single_flickering_tick(self):
        """Why the convention is a 1-second median and not one tick.

        A single wide print moves a single-tick benchmark by the full excursion
        and the median by nothing.
        """
        steady = [(99.99, 100.01)] * 8
        flicker = steady + [(100.90, 101.10), (99.99, 100.01)]
        self.assertAlmostEqual(median_mid_arrival_price(flicker), 100.00, places=12)
        # A single-tick capture that landed on the flicker would have been 100 bps
        # away, and the mean is dragged 10 bps by it.
        mids = [(b + a) / 2 for b, a in flicker]
        self.assertAlmostEqual(max(mids), 101.00, places=12)
        self.assertGreater(sum(mids) / len(mids), 100.09)

    def test_locked_market_is_accepted(self):
        # bid == ask is locked, not crossed; the mid is well defined.
        self.assertAlmostEqual(median_mid_arrival_price([(100.0, 100.0)]), 100.0, places=12)

    def test_empty_window_raises_instead_of_inferring_a_benchmark(self):
        with self.assertRaises(ValueError):
            median_mid_arrival_price([])

    def test_crossed_quote_raises(self):
        with self.assertRaises(ValueError) as ctx:
            median_mid_arrival_price([(100.05, 100.00)])
        self.assertIn("crossed", str(ctx.exception))

    def test_invalid_quote_prices_raise(self):
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(price=bad):
                with self.assertRaises(ValueError):
                    median_mid_arrival_price([(bad, 100.0)])
                with self.assertRaises(ValueError):
                    median_mid_arrival_price([(100.0, bad)])

    def test_malformed_quote_entries_raise(self):
        for bad in (100.0, "100/101", (100.0,), (100.0, 100.1, 100.2)):
            with self.subTest(entry=bad):
                with self.assertRaises(TypeError):
                    median_mid_arrival_price([bad])

    def test_benchmark_feeds_the_delay_impact_split(self):
        """End to end: capture P_a from the window, then split the executed leg."""
        arrival = median_mid_arrival_price([(100.08, 100.12), (100.09, 100.11)])
        self.assertAlmostEqual(arrival, 100.10, places=12)

        report = ImplementationShortfallEngine().evaluate_implementation_shortfall(
            symbol="TEST",
            side="BUY",
            total_order_qty=10_000,
            decision_price_p0=100.00,
            executed_fills=[
                ExecutedTradeFill("f1", 4_000, 100.20, 10.0, 1),
                ExecutedTradeFill("f2", 4_000, 100.30, 10.0, 2),
            ],
            final_market_price=101.00,
            arrival_price=arrival,
        )
        self.assertAlmostEqual(report.delay_cost_usd, 800.00, places=2)
        self.assertAlmostEqual(report.market_impact_cost_usd, 1_200.00, places=2)
        self.assertAlmostEqual(
            report.delay_cost_usd + report.market_impact_cost_usd,
            report.execution_cost_usd,
            places=2,
        )


# ---------------------------------------------------------------------- #
# Long-horizon trajectory numerics
# (folded in from implementation-shortfall-minimization)
# ---------------------------------------------------------------------- #
class TestTrajectoryLongHorizonNumerics(unittest.TestCase):
    """
    Regression tests for the sinh overflow failure mode.

    An implementation that multiplies the parent size by ``math.sinh(...)``
    before dividing overflows to ``inf`` while ``kappa*T`` is still under the
    ~710 float64 limit, and ``inf - inf`` then yields ``NaN``; a naive
    ``kappa*T > 700`` guard instead short-circuits the whole parent into
    interval 0 -- the maximum-impact outcome for an execution algo. The correct
    behaviour follows from the half-life ``1/kappa`` being independent of the
    horizon (A&C 2000, Sec. 2.3): at ``kappa = 1`` the first interval is
    ``1 - e^-1`` = 63.2% of the parent at *any* horizon.
    """

    def setUp(self):
        self.engine = ImplementationShortfallEngine()
        self.lam_k1 = lambda_for_kappa(1.0)
        self.lam_k05 = lambda_for_kappa(0.5)

    def test_lambda_for_kappa_helper_is_exact(self):
        self.assertAlmostEqual(almgren_chriss_kappa(self.lam_k1), 1.0, places=12)
        self.assertAlmostEqual(almgren_chriss_kappa(self.lam_k05), 0.5, places=12)

    def test_first_interval_share_is_horizon_independent(self):
        expected = 1.0 - math.exp(-1.0)
        for n_intervals in (10, 100, 701, 1000, 5000):
            with self.subTest(n_intervals=n_intervals):
                schedule = self.engine.calculate_almgren_chriss_trajectory(
                    1_000_000, n_intervals, self.lam_k1
                )
                self.assertEqual(sum(schedule), 1_000_000)
                self.assertAlmostEqual(schedule[0] / 1_000_000, expected, places=5)
                # Explicitly reject the degenerate single-interval dump.
                self.assertLess(schedule[0], 1_000_000)
                self.assertGreater(schedule[1], 0)

    def test_horizon_at_the_overflow_boundary_does_not_raise(self):
        # kappa*T in (697, 700] is where an unguarded sinh product turns NaN.
        for n_intervals in (698, 699, 700):
            with self.subTest(n_intervals=n_intervals):
                schedule = self.engine.calculate_almgren_chriss_trajectory(
                    1_000_000, n_intervals, self.lam_k1
                )
                self.assertEqual(sum(schedule), 1_000_000)
                self.assertTrue(all(size >= 0 for size in schedule))

    def test_slower_urgency_long_horizon(self):
        # kappa = 0.5 crosses the same boundary at ~1400 intervals; the correct
        # first interval is 1 - e^-0.5 = 39.3% of the parent regardless.
        expected = 1.0 - math.exp(-0.5)
        for n_intervals in (1396, 1400, 1402, 2000):
            with self.subTest(n_intervals=n_intervals):
                schedule = self.engine.calculate_almgren_chriss_trajectory(
                    1_000_000, n_intervals, self.lam_k05
                )
                self.assertEqual(sum(schedule), 1_000_000)
                self.assertAlmostEqual(schedule[0] / 1_000_000, expected, places=5)

    def test_matches_independent_closed_form_across_horizons(self):
        for n_intervals, lam, kappa in (
            (10, self.lam_k1, 1.0),
            (10, self.lam_k05, 0.5),
            (250, self.lam_k1, 1.0),
            (900, self.lam_k1, 1.0),
        ):
            with self.subTest(n_intervals=n_intervals, kappa=kappa):
                got = self.engine.calculate_almgren_chriss_trajectory(
                    1_000_000, n_intervals, lam
                )
                expected = reference_float_trade_list(1_000_000, n_intervals, kappa)
                for i, (g, e) in enumerate(zip(got, expected)):
                    self.assertLessEqual(
                        abs(g - e), 1.0, msg=f"interval {i}: got {g}, closed form {e}"
                    )

    def test_extreme_horizon_stays_finite_and_effectively_monotone(self):
        schedule = self.engine.calculate_almgren_chriss_trajectory(
            10_000_000, 20_000, self.lam_k1
        )
        self.assertEqual(sum(schedule), 10_000_000)
        self.assertTrue(all(size >= 0 for size in schedule))
        # Whole-share rounding of the holdings path can swap adjacent slices by
        # at most one share; it can never make the schedule rise.
        for i in range(len(schedule) - 1):
            self.assertGreaterEqual(schedule[i], schedule[i + 1] - 1)

    def test_schedule_is_deterministic(self):
        a = self.engine.calculate_almgren_chriss_trajectory(10_000, 10, self.lam_k1)
        b = self.engine.calculate_almgren_chriss_trajectory(10_000, 10, self.lam_k1)
        self.assertEqual(a, b)

    def test_one_share_across_many_intervals(self):
        for lam in (0.0, self.lam_k05, self.lam_k1):
            with self.subTest(risk_aversion=lam):
                schedule = self.engine.calculate_almgren_chriss_trajectory(1, 10, lam)
                self.assertEqual(sum(schedule), 1)
                self.assertTrue(all(size in (0, 1) for size in schedule))

    def test_quantity_smaller_than_interval_count(self):
        for lam in (0.0, self.lam_k05, self.lam_k1):
            with self.subTest(risk_aversion=lam):
                schedule = self.engine.calculate_almgren_chriss_trajectory(3, 10, lam)
                self.assertEqual(sum(schedule), 3)
                self.assertEqual(len(schedule), 10)
                self.assertTrue(all(size >= 0 for size in schedule))


# ---------------------------------------------------------------------- #
# Shortfall forecast (folded in from implementation-shortfall-minimization)
# ---------------------------------------------------------------------- #
class TestShortfallForecast(unittest.TestCase):
    """
    Cost-model tests.

    Expected values are derived independently of ``forecast_shortfall``: from the
    closed-form limiting cases Almgren & Chriss (2000) give for specific
    trajectories -- Eq. (10)/(11) for the uniform (TWAP) schedule and Eq. (13) for
    the single-interval dump -- and from Eq. (20) for the optimal trajectory. None
    shares an evaluation path with the implementation, which sums Eqs. (5)/(8)
    interval by interval.
    """

    def setUp(self):
        self.sigma = 0.02
        self.eta = 1e-6
        self.gamma = 5e-8
        self.epsilon = 0.01
        self.tau = 1.0
        self.params = ImpactParameters(
            sigma=self.sigma, eta=self.eta, gamma=self.gamma,
            epsilon=self.epsilon, tau=self.tau,
        )
        self.total = 1_000_000

    def test_eta_tilde_matches_paper(self):
        self.assertAlmostEqual(
            self.params.eta_tilde, self.eta - 0.5 * self.gamma * self.tau, places=18
        )

    def test_uniform_schedule_matches_equation_10_and_11(self):
        # Eq. (10): E = gamma*X^2/2 + eps*X + (eta - gamma*tau/2) * X^2/T
        # Eq. (11): V = (1/3) sigma^2 X^2 T (1 - 1/N)(1 - 1/(2N))
        n_intervals = 10
        horizon = n_intervals * self.tau
        schedule = [self.total / n_intervals] * n_intervals

        expected_e = (
            0.5 * self.gamma * self.total ** 2
            + self.epsilon * self.total
            + (self.eta - 0.5 * self.gamma * self.tau) * self.total ** 2 / horizon
        )
        expected_v = (
            (1.0 / 3.0) * self.sigma ** 2 * self.total ** 2 * horizon
            * (1.0 - 1.0 / n_intervals) * (1.0 - 1.0 / (2.0 * n_intervals))
        )

        got = forecast_shortfall(schedule, self.params)
        self.assertAlmostEqual(got.expected_cost / expected_e, 1.0, places=12)
        self.assertAlmostEqual(got.variance / expected_v, 1.0, places=12)

    def test_single_interval_dump_matches_equation_13(self):
        # Eq. (13): E = eps*X + eta*X^2/tau, V = 0. That equals
        # gamma*X^2/2 + eps*X + eta_tilde*X^2/tau, so it cross-checks the gamma
        # bookkeeping rather than restating the implementation.
        schedule = [self.total] + [0] * 9
        expected_e = self.epsilon * self.total + self.eta * self.total ** 2 / self.tau

        got = forecast_shortfall(schedule, self.params)
        self.assertAlmostEqual(got.expected_cost / expected_e, 1.0, places=12)
        self.assertEqual(got.variance, 0.0)
        self.assertEqual(got.stdev, 0.0)

    def test_optimal_trajectory_matches_equation_20(self):
        # Eq. (20), the closed-form E/V of the *optimal* trajectory, evaluated
        # directly with math.sinh (well conditioned at these moderate kappa*T).
        for n_intervals, kappa in ((10, 1.0), (10, 0.5), (25, 0.5)):
            with self.subTest(n_intervals=n_intervals, kappa=kappa):
                horizon = n_intervals * self.tau
                schedule = reference_float_trade_list(self.total, n_intervals, kappa)

                kt = kappa * horizon
                ktau = kappa * self.tau
                expected_e = (
                    0.5 * self.gamma * self.total ** 2
                    + self.epsilon * self.total
                    + self.params.eta_tilde * self.total ** 2 * math.tanh(0.5 * ktau)
                    * (self.tau * math.sinh(2 * kt) + 2 * horizon * math.sinh(ktau))
                    / (2 * self.tau ** 2 * math.sinh(kt) ** 2)
                )
                expected_v = (
                    0.5 * self.sigma ** 2 * self.total ** 2
                    * (
                        self.tau * math.sinh(kt) * math.cosh(kappa * (horizon - self.tau))
                        - horizon * math.sinh(ktau)
                    )
                    / (math.sinh(kt) ** 2 * math.sinh(ktau))
                )

                got = forecast_shortfall(schedule, self.params)
                self.assertAlmostEqual(got.expected_cost / expected_e, 1.0, places=10)
                self.assertAlmostEqual(got.variance / expected_v, 1.0, places=10)

    def test_expected_cost_and_variance_are_positive_across_kappa(self):
        # An impact cost cannot be negative for a one-sided schedule with
        # non-negative parameters, at any urgency.
        for n_intervals in (5, 10, 100):
            for kappa in (2.0, 1.0, 0.5, 0.1, 0.01, 1e-4):
                with self.subTest(n_intervals=n_intervals, kappa=kappa):
                    got = forecast_shortfall(
                        reference_float_trade_list(self.total, n_intervals, kappa),
                        self.params,
                    )
                    self.assertGreater(got.expected_cost, 0.0)
                    self.assertGreater(got.variance, 0.0)

    def test_variance_rises_to_the_twap_limit_as_kappa_falls(self):
        n_intervals = 10
        horizon = n_intervals * self.tau
        twap_variance = (
            (1.0 / 3.0) * self.sigma ** 2 * self.total ** 2 * horizon
            * (1.0 - 1.0 / n_intervals) * (1.0 - 1.0 / (2.0 * n_intervals))
        )
        previous = 0.0
        for kappa in (2.0, 1.0, 0.5, 0.1, 0.01):
            variance = forecast_shortfall(
                reference_float_trade_list(self.total, n_intervals, kappa), self.params
            ).variance
            self.assertGreater(variance, previous)
            previous = variance
        self.assertAlmostEqual(
            forecast_shortfall(
                reference_float_trade_list(self.total, n_intervals, 1e-6), self.params
            ).variance / twap_variance,
            1.0,
            places=6,
        )

    def test_front_loading_trades_impact_for_variance(self):
        """The core Almgren-Chriss tradeoff, priced on the integer schedule."""
        engine = ImplementationShortfallEngine()
        forecasts = [
            forecast_shortfall(
                engine.calculate_almgren_chriss_trajectory(self.total, 20, lam),
                self.params,
            )
            for lam in (lambda_for_kappa(1.0), lambda_for_kappa(0.5), 0.0)
        ]
        urgent, medium, patient = forecasts
        self.assertGreater(urgent.expected_cost, medium.expected_cost)
        self.assertGreater(medium.expected_cost, patient.expected_cost)
        self.assertLess(urgent.variance, medium.variance)
        self.assertLess(medium.variance, patient.variance)

    def test_objective_includes_the_risk_aversion_term(self):
        schedule = [self.total / 10] * 10
        neutral = forecast_shortfall(schedule, self.params, risk_aversion=0.0)
        averse = forecast_shortfall(schedule, self.params, risk_aversion=1e-6)
        self.assertEqual(neutral.objective, neutral.expected_cost)
        self.assertAlmostEqual(
            averse.objective, averse.expected_cost + 1e-6 * averse.variance, places=6
        )
        self.assertGreater(averse.objective, neutral.objective)

    def test_stdev_is_sqrt_of_variance_not_the_variance(self):
        got = forecast_shortfall([500, 300, 200], self.params)
        self.assertIsInstance(got, ShortfallForecast)
        self.assertAlmostEqual(got.stdev, math.sqrt(got.variance), places=12)
        # V is in currency squared; comparing a realised shortfall against it
        # rather than against stdev is the never-fires alert.
        self.assertNotAlmostEqual(got.stdev, got.variance, places=6)

    def test_forecast_is_immutable(self):
        got = forecast_shortfall([500, 300, 200], self.params)
        with self.assertRaises(AttributeError):
            got.expected_cost = 0.0

    def test_non_convex_parameters_rejected(self):
        # eta_tilde = eta - gamma*tau/2 <= 0 leaves the objective non-convex, so
        # the "optimal" trajectory is not a minimiser (A&C 2000, after Eq. 8).
        with self.assertRaises(ValueError):
            ImpactParameters(sigma=0.02, eta=1e-6, gamma=4e-6, tau=1.0)

    def test_invalid_impact_parameters_rejected(self):
        for kwargs in (
            {"sigma": 0.02, "eta": 0.0},
            {"sigma": 0.02, "eta": -1e-6},
            {"sigma": -0.02, "eta": 1e-6},
            {"sigma": 0.02, "eta": 1e-6, "gamma": -1e-9},
            {"sigma": 0.02, "eta": 1e-6, "epsilon": -0.01},
            {"sigma": 0.02, "eta": 1e-6, "tau": 0.0},
            {"sigma": float("nan"), "eta": 1e-6},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    ImpactParameters(**kwargs)
        with self.assertRaises(TypeError):
            ImpactParameters(sigma=0.02, eta=True)

    def test_invalid_schedules_rejected(self):
        with self.assertRaises(ValueError):
            forecast_shortfall([], self.params)
        with self.assertRaises(ValueError):
            forecast_shortfall([0, 0, 0], self.params)
        with self.assertRaises(ValueError):
            forecast_shortfall([100, -1], self.params)
        with self.assertRaises(ValueError):
            forecast_shortfall([100, float("nan")], self.params)
        with self.assertRaises(ValueError):
            forecast_shortfall([100, 100], self.params, risk_aversion=-1.0)
        with self.assertRaises(TypeError):
            forecast_shortfall("1000", self.params)
        with self.assertRaises(TypeError):
            forecast_shortfall([100, 100], {"sigma": 0.02})


if __name__ == "__main__":
    unittest.main()

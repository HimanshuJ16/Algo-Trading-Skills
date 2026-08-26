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
    almgren_chriss_kappa,
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


if __name__ == "__main__":
    unittest.main()

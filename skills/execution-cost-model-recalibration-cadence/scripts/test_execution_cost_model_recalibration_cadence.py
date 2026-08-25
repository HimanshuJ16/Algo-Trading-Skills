import math
import unittest

from execution_cost_model_recalibration_cadence import (
    STATUS_DEFERRED_INSUFFICIENT_SAMPLE,
    STATUS_MANUAL_REVIEW,
    STATUS_RECALIBRATION_RECOMMENDED,
    STATUS_STABLE,
    CostModelParameters,
    ExecutionCostModelRecalibrationEngine,
    TradeExecutionRecord,
)


def make_sample(eta: float, gamma: float, count: int = 60):
    """Build ``count`` trades whose realized IS is *exactly* the model at (eta, gamma).

    Spread, participation and volatility are cycled on co-prime periods so the two
    regressor columns vary independently: the design is well conditioned and the
    least-squares solution is unique, so a correct fit must return (eta, gamma)
    exactly regardless of what the active parameters were.
    """
    trades = []
    for i in range(count):
        spread_bps = 1.0 + (i % 7) * 0.5
        order_qty = 1_000 * (1 + i % 11)
        adv_shares = 250_000.0
        volatility_daily_pct = 1.0 + (i % 5) * 0.4

        # Regressors, recomputed here independently of the engine.
        x1 = spread_bps
        x2 = volatility_daily_pct * 100.0 * math.sqrt(order_qty / adv_shares)
        realized = eta * x1 + gamma * x2

        trades.append(TradeExecutionRecord(
            trade_id=f"TR_{i:03d}",
            symbol="AAPL",
            order_qty=order_qty,
            adv_shares=adv_shares,
            spread_bps=spread_bps,
            volatility_daily_pct=volatility_daily_pct,
            realized_is_bps=realized,
        ))
    return trades


class TestPrediction(unittest.TestCase):

    def setUp(self):
        self.engine = ExecutionCostModelRecalibrationEngine()
        self.params = CostModelParameters(eta_spread_coefficient=0.5, gamma_impact_coefficient=1.0)

    def test_prediction_matches_hand_computed_value_in_bps(self):
        # Hand computation, independent of the implementation:
        #   spread term  = 0.5 * 2.0 bps                     = 1.0 bps
        #   sigma        = 1.5 %/day = 150 bps
        #   participation= 9_000 / 100_000 = 0.09, sqrt       = 0.3
        #   impact term  = 1.0 * 150 bps * 0.3                = 45.0 bps
        #   total                                             = 46.0 bps
        record = TradeExecutionRecord("TR_01", "AAPL", 9_000, 100_000.0, 2.0, 1.5, 46.0)
        self.assertAlmostEqual(self.engine.predict_slippage_bps(self.params, record), 46.0, places=9)

    def test_impact_term_carries_the_square_root_law_magnitude(self):
        # Regression guard on the units contract: sigma enters in bps, not percent.
        # A 10%-of-ADV order at 1.5%/day volatility must predict tens of bps of impact,
        # not sub-bps. (Toth et al. 2011: I = Y * sigma * sqrt(Q/V), Y ~ 0.5-1.0.)
        record = TradeExecutionRecord("TR_01", "AAPL", 10_000, 100_000.0, 2.0, 1.5, 0.0)
        predicted = self.engine.predict_slippage_bps(self.params, record)
        expected = 0.5 * 2.0 + 1.0 * 150.0 * math.sqrt(0.1)
        self.assertAlmostEqual(predicted, expected, places=9)
        self.assertGreater(predicted, 40.0)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = ExecutionCostModelRecalibrationEngine()
        self.params = CostModelParameters(0.5, 1.0)

    def _audit(self, record):
        return self.engine.audit_and_recalibrate("M", "US_EQUITIES", self.params, [record])

    def test_nan_realized_is_rejected_rather_than_reported_stable(self):
        # Regression: an unvalidated NaN makes rmse and bias NaN, and every
        # ``nan > threshold`` comparison is False, so the engine would report
        # MODEL_PARAMETER_STABLE on corrupt data and suppress the recalibration.
        record = TradeExecutionRecord("TR_01", "AAPL", 10_000, 100_000.0, 2.0, 1.5, float("nan"))
        with self.assertRaises(ValueError):
            self._audit(record)

    def test_infinite_realized_is_rejected(self):
        record = TradeExecutionRecord("TR_01", "AAPL", 10_000, 100_000.0, 2.0, 1.5, float("inf"))
        with self.assertRaises(ValueError):
            self._audit(record)

    def test_zero_adv_rejected_with_actionable_message(self):
        record = TradeExecutionRecord("TR_01", "AAPL", 10_000, 0.0, 2.0, 1.5, 5.0)
        with self.assertRaises(ValueError) as ctx:
            self._audit(record)
        self.assertIn("adv_shares", str(ctx.exception))

    def test_negative_order_qty_rejected(self):
        record = TradeExecutionRecord("TR_01", "AAPL", -10_000, 100_000.0, 2.0, 1.5, 5.0)
        with self.assertRaises(ValueError) as ctx:
            self._audit(record)
        self.assertIn("order_qty", str(ctx.exception))

    def test_negative_spread_and_negative_volatility_rejected(self):
        with self.assertRaises(ValueError):
            self._audit(TradeExecutionRecord("TR_01", "AAPL", 10_000, 100_000.0, -2.0, 1.5, 5.0))
        with self.assertRaises(ValueError):
            self._audit(TradeExecutionRecord("TR_02", "AAPL", 10_000, 100_000.0, 2.0, -1.5, 5.0))

    def test_implausible_daily_volatility_rejected_as_unit_error(self):
        # 1.5%/day supplied in bps (150) is fine, but 250 %/day is not a market
        # observation - it is a unit mistake, and the message must say so.
        record = TradeExecutionRecord("TR_01", "AAPL", 10_000, 100_000.0, 2.0, 250.0, 5.0)
        with self.assertRaises(ValueError) as ctx:
            self._audit(record)
        self.assertIn("percent", str(ctx.exception))

    def test_empty_history_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.audit_and_recalibrate("M", "US_EQUITIES", self.params, [])

    def test_constructor_rejects_invalid_configuration(self):
        with self.assertRaises(ValueError):
            ExecutionCostModelRecalibrationEngine(max_tracking_error_rmse_bps=0.0)
        with self.assertRaises(ValueError):
            ExecutionCostModelRecalibrationEngine(max_systematic_bias_bps=-1.0)
        with self.assertRaises(ValueError):
            ExecutionCostModelRecalibrationEngine(min_recalibration_sample_size=1)
        with self.assertRaises(ValueError):
            ExecutionCostModelRecalibrationEngine(min_design_conditioning=1.0)


class TestLeastSquaresRefit(unittest.TestCase):

    def setUp(self):
        self.engine = ExecutionCostModelRecalibrationEngine()

    def test_refit_recovers_generating_parameters_exactly(self):
        # A consistent, well-conditioned design has a unique least-squares solution
        # equal to the parameters that generated the data.
        fit = self.engine.refit_model_parameters(make_sample(eta=0.8, gamma=1.2))
        self.assertTrue(fit.is_well_posed)
        self.assertAlmostEqual(fit.parameters.eta_spread_coefficient, 0.8, places=8)
        self.assertAlmostEqual(fit.parameters.gamma_impact_coefficient, 1.2, places=8)

    def test_refit_identifies_eta_and_gamma_independently(self):
        # Regression: a scalar rescale of fixed base parameters cannot separate the two
        # coefficients and pins their ratio at the base ratio no matter what the data
        # says. Here the true ratio is 0.2/3.0, far from any fixed seed.
        fit = self.engine.refit_model_parameters(make_sample(eta=0.2, gamma=3.0))
        self.assertTrue(fit.is_well_posed)
        self.assertAlmostEqual(fit.parameters.eta_spread_coefficient, 0.2, places=8)
        self.assertAlmostEqual(fit.parameters.gamma_impact_coefficient, 3.0, places=8)
        ratio = fit.parameters.eta_spread_coefficient / fit.parameters.gamma_impact_coefficient
        self.assertAlmostEqual(ratio, 0.2 / 3.0, places=8)

    def test_residuals_are_orthogonal_to_both_regressors(self):
        # First-order condition of least squares, derived independently of the closed
        # form: the residual vector is orthogonal to every regressor column.
        trades = make_sample(eta=0.8, gamma=1.2)
        # Perturb the targets so the fit is no longer exact and residuals are non-zero.
        for i, trade in enumerate(trades):
            trade.realized_is_bps += 2.0 * ((-1) ** i) + 0.1 * i

        fit = self.engine.refit_model_parameters(trades)
        self.assertTrue(fit.is_well_posed)

        dot_x1 = dot_x2 = scale = 0.0
        for trade in trades:
            x1 = trade.spread_bps
            x2 = trade.volatility_daily_pct * 100.0 * math.sqrt(trade.order_qty / trade.adv_shares)
            residual = trade.realized_is_bps - (
                fit.parameters.eta_spread_coefficient * x1
                + fit.parameters.gamma_impact_coefficient * x2
            )
            dot_x1 += x1 * residual
            dot_x2 += x2 * residual
            scale += abs(x2 * residual)

        self.assertAlmostEqual(dot_x1 / scale, 0.0, places=10)
        self.assertAlmostEqual(dot_x2 / scale, 0.0, places=10)

    def test_two_point_solve_matches_hand_computed_normal_equations(self):
        # Two observations, two unknowns: the least-squares solution is the exact
        # solve of  10a + 1b = 1  and  1a + 10b = 100, i.e. b = 999/99 = 10.0909...,
        # a = (1 - b)/10 = -0.90909...  (also a negative-coefficient case, below).
        trades = [
            TradeExecutionRecord("A", "AAPL", 1_000, 100_000.0, 10.0, 0.1, 1.0),
            TradeExecutionRecord("B", "AAPL", 1_000, 100_000.0, 1.0, 1.0, 100.0),
        ]
        engine = ExecutionCostModelRecalibrationEngine(min_recalibration_sample_size=2)
        fit = engine.refit_model_parameters(trades)
        self.assertAlmostEqual(fit.parameters.eta_spread_coefficient, -8910.0 / 9801.0, places=9)
        self.assertAlmostEqual(fit.parameters.gamma_impact_coefficient, 98901.0 / 9801.0, places=9)

    def test_collinear_design_is_rejected_not_silently_fitted(self):
        # Every trade identical: the two regressor columns are proportional, so eta and
        # gamma are not separately identifiable even though a fit would "succeed".
        trades = [
            TradeExecutionRecord(f"TR_{i}", "AAPL", 10_000, 100_000.0, 2.0, 1.5, 90.0)
            for i in range(60)
        ]
        fit = self.engine.refit_model_parameters(trades)
        self.assertFalse(fit.is_well_posed)
        self.assertIsNone(fit.parameters)
        self.assertIn("collinear", fit.rejection_reason.lower())

    def test_zero_spread_sample_is_rejected_as_degenerate(self):
        trades = [
            TradeExecutionRecord(f"TR_{i}", "AAPL", 1_000 * (1 + i % 9), 250_000.0, 0.0,
                                 1.0 + (i % 5) * 0.4, 30.0 + i)
            for i in range(60)
        ]
        fit = self.engine.refit_model_parameters(trades)
        self.assertFalse(fit.is_well_posed)
        self.assertIsNone(fit.parameters)


class TestAuditAndRecalibrate(unittest.TestCase):

    def setUp(self):
        self.engine = ExecutionCostModelRecalibrationEngine()
        self.active = CostModelParameters(eta_spread_coefficient=0.5, gamma_impact_coefficient=1.0)

    def test_stable_model_retains_active_parameters(self):
        # Data generated at exactly the active parameters: zero error by construction.
        trades = make_sample(eta=0.5, gamma=1.0)
        report = self.engine.audit_and_recalibrate("SQRT_MODEL", "US_EQUITIES", self.active, trades)

        self.assertFalse(report.is_recalibration_triggered)
        self.assertEqual(report.status, STATUS_STABLE)
        self.assertAlmostEqual(report.tracking_error_rmse_bps, 0.0, places=6)
        self.assertAlmostEqual(report.mean_prediction_bias_bps, 0.0, places=6)
        self.assertIsNone(report.recommended_parameters)
        self.assertIsNone(report.post_refit_rmse_bps)

    def test_regime_shift_triggers_refit_that_recovers_new_parameters(self):
        # Impact has doubled relative to the active model (gamma 1.0 -> 2.0).
        trades = make_sample(eta=0.5, gamma=2.0)
        report = self.engine.audit_and_recalibrate("SQRT_MODEL", "US_EQUITIES", self.active, trades)

        self.assertTrue(report.is_recalibration_triggered)
        self.assertEqual(report.status, STATUS_RECALIBRATION_RECOMMENDED)
        self.assertGreater(report.mean_prediction_bias_bps, 1.5)   # model under-predicts cost
        self.assertAlmostEqual(report.recommended_parameters.eta_spread_coefficient, 0.5, places=8)
        self.assertAlmostEqual(report.recommended_parameters.gamma_impact_coefficient, 2.0, places=8)
        # The refit reproduces the data exactly, so in-sample error collapses to zero.
        self.assertAlmostEqual(report.post_refit_rmse_bps, 0.0, places=6)
        self.assertAlmostEqual(report.post_refit_bias_bps, 0.0, places=6)

    def test_post_refit_rmse_never_exceeds_active_rmse(self):
        # Least squares minimises in-sample squared error by construction.
        trades = make_sample(eta=0.9, gamma=1.7)
        for i, trade in enumerate(trades):
            trade.realized_is_bps += 3.0 * ((-1) ** i)
        report = self.engine.audit_and_recalibrate("SQRT_MODEL", "US_EQUITIES", self.active, trades)

        self.assertEqual(report.status, STATUS_RECALIBRATION_RECOMMENDED)
        self.assertLessEqual(report.post_refit_rmse_bps, report.tracking_error_rmse_bps)

    def test_threshold_compared_on_unrounded_metrics(self):
        # Regression: rounding the metric to 2dp before the comparison creates a dead
        # band. Bias here is exactly +1.502 bps against a 1.50 bps limit - it must
        # trigger, not round itself back inside the limit.
        #   prediction = 0.5*2.0 + 1.0*(1.5%*100)*sqrt(1_000/100_000) = 1.0 + 15.0 = 16.0
        trades = [
            TradeExecutionRecord(f"TR_{i}", "AAPL", 1_000, 100_000.0, 2.0, 1.5, 16.0 + 1.502)
            for i in range(60)
        ]
        report = self.engine.audit_and_recalibrate("SQRT_MODEL", "US_EQUITIES", self.active, trades)

        self.assertAlmostEqual(report.mean_prediction_bias_bps, 1.5, places=6)  # reported rounded
        self.assertTrue(report.is_recalibration_triggered)

    def test_insufficient_sample_defers_refit_instead_of_fitting_noise(self):
        # Regression on the skill's headline pitfall: a breach observed over three
        # trades must not produce production parameters.
        trades = make_sample(eta=0.5, gamma=4.0, count=3)
        report = self.engine.audit_and_recalibrate("SQRT_MODEL", "US_EQUITIES", self.active, trades)

        self.assertTrue(report.is_recalibration_triggered)
        self.assertEqual(report.status, STATUS_DEFERRED_INSUFFICIENT_SAMPLE)
        self.assertIsNone(report.recommended_parameters)
        self.assertIsNone(report.fit_result)
        self.assertIn("minimum sample", report.audit_notes)

    def test_sample_at_exactly_the_minimum_is_refitted(self):
        # Boundary: the gate is ``n < minimum``, so n == minimum must proceed.
        engine = ExecutionCostModelRecalibrationEngine(min_recalibration_sample_size=50)
        trades = make_sample(eta=0.5, gamma=4.0, count=50)
        report = engine.audit_and_recalibrate("SQRT_MODEL", "US_EQUITIES", self.active, trades)
        self.assertEqual(report.status, STATUS_RECALIBRATION_RECOMMENDED)

    def test_collinear_sample_escalates_to_manual_review(self):
        trades = [
            TradeExecutionRecord(f"TR_{i}", "AAPL", 10_000, 100_000.0, 2.0, 1.5, 90.0)
            for i in range(60)
        ]
        report = self.engine.audit_and_recalibrate("SQRT_MODEL", "US_EQUITIES", self.active, trades)

        self.assertTrue(report.is_recalibration_triggered)
        self.assertEqual(report.status, STATUS_MANUAL_REVIEW)
        self.assertIsNone(report.recommended_parameters)
        self.assertFalse(report.fit_result.is_well_posed)

    def test_negative_fitted_coefficient_is_withheld_from_production(self):
        # Hand-solved above: eta = -0.9091, gamma = 10.0909. A negative eta implies
        # wider spreads reduce cost, so the fit must not be recommended.
        trades = [
            TradeExecutionRecord("A", "AAPL", 1_000, 100_000.0, 10.0, 0.1, 1.0),
            TradeExecutionRecord("B", "AAPL", 1_000, 100_000.0, 1.0, 1.0, 100.0),
        ]
        engine = ExecutionCostModelRecalibrationEngine(min_recalibration_sample_size=2)
        report = engine.audit_and_recalibrate("SQRT_MODEL", "US_EQUITIES", self.active, trades)

        self.assertEqual(report.status, STATUS_MANUAL_REVIEW)
        self.assertIsNone(report.recommended_parameters)
        self.assertFalse(report.fit_result.is_well_posed)
        self.assertLess(report.fit_result.parameters.eta_spread_coefficient, 0.0)
        self.assertIn("admissible", report.fit_result.rejection_reason)

    def test_bias_sign_convention_positive_means_under_prediction(self):
        trades = make_sample(eta=0.5, gamma=1.0)
        for trade in trades:
            trade.realized_is_bps += 5.0          # realized cost exceeds prediction
        report = self.engine.audit_and_recalibrate("SQRT_MODEL", "US_EQUITIES", self.active, trades)
        self.assertAlmostEqual(report.mean_prediction_bias_bps, 5.0, places=6)

        for trade in trades:
            trade.realized_is_bps -= 10.0         # realized cost beats prediction
        report = self.engine.audit_and_recalibrate("SQRT_MODEL", "US_EQUITIES", self.active, trades)
        self.assertAlmostEqual(report.mean_prediction_bias_bps, -5.0, places=6)
        self.assertTrue(report.is_recalibration_triggered)

    def test_report_records_sample_size_and_active_parameters(self):
        trades = make_sample(eta=0.5, gamma=1.0, count=55)
        report = self.engine.audit_and_recalibrate("SQRT_MODEL", "US_EQUITIES", self.active, trades)
        self.assertEqual(report.total_trades_analyzed, 55)
        self.assertIs(report.active_parameters, self.active)


if __name__ == "__main__":
    unittest.main()

import unittest
from execution_cost_model_recalibration_cadence import (
    ExecutionCostModelRecalibrationEngine, CostModelParameters, TradeExecutionRecord, CostModelRecalibrationReport
)

class TestExecutionCostModelRecalibrationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExecutionCostModelRecalibrationEngine(
            max_tracking_error_rmse_bps=3.5,
            max_systematic_bias_bps=1.5
        )
        self.active_params = CostModelParameters(eta_spread_coefficient=0.5, gamma_impact_coefficient=1.0)

    def test_stable_model_within_rmse_and_bias(self):
        # 3 trades with realized IS matching predictions closely (~1.47bps, ~1.85bps, ~1.53bps)
        trades = [
            TradeExecutionRecord("TR_01", "AAPL", 10_000, 100_000.0, 2.0, 0.015, realized_is_bps=1.50),
            TradeExecutionRecord("TR_02", "MSFT", 15_000, 100_000.0, 2.5, 0.018, realized_is_bps=1.90),
            TradeExecutionRecord("TR_03", "GOOGL", 12_000, 100_000.0, 2.1, 0.016, realized_is_bps=1.60)
        ]
        report = self.engine.audit_and_recalibrate("SQUARE_ROOT_MODEL", "US_EQUITIES", self.active_params, trades)

        self.assertFalse(report.is_recalibration_triggered)
        self.assertEqual(report.status, "MODEL_PARAMETER_STABLE")
        self.assertLess(report.tracking_error_rmse_bps, 3.5)
        self.assertIsNone(report.recommended_parameters)

    def test_recalibration_triggered_on_regime_shift_bias(self):
        # Market regime shift: Realized IS is much higher than predicted (Bias > +1.5 bps) -> RECALIBRATE!
        trades = [
            TradeExecutionRecord("TR_01", "AAPL", 10_000, 100_000.0, 2.0, 0.015, realized_is_bps=12.0), # Predicted ~1.47, diff = +10.53
            TradeExecutionRecord("TR_02", "MSFT", 15_000, 100_000.0, 2.5, 0.018, realized_is_bps=14.0),
            TradeExecutionRecord("TR_03", "GOOGL", 12_000, 100_000.0, 2.1, 0.016, realized_is_bps=13.0)
        ]
        report = self.engine.audit_and_recalibrate("SQUARE_ROOT_MODEL", "US_EQUITIES", self.active_params, trades)

        self.assertTrue(report.is_recalibration_triggered)
        self.assertEqual(report.status, "RECALIBRATION_RECOMMENDED")
        self.assertGreater(report.tracking_error_rmse_bps, 3.5)
        self.assertIsNotNone(report.recommended_parameters)
        self.assertGreater(report.recommended_parameters.gamma_impact_coefficient, 1.0)

if __name__ == '__main__':
    unittest.main()

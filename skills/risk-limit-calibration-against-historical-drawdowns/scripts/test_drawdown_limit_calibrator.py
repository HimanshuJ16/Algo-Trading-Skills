import unittest
from drawdown_limit_calibrator import (
    Config, MainEngine, DrawdownLimitCalibratorEngine,
    CalibrationMethod, InsufficientDataError
)

class TestMainEngineLegacy(unittest.TestCase):
    def test_default(self):
        engine = MainEngine(Config())
        self.assertEqual(engine.process(2.0), 2.0)

    def test_custom(self):
        engine = MainEngine(Config(param=3.0))
        self.assertEqual(engine.process(2.0), 6.0)

class TestDrawdownLimitCalibratorEngine(unittest.TestCase):

    def setUp(self):
        self.calibrator = DrawdownLimitCalibratorEngine(stress_buffer_multiplier=1.5)

    def test_compute_drawdown_metrics(self):
        # Synthetic return series with a drawdown: 5 positive, 3 negative (-2%), 5 positive
        returns = [0.01, 0.01, 0.005, 0.01, 0.005] + [-0.02, -0.02, -0.01] + [0.01, 0.015, 0.01, 0.02, 0.01]
        metrics = self.calibrator.compute_drawdown_metrics(returns)

        self.assertGreater(metrics.max_drawdown_pct, 4.0)  # ~4.9% max DD
        self.assertGreater(metrics.max_drawdown_duration_days, 0)
        self.assertGreater(metrics.var_99_pct, 0.0)
        self.assertGreater(metrics.cvar_99_pct, 0.0)
        self.assertGreater(metrics.ulcer_index, 0.0)

    def test_calibrate_risk_limits_historical_max_dd(self):
        returns = [0.01, 0.01, 0.005, 0.01, 0.005] + [-0.02, -0.02, -0.01] + [0.01, 0.015, 0.01, 0.02, 0.01]
        limits = self.calibrator.calibrate_risk_limits(
            daily_returns=returns,
            portfolio_capital_usd=1000000.0,
            method=CalibrationMethod.HISTORICAL_MAX_DD
        )
        self.assertEqual(limits.portfolio_capital_usd, 1000000.0)
        self.assertGreater(limits.calibrated_max_drawdown_pct, 5.0)  # > 5% floor
        self.assertGreater(limits.calibrated_max_drawdown_usd, 50000.0)
        self.assertGreater(limits.calibrated_daily_loss_limit_usd, 0.0)
        self.assertEqual(limits.position_size_scalar, 1.0)  # DD < 20% -> scalar = 1.0

    def test_high_drawdown_scales_position_size(self):
        # High drawdown series (-5% daily for 5 days -> ~23% DD)
        returns = [0.01] * 5 + [-0.05] * 5 + [0.01] * 5
        limits = self.calibrator.calibrate_risk_limits(
            daily_returns=returns,
            portfolio_capital_usd=500000.0,
            method=CalibrationMethod.HISTORICAL_MAX_DD
        )
        self.assertGreater(limits.calibrated_max_drawdown_pct, 20.0)
        self.assertLess(limits.position_size_scalar, 1.0)  # Position size scalar reduced (< 1.0)

    def test_insufficient_data_raises_error(self):
        returns = [0.01, -0.01, 0.02]  # Only 3 points (< 10 min)
        with self.assertRaises(InsufficientDataError):
            self.calibrator.compute_drawdown_metrics(returns)

if __name__ == '__main__':
    unittest.main()

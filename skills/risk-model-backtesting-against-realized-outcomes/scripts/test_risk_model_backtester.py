import unittest
from risk_model_backtester import (
    RiskModelBacktesterEngine, Result, DailyRiskObservation,
    RiskModelBacktestReport, BaselZone
)

class TestRiskModelBacktesterLegacy(unittest.TestCase):

    def setUp(self):
        self.engine = RiskModelBacktesterEngine()

    def test_execute_true(self):
        res = self.engine.execute(True)
        self.assertTrue(res.success)

    def test_execute_false(self):
        res = self.engine.execute(False)
        self.assertFalse(res.success)

class TestRiskModelBacktesterAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = RiskModelBacktesterEngine(confidence_level=0.99)

    def test_green_zone_model_accepted(self):
        # 250 observations with 2 exceptions (under 99% VaR limit $10,000)
        observations = []
        for i in range(250):
            pnl = -15000.0 if i in (50, 150) else 500.0
            observations.append(DailyRiskObservation(f"2026-01-{i+1:03d}", pnl, 10000.0))

        report = self.engine.backtest_var_model(observations)
        self.assertEqual(report.total_observations, 250)
        self.assertEqual(report.actual_exceptions, 2)
        self.assertEqual(report.basel_zone, BaselZone.GREEN)
        self.assertTrue(report.is_model_accepted)
        self.assertEqual(len(report.exceptions), 2)
        self.assertEqual(report.exceptions[0].breach_amount_usd, 5000.0)

    def test_yellow_zone_model_accepted_with_add_on(self):
        # 250 observations with 7 exceptions
        observations = []
        exception_days = set(range(10, 80, 10))  # 7 days
        for i in range(250):
            pnl = -12000.0 if i in exception_days else 500.0
            observations.append(DailyRiskObservation(f"2026-01-{i+1:03d}", pnl, 10000.0))

        report = self.engine.backtest_var_model(observations)
        self.assertEqual(report.actual_exceptions, 7)
        self.assertEqual(report.basel_zone, BaselZone.YELLOW)
        self.assertTrue(report.is_model_accepted)

    def test_red_zone_model_rejected(self):
        # 250 observations with 12 exceptions (severe under-reporting of risk)
        observations = []
        exception_days = set(range(10, 130, 10))  # 12 days
        for i in range(250):
            pnl = -15000.0 if i in exception_days else 500.0
            observations.append(DailyRiskObservation(f"2026-01-{i+1:03d}", pnl, 10000.0))

        report = self.engine.backtest_var_model(observations)
        self.assertEqual(report.actual_exceptions, 12)
        self.assertEqual(report.basel_zone, BaselZone.RED)
        self.assertFalse(report.is_model_accepted)
        self.assertGreater(report.kupiec_lr_stat, 3.841)  # Rejects null hypothesis

    def test_short_data_raises_error(self):
        observations = [DailyRiskObservation("2026-01-01", 100.0, 1000.0)] * 10
        with self.assertRaises(ValueError):
            self.engine.backtest_var_model(observations)

if __name__ == '__main__':
    unittest.main()

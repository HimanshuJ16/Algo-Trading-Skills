import unittest
from granularity_advisor import (
    BacktestGranularityAdvisorEngine, BacktestStrategyProfile, BacktestGranularityReport
)

class TestBacktestGranularityAdvisorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = BacktestGranularityAdvisorEngine()

    def test_intraday_strategy_granularity_approval(self):
        profile = BacktestStrategyProfile(
            strategy_name="Intraday_Momentum_01",
            holding_period="INTRADAY_MINUTES",
            trade_frequency_per_day=25.0,
            has_intraday_stop_loss=True,
            universe_size=500,
            history_years=5.0,
            selected_data_granularity="INTRADAY_1MIN"
        )
        report = self.engine.advise_backtest_granularity(profile)

        self.assertEqual(report.status, "GRANULARITY_APPROVED")
        self.assertFalse(report.has_ohlc_sequence_bias)
        self.assertGreater(report.estimated_storage_size_gb, 1.0) # ~18 GB

    def test_ohlc_sequence_bias_warning(self):
        # Stop-loss active with DAILY_EOD selected -> OHLC_SEQUENCE_BIAS_WARNING!
        profile = BacktestStrategyProfile(
            strategy_name="Flawed_EOD_Scalper",
            holding_period="INTRADAY_MINUTES",
            trade_frequency_per_day=15.0,
            has_intraday_stop_loss=True,
            universe_size=500,
            history_years=5.0,
            selected_data_granularity="DAILY_EOD"
        )
        report = self.engine.advise_backtest_granularity(profile)

        self.assertEqual(report.status, "OHLC_SEQUENCE_BIAS_WARNING")
        self.assertTrue(report.has_ohlc_sequence_bias)
        self.assertEqual(report.recommended_granularity, "INTRADAY_1MIN")

if __name__ == '__main__':
    unittest.main()

"""Unit tests for intraday-vs-eod-backtest-granularity-tradeoffs."""
import unittest
from granularity_advisor import GranularityAdvisor, DataGranularity


class TestGranularityAdvisor(unittest.TestCase):
    def setUp(self):
        self.advisor = GranularityAdvisor()

    def test_scalping_recommends_tick(self):
        rec = self.advisor.recommend(holding_period_days=0.001, signals_per_day=500)
        self.assertEqual(rec.recommended, DataGranularity.TICK)
        self.assertEqual(rec.strategy_type, "HFT/Scalping")

    def test_position_recommends_eod(self):
        rec = self.advisor.recommend(holding_period_days=30, signals_per_day=0.1)
        self.assertEqual(rec.recommended, DataGranularity.EOD)
        self.assertEqual(rec.strategy_type, "Position")

    def test_intraday_recommends_minute(self):
        rec = self.advisor.recommend(holding_period_days=0.1, signals_per_day=20)
        self.assertEqual(rec.recommended, DataGranularity.ONE_MINUTE)

if __name__ == "__main__":
    unittest.main()

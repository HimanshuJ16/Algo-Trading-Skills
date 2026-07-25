"""
Unit tests for vectorized-vs-event-driven-backtest-tradeoffs skill.
"""
import unittest
from engine_selector import DualBacktestEngineSelector, RecommendedEngine


class TestDualBacktestEngineSelector(unittest.TestCase):

    def setUp(self):
        self.selector = DualBacktestEngineSelector(commission_bps=5.0, slippage_bps=5.0)
        self.prices = [100.0, 101.0, 102.0, 101.5, 103.0, 104.0, 103.5, 105.0]
        self.signals = [0, 1, 1, 1, -1, -1, 1, 1]

    def test_engine_recommendation_scoring(self):
        # Low frequency daily strategy -> Vectorized recommended
        rec1 = self.selector.recommend_engine(trades_per_day=0.5, uses_limit_orders=False)
        self.assertEqual(rec1.engine, RecommendedEngine.VECTORIZED)

        # High frequency limit order strategy -> Event-Driven recommended
        rec2 = self.selector.recommend_engine(trades_per_day=15.0, uses_limit_orders=True)
        self.assertEqual(rec2.engine, RecommendedEngine.EVENT_DRIVEN)

    def test_dual_engine_comparison_and_execution_drag(self):
        report = self.selector.compare_engines(self.prices, self.signals)

        self.assertIsNotNone(report.vectorized_metrics)
        self.assertIsNotNone(report.event_driven_metrics)
        self.assertGreaterEqual(report.speedup_factor, 0.0)
        self.assertIn("Dual Engine Audit", report.summary)


if __name__ == "__main__":
    unittest.main()

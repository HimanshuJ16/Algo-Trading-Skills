import unittest
import time
from strategy_specific_data_dependency_mapping import (
    Config, Engine,
    StrategyDataDependencyEngine, DataDependencyNode, DependencyCriticality,
    FeedStatusUpdate, StrategyDataDependencyReport
)

class TestEngineLegacy(unittest.TestCase):
    def test_init(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertEqual(engine.config.name, "test")

    def test_run(self):
        config = Config(name="test")
        engine = Engine(config)
        self.assertTrue(engine.run())

class TestStrategyDataDependencyEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.deps = [
            DataDependencyNode("L2_BOOK", "L2 Orderbook Data", DependencyCriticality.CRITICAL, primary_vendor="Refinitiv", secondary_vendor="Bloomberg", max_acceptable_lag_seconds=60.0),
            DataDependencyNode("SENTIMENT", "Social Sentiment Stream", DependencyCriticality.HIGH, primary_vendor="Dataminr", secondary_vendor="RavenPack", max_acceptable_lag_seconds=300.0),
        ]
        self.engine = StrategyDataDependencyEngine("QUANT_ALPHA_01", self.deps)

    def test_strategy_ready_primary_feeds(self):
        now = time.time()
        updates = [
            FeedStatusUpdate("L2_BOOK", last_updated_epoch=now - 10, current_vendor="Refinitiv", is_healthy=True),
            FeedStatusUpdate("SENTIMENT", last_updated_epoch=now - 50, current_vendor="Dataminr", is_healthy=True),
        ]
        report = self.engine.evaluate_strategy_readiness(now, updates)

        self.assertTrue(report.is_strategy_ready_to_trade)
        self.assertEqual(report.readiness_score_pct, 100.0)
        self.assertEqual(len(report.blocked_dependencies), 0)
        self.assertEqual(report.active_feed_sources["L2_BOOK"], "Refinitiv")

    def test_primary_feed_failure_pivots_to_secondary_fallback(self):
        now = time.time()
        # Refinitiv L2_BOOK fails (stale lag 300s > 60s max)
        updates = [
            FeedStatusUpdate("L2_BOOK", last_updated_epoch=now - 300, current_vendor="Refinitiv", is_healthy=False),
            FeedStatusUpdate("SENTIMENT", last_updated_epoch=now - 50, current_vendor="Dataminr", is_healthy=True),
        ]
        report = self.engine.evaluate_strategy_readiness(now, updates)

        self.assertTrue(report.is_strategy_ready_to_trade) # Still ready because secondary vendor pivoted!
        self.assertEqual(report.active_feed_sources["L2_BOOK"], "Bloomberg")
        self.assertEqual(len(report.fallback_dependencies), 1)

    def test_critical_feed_breach_blocks_strategy(self):
        now = time.time()
        # L2_BOOK fails and has no secondary vendor
        deps_no_secondary = [
            DataDependencyNode("L2_BOOK", "L2 Orderbook", DependencyCriticality.CRITICAL, primary_vendor="Refinitiv", secondary_vendor=None, max_acceptable_lag_seconds=60.0)
        ]
        engine = StrategyDataDependencyEngine("QUANT_ALPHA_02", deps_no_secondary)
        updates = [
            FeedStatusUpdate("L2_BOOK", last_updated_epoch=now - 300, current_vendor="Refinitiv", is_healthy=False)
        ]
        report = engine.evaluate_strategy_readiness(now, updates)

        self.assertFalse(report.is_strategy_ready_to_trade)
        self.assertEqual(len(report.blocked_dependencies), 1)

if __name__ == '__main__':
    unittest.main()

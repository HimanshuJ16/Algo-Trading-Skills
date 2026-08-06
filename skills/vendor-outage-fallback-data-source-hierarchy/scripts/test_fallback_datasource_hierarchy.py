import unittest
import datetime
import time
from fallback_datasource_hierarchy import (
    DataSourceStatus,
    EngineState,
    DataSourceNode,
    MarketDataTick,
    FailoverEvent,
    VendorFallbackHierarchyEngine,
    FallbackEngineError,
)


class TestVendorFallbackHierarchyEngine(unittest.TestCase):
    def setUp(self):
        # Initialize engine with 1.0 second recovery cooling period for fast testing
        self.engine = VendorFallbackHierarchyEngine(recovery_cooling_seconds=1.0)

        # Register Primary (Priority 1), Secondary (Priority 2), Tertiary (Priority 3)
        self.primary = DataSourceNode(
            source_id="FEED-BPIPE",
            name="Bloomberg B-PIPE",
            priority=1,
            max_staleness_seconds=2.0,
            max_error_threshold=2,
        )
        self.secondary = DataSourceNode(
            source_id="FEED-REF",
            name="Refinitiv Elektron",
            priority=2,
            max_staleness_seconds=5.0,
            max_error_threshold=3,
        )
        self.tertiary = DataSourceNode(
            source_id="FEED-POLYGON",
            name="Polygon REST Feed",
            priority=3,
            max_staleness_seconds=10.0,
            max_error_threshold=5,
        )

        self.engine.register_data_source(self.primary)
        self.engine.register_data_source(self.secondary)
        self.engine.register_data_source(self.tertiary)

    def mock_fetch(self, symbol: str, source_id: str):
        prices = {"FEED-BPIPE": 150.0, "FEED-REF": 150.1, "FEED-POLYGON": 150.2}
        return prices.get(source_id, 150.0), 1000.0

    def test_primary_data_source_healthy_fetch(self):
        tick = self.engine.fetch_market_data_tick("AAPL", self.mock_fetch)
        self.assertEqual(tick.source_id, "FEED-BPIPE")
        self.assertEqual(tick.price, 150.0)
        self.assertFalse(tick.is_synthetic)
        self.assertEqual(self.engine.engine_state, EngineState.PRIMARY_ACTIVE)

    def test_primary_stale_failover_to_secondary(self):
        # Simulate Primary staleness (last heartbeat 10 seconds ago)
        self.primary.last_heartbeat = datetime.datetime.utcnow() - datetime.timedelta(seconds=10.0)

        tick = self.engine.fetch_market_data_tick("AAPL", self.mock_fetch)
        self.assertEqual(tick.source_id, "FEED-REF")
        self.assertEqual(tick.price, 150.1)
        self.assertEqual(self.engine.engine_state, EngineState.FAILOVER_ACTIVE)
        self.assertTrue(len(self.engine.event_log) > 0)

    def test_error_threshold_degradation_failover(self):
        # Exceed max error threshold on Primary
        self.engine.record_error("FEED-BPIPE", "Connection Timeout")
        self.engine.record_error("FEED-BPIPE", "Socket Closed")

        self.assertEqual(self.primary.status, DataSourceStatus.ERROR)

        tick = self.engine.fetch_market_data_tick("AAPL", self.mock_fetch)
        self.assertEqual(tick.source_id, "FEED-REF")

    def test_exhaustion_of_all_live_sources_fallback_to_synthetic_cache(self):
        # Seed synthetic cache with a healthy tick first
        self.engine.fetch_market_data_tick("AAPL", self.mock_fetch)

        # Make all live sources stale
        old_time = datetime.datetime.utcnow() - datetime.timedelta(seconds=30.0)
        self.primary.last_heartbeat = old_time
        self.secondary.last_heartbeat = old_time
        self.tertiary.last_heartbeat = old_time

        tick = self.engine.fetch_market_data_tick("AAPL", self.mock_fetch)
        self.assertEqual(tick.source_id, "SYNTHETIC_CACHE")
        self.assertTrue(tick.is_synthetic)
        self.assertEqual(self.engine.engine_state, EngineState.SYNTHETIC_CACHE_ACTIVE)

    def test_recovery_cooling_period_prevents_flapping(self):
        # 1. Primary goes stale -> Failover to Secondary
        self.primary.last_heartbeat = datetime.datetime.utcnow() - datetime.timedelta(seconds=10.0)
        self.engine.evaluate_health_and_failover()
        self.assertEqual(self.engine.active_source_id, "FEED-REF")

        # 2. Immediately restore Primary heartbeat
        self.engine.record_heartbeat("FEED-BPIPE")

        # 3. Cooling period active (1.0s) -> Should remain on Secondary temporarily
        active_node, _ = self.engine.evaluate_health_and_failover()
        self.assertEqual(active_node.source_id, "FEED-REF")

        # 4. Wait out cooling period
        time.sleep(1.1)

        # 5. Should successfully return to Primary
        active_node, _ = self.engine.evaluate_health_and_failover()
        self.assertEqual(active_node.source_id, "FEED-BPIPE")
        self.assertEqual(self.engine.engine_state, EngineState.PRIMARY_ACTIVE)

    def test_complete_outage_raises_error(self):
        # Empty synthetic cache, all sources stale
        old_time = datetime.datetime.utcnow() - datetime.timedelta(seconds=30.0)
        self.primary.last_heartbeat = old_time
        self.secondary.last_heartbeat = old_time
        self.tertiary.last_heartbeat = old_time

        with self.assertRaises(FallbackEngineError):
            self.engine.fetch_market_data_tick("MSFT", self.mock_fetch)


if __name__ == "__main__":
    unittest.main()


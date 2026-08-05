import unittest
from smart_order_router_failover_on_venue_outage import (
    Config, Engine,
    SmartOrderRouterFailoverEngine, TradingVenue, VenueHealthState,
    SORRoutingResult
)

class TestEngineLegacy(unittest.TestCase):
    def test_execute_true(self):
        engine = Engine(Config(enabled=True))
        self.assertTrue(engine.execute())

    def test_execute_false(self):
        engine = Engine(Config(enabled=False))
        self.assertFalse(engine.execute())

class TestSmartOrderRouterFailover(unittest.TestCase):

    def setUp(self):
        self.v1 = TradingVenue("NASDAQ", "Nasdaq Stock Market", ask_price=100.05, bid_price=100.00, available_qty=1000.0, max_error_threshold=3)
        self.v2 = TradingVenue("NYSE", "New York Stock Exchange", ask_price=100.10, bid_price=99.95, available_qty=2000.0, max_error_threshold=3)
        self.v3 = TradingVenue("BATS", "Cboe BATS Exchange", ask_price=100.08, bid_price=99.98, available_qty=1500.0, max_error_threshold=3)

        self.sor = SmartOrderRouterFailoverEngine(venues=[self.v1, self.v2, self.v3])

    def test_normal_best_execution_routing(self):
        # Buy 500 shares -> Should select NASDAQ (lowest ask price 100.05)
        res = self.sor.route_order("ORD_001", "BUY", 500.0)
        self.assertEqual(res.target_venue_id, "NASDAQ")
        self.assertEqual(res.routed_price, 100.05)
        self.assertFalse(res.is_failover_triggered)

    def test_circuit_breaker_and_automatic_failover(self):
        # Simulate 3 errors on preferred NASDAQ venue -> Circuit breaker trips!
        self.sor.report_venue_error("NASDAQ", "FIX Timeout 1")
        self.sor.report_venue_error("NASDAQ", "FIX Timeout 2")
        self.sor.report_venue_error("NASDAQ", "FIX Timeout 3")

        self.assertEqual(self.v1.state, VenueHealthState.CIRCUIT_BROKEN_OUTAGE)

        # Attempt to route to preferred NASDAQ -> Automatically fails over to BATS (ask 100.08 < NYSE 100.10)
        res = self.sor.route_order("ORD_002", "BUY", 500.0, preferred_venue_id="NASDAQ")
        self.assertTrue(res.is_failover_triggered)
        self.assertEqual(res.target_venue_id, "BATS")
        self.assertIn("NASDAQ", res.fallback_venues_used)
        self.assertEqual(res.routed_price, 100.08)

    def test_all_venues_outage_raises_runtime_error(self):
        # Trip circuit breakers on all 3 venues
        for _ in range(3):
            self.sor.report_venue_error("NASDAQ", "Down")
            self.sor.report_venue_error("NYSE", "Down")
            self.sor.report_venue_error("BATS", "Down")

        with self.assertRaises(RuntimeError) as ctx:
            self.sor.route_order("ORD_003", "BUY", 100.0)
        self.assertIn("All venues are in CIRCUIT_BROKEN_OUTAGE state", str(ctx.exception))

if __name__ == '__main__':
    unittest.main()

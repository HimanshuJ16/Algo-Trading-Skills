import unittest
from smart_order_routing_across_venues import (
    SmartOrderRoutingAcrossVenuesConfig,
    SmartOrderRoutingAcrossVenuesEngine,
    VenueQuote, ChildOrderRoute, SORRoutingPlan
)

class TestSmartOrderRoutingLegacy(unittest.TestCase):
    def setUp(self):
        self.config = SmartOrderRoutingAcrossVenuesConfig(enabled=True, threshold=100.0, size=50)
        self.engine = SmartOrderRoutingAcrossVenuesEngine(self.config)

    def test_evaluate_triggers_order(self):
        market_data = {"symbol": "AAPL", "price": 105.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["qty"], 50)

    def test_evaluate_no_trigger(self):
        market_data = {"symbol": "AAPL", "price": 95.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 0)

    def test_disabled_engine(self):
        self.engine.config.enabled = False
        market_data = {"symbol": "AAPL", "price": 105.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 0)

class TestSmartOrderRoutingEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = SmartOrderRoutingAcrossVenuesEngine()
        self.quotes = [
            VenueQuote("NASDAQ", bid_price=149.95, bid_qty=500, ask_price=150.00, ask_qty=300, taker_fee_per_share=0.0030),
            VenueQuote("BATS", bid_price=149.95, bid_qty=400, ask_price=150.00, ask_qty=400, taker_fee_per_share=0.0020),
            VenueQuote("NYSE", bid_price=149.90, bid_qty=1000, ask_price=150.05, ask_qty=1000, taker_fee_per_share=0.0030),
        ]

    def test_reg_nms_nbbo_consolidation_and_liquidity_slicing(self):
        # Parent order: BUY 600 shares of AAPL
        # NBBO Ask = $150.00 available at NASDAQ (300) and BATS (400).
        # Total NBBO liquidity = 700 shares. Order (600) fully satisfied at NBBO!
        plan = self.engine.route_parent_order(
            parent_order_id="PARENT_001",
            symbol="AAPL",
            side="BUY",
            quantity=600.0,
            venue_quotes=self.quotes
        )
        self.assertEqual(plan.nbbo_price, 150.00)
        self.assertEqual(plan.unrouted_quantity, 0.0)
        self.assertEqual(len(plan.routes), 2)
        # BATS has lower taker fee ($0.0020 vs $0.0030) -> Should route to BATS first (400 shares) then NASDAQ (200 shares)
        self.assertEqual(plan.routes[0].target_venue_id, "BATS")
        self.assertEqual(plan.routes[0].quantity, 400.0)
        self.assertEqual(plan.routes[1].target_venue_id, "NASDAQ")
        self.assertEqual(plan.routes[1].quantity, 200.0)

    def test_unrouted_quantity_when_liquidity_exhausted(self):
        # BUY 2,000 shares -> Reg NMS NBBO ask ($150.00) liquidity = 700 shares (NASDAQ 300 + BATS 400)
        # 2,000 - 700 = 1,300 unrouted shares at NBBO level
        plan = self.engine.route_parent_order(
            parent_order_id="PARENT_002",
            symbol="AAPL",
            side="BUY",
            quantity=2000.0,
            venue_quotes=self.quotes
        )
        self.assertEqual(plan.unrouted_quantity, 1300.0)  # 2000 - 700 = 1300 unrouted at NBBO

if __name__ == '__main__':
    unittest.main()

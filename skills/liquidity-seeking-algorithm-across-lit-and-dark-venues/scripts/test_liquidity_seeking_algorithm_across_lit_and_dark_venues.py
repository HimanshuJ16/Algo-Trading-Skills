import unittest
from liquidity_seeking_algorithm_across_lit_and_dark_venues import (
    LiquiditySeekingEngine, ParentOrderSpec, VenueBookSpec, LiquiditySeekingReport
)

class TestLiquiditySeekingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = LiquiditySeekingEngine()
        # Lit NBBO: $100.00 x $100.02 (Midpoint = $100.01)
        self.venues = [
            VenueBookSpec("NASDAQ", "LIT", bid_price=100.00, ask_price=100.02, bid_qty=5000, ask_qty=5000),
            VenueBookSpec("NYSE", "LIT", bid_price=100.00, ask_price=100.02, bid_qty=5000, ask_qty=5000),
            VenueBookSpec("DARK_ATS_ALPHA", "DARK", bid_price=100.01, ask_price=100.01, bid_qty=15000, ask_qty=15000, historical_fill_rate=0.80),
            VenueBookSpec("DARK_ATS_BETA", "DARK", bid_price=100.01, ask_price=100.01, bid_qty=5000, ask_qty=5000, historical_fill_rate=0.50)
        ]

    def test_successful_dark_and_lit_execution(self):
        # Target BUY 20,000 shares @ $100.05 limit
        # Dark Alpha (80% of 15k = 12,000 shares @ $100.01) -> $0.01 price improvement per share = $120.00
        # Dark Beta (50% of 5k = 2,500 shares @ $100.01) -> $0.01 price improvement per share = $25.00
        # Lit NASDAQ (fills remaining 5,500 shares @ $100.02)
        # Total Executed = 20,000 shares
        order = ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=20000, limit_price=100.05, min_dark_fill_qty=500)
        report = self.engine.execute_liquidity_seeking(order, self.venues)

        self.assertEqual(report.status, "LIQUIDITY_SEEKING_COMPLETE")
        self.assertEqual(report.total_executed_qty, 20000)
        self.assertEqual(report.dark_executed_qty, 14500) # 12,000 + 2,500
        self.assertEqual(report.lit_executed_qty, 5500)
        self.assertEqual(report.unfilled_qty, 0)
        self.assertEqual(report.nbbo_midpoint_price, 100.01)
        self.assertEqual(report.total_price_improvement_usd, 145.0)

    def test_min_dark_quantity_anti_pinging(self):
        # Setting min_dark_fill_qty = 20,000 skips Dark ATS Alpha (15k) and Beta (5k) -> Direct Lit Execution!
        order = ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=10000, limit_price=100.05, min_dark_fill_qty=20000)
        report = self.engine.execute_liquidity_seeking(order, self.venues)

        self.assertEqual(report.status, "LIQUIDITY_SEEKING_COMPLETE")
        self.assertEqual(report.dark_executed_qty, 0)
        self.assertEqual(report.lit_executed_qty, 10000)
        self.assertEqual(report.total_price_improvement_usd, 0.0)

if __name__ == '__main__':
    unittest.main()

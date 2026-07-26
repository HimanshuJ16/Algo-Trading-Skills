import unittest
from cross_asset_hedge_execution_synchronization import (
    CrossAssetHedgeSynchronizer, PrimaryFillEvent, HedgeOrder, HedgeSynchronizationStatus
)

class TestCrossAssetHedgeSynchronizer(unittest.TestCase):

    def setUp(self):
        # Options Delta Hedge Strategy:
        # Buy +1 Option Contract -> Short -50 Stock shares (Hedge Ratio = 50.0)
        self.synchronizer = CrossAssetHedgeSynchronizer(
            strategy_id="OPT_DELTA_HEDGE_01",
            primary_symbol="AAPL_250516_C200",
            hedge_symbol="AAPL",
            hedge_ratio=50.0,
            max_sync_delay_ms=100.0,
            unhedged_timeout_ms=500.0
        )

    def test_hedge_order_generation(self):
        # Primary fill: +10 call option contracts
        fill_event = PrimaryFillEvent(
            strategy_id="OPT_DELTA_HEDGE_01", fill_id="FILL_001",
            primary_symbol="AAPL_250516_C200", fill_qty=10.0,
            fill_price=5.50, timestamp_ms=1000.0
        )
        
        hedge_order = self.synchronizer.generate_hedge_order(fill_event)
        # Required Hedge Qty = -1.0 * 10 * 50.0 = -500 shares (SELL AAPL)
        self.assertEqual(hedge_order.hedge_symbol, "AAPL")
        self.assertEqual(hedge_order.target_hedge_qty, -500.0)
        self.assertEqual(hedge_order.side, "SELL")

    def test_synchronized_fill_within_sla(self):
        fill_event = PrimaryFillEvent(
            strategy_id="OPT_DELTA_HEDGE_01", fill_id="FILL_002",
            primary_symbol="AAPL_250516_C200", fill_qty=2.0,
            fill_price=5.50, timestamp_ms=1000.0
        )
        h_order = self.synchronizer.generate_hedge_order(fill_event)
        
        # Hedge filled at t = 1045 ms (45 ms delay <= 100 ms SLA)
        status = self.synchronizer.process_hedge_fill(
            hedge_order_id=h_order.hedge_order_id,
            filled_hedge_qty=-100.0,
            hedge_fill_timestamp_ms=1045.0
        )
        
        self.assertTrue(status.is_sync_sla_met)
        self.assertEqual(status.hedge_status, "SYNCHRONIZED_OK")
        self.assertEqual(status.sync_delay_ms, 45.0)

    def test_unhedged_timeout_trigger(self):
        fill_event = PrimaryFillEvent(
            strategy_id="OPT_DELTA_HEDGE_01", fill_id="FILL_003",
            primary_symbol="AAPL_250516_C200", fill_qty=5.0,
            fill_price=5.50, timestamp_ms=1000.0
        )
        h_order = self.synchronizer.generate_hedge_order(fill_event)
        
        # Hedge filled at t = 1600 ms (600 ms delay > 500 ms timeout!)
        status = self.synchronizer.process_hedge_fill(
            hedge_order_id=h_order.hedge_order_id,
            filled_hedge_qty=-250.0,
            hedge_fill_timestamp_ms=1600.0
        )
        
        self.assertFalse(status.is_sync_sla_met)
        self.assertEqual(status.hedge_status, "UNHEDGED_TIMEOUT_UNWIND")

if __name__ == '__main__':
    unittest.main()

import unittest
from calendar_spread_and_multi_leg_order_atomicity import (
    SpreadExecutionEngine,
    SpreadConfig,
    Leg,
    OrderSide,
    SpreadState
)

class TestMultiLegAtomicity(unittest.TestCase):
    
    def setUp(self):
        self.anchor = Leg("BTC-JUN", OrderSide.SELL, ratio=1, limit_price=65000)
        self.hedge = Leg("BTC-JUL", OrderSide.BUY, ratio=1, limit_price=66000)
        self.config = SpreadConfig(anchor_leg=self.anchor, hedge_leg=self.hedge, max_hedge_slippage_bps=10.0)
        
        self.submitted_orders = []
        
        def mock_broker_submit(symbol, side, qty, price, order_type):
            self.submitted_orders.append({
                "symbol": symbol, "side": side, "qty": qty, "price": price, "type": order_type
            })
            
        self.engine = SpreadExecutionEngine(self.config, mock_broker_submit)

    def test_perfect_fill(self):
        self.engine.start_execution(1.0)
        self.assertEqual(self.engine.state, SpreadState.PENDING_ANCHOR)
        self.assertEqual(len(self.submitted_orders), 1)
        self.assertEqual(self.submitted_orders[0]["type"], "LIMIT")
        
        # Anchor fills entirely
        self.engine.on_anchor_fill(1.0, 65000)
        self.assertEqual(self.engine.state, SpreadState.HEDGING)
        self.assertEqual(len(self.submitted_orders), 2)
        
        # Verify slippage calculation (Buy at 66000 with 10 bps slippage limit -> 66066)
        self.assertAlmostEqual(self.submitted_orders[1]["price"], 66066.0, places=2)
        self.assertEqual(self.submitted_orders[1]["type"], "IOC")
        
        # Hedge fills entirely
        self.engine.on_hedge_fill(1.0)
        self.assertEqual(self.engine.state, SpreadState.COMPLETED)

    def test_partial_fill_handling(self):
        self.engine.start_execution(10.0)
        
        # Anchor fills 4 units out of 10
        self.engine.on_anchor_fill(4.0, 65000)
        self.assertEqual(self.engine.state, SpreadState.ANCHOR_PARTIAL)
        
        # Engine should route hedge for exactly 4 units
        self.assertEqual(self.submitted_orders[1]["qty"], 4.0)
        
        # Hedge fills 4 units
        self.engine.on_hedge_fill(4.0)
        
        # State remains partial because 6 units of anchor are still pending
        self.assertEqual(self.engine.state, SpreadState.ANCHOR_PARTIAL)

    def test_legging_risk_broken_spread(self):
        self.engine.start_execution(1.0)
        self.engine.on_anchor_fill(1.0, 65000)
        
        # Hedge IOC order is submitted, but market moves and it only fills 0.5 before cancelling
        self.engine.on_hedge_fill(0.5)
        
        # Engine must detect legging risk
        self.assertEqual(self.engine.state, SpreadState.BROKEN)

if __name__ == '__main__':
    unittest.main()

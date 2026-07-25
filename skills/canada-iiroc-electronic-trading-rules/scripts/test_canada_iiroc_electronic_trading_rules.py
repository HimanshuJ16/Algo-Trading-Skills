import unittest
from canada_iiroc_electronic_trading_rules import (
    CiroPreTradeRiskEngine,
    RiskLimits,
    Order,
    OrderSide,
    ViolationCode
)

class TestCiroElectronicTradingRules(unittest.TestCase):
    def setUp(self):
        self.limits = RiskLimits(
            max_order_quantity=50000,
            max_order_value_cad=500000.0,
            max_price_deviation_pct=0.10
        )
        self.engine = CiroPreTradeRiskEngine(self.limits)

    def test_clean_order(self):
        order = Order(
            order_id="ORD1",
            symbol="RY.TO",
            side=OrderSide.BUY,
            quantity=1000,
            price=120.0,
            current_inventory=0,
            last_traded_price=121.0
        )
        res = self.engine.validate_order(order)
        self.assertTrue(res.is_compliant)

    def test_fat_finger_size(self):
        order = Order(
            order_id="ORD2",
            symbol="RY.TO",
            side=OrderSide.BUY,
            quantity=60000,  # Exceeds 50000
            price=1.0,       # Value is only 60k, so value is fine
            current_inventory=0,
            last_traded_price=1.0
        )
        res = self.engine.validate_order(order)
        self.assertFalse(res.is_compliant)
        self.assertIn(ViolationCode.FAT_FINGER_SIZE, res.violations)

    def test_fat_finger_price(self):
        order = Order(
            order_id="ORD3",
            symbol="RY.TO",
            side=OrderSide.BUY,
            quantity=1000,
            price=140.0,     # > 10% deviation from LTP
            current_inventory=0,
            last_traded_price=120.0
        )
        res = self.engine.validate_order(order)
        self.assertFalse(res.is_compliant)
        self.assertIn(ViolationCode.FAT_FINGER_PRICE, res.violations)

    def test_improper_short_mark(self):
        # Selling 1000, but only have 500. Should be marked SELL_SHORT.
        order = Order(
            order_id="ORD4",
            symbol="RY.TO",
            side=OrderSide.SELL,
            quantity=1000,
            price=120.0,
            current_inventory=500,
            last_traded_price=120.0
        )
        res = self.engine.validate_order(order)
        self.assertFalse(res.is_compliant)
        self.assertIn(ViolationCode.IMPROPER_SHORT_MARK, res.violations)
        
        # Fixing the side flag should pass
        order.side = OrderSide.SELL_SHORT
        res2 = self.engine.validate_order(order)
        self.assertTrue(res2.is_compliant)

if __name__ == '__main__':
    unittest.main()

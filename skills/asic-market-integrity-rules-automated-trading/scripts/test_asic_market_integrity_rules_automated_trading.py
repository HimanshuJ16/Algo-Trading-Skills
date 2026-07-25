import unittest
from asic_market_integrity_rules_automated_trading import (
    AsicMarketIntegrityConfig,
    AopOrderRequest,
    AsicKillSwitchManager,
    AsicAopPreTradeFilter
)

class TestAsicMarketIntegrityRulesAutomatedTrading(unittest.TestCase):
    def setUp(self):
        self.config = AsicMarketIntegrityConfig(
            max_order_value_aud=500000.0,
            max_order_volume=10000,
            max_price_deviation_pct=0.05 # 5%
        )
        self.kill_switch = AsicKillSwitchManager()
        self.filter = AsicAopPreTradeFilter(self.config, self.kill_switch)
        
    def test_valid_order(self):
        order = AopOrderRequest(symbol="BHP.AX", price=45.0, qty=1000, reference_price=44.5)
        res = self.filter.run_checks(order)
        self.assertTrue(res.is_compliant)

    def test_kill_switch_blocks_orders(self):
        self.kill_switch.trigger_kill_switch()
        order = AopOrderRequest(symbol="BHP.AX", price=45.0, qty=1000, reference_price=44.5)
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertIn("Kill Switch is currently active", res.reason)

    def test_max_volume_breach(self):
        order = AopOrderRequest(symbol="PEN.AX", price=0.10, qty=20000, reference_price=0.10)
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertIn("exceeds AOP limit (10000)", res.reason)

    def test_max_value_breach(self):
        order = AopOrderRequest(symbol="CBA.AX", price=100.0, qty=6000, reference_price=100.0)
        # Value = 600,000 > 500,000 limit
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertIn("exceeds AOP limit ($500,000.00)", res.reason)

    def test_price_deviation_breach(self):
        order = AopOrderRequest(symbol="BHP.AX", price=50.0, qty=1000, reference_price=45.0)
        # Deviation = (50 - 45)/45 = 11.1% > 5% limit
        res = self.filter.run_checks(order)
        self.assertFalse(res.is_compliant)
        self.assertIn("Price deviation (11.1%) exceeds AOP limit", res.reason)

if __name__ == '__main__':
    unittest.main()

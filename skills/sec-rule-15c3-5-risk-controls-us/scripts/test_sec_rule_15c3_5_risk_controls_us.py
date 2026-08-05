import unittest
from sec_rule_15c3_5_risk_controls_us import (
    SecRule15C35RiskControlsUsEngine, ComplianceResult,
    MarketAccessOrder, SecRule15c35Limits, MarketAccessRuleCode,
    MarketAccessCheckResult
)

class TestSecRule15C35Legacy(unittest.TestCase):
    def setUp(self):
        self.engine = SecRule15C35RiskControlsUsEngine()

    def test_empty(self):
        res = self.engine.run_checks({})
        self.assertFalse(res.is_compliant)

    def test_negative_size(self):
        res = self.engine.run_checks({'size': -10})
        self.assertFalse(res.is_compliant)

    def test_valid(self):
        res = self.engine.run_checks({'size': 100})
        self.assertTrue(res.is_compliant)

class TestSecRule15C35MarketAccessEngine(unittest.TestCase):

    def setUp(self):
        limits = SecRule15c35Limits(
            account_credit_cap_usd=500000.0,
            max_single_order_notional_usd=100000.0,
            max_single_order_qty=1000.0,
            max_price_collar_pct=0.05,
            restricted_symbols={"SANCTIONED_CO", "RESTRICTED_TICKER"}
        )
        self.engine = SecRule15C35RiskControlsUsEngine(limits=limits)

    def test_valid_order_allowed(self):
        order = MarketAccessOrder(
            order_id="ORD_001",
            account_id="ACC_100",
            symbol="AAPL",
            side="BUY",
            quantity=100.0,
            price=150.0,
            nbbo_mid_price=150.0
        )
        res = self.engine.evaluate_market_access_order(order)
        self.assertTrue(res.is_allowed)
        self.assertEqual(len(res.triggered_violations), 0)

    def test_notional_cap_and_qty_cap_breach(self):
        # Qty 2000 > 1000, Notional $400k > $100k cap
        order = MarketAccessOrder(
            order_id="ORD_002",
            account_id="ACC_100",
            symbol="AAPL",
            side="BUY",
            quantity=2000.0,
            price=200.0,
            nbbo_mid_price=200.0
        )
        res = self.engine.evaluate_market_access_order(order)
        self.assertFalse(res.is_allowed)
        self.assertIn(MarketAccessRuleCode.SINGLE_ORDER_QTY_CAP, res.triggered_violations)
        self.assertIn(MarketAccessRuleCode.SINGLE_ORDER_NOTIONAL_CAP, res.triggered_violations)

    def test_fat_finger_price_collar_breach(self):
        # Price $200 vs NBBO $150 (33.3% deviation > 5% collar limit)
        order = MarketAccessOrder(
            order_id="ORD_003",
            account_id="ACC_100",
            symbol="AAPL",
            side="BUY",
            quantity=100.0,
            price=200.0,
            nbbo_mid_price=150.0
        )
        res = self.engine.evaluate_market_access_order(order)
        self.assertFalse(res.is_allowed)
        self.assertIn(MarketAccessRuleCode.PRICE_COLLAR_FAT_FINGER, res.triggered_violations)

    def test_short_sale_missing_locate_breach(self):
        order = MarketAccessOrder(
            order_id="ORD_004",
            account_id="ACC_100",
            symbol="AAPL",
            side="SELL_SHORT",
            quantity=100.0,
            price=150.0,
            nbbo_mid_price=150.0,
            short_locate_id=None  # Missing locate!
        )
        res = self.engine.evaluate_market_access_order(order)
        self.assertFalse(res.is_allowed)
        self.assertIn(MarketAccessRuleCode.SHORT_SALE_LOCATE_MISSING, res.triggered_violations)

    def test_restricted_security_breach(self):
        order = MarketAccessOrder(
            order_id="ORD_005",
            account_id="ACC_100",
            symbol="RESTRICTED_TICKER",
            side="BUY",
            quantity=100.0,
            price=50.0,
            nbbo_mid_price=50.0
        )
        res = self.engine.evaluate_market_access_order(order)
        self.assertFalse(res.is_allowed)
        self.assertIn(MarketAccessRuleCode.RESTRICTED_SECURITY, res.triggered_violations)

if __name__ == '__main__':
    unittest.main()

import unittest
from cme_globex_futures_api_integration import (
    CmeGlobexOrderEngine, ContractSpec, CmeOrder
)

class TestCmeGlobexOrderEngine(unittest.TestCase):

    def setUp(self):
        # Spec for E-mini S&P 500 (ES)
        # Tick: 0.25, Price Band: 12.00 pts, MWP Protection: 6.00 pts
        es_spec = ContractSpec(
            symbol="ES",
            tick_size=0.25,
            price_band_points=12.00,
            protection_points=6.00
        )
        self.engine = CmeGlobexOrderEngine(contract_specs={"ES": es_spec})

    def test_rule_576_tag50_validation(self):
        # Test missing / empty / short / long Operator IDs
        self.assertFalse(self.engine.validate_operator_id(""))
        self.assertFalse(self.engine.validate_operator_id("A")) # < 2 chars
        self.assertFalse(self.engine.validate_operator_id("A" * 19)) # > 18 chars
        self.assertTrue(self.engine.validate_operator_id("OP123"))
        self.assertTrue(self.engine.validate_operator_id("DESK_ALGO_01"))

    def test_invalid_tag50_order_rejected(self):
        order = CmeOrder(
            symbol="ES", side="BUY", quantity=5, order_type="LIMIT",
            operator_id="X", account="ACCT1", price=5000.00
        )
        with self.assertRaises(ValueError) as ctx:
            self.engine.process_order(order, "ORD1", current_bid=4999.0, current_ask=5000.0, reference_price=5000.0)
        self.assertIn("Rule 576 Violation", str(ctx.exception))

    def test_market_with_protection_conversion(self):
        # Market Buy at Ask 5000.00 -> MWP Limit Buy at 5000.00 + 6.00 = 5006.00
        order = CmeOrder(
            symbol="ES", side="BUY", quantity=10, order_type="MARKET",
            operator_id="ALGO_TEAM_1", account="ACCT1"
        )
        msg = self.engine.process_order(order, "ORD2", current_bid=4999.75, current_ask=5000.00, reference_price=5000.00)
        
        self.assertTrue(msg.is_mwp_converted)
        self.assertEqual(msg.price, 5006.00)
        self.assertEqual(msg.operator_id, "ALGO_TEAM_1")

    def test_price_banding_violation(self):
        # Reference price 5000.00, Band 12.00 -> Allowed range [4988.00, 5012.00]
        # Attempt limit buy at 5015.00 -> Should fail price banding
        order = CmeOrder(
            symbol="ES", side="BUY", quantity=1, order_type="LIMIT",
            operator_id="ALGO_TEAM_1", account="ACCT1", price=5015.00
        )
        with self.assertRaises(ValueError) as ctx:
            self.engine.process_order(order, "ORD3", current_bid=4999.75, current_ask=5000.00, reference_price=5000.00)
        self.assertIn("Price Banding Violation", str(ctx.exception))

if __name__ == '__main__':
    unittest.main()

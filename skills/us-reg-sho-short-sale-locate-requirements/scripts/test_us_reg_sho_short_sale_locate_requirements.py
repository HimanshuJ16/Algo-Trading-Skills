import unittest
import datetime
from us_reg_sho_short_sale_locate_requirements import (
    OrderMarking,
    LocateRecord,
    OrderIntent,
    RegSHOValidationResult,
    RegSHOShortSaleEngine,
    RegSHOError,
)


class TestRegSHOShortSaleEngine(unittest.TestCase):
    def setUp(self):
        self.engine = RegSHOShortSaleEngine()

        # Grant a short sale locate: LOC-1001 for 1,000 shares of TSLA
        self.locate = self.engine.grant_locate(
            locate_id="LOC-1001", symbol="TSLA", quantity=1000, lender_id="GOLDMAN_PRIME"
        )

    def test_long_order_validation(self):
        order = OrderIntent(
            order_id="ORD-01-LONG",
            symbol="TSLA",
            marking=OrderMarking.LONG,
            quantity=500,
            price=200.00,
            nbb_price=199.90,
            nbo_price=200.00,
        )
        res = self.engine.validate_order_intent(order)
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.marking, OrderMarking.LONG)
        self.assertIsNone(res.locate_id)

    def test_short_order_missing_locate_rejected(self):
        order = OrderIntent(
            order_id="ORD-02-NAKED",
            symbol="TSLA",
            marking=OrderMarking.SHORT,
            quantity=100,
            price=200.00,
            nbb_price=199.90,
            nbo_price=200.00,
            locate_id=None,  # Missing locate ID!
        )
        res = self.engine.validate_order_intent(order)
        self.assertFalse(res.is_compliant)
        self.assertIn("Rule 203(b)(1) Violation", res.rejection_reason)

    def test_short_order_valid_locate_passed(self):
        order = OrderIntent(
            order_id="ORD-03-SHORT",
            symbol="TSLA",
            marking=OrderMarking.SHORT,
            quantity=400,
            price=200.00,
            nbb_price=199.90,
            nbo_price=200.00,
            locate_id="LOC-1001",
        )
        res = self.engine.validate_order_intent(order)
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.locate_id, "LOC-1001")
        self.assertEqual(self.locate.remaining_quantity, 600)  # 1000 - 400 = 600

    def test_short_order_insufficient_locate_quantity_rejected(self):
        order = OrderIntent(
            order_id="ORD-04-TOO-BIG",
            symbol="TSLA",
            marking=OrderMarking.SHORT,
            quantity=1500,  # 1500 > 1000 available!
            price=200.00,
            nbb_price=199.90,
            nbo_price=200.00,
            locate_id="LOC-1001",
        )
        res = self.engine.validate_order_intent(order)
        self.assertFalse(res.is_compliant)
        self.assertIn("Insufficient locate quantity", res.rejection_reason)

    def test_rule_201_ssr_rejects_short_sale_at_or_below_nbb(self):
        # Trigger Rule 201 SSR Circuit Breaker for TSLA
        self.engine.trigger_rule_201_ssr("TSLA")
        self.assertTrue(self.engine.is_ssr_active("TSLA"))

        # Order price $199.90 <= NBB $199.90 -> Violates SSR Alternative Uptick Rule!
        order = OrderIntent(
            order_id="ORD-05-SSR-BAD",
            symbol="TSLA",
            marking=OrderMarking.SHORT,
            quantity=100,
            price=199.90,  # Price <= NBB!
            nbb_price=199.90,
            nbo_price=200.00,
            locate_id="LOC-1001",
        )
        res = self.engine.validate_order_intent(order)
        self.assertFalse(res.is_compliant)
        self.assertTrue(res.ssr_active)
        self.assertIn("Rule 201 SSR Violation", res.rejection_reason)

    def test_rule_201_ssr_allows_short_sale_above_nbb(self):
        self.engine.trigger_rule_201_ssr("TSLA")

        # Order price $199.95 > NBB $199.90 -> Passes SSR Uptick Test!
        order = OrderIntent(
            order_id="ORD-06-SSR-GOOD",
            symbol="TSLA",
            marking=OrderMarking.SHORT,
            quantity=100,
            price=199.95,  # Price > NBB!
            nbb_price=199.90,
            nbo_price=200.00,
            locate_id="LOC-1001",
        )
        res = self.engine.validate_order_intent(order)
        self.assertTrue(res.is_compliant)

    def test_rule_201_ssr_allows_short_exempt_marking_at_or_below_nbb(self):
        self.engine.trigger_rule_201_ssr("TSLA")

        # SHORT_EXEMPT order at $199.90 <= NBB -> Exempt from Rule 201 price test!
        order = OrderIntent(
            order_id="ORD-07-EXEMPT",
            symbol="TSLA",
            marking=OrderMarking.SHORT_EXEMPT,
            quantity=100,
            price=199.90,
            nbb_price=199.90,
            nbo_price=200.00,
            locate_id="LOC-1001",
        )
        res = self.engine.validate_order_intent(order)
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.marking, OrderMarking.SHORT_EXEMPT)


if __name__ == "__main__":
    unittest.main()


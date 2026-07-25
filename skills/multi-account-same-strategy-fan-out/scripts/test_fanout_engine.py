"""
Unit tests for multi-account-same-strategy-fan-out skill.
"""
import unittest
from fanout_engine import MultiAccountStrategyFanOut


class TestMultiAccountStrategyFanOut(unittest.TestCase):

    def setUp(self):
        self.engine = MultiAccountStrategyFanOut(min_order_qty=1)
        self.engine.register_account("ACC_ALPHA", 500000.0)
        self.engine.register_account("ACC_BETA", 300000.0)
        self.engine.register_account("ACC_GAMMA", 200000.0)
        # Total NAV = $1,000,000

    def test_pro_rata_quantity_allocation(self):
        report = self.engine.calculate_fanout_orders(
            symbol="AAPL",
            action="BUY",
            total_target_quantity=1000,
        )

        self.assertEqual(len(report.account_orders), 3)
        self.assertEqual(report.total_allocated_qty, 1000)

        # Alpha (50% NAV) -> 500 shares
        alpha_ord = next(o for o in report.account_orders if o.account_id == "ACC_ALPHA")
        self.assertEqual(alpha_ord.allocated_quantity, 500)

        # Beta (30% NAV) -> 300 shares
        beta_ord = next(o for o in report.account_orders if o.account_id == "ACC_BETA")
        self.assertEqual(beta_ord.allocated_quantity, 300)

        # Gamma (20% NAV) -> 200 shares
        gamma_ord = next(o for o in report.account_orders if o.account_id == "ACC_GAMMA")
        self.assertEqual(gamma_ord.allocated_quantity, 200)

    def test_collision_free_client_order_ids(self):
        report = self.engine.calculate_fanout_orders("TSLA", "SELL", 500)
        order_ids = [o.client_order_id for o in report.account_orders]

        # All order IDs must be unique
        self.assertEqual(len(order_ids), len(set(order_ids)))
        for ord_id in order_ids:
            self.assertTrue(ord_id.startswith("CLORD_"))

    def test_minimum_quantity_enforcement(self):
        # Small account receiving < 1 share rounds up to min_order_qty=1
        engine = MultiAccountStrategyFanOut(min_order_qty=1)
        engine.register_account("ACC_HUGE", 999900.0)
        engine.register_account("ACC_TINY", 100.0)

        report = engine.calculate_fanout_orders("MSFT", "BUY", 10)
        tiny_ord = next(o for o in report.account_orders if o.account_id == "ACC_TINY")
        self.assertEqual(tiny_ord.allocated_quantity, 1)


if __name__ == "__main__":
    unittest.main()

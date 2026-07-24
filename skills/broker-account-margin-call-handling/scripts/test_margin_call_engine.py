"""
Unit tests for broker-account-margin-call-handling skill.

Tests:
1. Normal margin evaluation under 85% threshold.
2. Warning tier triggering block on new position entries.
3. Critical tier triggering open order cancellation.
4. Margin call breach state triggering automated de-leveraging plan generation.
"""
import unittest
from margin_call_engine import (
    AccountMarginSnapshot,
    BrokerMarginCallEngine,
    MarginCallError,
    MarginState,
    PositionMarginInfo,
)


class TestBrokerMarginCallEngine(unittest.TestCase):

    def setUp(self):
        self.engine = BrokerMarginCallEngine(
            warning_threshold=0.85, critical_threshold=0.95, breach_threshold=1.00
        )

    def test_normal_margin_health(self):
        snap = AccountMarginSnapshot(net_liquidation_value=100000.0, maintenance_margin=50000.0, excess_liquidity=50000.0)
        res = self.engine.evaluate_margin_health(snap)
        self.assertEqual(res.state, MarginState.NORMAL)
        self.assertTrue(self.engine.guard_new_order(snap, is_deleveraging=False))

    def test_warning_tier_vetoes_new_orders(self):
        # 88% margin ratio -> WARNING
        snap = AccountMarginSnapshot(net_liquidation_value=100000.0, maintenance_margin=88000.0, excess_liquidity=12000.0)
        res = self.engine.evaluate_margin_health(snap)
        self.assertEqual(res.state, MarginState.WARNING)

        with self.assertRaises(MarginCallError):
            self.engine.guard_new_order(snap, is_deleveraging=False)

        # De-leveraging order allowed
        self.assertTrue(self.engine.guard_new_order(snap, is_deleveraging=True))

    def test_critical_tier_evaluation(self):
        # 97% margin ratio -> CRITICAL
        snap = AccountMarginSnapshot(net_liquidation_value=100000.0, maintenance_margin=97000.0, excess_liquidity=3000.0)
        res = self.engine.evaluate_margin_health(snap)
        self.assertEqual(res.state, MarginState.CRITICAL)
        self.assertEqual(res.action_required, "CANCEL_ALL_PENDING_ORDERS")

    def test_deleveraging_plan_generation(self):
        # 105% margin ratio -> BREACH
        snap = AccountMarginSnapshot(net_liquidation_value=100000.0, maintenance_margin=105000.0, excess_liquidity=-5000.0)
        res = self.engine.evaluate_margin_health(snap)
        self.assertEqual(res.state, MarginState.MARGIN_CALL_BREACH)

        positions = [
            PositionMarginInfo(symbol="AAPL", quantity=100, current_price=150.0, margin_requirement=30000.0),
            PositionMarginInfo(symbol="TSLA", quantity=50, current_price=200.0, margin_requirement=50000.0),
        ]

        plan = self.engine.plan_deleveraging(snap, positions)
        self.assertTrue(len(plan) > 0)
        # Should prioritize highest margin requirement (TSLA first)
        self.assertEqual(plan[0][0].symbol, "TSLA")


if __name__ == "__main__":
    unittest.main()

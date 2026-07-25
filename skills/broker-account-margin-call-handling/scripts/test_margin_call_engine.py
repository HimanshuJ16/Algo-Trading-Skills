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
            warning_threshold=0.85, 
            critical_threshold=0.95, 
            breach_threshold=1.00,
            target_post_deleverage_ratio=0.75,
            liquidation_buffer_multiplier=1.0  # Keep at 1.0 for simpler math in tests
        )

    def test_normal_margin_health(self):
        snap = AccountMarginSnapshot(
            net_liquidation_value=100000.0, 
            initial_margin=60000.0,
            maintenance_margin=50000.0, 
            excess_liquidity=50000.0,
            available_funds=40000.0,
            buying_power=160000.0
        )
        res = self.engine.evaluate_margin_health(snap)
        self.assertEqual(res.state, MarginState.NORMAL)
        # 50k / 100k = 0.50 margin impact is 0.0 -> OK
        self.assertTrue(self.engine.guard_new_order(snap, margin_impact=0.0, is_deleveraging=False))

    def test_predictive_order_veto(self):
        snap = AccountMarginSnapshot(
            net_liquidation_value=100000.0, 
            initial_margin=80000.0,
            maintenance_margin=70000.0, 
            excess_liquidity=30000.0,
            available_funds=20000.0,
            buying_power=80000.0
        )
        # Current ratio 70%, normal. But order impact +20,000 -> 90%
        res = self.engine.evaluate_margin_health(snap)
        self.assertEqual(res.state, MarginState.NORMAL)
        
        with self.assertRaises(MarginCallError) as context:
            self.engine.guard_new_order(snap, margin_impact=20000.0, is_deleveraging=False)
        self.assertIn("Projected margin ratio 90.0% exceeds", str(context.exception))

    def test_critical_tier_evaluation(self):
        snap = AccountMarginSnapshot(
            net_liquidation_value=100000.0, 
            initial_margin=98000.0,
            maintenance_margin=96000.0, 
            excess_liquidity=4000.0,
            available_funds=2000.0,
            buying_power=8000.0
        )
        res = self.engine.evaluate_margin_health(snap)
        self.assertEqual(res.state, MarginState.CRITICAL)
        self.assertEqual(res.action_required, "CANCEL_ALL_PENDING_ORDERS")

    def test_deleveraging_plan_generation_with_priority(self):
        # 105% margin ratio -> BREACH
        snap = AccountMarginSnapshot(
            net_liquidation_value=100000.0, 
            initial_margin=110000.0,
            maintenance_margin=105000.0, 
            excess_liquidity=-5000.0,
            available_funds=-10000.0,
            buying_power=0.0
        )
        res = self.engine.evaluate_margin_health(snap)
        self.assertEqual(res.state, MarginState.MARGIN_CALL_BREACH)

        positions = [
            PositionMarginInfo(
                symbol="AAPL", asset_class="STK", quantity=100, current_price=150.0, 
                maintenance_margin_requirement=3000.0, average_daily_volume=50_000_000
            ),
            PositionMarginInfo(
                symbol="SPX_PUT_SHORT", asset_class="OPT", quantity=-5, current_price=20.0, 
                maintenance_margin_requirement=50000.0, is_short_option=True, average_daily_volume=1_000_000
            ),
            PositionMarginInfo(
                symbol="TSLA", asset_class="STK", quantity=200, current_price=200.0, 
                maintenance_margin_requirement=20000.0, average_daily_volume=10_000_000
            ),
        ]

        plan = self.engine.plan_deleveraging(snap, positions)
        self.assertTrue(len(plan) > 0)
        
        # Should prioritize short options (SPX_PUT_SHORT first) due to highest tail risk
        self.assertEqual(plan[0][0].symbol, "SPX_PUT_SHORT")
        
        # Target reduction = 105000 - (100000 * 0.75) = 105000 - 75000 = 30000
        # SPX_PUT_SHORT has 50000 margin for 5 units = 10000 per unit.
        # It needs to liquidate 3 units to hit 30000.
        self.assertAlmostEqual(plan[0][1], 3.0)

    def test_liquidity_cap_during_deleveraging(self):
        # 100k NLV, 150k Maint Margin = 150% ratio. Target 75% -> need 75k reduction.
        snap = AccountMarginSnapshot(
            net_liquidation_value=100000.0, 
            initial_margin=150000.0,
            maintenance_margin=150000.0, 
            excess_liquidity=-50000.0,
            available_funds=-50000.0,
            buying_power=0.0
        )
        # Position is illiquid. ADV = 100. Max participation = 10% = 10 units.
        positions = [
             PositionMarginInfo(
                symbol="ILLIQUID_CORP", asset_class="STK", quantity=500, current_price=10.0, 
                maintenance_margin_requirement=150000.0, average_daily_volume=100
            )
        ]
        
        # 150k margin / 500 units = 300 margin/unit. 
        # Need 75k reduction -> would need 250 units. 
        # But max participation caps at 10% of ADV(100) = 10 units.
        plan = self.engine.plan_deleveraging(snap, positions)
        self.assertEqual(plan[0][1], 10.0)

if __name__ == "__main__":
    unittest.main()

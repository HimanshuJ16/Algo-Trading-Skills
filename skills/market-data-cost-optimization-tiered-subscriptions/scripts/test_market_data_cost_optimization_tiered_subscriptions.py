import unittest
from market_data_cost_optimization_tiered_subscriptions import (
    MarketDataCostOptimizerEngine, SymbolSubscriptionSpec, MarketDataCostReport
)

class TestMarketDataCostOptimizerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MarketDataCostOptimizerEngine(demotion_inactivity_days_threshold=30)

    def test_cost_optimization_demotions_and_savings(self):
        # 10 Active symbols (Position=True, Tier1)
        # 90 Inactive symbols (Position=False, Signal=False, Inactivity=60d, currently Tier1)
        subscriptions = [
            SymbolSubscriptionSpec(f"ACT_{i}", "TIER1_DIRECT_L3", has_active_position=True, has_active_signal=True, days_since_last_trade=1)
            for i in range(10)
        ] + [
            SymbolSubscriptionSpec(f"INACT_{i}", "TIER1_DIRECT_L3", has_active_position=False, has_active_signal=False, days_since_last_trade=60)
            for i in range(90)
        ]

        report = self.engine.optimize_market_data_costs(subscriptions)

        self.assertEqual(report.status, "COST_OPTIMIZATION_SUCCESS")
        self.assertEqual(report.demotions_count, 90)
        self.assertEqual(report.promotions_count, 0)
        self.assertEqual(report.baseline_monthly_spend_usd, 100000.0) # 100 * $1,000
        self.assertEqual(report.optimized_monthly_spend_usd, 10450.0) # (10 * $1,000) + (90 * $5)
        self.assertEqual(report.total_monthly_savings_usd, 89550.0)
        self.assertAlmostEqual(report.savings_percentage, 89.55, delta=0.1)

    def test_symbol_promotion_to_tier1(self):
        # Inactive symbol currently on Tier3 gets active position and signal -> PROMOTE to Tier1!
        subscriptions = [
            SymbolSubscriptionSpec("TSLA", "TIER3_DELAYED_EOD", has_active_position=True, has_active_signal=True, days_since_last_trade=0)
        ]
        report = self.engine.optimize_market_data_costs(subscriptions)

        self.assertEqual(report.promotions_count, 1)
        self.assertEqual(report.decisions[0].action, "PROMOTE")
        self.assertEqual(report.decisions[0].recommended_tier, "TIER1_DIRECT_L3")

if __name__ == '__main__':
    unittest.main()

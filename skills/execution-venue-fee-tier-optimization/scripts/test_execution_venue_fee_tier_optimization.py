import unittest
from execution_venue_fee_tier_optimization import (
    ExecutionVenueFeeTierOptimizerEngine, ExecutionVenueSpec, VenueFeeTier, VenueFeeOptimizationReport
)

class TestExecutionVenueFeeTierOptimizerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExecutionVenueFeeTierOptimizerEngine(min_fill_probability_threshold=0.80)

        # Venue 1: NASDAQ (Maker Rebate -$0.0020 Tier 1, -$0.0028 Tier 2 VIP > 15M sh)
        self.nasdaq = ExecutionVenueSpec(
            venue_id="NASDAQ", fill_probability_rating=0.95,
            tiers=[
                VenueFeeTier("Tier 1", min_volume_shares=0, taker_rate_per_share=0.0030, maker_rate_per_share=-0.0020),
                VenueFeeTier("Tier 2 VIP", min_volume_shares=15_000_000, taker_rate_per_share=0.0025, maker_rate_per_share=-0.0028)
            ]
        )

        # Venue 2: Cboe EDGX (Maker Rebate -$0.0022 Tier 1, -$0.0029 Tier 2 VIP > 15M sh)
        self.edgx = ExecutionVenueSpec(
            venue_id="EDGX", fill_probability_rating=0.90,
            tiers=[
                VenueFeeTier("Tier 1", min_volume_shares=0, taker_rate_per_share=0.0030, maker_rate_per_share=-0.0022),
                VenueFeeTier("Tier 2 VIP", min_volume_shares=15_000_000, taker_rate_per_share=0.0025, maker_rate_per_share=-0.0029)
            ]
        )

    def test_multi_venue_optimization_selects_cheapest_net_cost(self):
        # 30,000,000 shares total volume budget (70% Maker / 30% Taker)
        venues = [self.nasdaq, self.edgx]
        report = self.engine.optimize_venue_fee_allocation(
            venues=venues, total_volume_shares=30_000_000, maker_ratio=0.70
        )

        self.assertEqual(report.total_monthly_volume_target_shares, 30_000_000)
        self.assertIsNotNone(report.optimal_strategy)
        self.assertGreater(report.optimal_strategy.total_maker_rebates_usd, 0.0)
        self.assertGreater(len(report.all_strategies_evaluated), 1)

if __name__ == '__main__':
    unittest.main()

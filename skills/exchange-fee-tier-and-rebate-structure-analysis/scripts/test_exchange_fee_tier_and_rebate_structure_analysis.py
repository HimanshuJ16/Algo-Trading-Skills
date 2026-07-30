import unittest
from exchange_fee_tier_and_rebate_structure_analysis import (
    ExchangeFeeTierAnalyzerEngine, FeeTierDefinition, VenueVolumeSummary, FeeTierAnalysisReport
)

class TestExchangeFeeTierAnalyzerEngine(unittest.TestCase):

    def setUp(self):
        # Nasdaq Maker-Taker Schedule:
        # Tier 1 (0 to 10M sh): Taker = +$0.0030/sh, Maker = -$0.0020/sh rebate
        # Tier 2 (> 10M sh): Taker = +$0.0025/sh, Maker = -$0.0024/sh rebate
        tiers = [
            FeeTierDefinition("Tier 1", min_monthly_volume_shares=0, maker_rate_per_share=-0.0020, taker_rate_per_share=0.0030),
            FeeTierDefinition("Tier 2 VIP", min_monthly_volume_shares=10_000_000, maker_rate_per_share=-0.0024, taker_rate_per_share=0.0025)
        ]
        self.engine = ExchangeFeeTierAnalyzerEngine("NASDAQ_EQUITIES", "MAKER_TAKER", tiers)

    def test_maker_taker_tier1_net_capture(self):
        # 8,000,000 shares total (5,000,000 Maker / 3,000,000 Taker)
        # Gross Taker Fees = 3,000,000 * 0.0030 = $9,000.00
        # Gross Maker Rebates = 5,000,000 * 0.0020 = $10,000.00
        # Net Cost = $9,000 - $10,000 = -$1,000.00 (Net Profit/Capture!)
        summary = VenueVolumeSummary(
            venue_id="NASDAQ_EQUITIES", pricing_model="MAKER_TAKER",
            rolling_30d_maker_volume_shares=5_000_000,
            rolling_30d_taker_volume_shares=3_000_000
        )
        report = self.engine.analyze_fee_tier_and_rebates(summary)
        
        self.assertEqual(report.active_tier_name, "Tier 1")
        self.assertEqual(report.gross_taker_fees_usd, 9000.0)
        self.assertEqual(report.gross_maker_rebates_usd, 10000.0)
        self.assertEqual(report.net_transaction_cost_usd, -1000.0)
        self.assertEqual(report.volume_needed_for_next_tier_shares, 2_000_000)
        self.assertEqual(report.next_tier_name, "Tier 2 VIP")

    def test_tier_2_vip_qualification(self):
        # 12,000,000 shares total (> 10,000,000 threshold -> Tier 2 VIP!)
        summary = VenueVolumeSummary(
            venue_id="NASDAQ_EQUITIES", pricing_model="MAKER_TAKER",
            rolling_30d_maker_volume_shares=8_000_000,
            rolling_30d_taker_volume_shares=4_000_000
        )
        report = self.engine.analyze_fee_tier_and_rebates(summary)
        
        self.assertEqual(report.active_tier_name, "Tier 2 VIP")
        self.assertEqual(report.volume_needed_for_next_tier_shares, 0)
        self.assertIsNone(report.next_tier_name)

if __name__ == '__main__':
    unittest.main()

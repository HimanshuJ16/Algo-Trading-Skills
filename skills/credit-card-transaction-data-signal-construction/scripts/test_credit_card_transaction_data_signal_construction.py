import unittest
from credit_card_transaction_data_signal_construction import (
    CreditCardTransactionSignalEngine, QuarterlyPanelData, TransactionSignalResult
)

class TestCreditCardTransactionSignalEngine(unittest.TestCase):

    def setUp(self):
        # Panel scaling factor = 50.0 (e.g. 2% panel sampling)
        self.engine = CreditCardTransactionSignalEngine(panel_scaling_multiplier=50.0, surprise_threshold_pct=2.5)

    def test_beat_buy_signal(self):
        # Panel spend = $40M -> Implied Revenue = $2.0B
        # Consensus = $1.90B -> Surprise = +5.26% (>= +2.5% -> BEAT_BUY)
        current = QuarterlyPanelData(
            ticker="CMG", fiscal_quarter="2025-Q1",
            panel_spend_usd=40_000_000.0, panel_transaction_count=2_000_000,
            consensus_revenue_usd=1_900_000_000.0
        )
        res = self.engine.generate_signal(current)
        
        self.assertEqual(res.signal, "BEAT_BUY")
        self.assertEqual(res.implied_revenue_usd, 2_000_000_000.0)
        self.assertAlmostEqual(res.predicted_surprise_pct, 5.26, places=2)

    def test_miss_sell_signal(self):
        # Panel spend = $35M -> Implied Revenue = $1.75B
        # Consensus = $1.90B -> Surprise = -7.89% (<= -2.5% -> MISS_SELL)
        current = QuarterlyPanelData(
            ticker="CMG", fiscal_quarter="2025-Q1",
            panel_spend_usd=35_000_000.0, panel_transaction_count=1_750_000,
            consensus_revenue_usd=1_900_000_000.0
        )
        res = self.engine.generate_signal(current)
        
        self.assertEqual(res.signal, "MISS_SELL")
        self.assertAlmostEqual(res.predicted_surprise_pct, -7.89, places=2)

    def test_yoy_growth_calculation(self):
        prior = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2024-Q1",
            panel_spend_usd=30_000_000.0, panel_transaction_count=1_500_000,
            consensus_revenue_usd=1_500_000_000.0
        )
        current = QuarterlyPanelData(
            ticker="SBUX", fiscal_quarter="2025-Q1",
            panel_spend_usd=33_000_000.0, panel_transaction_count=1_600_000,
            consensus_revenue_usd=1_650_000_000.0
        )
        
        res = self.engine.generate_signal(current, prior_year_data=prior)
        # Prior implied = $1.5B, Current implied = $1.65B -> YoY growth = +10.0%
        self.assertEqual(res.yoy_growth_pct, 10.0)
        self.assertEqual(res.signal, "NEUTRAL") # Matches consensus exactly -> Surprise 0%

if __name__ == '__main__':
    unittest.main()

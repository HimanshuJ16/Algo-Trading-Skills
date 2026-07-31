import unittest
from insider_transaction_filing_signal_research import (
    InsiderFilingSignalEngine, Form4TransactionRecord, InsiderFilingSignalReport
)

class TestInsiderFilingSignalEngine(unittest.TestCase):

    def setUp(self):
        self.engine = InsiderFilingSignalEngine()

    def test_strong_bullish_opportunistic_buy_signal(self):
        filings = [
            # 1. CEO Open-Market Purchase: 10,000 shares @ $50.00 = $500k (Weight 1.0) -> Discretionary!
            Form4TransactionRecord("F1", "Jane Doe", "CEO", "P", 10_000, 50.00, is_rule_10b5_1=False, is_open_market=True),
            # 2. Director Routine 10b5-1 Sale: 5,000 shares @ $50.00 = $250k -> FILTERED OUT!
            Form4TransactionRecord("F2", "John Smith", "DIRECTOR", "S", 5_000, 50.00, is_rule_10b5_1=True, is_open_market=True)
        ]
        report = self.engine.analyze_form4_filings("AAPL", filings)

        self.assertEqual(report.signal_classification, "STRONG_BULLISH_OPPORTUNISTIC_BUY")
        self.assertEqual(report.total_filings_analyzed, 2)
        self.assertEqual(report.routine_10b5_1_filtered_count, 1)
        self.assertEqual(report.opportunistic_buys_count, 1)
        self.assertEqual(report.total_opportunistic_buy_notional_usd, 500_000.0)
        self.assertEqual(report.weighted_net_sentiment_score, 1.00)

    def test_routine_10b5_1_sales_only_returns_neutral(self):
        # All 10b5-1 sales -> Filtered out -> NEUTRAL_ROUTINE!
        filings = [
            Form4TransactionRecord("F1", "Jane Doe", "CEO", "S", 10_000, 50.00, is_rule_10b5_1=True, is_open_market=True)
        ]
        report = self.engine.analyze_form4_filings("AAPL", filings)

        self.assertEqual(report.signal_classification, "NEUTRAL_ROUTINE")
        self.assertEqual(report.routine_10b5_1_filtered_count, 1)
        self.assertEqual(report.weighted_net_sentiment_score, 0.0)

if __name__ == '__main__':
    unittest.main()

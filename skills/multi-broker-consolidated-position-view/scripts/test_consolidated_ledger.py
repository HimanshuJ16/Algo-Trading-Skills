"""
Unit tests for multi-broker-consolidated-position-view skill.
"""
import unittest
from consolidated_ledger import MultiBrokerConsolidatedLedger, RawBrokerPosition


class TestMultiBrokerConsolidatedLedger(unittest.TestCase):

    def setUp(self):
        self.ledger = MultiBrokerConsolidatedLedger()
        self.ledger.register_symbol_mapping("AAPL STK SMART", "AAPL")
        self.ledger.register_symbol_mapping("AAPL.US", "AAPL")

    def test_consolidation_and_netting(self):
        raw_positions = [
            RawBrokerPosition("ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD"),
            RawBrokerPosition("alpaca", "AAPL.US", -40, 152.0, 160.0, "USD"),
            RawBrokerPosition("binance", "BTCUSDT", 2.0, 60000.0, 65000.0, "USD"),
        ]

        consolidated = self.ledger.consolidate_positions(raw_positions)

        self.assertIn("AAPL", consolidated)
        aapl = consolidated["AAPL"]
        # Net: 100 - 40 = 60 shares
        self.assertEqual(aapl.net_quantity, 60)
        # Gross: 100 + 40 = 140 shares
        self.assertEqual(aapl.gross_quantity, 140)
        self.assertEqual(aapl.broker_breakdown["ibkr"], 100)
        self.assertEqual(aapl.broker_breakdown["alpaca"], -40)

    def test_fx_conversion_to_usd(self):
        raw_positions = [
            RawBrokerPosition("degiro", "ASML", 10, 500.0, 500.0, "EUR"),  # 10 * 500 * 1.08 = $5400 USD
        ]

        consolidated = self.ledger.consolidate_positions(raw_positions)
        asml = consolidated["ASML"]
        self.assertAlmostEqual(asml.total_market_value_usd, 5400.0, places=2)

    def test_reconciliation_audit_discrepancy_detection(self):
        raw_positions = [
            RawBrokerPosition("ibkr", "AAPL STK SMART", 100, 150.0, 160.0, "USD"),
        ]
        target_ledger = {"AAPL": 80.0}  # Expected 80, but broker has 100

        discrepancies = self.ledger.reconcile_against_target(raw_positions, target_ledger)
        self.assertEqual(len(discrepancies), 1)
        self.assertEqual(discrepancies[0].canonical_symbol, "AAPL")
        self.assertAlmostEqual(discrepancies[0].discrepancy_qty, 20.0)


if __name__ == "__main__":
    unittest.main()

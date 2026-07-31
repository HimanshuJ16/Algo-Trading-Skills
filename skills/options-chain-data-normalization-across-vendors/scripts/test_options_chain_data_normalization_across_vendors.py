import unittest
from options_chain_data_normalization_across_vendors import (
    OptionsChainNormalizationEngine, NormalizedOptionContract, OptionsNormalizationReport
)

class TestOptionsChainNormalizationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = OptionsChainNormalizationEngine()

    def test_build_osi_symbol_formatting(self):
        # AAPL $150 Call expiring 2024-01-19 -> 'AAPL  240119C00150000' (21 chars)
        osi = self.engine.build_osi_symbol("AAPL", "2024-01-19", "CALL", 150.0)
        self.assertEqual(len(osi), 21)
        self.assertEqual(osi, "AAPL  240119C00150000")

        # NVDA $450 Put expiring 2024-06-21 -> 'NVDA  240621P00450000'
        osi_nvda = self.engine.build_osi_symbol("NVDA", "2024-06-21", "PUT", 450.0)
        self.assertEqual(len(osi_nvda), 21)
        self.assertEqual(osi_nvda, "NVDA  240621P00450000")

    def test_normalize_ibkr_and_polygon_chains(self):
        raw_ibkr = [
            {"symbol": "AAPL", "expiry": "20240119", "right": "C", "strike": 150.0, "bid": 5.20, "ask": 5.40}
        ]
        rep_ibkr = self.engine.normalize_chain("IBKR", raw_ibkr)
        self.assertEqual(rep_ibkr.quality_status, "DATA_INTEGRITY_OK")
        contract = rep_ibkr.normalized_contracts[0]
        self.assertEqual(contract.standard_osi_symbol, "AAPL  240119C00150000")
        self.assertEqual(contract.mid_price, 5.30)
        self.assertAlmostEqual(contract.spread, 0.20, delta=0.001)

    def test_invalid_quote_detection(self):
        # Inverted quote: Bid $6.00 > Ask $5.00 -> INVALID_QUOTE_DETECTED
        raw_bad = [
            {"underlying_ticker": "AAPL", "expiration_date": "2024-01-19", "contract_type": "call", "strike_price": 150.0, "bid": 6.00, "ask": 5.00}
        ]
        rep_bad = self.engine.normalize_chain("POLYGON", raw_bad)
        self.assertEqual(rep_bad.quality_status, "INVALID_QUOTE_DETECTED")

if __name__ == '__main__':
    unittest.main()

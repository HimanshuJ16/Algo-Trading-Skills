import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from algo_trading_disclosure_to_exchange_membership import (
    AlgoDisclosureComplianceEngine, 
    OutboundOrder
)

class TestAlgoTradingDisclosure(unittest.TestCase):
    def setUp(self):
        registry = {
            "VWAP_V2.0": "APPROVED",
            "STATARB_V1.1": "APPROVED",
            "TWAP_V1.0": "DEPRECATED",  # Exchange registration lapsed
            "NEW_ALGO_V1": "PENDING_EXCHANGE_APPROVAL"
        }
        self.engine = AlgoDisclosureComplianceEngine(registry)
        
    def test_compliant_algo_order(self):
        order = OutboundOrder("O-1", "AAPL", is_algorithmic=True, algo_id="VWAP_V2.0")
        res = self.engine.evaluate_order(order)
        self.assertTrue(res.is_compliant)
        
    def test_missing_algo_id_blocked(self):
        # Algorithmic order but forgot to tag the ID
        order = OutboundOrder("O-2", "MSFT", is_algorithmic=True, algo_id=None)
        res = self.engine.evaluate_order(order)
        self.assertFalse(res.is_compliant)
        self.assertIn("missing 'algo_id'", res.rejection_reason)
        
    def test_unapproved_algo_id_blocked(self):
        # Trying to trade with a deprecated or pending algo
        order = OutboundOrder("O-3", "TSLA", is_algorithmic=True, algo_id="TWAP_V1.0")
        res = self.engine.evaluate_order(order)
        self.assertFalse(res.is_compliant)
        self.assertIn("not an approved and disclosed algorithm", res.rejection_reason)
        
    def test_manual_order_passes(self):
        # Human trader, doesn't need an algo ID
        order = OutboundOrder("O-4", "GOOG", is_algorithmic=False, trader_id="TRADER_BOB")
        res = self.engine.evaluate_order(order)
        self.assertTrue(res.is_compliant)

if __name__ == '__main__':
    unittest.main()

import unittest
from datetime import datetime, timedelta
from binary_options import (
    Jurisdiction, TradeContext, ComplianceEngine, RiskEngine,
    BinaryOptionsManager, RegulatoryException, RiskException
)

class TestBinaryOptionsComplianceAndRisk(unittest.TestCase):
    def setUp(self):
        self.compliance_engine = ComplianceEngine()
        self.risk_engine = RiskEngine(max_notional_per_trade=100000.0)
        self.manager = BinaryOptionsManager(self.compliance_engine, self.risk_engine)
        self.future_expiry = datetime.now() + timedelta(days=1)

    def test_valid_us_professional_trade(self):
        ctx = TradeContext("T1", "AAPL", 50000.0, 150.0, self.future_expiry, Jurisdiction.US_CFTC, "PROFESSIONAL")
        result = self.manager.process_order(ctx)
        self.assertEqual(result["status"], "APPROVED")
        
    def test_banned_eu_retail_trade(self):
        ctx = TradeContext("T2", "EURUSD", 1000.0, 1.10, self.future_expiry, Jurisdiction.EU_ESMA, "RETAIL")
        result = self.manager.process_order(ctx)
        self.assertEqual(result["status"], "REJECTED_REGULATORY")
        self.assertIn("banned in EU_ESMA", result["message"])

    def test_unregulated_venue_prohibited(self):
        ctx = TradeContext("T3", "BTCUSD", 1000.0, 50000.0, self.future_expiry, Jurisdiction.UNREGULATED, "PROFESSIONAL")
        result = self.manager.process_order(ctx)
        self.assertEqual(result["status"], "REJECTED_REGULATORY")
        self.assertIn("strictly prohibited", result["message"])

    def test_risk_limit_exceeded(self):
        ctx = TradeContext("T4", "SPX", 200000.0, 4500.0, self.future_expiry, Jurisdiction.US_CFTC, "PROFESSIONAL")
        result = self.manager.process_order(ctx)
        self.assertEqual(result["status"], "REJECTED_RISK")
        self.assertIn("exceeds maximum per trade limit", result["message"])

if __name__ == '__main__':
    unittest.main()

import unittest
from cross_border_data_transfer_restrictions_for_trade_data import (
    CrossBorderTradeDataGovernanceEngine, JurisdictionTransferPolicy, TradeDataPayload
)

class TestCrossBorderTradeDataGovernanceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CrossBorderTradeDataGovernanceEngine([
            JurisdictionTransferPolicy("CN", "US", "REQUIRES_ANONYMIZATION", "PIPL_DSL"),
            JurisdictionTransferPolicy("CH", "US", "BLOCKED", "SwissBankingSecrecy"),
            JurisdictionTransferPolicy("UK", "US", "ALLOWED_UNRESTRICTED", "UK_GDPR_Adequacy")
        ])
        
        self.payload = TradeDataPayload(
            trade_id="TRD_9901", origin_country="CN",
            trader_id="QUANT_TRADER_42", client_name="John Doe Corp",
            account_number="1234567890", symbol="AAPL",
            quantity=100.0, price=150.0
        )

    def test_pii_anonymization_transfer(self):
        report = self.engine.process_data_transfer(self.payload, destination_country="US")
        
        self.assertTrue(report.transfer_approved)
        self.assertTrue(report.applied_anonymization)
        
        sanitized = report.sanitized_payload
        self.assertIsNotNone(sanitized)
        self.assertEqual(sanitized.client_name, "ANONYMOUS_CLIENT")
        self.assertTrue(sanitized.trader_id.startswith("TRD_HASH_"))
        self.assertEqual(sanitized.account_number, "XXXX-XXXX-7890")

    def test_blocked_cross_border_transfer(self):
        swiss_payload = TradeDataPayload(
            trade_id="TRD_9902", origin_country="CH",
            trader_id="SWISS_TRADER", client_name="Private Bank Client",
            account_number="CH998877", symbol="NESN",
            quantity=50.0, price=100.0
        )
        report = self.engine.process_data_transfer(swiss_payload, destination_country="US")
        
        self.assertFalse(report.transfer_approved)
        self.assertIsNone(report.sanitized_payload)
        self.assertIn("BLOCKED", report.audit_message)

    def test_unrestricted_transfer(self):
        uk_payload = TradeDataPayload(
            trade_id="TRD_9903", origin_country="UK",
            trader_id="UK_TRADER", client_name="London Capital",
            account_number="UK112233", symbol="BP",
            quantity=500.0, price=450.0
        )
        report = self.engine.process_data_transfer(uk_payload, destination_country="US")
        
        self.assertTrue(report.transfer_approved)
        self.assertFalse(report.applied_anonymization)

if __name__ == '__main__':
    unittest.main()

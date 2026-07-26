import unittest
from cross_chain_address_reuse_privacy_risk import (
    CrossChainAddressPrivacyAuditor, WalletAddressRecord, PrivacyRiskReport
)

class TestCrossChainAddressPrivacyAuditor(unittest.TestCase):

    def setUp(self):
        self.auditor = CrossChainAddressPrivacyAuditor(high_risk_threshold=70.0, total_tracked_chains=5)
        
        # Shared EVM address reused across 4 chains with 1 KYC link
        addr = "0x1234567890abcdef1234567890abcdef12345678"
        self.auditor.register_wallet(WalletAddressRecord("Ethereum", addr, "PUBKEY_01", False, "Bot_Eth"))
        self.auditor.register_wallet(WalletAddressRecord("Arbitrum", addr, "PUBKEY_01", False, "Bot_Arb"))
        self.auditor.register_wallet(WalletAddressRecord("Optimism", addr, "PUBKEY_01", False, "Bot_Op"))
        self.auditor.register_wallet(WalletAddressRecord("Polygon", addr, "PUBKEY_01", True, "Binance_Deposit"))

        # Isolated unique address
        self.auditor.register_wallet(WalletAddressRecord("Solana", "SolAddr111111111111111111", "PUBKEY_SOL", False, "Bot_Sol"))

    def test_high_privacy_risk_on_address_reuse_and_kyc(self):
        addr = "0x1234567890abcdef1234567890abcdef12345678"
        report = self.auditor.audit_address_privacy(addr)
        
        self.assertEqual(report.reused_chains_count, 4)
        self.assertTrue(report.is_kyc_contaminated)
        # Reuse score: 4/5 * 50 = 40.0 + 50.0 KYC = 90.0
        self.assertEqual(report.privacy_risk_score, 90.0)
        self.assertEqual(report.risk_level, "HIGH")
        self.assertEqual(len(report.remediation_actions), 2)

    def test_low_privacy_risk_on_isolated_address(self):
        report = self.auditor.audit_address_privacy("SolAddr111111111111111111")
        
        self.assertEqual(report.reused_chains_count, 1)
        self.assertFalse(report.is_kyc_contaminated)
        # Reuse score: 1/5 * 50 = 10.0
        self.assertEqual(report.privacy_risk_score, 10.0)
        self.assertEqual(report.risk_level, "LOW")

if __name__ == '__main__':
    unittest.main()

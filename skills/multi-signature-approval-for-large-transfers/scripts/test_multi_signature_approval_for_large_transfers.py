import unittest
from multi_signature_approval_for_large_transfers import (
    MultiSigApprovalEngine, MultiSigConfig, SignerApproval, TransferRequestPayload, MultiSigApprovalReport
)

class TestMultiSigApprovalEngine(unittest.TestCase):

    def setUp(self):
        self.config = MultiSigConfig(
            auto_approve_threshold_usd=10000.0,
            high_value_threshold_usd=100000.0,
            high_value_timelock_seconds=3600.0
        )
        self.engine = MultiSigApprovalEngine(self.config)

    def test_high_tier_multisig_and_timelock_approval(self):
        # $250k transfer -> High Tier (requires 3-of-5 signers + 3600s timelock)
        t0 = 1000.0
        t_now = t0 + 4000.0 # 4000s > 3600s timelock ok
        req = TransferRequestPayload(
            request_id="TX_LARGE_001",
            amount_usd=250000.0,
            source_wallet="0xTreasury",
            destination_address="0xExchangeVault",
            initiated_by="BOT_TRADER",
            creation_timestamp=t0
        )

        approvals = [
            SignerApproval("SIGNER_CFO", "CFO", t0 + 100),
            SignerApproval("SIGNER_RISK", "RISK_OFFICER", t0 + 200),
            SignerApproval("SIGNER_CIO", "CIO", t0 + 300),
        ]

        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=t_now)

        self.assertTrue(report.is_approved)
        self.assertEqual(report.status, "TRANSFER_APPROVED")
        self.assertEqual(report.risk_tier, "HIGH_MULTISIG_TIMELOCK")
        self.assertEqual(report.submitted_approvals_count, 3)
        self.assertTrue(report.timelock_satisfied)

    def test_timelock_pending_rejection(self):
        # High-tier transfer with quorum met but timelock not elapsed -> TIMELOCK_PENDING!
        t0 = 1000.0
        t_now = t0 + 500.0 # 500s < 3600s timelock pending
        req = TransferRequestPayload(
            request_id="TX_LARGE_002",
            amount_usd=500000.0,
            source_wallet="0xTreasury",
            destination_address="0xExchangeVault",
            initiated_by="BOT_TRADER",
            creation_timestamp=t0
        )

        approvals = [
            SignerApproval("SIGNER_CFO", "CFO", t0 + 100),
            SignerApproval("SIGNER_RISK", "RISK_OFFICER", t0 + 200),
            SignerApproval("SIGNER_CIO", "CIO", t0 + 300),
        ]

        report = self.engine.evaluate_transfer_approval(req, approvals, current_time=t_now)

        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "TIMELOCK_PENDING")

if __name__ == '__main__':
    unittest.main()

import unittest
from exchange_withdrawal_whitelist_enforcement import (
    ExchangeWithdrawalWhitelistEngine, WhitelistedAddressRecord, WithdrawalRequest, WithdrawalWhitelistAuditReport
)

class TestExchangeWithdrawalWhitelistEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeWithdrawalWhitelistEngine(default_cooloff_seconds=86_400.0)
        
        # Cold storage address added 48 hours ago (Cool-off = 24 hours -> Lock Expired)
        now = 1700000000.0
        self.engine.register_whitelisted_address(WhitelistedAddressRecord(
            address_id="ADDR_COLD_01", asset_symbol="BTC",
            destination_address="bc1q_cold_storage_address", label="Cold Storage Vault",
            added_timestamp_seconds=now - 172_800.0, cooloff_duration_seconds=86_400.0
        ))

        # Hot wallet address added 2 hours ago (Cool-off = 24 hours -> Lock Active!)
        self.engine.register_whitelisted_address(WhitelistedAddressRecord(
            address_id="ADDR_HOT_02", asset_symbol="BTC",
            destination_address="bc1q_new_hot_wallet", label="New Hot Wallet",
            added_timestamp_seconds=now - 7_200.0, cooloff_duration_seconds=86_400.0
        ))

    def test_withdrawal_approved_for_whitelisted_address(self):
        now = 1700000000.0
        req = WithdrawalRequest(
            request_id="REQ_WD_01", asset_symbol="BTC", amount=2.5,
            destination_address="bc1q_cold_storage_address", request_timestamp_seconds=now
        )
        report = self.engine.audit_withdrawal_request(req)
        
        self.assertTrue(report.is_withdrawal_approved)
        self.assertEqual(report.status, "WITHDRAWAL_APPROVED")
        self.assertTrue(report.is_address_whitelisted)

    def test_unauthorized_address_rejection(self):
        now = 1700000000.0
        req = WithdrawalRequest(
            request_id="REQ_WD_02", asset_symbol="BTC", amount=10.0,
            destination_address="bc1q_hacker_unregistered_address", request_timestamp_seconds=now
        )
        report = self.engine.audit_withdrawal_request(req)
        
        self.assertFalse(report.is_withdrawal_approved)
        self.assertEqual(report.status, "UNAUTHORIZED_ADDRESS_REJECTION")
        self.assertFalse(report.is_address_whitelisted)

    def test_cooloff_lock_active_rejection(self):
        now = 1700000000.0
        req = WithdrawalRequest(
            request_id="REQ_WD_03", asset_symbol="BTC", amount=1.0,
            destination_address="bc1q_new_hot_wallet", request_timestamp_seconds=now
        )
        report = self.engine.audit_withdrawal_request(req)
        
        self.assertFalse(report.is_withdrawal_approved)
        self.assertEqual(report.status, "COOLOFF_PERIOD_ACTIVE_REJECTION")
        self.assertGreater(report.remaining_cooloff_seconds, 0.0)

if __name__ == '__main__':
    unittest.main()

import unittest
import datetime
import time
from test_transaction_verification_before_large_transfers import (
    AssetConfig,
    VerificationConfig,
    TransferRequest,
    TransferVerificationEngine,
    VerificationStatus,
    RiskLevel,
    WhitelistError,
    VerificationError,
    TestTransactionPendingError,
    TestTransactionExpiredError,
)


class TestTransferVerificationEngine(unittest.TestCase):
    def setUp(self):
        self.v_config = VerificationConfig(
            large_transfer_threshold_usd=50_000.0,
            test_expiry_window_minutes=30,
            enforce_whitelisting=True,
            allow_bypass_for_whitelisted=False,
        )
        self.engine = TransferVerificationEngine(self.v_config)

        self.eth_config = AssetConfig(
            symbol="ETH",
            chain="ETHEREUM",
            decimals=18,
            min_confirmations=12,
            test_amount=0.001,
        )
        self.xrp_config = AssetConfig(
            symbol="XRP",
            chain="RIPPLE",
            decimals=6,
            min_confirmations=1,
            test_amount=1.0,
            requires_destination_tag=True,
        )
        self.engine.register_asset(self.eth_config)
        self.engine.register_asset(self.xrp_config)

        self.whitelist_addr = "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
        self.engine.add_to_whitelist(self.whitelist_addr)

    def test_unwhitelisted_address_raises_error(self):
        req = TransferRequest(
            request_id="REQ-001",
            asset_symbol="ETH",
            sender_address="0x1111111111111111111111111111111111111111",
            recipient_address="0xBAD0000000000000000000000000000000000000",
            amount=1.0,
            value_usd=3000.0,
        )
        with self.assertRaises(WhitelistError):
            self.engine.initiate_transfer_request(req)

    def test_small_transfer_authorized_immediately(self):
        req = TransferRequest(
            request_id="REQ-002",
            asset_symbol="ETH",
            sender_address="0x1111111111111111111111111111111111111111",
            recipient_address=self.whitelist_addr,
            amount=2.0,
            value_usd=6000.0,  # Below $50,000 USD
        )
        res = self.engine.initiate_transfer_request(req)
        self.assertTrue(res.is_approved)
        self.assertEqual(res.status, VerificationStatus.NOT_REQUIRED)

    def test_large_transfer_requires_test_transaction(self):
        req = TransferRequest(
            request_id="REQ-003",
            asset_symbol="ETH",
            sender_address="0x1111111111111111111111111111111111111111",
            recipient_address=self.whitelist_addr,
            amount=50.0,
            value_usd=150_000.0,  # Exceeds $50,000 USD
        )
        res = self.engine.initiate_transfer_request(req)
        self.assertFalse(res.is_approved)
        self.assertEqual(res.status, VerificationStatus.TEST_PENDING)
        self.assertEqual(res.risk_level, RiskLevel.HIGH)

    def test_destination_tag_missing_raises_error(self):
        self.engine.add_to_whitelist("rPvkhVAnSV9wAtx5fB5vjTXyZ3dB17PGLD")
        req = TransferRequest(
            request_id="REQ-004",
            asset_symbol="XRP",
            sender_address="rN7n7otQDd6FczFgLdSqtcsAUxDkw6fzRH",
            recipient_address="rPvkhVAnSV9wAtx5fB5vjTXyZ3dB17PGLD",
            amount=10_000.0,
            value_usd=60_000.0,
            destination_tag=None,  # Missing required destination tag
        )
        with self.assertRaises(VerificationError):
            self.engine.initiate_transfer_request(req)

    def test_full_test_transaction_lifecycle_and_authorization(self):
        req = TransferRequest(
            request_id="REQ-005",
            asset_symbol="ETH",
            sender_address="0x1111111111111111111111111111111111111111",
            recipient_address=self.whitelist_addr,
            amount=100.0,
            value_usd=300_000.0,
        )
        init_res = self.engine.initiate_transfer_request(req)
        self.assertEqual(init_res.status, VerificationStatus.TEST_PENDING)

        # 1. Record test transaction broadcast
        tx_hash = "0xabc123def4567890abcdef1234567890abcdef1234567890abcdef1234567890"
        test_tx = self.engine.record_test_transaction("REQ-005", tx_hash)
        self.assertEqual(test_tx.status, VerificationStatus.TEST_PENDING)

        # 2. Attempt authorization before confirmation -> expect TestTransactionPendingError
        with self.assertRaises(TestTransactionPendingError):
            self.engine.verify_and_authorize_large_transfer("REQ-005")

        # 3. Update confirmations (e.g. 5/12 blocks) -> still pending
        self.engine.update_test_confirmations("REQ-005", 5)
        with self.assertRaises(TestTransactionPendingError):
            self.engine.verify_and_authorize_large_transfer("REQ-005")

        # 4. Update confirmations (12/12 blocks) -> confirmed
        self.engine.update_test_confirmations("REQ-005", 12)
        self.assertEqual(self.engine.test_transactions["REQ-005"].status, VerificationStatus.TEST_CONFIRMED)

        # 5. Authorize primary transfer
        auth_res = self.engine.verify_and_authorize_large_transfer("REQ-005")
        self.assertTrue(auth_res.is_approved)
        self.assertEqual(auth_res.status, VerificationStatus.APPROVED)
        self.assertEqual(auth_res.test_tx_hash, tx_hash)

    def test_test_transaction_expiration(self):
        # Set small expiry window (0 minutes for instant timeout testing)
        short_config = VerificationConfig(
            large_transfer_threshold_usd=50_000.0,
            test_expiry_window_minutes=0,  # Immediate expiry
            enforce_whitelisting=True,
        )
        engine = TransferVerificationEngine(short_config)
        engine.register_asset(self.eth_config)
        engine.add_to_whitelist(self.whitelist_addr)

        req = TransferRequest(
            request_id="REQ-EXP",
            asset_symbol="ETH",
            sender_address="0x111",
            recipient_address=self.whitelist_addr,
            amount=50.0,
            value_usd=150_000.0,
        )
        engine.initiate_transfer_request(req)
        engine.record_test_transaction("REQ-EXP", "0x123")
        engine.update_test_confirmations("REQ-EXP", 12)

        # Manually backdate confirmed_at timestamp to simulate expired window
        engine.test_transactions["REQ-EXP"].confirmed_at = datetime.datetime.now(
            datetime.timezone.utc
        ) - datetime.timedelta(minutes=35)

        with self.assertRaises(TestTransactionExpiredError):
            engine.verify_and_authorize_large_transfer("REQ-EXP")


if __name__ == "__main__":
    unittest.main()


"""Unit tests for the large-transfer test-transaction verification gate.

The regression tests in ``TestSecurityRegressions`` each target a defect that made
the gate approve a transfer it should have refused. Every one of them fails against
the pre-fix implementation and passes against the current one.
"""
import datetime
import unittest

from test_transaction_verification_before_large_transfers import (
    AssetConfig,
    RiskLevel,
    TestTransactionExpiredError,
    TestTransactionMismatchError,
    TestTransactionPendingError,
    TransferRequest,
    TransferVerificationEngine,
    VerificationConfig,
    VerificationError,
    VerificationStatus,
    WhitelistError,
    canonicalize_address,
)

UTC = datetime.timezone.utc

# A checksummed EVM address and a Base58Check (P2PKH) Bitcoin address. The Bitcoin
# one matters: Base58Check is case-sensitive, so it must never be case-folded.
EVM_ADDR = "0x71C7656EC7ab88b098defB751B7401B5f6d8976F"
BTC_ADDR = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"
XRP_ADDR = "rPvkhVAnSV9wAtx5fB5vjTXyZ3dB17PGLD"


def _at(minute: float) -> datetime.datetime:
    """A fixed, timezone-aware instant offset by ``minute`` minutes.

    Tests drive the engine off an injected clock rather than the wall clock, so
    expiry behaviour is asserted deterministically instead of by mutating engine
    internals.
    """
    return datetime.datetime(2026, 3, 1, 12, 0, tzinfo=UTC) + datetime.timedelta(minutes=minute)


class _EngineFixture(unittest.TestCase):
    """Shared engine with ETH, BTC and XRP registered and one whitelisted EVM address."""

    def setUp(self):
        self.v_config = VerificationConfig(
            large_transfer_threshold_usd=50_000.0,
            test_expiry_window_minutes=30.0,
            enforce_whitelisting=True,
            allow_bypass_for_whitelisted=False,
        )
        self.engine = TransferVerificationEngine(self.v_config)

        self.eth_config = AssetConfig(
            symbol="ETH", chain="ETHEREUM", decimals=18,
            min_confirmations=12, test_amount=0.001,
        )
        self.btc_config = AssetConfig(
            symbol="BTC", chain="BITCOIN", decimals=8,
            min_confirmations=4, test_amount=0.0001,
        )
        self.xrp_config = AssetConfig(
            symbol="XRP", chain="RIPPLE", decimals=6,
            min_confirmations=1, test_amount=1.0, requires_destination_tag=True,
        )
        for cfg in (self.eth_config, self.btc_config, self.xrp_config):
            self.engine.register_asset(cfg)

        self.whitelist_addr = EVM_ADDR
        self.engine.add_to_whitelist(self.whitelist_addr)

    def _large_eth_request(self, request_id="REQ-L", value_usd=300_000.0):
        return TransferRequest(
            request_id=request_id,
            asset_symbol="ETH",
            sender_address="0x1111111111111111111111111111111111111111",
            recipient_address=self.whitelist_addr,
            amount=100.0,
            value_usd=value_usd,
        )

    def _drive_to_confirmed(self, request_id="REQ-L", tx_hash="0xdead", at=0.0):
        """Initiate, record a correctly bound test tx, and reach required depth."""
        self.engine.record_test_transaction(
            request_id, tx_hash,
            observed_recipient=self.whitelist_addr,
            observed_chain="ETHEREUM",
            observed_amount=0.001,
            now=_at(at),
        )
        self.engine.update_test_confirmations(request_id, 12, now=_at(at))


class TestPolicyGating(_EngineFixture):
    """Whitelist, threshold, destination-tag and lifecycle behaviour."""

    def test_unwhitelisted_address_raises_error(self):
        req = TransferRequest(
            request_id="REQ-001", asset_symbol="ETH",
            sender_address="0x1111111111111111111111111111111111111111",
            recipient_address="0xBAD0000000000000000000000000000000000000",
            amount=1.0, value_usd=3000.0,
        )
        with self.assertRaises(WhitelistError):
            self.engine.initiate_transfer_request(req)

    def test_small_transfer_authorized_immediately(self):
        req = TransferRequest(
            request_id="REQ-002", asset_symbol="ETH",
            sender_address="0x1111111111111111111111111111111111111111",
            recipient_address=self.whitelist_addr, amount=2.0, value_usd=6000.0,
        )
        res = self.engine.initiate_transfer_request(req)
        self.assertTrue(res.is_approved)
        self.assertEqual(res.status, VerificationStatus.NOT_REQUIRED)

    def test_large_transfer_requires_test_transaction(self):
        res = self.engine.initiate_transfer_request(self._large_eth_request("REQ-003"))
        self.assertFalse(res.is_approved)
        self.assertEqual(res.status, VerificationStatus.TEST_PENDING)
        self.assertEqual(res.risk_level, RiskLevel.HIGH)

    def test_transfer_exactly_at_threshold_is_large(self):
        """The threshold comparison is >=, so a transfer exactly at it is large."""
        res = self.engine.initiate_transfer_request(
            self._large_eth_request("REQ-EQ", value_usd=50_000.0))
        self.assertEqual(res.status, VerificationStatus.TEST_PENDING)

    def test_transfer_one_cent_below_threshold_is_not_large(self):
        res = self.engine.initiate_transfer_request(
            self._large_eth_request("REQ-LT", value_usd=49_999.99))
        self.assertEqual(res.status, VerificationStatus.NOT_REQUIRED)

    def test_destination_tag_missing_raises_error(self):
        self.engine.add_to_whitelist(XRP_ADDR)
        req = TransferRequest(
            request_id="REQ-004", asset_symbol="XRP",
            sender_address="rN7n7otQDd6FczFgLdSqtcsAUxDkw6fzRH",
            recipient_address=XRP_ADDR, amount=10_000.0, value_usd=60_000.0,
            destination_tag=None,
        )
        with self.assertRaises(VerificationError):
            self.engine.initiate_transfer_request(req)

    def test_whitespace_only_destination_tag_treated_as_missing(self):
        """An empty tag and a missing tag are the same uncredited deposit."""
        self.engine.add_to_whitelist(XRP_ADDR)
        req = TransferRequest(
            request_id="REQ-004B", asset_symbol="XRP",
            sender_address="rN7n7otQDd6FczFgLdSqtcsAUxDkw6fzRH",
            recipient_address=XRP_ADDR, amount=10_000.0, value_usd=60_000.0,
            destination_tag="   ",
        )
        with self.assertRaises(VerificationError):
            self.engine.initiate_transfer_request(req)

    def test_unregistered_asset_raises_error(self):
        req = TransferRequest(
            request_id="REQ-UNREG", asset_symbol="DOGE",
            sender_address="0x1111111111111111111111111111111111111111",
            recipient_address=self.whitelist_addr, amount=1.0, value_usd=100.0,
        )
        with self.assertRaises(VerificationError):
            self.engine.initiate_transfer_request(req)

    def test_full_test_transaction_lifecycle_and_authorization(self):
        init_res = self.engine.initiate_transfer_request(self._large_eth_request("REQ-005"))
        self.assertEqual(init_res.status, VerificationStatus.TEST_PENDING)

        tx_hash = "0xabc123def4567890abcdef1234567890abcdef1234567890abcdef1234567890"
        test_tx = self.engine.record_test_transaction(
            "REQ-005", tx_hash,
            observed_recipient=self.whitelist_addr,
            observed_chain="ETHEREUM", observed_amount=0.001, now=_at(0),
        )
        self.assertEqual(test_tx.status, VerificationStatus.TEST_PENDING)

        # Authorisation before confirmation.
        with self.assertRaises(TestTransactionPendingError):
            self.engine.verify_and_authorize_large_transfer("REQ-005", now=_at(1))

        # Partial depth (5/12).
        self.engine.update_test_confirmations("REQ-005", 5, now=_at(1))
        with self.assertRaises(TestTransactionPendingError):
            self.engine.verify_and_authorize_large_transfer("REQ-005", now=_at(2))

        # Required depth reached, but no counterparty receipt yet.
        self.engine.update_test_confirmations("REQ-005", 12, now=_at(3))
        self.assertEqual(
            self.engine.test_transactions["REQ-005"].status,
            VerificationStatus.TEST_CONFIRMED)
        with self.assertRaises(TestTransactionPendingError):
            self.engine.verify_and_authorize_large_transfer("REQ-005", now=_at(4))

        # Counterparty attests receipt out of band.
        self.engine.acknowledge_test_receipt(
            "REQ-005", attested_by="treasury-ops@counterparty",
            channel="approved-voice-callback", now=_at(5))
        self.assertEqual(
            self.engine.test_transactions["REQ-005"].status,
            VerificationStatus.RECEIPT_ACKNOWLEDGED)

        auth_res = self.engine.verify_and_authorize_large_transfer("REQ-005", now=_at(6))
        self.assertTrue(auth_res.is_approved)
        self.assertEqual(auth_res.status, VerificationStatus.APPROVED)
        self.assertEqual(auth_res.test_tx_hash, tx_hash)
        self.assertTrue(any("receipt attested by" in line for line in auth_res.audit_trail))

    def test_test_transaction_expiration(self):
        self.engine.initiate_transfer_request(self._large_eth_request("REQ-EXP"))
        self._drive_to_confirmed("REQ-EXP", "0x123", at=0.0)
        self.engine.acknowledge_test_receipt(
            "REQ-EXP", attested_by="ops", channel="voice", now=_at(0))

        # 30.1 minutes after confirmation, against a 30-minute window.
        with self.assertRaises(TestTransactionExpiredError):
            self.engine.verify_and_authorize_large_transfer("REQ-EXP", now=_at(30.1))
        self.assertEqual(
            self.engine.test_transactions["REQ-EXP"].status, VerificationStatus.EXPIRED)

    def test_authorization_exactly_at_window_boundary_is_allowed(self):
        """`elapsed > window` — the boundary itself is inside the window."""
        self.engine.initiate_transfer_request(self._large_eth_request("REQ-BND"))
        self._drive_to_confirmed("REQ-BND", "0xbnd", at=0.0)
        self.engine.acknowledge_test_receipt(
            "REQ-BND", attested_by="ops", channel="voice", now=_at(0))
        res = self.engine.verify_and_authorize_large_transfer("REQ-BND", now=_at(30.0))
        self.assertTrue(res.is_approved)

    def test_naive_datetime_rejected(self):
        self.engine.initiate_transfer_request(self._large_eth_request("REQ-NAIVE"))
        with self.assertRaises(VerificationError):
            self.engine.record_test_transaction(
                "REQ-NAIVE", "0xn", observed_recipient=self.whitelist_addr,
                observed_chain="ETHEREUM", observed_amount=0.001,
                now=datetime.datetime(2026, 3, 1, 12, 0),
            )

    def test_bypass_flag_approves_without_test_and_is_flagged_high_risk(self):
        cfg = VerificationConfig(
            large_transfer_threshold_usd=50_000.0, allow_bypass_for_whitelisted=True)
        engine = TransferVerificationEngine(cfg)
        engine.register_asset(self.eth_config)
        engine.add_to_whitelist(self.whitelist_addr)
        res = engine.initiate_transfer_request(self._large_eth_request("REQ-BYP"))
        self.assertTrue(res.is_approved)
        self.assertEqual(res.status, VerificationStatus.APPROVED)
        # A bypassed test transfer must not be reported as a medium-risk approval.
        self.assertEqual(res.risk_level, RiskLevel.HIGH)


class TestSecurityRegressions(_EngineFixture):
    """Each test here targets a defect that previously produced a false APPROVE."""

    def test_nan_notional_does_not_bypass_the_threshold(self):
        """`float('nan') >= threshold` is False, which classified NaN as 'small'."""
        with self.assertRaises(VerificationError):
            TransferRequest(
                request_id="REQ-NAN", asset_symbol="ETH",
                sender_address="0x1111111111111111111111111111111111111111",
                recipient_address=self.whitelist_addr,
                amount=500.0, value_usd=float("nan"),
            )
        with self.assertRaises(VerificationError):
            self.engine.is_large_transfer(float("nan"))

    def test_infinite_and_negative_notional_rejected(self):
        for bad in (float("inf"), -1.0):
            with self.subTest(value_usd=bad):
                with self.assertRaises(VerificationError):
                    TransferRequest(
                        request_id="REQ-BAD", asset_symbol="ETH",
                        sender_address="0x1111111111111111111111111111111111111111",
                        recipient_address=self.whitelist_addr,
                        amount=1.0, value_usd=bad,
                    )

    def test_non_positive_amount_rejected(self):
        with self.assertRaises(VerificationError):
            TransferRequest(
                request_id="REQ-ZERO", asset_symbol="ETH",
                sender_address="0x1111111111111111111111111111111111111111",
                recipient_address=self.whitelist_addr, amount=0.0, value_usd=100.0,
            )

    def test_base58_whitelist_is_case_sensitive(self):
        """Case-folding Base58Check collapsed distinct BTC addresses onto one key."""
        self.engine.add_to_whitelist(BTC_ADDR)
        self.assertTrue(self.engine.is_whitelisted(BTC_ADDR))
        self.assertFalse(self.engine.is_whitelisted(BTC_ADDR.lower()))
        self.assertFalse(self.engine.is_whitelisted(BTC_ADDR.upper()))

    def test_evm_whitelist_is_case_insensitive(self):
        """ERC-55 capitalisation is a checksum, not a distinct address."""
        self.assertTrue(self.engine.is_whitelisted(EVM_ADDR.lower()))
        self.assertTrue(self.engine.is_whitelisted(EVM_ADDR))

    def test_uppercase_bech32_matches_lowercase_entry(self):
        addr = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
        self.engine.add_to_whitelist(addr)
        self.assertTrue(self.engine.is_whitelisted(addr.upper()))

    def test_canonicalize_address_rejects_empty(self):
        for bad in ("", "   ", None, 123):
            with self.subTest(address=bad):
                with self.assertRaises(VerificationError):
                    canonicalize_address(bad)

    def test_test_transaction_must_land_at_the_request_recipient(self):
        """Previously any tx hash was accepted, so the test verified nothing."""
        self.engine.initiate_transfer_request(self._large_eth_request("REQ-BIND"))
        with self.assertRaises(TestTransactionMismatchError):
            self.engine.record_test_transaction(
                "REQ-BIND", "0xwrong",
                observed_recipient="0xDEAD000000000000000000000000000000000000",
                observed_chain="ETHEREUM", observed_amount=0.001, now=_at(0),
            )

    def test_test_transaction_must_be_on_the_configured_chain(self):
        """An approved address on the wrong network loses the funds."""
        self.engine.initiate_transfer_request(self._large_eth_request("REQ-CHAIN"))
        with self.assertRaises(TestTransactionMismatchError):
            self.engine.record_test_transaction(
                "REQ-CHAIN", "0xchain", observed_recipient=self.whitelist_addr,
                observed_chain="ARBITRUM", observed_amount=0.001, now=_at(0),
            )

    def test_test_transaction_amount_must_match_configured_dust(self):
        self.engine.initiate_transfer_request(self._large_eth_request("REQ-AMT"))
        with self.assertRaises(TestTransactionMismatchError):
            self.engine.record_test_transaction(
                "REQ-AMT", "0xamt", observed_recipient=self.whitelist_addr,
                observed_chain="ETHEREUM", observed_amount=0.5, now=_at(0),
            )

    def test_confirmation_polling_does_not_extend_the_expiry_window(self):
        """The window was previously restarted on every poll, so it never expired."""
        self.engine.initiate_transfer_request(self._large_eth_request("REQ-CLK"))
        self._drive_to_confirmed("REQ-CLK", "0xclk", at=0.0)
        latched = self.engine.test_transactions["REQ-CLK"].confirmed_at
        self.engine.acknowledge_test_receipt(
            "REQ-CLK", attested_by="ops", channel="voice", now=_at(0))

        # A monitoring loop keeps polling for the next 99 minutes.
        for minute in (10, 30, 60, 99):
            self.engine.update_test_confirmations("REQ-CLK", 12 + minute, now=_at(minute))
        self.assertEqual(self.engine.test_transactions["REQ-CLK"].confirmed_at, latched)

        with self.assertRaises(TestTransactionExpiredError):
            self.engine.verify_and_authorize_large_transfer("REQ-CLK", now=_at(99))

    def test_depth_regression_revokes_confirmation(self):
        """A re-org that drops the tx must reset the state, not leave it CONFIRMED."""
        self.engine.initiate_transfer_request(self._large_eth_request("REQ-REORG"))
        self._drive_to_confirmed("REQ-REORG", "0xreorg", at=0.0)
        self.engine.acknowledge_test_receipt(
            "REQ-REORG", attested_by="ops", channel="voice", now=_at(0))

        self.engine.update_test_confirmations("REQ-REORG", 0, now=_at(1))
        tx = self.engine.test_transactions["REQ-REORG"]
        self.assertEqual(tx.status, VerificationStatus.TEST_PENDING)
        self.assertIsNone(tx.confirmed_at)
        with self.assertRaises(TestTransactionPendingError):
            self.engine.verify_and_authorize_large_transfer("REQ-REORG", now=_at(2))

        # Re-confirmation restarts the window from the new confirmation, so a
        # transfer confirmed at t=0, re-orged, and re-confirmed at t=60 is live.
        self.engine.update_test_confirmations("REQ-REORG", 12, now=_at(60))
        res = self.engine.verify_and_authorize_large_transfer("REQ-REORG", now=_at(61))
        self.assertTrue(res.is_approved)

    def test_authorization_is_single_use(self):
        """One dust test previously authorised unlimited primary transfers."""
        self.engine.initiate_transfer_request(self._large_eth_request("REQ-ONCE"))
        self._drive_to_confirmed("REQ-ONCE", "0xonce", at=0.0)
        self.engine.acknowledge_test_receipt(
            "REQ-ONCE", attested_by="ops", channel="voice", now=_at(0))

        first = self.engine.verify_and_authorize_large_transfer("REQ-ONCE", now=_at(1))
        self.assertTrue(first.is_approved)
        with self.assertRaises(VerificationError):
            self.engine.verify_and_authorize_large_transfer("REQ-ONCE", now=_at(2))

    def test_consumed_request_id_cannot_be_reinitiated(self):
        self.engine.initiate_transfer_request(self._large_eth_request("REQ-REUSE"))
        self._drive_to_confirmed("REQ-REUSE", "0xreuse", at=0.0)
        self.engine.acknowledge_test_receipt(
            "REQ-REUSE", attested_by="ops", channel="voice", now=_at(0))
        self.engine.verify_and_authorize_large_transfer("REQ-REUSE", now=_at(1))
        with self.assertRaises(VerificationError):
            self.engine.initiate_transfer_request(self._large_eth_request("REQ-REUSE"))

    def test_whitelist_revocation_blocks_authorization_in_flight(self):
        """The whitelist was previously checked only at initiation."""
        self.engine.initiate_transfer_request(self._large_eth_request("REQ-REVOKE"))
        self._drive_to_confirmed("REQ-REVOKE", "0xrevoke", at=0.0)
        self.engine.acknowledge_test_receipt(
            "REQ-REVOKE", attested_by="ops", channel="voice", now=_at(0))

        self.assertTrue(self.engine.remove_from_whitelist(self.whitelist_addr))
        with self.assertRaises(WhitelistError):
            self.engine.verify_and_authorize_large_transfer("REQ-REVOKE", now=_at(1))

    def test_receipt_requirement_can_be_disabled_explicitly(self):
        """Depth-only mode still works, but only when opted into."""
        cfg = VerificationConfig(
            large_transfer_threshold_usd=50_000.0,
            test_expiry_window_minutes=30.0,
            require_counterparty_receipt=False,
        )
        engine = TransferVerificationEngine(cfg)
        engine.register_asset(self.eth_config)
        engine.add_to_whitelist(self.whitelist_addr)
        engine.initiate_transfer_request(self._large_eth_request("REQ-NORCPT"))
        engine.record_test_transaction(
            "REQ-NORCPT", "0xnorcpt", observed_recipient=self.whitelist_addr,
            observed_chain="ETHEREUM", observed_amount=0.001, now=_at(0))
        engine.update_test_confirmations("REQ-NORCPT", 12, now=_at(0))
        res = engine.verify_and_authorize_large_transfer("REQ-NORCPT", now=_at(1))
        self.assertTrue(res.is_approved)

    def test_backdated_clock_beyond_skew_tolerance_is_refused(self):
        """A confirmation timestamp in the future must not silently pass the window."""
        self.engine.initiate_transfer_request(self._large_eth_request("REQ-SKEW"))
        self._drive_to_confirmed("REQ-SKEW", "0xskew", at=0.0)
        self.engine.acknowledge_test_receipt(
            "REQ-SKEW", attested_by="ops", channel="voice", now=_at(0))
        with self.assertRaises(VerificationError) as ctx:
            self.engine.verify_and_authorize_large_transfer("REQ-SKEW", now=_at(-10))
        self.assertIn("clock", str(ctx.exception).lower())

    def test_resubmitting_an_id_cannot_inherit_a_test_for_another_recipient(self):
        """A verified test for address A must not authorise a transfer to address B."""
        other = "0x1234567890abcdef1234567890abcdef12345678"
        self.engine.add_to_whitelist(other)
        self.engine.initiate_transfer_request(self._large_eth_request("REQ-SWAP"))
        self._drive_to_confirmed("REQ-SWAP", "0xswap", at=0.0)
        self.engine.acknowledge_test_receipt(
            "REQ-SWAP", attested_by="ops", channel="voice", now=_at(0))

        self.engine.initiate_transfer_request(TransferRequest(
            request_id="REQ-SWAP", asset_symbol="ETH",
            sender_address="0x1111111111111111111111111111111111111111",
            recipient_address=other, amount=100.0, value_usd=300_000.0,
        ))
        with self.assertRaises(TestTransactionMismatchError):
            self.engine.verify_and_authorize_large_transfer("REQ-SWAP", now=_at(1))

    def test_zero_test_amount_rejected_at_asset_registration(self):
        """A zero-value test transfer confirms while moving nothing."""
        with self.assertRaises(VerificationError):
            AssetConfig(symbol="ETH", chain="ETHEREUM", test_amount=0.0)

    def test_zero_min_confirmations_rejected(self):
        with self.assertRaises(VerificationError):
            AssetConfig(symbol="ETH", chain="ETHEREUM", min_confirmations=0)


if __name__ == "__main__":
    unittest.main()

"""Unit tests for exchange-withdrawal-whitelist-enforcement."""
import logging
import unittest

from exchange_withdrawal_whitelist_enforcement import (
    COOLOFF_24H_SECONDS,
    COOLOFF_48H_SECONDS,
    COOLOFF_72H_SECONDS,
    STATUS_ADDRESS_REVOKED,
    STATUS_APPROVED,
    STATUS_COOLOFF_ACTIVE,
    STATUS_DESTINATION_TAG_MISMATCH,
    STATUS_KEY_NOT_IP_RESTRICTED,
    STATUS_KEY_WITHDRAWAL_DISABLED,
    STATUS_UNAUTHORIZED_ADDRESS,
    ExchangeWithdrawalWhitelistEngine,
    NetworkWithdrawalPolicy,
    WhitelistedAddressRecord,
    WithdrawalRequest,
    WithdrawalWhitelistError,
    canonicalize_address,
)

logging.getLogger("exchange_withdrawal_whitelist_enforcement").setLevel(logging.CRITICAL)

# A fixed trusted clock keeps every cool-off assertion deterministic.
NOW = 1_700_000_000.0
# Bech32 fixtures: the data part uses only the BIP-173 charset (no `b`, `i`, `o`, `1`).
COLD_BTC = "bc1qvaultcldstrage7t5h9adzscvqcx3s4npdz2gwsn"
HOT_BTC = "bc1qhtwalletnewdepsyt4v9z2xkc3sn7pgeaqzds5tw"
UNKNOWN_BTC = "bc1qattackeraddress0000000000000000000006v3xzk"


def make_engine(**kwargs) -> ExchangeWithdrawalWhitelistEngine:
    """Engine with the cold-storage entry unlocked and the hot entry mid-lock."""
    engine = ExchangeWithdrawalWhitelistEngine(**kwargs)
    engine.register_whitelisted_address(
        WhitelistedAddressRecord(
            address_id="ADDR_COLD_01", asset_symbol="BTC", network="BTC",
            destination_address=COLD_BTC, label="Cold Storage Vault",
            added_timestamp_seconds=NOW - 172_800.0,
            cooloff_duration_seconds=COOLOFF_24H_SECONDS,
        ),
        observed_at_seconds=NOW - 172_800.0,
    )
    engine.register_whitelisted_address(
        WhitelistedAddressRecord(
            address_id="ADDR_HOT_02", asset_symbol="BTC", network="BTC",
            destination_address=HOT_BTC, label="New Hot Wallet",
            added_timestamp_seconds=NOW - 7_200.0,
            cooloff_duration_seconds=COOLOFF_24H_SECONDS,
        ),
        observed_at_seconds=NOW - 7_200.0,
    )
    return engine


def make_request(**overrides) -> WithdrawalRequest:
    """A well-formed, fully permissioned request; override to introduce a flaw."""
    base = dict(
        request_id="REQ_WD_01", asset_symbol="BTC", network="BTC", amount=2.5,
        destination_address=COLD_BTC, request_timestamp_seconds=NOW,
        is_withdrawal_enabled_on_key=True, is_key_ip_restricted=True,
    )
    base.update(overrides)
    return WithdrawalRequest(**base)


class TestHappyPathAndCoreRejections(unittest.TestCase):

    def test_withdrawal_approved_for_unlocked_whitelisted_address(self):
        report = make_engine().audit_withdrawal_request(
            make_request(), evaluation_timestamp_seconds=NOW)
        self.assertTrue(report.is_withdrawal_approved)
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(report.is_address_whitelisted)
        self.assertTrue(report.is_cooloff_elapsed)
        self.assertEqual(report.matched_address_id, "ADDR_COLD_01")
        self.assertEqual(report.remaining_cooloff_seconds, 0.0)

    def test_unauthorized_address_rejection(self):
        report = make_engine().audit_withdrawal_request(
            make_request(request_id="REQ_WD_02",
                         destination_address=UNKNOWN_BTC),
            evaluation_timestamp_seconds=NOW)
        self.assertFalse(report.is_withdrawal_approved)
        self.assertEqual(report.status, STATUS_UNAUTHORIZED_ADDRESS)
        self.assertFalse(report.is_address_whitelisted)

    def test_cooloff_lock_active_rejection_reports_exact_remaining(self):
        report = make_engine().audit_withdrawal_request(
            make_request(request_id="REQ_WD_03", destination_address=HOT_BTC),
            evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_COOLOFF_ACTIVE)
        self.assertFalse(report.is_withdrawal_approved)
        self.assertTrue(report.is_address_whitelisted)
        # Added 7_200s ago under a 86_400s lock, so 79_200s remain.
        self.assertAlmostEqual(report.remaining_cooloff_seconds, 79_200.0)
        self.assertAlmostEqual(report.unlock_timestamp_seconds, NOW - 7_200.0 + 86_400.0)


class TestApiKeyGate(unittest.TestCase):

    def test_withdrawal_scope_defaults_to_denied(self):
        """Regression: the scope flag used to default to True, silently authorising
        any request that omitted it."""
        req = WithdrawalRequest(
            request_id="REQ_DEFAULT", asset_symbol="BTC", network="BTC", amount=1.0,
            destination_address=COLD_BTC, request_timestamp_seconds=NOW)
        self.assertFalse(req.is_withdrawal_enabled_on_key)
        self.assertFalse(req.is_key_ip_restricted)
        report = make_engine().audit_withdrawal_request(
            req, evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_KEY_WITHDRAWAL_DISABLED)
        self.assertFalse(report.is_withdrawal_approved)

    def test_key_rejection_does_not_assert_an_unevaluated_allowlist_result(self):
        report = make_engine().audit_withdrawal_request(
            make_request(is_withdrawal_enabled_on_key=False),
            evaluation_timestamp_seconds=NOW)
        # The address *is* whitelisted; the check simply never ran.
        self.assertIsNone(report.is_address_whitelisted)
        self.assertIsNone(report.remaining_cooloff_seconds)
        self.assertNotIn("allowlist_membership", report.checks_evaluated)

    def test_withdrawal_capable_key_without_ip_restriction_is_rejected(self):
        report = make_engine().audit_withdrawal_request(
            make_request(is_key_ip_restricted=False), evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_KEY_NOT_IP_RESTRICTED)
        self.assertFalse(report.is_withdrawal_approved)

    def test_ip_restriction_check_is_configurable(self):
        engine = make_engine(require_ip_restricted_key=False)
        report = engine.audit_withdrawal_request(
            make_request(is_key_ip_restricted=False), evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertNotIn("api_key_ip_restriction", report.checks_evaluated)


class TestNetworkScoping(unittest.TestCase):

    def test_same_address_on_another_network_is_not_authorised(self):
        """An EVM address whitelisted for USDT on ETH must not authorise a transfer
        routed over BSC, where the destination may not be able to receive."""
        engine = ExchangeWithdrawalWhitelistEngine()
        evm = "0x52908400098527886E0F7030069857D2E4169EE7"
        engine.register_whitelisted_address(
            WhitelistedAddressRecord(
                address_id="ADDR_ETH", asset_symbol="USDT", network="ETH",
                destination_address=evm, label="Treasury (ERC-20)",
                added_timestamp_seconds=NOW - 172_800.0),
            observed_at_seconds=NOW - 172_800.0)

        on_eth = engine.audit_withdrawal_request(
            make_request(asset_symbol="USDT", network="ETH", destination_address=evm),
            evaluation_timestamp_seconds=NOW)
        on_bsc = engine.audit_withdrawal_request(
            make_request(asset_symbol="USDT", network="BSC", destination_address=evm),
            evaluation_timestamp_seconds=NOW)

        self.assertEqual(on_eth.status, STATUS_APPROVED)
        self.assertEqual(on_bsc.status, STATUS_UNAUTHORIZED_ADDRESS)

    def test_same_address_under_another_asset_is_not_authorised(self):
        engine = make_engine()
        report = engine.audit_withdrawal_request(
            make_request(asset_symbol="LTC"), evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_UNAUTHORIZED_ADDRESS)


class TestAddressCanonicalization(unittest.TestCase):

    def test_evm_checksummed_request_matches_lowercase_allowlist_entry(self):
        """ERC-55 capitalisation is a checksum, not part of the address, so a
        checksummed request must match a lowercase entry rather than false-reject."""
        engine = ExchangeWithdrawalWhitelistEngine()
        lower = "0x52908400098527886e0f7030069857d2e4169ee7"
        checksummed = "0x52908400098527886E0F7030069857D2E4169EE7"
        engine.register_whitelisted_address(
            WhitelistedAddressRecord(
                address_id="ADDR_EVM", asset_symbol="ETH", network="ETH",
                destination_address=lower, label="Treasury",
                added_timestamp_seconds=NOW - 172_800.0),
            observed_at_seconds=NOW - 172_800.0)
        report = engine.audit_withdrawal_request(
            make_request(asset_symbol="ETH", network="ETH",
                         destination_address=checksummed),
            evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_APPROVED)

    def test_uppercase_bech32_is_the_same_address_as_lowercase(self):
        # BIP-173 forbids mixed case and treats the all-uppercase form (QR codes)
        # as the same address.
        self.assertEqual(canonicalize_address(COLD_BTC.upper()), COLD_BTC)

    def test_base58_case_is_never_folded(self):
        """Base58Check is case-sensitive; folding would map distinct address strings
        onto one allowlist key."""
        base58 = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"
        self.assertEqual(canonicalize_address(base58), base58)
        self.assertNotEqual(canonicalize_address(base58),
                            canonicalize_address(base58.lower()))

    def test_case_variant_of_base58_entry_does_not_match(self):
        engine = ExchangeWithdrawalWhitelistEngine()
        base58 = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"
        engine.register_whitelisted_address(
            WhitelistedAddressRecord(
                address_id="ADDR_LEGACY", asset_symbol="BTC", network="BTC",
                destination_address=base58, label="Legacy Vault",
                added_timestamp_seconds=NOW - 172_800.0),
            observed_at_seconds=NOW - 172_800.0)
        report = engine.audit_withdrawal_request(
            make_request(destination_address=base58.lower()),
            evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_UNAUTHORIZED_ADDRESS)


class TestCooloffIntegrity(unittest.TestCase):

    def test_future_request_timestamp_cannot_unlock_the_cooloff(self):
        """Regression: the cool-off used to be measured against the request's own
        timestamp, so a request claiming a far-future time was approved."""
        report = make_engine().audit_withdrawal_request(
            make_request(destination_address=HOT_BTC,
                         request_timestamp_seconds=NOW + 10 * COOLOFF_72H_SECONDS),
            evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_COOLOFF_ACTIVE)
        self.assertTrue(any("trusted evaluation clock" in w for w in report.warnings))

    def test_reregistering_with_an_older_timestamp_cannot_clear_an_active_lock(self):
        """Regression: registration used to overwrite, so back-dating an in-cool-off
        entry cleared its lock in a single call."""
        engine = make_engine()
        engine.register_whitelisted_address(
            WhitelistedAddressRecord(
                address_id="ADDR_HOT_02", asset_symbol="BTC", network="BTC",
                destination_address=HOT_BTC, label="New Hot Wallet",
                added_timestamp_seconds=NOW - 10 * COOLOFF_72H_SECONDS),
            observed_at_seconds=NOW)
        report = engine.audit_withdrawal_request(
            make_request(destination_address=HOT_BTC), evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_COOLOFF_ACTIVE)
        self.assertAlmostEqual(report.remaining_cooloff_seconds, 79_200.0)

    def test_reregistering_with_a_shorter_cooloff_cannot_shorten_an_active_lock(self):
        engine = make_engine()
        engine.register_whitelisted_address(
            WhitelistedAddressRecord(
                address_id="ADDR_HOT_02", asset_symbol="BTC", network="BTC",
                destination_address=HOT_BTC, label="New Hot Wallet",
                added_timestamp_seconds=NOW - 7_200.0, cooloff_duration_seconds=0.0),
            observed_at_seconds=NOW)
        report = engine.audit_withdrawal_request(
            make_request(destination_address=HOT_BTC), evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_COOLOFF_ACTIVE)

    def test_zero_cooloff_record_is_raised_to_the_engine_floor(self):
        """A record carrying cooloff_duration_seconds=0 must not disable the control."""
        engine = ExchangeWithdrawalWhitelistEngine(
            minimum_cooloff_seconds=COOLOFF_48H_SECONDS)
        engine.register_whitelisted_address(
            WhitelistedAddressRecord(
                address_id="ADDR_NO_LOCK", asset_symbol="BTC", network="BTC",
                destination_address=COLD_BTC, label="Vault",
                added_timestamp_seconds=NOW, cooloff_duration_seconds=0.0),
            observed_at_seconds=NOW)
        report = engine.audit_withdrawal_request(
            make_request(), evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_COOLOFF_ACTIVE)
        self.assertAlmostEqual(report.remaining_cooloff_seconds, COOLOFF_48H_SECONDS)

    def test_record_cooloff_longer_than_the_floor_is_honoured(self):
        engine = ExchangeWithdrawalWhitelistEngine(
            minimum_cooloff_seconds=COOLOFF_24H_SECONDS)
        engine.register_whitelisted_address(
            WhitelistedAddressRecord(
                address_id="ADDR_72H", asset_symbol="BTC", network="BTC",
                destination_address=COLD_BTC, label="Vault",
                added_timestamp_seconds=NOW - COOLOFF_48H_SECONDS,
                cooloff_duration_seconds=COOLOFF_72H_SECONDS),
            observed_at_seconds=NOW - COOLOFF_48H_SECONDS)
        report = engine.audit_withdrawal_request(
            make_request(), evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_COOLOFF_ACTIVE)
        self.assertAlmostEqual(report.remaining_cooloff_seconds, COOLOFF_24H_SECONDS)

    def test_unlock_boundary_is_inclusive(self):
        engine = make_engine()
        unlock_at = NOW - 7_200.0 + COOLOFF_24H_SECONDS
        just_before = engine.audit_withdrawal_request(
            make_request(destination_address=HOT_BTC),
            evaluation_timestamp_seconds=unlock_at - 1.0)
        exactly_at = engine.audit_withdrawal_request(
            make_request(destination_address=HOT_BTC),
            evaluation_timestamp_seconds=unlock_at)
        self.assertEqual(just_before.status, STATUS_COOLOFF_ACTIVE)
        self.assertEqual(exactly_at.status, STATUS_APPROVED)


class TestRevocation(unittest.TestCase):

    def test_revoked_address_is_rejected_with_a_distinct_status(self):
        engine = make_engine()
        self.assertTrue(engine.revoke_whitelisted_address(
            "BTC", "BTC", COLD_BTC, observed_at_seconds=NOW))
        report = engine.audit_withdrawal_request(
            make_request(), evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_ADDRESS_REVOKED)
        self.assertEqual(report.matched_address_id, "ADDR_COLD_01")

    def test_revoking_an_unknown_address_is_a_no_op(self):
        engine = make_engine()
        self.assertFalse(engine.revoke_whitelisted_address(
            "BTC", "BTC", UNKNOWN_BTC, observed_at_seconds=NOW))

    def test_readding_a_revoked_address_serves_a_fresh_cooloff(self):
        """A revoked-then-re-added entry must not inherit its original unlock time,
        even if the submitted added_timestamp is the original one."""
        engine = make_engine()
        engine.revoke_whitelisted_address("BTC", "BTC", COLD_BTC, observed_at_seconds=NOW)
        engine.register_whitelisted_address(
            WhitelistedAddressRecord(
                address_id="ADDR_COLD_01", asset_symbol="BTC", network="BTC",
                destination_address=COLD_BTC, label="Cold Storage Vault",
                added_timestamp_seconds=NOW - 172_800.0),
            observed_at_seconds=NOW)
        report = engine.audit_withdrawal_request(
            make_request(), evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_COOLOFF_ACTIVE)
        self.assertAlmostEqual(report.remaining_cooloff_seconds, COOLOFF_24H_SECONDS)


class TestDestinationTagBinding(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeWithdrawalWhitelistEngine()
        self.engine.register_network_policy(NetworkWithdrawalPolicy(
            asset_symbol="XRP", network="XRP", requires_destination_tag=True,
            memo_regex=r"^[0-9]{1,10}$"))
        self.engine.register_whitelisted_address(
            WhitelistedAddressRecord(
                address_id="ADDR_XRP", asset_symbol="XRP", network="XRP",
                destination_address="rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh",
                label="Exchange Deposit", added_timestamp_seconds=NOW - 172_800.0,
                destination_tag="1234567"),
            observed_at_seconds=NOW - 172_800.0)

    def _audit(self, **overrides):
        return self.engine.audit_withdrawal_request(
            make_request(asset_symbol="XRP", network="XRP",
                         destination_address="rEb8TK3gBgk5auZkwc6sHnwrGVJH8DuaLh",
                         **overrides),
            evaluation_timestamp_seconds=NOW)

    def test_matching_tag_is_approved(self):
        self.assertEqual(self._audit(destination_tag="1234567").status, STATUS_APPROVED)

    def test_substituted_tag_is_rejected(self):
        report = self._audit(destination_tag="7654321")
        self.assertEqual(report.status, STATUS_DESTINATION_TAG_MISMATCH)
        self.assertTrue(report.is_address_whitelisted)

    def test_dropped_tag_is_rejected(self):
        self.assertEqual(self._audit(destination_tag=None).status,
                         STATUS_DESTINATION_TAG_MISMATCH)

    def test_whitespace_only_tag_counts_as_absent(self):
        self.assertEqual(self._audit(destination_tag="   ").status,
                         STATUS_DESTINATION_TAG_MISMATCH)

    def test_unexpected_tag_on_a_tagless_entry_is_rejected(self):
        report = make_engine().audit_withdrawal_request(
            make_request(destination_tag="99"), evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_DESTINATION_TAG_MISMATCH)

    def test_registering_a_tagless_record_on_a_tag_required_network_raises(self):
        with self.assertRaises(WithdrawalWhitelistError):
            self.engine.register_whitelisted_address(
                WhitelistedAddressRecord(
                    address_id="ADDR_XRP_2", asset_symbol="XRP", network="XRP",
                    destination_address="rLHzPsX6oXkzU2qL12kHCH8G8cnZv1rBJh",
                    label="No Memo", added_timestamp_seconds=NOW),
                observed_at_seconds=NOW)

    def test_registering_a_malformed_memo_raises(self):
        with self.assertRaises(WithdrawalWhitelistError):
            self.engine.register_whitelisted_address(
                WhitelistedAddressRecord(
                    address_id="ADDR_XRP_3", asset_symbol="XRP", network="XRP",
                    destination_address="rLHzPsX6oXkzU2qL12kHCH8G8cnZv1rBJh",
                    label="Bad Memo", added_timestamp_seconds=NOW,
                    destination_tag="not-a-number"),
                observed_at_seconds=NOW)


class TestNetworkPolicyValidation(unittest.TestCase):

    def test_address_failing_the_venue_regex_is_rejected_at_registration(self):
        engine = ExchangeWithdrawalWhitelistEngine()
        engine.register_network_policy(NetworkWithdrawalPolicy(
            asset_symbol="USDT", network="ETH",
            address_regex=r"^(0x)[0-9a-fA-F]{40}$"))
        with self.assertRaises(WithdrawalWhitelistError):
            engine.register_whitelisted_address(
                WhitelistedAddressRecord(
                    address_id="ADDR_BAD", asset_symbol="USDT", network="ETH",
                    destination_address="0xdeadbeef", label="Truncated",
                    added_timestamp_seconds=NOW),
                observed_at_seconds=NOW)

    def test_missing_policy_is_surfaced_as_a_warning_not_a_silent_pass(self):
        report = make_engine().audit_withdrawal_request(
            make_request(), evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_APPROVED)
        self.assertTrue(any("No NetworkWithdrawalPolicy" in w for w in report.warnings))

    def test_invalid_regex_in_a_policy_raises(self):
        engine = ExchangeWithdrawalWhitelistEngine()
        with self.assertRaises(WithdrawalWhitelistError):
            engine.register_network_policy(NetworkWithdrawalPolicy(
                asset_symbol="BTC", network="BTC", address_regex="([unclosed"))


class TestInputValidation(unittest.TestCase):

    def test_nan_amount_raises_instead_of_being_approved(self):
        """Regression: NaN silently propagated through the float comparisons and
        fell through to WITHDRAWAL_APPROVED."""
        with self.assertRaises(WithdrawalWhitelistError):
            make_engine().audit_withdrawal_request(
                make_request(amount=float("nan")), evaluation_timestamp_seconds=NOW)

    def test_nan_evaluation_timestamp_raises(self):
        with self.assertRaises(WithdrawalWhitelistError):
            make_engine().audit_withdrawal_request(
                make_request(destination_address=HOT_BTC),
                evaluation_timestamp_seconds=float("nan"))

    def test_nan_added_timestamp_raises_at_registration(self):
        with self.assertRaises(WithdrawalWhitelistError):
            ExchangeWithdrawalWhitelistEngine().register_whitelisted_address(
                WhitelistedAddressRecord(
                    address_id="ADDR_NAN", asset_symbol="BTC", network="BTC",
                    destination_address=COLD_BTC, label="Vault",
                    added_timestamp_seconds=float("nan")),
                observed_at_seconds=NOW)

    def test_non_positive_and_infinite_amounts_raise(self):
        for bad in (0.0, -1.0, float("inf")):
            with self.subTest(amount=bad), self.assertRaises(WithdrawalWhitelistError):
                make_engine().audit_withdrawal_request(
                    make_request(amount=bad), evaluation_timestamp_seconds=NOW)

    def test_boolean_amount_is_rejected(self):
        with self.assertRaises(WithdrawalWhitelistError):
            make_engine().audit_withdrawal_request(
                make_request(amount=True), evaluation_timestamp_seconds=NOW)

    def test_blank_address_and_blank_identifiers_raise(self):
        for overrides in ({"destination_address": "   "}, {"request_id": " "},
                          {"asset_symbol": ""}, {"network": ""}):
            with self.subTest(**overrides), self.assertRaises(WithdrawalWhitelistError):
                make_engine().audit_withdrawal_request(
                    make_request(**overrides), evaluation_timestamp_seconds=NOW)

    def test_negative_cooloff_on_a_record_raises(self):
        with self.assertRaises(WithdrawalWhitelistError):
            ExchangeWithdrawalWhitelistEngine().register_whitelisted_address(
                WhitelistedAddressRecord(
                    address_id="ADDR_NEG", asset_symbol="BTC", network="BTC",
                    destination_address=COLD_BTC, label="Vault",
                    added_timestamp_seconds=NOW, cooloff_duration_seconds=-1.0),
                observed_at_seconds=NOW)

    def test_asset_and_network_matching_is_case_insensitive(self):
        report = make_engine().audit_withdrawal_request(
            make_request(asset_symbol="btc", network="btc"),
            evaluation_timestamp_seconds=NOW)
        self.assertEqual(report.status, STATUS_APPROVED)


if __name__ == "__main__":
    unittest.main()

"""
Unit tests for crypto-wallet-key-custody-security skill.

The controlling property under test is FAIL-CLOSED behaviour: unrecognized,
malformed or unattributed input must produce a finding, never a silent pass.
Several tests are regressions against real fail-open defects and use the actual
permission vocabulary published by Binance, Coinbase and Kraken.
"""
import unittest

from key_permission_audit import (
    AuditFinding,
    AuditSummary,
    KeyCustodySecurityAuditor,
    RiskLevel,
    StorageBackend,
    audit_key_permissions,
    check_hot_balance_ratio,
    is_funds_moving_permission,
    normalize_address,
    normalize_permission,
)

SECURE_BASE = {"ip_whitelisted": True, "storage_backend": StorageBackend.AWS_KMS}


class TestCryptoKeyCustodySecurity(unittest.TestCase):

    def setUp(self):
        self.alerts = []

        def mock_alert(msg):
            self.alerts.append(msg)

        self.auditor = KeyCustodySecurityAuditor(max_hot_ratio=0.15, alert_fn=mock_alert)

    def test_audit_key_permissions_and_scoping(self):
        key_cfg = {
            "name": "binance_bot_key",
            "used_by": "trading_bot",
            "permissions": ["READ", "TRADE", "WITHDRAW"],
            "ip_whitelisted": False,
            "storage_backend": StorageBackend.PLAINTEXT_FILE,
        }
        findings = self.auditor.audit_key_config(key_cfg)
        self.assertEqual(len(findings), 3)
        levels = [f.risk_level for f in findings]
        self.assertIn(RiskLevel.CRITICAL, levels)
        self.assertIn(RiskLevel.HIGH, levels)

    def test_secure_key_config_passes(self):
        secure_key = {
            "name": "secure_trade_key",
            "used_by": "trading_bot",
            "permissions": ["READ", "TRADE"],
            **SECURE_BASE,
        }
        self.assertEqual(self.auditor.audit_key_config(secure_key), [])
        self.assertTrue(self.auditor.summary().passed)

    def test_hot_cold_allocation_check(self):
        is_safe, ratio, finding = self.auditor.evaluate_hot_cold_allocation(
            hot_balance=25_000, total_balance=100_000
        )
        self.assertFalse(is_safe)
        self.assertEqual(ratio, 0.25)
        self.assertIsInstance(finding, AuditFinding)
        self.assertEqual(len(self.alerts), 1)

    def test_outbound_transfer_monitoring(self):
        whitelist = {"0x1234567890abcdef1234567890abcdef12345678"}
        self.assertTrue(self.auditor.audit_outbound_transfer(
            "0x1234567890abcdef1234567890abcdef12345678", 5.0, whitelist))
        self.assertFalse(self.auditor.audit_outbound_transfer(
            "0x000000000000000000000000000000000000dead", 10.0, whitelist))

    def test_backward_compatibility(self):
        key_configs = [
            {"name": "bot_key", "used_by": "trading_bot", "permissions": ["withdraw", "trade"]}
        ]
        messages = audit_key_permissions(key_configs)
        self.assertEqual(len(messages), 1)
        self.assertIn("withdraw permission", messages[0])

        is_ok, ratio = check_hot_balance_ratio(10, 100, 0.15)
        self.assertTrue(is_ok)
        self.assertAlmostEqual(ratio, 0.1)


class TestFundsMovingPermissionVocabulary(unittest.TestCase):
    """Regression: the audit matched the literal 'withdraw' and so missed every
    real exchange's actual permission name."""

    def setUp(self):
        self.auditor = KeyCustodySecurityAuditor(alert_fn=lambda m: None)

    def test_real_exchange_permission_names_are_detected(self):
        # Binance Get API Key Permission response fields.
        for name in ("enableWithdrawals", "enableInternalTransfer", "permitsUniversalTransfer"):
            self.assertTrue(is_funds_moving_permission(name), name)
        # Coinbase Advanced Trade Get API Key Permissions response field.
        self.assertTrue(is_funds_moving_permission("can_transfer"))
        # Kraken API key permission label.
        self.assertTrue(is_funds_moving_permission("Withdraw Funds"))

    def test_non_funds_moving_permissions_are_not_flagged(self):
        for name in ("enableReading", "enableSpotAndMarginTrading", "enableFutures",
                     "can_view", "can_trade", "Query Funds", "Create & Modify Orders",
                     "Cancel/Close Orders", "Deposit Funds", "READ", "TRADE"):
            self.assertFalse(is_funds_moving_permission(name), name)

    def test_binance_style_bot_key_is_flagged_critical(self):
        # Regression: this exact config produced ZERO findings before the fix.
        findings = self.auditor.audit_key_config({
            "name": "binance_key",
            "used_by": "trading_bot",
            "permissions": ["enableReading", "enableSpotAndMarginTrading", "enableWithdrawals"],
            **SECURE_BASE,
        })
        self.assertEqual([f.risk_level for f in findings], [RiskLevel.CRITICAL])
        self.assertIn("enableWithdrawals", findings[0].issue)

    def test_normalize_permission_rejects_non_strings(self):
        for bad in (None, 123, object()):
            with self.assertRaises(TypeError):
                normalize_permission(bad)

    def test_normalize_permission_collapses_separators(self):
        self.assertEqual(normalize_permission("Withdraw Funds"), "withdrawfunds")
        self.assertEqual(normalize_permission("can_transfer"), "cantransfer")
        self.assertEqual(normalize_permission("  ENABLE-Withdrawals "), "enablewithdrawals")


class TestKeyAttributionFailsClosed(unittest.TestCase):
    """Regression: the CRITICAL check required used_by == 'trading_bot' exactly, so
    every other spelling -- including omitting the field -- passed clean."""

    def setUp(self):
        self.auditor = KeyCustodySecurityAuditor(alert_fn=lambda m: None)

    def _audit(self, used_by):
        cfg = {"name": "k", "permissions": ["WITHDRAW"], **SECURE_BASE}
        if used_by is not None:
            cfg["used_by"] = used_by
        return KeyCustodySecurityAuditor(alert_fn=lambda m: None).audit_key_config(cfg)

    def test_automated_actor_spellings_all_flag_critical(self):
        for used_by in ("trading_bot", "trading-bot", "Trading_Bot", "bot",
                        "strategy_engine", "execution_service", "algo_trader"):
            findings = self._audit(used_by)
            self.assertEqual([f.risk_level for f in findings], [RiskLevel.CRITICAL], used_by)

    def test_unattributed_key_flags_critical(self):
        # The most dangerous original case: omit used_by entirely -> previously clean.
        for used_by in (None, "", "unknown", "unspecified"):
            findings = self._audit(used_by)
            self.assertEqual([f.risk_level for f in findings], [RiskLevel.CRITICAL], repr(used_by))
            self.assertIn("unattributed", findings[0].issue.lower())

    def test_human_gated_key_still_reported_as_high(self):
        # A withdrawal key must never audit clean, even when correctly attributed.
        findings = self._audit("treasury_officer_manual")
        self.assertEqual([f.risk_level for f in findings], [RiskLevel.HIGH])

    def test_bare_string_permissions_rejected(self):
        # Regression: a bare string iterates as characters and matched nothing.
        with self.assertRaises(TypeError):
            self.auditor.audit_key_config(
                {"name": "k", "used_by": "trading_bot", "permissions": "withdraw", **SECURE_BASE})

    def test_malformed_permissions_rejected(self):
        for perms in (None, [None], [123]):
            with self.assertRaises(TypeError):
                self.auditor.audit_key_config(
                    {"name": "k", "used_by": "bot", "permissions": perms, **SECURE_BASE})


class TestStorageBackendAllowlist(unittest.TestCase):
    """Regression: a denylist of insecure backends passed everything it did not
    recognize, including '', 'unknown' and '.env'."""

    def _findings(self, backend):
        return KeyCustodySecurityAuditor(alert_fn=lambda m: None).audit_key_config({
            "name": "k", "used_by": "trading_bot", "permissions": ["READ"],
            "ip_whitelisted": True, "storage_backend": backend,
        })

    def test_secure_backends_pass(self):
        for backend in (StorageBackend.AWS_KMS, StorageBackend.GCP_KMS,
                        StorageBackend.AZURE_KEY_VAULT, StorageBackend.HASHICORP_VAULT,
                        StorageBackend.HARDWARE_HSM, "aws_kms", "hardware_hsm"):
            self.assertEqual(self._findings(backend), [], backend)

    def test_known_insecure_backends_flagged(self):
        # Case/separator variants normalize onto the same token, so they are
        # recognised as the known-insecure backend rather than merely unrecognised.
        for backend in (StorageBackend.PLAINTEXT_FILE, StorageBackend.ENV_VARIABLE,
                        "ENV_VARIABLE", "Plaintext File"):
            findings = self._findings(backend)
            self.assertEqual(len(findings), 1, backend)
            self.assertIn("insecure backend", findings[0].issue)

    def test_unrecognized_backends_flagged_not_passed(self):
        for backend in (".env", "dotenv", "env", "config.yaml",
                        "unknown", "typo_kms", 12345):
            findings = self._findings(backend)
            self.assertEqual(len(findings), 1, backend)
            self.assertIn("Unrecognized storage backend", findings[0].issue)

    def test_missing_backend_flagged(self):
        for backend in (None, ""):
            findings = self._findings(backend)
            self.assertEqual(len(findings), 1, repr(backend))
            self.assertIn("No storage backend declared", findings[0].issue)

    def test_gcp_and_azure_are_recognized(self):
        # references/standards.md lists these as covered; the enum previously omitted them.
        self.assertEqual(StorageBackend.GCP_KMS.value, "gcp_kms")
        self.assertEqual(StorageBackend.AZURE_KEY_VAULT.value, "azure_key_vault")


class TestIpWhitelisting(unittest.TestCase):

    def _findings(self, value):
        cfg = {"name": "k", "used_by": "trading_bot", "permissions": ["READ"],
               "storage_backend": StorageBackend.AWS_KMS}
        if value is not None:
            cfg["ip_whitelisted"] = value
        return KeyCustodySecurityAuditor(alert_fn=lambda m: None).audit_key_config(cfg)

    def test_missing_or_falsey_ip_whitelist_flagged(self):
        for value in (None, False, "", 0):
            self.assertEqual(len(self._findings(value)), 1, repr(value))

    def test_truthy_non_true_values_do_not_satisfy_the_check(self):
        # "yes" is not evidence of an IP allowlist; require an explicit True.
        for value in ("yes", 1, "true"):
            self.assertEqual(len(self._findings(value)), 1, repr(value))

    def test_explicit_true_passes(self):
        self.assertEqual(self._findings(True), [])


class TestHotColdAllocationFailsClosed(unittest.TestCase):

    def setUp(self):
        self.alerts = []
        self.auditor = KeyCustodySecurityAuditor(
            max_hot_ratio=0.15, alert_fn=lambda m: self.alerts.append(m))

    def test_zero_total_with_hot_balance_is_not_safe(self):
        # Regression: previously returned is_safe=True, ratio=0.0 while 50k sat hot.
        is_safe, ratio, finding = self.auditor.evaluate_hot_cold_allocation(50_000, 0)
        self.assertFalse(is_safe)
        self.assertEqual(ratio, float("inf"))
        self.assertIsNotNone(finding)
        self.assertEqual(len(self.alerts), 1)

    def test_zero_total_and_zero_hot_is_safe(self):
        is_safe, ratio, finding = self.auditor.evaluate_hot_cold_allocation(0, 0)
        self.assertTrue(is_safe)
        self.assertEqual(ratio, 0.0)
        self.assertIsNone(finding)

    def test_negative_and_non_finite_inputs_raise(self):
        # Regression: NaN total and negative totals previously reported "safe".
        for hot, total in ((50_000, -100), (-50, 100), (50_000, float("nan")),
                           (float("nan"), 100), (float("inf"), 100), (1, float("inf"))):
            with self.assertRaises(ValueError, msg=f"{hot}/{total}"):
                self.auditor.evaluate_hot_cold_allocation(hot, total)

    def test_non_numeric_inputs_raise(self):
        for hot, total in (("50000", 100), (50_000, None), (True, 100)):
            with self.assertRaises(TypeError):
                self.auditor.evaluate_hot_cold_allocation(hot, total)

    def test_exact_threshold_is_inclusive_and_safe(self):
        # 15% exactly is at the limit, not over it.
        is_safe, ratio, finding = self.auditor.evaluate_hot_cold_allocation(15_000, 100_000)
        self.assertTrue(is_safe)
        self.assertAlmostEqual(ratio, 0.15)
        self.assertIsNone(finding)

    def test_just_over_threshold_is_unsafe(self):
        is_safe, _, finding = self.auditor.evaluate_hot_cold_allocation(15_001, 100_000)
        self.assertFalse(is_safe)
        self.assertIsNotNone(finding)

    def test_hot_exceeding_total_is_unsafe(self):
        is_safe, ratio, _ = self.auditor.evaluate_hot_cold_allocation(200, 100)
        self.assertFalse(is_safe)
        self.assertEqual(ratio, 2.0)

    def test_invalid_max_hot_ratio_rejected(self):
        for bad in (-0.1, 1.5, float("nan")):
            with self.assertRaises(ValueError):
                KeyCustodySecurityAuditor(max_hot_ratio=bad)


class TestAddressNormalization(unittest.TestCase):

    def setUp(self):
        self.auditor = KeyCustodySecurityAuditor(alert_fn=lambda m: None)

    def test_eip55_checksummed_evm_address_matches_lowercase_whitelist(self):
        # Regression: EIP-55 mixed case is a checksum over a case-insensitive hex
        # address, so these are THE SAME address and previously mismatched.
        lower = "0x52908400098527886e0f7030069857d2e4169ee7"
        checksummed = "0x52908400098527886E0F7030069857D2E4169EE7"
        self.assertTrue(self.auditor.audit_outbound_transfer(checksummed, 1.0, {lower}))
        self.assertTrue(self.auditor.audit_outbound_transfer(lower, 1.0, {checksummed}))

    def test_evm_prefix_case_is_not_address_data(self):
        # '0x' is a notation marker, not part of the address.
        lower = "0x52908400098527886e0f7030069857d2e4169ee7"
        self.assertEqual(normalize_address("0X52908400098527886E0F7030069857D2E4169EE7"), lower)

    def test_distinct_evm_addresses_still_rejected(self):
        self.assertFalse(self.auditor.audit_outbound_transfer(
            "0x52908400098527886E0F7030069857D2E4169EE8", 1.0,
            {"0x52908400098527886e0f7030069857d2e4169ee7"}))

    def test_base58_addresses_are_compared_case_sensitively(self):
        # Base58 is a case-sensitive alphabet: folding case would let two DISTINCT
        # addresses collide and turn the whitelist into a fail-open.
        whitelisted = "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"
        lowered = whitelisted.lower()
        self.assertNotEqual(normalize_address(whitelisted), normalize_address(lowered))
        self.assertFalse(self.auditor.audit_outbound_transfer(lowered, 1.0, {whitelisted}))

    def test_bech32_is_case_insensitive_but_rejects_mixed_case(self):
        lower = "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4"
        self.assertEqual(normalize_address(lower.upper()), lower)
        with self.assertRaises(ValueError):
            normalize_address("bc1QW508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4")

    def test_unparseable_destination_is_treated_as_unapproved(self):
        for bad in ("", "   ", None, 12345):
            self.assertFalse(self.auditor.audit_outbound_transfer(bad, 1.0, {"0x" + "a" * 40}))

    def test_unparseable_whitelist_entry_is_recorded_not_ignored(self):
        auditor = KeyCustodySecurityAuditor(alert_fn=lambda m: None)
        good = "0x" + "a" * 40
        self.assertTrue(auditor.audit_outbound_transfer(good, 1.0, {good, ""}))
        self.assertTrue(any("whitelist" in f.issue for f in auditor.findings))

    def test_invalid_transfer_amounts_raise(self):
        for amount in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.auditor.audit_outbound_transfer("0x" + "a" * 40, amount, {"0x" + "a" * 40})
        with self.assertRaises(TypeError):
            self.auditor.audit_outbound_transfer("0x" + "a" * 40, "5", {"0x" + "a" * 40})


class TestMultisigThreshold(unittest.TestCase):
    """SKILL.md workflow step 5 mandates threshold multi-sig; it had no implementation."""

    def setUp(self):
        self.auditor = KeyCustodySecurityAuditor(
            alert_fn=lambda m: None, multisig_threshold=10_000, required_approvals=2)

    def test_below_threshold_needs_no_approvals(self):
        approved, finding = self.auditor.evaluate_transfer_approval(9_999, approvals_present=0)
        self.assertTrue(approved)
        self.assertIsNone(finding)

    def test_at_threshold_is_inclusive_and_requires_approvals(self):
        approved, finding = self.auditor.evaluate_transfer_approval(10_000, approvals_present=0)
        self.assertFalse(approved)
        self.assertEqual(finding.risk_level, RiskLevel.CRITICAL)

    def test_insufficient_approvals_blocked(self):
        approved, _ = self.auditor.evaluate_transfer_approval(50_000, approvals_present=1)
        self.assertFalse(approved)

    def test_sufficient_approvals_allowed(self):
        approved, finding = self.auditor.evaluate_transfer_approval(50_000, approvals_present=2)
        self.assertTrue(approved)
        self.assertIsNone(finding)

    def test_threshold_disabled_by_default(self):
        default = KeyCustodySecurityAuditor(alert_fn=lambda m: None)
        approved, finding = default.evaluate_transfer_approval(10_000_000, approvals_present=0)
        self.assertTrue(approved)
        self.assertIsNone(finding)


class TestAlertChannelResilience(unittest.TestCase):
    """Regression: a raising alert_fn aborted the audit -- the monitor died on the
    exact event it exists to report."""

    def setUp(self):
        def failing_alert(msg):
            raise ConnectionError("pagerduty unreachable")

        self.auditor = KeyCustodySecurityAuditor(max_hot_ratio=0.15, alert_fn=failing_alert)

    def test_hot_cold_audit_survives_alert_failure(self):
        is_safe, ratio, finding = self.auditor.evaluate_hot_cold_allocation(50_000, 100_000)
        self.assertFalse(is_safe)
        self.assertEqual(ratio, 0.5)
        self.assertIsNotNone(finding)

    def test_transfer_audit_survives_alert_failure(self):
        self.assertFalse(self.auditor.audit_outbound_transfer("0x" + "b" * 40, 1.0, set()))

    def test_alert_delivery_failure_is_itself_recorded(self):
        self.auditor.evaluate_hot_cold_allocation(50_000, 100_000)
        self.assertTrue(any("Alert channel failed" in f.issue for f in self.auditor.findings))
        self.assertFalse(self.auditor.summary().passed)


class TestAuditorStateAndSummary(unittest.TestCase):

    def setUp(self):
        self.auditor = KeyCustodySecurityAuditor(alert_fn=lambda m: None)

    def test_reset_clears_accumulated_findings(self):
        cfg = {"name": "k", "used_by": "trading_bot", "permissions": ["withdraw"],
               "ip_whitelisted": False, "storage_backend": "plaintext_file"}
        for _ in range(3):
            self.auditor.audit_key_config(cfg)
        self.assertEqual(len(self.auditor.findings), 9)
        self.auditor.reset()
        self.assertEqual(self.auditor.findings, [])
        self.assertTrue(self.auditor.summary().passed)

    def test_summary_counts_by_severity(self):
        self.auditor.audit_key_config({
            "name": "k", "used_by": "trading_bot", "permissions": ["withdraw"],
            "ip_whitelisted": False, "storage_backend": "plaintext_file"})
        summary = self.auditor.summary()
        self.assertIsInstance(summary, AuditSummary)
        self.assertEqual(summary.critical, 1)
        self.assertEqual(summary.high, 2)
        self.assertEqual(summary.total_findings, 3)
        self.assertFalse(summary.passed)

    def test_summary_passes_only_on_a_clean_audit(self):
        self.auditor.audit_key_config({
            "name": "k", "used_by": "trading_bot", "permissions": ["READ", "TRADE"],
            **SECURE_BASE})
        self.assertTrue(self.auditor.summary().passed)

    def test_summary_findings_is_a_copy(self):
        self.auditor.audit_key_config({
            "name": "k", "used_by": "bot", "permissions": ["withdraw"], **SECURE_BASE})
        summary = self.auditor.summary()
        summary.findings.clear()
        self.assertEqual(len(self.auditor.findings), 1)

    def test_non_dict_config_rejected(self):
        with self.assertRaises(TypeError):
            self.auditor.audit_key_config(["not", "a", "dict"])


if __name__ == "__main__":
    unittest.main()

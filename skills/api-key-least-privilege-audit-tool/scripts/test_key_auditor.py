"""
Unit tests for api-key-least-privilege-audit-tool skill.

Tests:
1. Compliant execution key passes audit.
2. Over-privileged key with withdrawal permissions flagged.
3. Insufficient permissions key flagged.
4. MARKET_DATA_ONLY role — compliant and over-privileged.
5. PORTFOLIO_MONITOR role — compliant and with trading permissions.
6. ADMIN_SUPERVISOR role — compliant with admin scopes, flagged with withdrawal.
7. Wildcard * permission always flagged as critical violation.
8. Empty permissions set flagged as insufficient.
9. Case-insensitive matching (mixed-case scopes accepted).
10. Undefined role raises ValueError.
11. Custom policy injection via constructor.
12. Frozen dataclass immutability for KeyAuditReport.
13. Machine-readable severity distinguishes over- from under-privileged keys.
14. Report list ordering is deterministic across processes.
15. Malformed granted_permissions input is rejected rather than silently audited.
16. Unrecognised broker-native scope names fail closed (deny by default).
"""
import subprocess
import sys
import unittest
from key_auditor import (
    CRITICAL_FORBIDDEN_PERMISSIONS,
    SEVERITY_COMPLIANT,
    SEVERITY_CRITICAL,
    SEVERITY_INSUFFICIENT,
    APIKeyLeastPrivilegeAuditor,
    BotRole,
    KeyAuditReport,
    ROLE_POLICIES,
    RoleSecurityPolicy,
)


class TestAPIKeyLeastPrivilegeAuditor(unittest.TestCase):

    def setUp(self):
        self.auditor = APIKeyLeastPrivilegeAuditor()

    # --- EXECUTION_BOT ---

    def test_compliant_execution_key(self):
        granted = {"read_market_data", "place_orders", "cancel_orders", "read_positions"}
        report = self.auditor.audit_key("key_exec_prod", "binance", BotRole.EXECUTION_BOT, granted)

        self.assertTrue(report.is_compliant)
        self.assertEqual(len(report.excess_violations), 0)
        self.assertEqual(len(report.missing_required), 0)
        self.assertIsNone(report.security_warning)

    def test_over_privileged_key_with_withdrawals(self):
        granted = {"read_market_data", "place_orders", "cancel_orders", "withdraw_funds"}
        report = self.auditor.audit_key("key_exec_bad", "coinbase", BotRole.EXECUTION_BOT, granted)

        self.assertFalse(report.is_compliant)
        self.assertIn("withdraw_funds", report.excess_violations)
        self.assertIn("CRITICAL SECURITY VIOLATION", report.security_warning)

    def test_insufficient_permissions_key(self):
        granted = {"read_market_data", "place_orders"}
        report = self.auditor.audit_key("key_exec_partial", "ibkr", BotRole.EXECUTION_BOT, granted)

        self.assertFalse(report.is_compliant)
        self.assertIn("cancel_orders", report.missing_required)
        self.assertIn("INSUFFICIENT PERMISSIONS", report.security_warning)

    # --- MARKET_DATA_ONLY ---

    def test_compliant_market_data_key(self):
        granted = {"read_market_data"}
        report = self.auditor.audit_key("key_md", "alpaca", BotRole.MARKET_DATA_ONLY, granted)
        self.assertTrue(report.is_compliant)

    def test_market_data_key_with_trading_permissions(self):
        granted = {"read_market_data", "place_orders"}
        report = self.auditor.audit_key("key_md_bad", "alpaca", BotRole.MARKET_DATA_ONLY, granted)
        self.assertFalse(report.is_compliant)
        self.assertIn("place_orders", report.excess_violations)

    # --- PORTFOLIO_MONITOR ---

    def test_compliant_portfolio_monitor(self):
        granted = {"read_account_info", "read_positions", "read_orders"}
        report = self.auditor.audit_key("key_pm", "ibkr", BotRole.PORTFOLIO_MONITOR, granted)
        self.assertTrue(report.is_compliant)

    def test_portfolio_monitor_with_trading_permissions(self):
        granted = {"read_account_info", "read_positions", "place_orders"}
        report = self.auditor.audit_key("key_pm_bad", "ibkr", BotRole.PORTFOLIO_MONITOR, granted)
        self.assertFalse(report.is_compliant)
        self.assertIn("place_orders", report.excess_violations)

    # --- ADMIN_SUPERVISOR ---

    def test_compliant_admin_supervisor(self):
        granted = {"read_account_info", "read_positions", "account_admin", "api_key_manage"}
        report = self.auditor.audit_key("key_admin", "binance", BotRole.ADMIN_SUPERVISOR, granted)
        self.assertTrue(report.is_compliant)

    def test_admin_supervisor_with_withdrawal_flagged(self):
        granted = {"read_account_info", "read_positions", "account_admin", "withdraw"}
        report = self.auditor.audit_key("key_admin_bad", "binance", BotRole.ADMIN_SUPERVISOR, granted)
        self.assertFalse(report.is_compliant)
        self.assertIn("withdraw", report.excess_violations)

    # --- Wildcard detection ---

    def test_wildcard_permission_always_flagged(self):
        for role in BotRole:
            granted = {"*"}
            report = self.auditor.audit_key("key_wildcard", "binance", role, granted)
            self.assertFalse(report.is_compliant, f"Wildcard should be flagged for {role.value}")
            self.assertIn("*", report.excess_violations)
            self.assertIn("WILDCARD PERMISSION DETECTED", report.security_warning)

    def test_wildcard_alongside_valid_permissions(self):
        granted = {"read_market_data", "place_orders", "cancel_orders", "*"}
        report = self.auditor.audit_key("key_wildcard_mixed", "binance", BotRole.EXECUTION_BOT, granted)
        self.assertFalse(report.is_compliant)
        self.assertIn("*", report.excess_violations)

    # --- Empty permissions ---

    def test_empty_permissions_flagged(self):
        report = self.auditor.audit_key("key_empty", "binance", BotRole.EXECUTION_BOT, set())
        self.assertFalse(report.is_compliant)
        self.assertGreater(len(report.missing_required), 0)

    # --- Case-insensitivity ---

    def test_case_insensitive_matching(self):
        granted = {"Read_Market_Data", "PLACE_ORDERS", "Cancel_Orders"}
        report = self.auditor.audit_key("key_mixed_case", "binance", BotRole.EXECUTION_BOT, granted)
        self.assertTrue(report.is_compliant)
        self.assertEqual(len(report.missing_required), 0)

    def test_case_insensitive_forbidden_detection(self):
        granted = {"read_market_data", "place_orders", "cancel_orders", "WITHDRAW_FUNDS"}
        report = self.auditor.audit_key("key_upper_forbidden", "binance", BotRole.EXECUTION_BOT, granted)
        self.assertFalse(report.is_compliant)
        self.assertIn("withdraw_funds", report.excess_violations)

    # --- Undefined role ---

    def test_undefined_role_raises_value_error(self):
        bogus_role = BotRole.MARKET_DATA_ONLY  # Use valid role but remove its policy
        auditor = APIKeyLeastPrivilegeAuditor(policies={})
        with self.assertRaises(ValueError):
            auditor.audit_key("key_test", "binance", bogus_role, {"read_market_data"})

    # --- Custom policy injection ---

    def test_custom_policy_injection(self):
        custom_role = BotRole.EXECUTION_BOT
        custom_policy = RoleSecurityPolicy(
            role=custom_role,
            required_permissions=frozenset({"custom_read"}),
            allowed_permissions=frozenset({"custom_read", "custom_trade"}),
            forbidden_permissions=frozenset({"withdraw"}),
        )
        auditor = APIKeyLeastPrivilegeAuditor(policies={custom_role: custom_policy})
        report = auditor.audit_key("key_custom", "binance", custom_role, {"custom_read", "custom_trade"})
        self.assertTrue(report.is_compliant)

    # --- Immutability ---

    def test_key_audit_report_is_frozen(self):
        report = self.auditor.audit_key("key_test", "binance", BotRole.EXECUTION_BOT, {"read_market_data", "place_orders", "cancel_orders"})
        with self.assertRaises(Exception):
            report.is_compliant = False

    def test_role_security_policy_is_frozen(self):
        policy = ROLE_POLICIES[BotRole.EXECUTION_BOT]
        with self.assertRaises(Exception):
            policy.required_permissions = frozenset({"withdraw"})


class TestAuditSeverity(unittest.TestCase):
    """A deployment gate must distinguish 'revoke this key' from 'reissue this key'
    without string-matching the human-readable warning."""

    def setUp(self):
        self.auditor = APIKeyLeastPrivilegeAuditor()

    def test_compliant_key_reports_compliant_severity(self):
        report = self.auditor.audit_key(
            "key_ok", "binance", BotRole.EXECUTION_BOT,
            {"read_market_data", "place_orders", "cancel_orders"},
        )
        self.assertEqual(report.severity, SEVERITY_COMPLIANT)
        self.assertFalse(report.has_critical_violation)

    def test_over_privileged_key_reports_critical_severity(self):
        report = self.auditor.audit_key(
            "key_bad", "binance", BotRole.EXECUTION_BOT,
            {"read_market_data", "place_orders", "cancel_orders", "withdraw_funds"},
        )
        self.assertEqual(report.severity, SEVERITY_CRITICAL)
        self.assertTrue(report.has_critical_violation)

    def test_under_privileged_key_reports_insufficient_not_critical(self):
        """An under-privileged key is a deployment blocker but NOT a revocation event.
        Conflating the two sends operators to revoke a key that is merely too weak."""
        report = self.auditor.audit_key(
            "key_weak", "binance", BotRole.EXECUTION_BOT, {"read_market_data"},
        )
        self.assertEqual(report.severity, SEVERITY_INSUFFICIENT)
        self.assertFalse(report.has_critical_violation)
        self.assertFalse(report.is_compliant)

    def test_excess_takes_precedence_over_missing_but_both_are_reported(self):
        """A key can be simultaneously over- and under-privileged. Reporting only the
        excess hides half the remediation."""
        report = self.auditor.audit_key(
            "key_both", "binance", BotRole.EXECUTION_BOT, {"withdraw_funds"},
        )
        self.assertEqual(report.severity, SEVERITY_CRITICAL)
        self.assertEqual(report.excess_violations, ["withdraw_funds"])
        self.assertEqual(
            report.missing_required,
            ["cancel_orders", "place_orders", "read_market_data"],
        )
        self.assertIn("CRITICAL SECURITY VIOLATION", report.security_warning)
        self.assertIn("also missing required permissions", report.security_warning)

    def test_empty_scope_set_warns_about_failed_probe(self):
        """An empty scope set is far more often a broken permission probe than a
        genuinely unprivileged key, and the record must say so."""
        report = self.auditor.audit_key("key_empty", "kraken", BotRole.EXECUTION_BOT, set())
        self.assertFalse(report.is_compliant)
        self.assertEqual(report.severity, SEVERITY_INSUFFICIENT)
        self.assertIn("EMPTY", report.security_warning)

    def test_blank_and_whitespace_scopes_are_discarded_not_audited(self):
        report = self.auditor.audit_key(
            "key_blank", "binance", BotRole.EXECUTION_BOT,
            {"read_market_data", "  ", "", "place_orders", "cancel_orders"},
        )
        self.assertTrue(report.is_compliant)
        self.assertEqual(
            report.granted_permissions,
            frozenset({"read_market_data", "place_orders", "cancel_orders"}),
        )


class TestDenyByDefault(unittest.TestCase):
    """Unrecognised scope names must fail closed. Broker-native scope vocabularies
    do not match this skill's canonical names, and a scope the policy has never
    heard of is exactly the case that must not slip through."""

    def setUp(self):
        self.auditor = APIKeyLeastPrivilegeAuditor()

    def test_unmapped_broker_native_withdrawal_scopes_are_flagged(self):
        for native_scope in ("enableWithdrawals", "can_transfer", "Withdraw Funds", "Funding"):
            with self.subTest(scope=native_scope):
                report = self.auditor.audit_key(
                    "key_native", "venue", BotRole.EXECUTION_BOT,
                    {"read_market_data", "place_orders", "cancel_orders", native_scope},
                )
                self.assertFalse(report.is_compliant)
                self.assertEqual(report.severity, SEVERITY_CRITICAL)
                self.assertIn(native_scope.strip().lower(), report.excess_violations)


class TestInputValidation(unittest.TestCase):
    """A bare string is iterable. Auditing one character at a time would produce a
    confident-looking report about scopes that do not exist."""

    def setUp(self):
        self.auditor = APIKeyLeastPrivilegeAuditor()

    def test_bare_string_scope_is_rejected(self):
        with self.assertRaises(TypeError) as ctx:
            self.auditor.audit_key("key", "binance", BotRole.EXECUTION_BOT, "read_market_data")
        self.assertIn("not a bare str", str(ctx.exception))

    def test_bytes_scope_is_rejected(self):
        with self.assertRaises(TypeError):
            self.auditor.audit_key("key", "binance", BotRole.EXECUTION_BOT, b"read_market_data")

    def test_none_scopes_rejected(self):
        with self.assertRaises(TypeError):
            self.auditor.audit_key("key", "binance", BotRole.EXECUTION_BOT, None)

    def test_non_iterable_scopes_rejected(self):
        with self.assertRaises(TypeError):
            self.auditor.audit_key("key", "binance", BotRole.EXECUTION_BOT, 42)

    def test_non_string_element_rejected(self):
        with self.assertRaises(TypeError):
            self.auditor.audit_key(
                "key", "binance", BotRole.EXECUTION_BOT, ["read_market_data", None],
            )

    def test_list_input_accepted(self):
        """Scopes decoded from a broker JSON response arrive as a list, not a set."""
        report = self.auditor.audit_key(
            "key_list", "binance", BotRole.EXECUTION_BOT,
            ["read_market_data", "place_orders", "cancel_orders", "place_orders"],
        )
        self.assertTrue(report.is_compliant)


class TestPolicyInvariants(unittest.TestCase):
    """An unsatisfiable policy reports a correctly-configured key as a CRITICAL
    violation, i.e. orders the revocation of a good key. Catch it at definition time."""

    def test_shipped_policies_are_satisfiable(self):
        for role, policy in ROLE_POLICIES.items():
            with self.subTest(role=role.value):
                self.assertTrue(
                    policy.required_lower <= policy.allowed_lower,
                    f"{role.value}: required scopes missing from allowed",
                )
                self.assertFalse(
                    policy.required_lower & policy.forbidden_lower,
                    f"{role.value}: scopes both required and forbidden",
                )

    def test_required_scope_absent_from_allowed_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            RoleSecurityPolicy(
                role=BotRole.EXECUTION_BOT,
                required_permissions=frozenset({"place_orders"}),
                allowed_permissions=frozenset({"read_market_data"}),
                forbidden_permissions=frozenset({"withdraw"}),
            )
        self.assertIn("unsatisfiable", str(ctx.exception))

    def test_required_scope_also_forbidden_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            RoleSecurityPolicy(
                role=BotRole.EXECUTION_BOT,
                required_permissions=frozenset({"place_orders"}),
                allowed_permissions=frozenset({"place_orders"}),
                forbidden_permissions=frozenset({"place_orders"}),
            )
        self.assertIn("contradictory", str(ctx.exception))

    def test_policy_invariants_are_case_insensitive(self):
        """Mixed-case policy definitions must not evade the invariant check."""
        with self.assertRaises(ValueError):
            RoleSecurityPolicy(
                role=BotRole.EXECUTION_BOT,
                required_permissions=frozenset({"Place_Orders"}),
                allowed_permissions=frozenset({"place_orders"}),
                forbidden_permissions=frozenset({"PLACE_ORDERS"}),
            )


class TestReportDeterminism(unittest.TestCase):
    """Regression: report lists were built by iterating a set, so their order varied
    with PYTHONHASHSEED. Audit records that differ run-to-run cannot be diffed."""

    PROBE = (
        "import logging; logging.disable(logging.CRITICAL)\n"
        "from key_auditor import APIKeyLeastPrivilegeAuditor, BotRole\n"
        "a = APIKeyLeastPrivilegeAuditor()\n"
        "r1 = a.audit_key('k', 'b', BotRole.EXECUTION_BOT, set())\n"
        "r2 = a.audit_key('k', 'b', BotRole.MARKET_DATA_ONLY,\n"
        "                 {'place_orders', 'withdraw', 'transfer', 'cancel_orders'})\n"
        "print(r1.missing_required, r2.excess_violations)\n"
    )

    def test_lists_are_sorted_in_process(self):
        auditor = APIKeyLeastPrivilegeAuditor()
        report = auditor.audit_key(
            "key", "venue", BotRole.MARKET_DATA_ONLY,
            {"withdraw", "transfer", "place_orders", "cancel_orders", "read_market_data"},
        )
        self.assertEqual(report.excess_violations, sorted(report.excess_violations))
        self.assertEqual(
            report.excess_violations,
            ["cancel_orders", "place_orders", "transfer", "withdraw"],
        )

    def test_ordering_is_stable_across_hash_seeds(self):
        import os

        script_dir = os.path.dirname(os.path.abspath(__file__))
        outputs = set()
        for seed in ("0", "1", "7", "12345"):
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=script_dir)
            result = subprocess.run(
                [sys.executable, "-c", self.PROBE],
                cwd=script_dir, env=env, capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            outputs.add(result.stdout.strip())
        self.assertEqual(
            len(outputs), 1,
            f"Audit report ordering varied with PYTHONHASHSEED: {outputs}",
        )


if __name__ == "__main__":
    unittest.main()

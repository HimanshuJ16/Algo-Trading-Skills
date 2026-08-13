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
"""
import unittest
from key_auditor import (
    CRITICAL_FORBIDDEN_PERMISSIONS,
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


if __name__ == "__main__":
    unittest.main()

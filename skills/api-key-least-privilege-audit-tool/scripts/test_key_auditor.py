"""
Unit tests for api-key-least-privilege-audit-tool skill.
"""
import unittest
from key_auditor import APIKeyLeastPrivilegeAuditor, BotRole


class TestAPIKeyLeastPrivilegeAuditor(unittest.TestCase):

    def setUp(self):
        self.auditor = APIKeyLeastPrivilegeAuditor()

    def test_compliant_execution_key(self):
        granted = {"read_market_data", "place_orders", "cancel_orders", "read_positions"}
        report = self.auditor.audit_key("key_exec_prod", "binance", BotRole.EXECUTION_BOT, granted)

        self.assertTrue(report.is_compliant)
        self.assertEqual(len(report.excess_violations), 0)
        self.assertEqual(len(report.missing_required), 0)

    def test_over_privileged_key_with_withdrawals(self):
        # Execution key possessing withdraw_funds permission -> CRITICAL VIOLATION
        granted = {"read_market_data", "place_orders", "cancel_orders", "withdraw_funds"}
        report = self.auditor.audit_key("key_exec_bad", "coinbase", BotRole.EXECUTION_BOT, granted)

        self.assertFalse(report.is_compliant)
        self.assertIn("withdraw_funds", report.excess_violations)
        self.assertIn("CRITICAL SECURITY VIOLATION", report.security_warning)

    def test_insufficient_permissions_key(self):
        # Execution key missing cancel_orders
        granted = {"read_market_data", "place_orders"}
        report = self.auditor.audit_key("key_exec_partial", "ibkr", BotRole.EXECUTION_BOT, granted)

        self.assertFalse(report.is_compliant)
        self.assertIn("cancel_orders", report.missing_required)


if __name__ == "__main__":
    unittest.main()

"""
Unit tests for crypto-wallet-key-custody-security skill.

Tests:
1. API key permission scoping audit (WITHDRAW permission on bot key).
2. Missing IP whitelisting detection.
3. Insecure storage backend detection (plaintext/env files vs Vault/KMS).
4. Hot vs Cold wallet balance allocation check.
5. Outbound transfer whitelist monitoring and alert trigger.
6. Backward compatibility with standalone audit functions.
"""
import unittest
from key_permission_audit import (
    AuditFinding,
    KeyCustodySecurityAuditor,
    RiskLevel,
    StorageBackend,
    audit_key_permissions,
    check_hot_balance_ratio,
)


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
            "ip_whitelisted": True,
            "storage_backend": StorageBackend.AWS_KMS,
        }
        findings = self.auditor.audit_key_config(secure_key)
        self.assertEqual(len(findings), 0)

    def test_hot_cold_allocation_check(self):
        is_safe, ratio, finding = self.auditor.evaluate_hot_cold_allocation(
            hot_balance=25_000, total_balance=100_000
        )
        self.assertFalse(is_safe)
        self.assertEqual(ratio, 0.25)
        self.assertIsNotNone(finding)
        self.assertEqual(len(self.alerts), 1)

    def test_outbound_transfer_monitoring(self):
        whitelist = {"0x1234567890abcdef1234567890abcdef12345678"}
        # Safe transfer to whitelisted address
        res1 = self.auditor.audit_outbound_transfer(
            destination_address="0x1234567890abcdef1234567890abcdef12345678",
            amount=5.0,
            approved_whitelist=whitelist,
        )
        self.assertTrue(res1)

        # Unapproved transfer
        res2 = self.auditor.audit_outbound_transfer(
            destination_address="0xBADADDRESS",
            amount=10.0,
            approved_whitelist=whitelist,
        )
        self.assertFalse(res2)

    def test_backward_compatibility(self):
        key_configs = [
            {"name": "bot_key", "used_by": "trading_bot", "permissions": ["withdraw", "trade"]}
        ]
        f = audit_key_permissions(key_configs)
        self.assertEqual(len(f), 1)
        self.assertIn("withdraw permission", f[0])

        is_ok, r = check_hot_balance_ratio(10, 100, 0.15)
        self.assertTrue(is_ok)


if __name__ == "__main__":
    unittest.main()

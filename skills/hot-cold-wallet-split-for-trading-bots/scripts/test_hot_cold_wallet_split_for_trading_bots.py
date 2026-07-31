import unittest
from hot_cold_wallet_split_for_trading_bots import (
    HotColdWalletManagerEngine, WalletBalances, HotColdWalletAuditReport
)

class TestHotColdWalletManagerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = HotColdWalletManagerEngine(
            target_hot_ratio=0.15, max_hot_ratio_threshold=0.25, min_hot_ratio_threshold=0.05
        )

    def test_sweep_to_cold_when_hot_ratio_exceeds_max(self):
        # Total $1,000,000. Hot = $300,000 (30% > 25%). Target Hot = $150,000 (15%). Sweep = $150,000!
        balances = WalletBalances(hot_wallet_usd=300_000.0, cold_vault_usd=700_000.0)
        report = self.engine.audit_and_rebalance_treasury(balances)

        self.assertEqual(report.status, "REBALANCE_REQUIRED")
        self.assertEqual(report.rebalance_action, "SWEEP_TO_COLD")
        self.assertEqual(report.proposed_transfer_usd, 150_000.0)
        self.assertTrue(report.is_api_key_secure)

    def test_refill_hot_when_hot_ratio_below_min(self):
        # Total $1,000,000. Hot = $30,000 (3% < 5%). Target Hot = $150,000 (15%). Refill = $120,000!
        balances = WalletBalances(hot_wallet_usd=30_000.0, cold_vault_usd=970_000.0)
        report = self.engine.audit_and_rebalance_treasury(balances)

        self.assertEqual(report.status, "REBALANCE_REQUIRED")
        self.assertEqual(report.rebalance_action, "REFILL_HOT_FROM_COLD")
        self.assertEqual(report.proposed_transfer_usd, 120_000.0)

    def test_critical_alert_when_withdrawal_enabled(self):
        balances = WalletBalances(
            hot_wallet_usd=150_000.0, cold_vault_usd=850_000.0, api_key_withdraw_permission_enabled=True
        )
        report = self.engine.audit_and_rebalance_treasury(balances)

        self.assertEqual(report.status, "CRITICAL_SECURITY_ALERT")
        self.assertFalse(report.is_api_key_secure)

if __name__ == '__main__':
    unittest.main()

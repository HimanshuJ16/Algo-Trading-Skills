import time
import unittest
from key_rotation_schedule_for_hot_wallet_keys import (
    HotWalletKeyRotationEngine, HotWalletKeyMetadata, KeyRotationReport
)

class TestHotWalletKeyRotationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = HotWalletKeyRotationEngine(
            max_key_age_days=90.0, max_signatures_limit=100_000, max_volume_usd_limit=10_000_000.0
        )
        self.now = 1700000000.0

    def test_healthy_active_key(self):
        # 30-day old key, 5,000 txs -> HEALTHY_ACTIVE!
        meta = HotWalletKeyMetadata(
            key_id="HOT_KEY_01",
            created_timestamp_epoch=self.now - (30 * 86400),
            last_used_timestamp_epoch=self.now,
            total_signatures_count=5000,
            total_volume_usd_signed=500_000.0,
            is_compromised=False
        )
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=self.now)

        self.assertEqual(report.status, "KEY_HEALTHY_ACTIVE")
        self.assertFalse(report.is_rotation_required)
        self.assertEqual(report.new_key_state, "ACTIVE")

    def test_90_day_expired_key_rotation(self):
        # 95-day old key -> ROTATION_INITIATED_AGE_EXPIRED!
        meta = HotWalletKeyMetadata(
            key_id="HOT_KEY_02",
            created_timestamp_epoch=self.now - (95 * 86400),
            last_used_timestamp_epoch=self.now,
            total_signatures_count=1000,
            total_volume_usd_signed=100_000.0,
            is_compromised=False
        )
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=self.now)

        self.assertEqual(report.status, "ROTATION_INITIATED_AGE_EXPIRED")
        self.assertTrue(report.is_rotation_required)
        self.assertEqual(report.new_key_state, "DEPRECATED_GRACE_PERIOD")
        self.assertEqual(report.replacement_key_id, "HOT_KEY_02_V2")

    def test_emergency_compromise_revocation(self):
        # Compromised flag = True -> EMERGENCY_REVOKED_COMPROMISED!
        meta = HotWalletKeyMetadata(
            key_id="HOT_KEY_03",
            created_timestamp_epoch=self.now - (10 * 86400),
            last_used_timestamp_epoch=self.now,
            total_signatures_count=50,
            total_volume_usd_signed=5000.0,
            is_compromised=True
        )
        report = self.engine.audit_and_rotate_key(meta, current_time_epoch=self.now)

        self.assertEqual(report.status, "EMERGENCY_REVOKED_COMPROMISED")
        self.assertTrue(report.is_rotation_required)
        self.assertEqual(report.new_key_state, "REVOKED_SHREDDED")

if __name__ == '__main__':
    unittest.main()

import unittest
from recovery_plan_for_lost_or_compromised_keys import (
    RecoveryPlanForLostOrCompromisedKeysConfig,
    RecoveryPlanForLostOrCompromisedKeysEngine,
    RecoveryPlanSpec, KeyRecoveryReadinessReport
)

class TestRecoveryPlanForLostOrCompromisedKeys(unittest.TestCase):

    def setUp(self):
        self.config = RecoveryPlanForLostOrCompromisedKeysConfig(enabled=True)
        self.engine = RecoveryPlanForLostOrCompromisedKeysEngine(self.config)

    def test_legacy_execute_true(self):
        self.assertTrue(self.engine.execute())

    def test_legacy_execute_false(self):
        engine = RecoveryPlanForLostOrCompromisedKeysEngine(
            RecoveryPlanForLostOrCompromisedKeysConfig(enabled=False)
        )
        self.assertFalse(engine.execute())

    def test_recovery_plan_ready(self):
        plans = [
            RecoveryPlanSpec(
                plan_id="PLAN_001", wallet_type="COLD", backup_method="SHAMIR_SSS",
                shamir_threshold=3, shamir_total_shards=5, verified_shards_available=4,
                sweep_wallet_configured=True, days_since_last_drill=30,
                incident_response_contacts=3
            )
        ]
        report = self.engine.audit_recovery_plans(plans)
        self.assertEqual(report.status, "RECOVERY_PLAN_READY")
        self.assertEqual(report.ready_count, 1)
        self.assertEqual(len(report.issues), 0)

    def test_recovery_plan_not_ready_insufficient_shards(self):
        plans = [
            RecoveryPlanSpec(
                plan_id="PLAN_002", wallet_type="COLD", backup_method="SHAMIR_SSS",
                shamir_threshold=3, shamir_total_shards=5, verified_shards_available=2,
                sweep_wallet_configured=True, days_since_last_drill=30,
                incident_response_contacts=2
            )
        ]
        report = self.engine.audit_recovery_plans(plans)
        self.assertEqual(report.status, "RECOVERY_PLAN_NOT_READY")
        self.assertEqual(report.not_ready_count, 1)
        self.assertTrue(any(i.issue_type == "INSUFFICIENT_SHARDS" for i in report.issues))

    def test_recovery_plan_not_ready_drill_overdue(self):
        plans = [
            RecoveryPlanSpec(
                plan_id="PLAN_003", wallet_type="HOT", backup_method="HSM_SEED",
                sweep_wallet_configured=True, days_since_last_drill=120,
                incident_response_contacts=2
            )
        ]
        report = self.engine.audit_recovery_plans(plans)
        self.assertEqual(report.status, "RECOVERY_PLAN_NOT_READY")
        self.assertTrue(any(i.issue_type == "DRILL_OVERDUE" for i in report.issues))

if __name__ == '__main__':
    unittest.main()

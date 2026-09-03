import unittest
from datetime import date, datetime

from recovery_plan_for_lost_or_compromised_keys import (
    KeyRecoveryPlanError,
    RecoveryPlanForLostOrCompromisedKeysConfig,
    RecoveryPlanForLostOrCompromisedKeysEngine,
    RecoveryPlanSpec,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_MEDIUM,
)

#: Fixed audit date so every assertion about drill recency is deterministic.
AS_OF = date(2026, 8, 27)


def fully_ready_plan(**overrides) -> RecoveryPlanSpec:
    """A 3-of-5 Shamir plan that passes every check, for single-field mutation."""
    defaults = dict(
        plan_id="PLAN_OK",
        wallet_type="COLD",
        backup_method="SHAMIR_SSS",
        shamir_threshold=3,
        shamir_total_shards=5,
        verified_shards_available=4,
        max_shards_at_single_location=2,
        distinct_backup_locations=3,
        sweep_wallet_configured=True,
        sweep_wallet_independently_keyed=True,
        sweep_wallet_test_transaction_verified=True,
        key_inventory_documented=True,
        incident_response_contacts=3,
        last_drill_date=date(2026, 7, 28),          # 30 days before AS_OF
    )
    defaults.update(overrides)
    return RecoveryPlanSpec(**defaults)


class TestRecoveryPlanForLostOrCompromisedKeys(unittest.TestCase):

    def setUp(self):
        self.config = RecoveryPlanForLostOrCompromisedKeysConfig(enabled=True)
        self.engine = RecoveryPlanForLostOrCompromisedKeysEngine(self.config)

    def issue_types(self, report):
        return {i.issue_type for i in report.issues}

    def issue_by_type(self, report, issue_type):
        matches = [i for i in report.issues if i.issue_type == issue_type]
        self.assertEqual(len(matches), 1, f"expected exactly one {issue_type}")
        return matches[0]

    # -------------------------------------------------- legacy surface
    def test_legacy_execute_true(self):
        self.assertTrue(self.engine.execute())

    def test_legacy_execute_false(self):
        engine = RecoveryPlanForLostOrCompromisedKeysEngine(
            RecoveryPlanForLostOrCompromisedKeysConfig(enabled=False)
        )
        self.assertFalse(engine.execute())

    # -------------------------------------------------- happy path
    def test_recovery_plan_ready(self):
        report = self.engine.audit_recovery_plans([fully_ready_plan()], as_of_date=AS_OF)
        self.assertEqual(report.status, "RECOVERY_PLAN_READY")
        self.assertEqual(report.ready_count, 1)
        self.assertEqual(report.not_ready_count, 0)
        self.assertEqual(report.issues, [])
        self.assertEqual(report.as_of_date, AS_OF)
        self.assertEqual(report.critical_issue_count, 0)

    def test_hsm_plan_can_be_ready_without_shamir_fields(self):
        plan = fully_ready_plan(
            plan_id="PLAN_HSM", wallet_type="WARM", backup_method="HSM_SEED",
            shamir_threshold=0, shamir_total_shards=0, verified_shards_available=0,
            max_shards_at_single_location=0,
        )
        report = self.engine.audit_recovery_plans([plan], as_of_date=AS_OF)
        self.assertEqual(report.status, "RECOVERY_PLAN_READY")

    def test_lowercase_backup_method_accepted(self):
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(backup_method="shamir_sss")], as_of_date=AS_OF
        )
        self.assertEqual(report.status, "RECOVERY_PLAN_READY")

    # -------------------------------------------------- shard sufficiency
    def test_shards_below_threshold_is_critical(self):
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(verified_shards_available=2)], as_of_date=AS_OF
        )
        self.assertEqual(report.status, "RECOVERY_PLAN_NOT_READY")
        self.assertEqual(report.not_ready_count, 1)
        issue = self.issue_by_type(report, "SHARDS_BELOW_THRESHOLD")
        self.assertEqual(issue.severity, SEVERITY_CRITICAL)
        self.assertNotIn("NO_SHARD_SURPLUS", self.issue_types(report))

    def test_exactly_at_threshold_is_high_not_critical(self):
        """3 verified shards on a 3-of-5 split: recoverable today, no margin.

        a naive engine lumped this with an unrecoverable key. It is not the
        same finding, and treating it as one hides the genuinely dead plans.
        """
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(verified_shards_available=3)], as_of_date=AS_OF
        )
        issue = self.issue_by_type(report, "NO_SHARD_SURPLUS")
        self.assertEqual(issue.severity, SEVERITY_HIGH)
        self.assertEqual(report.critical_issue_count, 0)

    def test_zero_surplus_policy_allows_exact_threshold(self):
        engine = RecoveryPlanForLostOrCompromisedKeysEngine(
            RecoveryPlanForLostOrCompromisedKeysConfig(min_shamir_surplus_shards=0)
        )
        report = engine.audit_recovery_plans(
            [fully_ready_plan(verified_shards_available=3)], as_of_date=AS_OF
        )
        self.assertEqual(report.status, "RECOVERY_PLAN_READY")

    def test_one_of_n_split_flagged(self):
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(shamir_threshold=1, max_shards_at_single_location=0)],
            as_of_date=AS_OF,
        )
        self.assertEqual(
            self.issue_by_type(report, "WEAK_SHAMIR_THRESHOLD").severity, SEVERITY_HIGH
        )

    # -------------------------------------------------- shard distribution
    def test_quorum_at_single_location_is_critical(self):
        """3 of the 5 shards in one vault: that vault alone can reconstruct the key."""
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(max_shards_at_single_location=3)], as_of_date=AS_OF
        )
        self.assertEqual(
            self.issue_by_type(report, "SHARD_QUORUM_CO_LOCATED").severity,
            SEVERITY_CRITICAL,
        )

    def test_one_below_threshold_at_single_location_passes(self):
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(max_shards_at_single_location=2)], as_of_date=AS_OF
        )
        self.assertEqual(report.status, "RECOVERY_PLAN_READY")

    def test_unrecorded_shard_distribution_is_medium(self):
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(max_shards_at_single_location=0)], as_of_date=AS_OF
        )
        self.assertEqual(
            self.issue_by_type(report, "SHARD_DISTRIBUTION_UNRECORDED").severity,
            SEVERITY_MEDIUM,
        )
        self.assertEqual(report.status, "RECOVERY_PLAN_NOT_READY")

    def test_single_backup_location_flagged_for_any_method(self):
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(backup_method="MNEMONIC_PHRASE",
                              shamir_threshold=0, shamir_total_shards=0,
                              verified_shards_available=0,
                              max_shards_at_single_location=0,
                              distinct_backup_locations=1)],
            as_of_date=AS_OF,
        )
        self.assertIn("BACKUP_NOT_GEOGRAPHICALLY_SEPARATED", self.issue_types(report))

    # -------------------------------------------------- sweep readiness
    def test_missing_sweep_wallet_is_critical_and_suppresses_sub_checks(self):
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(sweep_wallet_configured=False,
                              sweep_wallet_independently_keyed=False,
                              sweep_wallet_test_transaction_verified=False)],
            as_of_date=AS_OF,
        )
        self.assertEqual(
            self.issue_by_type(report, "NO_SWEEP_WALLET").severity, SEVERITY_CRITICAL
        )
        # No point reporting an untested sweep wallet that does not exist.
        self.assertNotIn("SWEEP_WALLET_UNTESTED", self.issue_types(report))

    def test_sweep_wallet_sharing_compromised_seed_is_critical(self):
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(sweep_wallet_independently_keyed=False)], as_of_date=AS_OF
        )
        self.assertEqual(
            self.issue_by_type(report, "SWEEP_WALLET_NOT_INDEPENDENTLY_KEYED").severity,
            SEVERITY_CRITICAL,
        )

    def test_untested_sweep_wallet_is_high(self):
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(sweep_wallet_test_transaction_verified=False)],
            as_of_date=AS_OF,
        )
        self.assertEqual(
            self.issue_by_type(report, "SWEEP_WALLET_UNTESTED").severity, SEVERITY_HIGH
        )

    # -------------------------------------------------- incident response
    def test_missing_key_inventory_flagged(self):
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(key_inventory_documented=False)], as_of_date=AS_OF
        )
        self.assertIn("KEY_INVENTORY_MISSING", self.issue_types(report))

    def test_single_ir_contact_flagged(self):
        """a naive engine collected this field and never checked it."""
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(incident_response_contacts=1)], as_of_date=AS_OF
        )
        self.assertIn("INSUFFICIENT_IR_CONTACTS", self.issue_types(report))

    # -------------------------------------------------- drill recency
    def test_never_drilled_is_critical_and_distinct_from_overdue(self):
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(last_drill_date=None)], as_of_date=AS_OF
        )
        self.assertEqual(
            self.issue_by_type(report, "DRILL_NEVER_CONDUCTED").severity,
            SEVERITY_CRITICAL,
        )
        self.assertNotIn("DRILL_OVERDUE", self.issue_types(report))

    def test_drill_overdue(self):
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(last_drill_date=date(2026, 4, 29))],  # 120 days
            as_of_date=AS_OF,
        )
        issue = self.issue_by_type(report, "DRILL_OVERDUE")
        self.assertEqual(issue.severity, SEVERITY_HIGH)
        self.assertIn("120 days", issue.detail)

    def test_drill_exactly_at_limit_passes(self):
        """90 days is 'within the 90-day window', 91 is not."""
        at_limit = self.engine.audit_recovery_plans(
            [fully_ready_plan(last_drill_date=date(2026, 5, 29))], as_of_date=AS_OF
        )
        self.assertEqual((AS_OF - date(2026, 5, 29)).days, 90)
        self.assertEqual(at_limit.status, "RECOVERY_PLAN_READY")

        over_limit = self.engine.audit_recovery_plans(
            [fully_ready_plan(last_drill_date=date(2026, 5, 28))], as_of_date=AS_OF
        )
        self.assertEqual((AS_OF - date(2026, 5, 28)).days, 91)
        self.assertIn("DRILL_OVERDUE", self.issue_types(over_limit))

    # -------------------------------------------------- validation
    def test_unrecognised_backup_method_is_a_finding_not_an_exception(self):
        report = self.engine.audit_recovery_plans(
            [fully_ready_plan(backup_method="PAPER_IN_A_DRAWER",
                              shamir_threshold=0, shamir_total_shards=0,
                              verified_shards_available=0,
                              max_shards_at_single_location=0)],
            as_of_date=AS_OF,
        )
        self.assertEqual(
            self.issue_by_type(report, "INVALID_BACKUP_METHOD").severity,
            SEVERITY_CRITICAL,
        )

    def test_threshold_above_total_raises(self):
        with self.assertRaises(KeyRecoveryPlanError):
            self.engine.audit_recovery_plans(
                [fully_ready_plan(shamir_threshold=6, shamir_total_shards=5)],
                as_of_date=AS_OF,
            )

    def test_more_verified_shards_than_exist_raises(self):
        with self.assertRaises(KeyRecoveryPlanError):
            self.engine.audit_recovery_plans(
                [fully_ready_plan(verified_shards_available=6)], as_of_date=AS_OF
            )

    def test_negative_count_raises(self):
        with self.assertRaises(KeyRecoveryPlanError):
            self.engine.audit_recovery_plans(
                [fully_ready_plan(incident_response_contacts=-1)], as_of_date=AS_OF
            )

    def test_future_drill_date_raises(self):
        with self.assertRaises(KeyRecoveryPlanError):
            self.engine.audit_recovery_plans(
                [fully_ready_plan(last_drill_date=date(2026, 9, 1))], as_of_date=AS_OF
            )

    def test_unknown_wallet_type_raises(self):
        with self.assertRaises(KeyRecoveryPlanError):
            self.engine.audit_recovery_plans(
                [fully_ready_plan(wallet_type="FROZEN")], as_of_date=AS_OF
            )

    def test_blank_plan_id_raises(self):
        with self.assertRaises(KeyRecoveryPlanError):
            self.engine.audit_recovery_plans(
                [fully_ready_plan(plan_id="   ")], as_of_date=AS_OF
            )

    def test_duplicate_plan_ids_raise(self):
        with self.assertRaises(KeyRecoveryPlanError):
            self.engine.audit_recovery_plans(
                [fully_ready_plan(), fully_ready_plan()], as_of_date=AS_OF
            )

    def test_shamir_plan_without_shard_counts_raises(self):
        with self.assertRaises(KeyRecoveryPlanError):
            self.engine.audit_recovery_plans(
                [fully_ready_plan(shamir_threshold=0, shamir_total_shards=0,
                                  verified_shards_available=0,
                                  max_shards_at_single_location=0)],
                as_of_date=AS_OF,
            )

    def test_datetime_instead_of_date_raises(self):
        """datetime subclasses date, so an unguarded engine would TypeError instead."""
        with self.assertRaises(KeyRecoveryPlanError):
            self.engine.audit_recovery_plans(
                [fully_ready_plan(last_drill_date=datetime(2026, 7, 28, 9, 0))],
                as_of_date=AS_OF,
            )

    def test_single_plan_instead_of_list_raises(self):
        with self.assertRaises(KeyRecoveryPlanError):
            self.engine.audit_recovery_plans(fully_ready_plan(), as_of_date=AS_OF)

    def test_invalid_config_raises_at_construction(self):
        with self.assertRaises(KeyRecoveryPlanError):
            RecoveryPlanForLostOrCompromisedKeysEngine(
                RecoveryPlanForLostOrCompromisedKeysConfig(max_days_since_drill=0)
            )

    # -------------------------------------------------- report semantics
    def test_empty_plan_set_is_not_ready(self):
        report = self.engine.audit_recovery_plans([], as_of_date=AS_OF)
        self.assertEqual(report.status, "RECOVERY_PLAN_NOT_READY")
        self.assertEqual(report.total_plans, 0)
        self.assertIn("no plans supplied", report.audit_notes)

    def test_mixed_batch_counts_plans_not_issues(self):
        plans = [
            fully_ready_plan(plan_id="OK_1"),
            fully_ready_plan(plan_id="BAD_1", verified_shards_available=0,
                             sweep_wallet_configured=False),
        ]
        report = self.engine.audit_recovery_plans(plans, as_of_date=AS_OF)
        self.assertEqual(report.ready_count, 1)
        self.assertEqual(report.not_ready_count, 1)
        self.assertEqual(
            self.issue_types(report), {"SHARDS_BELOW_THRESHOLD", "NO_SWEEP_WALLET"}
        )
        self.assertEqual(
            sum(report.issues_by_severity.values()), len(report.issues)
        )
        self.assertTrue(all(i.plan_id == "BAD_1" for i in report.issues))


if __name__ == '__main__':
    unittest.main()

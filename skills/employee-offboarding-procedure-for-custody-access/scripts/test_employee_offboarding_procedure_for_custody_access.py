"""Unit tests for employee-offboarding-procedure-for-custody-access."""
import logging
import unittest

from employee_offboarding_procedure_for_custody_access import (
    CREDENTIAL_REVOCATION_STEPS,
    OFFBOARDING_STEPS,
    RISK_CRITICAL_KEY_EXPOSURE,
    RISK_ELEVATED_ROTATION_PENDING,
    RISK_HIGH_CREDENTIAL_EXPOSURE,
    RISK_LOW,
    RISK_PENDING_LOW,
    STEP_CUSTODY_PORTAL_REVOKED,
    STEP_EXCHANGE_API_KEYS_REVOKED,
    STEP_HARDWARE_TOKEN_WIPED,
    STEP_IDP_SSO_REVOKED,
    STEP_MULTISIG_MPC_KEY_ROTATED,
    CustodyOffboardingEngine,
    CustodyOffboardingError,
    EmployeeOffboardingRecord,
)

# A fixed clock keeps every assertion reproducible; the engine only falls back to
# time.time() when no explicit timestamp is supplied.
NOW = 1_700_000_000.0
HOUR = 3600.0

ALL_STEPS = list(OFFBOARDING_STEPS)


def record(**overrides):
    base = dict(
        employee_id="EMP_KEY_01",
        employee_name="J. Doe",
        role="KEY_CUSTODIAN",
        termination_time_epoch=NOW - HOUR,
        held_custody_keys=True,
        completed_steps=[],
    )
    base.update(overrides)
    return EmployeeOffboardingRecord(**base)


class TestCustodyOffboardingEngine(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # The engine logs at CRITICAL for exposure escalations; keep test output clean.
        logging.disable(logging.CRITICAL)

    @classmethod
    def tearDownClass(cls):
        logging.disable(logging.NOTSET)

    def setUp(self):
        self.engine = CustodyOffboardingEngine(key_rotation_sla_hours=24.0)

    # -- completion scoring ------------------------------------------------

    def test_full_offboarding_compliance(self):
        report = self.engine.evaluate_offboarding_status(
            record(completed_steps=ALL_STEPS), current_time_epoch=NOW)

        self.assertTrue(report.is_fully_compliant)
        self.assertEqual(report.completion_percentage, 100.0)
        self.assertEqual(report.key_exposure_risk, RISK_LOW)
        self.assertEqual(report.pending_steps, [])
        self.assertEqual(report.overdue_steps, [])
        self.assertEqual(report.hours_since_termination, 1.0)

    def test_duplicate_attestations_do_not_inflate_completion(self):
        report = self.engine.evaluate_offboarding_status(
            record(completed_steps=[STEP_IDP_SSO_REVOKED] * 4), current_time_epoch=NOW)

        self.assertEqual(report.completion_percentage, 20.0)
        self.assertFalse(report.is_fully_compliant)

    def test_unrecognised_step_is_rejected_not_counted(self):
        # Regression: a misspelled step name previously counted toward completion,
        # producing scores above 100% while real steps stayed pending.
        with self.assertRaises(CustodyOffboardingError) as ctx:
            self.engine.evaluate_offboarding_status(
                record(completed_steps=ALL_STEPS + ["MULTISIG_MPC_ROTATED"]),
                current_time_epoch=NOW)
        self.assertIn("MULTISIG_MPC_ROTATED", str(ctx.exception))

    def test_waived_step_leaves_the_denominator(self):
        report = self.engine.evaluate_offboarding_status(
            record(
                held_custody_keys=False,
                role="RESEARCH_ANALYST",
                completed_steps=[
                    STEP_IDP_SSO_REVOKED,
                    STEP_CUSTODY_PORTAL_REVOKED,
                    STEP_MULTISIG_MPC_KEY_ROTATED,
                    STEP_HARDWARE_TOKEN_WIPED,
                ],
                not_applicable_steps={
                    STEP_EXCHANGE_API_KEYS_REVOKED: "never issued an exchange key; "
                                                    "confirmed against key inventory",
                },
            ),
            current_time_epoch=NOW)

        self.assertEqual(report.completion_percentage, 100.0)
        self.assertTrue(report.is_fully_compliant)
        self.assertEqual(report.waived_steps, [STEP_EXCHANGE_API_KEYS_REVOKED])
        self.assertEqual(report.key_exposure_risk, RISK_LOW)

    def test_waiver_without_justification_is_rejected(self):
        with self.assertRaises(CustodyOffboardingError):
            self.engine.evaluate_offboarding_status(
                record(held_custody_keys=False,
                       not_applicable_steps={STEP_HARDWARE_TOKEN_WIPED: "   "}),
                current_time_epoch=NOW)

    def test_key_holder_cannot_waive_key_rotation(self):
        with self.assertRaises(CustodyOffboardingError):
            self.engine.evaluate_offboarding_status(
                record(not_applicable_steps={
                    STEP_MULTISIG_MPC_KEY_ROTATED: "we would rather not"}),
                current_time_epoch=NOW)

    def test_sso_revocation_can_never_be_waived(self):
        with self.assertRaises(CustodyOffboardingError):
            self.engine.evaluate_offboarding_status(
                record(held_custody_keys=False,
                       not_applicable_steps={STEP_IDP_SSO_REVOKED: "contractor"}),
                current_time_epoch=NOW)

    def test_step_cannot_be_both_completed_and_waived(self):
        with self.assertRaises(CustodyOffboardingError):
            self.engine.evaluate_offboarding_status(
                record(held_custody_keys=False,
                       completed_steps=[STEP_HARDWARE_TOKEN_WIPED],
                       not_applicable_steps={
                           STEP_HARDWARE_TOKEN_WIPED: "no device issued"}),
                current_time_epoch=NOW)

    # -- risk classification -----------------------------------------------

    def test_unresolved_key_exposure_risk_alert(self):
        report = self.engine.evaluate_offboarding_status(
            record(
                employee_id="EMP_KEY_02",
                role="DEVOPS",
                termination_time_epoch=NOW - 30.0 * HOUR,
                completed_steps=[STEP_IDP_SSO_REVOKED, STEP_EXCHANGE_API_KEYS_REVOKED],
            ),
            current_time_epoch=NOW)

        self.assertFalse(report.is_fully_compliant)
        self.assertEqual(report.completion_percentage, 40.0)
        self.assertEqual(report.key_exposure_risk, RISK_CRITICAL_KEY_EXPOSURE)
        self.assertIn(STEP_MULTISIG_MPC_KEY_ROTATED, report.pending_steps)
        self.assertIn(STEP_MULTISIG_MPC_KEY_ROTATED, report.overdue_steps)

    def test_live_exchange_keys_escalate_even_without_custody_keys(self):
        # Regression: an un-revoked exchange API key 100 hours after termination was
        # previously reported as LOW_RISK because the employee held no key shard.
        report = self.engine.evaluate_offboarding_status(
            record(
                held_custody_keys=False,
                role="QUANT_DEV",
                termination_time_epoch=NOW - 100.0 * HOUR,
                completed_steps=[STEP_IDP_SSO_REVOKED],
            ),
            current_time_epoch=NOW)

        self.assertEqual(report.key_exposure_risk, RISK_HIGH_CREDENTIAL_EXPOSURE)
        self.assertIn(STEP_EXCHANGE_API_KEYS_REVOKED, report.overdue_steps)
        self.assertIn(STEP_CUSTODY_PORTAL_REVOKED, report.overdue_steps)
        # Key rotation is not overdue for someone who held no signing material.
        self.assertNotIn(STEP_MULTISIG_MPC_KEY_ROTATED, report.overdue_steps)

    def test_key_rotation_outranks_credential_exposure(self):
        report = self.engine.evaluate_offboarding_status(
            record(termination_time_epoch=NOW - 48.0 * HOUR),
            current_time_epoch=NOW)

        self.assertEqual(report.key_exposure_risk, RISK_CRITICAL_KEY_EXPOSURE)

    def test_rotation_pending_within_sla_is_not_low_risk(self):
        report = self.engine.evaluate_offboarding_status(
            record(
                termination_time_epoch=NOW - 2.0 * HOUR,
                completed_steps=[s for s in ALL_STEPS if s != STEP_MULTISIG_MPC_KEY_ROTATED],
            ),
            current_time_epoch=NOW)

        self.assertEqual(report.key_exposure_risk, RISK_ELEVATED_ROTATION_PENDING)
        self.assertEqual(report.overdue_steps, [])

    def test_key_rotation_sla_boundary_is_exclusive(self):
        pending_rotation = [s for s in ALL_STEPS if s != STEP_MULTISIG_MPC_KEY_ROTATED]

        at_sla = self.engine.evaluate_offboarding_status(
            record(termination_time_epoch=NOW - 24.0 * HOUR,
                   completed_steps=pending_rotation),
            current_time_epoch=NOW)
        just_past = self.engine.evaluate_offboarding_status(
            record(termination_time_epoch=NOW - (24.0 * HOUR + 1.0),
                   completed_steps=pending_rotation),
            current_time_epoch=NOW)

        self.assertEqual(at_sla.key_exposure_risk, RISK_ELEVATED_ROTATION_PENDING)
        self.assertEqual(just_past.key_exposure_risk, RISK_CRITICAL_KEY_EXPOSURE)

    def test_hardware_token_pending_alone_is_low_priority(self):
        report = self.engine.evaluate_offboarding_status(
            record(
                held_custody_keys=False,
                termination_time_epoch=NOW - 100.0 * HOUR,
                completed_steps=[s for s in ALL_STEPS if s != STEP_HARDWARE_TOKEN_WIPED],
            ),
            current_time_epoch=NOW)

        self.assertEqual(report.key_exposure_risk, RISK_PENDING_LOW)
        self.assertEqual(report.overdue_steps, [])

    def test_future_dated_termination_is_not_yet_overdue(self):
        # A departure prepared a week in advance, or clock skew against the HR
        # system, must not read as overdue access.
        report = self.engine.evaluate_offboarding_status(
            record(termination_time_epoch=NOW + 168.0 * HOUR), current_time_epoch=NOW)

        self.assertEqual(report.overdue_steps, [])
        self.assertEqual(report.key_exposure_risk, RISK_ELEVATED_ROTATION_PENDING)
        self.assertLess(report.hours_since_termination, 0.0)

    def test_credential_grace_period_is_configurable(self):
        engine = CustodyOffboardingEngine(credential_revocation_sla_hours=4.0)
        rec = record(held_custody_keys=False, termination_time_epoch=NOW - 2.0 * HOUR)

        within = engine.evaluate_offboarding_status(rec, current_time_epoch=NOW)
        self.assertEqual(within.key_exposure_risk, RISK_PENDING_LOW)

        past = engine.evaluate_offboarding_status(
            record(held_custody_keys=False, termination_time_epoch=NOW - 5.0 * HOUR),
            current_time_epoch=NOW)
        self.assertEqual(past.key_exposure_risk, RISK_HIGH_CREDENTIAL_EXPOSURE)

    # -- configuration and input validation --------------------------------

    def test_negative_sla_configuration_is_rejected(self):
        with self.assertRaises(CustodyOffboardingError):
            CustodyOffboardingEngine(key_rotation_sla_hours=-1.0)
        with self.assertRaises(CustodyOffboardingError):
            CustodyOffboardingEngine(credential_revocation_sla_hours=float("nan"))

    def test_malformed_record_fields_are_rejected(self):
        with self.assertRaises(CustodyOffboardingError):
            self.engine.evaluate_offboarding_status(
                record(employee_id="  "), current_time_epoch=NOW)
        with self.assertRaises(CustodyOffboardingError):
            self.engine.evaluate_offboarding_status(
                record(termination_time_epoch=float("inf")), current_time_epoch=NOW)
        with self.assertRaises(CustodyOffboardingError):
            self.engine.evaluate_offboarding_status(
                record(held_custody_keys="yes"), current_time_epoch=NOW)
        with self.assertRaises(CustodyOffboardingError):
            self.engine.evaluate_offboarding_status(
                record(), current_time_epoch=float("nan"))

    def test_collection_shapes_are_rejected(self):
        # A bare string would otherwise be iterated character by character.
        with self.assertRaises(CustodyOffboardingError):
            self.engine.evaluate_offboarding_status(
                record(completed_steps=STEP_IDP_SSO_REVOKED), current_time_epoch=NOW)
        with self.assertRaises(CustodyOffboardingError):
            self.engine.evaluate_offboarding_status(
                record(held_custody_keys=False,
                       not_applicable_steps=[STEP_HARDWARE_TOKEN_WIPED]),
                current_time_epoch=NOW)

    def test_step_taxonomy_is_stable(self):
        self.assertEqual(len(OFFBOARDING_STEPS), 5)
        self.assertTrue(CREDENTIAL_REVOCATION_STEPS.issubset(set(OFFBOARDING_STEPS)))
        self.assertIsInstance(OFFBOARDING_STEPS, tuple)


if __name__ == '__main__':
    unittest.main()

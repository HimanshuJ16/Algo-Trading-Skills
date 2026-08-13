import dataclasses
import unittest
from datetime import datetime, timedelta
from annual_compliance_attestation_workflow import (
    AnnualComplianceChecklist,
    AnnualComplianceAttestationEngine,
    AttestationReport,
)


class TestAnnualComplianceAttestationWorkflow(unittest.TestCase):
    def setUp(self):
        self.engine = AnnualComplianceAttestationEngine()
        self.reporting_year = 2026

    def _get_valid_date(self):
        return datetime(self.reporting_year, 11, 15)

    def _valid_ria_checklist(self, **overrides):
        defaults = dict(
            reporting_year=self.reporting_year,
            is_broker_dealer=False,
            legal_entity_id="RIA-LE-001",
            annual_policy_review_date=self._get_valid_date(),
            annual_review_documentation_date=self._get_valid_date(),
            algo_code_integrity_review_date=self._get_valid_date(),
            trade_surveillance_test_date=self._get_valid_date(),
        )
        defaults.update(overrides)
        return AnnualComplianceChecklist(**defaults)

    def _valid_bd_checklist(self, **overrides):
        signing = datetime(self.reporting_year, 12, 1)
        defaults = dict(
            reporting_year=self.reporting_year,
            is_broker_dealer=True,
            legal_entity_id="BD-LE-001",
            annual_policy_review_date=self._get_valid_date(),
            annual_review_documentation_date=self._get_valid_date(),
            algo_code_integrity_review_date=self._get_valid_date(),
            trade_surveillance_test_date=self._get_valid_date(),
            ceo_cco_meeting_date=datetime(self.reporting_year, 6, 15),
            ceo_certification_signed_date=signing,
            certification_signing_date=signing,
            prior_certification_date=datetime(self.reporting_year - 1, 12, 1),
            board_submission_date=signing + timedelta(days=10),
            audit_committee_acknowledgment_date=signing + timedelta(days=12),
            rule_3120_report_date=datetime(self.reporting_year, 5, 1),
            rule_15c3_5_annual_review_date=self._get_valid_date(),
            rule_15c3_5_ceo_certification_date=self._get_valid_date(),
        )
        defaults.update(overrides)
        return AnnualComplianceChecklist(**defaults)

    # --- happy paths -------------------------------------------------------

    def test_valid_hedge_fund(self):
        checklist = self._valid_ria_checklist()
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation)
        self.assertEqual(report.missing_requirements, [])
        self.assertEqual(report.missing_requirement_codes, [])
        self.assertTrue(report.content_hash)
        self.assertIsInstance(report, AttestationReport)

    def test_valid_broker_dealer(self):
        checklist = self._valid_bd_checklist()
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation, report.missing_requirements)
        self.assertEqual(report.missing_requirements, [])

    # --- reporting_year validation -----------------------------------------

    def test_reporting_year_none_raises(self):
        with self.assertRaises(ValueError):
            self._valid_ria_checklist(reporting_year=None)

    def test_reporting_year_str_raises(self):
        with self.assertRaises(ValueError):
            self._valid_ria_checklist(reporting_year="2026")

    def test_reporting_year_out_of_range_raises(self):
        with self.assertRaises(ValueError):
            self._valid_ria_checklist(reporting_year=1999)
        with self.assertRaises(ValueError):
            self._valid_ria_checklist(reporting_year=2101)

    def test_reporting_year_bool_raises(self):
        # bool is a subclass of int; must be rejected explicitly.
        with self.assertRaises(ValueError):
            self._valid_ria_checklist(reporting_year=True)

    def test_legal_entity_id_required(self):
        with self.assertRaises((ValueError, TypeError)):
            AnnualComplianceChecklist(
                reporting_year=self.reporting_year,
                is_broker_dealer=False,
                legal_entity_id="",
                annual_policy_review_date=self._get_valid_date(),
                annual_review_documentation_date=self._get_valid_date(),
                algo_code_integrity_review_date=self._get_valid_date(),
                trade_surveillance_test_date=self._get_valid_date(),
            )

    # --- frozen dataclass --------------------------------------------------

    def test_checklist_is_frozen(self):
        checklist = self._valid_ria_checklist()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            checklist.is_broker_dealer = True  # type: ignore[misc]

    def test_report_is_frozen(self):
        report = self.engine.evaluate(self._valid_ria_checklist())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            report.is_ready_for_attestation = False  # type: ignore[misc]

    # --- field year mismatch via subTest ------------------------------------

    def test_each_date_field_year_mismatch(self):
        fields = [
            "annual_policy_review_date",
            "annual_review_documentation_date",
            "algo_code_integrity_review_date",
            "trade_surveillance_test_date",
        ]
        for field in fields:
            with self.subTest(field=field):
                checklist = self._valid_ria_checklist(
                    **{field: datetime(self.reporting_year - 1, 11, 15)}
                )
                report = self.engine.evaluate(checklist)
                self.assertFalse(report.is_ready_for_attestation)

    def test_each_missing_date_field_alone(self):
        fields = [
            "annual_policy_review_date",
            "annual_review_documentation_date",
            "algo_code_integrity_review_date",
            "trade_surveillance_test_date",
        ]
        for field in fields:
            with self.subTest(field=field):
                checklist = self._valid_ria_checklist(**{field: None})
                report = self.engine.evaluate(checklist)
                self.assertFalse(report.is_ready_for_attestation)
                self.assertTrue(report.missing_requirement_codes)

    # --- SEC 206(4)-7 ------------------------------------------------------

    def test_missing_sec_policy_review(self):
        checklist = self._valid_ria_checklist(annual_policy_review_date=None)
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("REQ_SEC_206_4_7_POLICY_REVIEW", report.missing_requirement_codes)

    def test_missing_sec_review_documentation(self):
        checklist = self._valid_ria_checklist(annual_review_documentation_date=None)
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("REQ_SEC_206_4_7_POLICY_REVIEW", report.missing_requirement_codes)

    # --- Quant controls ----------------------------------------------------

    def test_missing_quant_controls(self):
        checklist = self._valid_ria_checklist(algo_code_integrity_review_date=None)
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn(
            "REQ_QUANT_ALGO_CODE_INTEGRITY_REVIEW", report.missing_requirement_codes
        )

    # --- FINRA 3130 rolling window & ordering ------------------------------

    def test_bd_missing_ceo_meeting(self):
        checklist = self._valid_bd_checklist(ceo_cco_meeting_date=None)
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("REQ_FINRA_3130_CEO_CCO_MEETING", report.missing_requirement_codes)

    def test_bd_missing_ceo_cert_signed_date(self):
        checklist = self._valid_bd_checklist(ceo_certification_signed_date=None)
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("REQ_FINRA_3130_CEO_CERT", report.missing_requirement_codes)

    def test_bd_stale_meeting_outside_rolling_window(self):
        # Meeting 13 months before signing => outside 12-month window.
        signing = datetime(self.reporting_year, 3, 1)
        stale_meeting = signing - timedelta(days=395)
        checklist = self._valid_bd_checklist(
            ceo_cco_meeting_date=stale_meeting,
            certification_signing_date=signing,
            ceo_certification_signed_date=signing,
            board_submission_date=signing + timedelta(days=10),
        )
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("REQ_FINRA_3130_CEO_CCO_MEETING", report.missing_requirement_codes)

    def test_bd_dec_meeting_for_march_anniversary_is_valid(self):
        # Prior cert March 2025; signing March 2026; Dec 2025 meeting is
        # within 12 months of signing and before the March anniversary.
        prior = datetime(self.reporting_year - 1, 3, 1)
        signing = datetime(self.reporting_year, 3, 1)
        dec_meeting = datetime(self.reporting_year - 1, 12, 15)
        checklist = self._valid_bd_checklist(
            prior_certification_date=prior,
            certification_signing_date=signing,
            ceo_cco_meeting_date=dec_meeting,
            ceo_certification_signed_date=signing,
            board_submission_date=signing + timedelta(days=10),
            rule_3120_report_date=datetime(self.reporting_year - 1, 9, 1),
            rule_15c3_5_annual_review_date=datetime(self.reporting_year, 2, 1),
            rule_15c3_5_ceo_certification_date=datetime(self.reporting_year, 2, 1),
        )
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation, report.missing_requirements)

    def test_bd_jan_meeting_after_prior_march_anniversary_stale(self):
        # Prior cert March 2024; Jan 2026 meeting is > 12 months after the
        # prior certification anniversary => stale per the anniversary rule.
        prior = datetime(self.reporting_year - 2, 3, 1)
        signing = datetime(self.reporting_year, 3, 1)
        jan_meeting = datetime(self.reporting_year, 1, 15)
        checklist = self._valid_bd_checklist(
            prior_certification_date=prior,
            certification_signing_date=signing,
            ceo_cco_meeting_date=jan_meeting,
            ceo_certification_signed_date=signing,
            board_submission_date=signing + timedelta(days=10),
            rule_3120_report_date=datetime(self.reporting_year - 1, 9, 1),
            rule_15c3_5_annual_review_date=datetime(self.reporting_year, 2, 1),
            rule_15c3_5_ceo_certification_date=datetime(self.reporting_year, 2, 1),
        )
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("REQ_FINRA_3130_CEO_CCO_MEETING", report.missing_requirement_codes)

    def test_bd_meeting_after_signing_rubber_stamp(self):
        # Meeting post-dates the certification signature => rubber-stamping.
        signing = datetime(self.reporting_year, 6, 1)
        late_meeting = datetime(self.reporting_year, 7, 1)
        checklist = self._valid_bd_checklist(
            certification_signing_date=signing,
            ceo_cco_meeting_date=late_meeting,
            ceo_certification_signed_date=signing,
            board_submission_date=signing + timedelta(days=10),
        )
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn(
            "REQ_FINRA_3130_MEETING_PRECEDES_CERT", report.missing_requirement_codes
        )

    # --- FINRA 3130.04 board submission ------------------------------------

    def test_bd_board_submission_late(self):
        signing = datetime(self.reporting_year, 6, 1)
        late_board = signing + timedelta(days=50)
        checklist = self._valid_bd_checklist(
            certification_signing_date=signing,
            ceo_certification_signed_date=signing,
            board_submission_date=late_board,
        )
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn(
            "REQ_FINRA_3130_04_BOARD_SUBMISSION", report.missing_requirement_codes
        )

    def test_bd_board_submission_missing(self):
        checklist = self._valid_bd_checklist(board_submission_date=None)
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn(
            "REQ_FINRA_3130_04_BOARD_SUBMISSION", report.missing_requirement_codes
        )

    # --- FINRA 3120 --------------------------------------------------------

    def test_bd_missing_rule_3120_report(self):
        checklist = self._valid_bd_checklist(rule_3120_report_date=None)
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("REQ_FINRA_3120_REPORT", report.missing_requirement_codes)

    # --- SEC 15c3-5 --------------------------------------------------------

    def test_bd_missing_15c3_5_annual_review(self):
        checklist = self._valid_bd_checklist(rule_15c3_5_annual_review_date=None)
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("REQ_SEC_15C3_5_ANNUAL_REVIEW", report.missing_requirement_codes)

    def test_bd_missing_15c3_5_ceo_cert(self):
        checklist = self._valid_bd_checklist(rule_15c3_5_ceo_certification_date=None)
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("REQ_SEC_15C3_5_CEO_CERT", report.missing_requirement_codes)

    # --- RIA must not be gated on BD-only rules ----------------------------

    def test_ria_not_checked_for_bd_rules(self):
        # RIA checklist with all BD fields None should still be ready.
        checklist = self._valid_ria_checklist(
            ceo_cco_meeting_date=None,
            ceo_certification_signed_date=None,
            rule_3120_report_date=None,
            rule_15c3_5_annual_review_date=None,
            rule_15c3_5_ceo_certification_date=None,
        )
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation, report.missing_requirements)


# dataclasses imported at top of module.


if __name__ == "__main__":
    unittest.main()

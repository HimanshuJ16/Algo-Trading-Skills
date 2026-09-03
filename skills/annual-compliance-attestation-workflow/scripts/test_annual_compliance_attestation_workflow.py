import dataclasses
import unittest
from datetime import date, datetime, timedelta, timezone

from annual_compliance_attestation_workflow import (
    AnnualComplianceChecklist,
    AnnualComplianceAttestationEngine,
    AttestationReport,
    _shift_years,
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

    def test_evaluate_is_deterministic(self):
        # Same evidence must always yield the same verdict and codes: an archived
        # verdict has to be reproducible at examination time.
        checklist = self._valid_bd_checklist(ceo_cco_meeting_date=None)
        first = self.engine.evaluate(checklist)
        second = self.engine.evaluate(checklist)
        self.assertEqual(first.is_ready_for_attestation, second.is_ready_for_attestation)
        self.assertEqual(first.missing_requirement_codes, second.missing_requirement_codes)

    def test_generated_at_is_timezone_aware(self):
        report = self.engine.evaluate(self._valid_ria_checklist())
        self.assertIsNotNone(report.generated_at.tzinfo)
        self.assertEqual(report.generated_at.utcoffset(), timedelta(0))

    # --- construction-time validation --------------------------------------

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
            self._valid_ria_checklist(legal_entity_id="")

    def test_legal_entity_id_whitespace_only_rejected(self):
        with self.assertRaises(ValueError):
            self._valid_ria_checklist(legal_entity_id="   ")

    def test_non_bool_flags_rejected(self):
        for flag in ("is_broker_dealer", "has_market_access"):
            with self.subTest(flag=flag):
                with self.assertRaises(ValueError):
                    self._valid_ria_checklist(**{flag: 1})

    def test_date_object_rejected(self):
        # datetime.date exposes .year, so it would pass the calendar-year checks
        # and then raise TypeError on the first ordering comparison.
        with self.assertRaises(ValueError):
            self._valid_ria_checklist(
                annual_policy_review_date=date(self.reporting_year, 11, 15)
            )

    def test_string_date_rejected(self):
        with self.assertRaises(ValueError):
            self._valid_ria_checklist(annual_policy_review_date="2026-11-15")

    def test_mixed_naive_and_aware_dates_rejected(self):
        with self.assertRaises(ValueError):
            self._valid_ria_checklist(
                annual_policy_review_date=datetime(
                    self.reporting_year, 11, 15, tzinfo=timezone.utc
                )
            )

    def test_transposed_prior_certification_date_rejected(self):
        # A "prior" certification dated after this cycle's certification implies a
        # future anniversary deadline, which every gate would pass silently.
        signing = datetime(self.reporting_year, 12, 1)
        with self.assertRaises(ValueError):
            self._valid_bd_checklist(
                certification_signing_date=signing,
                ceo_certification_signed_date=signing,
                prior_certification_date=signing + timedelta(days=1),
            )
        with self.assertRaises(ValueError):
            self._valid_bd_checklist(
                certification_signing_date=signing,
                ceo_certification_signed_date=signing,
                prior_certification_date=signing,
            )

    def test_timezone_aware_checklist_evaluates_without_error(self):
        # Regression: comparing a tz-aware evidence date against a naive
        # wall-clock reference used to raise TypeError mid-evaluation.
        utc = timezone.utc
        signing = datetime(self.reporting_year, 12, 1, tzinfo=utc)
        checklist = AnnualComplianceChecklist(
            reporting_year=self.reporting_year,
            is_broker_dealer=True,
            legal_entity_id="BD-TZ-001",
            annual_policy_review_date=datetime(self.reporting_year, 11, 15, tzinfo=utc),
            annual_review_documentation_date=datetime(self.reporting_year, 11, 15, tzinfo=utc),
            algo_code_integrity_review_date=datetime(self.reporting_year, 11, 15, tzinfo=utc),
            trade_surveillance_test_date=datetime(self.reporting_year, 11, 15, tzinfo=utc),
            ceo_cco_meeting_date=datetime(self.reporting_year, 6, 15, tzinfo=utc),
            ceo_certification_signed_date=signing,
            certification_signing_date=signing,
            prior_certification_date=datetime(self.reporting_year - 1, 12, 1, tzinfo=utc),
            board_submission_date=signing + timedelta(days=10),
            rule_3120_report_date=datetime(self.reporting_year, 5, 1, tzinfo=utc),
            rule_15c3_5_annual_review_date=datetime(self.reporting_year, 11, 15, tzinfo=utc),
            rule_15c3_5_ceo_certification_date=datetime(self.reporting_year, 11, 15, tzinfo=utc),
        )
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation, report.missing_requirements)

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

    # --- SEC 206(4)-7 / 204-2 ----------------------------------------------

    def test_missing_sec_policy_review(self):
        checklist = self._valid_ria_checklist(annual_policy_review_date=None)
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("REQ_SEC_206_4_7_POLICY_REVIEW", report.missing_requirement_codes)

    def test_missing_review_record_uses_204_2_code(self):
        # The record of the annual review is a Rule 204-2(a)(17)(ii) obligation.
        # The 206(4)-7 writing amendment was vacated in 2024, so the block must
        # not be attributed to 206(4)-7.
        checklist = self._valid_ria_checklist(annual_review_documentation_date=None)
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn(
            "REQ_SEC_204_2_A17_ANNUAL_REVIEW_RECORD", report.missing_requirement_codes
        )
        self.assertNotIn(
            "REQ_SEC_206_4_7_POLICY_REVIEW", report.missing_requirement_codes
        )

    def test_review_and_record_emit_distinct_codes(self):
        # Regression: both gaps used to emit the same code twice, which defeats
        # code-based routing.
        checklist = self._valid_ria_checklist(
            annual_policy_review_date=None, annual_review_documentation_date=None
        )
        report = self.engine.evaluate(checklist)
        codes = report.missing_requirement_codes
        self.assertIn("REQ_SEC_206_4_7_POLICY_REVIEW", codes)
        self.assertIn("REQ_SEC_204_2_A17_ANNUAL_REVIEW_RECORD", codes)
        self.assertEqual(len(codes), len(set(codes)))

    # --- Quant controls ----------------------------------------------------

    def test_missing_quant_controls(self):
        checklist = self._valid_ria_checklist(algo_code_integrity_review_date=None)
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn(
            "REQ_QUANT_ALGO_CODE_INTEGRITY_REVIEW", report.missing_requirement_codes
        )

    # --- FINRA 3130(c)(2) meeting window -----------------------------------

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
        # Meeting 13 months before execution => outside the preceding 12 months.
        signing = datetime(self.reporting_year, 3, 1)
        checklist = self._valid_bd_checklist(
            ceo_cco_meeting_date=signing - timedelta(days=395),
            certification_signing_date=signing,
            ceo_certification_signed_date=signing,
            prior_certification_date=datetime(self.reporting_year - 1, 3, 1),
            board_submission_date=signing + timedelta(days=10),
            rule_3120_report_date=datetime(self.reporting_year - 1, 9, 1),
            rule_15c3_5_annual_review_date=datetime(self.reporting_year, 2, 1),
            rule_15c3_5_ceo_certification_date=datetime(self.reporting_year, 2, 1),
        )
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("REQ_FINRA_3130_CEO_CCO_MEETING", report.missing_requirement_codes)
        # The certification itself was timely, so the anniversary rule must stay silent.
        self.assertNotIn(
            "REQ_FINRA_3130_CERT_ANNIVERSARY", report.missing_requirement_codes
        )

    def test_bd_meeting_exactly_twelve_months_before_execution_passes(self):
        # "in the preceding 12 months" is inclusive at the boundary.
        signing = datetime(self.reporting_year, 3, 1)
        checklist = self._valid_bd_checklist(
            ceo_cco_meeting_date=datetime(self.reporting_year - 1, 3, 1),
            certification_signing_date=signing,
            ceo_certification_signed_date=signing,
            prior_certification_date=datetime(self.reporting_year - 1, 3, 1),
            board_submission_date=signing + timedelta(days=10),
            rule_3120_report_date=datetime(self.reporting_year - 1, 9, 1),
            rule_15c3_5_annual_review_date=datetime(self.reporting_year, 2, 1),
            rule_15c3_5_ceo_certification_date=datetime(self.reporting_year, 2, 1),
        )
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation, report.missing_requirements)

    def test_bd_dec_meeting_for_march_anniversary_is_valid(self):
        # Prior cert March 2025; execution March 2026; a Dec 2025 meeting sits
        # inside the preceding 12 months and the cert lands on the anniversary.
        signing = datetime(self.reporting_year, 3, 1)
        checklist = self._valid_bd_checklist(
            prior_certification_date=datetime(self.reporting_year - 1, 3, 1),
            certification_signing_date=signing,
            ceo_cco_meeting_date=datetime(self.reporting_year - 1, 12, 15),
            ceo_certification_signed_date=signing,
            board_submission_date=signing + timedelta(days=10),
            rule_3120_report_date=datetime(self.reporting_year - 1, 9, 1),
            rule_15c3_5_annual_review_date=datetime(self.reporting_year, 2, 1),
            rule_15c3_5_ceo_certification_date=datetime(self.reporting_year, 2, 1),
        )
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation, report.missing_requirements)

    def test_bd_meeting_after_signing_rubber_stamp(self):
        # Meeting post-dates the certification signature => rubber-stamping.
        signing = datetime(self.reporting_year, 6, 1)
        checklist = self._valid_bd_checklist(
            certification_signing_date=signing,
            ceo_cco_meeting_date=datetime(self.reporting_year, 7, 1),
            ceo_certification_signed_date=signing,
            board_submission_date=signing + timedelta(days=10),
        )
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn(
            "REQ_FINRA_3130_MEETING_PRECEDES_CERT", report.missing_requirement_codes
        )

    # --- FINRA 3130(b) fn.1 certification anniversary ----------------------

    def test_certification_after_prior_anniversary_blocks(self):
        # Regression: footnote 1 to 3130(b) constrains the CERTIFICATION, not the
        # meeting. Prior cert March 2024, executed March 2026 => the 2025
        # anniversary was missed, even though the meeting is perfectly timely.
        signing = datetime(self.reporting_year, 3, 1)
        checklist = self._valid_bd_checklist(
            prior_certification_date=datetime(self.reporting_year - 2, 3, 1),
            certification_signing_date=signing,
            ceo_cco_meeting_date=datetime(self.reporting_year, 1, 15),
            ceo_certification_signed_date=signing,
            board_submission_date=signing + timedelta(days=10),
            rule_3120_report_date=datetime(self.reporting_year - 1, 9, 1),
            rule_15c3_5_annual_review_date=datetime(self.reporting_year, 2, 1),
            rule_15c3_5_ceo_certification_date=datetime(self.reporting_year, 2, 1),
        )
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn(
            "REQ_FINRA_3130_CERT_ANNIVERSARY", report.missing_requirement_codes
        )
        # The meeting was inside the 12-month window; it must not be flagged.
        self.assertNotIn(
            "REQ_FINRA_3130_CEO_CCO_MEETING", report.missing_requirement_codes
        )

    def test_certification_exactly_on_prior_anniversary_passes(self):
        # "no later than on the anniversary date" => the anniversary itself is fine.
        signing = datetime(self.reporting_year, 3, 1)
        checklist = self._valid_bd_checklist(
            prior_certification_date=datetime(self.reporting_year - 1, 3, 1),
            certification_signing_date=signing,
            ceo_cco_meeting_date=datetime(self.reporting_year, 1, 15),
            ceo_certification_signed_date=signing,
            board_submission_date=signing + timedelta(days=10),
            rule_3120_report_date=datetime(self.reporting_year - 1, 9, 1),
            rule_15c3_5_annual_review_date=datetime(self.reporting_year, 2, 1),
            rule_15c3_5_ceo_certification_date=datetime(self.reporting_year, 2, 1),
        )
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation, report.missing_requirements)

    def test_anniversary_uses_calendar_years_not_365_days(self):
        # Regression against timedelta(days=365): 2023-03-01 + 365 days is
        # 2024-02-29 because 2024 has a leap day, so a certification executed on
        # the true 2024-03-01 anniversary would have been wrongly blocked.
        self.assertEqual(
            _shift_years(datetime(2023, 3, 1), 1), datetime(2024, 3, 1)
        )
        self.assertNotEqual(
            datetime(2023, 3, 1) + timedelta(days=365), datetime(2024, 3, 1)
        )
        signing = datetime(2024, 3, 1)
        checklist = AnnualComplianceChecklist(
            reporting_year=2024,
            is_broker_dealer=True,
            legal_entity_id="BD-LEAP-001",
            annual_policy_review_date=datetime(2024, 2, 1),
            annual_review_documentation_date=datetime(2024, 2, 1),
            algo_code_integrity_review_date=datetime(2024, 2, 1),
            trade_surveillance_test_date=datetime(2024, 2, 1),
            ceo_cco_meeting_date=datetime(2024, 1, 15),
            ceo_certification_signed_date=signing,
            certification_signing_date=signing,
            prior_certification_date=datetime(2023, 3, 1),
            board_submission_date=signing + timedelta(days=10),
            rule_3120_report_date=datetime(2023, 9, 1),
            rule_15c3_5_annual_review_date=datetime(2024, 2, 1),
            rule_15c3_5_ceo_certification_date=datetime(2024, 2, 1),
        )
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation, report.missing_requirements)

    def test_deadlines_ignore_time_of_day(self):
        # Regression: a datetime comparison blocked a certification executed at
        # 16:30 on its anniversary because the prior year's was signed at 16:00.
        # These deadlines are day-granular; the clock time carries no regulatory
        # meaning. Same for the 12-month meeting window and the 45-day board limb.
        signing = datetime(self.reporting_year, 4, 15, 16, 30)
        checklist = self._valid_bd_checklist(
            prior_certification_date=datetime(self.reporting_year - 1, 4, 15, 16, 0),
            certification_signing_date=signing,
            ceo_certification_signed_date=signing,
            # exactly 12 months earlier, but later in the day than execution
            ceo_cco_meeting_date=datetime(self.reporting_year - 1, 4, 15, 23, 45),
            # day 45, but past the hour of execution
            board_submission_date=datetime(self.reporting_year, 5, 30, 23, 59),
            rule_3120_report_date=datetime(self.reporting_year - 1, 4, 15, 23, 45),
            rule_15c3_5_annual_review_date=datetime(self.reporting_year, 3, 30),
            rule_15c3_5_ceo_certification_date=datetime(self.reporting_year, 4, 1),
            annual_policy_review_date=datetime(self.reporting_year, 3, 2),
            annual_review_documentation_date=datetime(self.reporting_year, 3, 20),
            algo_code_integrity_review_date=datetime(self.reporting_year, 2, 10),
            trade_surveillance_test_date=datetime(self.reporting_year, 2, 24),
        )
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation, report.missing_requirements)

    def test_same_day_meeting_and_signature_is_not_rubber_stamping(self):
        # Minutes and the certification can carry the same date; only a meeting on
        # a strictly later calendar day is an ordering breach.
        signing = datetime(self.reporting_year, 6, 1, 9, 0)
        checklist = self._valid_bd_checklist(
            certification_signing_date=signing,
            ceo_certification_signed_date=signing,
            ceo_cco_meeting_date=datetime(self.reporting_year, 6, 1, 17, 0),
            board_submission_date=signing + timedelta(days=10),
            rule_15c3_5_annual_review_date=datetime(self.reporting_year, 5, 1),
            rule_15c3_5_ceo_certification_date=datetime(self.reporting_year, 5, 1),
        )
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation, report.missing_requirements)

    def test_shift_years_maps_leap_day_to_feb_28(self):
        self.assertEqual(_shift_years(datetime(2024, 2, 29), 1), datetime(2025, 2, 28))
        self.assertEqual(_shift_years(datetime(2024, 2, 29), -1), datetime(2023, 2, 28))

    # --- anchoring / determinism -------------------------------------------

    def test_bd_without_execution_anchor_blocks_windows(self):
        # No execution date and no as_of: the engine must block rather than
        # silently anchoring the windows on the wall clock.
        checklist = self._valid_bd_checklist(
            certification_signing_date=None, ceo_certification_signed_date=None
        )
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        codes = report.missing_requirement_codes
        self.assertIn("REQ_FINRA_3130_CEO_CCO_MEETING", codes)
        self.assertIn("REQ_FINRA_3120_REPORT", codes)
        self.assertIn("REQ_FINRA_3130_CEO_CERT", codes)

    def test_as_of_supplies_the_anchor_when_no_certification_dates(self):
        as_of = datetime(self.reporting_year, 12, 1)
        checklist = self._valid_bd_checklist(
            certification_signing_date=None, ceo_certification_signed_date=None
        )
        report = self.engine.evaluate(checklist, as_of=as_of)
        codes = report.missing_requirement_codes
        # The windows are now evaluable, so only the unsigned certification and
        # the board submission that depends on it remain.
        self.assertNotIn("REQ_FINRA_3130_CEO_CCO_MEETING", codes)
        self.assertNotIn("REQ_FINRA_3120_REPORT", codes)
        self.assertIn("REQ_FINRA_3130_CEO_CERT", codes)

    def test_as_of_awareness_mismatch_raises(self):
        checklist = self._valid_bd_checklist()
        with self.assertRaises(ValueError):
            self.engine.evaluate(
                checklist, as_of=datetime(self.reporting_year, 12, 1, tzinfo=timezone.utc)
            )

    def test_as_of_must_be_datetime(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate(self._valid_bd_checklist(), as_of="2026-12-01")

    # --- FINRA 3130(c)(3) board submission ---------------------------------

    def test_bd_board_submission_late(self):
        signing = datetime(self.reporting_year, 6, 1)
        checklist = self._valid_bd_checklist(
            certification_signing_date=signing,
            ceo_certification_signed_date=signing,
            ceo_cco_meeting_date=datetime(self.reporting_year, 5, 1),
            board_submission_date=signing + timedelta(days=50),
        )
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn(
            "REQ_FINRA_3130_C3_BOARD_SUBMISSION", report.missing_requirement_codes
        )

    def test_bd_board_submission_exactly_45_days_passes(self):
        signing = datetime(self.reporting_year, 6, 1)
        checklist = self._valid_bd_checklist(
            certification_signing_date=signing,
            ceo_certification_signed_date=signing,
            ceo_cco_meeting_date=datetime(self.reporting_year, 5, 1),
            board_submission_date=signing + timedelta(days=45),
            rule_15c3_5_annual_review_date=datetime(self.reporting_year, 5, 1),
            rule_15c3_5_ceo_certification_date=datetime(self.reporting_year, 5, 1),
        )
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation, report.missing_requirements)

    def test_bd_board_submission_before_execution_is_permitted(self):
        # FINRA 3130(c)(3): "The final report has been submitted ... or will be
        # submitted ... within 45 days". A prior submission is expressly allowed,
        # so the engine must impose no lower bound.
        signing = datetime(self.reporting_year, 6, 1)
        checklist = self._valid_bd_checklist(
            certification_signing_date=signing,
            ceo_certification_signed_date=signing,
            ceo_cco_meeting_date=datetime(self.reporting_year, 5, 1),
            board_submission_date=datetime(self.reporting_year, 5, 20),
            rule_15c3_5_annual_review_date=datetime(self.reporting_year, 5, 1),
            rule_15c3_5_ceo_certification_date=datetime(self.reporting_year, 5, 1),
        )
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation, report.missing_requirements)

    def test_bd_board_submission_missing(self):
        checklist = self._valid_bd_checklist(board_submission_date=None)
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn(
            "REQ_FINRA_3130_C3_BOARD_SUBMISSION", report.missing_requirement_codes
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

    def test_bd_without_market_access_not_gated_on_15c3_5(self):
        # Rule 15c3-5(b) binds a broker-dealer WITH market access. A BD without
        # it must not be blocked on an obligation it does not have.
        checklist = self._valid_bd_checklist(
            has_market_access=False,
            rule_15c3_5_annual_review_date=None,
            rule_15c3_5_ceo_certification_date=None,
        )
        report = self.engine.evaluate(checklist)
        self.assertTrue(report.is_ready_for_attestation, report.missing_requirements)

    def test_market_access_bd_still_gated_on_15c3_5(self):
        checklist = self._valid_bd_checklist(
            has_market_access=True,
            rule_15c3_5_annual_review_date=None,
            rule_15c3_5_ceo_certification_date=None,
        )
        report = self.engine.evaluate(checklist)
        self.assertFalse(report.is_ready_for_attestation)
        self.assertIn("REQ_SEC_15C3_5_ANNUAL_REVIEW", report.missing_requirement_codes)
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

    # --- sealing -----------------------------------------------------------

    def test_seal_binds_evidence_dates(self):
        # Regression: the hash used to cover only the verdict, so evidence dates
        # could be swapped while the seal still matched.
        generated_at = datetime(self.reporting_year, 12, 31, tzinfo=timezone.utc)
        first = self._valid_bd_checklist()
        second = self._valid_bd_checklist(
            ceo_cco_meeting_date=datetime(self.reporting_year, 6, 16)
        )
        hash_first = AnnualComplianceAttestationEngine._compute_content_hash(
            first, True, [], [], generated_at
        )
        hash_second = AnnualComplianceAttestationEngine._compute_content_hash(
            second, True, [], [], generated_at
        )
        self.assertNotEqual(hash_first, hash_second)

    def test_verify_report_accepts_untampered_report(self):
        checklist = self._valid_bd_checklist()
        report = self.engine.evaluate(checklist)
        self.assertTrue(self.engine.verify_report(checklist, report))

    def test_verify_report_detects_mutated_findings_list(self):
        # The frozen dataclass still holds mutable lists; the seal is what makes
        # post-hoc edits detectable.
        checklist = self._valid_bd_checklist(ceo_cco_meeting_date=None)
        report = self.engine.evaluate(checklist)
        report.missing_requirements.clear()
        report.missing_requirement_codes.clear()
        self.assertFalse(self.engine.verify_report(checklist, report))

    def test_verify_report_detects_swapped_evidence(self):
        checklist = self._valid_bd_checklist()
        report = self.engine.evaluate(checklist)
        swapped = self._valid_bd_checklist(
            ceo_cco_meeting_date=datetime(self.reporting_year, 7, 20)
        )
        self.assertFalse(self.engine.verify_report(swapped, report))


if __name__ == "__main__":
    unittest.main()

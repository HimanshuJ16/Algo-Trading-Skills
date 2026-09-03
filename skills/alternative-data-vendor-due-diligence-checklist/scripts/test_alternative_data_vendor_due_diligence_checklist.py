import dataclasses
import datetime
import json
import unittest
from alternative_data_vendor_due_diligence_checklist import (
    Decision,
    DiligenceRecord,
    FlagCode,
    RULE_VERSION,
    VendorDueDiligenceEvaluator,
    VendorDueDiligenceQuestionnaire,
)


def _utc_today():
    """The evaluation date the engine uses.

    Anchoring the DDQ-age boundary tests to the local calendar day would make
    them pass or fail depending on the runner's UTC offset.
    """
    return datetime.datetime.now(datetime.timezone.utc).date()


def _clean_ddq(**overrides):
    """Build a clean, fully-compliant DDQ with a fresh as-of date."""
    defaults = dict(
        vendor_name="CleanWeather API",
        dataset_name="Global Temps",
        has_resell_rights=True,
        is_web_scraped=False,
        scrapes_behind_login=False,
        bypasses_captchas=False,
        contains_pii=False,
        is_gdpr_ccpa_compliant=True,
        has_robust_anonymization=True,
        is_material_non_public_information=False,
        is_tos_compliant=True,
        ddq_as_of_date=_utc_today(),
    )
    defaults.update(overrides)
    return VendorDueDiligenceQuestionnaire(**defaults)


class TestVendorDueDiligenceEvaluator(unittest.TestCase):
    def setUp(self):
        self.evaluator = VendorDueDiligenceEvaluator()

    def test_clean_vendor_approved(self):
        ddq = _clean_ddq()
        record = self.evaluator.evaluate(ddq)
        self.assertIsInstance(record, DiligenceRecord)
        self.assertTrue(record.is_approved)
        self.assertEqual(record.decision, Decision.APPROVED)
        self.assertEqual(len(record.critical_flags), 0)
        self.assertEqual(len(record.critical_codes), 0)
        self.assertEqual(len(record.warnings), 0)
        # Audit-record fields are populated.
        self.assertEqual(record.vendor_name, ddq.vendor_name)
        self.assertEqual(record.dataset_name, ddq.dataset_name)
        self.assertEqual(record.rule_version, RULE_VERSION)
        self.assertIsNotNone(record.evaluated_at)
        self.assertIsNotNone(record.risk_tier)
        self.assertIsNotNone(record.next_review_date)
        self.assertIn("Tier", record.risk_tier)

    def test_mnpi_rejection(self):
        ddq = _clean_ddq(
            vendor_name="ShadyData",
            dataset_name="Board Minutes",
            is_material_non_public_information=True,
        )
        record = self.evaluator.evaluate(ddq)
        self.assertFalse(record.is_approved)
        self.assertEqual(record.decision, Decision.REJECTED)
        self.assertIn(FlagCode.MNPI, record.critical_codes)
        # next_review_date is not set for rejected vendors.
        self.assertIsNone(record.risk_tier)
        self.assertIsNone(record.next_review_date)

    def test_cfaa_scraping_rejection(self):
        ddq = _clean_ddq(
            vendor_name="Scrape Hub",
            dataset_name="Employee Headcounts",
            is_web_scraped=True,
            scrapes_behind_login=True,
            bypasses_captchas=True,
        )
        record = self.evaluator.evaluate(ddq)
        self.assertFalse(record.is_approved)
        self.assertEqual(record.decision, Decision.REJECTED)
        # has_documented_login_authorization defaults to False: an unmapped DDQ
        # must still hard-reject a behind-login scrape.
        self.assertFalse(ddq.has_documented_login_authorization)
        self.assertIn(FlagCode.CFAA_LOGIN_SCRAPE, record.critical_codes)
        self.assertIn(FlagCode.TOS_NONCOMPLIANT, record.warning_codes)
        self.assertEqual(len(record.warning_codes), 1)

    def test_pii_without_gdpr_rejection(self):
        ddq = _clean_ddq(
            vendor_name="CardLogs",
            dataset_name="Consumer Spend",
            contains_pii=True,
            is_gdpr_ccpa_compliant=False,
            has_robust_anonymization=False,
        )
        record = self.evaluator.evaluate(ddq)
        self.assertFalse(record.is_approved)
        self.assertEqual(len(record.critical_codes), 2)
        self.assertIn(FlagCode.PII_NO_GDPR, record.critical_codes)
        self.assertIn(FlagCode.PII_NO_ANONYMIZATION, record.critical_codes)

    def test_no_resell_rights_rejection(self):
        ddq = _clean_ddq(has_resell_rights=False)
        record = self.evaluator.evaluate(ddq)
        self.assertFalse(record.is_approved)
        self.assertIn(FlagCode.NO_RESELL_RIGHTS, record.critical_codes)

    def test_tos_noncompliant_rejection(self):
        ddq = _clean_ddq(is_tos_compliant=False)
        record = self.evaluator.evaluate(ddq)
        self.assertFalse(record.is_approved)
        self.assertIn(FlagCode.TOS_NONCOMPLIANT, record.critical_codes)

    def test_approved_with_warnings_captcha_bypass(self):
        # Public scraping that bypasses CAPTCHAs: a warning, not a rejection.
        ddq = _clean_ddq(
            is_web_scraped=True,
            scrapes_behind_login=False,
            bypasses_captchas=True,
        )
        record = self.evaluator.evaluate(ddq)
        self.assertTrue(record.is_approved)
        self.assertEqual(record.decision, Decision.APPROVED_WITH_WARNINGS)
        self.assertIn(FlagCode.TOS_NONCOMPLIANT, record.warning_codes)
        # Tier-1 because a warning is present.
        self.assertEqual(record.risk_tier, "Tier-1")

    def test_immutability_of_record(self):
        ddq = _clean_ddq()
        record = self.evaluator.evaluate(ddq)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            record.is_approved = True  # type: ignore[misc]
        # tuple fields cannot be mutated.
        self.assertIsInstance(record.critical_flags, tuple)
        self.assertIsInstance(record.critical_codes, tuple)
        self.assertIsInstance(record.warnings, tuple)

    def test_truthy_non_bool_input_rejected_at_construction(self):
        # A sloppily-mapped DDQ string like 'no' is truthy and must NOT silently
        # approve the vendor.
        with self.assertRaises(TypeError):
            VendorDueDiligenceQuestionnaire(
                vendor_name="BadMap",
                dataset_name="Panel Data",
                has_resell_rights="no",  # type: ignore[arg-type]
                is_web_scraped=False,
                scrapes_behind_login=False,
                bypasses_captchas=False,
                contains_pii=False,
                is_gdpr_ccpa_compliant=True,
                has_robust_anonymization=True,
                is_material_non_public_information=False,
            )

    def test_impossible_state_captcha_without_scraping_rejected(self):
        with self.assertRaises(ValueError):
            VendorDueDiligenceQuestionnaire(
                vendor_name="Inconsistent",
                dataset_name="Web Data",
                has_resell_rights=True,
                is_web_scraped=False,
                scrapes_behind_login=False,
                bypasses_captchas=True,  # impossible without scraping
                contains_pii=False,
                is_gdpr_ccpa_compliant=True,
                has_robust_anonymization=True,
                is_material_non_public_information=False,
            )

    def test_impossible_state_login_scrape_without_scraping_rejected(self):
        with self.assertRaises(ValueError):
            VendorDueDiligenceQuestionnaire(
                vendor_name="Inconsistent",
                dataset_name="Web Data",
                has_resell_rights=True,
                is_web_scraped=False,
                scrapes_behind_login=True,  # impossible without scraping
                bypasses_captchas=False,
                contains_pii=False,
                is_gdpr_ccpa_compliant=True,
                has_robust_anonymization=True,
                is_material_non_public_information=False,
            )

    def test_stale_ddq_fails_closed(self):
        ddq = _clean_ddq(
            ddq_as_of_date=_utc_today() - datetime.timedelta(days=400),
        )
        record = self.evaluator.evaluate(ddq)
        self.assertFalse(record.is_approved)
        self.assertIn(FlagCode.STALE_DDQ, record.critical_codes)

    def test_undated_ddq_fails_closed(self):
        ddq = _clean_ddq(ddq_as_of_date=None)
        record = self.evaluator.evaluate(ddq)
        self.assertFalse(record.is_approved)
        self.assertIn(FlagCode.STALE_DDQ, record.critical_codes)

    def test_custom_max_ddq_age_days(self):
        strict_evaluator = VendorDueDiligenceEvaluator(max_ddq_age_days=30)
        ddq = _clean_ddq(
            ddq_as_of_date=_utc_today() - datetime.timedelta(days=60),
        )
        record = strict_evaluator.evaluate(ddq)
        self.assertFalse(record.is_approved)
        self.assertIn(FlagCode.STALE_DDQ, record.critical_codes)

    def test_invalid_max_ddq_age_days(self):
        with self.assertRaises(ValueError):
            VendorDueDiligenceEvaluator(max_ddq_age_days=0)

    def test_next_review_date_derived_from_tier(self):
        # Tier-3 clean vendor -> triennial review (~1095 days out).
        ddq = _clean_ddq()  # no PII, no scraping, no warnings
        record = self.evaluator.evaluate(ddq)
        self.assertEqual(record.risk_tier, "Tier-3")
        as_of = datetime.date.fromisoformat(record.evaluated_at[:10])
        review = datetime.date.fromisoformat(record.next_review_date)
        self.assertEqual((review - as_of).days, 1095)

    def test_record_is_json_serializable(self):
        # SKILL.md promises an "audit-serializable" record. asdict() does not
        # convert enum members; the record survives json.dumps only because
        # Decision/FlagCode subclass str. Assert the round-trip, not the
        # trivially-true enum-equals-its-own-value comparison.
        ddq = _clean_ddq(
            is_web_scraped=True,
            scrapes_behind_login=False,
            bypasses_captchas=True,
        )
        record = self.evaluator.evaluate(ddq)
        round_tripped = json.loads(json.dumps(dataclasses.asdict(record)))
        self.assertEqual(round_tripped["decision"], "APPROVED_WITH_WARNINGS")
        self.assertEqual(round_tripped["rule_version"], RULE_VERSION)
        self.assertEqual(round_tripped["warning_codes"], ["TOS_NONCOMPLIANT"])
        self.assertEqual(round_tripped["risk_tier"], "Tier-1")

    # ------------------------------------------------------------------
    # Behind-login scraping: authorization is what the CFAA turns on.
    # ------------------------------------------------------------------

    def test_authorized_login_scrape_warns_instead_of_rejecting(self):
        ddq = _clean_ddq(
            vendor_name="Partner Portal Feed",
            dataset_name="Licensed Inventory",
            is_web_scraped=True,
            scrapes_behind_login=True,
            has_documented_login_authorization=True,
        )
        record = self.evaluator.evaluate(ddq)
        self.assertTrue(record.is_approved)
        self.assertEqual(record.decision, Decision.APPROVED_WITH_WARNINGS)
        self.assertNotIn(FlagCode.CFAA_LOGIN_SCRAPE, record.critical_codes)
        self.assertIn(FlagCode.LOGIN_SCRAPE_AUTHORIZED, record.warning_codes)
        # Never a clean approval: the warning forces the legal-review branch.
        self.assertEqual(record.risk_tier, "Tier-1")
        self.assertIn("legal review", record.audit_notes.lower())

    def test_login_authorization_requires_login_scraping(self):
        with self.assertRaises(ValueError):
            _clean_ddq(
                is_web_scraped=True,
                scrapes_behind_login=False,
                has_documented_login_authorization=True,
            )

    def test_login_authorization_must_be_strict_bool(self):
        # Assert on the message too: an unknown-kwarg TypeError would otherwise
        # let this pass without the strict-bool check existing at all.
        with self.assertRaises(TypeError) as ctx:
            _clean_ddq(
                is_web_scraped=True,
                scrapes_behind_login=True,
                has_documented_login_authorization="yes",
            )
        self.assertIn("has_documented_login_authorization", str(ctx.exception))
        self.assertIn("strict bool", str(ctx.exception))

    # ------------------------------------------------------------------
    # DDQ freshness boundaries.
    # ------------------------------------------------------------------

    def test_ddq_exactly_at_age_limit_is_accepted(self):
        ddq = _clean_ddq(ddq_as_of_date=_utc_today() - datetime.timedelta(days=365))
        record = self.evaluator.evaluate(ddq)
        self.assertTrue(record.is_approved)

    def test_ddq_one_day_past_age_limit_is_rejected(self):
        ddq = _clean_ddq(ddq_as_of_date=_utc_today() - datetime.timedelta(days=366))
        record = self.evaluator.evaluate(ddq)
        self.assertFalse(record.is_approved)
        self.assertIn(FlagCode.STALE_DDQ, record.critical_codes)

    def test_future_dated_ddq_fails_closed(self):
        # A DDQ dated well ahead of the evaluation date is a data-entry or
        # backdating error. Before the FUTURE_DATED_DDQ rule the computed age
        # was simply negative and sailed through the staleness comparison.
        ddq = _clean_ddq(ddq_as_of_date=_utc_today() + datetime.timedelta(days=45))
        record = self.evaluator.evaluate(ddq)
        self.assertFalse(record.is_approved)
        self.assertEqual(record.decision, Decision.REJECTED)
        self.assertIn(FlagCode.FUTURE_DATED_DDQ, record.critical_codes)
        self.assertNotIn(FlagCode.STALE_DDQ, record.critical_codes)

    def test_one_day_future_ddq_tolerated_as_timezone_skew(self):
        ddq = _clean_ddq(ddq_as_of_date=_utc_today() + datetime.timedelta(days=1))
        record = self.evaluator.evaluate(ddq)
        self.assertTrue(record.is_approved)
        self.assertEqual(record.decision, Decision.APPROVED)

    def test_two_day_future_ddq_rejected(self):
        ddq = _clean_ddq(ddq_as_of_date=_utc_today() + datetime.timedelta(days=2))
        record = self.evaluator.evaluate(ddq)
        self.assertFalse(record.is_approved)
        self.assertIn(FlagCode.FUTURE_DATED_DDQ, record.critical_codes)


if __name__ == "__main__":
    unittest.main()

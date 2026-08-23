import unittest

from data_localization_requirements_for_trade_records import (
    DataLocalizationComplianceEngine,
    RetentionConfiguration,
    STATUS_BLOCKED,
    STATUS_COMPLIANT,
    STATUS_MECHANISM_REQUIRED,
    STATUS_REVIEW_REQUIRED,
    TradeRecordPayload,
)


class TestDataLocalizationComplianceEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DataLocalizationComplianceEngine()

    # -- documented core behaviour ----------------------------------------

    def test_chinese_trade_record_egress_to_us_blocked(self):
        rec = TradeRecordPayload("TR_CN_001", "CN", "us-east-1", "TRADE_EXECUTION",
                                 is_primary_store=True)
        report = self.engine.audit_trade_record_localization(rec)

        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, STATUS_BLOCKED)
        self.assertGreater(len(report.remediation_instructions), 0)
        self.assertEqual(report.destination_jurisdiction, "US")

    def test_indian_payment_ledger_in_mumbai_approved(self):
        rec = TradeRecordPayload("TR_IN_001", "IN", "ap-south-1", "PAYMENT_LEDGER",
                                 is_primary_store=True)
        report = self.engine.audit_trade_record_localization(rec)

        self.assertTrue(report.is_compliant)
        self.assertEqual(report.status, STATUS_COMPLIANT)

    def test_chinese_record_in_country_compliant(self):
        rec = TradeRecordPayload("TR_CN_002", "CN", "cn-northwest-1", "CLIENT_PII",
                                 is_primary_store=True)
        self.assertEqual(
            self.engine.audit_trade_record_localization(rec).status, STATUS_COMPLIANT
        )

    # -- regression: fail-open defaults ------------------------------------

    def test_unregistered_origin_jurisdiction_does_not_fail_open(self):
        """A jurisdiction with no registered policy (e.g. RU, subject to 242-FZ)
        previously fell through to an unconditional COMPLIANT."""
        rec = TradeRecordPayload("TR_RU_001", "RU", "us-east-1", "CLIENT_PII",
                                 is_primary_store=True)
        report = self.engine.audit_trade_record_localization(rec)

        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, STATUS_REVIEW_REQUIRED)

    def test_unmapped_destination_region_does_not_fail_open(self):
        rec = TradeRecordPayload("TR_US_009", "US", "xx-nowhere-9", "TRADE_EXECUTION",
                                 is_primary_store=True)
        report = self.engine.audit_trade_record_localization(rec)

        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, STATUS_REVIEW_REQUIRED)
        self.assertEqual(report.destination_jurisdiction, "UNKNOWN")

    def test_us_record_into_china_flagged_for_retrieval_risk(self):
        """Previously any US-origin destination was approved unconditionally,
        including Chinese regions from which production to the SEC can be blocked."""
        rec = TradeRecordPayload("TR_US_010", "US", "cn-north-1", "TRADE_EXECUTION",
                                 is_primary_store=True)
        report = self.engine.audit_trade_record_localization(rec)

        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, STATUS_REVIEW_REQUIRED)
        self.assertIn("17a-4(j)", report.applied_policy)

    # -- regression: EU treated as a localization regime -------------------

    def test_eu_egress_requires_mechanism_rather_than_being_blocked(self):
        """GDPR imposes no localization mandate; a third-country transfer needs a
        Chapter V mechanism, so the engine must not report a flat violation."""
        rec = TradeRecordPayload("TR_EU_001", "EU", "us-east-1", "CLIENT_PII",
                                 is_primary_store=True)
        report = self.engine.audit_trade_record_localization(rec)

        self.assertEqual(report.status, STATUS_MECHANISM_REQUIRED)
        self.assertFalse(report.is_compliant)
        self.assertNotEqual(report.status, STATUS_BLOCKED)

    def test_eu_prefixed_region_outside_the_eea_is_not_treated_as_in_country(self):
        """eu-west-2 is London and eu-central-2 is Zurich: both are third countries."""
        for region, expected_jurisdiction in (("eu-west-2", "UK"), ("eu-central-2", "CH")):
            with self.subTest(region=region):
                rec = TradeRecordPayload(f"TR_EU_{region}", "EU", region, "CLIENT_PII",
                                         is_primary_store=True)
                report = self.engine.audit_trade_record_localization(rec)
                self.assertEqual(report.destination_jurisdiction, expected_jurisdiction)
                self.assertEqual(report.status, STATUS_MECHANISM_REQUIRED)

    def test_eu_region_added_to_allowed_list_is_compliant(self):
        rec = TradeRecordPayload("TR_EU_002", "EU", "eu-north-1", "TRADE_EXECUTION",
                                 is_primary_store=True)
        self.assertEqual(
            self.engine.audit_trade_record_localization(rec).status, STATUS_COMPLIANT
        )

    # -- record_type is actually used --------------------------------------

    def test_non_personal_market_tick_leaving_the_eu_is_out_of_gdpr_scope(self):
        rec = TradeRecordPayload("TR_EU_TICK", "EU", "us-east-1", "MARKET_TICK",
                                 is_primary_store=False)
        self.assertEqual(
            self.engine.audit_trade_record_localization(rec).status, STATUS_COMPLIANT
        )

    def test_chinese_market_tick_egress_is_review_not_blanket_block(self):
        rec = TradeRecordPayload("TR_CN_TICK", "CN", "us-east-1", "MARKET_TICK",
                                 is_primary_store=False)
        report = self.engine.audit_trade_record_localization(rec)
        self.assertEqual(report.status, STATUS_REVIEW_REQUIRED)
        self.assertFalse(report.is_compliant)

    def test_indian_trade_record_offshore_is_review_not_blocked(self):
        """The RBI mandate covers payment system data; SEBI CSCRF PR.DS.S2 is in
        abeyance, so an offshore Indian trade record is unresolved, not illegal."""
        rec = TradeRecordPayload("TR_IN_002", "IN", "us-east-1", "TRADE_EXECUTION",
                                 is_primary_store=True)
        report = self.engine.audit_trade_record_localization(rec)
        self.assertEqual(report.status, STATUS_REVIEW_REQUIRED)

    def test_indian_payment_ledger_offshore_is_blocked(self):
        rec = TradeRecordPayload("TR_IN_003", "IN", "us-east-1", "PAYMENT_LEDGER",
                                 is_primary_store=False)
        report = self.engine.audit_trade_record_localization(rec)
        self.assertEqual(report.status, STATUS_BLOCKED)

    # -- input validation ---------------------------------------------------

    def test_blank_and_wrongly_typed_fields_are_rejected(self):
        cases = [
            (TradeRecordPayload("", "CN", "us-east-1", "TRADE_EXECUTION", True), ValueError),
            (TradeRecordPayload("T1", "   ", "us-east-1", "TRADE_EXECUTION", True), ValueError),
            (TradeRecordPayload("T1", "CN", "", "TRADE_EXECUTION", True), ValueError),
            (TradeRecordPayload("T1", "CN", "us-east-1", "NOT_A_TYPE", True), ValueError),
            (TradeRecordPayload("T1", "CN", "us-east-1", "TRADE_EXECUTION", "yes"), TypeError),
            (TradeRecordPayload(None, "CN", "us-east-1", "TRADE_EXECUTION", True), TypeError),
        ]
        for rec, expected in cases:
            with self.subTest(record=rec):
                with self.assertRaises(expected):
                    self.engine.audit_trade_record_localization(rec)

    def test_non_payload_input_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.audit_trade_record_localization({"record_id": "T1"})

    def test_jurisdiction_and_region_are_case_and_whitespace_normalized(self):
        rec = TradeRecordPayload("TR_CN_003", " cn ", " CN-NORTH-1 ", "trade_execution",
                                 is_primary_store=True)
        report = self.engine.audit_trade_record_localization(rec)
        self.assertEqual(report.status, STATUS_COMPLIANT)
        self.assertEqual(report.origin_jurisdiction, "CN")
        self.assertEqual(report.destination_cloud_region, "cn-north-1")

    # -- audit trail --------------------------------------------------------

    def test_audit_trail_records_each_decision_and_is_not_mutable_by_callers(self):
        blocked = TradeRecordPayload("TR_CN_004", "CN", "us-east-1", "TRADE_EXECUTION", True)
        allowed = TradeRecordPayload("TR_IN_004", "IN", "ap-south-1", "PAYMENT_LEDGER", True)
        self.engine.audit_trade_record_localization(blocked)
        self.engine.audit_trade_record_localization(allowed)

        trail = self.engine.audit_trail
        self.assertEqual([e.record_id for e in trail], ["TR_CN_004", "TR_IN_004"])

        trail[0].status = STATUS_COMPLIANT
        trail[0].is_compliant = True
        trail[0].remediation_instructions.clear()

        refetched = self.engine.audit_trail[0]
        self.assertEqual(refetched.status, STATUS_BLOCKED)
        self.assertFalse(refetched.is_compliant)
        self.assertGreater(len(refetched.remediation_instructions), 0)

    def test_rejected_input_leaves_no_audit_entry(self):
        with self.assertRaises(ValueError):
            self.engine.audit_trade_record_localization(
                TradeRecordPayload("", "CN", "us-east-1", "TRADE_EXECUTION", True)
            )
        self.assertEqual(self.engine.audit_trail, [])


class TestSec17a4RetentionVerification(unittest.TestCase):

    def _verify(self, **kwargs):
        defaults = dict(
            record_id="R1",
            sec_record_class="17a-4(a)",
            retention_years=6,
            storage_mode="WORM",
            first_two_years_readily_accessible=True,
        )
        defaults.update(kwargs)
        return DataLocalizationComplianceEngine.verify_sec_17a4_retention(
            RetentionConfiguration(**defaults)
        )

    def test_worm_six_year_configuration_passes(self):
        report = self._verify()
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.required_retention_years, 6)
        self.assertEqual(report.findings, [])

    def test_audit_trail_alternative_is_permitted_not_only_worm(self):
        """The 2022 amendments (effective 3 Jan 2023) added the audit-trail
        alternative alongside WORM."""
        report = self._verify(storage_mode="AUDIT_TRAIL")
        self.assertTrue(report.is_compliant)

    def test_mutable_storage_fails(self):
        report = self._verify(storage_mode="MUTABLE")
        self.assertFalse(report.is_compliant)
        self.assertTrue(any("17a-4(f)" in f for f in report.findings))

    def test_three_year_class_requires_only_three_years(self):
        report = self._verify(sec_record_class="17a-4(b)", retention_years=3)
        self.assertTrue(report.is_compliant)
        self.assertEqual(report.required_retention_years, 3)

    def test_six_year_class_is_not_satisfied_by_three_years(self):
        report = self._verify(retention_years=3)
        self.assertFalse(report.is_compliant)
        self.assertTrue(any("6 years" in f for f in report.findings))

    def test_boundary_just_below_required_period_fails(self):
        self.assertFalse(self._verify(retention_years=5.99).is_compliant)
        self.assertTrue(self._verify(retention_years=6.0).is_compliant)

    def test_accessibility_qualifier_applies_only_to_paragraph_a(self):
        self.assertFalse(
            self._verify(first_two_years_readily_accessible=False).is_compliant
        )
        self.assertTrue(
            self._verify(sec_record_class="17a-4(b)", retention_years=3,
                         first_two_years_readily_accessible=False).is_compliant
        )

    def test_invalid_configuration_rejected(self):
        with self.assertRaises(ValueError):
            self._verify(sec_record_class="17a-4(z)")
        with self.assertRaises(TypeError):
            self._verify(retention_years="six")
        with self.assertRaises(TypeError):
            DataLocalizationComplianceEngine.verify_sec_17a4_retention({"record_id": "R1"})


if __name__ == '__main__':
    unittest.main()

import unittest

from insider_trading_controls_for_alternative_data_usage import (
    CONTROL_EARNINGS_BLACKOUT,
    CONTROL_MNPI_PROVENANCE,
    CONTROL_PANEL_AGGREGATION,
    CONTROL_PII_SCRUBBING,
    CONTROL_TERMS_OF_SERVICE,
    CONTROL_VENDOR_DILIGENCE_SIGNOFF,
    AltDataComplianceError,
    AltDataDatasetSpec,
    AltDataInsiderTradingComplianceEngine,
)


def make_spec(**overrides) -> AltDataDatasetSpec:
    """A fully compliant satellite dataset, overridable field by field."""
    base = dict(
        dataset_name="Orbital_Satellite_Parking_Lot_V2",
        data_source_type="SATELLITE_IMAGERY",
        has_mnpi_risk=False,
        has_vendor_diligence_signoff=True,
        is_tos_compliant=True,
        is_pii_scrubbed=True,
        panel_aggregation_count=250,
        hours_to_earnings_release=72.0,
    )
    base.update(overrides)
    return AltDataDatasetSpec(**base)


class TestCompliantPath(unittest.TestCase):

    def setUp(self):
        self.engine = AltDataInsiderTradingComplianceEngine(
            min_panel_aggregation_count=50, earnings_blackout_window_hours=48.0
        )

    def test_low_risk_alt_data_approval(self):
        report = self.engine.audit_alt_data_dataset(make_spec())

        self.assertEqual(report.risk_classification, "LOW_RISK_APPROVED")
        self.assertTrue(report.is_mnpi_cleared)
        self.assertTrue(report.is_vendor_diligence_cleared)
        self.assertTrue(report.is_pii_anonymization_cleared)
        self.assertTrue(report.is_blackout_window_cleared)
        self.assertEqual(report.failed_controls, ())

    def test_no_scheduled_earnings_clears_blackout_gate(self):
        report = self.engine.audit_alt_data_dataset(
            make_spec(hours_to_earnings_release=None)
        )

        self.assertEqual(report.risk_classification, "LOW_RISK_APPROVED")
        self.assertTrue(report.is_blackout_window_cleared)


class TestControlFailures(unittest.TestCase):

    def setUp(self):
        self.engine = AltDataInsiderTradingComplianceEngine(
            min_panel_aggregation_count=50, earnings_blackout_window_hours=48.0
        )

    def test_mnpi_risk_rejection(self):
        report = self.engine.audit_alt_data_dataset(
            make_spec(
                dataset_name="Leaked_Executive_Emails",
                data_source_type="WEB_SCRAPED",
                has_mnpi_risk=True,
            )
        )

        self.assertEqual(report.risk_classification, "REJECTED_MNPI_RISK")
        self.assertFalse(report.is_mnpi_cleared)
        self.assertIn(CONTROL_MNPI_PROVENANCE, report.failed_controls)

    def test_unaggregated_pii_rejection(self):
        report = self.engine.audit_alt_data_dataset(
            make_spec(
                dataset_name="Raw_Consumer_Geolocation",
                data_source_type="GEOLOCATION",
                panel_aggregation_count=5,
                hours_to_earnings_release=96.0,
            )
        )

        self.assertEqual(report.risk_classification, "REJECTED_UNAGGREGATED_PII")
        self.assertFalse(report.is_pii_anonymization_cleared)
        self.assertIn(CONTROL_PANEL_AGGREGATION, report.failed_controls)

    def test_blackout_window_restriction_is_not_a_rejection(self):
        report = self.engine.audit_alt_data_dataset(
            make_spec(hours_to_earnings_release=6.0)
        )

        self.assertEqual(report.risk_classification, "BLACKOUT_WINDOW_RESTRICTED")
        self.assertFalse(report.is_blackout_window_cleared)
        # Everything else genuinely passed, so it must still read as cleared.
        self.assertTrue(report.is_mnpi_cleared)
        self.assertTrue(report.is_vendor_diligence_cleared)
        self.assertTrue(report.is_pii_anonymization_cleared)
        self.assertEqual(report.failed_controls, (CONTROL_EARNINGS_BLACKOUT,))

    def test_blackout_window_is_two_sided_around_the_release(self):
        # 6 hours *after* the release is as restricted as 6 hours before it.
        report = self.engine.audit_alt_data_dataset(
            make_spec(hours_to_earnings_release=-6.0)
        )

        self.assertEqual(report.risk_classification, "BLACKOUT_WINDOW_RESTRICTED")
        self.assertIn("6.0h from release", report.audit_notes)


class TestAuditRecordTruthfulness(unittest.TestCase):
    """Regression tests for an audit trail that misreported its own findings."""

    def setUp(self):
        self.engine = AltDataInsiderTradingComplianceEngine()

    def test_unscrubbed_pii_is_not_reported_as_a_panel_size_failure(self):
        # Regression: a 500,000-contributor panel that was never scrubbed used to
        # produce "panel aggregation count (500000) is below minimum
        # requirement (50)" -- a false statement in a compliance record.
        report = self.engine.audit_alt_data_dataset(
            make_spec(
                dataset_name="Raw_Card_Feed",
                data_source_type="CREDIT_CARD_TRANSACTIONS",
                is_pii_scrubbed=False,
                panel_aggregation_count=500_000,
            )
        )

        self.assertEqual(report.risk_classification, "REJECTED_UNAGGREGATED_PII")
        self.assertEqual(report.failed_controls, (CONTROL_PII_SCRUBBING,))
        self.assertIn("PII scrubbing not verified", report.audit_notes)
        self.assertNotIn("below the firm-policy minimum", report.audit_notes)

    def test_rejection_on_one_gate_never_asserts_a_pass_on_another(self):
        # Regression: an MNPI rejection used to hard-code
        # is_blackout_window_cleared=True even one hour from a release, and to
        # echo is_pii_scrubbed without ever applying the panel-size test.
        report = self.engine.audit_alt_data_dataset(
            make_spec(
                dataset_name="Leak",
                data_source_type="WEB_SCRAPED",
                has_mnpi_risk=True,
                has_vendor_diligence_signoff=False,
                is_tos_compliant=False,
                is_pii_scrubbed=True,
                panel_aggregation_count=1,
                hours_to_earnings_release=1.0,
            )
        )

        self.assertEqual(report.risk_classification, "REJECTED_MNPI_RISK")
        self.assertFalse(report.is_mnpi_cleared)
        self.assertFalse(report.is_vendor_diligence_cleared)
        self.assertFalse(report.is_pii_anonymization_cleared)
        self.assertFalse(report.is_blackout_window_cleared)
        self.assertEqual(
            report.failed_controls,
            (
                CONTROL_MNPI_PROVENANCE,
                CONTROL_VENDOR_DILIGENCE_SIGNOFF,
                CONTROL_TERMS_OF_SERVICE,
                CONTROL_PANEL_AGGREGATION,
                CONTROL_EARNINGS_BLACKOUT,
            ),
        )

    def test_tos_breach_is_distinguishable_from_a_missing_signoff(self):
        # Regression: both used to collapse into one "missing sign-off OR ToS"
        # note, so an auditor could not tell which control had failed.
        tos_only = self.engine.audit_alt_data_dataset(
            make_spec(data_source_type="WEB_SCRAPED", is_tos_compliant=False)
        )
        signoff_only = self.engine.audit_alt_data_dataset(
            make_spec(has_vendor_diligence_signoff=False)
        )

        self.assertEqual(tos_only.risk_classification, "REJECTED_MISSING_DILIGENCE")
        self.assertEqual(tos_only.failed_controls, (CONTROL_TERMS_OF_SERVICE,))
        self.assertEqual(
            signoff_only.failed_controls, (CONTROL_VENDOR_DILIGENCE_SIGNOFF,)
        )


class TestBoundaries(unittest.TestCase):

    def setUp(self):
        self.engine = AltDataInsiderTradingComplianceEngine(
            min_panel_aggregation_count=50, earnings_blackout_window_hours=48.0
        )

    def test_panel_count_exactly_at_threshold_clears(self):
        report = self.engine.audit_alt_data_dataset(
            make_spec(panel_aggregation_count=50)
        )
        self.assertEqual(report.risk_classification, "LOW_RISK_APPROVED")

    def test_panel_count_one_below_threshold_fails(self):
        report = self.engine.audit_alt_data_dataset(
            make_spec(panel_aggregation_count=49)
        )
        self.assertEqual(report.risk_classification, "REJECTED_UNAGGREGATED_PII")

    def test_hours_exactly_at_window_edge_clears(self):
        report = self.engine.audit_alt_data_dataset(
            make_spec(hours_to_earnings_release=48.0)
        )
        self.assertTrue(report.is_blackout_window_cleared)

    def test_hours_just_inside_window_is_restricted(self):
        report = self.engine.audit_alt_data_dataset(
            make_spec(hours_to_earnings_release=47.9)
        )
        self.assertFalse(report.is_blackout_window_cleared)

    def test_zero_hour_window_disables_the_blackout_gate(self):
        engine = AltDataInsiderTradingComplianceEngine(
            earnings_blackout_window_hours=0.0
        )
        report = engine.audit_alt_data_dataset(
            make_spec(hours_to_earnings_release=0.0)
        )
        self.assertEqual(report.risk_classification, "LOW_RISK_APPROVED")


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = AltDataInsiderTradingComplianceEngine()

    def test_truthy_string_answers_are_rejected_not_coerced(self):
        # Regression: 'no' is truthy, so a CSV/LLM-mapped spec whose vendor
        # sign-off, ToS and PII answers were all the string 'no' used to be
        # classified LOW_RISK_APPROVED.
        with self.assertRaises(AltDataComplianceError) as ctx:
            make_spec(
                has_vendor_diligence_signoff="no",
                is_tos_compliant="no",
                is_pii_scrubbed="no",
            )
        self.assertIn("has_vendor_diligence_signoff", str(ctx.exception))

    def test_post_construction_mutation_is_revalidated_at_audit_time(self):
        # Dataclass fields are mutable, so validating only at construction would
        # leave the truthy-string fail-open reachable one assignment later.
        spec = make_spec()
        spec.is_pii_scrubbed = "no"
        with self.assertRaises(AltDataComplianceError):
            self.engine.audit_alt_data_dataset(spec)

    def test_nan_hours_to_earnings_is_rejected(self):
        with self.assertRaises(AltDataComplianceError):
            make_spec(hours_to_earnings_release=float("nan"))

    def test_infinite_hours_to_earnings_is_rejected(self):
        with self.assertRaises(AltDataComplianceError):
            make_spec(hours_to_earnings_release=float("inf"))

    def test_negative_panel_count_is_rejected(self):
        with self.assertRaises(AltDataComplianceError):
            make_spec(panel_aggregation_count=-5)

    def test_non_integer_panel_count_is_rejected(self):
        with self.assertRaises(AltDataComplianceError):
            make_spec(panel_aggregation_count=250.0)

    def test_blank_dataset_name_is_rejected(self):
        with self.assertRaises(AltDataComplianceError):
            make_spec(dataset_name="   ")

    def test_engine_rejects_a_panel_threshold_below_one(self):
        with self.assertRaises(AltDataComplianceError):
            AltDataInsiderTradingComplianceEngine(min_panel_aggregation_count=0)

    def test_engine_rejects_a_negative_blackout_window(self):
        with self.assertRaises(AltDataComplianceError):
            AltDataInsiderTradingComplianceEngine(earnings_blackout_window_hours=-1.0)

    def test_audit_rejects_a_non_spec_argument(self):
        with self.assertRaises(AltDataComplianceError):
            self.engine.audit_alt_data_dataset({"dataset_name": "dict_not_spec"})


if __name__ == "__main__":
    unittest.main()

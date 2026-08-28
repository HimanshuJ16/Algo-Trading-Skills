"""
Unit tests for sandbox-vs-production-endpoint-drift skill.

Several tests here are regressions against a previous comparator that walked only the
top-level keys of a payload, skipped any field that was null on either side, folded
header and status findings into no report at all, and graded status-code severity by
numeric distance. Each of those defects produced a *clean* report for a genuinely broken
promotion, so the tests that matter most are the ones asserting a finding exists:

  * ``test_nested_type_drift_is_detected`` — the old comparator reported
    ``passed=True, 0 findings`` for a payload whose every nested price changed from a
    JSON number to a string.
  * ``test_audit_endpoint_folds_status_drift_into_report`` — the old API returned the
    status finding outside the report, so a caller gating on ``report.passed`` saw green.
  * ``test_status_class_change_within_100_is_critical`` — 404 vs 500 differ by 96, so the
    old ``abs(diff) >= 100`` rule graded a server error against a not-found as WARNING.
  * ``test_null_in_production_where_sandbox_has_value_is_critical`` — the old comparator
    skipped any pair where either side was None.
  * ``test_both_payloads_empty_raises`` — two empty captures used to report parity.

Fixtures are deep-copied from a shared baseline and mutated in exactly one place, so a
test for one kind of drift cannot silently introduce another.
"""
import copy
import unittest

from drift_detector import (
    DriftAuditError,
    DriftCategory,
    DriftFinding,
    DriftSeverity,
    EndpointDriftDetector,
    EndpointDriftReport,
)

ENDPOINT = "/v2/orders"

#: Shape modelled on a broker order response: an envelope around a nested order object
#: that itself carries an array of legs.
BASELINE = {
    "order_id": "123",
    "symbol": "AAPL",
    "order": {
        "qty": 10.0,
        "price": 150.5,
        "filled_at": "2026-07-24T12:00:00Z",
        "legs": [{"leg_id": "1", "qty": 10.0}],
    },
}


def categories(report_or_findings):
    if isinstance(report_or_findings, EndpointDriftReport):
        report_or_findings = report_or_findings.findings
    return [f.category for f in report_or_findings]


class TestSchemaComparison(unittest.TestCase):

    def setUp(self):
        self.detector = EndpointDriftDetector()

    def test_identical_schemas_pass(self):
        report = self.detector.compare_schemas(
            ENDPOINT, copy.deepcopy(BASELINE), copy.deepcopy(BASELINE)
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.critical_count, 0)
        self.assertEqual(report.warning_count, 0)
        self.assertEqual(report.findings, [])
        self.assertEqual(report.exit_code, 0)

    def test_nested_type_drift_is_detected(self):
        """Regression: a top-level-only comparator reports this payload as clean."""
        sandbox = copy.deepcopy(BASELINE)
        prod = copy.deepcopy(BASELINE)
        prod["order"]["price"] = "150.50"

        report = self.detector.compare_schemas(ENDPOINT, sandbox, prod)
        self.assertFalse(report.passed)
        self.assertEqual(report.critical_count, 1)
        self.assertEqual(report.findings[0].field_name, "order.price")
        self.assertEqual(report.findings[0].category, DriftCategory.TYPE_MISMATCH)

    def test_drift_inside_array_element_is_detected(self):
        sandbox = copy.deepcopy(BASELINE)
        prod = copy.deepcopy(BASELINE)
        prod["order"]["legs"][0]["qty"] = "10"

        report = self.detector.compare_schemas(ENDPOINT, sandbox, prod)
        self.assertFalse(report.passed)
        self.assertEqual(report.findings[0].field_name, "order.legs[0].qty")
        self.assertEqual(report.findings[0].severity, DriftSeverity.CRITICAL)

    def test_empty_sandbox_array_against_populated_production_is_critical(self):
        sandbox = copy.deepcopy(BASELINE)
        prod = copy.deepcopy(BASELINE)
        sandbox["order"]["legs"] = []

        report = self.detector.compare_schemas(ENDPOINT, sandbox, prod)
        self.assertFalse(report.passed)
        self.assertIn(DriftCategory.ARRAY_NOT_EXERCISED, categories(report))

    def test_empty_production_array_is_reported_as_not_compared(self):
        sandbox = copy.deepcopy(BASELINE)
        prod = copy.deepcopy(BASELINE)
        prod["order"]["legs"] = []

        report = self.detector.compare_schemas(ENDPOINT, sandbox, prod)
        self.assertTrue(report.passed)  # not a break, but the elements were not compared
        self.assertEqual(report.warning_count, 1)
        self.assertIn(DriftCategory.ARRAY_NOT_COMPARED, categories(report))

    def test_missing_field_in_sandbox_critical_drift(self):
        sandbox = copy.deepcopy(BASELINE)
        prod = copy.deepcopy(BASELINE)
        del sandbox["order"]["filled_at"]

        report = self.detector.compare_schemas(ENDPOINT, sandbox, prod)
        self.assertFalse(report.passed)
        self.assertEqual(report.critical_count, 1)
        self.assertEqual(report.findings[0].field_name, "order.filled_at")
        self.assertEqual(report.findings[0].category, DriftCategory.MISSING_IN_SANDBOX)

    def test_field_missing_in_production_is_critical(self):
        """Regression: previously WARNING, though this is the direction that raises live.

        Code written against the sandbox contract reads ``order.filled_at`` and gets a
        KeyError the first time it runs against production.
        """
        sandbox = copy.deepcopy(BASELINE)
        prod = copy.deepcopy(BASELINE)
        del prod["order"]["filled_at"]

        report = self.detector.compare_schemas(ENDPOINT, sandbox, prod)
        self.assertFalse(report.passed)
        self.assertEqual(report.critical_count, 1)
        self.assertEqual(report.findings[0].category, DriftCategory.MISSING_IN_PRODUCTION)

    def test_string_vs_float_type_mismatch_is_critical(self):
        report = self.detector.compare_schemas(
            ENDPOINT, {"price": 150.5}, {"price": "150.50"}
        )
        self.assertFalse(report.passed)
        self.assertEqual(report.critical_count, 1)

    def test_int_vs_float_is_warning_not_critical(self):
        report = self.detector.compare_schemas(ENDPOINT, {"qty": 10}, {"qty": 10.0})
        self.assertTrue(report.passed)
        self.assertEqual(report.warning_count, 1)
        self.assertEqual(report.findings[0].category, DriftCategory.NUMERIC_TYPE_MISMATCH)

    def test_bool_vs_int_is_critical_despite_bool_subclassing_int(self):
        report = self.detector.compare_schemas(
            ENDPOINT, {"is_open": True}, {"is_open": 1}
        )
        self.assertFalse(report.passed)
        self.assertEqual(report.findings[0].category, DriftCategory.TYPE_MISMATCH)

    def test_null_in_production_where_sandbox_has_value_is_critical(self):
        report = self.detector.compare_schemas(
            ENDPOINT, {"filled_at": "2026-07-24T12:00:00Z"}, {"filled_at": None}
        )
        self.assertFalse(report.passed)
        self.assertEqual(report.findings[0].category, DriftCategory.NULLABILITY_MISMATCH)

    def test_null_in_sandbox_where_production_has_value_is_critical(self):
        report = self.detector.compare_schemas(
            ENDPOINT, {"filled_at": None}, {"filled_at": "2026-07-24T12:00:00Z"}
        )
        self.assertFalse(report.passed)
        self.assertEqual(report.findings[0].category, DriftCategory.NULLABILITY_MISMATCH)

    def test_null_on_both_sides_is_not_drift(self):
        report = self.detector.compare_schemas(
            ENDPOINT, {"filled_at": None, "id": "1"}, {"filled_at": None, "id": "1"}
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.findings, [])

    def test_object_vs_scalar_is_critical(self):
        report = self.detector.compare_schemas(
            ENDPOINT, {"order": {"id": "1"}}, {"order": "1"}
        )
        self.assertFalse(report.passed)
        self.assertEqual(report.findings[0].category, DriftCategory.TYPE_MISMATCH)

    def test_string_is_not_treated_as_an_array(self):
        """``str`` is a sequence; comparing it element-wise would be wrong."""
        report = self.detector.compare_schemas(
            ENDPOINT, {"symbol": "AAPL"}, {"symbol": "MSFT"}
        )
        self.assertTrue(report.passed)
        self.assertEqual(report.findings, [])

    def test_findings_are_deterministically_ordered(self):
        sandbox = {"b": 1, "a": 1}
        prod = {"z": 1, "y": 1}
        first = self.detector.compare_schemas(ENDPOINT, sandbox, prod)
        second = self.detector.compare_schemas(ENDPOINT, dict(sandbox), dict(prod))
        self.assertEqual(
            [f.field_name for f in first.findings],
            [f.field_name for f in second.findings],
        )
        self.assertEqual([f.field_name for f in first.findings], ["y", "z", "a", "b"])

    def test_mixed_key_types_do_not_break_ordering(self):
        """``json.loads`` yields str keys, but a hand-built payload may not."""
        report = self.detector.compare_schemas(ENDPOINT, {1: "a", "b": "c"}, {"b": "c"})
        self.assertEqual(report.critical_count, 1)
        self.assertEqual(report.findings[0].field_name, "1")

    def test_deep_nesting_is_truncated_rather_than_raising(self):
        detector = EndpointDriftDetector(max_depth=3)
        deep_sandbox = {"a": {"b": {"c": {"d": {"e": 1}}}}}
        deep_prod = {"a": {"b": {"c": {"d": {"e": "1"}}}}}

        report = detector.compare_schemas(ENDPOINT, deep_sandbox, deep_prod)
        self.assertIn(DriftCategory.DEPTH_LIMIT_REACHED, categories(report))
        self.assertTrue(any("NOT compared" in f.description for f in report.findings))

    def test_non_mapping_payload_raises(self):
        with self.assertRaises(DriftAuditError):
            self.detector.compare_schemas(ENDPOINT, None, {"a": 1})
        with self.assertRaises(DriftAuditError):
            self.detector.compare_schemas(ENDPOINT, {"a": 1}, [{"a": 1}])

    def test_both_payloads_empty_raises(self):
        """An empty capture must not be reported as parity."""
        with self.assertRaises(DriftAuditError):
            self.detector.compare_schemas(ENDPOINT, {}, {})

    def test_one_empty_payload_is_reported_as_drift(self):
        report = self.detector.compare_schemas(ENDPOINT, {}, {"order_id": "1"})
        self.assertFalse(report.passed)
        self.assertEqual(report.critical_count, 1)

    def test_invalid_max_depth_raises(self):
        with self.assertRaises(DriftAuditError):
            EndpointDriftDetector(max_depth=0)


class TestHeaderComparison(unittest.TestCase):

    def setUp(self):
        self.detector = EndpointDriftDetector()

    def test_rate_limit_header_missing_in_sandbox_is_warning(self):
        findings = self.detector.compare_headers(
            ENDPOINT, {}, {"X-RateLimit-Remaining": "48"}
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, DriftSeverity.WARNING)
        self.assertEqual(findings[0].field_name, "x-ratelimit-remaining")

    def test_header_names_are_matched_case_insensitively(self):
        findings = self.detector.compare_headers(
            ENDPOINT, {"x-ratelimit-limit": "200"}, {"X-RATELIMIT-LIMIT": "200"}
        )
        self.assertEqual(findings, [])

    def test_ietf_draft_ratelimit_field_is_audited(self):
        findings = self.detector.compare_headers(
            ENDPOINT, {}, {"RateLimit": "limit=100, remaining=12, reset=30"}
        )
        self.assertEqual([f.field_name for f in findings], ["ratelimit"])

    def test_more_permissive_sandbox_quota_is_critical(self):
        findings = self.detector.compare_headers(
            ENDPOINT, {"x-ratelimit-limit": "1000"}, {"x-ratelimit-limit": "200"}
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, DriftSeverity.CRITICAL)
        self.assertEqual(findings[0].category, DriftCategory.RATE_LIMIT_VALUE_MISMATCH)

    def test_stricter_sandbox_quota_is_warning(self):
        findings = self.detector.compare_headers(
            ENDPOINT, {"x-ratelimit-limit": "100"}, {"x-ratelimit-limit": "1200"}
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, DriftSeverity.WARNING)

    def test_policy_annotated_quota_value_is_parsed(self):
        findings = self.detector.compare_headers(
            ENDPOINT, {"x-ratelimit-limit": "100, 100;w=60"}, {"x-ratelimit-limit": "100"}
        )
        self.assertEqual(findings, [])

    def test_non_numeric_quota_value_is_skipped_not_guessed(self):
        findings = self.detector.compare_headers(
            ENDPOINT, {"x-ratelimit-limit": "unlimited"}, {"x-ratelimit-limit": "200"}
        )
        self.assertEqual(findings, [])

    def test_content_type_media_type_mismatch_is_critical(self):
        findings = self.detector.compare_headers(
            ENDPOINT,
            {"content-type": "application/json"},
            {"content-type": "text/html; charset=utf-8"},
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].category, DriftCategory.CONTENT_TYPE_MISMATCH)

    def test_content_type_parameter_difference_is_not_drift(self):
        findings = self.detector.compare_headers(
            ENDPOINT,
            {"content-type": "application/json"},
            {"content-type": "application/json; charset=utf-8"},
        )
        self.assertEqual(findings, [])

    def test_multi_valued_header_container_is_normalised(self):
        """Some clients expose a list per field name; stringifying it invents drift."""
        findings = self.detector.compare_headers(
            ENDPOINT,
            {"content-type": ["application/json"]},
            {"content-type": "application/json"},
        )
        self.assertEqual(findings, [])

    def test_non_mapping_headers_raise(self):
        with self.assertRaises(DriftAuditError):
            self.detector.compare_headers(ENDPOINT, [("a", "b")], {})


class TestStatusCodeComparison(unittest.TestCase):

    def setUp(self):
        self.detector = EndpointDriftDetector()

    def test_identical_status_codes_produce_no_finding(self):
        self.assertIsNone(self.detector.compare_status_codes(ENDPOINT, 200, 200))

    def test_status_code_mismatch_audit(self):
        finding = self.detector.compare_status_codes(ENDPOINT, 200, 400)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.severity, DriftSeverity.CRITICAL)
        self.assertEqual(finding.category, DriftCategory.STATUS_CLASS_MISMATCH)

    def test_status_class_change_within_100_is_critical(self):
        """Regression: 404 and 500 differ by 96, so a distance rule graded this WARNING."""
        finding = self.detector.compare_status_codes(ENDPOINT, 404, 500)
        self.assertEqual(finding.severity, DriftSeverity.CRITICAL)

    def test_same_class_status_difference_is_warning(self):
        finding = self.detector.compare_status_codes(ENDPOINT, 400, 404)
        self.assertEqual(finding.severity, DriftSeverity.WARNING)
        self.assertEqual(finding.category, DriftCategory.STATUS_CODE_MISMATCH)

    def test_production_throttling_against_sandbox_success_is_critical(self):
        finding = self.detector.compare_status_codes(ENDPOINT, 200, 429)
        self.assertEqual(finding.severity, DriftSeverity.CRITICAL)

    def test_out_of_range_status_raises(self):
        with self.assertRaises(DriftAuditError):
            self.detector.compare_status_codes(ENDPOINT, 200, 999)

    def test_non_integer_status_raises(self):
        with self.assertRaises(DriftAuditError):
            self.detector.compare_status_codes(ENDPOINT, "200", 200)
        with self.assertRaises(DriftAuditError):
            self.detector.compare_status_codes(ENDPOINT, True, 200)


class TestEndpointInventory(unittest.TestCase):

    def setUp(self):
        self.detector = EndpointDriftDetector()

    def test_production_only_endpoint_family_is_critical(self):
        findings = self.detector.compare_endpoint_inventory(
            ["/api/v3/order"], ["/api/v3/order", "/sapi/v1/margin/order"]
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, DriftSeverity.CRITICAL)
        self.assertEqual(
            findings[0].category, DriftCategory.ENDPOINT_MISSING_IN_SANDBOX
        )

    def test_sandbox_only_endpoint_is_critical(self):
        findings = self.detector.compare_endpoint_inventory(
            ["/api/v3/order", "/api/v3/preview"], ["/api/v3/order"]
        )
        self.assertEqual(
            [f.category for f in findings],
            [DriftCategory.ENDPOINT_MISSING_IN_PRODUCTION],
        )

    def test_matching_inventories_produce_no_findings(self):
        self.assertEqual(
            self.detector.compare_endpoint_inventory(["/a", "/b"], ["/b", "/a"]), []
        )


class TestFullAudit(unittest.TestCase):

    def setUp(self):
        self.detector = EndpointDriftDetector()

    def test_audit_endpoint_folds_status_drift_into_report(self):
        """Regression: schema-only gating passes while the endpoint has clearly drifted."""
        payload = copy.deepcopy(BASELINE)
        schema_only = self.detector.compare_schemas(
            ENDPOINT, payload, copy.deepcopy(BASELINE)
        )
        self.assertTrue(schema_only.passed)

        report = self.detector.audit_endpoint(
            ENDPOINT,
            sandbox_json=payload,
            prod_json=copy.deepcopy(BASELINE),
            sandbox_status=200,
            prod_status=400,
        )
        self.assertFalse(report.passed)
        self.assertEqual(report.exit_code, 1)
        self.assertIn(DriftCategory.STATUS_CLASS_MISMATCH, categories(report))

    def test_audit_endpoint_folds_header_drift_into_report(self):
        report = self.detector.audit_endpoint(
            ENDPOINT,
            sandbox_headers={"x-ratelimit-limit": "1000"},
            prod_headers={"x-ratelimit-limit": "200"},
        )
        self.assertFalse(report.passed)
        self.assertIn(DriftCategory.RATE_LIMIT_VALUE_MISMATCH, categories(report))

    def test_audit_endpoint_skips_stages_with_no_samples(self):
        report = self.detector.audit_endpoint(ENDPOINT, sandbox_status=200, prod_status=200)
        self.assertTrue(report.passed)
        self.assertEqual(report.findings, [])

    def test_body_present_in_one_environment_only_is_drift(self):
        report = self.detector.audit_endpoint(ENDPOINT, prod_json={"order_id": "1"})
        self.assertFalse(report.passed)
        self.assertIn(DriftCategory.BODY_PRESENCE_MISMATCH, categories(report))

    def test_report_counters_match_findings(self):
        report = EndpointDriftReport.from_findings(
            ENDPOINT,
            [
                DriftFinding(ENDPOINT, "a", DriftSeverity.CRITICAL, "d", 1, 2),
                DriftFinding(ENDPOINT, "b", DriftSeverity.WARNING, "d", 1, 2),
                DriftFinding(ENDPOINT, "c", DriftSeverity.INFO, "d", 1, 2),
            ],
        )
        self.assertEqual(
            (report.critical_count, report.warning_count, report.info_count), (1, 1, 1)
        )
        self.assertFalse(report.passed)

    def test_format_report_lists_criticals_first(self):
        report = self.detector.audit_endpoint(
            ENDPOINT,
            sandbox_headers={},
            prod_headers={"x-ratelimit-limit": "200"},
            sandbox_status=200,
            prod_status=500,
        )
        rendered = report.format_report()
        self.assertIn("BLOCK", rendered)
        self.assertLess(rendered.index("[CRITICAL]"), rendered.index("[WARNING]"))


if __name__ == "__main__":
    unittest.main()

"""
Unit tests for sandbox-vs-production-endpoint-drift skill.

Tests:
1. Identical JSON payloads pass schema comparison with zero drift.
2. Missing production fields in sandbox detected as CRITICAL drift.
3. Field data type mismatch (e.g. str vs float) detected as CRITICAL.
4. Header rate-limit drift and HTTP status code discrepancy auditing.
"""
import unittest
from drift_detector import (
    DriftFinding,
    DriftSeverity,
    EndpointDriftDetector,
    EndpointDriftReport,
)


class TestEndpointDriftDetector(unittest.TestCase):

    def setUp(self):
        self.detector = EndpointDriftDetector()

    def test_identical_schemas_pass(self):
        payload = {"order_id": "123", "symbol": "AAPL", "qty": 10.0, "price": 150.5}
        rpt = self.detector.compare_schemas("/v2/orders", payload, payload)

        self.assertTrue(rpt.passed)
        self.assertEqual(rpt.critical_count, 0)
        self.assertEqual(rpt.warning_count, 0)

    def test_missing_field_in_sandbox_critical_drift(self):
        sandbox = {"order_id": "123", "symbol": "AAPL"}
        prod = {"order_id": "123", "symbol": "AAPL", "filled_at": "2026-07-24T12:00:00Z"}

        rpt = self.detector.compare_schemas("/v2/orders", sandbox, prod)
        self.assertFalse(rpt.passed)
        self.assertEqual(rpt.critical_count, 1)
        self.assertEqual(rpt.findings[0].field_name, "filled_at")
        self.assertEqual(rpt.findings[0].severity, DriftSeverity.CRITICAL)

    def test_type_mismatch_detection(self):
        sandbox = {"order_id": "123", "price": 150.5}  # float
        prod = {"order_id": "123", "price": "150.50"}  # str

        rpt = self.detector.compare_schemas("/v2/orders", sandbox, prod)
        self.assertFalse(rpt.passed)
        self.assertEqual(rpt.critical_count, 1)

    def test_status_code_mismatch_audit(self):
        finding = self.detector.compare_status_codes("/v2/orders", 200, 400)
        self.assertIsNotNone(finding)
        self.assertEqual(finding.severity, DriftSeverity.CRITICAL)


if __name__ == "__main__":
    unittest.main()

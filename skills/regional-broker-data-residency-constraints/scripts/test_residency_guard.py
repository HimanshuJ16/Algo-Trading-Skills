"""
Unit tests for regional-broker-data-residency-constraints skill.
"""
import unittest
from residency_guard import DataResidencyComplianceGuard, DataResidencyViolationError


class TestDataResidencyComplianceGuard(unittest.TestCase):

    def setUp(self):
        self.guard = DataResidencyComplianceGuard()

    def test_compliant_region_validation(self):
        # Zerodha (India) hosted in ap-south-1 (Mumbai) -> Valid
        self.assertTrue(self.guard.validate_residency("zerodha", current_region="ap-south-1"))

        # DEGIRO (EU) hosted in eu-central-1 (Frankfurt) -> Valid
        self.assertTrue(self.guard.validate_residency("degiro", current_region="eu-central-1"))

    def test_non_compliant_region_violation(self):
        # Zerodha (India) hosted in us-east-1 (N. Virginia) -> DataResidencyViolationError
        with self.assertRaises(DataResidencyViolationError) as ctx:
            self.guard.validate_residency("zerodha", current_region="us-east-1")
        self.assertIn("DATA RESIDENCY VIOLATION", str(ctx.exception))
        self.assertIn("SEBI", str(ctx.exception))

    def test_unregistered_broker_passes(self):
        # Unregistered broker with no strict policy -> True
        self.assertTrue(self.guard.validate_residency("mock_broker_xyz", current_region="us-east-1"))


if __name__ == "__main__":
    unittest.main()

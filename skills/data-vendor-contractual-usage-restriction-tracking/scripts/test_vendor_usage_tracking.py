import unittest
from vendor_usage_tracking import (
    VendorUsageRestrictionEngine, VendorContractSpec, DataAccessRequest, VendorUsageAuditReport
)

class TestVendorUsageRestrictionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = VendorUsageRestrictionEngine()

        # Bloomberg B-PIPE Contract (Non-Display=True, Redistribution=False, MaxSeats=10)
        self.engine.register_contract(VendorContractSpec(
            vendor_id="BLOOMBERG_BPIPE",
            vendor_name="Bloomberg B-PIPE Enterprise",
            license_tier="ENTERPRISE_FEED",
            allowed_use_cases=["INTERNAL_RESEARCH", "NON_DISPLAY_TRADING", "RISK_MANAGEMENT"],
            is_non_display_allowed=True,
            is_redistribution_allowed=False,
            max_concurrent_entitlements=10,
            current_active_entitlements=2
        ))

    def test_internal_hft_trading_request_approved(self):
        req = DataAccessRequest("REQ_01", "BLOOMBERG_BPIPE", "HFT_ENGINE_01", "NON_DISPLAY_TRADING", is_external_redistribution=False)
        report = self.engine.evaluate_access_request(req)
        
        self.assertTrue(report.is_approved)
        self.assertEqual(report.status, "APPROVED")
        self.assertEqual(report.active_entitlements_remaining, 7)

    def test_external_redistribution_request_denied(self):
        req = DataAccessRequest("REQ_02", "BLOOMBERG_BPIPE", "WEB_PORTAL", "INTERNAL_RESEARCH", is_external_redistribution=True)
        report = self.engine.evaluate_access_request(req)
        
        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "REDISTRIBUTION_LICENSING_VIOLATION")

if __name__ == '__main__':
    unittest.main()

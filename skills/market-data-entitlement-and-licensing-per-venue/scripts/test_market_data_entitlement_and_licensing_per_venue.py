import unittest
from market_data_entitlement_and_licensing_per_venue import (
    MarketDataEntitlementEngine, UserEntitlementProfile, DataStreamRequest, EntitlementAuditReport
)

class TestMarketDataEntitlementEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MarketDataEntitlementEngine()
        self.valid_profile = UserEntitlementProfile(
            user_id="PROP_FUND_01",
            is_corporate_or_algo_entity=True,
            is_professional=True,
            has_non_display_license=True,
            licensed_venues={"CME", "NASDAQ", "NYSE"},
            venue_license_expiries={"CME": 2000000000.0, "NASDAQ": 2000000000.0}
        )

    def test_successful_stream_entitlement(self):
        req = DataStreamRequest(user_id="PROP_FUND_01", venue_id="CME", data_level="L2", usage_type="NON_DISPLAY_ALGO")
        report = self.engine.audit_stream_entitlement(self.valid_profile, req)

        self.assertEqual(report.status, "ENTITLEMENT_APPROVED")
        self.assertTrue(report.is_authorized)

    def test_misclassified_subscriber_rejection(self):
        # Algo entity classified as Non-Professional -> REJECT!
        bad_profile = UserEntitlementProfile(
            user_id="BOT_99", is_corporate_or_algo_entity=True, is_professional=False,
            has_non_display_license=True, licensed_venues={"CME"}
        )
        req = DataStreamRequest(user_id="BOT_99", venue_id="CME", data_level="L1", usage_type="NON_DISPLAY_ALGO")
        report = self.engine.audit_stream_entitlement(bad_profile, req)

        self.assertEqual(report.status, "ENTITLEMENT_DENIED_MISCLASSIFIED_SUBSCRIBER")
        self.assertFalse(report.is_authorized)

    def test_unlicensed_venue_rejection(self):
        # Request LSE (not in licensed set {'CME', 'NASDAQ', 'NYSE'}) -> REJECT!
        req = DataStreamRequest(user_id="PROP_FUND_01", venue_id="LSE", data_level="L1", usage_type="NON_DISPLAY_ALGO")
        report = self.engine.audit_stream_entitlement(self.valid_profile, req)

        self.assertEqual(report.status, "ENTITLEMENT_DENIED_UNLICENSED_VENUE")
        self.assertFalse(report.is_authorized)

    def test_missing_non_display_license_rejection(self):
        # User lacks Non-Display license but requests NON_DISPLAY_ALGO -> REJECT!
        display_only_profile = UserEntitlementProfile(
            user_id="TRADER_02", is_corporate_or_algo_entity=False, is_professional=True,
            has_non_display_license=False, licensed_venues={"NASDAQ"}
        )
        req = DataStreamRequest(user_id="TRADER_02", venue_id="NASDAQ", data_level="L1", usage_type="NON_DISPLAY_ALGO")
        report = self.engine.audit_stream_entitlement(display_only_profile, req)

        self.assertEqual(report.status, "ENTITLEMENT_DENIED_MISSING_NON_DISPLAY_LICENSE")
        self.assertFalse(report.is_authorized)

if __name__ == '__main__':
    unittest.main()

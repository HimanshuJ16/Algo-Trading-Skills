import unittest
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
from algorithmic_trading_firm_licensing_thresholds import (
    LicensingThresholdEvaluator, 
    FirmTradingActivity
)

class TestFirmLicensingThresholds(unittest.TestCase):
    def setUp(self):
        self.evaluator = LicensingThresholdEvaluator()

    def test_us_15b9_1_exemption_loss(self):
        # A prop trading firm starts routing to Dark Pools (off-exchange). 
        # Under 2023 SEC rules, they lose their 15b9-1 exemption and must join FINRA.
        activity = FirmTradingActivity(
            jurisdiction='US',
            is_exchange_member=True,
            has_customers=False,
            off_exchange_volume_usd=500_000.0,  # Breaches exemption
            peak_orders_per_second=5
        )
        report = self.evaluator.evaluate(activity)
        self.assertTrue(report.requires_registration)
        self.assertIn("FINRA registration required", report.reason)

    def test_eu_mifid2_hft_designation(self):
        # EU prop firm pushes 100 orders per second, triggering HFT status.
        activity = FirmTradingActivity(
            jurisdiction='EU',
            is_exchange_member=False,
            has_customers=False,
            off_exchange_volume_usd=0.0,
            peak_orders_per_second=100
        )
        report = self.evaluator.evaluate(activity)
        self.assertTrue(report.requires_registration)
        self.assertIn("exceeds HFT designation threshold", report.reason)

    def test_in_sebi_retail_algo_limit(self):
        # Indian trader runs 12 OPS, breaching the typical 10 OPS retail limit.
        activity = FirmTradingActivity(
            jurisdiction='IN',
            is_exchange_member=False,
            has_customers=False,
            off_exchange_volume_usd=0.0,
            peak_orders_per_second=12
        )
        report = self.evaluator.evaluate(activity)
        self.assertTrue(report.requires_registration)
        self.assertIn("exceeds retail limit", report.reason)
        
    def test_compliant_us_prop_firm(self):
        # Strict US prop firm, only trades on the exchange they are a member of.
        activity = FirmTradingActivity(
            jurisdiction='US',
            is_exchange_member=True,
            has_customers=False,
            off_exchange_volume_usd=0.0,
            peak_orders_per_second=100
        )
        report = self.evaluator.evaluate(activity)
        self.assertFalse(report.requires_registration)

if __name__ == '__main__':
    unittest.main()

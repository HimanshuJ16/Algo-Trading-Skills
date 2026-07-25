import unittest
from datetime import datetime
from app_download_and_usage_data_for_consumer_companies import (
    AppUsageDataPoint,
    AppUsageSignalEngine
)

class TestAppUsageSignalEngine(unittest.TestCase):
    def setUp(self):
        self.engine = AppUsageSignalEngine()

    def test_world_class_stickiness(self):
        # A highly engaging app (e.g., major social media)
        data = AppUsageDataPoint(
            ticker="META",
            date=datetime(2026, 1, 1),
            downloads=50000,
            dau=6000000,
            mau=10000000
        )
        # Stickiness = 60%
        signal = self.engine.process(data)
        
        self.assertEqual(signal.stickiness_ratio, 0.60)
        self.assertTrue(signal.is_world_class)
        self.assertFalse(signal.churn_risk_warning)

    def test_leaky_bucket_churn_risk(self):
        # An over-marketed game app losing players fast
        data = AppUsageDataPoint(
            ticker="MOBILE",
            date=datetime(2026, 1, 1),
            downloads=200000,   # High acquisition (>10% of MAU)
            dau=150000,
            mau=1000000
        )
        # Stickiness = 15%
        signal = self.engine.process(data)
        
        self.assertEqual(signal.stickiness_ratio, 0.15)
        self.assertFalse(signal.is_world_class)
        self.assertTrue(signal.churn_risk_warning)
        self.assertIn("LEAKY BUCKET", signal.signal_summary)

    def test_average_engagement(self):
        # A utility app (e.g., airline booking) - low daily use, but not bleeding money on ads
        data = AppUsageDataPoint(
            ticker="AIRLINE",
            date=datetime(2026, 1, 1),
            downloads=10000,    # Low acquisition (1% of MAU)
            dau=150000,
            mau=1000000
        )
        # Stickiness = 15%
        signal = self.engine.process(data)
        
        self.assertEqual(signal.stickiness_ratio, 0.15)
        self.assertFalse(signal.is_world_class)
        self.assertFalse(signal.churn_risk_warning) # Not a leaky bucket because acquisition is low

    def test_dau_exceeds_mau_anomaly(self):
        # Bad data handling
        data = AppUsageDataPoint(
            ticker="ERR",
            date=datetime(2026, 1, 1),
            downloads=0,
            dau=200,
            mau=100 # DAU > MAU is mathematically impossible
        )
        signal = self.engine.process(data)
        self.assertEqual(signal.stickiness_ratio, 1.0) # Truncated to 100%

if __name__ == '__main__':
    unittest.main()

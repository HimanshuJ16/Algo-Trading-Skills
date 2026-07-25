"""
Unit tests for market-data-feed-arbitration-across-vendors skill.
"""
import time
import unittest
from feed_arbitrator import MarketDataFeedArbitrator, VendorStatus


class TestMarketDataFeedArbitrator(unittest.TestCase):

    def setUp(self):
        self.arbitrator = MarketDataFeedArbitrator(max_divergence_pct=0.05, max_stale_seconds=2.0)

    def test_consensus_arbitration_within_tolerance(self):
        t0 = time.time()
        # Primary: 100.00, Secondary: 100.02 (0.02% divergence <= 0.05%)
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=t0)
        res = self.arbitrator.process_vendor_tick("secondary", "AAPL", 100.02, timestamp=t0)

        self.assertTrue(res.is_arbitrated)
        self.assertEqual(res.active_vendor, "CONSENSUS_BOTH")
        self.assertAlmostEqual(res.consensus_price, 100.01, places=2)
        self.assertEqual(res.primary_vendor_status, VendorStatus.HEALTHY)

    def test_divergence_outlier_quarantine(self):
        t0 = time.time()
        # Primary: 100.00, Secondary: 105.00 (5.0% bad tick spike)
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=t0)
        res = self.arbitrator.process_vendor_tick("secondary", "AAPL", 105.00, timestamp=t0)

        self.assertTrue(res.is_arbitrated)
        self.assertEqual(res.secondary_vendor_status, VendorStatus.DIVERGENT_OUTLIER)
        self.assertEqual(res.consensus_price, 100.00)  # Primary preserved

    def test_stale_vendor_failover(self):
        t0 = time.time()
        # Primary tick at t0
        self.arbitrator.process_vendor_tick("primary", "AAPL", 100.00, timestamp=t0)

        # Secondary tick arrives 3.0 seconds later (primary is now stale)
        res = self.arbitrator.process_vendor_tick("secondary", "AAPL", 100.05, timestamp=t0 + 3.0)

        self.assertEqual(res.primary_vendor_status, VendorStatus.STALE_TIMEOUT)
        self.assertEqual(res.active_vendor, "secondary")
        self.assertEqual(res.consensus_price, 100.05)


if __name__ == "__main__":
    unittest.main()

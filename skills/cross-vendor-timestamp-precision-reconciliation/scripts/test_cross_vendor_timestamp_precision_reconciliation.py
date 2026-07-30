import unittest
import datetime
from cross_vendor_timestamp_precision_reconciliation import (
    CrossVendorTimestampReconciler, VendorTickRecord, TimestampReconciliationReport
)

class TestCrossVendorTimestampReconciler(unittest.TestCase):

    def setUp(self):
        self.reconciler = CrossVendorTimestampReconciler(max_allowed_vendor_drift_ms=5.0)

        # 1700000000 seconds = 2023-11-14T22:13:20.000Z
        dt_base = datetime.datetime.fromtimestamp(1700000000.0, tz=datetime.timezone.utc)
        iso_base_str = dt_base.strftime("%Y-%m-%dT%H:%M:%S")

        # 3 Multi-vendor ticks for AAPL at t ~ 1700000000 s
        # 1. Bloomberg: float seconds 1700000000.100 (1700000000100000000 ns)
        # 2. Refinitiv: ISO string '2023-11-14T22:13:20.102Z' (1700000000102000000 ns) -> 2ms after Bloomberg
        # 3. Databento: int64 nanoseconds 1700000000120000000 ns -> 20ms after Bloomberg (triggers >5ms drift warning!)
        self.ticks = [
            VendorTickRecord("T1", "BLOOMBERG", "AAPL", 180.50, 100, 1700000000.100, "SECONDS"),
            VendorTickRecord("T2", "REFINITIV", "AAPL", 180.51, 200, f"{iso_base_str}.102Z", "ISO8601"),
            VendorTickRecord("T3", "DATABENTO", "AAPL", 180.55, 300, 1700000000120000000, "NANOSECONDS")
        ]

    def test_timestamp_normalization_and_sorting(self):
        report = self.reconciler.reconcile_vendor_ticks(self.ticks)
        
        self.assertEqual(report.total_ticks_processed, 3)
        self.assertEqual(len(report.normalized_ticks), 3)
        
        # Verify nanosecond integer ordering
        ns_list = [t.normalized_ns_utc for t in report.normalized_ticks]
        self.assertEqual(ns_list, sorted(ns_list))
        
        # Databento vs Refinitiv drift (18ms > 5ms limit) triggers drift warning
        self.assertGreaterEqual(len(report.vendor_drift_warnings), 1)

    def test_iso_and_milliseconds_conversion(self):
        ns, iso_str, tier = self.reconciler.normalize_timestamp_to_ns(1700000000500, "MILLISECONDS")
        self.assertEqual(ns, 1700000000500000000)
        self.assertEqual(tier, "MILLISECONDS")

if __name__ == '__main__':
    unittest.main()

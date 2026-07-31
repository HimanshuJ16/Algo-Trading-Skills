import unittest
from price_reconciliation import (
    MultiSourcePriceReconcilerEngine, VendorPriceQuote, ReconciliationConfig, PriceReconciliationReport
)

class TestMultiSourcePriceReconcilerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MultiSourcePriceReconcilerEngine()

    def test_outlier_filtering_and_tolerance_weighted_average(self):
        # 3 quotes:
        # Bloomberg: $100.00 (weight 1.0)
        # Refinitiv: $100.02 (weight 1.0)
        # Bad Polygon Quote: $105.00 (5% deviation -> Outlier!)
        quotes = [
            VendorPriceQuote("BLOOMBERG", "AAPL", price=100.00, timestamp=1000.0, vendor_priority=1, reliability_weight=1.0),
            VendorPriceQuote("REFINITIV", "AAPL", price=100.02, timestamp=1000.0, vendor_priority=2, reliability_weight=1.0),
            VendorPriceQuote("POLYGON_BAD", "AAPL", price=105.00, timestamp=1000.0, vendor_priority=3, reliability_weight=1.0),
        ]

        report = self.engine.reconcile_prices("AAPL", quotes)

        self.assertEqual(report.status, "RECONCILIATION_SUCCESS")
        self.assertEqual(report.total_quotes_received, 3)
        self.assertEqual(report.valid_quotes_count, 2)
        self.assertEqual(report.outlier_quotes_count, 1)
        # Weighted average of $100.00 and $100.02 = $100.01
        self.assertEqual(report.canonical_price, 100.01)

    def test_priority_tie_breaker_on_conflicting_quotes(self):
        # 2 quotes outside tolerance (spread = 0.50% > 0.05% tolerance):
        # Bloomberg: $100.00 (Priority 1)
        # Refinitiv: $100.50 (Priority 2)
        cfg = ReconciliationConfig(max_deviation_pct=0.05, tolerance_pct=0.0005, tie_breaker_method="PRIORITY")
        engine = MultiSourcePriceReconcilerEngine(cfg)

        quotes = [
            VendorPriceQuote("REFINITIV", "AAPL", price=100.50, timestamp=1000.0, vendor_priority=2),
            VendorPriceQuote("BLOOMBERG", "AAPL", price=100.00, timestamp=1000.0, vendor_priority=1),
        ]

        report = engine.reconcile_prices("AAPL", quotes)

        self.assertEqual(report.canonical_price, 100.00)
        self.assertEqual(report.winning_vendor_id, "BLOOMBERG")
        self.assertEqual(report.tie_breaker_used, "PRIORITY_RANK")

if __name__ == '__main__':
    unittest.main()

import unittest
import datetime
from vendor_specific_adjustment_methodology_reconciliation import (
    CorporateActionType,
    VendorMethodology,
    CorporateAction,
    PriceBar,
    ReconciliationDivergence,
    ReconciliationReport,
    VendorAdjustmentReconciliationEngine,
    ReconciliationError,
)


class TestVendorAdjustmentReconciliationEngine(unittest.TestCase):
    def setUp(self):
        self.engine = VendorAdjustmentReconciliationEngine()

        self.d1 = datetime.date(2025, 1, 10)
        self.d2 = datetime.date(2025, 1, 13)
        self.d3 = datetime.date(2025, 1, 14)  # Ex-date

        self.raw_bars = [
            PriceBar("AAPL", self.d1, 200.0, 205.0, 199.0, 200.0, 1_000_000.0),
            PriceBar("AAPL", self.d2, 200.0, 202.0, 198.0, 200.0, 1_200_000.0),
            PriceBar("AAPL", self.d3, 100.0, 103.0, 99.0, 100.0, 2_400_000.0),
        ]

    def test_stock_split_adjustment_factor(self):
        # 2-for-1 stock split on d3
        split_action = CorporateAction(
            event_id="CA-001",
            symbol="AAPL",
            ex_date=self.d3,
            action_type=CorporateActionType.STOCK_SPLIT,
            split_ratio=2.0,
        )

        adj_bars = self.engine.adjust_price_series(
            self.raw_bars, [split_action], VendorMethodology.CRSP_TOTAL_RETURN
        )

        # Before split (d1, d2): prices halved (200 -> 100), volume doubled (1M -> 2M)
        self.assertEqual(adj_bars[0].close, 100.0)
        self.assertEqual(adj_bars[0].volume, 2_000_000.0)
        # On/after split (d3): prices unadjusted (100 -> 100)
        self.assertEqual(adj_bars[2].close, 100.0)

    def test_cash_dividend_adjustment_factor(self):
        # $10 cash dividend on d3, cum_price = $200
        div_action = CorporateAction(
            event_id="CA-002",
            symbol="AAPL",
            ex_date=self.d3,
            action_type=CorporateActionType.CASH_DIVIDEND,
            cash_amount=10.0,
            cum_price=200.0,
        )
        # Factor = 1 - (10/200) = 0.95

        adj_bars = self.engine.adjust_price_series(
            self.raw_bars, [div_action], VendorMethodology.CRSP_TOTAL_RETURN
        )

        # Before ex-date (d1): price 200 * 0.95 = 190.0
        self.assertEqual(adj_bars[0].close, 190.0)
        # Ex-date (d3): price unadjusted = 100.0
        self.assertEqual(adj_bars[2].close, 100.0)

    def test_reconcile_identical_series_passed(self):
        report = self.engine.reconcile_vendor_series(
            symbol="AAPL",
            series_a=self.raw_bars,
            vendor_a_name="Bloomberg",
            series_b=self.raw_bars,
            vendor_b_name="Refinitiv",
            tolerance_pct=0.5,
        )

        self.assertEqual(report.status, "PASSED")
        self.assertEqual(report.divergence_count, 0)
        self.assertEqual(report.max_divergence_pct, 0.0)

    def test_reconcile_divergent_vendor_series_failed(self):
        # Series B has unadjusted pre-split price 200.0 vs adjusted 100.0 in Series A
        series_b = [
            PriceBar("AAPL", self.d1, 200.0, 205.0, 199.0, 200.0, 1_000_000.0),
            PriceBar("AAPL", self.d2, 200.0, 202.0, 198.0, 200.0, 1_200_000.0),
            PriceBar("AAPL", self.d3, 100.0, 103.0, 99.0, 100.0, 2_400_000.0),
        ]
        # Split adjust Series A
        split_action = CorporateAction(
            event_id="CA-001",
            symbol="AAPL",
            ex_date=self.d3,
            action_type=CorporateActionType.STOCK_SPLIT,
            split_ratio=2.0,
        )
        series_a = self.engine.adjust_price_series(self.raw_bars, [split_action], VendorMethodology.CRSP_TOTAL_RETURN)

        report = self.engine.reconcile_vendor_series(
            symbol="AAPL",
            series_a=series_a,
            vendor_a_name="CRSP",
            series_b=series_b,
            vendor_b_name="Unadjusted Feed",
            tolerance_pct=0.5,
        )

        self.assertEqual(report.status, "FAILED")
        self.assertTrue(report.divergence_count > 0)
        self.assertTrue(report.max_divergence_pct > 50.0)

    def test_no_common_dates_raises_error(self):
        other_bars = [
            PriceBar("AAPL", datetime.date(2020, 1, 1), 10.0, 10.0, 10.0, 10.0, 100.0)
        ]
        with self.assertRaises(ReconciliationError):
            self.engine.reconcile_vendor_series(
                "AAPL", self.raw_bars, "Vendor A", other_bars, "Vendor B"
            )


if __name__ == "__main__":
    unittest.main()


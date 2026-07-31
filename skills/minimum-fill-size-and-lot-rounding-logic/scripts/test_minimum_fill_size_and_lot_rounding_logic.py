import unittest
from minimum_fill_size_and_lot_rounding_logic import (
    MinimumFillSizeAndLotRoundingEngine, OrderRoundingConfig, RawOrderRequest, OrderRoundingReport
)

class TestMinimumFillSizeAndLotRoundingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MinimumFillSizeAndLotRoundingEngine()

    def test_floor_lot_rounding_and_fix_tags(self):
        # Raw Qty = 275 shares, Lot Size = 100, MinQty = 100, Mode = FLOOR
        # Rounded Qty = 200 shares -> LOT_ROUNDING_SUCCESS / ODD_LOT_ADJUSTED!
        cfg = OrderRoundingConfig("AAPL", lot_size=100.0, min_qty=100.0, rounding_mode="FLOOR")
        order = RawOrderRequest("ORD_101", "AAPL", "BUY", raw_quantity=275.0, limit_price=150.0)

        report = self.engine.apply_lot_rounding_and_min_qty_rules(cfg, order)

        self.assertTrue(report.is_compliant)
        self.assertEqual(report.rounded_quantity, 200.0)
        self.assertEqual(report.fix_tag_110_min_qty, 100.0)
        self.assertEqual(report.fix_tag_1089_match_increment, 100.0)
        self.assertTrue(report.is_odd_lot)

    def test_order_rejected_below_min_qty(self):
        # Raw Qty = 50 shares < MinQty 100 shares under FLOOR mode -> ORDER_REJECTED_BELOW_MIN_QTY!
        cfg = OrderRoundingConfig("TSE_7203", lot_size=100.0, min_qty=100.0, rounding_mode="FLOOR")
        order = RawOrderRequest("ORD_102", "TSE_7203", "BUY", raw_quantity=50.0, limit_price=2000.0)

        report = self.engine.apply_lot_rounding_and_min_qty_rules(cfg, order)

        self.assertFalse(report.is_compliant)
        self.assertEqual(report.status, "ORDER_REJECTED_BELOW_MIN_QTY")
        self.assertEqual(report.rounded_quantity, 0.0)

if __name__ == '__main__':
    unittest.main()

import unittest
from deutsche_borse_xetra_api_integration import (
    DeutscheBorseXetraApiEngine, XetraOrderRequest, XetraOrderExecutionReport
)

class TestDeutscheBorseXetraApiEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DeutscheBorseXetraApiEngine(session_id=98765)

    def test_valid_xetra_t7_order_dispatched(self):
        # Order for Mercedes-Benz (DE0007100000) @ €62.50 (valid €0.01 tick)
        req = XetraOrderRequest(
            cl_ord_id="ORD_XETRA_01",
            isin="DE0007100000",
            side="BUY",
            order_qty=500,
            price_eur=62.50,
            account_type="P",                 # Proprietary
            mifid_short_code=99201
        )
        report = self.engine.process_xetra_order(req)
        
        self.assertTrue(report.is_dispatched)
        self.assertEqual(report.status, "STATUS_OK")
        self.assertIsNotNone(report.eti_header)
        self.assertEqual(report.eti_header.session_id, 98765)

    def test_invalid_xetra_tick_size_rejected(self):
        # Order @ €62.503 (violates €0.01 tick step for price >= €50.0) -> REJECTED!
        req = XetraOrderRequest(
            cl_ord_id="ORD_XETRA_02",
            isin="DE0007100000",
            side="BUY",
            order_qty=500,
            price_eur=62.503,
            account_type="P",
            mifid_short_code=99201
        )
        report = self.engine.process_xetra_order(req)
        
        self.assertFalse(report.is_dispatched)
        self.assertEqual(report.status, "INVALID_TICK_SIZE")

if __name__ == '__main__':
    unittest.main()

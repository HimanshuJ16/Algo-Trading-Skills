import unittest
from eurex_market_data_and_order_api import (
    EurexMarketDataAndOrderApiEngine, EurexOrderRequest, EurexOrderExecutionReport
)

class TestEurexMarketDataAndOrderApiEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EurexMarketDataAndOrderApiEngine(session_id=55443)

    def test_valid_fesx_futures_order_dispatched(self):
        # EURO STOXX 50 Future (FESX 202609) @ 4851.0 (valid 1.0 tick, Mid=4850.5, Notional = 10 * 4851.0 * 10 = €485,100)
        req = EurexOrderRequest(
            cl_ord_id="ORD_EUREX_01",
            contract_symbol="FESX",
            expiry="202609",
            side="BUY",
            order_qty=10,
            price=4851.0,
            market_mid_price=4850.5
        )
        report = self.engine.process_eurex_order(req)
        
        self.assertTrue(report.is_dispatched)
        self.assertEqual(report.status, "STATUS_OK")
        self.assertEqual(report.notional_value_eur, 485100.0)
        self.assertIsNotNone(report.eti_header)
        self.assertEqual(report.eti_header.session_id, 55443)

    def test_off_tick_price_fesx_rejected(self):
        # Price 4851.5 (violates 1.0 tick step for FESX) -> REJECTED!
        req = EurexOrderRequest(
            cl_ord_id="ORD_EUREX_02",
            contract_symbol="FESX",
            expiry="202609",
            side="BUY",
            order_qty=10,
            price=4851.5,
            market_mid_price=4850.5
        )
        report = self.engine.process_eurex_order(req)
        
        self.assertFalse(report.is_dispatched)
        self.assertEqual(report.status, "INVALID_TICK_SIZE")

    def test_price_reasonability_breach_rejected(self):
        # Price 4950.0 (deviates 99.5 points > max 50.0 points from Mid 4850.5) -> REJECTED!
        req = EurexOrderRequest(
            cl_ord_id="ORD_EUREX_03",
            contract_symbol="FESX",
            expiry="202609",
            side="BUY",
            order_qty=10,
            price=4950.0,
            market_mid_price=4850.5
        )
        report = self.engine.process_eurex_order(req)
        
        self.assertFalse(report.is_dispatched)
        self.assertEqual(report.status, "PRICE_REASONABILITY_BREACH")

if __name__ == '__main__':
    unittest.main()

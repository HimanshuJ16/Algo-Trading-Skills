import unittest
from dubai_financial_market_dfm_api import (
    DubaiFinancialMarketApiEngine, DfmOrderRequest, DfmOrderExecutionReport
)

class TestDubaiFinancialMarketApiEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DubaiFinancialMarketApiEngine()

    def test_valid_dfm_order_dispatched(self):
        # Emaar Properties (EMAAR) @ 7.85 AED (valid 0.01 tick, NIN 1099887766, 0.64% settlement deviation)
        req = DfmOrderRequest(
            cl_ord_id="ORD_DFM_01",
            nin_investor_number="1099887766",
            symbol="EMAAR",
            side="BUY",
            order_qty=10000,
            price_aed=7.85,
            prior_settlement_price_aed=7.80
        )
        report = self.engine.process_dfm_order(req)
        
        self.assertTrue(report.is_dispatched)
        self.assertEqual(report.status, "STATUS_OK")
        self.assertIsNotNone(report.fix_payload)
        self.assertEqual(report.fix_payload.account_nin, "1099887766")
        self.assertIn("15=AED", report.fix_payload.fix_raw_string)

    def test_off_tick_price_rejected(self):
        # Price 7.855 AED (violates 0.01 tick step for price between 1.00 and 10.00 AED) -> REJECTED!
        req = DfmOrderRequest(
            cl_ord_id="ORD_DFM_02",
            nin_investor_number="1099887766",
            symbol="EMAAR",
            side="BUY",
            order_qty=10000,
            price_aed=7.855,
            prior_settlement_price_aed=7.80
        )
        report = self.engine.process_dfm_order(req)
        
        self.assertFalse(report.is_dispatched)
        self.assertEqual(report.status, "INVALID_TICK_SIZE")

    def test_circuit_breaker_breach_rejected(self):
        # Price 9.00 AED (> 10% deviation from settlement 7.80 AED) -> REJECTED!
        req = DfmOrderRequest(
            cl_ord_id="ORD_DFM_03",
            nin_investor_number="1099887766",
            symbol="EMAAR",
            side="BUY",
            order_qty=10000,
            price_aed=9.00,
            prior_settlement_price_aed=7.80
        )
        report = self.engine.process_dfm_order(req)
        
        self.assertFalse(report.is_dispatched)
        self.assertEqual(report.status, "CIRCUIT_BREAKER_BAND_BREACH")

if __name__ == '__main__':
    unittest.main()

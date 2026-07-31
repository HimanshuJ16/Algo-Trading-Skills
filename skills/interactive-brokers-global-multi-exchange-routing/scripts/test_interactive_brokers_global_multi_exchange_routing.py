import unittest
from interactive_brokers_global_multi_exchange_routing import (
    IbkrGlobalRoutingEngine, IbkrContractSpec, IbkrOrderPayload, IbkrRoutingReport
)

class TestIbkrGlobalRoutingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = IbkrGlobalRoutingEngine()

    def test_valid_us_equity_smart_routing(self):
        contract = IbkrContractSpec(
            symbol="AAPL", sec_type="STK", currency="USD", exchange="SMART",
            primary_exchange="NASDAQ", routing_mode="SMART_BEST_EXECUTION"
        )
        payload = IbkrOrderPayload(contract, action="BUY", order_type="LMT", quantity=100, lmt_price=150.0)
        report = self.engine.audit_and_route_order(payload)

        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertEqual(report.symbol, "AAPL")
        self.assertEqual(report.target_exchange, "SMART")
        self.assertEqual(report.primary_exchange, "NASDAQ")
        self.assertTrue(report.is_contract_valid)

    def test_valid_hkex_numeric_symbol_routing(self):
        # Tencent HKEX stock code 700 -> Auto-padded to 00700!
        contract = IbkrContractSpec(
            symbol="700", sec_type="STK", currency="HKD", exchange="SEHK",
            primary_exchange="SEHK", routing_mode="SMART_BEST_EXECUTION"
        )
        payload = IbkrOrderPayload(contract, action="BUY", order_type="LMT", quantity=500, lmt_price=300.0)
        report = self.engine.audit_and_route_order(payload)

        self.assertEqual(report.status, "IBKR_ROUTING_VALIDATED")
        self.assertEqual(report.symbol, "00700") # Padded!

    def test_currency_mismatch_rejection(self):
        # EUR currency routed to US venue ISLAND -> REJECTED_CURRENCY_MISMATCH!
        contract = IbkrContractSpec(
            symbol="AAPL", sec_type="STK", currency="EUR", exchange="ISLAND",
            primary_exchange="NASDAQ", routing_mode="DIRECT_EXCHANGE"
        )
        payload = IbkrOrderPayload(contract, action="BUY", order_type="LMT", quantity=100, lmt_price=150.0)
        report = self.engine.audit_and_route_order(payload)

        self.assertEqual(report.status, "REJECTED_CURRENCY_MISMATCH")
        self.assertFalse(report.is_currency_matched)

if __name__ == '__main__':
    unittest.main()

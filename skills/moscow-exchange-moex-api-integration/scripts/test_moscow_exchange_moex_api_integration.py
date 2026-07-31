import unittest
from moscow_exchange_moex_api_integration import (
    MOEXApiIntegrationEngine, MOEXOrderConfig, MOEXOrderRequest, MOEXOrderReport
)

class TestMOEXApiIntegrationEngine(unittest.TestCase):

    def setUp(self):
        self.engine = MOEXApiIntegrationEngine()

    def test_valid_tqbr_equity_order_approval(self):
        # SBER order on TQBR board, ref price 280.00 RUB, order price 280.50 RUB (0.18% dev <= 5% collar) -> APPROVED!
        cfg = MOEXOrderConfig("SBER", "TQBR", "ACC_MOEX_01", "CLIENT_99", max_price_collar_pct=0.05)
        order = MOEXOrderRequest("SBER", "TQBR", "BUY", quantity=100, price=280.50, reference_price=280.00, tick_size=0.01)

        report = self.engine.validate_and_serialize_order(cfg, order)

        self.assertTrue(report.is_approved)
        self.assertEqual(report.status, "MOEX_ORDER_APPROVED")
        self.assertEqual(report.rounded_price, 280.50)
        self.assertEqual(report.fix_twime_payload["SecurityExchange"], "MISX")
        self.assertEqual(report.fix_twime_payload["BoardID"], "TQBR")

    def test_price_collar_breach_rejection(self):
        # SBER order price 310.00 RUB vs ref price 280.00 RUB (10.7% dev > 5% collar) -> MOEX_PRICE_COLLAR_BREACH!
        cfg = MOEXOrderConfig("SBER", "TQBR", "ACC_MOEX_01", "CLIENT_99", max_price_collar_pct=0.05)
        order = MOEXOrderRequest("SBER", "TQBR", "BUY", quantity=100, price=310.00, reference_price=280.00, tick_size=0.01)

        report = self.engine.validate_and_serialize_order(cfg, order)

        self.assertFalse(report.is_approved)
        self.assertEqual(report.status, "MOEX_PRICE_COLLAR_BREACH")
        self.assertEqual(report.rounded_price, 310.00)

if __name__ == '__main__':
    unittest.main()

import unittest
from euronext_optiq_market_data_integration import (
    EuronextOptiqMarketDataEngine, OptiqMarketDataAuditReport, MSG_TYPE_MARKET_UPDATE, MSG_TYPE_SYMBOL_STATUS
)

class TestEuronextOptiqMarketDataEngine(unittest.TestCase):

    def setUp(self):
        self.engine = EuronextOptiqMarketDataEngine()

    def test_optiq_l2_book_reconstruction_and_imbalance(self):
        # Add LVMH (FR0000121014) Bid = €785.00 @ 500 qty, Ask = €785.50 @ 200 qty
        self.engine.update_book_level(side="BUY", price_eur=785.00, quantity=500, action="ADD")
        self.engine.update_book_level(side="SELL", price_eur=785.50, quantity=200, action="ADD")

        report = self.engine.process_optiq_message(
            isin="FR0000121014", symbol_id="MC_PAR", msg_type=MSG_TYPE_MARKET_UPDATE,
            seq_num=1001, book_in_ns=1700000000000000000
        )

        self.assertTrue(report.is_quoting_allowed)
        self.assertEqual(report.best_bid_eur, 785.00)
        self.assertEqual(report.best_ask_eur, 785.50)
        self.assertEqual(report.mid_price_eur, 785.25)
        self.assertEqual(report.spread_eur, 0.50)
        # Imbalance = (500 - 200) / (500 + 200) = 300 / 700 = +0.4286
        self.assertAlmostEqual(report.book_imbalance_ratio, 0.4286, places=3)

    def test_trading_halt_status_disables_quoting(self):
        self.engine.update_book_level(side="BUY", price_eur=785.00, quantity=500, action="ADD")
        self.engine.update_book_level(side="SELL", price_eur=785.50, quantity=200, action="ADD")

        report = self.engine.process_optiq_message(
            isin="FR0000121014", symbol_id="MC_PAR", msg_type=MSG_TYPE_SYMBOL_STATUS,
            seq_num=1002, book_in_ns=1700000000100000000, status_override="TRADING_HALTED"
        )

        self.assertFalse(report.is_quoting_allowed)
        self.assertEqual(report.trading_status, "TRADING_HALTED")

if __name__ == '__main__':
    unittest.main()

import unittest
from deribit_crypto_options_api import (
    DeribitCryptoOptionsApiEngine, DeribitOrderRequest, DeribitOptionTicker, DeribitOptionsOrderReport
)

class TestDeribitCryptoOptionsApiEngine(unittest.TestCase):

    def setUp(self):
        self.engine = DeribitCryptoOptionsApiEngine(is_testnet=True)
        
        # Ticker for BTC-28MAR26-60000-C (Index = $60,000, Mark Price = 0.05 BTC, Delta = 0.60)
        self.ticker = DeribitOptionTicker(
            instrument_name="BTC-28MAR26-60000-C",
            index_price_usd=60000.0,
            mark_price_coin=0.05,
            mark_iv=65.0,
            delta=0.60,
            gamma=0.0001,
            vega=12.5,
            theta=-1.5,
            best_bid_price_coin=0.049,
            best_ask_price_coin=0.051
        )

    def test_inverse_option_premium_and_greeks_conversion(self):
        # Order: BUY 10 contracts of BTC-28MAR26-60000-C @ 0.05 BTC
        # Premium Coin = 10 * 0.05 = 0.50 BTC
        # Premium USD = 0.50 * 60,000 = $30,000 USD
        # Delta Coin = 10 * 0.60 = 6.0 BTC
        # Delta USD = 6.0 * 60,000 = $360,000 USD
        req = DeribitOrderRequest("ORD_DERIBIT_01", "BTC-28MAR26-60000-C", "buy", amount_contracts=10.0, price_coin=0.05)
        report = self.engine.process_option_order(req, self.ticker, available_balance_coin=2.0)
        
        self.assertTrue(report.is_dispatched)
        self.assertEqual(report.price_usd_equivalent, 3000.0)
        self.assertEqual(report.total_premium_coin, 0.50)
        self.assertEqual(report.total_premium_usd, 30000.0)
        self.assertEqual(report.portfolio_delta_coin_impact, 6.0)
        self.assertEqual(report.portfolio_delta_usd_impact, 360000.0)
        self.assertEqual(report.json_rpc_payload["method"], "private/buy")

    def test_insufficient_balance_rejection(self):
        # Premium required = 0.50 BTC, Available = 0.10 BTC -> REJECTED!
        req = DeribitOrderRequest("ORD_DERIBIT_02", "BTC-28MAR26-60000-C", "buy", amount_contracts=10.0, price_coin=0.05)
        report = self.engine.process_option_order(req, self.ticker, available_balance_coin=0.10)
        
        self.assertFalse(report.is_dispatched)

if __name__ == '__main__':
    unittest.main()

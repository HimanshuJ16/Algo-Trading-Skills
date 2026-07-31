import unittest
from okx_unified_account_api import (
    OKXUnifiedAccountEngine, OKXTokenBalance, OKXAccountReport
)

class TestOKXUnifiedAccountEngine(unittest.TestCase):

    def setUp(self):
        self.engine = OKXUnifiedAccountEngine(
            api_key="TEST_API_KEY",
            secret_key="TEST_SECRET_KEY",
            passphrase="TEST_PASSPHRASE"
        )

    def test_hmac_sha256_signature_generation(self):
        # Known test vector prehash
        timestamp = "2026-07-31T13:39:00.000Z"
        method = "GET"
        path = "/api/v5/account/balance?ccy=BTC"
        body = ""

        sig = self.engine.generate_signature(timestamp, method, path, body)
        self.assertTrue(len(sig) > 20)

        headers = self.engine.get_auth_headers(method, path, body, timestamp)
        self.assertEqual(headers["OK-ACCESS-KEY"], "TEST_API_KEY")
        self.assertEqual(headers["OK-ACCESS-SIGN"], sig)
        self.assertEqual(headers["OK-ACCESS-PASSPHRASE"], "TEST_PASSPHRASE")

    def test_multi_currency_margin_equity_and_status(self):
        # 1 BTC ($60,000, 0.95 factor = $57,000) + 10,000 USDT ($1.0, 1.0 factor = $10,000)
        # Total USD Equity = $70,000, Adjusted Equity = $67,000
        balances = [
            OKXTokenBalance("BTC", 1.0, 60000.0, 0.95),
            OKXTokenBalance("USDT", 10000.0, 1.0, 1.0)
        ]

        # Case 1: Maintenance margin $10,000 -> mrr = 670% -> SAFE
        report_safe = self.engine.compute_multi_currency_margin(balances, 10000.0)
        self.assertEqual(report_safe.status, "SAFE")
        self.assertEqual(report_safe.adjusted_usd_equity, 67000.0)
        self.assertAlmostEqual(report_safe.margin_ratio_pct, 670.0, delta=0.1)

        # Case 2: Maintenance margin $70,000 -> mrr = 95.71% -> LIQUIDATION_RISK_CALL
        report_call = self.engine.compute_multi_currency_margin(balances, 70000.0)
        self.assertEqual(report_call.status, "LIQUIDATION_RISK_CALL")
        self.assertLessEqual(report_call.margin_ratio_pct, 100.0)

    def test_order_payload_building(self):
        payload = self.engine.build_order_payload(
            inst_id="BTC-USDT-SWAP",
            td_mode="cross",
            side="buy",
            ord_type="limit",
            size=1.0,
            price=60000.0
        )
        self.assertEqual(payload["instId"], "BTC-USDT-SWAP")
        self.assertEqual(payload["tdMode"], "cross")
        self.assertEqual(payload["side"], "buy")
        self.assertEqual(payload["px"], "60000.0")

if __name__ == '__main__':
    unittest.main()

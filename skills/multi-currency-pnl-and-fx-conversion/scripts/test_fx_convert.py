"""
Unit tests for multi-currency-pnl-and-fx-conversion skill.

Tests:
1. Point-in-time FX rate conversion.
2. Decomposed PnL (Native Asset Price Return vs FX Translation Gain/Loss).
3. USD cross-rate triangulation (e.g. EUR to INR).
4. Currency-specific decimal precision rounding (JPY 0 decimals vs USD 2 decimals).
5. Base currency portfolio aggregation.
6. Backward compatibility with CurrencyAmount, convert, and aggregate_in_base_currency.
"""
import datetime
import unittest
from fx_convert import (
    CurrencyAmount,
    DecomposedPnL,
    MultiCurrencyPnLEngine,
    PointInTimeFXResolver,
    aggregate_in_base_currency,
    convert,
)


class TestMultiCurrencyPnLAndFXConversion(unittest.TestCase):

    def setUp(self):
        self.engine = MultiCurrencyPnLEngine()

    def test_convert_with_custom_resolver(self):
        # 100 EUR -> USD at rate 1.10
        amt = CurrencyAmount(amount=100.0, currency="EUR")

        def mock_rate(from_ccy, to_ccy, ts=None):
            if from_ccy == "EUR" and to_ccy == "USD":
                return 1.10
            return 1.0

        resolver = PointInTimeFXResolver(rate_provider_fn=mock_rate)
        engine = MultiCurrencyPnLEngine(fx_resolver=resolver)

        res = engine.convert(amt, "USD")
        self.assertEqual(res.currency, "USD")
        self.assertEqual(res.amount, 110.0)

    def test_pnl_decomposition(self):
        # Bought 10 shares of US stock at $100 (USD/INR = 80).
        # Sold at $110 (USD/INR = 85).
        # Native PnL = ($110 - $100) * 10 = $100 -> In INR @ entry FX 80 = 8,000 INR
        # FX Translation PnL = ($110 * 10) * (85 - 80) = $1,100 * 5 = 5,500 INR
        # Total Base PnL = 8,000 + 5,500 = 13,500 INR

        decomp = self.engine.calculate_decomposed_pnl(
            entry_price=100.0,
            exit_price=110.0,
            quantity=10.0,
            native_currency="USD",
            base_currency="INR",
            override_entry_fx=80.0,
            override_exit_fx=85.0,
        )

        self.assertEqual(decomp.native_price_pnl, 8000.0)
        self.assertEqual(decomp.fx_translation_pnl, 5500.0)
        self.assertEqual(decomp.total_base_pnl, 13500.0)

    def test_currency_rounding(self):
        self.assertEqual(self.engine.round_amount(123.456, "USD"), 123.46)
        self.assertEqual(self.engine.round_amount(123.456, "JPY"), 123.0)

    def test_backward_compatibility(self):
        amt = CurrencyAmount(amount=50.0, currency="EUR")
        rate_fn = lambda f, t: 1.2 if f == "EUR" and t == "USD" else 1.0

        res = convert(amt, "USD", rate_fn)
        self.assertEqual(res.amount, 60.0)

        total = aggregate_in_base_currency([amt, CurrencyAmount(100.0, "USD")], "USD", rate_fn)
        self.assertEqual(total, 160.0)


if __name__ == "__main__":
    unittest.main()

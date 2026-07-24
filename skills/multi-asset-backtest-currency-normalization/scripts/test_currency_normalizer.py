"""
Unit tests for multi-asset-backtest-currency-normalization skill.

Tests:
1. Converting cash balances in EUR, JPY, and USD to USD reporting currency.
2. Converting position valuations in local currencies.
3. Total NAV calculation in reporting currency.
4. Error handling for missing point-in-time FX rates.
"""
import datetime
import unittest
from currency_normalizer import (
    CurrencyMismatchError,
    MultiCurrencyNAV,
    MultiCurrencyPortfolioNormalizer,
    PositionValuation,
)


class TestMultiCurrencyPortfolioNormalizer(unittest.TestCase):

    def setUp(self):
        self.norm = MultiCurrencyPortfolioNormalizer(reporting_currency="USD")
        self.eval_date = datetime.date(2026, 7, 24)

        # Register FX rates for eval_date
        # EUR/USD = 1.10 (1 EUR = 1.10 USD)
        self.norm.register_fx_rate("EUR", "USD", self.eval_date, 1.10)
        # USD/JPY = 150.0 (1 USD = 150 JPY -> 1 JPY = 0.006667 USD)
        self.norm.register_fx_rate("USD", "JPY", self.eval_date, 150.0)

    def test_cash_and_position_conversion_to_usd(self):
        # Set cash balances
        self.norm.set_cash_balance("USD", 50000.0)
        self.norm.set_cash_balance("EUR", 10000.0)  # 10000 EUR * 1.10 = 11000 USD

        # Add European stock position (SAP in EUR)
        self.norm.add_position(
            PositionValuation(symbol="SAP", local_currency="EUR", quantity=100, price_in_local=150.0)
        )
        # 100 * 150 EUR = 15,000 EUR * 1.10 = 16,500 USD

        nav_res = self.norm.compute_total_nav(self.eval_date)

        # Total cash in USD: 50,000 + 11,000 = 61,000 USD
        self.assertAlmostEqual(nav_res.total_cash_reporting, 61000.0, delta=0.01)
        # Total positions in USD: 16,500 USD
        self.assertAlmostEqual(nav_res.total_positions_reporting, 16500.0, delta=0.01)
        # Total NAV in USD: 61,000 + 16,500 = 77,500 USD
        self.assertAlmostEqual(nav_res.total_nav_reporting, 77500.0, delta=0.01)

    def test_jpy_conversion(self):
        # 1,500,000 JPY / 150 = 10,000 USD
        converted_usd = self.norm.convert_amount(1500000.0, "JPY", "USD", self.eval_date)
        self.assertAlmostEqual(converted_usd, 10000.0, delta=0.01)

    def test_missing_fx_rate_raises_error(self):
        missing_date = datetime.date(2020, 1, 1)
        with self.assertRaises(CurrencyMismatchError):
            self.norm.get_fx_rate("EUR", "USD", missing_date)


if __name__ == "__main__":
    unittest.main()

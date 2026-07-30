import unittest
from currency_pair_quoting_convention_normalization import (
    CurrencyPairQuotingNormalizer, RawFxQuote, NormalizedFxQuoteReport
)

class TestCurrencyPairQuotingNormalizer(unittest.TestCase):

    def setUp(self):
        self.normalizer = CurrencyPairQuotingNormalizer()

    def test_standard_eur_usd_quote(self):
        quote = RawFxQuote("EUR/USD", 1.0990, 1.0992, "BLOOMBERG")
        report = self.normalizer.normalize_quote(quote)
        
        self.assertEqual(report.normalized_symbol, "EUR/USD")
        self.assertFalse(report.is_inverted)
        self.assertEqual(report.normalized_bid, 1.0990)
        self.assertEqual(report.normalized_ask, 1.0992)
        self.assertEqual(report.pip_size, 0.0001)
        self.assertEqual(report.spread_pips, 2.0)

    def test_inverted_usd_eur_quote_conversion(self):
        # Inverted USD/EUR quote: Bid = 0.90909 (1/1.1000), Ask = 0.90950 (1/1.0995)
        # Should convert to EUR/USD:
        # Norm Bid = 1 / 0.90950 = 1.09951
        # Norm Ask = 1 / 0.90909 = 1.10000
        quote = RawFxQuote("USD/EUR", 0.90909, 0.90950, "REFINITIV")
        report = self.normalizer.normalize_quote(quote)
        
        self.assertTrue(report.is_inverted)
        self.assertEqual(report.normalized_symbol, "EUR/USD")
        self.assertAlmostEqual(report.normalized_bid, 1.09951, places=4)
        self.assertAlmostEqual(report.normalized_ask, 1.10000, places=4)
        self.assertEqual(report.spread_pips, 4.96)

    def test_jpy_terms_pip_calculation(self):
        quote = RawFxQuote("USD/JPY", 150.00, 150.03, "IBKR")
        report = self.normalizer.normalize_quote(quote)
        
        self.assertEqual(report.normalized_symbol, "USD/JPY")
        self.assertEqual(report.pip_size, 0.01)
        self.assertEqual(report.spread_pips, 3.0)

if __name__ == '__main__':
    unittest.main()

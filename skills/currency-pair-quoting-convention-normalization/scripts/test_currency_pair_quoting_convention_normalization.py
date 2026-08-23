import logging
import unittest

from currency_pair_quoting_convention_normalization import (
    CLASSIFICATION_INVERTED,
    CLASSIFICATION_STANDARD,
    CLASSIFICATION_UNCLASSIFIED,
    CurrencyPairQuotingNormalizer,
    RawFxQuote,
)


def _quiet(func):
    """Silence the module's warnings for a single call."""
    logging.disable(logging.WARNING)
    try:
        return func()
    finally:
        logging.disable(logging.NOTSET)


class TestCurrencyPairQuotingNormalizer(unittest.TestCase):

    def setUp(self):
        self.normalizer = CurrencyPairQuotingNormalizer()

    def _normalize(self, symbol, bid, ask, vendor="VENDOR"):
        return _quiet(
            lambda: self.normalizer.normalize_quote(RawFxQuote(symbol, bid, ask, vendor))
        )

    # ------------------------------------------------------------------ #
    # Ranking and inversion
    # ------------------------------------------------------------------ #
    def test_standard_eur_usd_quote(self):
        quote = RawFxQuote("EUR/USD", 1.0990, 1.0992, "BLOOMBERG")
        report = self.normalizer.normalize_quote(quote)

        self.assertEqual(report.normalized_symbol, "EUR/USD")
        self.assertEqual(report.classification, CLASSIFICATION_STANDARD)
        self.assertFalse(report.is_inverted)
        self.assertEqual(report.normalized_bid, 1.0990)
        self.assertEqual(report.normalized_ask, 1.0992)
        self.assertEqual(report.pip_size, 0.0001)
        self.assertEqual(report.spread_pips, 2.0)
        self.assertFalse(report.is_crossed)

    def test_inverted_usd_eur_quote_conversion(self):
        # Inverted USD/EUR quote: Bid = 0.90909 (1/1.1000), Ask = 0.90950 (1/1.0995)
        # Should convert to EUR/USD:
        # Norm Bid = 1 / 0.90950 = 1.09951
        # Norm Ask = 1 / 0.90909 = 1.10000
        quote = RawFxQuote("USD/EUR", 0.90909, 0.90950, "REFINITIV")
        report = self.normalizer.normalize_quote(quote)

        self.assertTrue(report.is_inverted)
        self.assertEqual(report.classification, CLASSIFICATION_INVERTED)
        self.assertEqual(report.normalized_symbol, "EUR/USD")
        self.assertAlmostEqual(report.normalized_bid, 1.09951, places=4)
        self.assertAlmostEqual(report.normalized_ask, 1.10000, places=4)
        self.assertEqual(report.spread_pips, 4.96)

    def test_documented_worked_example_from_skill_md(self):
        # SKILL.md: USD/EUR 0.9090 / 0.9095 -> EUR/USD 1.099505 / 1.100110, 6.05 pips.
        # Expected values derived independently: 1/0.9095 and 1/0.9090.
        report = self._normalize("USD/EUR", 0.9090, 0.9095)
        self.assertEqual(report.normalized_symbol, "EUR/USD")
        self.assertAlmostEqual(report.normalized_bid, 1.099505, places=6)
        self.assertAlmostEqual(report.normalized_ask, 1.100110, places=6)
        self.assertEqual(report.spread_pips, 6.05)

    def test_cross_inversion_uses_opposite_side(self):
        """Bid_std = 1/Ask_inv, not 1/Bid_inv - the naive form narrows the spread."""
        report = self._normalize("USD/EUR", 0.9090, 0.9095)
        self.assertAlmostEqual(report.normalized_bid, 1.0 / 0.9095, places=9)
        self.assertAlmostEqual(report.normalized_ask, 1.0 / 0.9090, places=9)
        self.assertGreater(report.spread_price, 0.0)
        self.assertFalse(report.is_crossed)

    def test_jpy_terms_pip_calculation(self):
        quote = RawFxQuote("USD/JPY", 150.00, 150.03, "IBKR")
        report = self.normalizer.normalize_quote(quote)

        self.assertEqual(report.normalized_symbol, "USD/JPY")
        self.assertEqual(report.pip_size, 0.01)
        self.assertEqual(report.spread_pips, 3.0)

    def test_pip_size_follows_normalized_terms_currency_not_raw(self):
        """JPY/USD inverts to USD/JPY, so the pip must become 0.01, not stay 0.0001."""
        report = self._normalize("JPY/USD", 1.0 / 150.03, 1.0 / 150.00)

        self.assertEqual(report.normalized_symbol, "USD/JPY")
        self.assertTrue(report.is_inverted)
        self.assertAlmostEqual(report.normalized_bid, 150.00, places=6)
        self.assertAlmostEqual(report.normalized_ask, 150.03, places=6)
        self.assertEqual(report.pip_size, 0.01)
        self.assertAlmostEqual(report.spread_pips, 3.0, places=2)

    def test_report_spread_is_consistent_with_reported_prices(self):
        """Regression: prices were rounded to 5dp while spread_pips came from the
        unrounded values, so recomputing the spread from the report disagreed
        (4.9 vs the reported 4.96)."""
        report = self._normalize("USD/EUR", 0.90909, 0.90950)
        recomputed = round(
            (report.normalized_ask - report.normalized_bid) / report.pip_size, 2
        )
        self.assertEqual(recomputed, report.spread_pips)

    # ------------------------------------------------------------------ #
    # Unrankable pairs must never be inverted
    # ------------------------------------------------------------------ #
    def test_gold_quote_is_not_inverted(self):
        """Regression: XAU ranked 999 ('unknown' read as 'lowest'), so XAU/USD
        was flipped to USD/XAU and 2000.10 became 0.0005.

        XAU is an ISO 4217 code for one troy ounce of gold, and the LBMA Gold
        Price is set in US dollars per fine troy ounce - gold is the base.
        """
        report = self._normalize("XAU/USD", 2000.10, 2000.50)

        self.assertEqual(report.classification, CLASSIFICATION_UNCLASSIFIED)
        self.assertFalse(report.is_inverted)
        self.assertEqual(report.normalized_symbol, "XAU/USD")
        self.assertEqual(report.normalized_bid, 2000.10)
        self.assertEqual(report.normalized_ask, 2000.50)
        self.assertIsNone(report.pip_size)
        self.assertIsNone(report.spread_pips)
        self.assertAlmostEqual(report.spread_price, 0.40, places=6)

    def test_crypto_quote_is_not_inverted(self):
        """Regression: BTC/USD was flipped to USD/BTC at 0.0000167."""
        report = self._normalize("BTC/USD", 60000.0, 60010.0)

        self.assertEqual(report.classification, CLASSIFICATION_UNCLASSIFIED)
        self.assertEqual(report.normalized_symbol, "BTC/USD")
        self.assertEqual(report.normalized_bid, 60000.0)
        self.assertIsNone(report.spread_pips)

    def test_exotic_pair_is_flagged_not_silently_declared_standard(self):
        """Both legs unrankable: the caller must be able to tell this apart from
        a verified in-order pair."""
        report = self._normalize("MXN/ZAR", 1.05, 1.06)

        self.assertEqual(report.classification, CLASSIFICATION_UNCLASSIFIED)
        self.assertFalse(report.is_inverted)
        self.assertIsNone(report.pip_size)

    def test_unclassified_pair_logs_a_warning(self):
        with self.assertLogs(
            "currency_pair_quoting_convention_normalization", level="WARNING"
        ) as captured:
            self.normalizer.normalize_quote(RawFxQuote("XAU/USD", 2000.1, 2000.5, "V"))
        self.assertTrue(any("UNCLASSIFIED" in line for line in captured.output))

    def test_extending_priority_list_enables_ranking(self):
        """The documented escape hatch: rank ZAR below USD and ZAR/USD inverts."""
        normalizer = CurrencyPairQuotingNormalizer(
            priority_list=["EUR", "GBP", "AUD", "NZD", "USD", "CAD", "CHF", "JPY", "ZAR"]
        )
        report = _quiet(
            lambda: normalizer.normalize_quote(RawFxQuote("ZAR/USD", 0.054, 0.055, "V"))
        )
        self.assertEqual(report.classification, CLASSIFICATION_INVERTED)
        self.assertEqual(report.normalized_symbol, "USD/ZAR")
        self.assertAlmostEqual(report.normalized_bid, 1.0 / 0.055, places=9)

    def test_pip_size_override_applies_to_unclassified_pair(self):
        normalizer = CurrencyPairQuotingNormalizer(pip_size_overrides={"XAU/USD": 0.01})
        report = _quiet(
            lambda: normalizer.normalize_quote(RawFxQuote("XAU/USD", 2000.10, 2000.50, "V"))
        )
        self.assertEqual(report.pip_size, 0.01)
        self.assertAlmostEqual(report.spread_pips, 40.0, places=2)

    # ------------------------------------------------------------------ #
    # Price validation - the module must not emit NaN or negative quotes
    # ------------------------------------------------------------------ #
    def test_nan_bid_on_standard_pair_is_rejected(self):
        """Regression: only the inversion path validated prices, so a NaN bid on
        an already-standard pair produced a NaN spread that compares False
        against every downstream threshold."""
        with self.assertRaises(ValueError):
            self.normalizer.normalize_quote(
                RawFxQuote("EUR/USD", float("nan"), 1.1000, "V")
            )

    def test_infinite_ask_is_rejected(self):
        with self.assertRaises(ValueError):
            self.normalizer.normalize_quote(RawFxQuote("EUR/USD", 1.10, float("inf"), "V"))

    def test_negative_price_on_standard_pair_is_rejected(self):
        """Regression: negative prices flowed straight through the standard path."""
        with self.assertRaises(ValueError):
            self.normalizer.normalize_quote(RawFxQuote("EUR/USD", -1.10, -1.09, "V"))

    def test_zero_price_is_rejected(self):
        with self.assertRaises(ValueError):
            self.normalizer.normalize_quote(RawFxQuote("USD/EUR", 0.0, 0.9095, "V"))

    def test_non_numeric_price_is_rejected(self):
        with self.assertRaises(TypeError):
            self.normalizer.normalize_quote(RawFxQuote("EUR/USD", "1.10", 1.11, "V"))

    def test_crossed_market_is_flagged_not_silently_negative(self):
        report = self._normalize("EUR/USD", 1.1002, 1.1000)
        self.assertTrue(report.is_crossed)
        self.assertLess(report.spread_pips, 0.0)

    def test_inversion_preserves_crossing(self):
        report = self._normalize("USD/EUR", 0.9095, 0.9090)
        self.assertTrue(report.is_inverted)
        self.assertTrue(report.is_crossed)

    # ------------------------------------------------------------------ #
    # Symbol parsing
    # ------------------------------------------------------------------ #
    def test_accepts_common_separators_and_bare_form(self):
        for symbol in ("EUR/USD", "EUR_USD", "EUR-USD", "EUR.USD", "EUR:USD",
                       "EUR USD", "EURUSD", "eurusd", " EUR/USD "):
            with self.subTest(symbol=symbol):
                self.assertEqual(self.normalizer.parse_symbol(symbol), ("EUR", "USD"))

    def test_four_letter_leg_is_rejected_with_a_scope_message(self):
        with self.assertRaises(ValueError) as ctx:
            self.normalizer.parse_symbol("USDT/EUR")
        self.assertIn("out of scope", str(ctx.exception))

    def test_non_alphabetic_symbol_is_rejected(self):
        """Regression: '123456' parsed into '123'/'456' and was reported as a
        successfully normalized pair."""
        with self.assertRaises(ValueError):
            self.normalizer.parse_symbol("123456")

    def test_same_currency_on_both_legs_is_rejected(self):
        with self.assertRaises(ValueError):
            self.normalizer.parse_symbol("EUR/EUR")

    def test_non_string_symbol_is_rejected(self):
        with self.assertRaises(TypeError):
            self.normalizer.parse_symbol(None)

    def test_unrecognised_three_letter_codes_are_unclassified_not_rejected(self):
        """The module has no ISO 4217 register, so it cannot tell an exotic
        currency from a typo. It declines to transform either."""
        report = self._normalize("ABC/DEF", 5.0, 6.0)
        self.assertEqual(report.classification, CLASSIFICATION_UNCLASSIFIED)
        self.assertEqual(report.normalized_bid, 5.0)

    # ------------------------------------------------------------------ #
    # Configuration validation
    # ------------------------------------------------------------------ #
    def test_duplicate_priority_entry_is_rejected(self):
        with self.assertRaises(ValueError):
            CurrencyPairQuotingNormalizer(priority_list=["EUR", "USD", "EUR"])

    def test_empty_priority_list_is_rejected(self):
        with self.assertRaises(ValueError):
            CurrencyPairQuotingNormalizer(priority_list=[])

    def test_malformed_priority_code_is_rejected(self):
        with self.assertRaises(ValueError):
            CurrencyPairQuotingNormalizer(priority_list=["EUR", "DOLLAR"])

    def test_priority_list_is_case_insensitive(self):
        normalizer = CurrencyPairQuotingNormalizer(priority_list=["eur", "usd"])
        report = _quiet(
            lambda: normalizer.normalize_quote(RawFxQuote("USD/EUR", 0.9090, 0.9095, "V"))
        )
        self.assertEqual(report.normalized_symbol, "EUR/USD")

    def test_invalid_pip_override_is_rejected(self):
        with self.assertRaises(ValueError):
            CurrencyPairQuotingNormalizer(pip_size_overrides={"XAU/USD": 0.0})
        with self.assertRaises(ValueError):
            CurrencyPairQuotingNormalizer(pip_size_overrides={"XAUUSD": 0.01})

    def test_custom_two_decimal_terms_currency(self):
        normalizer = CurrencyPairQuotingNormalizer(
            priority_list=["USD", "HUF"], two_decimal_terms_currencies=["JPY", "HUF"]
        )
        report = normalizer.normalize_quote(RawFxQuote("USD/HUF", 350.00, 350.05, "V"))
        self.assertEqual(report.pip_size, 0.01)
        self.assertAlmostEqual(report.spread_pips, 5.0, places=2)


if __name__ == "__main__":
    unittest.main()

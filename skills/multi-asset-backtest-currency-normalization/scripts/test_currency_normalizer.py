"""
Unit tests for multi-asset-backtest-currency-normalization.

Expected values are derived by hand from the rate convention documented in
``currency_normalizer`` (rate = units of ``to`` per one unit of ``from``), not by
re-running the implementation's own arithmetic.

Coverage:
1. Cash/position translation and NAV aggregation, including the SKILL.md verification
   scenario and a non-USD reporting currency.
2. Unit tagging of the NAV snapshot (local vs reporting) and per-symbol aggregation.
3. FX rate table integrity: derived inverses, conflicting quotes, non-finite rates.
4. Input validation: currency codes, dates, non-finite amounts.
5. Point-in-time staleness fallback, including the look-ahead guard.
6. Exact NAV-change attribution into local, FX-translation and interaction effects.
"""
import datetime
import unittest

from currency_normalizer import (
    CurrencyMismatchError,
    MultiCurrencyNAV,
    MultiCurrencyPortfolioNormalizer,
    PositionValuation,
    normalize_currency_code,
)


class TestNAVComputation(unittest.TestCase):
    def setUp(self):
        self.norm = MultiCurrencyPortfolioNormalizer(reporting_currency="USD")
        self.eval_date = datetime.date(2026, 7, 24)
        # EUR/USD = 1.10 -> 1 EUR = 1.10 USD
        self.norm.register_fx_rate("EUR", "USD", self.eval_date, 1.10)
        # USD/JPY = 150.0 -> 1 USD = 150 JPY
        self.norm.register_fx_rate("USD", "JPY", self.eval_date, 150.0)

    def test_cash_and_position_conversion_to_usd(self):
        self.norm.set_cash_balance("USD", 50000.0)
        self.norm.set_cash_balance("EUR", 10000.0)  # 10,000 * 1.10 = 11,000 USD
        self.norm.add_position(
            PositionValuation("SAP", "EUR", 100, 150.0)  # 15,000 EUR -> 16,500 USD
        )

        nav = self.norm.compute_total_nav(self.eval_date)

        self.assertAlmostEqual(nav.total_cash_reporting, 61000.0, delta=0.01)
        self.assertAlmostEqual(nav.total_positions_reporting, 16500.0, delta=0.01)
        self.assertAlmostEqual(nav.total_nav_reporting, 77500.0, delta=0.01)

    def test_skill_md_verification_scenario(self):
        """50,000 USD + 30,000 EUR @1.10 + 1,000,000 JPY @ USD/JPY 150."""
        self.norm.set_cash_balance("USD", 50000.0)
        self.norm.set_cash_balance("EUR", 30000.0)
        self.norm.set_cash_balance("JPY", 1000000.0)

        nav = self.norm.compute_total_nav(self.eval_date)

        # 50,000 + 33,000 + 1,000,000/150 = 89,666.666...
        self.assertAlmostEqual(nav.total_nav_reporting, 50000.0 + 33000.0 + 20000.0 / 3.0, places=6)
        self.assertAlmostEqual(nav.cash_reporting_by_currency["JPY"], 6666.666666, places=4)

    def test_non_usd_reporting_currency(self):
        jpy_book = MultiCurrencyPortfolioNormalizer(reporting_currency="JPY")
        jpy_book.register_fx_rate("USD", "JPY", self.eval_date, 150.0)
        jpy_book.set_cash_balance("USD", 10000.0)

        nav = jpy_book.compute_total_nav(self.eval_date)

        self.assertEqual(nav.reporting_currency, "JPY")
        self.assertAlmostEqual(nav.total_nav_reporting, 1500000.0, delta=0.01)

    def test_reporting_currency_needs_no_registered_rate(self):
        book = MultiCurrencyPortfolioNormalizer(reporting_currency="USD")
        book.set_cash_balance("USD", 1234.5)
        nav = book.compute_total_nav(self.eval_date)
        self.assertAlmostEqual(nav.total_nav_reporting, 1234.5, delta=0.01)
        self.assertEqual(nav.fx_rates_used["USD"], 1.0)

    def test_empty_portfolio_is_zero_not_an_error(self):
        nav = self.norm.compute_total_nav(self.eval_date)
        self.assertEqual(nav.total_nav_reporting, 0.0)
        self.assertEqual(nav.cash_local_by_currency, {})

    def test_negative_cash_and_short_position_are_legitimate(self):
        # A EUR margin loan against a short EUR position.
        self.norm.set_cash_balance("EUR", -5000.0)
        self.norm.add_position(PositionValuation("SAP", "EUR", -10, 150.0))

        nav = self.norm.compute_total_nav(self.eval_date)

        # (-5,000 + -1,500) EUR * 1.10 = -7,150 USD
        self.assertAlmostEqual(nav.total_nav_reporting, -7150.0, delta=0.01)

    def test_missing_rate_for_held_currency_raises_rather_than_omitting_it(self):
        self.norm.set_cash_balance("GBP", 1000.0)
        with self.assertRaises(CurrencyMismatchError):
            self.norm.compute_total_nav(self.eval_date)

    def test_set_cash_balance_replaces_rather_than_accumulates(self):
        self.norm.set_cash_balance("USD", 100.0)
        self.norm.set_cash_balance("USD", 250.0)
        self.assertEqual(self.norm.cash_balances["USD"], 250.0)


class TestNAVSnapshotUnitTagging(unittest.TestCase):
    """Regression: the snapshot used to mix local-unit cash with reporting-unit
    positions under near-identical field names, and key positions by symbol under a
    field named ``positions_by_currency``."""

    def setUp(self):
        self.date = datetime.date(2026, 7, 24)
        self.norm = MultiCurrencyPortfolioNormalizer("USD")
        self.norm.register_fx_rate("EUR", "USD", self.date, 1.10)
        self.norm.set_cash_balance("EUR", 10000.0)
        self.norm.add_position(PositionValuation("SAP", "EUR", 100, 150.0))

    def test_local_and_reporting_breakdowns_are_separate(self):
        nav = self.norm.compute_total_nav(self.date)

        self.assertAlmostEqual(nav.cash_local_by_currency["EUR"], 10000.0, delta=0.01)
        self.assertAlmostEqual(nav.cash_reporting_by_currency["EUR"], 11000.0, delta=0.01)
        self.assertAlmostEqual(nav.positions_local_by_currency["EUR"], 15000.0, delta=0.01)
        self.assertAlmostEqual(nav.positions_reporting_by_currency["EUR"], 16500.0, delta=0.01)

    def test_reporting_breakdowns_sum_to_the_reported_totals(self):
        nav = self.norm.compute_total_nav(self.date)

        self.assertAlmostEqual(
            sum(nav.cash_reporting_by_currency.values()), nav.total_cash_reporting, delta=1e-9
        )
        self.assertAlmostEqual(
            sum(nav.positions_reporting_by_currency.values()),
            nav.total_positions_reporting,
            delta=1e-9,
        )
        self.assertAlmostEqual(
            sum(nav.positions_reporting_by_symbol.values()),
            nav.total_positions_reporting,
            delta=1e-9,
        )

    def test_duplicate_symbol_lots_aggregate_instead_of_overwriting(self):
        self.norm.add_position(PositionValuation("SAP", "EUR", 50, 150.0))

        nav = self.norm.compute_total_nav(self.date)

        # (100 + 50) * 150 EUR = 22,500 EUR -> 24,750 USD, in one symbol bucket.
        self.assertAlmostEqual(nav.total_positions_reporting, 24750.0, delta=0.01)
        self.assertAlmostEqual(nav.positions_reporting_by_symbol["SAP"], 24750.0, delta=0.01)

    def test_local_value_by_currency_combines_cash_and_positions(self):
        nav = self.norm.compute_total_nav(self.date)
        self.assertAlmostEqual(nav.local_value_by_currency["EUR"], 25000.0, delta=0.01)

    def test_fx_rates_used_records_the_applied_rate_and_its_date(self):
        nav = self.norm.compute_total_nav(self.date)
        self.assertAlmostEqual(nav.fx_rates_used["EUR"], 1.10, delta=1e-12)
        self.assertEqual(nav.fx_rate_dates_used["EUR"], self.date)
        self.assertEqual(nav.stale_fx_currencies, {})


class TestFXRateTableIntegrity(unittest.TestCase):
    def setUp(self):
        self.date = datetime.date(2026, 7, 24)
        self.norm = MultiCurrencyPortfolioNormalizer("USD")

    def test_inverse_is_derived_without_being_stored(self):
        self.norm.register_fx_rate("USD", "JPY", self.date, 150.0)
        self.assertAlmostEqual(
            self.norm.convert_amount(1500000.0, "JPY", "USD", self.date), 10000.0, delta=1e-9
        )

    def test_explicit_opposite_quote_does_not_corrupt_the_direct_quote(self):
        """Regression: auto-registering the reciprocal let a later opposite-direction
        quote silently rewrite an already-registered rate (1.10 -> 1.1111)."""
        self.norm.register_fx_rate("EUR", "USD", self.date, 1.10)
        self.norm.register_fx_rate("USD", "EUR", self.date, 0.90)

        self.assertAlmostEqual(self.norm.get_fx_rate("EUR", "USD", self.date), 1.10, delta=1e-12)
        self.assertAlmostEqual(self.norm.get_fx_rate("USD", "EUR", self.date), 0.90, delta=1e-12)

    def test_conflicting_same_direction_rate_raises(self):
        self.norm.register_fx_rate("EUR", "USD", self.date, 1.10)
        with self.assertRaises(CurrencyMismatchError):
            self.norm.register_fx_rate("EUR", "USD", self.date, 1.15)

    def test_identical_reregistration_is_a_no_op(self):
        self.norm.register_fx_rate("EUR", "USD", self.date, 1.10)
        self.norm.register_fx_rate("EUR", "USD", self.date, 1.10)
        self.assertAlmostEqual(self.norm.get_fx_rate("EUR", "USD", self.date), 1.10, delta=1e-12)

    def test_allow_overwrite_replaces_deliberately(self):
        self.norm.register_fx_rate("EUR", "USD", self.date, 1.10)
        self.norm.register_fx_rate("EUR", "USD", self.date, 1.15, allow_overwrite=True)
        self.assertAlmostEqual(self.norm.get_fx_rate("EUR", "USD", self.date), 1.15, delta=1e-12)

    def test_nan_rate_is_rejected(self):
        """Regression: ``float('nan') <= 0`` is False, so a NaN rate passed the old
        positivity check and produced a NaN NAV."""
        with self.assertRaises(CurrencyMismatchError):
            self.norm.register_fx_rate("EUR", "USD", self.date, float("nan"))

    def test_infinite_rate_is_rejected(self):
        with self.assertRaises(CurrencyMismatchError):
            self.norm.register_fx_rate("EUR", "USD", self.date, float("inf"))

    def test_zero_and_negative_rates_are_rejected(self):
        for bad in (0.0, -1.10):
            with self.subTest(rate=bad):
                with self.assertRaises(CurrencyMismatchError):
                    self.norm.register_fx_rate("EUR", "USD", self.date, bad)

    def test_identity_rate_is_one(self):
        self.assertEqual(self.norm.get_fx_rate("USD", "USD", self.date), 1.0)

    def test_self_pair_registered_at_a_non_unit_rate_is_rejected(self):
        with self.assertRaises(CurrencyMismatchError):
            self.norm.register_fx_rate("USD", "USD", self.date, 1.05)

    def test_missing_fx_rate_raises_error(self):
        self.norm.register_fx_rate("EUR", "USD", self.date, 1.10)
        with self.assertRaises(CurrencyMismatchError):
            self.norm.get_fx_rate("EUR", "USD", datetime.date(2020, 1, 1))

    def test_no_triangulation_through_a_third_currency(self):
        self.norm.register_fx_rate("EUR", "USD", self.date, 1.10)
        self.norm.register_fx_rate("USD", "JPY", self.date, 150.0)
        with self.assertRaises(CurrencyMismatchError):
            self.norm.get_fx_rate("EUR", "JPY", self.date)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.date = datetime.date(2026, 7, 24)
        self.norm = MultiCurrencyPortfolioNormalizer("USD")

    def test_currency_codes_are_stripped_and_upper_cased(self):
        self.assertEqual(normalize_currency_code(" eur "), "EUR")

    def test_whitespace_variant_does_not_create_a_second_ledger(self):
        """Regression: ``"USD "`` used to become a distinct cash-balance key."""
        self.norm.set_cash_balance("USD ", 1000.0)
        self.norm.set_cash_balance("usd", 500.0)
        self.assertEqual(self.norm.cash_balances, {"USD": 500.0})

    def test_malformed_currency_codes_are_rejected(self):
        for bad in ("", "US", "EURO", "U$D", "  ", 840, None):
            with self.subTest(code=bad):
                with self.assertRaises(CurrencyMismatchError):
                    normalize_currency_code(bad)

    def test_datetime_is_rejected_instead_of_silently_missing_the_date_key(self):
        """Regression: ``datetime.datetime`` is a ``date`` subclass but hashes
        differently, so a rate stored under a timestamp was invisible to a date
        lookup."""
        stamp = datetime.datetime(2026, 7, 24, 16, 0)
        with self.assertRaises(CurrencyMismatchError):
            self.norm.register_fx_rate("EUR", "USD", stamp, 1.10)
        self.norm.register_fx_rate("EUR", "USD", self.date, 1.10)
        with self.assertRaises(CurrencyMismatchError):
            self.norm.get_fx_rate("EUR", "USD", stamp)
        with self.assertRaises(CurrencyMismatchError):
            self.norm.compute_total_nav(stamp)

    def test_non_finite_cash_balance_is_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(amount=bad):
                with self.assertRaises(CurrencyMismatchError):
                    self.norm.set_cash_balance("USD", bad)

    def test_non_finite_position_inputs_are_rejected(self):
        with self.assertRaises(CurrencyMismatchError):
            PositionValuation("SAP", "EUR", float("nan"), 150.0)
        with self.assertRaises(CurrencyMismatchError):
            PositionValuation("SAP", "EUR", 100, float("inf"))

    def test_empty_symbol_is_rejected(self):
        with self.assertRaises(CurrencyMismatchError):
            PositionValuation("   ", "EUR", 100, 150.0)

    def test_non_finite_convert_amount_is_rejected(self):
        self.norm.register_fx_rate("EUR", "USD", self.date, 1.10)
        with self.assertRaises(CurrencyMismatchError):
            self.norm.convert_amount(float("nan"), "EUR", "USD", self.date)

    def test_add_position_rejects_wrong_type(self):
        with self.assertRaises(CurrencyMismatchError):
            self.norm.add_position({"symbol": "SAP"})

    def test_invalid_staleness_configuration_is_rejected(self):
        for bad in (-1, True, 1.5):
            with self.subTest(value=bad):
                with self.assertRaises(CurrencyMismatchError):
                    MultiCurrencyPortfolioNormalizer("USD", max_staleness_days=bad)


class TestStalenessFallback(unittest.TestCase):
    """Global books span mismatched calendars (the ECB publishes euro reference rates
    only on TARGET working days), so an opt-in backward-only fallback is needed."""

    def setUp(self):
        self.friday = datetime.date(2026, 7, 24)
        self.monday = datetime.date(2026, 7, 27)

    def test_exact_match_is_required_by_default(self):
        norm = MultiCurrencyPortfolioNormalizer("USD")
        norm.register_fx_rate("EUR", "USD", self.friday, 1.10)
        with self.assertRaises(CurrencyMismatchError):
            norm.get_fx_rate("EUR", "USD", self.monday)

    def test_backward_fallback_within_window(self):
        norm = MultiCurrencyPortfolioNormalizer("USD", max_staleness_days=3)
        norm.register_fx_rate("EUR", "USD", self.friday, 1.10)
        self.assertAlmostEqual(norm.get_fx_rate("EUR", "USD", self.monday), 1.10, delta=1e-12)

    def test_fallback_beyond_window_raises(self):
        norm = MultiCurrencyPortfolioNormalizer("USD", max_staleness_days=1)
        norm.register_fx_rate("EUR", "USD", self.friday, 1.10)
        with self.assertRaises(CurrencyMismatchError):
            norm.get_fx_rate("EUR", "USD", self.monday)

    def test_a_future_rate_is_never_used(self):
        """Look-ahead guard: the fallback searches strictly backwards."""
        norm = MultiCurrencyPortfolioNormalizer("USD", max_staleness_days=30)
        norm.register_fx_rate("EUR", "USD", self.monday, 1.20)
        with self.assertRaises(CurrencyMismatchError):
            norm.get_fx_rate("EUR", "USD", self.friday)

    def test_exact_dated_inverse_beats_a_stale_direct_quote(self):
        """Freshness outranks direction: a same-day derived inverse must win over a
        stale direct quote, or a Monday NAV silently marks EUR at Friday's rate."""
        norm = MultiCurrencyPortfolioNormalizer("USD", max_staleness_days=5)
        norm.register_fx_rate("EUR", "USD", self.friday, 1.10)
        norm.register_fx_rate("USD", "EUR", self.monday, 0.80)  # implies EUR->USD 1.25

        self.assertAlmostEqual(norm.get_fx_rate("EUR", "USD", self.monday), 1.25, delta=1e-12)

    def test_most_recent_prior_rate_wins(self):
        norm = MultiCurrencyPortfolioNormalizer("USD", max_staleness_days=30)
        norm.register_fx_rate("EUR", "USD", datetime.date(2026, 7, 20), 1.05)
        norm.register_fx_rate("EUR", "USD", self.friday, 1.10)
        self.assertAlmostEqual(norm.get_fx_rate("EUR", "USD", self.monday), 1.10, delta=1e-12)

    def test_nav_records_stale_rate_provenance(self):
        norm = MultiCurrencyPortfolioNormalizer("USD", max_staleness_days=3)
        norm.register_fx_rate("EUR", "USD", self.friday, 1.10)
        norm.set_cash_balance("EUR", 10000.0)

        nav = norm.compute_total_nav(self.monday)

        self.assertAlmostEqual(nav.total_nav_reporting, 11000.0, delta=0.01)
        self.assertEqual(nav.fx_rate_dates_used["EUR"], self.friday)
        self.assertEqual(nav.stale_fx_currencies, {"EUR": 3})


class TestNAVChangeAttribution(unittest.TestCase):
    def setUp(self):
        self.t0 = datetime.date(2026, 1, 5)
        self.t1 = datetime.date(2026, 2, 5)
        self.norm = MultiCurrencyPortfolioNormalizer("USD")

    def _snapshot(self, date, eur_rate, eur_cash):
        self.norm.register_fx_rate("EUR", "USD", date, eur_rate)
        self.norm.set_cash_balance("EUR", eur_cash)
        return self.norm.compute_total_nav(date)

    def test_three_way_decomposition_matches_hand_calculation(self):
        opening = self._snapshot(self.t0, 1.10, 10000.0)   # 11,000 USD
        closing = self._snapshot(self.t1, 1.20, 12000.0)   # 14,400 USD

        attr = self.norm.attribute_nav_change(opening, closing)

        self.assertAlmostEqual(attr.total_nav_change, 3400.0, delta=1e-9)
        self.assertAlmostEqual(attr.local_effect, 2200.0, delta=1e-9)          # 2,000 * 1.10
        self.assertAlmostEqual(attr.fx_translation_effect, 1000.0, delta=1e-9)  # 10,000 * 0.10
        self.assertAlmostEqual(attr.interaction_effect, 200.0, delta=1e-9)      # 2,000 * 0.10

    def test_components_reconstruct_the_total_exactly(self):
        opening = self._snapshot(self.t0, 1.10, 10000.0)
        closing = self._snapshot(self.t1, 1.20, 12000.0)

        attr = self.norm.attribute_nav_change(opening, closing)

        self.assertAlmostEqual(
            attr.local_effect + attr.fx_translation_effect + attr.interaction_effect,
            attr.total_nav_change,
            delta=1e-9,
        )
        eur = attr.by_currency["EUR"]
        self.assertAlmostEqual(eur.total_effect, attr.total_nav_change, delta=1e-9)

    def test_pure_fx_move_produces_no_local_effect(self):
        """JPY 1,500,000 held flat while USD/JPY goes 150 -> 160."""
        norm = MultiCurrencyPortfolioNormalizer("USD")
        norm.set_cash_balance("JPY", 1500000.0)
        norm.register_fx_rate("USD", "JPY", self.t0, 150.0)
        norm.register_fx_rate("USD", "JPY", self.t1, 160.0)

        opening = norm.compute_total_nav(self.t0)   # 10,000.00 USD
        closing = norm.compute_total_nav(self.t1)   # 9,375.00 USD
        attr = norm.attribute_nav_change(opening, closing)

        self.assertAlmostEqual(attr.total_nav_change, -625.0, delta=1e-9)
        self.assertAlmostEqual(attr.local_effect, 0.0, delta=1e-9)
        self.assertAlmostEqual(attr.fx_translation_effect, -625.0, delta=1e-9)
        self.assertAlmostEqual(attr.interaction_effect, 0.0, delta=1e-9)

    def test_currency_entering_only_in_the_closing_snapshot(self):
        norm = MultiCurrencyPortfolioNormalizer("USD")
        norm.register_fx_rate("EUR", "USD", self.t0, 1.10)
        norm.register_fx_rate("EUR", "USD", self.t1, 1.20)

        opening = norm.compute_total_nav(self.t0)          # empty book, NAV 0
        norm.set_cash_balance("EUR", 10000.0)
        closing = norm.compute_total_nav(self.t1)          # 12,000 USD

        attr = norm.attribute_nav_change(opening, closing)

        # V0 = 0, so the FX effect on the opening balance is zero; the position is
        # attributed at the opening rate plus the interaction of the rate move.
        self.assertAlmostEqual(attr.local_effect, 11000.0, delta=1e-9)
        self.assertAlmostEqual(attr.fx_translation_effect, 0.0, delta=1e-9)
        self.assertAlmostEqual(attr.interaction_effect, 1000.0, delta=1e-9)
        self.assertAlmostEqual(attr.total_nav_change, 12000.0, delta=1e-9)

    def test_missing_opening_rate_for_a_new_currency_raises(self):
        norm = MultiCurrencyPortfolioNormalizer("USD")
        opening = norm.compute_total_nav(self.t0)
        norm.register_fx_rate("EUR", "USD", self.t1, 1.20)
        norm.set_cash_balance("EUR", 10000.0)
        closing = norm.compute_total_nav(self.t1)

        with self.assertRaises(CurrencyMismatchError):
            norm.attribute_nav_change(opening, closing)

    def test_reversed_or_equal_dates_are_rejected(self):
        opening = self._snapshot(self.t0, 1.10, 10000.0)
        closing = self._snapshot(self.t1, 1.20, 12000.0)

        with self.assertRaises(CurrencyMismatchError):
            self.norm.attribute_nav_change(closing, opening)
        with self.assertRaises(CurrencyMismatchError):
            self.norm.attribute_nav_change(opening, opening)

    def test_mismatched_reporting_currencies_are_rejected(self):
        opening = self._snapshot(self.t0, 1.10, 10000.0)
        foreign = MultiCurrencyNAV(
            reporting_currency="EUR",
            date=self.t1,
            total_cash_reporting=0.0,
            total_positions_reporting=0.0,
            total_nav_reporting=0.0,
            cash_local_by_currency={},
            cash_reporting_by_currency={},
            positions_local_by_currency={},
            positions_reporting_by_currency={},
            positions_reporting_by_symbol={},
            fx_rates_used={},
        )
        with self.assertRaises(CurrencyMismatchError):
            self.norm.attribute_nav_change(opening, foreign)


if __name__ == "__main__":
    unittest.main()

"""Unit tests for the exchange tick size regime engine.

Expected tick values are taken from the published regimes, not from the engine's own
tables: SEC Rule 612 (17 CFR 242.612), the MiFID II RTS 11 Annex (Commission Delegated
Regulation (EU) 2017/588) and DFM Circular 02/2026 (effective 2026-04-06).
"""
import logging
import unittest
from decimal import Decimal

from exchange_tick_size_regime_tracking import (
    ExchangeTickSizeRegimeEngine,
    LiquidityBandRequiredError,
    OrderSide,
    PriceBandTickRule,
    TickRegimeError,
    TickRoundingPolicy,
    UnknownVenueError,
    VenueTickRegime,
)

logging.disable(logging.CRITICAL)


class TestUSRule612(unittest.TestCase):
    def setUp(self):
        self.engine = ExchangeTickSizeRegimeEngine()

    def test_penny_and_sub_penny_bands(self):
        self.assertEqual(self.engine.get_active_tick_size("US_EQUITIES", 150.00), 0.01)
        self.assertEqual(self.engine.get_active_tick_size("US_EQUITIES", 0.50), 0.0001)

    def test_one_dollar_boundary_is_inclusive_of_the_penny_band(self):
        # Rule 612(b)(1): "equal to or greater than $1.00" takes the $0.01 increment.
        self.assertEqual(self.engine.get_active_tick_size_decimal("US_EQUITIES", "1.00"), Decimal("0.01"))
        self.assertEqual(self.engine.get_active_tick_size_decimal("US_EQUITIES", "0.9999"), Decimal("0.0001"))

    def test_tick_constrained_half_penny_is_opt_in_only(self):
        # The amended $0.005 increment is assigned per symbol and is not yet operative,
        # so it must never appear unless the caller asks for it.
        self.assertEqual(self.engine.get_active_tick_size_decimal("US_EQUITIES", "25.00"), Decimal("0.01"))
        self.assertEqual(
            self.engine.get_active_tick_size_decimal("US_EQUITIES", "25.00", tick_constrained=True),
            Decimal("0.005"),
        )

    def test_tick_constrained_does_not_reach_below_one_dollar(self):
        self.assertEqual(
            self.engine.get_active_tick_size_decimal("US_EQUITIES", "0.75", tick_constrained=True),
            Decimal("0.0001"),
        )

    def test_tick_constrained_rejected_on_non_us_venues(self):
        with self.assertRaises(TickRegimeError):
            self.engine.get_active_tick_size("DFM_DUBAI", 25.00, tick_constrained=True)


class TestRTS11LiquidityBands(unittest.TestCase):
    def setUp(self):
        self.engine = ExchangeTickSizeRegimeEngine()

    def test_price_alone_is_not_enough(self):
        # RTS 11 is two-dimensional; a price-only lookup silently invents a tick.
        with self.assertRaises(LiquidityBandRequiredError):
            self.engine.get_active_tick_size("EU_XETRA", 25.00)

    def test_annex_cells_across_bands(self):
        cases = [
            (25.00, 1, "0.2"),     # 20 <= P < 50, ADNT < 10
            (25.00, 6, "0.005"),   # 20 <= P < 50, ADNT >= 9000
            (0.05, 3, "0.0001"),   # 0 <= P < 0.1, 80 <= ADNT < 600
            (1.50, 4, "0.001"),    # 1 <= P < 2, 600 <= ADNT < 2000
            (150.00, 2, "0.5"),    # 100 <= P < 200, 10 <= ADNT < 80
            (10500.00, 6, "2"),    # 10000 <= P < 20000, ADNT >= 9000
            (60000.00, 1, "500"),  # P >= 50000, ADNT < 10
        ]
        for price, band, expected in cases:
            with self.subTest(price=price, band=band):
                self.assertEqual(
                    self.engine.get_active_tick_size_decimal("EU_RTS11", price, liquidity_band=band),
                    Decimal(expected),
                )

    def test_price_only_table_would_have_been_forty_times_too_fine(self):
        # Regression: a naive engine returned EUR 0.005 for any EUR 10-50 order.
        # An illiquid (band 1) name at EUR 25 must be quoted in EUR 0.2 steps.
        self.assertEqual(
            self.engine.get_active_tick_size_decimal("EU_RTS11", "25.00", liquidity_band=1),
            Decimal("0.2"),
        )

    def test_xetra_alias_resolves_to_the_rts11_regime(self):
        self.assertEqual(
            self.engine.get_active_tick_size_decimal("EU_XETRA", "25.00", liquidity_band=6),
            self.engine.get_active_tick_size_decimal("EU_RTS11", "25.00", liquidity_band=6),
        )

    def test_invalid_band_rejected(self):
        for band in (0, 7, -1, "6", 1.0, True):
            with self.subTest(band=band):
                with self.assertRaises(TickRegimeError):
                    self.engine.get_active_tick_size("EU_RTS11", 25.00, liquidity_band=band)

    def test_band_rejected_on_band_independent_venue(self):
        with self.assertRaises(TickRegimeError):
            self.engine.get_active_tick_size("US_EQUITIES", 25.00, liquidity_band=6)

    def test_adnt_maps_to_band_at_the_published_thresholds(self):
        cases = [(0, 1), (9.99, 1), (10, 2), (79, 2), (80, 3), (599, 3),
                 (600, 4), (1999, 4), (2000, 5), (8999, 5), (9000, 6), (10 ** 9, 6)]
        for adnt, expected in cases:
            with self.subTest(adnt=adnt):
                self.assertEqual(ExchangeTickSizeRegimeEngine.liquidity_band_for_adnt(adnt), expected)

    def test_negative_adnt_rejected(self):
        with self.assertRaises(TickRegimeError):
            ExchangeTickSizeRegimeEngine.liquidity_band_for_adnt(-1)


class TestDFMCircular022026(unittest.TestCase):
    def setUp(self):
        self.engine = ExchangeTickSizeRegimeEngine()

    def test_all_five_bands(self):
        cases = [("0.500", "0.001"), ("1.00", "0.01"), ("9.99", "0.01"), ("10.00", "0.02"),
                 ("49.98", "0.02"), ("50.00", "0.05"), ("99.95", "0.05"), ("100.00", "0.10"),
                 ("250.00", "0.10")]
        for price, expected in cases:
            with self.subTest(price=price):
                self.assertEqual(
                    self.engine.get_active_tick_size_decimal("DFM_DUBAI", price), Decimal(expected)
                )

    def test_high_price_band_regression(self):
        # Regression: an earlier table stopped at AED 50 and returned 0.05 above it,
        # so AED 150.05 was reported compliant. Under Circular 02/2026 the tick is 0.10
        # and 150.05 is off-tick.
        report = self.engine.audit_order_tick_compliance("DFM_DUBAI", "EMAAR", "150.05")
        self.assertEqual(report.active_tick_size_decimal, Decimal("0.10"))
        self.assertFalse(report.is_on_tick)
        self.assertEqual(report.aligned_price_decimal, Decimal("150.10"))


class TestPriceAlignment(unittest.TestCase):
    def setUp(self):
        self.engine = ExchangeTickSizeRegimeEngine()

    def test_nearest_rounds_half_up(self):
        # 150.005 / 0.01 = 15000.5 -> 15001 steps -> 150.01
        self.assertEqual(self.engine.align_price_to_tick_decimal("150.005", "0.01"), Decimal("150.01"))
        self.assertEqual(self.engine.align_price_to_tick("150.005", "0.01"), 150.01)

    def test_on_tick_price_is_unchanged(self):
        self.assertEqual(self.engine.align_price_to_tick_decimal("150.01", "0.01"), Decimal("150.01"))

    def test_passive_rounding_never_worsens_the_limit(self):
        self.assertEqual(
            self.engine.align_price_to_tick_decimal("150.005", "0.01", side="BUY", policy="PASSIVE"),
            Decimal("150.00"),
        )
        self.assertEqual(
            self.engine.align_price_to_tick_decimal("150.005", "0.01", side=OrderSide.SELL, policy=TickRoundingPolicy.PASSIVE),
            Decimal("150.01"),
        )

    def test_aggressive_rounding_moves_toward_the_market(self):
        self.assertEqual(
            self.engine.align_price_to_tick_decimal("150.001", "0.01", side="BUY", policy="AGGRESSIVE"),
            Decimal("150.01"),
        )
        self.assertEqual(
            self.engine.align_price_to_tick_decimal("150.009", "0.01", side="SELL", policy="AGGRESSIVE"),
            Decimal("150.00"),
        )

    def test_directional_policies_require_a_side(self):
        for policy in ("PASSIVE", "AGGRESSIVE"):
            with self.subTest(policy=policy):
                with self.assertRaises(TickRegimeError):
                    self.engine.align_price_to_tick("150.005", "0.01", policy=policy)

    def test_unknown_policy_or_side_rejected(self):
        with self.assertRaises(TickRegimeError):
            self.engine.align_price_to_tick("150.005", "0.01", policy="FLOOR")
        with self.assertRaises(TickRegimeError):
            self.engine.align_price_to_tick("150.005", "0.01", side="LONG", policy="PASSIVE")

    def test_non_positive_tick_rejected_rather_than_silently_passing_price_through(self):
        for tick in (0, -0.01):
            with self.subTest(tick=tick):
                with self.assertRaises(TickRegimeError):
                    self.engine.align_price_to_tick(150.00, tick)

    def test_non_finite_and_non_positive_prices_rejected(self):
        for price in (float("nan"), float("inf"), -1.0, 0.0):
            with self.subTest(price=price):
                with self.assertRaises(TickRegimeError):
                    self.engine.align_price_to_tick(price, 0.01)

    def test_price_below_half_a_tick_raises_instead_of_inventing_one(self):
        # 0.00004 is less than half of a $0.0001 tick; rounding it to zero would be an
        # invalid order price and rounding it up would multiply the limit by 2.5.
        with self.assertRaises(TickRegimeError):
            self.engine.align_price_to_tick("0.00004", "0.0001")

    def test_price_too_large_for_exact_decimal_arithmetic_raises(self):
        with self.assertRaises(TickRegimeError):
            self.engine.align_price_to_tick(Decimal("1e30"), "0.01")

    def test_integer_tick_alignment_stays_readable(self):
        # RTS 11 band 1, 100 <= P < 200 -> tick of EUR 1.
        self.assertEqual(self.engine.align_price_to_tick_decimal("150.4", "1"), Decimal("150"))

    def test_dirty_float_is_treated_as_the_value_it_actually_holds(self):
        # 0.1 + 0.2 is 0.30000000000000004; it is off-tick and must be aligned, not
        # waved through by a tolerance wider than a sub-penny tick.
        self.assertEqual(self.engine.align_price_to_tick_decimal(0.1 + 0.2, "0.0001"), Decimal("0.3000"))

    def test_decimal_and_string_inputs_are_exact(self):
        self.assertEqual(
            self.engine.align_price_to_tick_decimal(Decimal("0.12345"), Decimal("0.0001")),
            Decimal("0.1235"),
        )


class TestAuditReport(unittest.TestCase):
    def setUp(self):
        self.engine = ExchangeTickSizeRegimeEngine()

    def test_compliant_order(self):
        report = self.engine.audit_order_tick_compliance("US_EQUITIES", "AAPL", "150.01")
        self.assertTrue(report.is_on_tick)
        self.assertEqual(report.status, "TICK_COMPLIANT")
        self.assertEqual(report.aligned_price_decimal, Decimal("150.01"))
        self.assertIn("242.612", report.regulatory_source)

    def test_off_tick_auto_aligned(self):
        report = self.engine.audit_order_tick_compliance("US_EQUITIES", "AAPL", 150.005, auto_align=True)
        self.assertFalse(report.is_on_tick)
        self.assertEqual(report.active_tick_size, 0.01)
        self.assertEqual(report.aligned_price, 150.01)
        self.assertEqual(report.status, "OFF_TICK_ALIGNED")

    def test_off_tick_rejected_when_auto_align_disabled(self):
        report = self.engine.audit_order_tick_compliance("US_EQUITIES", "AAPL", 150.005, auto_align=False)
        self.assertEqual(report.status, "OFF_TICK_REJECTED")
        self.assertEqual(report.aligned_price_decimal, Decimal("150.01"))

    def test_passive_buy_audit_never_raises_the_limit_price(self):
        report = self.engine.audit_order_tick_compliance(
            "US_EQUITIES", "AAPL", "150.005", side="BUY", policy="PASSIVE"
        )
        self.assertEqual(report.aligned_price_decimal, Decimal("150.00"))
        self.assertLess(report.aligned_price_decimal, report.proposed_price_decimal)
        self.assertEqual(report.side, "BUY")
        self.assertEqual(report.rounding_policy, "PASSIVE")

    def test_alignment_across_a_band_boundary_reports_the_governing_tick(self):
        # 0.99999 rounds to 1.0000, where Rule 612 requires $0.01 rather than $0.0001.
        report = self.engine.audit_order_tick_compliance("US_EQUITIES", "PENNY", "0.99999")
        self.assertEqual(report.aligned_price_decimal, Decimal("1.00"))
        self.assertEqual(report.active_tick_size_decimal, Decimal("0.01"))
        self.assertTrue(report.crossed_price_band)

    def test_rts11_audit_carries_the_band(self):
        report = self.engine.audit_order_tick_compliance(
            "EU_XETRA", "SAP", "25.003", liquidity_band=1
        )
        self.assertEqual(report.venue_id, "EU_RTS11")
        self.assertEqual(report.active_tick_size_decimal, Decimal("0.2"))
        self.assertEqual(report.aligned_price_decimal, Decimal("25.0"))
        self.assertEqual(report.liquidity_band, 1)

    def test_venue_assigned_tick_may_be_coarser_than_the_regulatory_floor(self):
        report = self.engine.audit_order_tick_compliance(
            "EU_RTS11", "SAP", "25.01", liquidity_band=6, venue_assigned_tick="0.05"
        )
        self.assertEqual(report.active_tick_size_decimal, Decimal("0.05"))
        self.assertEqual(report.aligned_price_decimal, Decimal("25.00"))

    def test_venue_assigned_tick_finer_than_the_regulatory_floor_is_rejected(self):
        with self.assertRaises(TickRegimeError):
            self.engine.audit_order_tick_compliance(
                "EU_RTS11", "SAP", "25.001", liquidity_band=1, venue_assigned_tick="0.001"
            )

    def test_unknown_venue_raises_instead_of_defaulting_to_a_penny(self):
        with self.assertRaises(UnknownVenueError):
            self.engine.audit_order_tick_compliance("TSE_JAPAN", "7203", 2500.0)

    def test_invalid_prices_rejected(self):
        for price in (0, -5, float("nan"), float("inf"), "abc", None):
            with self.subTest(price=price):
                with self.assertRaises(TickRegimeError):
                    self.engine.audit_order_tick_compliance("US_EQUITIES", "AAPL", price)


class TestVenueRegistry(unittest.TestCase):
    def setUp(self):
        self.engine = ExchangeTickSizeRegimeEngine()

    def _regime(self, rules):
        return VenueTickRegime(
            venue_id="TEST_VENUE",
            currency="USD",
            source="unit test",
            rules_by_band={0: tuple(rules)},
        )

    def test_register_and_query_custom_venue(self):
        self.engine.register_venue(
            self._regime([PriceBandTickRule("0", "5", "0.005"), PriceBandTickRule("5", "Infinity", "0.05")]),
            aliases=("TESTV",),
        )
        self.assertEqual(self.engine.get_active_tick_size_decimal("TESTV", "7.50"), Decimal("0.05"))

    def test_gapped_table_rejected(self):
        with self.assertRaises(TickRegimeError):
            self.engine.register_venue(
                self._regime([PriceBandTickRule("0", "5", "0.005"), PriceBandTickRule("10", "Infinity", "0.05")])
            )

    def test_unbounded_top_band_required(self):
        with self.assertRaises(TickRegimeError):
            self.engine.register_venue(self._regime([PriceBandTickRule("0", "5", "0.005")]))

    def test_table_must_start_at_zero(self):
        with self.assertRaises(TickRegimeError):
            self.engine.register_venue(self._regime([PriceBandTickRule("1", "Infinity", "0.01")]))

    def test_invalid_price_band_rule_rejected(self):
        with self.assertRaises(TickRegimeError):
            PriceBandTickRule("5", "1", "0.01")
        with self.assertRaises(TickRegimeError):
            PriceBandTickRule("0", "1", "0")


if __name__ == "__main__":
    unittest.main()

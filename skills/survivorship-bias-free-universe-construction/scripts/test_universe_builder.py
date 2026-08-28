"""
Unit tests for survivorship-bias-free-universe-construction.

Fixtures use dates and prices verified against primary sources, so the expected values
are independently derived rather than restatements of the implementation:

- **LEH** -- Lehman Brothers Holdings. Chapter 11 filed 2008-09-15; NYSE listing ended
  the following days and the common continued over the counter as LEHMQ before common
  holders were wiped out. Used for the delisting-return settlement path, because
  "bankruptcy therefore zero" is exactly the shortcut this module refuses to take.
- **TWTR** -- Twitter Inc. Merger effective 2022-10-27; last NYSE trade 2022-10-27 at a
  $53.70 close; trading suspended before the open on 2022-10-28; NYSE Form 25-NSE
  effective 2022-11-08. Consideration $54.20 cash per share. The settlement price and
  the last close differ, which is the point.
- **GM** -- ticker recycled. Old General Motors Corporation (Chapter 11 2009-06-01,
  ticker moved to GMGMQ then MTLQQ effective 2009-07-15) and the new General Motors
  Company (IPO 2010-11-18) both trade as ``GM``.
"""
import datetime
import logging
import unittest

from universe_builder import (
    SHUMWAY_1997_NYSE_AMEX_DELISTING_RETURN,
    SHUMWAY_WARTHER_1999_NASDAQ_DELISTING_RETURN,
    DelistingReason,
    InstrumentMetadata,
    SurvivorshipFreeUniverseEngine,
    UniverseError,
)

# The engine logs a line per settlement and per audit; keep test output readable.
logging.getLogger("universe_builder").setLevel(logging.CRITICAL)

D = datetime.date


def _apple() -> InstrumentMetadata:
    return InstrumentMetadata(
        symbol="AAPL", name="Apple Inc", listing_date=D(1980, 12, 12), security_id="PERMNO-14593"
    )


def _lehman() -> InstrumentMetadata:
    """Settles on an imputed delisting return, not on a fabricated zero."""
    return InstrumentMetadata(
        symbol="LEH",
        name="Lehman Brothers Holdings Inc",
        listing_date=D(1994, 5, 31),
        delisting_date=D(2008, 9, 16),
        delisting_reason=DelistingReason.BANKRUPTCY,
        delisting_return=SHUMWAY_1997_NYSE_AMEX_DELISTING_RETURN,
        security_id="PERMNO-84129",
    )


def _twitter() -> InstrumentMetadata:
    """Last traded 2022-10-27 at $53.70; each share converted to $54.20 cash."""
    return InstrumentMetadata(
        symbol="TWTR",
        name="Twitter Inc",
        listing_date=D(2013, 11, 7),
        delisting_date=D(2022, 10, 27),
        delisting_reason=DelistingReason.MERGER_ACQUISITION,
        delisting_settlement_price=54.20,
        security_id="CUSIP-90184L102",
    )


class TestInstrumentMetadataValidation(unittest.TestCase):
    """Registration-time validation. Every one of these silently passed before."""

    def test_delisting_date_before_listing_date_rejected(self):
        with self.assertRaises(UniverseError) as ctx:
            InstrumentMetadata(
                symbol="BAD",
                name="Impossible",
                listing_date=D(2020, 1, 1),
                delisting_date=D(2010, 1, 1),
                delisting_reason=DelistingReason.BANKRUPTCY,
                delisting_settlement_price=0.0,
            )
        self.assertIn("precedes", str(ctx.exception))

    def test_delisted_reason_without_date_rejected(self):
        with self.assertRaises(UniverseError) as ctx:
            InstrumentMetadata(
                symbol="X",
                name="X",
                listing_date=D(2000, 1, 1),
                delisting_reason=DelistingReason.BANKRUPTCY,
                delisting_settlement_price=0.0,
            )
        self.assertIn("never leave the universe", str(ctx.exception))

    def test_delisting_date_with_active_reason_rejected(self):
        with self.assertRaises(UniverseError):
            InstrumentMetadata(
                symbol="X", name="X", listing_date=D(2000, 1, 1), delisting_date=D(2005, 1, 1)
            )

    def test_active_instrument_may_not_carry_terminal_value(self):
        with self.assertRaises(UniverseError):
            InstrumentMetadata(
                symbol="X",
                name="X",
                listing_date=D(2000, 1, 1),
                delisting_settlement_price=10.0,
            )

    def test_delisted_without_terminal_value_rejected(self):
        """Regression: this used to default to 0.0 and settle a merger at nothing."""
        with self.assertRaises(UniverseError) as ctx:
            InstrumentMetadata(
                symbol="TWTR",
                name="Twitter Inc",
                listing_date=D(2013, 11, 7),
                delisting_date=D(2022, 10, 27),
                delisting_reason=DelistingReason.MERGER_ACQUISITION,
            )
        self.assertIn("fabricates a total loss", str(ctx.exception))

    def test_both_terminal_value_modes_rejected(self):
        with self.assertRaises(UniverseError) as ctx:
            InstrumentMetadata(
                symbol="X",
                name="X",
                listing_date=D(2000, 1, 1),
                delisting_date=D(2005, 1, 1),
                delisting_reason=DelistingReason.BANKRUPTCY,
                delisting_settlement_price=1.0,
                delisting_return=-0.5,
            )
        self.assertIn("exactly one", str(ctx.exception))

    def test_negative_settlement_price_rejected(self):
        with self.assertRaises(UniverseError):
            InstrumentMetadata(
                symbol="X",
                name="X",
                listing_date=D(2000, 1, 1),
                delisting_date=D(2005, 1, 1),
                delisting_reason=DelistingReason.BANKRUPTCY,
                delisting_settlement_price=-5.0,
            )

    def test_delisting_return_below_total_loss_rejected(self):
        with self.assertRaises(UniverseError):
            InstrumentMetadata(
                symbol="X",
                name="X",
                listing_date=D(2000, 1, 1),
                delisting_date=D(2005, 1, 1),
                delisting_reason=DelistingReason.BANKRUPTCY,
                delisting_return=-1.5,
            )

    def test_delisting_return_of_exactly_total_loss_accepted(self):
        """-1.0 is a real outcome and must not be swept up by the boundary check."""
        meta = InstrumentMetadata(
            symbol="X",
            name="X",
            listing_date=D(2000, 1, 1),
            delisting_date=D(2005, 1, 1),
            delisting_reason=DelistingReason.BANKRUPTCY,
            delisting_return=-1.0,
        )
        self.assertEqual(meta.delisting_return, -1.0)

    def test_non_finite_settlement_price_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.subTest(value=bad):
                with self.assertRaises(UniverseError):
                    InstrumentMetadata(
                        symbol="X",
                        name="X",
                        listing_date=D(2000, 1, 1),
                        delisting_date=D(2005, 1, 1),
                        delisting_reason=DelistingReason.BANKRUPTCY,
                        delisting_settlement_price=bad,
                    )

    def test_datetime_rejected_at_registration_not_at_comparison(self):
        with self.assertRaises(UniverseError) as ctx:
            InstrumentMetadata(
                symbol="X", name="X", listing_date=datetime.datetime(2000, 1, 1, 9, 30)
            )
        self.assertIn("datetime.datetime", str(ctx.exception))

    def test_empty_symbol_rejected(self):
        with self.assertRaises(UniverseError):
            InstrumentMetadata(symbol="   ", name="X", listing_date=D(2000, 1, 1))

    def test_symbol_normalised_and_security_id_defaults_to_symbol(self):
        meta = InstrumentMetadata(symbol=" aapl ", name="Apple", listing_date=D(1980, 12, 12))
        self.assertEqual(meta.symbol, "AAPL")
        self.assertEqual(meta.security_id, "AAPL")


class TestPointInTimeUniverse(unittest.TestCase):
    def setUp(self):
        self.engine = SurvivorshipFreeUniverseEngine()
        for meta in (_apple(), _lehman(), _twitter()):
            self.engine.add_instrument(meta)

    def test_universe_on_a_historical_date_contains_the_since_delisted_name(self):
        univ = self.engine.get_active_universe(D(2008, 1, 1))
        self.assertEqual(univ, ["AAPL", "LEH"])

    def test_universe_after_delisting_drops_the_name(self):
        univ = self.engine.get_active_universe(D(2020, 1, 1))
        self.assertEqual(univ, ["AAPL", "TWTR"])

    def test_delisting_date_is_the_last_trading_day_and_is_inclusive(self):
        """TWTR printed its final NYSE trade on 2022-10-27, so it is in that universe.

        Off by one in either direction is a real bug: excluding 10-27 loses a whole
        session of a name that traded, and including 10-28 holds a name that did not.
        """
        self.assertIn("TWTR", self.engine.get_active_universe(D(2022, 10, 27)))
        self.assertNotIn("TWTR", self.engine.get_active_universe(D(2022, 10, 28)))

    def test_listing_date_is_inclusive(self):
        self.assertIn("TWTR", self.engine.get_active_universe(D(2013, 11, 7)))
        self.assertNotIn("TWTR", self.engine.get_active_universe(D(2013, 11, 6)))

    def test_get_active_securities_returns_metadata(self):
        live = self.engine.get_active_securities(D(2008, 1, 1))
        self.assertEqual([m.security_id for m in live], ["PERMNO-14593", "PERMNO-84129"])

    def test_datetime_as_of_date_rejected(self):
        with self.assertRaises(UniverseError):
            self.engine.get_active_universe(datetime.datetime(2008, 1, 1, 9, 30))


class TestTickerRecycling(unittest.TestCase):
    """Regression for the defect that silently reintroduced survivorship bias.

    Keyed by ticker, registering the new GM discarded the old one and every pre-2010
    universe query returned an empty list where the old issuer belonged.
    """

    def setUp(self):
        self.engine = SurvivorshipFreeUniverseEngine()
        self.old_gm = InstrumentMetadata(
            symbol="GM",
            name="General Motors Corporation",
            listing_date=D(1916, 9, 16),
            delisting_date=D(2009, 6, 1),
            delisting_reason=DelistingReason.BANKRUPTCY,
            delisting_return=SHUMWAY_1997_NYSE_AMEX_DELISTING_RETURN,
            security_id="PERMNO-12079",
        )
        self.new_gm = InstrumentMetadata(
            symbol="GM",
            name="General Motors Company",
            listing_date=D(2010, 11, 18),
            security_id="PERMNO-12369",
        )
        self.engine.add_instrument(self.old_gm)
        self.engine.add_instrument(self.new_gm)

    def test_both_issuers_survive_registration(self):
        self.assertEqual(len(self.engine.instruments), 2)

    def test_old_issuer_is_in_the_2008_universe(self):
        self.assertIn("GM", self.engine.get_active_universe(D(2008, 1, 1)))

    def test_ticker_resolves_to_the_issuer_that_held_it_on_that_date(self):
        self.assertEqual(
            self.engine.resolve_symbol("GM", D(2008, 1, 1)).name,
            "General Motors Corporation",
        )
        self.assertEqual(
            self.engine.resolve_symbol("GM", D(2015, 1, 1)).name, "General Motors Company"
        )

    def test_ticker_resolves_to_nothing_in_the_gap_between_issuers(self):
        self.assertIsNone(self.engine.resolve_symbol("GM", D(2010, 1, 1)))

    def test_settling_a_recycled_ticker_by_ticker_is_refused(self):
        with self.assertRaises(UniverseError) as ctx:
            self.engine.process_delisting_settlement("GM", 100, last_traded_price=1.00)
        self.assertIn("ambiguous", str(ctx.exception))

    def test_default_security_id_colliding_with_a_recycled_ticker_is_refused(self):
        """A ``security_id`` that defaults to the symbol lands in both namespaces.

        Resolving the id first picked one issuer silently, and which one depended on
        registration order -- so the same call settled a different company, or an
        ACTIVE one, purely on load order.
        """
        for order in ("old_first", "new_first"):
            with self.subTest(order=order):
                engine = SurvivorshipFreeUniverseEngine()
                old_gm = InstrumentMetadata(
                    symbol="GM",
                    name="General Motors Corporation",
                    listing_date=D(1916, 9, 16),
                    delisting_date=D(2009, 6, 1),
                    delisting_reason=DelistingReason.BANKRUPTCY,
                    delisting_return=SHUMWAY_1997_NYSE_AMEX_DELISTING_RETURN,
                )  # security_id defaults to "GM"
                new_gm = InstrumentMetadata(
                    symbol="GM",
                    name="General Motors Company",
                    listing_date=D(2010, 11, 18),
                    security_id="PERMNO-12369",
                )
                pair = (old_gm, new_gm) if order == "old_first" else (new_gm, old_gm)
                for meta in pair:
                    engine.add_instrument(meta)
                with self.assertRaises(UniverseError) as ctx:
                    engine.process_delisting_settlement("GM", 100, last_traded_price=1.00)
                self.assertIn("ambiguous", str(ctx.exception))

    def test_settling_a_recycled_ticker_by_security_id_works(self):
        cash, _ = self.engine.process_delisting_settlement(
            "PERMNO-12079", 100, last_traded_price=1.00
        )
        self.assertAlmostEqual(cash, 70.0, places=9)

    def test_duplicate_security_id_is_refused_not_overwritten(self):
        with self.assertRaises(UniverseError) as ctx:
            self.engine.add_instrument(
                InstrumentMetadata(
                    symbol="GMX", name="Impostor", listing_date=D(2020, 1, 1),
                    security_id="PERMNO-12369",
                )
            )
        self.assertIn("already registered", str(ctx.exception))

    def test_overlapping_listing_windows_on_one_ticker_raise(self):
        engine = SurvivorshipFreeUniverseEngine()
        engine.add_instrument(
            InstrumentMetadata(symbol="ZZZ", name="A", listing_date=D(2000, 1, 1), security_id="A")
        )
        engine.add_instrument(
            InstrumentMetadata(symbol="ZZZ", name="B", listing_date=D(2001, 1, 1), security_id="B")
        )
        with self.assertRaises(UniverseError) as ctx:
            engine.get_active_universe(D(2002, 1, 1))
        self.assertIn("Ticker collision", str(ctx.exception))


class TestDelistingSettlement(unittest.TestCase):
    def setUp(self):
        self.engine = SurvivorshipFreeUniverseEngine()
        for meta in (_apple(), _lehman(), _twitter()):
            self.engine.add_instrument(meta)

    def test_merger_settles_at_the_consideration_not_the_last_close(self):
        """100 shares x $54.20 consideration = $5,420.00, not 100 x $53.70 = $5,370.00."""
        cash, msg = self.engine.process_delisting_settlement("TWTR", position_qty=100)
        self.assertAlmostEqual(cash, 5420.0, places=9)
        self.assertNotAlmostEqual(cash, 5370.0, places=2)
        self.assertIn("MERGER_ACQUISITION", msg)

    def test_bankruptcy_settles_on_the_imputed_delisting_return(self):
        """Shumway (1997): -30% of the last traded price. 100 x $3.65 x 0.70 = $255.50.

        Not $0.00 -- LEH went on trading over the counter after the Chapter 11 filing.
        """
        cash, msg = self.engine.process_delisting_settlement(
            "LEH", position_qty=100, last_traded_price=3.65
        )
        self.assertAlmostEqual(cash, 255.5, places=9)
        self.assertIn("BANKRUPTCY", msg)
        self.assertIn("-30.00%", msg)

    def test_nasdaq_imputation_constant_is_the_shumway_warther_figure(self):
        self.assertEqual(SHUMWAY_WARTHER_1999_NASDAQ_DELISTING_RETURN, -0.55)
        self.assertEqual(SHUMWAY_1997_NYSE_AMEX_DELISTING_RETURN, -0.30)

    def test_total_loss_settles_at_zero_when_that_is_what_was_supplied(self):
        engine = SurvivorshipFreeUniverseEngine()
        engine.add_instrument(
            InstrumentMetadata(
                symbol="ZERO",
                name="Wiped Out Inc",
                listing_date=D(2000, 1, 1),
                delisting_date=D(2005, 1, 1),
                delisting_reason=DelistingReason.BANKRUPTCY,
                delisting_settlement_price=0.0,
            )
        )
        cash, _ = engine.process_delisting_settlement("ZERO", position_qty=100)
        self.assertEqual(cash, 0.0)

    def test_short_position_in_a_wiped_out_name_realises_a_gain(self):
        engine = SurvivorshipFreeUniverseEngine()
        engine.add_instrument(
            InstrumentMetadata(
                symbol="ZERO",
                name="Wiped Out Inc",
                listing_date=D(2000, 1, 1),
                delisting_date=D(2005, 1, 1),
                delisting_reason=DelistingReason.BANKRUPTCY,
                delisting_return=-1.0,
            )
        )
        cash, _ = engine.process_delisting_settlement(
            "ZERO", position_qty=-100, last_traded_price=8.0
        )
        self.assertEqual(cash, 0.0)

    def test_settling_an_active_instrument_raises(self):
        """Regression: this used to return qty x 0.0 and book a silent 100% loss."""
        with self.assertRaises(UniverseError) as ctx:
            self.engine.process_delisting_settlement("AAPL", position_qty=100)
        self.assertIn("ACTIVE", str(ctx.exception))

    def test_delisting_return_mode_without_a_price_raises(self):
        with self.assertRaises(UniverseError) as ctx:
            self.engine.process_delisting_settlement("LEH", position_qty=100)
        self.assertIn("last_traded_price", str(ctx.exception))

    def test_non_positive_last_traded_price_raises(self):
        for bad in (0.0, -1.0):
            with self.subTest(price=bad):
                with self.assertRaises(UniverseError):
                    self.engine.process_delisting_settlement("LEH", 100, last_traded_price=bad)

    def test_non_finite_quantity_raises(self):
        with self.assertRaises(UniverseError):
            self.engine.process_delisting_settlement("TWTR", position_qty=float("nan"))

    def test_unknown_symbol_raises(self):
        with self.assertRaises(UniverseError):
            self.engine.process_delisting_settlement("NOPE", position_qty=1)

    def test_settlement_by_security_id_matches_settlement_by_ticker(self):
        by_ticker, _ = self.engine.process_delisting_settlement("TWTR", 100)
        by_id, _ = self.engine.process_delisting_settlement("CUSIP-90184L102", 100)
        self.assertEqual(by_ticker, by_id)


class TestSurvivorshipBiasAudit(unittest.TestCase):
    def setUp(self):
        self.engine = SurvivorshipFreeUniverseEngine()
        for meta in (_apple(), _lehman(), _twitter()):
            self.engine.add_instrument(meta)

    def test_attrition_counts_only_names_live_in_the_window(self):
        """2008-01-01..2008-12-31: AAPL and LEH are live, TWTR listed in 2013.

        Denominator 2, LEH delists inside the window, so attrition is 1/2 = 0.5. The
        old implementation divided by all 3 registered names and reported 0.333.
        """
        audit = self.engine.audit_survivorship_bias(D(2008, 1, 1), D(2008, 12, 31))
        self.assertEqual(audit["universe_in_period"], 2)
        self.assertEqual(audit["never_live_in_period"], 1)
        self.assertEqual(audit["delisted_in_period"], 1)
        self.assertEqual(audit["delisted_symbols"], ["LEH"])
        self.assertEqual(audit["survivors_at_end"], 1)
        self.assertAlmostEqual(audit["attrition_rate"], 0.5, places=9)

    def test_name_delisted_before_the_window_is_excluded_entirely(self):
        audit = self.engine.audit_survivorship_bias(D(2015, 1, 1), D(2016, 1, 1))
        self.assertEqual(audit["universe_in_period"], 2)  # AAPL, TWTR
        self.assertEqual(audit["delisted_in_period"], 0)
        self.assertEqual(audit["attrition_rate"], 0.0)

    def test_delisting_on_the_window_boundary_counts(self):
        audit = self.engine.audit_survivorship_bias(D(2008, 9, 16), D(2008, 9, 16))
        self.assertEqual(audit["delisted_symbols"], ["LEH"])

    def test_a_universe_of_names_that_never_traded_in_the_window_reports_zero(self):
        """Regression: 99 names listing in 2030 plus one delisting used to report a
        1% ratio and a True bias-protection flag over a window in which nothing traded.
        """
        engine = SurvivorshipFreeUniverseEngine()
        for i in range(99):
            engine.add_instrument(
                InstrumentMetadata(
                    symbol=f"S{i}", name="Future", listing_date=D(2030, 1, 1), security_id=f"F{i}"
                )
            )
        audit = engine.audit_survivorship_bias(D(2000, 1, 1), D(2010, 1, 1))
        self.assertEqual(audit["universe_in_period"], 0)
        self.assertEqual(audit["never_live_in_period"], 99)
        self.assertEqual(audit["attrition_rate"], 0.0)
        self.assertNotIn("has_bias_protection", audit)

    def test_ghost_audit_finds_names_missing_from_todays_snapshot(self):
        audit = self.engine.audit_survivorship_bias(
            D(2008, 1, 1), D(2023, 1, 1), current_static_universe=["AAPL"]
        )
        self.assertEqual(audit["ghost_count"], 2)
        self.assertEqual(audit["ghost_symbols"], ["LEH", "TWTR"])

    def test_ghost_count_is_none_when_not_audited(self):
        audit = self.engine.audit_survivorship_bias(D(2008, 1, 1), D(2023, 1, 1))
        self.assertIsNone(audit["ghost_count"])
        self.assertIsNone(audit["ghost_symbols"])

    def test_a_snapshot_built_universe_has_no_ghosts(self):
        """The signature of the bias: today's names, applied backwards, ghost count 0."""
        engine = SurvivorshipFreeUniverseEngine()
        engine.add_instrument(_apple())
        audit = engine.audit_survivorship_bias(
            D(2008, 1, 1), D(2023, 1, 1), current_static_universe=["AAPL"]
        )
        self.assertEqual(audit["ghost_count"], 0)

    def test_expected_attrition_threshold_is_the_callers(self):
        audit = self.engine.audit_survivorship_bias(
            D(2008, 1, 1), D(2008, 12, 31), min_expected_attrition_rate=0.5
        )
        self.assertTrue(audit["meets_expected_attrition"])
        audit = self.engine.audit_survivorship_bias(
            D(2008, 1, 1), D(2008, 12, 31), min_expected_attrition_rate=0.51
        )
        self.assertFalse(audit["meets_expected_attrition"])

    def test_expected_attrition_is_none_when_not_supplied(self):
        audit = self.engine.audit_survivorship_bias(D(2008, 1, 1), D(2008, 12, 31))
        self.assertIsNone(audit["meets_expected_attrition"])

    def test_inverted_window_raises(self):
        with self.assertRaises(UniverseError):
            self.engine.audit_survivorship_bias(D(2010, 1, 1), D(2009, 1, 1))

    def test_out_of_range_threshold_raises(self):
        with self.assertRaises(UniverseError):
            self.engine.audit_survivorship_bias(
                D(2008, 1, 1), D(2009, 1, 1), min_expected_attrition_rate=1.5
            )

    def test_bare_string_snapshot_raises_rather_than_iterating_characters(self):
        with self.assertRaises(UniverseError):
            self.engine.audit_survivorship_bias(
                D(2008, 1, 1), D(2009, 1, 1), current_static_universe="AAPL"
            )


if __name__ == "__main__":
    unittest.main()

"""Unit tests for the SEC Reg NMS Rule 611 order protection engine.

Expected values are derived from the rule text, not from the implementation:
the trade-through test comes from the definition in Rule 600(b)(105), the
one-second flickering window and its "equal or inferior" comparison from
Rule 611(b)(8), the stopped-order underwater test from Rule 611(b)(9)(iii), and
the ISO sweep obligation from Rule 600(b)(47)(ii).
"""
import datetime
import logging
import unittest

from us_reg_nms_order_protection_rule_compliance import (
    FLICKERING_QUOTE_WINDOW,
    ExecutionRecord,
    OrderSide,
    ProtectedQuote,
    RegNMSError,
    RegNMSOrderProtectionEngine,
    Rule611Status,
    TradeThroughKind,
    is_regular_trading_hours,
)

UTC = datetime.timezone.utc

# 2 June 2026 was a Tuesday. 14:00 UTC is 10:00 a.m. Eastern (EDT), squarely
# inside regular trading hours, so the session gate never confounds a test.
MIDSESSION = datetime.datetime(2026, 6, 2, 14, 0, 0, tzinfo=UTC)


class RegNMSTestBase(unittest.TestCase):
    def setUp(self):
        # The engine logs a violation at ERROR; keep the test output readable.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)
        self.engine = RegNMSOrderProtectionEngine()
        self.now = MIDSESSION
        self.stale = self.now - datetime.timedelta(seconds=5)

        # Protected NBBO: NBB $100.00 (NASDAQ), NBO $100.05 (NYSE).
        self.quotes = [
            self.quote("NASDAQ", 100.00, 100.10, self.stale),
            self.quote("NYSE", 99.95, 100.05, self.stale),
        ]

    @staticmethod
    def quote(venue, bid, offer, timestamp, symbol="AAPL", automated=True):
        return ProtectedQuote(
            venue_id=venue,
            symbol=symbol,
            nbb_price=bid,
            nbb_size=500,
            nbo_price=offer,
            nbo_size=500,
            is_automated=automated,
            timestamp=timestamp,
        )

    def execution(self, **overrides):
        params = dict(
            execution_id="EXEC-1",
            symbol="AAPL",
            side=OrderSide.BUY,
            price=100.00,
            quantity=100,
            venue_id="IEX",
            execution_timestamp=self.now,
        )
        params.update(overrides)
        return ExecutionRecord(**params)


class TestProtectedNBBO(RegNMSTestBase):
    def test_nbbo_is_best_bid_and_best_offer_across_venues(self):
        nbb, nbo, nbb_venue, nbo_venue = self.engine.compute_protected_nbbo(
            self.quotes, as_of=self.now, symbol="AAPL"
        )
        self.assertAlmostEqual(nbb, 100.00)
        self.assertAlmostEqual(nbo, 100.05)
        self.assertEqual(nbb_venue, "NASDAQ")
        self.assertEqual(nbo_venue, "NYSE")

    def test_manual_quotes_are_not_protected(self):
        """Rule 611 protects automated quotations only (Rule 600(b)(54), (b)(81))."""
        quotes = list(self.quotes) + [
            self.quote("CHX", 100.30, 100.01, self.stale, automated=False)
        ]
        nbb, nbo, _, nbo_venue = self.engine.compute_protected_nbbo(
            quotes, as_of=self.now, symbol="AAPL"
        )
        self.assertAlmostEqual(nbb, 100.00)
        self.assertAlmostEqual(nbo, 100.05)
        self.assertNotEqual(nbo_venue, "CHX")

    def test_only_the_latest_quote_per_venue_at_or_before_as_of_is_used(self):
        quotes = [
            self.quote("NASDAQ", 100.00, 100.10, self.now - datetime.timedelta(seconds=9)),
            self.quote("NASDAQ", 99.50, 100.40, self.now - datetime.timedelta(seconds=3)),
            self.quote("NYSE", 99.95, 100.05, self.stale),
        ]
        nbb, nbo, _, _ = self.engine.compute_protected_nbbo(
            quotes, as_of=self.now, symbol="AAPL"
        )
        self.assertAlmostEqual(nbb, 99.95)   # NASDAQ's superseded $100.00 is gone
        self.assertAlmostEqual(nbo, 100.05)

    def test_quotes_after_as_of_are_excluded(self):
        """A quote that did not yet exist cannot have been traded through."""
        quotes = list(self.quotes) + [
            self.quote("MEMX", 100.90, 100.95, self.now + datetime.timedelta(seconds=1))
        ]
        nbb, _, nbb_venue, _ = self.engine.compute_protected_nbbo(
            quotes, as_of=self.now, symbol="AAPL"
        )
        self.assertAlmostEqual(nbb, 100.00)
        self.assertEqual(nbb_venue, "NASDAQ")

    def test_other_symbols_are_excluded(self):
        quotes = list(self.quotes) + [self.quote("NYSE", 5.00, 5.05, self.stale, symbol="F")]
        nbb, nbo, _, _ = self.engine.compute_protected_nbbo(
            quotes, as_of=self.now, symbol="AAPL"
        )
        self.assertAlmostEqual(nbb, 100.00)
        self.assertAlmostEqual(nbo, 100.05)

    def test_one_sided_book_raises(self):
        quotes = [self.quote("NASDAQ", 100.00, 0.0, self.stale)]
        with self.assertRaises(RegNMSError):
            self.engine.compute_protected_nbbo(quotes, as_of=self.now, symbol="AAPL")

    def test_no_quotes_raises(self):
        with self.assertRaises(RegNMSError):
            self.engine.compute_protected_nbbo([], as_of=self.now, symbol="AAPL")


class TestTradeThroughDetection(RegNMSTestBase):
    def test_buy_at_the_protected_offer_is_compliant(self):
        res = self.engine.evaluate_execution(
            self.execution(price=100.05), self.quotes
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.COMPLIANT_NO_TRADE_THROUGH)
        self.assertAlmostEqual(res.protected_nbb, 100.00)
        self.assertAlmostEqual(res.protected_nbo, 100.05)
        self.assertEqual(res.quote_as_of, self.now)

    def test_buy_above_the_protected_offer_is_a_trade_through(self):
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-BUY-TT", price=100.08), self.quotes
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.TRADE_THROUGH_VIOLATION)
        self.assertEqual(res.trade_through_kind, TradeThroughKind.THROUGH_PROTECTED_OFFER)
        self.assertEqual(res.violating_venue_id, "NYSE")
        self.assertAlmostEqual(res.traded_through_quote_price, 100.05)
        # (100.08 - 100.05) / 100.05 * 10000 = 2.9985 bps, derived by hand.
        self.assertAlmostEqual(res.trade_through_bps, 2.9985007, places=5)

    def test_sell_below_the_protected_bid_is_a_trade_through(self):
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-SELL-TT", side=OrderSide.SELL, price=99.90),
            self.quotes,
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.trade_through_kind, TradeThroughKind.THROUGH_PROTECTED_BID)
        self.assertEqual(res.violating_venue_id, "NASDAQ")
        # (100.00 - 99.90) / 100.00 * 10000 = 10.0 bps.
        self.assertAlmostEqual(res.trade_through_bps, 10.0, places=6)

    def test_buy_below_the_protected_bid_is_also_a_trade_through(self):
        """Regression: Rule 600(b)(105) is a price test, not a side test.

        A *purchase* printed below the protected bid trades through that bid.
        An engine that only tests buys against the offer reports this clean --
        which is exactly why Rule 611(b)(9) has to except a stopped buy order
        printed below the national best bid.
        """
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-BUY-UNDER-BID", price=99.50), self.quotes
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.TRADE_THROUGH_VIOLATION)
        self.assertEqual(res.trade_through_kind, TradeThroughKind.THROUGH_PROTECTED_BID)
        self.assertEqual(res.violating_venue_id, "NASDAQ")

    def test_sell_above_the_protected_offer_is_also_a_trade_through(self):
        res = self.engine.evaluate_execution(
            self.execution(
                execution_id="E-SELL-OVER-OFFER", side=OrderSide.SELL, price=100.60
            ),
            self.quotes,
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.trade_through_kind, TradeThroughKind.THROUGH_PROTECTED_OFFER)

    def test_execution_exactly_at_the_protected_bid_is_compliant(self):
        res = self.engine.evaluate_execution(
            self.execution(side=OrderSide.SELL, price=100.00), self.quotes
        )
        self.assertEqual(res.status, Rule611Status.COMPLIANT_NO_TRADE_THROUGH)

    def test_execution_inside_the_spread_is_compliant(self):
        res = self.engine.evaluate_execution(
            self.execution(price=100.025), self.quotes
        )
        self.assertEqual(res.status, Rule611Status.COMPLIANT_NO_TRADE_THROUGH)


class TestRegularTradingHours(RegNMSTestBase):
    @staticmethod
    def at(month, day, hour, minute, second=0):
        return datetime.datetime(2026, month, day, hour, minute, second, tzinfo=UTC)

    def test_rth_boundaries(self):
        # 2 June 2026 (Tuesday), EDT = UTC-4.
        self.assertFalse(is_regular_trading_hours(self.at(6, 2, 13, 29, 59)))
        self.assertTrue(is_regular_trading_hours(self.at(6, 2, 13, 30)))
        self.assertTrue(is_regular_trading_hours(self.at(6, 2, 20, 0)))
        self.assertFalse(is_regular_trading_hours(self.at(6, 2, 20, 0, 1)))
        # Saturday.
        self.assertFalse(is_regular_trading_hours(self.at(6, 6, 14, 0)))

    def test_rth_uses_eastern_time_not_utc_across_the_dst_change(self):
        """January is EST (UTC-5); 14:30 UTC is 09:30 ET, 14:29 UTC is not."""
        self.assertTrue(is_regular_trading_hours(self.at(1, 6, 14, 30)))
        self.assertFalse(is_regular_trading_hours(self.at(1, 6, 14, 29)))

    def test_premarket_execution_is_not_subject_to_rule_611(self):
        """Rule 600(b)(105) confines trade-throughs to regular trading hours."""
        premarket = datetime.datetime(2026, 6, 2, 12, 0, tzinfo=UTC)  # 08:00 ET
        res = self.engine.evaluate_execution(
            self.execution(price=105.00, execution_timestamp=premarket),
            [self.quote("NASDAQ", 100.00, 100.10, premarket - datetime.timedelta(seconds=5)),
             self.quote("NYSE", 99.95, 100.05, premarket - datetime.timedelta(seconds=5))],
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.NOT_SUBJECT_RULE_611)
        self.assertFalse(res.is_regular_trading_hours)

    def test_after_hours_execution_needs_no_quotes_at_all(self):
        after = datetime.datetime(2026, 6, 2, 21, 0, tzinfo=UTC)  # 17:00 ET
        res = self.engine.evaluate_execution(
            self.execution(price=105.00, execution_timestamp=after), []
        )
        self.assertEqual(res.status, Rule611Status.NOT_SUBJECT_RULE_611)


class TestFlickeringQuoteException(RegNMSTestBase):
    """Rule 611(b)(8): per-venue, strictly prior, equal-or-inferior price."""

    def test_exempt_when_the_traded_through_venue_showed_an_inferior_offer(self):
        quotes = [
            # NYSE showed $100.09 half a second before the print, then improved
            # to $100.05 at the moment of execution.
            self.quote("NYSE", 99.95, 100.09, self.now - datetime.timedelta(seconds=0.5)),
            self.quote("NYSE", 99.95, 100.05, self.now),
            self.quote("NASDAQ", 100.00, 100.10, self.stale),
        ]
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-FLICKER", price=100.08), quotes
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.EXEMPT_FLICKERING_QUOTE)

    def test_not_exempt_when_the_prior_quote_was_superior_to_the_print(self):
        """A prior offer *better* than the print does not support (b)(8)."""
        quotes = [
            self.quote("NYSE", 99.95, 100.02, self.now - datetime.timedelta(seconds=0.5)),
            self.quote("NYSE", 99.95, 100.05, self.now),
            self.quote("NASDAQ", 100.00, 100.10, self.stale),
        ]
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-NOFLICK", price=100.08), quotes
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.TRADE_THROUGH_VIOLATION)

    def test_not_exempt_when_the_inferior_quote_was_a_different_venue(self):
        """(b)(8) looks only at the venue whose quotation was traded through."""
        quotes = [
            self.quote("NYSE", 99.95, 100.05, self.stale),
            # MEMX flickered, but NYSE is the venue that was traded through.
            self.quote("MEMX", 99.90, 100.40, self.now - datetime.timedelta(seconds=0.5)),
            self.quote("MEMX", 99.90, 100.30, self.now),
            self.quote("NASDAQ", 100.00, 100.10, self.stale),
        ]
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-OTHERVENUE", price=100.08), quotes
        )
        self.assertEqual(res.status, Rule611Status.TRADE_THROUGH_VIOLATION)

    def test_not_exempt_when_the_inferior_quote_is_older_than_one_second(self):
        just_outside = self.now - FLICKERING_QUOTE_WINDOW - datetime.timedelta(milliseconds=1)
        quotes = [
            # NYSE showed $100.20 a hair over a second ago, then $100.05, which
            # is the quote in force at execution.
            self.quote("NYSE", 99.95, 100.20, just_outside),
            self.quote("NYSE", 99.95, 100.05, self.now - datetime.timedelta(milliseconds=900)),
            self.quote("NASDAQ", 100.00, 100.10, self.stale),
        ]
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-TOOOLD", price=100.08), quotes
        )
        self.assertEqual(res.status, Rule611Status.TRADE_THROUGH_VIOLATION)

    def test_exempt_at_exactly_one_second(self):
        """The window is inclusive at exactly one second prior."""
        quotes = [
            self.quote("NYSE", 99.95, 100.20, self.now - FLICKERING_QUOTE_WINDOW),
            self.quote("NYSE", 99.95, 100.05, self.now - datetime.timedelta(milliseconds=500)),
            self.quote("NASDAQ", 100.00, 100.10, self.stale),
        ]
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-EXACT1S", price=100.08), quotes
        )
        self.assertEqual(res.status, Rule611Status.EXEMPT_FLICKERING_QUOTE)

    def test_a_current_quote_stream_does_not_except_every_trade_through(self):
        """Regression: the exception must not swallow all surveillance.

        Every venue is quoting *as of* the execution instant, which is the
        normal state of a live book. Reading (b)(8) as "any quote changed
        within a second" makes an egregious 95 bps trade-through exempt.
        """
        quotes = [
            self.quote("NASDAQ", 100.00, 100.10, self.now),
            self.quote("NYSE", 99.95, 100.05, self.now),
        ]
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-EGREGIOUS", price=101.00), quotes
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.TRADE_THROUGH_VIOLATION)


class TestSelfHelpException(RegNMSTestBase):
    """Rule 611(b)(1), with the FAQ 4.07 notice element recorded."""

    def test_self_help_removes_the_venue_from_the_protected_nbbo(self):
        self.engine.declare_self_help(
            "NYSE", "Repeated IOC timeout", declared_at=self.stale, notice_sent=True
        )
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-SH", price=100.08), self.quotes
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.EXEMPT_SELF_HELP)
        self.assertEqual(res.self_help_venues, ("NYSE",))
        self.assertIn("NASDAQ", res.contributing_venues)

    def test_self_help_does_not_excuse_a_trade_through_of_another_venue(self):
        self.engine.declare_self_help(
            "NYSE", declared_at=self.stale, notice_sent=True
        )
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-SH-STILL-BAD", price=100.50), self.quotes
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.violating_venue_id, "NASDAQ")

    def test_declaration_is_evaluated_at_the_execution_time_not_now(self):
        """A replay of yesterday's tape must not depend on today's state."""
        declared = self.now - datetime.timedelta(minutes=10)
        revoked = self.now - datetime.timedelta(minutes=5)
        self.engine.declare_self_help("NYSE", declared_at=declared, notice_sent=True)
        self.engine.revoke_self_help("NYSE", revoked_at=revoked)

        during = self.execution(
            execution_id="E-DURING",
            price=100.08,
            execution_timestamp=declared + datetime.timedelta(minutes=1),
        )
        quotes = [
            self.quote("NASDAQ", 100.00, 100.10, declared),
            self.quote("NYSE", 99.95, 100.05, declared),
        ]
        self.assertEqual(
            self.engine.evaluate_execution(during, quotes).status,
            Rule611Status.EXEMPT_SELF_HELP,
        )

        after = self.execution(execution_id="E-AFTER", price=100.08)
        self.assertEqual(
            self.engine.evaluate_execution(after, self.quotes).status,
            Rule611Status.TRADE_THROUGH_VIOLATION,
        )

    def test_is_self_help_active_accepts_a_point_in_time(self):
        declared = self.now - datetime.timedelta(minutes=10)
        self.engine.declare_self_help("NYSE", declared_at=declared, notice_sent=True)
        self.assertFalse(
            self.engine.is_self_help_active("NYSE", declared - datetime.timedelta(seconds=1))
        )
        self.assertTrue(self.engine.is_self_help_active("NYSE", declared))
        self.assertTrue(self.engine.is_self_help_active("NYSE", self.now))

    def test_missing_notice_is_carried_into_the_audit_record(self):
        """FAQ 4.07 makes notice to the bypassed trading centre mandatory."""
        self.engine.declare_self_help("NYSE", declared_at=self.stale, notice_sent=False)
        res = self.engine.evaluate_execution(
            self.execution(price=100.08), self.quotes
        )
        self.assertEqual(res.status, Rule611Status.EXEMPT_SELF_HELP)
        self.assertIn("notice NOT recorded", res.exemption_reason)

    def test_revocation_before_declaration_raises(self):
        self.engine.declare_self_help("NYSE", declared_at=self.now, notice_sent=True)
        with self.assertRaises(RegNMSError):
            self.engine.revoke_self_help("NYSE", revoked_at=self.now - datetime.timedelta(hours=1))


class TestCrossedMarketException(RegNMSTestBase):
    """Rule 611(b)(4): a protected bid priced above a protected offer."""

    def test_crossed_protected_market_is_excepted(self):
        quotes = [
            self.quote("NASDAQ", 100.20, 100.30, self.stale),  # bid $100.20
            self.quote("NYSE", 99.95, 100.05, self.stale),     # offer $100.05
        ]
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-CROSS", price=100.10), quotes
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.EXEMPT_CROSSED_MARKET)

    def test_a_locked_market_is_not_crossed_and_is_not_excepted(self):
        """NBB == NBO is locked, not crossed; Rule 611(b)(4) does not apply."""
        quotes = [
            self.quote("NASDAQ", 100.05, 100.30, self.stale),
            self.quote("NYSE", 99.95, 100.05, self.stale),
        ]
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-LOCK", price=100.20), quotes
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.TRADE_THROUGH_VIOLATION)


class TestIsoException(RegNMSTestBase):
    """Rule 611(b)(5)/(6) with the Rule 600(b)(47)(ii) sweep obligation."""

    def test_iso_marking_alone_is_exempt_but_recorded_as_unverified(self):
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-ISO", price=100.30, is_iso_tagged=True),
            self.quotes,
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.EXEMPT_ISO)
        self.assertIn("NOT verified", res.exemption_reason)

    def test_iso_snapshot_is_retained_in_the_audit_record(self):
        """An ISO audit record with a zeroed NBBO is useless for CAT review."""
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-ISO-SNAP", price=100.30, is_iso_tagged=True),
            self.quotes,
        )
        self.assertAlmostEqual(res.protected_nbb, 100.00)
        self.assertAlmostEqual(res.protected_nbo, 100.05)
        self.assertEqual(res.quote_as_of, self.now)
        self.assertGreater(res.trade_through_bps, 0.0)

    def test_iso_that_did_not_trade_through_is_reported_compliant_not_exempt(self):
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-ISO-CLEAN", price=100.05, is_iso_tagged=True),
            self.quotes,
        )
        self.assertEqual(res.status, Rule611Status.COMPLIANT_NO_TRADE_THROUGH)

    def test_complete_sweep_substantiates_the_exception(self):
        """Buy ISO limit $100.30: NYSE $100.05 and NASDAQ $100.10 are superior."""
        res = self.engine.evaluate_execution(
            self.execution(
                execution_id="E-ISO-FULL",
                price=100.30,
                is_iso_tagged=True,
                iso_limit_price=100.30,
                iso_routed_venue_ids=("NYSE", "NASDAQ"),
            ),
            self.quotes,
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.EXEMPT_ISO)
        self.assertIn("2 protected quotation", res.exemption_reason)

    def test_incomplete_sweep_is_flagged(self):
        res = self.engine.evaluate_execution(
            self.execution(
                execution_id="E-ISO-PARTIAL",
                price=100.30,
                is_iso_tagged=True,
                iso_limit_price=100.30,
                iso_routed_venue_ids=("NYSE",),  # NASDAQ's $100.10 offer missed
            ),
            self.quotes,
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.ISO_SWEEP_NOT_SUBSTANTIATED)
        self.assertIn("NASDAQ", res.exemption_reason)

    def test_sell_iso_sweeps_superior_bids(self):
        """For a sell ISO, superior means a *higher* protected bid."""
        res = self.engine.evaluate_execution(
            self.execution(
                execution_id="E-ISO-SELL",
                side=OrderSide.SELL,
                price=99.50,
                is_iso_tagged=True,
                iso_limit_price=99.50,
                iso_routed_venue_ids=("NASDAQ",),  # NYSE's $99.95 bid missed
            ),
            self.quotes,
        )
        self.assertEqual(res.status, Rule611Status.ISO_SWEEP_NOT_SUBSTANTIATED)
        self.assertIn("NYSE", res.exemption_reason)

    def test_self_help_venue_need_not_be_swept(self):
        """Reg NMS FAQ 4.09 permits combining the ISO and Self-Help exceptions."""
        self.engine.declare_self_help(
            "NASDAQ", declared_at=self.stale, notice_sent=True
        )
        res = self.engine.evaluate_execution(
            self.execution(
                execution_id="E-ISO-SH",
                price=100.30,
                is_iso_tagged=True,
                iso_limit_price=100.30,
                iso_routed_venue_ids=("NYSE",),
            ),
            self.quotes,
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.EXEMPT_ISO)


class TestBenchmarkException(RegNMSTestBase):
    """Rule 611(b)(7) is a facts-and-circumstances test (FAQ 3.16)."""

    def test_benchmark_with_a_documented_reference(self):
        res = self.engine.evaluate_execution(
            self.execution(
                execution_id="E-VWAP",
                price=100.40,
                is_benchmark_vwap=True,
                benchmark_reference="Intraday VWAP 10:00-15:00 ET",
            ),
            self.quotes,
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.EXEMPT_BENCHMARK)
        self.assertIn("Intraday VWAP", res.exemption_reason)

    def test_benchmark_without_a_reference_is_flagged_unsubstantiated(self):
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-VWAP-BARE", price=100.40, is_benchmark_vwap=True),
            self.quotes,
        )
        self.assertEqual(res.status, Rule611Status.EXEMPT_BENCHMARK)
        self.assertIn("Unsubstantiated", res.exemption_reason)


class TestStoppedOrderException(RegNMSTestBase):
    """Rule 611(b)(9), including the (b)(9)(iii) underwater condition."""

    def test_stopped_buy_below_the_protected_bid_is_excepted(self):
        res = self.engine.evaluate_execution(
            self.execution(
                execution_id="E-STOP-BUY",
                side=OrderSide.BUY,
                price=99.80,
                is_stopped_order=True,
                stopped_order_customer_agreed=True,
            ),
            self.quotes,
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.EXEMPT_STOPPED_ORDER)

    def test_stopped_sell_above_the_protected_offer_is_excepted(self):
        res = self.engine.evaluate_execution(
            self.execution(
                execution_id="E-STOP-SELL",
                side=OrderSide.SELL,
                price=100.40,
                is_stopped_order=True,
                stopped_order_customer_agreed=True,
            ),
            self.quotes,
        )
        self.assertEqual(res.status, Rule611Status.EXEMPT_STOPPED_ORDER)

    def test_stopped_order_without_customer_agreement_is_not_excepted(self):
        res = self.engine.evaluate_execution(
            self.execution(
                execution_id="E-STOP-NOAGREE",
                price=99.80,
                is_stopped_order=True,
                stopped_order_customer_agreed=False,
            ),
            self.quotes,
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.TRADE_THROUGH_VIOLATION)

    def test_stopped_buy_that_is_not_underwater_is_not_excepted(self):
        """A stopped buy printed *above* the offer fails (b)(9)(iii)."""
        res = self.engine.evaluate_execution(
            self.execution(
                execution_id="E-STOP-ABOVEWATER",
                side=OrderSide.BUY,
                price=100.40,
                is_stopped_order=True,
                stopped_order_customer_agreed=True,
            ),
            self.quotes,
        )
        self.assertFalse(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.TRADE_THROUGH_VIOLATION)


class TestTransactionTypeExceptions(RegNMSTestBase):
    def test_not_regular_way(self):
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-NRW", price=100.40, is_regular_way=False),
            self.quotes,
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.EXEMPT_NOT_REGULAR_WAY)

    def test_single_priced_auction(self):
        res = self.engine.evaluate_execution(
            self.execution(
                execution_id="E-AUCTION", price=100.40, is_single_priced_auction=True
            ),
            self.quotes,
        )
        self.assertTrue(res.is_compliant)
        self.assertEqual(res.status, Rule611Status.EXEMPT_SINGLE_PRICED_AUCTION)


class TestInputValidation(RegNMSTestBase):
    def test_naive_timestamps_are_read_as_utc_rather_than_raising(self):
        naive_quotes = [
            self.quote("NASDAQ", 100.00, 100.10, self.stale.replace(tzinfo=None)),
            self.quote("NYSE", 99.95, 100.05, self.stale.replace(tzinfo=None)),
        ]
        res = self.engine.evaluate_execution(
            self.execution(price=100.08), naive_quotes
        )
        self.assertEqual(res.status, Rule611Status.TRADE_THROUGH_VIOLATION)

    def test_mixed_naive_and_aware_timestamps_do_not_raise_typeerror(self):
        mixed = [
            self.quote("NASDAQ", 100.00, 100.10, self.stale.replace(tzinfo=None)),
            self.quote("NYSE", 99.95, 100.05, self.stale),
        ]
        res = self.engine.evaluate_execution(self.execution(price=100.08), mixed)
        self.assertEqual(res.violating_venue_id, "NYSE")

    def test_nan_price_raises_rather_than_reporting_compliant(self):
        with self.assertRaises(RegNMSError):
            self.engine.evaluate_execution(
                self.execution(price=float("nan")), self.quotes
            )

    def test_infinite_price_raises(self):
        with self.assertRaises(RegNMSError):
            self.engine.evaluate_execution(
                self.execution(price=float("inf")), self.quotes
            )

    def test_negative_price_raises(self):
        with self.assertRaises(RegNMSError):
            self.engine.evaluate_execution(self.execution(price=-5.0), self.quotes)

    def test_non_positive_quantity_raises(self):
        with self.assertRaises(RegNMSError):
            self.engine.evaluate_execution(self.execution(quantity=0), self.quotes)

    def test_string_side_raises(self):
        with self.assertRaises(RegNMSError):
            self.engine.evaluate_execution(self.execution(side="BUY"), self.quotes)

    def test_quotes_for_a_different_symbol_raise_rather_than_being_used(self):
        foreign = [self.quote("NYSE", 5.00, 5.05, self.stale, symbol="F")]
        with self.assertRaises(RegNMSError):
            self.engine.evaluate_execution(self.execution(price=100.08), foreign)

    def test_venue_and_symbol_matching_is_case_insensitive(self):
        quotes = [
            self.quote("nasdaq", 100.00, 100.10, self.stale, symbol="aapl"),
            self.quote("nyse", 99.95, 100.05, self.stale, symbol="aapl"),
        ]
        res = self.engine.evaluate_execution(self.execution(price=100.08), quotes)
        self.assertEqual(res.violating_venue_id, "NYSE")


class TestAuditRecord(RegNMSTestBase):
    def test_audit_record_carries_the_reproducibility_fields(self):
        res = self.engine.evaluate_execution(
            self.execution(execution_id="E-AUDIT", price=100.08), self.quotes
        )
        self.assertEqual(res.execution_id, "E-AUDIT")
        self.assertEqual(res.symbol, "AAPL")
        self.assertEqual(res.side, OrderSide.BUY)
        self.assertEqual(res.quote_as_of, self.now)
        self.assertTrue(res.is_regular_trading_hours)
        self.assertEqual(res.contributing_venues, ("NASDAQ", "NYSE"))
        self.assertIsNotNone(res.audit_timestamp.tzinfo)


if __name__ == "__main__":
    unittest.main()

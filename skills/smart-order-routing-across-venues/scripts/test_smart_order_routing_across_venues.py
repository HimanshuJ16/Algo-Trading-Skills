import unittest

from smart_order_routing_across_venues import (
    SmartOrderRoutingAcrossVenuesConfig,
    SmartOrderRoutingAcrossVenuesEngine,
    VenueQuote, ChildOrderRoute, SORRoutingPlan,
    _price_to_ticks,
)


class TestSmartOrderRoutingLegacy(unittest.TestCase):
    def setUp(self):
        self.config = SmartOrderRoutingAcrossVenuesConfig(enabled=True, threshold=100.0, size=50)
        self.engine = SmartOrderRoutingAcrossVenuesEngine(self.config)

    def test_evaluate_triggers_order(self):
        market_data = {"symbol": "AAPL", "price": 105.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 1)
        self.assertEqual(orders[0]["qty"], 50)

    def test_evaluate_no_trigger(self):
        market_data = {"symbol": "AAPL", "price": 95.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 0)

    def test_disabled_engine(self):
        self.engine.config.enabled = False
        market_data = {"symbol": "AAPL", "price": 105.0}
        orders = self.engine.evaluate(market_data)
        self.assertEqual(len(orders), 0)


class TestSmartOrderRoutingEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = SmartOrderRoutingAcrossVenuesEngine()
        self.quotes = [
            VenueQuote("NASDAQ", bid_price=149.95, bid_qty=500, ask_price=150.00, ask_qty=300, taker_fee_per_share=0.0030),
            VenueQuote("BATS", bid_price=149.95, bid_qty=400, ask_price=150.00, ask_qty=400, taker_fee_per_share=0.0020),
            VenueQuote("NYSE", bid_price=149.90, bid_qty=1000, ask_price=150.05, ask_qty=1000, taker_fee_per_share=0.0030),
        ]

    def test_reg_nms_nbbo_consolidation_and_liquidity_slicing(self):
        # Parent order: BUY 600 shares of AAPL
        # NBBO Ask = $150.00 available at NASDAQ (300) and BATS (400).
        # Total NBBO liquidity = 700 shares. Order (600) fully satisfied at NBBO!
        plan = self.engine.route_parent_order(
            parent_order_id="PARENT_001",
            symbol="AAPL",
            side="BUY",
            quantity=600.0,
            venue_quotes=self.quotes
        )
        self.assertEqual(plan.nbbo_price, 150.00)
        self.assertEqual(plan.unrouted_quantity, 0.0)
        self.assertEqual(len(plan.routes), 2)
        # BATS has lower taker fee ($0.0020 vs $0.0030) -> Should route to BATS first (400 shares) then NASDAQ (200 shares)
        self.assertEqual(plan.routes[0].target_venue_id, "BATS")
        self.assertEqual(plan.routes[0].quantity, 400.0)
        self.assertEqual(plan.routes[1].target_venue_id, "NASDAQ")
        self.assertEqual(plan.routes[1].quantity, 200.0)
        # No remainder -> no ISO obligation is created by this plan.
        self.assertFalse(plan.iso_required_for_remainder)
        self.assertFalse(plan.locked_or_crossed)

    def test_unrouted_quantity_when_liquidity_exhausted(self):
        # BUY 2,000 shares -> Reg NMS NBBO ask ($150.00) liquidity = 700 shares (NASDAQ 300 + BATS 400)
        # 2,000 - 700 = 1,300 unrouted shares at NBBO level
        plan = self.engine.route_parent_order(
            parent_order_id="PARENT_002",
            symbol="AAPL",
            side="BUY",
            quantity=2000.0,
            venue_quotes=self.quotes
        )
        self.assertEqual(plan.unrouted_quantity, 1300.0)  # 2000 - 700 = 1300 unrouted at NBBO
        # NYSE quotes 150.05 with 1,000 shares but the router must NOT sweep into it:
        # that would trade through the $150.00 protected offers.
        self.assertNotIn("NYSE", [r.target_venue_id for r in plan.routes])
        self.assertTrue(plan.iso_required_for_remainder)
        self.assertIn("ISO-marked", plan.audit_notes)

    def test_expected_cost_and_fees_independently_derived(self):
        # BUY 600: 400 @ BATS $150.00 + $0.0020 fee = $150.0020 -> 400 * 150.0020 = 60,000.80
        #          200 @ NASDAQ $150.00 + $0.0030 fee = $150.0030 -> 200 * 150.0030 = 30,000.60
        # Total = 90,001.40 ; fees = 400*0.0020 + 200*0.0030 = 0.80 + 0.60 = 1.40
        plan = self.engine.route_parent_order(
            "PARENT_003", "AAPL", "BUY", 600.0, self.quotes)
        self.assertAlmostEqual(plan.net_expected_cost_usd, 90001.40, places=2)
        self.assertAlmostEqual(plan.total_taker_fee_usd, 1.40, places=4)
        self.assertAlmostEqual(plan.routes[0].taker_fee_usd, 0.80, places=4)
        self.assertAlmostEqual(plan.routes[1].taker_fee_usd, 0.60, places=4)

    def test_sell_side_routes_national_best_bid(self):
        # NBB = $149.95 at NASDAQ (500) and BATS (400) = 900 shares.
        # NASDAQ ranks first on the sell side too: net proceeds 149.95 - 0.0020 (BATS)
        # vs 149.95 - 0.0030 (NASDAQ) -> BATS keeps more, so BATS first.
        plan = self.engine.route_parent_order(
            "PARENT_004", "AAPL", "SELL", 900.0, self.quotes)
        self.assertEqual(plan.nbbo_price, 149.95)
        self.assertEqual(plan.routes[0].target_venue_id, "BATS")
        self.assertEqual(plan.routes[0].quantity, 400.0)
        self.assertEqual(plan.routes[1].target_venue_id, "NASDAQ")
        self.assertEqual(plan.routes[1].quantity, 500.0)
        self.assertEqual(plan.unrouted_quantity, 0.0)
        # Proceeds: 400 * (149.95 - 0.0020) + 500 * (149.95 - 0.0030)
        #         = 400 * 149.9480 + 500 * 149.9470 = 59,979.20 + 74,973.50 = 134,952.70
        self.assertAlmostEqual(plan.net_expected_cost_usd, 134952.70, places=2)
        # NYSE bids only 149.90 and must not be routed to.
        self.assertNotIn("NYSE", [r.target_venue_id for r in plan.routes])


class TestFloatEqualityRegression(unittest.TestCase):
    """Regression: identically-quoted venues must not be split by float noise.

    ``100.07`` reached via ``10007 / 100.0`` and via ``10007 * 0.01`` differ in
    the last bit. Exact float equality treated these as two price levels and
    dropped the second venue's displayed size, reporting it as unrouted -- which
    then invites the caller to fill it at an inferior price, trading through a
    protected quotation at the very price it just discarded.
    """

    def setUp(self):
        self.engine = SmartOrderRoutingAcrossVenuesEngine()
        self.price_a = 10007 / 100.0          # 100.07
        self.price_b = 10007 * 0.01           # 100.07000000000001
        self.assertNotEqual(self.price_a, self.price_b, "float paths must differ for this regression")

    def test_bitwise_different_but_identical_prices_are_one_level(self):
        quotes = [
            VenueQuote("NASDAQ", 100.00, 500, self.price_a, 300, taker_fee_per_share=0.0030),
            VenueQuote("BATS", 100.00, 400, self.price_b, 400, taker_fee_per_share=0.0020),
        ]
        plan = self.engine.route_parent_order("P", "AAPL", "BUY", 600.0, quotes)
        self.assertEqual(len(plan.routes), 2, "both venues quote 100.07 and must both be routed to")
        self.assertEqual(plan.unrouted_quantity, 0.0)
        self.assertEqual(
            {r.target_venue_id: r.quantity for r in plan.routes},
            {"BATS": 400.0, "NASDAQ": 200.0},
        )

    def test_price_to_ticks_quantizes_both_paths_identically(self):
        self.assertEqual(_price_to_ticks(self.price_a, 0.01), 10007)
        self.assertEqual(_price_to_ticks(self.price_b, 0.01), 10007)

    def test_sub_dollar_stock_needs_explicit_increment(self):
        # Rule 612 quotes sub-$1.00 NMS stocks in $0.0001. At the default $0.01
        # increment, $0.3401 and $0.3405 collapse onto the same tick and are
        # wrongly treated as one price level -- the router would then plan a
        # route at $0.3405 while $0.3401 is the real best offer. With the correct
        # increment they are two levels and only the better one is routed to.
        quotes = [
            VenueQuote("NASDAQ", 0.3300, 5000, 0.3401, 3000, taker_fee_per_share=0.0003),
            VenueQuote("BATS", 0.3300, 4000, 0.3405, 4000, taker_fee_per_share=0.0002),
        ]
        merged = self.engine.route_parent_order("P1", "PENNY", "BUY", 6000.0, quotes)
        self.assertEqual(len(merged.routes), 2, "a too-coarse increment merges distinct levels")
        self.assertIn(0.3405, [r.limit_price for r in merged.routes])

        correct = self.engine.route_parent_order(
            "P2", "PENNY", "BUY", 6000.0, quotes, price_increment=0.0001)
        self.assertEqual(len(correct.routes), 1)
        self.assertEqual(correct.routes[0].target_venue_id, "NASDAQ")
        self.assertEqual(correct.routes[0].limit_price, 0.3401)
        self.assertEqual(correct.unrouted_quantity, 3000.0)


class TestTradeThroughSafety(unittest.TestCase):
    """The router must never plan a route inferior to a better priced venue."""

    def setUp(self):
        self.engine = SmartOrderRoutingAcrossVenuesEngine()

    def test_zero_size_best_quote_does_not_authorize_routing_through_it(self):
        # Old behaviour: no venue at the $150.00 NBBO had size, so the fallback
        # sorted every venue by price and routed at $150.50 while still
        # reporting nbbo_price = $150.00.
        quotes = [
            VenueQuote("NASDAQ", 149.95, 500, 150.00, 0, taker_fee_per_share=0.0030),
            VenueQuote("NYSE", 149.90, 1000, 150.50, 1000, taker_fee_per_share=0.0030),
        ]
        with self.assertLogs("smart_order_routing_across_venues", level="WARNING") as captured:
            plan = self.engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)
        self.assertEqual(plan.best_quoted_price, 150.00)
        self.assertEqual(plan.nbbo_price, 150.50, "routable price excludes the zero-size quote")
        self.assertEqual(plan.routes[0].limit_price, 150.50)
        self.assertTrue(any("zero displayed size" in m for m in captured.output))

    def test_no_displayed_size_anywhere_routes_nothing(self):
        quotes = [
            VenueQuote("NASDAQ", 149.95, 500, 150.00, 0, taker_fee_per_share=0.0030),
            VenueQuote("BATS", 149.95, 400, 150.00, 0, taker_fee_per_share=0.0020),
        ]
        plan = self.engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)
        self.assertEqual(plan.routes, [])
        self.assertEqual(plan.unrouted_quantity, 100.0)
        self.assertEqual(plan.net_expected_cost_usd, 0.0)
        self.assertTrue(plan.iso_required_for_remainder)

    def test_buy_limit_price_blocks_routing_above_it(self):
        quotes = [VenueQuote("NASDAQ", 149.95, 500, 150.00, 300, taker_fee_per_share=0.0030)]
        blocked = self.engine.route_parent_order(
            "P1", "AAPL", "BUY", 100.0, quotes, limit_price=149.99)
        self.assertEqual(blocked.routes, [])
        self.assertEqual(blocked.unrouted_quantity, 100.0)

        allowed = self.engine.route_parent_order(
            "P2", "AAPL", "BUY", 100.0, quotes, limit_price=150.00)
        self.assertEqual(len(allowed.routes), 1, "limit exactly at the offer must still route")
        self.assertEqual(allowed.routes[0].quantity, 100.0)

    def test_off_grid_limit_never_rounds_into_permission_to_pay_more(self):
        # A buy limit of $149.996 is below the $150.00 offer. Nearest-tick
        # rounding would lift it to 15000 ticks and authorize the fill; the bound
        # must round down for a buy (and up for a sell) instead.
        quotes = [VenueQuote("NASDAQ", 149.95, 500, 150.00, 300, taker_fee_per_share=0.0030)]
        blocked = self.engine.route_parent_order(
            "P1", "AAPL", "BUY", 100.0, quotes, limit_price=149.996)
        self.assertEqual(blocked.routes, [], "must not pay 150.00 against a 149.996 limit")

        sell_blocked = self.engine.route_parent_order(
            "P2", "AAPL", "SELL", 100.0, quotes, limit_price=149.954)
        self.assertEqual(sell_blocked.routes, [], "must not sell at 149.95 against a 149.954 limit")

    def test_sell_limit_price_blocks_routing_below_it(self):
        quotes = [VenueQuote("NASDAQ", 149.95, 500, 150.00, 300, taker_fee_per_share=0.0030)]
        blocked = self.engine.route_parent_order(
            "P1", "AAPL", "SELL", 100.0, quotes, limit_price=150.00)
        self.assertEqual(blocked.routes, [])

        allowed = self.engine.route_parent_order(
            "P2", "AAPL", "SELL", 100.0, quotes, limit_price=149.95)
        self.assertEqual(len(allowed.routes), 1)

    def test_crossed_consolidated_book_is_flagged(self):
        # Venue A bids 150.10 while venue B offers 150.00 -> crossed across venues.
        quotes = [
            VenueQuote("A", 150.10, 500, 150.20, 500, taker_fee_per_share=0.0030),
            VenueQuote("B", 149.90, 500, 150.00, 500, taker_fee_per_share=0.0030),
        ]
        plan = self.engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)
        self.assertTrue(plan.locked_or_crossed)

    def test_locked_consolidated_book_is_flagged(self):
        quotes = [
            VenueQuote("A", 150.00, 500, 150.05, 500, taker_fee_per_share=0.0030),
            VenueQuote("B", 149.95, 500, 150.00, 500, taker_fee_per_share=0.0030),
        ]
        plan = self.engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)
        self.assertTrue(plan.locked_or_crossed)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = SmartOrderRoutingAcrossVenuesEngine()
        self.quotes = [
            VenueQuote("NASDAQ", 149.95, 500, 150.00, 300, taker_fee_per_share=0.0030),
        ]

    def test_empty_quote_list_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.route_parent_order("P", "AAPL", "BUY", 100.0, [])

    def test_unrecognized_side_rejected(self):
        # Previously any non-'BUY' string fell through to the sell path and
        # quoted the bid, silently inverting the order's intent.
        for bad_side in ("buy_limit", "B", "long", ""):
            with self.subTest(side=bad_side), self.assertRaises(ValueError):
                self.engine.route_parent_order("P", "AAPL", bad_side, 100.0, self.quotes)

    def test_side_is_case_and_whitespace_insensitive(self):
        plan = self.engine.route_parent_order("P", "AAPL", " buy ", 100.0, self.quotes)
        self.assertEqual(plan.side, "BUY")
        self.assertEqual(plan.nbbo_price, 150.00)

    def test_non_positive_quantity_rejected(self):
        for bad_qty in (0.0, -500.0):
            with self.subTest(qty=bad_qty), self.assertRaises(ValueError):
                self.engine.route_parent_order("P", "AAPL", "BUY", bad_qty, self.quotes)

    def test_nan_and_inf_quantity_rejected(self):
        for bad_qty in (float("nan"), float("inf")):
            with self.subTest(qty=bad_qty), self.assertRaises(ValueError):
                self.engine.route_parent_order("P", "AAPL", "BUY", bad_qty, self.quotes)

    def test_nan_price_rejected_rather_than_propagated(self):
        # min() over a list containing NaN returns NaN, which previously produced
        # a plan with a NaN NBBO and a NaN child limit price.
        quotes = [
            VenueQuote("A", 149.90, 500, float("nan"), 500, taker_fee_per_share=0.0030),
            VenueQuote("B", 149.90, 500, 150.00, 500, taker_fee_per_share=0.0030),
        ]
        with self.assertRaises(ValueError) as ctx:
            self.engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)
        self.assertIn("ask_price", str(ctx.exception))

    def test_negative_displayed_size_rejected(self):
        quotes = [VenueQuote("A", 149.95, 500, 150.00, -100, taker_fee_per_share=0.0030)]
        with self.assertRaises(ValueError):
            self.engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)

    def test_duplicate_venue_id_rejected(self):
        quotes = [
            VenueQuote("NASDAQ", 149.95, 500, 150.00, 300, taker_fee_per_share=0.0030),
            VenueQuote("NASDAQ", 149.95, 400, 150.00, 400, taker_fee_per_share=0.0020),
        ]
        with self.assertRaises(ValueError):
            self.engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)

    def test_self_crossed_venue_book_rejected(self):
        quotes = [VenueQuote("A", 150.10, 500, 150.00, 500, taker_fee_per_share=0.0030)]
        with self.assertRaises(ValueError):
            self.engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)

    def test_one_sided_venue_with_placeholder_price_is_accepted(self):
        # A venue showing no offer is legitimately expressed as ask_qty=0.
        quotes = [
            VenueQuote("DARK", 149.95, 500, 0.0, 0, taker_fee_per_share=0.0030),
            VenueQuote("BATS", 149.95, 400, 150.00, 400, taker_fee_per_share=0.0020),
        ]
        plan = self.engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)
        self.assertEqual(plan.nbbo_price, 150.00)
        self.assertEqual(plan.routes[0].target_venue_id, "BATS")

    def test_string_numerics_rejected_not_coerced(self):
        # An agent serializing quotes through JSON can hand back strings.
        # float("300") would validate and then blow up on "300" > 0 much later.
        quotes = [VenueQuote("A", 149.95, 500, 150.00, "300", taker_fee_per_share=0.0030)]
        with self.assertRaises(ValueError) as ctx:
            self.engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)
        self.assertIn("not a string", str(ctx.exception))

        with self.assertRaises(ValueError):
            self.engine.route_parent_order("P", "AAPL", "BUY", "100", self.quotes)

    def test_decimal_prices_are_accepted(self):
        # Decimal is a legitimate carrier for exchange prices and must survive
        # the string guard.
        from decimal import Decimal
        quotes = [VenueQuote("A", Decimal("149.95"), 500, Decimal("150.00"), 300,
                             taker_fee_per_share=0.0030)]
        plan = self.engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)
        self.assertEqual(len(plan.routes), 1)

    def test_non_positive_price_increment_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.route_parent_order(
                "P", "AAPL", "BUY", 100.0, self.quotes, price_increment=0.0)


class TestFeeSemantics(unittest.TestCase):

    def setUp(self):
        self.engine = SmartOrderRoutingAcrossVenuesEngine()
        self.quotes = [
            VenueQuote("NASDAQ", 149.95, 500, 150.00, 300, taker_fee_per_share=0.0030),
            VenueQuote("BATS", 149.95, 400, 150.00, 400, taker_fee_per_share=0.0020),
        ]

    def test_fee_aware_changes_ranking_only_not_reported_cost(self):
        # fee_aware=False drops the fee from the *ranking* key, so the venues fall
        # back to the latency/venue-id tiebreaker -- but the taker fee is still
        # paid and must still appear in the reported cost.
        aware = self.engine.route_parent_order(
            "P1", "AAPL", "BUY", 100.0, self.quotes, fee_aware=True)
        unaware = self.engine.route_parent_order(
            "P2", "AAPL", "BUY", 100.0, self.quotes, fee_aware=False)

        self.assertEqual(aware.routes[0].target_venue_id, "BATS")
        self.assertEqual(unaware.routes[0].target_venue_id, "BATS",
                         "equal latency -> deterministic venue_id tiebreaker")
        for plan in (aware, unaware):
            with self.subTest(plan=plan.parent_order_id):
                self.assertGreater(plan.total_taker_fee_usd, 0.0)
                self.assertAlmostEqual(
                    plan.routes[0].effective_net_price,
                    plan.routes[0].limit_price + 0.0020, places=6)

    def test_fee_above_access_fee_cap_is_warned_not_silently_accepted(self):
        quotes = [VenueQuote("OTC", 149.95, 500, 150.00, 300, taker_fee_per_share=0.0100)]
        with self.assertLogs("smart_order_routing_across_venues", level="WARNING") as captured:
            self.engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)
        self.assertTrue(any("access fee cap" in m for m in captured.output))

    def test_access_fee_cap_is_configurable(self):
        # The 2024 Reg NMS amendment lowers the cap to $0.0010/share once its
        # compliance date arrives; the threshold must not be hard-coded.
        config = SmartOrderRoutingAcrossVenuesConfig(access_fee_cap_per_share=0.0010)
        engine = SmartOrderRoutingAcrossVenuesEngine(config)
        quotes = [VenueQuote("NASDAQ", 149.95, 500, 150.00, 300, taker_fee_per_share=0.0030)]
        with self.assertLogs("smart_order_routing_across_venues", level="WARNING") as captured:
            engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)
        self.assertTrue(any("0.0010" in m for m in captured.output))


class TestDeterminism(unittest.TestCase):

    def test_identical_venues_produce_a_stable_ordering(self):
        engine = SmartOrderRoutingAcrossVenuesEngine()
        base = [
            VenueQuote("EDGX", 149.95, 400, 150.00, 200, taker_fee_per_share=0.0030, latency_ms=1.0),
            VenueQuote("BATS", 149.95, 400, 150.00, 200, taker_fee_per_share=0.0030, latency_ms=1.0),
            VenueQuote("IEX", 149.95, 400, 150.00, 200, taker_fee_per_share=0.0030, latency_ms=1.0),
        ]
        first = engine.route_parent_order("P1", "AAPL", "BUY", 600.0, base)
        shuffled = [base[2], base[0], base[1]]
        second = engine.route_parent_order("P2", "AAPL", "BUY", 600.0, shuffled)
        self.assertEqual(
            [r.target_venue_id for r in first.routes],
            [r.target_venue_id for r in second.routes],
            "venue ordering must not depend on input order",
        )
        self.assertEqual([r.target_venue_id for r in first.routes], ["BATS", "EDGX", "IEX"])

    def test_lower_latency_wins_when_price_and_fee_tie(self):
        engine = SmartOrderRoutingAcrossVenuesEngine()
        quotes = [
            VenueQuote("SLOW", 149.95, 400, 150.00, 200, taker_fee_per_share=0.0030, latency_ms=9.0),
            VenueQuote("FAST", 149.95, 400, 150.00, 200, taker_fee_per_share=0.0030, latency_ms=0.5),
        ]
        plan = engine.route_parent_order("P", "AAPL", "BUY", 100.0, quotes)
        self.assertEqual(plan.routes[0].target_venue_id, "FAST")


if __name__ == '__main__':
    # Do NOT disable logging here: several tests assert on the engine's warnings
    # via assertLogs, which a global logging.disable() would silently defeat.
    unittest.main()

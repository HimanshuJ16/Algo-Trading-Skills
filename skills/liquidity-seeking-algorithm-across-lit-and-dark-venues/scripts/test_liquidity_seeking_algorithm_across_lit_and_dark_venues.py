import unittest

from liquidity_seeking_algorithm_across_lit_and_dark_venues import (
    LiquiditySeekingEngine,
    LiquiditySeekingError,
    ParentOrderSpec,
    VenueBookSpec,
    SKIP_BELOW_MIN_DARK_QTY,
    SKIP_EXPECTED_FILL_BELOW_MIN_QTY,
    SKIP_LIMIT_THROUGH_MIDPOINT,
)


class TestLiquiditySeekingEngine(unittest.TestCase):

    def setUp(self):
        self.engine = LiquiditySeekingEngine()
        # Lit NBBO: $100.00 x $100.02 (Midpoint = $100.01)
        self.venues = [
            VenueBookSpec("NASDAQ", "LIT", bid_price=100.00, ask_price=100.02, bid_qty=5000, ask_qty=5000),
            VenueBookSpec("NYSE", "LIT", bid_price=100.00, ask_price=100.02, bid_qty=5000, ask_qty=5000),
            VenueBookSpec("DARK_ATS_ALPHA", "DARK", bid_price=100.01, ask_price=100.01, bid_qty=15000, ask_qty=15000, historical_fill_rate=0.80),
            VenueBookSpec("DARK_ATS_BETA", "DARK", bid_price=100.01, ask_price=100.01, bid_qty=5000, ask_qty=5000, historical_fill_rate=0.50)
        ]

    def test_successful_dark_and_lit_execution(self):
        # Target BUY 20,000 shares @ $100.05 limit
        # Dark Alpha (80% of 15k = 12,000 shares @ $100.01) -> $0.01 price improvement per share = $120.00
        # Dark Beta (50% of 5k = 2,500 shares @ $100.01) -> $0.01 price improvement per share = $25.00
        # Lit NASDAQ 5,000 + NYSE 500 (remaining 5,500 shares @ $100.02, no improvement at the touch)
        # Total Executed = 20,000 shares
        order = ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=20000, limit_price=100.05, min_dark_fill_qty=500)
        report = self.engine.execute_liquidity_seeking(order, self.venues)

        self.assertEqual(report.status, "LIQUIDITY_SEEKING_COMPLETE")
        self.assertEqual(report.total_executed_qty, 20000)
        self.assertEqual(report.dark_executed_qty, 14500) # 12,000 + 2,500
        self.assertEqual(report.lit_executed_qty, 5500)
        self.assertEqual(report.unfilled_qty, 0)
        self.assertEqual(report.nbbo_midpoint_price, 100.01)
        self.assertEqual(report.total_price_improvement_usd, 145.0)
        # Independently derived VWAP: (14,500 x 100.01 + 5,500 x 100.02) / 20,000
        self.assertAlmostEqual(report.average_fill_price, 100.01275, places=3)
        self.assertFalse(report.requires_iso_marking)

    def test_min_dark_quantity_anti_pinging(self):
        # Setting min_dark_fill_qty = 20,000 skips Dark ATS Alpha (15k) and Beta (5k) -> Direct Lit Execution!
        order = ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=10000, limit_price=100.05, min_dark_fill_qty=20000)
        report = self.engine.execute_liquidity_seeking(order, self.venues)

        self.assertEqual(report.status, "LIQUIDITY_SEEKING_COMPLETE")
        self.assertEqual(report.dark_executed_qty, 0)
        self.assertEqual(report.lit_executed_qty, 10000)
        self.assertEqual(report.total_price_improvement_usd, 0.0)

    # --- Rule 611 trade-through regression ------------------------------------

    def test_lit_sweep_takes_best_price_first_regardless_of_input_order(self):
        """A worse-priced venue listed first must not be swept before the NBO.

        Regression: iterating the venue list in input order executed 5,000
        shares at $100.05 while NASDAQ's protected offer of $100.02 was still
        displayed -- a trade-through of exactly the kind 17 CFR 242.611(a)
        addresses.
        """
        venues = [
            VenueBookSpec("EDGX_WIDE", "LIT", bid_price=99.98, ask_price=100.05, bid_qty=5000, ask_qty=5000),
            VenueBookSpec("NASDAQ", "LIT", bid_price=100.00, ask_price=100.02, bid_qty=5000, ask_qty=5000),
        ]
        order = ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=6000, limit_price=100.10, min_dark_fill_qty=500)
        report = self.engine.execute_liquidity_seeking(order, venues)

        self.assertEqual(len(report.child_routes), 2)
        first, second = report.child_routes

        # The protected offer is taken first, and taken in full -- which is the
        # precondition the ISO exception (Rule 611(b)(5)-(6)) requires.
        self.assertEqual(first.venue_id, "NASDAQ")
        self.assertEqual(first.price, 100.02)
        self.assertEqual(first.filled_quantity, 5000)
        self.assertFalse(first.requires_iso_marking)

        # Only the residual reaches the inferior price, and it is flagged.
        self.assertEqual(second.venue_id, "EDGX_WIDE")
        self.assertEqual(second.filled_quantity, 1000)
        self.assertTrue(second.requires_iso_marking)
        self.assertTrue(report.requires_iso_marking)
        self.assertIn("ISO MARKING REQUIRED", report.audit_notes)

        # 1,000 shares x ($100.02 - $100.05) = -$30.00 versus the protected offer.
        self.assertEqual(second.price_improvement_usd, -30.0)
        self.assertEqual(report.total_price_improvement_usd, -30.0)

    # --- Limit-price safety ---------------------------------------------------

    def test_sell_limit_above_midpoint_blocks_the_dark_stage(self):
        """A SELL must never fill below its limit at the midpoint.

        Regression: the limit check guarded only the BUY side, so this order
        filled 8,000 shares dark at $100.01 against a $100.02 limit.
        """
        order = ParentOrderSpec(symbol="AAPL", side="SELL", target_quantity=10000, limit_price=100.02, min_dark_fill_qty=500)
        report = self.engine.execute_liquidity_seeking(order, self.venues)

        self.assertEqual(report.dark_executed_qty, 0)
        self.assertEqual(report.lit_executed_qty, 0)
        self.assertEqual(report.unfilled_qty, 10000)
        self.assertEqual(report.status, "INSUFFICIENT_LIQUIDITY")
        self.assertIn(SKIP_LIMIT_THROUGH_MIDPOINT, report.dark_skip_reasons)
        for route in report.child_routes:
            self.assertGreaterEqual(route.price, order.limit_price)

    def test_buy_limit_below_midpoint_skips_dark_without_raising(self):
        order = ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=10000, limit_price=100.00, min_dark_fill_qty=500)
        report = self.engine.execute_liquidity_seeking(order, self.venues)

        self.assertEqual(report.dark_executed_qty, 0)
        self.assertEqual(report.status, "INSUFFICIENT_LIQUIDITY")
        self.assertIn(SKIP_LIMIT_THROUGH_MIDPOINT, report.dark_skip_reasons)

    def test_sell_side_price_improvement_is_measured_against_the_bid(self):
        # SELL 10,000 @ $99.50 limit. Dark Alpha 80% of 10,000 = 8,000 @ $100.01;
        # Dark Beta 50% of the 2,000 residual = 1,000 @ $100.01; NASDAQ 1,000 @ $100.00.
        # Improvement vs the National Best Bid: 9,000 x $0.01 = $90.00.
        order = ParentOrderSpec(symbol="AAPL", side="SELL", target_quantity=10000, limit_price=99.50, min_dark_fill_qty=500)
        report = self.engine.execute_liquidity_seeking(order, self.venues)

        self.assertEqual(report.status, "LIQUIDITY_SEEKING_COMPLETE")
        self.assertEqual(report.dark_executed_qty, 9000)
        self.assertEqual(report.lit_executed_qty, 1000)
        self.assertEqual(report.total_price_improvement_usd, 90.0)

    # --- Anti-pinging (MinQty) ------------------------------------------------

    def test_residual_below_min_dark_qty_is_not_pinged(self):
        """A 300-share residual must not be pinged into a deep pool.

        Regression: the floor was compared against the venue's available
        liquidity rather than the routed quantity, so a 300-share ping went out
        under a 500-share anti-pinging floor.
        """
        venues = [
            VenueBookSpec("NASDAQ", "LIT", bid_price=100.00, ask_price=100.02, bid_qty=5000, ask_qty=5000),
            VenueBookSpec("DARK_ALPHA", "DARK", bid_price=100.01, ask_price=100.01, bid_qty=9700, ask_qty=9700, historical_fill_rate=1.00),
            VenueBookSpec("DARK_BETA", "DARK", bid_price=100.01, ask_price=100.01, bid_qty=5000, ask_qty=5000, historical_fill_rate=0.90),
        ]
        order = ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=10000, limit_price=100.05, min_dark_fill_qty=500)
        report = self.engine.execute_liquidity_seeking(order, venues)

        self.assertEqual(report.dark_executed_qty, 9700)
        self.assertEqual(report.lit_executed_qty, 300)
        self.assertNotIn("DARK_BETA", [r.venue_id for r in report.child_routes])
        self.assertIn(f"DARK_BETA:{SKIP_BELOW_MIN_DARK_QTY}", report.dark_skip_reasons)

    def test_projected_fill_below_min_qty_is_no_fill(self):
        """IOC + MinQty(110) executes at least MinQty or not at all."""
        venues = [
            VenueBookSpec("NASDAQ", "LIT", bid_price=100.00, ask_price=100.02, bid_qty=5000, ask_qty=5000),
            VenueBookSpec("DARK_THIN", "DARK", bid_price=100.01, ask_price=100.01, bid_qty=5000, ask_qty=5000, historical_fill_rate=0.05),
        ]
        order = ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=5000, limit_price=100.05, min_dark_fill_qty=500)
        report = self.engine.execute_liquidity_seeking(order, venues)

        # 5,000 x 0.05 = 250 projected, below the 500-share floor -> no dark fill.
        self.assertEqual(report.dark_executed_qty, 0)
        self.assertIn(f"DARK_THIN:{SKIP_EXPECTED_FILL_BELOW_MIN_QTY}", report.dark_skip_reasons)

    def test_min_qty_instruction_never_exceeds_routed_quantity(self):
        order = ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=20000, limit_price=100.05, min_dark_fill_qty=500)
        report = self.engine.execute_liquidity_seeking(order, self.venues)

        dark_routes = [r for r in report.child_routes if r.venue_type == "DARK"]
        self.assertTrue(dark_routes)
        for route in dark_routes:
            self.assertLessEqual(route.min_qty_instruction, route.quantity)
            self.assertEqual(route.min_qty_instruction, 500)

    # --- Sub-penny (Rule 612) -------------------------------------------------

    def test_odd_cent_spread_midpoint_is_pegged_not_sent_as_a_limit(self):
        """A $0.01 spread midpoints to a half cent; Rule 612 bars accepting
        that as an order price, so the child must be a midpoint peg."""
        venues = [
            VenueBookSpec("NASDAQ", "LIT", bid_price=100.00, ask_price=100.01, bid_qty=5000, ask_qty=5000),
            VenueBookSpec("DARK_ALPHA", "DARK", bid_price=100.005, ask_price=100.005, bid_qty=15000, ask_qty=15000, historical_fill_rate=0.80),
        ]
        order = ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=5000, limit_price=100.05, min_dark_fill_qty=500)
        report = self.engine.execute_liquidity_seeking(order, venues)

        self.assertEqual(report.nbbo_midpoint_price, 100.005)
        dark_route = report.child_routes[0]
        self.assertEqual(dark_route.venue_type, "DARK")
        self.assertEqual(dark_route.price_instruction, "MIDPOINT_PEG")
        self.assertEqual(dark_route.filled_quantity, 4000)
        # 4,000 x ($100.01 - $100.005) = $20.00
        self.assertEqual(dark_route.price_improvement_usd, 20.0)
        # (4,000 x 100.005 + 1,000 x 100.01) / 5,000 = 100.006
        self.assertAlmostEqual(report.average_fill_price, 100.006, places=4)
        for route in report.child_routes:
            if route.venue_type == "LIT":
                self.assertEqual(route.price_instruction, "LIMIT")

    # --- NBBO integrity -------------------------------------------------------

    def test_zero_size_quote_does_not_set_the_touch(self):
        """A quote with no displayed size is not a quotation and must not
        skew the midpoint the dark stage prices against."""
        venues = [
            VenueBookSpec("GHOST", "LIT", bid_price=100.00, ask_price=100.00, bid_qty=0, ask_qty=0),
            VenueBookSpec("NASDAQ", "LIT", bid_price=100.00, ask_price=100.02, bid_qty=5000, ask_qty=5000),
        ]
        best_bid, best_ask, midpoint = self.engine.compute_nbbo(venues)

        self.assertEqual(best_bid, 100.00)
        self.assertEqual(best_ask, 100.02)
        self.assertEqual(midpoint, 100.01)

    def test_crossed_nbbo_is_rejected(self):
        venues = [
            VenueBookSpec("NASDAQ", "LIT", bid_price=100.05, ask_price=100.06, bid_qty=5000, ask_qty=5000),
            VenueBookSpec("NYSE", "LIT", bid_price=100.02, ask_price=100.03, bid_qty=5000, ask_qty=5000),
        ]
        with self.assertRaises(LiquiditySeekingError) as ctx:
            self.engine.compute_nbbo(venues)
        self.assertIn("Crossed NBBO", str(ctx.exception))

    def test_locked_nbbo_yields_no_dark_price_improvement(self):
        venues = [
            VenueBookSpec("NASDAQ", "LIT", bid_price=100.00, ask_price=100.00, bid_qty=5000, ask_qty=5000),
            VenueBookSpec("DARK_ALPHA", "DARK", bid_price=100.00, ask_price=100.00, bid_qty=15000, ask_qty=15000, historical_fill_rate=0.80),
        ]
        order = ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=5000, limit_price=100.05, min_dark_fill_qty=500)
        report = self.engine.execute_liquidity_seeking(order, venues)

        self.assertEqual(report.nbbo_midpoint_price, 100.00)
        self.assertEqual(report.total_price_improvement_usd, 0.0)

    def test_no_lit_venue_rejects(self):
        venues = [
            VenueBookSpec("DARK_ALPHA", "DARK", bid_price=100.01, ask_price=100.01, bid_qty=15000, ask_qty=15000),
        ]
        with self.assertRaises(LiquiditySeekingError):
            self.engine.compute_nbbo(venues)

    def test_duplicate_venue_id_rejects(self):
        venues = [
            VenueBookSpec("NASDAQ", "LIT", bid_price=100.00, ask_price=100.02, bid_qty=5000, ask_qty=5000),
            VenueBookSpec("NASDAQ", "LIT", bid_price=100.00, ask_price=100.02, bid_qty=5000, ask_qty=5000),
        ]
        with self.assertRaises(LiquiditySeekingError):
            self.engine.compute_nbbo(venues)

    # --- Input validation -----------------------------------------------------

    def test_fill_rate_above_one_is_rejected(self):
        """Regression: an unvalidated rate > 1.0 projected a fill larger than
        the routed quantity and over-filled the parent."""
        with self.assertRaises(LiquiditySeekingError):
            VenueBookSpec("DARK_BROKEN", "DARK", bid_price=100.01, ask_price=100.01,
                          bid_qty=15000, ask_qty=15000, historical_fill_rate=1.5)

    def test_unknown_side_is_rejected_not_defaulted_to_sell(self):
        """Regression: any side other than 'BUY' silently took the SELL branch."""
        with self.assertRaises(LiquiditySeekingError):
            ParentOrderSpec(symbol="AAPL", side="B", target_quantity=1000, limit_price=100.05)

    def test_side_is_normalised(self):
        order = ParentOrderSpec(symbol=" aapl ", side=" buy ", target_quantity=1000, limit_price=100.05)
        self.assertEqual(order.side, "BUY")
        self.assertEqual(order.symbol, "aapl")

    def test_invalid_quantities_and_prices_are_rejected(self):
        with self.assertRaises(LiquiditySeekingError):
            ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=0, limit_price=100.05)
        with self.assertRaises(LiquiditySeekingError):
            ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=1000, limit_price=0.0)
        with self.assertRaises(LiquiditySeekingError):
            ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=1000.5, limit_price=100.05)
        with self.assertRaises(LiquiditySeekingError):
            VenueBookSpec("NASDAQ", "LIT", bid_price=float("nan"), ask_price=100.02, bid_qty=5000, ask_qty=5000)
        with self.assertRaises(LiquiditySeekingError):
            VenueBookSpec("NASDAQ", "LIT", bid_price=100.03, ask_price=100.02, bid_qty=5000, ask_qty=5000)
        with self.assertRaises(LiquiditySeekingError):
            VenueBookSpec("NASDAQ", "MIXED", bid_price=100.00, ask_price=100.02, bid_qty=5000, ask_qty=5000)
        with self.assertRaises(LiquiditySeekingError):
            VenueBookSpec("NASDAQ", "LIT", bid_price=100.00, ask_price=100.02, bid_qty=-1, ask_qty=5000)

    # --- Invariants -----------------------------------------------------------

    def test_quantity_conservation_across_scenarios(self):
        scenarios = [
            ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=20000, limit_price=100.05, min_dark_fill_qty=500),
            ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=100000, limit_price=100.05, min_dark_fill_qty=500),
            ParentOrderSpec(symbol="AAPL", side="SELL", target_quantity=7331, limit_price=99.00, min_dark_fill_qty=100),
            ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=1, limit_price=100.05, min_dark_fill_qty=0),
        ]
        for order in scenarios:
            with self.subTest(side=order.side, qty=order.target_quantity):
                report = self.engine.execute_liquidity_seeking(order, self.venues)
                self.assertEqual(
                    report.total_executed_qty + report.unfilled_qty,
                    report.total_requested_qty,
                )
                self.assertGreaterEqual(report.unfilled_qty, 0)
                self.assertEqual(
                    report.total_executed_qty,
                    report.dark_executed_qty + report.lit_executed_qty,
                )
                self.assertEqual(
                    report.total_executed_qty,
                    sum(r.filled_quantity for r in report.child_routes),
                )

    def test_report_declares_its_fill_model(self):
        order = ParentOrderSpec(symbol="AAPL", side="BUY", target_quantity=20000, limit_price=100.05, min_dark_fill_qty=500)
        report = self.engine.execute_liquidity_seeking(order, self.venues)
        self.assertEqual(report.fill_model, "DETERMINISTIC_EXPECTED_FILL")
        self.assertEqual(report.nbbo_bid, 100.00)
        self.assertEqual(report.nbbo_ask, 100.02)


if __name__ == '__main__':
    unittest.main()

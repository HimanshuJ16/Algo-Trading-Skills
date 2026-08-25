import logging
import unittest

from historical_order_book_reconstruction_from_message_logs import (
    BUY,
    SELL,
    VIOLATION_DUPLICATE_ORDER_ID,
    VIOLATION_OVER_CANCEL,
    VIOLATION_OVER_EXECUTE,
    VIOLATION_TIMESTAMP_REGRESSION,
    VIOLATION_UNKNOWN_ORDER,
    BookIntegrityError,
    HistoricalOrderBookReconstructEngine,
    L3OrderMessage,
)

# The engine logs every integrity violation at WARNING; silence it so the
# expected-failure tests do not spam the test output.
logging.getLogger(
    "historical_order_book_reconstruction_from_message_logs").setLevel(logging.CRITICAL)


class TestBookReconstruction(unittest.TestCase):
    """Normal-path L3 -> L2 aggregation."""

    def setUp(self):
        self.engine = HistoricalOrderBookReconstructEngine(symbol="AAPL")

    def test_l3_message_replay_reconstructs_l2_book(self):
        """The SKILL.md Verification scenario, worked by hand.

        ADD BUY ID_1 100.00 x10, ADD BUY ID_2 100.00 x5 -> 15 @ 100.00.
        ADD SELL ID_3 101.00 x8. Partial CANCEL of 4 on ID_1 -> ID_1 holds 6,
        so the 100.00 bid level holds 6 + 5 = 11 across 2 orders.
        Mid = (100 + 101)/2 = 100.50; spread = 1.00.
        """
        self.engine.process_l3_message(L3OrderMessage.add("ID_1", BUY, 100.0, 10, 1000))
        self.engine.process_l3_message(L3OrderMessage.add("ID_2", BUY, 100.0, 5, 1001))
        self.engine.process_l3_message(L3OrderMessage.add("ID_3", SELL, 101.0, 8, 1002))
        self.engine.process_l3_message(L3OrderMessage.cancel("ID_1", 4, 1003))

        snapshot = self.engine.get_l2_reconstructed_snapshot(top_n_levels=5)

        self.assertEqual(snapshot.best_bid_price, 100.0)
        self.assertEqual(snapshot.best_bid_qty, 11)
        self.assertEqual(snapshot.best_ask_price, 101.0)
        self.assertEqual(snapshot.best_ask_qty, 8)
        self.assertEqual(snapshot.mid_price, 100.50)
        self.assertEqual(snapshot.spread, 1.0)
        self.assertFalse(snapshot.is_crossed_book)
        self.assertFalse(snapshot.is_locked_book)
        self.assertEqual(snapshot.total_active_l3_orders, 3)
        self.assertEqual(snapshot.integrity_violation_count, 0)
        # Order count at the touch is per-order, not per-share.
        self.assertEqual(snapshot.l2_bids[0].order_count, 2)
        self.assertEqual(self.engine.active_orders["ID_1"].quantity, 6)

    def test_legacy_positional_construction_still_works(self):
        """The original positional L3OrderMessage(...) form is unchanged."""
        self.engine.process_l3_message(L3OrderMessage("ID_1", "ADD", "BUY", 100.0, 10, 1000))
        self.engine.process_l3_message(L3OrderMessage("ID_1", "CANCEL", "BUY", 100.0, 4, 1001))
        snapshot = self.engine.get_l2_reconstructed_snapshot()
        self.assertEqual(snapshot.best_bid_qty, 6)

    def test_multi_level_depth_sorting_and_truncation(self):
        """Bids descend, asks ascend, and only top_n_levels are returned."""
        for i, price in enumerate([99.0, 100.0, 98.0, 97.0, 96.0, 95.0]):
            self.engine.process_l3_message(
                L3OrderMessage.add(f"B{i}", BUY, price, 10, 1000 + i))
        for i, price in enumerate([103.0, 101.0, 102.0, 104.0]):
            self.engine.process_l3_message(
                L3OrderMessage.add(f"S{i}", SELL, price, 7, 2000 + i))

        snapshot = self.engine.get_l2_reconstructed_snapshot(top_n_levels=3)

        self.assertEqual([lvl.price for lvl in snapshot.l2_bids], [100.0, 99.0, 98.0])
        self.assertEqual([lvl.price for lvl in snapshot.l2_asks], [101.0, 102.0, 103.0])
        self.assertEqual(snapshot.depth_levels_requested, 3)
        # Truncating the view must not drop orders from the L3 map.
        self.assertEqual(snapshot.total_active_l3_orders, 10)

    def test_empty_book_reports_no_bbo(self):
        snapshot = self.engine.get_l2_reconstructed_snapshot()
        self.assertIsNone(snapshot.best_bid_price)
        self.assertIsNone(snapshot.best_ask_price)
        self.assertIsNone(snapshot.mid_price)
        self.assertIsNone(snapshot.spread)
        self.assertFalse(snapshot.is_crossed_book)
        self.assertFalse(snapshot.is_locked_book)
        self.assertEqual(snapshot.best_bid_qty, 0)

    def test_one_sided_book_reports_no_mid_or_spread(self):
        self.engine.process_l3_message(L3OrderMessage.add("ID_1", BUY, 100.0, 10, 1000))
        snapshot = self.engine.get_l2_reconstructed_snapshot()
        self.assertEqual(snapshot.best_bid_price, 100.0)
        self.assertIsNone(snapshot.best_ask_price)
        self.assertIsNone(snapshot.mid_price)
        self.assertIsNone(snapshot.spread)

    def test_half_tick_mid_price_is_not_truncated(self):
        """Mid of two adjacent cents is a half-cent and must survive rounding."""
        self.engine.process_l3_message(L3OrderMessage.add("B", BUY, 100.01, 10, 1000))
        self.engine.process_l3_message(L3OrderMessage.add("S", SELL, 100.02, 10, 1001))
        snapshot = self.engine.get_l2_reconstructed_snapshot()
        self.assertEqual(snapshot.mid_price, 100.015)
        self.assertEqual(snapshot.spread, 0.01)


class TestExecuteCancelDelete(unittest.TestCase):
    """CANCEL / EXECUTE decrement; DELETE removes outright."""

    def setUp(self):
        self.engine = HistoricalOrderBookReconstructEngine(symbol="AAPL")
        self.engine.process_l3_message(L3OrderMessage.add("ID_1", BUY, 100.0, 10, 1000))

    def test_execute_decrements_remaining_shares(self):
        self.engine.process_l3_message(L3OrderMessage.execute("ID_1", 4, 1001))
        snapshot = self.engine.get_l2_reconstructed_snapshot()
        self.assertEqual(snapshot.best_bid_qty, 6)
        self.assertEqual(snapshot.total_active_l3_orders, 1)

    def test_cumulative_executions_remove_order_at_zero(self):
        """ITCH 5.0 4.4: modify effects are cumulative; at zero shares the order dies."""
        self.engine.process_l3_message(L3OrderMessage.execute("ID_1", 4, 1001))
        self.engine.process_l3_message(L3OrderMessage.execute("ID_1", 6, 1002))
        snapshot = self.engine.get_l2_reconstructed_snapshot()
        self.assertEqual(snapshot.total_active_l3_orders, 0)
        self.assertIsNone(snapshot.best_bid_price)
        # Reaching exactly zero is normal, not an integrity violation.
        self.assertEqual(snapshot.integrity_violation_count, 0)

    def test_delete_removes_whole_order_without_a_share_count(self):
        """ITCH 5.0 4.4.4 Order Delete carries no share field at all."""
        self.engine.process_l3_message(L3OrderMessage.delete("ID_1", 1001))
        snapshot = self.engine.get_l2_reconstructed_snapshot()
        self.assertEqual(snapshot.total_active_l3_orders, 0)
        self.assertIsNone(snapshot.best_bid_price)
        self.assertEqual(snapshot.integrity_violation_count, 0)

    def test_cancel_is_a_decrement_not_a_delete(self):
        """Regression: a CANCEL smaller than the resting size must not delete the order.

        Under the old conflated model a caller with only a DELETE to express had
        to send an oversized CANCEL; this asserts the two are now distinct.
        """
        self.engine.process_l3_message(L3OrderMessage.cancel("ID_1", 1, 1001))
        self.assertEqual(self.engine.active_orders["ID_1"].quantity, 9)
        self.assertEqual(self.engine.get_l2_reconstructed_snapshot().best_bid_qty, 9)

    def test_partial_cancel_of_one_order_leaves_level_order_count(self):
        self.engine.process_l3_message(L3OrderMessage.add("ID_2", BUY, 100.0, 5, 1001))
        self.engine.process_l3_message(L3OrderMessage.cancel("ID_1", 3, 1002))
        level = self.engine.get_l2_reconstructed_snapshot().l2_bids[0]
        self.assertEqual(level.total_quantity, 12)   # (10-3) + 5
        self.assertEqual(level.order_count, 2)


class TestReplaceSemantics(unittest.TestCase):
    """ITCH 5.0 4.4.5 Order Replace."""

    def setUp(self):
        self.engine = HistoricalOrderBookReconstructEngine(symbol="AAPL")
        self.engine.process_l3_message(L3OrderMessage.add("OLD", BUY, 100.0, 10, 1000))

    def test_replace_moves_order_to_new_reference_number(self):
        """"...a new order reference number which will be used henceforth."""
        self.engine.process_l3_message(
            L3OrderMessage.replace("OLD", "NEW", 99.0, 4, 1001))

        self.assertNotIn("OLD", self.engine.active_orders)
        self.assertIn("NEW", self.engine.active_orders)
        snapshot = self.engine.get_l2_reconstructed_snapshot()
        self.assertEqual(snapshot.best_bid_price, 99.0)
        self.assertEqual(snapshot.best_bid_qty, 4)
        self.assertEqual(snapshot.total_active_l3_orders, 1)

    def test_replace_quantity_is_absolute_not_a_decrement(self):
        """Shares on a Replace is "the new total displayed quantity"."""
        self.engine.process_l3_message(
            L3OrderMessage.replace("OLD", "NEW", 100.0, 25, 1001))
        self.assertEqual(self.engine.active_orders["NEW"].quantity, 25)

    def test_replace_inherits_side_from_the_original_order(self):
        """Side is not carried on a Replace and must come from the original ADD.

        Regression: a caller passing the wrong side previously flipped a bid
        onto the ask book and manufactured a crossed book out of one order.
        """
        msg = L3OrderMessage("OLD", "REPLACE", "SELL", 100.0, 10, 1001, new_order_id="NEW")
        self.engine.process_l3_message(msg)

        self.assertEqual(self.engine.active_orders["NEW"].side, BUY)
        snapshot = self.engine.get_l2_reconstructed_snapshot()
        self.assertEqual(snapshot.best_bid_price, 100.0)
        self.assertIsNone(snapshot.best_ask_price)
        self.assertFalse(snapshot.is_crossed_book)

    def test_replace_of_unknown_order_does_not_fabricate_depth(self):
        """Regression: the old engine created an order out of a replace for an
        id it had never seen, inventing book depth that never existed."""
        engine = HistoricalOrderBookReconstructEngine(symbol="AAPL")
        engine.process_l3_message(L3OrderMessage.replace("GHOST", "NEW", 100.0, 10, 1000))

        snapshot = engine.get_l2_reconstructed_snapshot()
        self.assertEqual(snapshot.total_active_l3_orders, 0)
        self.assertIsNone(snapshot.best_bid_price)
        self.assertEqual(engine.violations_by_kind.get(VIOLATION_UNKNOWN_ORDER), 1)

    def test_replace_without_new_id_updates_in_place(self):
        """Simplified logs that reuse the id are accepted, not flagged."""
        self.engine.process_l3_message(
            L3OrderMessage("OLD", "REPLACE", "", 101.0, 3, 1001))
        self.assertEqual(self.engine.active_orders["OLD"].quantity, 3)
        self.assertEqual(self.engine.get_l2_reconstructed_snapshot().best_bid_price, 101.0)
        self.assertEqual(self.engine.integrity_violation_count, 0)


class TestBookIntegrityDetection(unittest.TestCase):
    """Gaps and corruption in the log must be surfaced, never silently absorbed."""

    def setUp(self):
        self.engine = HistoricalOrderBookReconstructEngine(symbol="AAPL")

    def test_cancel_of_unknown_order_is_flagged(self):
        """Regression: previously a no-op, leaving the book silently wrong."""
        self.engine.process_l3_message(L3OrderMessage.cancel("GHOST", 5, 1000))
        self.assertEqual(self.engine.violations_by_kind.get(VIOLATION_UNKNOWN_ORDER), 1)
        self.assertEqual(
            self.engine.get_l2_reconstructed_snapshot().integrity_violation_count, 1)

    def test_delete_of_unknown_order_is_flagged(self):
        self.engine.process_l3_message(L3OrderMessage.delete("GHOST", 1000))
        self.assertEqual(self.engine.violations_by_kind.get(VIOLATION_UNKNOWN_ORDER), 1)

    def test_over_cancel_is_flagged_and_removes_the_order(self):
        self.engine.process_l3_message(L3OrderMessage.add("ID_1", BUY, 100.0, 10, 1000))
        self.engine.process_l3_message(L3OrderMessage.cancel("ID_1", 25, 1001))

        self.assertEqual(self.engine.violations_by_kind.get(VIOLATION_OVER_CANCEL), 1)
        self.assertEqual(self.engine.get_l2_reconstructed_snapshot().total_active_l3_orders, 0)

    def test_over_execute_is_flagged_separately_from_over_cancel(self):
        self.engine.process_l3_message(L3OrderMessage.add("ID_1", BUY, 100.0, 10, 1000))
        self.engine.process_l3_message(L3OrderMessage.execute("ID_1", 11, 1001))
        self.assertEqual(self.engine.violations_by_kind.get(VIOLATION_OVER_EXECUTE), 1)
        self.assertNotIn(VIOLATION_OVER_CANCEL, self.engine.violations_by_kind)

    def test_duplicate_add_is_flagged_and_does_not_double_count_depth(self):
        """Order reference numbers are day-unique (ITCH 5.0 4.3)."""
        self.engine.process_l3_message(L3OrderMessage.add("ID_1", BUY, 100.0, 10, 1000))
        self.engine.process_l3_message(L3OrderMessage.add("ID_1", BUY, 100.0, 7, 1001))

        self.assertEqual(self.engine.violations_by_kind.get(VIOLATION_DUPLICATE_ORDER_ID), 1)
        snapshot = self.engine.get_l2_reconstructed_snapshot()
        self.assertEqual(snapshot.total_active_l3_orders, 1)
        self.assertEqual(snapshot.best_bid_qty, 7)   # superseded, not 10 + 7

    def test_timestamp_regression_is_flagged(self):
        """Regression: timestamp_nanos was declared but never checked, so an
        out-of-order log replayed silently into a wrong book."""
        self.engine.process_l3_message(L3OrderMessage.add("ID_1", BUY, 100.0, 10, 5000))
        self.engine.process_l3_message(L3OrderMessage.add("ID_2", BUY, 99.0, 10, 4000))
        self.assertEqual(
            self.engine.violations_by_kind.get(VIOLATION_TIMESTAMP_REGRESSION), 1)

    def test_equal_timestamps_are_legal(self):
        """Multiple ITCH messages can share a nanosecond timestamp."""
        self.engine.process_l3_message(L3OrderMessage.add("ID_1", BUY, 100.0, 10, 5000))
        self.engine.process_l3_message(L3OrderMessage.add("ID_2", BUY, 99.0, 10, 5000))
        self.assertEqual(self.engine.integrity_violation_count, 0)

    def test_strict_mode_raises_on_first_violation(self):
        engine = HistoricalOrderBookReconstructEngine(symbol="AAPL", strict=True)
        with self.assertRaises(BookIntegrityError) as ctx:
            engine.process_l3_message(L3OrderMessage.cancel("GHOST", 5, 1000))
        self.assertEqual(ctx.exception.kind, VIOLATION_UNKNOWN_ORDER)

    def test_violation_records_are_capped_but_counts_are_exact(self):
        engine = HistoricalOrderBookReconstructEngine(
            symbol="AAPL", max_retained_violations=3)
        for i in range(10):
            engine.process_l3_message(L3OrderMessage.cancel(f"GHOST_{i}", 1, 1000 + i))
        self.assertEqual(len(engine.violations), 3)
        self.assertEqual(engine.integrity_violation_count, 10)


class TestCrossedAndLockedBooks(unittest.TestCase):

    def setUp(self):
        self.engine = HistoricalOrderBookReconstructEngine(symbol="AAPL")

    def test_crossed_book_detection(self):
        self.engine.process_l3_message(L3OrderMessage.add("ID_1", BUY, 102.0, 10, 1000))
        self.engine.process_l3_message(L3OrderMessage.add("ID_2", SELL, 101.0, 5, 1001))

        snapshot = self.engine.get_l2_reconstructed_snapshot()
        self.assertTrue(snapshot.is_crossed_book)
        self.assertFalse(snapshot.is_locked_book)
        # The spread is negative and the mid meaningless; both are still
        # populated, so consumers must gate on the flag.
        self.assertEqual(snapshot.spread, -1.0)

    def test_locked_book_is_not_reported_as_crossed(self):
        """bid == ask is locked, a distinct condition from crossed."""
        self.engine.process_l3_message(L3OrderMessage.add("ID_1", BUY, 100.0, 10, 1000))
        self.engine.process_l3_message(L3OrderMessage.add("ID_2", SELL, 100.0, 5, 1001))

        snapshot = self.engine.get_l2_reconstructed_snapshot()
        self.assertFalse(snapshot.is_crossed_book)
        self.assertTrue(snapshot.is_locked_book)
        self.assertEqual(snapshot.spread, 0.0)
        self.assertEqual(snapshot.mid_price, 100.0)


class TestPriceTickNormalisation(unittest.TestCase):

    def test_float_artifact_prices_aggregate_into_one_level(self):
        """Regression: float dict keys split one tick into two price levels.

        102.4 + 0.7 == 103.10000000000001 != 103.1, so two orders genuinely
        quoted at 103.10 arrived as different dict keys and the level's
        displayed depth was understated. Both map to tick 1031000.
        """
        engine = HistoricalOrderBookReconstructEngine(symbol="AAPL")
        self.assertNotEqual(102.4 + 0.7, 103.1)   # the hazard, made explicit

        engine.process_l3_message(L3OrderMessage.add("A", BUY, 102.4 + 0.7, 10, 1000))
        engine.process_l3_message(L3OrderMessage.add("B", BUY, 103.1, 5, 1001))

        snapshot = engine.get_l2_reconstructed_snapshot()
        self.assertEqual(len(snapshot.l2_bids), 1)
        self.assertEqual(snapshot.l2_bids[0].price, 103.1)
        self.assertEqual(snapshot.l2_bids[0].total_quantity, 15)
        self.assertEqual(snapshot.l2_bids[0].order_count, 2)

    def test_cent_scaled_engine_rejects_a_sub_tick_price(self):
        """A price finer than the configured scale is a unit error, not data."""
        engine = HistoricalOrderBookReconstructEngine(symbol="AAPL", price_scale=100)
        with self.assertRaises(ValueError):
            engine.process_l3_message(L3OrderMessage.add("A", BUY, 100.001, 10, 1000))

    def test_max_price_catches_raw_wire_integers(self):
        """A raw ITCH integer passed as a price reads as a plausible book 10,000x high."""
        engine = HistoricalOrderBookReconstructEngine(symbol="AAPL", max_price=200_000.0)
        engine.process_l3_message(L3OrderMessage.add("OK", BUY, 100.0, 10, 1000))
        with self.assertRaises(ValueError):
            # 1_000_000 is the raw Price(4) integer for $100.00.
            engine.process_l3_message(L3OrderMessage.add("BAD", BUY, 1_000_000, 10, 1001))

    def test_max_price_is_off_by_default(self):
        engine = HistoricalOrderBookReconstructEngine(symbol="AAPL")
        engine.process_l3_message(L3OrderMessage.add("BIG", BUY, 1_000_000, 10, 1000))
        self.assertEqual(engine.get_l2_reconstructed_snapshot().best_bid_price, 1_000_000.0)

    def test_invalid_max_price_raises(self):
        with self.assertRaises(ValueError):
            HistoricalOrderBookReconstructEngine(symbol="AAPL", max_price=-1.0)

    def test_price_scale_must_be_a_power_of_ten(self):
        with self.assertRaises(ValueError):
            HistoricalOrderBookReconstructEngine(symbol="AAPL", price_scale=1024)
        with self.assertRaises(ValueError):
            HistoricalOrderBookReconstructEngine(symbol="AAPL", price_scale=0)


class TestInputValidation(unittest.TestCase):
    """Malformed messages raise; they are not silently absorbed into the book."""

    def setUp(self):
        self.engine = HistoricalOrderBookReconstructEngine(symbol="AAPL")

    def test_unknown_message_type_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_l3_message(
                L3OrderMessage("ID_1", "AMEND", BUY, 100.0, 10, 1000))

    def test_invalid_side_raises_instead_of_landing_on_the_ask_book(self):
        """Regression: any side != "BUY" was silently treated as an ask, so a
        typo put a bid on the sell side and fabricated a crossed book."""
        for bad_side in ["B", "SEL", "", "BID"]:
            with self.subTest(side=bad_side):
                engine = HistoricalOrderBookReconstructEngine(symbol="AAPL")
                with self.assertRaises(ValueError):
                    engine.process_l3_message(
                        L3OrderMessage.add("ID_1", bad_side, 100.0, 10, 1000))

    def test_lowercase_side_and_message_type_are_accepted(self):
        self.engine.process_l3_message(L3OrderMessage("ID_1", "add", "buy", 100.0, 10, 1000))
        self.assertEqual(self.engine.get_l2_reconstructed_snapshot().best_bid_qty, 10)

    def test_non_positive_quantity_raises(self):
        for bad_qty in [0, -5]:
            with self.subTest(qty=bad_qty):
                with self.assertRaises(ValueError):
                    self.engine.process_l3_message(
                        L3OrderMessage.add("ID_1", BUY, 100.0, bad_qty, 1000))

    def test_non_finite_or_non_positive_price_raises(self):
        for bad_price in [float("nan"), float("inf"), 0.0, -100.0]:
            with self.subTest(price=bad_price):
                with self.assertRaises(ValueError):
                    self.engine.process_l3_message(
                        L3OrderMessage.add("ID_1", BUY, bad_price, 10, 1000))

    def test_empty_order_id_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_l3_message(L3OrderMessage.add("   ", BUY, 100.0, 10, 1000))

    def test_negative_timestamp_raises(self):
        with self.assertRaises(ValueError):
            self.engine.process_l3_message(L3OrderMessage.add("ID_1", BUY, 100.0, 10, -1))

    def test_invalid_top_n_levels_raises(self):
        with self.assertRaises(ValueError):
            self.engine.get_l2_reconstructed_snapshot(top_n_levels=0)
        with self.assertRaises(TypeError):
            self.engine.get_l2_reconstructed_snapshot(top_n_levels=2.5)


class TestLevelBookkeepingConsistency(unittest.TestCase):
    """The incremental L2 aggregate must always match a from-scratch rebuild."""

    def _rebuild_from_order_map(self, engine):
        """Independent reference aggregation straight off the L3 order map."""
        bids, asks = {}, {}
        for order in engine.active_orders.values():
            target = bids if order.side == BUY else asks
            qty, count = target.get(order.price_ticks, (0, 0))
            target[order.price_ticks] = (qty + order.quantity, count + 1)
        return bids, asks

    def test_incremental_aggregate_matches_full_rebuild_over_a_mixed_replay(self):
        engine = HistoricalOrderBookReconstructEngine(symbol="AAPL")
        ts = 1000
        for i in range(40):
            side = BUY if i % 2 == 0 else SELL
            price = 100.0 - (i % 5) * 0.01 if side == BUY else 100.10 + (i % 5) * 0.01
            engine.process_l3_message(L3OrderMessage.add(f"O{i}", side, price, 10 + i, ts))
            ts += 1
        for i in range(0, 40, 3):
            engine.process_l3_message(L3OrderMessage.cancel(f"O{i}", 3, ts)); ts += 1
        for i in range(1, 40, 5):
            engine.process_l3_message(L3OrderMessage.execute(f"O{i}", 2, ts)); ts += 1
        for i in range(2, 40, 7):
            engine.process_l3_message(L3OrderMessage.delete(f"O{i}", ts)); ts += 1
        for i in range(4, 40, 9):
            engine.process_l3_message(
                L3OrderMessage.replace(f"O{i}", f"R{i}", 100.05, 12, ts)); ts += 1

        self.assertEqual(engine.integrity_violation_count, 0)
        exp_bids, exp_asks = self._rebuild_from_order_map(engine)
        self.assertEqual(
            {t: tuple(v) for t, v in engine._bid_levels.items()}, exp_bids)
        self.assertEqual(
            {t: tuple(v) for t, v in engine._ask_levels.items()}, exp_asks)

    def test_emptying_a_level_removes_it_entirely(self):
        engine = HistoricalOrderBookReconstructEngine(symbol="AAPL")
        engine.process_l3_message(L3OrderMessage.add("A", BUY, 100.0, 10, 1000))
        engine.process_l3_message(L3OrderMessage.add("B", BUY, 99.0, 10, 1001))
        engine.process_l3_message(L3OrderMessage.delete("A", 1002))

        snapshot = engine.get_l2_reconstructed_snapshot()
        self.assertEqual([lvl.price for lvl in snapshot.l2_bids], [99.0])
        self.assertEqual(snapshot.best_bid_price, 99.0)


if __name__ == '__main__':
    unittest.main()

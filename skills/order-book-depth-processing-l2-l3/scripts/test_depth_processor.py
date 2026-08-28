"""Unit tests for the order-book-depth-processing-l2-l3 skill.

Expected metric values are derived by hand from the canonical definitions
rather than by re-running the implementation's own arithmetic:

  weighted mid  P_wmid = (V_ask * P_bid + V_bid * P_ask) / (V_bid + V_ask)
  depth imbalance   I  = (V_bid - V_ask) / (V_bid + V_ask)

Several tests are explicit regressions against defects that the previous
implementation exhibited; each is marked REGRESSION with the wrong value it
used to produce.
"""

import threading
import unittest

from depth_processor import (
    BookSnapshot,
    DepthMetrics,
    DepthProcessorError,
    L2L3DepthProcessor,
)


class TestL2DepthAndMetrics(unittest.TestCase):
    """Level 2 ingress, top-of-book, weighted mid-price and depth imbalance."""

    def setUp(self):
        self.processor = L2L3DepthProcessor(symbol="NIFTY")

    def _seed(self):
        self.assertTrue(
            self.processor.update_l2_depth(
                [(100.0, 10.0), (99.5, 20.0)],
                [(101.0, 5.0), (101.5, 15.0)],
            )
        )

    def test_top_of_book_and_spread(self):
        self._seed()
        metrics = self.processor.compute_metrics(depth_levels=2)
        self.assertFalse(metrics.is_crossed)
        self.assertEqual(metrics.best_bid, 100.0)
        self.assertEqual(metrics.best_ask, 101.0)
        self.assertEqual(metrics.mid_price, 100.5)
        self.assertEqual(metrics.spread, 1.0)

    def test_weighted_mid_price_matches_hand_calculation(self):
        # Touch volumes only: (100.0 * 5 + 101.0 * 10) / 15 = 1510 / 15
        self._seed()
        metrics = self.processor.compute_metrics(depth_levels=2)
        self.assertAlmostEqual(metrics.weighted_mid_price, 1510.0 / 15.0, places=12)

    def test_weighted_mid_leans_toward_the_heavier_side(self):
        # A bid-heavy touch must price above the arithmetic mid, and vice versa.
        self.processor.update_l2_depth([(100.0, 90.0)], [(101.0, 10.0)])
        self.assertGreater(
            self.processor.compute_metrics().weighted_mid_price, 100.5
        )
        flipped = L2L3DepthProcessor("NIFTY")
        flipped.update_l2_depth([(100.0, 10.0)], [(101.0, 90.0)])
        self.assertLess(flipped.compute_metrics().weighted_mid_price, 100.5)

    def test_imbalance_aggregation_depends_on_depth_levels(self):
        # depth 1: (10 - 5) / 15; depth 2: (30 - 20) / 50. The weighted mid is
        # unchanged because it is always a top-of-book quantity.
        self._seed()
        one = self.processor.compute_metrics(depth_levels=1)
        two = self.processor.compute_metrics(depth_levels=2)
        self.assertAlmostEqual(one.imbalance_ratio, 5.0 / 15.0, places=12)
        self.assertAlmostEqual(two.imbalance_ratio, 10.0 / 50.0, places=12)
        self.assertAlmostEqual(
            one.weighted_mid_price, two.weighted_mid_price, places=12
        )
        self.assertEqual((one.bid_levels, one.ask_levels), (1, 1))
        self.assertEqual((two.bid_levels, two.ask_levels), (2, 2))
        self.assertEqual(two.total_bid_volume, 30.0)
        self.assertEqual(two.total_ask_volume, 20.0)

    def test_asymmetric_depth_is_reported_not_hidden(self):
        # A thin side contributes fewer levels than requested. The ratio is
        # still meaningful, but the caller has to be able to see that it mixes
        # one bid level with three ask levels.
        self.processor.update_l2_depth(
            [(100.0, 10.0)], [(101.0, 2.0), (102.0, 3.0), (103.0, 5.0)]
        )
        metrics = self.processor.compute_metrics(depth_levels=3)
        self.assertEqual((metrics.bid_levels, metrics.ask_levels), (1, 3))
        self.assertEqual(metrics.total_bid_volume, 10.0)
        self.assertEqual(metrics.total_ask_volume, 10.0)
        self.assertEqual(metrics.imbalance_ratio, 0.0)

    def test_imbalance_is_zero_for_a_symmetric_book_and_stays_bounded(self):
        self.processor.update_l2_depth([(100.0, 7.0)], [(101.0, 7.0)])
        self.assertEqual(self.processor.compute_metrics().imbalance_ratio, 0.0)

        one_sided = L2L3DepthProcessor("NIFTY")
        one_sided.update_l2_depth([(100.0, 1000.0)], [(101.0, 1e-9)])
        ratio = one_sided.compute_metrics().imbalance_ratio
        self.assertLessEqual(ratio, 1.0)
        self.assertGreater(ratio, 0.999)

    def test_small_volumes_are_not_rescaled_by_an_epsilon_floor(self):
        # REGRESSION: denominators were clamped with max(volume, 1e-5), which
        # rescaled every metric for books quoted in fractions of a unit. This
        # book previously returned a weighted mid of 40.1 and an imbalance of
        # -0.2 instead of 100.25 and -0.5.
        self.processor.update_l2_depth([(100.0, 1e-6)], [(101.0, 3e-6)])
        metrics = self.processor.compute_metrics()
        self.assertAlmostEqual(metrics.weighted_mid_price, 100.25, places=9)
        self.assertAlmostEqual(metrics.imbalance_ratio, -0.5, places=12)
        self.assertGreater(metrics.weighted_mid_price, metrics.best_bid)
        self.assertLess(metrics.weighted_mid_price, metrics.best_ask)

    def test_zero_quantity_removes_the_price_level(self):
        self._seed()
        self.assertTrue(self.processor.update_l2_depth([(100.0, 0.0)], []))
        self.assertNotIn(100.0, self.processor.bids)
        self.assertEqual(self.processor.compute_metrics().best_bid, 99.5)

    def test_absolute_quantities_replace_rather_than_accumulate(self):
        self.processor.update_l2_depth([(100.0, 10.0)], [(101.0, 5.0)])
        self.processor.update_l2_depth([(100.0, 4.0)], [])
        self.assertEqual(self.processor.bids[100.0], 4.0)

    def test_metrics_require_both_sides_of_the_book(self):
        self.processor.update_l2_depth([(100.0, 10.0)], [])
        with self.assertRaises(DepthProcessorError):
            self.processor.compute_metrics()


class TestL2InputValidation(unittest.TestCase):
    """Malformed feed input must be rejected at ingress, not consumed."""

    def setUp(self):
        self.processor = L2L3DepthProcessor(symbol="NIFTY")

    def test_non_finite_price_is_rejected(self):
        # REGRESSION: a NaN price was accepted, and because `nan >= x` is False
        # it silently disabled the crossed-book guard while turning every
        # derived metric into NaN.
        for bad_price in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(price=bad_price):
                with self.assertRaises(DepthProcessorError):
                    self.processor.update_l2_depth([(bad_price, 5.0)], [])
        self.assertEqual(len(self.processor.bids), 0)

    def test_non_positive_price_is_rejected(self):
        for bad_price in (0.0, -100.0):
            with self.subTest(price=bad_price):
                with self.assertRaises(DepthProcessorError):
                    self.processor.update_l2_depth([], [(bad_price, 5.0)])

    def test_non_finite_size_is_rejected(self):
        with self.assertRaises(DepthProcessorError):
            self.processor.update_l2_depth([(100.0, float("nan"))], [])

    def test_negative_quantity_is_rejected_not_treated_as_a_delete(self):
        # REGRESSION: `qty <= 0` deleted the level, so a corrupt negative
        # quantity silently removed liquidity instead of failing.
        self.processor.update_l2_depth([(100.0, 10.0)], [])
        with self.assertRaises(DepthProcessorError):
            self.processor.update_l2_depth([(100.0, -10.0)], [])
        self.assertEqual(self.processor.bids[100.0], 10.0)

    def test_a_rejected_batch_leaves_the_book_untouched(self):
        self.processor.update_l2_depth([(100.0, 10.0)], [(101.0, 5.0)])
        with self.assertRaises(DepthProcessorError):
            self.processor.update_l2_depth(
                [(99.0, 1.0), (98.0, float("nan"))], [(102.0, 1.0)]
            )
        self.assertEqual(dict(self.processor.bids), {100.0: 10.0})
        self.assertEqual(dict(self.processor.asks), {101.0: 5.0})

    def test_malformed_update_rows_raise_a_typed_error(self):
        # A decoder emitting the wrong arity should not surface as a bare
        # unpacking ValueError from inside the ingress loop.
        for bad_row in ((100.0,), (100.0, 5.0, 1), 100.0, None):
            with self.subTest(row=bad_row):
                with self.assertRaises(DepthProcessorError):
                    self.processor.update_l2_depth([bad_row], [])

    def test_depth_levels_must_be_a_positive_int(self):
        # REGRESSION: 0 raised IndexError from inside the metrics path, and -1
        # silently dropped the last level via negative slicing, returning a
        # plausible but wrong imbalance.
        self.processor.update_l2_depth(
            [(100.0, 10.0), (99.0, 10.0)], [(101.0, 5.0), (102.0, 5.0)]
        )
        for bad in (0, -1, 1.5, "2", True, None):
            with self.subTest(depth_levels=bad):
                with self.assertRaises(DepthProcessorError):
                    self.processor.compute_metrics(depth_levels=bad)

    def test_symbol_must_be_a_non_empty_string(self):
        for bad in ("", "   ", None):
            with self.subTest(symbol=bad):
                with self.assertRaises(DepthProcessorError):
                    L2L3DepthProcessor(symbol=bad)


class TestCrossedAndLockedBooks(unittest.TestCase):
    """Crossed detection, reporting and the recovery path."""

    def setUp(self):
        self.processor = L2L3DepthProcessor(symbol="NIFTY")

    def test_crossed_update_returns_false_and_is_counted(self):
        self.assertFalse(
            self.processor.update_l2_depth([(102.0, 10.0)], [(101.0, 5.0)])
        )
        self.assertTrue(self.processor.is_crossed)
        self.assertEqual(self.processor.violations_by_kind["CROSSED_BOOK"], 1)

    def test_locked_book_bid_equals_ask_is_flagged(self):
        self.assertFalse(
            self.processor.update_l2_depth([(101.0, 10.0)], [(101.0, 5.0)])
        )
        metrics = self.processor.compute_metrics()
        self.assertTrue(metrics.is_crossed)
        self.assertEqual(metrics.spread, 0.0)

    def test_crossed_metrics_report_real_values_not_a_neutral_placeholder(self):
        # REGRESSION: a crossed book returned imbalance_ratio == 0.0, which a
        # caller that skipped the is_crossed flag would read as a balanced book.
        self.processor.update_l2_depth([(102.0, 10.0)], [(101.0, 5.0)])
        metrics = self.processor.compute_metrics()
        self.assertTrue(metrics.is_crossed)
        self.assertEqual(metrics.spread, -1.0)
        self.assertAlmostEqual(metrics.imbalance_ratio, 5.0 / 15.0, places=12)
        self.assertAlmostEqual(
            metrics.weighted_mid_price, (102.0 * 5.0 + 101.0 * 10.0) / 15.0, places=12
        )

    def test_crossing_update_is_kept_not_silently_dropped(self):
        # The offending update stays applied so the divergence is visible; the
        # documented recovery is reset() plus a fresh snapshot.
        self.processor.update_l2_depth([(100.0, 1.0)], [(101.0, 1.0)])
        self.processor.update_l2_depth([(102.0, 1.0)], [])
        self.assertIn(102.0, self.processor.bids)

    def test_reset_clears_the_book_but_keeps_violation_history(self):
        self.processor.update_l2_depth([(102.0, 10.0)], [(101.0, 5.0)])
        self.processor.reset()
        self.assertEqual(len(self.processor.bids), 0)
        self.assertEqual(len(self.processor.asks), 0)
        self.assertEqual(len(self.processor.l3_orders), 0)
        self.assertFalse(self.processor.is_crossed)
        self.assertEqual(self.processor.integrity_violation_count, 1)


class TestL3OrderLifecycle(unittest.TestCase):
    """Order-by-order ingress, aggregation and integrity accounting."""

    def setUp(self):
        self.processor = L2L3DepthProcessor(symbol="AAPL")

    def test_orders_aggregate_into_price_levels(self):
        self.processor.add_l3_order("ORD_1", "BUY", 100.0, 5.0)
        self.processor.add_l3_order("ORD_2", "BUY", 100.0, 10.0)
        self.processor.add_l3_order("ORD_3", "SELL", 101.0, 8.0)
        self.assertEqual(self.processor.bids[100.0], 15.0)
        self.assertEqual(self.processor.asks[101.0], 8.0)

        self.assertTrue(self.processor.cancel_l3_order("ORD_1"))
        self.assertEqual(self.processor.bids[100.0], 10.0)

    def test_cancelling_the_last_order_removes_the_level(self):
        self.processor.add_l3_order("ORD_1", "BUY", 100.0, 5.0)
        self.processor.cancel_l3_order("ORD_1")
        self.assertNotIn(100.0, self.processor.bids)
        self.assertNotIn("ORD_1", self.processor.l3_orders)

    def test_venue_side_tokens_route_to_the_correct_book(self):
        # REGRESSION: any side that was not exactly "BUY" fell through to the
        # ask book, so an ITCH 'B' or a Coinbase 'buy' rested a bid on the
        # offer side without raising.
        for i, token in enumerate(("BUY", "buy", "B", "b", "Bid")):
            with self.subTest(side=token):
                book = L2L3DepthProcessor("AAPL")
                book.add_l3_order(f"O{i}", token, 100.0, 1.0)
                self.assertEqual(dict(book.bids), {100.0: 1.0})
                self.assertEqual(dict(book.asks), {})
        for i, token in enumerate(("SELL", "sell", "S", "s", "Ask")):
            with self.subTest(side=token):
                book = L2L3DepthProcessor("AAPL")
                book.add_l3_order(f"O{i}", token, 101.0, 1.0)
                self.assertEqual(dict(book.asks), {101.0: 1.0})
                self.assertEqual(dict(book.bids), {})

    def test_unrecognised_side_is_rejected(self):
        for bad in ("LONG", "", "X", 1, None):
            with self.subTest(side=bad):
                with self.assertRaises(DepthProcessorError):
                    self.processor.add_l3_order("ORD_X", bad, 100.0, 1.0)
        self.assertEqual(len(self.processor.l3_orders), 0)

    def test_duplicate_order_id_is_rejected_and_counted(self):
        # REGRESSION: a repeated id added its size a second time (5 + 7 = 12),
        # and the eventual cancel could only deduct one order's worth, stranding
        # 5.0 of phantom liquidity at that level for the rest of the session.
        self.assertTrue(self.processor.add_l3_order("ORD_1", "BUY", 100.0, 5.0))
        self.assertFalse(self.processor.add_l3_order("ORD_1", "BUY", 100.0, 7.0))
        self.assertEqual(self.processor.bids[100.0], 5.0)
        self.assertEqual(self.processor.violations_by_kind["DUPLICATE_ORDER_ID"], 1)

        self.processor.cancel_l3_order("ORD_1")
        self.assertNotIn(100.0, self.processor.bids)

    def test_cancel_for_an_unknown_order_is_counted_not_absorbed(self):
        self.assertFalse(self.processor.cancel_l3_order("NEVER_ADDED"))
        self.assertEqual(self.processor.violations_by_kind["UNKNOWN_ORDER"], 1)

    def test_partial_then_full_execution(self):
        self.processor.add_l3_order("ORD_1", "SELL", 101.0, 10.0)
        self.assertTrue(self.processor.execute_l3_order("ORD_1", 4.0))
        self.assertEqual(self.processor.asks[101.0], 6.0)
        self.assertEqual(self.processor.l3_orders["ORD_1"][2], 6.0)

        self.assertTrue(self.processor.execute_l3_order("ORD_1", 6.0))
        self.assertNotIn(101.0, self.processor.asks)
        self.assertNotIn("ORD_1", self.processor.l3_orders)
        self.assertEqual(self.processor.integrity_violation_count, 0)

    def test_over_execution_is_flagged_rather_than_clamped(self):
        self.processor.add_l3_order("ORD_1", "BUY", 100.0, 3.0)
        self.assertFalse(self.processor.execute_l3_order("ORD_1", 9.0))
        self.assertEqual(self.processor.violations_by_kind["OVER_EXECUTE"], 1)
        self.assertNotIn(100.0, self.processor.bids)
        self.assertNotIn("ORD_1", self.processor.l3_orders)

    def test_execution_for_an_unknown_order_is_counted(self):
        self.assertFalse(self.processor.execute_l3_order("NEVER_ADDED", 1.0))
        self.assertEqual(self.processor.violations_by_kind["UNKNOWN_ORDER"], 1)

    def test_modify_sets_an_absolute_size(self):
        self.processor.add_l3_order("ORD_1", "BUY", 100.0, 10.0)
        self.processor.add_l3_order("ORD_2", "BUY", 100.0, 5.0)
        self.assertTrue(self.processor.modify_l3_order("ORD_1", 2.0))
        self.assertEqual(self.processor.bids[100.0], 7.0)
        self.assertEqual(self.processor.l3_orders["ORD_1"][2], 2.0)

    def test_modify_for_an_unknown_order_is_counted(self):
        self.assertFalse(self.processor.modify_l3_order("NEVER_ADDED", 1.0))
        self.assertEqual(self.processor.violations_by_kind["UNKNOWN_ORDER"], 1)

    def test_l3_rejects_malformed_price_and_size(self):
        for price, size in ((float("nan"), 1.0), (0.0, 1.0), (100.0, 0.0), (100.0, -1.0)):
            with self.subTest(price=price, size=size):
                with self.assertRaises(DepthProcessorError):
                    self.processor.add_l3_order("ORD_X", "BUY", price, size)
        self.assertEqual(len(self.processor.l3_orders), 0)

    def test_l3_aggregate_feeds_the_same_metrics_path(self):
        self.processor.add_l3_order("B1", "B", 100.0, 6.0)
        self.processor.add_l3_order("B2", "B", 100.0, 4.0)
        self.processor.add_l3_order("S1", "S", 101.0, 5.0)
        metrics = self.processor.compute_metrics(depth_levels=1)
        self.assertAlmostEqual(
            metrics.weighted_mid_price, (100.0 * 5.0 + 101.0 * 10.0) / 15.0, places=12
        )
        self.assertAlmostEqual(metrics.imbalance_ratio, 5.0 / 15.0, places=12)


class TestStateExposureAndSnapshots(unittest.TestCase):
    """The thread-safety guarantee has to hold at the API boundary too."""

    def setUp(self):
        self.processor = L2L3DepthProcessor(symbol="NIFTY")
        self.processor.update_l2_depth(
            [(100.0, 10.0), (99.0, 20.0), (98.0, 30.0)],
            [(101.0, 5.0), (102.0, 15.0)],
        )

    def test_book_views_cannot_be_mutated_from_outside_the_lock(self):
        # REGRESSION: bids/asks were plain dicts, so any caller could write to
        # the book without holding the mutex the whole design rests on.
        for view in (self.processor.bids, self.processor.asks, self.processor.l3_orders):
            with self.subTest(view=type(view)):
                with self.assertRaises(TypeError):
                    view[123.0] = 1.0

    def test_snapshot_is_ordered_bounded_and_immutable(self):
        snapshot = self.processor.get_snapshot(depth_levels=2)
        self.assertIsInstance(snapshot, BookSnapshot)
        self.assertEqual(snapshot.bids, ((100.0, 10.0), (99.0, 20.0)))
        self.assertEqual(snapshot.asks, ((101.0, 5.0), (102.0, 15.0)))
        self.assertFalse(snapshot.is_crossed)
        with self.assertRaises(AttributeError):
            snapshot.symbol = "OTHER"

    def test_snapshot_does_not_track_later_mutations(self):
        snapshot = self.processor.get_snapshot(depth_levels=1)
        self.processor.update_l2_depth([(100.0, 0.0)], [])
        self.assertEqual(snapshot.bids, ((100.0, 10.0),))

    def test_snapshot_validates_depth_levels(self):
        with self.assertRaises(DepthProcessorError):
            self.processor.get_snapshot(depth_levels=0)

    def test_metrics_are_immutable(self):
        metrics = self.processor.compute_metrics()
        self.assertIsInstance(metrics, DepthMetrics)
        with self.assertRaises(AttributeError):
            metrics.imbalance_ratio = 1.0


class TestConcurrency(unittest.TestCase):
    """Concurrent producers must not tear the aggregate or the order map."""

    def test_parallel_add_and_cancel_leaves_a_consistent_book(self):
        processor = L2L3DepthProcessor(symbol="AAPL")
        orders_per_thread = 200
        threads = []
        errors = []

        def churn(thread_id):
            try:
                for i in range(orders_per_thread):
                    order_id = f"T{thread_id}_{i}"
                    side = "B" if i % 2 == 0 else "S"
                    price = 100.0 if side == "B" else 101.0
                    processor.add_l3_order(order_id, side, price, 1.0)
                    processor.get_snapshot(depth_levels=2)
                    processor.cancel_l3_order(order_id)
            except Exception as exc:  # surfaced below rather than lost in a thread
                errors.append(exc)

        for thread_id in range(8):
            thread = threading.Thread(target=churn, args=(thread_id,))
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(processor.l3_orders), 0)
        self.assertEqual(dict(processor.bids), {})
        self.assertEqual(dict(processor.asks), {})
        self.assertEqual(processor.integrity_violation_count, 0)


if __name__ == "__main__":
    unittest.main()

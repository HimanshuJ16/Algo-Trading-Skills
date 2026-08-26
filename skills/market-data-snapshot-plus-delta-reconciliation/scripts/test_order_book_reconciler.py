"""
Unit tests for market-data-snapshot-plus-delta-reconciliation skill.

Tests:
1. Buffering deltas prior to snapshot application.
2. Snapshot initialization and discarding stale buffered deltas.
3. Applying sequential delta updates and price level deletions (zero-qty).
4. Detecting sequence gaps and marking book state as CORRUPT.
5. Rejecting a snapshot that predates the buffered delta stream.
6. Detecting a sequence gap inside the buffered delta stream.
7. Buffering deltas during CORRUPT state and completing an end-to-end re-sync.
8. Bounding the delta buffer and rejecting malformed price levels / sequence IDs.
9. Crossed-book detection as a desynchronization signal.
"""
import logging
import unittest
from order_book_reconciler import BookState, DeltaUpdate, OrderBookError, OrderBookReconciler

# Sequence-gap and corruption paths log at CRITICAL by design; attaching a NullHandler
# keeps the expected-failure tests from spraying logging's lastResort output on stderr.
logging.getLogger("order_book_reconciler").addHandler(logging.NullHandler())


class TestOrderBookReconciler(unittest.TestCase):

    def setUp(self):
        self.reconciler = OrderBookReconciler(symbol="BTCUSDT")

    def test_snapshot_application_and_top_of_book(self):
        snapshot_bids = [(100.0, 1.5), (99.0, 2.0)]
        snapshot_asks = [(101.0, 1.0), (102.0, 3.0)]

        self.reconciler.apply_snapshot(last_update_id=1000, snapshot_bids=snapshot_bids, snapshot_asks=snapshot_asks)

        self.assertEqual(self.reconciler.state, BookState.SYNCHRONIZED)
        top_bid, top_ask = self.reconciler.get_top_of_book()

        self.assertEqual(top_bid, (100.0, 1.5))
        self.assertEqual(top_ask, (101.0, 1.0))

    def test_sequential_delta_updates_and_deletion(self):
        snapshot_bids = [(100.0, 1.5)]
        snapshot_asks = [(101.0, 1.0)]
        self.reconciler.apply_snapshot(1000, snapshot_bids, snapshot_asks)

        # Delta 1001: Delete ask at 101.0 (qty=0), Add new ask at 100.5 (qty=2.0)
        delta1 = DeltaUpdate(
            first_update_id=1001,
            final_update_id=1001,
            bids=[],
            asks=[(101.0, 0.0), (100.5, 2.0)],
        )

        self.reconciler.process_delta(delta1)
        top_bid, top_ask = self.reconciler.get_top_of_book()

        self.assertEqual(top_ask, (100.5, 2.0))
        self.assertNotIn(101.0, self.reconciler.asks)

    def test_stale_delta_discard_during_reconciliation(self):
        # Buffer stale delta 900 (before snapshot 1000)
        self.reconciler.buffer_delta(DeltaUpdate(900, 900, [(98.0, 1.0)], []))
        # Buffer fresh delta 1001 (after snapshot 1000)
        self.reconciler.buffer_delta(DeltaUpdate(1001, 1001, [(100.5, 0.5)], []))

        self.reconciler.apply_snapshot(1000, [(100.0, 1.0)], [(101.0, 1.0)])

        self.assertEqual(self.reconciler.last_sequence_id, 1001)
        self.assertNotIn(98.0, self.reconciler.bids)
        self.assertIn(100.5, self.reconciler.bids)

    def test_sequence_gap_triggers_corrupt_state(self):
        self.reconciler.apply_snapshot(1000, [(100.0, 1.0)], [(101.0, 1.0)])

        # Inject sequence gap (expected 1001, got 1005)
        gap_delta = DeltaUpdate(1005, 1005, [(100.0, 2.0)], [])

        with self.assertRaises(OrderBookError):
            self.reconciler.process_delta(gap_delta)

        self.assertEqual(self.reconciler.state, BookState.CORRUPT)

    def test_gap_clears_book_so_stale_depth_is_unreadable(self):
        """A CORRUPT book must expose no depth at all, not the pre-gap levels."""
        self.reconciler.apply_snapshot(1000, [(100.0, 1.0)], [(101.0, 1.0)])

        with self.assertRaises(OrderBookError):
            self.reconciler.process_delta(DeltaUpdate(1005, 1005, [(100.0, 2.0)], []))

        self.assertEqual(self.reconciler.bids, {})
        self.assertEqual(self.reconciler.asks, {})
        self.assertEqual(self.reconciler.get_top_of_book(), (None, None))
        # No book version survives corruption, so none can be misread as live.
        self.assertEqual(self.reconciler.last_sequence_id, -1)

    def test_delta_straddling_snapshot_boundary_is_applied_in_full(self):
        """An event with U <= lastUpdateId+1 <= u is the first valid event, not a stale one."""
        self.reconciler.buffer_delta(DeltaUpdate(999, 1002, [(100.5, 0.5)], []))

        self.reconciler.apply_snapshot(1000, [(100.0, 1.0)], [(101.0, 1.0)])

        self.assertEqual(self.reconciler.state, BookState.SYNCHRONIZED)
        self.assertEqual(self.reconciler.last_sequence_id, 1002)
        self.assertEqual(self.reconciler.bids[100.5], 0.5)

    def test_snapshot_older_than_buffered_stream_is_rejected(self):
        """Snapshot lastUpdateId=1000 but the stream starts at 1010 -- events 1001..1009
        were never seen by either source, so the book would have a silent hole."""
        self.reconciler.buffer_delta(DeltaUpdate(1010, 1011, [(100.5, 0.5)], []))

        with self.assertRaises(OrderBookError):
            self.reconciler.apply_snapshot(1000, [(100.0, 1.0)], [(101.0, 1.0)])

        self.assertEqual(self.reconciler.state, BookState.CORRUPT)
        self.assertEqual(self.reconciler.bids, {})
        # The rejected snapshot's own last_update_id must not survive as the book version.
        self.assertEqual(self.reconciler.last_sequence_id, -1)
        # Buffer is retained: recovery is a fresher snapshot, not a resubscribe.
        self.assertEqual(len(self.reconciler.delta_buffer), 1)

    def test_fresher_snapshot_recovers_from_stale_snapshot_rejection(self):
        self.reconciler.buffer_delta(DeltaUpdate(1010, 1011, [(100.5, 0.5)], []))

        with self.assertRaises(OrderBookError):
            self.reconciler.apply_snapshot(1000, [(100.0, 1.0)], [(101.0, 1.0)])

        # Re-fetch: lastUpdateId 1009 sits exactly on the buffered stream's left edge.
        self.reconciler.apply_snapshot(1009, [(100.0, 1.0)], [(101.0, 1.0)])

        self.assertEqual(self.reconciler.state, BookState.SYNCHRONIZED)
        self.assertEqual(self.reconciler.last_sequence_id, 1011)
        self.assertEqual(self.reconciler.bids[100.5], 0.5)

    def test_sequence_gap_inside_buffered_deltas_is_rejected(self):
        """Buffered events must be continuity-checked too, not applied blindly."""
        self.reconciler.buffer_delta(DeltaUpdate(1001, 1001, [(100.5, 0.5)], []))
        self.reconciler.buffer_delta(DeltaUpdate(1005, 1005, [(100.6, 0.6)], []))

        with self.assertRaises(OrderBookError):
            self.reconciler.apply_snapshot(1000, [(100.0, 1.0)], [(101.0, 1.0)])

        self.assertEqual(self.reconciler.state, BookState.CORRUPT)
        self.assertEqual(self.reconciler.bids, {})
        # The WebSocket stream itself lost events: the buffer is unusable, resubscribe.
        self.assertEqual(self.reconciler.delta_buffer, [])

    def test_deltas_arriving_while_corrupt_are_buffered_not_dropped(self):
        self.reconciler.apply_snapshot(1000, [(100.0, 1.0)], [(101.0, 1.0)])

        with self.assertRaises(OrderBookError):
            self.reconciler.process_delta(DeltaUpdate(1005, 1005, [(100.2, 2.0)], []))

        # The offending delta is retained as the new buffer head.
        self.assertEqual(len(self.reconciler.delta_buffer), 1)
        self.assertEqual(self.reconciler.delta_buffer[0].first_update_id, 1005)

        self.reconciler.buffer_delta(DeltaUpdate(1006, 1006, [(100.3, 3.0)], []))
        self.assertEqual(len(self.reconciler.delta_buffer), 2)

    def test_end_to_end_resync_after_sequence_gap(self):
        self.reconciler.apply_snapshot(1000, [(100.0, 1.0)], [(101.0, 1.0)])

        with self.assertRaises(OrderBookError):
            self.reconciler.process_delta(DeltaUpdate(1005, 1005, [(100.2, 2.0)], []))

        self.reconciler.buffer_delta(DeltaUpdate(1006, 1006, [], [(101.0, 0.0), (101.5, 4.0)]))

        # Fresh snapshot at 1004 aligns with the retained buffer head (1005).
        self.reconciler.apply_snapshot(1004, [(100.0, 1.0)], [(101.0, 1.0)])

        self.assertEqual(self.reconciler.state, BookState.SYNCHRONIZED)
        self.assertEqual(self.reconciler.last_sequence_id, 1006)
        self.assertEqual(self.reconciler.bids[100.2], 2.0)
        self.assertNotIn(101.0, self.reconciler.asks)
        self.assertEqual(self.reconciler.get_top_of_book(), ((100.2, 2.0), (101.5, 4.0)))

    def test_delta_buffer_is_bounded(self):
        reconciler = OrderBookReconciler(symbol="BTCUSDT", max_buffer_size=3)
        for seq in range(1001, 1004):
            reconciler.buffer_delta(DeltaUpdate(seq, seq, [(100.0, 1.0)], []))

        with self.assertRaises(OrderBookError):
            reconciler.buffer_delta(DeltaUpdate(1004, 1004, [(100.0, 1.0)], []))

        self.assertEqual(reconciler.state, BookState.CORRUPT)
        # Buffered events are refused, never silently dropped from the head.
        self.assertEqual(len(reconciler.delta_buffer), 3)
        self.assertEqual(reconciler.delta_buffer[0].first_update_id, 1001)

    def test_non_finite_and_negative_levels_are_rejected(self):
        with self.assertRaises(ValueError):
            DeltaUpdate(1001, 1001, [(float("nan"), 1.0)], [])
        with self.assertRaises(ValueError):
            DeltaUpdate(1001, 1001, [], [(float("inf"), 1.0)])
        with self.assertRaises(ValueError):
            DeltaUpdate(1001, 1001, [(100.0, float("nan"))], [])
        with self.assertRaises(ValueError):
            DeltaUpdate(1001, 1001, [(-100.0, 1.0)], [])
        # Negative size is malformed, not a deletion instruction.
        with self.assertRaises(ValueError):
            DeltaUpdate(1001, 1001, [(100.0, -1.0)], [])
        with self.assertRaises(ValueError):
            self.reconciler.apply_snapshot(1000, [(float("nan"), 1.0)], [])

    def test_inverted_and_negative_sequence_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            DeltaUpdate(1005, 1001, [], [])
        with self.assertRaises(ValueError):
            DeltaUpdate(-1, 1001, [], [])
        with self.assertRaises(ValueError):
            self.reconciler.apply_snapshot(-1, [(100.0, 1.0)], [(101.0, 1.0)])

    def test_crossed_book_is_detected(self):
        self.reconciler.apply_snapshot(1000, [(100.0, 1.0)], [(101.0, 1.0)])
        self.assertFalse(self.reconciler.is_crossed())

        # A bid posted through the ask can only mean a missed or misapplied delta.
        self.reconciler.process_delta(DeltaUpdate(1001, 1001, [(101.5, 2.0)], []))
        self.assertTrue(self.reconciler.is_crossed())


if __name__ == "__main__":
    unittest.main()

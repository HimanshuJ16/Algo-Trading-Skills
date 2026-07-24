"""
Unit tests for market-data-snapshot-plus-delta-reconciliation skill.

Tests:
1. Buffering deltas prior to snapshot application.
2. Snapshot initialization and discarding stale buffered deltas.
3. Applying sequential delta updates and price level deletions (zero-qty).
4. Detecting sequence gaps and marking book state as CORRUPT.
"""
import unittest
from order_book_reconciler import BookState, DeltaUpdate, OrderBookError, OrderBookReconciler


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


if __name__ == "__main__":
    unittest.main()

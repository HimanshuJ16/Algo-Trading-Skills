"""
Unit tests for sequence-number-gap-detection-for-feeds skill.
"""
import unittest
from gap_detector import FeedFrame, FeedSyncState, SequenceGapDetector


class TestSequenceGapDetector(unittest.TestCase):

    def setUp(self):
        self.detector = SequenceGapDetector(max_buffer_size=100)

    def test_in_order_sequence_processing(self):
        f1 = FeedFrame("BTCUSDT", 100, {"price": 60000.0})
        f2 = FeedFrame("BTCUSDT", 101, {"price": 60010.0})

        res1 = self.detector.ingest_frame(f1)
        self.assertEqual(res1.state, FeedSyncState.SYNCED)
        self.assertEqual(len(res1.processed_frames), 1)

        res2 = self.detector.ingest_frame(f2)
        self.assertEqual(res2.state, FeedSyncState.SYNCED)
        self.assertEqual(len(res2.processed_frames), 1)

    def test_sequence_gap_detection_and_buffering(self):
        f1 = FeedFrame("BTCUSDT", 100, {"price": 60000.0})
        # Jump from 100 to 103 (missing 101, 102)
        f4 = FeedFrame("BTCUSDT", 103, {"price": 60030.0})

        self.detector.ingest_frame(f1)
        res_gap = self.detector.ingest_frame(f4)

        self.assertTrue(res_gap.is_gap_detected)
        self.assertEqual(res_gap.state, FeedSyncState.DIRTY_SYNC_PENDING)
        self.assertEqual(res_gap.missing_range, (101, 102))
        self.assertEqual(len(res_gap.processed_frames), 0)

    def test_gap_reconciliation_and_buffer_drain(self):
        f1 = FeedFrame("BTCUSDT", 100, {"price": 60000.0})
        f4 = FeedFrame("BTCUSDT", 103, {"price": 60030.0})

        self.detector.ingest_frame(f1)
        self.detector.ingest_frame(f4)  # Buffers 103

        # Supply missing frames 101 and 102
        f2 = FeedFrame("BTCUSDT", 101, {"price": 60010.0})
        f3 = FeedFrame("BTCUSDT", 102, {"price": 60020.0})

        processed = self.detector.reconcile_missing_frames("BTCUSDT", [f2, f3])

        # Should return 101, 102, and automatically drained 103 -> 3 frames total
        self.assertEqual(len(processed), 3)
        self.assertEqual(processed[0].sequence_id, 101)
        self.assertEqual(processed[1].sequence_id, 102)
        self.assertEqual(processed[2].sequence_id, 103)
        self.assertEqual(self.detector.sync_states["BTCUSDT"], FeedSyncState.SYNCED)


if __name__ == "__main__":
    unittest.main()

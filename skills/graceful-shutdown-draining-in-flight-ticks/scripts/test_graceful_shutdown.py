"""
Unit tests for graceful-shutdown-draining-in-flight-ticks skill.
"""
import unittest
from graceful_shutdown import GracefulShutdownManager, ShutdownState


class TestGracefulShutdownManager(unittest.TestCase):

    def setUp(self):
        self.mgr = GracefulShutdownManager(max_drain_timeout_sec=2.0)

    def test_clean_queue_drain_on_shutdown(self):
        item_queue = [{"tick": i} for i in range(50)]
        flushed_items = []

        def mock_flush(batch):
            flushed_items.extend(batch)

        self.mgr.trigger_shutdown_manual()
        self.assertTrue(self.mgr.is_shutdown_requested)

        report = self.mgr.drain_queue_and_flush(item_queue, mock_flush)

        self.assertTrue(report.is_clean_exit)
        self.assertEqual(report.initial_queue_size, 50)
        self.assertEqual(report.drained_items_count, 50)
        self.assertEqual(len(item_queue), 0)
        self.assertEqual(len(flushed_items), 50)
        self.assertEqual(report.state, ShutdownState.FLUSHED)

    def test_manual_shutdown_trigger(self):
        self.assertEqual(self.mgr.state, ShutdownState.RUNNING)
        self.mgr.trigger_shutdown_manual()
        self.assertEqual(self.mgr.state, ShutdownState.DRAINING)


if __name__ == "__main__":
    unittest.main()

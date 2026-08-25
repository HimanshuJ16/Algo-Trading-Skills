"""
Unit tests for graceful-shutdown-draining-in-flight-ticks skill.

Tests:
1. Clean drain of a full in-flight queue, with offset commit ordered after flush.
2. Signal-handler registration and SIGTERM/SIGINT trapping (incl. non-main thread).
3. Sink-failure handling: no silent loss, no false clean exit, no offset commit.
4. Transient sink failure recovers within the deadline, preserving tick order.
5. Drain-deadline enforcement leaves undrained items queued for replay.
6. Ingress gate closes on shutdown request.
7. Second signal escalates to immediate exit.
8. Concurrent producer drain under a shared lock loses no ticks.
9. Drain-timeout budget resolution against platform grace periods.
"""
import logging
import signal
import threading
import time
import unittest

from graceful_shutdown import (
    EXIT_CLEAN,
    EXIT_INCOMPLETE_DRAIN,
    GracefulShutdownManager,
    ShutdownState,
    resolve_drain_timeout,
)

# The module logs shutdown failures at ERROR by design; keep test output readable.
logging.disable(logging.CRITICAL)


class TestGracefulShutdownManager(unittest.TestCase):

    def setUp(self):
        self.mgr = GracefulShutdownManager(max_drain_timeout_sec=2.0)

    # ---------------------------------------------------------------- happy path

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
        self.assertEqual(report.undrained_items_count, 0)
        self.assertEqual(len(item_queue), 0)
        self.assertEqual(len(flushed_items), 50)
        self.assertEqual(report.state, ShutdownState.FLUSHED)
        self.assertEqual(report.exit_code, EXIT_CLEAN)

    def test_manual_shutdown_trigger(self):
        self.assertEqual(self.mgr.state, ShutdownState.RUNNING)
        self.mgr.trigger_shutdown_manual()
        self.assertEqual(self.mgr.state, ShutdownState.DRAINING)

    def test_offsets_committed_only_after_successful_flush(self):
        """Offset commit must be ordered strictly after the sink flush (at-least-once)."""
        item_queue = [1, 2, 3]
        event_log = []

        report = self.mgr.drain_queue_and_flush(
            item_queue,
            flush_callback=lambda batch: event_log.append(("flush", list(batch))),
            commit_offsets_callback=lambda: event_log.append(("commit", None)),
        )

        self.assertTrue(report.offsets_committed)
        self.assertEqual([e[0] for e in event_log], ["flush", "commit"])

    # -------------------------------------------------------- signal registration

    def test_register_signal_handlers_installs_and_traps(self):
        """Regression: register_signal_handlers() was defined without `self` and
        raised TypeError on every call, leaving the process untrapped."""
        original_int = signal.getsignal(signal.SIGINT)
        original_term = signal.getsignal(signal.SIGTERM)
        try:
            self.assertTrue(self.mgr.register_signal_handlers())
            for sig in (signal.SIGINT, signal.SIGTERM):
                installed = signal.getsignal(sig)
                self.assertEqual(installed.__func__, GracefulShutdownManager._handle_signal)
                self.assertIs(installed.__self__, self.mgr)
        finally:
            signal.signal(signal.SIGINT, original_int)
            signal.signal(signal.SIGTERM, original_term)

    def test_unknown_signal_number_does_not_raise_in_handler(self):
        """A handler that raises would abort the shutdown path entirely."""
        self.mgr._handle_signal(9999, None)
        self.assertTrue(self.mgr.is_shutdown_requested)
        self.assertEqual(self.mgr.state, ShutdownState.DRAINING)

    def test_signal_handler_transitions_to_draining(self):
        self.assertTrue(self.mgr.is_accepting_ingress())
        self.mgr._handle_signal(signal.SIGTERM, None)
        self.assertTrue(self.mgr.is_shutdown_requested)
        self.assertEqual(self.mgr.state, ShutdownState.DRAINING)
        self.assertFalse(self.mgr.force_immediate_exit)

    def test_register_signal_handlers_returns_false_off_main_thread(self):
        """Python only allows signal.signal() on the main thread of the main
        interpreter; the manager must report that instead of raising."""
        results = []

        def worker():
            results.append(self.mgr.register_signal_handlers())

        t = threading.Thread(target=worker)
        t.start()
        t.join()

        self.assertEqual(results, [False])

    def test_second_signal_forces_immediate_exit(self):
        self.mgr._handle_signal(signal.SIGTERM, None)
        self.mgr._handle_signal(signal.SIGTERM, None)
        self.assertTrue(self.mgr.force_immediate_exit)

        item_queue = [1, 2, 3]
        report = self.mgr.drain_queue_and_flush(item_queue, lambda batch: None)

        self.assertFalse(report.is_clean_exit)
        self.assertEqual(report.drained_items_count, 0)
        self.assertEqual(report.undrained_items_count, 3)
        self.assertEqual(report.exit_code, EXIT_INCOMPLETE_DRAIN)

    # ------------------------------------------------------------ sink failures

    def test_persistent_flush_failure_preserves_items_and_reports_dirty(self):
        """Regression: a raising flush callback used to destroy the batch (it had
        already been cleared from the queue) while still reporting is_clean_exit=True."""
        mgr = GracefulShutdownManager(max_drain_timeout_sec=0.2, retry_interval_sec=0.01)
        item_queue = [{"tick": i} for i in range(10)]

        def failing_flush(batch):
            raise RuntimeError("sink unavailable")

        committed = []
        report = mgr.drain_queue_and_flush(
            item_queue, failing_flush, commit_offsets_callback=lambda: committed.append(1)
        )

        self.assertFalse(report.is_clean_exit)
        self.assertEqual(report.drained_items_count, 0)
        self.assertEqual(report.undrained_items_count, 10)
        self.assertEqual(len(item_queue), 10, "unflushed ticks must stay queued, not vanish")
        self.assertEqual(report.state, ShutdownState.TERMINATED)
        self.assertEqual(report.exit_code, EXIT_INCOMPLETE_DRAIN)
        self.assertGreater(report.flush_failure_count, 0)
        self.assertFalse(report.offsets_committed)
        self.assertEqual(committed, [], "offsets must not be committed after data loss")

    def test_transient_flush_failure_recovers_and_preserves_order(self):
        mgr = GracefulShutdownManager(max_drain_timeout_sec=2.0, retry_interval_sec=0.01)
        item_queue = list(range(6))
        flushed = []
        attempts = {"n": 0}

        def flaky_flush(batch):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise IOError("transient sink blip")
            flushed.extend(batch)

        report = mgr.drain_queue_and_flush(item_queue, flaky_flush)

        self.assertTrue(report.is_clean_exit)
        self.assertEqual(report.drained_items_count, 6)
        self.assertEqual(report.flush_failure_count, 2)
        self.assertEqual(flushed, list(range(6)), "restored batch must keep tick order")

    def test_offset_commit_failure_marks_exit_dirty(self):
        item_queue = [1, 2]

        def failing_commit():
            raise RuntimeError("coordinator unreachable")

        report = self.mgr.drain_queue_and_flush(
            item_queue, lambda batch: None, commit_offsets_callback=failing_commit
        )

        self.assertEqual(report.drained_items_count, 2)
        self.assertFalse(report.offsets_committed)
        self.assertFalse(report.is_clean_exit)
        self.assertEqual(report.exit_code, EXIT_INCOMPLETE_DRAIN)

    # ---------------------------------------------------------------- deadlines

    def test_drain_deadline_stops_slow_sink_and_keeps_remainder(self):
        mgr = GracefulShutdownManager(max_drain_timeout_sec=0.15, retry_interval_sec=0.01)
        item_queue = list(range(100))

        def slow_one_at_a_time(batch):
            # Sink accepts only the first item per call, slowly.
            time.sleep(0.05)
            keep = batch[1:]
            if keep:
                raise RuntimeError("sink accepted only part of the batch")

        started = time.monotonic()
        report = mgr.drain_queue_and_flush(item_queue, slow_one_at_a_time)
        elapsed = time.monotonic() - started

        self.assertFalse(report.is_clean_exit)
        self.assertLess(elapsed, 1.0, "drain must not overrun its deadline materially")
        self.assertEqual(len(item_queue), report.undrained_items_count)
        self.assertGreater(report.undrained_items_count, 0)

    def test_empty_queue_drains_cleanly(self):
        item_queue = []
        report = self.mgr.drain_queue_and_flush(item_queue, lambda batch: None)
        self.assertTrue(report.is_clean_exit)
        self.assertEqual(report.initial_queue_size, 0)
        self.assertEqual(report.drained_items_count, 0)
        self.assertEqual(report.exit_code, EXIT_CLEAN)

    def test_rejects_non_positive_timeout(self):
        with self.assertRaises(ValueError):
            GracefulShutdownManager(max_drain_timeout_sec=0)
        with self.assertRaises(ValueError):
            GracefulShutdownManager(max_drain_timeout_sec=-1.0)

    # ------------------------------------------------------------------ ingress

    def test_ingress_gate_closes_on_shutdown(self):
        accepted, rejected = [], []

        def on_tick(tick):
            (accepted if self.mgr.is_accepting_ingress() else rejected).append(tick)

        on_tick("t1")
        self.mgr.trigger_shutdown_manual()
        on_tick("t2")

        self.assertEqual(accepted, ["t1"])
        self.assertEqual(rejected, ["t2"])

    # -------------------------------------------------------------- concurrency

    def test_concurrent_producer_loses_no_ticks_under_shared_lock(self):
        """A producer appending while the drain detaches batches must not have its
        tick dropped between the read and the removal."""
        mgr = GracefulShutdownManager(max_drain_timeout_sec=3.0, retry_interval_sec=0.001)
        lock = threading.Lock()
        item_queue = []
        flushed = []
        total = 300

        def producer():
            for i in range(total):
                with lock:
                    item_queue.append(i)
                time.sleep(0)

        t = threading.Thread(target=producer)
        t.start()
        t.join()

        report = mgr.drain_queue_and_flush(
            item_queue, lambda batch: flushed.extend(batch), queue_lock=lock
        )

        self.assertTrue(report.is_clean_exit)
        self.assertEqual(sorted(flushed), list(range(total)))
        self.assertEqual(report.drained_items_count, total)


class TestDrainBudget(unittest.TestCase):

    def test_budget_subtracts_prestop_and_exit_overhead(self):
        # Kubernetes default grace period is 30s; a 5s preStop plus 1s exit
        # reserve leaves 24s of drain.
        self.assertEqual(resolve_drain_timeout(30.0, pre_stop_sec=5.0), 24.0)

    def test_budget_defaults_to_exit_overhead_only(self):
        self.assertEqual(resolve_drain_timeout(10.0), 9.0)

    def test_budget_raises_when_grace_period_fully_consumed(self):
        with self.assertRaises(ValueError):
            resolve_drain_timeout(10.0, pre_stop_sec=15.0)
        with self.assertRaises(ValueError):
            resolve_drain_timeout(0.0)

    def test_budget_rejects_negative_components(self):
        with self.assertRaises(ValueError):
            resolve_drain_timeout(30.0, pre_stop_sec=-1.0)


if __name__ == "__main__":
    unittest.main()

"""
Unit tests for broker-side-order-throttle-detection skill.

Expected EWMA/EWMVar values are derived by hand from the Finch (2009) eq. 143
recurrence rather than by calling the implementation, so the statistical tests fail
if the recurrence itself is wrong.
"""
import logging
import math
import threading
import unittest

from throttle_detector import (
    OrderThrottleDetector,
    ThrottleDataError,
    ThrottleState,
)

# The detector logs every throttle at WARNING; silence it so test output stays readable.
logging.getLogger("throttle_detector").addHandler(logging.NullHandler())
logging.getLogger("throttle_detector").propagate = False


def _ack(detector, order_id, rtt_ms, submitted_at=0.0):
    """Record one ACK expressed directly as an RTT in milliseconds."""
    return detector.record_order_ack(order_id, submitted_at, submitted_at + rtt_ms / 1000.0)


def _warm_up(detector, rtt_ms=15.0, count=20, prefix="WARM"):
    for i in range(count):
        _ack(detector, f"{prefix}_{i}", rtt_ms)
    return detector


class TestBaselineStatistics(unittest.TestCase):
    """The EWMA/EWMVar recurrence and the variance floor."""

    def test_ewma_and_ewmvar_match_hand_derived_values(self):
        # alpha=0.5, samples [10, 20]:
        #   n=1: mean=10, var=0
        #   n=2: diff=10, incr=alpha*diff=5, mean=15,
        #        var=(1-alpha)*(var + diff*incr) = 0.5*(0 + 50) = 25  -> sigma = 5
        d = OrderThrottleDetector(alpha=0.5, min_samples_for_detection=2, min_variance_clamp=1.0)
        _ack(d, "A", 10.0)
        _ack(d, "B", 20.0)
        self.assertAlmostEqual(d.ewma_rtt, 15.0, places=9)
        self.assertAlmostEqual(d.ewmvar_rtt, 25.0, places=9)

        # A third sample is classified against mean=15, sigma=5, so z = (40-15)/5 = 5.
        report = _ack(d, "C", 40.0)
        self.assertAlmostEqual(report.ewma_rtt_ms, 15.0, places=9)
        self.assertAlmostEqual(report.ewmsd_rtt_ms, 5.0, places=9)
        self.assertAlmostEqual(report.z_score, 5.0, places=9)

    def test_reported_z_score_is_reproducible_from_reported_baseline(self):
        """The mean and sigma in the report must be the ones the decision used."""
        d = OrderThrottleDetector(alpha=0.3, min_samples_for_detection=5)
        for i, rtt in enumerate([12.0, 18.0, 14.0, 21.0, 11.0, 16.0]):
            _ack(d, f"S{i}", rtt)
        report = _ack(d, "CHECK", 40.0)
        self.assertGreater(report.ewmsd_rtt_ms, 0.0)
        self.assertAlmostEqual(
            report.z_score,
            (report.latest_rtt_ms - report.ewma_rtt_ms) / report.ewmsd_rtt_ms,
            places=9,
        )

    def test_variance_floor_is_applied_before_the_square_root(self):
        """
        min_variance_clamp is a variance in ms^2, so a floor of 100 must yield a sigma
        floor of 10 ms. The earlier implementation applied max() to the deviation, which
        would produce 100 ms here.
        """
        d = OrderThrottleDetector(alpha=0.2, min_variance_clamp=100.0,
                                  min_samples_for_detection=2)
        _warm_up(d, rtt_ms=15.0, count=5)  # constant input -> variance 0
        report = _ack(d, "FLOORED", 16.0)
        self.assertAlmostEqual(report.ewmsd_rtt_ms, 10.0, places=9)


class TestWarmup(unittest.TestCase):

    def test_state_is_warmup_until_enough_samples(self):
        # Each sample is classified against the baseline as it stood *before* that
        # sample was admitted, so the 10th ACK still sees a 9-sample baseline and the
        # 11th is the first to be judged on a full one.
        d = OrderThrottleDetector(min_samples_for_detection=10)
        for i in range(10):
            report = _ack(d, f"W{i}", 15.0)
            self.assertEqual(report.state, ThrottleState.WARMUP)
            self.assertFalse(report.is_throttled)
            self.assertEqual(report.baseline_sample_count, i + 1)
        self.assertEqual(_ack(d, "W10", 15.0).state, ThrottleState.NORMAL)

    def test_absolute_ceiling_still_fires_during_warmup(self):
        """A 600 ms ACK is a throttle whether or not a baseline exists yet."""
        d = OrderThrottleDetector(min_samples_for_detection=20, max_absolute_rtt_ms=500.0)
        _ack(d, "W0", 15.0)
        report = _ack(d, "SPIKE", 600.0)
        self.assertEqual(report.state, ThrottleState.SILENT_THROTTLE)
        self.assertTrue(report.is_throttled)

    def test_throttled_warmup_sample_does_not_seed_the_baseline(self):
        d = OrderThrottleDetector(min_samples_for_detection=20)
        report = _ack(d, "BAD_FIRST", 900.0)
        self.assertEqual(report.state, ThrottleState.SILENT_THROTTLE)
        self.assertFalse(report.baseline_admitted)
        self.assertEqual(d.sample_count, 0)
        self.assertFalse(d.initialized)


class TestThrottleDetection(unittest.TestCase):

    def setUp(self):
        self.detector = OrderThrottleDetector(
            alpha=0.2,
            z_score_threshold=3.0,
            max_absolute_rtt_ms=500.0,
            min_variance_clamp=1.0,
            min_samples_for_detection=20,
        )

    def test_normal_latency_produces_no_backoff(self):
        for i in range(20):
            _ack(self.detector, f"ORD_{i}", 15.0)
        report = _ack(self.detector, "ORD_STEADY", 15.0)
        self.assertEqual(report.state, ThrottleState.NORMAL)
        self.assertFalse(report.is_throttled)
        self.assertEqual(report.recommended_backoff_ms, 0.0)

    def test_absolute_spike_is_flagged(self):
        _warm_up(self.detector, rtt_ms=20.0)
        report = _ack(self.detector, "ORD_SPIKE", 600.0)
        self.assertEqual(report.state, ThrottleState.SILENT_THROTTLE)
        self.assertTrue(report.is_throttled)
        self.assertGreaterEqual(report.recommended_backoff_ms, 10.0)

    def test_statistical_anomaly_below_absolute_ceiling_is_flagged(self):
        """200 ms is well under the 500 ms ceiling but far outside a jittery baseline."""
        for i, rtt in enumerate([9.0, 11.0, 10.0, 12.0, 8.0, 10.0, 11.0, 9.0,
                                 10.0, 12.0, 9.0, 11.0, 10.0, 8.0, 12.0, 10.0,
                                 9.0, 11.0, 10.0, 10.0]):
            _ack(self.detector, f"ORD_{i}", rtt)
        report = _ack(self.detector, "ORD_ANOMALY", 200.0)
        self.assertLess(report.latest_rtt_ms, self.detector.max_absolute_rtt_ms)
        self.assertGreaterEqual(report.z_score, 3.0)
        self.assertEqual(report.state, ThrottleState.SILENT_THROTTLE)

    def test_elevated_latency_band(self):
        d = OrderThrottleDetector(alpha=0.2, z_score_threshold=3.0, elevated_z_threshold=1.0,
                                  min_variance_clamp=1.0, min_samples_for_detection=5)
        _warm_up(d, rtt_ms=15.0, count=10)  # sigma floored at 1 ms
        report = _ack(d, "MILD", 17.0)  # z = 2.0: above elevated, below throttle
        self.assertEqual(report.state, ThrottleState.ELEVATED_LATENCY)
        self.assertFalse(report.is_throttled)
        self.assertGreaterEqual(report.recommended_backoff_ms, d.min_backoff_ms)

    def test_sustained_sub_ceiling_throttle_stays_detected(self):
        """
        Regression for the defect where throttled samples were folded into the baseline.

        A persistent 300 ms throttle (20x baseline, below the 500 ms ceiling) previously
        trained the EWMA onto itself and was reported NORMAL from the fourth sample on,
        with the backoff decaying to zero while the throttle was still in force.
        """
        _warm_up(self.detector, rtt_ms=15.0, count=30)
        states = []
        for i in range(15):
            report = _ack(self.detector, f"THROTTLED_{i}", 300.0)
            states.append(report.state)
        self.assertTrue(
            all(s is ThrottleState.SILENT_THROTTLE for s in states),
            f"throttle detection lapsed mid-episode: {[s.value for s in states]}",
        )
        self.assertEqual(self.detector.current_backoff_ms, self.detector.max_backoff_ms)
        # The baseline must be untouched by the episode.
        self.assertAlmostEqual(self.detector.ewma_rtt, 15.0, places=6)

    def test_recovery_after_sustained_throttle(self):
        _warm_up(self.detector, rtt_ms=15.0, count=30)
        for i in range(5):
            _ack(self.detector, f"T{i}", 300.0)
        elevated = self.detector.current_backoff_ms
        self.assertGreater(elevated, 0.0)
        report = _ack(self.detector, "RECOVERED", 15.0)
        self.assertEqual(report.state, ThrottleState.NORMAL)
        self.assertLess(report.recommended_backoff_ms, elevated)


class TestAIMDBackoff(unittest.TestCase):

    def setUp(self):
        self.detector = OrderThrottleDetector(
            alpha=0.2, min_samples_for_detection=10, max_backoff_ms=2000.0,
            backoff_multiplier=2.0, backoff_additive_decrease_ms=20.0,
        )
        _warm_up(self.detector, rtt_ms=15.0, count=10)

    def test_multiplicative_increase_then_additive_decay(self):
        first = _ack(self.detector, "SPIKE_1", 600.0).recommended_backoff_ms
        second = _ack(self.detector, "SPIKE_2", 600.0).recommended_backoff_ms
        self.assertAlmostEqual(second, first * 2.0, places=6)
        third = _ack(self.detector, "OK", 15.0).recommended_backoff_ms
        self.assertAlmostEqual(third, second - 20.0, places=6)

    def test_backoff_is_clamped_to_max(self):
        for i in range(20):
            _ack(self.detector, f"SPIKE_{i}", 600.0)
        self.assertEqual(self.detector.current_backoff_ms, 2000.0)

    def test_backoff_decays_to_exactly_zero_and_stops(self):
        _ack(self.detector, "SPIKE", 600.0)
        for i in range(200):
            _ack(self.detector, f"OK_{i}", 15.0)
        self.assertEqual(self.detector.current_backoff_ms, 0.0)

    def test_configured_aimd_constants_are_honoured(self):
        d = OrderThrottleDetector(
            alpha=0.2, min_samples_for_detection=5, backoff_multiplier=3.0,
            backoff_additive_decrease_ms=5.0, max_backoff_ms=10000.0,
        )
        _warm_up(d, rtt_ms=15.0, count=5)
        first = _ack(d, "S1", 600.0).recommended_backoff_ms
        second = _ack(d, "S2", 600.0).recommended_backoff_ms
        self.assertAlmostEqual(second, first * 3.0, places=6)
        third = _ack(d, "OK", 15.0).recommended_backoff_ms
        self.assertAlmostEqual(third, second - 5.0, places=6)


class TestMissingAcknowledgments(unittest.TestCase):
    """The failure mode RTT samples cannot see: an ACK that never arrives."""

    def test_overdue_order_is_reported_as_ack_timeout(self):
        d = OrderThrottleDetector(min_samples_for_detection=5, ack_timeout_ms=5000.0)
        _warm_up(d, rtt_ms=15.0, count=5)
        d.register_order_submission("STALLED", 100.0)
        self.assertEqual(d.sweep_pending_acks(102.0), [])  # 2 s old, not yet overdue
        reports = d.sweep_pending_acks(106.0)  # 6 s old
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].state, ThrottleState.ACK_TIMEOUT)
        self.assertTrue(reports[0].is_throttled)
        self.assertEqual(reports[0].order_id, "STALLED")
        self.assertGreater(d.current_backoff_ms, 0.0)

    def test_timed_out_order_is_reported_only_once(self):
        d = OrderThrottleDetector(min_samples_for_detection=5, ack_timeout_ms=1000.0)
        _warm_up(d, rtt_ms=15.0, count=5)
        d.register_order_submission("STALLED", 0.0)
        self.assertEqual(len(d.sweep_pending_acks(10.0)), 1)
        self.assertEqual(d.sweep_pending_acks(20.0), [])
        self.assertEqual(d.pending_order_ids, ())

    def test_timeout_does_not_contaminate_the_baseline(self):
        d = OrderThrottleDetector(alpha=0.2, min_samples_for_detection=5, ack_timeout_ms=1000.0)
        _warm_up(d, rtt_ms=15.0, count=5)
        baseline_before, count_before = d.ewma_rtt, d.sample_count
        d.register_order_submission("STALLED", 0.0)
        d.sweep_pending_acks(60.0)
        self.assertAlmostEqual(d.ewma_rtt, baseline_before, places=9)
        self.assertEqual(d.sample_count, count_before)

    def test_sweep_clock_behind_submission_warns_without_aborting(self):
        """
        One entry with an untrustworthy age must not abort the sweep for the others.
        """
        d = OrderThrottleDetector(min_samples_for_detection=5, ack_timeout_ms=1000.0)
        _warm_up(d, rtt_ms=15.0, count=5)
        d.register_order_submission("FUTURE", 500.0)
        d.register_order_submission("OVERDUE", 0.0)
        with self.assertLogs("throttle_detector", level="WARNING"):
            reports = d.sweep_pending_acks(100.0)
        self.assertEqual([r.order_id for r in reports], ["OVERDUE"])
        self.assertEqual(d.pending_order_ids, ("FUTURE",))

    def test_acknowledged_order_is_removed_from_pending(self):
        d = OrderThrottleDetector(min_samples_for_detection=5, ack_timeout_ms=1000.0)
        d.register_order_submission("ORD", 0.0)
        self.assertEqual(d.pending_order_ids, ("ORD",))
        _ack(d, "ORD", 15.0)
        self.assertEqual(d.pending_order_ids, ())
        self.assertEqual(d.sweep_pending_acks(999.0), [])


class TestInputValidation(unittest.TestCase):
    """
    NaN is the dangerous case: it compares False against every threshold, so an
    unvalidated NaN reads as NORMAL and then makes the baseline NaN permanently.
    """

    def setUp(self):
        self.detector = OrderThrottleDetector(min_samples_for_detection=5)
        _warm_up(self.detector, rtt_ms=15.0, count=5)

    def test_nan_timestamp_is_rejected(self):
        with self.assertRaises(ThrottleDataError):
            self.detector.record_order_ack("NAN", 0.0, float("nan"))
        self.assertTrue(math.isfinite(self.detector.ewma_rtt))
        self.assertAlmostEqual(self.detector.ewma_rtt, 15.0, places=9)

    def test_infinite_timestamp_is_rejected(self):
        with self.assertRaises(ThrottleDataError):
            self.detector.record_order_ack("INF", 0.0, float("inf"))

    def test_clock_regression_is_rejected_not_clamped_to_zero(self):
        """
        A negative RTT means a non-monotonic clock or an out-of-order callback. Clamping
        it to 0 ms drags the baseline down and makes healthy ACKs look anomalous.
        """
        with self.assertRaises(ThrottleDataError):
            self.detector.record_order_ack("BACKWARDS", 100.0, 99.5)
        self.assertAlmostEqual(self.detector.ewma_rtt, 15.0, places=9)

    def test_overflowing_rtt_is_rejected(self):
        """Two individually finite timestamps can still scale to inf milliseconds."""
        with self.assertRaises(ThrottleDataError):
            self.detector.record_order_ack("HUGE", 0.0, 1e308)
        self.assertAlmostEqual(self.detector.ewma_rtt, 15.0, places=9)

    def test_non_numeric_timestamp_is_rejected(self):
        with self.assertRaises(ThrottleDataError):
            self.detector.record_order_ack("STR", 0.0, "later")

    def test_empty_order_id_is_rejected_on_registration(self):
        with self.assertRaises(ThrottleDataError):
            self.detector.register_order_submission("", 0.0)


class TestConfigurationValidation(unittest.TestCase):

    def test_alpha_must_be_in_range(self):
        for bad in (0.0, -0.1, 1.5, float("nan")):
            with self.assertRaises(ThrottleDataError):
                OrderThrottleDetector(alpha=bad)

    def test_zero_variance_clamp_is_rejected(self):
        with self.assertRaises(ThrottleDataError):
            OrderThrottleDetector(min_variance_clamp=0.0)

    def test_backoff_bounds_must_be_ordered(self):
        with self.assertRaises(ThrottleDataError):
            OrderThrottleDetector(min_backoff_ms=500.0, max_backoff_ms=100.0)

    def test_elevated_threshold_must_not_exceed_throttle_threshold(self):
        with self.assertRaises(ThrottleDataError):
            OrderThrottleDetector(elevated_z_threshold=4.0, z_score_threshold=3.0)

    def test_min_samples_must_be_a_positive_int(self):
        for bad in (0, -1, 2.5):
            with self.assertRaises(ThrottleDataError):
                OrderThrottleDetector(min_samples_for_detection=bad)

    def test_multiplier_below_one_is_rejected(self):
        with self.assertRaises(ThrottleDataError):
            OrderThrottleDetector(backoff_multiplier=0.5)


class TestOptionalRebaselining(unittest.TestCase):

    def test_disabled_by_default(self):
        d = OrderThrottleDetector(alpha=0.2, min_samples_for_detection=5)
        self.assertEqual(d.rebaseline_after_consecutive, 0)
        _warm_up(d, rtt_ms=15.0, count=10)
        for i in range(50):
            report = _ack(d, f"T{i}", 300.0)
        self.assertEqual(report.state, ThrottleState.SILENT_THROTTLE)
        self.assertAlmostEqual(d.ewma_rtt, 15.0, places=6)

    def test_enabled_re_anchors_after_the_configured_run(self):
        d = OrderThrottleDetector(alpha=0.2, min_samples_for_detection=5,
                                  rebaseline_after_consecutive=10)
        _warm_up(d, rtt_ms=15.0, count=10)
        for i in range(10):
            _ack(d, f"T{i}", 300.0)
        self.assertAlmostEqual(d.ewma_rtt, 300.0, places=6)
        self.assertEqual(_ack(d, "AFTER", 300.0).state, ThrottleState.NORMAL)


class TestConcurrency(unittest.TestCase):

    def test_concurrent_acks_do_not_lose_updates(self):
        """
        Broker SDKs deliver ACKs on their own callback threads; an unlocked
        read-modify-write of the baseline or the backoff silently drops samples.
        """
        d = OrderThrottleDetector(alpha=0.05, min_samples_for_detection=5)
        threads, per_thread = 8, 100

        def worker(tid):
            for i in range(per_thread):
                _ack(d, f"T{tid}_{i}", 15.0)

        workers = [threading.Thread(target=worker, args=(t,)) for t in range(threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join()

        self.assertEqual(d.sample_count, threads * per_thread)
        self.assertAlmostEqual(d.ewma_rtt, 15.0, places=6)


class TestReset(unittest.TestCase):

    def test_reset_clears_baseline_backoff_and_pending(self):
        d = OrderThrottleDetector(min_samples_for_detection=5)
        _warm_up(d, rtt_ms=15.0, count=10)
        _ack(d, "SPIKE", 600.0)
        d.register_order_submission("PENDING", 0.0)
        d.reset()
        self.assertEqual(d.sample_count, 0)
        self.assertFalse(d.initialized)
        self.assertEqual(d.current_backoff_ms, 0.0)
        self.assertEqual(d.pending_order_ids, ())


if __name__ == "__main__":
    unittest.main()

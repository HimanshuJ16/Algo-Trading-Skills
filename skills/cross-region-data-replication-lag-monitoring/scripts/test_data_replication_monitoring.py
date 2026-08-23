import unittest

from data_replication_monitoring import (
    MIN_SAMPLES_FOR_P99,
    STATUS_CLOCK_SKEW_SUSPECT,
    STATUS_DEGRADED_WARNING,
    STATUS_HEALTHY,
    STATUS_UNKNOWN_INSUFFICIENT_SAMPLES,
    STATUS_UNKNOWN_NO_DATA,
    STATUS_UNSAFE_STALE,
    CrossRegionReplicationLagMonitor,
    ReplicationHeartbeat,
)


def make_heartbeats(lags_ms, origin="us-east-1", replica="eu-west-1", prefix="HB"):
    """Build heartbeats whose measured lag is exactly each value in `lags_ms`."""
    heartbeats = []
    for i, lag in enumerate(lags_ms):
        write_t = 1000.0 + i * 100.0
        heartbeats.append(ReplicationHeartbeat(
            heartbeat_id=f"{prefix}_{i}", origin_region=origin, replica_region=replica,
            primary_write_timestamp_ms=write_t,
            replica_receive_timestamp_ms=write_t + lag,
        ))
    return heartbeats


class TestCrossRegionReplicationLagMonitor(unittest.TestCase):

    def setUp(self):
        self.monitor = CrossRegionReplicationLagMonitor(
            p99_warning_threshold_ms=100.0,
            p99_unsafe_threshold_ms=500.0,
        )
        # 100 heartbeats us-east-1 -> eu-west-1, lags cycling 40, 41, 42, 43, 44 ms.
        # Sorted, indices 0-19 are 40ms ... indices 80-99 are 44ms, so both the
        # P95 (linear index 0.95*99 = 94.05) and the P99 (0.99*99 = 98.01) land on
        # 44.0ms, and the mean is (40+41+42+43+44)/5 = 42.0ms.
        self.heartbeats = make_heartbeats([40.0 + (i % 5) for i in range(100)])

    def test_healthy_replication_lag(self):
        report = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", self.heartbeats)

        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertFalse(report.is_read_failover_recommended)
        self.assertEqual(report.sample_count, 100)
        self.assertEqual(report.mean_lag_ms, 42.0)
        self.assertEqual(report.p95_lag_ms, 44.0)
        self.assertEqual(report.p99_lag_ms, 44.0)
        self.assertEqual(report.max_lag_ms, 44.0)
        self.assertEqual(report.negative_lag_sample_count, 0)

    def test_unsafe_stale_replication_lag(self):
        # Inject 5 delayed heartbeats with 1200ms lag. With 105 samples the P99 index
        # is 0.99*104 = 102.96, which falls inside the block of 1200ms samples.
        heartbeats = self.heartbeats + make_heartbeats([1200.0] * 5, prefix="HB_STALE")

        report = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", heartbeats)

        self.assertEqual(report.status, STATUS_UNSAFE_STALE)
        self.assertTrue(report.is_read_failover_recommended)
        self.assertEqual(report.p99_lag_ms, 1200.0)
        self.assertEqual(report.max_lag_ms, 1200.0)

    def test_degraded_warning_band(self):
        report = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", make_heartbeats([250.0] * 100))

        self.assertEqual(report.status, STATUS_DEGRADED_WARNING)
        self.assertFalse(report.is_read_failover_recommended)
        self.assertEqual(report.p99_lag_ms, 250.0)

    def test_thresholds_are_inclusive_at_the_boundary(self):
        # Exactly at the unsafe threshold -> UNSAFE (fail-safe, matches the documented
        # >= semantics).
        at_unsafe = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", make_heartbeats([500.0] * 100))
        self.assertEqual(at_unsafe.status, STATUS_UNSAFE_STALE)
        self.assertTrue(at_unsafe.is_read_failover_recommended)

        # Exactly at the warning threshold -> DEGRADED.
        at_warning = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", make_heartbeats([100.0] * 100))
        self.assertEqual(at_warning.status, STATUS_DEGRADED_WARNING)

        # One millisecond below the warning threshold -> HEALTHY.
        below_warning = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", make_heartbeats([99.0] * 100))
        self.assertEqual(below_warning.status, STATUS_HEALTHY)

    def test_negative_lag_is_reported_as_clock_skew_not_zero_lag(self):
        # Regression: lags used to be clamped with max(0.0, lag), so a replica whose
        # clock read 10ms earlier than the primary's looked perfectly HEALTHY.
        report = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", make_heartbeats([-10.0] * 100))

        self.assertEqual(report.status, STATUS_CLOCK_SKEW_SUSPECT)
        self.assertTrue(report.is_read_failover_recommended)
        self.assertEqual(report.negative_lag_sample_count, 100)
        self.assertEqual(report.p99_lag_ms, -10.0)
        self.assertIn("NTP", report.recommendation_message)

    def test_single_negative_sample_blocks_a_healthy_verdict(self):
        lags = [40.0] * 99 + [-1.0]
        report = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", make_heartbeats(lags))

        self.assertEqual(report.status, STATUS_CLOCK_SKEW_SUSPECT)
        self.assertEqual(report.negative_lag_sample_count, 1)

    def test_clock_skew_tolerance_allows_sub_tolerance_negatives(self):
        tolerant = CrossRegionReplicationLagMonitor(clock_skew_tolerance_ms=20.0)
        report = tolerant.evaluate_replica_health(
            "us-east-1", "eu-west-1", make_heartbeats([-10.0] * 100))

        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertEqual(report.negative_lag_sample_count, 0)

    def test_unsafe_lag_takes_precedence_over_skew_but_reports_it(self):
        lags = [900.0] * 99 + [-5.0]
        report = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", make_heartbeats(lags))

        self.assertEqual(report.status, STATUS_UNSAFE_STALE)
        self.assertTrue(report.is_read_failover_recommended)
        self.assertEqual(report.negative_lag_sample_count, 1)
        self.assertIn("clock skew", report.recommendation_message)

    def test_sub_p99_spikes_are_counted_even_when_the_p99_stays_healthy(self):
        # 2 stalls in 600 samples = 0.33% of the window, below the 99th percentile, so
        # the P99 does not move. They were still two real stale-read windows.
        lags = [40.0] * 598 + [1800.0, 1800.0]
        report = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", make_heartbeats(lags))

        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertEqual(report.p99_lag_ms, 40.0)
        self.assertEqual(report.samples_over_unsafe_threshold, 2)
        self.assertEqual(report.max_lag_ms, 1800.0)
        self.assertIn("worst 1%", report.recommendation_message)

    def test_no_spike_note_on_a_clean_window(self):
        report = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", self.heartbeats)

        self.assertEqual(report.samples_over_unsafe_threshold, 0)
        self.assertNotIn("NOTE", report.recommendation_message)

    def test_no_heartbeats_is_unknown_not_healthy(self):
        # Regression: an empty window used to report HEALTHY with no failover, so a
        # dead heartbeat probe silently certified the replica.
        report = self.monitor.evaluate_replica_health("us-east-1", "eu-west-1", [])

        self.assertEqual(report.status, STATUS_UNKNOWN_NO_DATA)
        self.assertTrue(report.is_read_failover_recommended)
        self.assertEqual(report.sample_count, 0)

    def test_other_region_pairs_are_filtered_out(self):
        other = make_heartbeats([2000.0] * 100, origin="us-east-1",
                                replica="ap-south-1", prefix="HB_OTHER")
        report = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", self.heartbeats + other)

        self.assertEqual(report.sample_count, 100)
        self.assertEqual(report.status, STATUS_HEALTHY)

    def test_too_few_samples_for_a_p99_is_unknown_not_healthy(self):
        # Regression: 5 fast heartbeats used to report HEALTHY, but a "P99" over 5
        # samples is just the observed maximum.
        report = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", make_heartbeats([40.0] * 5))

        self.assertEqual(report.status, STATUS_UNKNOWN_INSUFFICIENT_SAMPLES)
        self.assertTrue(report.is_read_failover_recommended)
        self.assertEqual(report.sample_count, 5)

    def test_small_window_still_escalates_an_observed_unsafe_lag(self):
        report = self.monitor.evaluate_replica_health(
            "us-east-1", "eu-west-1", make_heartbeats([40.0] * 4 + [3000.0]))

        self.assertEqual(report.status, STATUS_UNSAFE_STALE)
        self.assertTrue(report.is_read_failover_recommended)

    def test_min_sample_count_is_configurable(self):
        lenient = CrossRegionReplicationLagMonitor(min_sample_count=5)
        report = lenient.evaluate_replica_health(
            "us-east-1", "eu-west-1", make_heartbeats([40.0] * 5))

        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertEqual(MIN_SAMPLES_FOR_P99, 100)

    def test_non_finite_timestamps_are_rejected_loudly(self):
        # Regression: a NaN lag made every threshold comparison False, so a stale
        # replica was reported HEALTHY.
        bad = make_heartbeats([40.0] * 100)
        bad[7].replica_receive_timestamp_ms = float("nan")

        with self.assertRaises(ValueError):
            self.monitor.evaluate_replica_health("us-east-1", "eu-west-1", bad)

        bad[7].replica_receive_timestamp_ms = float("inf")
        with self.assertRaises(ValueError):
            self.monitor.evaluate_replica_health("us-east-1", "eu-west-1", bad)

    def test_compute_replication_lags_returns_signed_values(self):
        lags = self.monitor.compute_replication_lags(make_heartbeats([-25.0, 30.0]))

        self.assertEqual(lags, [-25.0, 30.0])

    def test_invalid_constructor_arguments_are_rejected(self):
        with self.assertRaises(ValueError):
            CrossRegionReplicationLagMonitor(p99_warning_threshold_ms=-1.0)
        with self.assertRaises(ValueError):
            CrossRegionReplicationLagMonitor(p99_unsafe_threshold_ms=float("nan"))
        with self.assertRaises(ValueError):
            CrossRegionReplicationLagMonitor(
                p99_warning_threshold_ms=600.0, p99_unsafe_threshold_ms=500.0)
        with self.assertRaises(ValueError):
            CrossRegionReplicationLagMonitor(min_sample_count=0)
        with self.assertRaises(ValueError):
            CrossRegionReplicationLagMonitor(clock_skew_tolerance_ms=-5.0)


if __name__ == '__main__':
    unittest.main()

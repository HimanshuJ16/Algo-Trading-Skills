import unittest

from custody_solution_uptime_and_liveness_guarantees import (
    CustodyHealthProbe,
    CustodyLivenessMonitorEngine,
    ProviderSlaConfig,
    STATUS_DEGRADED_SLA_BREACH,
    STATUS_HEALTHY,
    STATUS_QUORUM_AT_RISK,
    STATUS_QUORUM_LOST,
    STATUS_STALE_TELEMETRY,
    STATUS_UNKNOWN_NO_TELEMETRY,
    _percentile,
)


def _config(**overrides):
    base = dict(
        provider_id="FIREBLOCKS_01",
        provider_name="Fireblocks Institutional Custody",
        target_uptime_pct=99.9,
        max_signing_latency_ms=2000.0,
        mpc_threshold_k=2,
        mpc_total_n=3,
    )
    base.update(overrides)
    return ProviderSlaConfig(**base)


def _probes(n, *, healthy=True, latency=450.0, nodes=3, start_ms=1000.0, step_ms=10.0):
    return [
        CustodyHealthProbe(f"P_{i}", start_ms + i * step_ms, healthy, latency, nodes)
        for i in range(n)
    ]


class TestHealthyBaseline(unittest.TestCase):

    def setUp(self):
        self.engine = CustodyLivenessMonitorEngine(_config())

    def test_healthy_custody_liveness(self):
        report = self.engine.audit_liveness(_probes(100))

        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertEqual(report.rolling_uptime_pct, 100.0)
        self.assertTrue(report.is_mpc_quorum_maintained)
        self.assertFalse(report.is_failover_recommended)
        self.assertEqual(report.breached_conditions, [])
        # 3 active nodes against k=2 leaves one spare.
        self.assertEqual(report.redundant_nodes, 1)
        self.assertEqual(report.probe_count, 100)
        self.assertTrue(report.percentiles_reliable)

    def test_window_bounds_are_reported(self):
        report = self.engine.audit_liveness(_probes(100, start_ms=1000.0, step_ms=10.0))
        self.assertEqual(report.window_start_ms, 1000.0)
        self.assertEqual(report.window_end_ms, 1000.0 + 99 * 10.0)


class TestFailClosedOnMissingTelemetry(unittest.TestCase):
    """A liveness monitor that reports HEALTHY when blind is worse than useless."""

    def setUp(self):
        self.engine = CustodyLivenessMonitorEngine(_config(max_probe_age_ms=60_000.0))

    def test_no_probes_is_unknown_not_healthy(self):
        # Regression: previously returned HEALTHY with rolling_uptime_pct=100.0
        # and is_failover_recommended=False, so a dead collector looked perfect.
        report = self.engine.audit_liveness([])

        self.assertEqual(report.status, STATUS_UNKNOWN_NO_TELEMETRY)
        self.assertTrue(report.is_failover_recommended)
        self.assertFalse(report.is_mpc_quorum_maintained)
        self.assertEqual(report.rolling_uptime_pct, 0.0)
        self.assertEqual(report.probe_count, 0)

    def test_stale_telemetry_forces_failover(self):
        # Newest probe at t=1990ms, audited at t=5,000,000ms -> ~5,000s old.
        probes = _probes(100)
        report = self.engine.audit_liveness(probes, as_of_timestamp_ms=5_000_000.0)

        self.assertEqual(report.status, STATUS_STALE_TELEMETRY)
        self.assertTrue(report.is_failover_recommended)
        self.assertAlmostEqual(report.newest_probe_age_ms, 5_000_000.0 - 1990.0)

    def test_fresh_telemetry_within_bound_is_healthy(self):
        probes = _probes(100)
        report = self.engine.audit_liveness(probes, as_of_timestamp_ms=1990.0 + 30_000.0)

        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertFalse(report.is_failover_recommended)

    def test_staleness_boundary_is_exclusive(self):
        probes = _probes(1, start_ms=0.0)
        # age == max_probe_age_ms is within bound; one ms older is not.
        at_bound = self.engine.audit_liveness(probes, as_of_timestamp_ms=60_000.0)
        over_bound = self.engine.audit_liveness(probes, as_of_timestamp_ms=60_001.0)

        self.assertNotEqual(at_bound.status, STATUS_STALE_TELEMETRY)
        self.assertEqual(over_bound.status, STATUS_STALE_TELEMETRY)

    def test_future_dated_probe_is_flagged_as_clock_skew(self):
        # A probe stamped after the audit instant cannot be "fresh"; it means the
        # collector clock is wrong, and a negative age must not pass silently.
        probes = _probes(1, start_ms=10_000.0)
        report = self.engine.audit_liveness(probes, as_of_timestamp_ms=5_000.0)

        self.assertEqual(report.newest_probe_age_ms, -5_000.0)
        self.assertTrue(any("CLOCK SKEW" in r for r in report.recommendations))

    def test_missing_as_of_timestamp_says_freshness_unevaluated(self):
        report = self.engine.audit_liveness(_probes(100))
        self.assertIsNone(report.newest_probe_age_ms)
        self.assertTrue(any("Freshness NOT evaluated" in r for r in report.recommendations))


class TestMpcQuorum(unittest.TestCase):

    def setUp(self):
        self.engine = CustodyLivenessMonitorEngine(_config())

    def test_mpc_quorum_loss_triggers_liveness_halt_and_failover(self):
        probes = _probes(10)
        probes.append(CustodyHealthProbe("P_FAIL", 2000.0, True, 450.0, active_mpc_nodes=1))

        report = self.engine.audit_liveness(probes)

        self.assertEqual(report.status, STATUS_QUORUM_LOST)
        self.assertFalse(report.is_mpc_quorum_maintained)
        self.assertTrue(report.is_failover_recommended)
        self.assertEqual(report.redundant_nodes, -1)

    def test_out_of_order_probes_cannot_mask_a_quorum_loss(self):
        # Regression: the engine took probes[-1] as "current". Here the newest
        # probe by timestamp (t=9000, 1 node) is buried mid-list while an older
        # healthy probe (t=2000, 3 nodes) arrives last, as happens whenever
        # collectors report concurrently.
        probes = [
            CustodyHealthProbe("P_OLD", 1000.0, True, 450.0, 3),
            CustodyHealthProbe("P_NEWEST_FAIL", 9000.0, True, 450.0, 1),
            CustodyHealthProbe("P_LATE_ARRIVAL", 2000.0, True, 450.0, 3),
        ]
        report = self.engine.audit_liveness(probes)

        self.assertEqual(report.status, STATUS_QUORUM_LOST)
        self.assertEqual(report.current_active_mpc_nodes, 1)
        self.assertTrue(report.is_failover_recommended)

    def test_active_equal_to_threshold_is_at_risk_not_healthy(self):
        # k=2 of n=3 with exactly 2 active: quorum holds, but the next node loss
        # halts signing. Previously reported HEALTHY with no warning.
        probes = _probes(100, nodes=2)
        report = self.engine.audit_liveness(probes)

        self.assertEqual(report.status, STATUS_QUORUM_AT_RISK)
        self.assertTrue(report.is_mpc_quorum_maintained)
        self.assertEqual(report.redundant_nodes, 0)
        # At-risk is a warning, not a failover trigger.
        self.assertFalse(report.is_failover_recommended)

    def test_tied_timestamps_resolve_to_the_conservative_node_count(self):
        # Two collectors report the same instant and disagree. Whichever order
        # they arrive in, the lower node count must win - otherwise merely
        # reshuffling the input list erases a quorum loss.
        for order in ((3, 1), (1, 3)):
            with self.subTest(order=order):
                probes = [
                    CustodyHealthProbe("A", 5000.0, True, 450.0, order[0]),
                    CustodyHealthProbe("B", 5000.0, True, 450.0, order[1]),
                ]
                report = self.engine.audit_liveness(probes)
                self.assertEqual(report.current_active_mpc_nodes, 1)
                self.assertEqual(report.status, STATUS_QUORUM_LOST)

    def test_zero_active_nodes_is_quorum_lost(self):
        report = self.engine.audit_liveness(_probes(10, nodes=0))
        self.assertEqual(report.status, STATUS_QUORUM_LOST)
        self.assertTrue(report.is_failover_recommended)


class TestUptimeThreshold(unittest.TestCase):

    def setUp(self):
        self.engine = CustodyLivenessMonitorEngine(_config())

    def test_rounding_cannot_hide_a_breach(self):
        # 24,974 healthy of 25,000 = 99.896%, a genuine breach of a 99.9% target.
        # The previous revision rounded to 2dp first (99.896 -> 99.9) and then
        # compared 99.9 < 99.9, so the breach cleared the gate.
        total, healthy = 25_000, 24_974
        probes = _probes(healthy, healthy=True)
        probes += [
            CustodyHealthProbe(f"D_{i}", 900_000.0 + i, False, 450.0, 3)
            for i in range(total - healthy)
        ]
        report = self.engine.audit_liveness(probes)

        self.assertEqual(report.rolling_uptime_pct, 99.896)
        self.assertEqual(report.status, STATUS_DEGRADED_SLA_BREACH)
        self.assertTrue(report.is_failover_recommended)

    def test_uptime_exactly_at_target_is_not_a_breach(self):
        # 999 of 1000 = 99.9% exactly; the SLA is "at least", so this passes.
        probes = _probes(999)
        probes.append(CustodyHealthProbe("DOWN", 900_000.0, False, 450.0, 3))
        report = self.engine.audit_liveness(probes)

        self.assertEqual(report.rolling_uptime_pct, 99.9)
        self.assertEqual(report.status, STATUS_HEALTHY)

    def test_uptime_just_below_target_is_a_breach(self):
        probes = _probes(998)
        probes += [
            CustodyHealthProbe(f"DOWN_{i}", 900_000.0 + i, False, 450.0, 3) for i in range(2)
        ]
        report = self.engine.audit_liveness(probes)

        self.assertEqual(report.rolling_uptime_pct, 99.8)
        self.assertEqual(report.status, STATUS_DEGRADED_SLA_BREACH)
        self.assertTrue(report.is_failover_recommended)


class TestLatencySla(unittest.TestCase):

    def test_p99_breach_is_reported_but_does_not_fail_over_by_default(self):
        engine = CustodyLivenessMonitorEngine(_config())
        probes = _probes(100, latency=5000.0)
        report = engine.audit_liveness(probes)

        self.assertEqual(report.status, STATUS_DEGRADED_SLA_BREACH)
        self.assertTrue(report.percentiles_reliable)
        self.assertFalse(report.is_failover_recommended)

    def test_p99_breach_fails_over_when_configured(self):
        engine = CustodyLivenessMonitorEngine(_config(failover_on_latency_breach=True))
        report = engine.audit_liveness(_probes(100, latency=5000.0))

        self.assertEqual(report.status, STATUS_DEGRADED_SLA_BREACH)
        self.assertTrue(report.is_failover_recommended)

    def test_p99_from_too_few_samples_is_not_gated(self):
        # 5 samples cannot resolve a 99th percentile - it is just the maximum.
        # Reported for visibility, but it must not trip an SLA gate.
        engine = CustodyLivenessMonitorEngine(_config(failover_on_latency_breach=True))
        report = engine.audit_liveness(_probes(5, latency=9000.0))

        self.assertFalse(report.percentiles_reliable)
        self.assertEqual(report.latency_sample_count, 5)
        self.assertEqual(report.status, STATUS_HEALTHY)
        self.assertFalse(report.is_failover_recommended)
        self.assertTrue(any("NOT GATED" in r for r in report.recommendations))

    def test_unhealthy_probes_are_excluded_from_the_latency_sample(self):
        probes = _probes(100, latency=100.0)
        probes += [
            CustodyHealthProbe(f"D_{i}", 500_000.0 + i, False, 90_000.0, 3) for i in range(10)
        ]
        report = CustodyLivenessMonitorEngine(_config()).audit_liveness(probes)

        self.assertEqual(report.latency_sample_count, 100)
        self.assertEqual(report.p99_signing_latency_ms, 100.0)

    def test_rolling_window_limits_the_latency_sample(self):
        # 200 slow probes then 100 fast ones; a 100-wide window sees only the fast tail.
        engine = CustodyLivenessMonitorEngine(
            _config(latency_rolling_window=100, min_latency_samples=100)
        )
        probes = _probes(200, latency=9000.0, start_ms=1000.0)
        probes += _probes(100, latency=120.0, start_ms=500_000.0)
        report = engine.audit_liveness(probes)

        self.assertEqual(report.latency_sample_count, 100)
        self.assertEqual(report.p99_signing_latency_ms, 120.0)
        self.assertEqual(report.status, STATUS_HEALTHY)

    def test_percentile_helper_matches_known_values(self):
        # Independently derived from the type-7 definition: rank = q*(n-1).
        # n=101, q=0.99 -> rank 99.0 -> exactly the 100th ordered value.
        values = [float(i) for i in range(101)]
        self.assertEqual(_percentile(values, 0.99), 99.0)
        # n=5, q=0.5 -> rank 2.0 -> the median.
        self.assertEqual(_percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.50), 3.0)
        # Interpolating case: n=4, q=0.5 -> rank 1.5 -> midpoint of 2 and 3.
        self.assertEqual(_percentile([1.0, 2.0, 3.0, 4.0], 0.50), 2.5)
        self.assertEqual(_percentile([7.0], 0.99), 7.0)


class TestConcurrentBreaches(unittest.TestCase):

    def test_quorum_loss_does_not_hide_a_simultaneous_uptime_breach(self):
        # Regression: an if/elif chain reported exactly one condition, so an
        # operator saw the quorum halt and never learned uptime had also breached.
        engine = CustodyLivenessMonitorEngine(_config())
        probes = _probes(900, healthy=True)
        probes += [
            CustodyHealthProbe(f"D_{i}", 500_000.0 + i, False, 450.0, 3) for i in range(100)
        ]
        probes.append(CustodyHealthProbe("P_FAIL", 900_000.0, True, 450.0, 1))

        report = engine.audit_liveness(probes)

        self.assertEqual(report.status, STATUS_QUORUM_LOST)
        self.assertIn(STATUS_QUORUM_LOST, report.breached_conditions)
        self.assertIn(STATUS_DEGRADED_SLA_BREACH, report.breached_conditions)
        self.assertGreaterEqual(len(report.recommendations), 2)
        self.assertTrue(report.is_failover_recommended)


class TestValidation(unittest.TestCase):

    def test_threshold_above_total_shares_is_rejected(self):
        # A 4-of-3 quorum can never be satisfied; the cluster is dead on arrival.
        with self.assertRaises(ValueError):
            CustodyLivenessMonitorEngine(_config(mpc_threshold_k=4, mpc_total_n=3))

    def test_non_positive_cluster_parameters_are_rejected(self):
        for kwargs in ({"mpc_threshold_k": 0}, {"mpc_total_n": 0}, {"min_latency_samples": 0}):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    CustodyLivenessMonitorEngine(_config(**kwargs))

    def test_out_of_range_sla_targets_are_rejected(self):
        for kwargs in (
            {"target_uptime_pct": 101.0},
            {"target_uptime_pct": -1.0},
            {"target_uptime_pct": float("nan")},
            {"max_signing_latency_ms": 0.0},
            {"max_probe_age_ms": -5.0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(ValueError):
                    CustodyLivenessMonitorEngine(_config(**kwargs))

    def test_nan_latency_is_rejected_rather_than_silently_passing(self):
        # Regression: NaN propagated through the percentile, and `nan > sla` is
        # False, so a corrupt feed cleared the latency gate instead of tripping it.
        engine = CustodyLivenessMonitorEngine(_config())
        probes = _probes(99)
        probes.append(CustodyHealthProbe("BAD", 99_000.0, True, float("nan"), 3))

        with self.assertRaises(ValueError):
            engine.audit_liveness(probes)

    def test_infinite_and_negative_telemetry_is_rejected(self):
        engine = CustodyLivenessMonitorEngine(_config())
        for probe in (
            CustodyHealthProbe("INF", 1000.0, True, float("inf"), 3),
            CustodyHealthProbe("NEG", 1000.0, True, -1.0, 3),
            CustodyHealthProbe("BADTS", float("nan"), True, 450.0, 3),
            CustodyHealthProbe("NEGNODE", 1000.0, True, 450.0, -1),
        ):
            with self.subTest(probe=probe.probe_id):
                with self.assertRaises(ValueError):
                    engine.audit_liveness([probe])

    def test_node_count_above_provisioned_cluster_is_rejected(self):
        # 5 active nodes in a 3-node cluster means the feed is wrong; trusting it
        # would mask a real quorum loss.
        engine = CustodyLivenessMonitorEngine(_config())
        with self.assertRaises(ValueError):
            engine.audit_liveness([CustodyHealthProbe("X", 1000.0, True, 450.0, 5)])

    def test_non_finite_as_of_timestamp_is_rejected(self):
        engine = CustodyLivenessMonitorEngine(_config())
        with self.assertRaises(ValueError):
            engine.audit_liveness(_probes(10), as_of_timestamp_ms=float("inf"))


if __name__ == '__main__':
    unittest.main()

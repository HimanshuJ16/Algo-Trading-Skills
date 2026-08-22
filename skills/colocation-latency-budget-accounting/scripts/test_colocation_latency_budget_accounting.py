import unittest

import numpy as np

from colocation_latency_budget_accounting import (
    DEFAULT_PHASE_SLAS_NS,
    PHASE_NAMES,
    HotPathTrace,
    LatencyBudgetAccountingEngine,
)


def _trace(trace_id, phases, base=1_000_000):
    """Build a trace from five phase durations (ns), in hot-path order."""
    t = [base]
    for d in phases:
        t.append(t[-1] + d)
    return HotPathTrace(
        trace_id=trace_id,
        t0_nic_ingress_ns=t[0],
        t1_decode_ns=t[1],
        t2_signal_ns=t[2],
        t3_risk_ns=t[3],
        t4_encode_ns=t[4],
        t5_nic_egress_ns=t[5],
    )


class TestTraceValidation(unittest.TestCase):
    def test_non_monotonic_timestamps_rejected(self):
        # Regression: previously produced a silent -100 ns phase duration that
        # flowed into SLA decisions and percentile stats.
        with self.assertRaises(ValueError) as ctx:
            HotPathTrace("BAD", 1000, 900, 2000, 3000, 4000, 5000)
        self.assertIn("t1_decode_ns", str(ctx.exception))

    def test_egress_before_encode_rejected(self):
        with self.assertRaises(ValueError):
            HotPathTrace("BAD2", 0, 1, 2, 3, 400, 4)

    def test_equal_timestamps_allowed(self):
        # A phase that completes inside the timer's resolution is legal.
        trace = HotPathTrace("ZERO", 500, 500, 500, 500, 500, 500)
        self.assertEqual(
            LatencyBudgetAccountingEngine().decompose_trace(trace).total_tick_to_trade_ns, 0
        )

    def test_non_integer_timestamp_rejected(self):
        with self.assertRaises(TypeError):
            HotPathTrace("F", 0, 1.5, 2, 3, 4, 5)

    def test_bool_timestamp_rejected(self):
        with self.assertRaises(TypeError):
            HotPathTrace("B", 0, True, 2, 3, 4, 5)

    def test_numpy_integer_timestamps_accepted_and_normalised(self):
        # Traces loaded from a NumPy/pandas telemetry pipeline arrive as int64.
        trace = HotPathTrace("NP", *(np.int64(v) for v in (0, 10, 20, 30, 40, 55)))
        self.assertIsInstance(trace.t5_nic_egress_ns, int)
        self.assertNotIsInstance(trace.t5_nic_egress_ns, np.integer)
        breakdown = LatencyBudgetAccountingEngine().decompose_trace(trace)
        self.assertEqual(breakdown.total_tick_to_trade_ns, 55)

    def test_blank_trace_id_rejected(self):
        with self.assertRaises(ValueError):
            HotPathTrace("   ", 0, 1, 2, 3, 4, 5)


class TestEngineConfiguration(unittest.TestCase):
    def test_unknown_phase_sla_key_rejected(self):
        # Regression: an unrecognised key used to fall through to a 0 ns budget,
        # making an unrelated phase look like the bottleneck.
        with self.assertRaises(ValueError) as ctx:
            LatencyBudgetAccountingEngine(phase_slas_ns={"decode_ns": 1500})
        self.assertIn("unknown phase SLA key", str(ctx.exception))

    def test_incomplete_phase_sla_map_rejected(self):
        partial = {k: v for k, v in DEFAULT_PHASE_SLAS_NS.items() if k != "risk_to_encode_ns"}
        with self.assertRaises(ValueError) as ctx:
            LatencyBudgetAccountingEngine(phase_slas_ns=partial)
        self.assertIn("risk_to_encode_ns", str(ctx.exception))

    def test_negative_phase_sla_rejected(self):
        bad = dict(DEFAULT_PHASE_SLAS_NS, signal_to_risk_ns=-1)
        with self.assertRaises(ValueError):
            LatencyBudgetAccountingEngine(phase_slas_ns=bad)

    def test_non_positive_total_sla_rejected(self):
        with self.assertRaises(ValueError):
            LatencyBudgetAccountingEngine(total_sla_ns=0)

    def test_phase_slas_exceeding_total_is_warned_not_rejected(self):
        with self.assertLogs("colocation_latency_budget_accounting", level="WARNING") as logs:
            LatencyBudgetAccountingEngine(total_sla_ns=5000)  # defaults sum to 8000
        self.assertTrue(any("above the 5000 ns total budget" in m for m in logs.output))


class TestSlaAudit(unittest.TestCase):
    def setUp(self):
        self.engine = LatencyBudgetAccountingEngine(total_sla_ns=10000)  # 10 us

    def test_normal_trace_no_breach(self):
        trace = _trace("TR_1", [1000, 1000, 1000, 1000, 1000])
        report = self.engine.audit_trace(trace)

        self.assertFalse(report.is_sla_breach)
        self.assertEqual(report.total_t2t_ns, 5000)
        self.assertIsNone(report.primary_bottleneck_phase)
        self.assertEqual(report.phase_excess_ns, {})

    def test_exact_sla_is_not_a_breach(self):
        # Boundary: a trace consuming exactly the budget stays inside it.
        report = self.engine.audit_trace(_trace("EDGE", [2000, 2000, 2000, 2000, 2000]))
        self.assertEqual(report.total_t2t_ns, 10000)
        self.assertFalse(report.is_sla_breach)

        over = self.engine.audit_trace(_trace("EDGE2", [2000, 2000, 2000, 2000, 2001]))
        self.assertTrue(over.is_sla_breach)

    def test_sla_breach_bottleneck_identification(self):
        # Risk phase runs 8,000 ns against a 1,500 ns budget -> +6,500 ns excess,
        # the largest of any phase, so it is the primary bottleneck.
        report = self.engine.audit_trace(_trace("TR_2", [1000, 1000, 8000, 1000, 4000]))

        self.assertTrue(report.is_sla_breach)
        self.assertEqual(report.total_t2t_ns, 15000)
        self.assertEqual(report.primary_bottleneck_phase, "signal_to_risk_ns")
        self.assertEqual(report.phase_excess_ns["signal_to_risk_ns"], 6500)
        self.assertEqual(report.phase_excess_ns["encode_to_egress_ns"], 2500)

    def test_breach_with_every_phase_inside_its_own_budget_still_names_a_phase(self):
        # Regression: with total_sla_ns below the sum of phase budgets, every
        # phase excess is negative. The old `max_excess = -1` sentinel left
        # primary_bottleneck_phase as None on a reported breach.
        engine = LatencyBudgetAccountingEngine(total_sla_ns=5000)
        report = engine.audit_trace(_trace("TR_3", [1100, 1100, 1100, 1100, 1100]))

        self.assertTrue(report.is_sla_breach)
        self.assertIsNotNone(report.primary_bottleneck_phase)
        # Closest to its own budget: 1100 - 1500 = -400 for four phases, but
        # decode_to_signal_ns has a 2000 ns budget (-900), so hot-path order
        # picks the first of the -400 phases.
        self.assertEqual(report.primary_bottleneck_phase, "ingress_to_decode_ns")
        self.assertEqual(report.phase_excess_ns["decode_to_signal_ns"], -900)

    def test_bottleneck_tie_break_follows_hot_path_order(self):
        engine = LatencyBudgetAccountingEngine(
            phase_slas_ns={name: 1000 for name in PHASE_NAMES}, total_sla_ns=4000
        )
        report = engine.audit_trace(_trace("TIE", [3000, 3000, 100, 100, 100]))
        self.assertTrue(report.is_sla_breach)
        self.assertEqual(report.primary_bottleneck_phase, "ingress_to_decode_ns")


class TestPercentiles(unittest.TestCase):
    def setUp(self):
        self.engine = LatencyBudgetAccountingEngine()

    def _uniform_batch(self, n):
        # total_t2t_ns takes the values 1..n, carried entirely by the final phase;
        # the four earlier phases are 0 ns.
        traces = []
        for i in range(1, n + 1):
            base = i * 10_000_000
            traces.append(
                HotPathTrace(
                    trace_id=f"T_{i}",
                    t0_nic_ingress_ns=base,
                    t1_decode_ns=base,
                    t2_signal_ns=base,
                    t3_risk_ns=base,
                    t4_encode_ns=base,
                    t5_nic_egress_ns=base + i,
                )
            )
        return traces

    def test_percentiles_match_independently_derived_values(self):
        # For the sample {1..1000}, NumPy's default linear percentile at q takes
        # the value at fractional rank q/100 * (n - 1) = q/100 * 999, then adds
        # one (values are consecutive integers starting at 1):
        #   p50   -> rank 499.5   -> 500.5
        #   p99   -> rank 989.01  -> 990.01 -> 990.0 at 1 dp
        #   p99.9 -> rank 998.001 -> 999.001 -> 999.0 at 1 dp
        # mean of 1..1000 = 500.5.
        stats = self.engine.compute_percentiles(self._uniform_batch(1000))["total_t2t_ns"]

        self.assertEqual(stats["count"], 1000.0)
        self.assertEqual(stats["mean"], 500.5)
        self.assertEqual(stats["p50"], 500.5)
        self.assertEqual(stats["p99"], 990.0)
        self.assertEqual(stats["p99_9"], 999.0)

    def test_every_phase_is_reported(self):
        stats = self.engine.compute_percentiles(self._uniform_batch(10))
        self.assertEqual(set(stats), {"total_t2t_ns", *PHASE_NAMES})
        # The batch puts the whole duration in the final phase; the rest are 0 ns.
        for phase in PHASE_NAMES[:-1]:
            self.assertEqual(stats[phase]["p99_9"], 0.0)
            self.assertEqual(stats[phase]["mean"], 0.0)
        self.assertEqual(stats["encode_to_egress_ns"], stats["total_t2t_ns"])

    def test_percentiles_are_monotone_in_q(self):
        stats = self.engine.compute_percentiles(self._uniform_batch(1000))["total_t2t_ns"]
        self.assertLessEqual(stats["p50"], stats["p95"])
        self.assertLessEqual(stats["p95"], stats["p99"])
        self.assertLessEqual(stats["p99"], stats["p99_9"])

    def test_small_batch_warns_that_tail_percentiles_are_interpolated(self):
        with self.assertLogs("colocation_latency_budget_accounting", level="WARNING") as logs:
            stats = self.engine.compute_percentiles(self._uniform_batch(10))
        joined = " ".join(logs.output)
        self.assertIn("p99", joined)
        self.assertIn("p99_9", joined)
        self.assertEqual(stats["total_t2t_ns"]["count"], 10.0)

    def test_sufficient_batch_does_not_warn(self):
        engine = LatencyBudgetAccountingEngine()
        with self.assertNoLogs("colocation_latency_budget_accounting", level="WARNING"):
            engine.compute_percentiles(self._uniform_batch(1000))

    def test_empty_batch_returns_no_stats(self):
        with self.assertLogs("colocation_latency_budget_accounting", level="WARNING"):
            self.assertEqual(self.engine.compute_percentiles([]), {})


if __name__ == "__main__":
    unittest.main()

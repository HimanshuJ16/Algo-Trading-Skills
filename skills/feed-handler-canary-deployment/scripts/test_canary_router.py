"""Unit tests for feed-handler-canary-deployment skill."""
import math
import os
import subprocess
import sys
import threading
import unittest

from canary_router import (
    CanaryStatus,
    FeedHandlerCanaryRouter,
)

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _universe(count: int) -> list:
    """Synthetic symbol universe, independent of any real listing."""
    return [f"SYM{i:05d}" for i in range(count)]


class TestSymbolRouting(unittest.TestCase):

    def setUp(self):
        self.router = FeedHandlerCanaryRouter(
            canary_percentage=20.0,
            canary_symbols=["AAPL", "MSFT"],
            max_allowed_error_rate=0.05,
        )

    def test_whitelisted_symbols_route_to_canary(self):
        for sym in ("AAPL", "MSFT"):
            decision = self.router.route_symbol(sym)
            self.assertTrue(decision.is_canary)
            self.assertEqual(decision.version_tag, "V_canary")
            self.assertEqual(decision.reason, "whitelist")

    def test_symbol_is_normalised_before_routing(self):
        self.assertTrue(self.router.route_symbol("  aapl ").is_canary)
        self.assertEqual(self.router.route_symbol(" aapl ").symbol, "AAPL")

    def test_invalid_symbols_are_rejected(self):
        for bad in ("", "   ", None, 42):
            with self.assertRaises(ValueError):
                self.router.route_symbol(bad)

    def test_allocation_matches_requested_percentage(self):
        # Behavioural check: over a large universe the realised share should sit
        # close to the requested one. Independent of the hash implementation.
        universe = _universe(5000)
        for pct in (5.0, 10.0, 50.0):
            router = FeedHandlerCanaryRouter(canary_percentage=pct)
            realised = sum(1 for s in universe if router.route_symbol(s).is_canary) / len(universe)
            self.assertAlmostEqual(realised, pct / 100.0, delta=0.02,
                                   msg=f"allocation drift at {pct}%")

    def test_zero_and_full_allocation_are_exact(self):
        universe = _universe(500)
        none_router = FeedHandlerCanaryRouter(canary_percentage=0.0)
        all_router = FeedHandlerCanaryRouter(canary_percentage=100.0)
        self.assertEqual(sum(1 for s in universe if none_router.route_symbol(s).is_canary), 0)
        self.assertEqual(sum(1 for s in universe if all_router.route_symbol(s).is_canary), len(universe))

    def test_sub_one_percent_allocation_is_representable(self):
        # Regression: 100-bucket allocation cannot express 0.5% and silently
        # rounds it up to a whole bucket (1%).
        universe = _universe(20000)
        router = FeedHandlerCanaryRouter(canary_percentage=0.5)
        realised = sum(1 for s in universe if router.route_symbol(s).is_canary) / len(universe)
        self.assertLess(realised, 0.008)
        self.assertGreater(realised, 0.002)

    def test_ramping_up_only_adds_symbols(self):
        # A symbol already served by the canary must not move back to baseline
        # when the allocation is ramped, or the ramp reshuffles ownership.
        universe = _universe(2000)
        router = FeedHandlerCanaryRouter(canary_percentage=10.0)
        at_10 = {s for s in universe if router.route_symbol(s).is_canary}
        router.set_canary_percentage(50.0, authorised_by="release-manager")
        at_50 = {s for s in universe if router.route_symbol(s).is_canary}
        self.assertTrue(at_10.issubset(at_50))
        self.assertGreater(len(at_50), len(at_10))

    def test_routing_is_stable_across_router_instances(self):
        universe = _universe(300)
        a = FeedHandlerCanaryRouter(canary_percentage=25.0)
        b = FeedHandlerCanaryRouter(canary_percentage=25.0)
        self.assertEqual(
            [a.route_symbol(s).is_canary for s in universe],
            [b.route_symbol(s).is_canary for s in universe],
        )

    def test_routing_is_stable_across_processes(self):
        # Regression against bucketing on Python's built-in hash(): str hashing
        # is salted per process (PYTHONHASHSEED), so two feed handler processes
        # would disagree about which symbols the canary owns.
        code = (
            "import sys;sys.path.insert(0, sys.argv[1]);"
            "from canary_router import FeedHandlerCanaryRouter as R;"
            "r=R(canary_percentage=30.0);"
            "print(''.join('1' if r.route_symbol(f'SYM{i:05d}').is_canary else '0' "
            "for i in range(200)))"
        )
        outputs = []
        for seed in ("0", "1", "12345"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            result = subprocess.run(
                [sys.executable, "-c", code, SCRIPT_DIR],
                capture_output=True, text=True, env=env, check=True,
            )
            outputs.append(result.stdout.strip())
        self.assertEqual(len(set(outputs)), 1, "routing differs across process hash seeds")
        self.assertIn("1", outputs[0])


class TestConfigurationValidation(unittest.TestCase):

    def test_out_of_range_percentage_rejected(self):
        for bad in (-1.0, 100.5, float("nan"), float("inf"), "10", True):
            with self.assertRaises(ValueError, msg=f"accepted {bad!r}"):
                FeedHandlerCanaryRouter(canary_percentage=bad)

    def test_out_of_range_error_rate_rejected(self):
        for bad in (-0.1, 1.5, float("nan")):
            with self.assertRaises(ValueError, msg=f"accepted {bad!r}"):
                FeedHandlerCanaryRouter(max_allowed_error_rate=bad)

    def test_invalid_tolerance_and_counters_rejected(self):
        with self.assertRaises(ValueError):
            FeedHandlerCanaryRouter(price_tolerance=-0.001)
        with self.assertRaises(ValueError):
            FeedHandlerCanaryRouter(price_tolerance=float("nan"))
        with self.assertRaises(ValueError):
            FeedHandlerCanaryRouter(min_ticks_before_rollback=0)
        with self.assertRaises(ValueError):
            FeedHandlerCanaryRouter(max_allowed_exceptions=-1)

    def test_ramp_rejects_out_of_range_percentage(self):
        router = FeedHandlerCanaryRouter(canary_percentage=10.0)
        with self.assertRaises(ValueError):
            router.set_canary_percentage(101.0)
        self.assertEqual(router.canary_percentage, 10.0)


class TestComparativeAudit(unittest.TestCase):

    def _router(self, **kwargs):
        params = dict(canary_percentage=20.0, max_allowed_error_rate=0.05)
        params.update(kwargs)
        return FeedHandlerCanaryRouter(**params)

    def test_identical_prices_agree(self):
        router = self._router()
        self.assertTrue(router.audit_tick_pair("AAPL", 150.25, 150.25))
        self.assertEqual(router.get_audit_summary(["AAPL"]).price_mismatch_count, 0)

    def test_nan_canary_price_is_a_mismatch(self):
        # Regression: a relative-difference check scores NaN as agreement,
        # because NaN > tolerance is False.
        router = self._router()
        router.audit_tick_pair("AAPL", 150.0, float("nan"))
        self.assertEqual(router.get_audit_summary(["AAPL"]).price_mismatch_count, 1)

    def test_infinite_canary_price_is_a_mismatch(self):
        router = self._router()
        router.audit_tick_pair("AAPL", 150.0, float("inf"))
        self.assertEqual(router.get_audit_summary(["AAPL"]).price_mismatch_count, 1)

    def test_zero_baseline_price_is_a_mismatch(self):
        # Regression: guarding the divisor by returning a zero difference marks
        # a corrupt baseline tick as a perfect match.
        router = self._router()
        router.audit_tick_pair("AAPL", 0.0, 150.0)
        self.assertEqual(router.get_audit_summary(["AAPL"]).price_mismatch_count, 1)

    def test_negative_and_zero_canary_price_are_mismatches(self):
        router = self._router()
        router.audit_tick_pair("AAPL", 150.0, 0.0)
        router.audit_tick_pair("AAPL", 150.0, -150.0)
        self.assertEqual(router.get_audit_summary(["AAPL"]).price_mismatch_count, 2)

    def test_default_tolerance_requires_exact_agreement(self):
        # Both handlers decode the same message, so a sub-basis-point drift is
        # a decode defect, not acceptable noise.
        router = self._router()
        router.audit_tick_pair("AAPL", 150.0, 150.0001)
        self.assertEqual(router.get_audit_summary(["AAPL"]).price_mismatch_count, 1)

    def test_explicit_tolerance_admits_small_drift(self):
        router = self._router(price_tolerance=0.001)
        router.audit_tick_pair("AAPL", 150.0, 150.0001)   # 6.7e-7 relative
        router.audit_tick_pair("AAPL", 150.0, 150.30)     # 2.0e-3 relative
        summary = router.get_audit_summary(["AAPL"])
        self.assertEqual(summary.price_mismatch_count, 1)
        self.assertEqual(summary.total_evaluated_ticks, 2)

    def test_tolerance_boundary_is_inclusive(self):
        # Exactly at tolerance passes; one ulp beyond fails.
        router = self._router(price_tolerance=0.01)
        self.assertIsNone(router._classify_mismatch(100.0, 101.0))
        self.assertEqual(router._classify_mismatch(100.0, 101.1), "tolerance_breach")

    def test_non_numeric_price_rejected(self):
        router = self._router()
        with self.assertRaises(ValueError):
            router.audit_tick_pair("AAPL", "150.0", 150.0)
        with self.assertRaises(ValueError):
            router.audit_tick_pair("AAPL", 150.0, None)

    def test_unaligned_sequence_numbers_raise_and_do_not_count(self):
        router = self._router()
        with self.assertRaises(ValueError):
            router.audit_tick_pair("AAPL", 150.0, 151.0, sequence_number=7,
                                   canary_sequence_number=8)
        summary = router.get_audit_summary(["AAPL"])
        self.assertEqual(summary.total_evaluated_ticks, 0)
        self.assertEqual(summary.price_mismatch_count, 0)

    def test_aligned_sequence_numbers_are_audited(self):
        router = self._router()
        self.assertTrue(
            router.audit_tick_pair("AAPL", 150.0, 150.0, sequence_number=7,
                                   canary_sequence_number=7)
        )
        self.assertEqual(router.get_audit_summary(["AAPL"]).total_evaluated_ticks, 1)


class TestAutoRollback(unittest.TestCase):

    def setUp(self):
        self.ticks = [1_000.0]
        self.router = FeedHandlerCanaryRouter(
            canary_percentage=20.0,
            canary_symbols=["AAPL", "MSFT"],
            max_allowed_error_rate=0.05,
            min_ticks_before_rollback=10,
            clock=lambda: self.ticks[0],
        )

    def test_rate_breaker_trips_and_reverts_all_symbols(self):
        for _ in range(8):
            self.assertTrue(self.router.audit_tick_pair("AAPL", 150.0, 150.0))

        # 9th tick mismatches but the sample is still below the minimum.
        self.assertTrue(self.router.audit_tick_pair("AAPL", 150.0, 160.0))
        self.assertFalse(self.router.is_rolled_back)

        # 10th tick takes the rate to 20% over 10 ticks, above the 5% limit.
        self.assertFalse(self.router.audit_tick_pair("AAPL", 150.0, 170.0))
        self.assertTrue(self.router.is_rolled_back)

        for sym in ("AAPL", "MSFT", "SYM00001"):
            decision = self.router.route_symbol(sym)
            self.assertFalse(decision.is_canary)
            self.assertEqual(decision.version_tag, "V_stable")
            self.assertEqual(decision.reason, "rolled_back")

    def test_min_sample_gate_prevents_single_tick_rollback(self):
        self.assertTrue(self.router.audit_tick_pair("AAPL", 150.0, 900.0))
        self.assertFalse(self.router.is_rolled_back)

    def test_audits_after_rollback_are_inert(self):
        self.router.force_rollback("operator halted deployment", authorised_by="oncall")
        self.assertFalse(self.router.audit_tick_pair("AAPL", 150.0, 150.0))
        summary = self.router.get_audit_summary(["AAPL"])
        self.assertEqual(summary.total_evaluated_ticks, 0)
        self.assertEqual(summary.status, CanaryStatus.ROLLED_BACK)

    def test_rollback_is_idempotent_and_keeps_the_first_reason(self):
        self.router.force_rollback("first reason")
        self.router.force_rollback("second reason")
        self.router.record_canary_exception("AAPL", "third reason")
        self.assertEqual(self.router.rollback_reason, "first reason")
        rollbacks = [e for e in self.router.events if "ROLLBACK" in e.event_type]
        self.assertEqual(len(rollbacks), 1)

    def test_single_decoder_exception_rolls_back_by_default(self):
        self.router.record_canary_exception("AAPL", "struct.error: unpack requires 8 bytes")
        self.assertTrue(self.router.is_rolled_back)
        self.assertIn("struct.error", self.router.rollback_reason)

    def test_exception_budget_is_respected(self):
        router = FeedHandlerCanaryRouter(
            canary_percentage=20.0,
            max_allowed_error_rate=0.5,
            max_allowed_exceptions=2,
            min_ticks_before_rollback=100,
        )
        router.record_canary_exception("AAPL", "transient")
        router.record_canary_exception("AAPL", "transient")
        self.assertFalse(router.is_rolled_back)
        router.record_canary_exception("AAPL", "third")
        self.assertTrue(router.is_rolled_back)

    def test_ramp_is_refused_after_rollback(self):
        self.router.force_rollback("audit failure")
        with self.assertRaises(RuntimeError):
            self.router.set_canary_percentage(50.0)
        with self.assertRaises(RuntimeError):
            self.router.promote_to_full(authorised_by="release-manager")

    def test_events_are_timestamped_and_attributed(self):
        self.ticks[0] = 1_700_000_000.0
        self.router.set_canary_percentage(50.0, authorised_by="release-manager")
        self.ticks[0] = 1_700_000_060.0
        self.router.force_rollback("latency regression", authorised_by="oncall")

        events = self.router.events
        self.assertEqual([e.event_type for e in events], ["RAMP", "MANUAL_ROLLBACK"])
        self.assertEqual([e.timestamp for e in events], [1_700_000_000.0, 1_700_000_060.0])
        self.assertEqual(events[0].authorised_by, "release-manager")
        self.assertEqual(events[1].authorised_by, "oncall")

    def test_promotion_records_ramp_and_promotion_events(self):
        self.router.promote_to_full(authorised_by="release-manager")
        self.assertEqual(self.router.canary_percentage, 100.0)
        self.assertEqual(
            [e.event_type for e in self.router.events], ["RAMP", "PROMOTION"]
        )


class TestAuditSummary(unittest.TestCase):

    def test_healthy_summary_reports_split_and_zero_error_rate(self):
        router = FeedHandlerCanaryRouter(canary_percentage=10.0)
        universe = _universe(1000)
        summary = router.get_audit_summary(universe)
        self.assertEqual(summary.status, CanaryStatus.HEALTHY)
        self.assertEqual(summary.canary_symbols_count + summary.baseline_symbols_count, 1000)
        self.assertEqual(summary.error_rate, 0.0)
        self.assertEqual(summary.canary_percentage, 10.0)
        self.assertFalse(summary.is_rolled_back)
        self.assertIsNone(summary.rollback_reason)

    def test_audit_fail_status_without_rollback(self):
        router = FeedHandlerCanaryRouter(canary_percentage=10.0, max_allowed_error_rate=0.5)
        for _ in range(9):
            router.audit_tick_pair("AAPL", 150.0, 150.0)
        router.audit_tick_pair("AAPL", 150.0, 151.0)
        summary = router.get_audit_summary(["AAPL"])
        self.assertEqual(summary.status, CanaryStatus.AUDIT_FAIL)
        self.assertFalse(summary.is_rolled_back)
        self.assertAlmostEqual(summary.error_rate, 0.1)

    def test_rolled_back_summary_reports_zero_canary_symbols(self):
        router = FeedHandlerCanaryRouter(canary_percentage=100.0)
        router.force_rollback("decoder crash")
        summary = router.get_audit_summary(_universe(50))
        self.assertTrue(summary.is_rolled_back)
        self.assertEqual(summary.canary_symbols_count, 0)
        self.assertEqual(summary.baseline_symbols_count, 50)
        self.assertIn("decoder crash", summary.message)

    def test_error_rate_is_zero_before_any_tick(self):
        router = FeedHandlerCanaryRouter(canary_percentage=10.0)
        self.assertEqual(router.error_rate, 0.0)
        self.assertFalse(math.isnan(router.error_rate))


class TestConcurrency(unittest.TestCase):

    def test_counters_survive_concurrent_feed_threads(self):
        router = FeedHandlerCanaryRouter(
            canary_percentage=10.0,
            max_allowed_error_rate=1.0,
            min_ticks_before_rollback=1,
        )
        threads_count, per_thread = 8, 500

        def worker():
            for _ in range(per_thread):
                router.audit_tick_pair("AAPL", 150.0, 150.0)

        threads = [threading.Thread(target=worker) for _ in range(threads_count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(router.total_ticks_processed, threads_count * per_thread)
        self.assertFalse(router.is_rolled_back)

    def test_concurrent_rollback_records_exactly_one_event(self):
        router = FeedHandlerCanaryRouter(canary_percentage=10.0)
        barrier = threading.Barrier(8)

        def worker(i):
            barrier.wait()
            router.force_rollback(f"reason-{i}")

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(router.is_rolled_back)
        self.assertEqual(len(router.events), 1)


if __name__ == "__main__":
    unittest.main()

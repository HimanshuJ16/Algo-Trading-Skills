import logging
import os
import random
import threading
import time
import unittest
from unittest import mock

from chaos_monkey_trading_simulator import (
    DEFAULT_ENABLE_ENV_VAR,
    ChaosConfig,
    ChaosConfigError,
    ChaosInjector,
    FaultStats,
    MockFixClient,
    SimulatedProcessCrash,
)


def setUpModule():
    # The injector logs every fault by design; several hundred of them would
    # drown the shared repo-wide test run.
    logging.disable(logging.CRITICAL)


def tearDownModule():
    logging.disable(logging.NOTSET)


class TestChaosConfigValidation(unittest.TestCase):
    """A misconfigured injector produces an experiment that lies about the system."""

    def test_probability_above_one_rejected(self):
        # 10 meaning "10 percent" would drop every single message and read as a
        # total resilience failure.
        with self.assertRaises(ChaosConfigError):
            ChaosConfig(drop_probability=10)
        with self.assertRaises(ChaosConfigError):
            ChaosConfig(crash_probability=1.0001)

    def test_negative_values_rejected(self):
        for kwargs in ({"drop_probability": -0.1}, {"latency_ms": -1}, {"jitter_ms": -0.5}):
            with self.subTest(**kwargs):
                with self.assertRaises(ChaosConfigError):
                    ChaosConfig(**kwargs)

    def test_non_finite_and_non_numeric_rejected(self):
        for kwargs in ({"latency_ms": float("nan")}, {"latency_ms": float("inf")},
                       {"drop_probability": "0.5"}, {"seed": "42"}):
            with self.subTest(**kwargs):
                with self.assertRaises(ChaosConfigError):
                    ChaosConfig(**kwargs)

    def test_boundary_values_accepted(self):
        cfg = ChaosConfig(latency_ms=0, jitter_ms=0, drop_probability=1.0, crash_probability=0.0)
        self.assertEqual(cfg.drop_probability, 1.0)

    def test_injector_rejects_non_config(self):
        with self.assertRaises(ChaosConfigError):
            ChaosInjector({"drop_probability": 1.0}, enabled=True)

    def test_execute_rejects_non_callable(self):
        injector = ChaosInjector(ChaosConfig(), enabled=True)
        with self.assertRaises(TypeError):
            injector.execute("not-a-callable")


class TestActivationGate(unittest.TestCase):
    """
    Fail-closed activation is the blast-radius control of last resort: a chaos
    wrapper left in a path that reaches production must be inert.
    """

    def setUp(self):
        self.client = MockFixClient()
        self.lethal = ChaosConfig(drop_probability=1.0, crash_probability=1.0, latency_ms=50)

    def test_disabled_by_default_when_env_absent(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            injector = ChaosInjector(self.lethal)
        self.assertFalse(injector.enabled)

        start = time.perf_counter()
        result = injector.execute(self.client.send_order, "ORD-1")
        elapsed_ms = (time.perf_counter() - start) * 1000

        # Transparent: the call goes through, unfaulted and undelayed.
        self.assertEqual(result, "ACK-ORD-1")
        self.assertLess(elapsed_ms, 50.0)
        self.assertEqual(injector.stats.faults_injected, 0)
        self.assertEqual(injector.stats.passthrough_calls, 1)
        self.assertEqual(injector.stats.calls, 1)

    def test_env_var_enables_injection(self):
        with mock.patch.dict(os.environ, {DEFAULT_ENABLE_ENV_VAR: "true"}, clear=True):
            injector = ChaosInjector(ChaosConfig(drop_probability=1.0))
        self.assertTrue(injector.enabled)
        with self.assertRaises(ConnectionAbortedError):
            injector.execute(self.client.send_order, "ORD-2")

    def test_falsey_env_value_does_not_enable(self):
        with mock.patch.dict(os.environ, {DEFAULT_ENABLE_ENV_VAR: "false"}, clear=True):
            injector = ChaosInjector(ChaosConfig(drop_probability=1.0))
        self.assertFalse(injector.enabled)
        self.assertEqual(injector.execute(self.client.send_order, "ORD-3"), "ACK-ORD-3")

    def test_explicit_enabled_false_overrides_env(self):
        with mock.patch.dict(os.environ, {DEFAULT_ENABLE_ENV_VAR: "1"}, clear=True):
            injector = ChaosInjector(ChaosConfig(drop_probability=1.0), enabled=False)
        self.assertFalse(injector.enabled)
        self.assertEqual(injector.execute(self.client.send_order, "ORD-4"), "ACK-ORD-4")

    def test_activation_resolved_once_at_construction(self):
        # An experiment must not change shape halfway through because something
        # else in the process mutated the environment.
        with mock.patch.dict(os.environ, {}, clear=True):
            injector = ChaosInjector(ChaosConfig(drop_probability=1.0))
            os.environ[DEFAULT_ENABLE_ENV_VAR] = "1"
            self.assertEqual(injector.execute(self.client.send_order, "ORD-5"), "ACK-ORD-5")


class TestFaultInjection(unittest.TestCase):

    def setUp(self):
        self.client = MockFixClient()

    def test_latency_injection(self):
        injector = ChaosInjector(ChaosConfig(latency_ms=50, jitter_ms=0), enabled=True)

        start = time.perf_counter()
        result = injector.execute(self.client.send_order, "ORD-1")
        elapsed_ms = (time.perf_counter() - start) * 1000

        self.assertEqual(result, "ACK-ORD-1")
        # time.sleep() guarantees *at least* the requested interval, so the
        # full 50 ms is a hard floor, not an approximation.
        self.assertGreaterEqual(elapsed_ms, 50.0)
        self.assertEqual(injector.stats.delayed_calls, 1)
        self.assertAlmostEqual(injector.stats.total_delay_ms, 50.0, places=6)

    def test_jitter_stays_within_configured_band(self):
        injector = ChaosInjector(ChaosConfig(latency_ms=1, jitter_ms=4, seed=7), enabled=True)
        for _ in range(50):
            injector.execute(self.client.send_order, "ORD-J")
        mean_delay = injector.stats.total_delay_ms / injector.stats.calls
        self.assertGreaterEqual(mean_delay, 1.0)
        self.assertLess(mean_delay, 5.0)

    def test_drop_probability(self):
        injector = ChaosInjector(ChaosConfig(drop_probability=1.0), enabled=True)
        with self.assertRaises(ConnectionAbortedError):
            injector.execute(self.client.send_order, "ORD-2")
        self.assertEqual(injector.stats.drops_injected, 1)

    def test_drop_is_delayed_not_instantaneous(self):
        # Regression: v1 evaluated the drop before the delay, so a dropped
        # message failed instantly and could never exercise a client's read
        # timeout — the failure mode that actually hurts in production.
        injector = ChaosInjector(ChaosConfig(latency_ms=40, drop_probability=1.0), enabled=True)
        start = time.perf_counter()
        with self.assertRaises(ConnectionAbortedError):
            injector.execute(self.client.send_order, "ORD-3")
        self.assertGreaterEqual((time.perf_counter() - start) * 1000, 40.0)

    def test_crash_probability(self):
        injector = ChaosInjector(ChaosConfig(crash_probability=1.0), enabled=True)
        with self.assertRaises(SimulatedProcessCrash):
            injector.execute(self.client.send_order, "ORD-4")
        self.assertEqual(injector.stats.crashes_injected, 1)

    def test_crash_is_not_swallowed_by_except_exception(self):
        # A real SIGKILL is not recoverable by application error handling, so a
        # simulated one must not be either.
        injector = ChaosInjector(ChaosConfig(crash_probability=1.0), enabled=True)
        with self.assertRaises(SimulatedProcessCrash):
            try:
                injector.execute(self.client.send_order, "ORD-5")
            except Exception:  # noqa: BLE001 - deliberately broad, that is the test
                self.fail("SimulatedProcessCrash must not be catchable as Exception")

    def test_crash_is_not_system_exit(self):
        # Regression: v1 raised SystemExit. threading swallows SystemExit in a
        # worker thread with no traceback, so a crash injected into a feed
        # handler thread vanished and the experiment reported success.
        injector = ChaosInjector(ChaosConfig(crash_probability=1.0), enabled=True)
        self.assertFalse(issubclass(SimulatedProcessCrash, SystemExit))

        captured = []
        original_hook = threading.excepthook
        threading.excepthook = captured.append
        try:
            worker = threading.Thread(
                target=injector.execute, args=(self.client.send_order, "ORD-6"))
            worker.start()
            worker.join(timeout=5)
        finally:
            threading.excepthook = original_hook

        self.assertEqual(len(captured), 1, "simulated crash in a worker thread went unreported")
        self.assertIs(captured[0].exc_type, SimulatedProcessCrash)

    def test_crash_takes_precedence_over_drop(self):
        injector = ChaosInjector(
            ChaosConfig(drop_probability=1.0, crash_probability=1.0), enabled=True)
        with self.assertRaises(SimulatedProcessCrash):
            injector.execute(self.client.send_order, "ORD-7")
        self.assertEqual(injector.stats.crashes_injected, 1)
        self.assertEqual(injector.stats.drops_injected, 0)

    def test_wrapped_function_receives_args_and_kwargs(self):
        seen = {}

        def target(a, b=None):
            seen["a"], seen["b"] = a, b
            return "OK"

        injector = ChaosInjector(ChaosConfig(), enabled=True)
        self.assertEqual(injector.execute(target, 1, b=2), "OK")
        self.assertEqual(seen, {"a": 1, "b": 2})

    def test_no_faults_configured_means_no_faults_recorded(self):
        injector = ChaosInjector(ChaosConfig(), enabled=True)
        for _ in range(20):
            injector.execute(self.client.send_order, "ORD-8")
        self.assertEqual(injector.stats.faults_injected, 0)
        self.assertEqual(injector.stats.delayed_calls, 0)
        self.assertEqual(injector.stats.calls, 20)


class TestDeterminism(unittest.TestCase):
    """
    references/standards.md requires a failing chaos run to be reproducible
    exactly. These tests are what make that claim true rather than aspirational.
    """

    def setUp(self):
        self.client = MockFixClient()

    def _outcome_sequence(self, config, trials=200):
        injector = ChaosInjector(config, enabled=True)
        outcomes = []
        for i in range(trials):
            try:
                injector.execute(self.client.send_order, f"ORD-{i}")
                outcomes.append("ACK")
            except ConnectionAbortedError:
                outcomes.append("DROP")
            except SimulatedProcessCrash:
                outcomes.append("CRASH")
        return outcomes

    def test_same_seed_reproduces_identical_outcome_sequence(self):
        config = ChaosConfig(drop_probability=0.3, crash_probability=0.1, seed=42)
        first = self._outcome_sequence(config)
        second = self._outcome_sequence(config)

        self.assertEqual(first, second)
        # Guard against a trivially-passing test: the sequence must actually
        # contain a mix of outcomes, otherwise equality proves nothing.
        self.assertGreaterEqual(len(set(first)), 3, f"degenerate outcome mix: {set(first)}")

    def test_different_seeds_produce_different_sequences(self):
        a = self._outcome_sequence(ChaosConfig(drop_probability=0.3, seed=1))
        b = self._outcome_sequence(ChaosConfig(drop_probability=0.3, seed=2))
        self.assertNotEqual(a, b)

    def test_drop_stream_is_independent_of_crash_probability(self):
        # Re-running a failed experiment with crashes disabled must reproduce the
        # same drops; a single shared stream would shift every subsequent draw.
        with_crashes = self._outcome_sequence(
            ChaosConfig(drop_probability=0.3, crash_probability=0.2, seed=99))
        without_crashes = self._outcome_sequence(
            ChaosConfig(drop_probability=0.3, crash_probability=0.0, seed=99))

        drops_a = {i for i, o in enumerate(with_crashes) if o == "DROP"}
        drops_b = {i for i, o in enumerate(without_crashes) if o == "DROP"}
        crashes = {i for i, o in enumerate(with_crashes) if o == "CRASH"}
        self.assertTrue(crashes, "expected some crashes with crash_probability=0.2")
        self.assertTrue(drops_b, "expected some drops with drop_probability=0.3")
        # The drop draws are identical in both runs; the only difference is that
        # a crash pre-empted the drop at those indices.
        self.assertEqual(drops_a, drops_b - crashes)

    def test_global_random_state_is_not_hijacked(self):
        # Regression: v1 called random.seed() in ChaosConfig.__init__, silently
        # re-seeding the RNG used by everything else in the process — including
        # the system under test's own retry backoff and jitter.
        saved_state = random.getstate()
        try:
            random.seed(1234)
            expected = [random.random() for _ in range(3)]

            random.seed(1234)
            injector = ChaosInjector(ChaosConfig(drop_probability=0.5, seed=42), enabled=True)
            for _ in range(5):
                try:
                    injector.execute(self.client.send_order, "ORD-X")
                except ConnectionAbortedError:
                    pass
            actual = [random.random() for _ in range(3)]
        finally:
            # This suite shares a process with every other skill's tests.
            random.setstate(saved_state)

        self.assertEqual(expected, actual)


class TestConcurrency(unittest.TestCase):

    def test_stats_are_consistent_under_concurrent_execution(self):
        injector = ChaosInjector(ChaosConfig(drop_probability=0.5, seed=5), enabled=True)
        client = MockFixClient()
        threads, per_thread = 8, 25

        def hammer():
            for i in range(per_thread):
                try:
                    injector.execute(client.send_order, f"ORD-{i}")
                except ConnectionAbortedError:
                    pass

        workers = [threading.Thread(target=hammer) for _ in range(threads)]
        for w in workers:
            w.start()
        for w in workers:
            w.join(timeout=30)

        stats = injector.stats
        self.assertIsInstance(stats, FaultStats)
        # Lost updates under concurrency would show up as a short count here.
        self.assertEqual(stats.calls, threads * per_thread)
        self.assertGreater(stats.drops_injected, 0)
        self.assertLess(stats.drops_injected, stats.calls)


if __name__ == "__main__":
    unittest.main()

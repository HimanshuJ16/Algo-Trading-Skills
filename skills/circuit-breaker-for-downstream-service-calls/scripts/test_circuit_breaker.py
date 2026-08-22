"""
Unit tests for the downstream-service circuit breaker.

Time is injected (``FakeClock``) rather than slept on, so the state machine is
tested deterministically: a test that sleeps 0.15s to cross a 0.1s boundary is
a test that fails on a loaded CI box for reasons unrelated to the breaker.
"""
import random
import threading
import time
import unittest

from circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerOpenException,
    CircuitState,
)


class FakeClock:
    """Monotonic, manually advanced clock."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = float(start)
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._now += float(seconds)


class BreakerTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.calls = 0
        self.cb = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout_sec=10.0,
            expected_exceptions=(ConnectionError,),
            clock=self.clock,
            name="test-dependency",
        )

    def _success(self):
        self.calls += 1
        return "SUCCESS"

    def _failure(self):
        self.calls += 1
        raise ConnectionError("Service Down")

    def _business_error(self):
        self.calls += 1
        raise ValueError("Invalid Input")

    def _trip(self, breaker=None, times=None):
        breaker = breaker or self.cb
        times = times if times is not None else breaker.failure_threshold
        for _ in range(times):
            with self.assertRaises(ConnectionError):
                breaker.call(self._failure)


class TestClosedState(BreakerTestBase):
    def test_success_passes_through(self):
        self.assertEqual(self.cb.call(self._success), "SUCCESS")
        self.assertEqual(self.cb.state, CircuitState.CLOSED)
        self.assertEqual(self.cb.snapshot().total_successes, 1)

    def test_arguments_are_forwarded(self):
        self.assertEqual(self.cb.call(lambda a, b=0: a + b, 2, b=3), 5)

    def test_business_error_does_not_trip_or_count(self):
        for _ in range(10):
            with self.assertRaises(ValueError):
                self.cb.call(self._business_error)
        self.assertEqual(self.cb.failure_count, 0)
        self.assertEqual(self.cb.state, CircuitState.CLOSED)
        self.assertEqual(self.cb.snapshot().total_failures, 0)

    def test_consecutive_failures_trip_the_circuit(self):
        self._trip()
        self.assertEqual(self.cb.state, CircuitState.OPEN)

    def test_success_resets_the_failure_count(self):
        """
        Regression: a breaker whose counter only ever increments trips on
        unrelated failures scattered across a session. Two failures, a success,
        then two more failures is not a broken dependency.
        """
        self._trip(times=2)
        self.cb.call(self._success)
        self.assertEqual(self.cb.failure_count, 0)
        self._trip(times=2)
        self.assertEqual(self.cb.state, CircuitState.CLOSED)

    def test_failure_window_ages_out_stale_failures(self):
        cb = CircuitBreaker(
            failure_threshold=3,
            recovery_timeout_sec=10.0,
            expected_exceptions=(ConnectionError,),
            failure_window_sec=60.0,
            clock=self.clock,
        )
        self._trip(breaker=cb, times=2)
        self.clock.advance(61.0)
        with self.assertRaises(ConnectionError):
            cb.call(self._failure)
        # The two old failures expired, so this is failure #1, not #3.
        self.assertEqual(cb.failure_count, 1)
        self.assertEqual(cb.state, CircuitState.CLOSED)


class TestOpenState(BreakerTestBase):
    def test_open_circuit_does_not_invoke_the_callable(self):
        self._trip()
        calls_before = self.calls
        with self.assertRaises(CircuitBreakerOpenException):
            self.cb.call(self._success)
        self.assertEqual(self.calls, calls_before, "downstream must not be called when OPEN")
        self.assertEqual(self.cb.snapshot().total_short_circuits, 1)

    def test_exception_reports_state_and_retry_estimate(self):
        self._trip()
        self.clock.advance(4.0)
        with self.assertRaises(CircuitBreakerOpenException) as ctx:
            self.cb.call(self._success)
        self.assertEqual(ctx.exception.state, CircuitState.OPEN)
        self.assertEqual(ctx.exception.name, "test-dependency")
        self.assertAlmostEqual(ctx.exception.retry_after_sec, 6.0, places=6)

    def test_state_read_does_not_transition_without_traffic(self):
        self._trip()
        self.clock.advance(100.0)
        self.assertEqual(self.cb.state, CircuitState.OPEN)


class TestHalfOpenState(BreakerTestBase):
    def test_probe_admitted_only_after_recovery_timeout(self):
        self._trip()
        self.clock.advance(9.99)
        with self.assertRaises(CircuitBreakerOpenException):
            self.cb.call(self._success)
        self.clock.advance(0.01)
        self.assertEqual(self.cb.call(self._success), "SUCCESS")
        self.assertEqual(self.cb.state, CircuitState.CLOSED)
        self.assertEqual(self.cb.failure_count, 0)

    def test_probe_failure_reopens_and_escalates_backoff(self):
        self._trip()
        self.clock.advance(10.0)
        with self.assertRaises(ConnectionError):
            self.cb.call(self._failure)
        self.assertEqual(self.cb.state, CircuitState.OPEN)

        # backoff_multiplier defaults to 2.0: the next window is 20s, not 10s.
        self.clock.advance(10.0)
        with self.assertRaises(CircuitBreakerOpenException):
            self.cb.call(self._success)
        self.clock.advance(10.0)
        self.assertEqual(self.cb.call(self._success), "SUCCESS")

    def test_backoff_is_capped_and_reset_on_close(self):
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout_sec=10.0,
            expected_exceptions=(ConnectionError,),
            backoff_multiplier=2.0,
            max_recovery_timeout_sec=25.0,
            clock=self.clock,
        )
        self._trip(breaker=cb, times=1)
        for _ in range(5):
            self.clock.advance(cb.snapshot().current_recovery_timeout_sec)
            with self.assertRaises(ConnectionError):
                cb.call(self._failure)
        self.assertEqual(cb.snapshot().current_recovery_timeout_sec, 25.0)

        self.clock.advance(25.0)
        cb.call(self._success)
        self.assertEqual(cb.state, CircuitState.CLOSED)
        self.assertEqual(cb.snapshot().current_recovery_timeout_sec, 10.0)

    def test_success_threshold_requires_consecutive_probes(self):
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout_sec=10.0,
            expected_exceptions=(ConnectionError,),
            half_open_success_threshold=2,
            clock=self.clock,
        )
        self._trip(breaker=cb, times=1)
        self.clock.advance(10.0)
        cb.call(self._success)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        cb.call(self._success)
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_only_one_probe_is_admitted_concurrently(self):
        """
        The whole point of HALF_OPEN is to send *one* request at a recovering
        service, not the whole backlog. A breaker without a lock lets every
        waiting thread through at once.
        """
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout_sec=10.0,
            expected_exceptions=(ConnectionError,),
            clock=self.clock,
        )
        self._trip(breaker=cb, times=1)
        self.clock.advance(10.0)

        probe_started = threading.Event()
        release_probe = threading.Event()
        probe_count = []

        def slow_probe():
            probe_count.append(1)
            probe_started.set()
            release_probe.wait(timeout=5.0)
            return "SUCCESS"

        probe_thread = threading.Thread(target=lambda: cb.call(slow_probe))
        probe_thread.start()
        self.assertTrue(probe_started.wait(timeout=5.0))

        with self.assertRaises(CircuitBreakerOpenException) as ctx:
            cb.call(slow_probe)
        self.assertEqual(ctx.exception.state, CircuitState.HALF_OPEN)

        release_probe.set()
        probe_thread.join(timeout=5.0)
        self.assertEqual(len(probe_count), 1)
        self.assertEqual(cb.state, CircuitState.CLOSED)

    def test_probe_slot_is_released_when_call_raises_unexpected_error(self):
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout_sec=10.0,
            expected_exceptions=(ConnectionError,),
            clock=self.clock,
        )
        self._trip(breaker=cb, times=1)
        self.clock.advance(10.0)
        with self.assertRaises(ValueError):
            cb.call(self._business_error)
        self.assertEqual(cb.state, CircuitState.HALF_OPEN)
        self.assertEqual(cb.snapshot().half_open_in_flight, 0)
        # The slot is free, so the next probe is admitted rather than rejected.
        cb.call(self._success)
        self.assertEqual(cb.state, CircuitState.CLOSED)


class TestSlowCalls(BreakerTestBase):
    def test_slow_success_counts_as_failure(self):
        clock = self.clock

        def slow_but_successful():
            clock.advance(3.0)
            return "SUCCESS"

        cb = CircuitBreaker(
            failure_threshold=2,
            recovery_timeout_sec=10.0,
            expected_exceptions=(ConnectionError,),
            slow_call_duration_sec=1.0,
            clock=clock,
        )
        self.assertEqual(cb.call(slow_but_successful), "SUCCESS")
        self.assertEqual(cb.failure_count, 1)
        cb.call(slow_but_successful)
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertEqual(cb.snapshot().total_slow_calls, 2)

    def test_fast_success_is_not_slow(self):
        cb = CircuitBreaker(
            failure_threshold=2,
            expected_exceptions=(ConnectionError,),
            slow_call_duration_sec=1.0,
            clock=self.clock,
        )
        cb.call(self._success)
        self.assertEqual(cb.failure_count, 0)
        self.assertEqual(cb.snapshot().total_slow_calls, 0)


class TestNestedBreakers(BreakerTestBase):
    def test_inner_open_circuit_does_not_trip_outer_breaker(self):
        inner = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout_sec=10.0,
            expected_exceptions=(ConnectionError,),
            clock=self.clock,
            name="inner",
        )
        outer = CircuitBreaker(
            failure_threshold=2,
            recovery_timeout_sec=10.0,
            # Deliberately over-broad, as a careless caller would configure it.
            expected_exceptions=(Exception,),
            clock=self.clock,
            name="outer",
        )
        self._trip(breaker=inner, times=1)
        for _ in range(5):
            with self.assertRaises(CircuitBreakerOpenException):
                outer.call(inner.call, self._success)
        self.assertEqual(outer.state, CircuitState.CLOSED)
        self.assertEqual(outer.failure_count, 0)


class TestManualOverride(BreakerTestBase):
    def test_reset_closes_the_circuit_and_clears_backoff(self):
        self._trip()
        self.clock.advance(10.0)
        with self.assertRaises(ConnectionError):
            self.cb.call(self._failure)
        self.cb.reset()
        self.assertEqual(self.cb.state, CircuitState.CLOSED)
        self.assertEqual(self.cb.failure_count, 0)
        self.assertEqual(self.cb.snapshot().current_recovery_timeout_sec, 10.0)
        self.assertEqual(self.cb.call(self._success), "SUCCESS")

    def test_force_open_blocks_calls_for_a_full_timeout(self):
        self.cb.force_open()
        self.assertEqual(self.cb.state, CircuitState.OPEN)
        with self.assertRaises(CircuitBreakerOpenException):
            self.cb.call(self._success)
        self.clock.advance(10.0)
        self.assertEqual(self.cb.call(self._success), "SUCCESS")


class TestObservability(BreakerTestBase):
    def test_state_changes_are_reported_to_the_callback(self):
        seen = []
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout_sec=10.0,
            expected_exceptions=(ConnectionError,),
            on_state_change=lambda snap: seen.append(snap.state),
            clock=self.clock,
        )
        self._trip(breaker=cb, times=1)
        self.clock.advance(10.0)
        cb.call(self._success)
        self.assertEqual(
            seen, [CircuitState.OPEN, CircuitState.HALF_OPEN, CircuitState.CLOSED]
        )

    def test_callback_failure_does_not_break_the_call_path(self):
        def exploding_callback(_snapshot):
            raise RuntimeError("alerting is down")

        cb = CircuitBreaker(
            failure_threshold=1,
            expected_exceptions=(ConnectionError,),
            on_state_change=exploding_callback,
            clock=self.clock,
        )
        with self.assertLogs("circuit_breaker", level="ERROR"):
            with self.assertRaises(ConnectionError):
                cb.call(self._failure)
        self.assertEqual(cb.state, CircuitState.OPEN)

    def test_snapshot_counters(self):
        self.cb.call(self._success)
        with self.assertRaises(ConnectionError):
            self.cb.call(self._failure)
        snap = self.cb.snapshot()
        self.assertEqual(snap.name, "test-dependency")
        self.assertEqual(snap.total_calls, 2)
        self.assertEqual(snap.total_successes, 1)
        self.assertEqual(snap.total_failures, 1)
        self.assertEqual(snap.state, CircuitState.CLOSED)


class TestCallStyles(BreakerTestBase):
    def test_decorator_preserves_metadata_and_guards_the_call(self):
        @self.cb.decorate
        def fetch_reference_data(symbol):
            """Docstring preserved."""
            return self._failure()

        self.assertEqual(fetch_reference_data.__name__, "fetch_reference_data")
        self.assertEqual(fetch_reference_data.__doc__, "Docstring preserved.")
        for _ in range(3):
            with self.assertRaises(ConnectionError):
                fetch_reference_data("AAPL")
        with self.assertRaises(CircuitBreakerOpenException):
            fetch_reference_data("AAPL")

    def test_guard_records_success_and_failure(self):
        with self.cb.guard():
            pass
        self.assertEqual(self.cb.snapshot().total_successes, 1)
        for _ in range(3):
            with self.assertRaises(ConnectionError):
                with self.cb.guard():
                    raise ConnectionError("Service Down")
        self.assertEqual(self.cb.state, CircuitState.OPEN)
        with self.assertRaises(CircuitBreakerOpenException):
            with self.cb.guard():
                self.fail("guard body must not execute while OPEN")

    def test_call_rejects_non_callable(self):
        with self.assertRaises(ValueError):
            self.cb.call("not-a-callable")


class TestConcurrency(BreakerTestBase):
    def test_concurrent_failures_leave_consistent_state(self):
        transitions = []
        cb = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout_sec=10.0,
            expected_exceptions=(ConnectionError,),
            on_state_change=transitions.append,
            clock=self.clock,
        )
        threads = 32
        barrier = threading.Barrier(threads)
        short_circuited = []

        def worker():
            barrier.wait(timeout=5.0)
            try:
                cb.call(self._failure)
            except ConnectionError:
                pass
            except CircuitBreakerOpenException:
                short_circuited.append(1)

        workers = [threading.Thread(target=worker) for _ in range(threads)]
        for t in workers:
            t.start()
        for t in workers:
            t.join(timeout=10.0)

        snap = cb.snapshot()
        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertGreaterEqual(snap.total_failures, 5)
        self.assertEqual(snap.half_open_in_flight, 0)
        self.assertEqual(snap.total_calls + len(short_circuited), threads)
        self.assertEqual(snap.total_short_circuits, len(short_circuited))
        # A burst of concurrent failures must open the circuit once, not once per
        # late-returning thread: repeated re-opens would extend the outage window
        # and spam the alerting path.
        self.assertEqual([t.state for t in transitions], [CircuitState.OPEN])

    def test_stale_probe_result_cannot_close_a_reopened_circuit(self):
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout_sec=10.0,
            expected_exceptions=(ConnectionError,),
            clock=self.clock,
        )
        self._trip(breaker=cb, times=1)
        self.clock.advance(10.0)

        probe_started = threading.Event()
        release_probe = threading.Event()

        def slow_probe():
            probe_started.set()
            release_probe.wait(timeout=5.0)
            return "SUCCESS"

        thread = threading.Thread(target=lambda: cb.call(slow_probe))
        thread.start()
        self.assertTrue(probe_started.wait(timeout=5.0))

        # An operator forces the circuit open while the probe is still in flight.
        cb.force_open()
        release_probe.set()
        thread.join(timeout=5.0)

        self.assertEqual(cb.state, CircuitState.OPEN)
        self.assertEqual(cb.snapshot().half_open_in_flight, 0)


class TestConfigurationValidation(unittest.TestCase):
    def test_default_clock_is_monotonic(self):
        """
        A wall clock lets an NTP step make the recovery timeout elapse instantly
        or never; time.monotonic() cannot go backwards.
        """
        cb = CircuitBreaker(expected_exceptions=(ConnectionError,))
        self.assertIs(cb._clock, time.monotonic)

    def test_invalid_configuration_is_rejected_at_construction(self):
        bad_kwargs = [
            {"failure_threshold": 0},
            {"failure_threshold": 1.5},
            {"failure_threshold": True},
            {"recovery_timeout_sec": 0},
            {"recovery_timeout_sec": -1.0},
            {"recovery_timeout_sec": float("nan")},
            {"recovery_timeout_sec": float("inf")},
            {"max_recovery_timeout_sec": 0.5},
            {"expected_exceptions": ()},
            {"expected_exceptions": (ValueError,) + (1,)},
            {"expected_exceptions": ConnectionError},
            {"half_open_max_calls": 0},
            {"half_open_success_threshold": 0},
            {"backoff_multiplier": 0.5},
            {"jitter_ratio": 1.0},
            {"jitter_ratio": -0.1},
            {"failure_window_sec": 0.0},
            {"slow_call_duration_sec": -1.0},
            {"name": "  "},
            {"clock": "not-callable"},
            {"on_state_change": "not-callable"},
        ]
        for kwargs in bad_kwargs:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    CircuitBreaker(**kwargs)

    def test_overbroad_expected_exceptions_is_warned_about(self):
        with self.assertLogs("circuit_breaker", level="WARNING") as logs:
            CircuitBreaker(expected_exceptions=(Exception,))
        self.assertTrue(any("business errors" in line for line in logs.output))

    def test_jitter_stays_within_the_configured_ratio(self):
        clock = FakeClock()
        cb = CircuitBreaker(
            failure_threshold=1,
            recovery_timeout_sec=10.0,
            expected_exceptions=(ConnectionError,),
            jitter_ratio=0.25,
            clock=clock,
            rng=random.Random(7),
        )
        for _ in range(20):
            cb.reset()
            with self.assertRaises(ConnectionError):
                cb.call(self._raise)
            timeout = cb.snapshot().current_recovery_timeout_sec
            self.assertGreaterEqual(timeout, 7.5)
            self.assertLessEqual(timeout, 12.5)

    @staticmethod
    def _raise():
        raise ConnectionError("Service Down")


if __name__ == "__main__":
    unittest.main()

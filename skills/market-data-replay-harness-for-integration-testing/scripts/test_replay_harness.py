"""
Unit tests for market-data-replay-harness-for-integration-testing.

Scheduling behaviour is tested against an injected fake clock rather than the wall
clock: the properties that matter (no cumulative drift, correct speed scaling, honest
lag accounting) are exact, and asserting them against `time.perf_counter` would only
produce a flaky test of the host's scheduler. One loose real-clock smoke test at the
end confirms the default `time.perf_counter`/`time.sleep` wiring actually runs.
"""
import logging
import math
import time
import unittest

from replay_harness import (
    DEFAULT_MIN_SLEEP_SEC,
    MarketDataReplayHarness,
    ReplayCallbackError,
    ReplayOrderingError,
    ReplaySessionSummary,
    ReplayTick,
)


class FakeClock:
    """Deterministic stand-in for perf_counter/sleep. Records every sleep requested."""

    def __init__(self, start: float = 1_000.0):
        self.t = start
        self.sleeps = []

    def now(self) -> float:
        return self.t

    def sleep(self, duration: float) -> None:
        if duration < 0:
            raise ValueError("sleep length must be non-negative")
        self.sleeps.append(duration)
        self.t += duration

    def advance(self, duration: float) -> None:
        """Simulate work (e.g. a slow strategy callback) consuming wall time."""
        self.t += duration


def ticks_at(spacing: float, count: int, start: float = 1_000.0):
    """count ticks evenly spaced by `spacing` seconds, sequence ids 1..count."""
    return [
        ReplayTick("AAPL", start + i * spacing, i + 1, 150.00 + i, 150.05 + i, 100)
        for i in range(count)
    ]


class TestMarketDataReplayHarness(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Keep the harness's own warnings off the test runner's stderr without
        # disabling logging globally, which would break assertLogs below.
        logging.getLogger("replay_harness").addHandler(logging.NullHandler())

    def setUp(self):
        self.harness = MarketDataReplayHarness(speed_multiplier=100.0)  # 100x fast forward
        self.ticks = [
            ReplayTick("AAPL", 1000.0, 1, 150.00, 150.05, 100),
            ReplayTick("AAPL", 1001.0, 2, 150.10, 150.15, 200),
            ReplayTick("AAPL", 1002.0, 3, 150.20, 150.25, 300),
        ]

    # ------------------------------------------------------------------ dispatch

    def test_asap_replay_execution(self):
        def dummy_strategy(tick):
            if tick.price >= 150.20:
                return {"action": "SELL", "price": tick.price}
            return None

        summary = self.harness.replay_session(self.ticks, dummy_strategy, asap_mode=True)

        self.assertEqual(summary.total_ticks_replayed, 3)
        self.assertEqual(summary.emitted_orders_count, 1)
        self.assertEqual(summary.simulated_duration_sec, 2.0)
        self.assertEqual(self.harness.generated_orders[0]["action"], "SELL")
        # ASAP has no deadlines, so lag accounting must not claim any.
        self.assertFalse(summary.wall_clock_replay)
        self.assertEqual(summary.ticks_dispatched_late, 0)
        self.assertEqual(summary.max_scheduling_lag_sec, 0.0)

    def test_sorted_tick_replay(self):
        # Unsorted ticks input
        unsorted = [self.ticks[2], self.ticks[0], self.ticks[1]]
        received_seqs = []

        def callback(tick):
            received_seqs.append(tick.sequence_id)
            return None

        with self.assertLogs("replay_harness", level="WARNING") as captured:
            summary = self.harness.replay_session(unsorted, callback, asap_mode=True)

        # Should replay strictly in timestamp sorted order (seq 1, 2, 3)
        self.assertEqual(received_seqs, [1, 2, 3])
        # Reordering is reported, not silently swallowed: an out-of-order capture is a
        # finding about the recorder, not a detail for the harness to hide.
        # One backward adjacent pair in [t3, t1, t2]: the metric counts backward
        # transitions in the input, which is what a recorder fault shows up as.
        self.assertEqual(summary.out_of_order_input_pairs, 1)
        self.assertTrue(any("not in replay order" in line for line in captured.output))

    def test_equal_timestamps_break_ties_on_sequence_id(self):
        # Regression: ties used to preserve INPUT order, so the same capture read in a
        # different order replayed in a different order. Determinism is the whole claim.
        tied = [
            ReplayTick("AAPL", 1000.0, 9, 1.0, 1.02, 1),
            ReplayTick("AAPL", 1000.0, 2, 1.0, 1.02, 1),
            ReplayTick("AAPL", 999.5, 5, 1.0, 1.02, 1),
        ]
        seen_a, seen_b = [], []
        MarketDataReplayHarness().replay_session(
            tied, lambda t: seen_a.append(t.sequence_id), asap_mode=True)
        MarketDataReplayHarness().replay_session(
            list(reversed(tied)), lambda t: seen_b.append(t.sequence_id), asap_mode=True)

        self.assertEqual(seen_a, [5, 2, 9])
        self.assertEqual(seen_a, seen_b)

    def test_session_state_is_reset_between_sessions(self):
        # Regression: order and tick accumulators used to persist across sessions, so a
        # suite reusing one harness read the SECOND session's count as 6 instead of 3.
        harness = MarketDataReplayHarness()
        first = harness.replay_session(self.ticks, lambda t: {"o": 1}, asap_mode=True)
        second = harness.replay_session(self.ticks, lambda t: {"o": 1}, asap_mode=True)

        self.assertEqual(first.emitted_orders_count, 3)
        self.assertEqual(second.emitted_orders_count, 3)
        self.assertEqual(len(harness.generated_orders), 3)
        self.assertEqual(len(harness.replayed_ticks), 3)

    def test_empty_tick_log_returns_zero_summary(self):
        summary = MarketDataReplayHarness(2.0).replay_session([], lambda t: None)
        self.assertIsInstance(summary, ReplaySessionSummary)
        self.assertEqual(summary.total_ticks_replayed, 0)
        self.assertEqual(summary.emitted_orders_count, 0)
        self.assertEqual(summary.simulated_duration_sec, 0.0)
        self.assertEqual(summary.speed_multiplier, 2.0)

    def test_retain_replayed_ticks_false_still_counts(self):
        # Millions of ticks must not be pinned in memory just to count them.
        harness = MarketDataReplayHarness(retain_replayed_ticks=False)
        summary = harness.replay_session(self.ticks, lambda t: None, asap_mode=True)
        self.assertEqual(summary.total_ticks_replayed, 3)
        self.assertEqual(harness.replayed_ticks, [])

    def test_simulated_now_tracks_the_tick_being_dispatched(self):
        harness = MarketDataReplayHarness()
        observed = []

        def callback(tick):
            observed.append(harness.simulated_now())
            return None

        self.assertIsNone(harness.simulated_now())
        harness.replay_session(self.ticks, callback, asap_mode=True)
        self.assertEqual(observed, [1000.0, 1001.0, 1002.0])
        self.assertIsNone(harness.simulated_now())

    # ------------------------------------------------------------------ scheduling

    def test_callback_cost_does_not_accumulate_into_drift(self):
        # 10 ticks spaced 10 ms, callback burns 5 ms each, replayed at 1x.
        # Correct (absolute deadlines): each sleep is 5 ms and the session spans the
        # recorded 90 ms plus the final callback = 95 ms.
        # The pre-2.0 per-tick `sleep(delta / S)` scheme slept a full 10 ms after every
        # 5 ms callback and took 150 ms - a 58% drift on a harness sold as "1x".
        clock = FakeClock()
        harness = MarketDataReplayHarness(
            1.0, clock=clock.now, sleeper=clock.sleep, min_sleep_sec=0.0)
        start = clock.now()

        summary = harness.replay_session(
            ticks_at(0.010, 10), lambda t: clock.advance(0.005) or None)

        self.assertEqual(len(clock.sleeps), 9)
        for slept in clock.sleeps:
            self.assertAlmostEqual(slept, 0.005, places=9)
        self.assertAlmostEqual(clock.now() - start, 0.095, places=9)
        self.assertAlmostEqual(summary.actual_wall_time_sec, 0.095, places=9)
        self.assertEqual(summary.ticks_dispatched_late, 0)
        self.assertAlmostEqual(summary.max_scheduling_lag_sec, 0.0, places=9)
        self.assertTrue(summary.wall_clock_replay)

    def test_speed_multiplier_scales_the_gaps(self):
        clock = FakeClock()
        harness = MarketDataReplayHarness(
            10.0, clock=clock.now, sleeper=clock.sleep, min_sleep_sec=0.0)

        summary = harness.replay_session(ticks_at(0.100, 5), lambda t: None)

        # 100 ms recorded gaps at 10x => 10 ms each; 4 gaps => 40 ms of wall time.
        self.assertEqual(len(clock.sleeps), 4)
        for slept in clock.sleeps:
            self.assertAlmostEqual(slept, 0.010, places=9)
        self.assertAlmostEqual(summary.actual_wall_time_sec, 0.040, places=9)
        self.assertAlmostEqual(summary.simulated_duration_sec, 0.400, places=9)
        self.assertAlmostEqual(summary.achieved_speed_multiplier, 10.0, places=6)

    def test_slow_consumer_is_reported_late_not_silently_absorbed(self):
        # Callback burns 20 ms per tick on 10 ms recorded spacing: the consumer cannot
        # keep up, so the strategy never sees the recorded arrival spacing. That has to
        # surface in the report, otherwise a latency assertion passes on a lie.
        clock = FakeClock()
        harness = MarketDataReplayHarness(
            1.0, clock=clock.now, sleeper=clock.sleep, late_tolerance_sec=0.001)

        with self.assertLogs("replay_harness", level="WARNING") as captured:
            summary = harness.replay_session(
                ticks_at(0.010, 5), lambda t: clock.advance(0.020) or None)

        self.assertEqual(clock.sleeps, [])           # never early, so never sleeps
        self.assertEqual(summary.ticks_dispatched_late, 4)
        # Tick i is dispatched at 20i ms against a deadline of 10i ms => lag 10i ms.
        self.assertAlmostEqual(summary.max_scheduling_lag_sec, 0.040, places=9)
        self.assertAlmostEqual(
            summary.mean_scheduling_lag_sec, (0.0 + 0.01 + 0.02 + 0.03 + 0.04) / 5, places=9)
        self.assertLess(summary.achieved_speed_multiplier, 1.0)
        self.assertTrue(any("could not keep up" in line for line in captured.output))

    def test_asap_mode_never_sleeps(self):
        def exploding_sleeper(_):
            raise AssertionError("ASAP mode must not sleep")

        clock = FakeClock()
        harness = MarketDataReplayHarness(1.0, clock=clock.now, sleeper=exploding_sleeper)
        summary = harness.replay_session(ticks_at(1.0, 4), lambda t: None, asap_mode=True)
        self.assertEqual(summary.total_ticks_replayed, 4)
        self.assertFalse(summary.wall_clock_replay)

    def test_infinite_speed_multiplier_is_equivalent_to_asap(self):
        def exploding_sleeper(_):
            raise AssertionError("an infinite multiplier must not sleep")

        clock = FakeClock()
        harness = MarketDataReplayHarness(
            float("inf"), clock=clock.now, sleeper=exploding_sleeper)
        summary = harness.replay_session(ticks_at(1.0, 4), lambda t: None)
        self.assertFalse(summary.wall_clock_replay)
        self.assertEqual(summary.total_ticks_replayed, 4)

    def test_gaps_below_min_sleep_are_dispatched_immediately(self):
        # 100 us spacing is below what time.sleep can deliver; the harness dispatches
        # immediately and books the shortfall as lag instead of pretending to sleep.
        clock = FakeClock()
        harness = MarketDataReplayHarness(
            1.0, clock=clock.now, sleeper=clock.sleep,
            min_sleep_sec=DEFAULT_MIN_SLEEP_SEC, late_tolerance_sec=0.0)

        summary = harness.replay_session(ticks_at(0.0001, 5), lambda t: None)

        self.assertEqual(clock.sleeps, [])
        self.assertAlmostEqual(summary.actual_wall_time_sec, 0.0, places=9)
        # Dispatched early, never late: negative lag is not counted as lateness.
        self.assertEqual(summary.ticks_dispatched_late, 0)

    def test_actual_wall_time_is_not_clamped_to_a_fabricated_floor(self):
        # Regression: the summary used to report max(0.0001, elapsed), inventing a
        # 100 us measurement for a session that took no measurable time at all.
        clock = FakeClock()
        harness = MarketDataReplayHarness(1.0, clock=clock.now, sleeper=clock.sleep)
        summary = harness.replay_session(self.ticks, lambda t: None, asap_mode=True)
        self.assertEqual(summary.actual_wall_time_sec, 0.0)
        self.assertEqual(summary.achieved_speed_multiplier, math.inf)

    # ------------------------------------------------------------------ validation

    def test_invalid_speed_multipliers_are_rejected(self):
        for bad in (0.0, -1.0, float("nan"), "10", True, None):
            with self.subTest(speed=bad):
                with self.assertRaises(ValueError):
                    MarketDataReplayHarness(bad)

    def test_invalid_arguments_are_rejected(self):
        harness = MarketDataReplayHarness()
        with self.assertRaises(ValueError):
            harness.replay_session(self.ticks, "not-callable")
        with self.assertRaises(ValueError):
            harness.replay_session("AAPL", lambda t: None)
        with self.assertRaises(ValueError):
            MarketDataReplayHarness(min_sleep_sec=-0.1)
        with self.assertRaises(ValueError):
            MarketDataReplayHarness(max_projected_wall_time_sec=float("inf"))
        with self.assertRaises(ValueError):
            MarketDataReplayHarness(clock=None)

    def test_non_finite_timestamps_are_rejected(self):
        # NaN sorts arbitrarily and would randomise replay order in silence.
        for bad_ts in (float("nan"), float("inf")):
            with self.subTest(ts=bad_ts):
                bad = [ReplayTick("AAPL", bad_ts, 1, 1.0, 1.02, 1)] + self.ticks
                with self.assertRaises(ValueError):
                    MarketDataReplayHarness().replay_session(
                        bad, lambda t: None, asap_mode=True)

    def test_non_integer_sequence_id_is_rejected(self):
        bad = [ReplayTick("AAPL", 1000.0, "1", 1.0, 1.02, 1)]
        with self.assertRaises(ValueError):
            MarketDataReplayHarness().replay_session(bad, lambda t: None, asap_mode=True)

    def test_strict_ordering_refuses_an_out_of_order_capture(self):
        harness = MarketDataReplayHarness(strict_ordering=True)
        unsorted = [self.ticks[2], self.ticks[0], self.ticks[1]]
        with self.assertRaises(ReplayOrderingError):
            harness.replay_session(unsorted, lambda t: None, asap_mode=True)

    def test_millisecond_timestamps_are_caught_by_the_wall_time_guard(self):
        # A 3-second capture recorded in milliseconds looks like a 50-minute session.
        ms_ticks = [ReplayTick("AAPL", 1_000_000.0 + i * 1000.0, i + 1, 1.0, 1.02, 1)
                    for i in range(4)]
        harness = MarketDataReplayHarness(1.0, max_projected_wall_time_sec=60.0)
        with self.assertRaises(ValueError) as ctx:
            harness.replay_session(ms_ticks, lambda t: None)
        self.assertIn("SECONDS", str(ctx.exception))

    def test_implausible_wall_time_warns_when_no_guard_is_set(self):
        clock = FakeClock()
        long_ticks = [ReplayTick("AAPL", i * 5_000.0, i + 1, 1.0, 1.02, 1) for i in range(3)]
        harness = MarketDataReplayHarness(1.0, clock=clock.now, sleeper=clock.sleep)
        with self.assertLogs("replay_harness", level="WARNING") as captured:
            harness.replay_session(long_ticks, lambda t: None)
        self.assertTrue(any("milliseconds or nanoseconds" in line for line in captured.output))

    # ------------------------------------------------------------------ callbacks

    def test_callback_exception_names_the_offending_tick(self):
        def exploding(tick):
            if tick.sequence_id == 2:
                raise ZeroDivisionError("bad spread math")
            return None

        harness = MarketDataReplayHarness()
        with self.assertRaises(ReplayCallbackError) as ctx:
            harness.replay_session(self.ticks, exploding, asap_mode=True)

        self.assertEqual(ctx.exception.tick_index, 1)
        self.assertEqual(ctx.exception.tick.sequence_id, 2)
        self.assertIsInstance(ctx.exception.__cause__, ZeroDivisionError)
        self.assertIsNone(harness.simulated_now())

    def test_empty_dict_order_is_counted_not_dropped(self):
        # Regression: `if order:` treated an empty dict as "no order", silently losing
        # an emission the callback explicitly made.
        summary = MarketDataReplayHarness().replay_session(
            self.ticks, lambda t: {}, asap_mode=True)
        self.assertEqual(summary.emitted_orders_count, 3)

    def test_non_dict_callback_return_is_rejected(self):
        # A callback returning a list of orders would otherwise be counted as one.
        with self.assertRaises(TypeError):
            MarketDataReplayHarness().replay_session(
                self.ticks, lambda t: [{"action": "BUY"}], asap_mode=True)

    def test_price_is_the_arithmetic_mid(self):
        self.assertAlmostEqual(self.ticks[0].price, 150.025, places=9)
        one_sided = ReplayTick("AAPL", 1.0, 1, float("nan"), 150.05, 1)
        self.assertTrue(math.isnan(one_sided.price))

    # ------------------------------------------------------------------ smoke

    def test_real_clock_smoke(self):
        # Confirms the default perf_counter/sleep wiring runs end to end. Bounds are
        # deliberately loose: OS sleep overshoot is not this test's business.
        harness = MarketDataReplayHarness(10.0)
        received = []
        started = time.perf_counter()
        summary = harness.replay_session(
            ticks_at(0.020, 5), lambda t: received.append(t.sequence_id) or None)
        elapsed = time.perf_counter() - started

        self.assertEqual(received, [1, 2, 3, 4, 5])
        self.assertTrue(summary.wall_clock_replay)
        self.assertGreater(summary.actual_wall_time_sec, 0.0)
        self.assertLess(elapsed, 5.0)


if __name__ == "__main__":
    unittest.main()

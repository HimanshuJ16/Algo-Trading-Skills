"""
Tests for historical-data-backfill-rate-limit-management.

Pacing and backoff are verified against an injected clock and sleep recorder rather
than by sleeping: the point under test is *how long the engine decided to wait*, and
asserting that against a recorder is both exact and instant. Production defaults
remain ``time.monotonic`` / ``time.sleep``.
"""
import json
import os
import random
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from historical_data_backfill_rate_limit_management import (
    BackfillChunk,
    BackfillConfigurationError,
    BackfillExecutionReport,
    CheckpointStore,
    FetchOutcome,
    HistoricalBackfillManagerEngine,
    JsonFileCheckpointStore,
    TokenBucketLimiter,
    generate_date_chunks,
    parse_retry_after,
)


class FakeClock:
    """Monotonic clock whose only movement is the sleeps the engine asks for."""

    def __init__(self, start: float = 1000.0):
        self.now = start
        self.sleeps = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds

    @property
    def total_slept(self) -> float:
        return sum(self.sleeps)


def make_engine(clock: FakeClock, **kwargs) -> HistoricalBackfillManagerEngine:
    params = dict(
        requests_per_minute=60,
        max_burst_capacity=5,
        max_retries=3,
        clock=clock,
        sleep_func=clock.sleep,
    )
    params.update(kwargs)
    return HistoricalBackfillManagerEngine(**params)


def sample_chunks(count: int = 5):
    return [
        BackfillChunk(f"CHK_{i:02d}", f"2026-{i:02d}-01", f"2026-{i:02d}-28")
        for i in range(1, count + 1)
    ]


class TestDateChunking(unittest.TestCase):

    def test_chunks_are_contiguous_inclusive_and_non_overlapping(self):
        # 2026-01-01..2026-01-10 inclusive is 10 days; at 3 days per chunk that is
        # 4 chunks (3+3+3+1), derived by hand, not from the implementation.
        chunks = generate_date_chunks("2026-01-01", "2026-01-10", chunk_days=3)
        self.assertEqual(
            [(c.start_date_iso, c.end_date_iso) for c in chunks],
            [
                ("2026-01-01", "2026-01-03"),
                ("2026-01-04", "2026-01-06"),
                ("2026-01-07", "2026-01-09"),
                ("2026-01-10", "2026-01-10"),
            ],
        )
        self.assertEqual(len(set(c.chunk_id for c in chunks)), 4)

    def test_single_day_range_yields_one_chunk(self):
        chunks = generate_date_chunks("2026-03-05", "2026-03-05", chunk_days=30)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0].start_date_iso, "2026-03-05")
        self.assertEqual(chunks[0].end_date_iso, "2026-03-05")

    def test_leap_day_range_is_covered_exactly_once(self):
        chunks = generate_date_chunks("2028-02-27", "2028-03-01", chunk_days=1)
        self.assertEqual(
            [c.start_date_iso for c in chunks],
            ["2028-02-27", "2028-02-28", "2028-02-29", "2028-03-01"],
        )

    def test_invalid_ranges_and_chunk_sizes_raise(self):
        with self.assertRaises(BackfillConfigurationError):
            generate_date_chunks("2026-01-10", "2026-01-01", chunk_days=5)
        with self.assertRaises(BackfillConfigurationError):
            generate_date_chunks("2026-01-01", "2026-01-10", chunk_days=0)
        with self.assertRaises(BackfillConfigurationError):
            generate_date_chunks("01/01/2026", "2026-01-10", chunk_days=5)


class TestTokenBucketLimiter(unittest.TestCase):

    def test_burst_is_capped_and_refill_wait_is_exact(self):
        clock = FakeClock()
        # 1 token/sec, capacity 2: two immediate requests, then a full second's wait.
        limiter = TokenBucketLimiter(1.0, 2.0, clock=clock, sleep_func=clock.sleep)
        self.assertEqual(limiter.acquire(), 0.0)
        self.assertEqual(limiter.acquire(), 0.0)
        self.assertAlmostEqual(limiter.acquire(), 1.0, places=6)
        self.assertAlmostEqual(clock.total_slept, 1.0, places=6)

    def test_pacing_actually_blocks_rather_than_truncating_the_sleep(self):
        # Regression: the limiter previously computed a wait and then slept a fixed
        # 10 ms, dispatching anyway. 10 requests at 1/sec with a burst of 2 must cost
        # 8 seconds of real waiting, not 0.08.
        clock = FakeClock()
        limiter = TokenBucketLimiter(1.0, 2.0, clock=clock, sleep_func=clock.sleep)
        for _ in range(10):
            limiter.acquire()
        self.assertAlmostEqual(clock.total_slept, 8.0, places=6)

    def test_try_acquire_does_not_consume_when_bucket_is_empty(self):
        clock = FakeClock()
        limiter = TokenBucketLimiter(1.0, 1.0, clock=clock, sleep_func=clock.sleep)
        self.assertEqual(limiter.try_acquire(), 0.0)
        before = limiter.available_tokens
        self.assertGreater(limiter.try_acquire(), 0.0)
        self.assertAlmostEqual(limiter.available_tokens, before, places=9)

    def test_backward_clock_step_does_not_drain_the_bucket(self):
        # Regression for wall-clock use: an NTP step backwards produced a negative
        # elapsed interval, which subtracted tokens instead of adding them.
        clock = FakeClock()
        limiter = TokenBucketLimiter(1.0, 5.0, clock=clock, sleep_func=clock.sleep)
        limiter.acquire()                       # 4 tokens left
        clock.now -= 60.0                       # clock steps backwards one minute
        self.assertAlmostEqual(limiter.available_tokens, 4.0, places=6)

    def test_shared_limiter_never_over_issues_under_concurrency(self):
        clock = FakeClock()
        limiter = TokenBucketLimiter(1.0, 4.0, clock=clock, sleep_func=clock.sleep)
        granted = []
        lock = threading.Lock()

        def worker():
            if limiter.try_acquire() == 0.0:
                with lock:
                    granted.append(1)

        threads = [threading.Thread(target=worker) for _ in range(16)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # The clock never advances, so exactly the 4 tokens in the bucket may be issued.
        self.assertEqual(len(granted), 4)

    def test_invalid_limiter_configuration_raises(self):
        with self.assertRaises(BackfillConfigurationError):
            TokenBucketLimiter(0.0, 5.0)
        with self.assertRaises(BackfillConfigurationError):
            TokenBucketLimiter(1.0, 0.0)
        limiter = TokenBucketLimiter(1.0, 2.0)
        with self.assertRaises(BackfillConfigurationError):
            limiter.try_acquire(5.0)            # unsatisfiable: exceeds capacity
        with self.assertRaises(BackfillConfigurationError):
            limiter.try_acquire(0.0)


class TestRetryAfterParsing(unittest.TestCase):

    def test_delay_seconds_forms(self):
        self.assertEqual(parse_retry_after(30), 30.0)
        self.assertEqual(parse_retry_after("45"), 45.0)
        self.assertEqual(parse_retry_after(2.5), 2.5)
        self.assertEqual(parse_retry_after(0), 0.0)

    def test_http_date_form_is_resolved_to_seconds(self):
        # RFC 9110 Sec. 10.2.3 permits an HTTP-date; a float-only parser silently
        # discarded it and retried on the short computed backoff instead.
        target = datetime.now(timezone.utc) + timedelta(seconds=120)
        parsed = parse_retry_after(format_datetime(target, usegmt=True))
        self.assertIsNotNone(parsed)
        self.assertAlmostEqual(parsed, 120.0, delta=5.0)

    def test_past_http_date_clamps_to_zero(self):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        self.assertEqual(parse_retry_after(format_datetime(past, usegmt=True)), 0.0)

    def test_unparseable_or_negative_values_yield_none(self):
        for value in (None, "", "   ", "soon", -5, True, False, "-10", [], {}):
            self.assertIsNone(parse_retry_after(value), msg=f"value={value!r}")

    def test_non_finite_values_are_rejected_rather_than_slept_on(self):
        # inf passes a naive `>= 0` test; nan fails every comparison. Neither is a delay.
        for value in (float("inf"), float("-inf"), float("nan"), "inf", "nan"):
            self.assertIsNone(parse_retry_after(value), msg=f"value={value!r}")

    def test_bytes_header_is_decoded(self):
        # Some HTTP clients surface raw header bytes; str(b"30") would be "b'30'".
        self.assertEqual(parse_retry_after(b"30"), 30.0)


class TestBackoffCalculation(unittest.TestCase):

    def setUp(self):
        self.engine = make_engine(FakeClock(), base_retry_delay_sec=1.0, max_retry_delay_sec=16.0)

    def test_full_jitter_spans_the_whole_interval_and_respects_the_cap(self):
        # Regression: additive jitter under a cap produced *exactly* the cap on every
        # attempt past the knee, i.e. zero decorrelation when the herd is largest.
        # Seeded so the assertion is deterministic: `random.uniform(0, 16)` rounded to
        # 3dp does land on exactly 16.0 about once per 32k draws, so a strict
        # `max(samples) < cap` assertion is flaky by construction. What distinguishes
        # full jitter from the old formula is the *distribution*, not one extreme.
        state = random.getstate()
        random.seed(20260825)
        try:
            samples = [self.engine.calculate_backoff_delay(8) for _ in range(400)]
        finally:
            random.setstate(state)

        self.assertTrue(all(0.0 <= s <= 16.0 for s in samples))
        self.assertLess(sum(1 for s in samples if s >= 15.999), 5)   # old code: 400/400
        self.assertGreater(len(set(samples)), 300)                   # old code: 1 value
        self.assertLess(min(samples), 4.0)           # genuinely spans the low end
        self.assertLess(abs(sum(samples) / len(samples) - 8.0), 1.5)  # mean ~ cap/2

    def test_early_attempt_is_bounded_by_the_exponential_not_the_cap(self):
        # attempt 2 -> ceiling = 1.0 * 2**2 = 4.0, well under the 16 s cap.
        samples = [self.engine.calculate_backoff_delay(2) for _ in range(200)]
        self.assertTrue(all(0.0 <= s <= 4.0 for s in samples))

    def test_server_retry_after_overrides_computed_backoff(self):
        for _ in range(20):
            self.assertEqual(self.engine.calculate_backoff_delay(1, retry_after_header=37), 37.0)

    def test_malformed_retry_after_falls_back_to_jitter(self):
        delay = self.engine.calculate_backoff_delay(1, retry_after_header="not-a-date")
        self.assertGreaterEqual(delay, 0.0)
        self.assertLessEqual(delay, 2.0)

    def test_negative_attempt_raises(self):
        with self.assertRaises(BackfillConfigurationError):
            self.engine.calculate_backoff_delay(-1)


class TestEngineConfigurationValidation(unittest.TestCase):

    def test_rejects_configurations_that_can_never_dispatch(self):
        # requests_per_minute=0 previously divided by zero inside the limiter.
        for kwargs in (
            {"requests_per_minute": 0},
            {"requests_per_minute": -30},
            {"max_burst_capacity": 0},
            {"max_retries": -1},
            {"base_retry_delay_sec": 0},
            {"base_retry_delay_sec": 10.0, "max_retry_delay_sec": 2.0},
            {"max_retry_after_sec": -1.0},
        ):
            with self.subTest(**kwargs):
                with self.assertRaises(BackfillConfigurationError):
                    make_engine(FakeClock(), **kwargs)

    def test_rejects_empty_duplicate_and_non_callable_inputs(self):
        engine = make_engine(FakeClock())
        with self.assertRaises(BackfillConfigurationError):
            engine.execute_backfill("AAPL", [], lambda c: FetchOutcome(True, 1))
        with self.assertRaises(BackfillConfigurationError):
            engine.execute_backfill("AAPL", sample_chunks(2), "not-callable")
        dupes = [BackfillChunk("SAME", "2026-01-01", "2026-01-31")] * 2
        with self.assertRaises(BackfillConfigurationError):
            engine.execute_backfill("AAPL", dupes, lambda c: FetchOutcome(True, 1))


class TestBackfillExecution(unittest.TestCase):

    def test_backfill_success_without_throttling(self):
        clock = FakeClock()
        engine = make_engine(clock)
        report = engine.execute_backfill("AAPL", sample_chunks(5), lambda c: (True, False, 1000))

        self.assertIsInstance(report, BackfillExecutionReport)
        self.assertEqual(report.status, "BACKFILL_SUCCESS")
        self.assertEqual(report.completed_chunks_count, 5)
        self.assertEqual(report.failed_chunks_count, 0)
        self.assertEqual(report.total_records_ingested, 5000)
        self.assertEqual(report.total_rate_limit_throttles, 0)

    def test_backfill_handles_http_429_with_backoff(self):
        clock = FakeClock()
        engine = make_engine(clock)
        attempts = {}

        def fetch_func(chunk):
            attempts[chunk.chunk_id] = attempts.get(chunk.chunk_id, 0) + 1
            if attempts[chunk.chunk_id] == 1:
                return False, True, 0
            return True, False, 1000

        report = engine.execute_backfill("AAPL", sample_chunks(5), fetch_func)

        self.assertEqual(report.status, "BACKFILL_SUCCESS")
        self.assertEqual(report.completed_chunks_count, 5)
        self.assertEqual(report.total_rate_limit_throttles, 5)
        # Regression: backoff sleeps were clamped to 10 ms, so a 429 was effectively
        # retried immediately. Five throttles must produce five real waits.
        self.assertGreater(report.total_backoff_wait_sec, 0.0)

    def test_pacing_delays_are_applied_across_chunks(self):
        # 6 requests at 60 req/min with a burst of 2: 4 requests must wait ~1 s each.
        clock = FakeClock()
        engine = make_engine(clock, max_burst_capacity=2)
        report = engine.execute_backfill("AAPL", sample_chunks(6), lambda c: (True, False, 10))
        self.assertAlmostEqual(report.total_pacing_wait_sec, 4.0, places=3)

    def test_server_retry_after_is_honoured_during_execution(self):
        # Regression: execute_backfill never passed Retry-After to the backoff
        # calculation, so a documented behaviour did not exist in the code path.
        clock = FakeClock()
        engine = make_engine(clock, max_burst_capacity=10)
        state = {"n": 0}

        def fetch_func(chunk):
            state["n"] += 1
            if state["n"] == 1:
                return FetchOutcome(success=False, status_code=429, retry_after=42)
            return FetchOutcome(success=True, records=500)

        report = engine.execute_backfill("BTCUSDT", [sample_chunks(1)[0]], fetch_func)

        self.assertEqual(report.status, "BACKFILL_SUCCESS")
        self.assertAlmostEqual(report.total_backoff_wait_sec, 42.0, places=3)
        self.assertIn(42.0, clock.sleeps)

    def test_non_retryable_error_fails_fast_without_burning_quota(self):
        clock = FakeClock()
        engine = make_engine(clock, max_burst_capacity=10)
        calls = []

        def fetch_func(chunk):
            calls.append(chunk.chunk_id)
            return FetchOutcome(success=False, status_code=404, error_message="unknown symbol")

        chunks = sample_chunks(1)
        report = engine.execute_backfill("NOSUCH", chunks, fetch_func)

        self.assertEqual(len(calls), 1)                      # no retries at all
        self.assertEqual(report.status, "BACKFILL_FAILED")
        self.assertEqual(chunks[0].status, "FAILED")
        self.assertEqual(report.total_backoff_wait_sec, 0.0)
        self.assertEqual(report.total_rate_limit_throttles, 0)

    def test_retry_budget_is_bounded_for_retryable_errors(self):
        clock = FakeClock()
        engine = make_engine(clock, max_burst_capacity=10, max_retries=2)
        calls = []

        def fetch_func(chunk):
            calls.append(1)
            return FetchOutcome(success=False, status_code=503)

        report = engine.execute_backfill("AAPL", sample_chunks(1), fetch_func)

        self.assertEqual(len(calls), 3)                      # initial attempt + 2 retries
        self.assertEqual(report.status, "BACKFILL_FAILED")
        self.assertEqual(report.failed_chunks_count, 1)

    def test_ip_ban_is_deferred_not_retried(self):
        # Binance returns 418 once an IP is auto-banned; bans scale to days, so an
        # in-process retry loop is never the right response.
        clock = FakeClock()
        engine = make_engine(clock, max_burst_capacity=10)
        calls = []

        def fetch_func(chunk):
            calls.append(1)
            return FetchOutcome(success=False, status_code=418, retry_after=172800)

        chunks = sample_chunks(1)
        report = engine.execute_backfill("BTCUSDT", chunks, fetch_func)

        self.assertEqual(len(calls), 1)
        self.assertEqual(chunks[0].status, "DEFERRED_RETRY_AFTER")
        self.assertEqual(report.deferred_chunks_count, 1)
        self.assertEqual(report.status, "BACKFILL_FAILED")
        self.assertEqual(report.total_backoff_wait_sec, 0.0)

    def test_excessive_retry_after_defers_instead_of_sleeping(self):
        clock = FakeClock()
        engine = make_engine(clock, max_burst_capacity=10, max_retry_after_sec=60.0)

        def fetch_func(chunk):
            return FetchOutcome(success=False, status_code=429, retry_after=3600)

        chunks = sample_chunks(1)
        report = engine.execute_backfill("AAPL", chunks, fetch_func)

        self.assertEqual(chunks[0].status, "DEFERRED_RETRY_AFTER")
        self.assertEqual(report.deferred_chunks_count, 1)
        self.assertEqual(report.total_backoff_wait_sec, 0.0)
        self.assertLess(clock.total_slept, 60.0)             # the hour was never slept

    def test_callback_exception_is_treated_as_retryable_transport_failure(self):
        clock = FakeClock()
        engine = make_engine(clock, max_burst_capacity=10, max_retries=1)
        calls = []

        def fetch_func(chunk):
            calls.append(1)
            raise TimeoutError("read timed out")

        report = engine.execute_backfill("AAPL", sample_chunks(1), fetch_func)

        self.assertEqual(len(calls), 2)                      # retried once, then failed
        self.assertEqual(report.status, "BACKFILL_FAILED")

    def test_partial_status_when_some_chunks_fail(self):
        clock = FakeClock()
        engine = make_engine(clock, max_burst_capacity=10)

        def fetch_func(chunk):
            if chunk.chunk_id == "CHK_03":
                return FetchOutcome(success=False, status_code=403)
            return FetchOutcome(success=True, records=100)

        report = engine.execute_backfill("AAPL", sample_chunks(5), fetch_func)
        self.assertEqual(report.status, "BACKFILL_PARTIAL")
        self.assertEqual(report.completed_chunks_count, 4)
        self.assertEqual(report.failed_chunks_count, 1)
        self.assertEqual(report.total_records_ingested, 400)

    def test_malformed_callback_return_raises(self):
        engine = make_engine(FakeClock())
        with self.assertRaises(BackfillConfigurationError):
            engine.execute_backfill("AAPL", sample_chunks(1), lambda c: "done")

    def test_impossible_record_counts_are_rejected(self):
        # A buggy callback previously drove total_records_ingested negative under a
        # BACKFILL_SUCCESS status; the audit report is the only record of what landed.
        engine = make_engine(FakeClock(), max_burst_capacity=10)
        for bad in (-500, float("nan"), float("inf"), "1000", None):
            with self.subTest(records=bad):
                with self.assertRaises(BackfillConfigurationError):
                    engine.execute_backfill(
                        "AAPL", sample_chunks(1), lambda c, b=bad: FetchOutcome(True, b)
                    )
        with self.assertRaises(BackfillConfigurationError):    # legacy tuple form
            engine.execute_backfill("AAPL", sample_chunks(1), lambda c: (True, False, -1))


class TestCheckpointing(unittest.TestCase):

    def test_completed_chunks_are_checkpointed_and_skipped_on_resume(self):
        # Regression: checkpointing was claimed in the documentation but no
        # checkpoint state was written or read anywhere.
        store = CheckpointStore()
        chunks = sample_chunks(4)

        engine = make_engine(FakeClock(), max_burst_capacity=10, checkpoint_store=store)
        first = engine.execute_backfill("AAPL", chunks, lambda c: FetchOutcome(True, 250))
        self.assertEqual(first.completed_chunks_count, 4)
        self.assertEqual(first.resumed_chunks_count, 0)
        self.assertEqual(store.completed_chunk_ids(), {c.chunk_id for c in chunks})

        calls = []

        def should_not_fetch(chunk):
            calls.append(chunk.chunk_id)
            return FetchOutcome(True, 250)

        engine2 = make_engine(FakeClock(), max_burst_capacity=10, checkpoint_store=store)
        second = engine2.execute_backfill("AAPL", sample_chunks(4), should_not_fetch)

        self.assertEqual(calls, [])
        self.assertEqual(second.resumed_chunks_count, 4)
        self.assertEqual(second.status, "BACKFILL_SUCCESS")
        self.assertEqual(second.total_records_ingested, 0)   # nothing re-ingested

    def test_failed_chunks_are_retried_on_resume(self):
        store = CheckpointStore()
        chunks = sample_chunks(3)
        state = {"fail": True}

        def fetch_func(chunk):
            if chunk.chunk_id == "CHK_02" and state["fail"]:
                return FetchOutcome(success=False, status_code=403)
            return FetchOutcome(True, 10)

        engine = make_engine(FakeClock(), max_burst_capacity=10, checkpoint_store=store)
        engine.execute_backfill("AAPL", chunks, fetch_func)
        self.assertNotIn("CHK_02", store.completed_chunk_ids())

        state["fail"] = False
        calls = []

        def tracking_fetch(chunk):
            calls.append(chunk.chunk_id)
            return fetch_func(chunk)

        engine2 = make_engine(FakeClock(), max_burst_capacity=10, checkpoint_store=store)
        report = engine2.execute_backfill("AAPL", sample_chunks(3), tracking_fetch)

        self.assertEqual(calls, ["CHK_02"])                  # only the gap is re-fetched
        self.assertEqual(report.status, "BACKFILL_SUCCESS")
        self.assertEqual(report.resumed_chunks_count, 2)

    def test_json_file_checkpoint_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backfill.json")
            store = JsonFileCheckpointStore(path)
            engine = make_engine(FakeClock(), max_burst_capacity=10, checkpoint_store=store)
            engine.execute_backfill("AAPL", sample_chunks(3), lambda c: FetchOutcome(True, 7))

            self.assertTrue(os.path.isfile(path))
            with open(path, encoding="utf-8") as handle:
                persisted = json.load(handle)
            self.assertEqual(len(persisted), 3)
            self.assertTrue(all(r["status"] == "COMPLETED" for r in persisted.values()))

            reopened = JsonFileCheckpointStore(path)
            self.assertEqual(len(reopened.completed_chunk_ids()), 3)

    def test_corrupt_checkpoint_file_does_not_abort_the_backfill(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "backfill.json")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not valid json")

            store = JsonFileCheckpointStore(path)
            self.assertEqual(store.completed_chunk_ids(), set())

            engine = make_engine(FakeClock(), max_burst_capacity=10, checkpoint_store=store)
            report = engine.execute_backfill("AAPL", sample_chunks(2), lambda c: FetchOutcome(True, 5))
            self.assertEqual(report.status, "BACKFILL_SUCCESS")


if __name__ == "__main__":
    unittest.main()

"""Behavioral tests for adaptive systematic tick sampling."""

import math
import threading
import unittest

from tick_sampler import AdaptiveTickSamplerEngine, SamplingMode


class TestAdaptiveTickSamplerEngine(unittest.TestCase):
    def setUp(self):
        self.sampler = AdaptiveTickSamplerEngine(target_max_rate_per_sec=10)

    def test_passthrough_mode_under_normal_rate(self):
        emitted = []
        timestamp = 1000.0
        for sequence_id in range(5):
            result = self.sampler.ingest_tick(
                "AAPL", sequence_id, 150.0 + sequence_id, 10.0, timestamp=timestamp
            )
            if result is not None:
                emitted.append(result)
            timestamp += 0.2

        self.assertEqual(len(emitted), 5)
        self.assertEqual(emitted[0].mode, SamplingMode.PASSTHROUGH)
        self.assertEqual(emitted[0].sampling_factor, 1)
        self.assertEqual(emitted[0].aggregated_tick_count, 1)
        self.assertFalse(emitted[0].is_flush)

    def test_systematic_sampling_preserves_volume_and_vwap(self):
        emitted = []
        timestamp = 1700000000.0
        total_input_volume = 0.0
        total_input_notional = 0.0
        for sequence_id in range(40):
            volume = 5.0
            price = 100.0 + sequence_id
            total_input_volume += volume
            total_input_notional += price * volume
            result = self.sampler.ingest_tick(
                "AAPL", sequence_id, price, volume, timestamp=timestamp
            )
            if result is not None:
                emitted.append(result)
            timestamp += 0.002

        self.assertEqual(emitted[-1].mode, SamplingMode.SYSTEMATIC_SAMPLING)
        self.assertGreater(emitted[-1].sampling_factor, 1)
        flushed = self.sampler.flush("AAPL")
        if flushed is not None:
            emitted.append(flushed)

        total_emitted_volume = sum(tick.volume for tick in emitted)
        total_emitted_notional = sum(tick.price * tick.volume for tick in emitted)
        self.assertAlmostEqual(total_emitted_volume, total_input_volume, places=8)
        self.assertAlmostEqual(total_emitted_notional, total_input_notional, places=8)

    def test_zero_timestamp_is_preserved(self):
        result = self.sampler.ingest_tick(
            "AAPL", 1, 100.0, 1.0, timestamp=0.0
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.timestamp, 0.0)

    def test_flush_is_deterministic_and_marked_synthetic(self):
        sampler = AdaptiveTickSamplerEngine(target_max_rate_per_sec=1)
        sampler.ingest_tick("AAPL", 1, 99.0, 1.0, timestamp=10.0)
        sampler.ingest_tick("AAPL", 2, 100.0, 2.0, timestamp=10.1)
        sampler.ingest_tick("AAPL", 3, 102.0, 3.0, timestamp=10.2)
        flushed = sampler.flush("AAPL", timestamp=20.0)

        self.assertIsNotNone(flushed)
        self.assertTrue(flushed.is_flush)
        self.assertEqual(flushed.sequence_id, -1)
        self.assertEqual(flushed.timestamp, 20.0)
        self.assertEqual(flushed.aggregated_tick_count, 2)
        self.assertAlmostEqual(flushed.price, 101.2)
        self.assertAlmostEqual(flushed.volume, 5.0)
        sampler.ingest_tick("AAPL", 4, 103.0, 1.0, timestamp=10.3)
        self.assertIsNotNone(sampler.flush("AAPL"))

    def test_flush_all_is_sorted_and_reset_removes_state(self):
        sampler = AdaptiveTickSamplerEngine(target_max_rate_per_sec=1)
        for symbol in ("MSFT", "AAPL"):
            sampler.ingest_tick(symbol, 1, 99.0, 1.0, timestamp=1.0)
            sampler.ingest_tick(symbol, 2, 100.0, 2.0, timestamp=1.1)
            sampler.ingest_tick(symbol, 3, 102.0, 3.0, timestamp=1.2)

        flushed = sampler.flush_all()

        self.assertEqual([tick.symbol for tick in flushed], ["AAPL", "MSFT"])
        self.assertEqual(sampler.tracked_symbol_count, 2)
        sampler.reset_symbol("AAPL")
        self.assertEqual(sampler.tracked_symbol_count, 1)

    def test_duplicate_and_out_of_order_ticks_are_rejected(self):
        self.sampler.ingest_tick("AAPL", 10, 100.0, 1.0, timestamp=10.0)

        with self.assertRaises(ValueError):
            self.sampler.ingest_tick("AAPL", 10, 101.0, 1.0, timestamp=10.1)
        with self.assertRaises(ValueError):
            self.sampler.ingest_tick("AAPL", 11, 101.0, 1.0, timestamp=9.9)

    def test_invalid_tick_values_are_rejected(self):
        invalid_ticks = (
            {"symbol": "", "sequence_id": 1, "price": 100.0, "volume": 1.0},
            {"symbol": "AAPL", "sequence_id": 1, "price": math.nan, "volume": 1.0},
            {"symbol": "AAPL", "sequence_id": 1, "price": 100.0, "volume": 0.0},
            {"symbol": "AAPL", "sequence_id": 1, "price": 100.0, "volume": -1.0},
            {"symbol": "AAPL", "sequence_id": 1, "price": 100.0, "volume": 1.0, "timestamp": math.inf},
        )
        for invalid_tick in invalid_ticks:
            with self.subTest(invalid_tick=invalid_tick):
                with self.assertRaises((TypeError, ValueError)):
                    self.sampler.ingest_tick(**invalid_tick)

    def test_invalid_target_rate_is_rejected(self):
        for target_rate in (0, -1, True, 10.0):
            with self.subTest(target_rate=target_rate):
                with self.assertRaises((TypeError, ValueError)):
                    AdaptiveTickSamplerEngine(target_max_rate_per_sec=target_rate)

    def test_future_flush_timestamp_does_not_change_event_watermark(self):
        sampler = AdaptiveTickSamplerEngine(target_max_rate_per_sec=1)
        sampler.ingest_tick("AAPL", 1, 100.0, 1.0, timestamp=10.0)
        sampler.ingest_tick("AAPL", 2, 101.0, 1.0, timestamp=10.1)
        sampler.ingest_tick("AAPL", 3, 102.0, 1.0, timestamp=10.2)
        sampler.flush("AAPL", timestamp=20.0)

        sampler.ingest_tick("AAPL", 4, 103.0, 1.0, timestamp=10.3)

        self.assertIsNotNone(sampler.flush("AAPL"))

    def _symbol_state(self, sampler, symbol):
        """Every mutable per-symbol field the engine keeps, as one comparable tuple."""
        return (
            sampler.tick_counters[symbol],
            sampler.accumulated_vol[symbol],
            sampler.accumulated_notional[symbol],
            sampler.current_window_sec[symbol],
            sampler.current_window_count[symbol],
            sampler.previous_window_count[symbol],
            sampler.last_sequence_id[symbol],
            sampler.last_timestamp[symbol],
        )

    def test_aggregate_overflow_is_rejected_without_corrupting_state(self):
        """A rejected tick must leave every piece of engine state untouched,
        including the rolling-rate window that drives the sampling factor."""
        sampler = AdaptiveTickSamplerEngine(target_max_rate_per_sec=1)
        sampler.ingest_tick("AAPL", 1, 1e154, 1e154, timestamp=1.0)
        sampler.ingest_tick("AAPL", 2, 1e154, 1e154, timestamp=1.1)
        state_before = self._symbol_state(sampler, "AAPL")

        with self.assertRaises(OverflowError):
            sampler.ingest_tick("AAPL", 3, 1e154, 1e154, timestamp=1.2)

        self.assertEqual(self._symbol_state(sampler, "AAPL"), state_before)
        self.assertEqual(sampler.tracked_symbol_count, 1)
        self.assertEqual(sampler.last_sequence_id["AAPL"], 2)


    def test_symbol_state_isolated(self):
        first_tick = self.sampler.ingest_tick(
            "AAPL", 1, 100.0, 2.0, timestamp=1.0
        )
        second_tick = self.sampler.ingest_tick(
            "MSFT", 1, 200.0, 3.0, timestamp=1.0
        )

        self.assertEqual(first_tick.symbol, "AAPL")
        self.assertEqual(second_tick.symbol, "MSFT")
        self.assertEqual(self.sampler.tracked_symbol_count, 2)

    def test_rate_equal_to_target_stays_in_passthrough(self):
        """The overload boundary is strictly greater-than: at exactly the target
        rate every trade must still be emitted one-for-one."""
        sampler = AdaptiveTickSamplerEngine(target_max_rate_per_sec=3)
        # Within a single event-time second the previous window is empty, so the
        # rolling estimate for the nth tick of that second is exactly n.
        first = sampler.ingest_tick("AAPL", 0, 100.0, 1.0, timestamp=200.000)
        second = sampler.ingest_tick("AAPL", 1, 101.0, 1.0, timestamp=200.001)
        third = sampler.ingest_tick("AAPL", 2, 102.0, 1.0, timestamp=200.002)

        for emitted in (first, second, third):
            self.assertIsNotNone(emitted)
            self.assertEqual(emitted.mode, SamplingMode.PASSTHROUGH)
            self.assertEqual(emitted.sampling_factor, 1)
            self.assertEqual(emitted.aggregated_tick_count, 1)

        # The fourth tick puts the estimate at 4 > 3 and opens a sampled block.
        self.assertIsNone(sampler.ingest_tick("AAPL", 3, 103.0, 1.0, timestamp=200.003))
        fifth = sampler.ingest_tick("AAPL", 4, 104.0, 1.0, timestamp=200.004)

        self.assertEqual(fifth.mode, SamplingMode.SYSTEMATIC_SAMPLING)
        self.assertEqual(fifth.sampling_factor, 2)
        self.assertEqual(fifth.aggregated_tick_count, 2)

    def test_passthrough_emission_can_drain_a_partial_sampled_block(self):
        """When the rate falls back below target with a block half accumulated,
        the next emission reports mode=PASSTHROUGH and sampling_factor=1 while
        still representing more than one trade, at a VWAP that was never traded.
        Consumers must key on aggregated_tick_count, not on mode."""
        sampler = AdaptiveTickSamplerEngine(target_max_rate_per_sec=5)
        timestamp = 100.0
        for sequence_id in range(13):
            sampler.ingest_tick(
                "AAPL", sequence_id, 100.0 + sequence_id, 1.0, timestamp=timestamp
            )
            timestamp += 0.001
        self.assertEqual(sampler.tick_counters["AAPL"], 1)

        drained = sampler.ingest_tick("AAPL", 99, 500.0, 1.0, timestamp=103.0)

        self.assertEqual(drained.mode, SamplingMode.PASSTHROUGH)
        self.assertEqual(drained.sampling_factor, 1)
        self.assertEqual(drained.aggregated_tick_count, 2)
        # Residual trade 12 priced 112.0 plus the new trade priced 500.0.
        self.assertAlmostEqual(drained.volume, 2.0)
        self.assertAlmostEqual(drained.price, (112.0 * 1.0 + 500.0 * 1.0) / 2.0)
        self.assertEqual(drained.sequence_id, 99)
        self.assertFalse(drained.is_flush)

    def test_flush_timestamp_before_last_tick_is_rejected(self):
        sampler = AdaptiveTickSamplerEngine(target_max_rate_per_sec=1)
        sampler.ingest_tick("AAPL", 1, 100.0, 1.0, timestamp=10.0)
        sampler.ingest_tick("AAPL", 2, 101.0, 2.0, timestamp=10.1)

        with self.assertRaises(ValueError):
            sampler.flush("AAPL", timestamp=9.0)
        with self.assertRaises(ValueError):
            sampler.flush_all(timestamp=9.0)

        # A rejected flush must not have consumed or partially emitted residual.
        remaining = sampler.flush_all()
        self.assertEqual(len(remaining), 1)
        self.assertAlmostEqual(remaining[0].volume, 2.0)

    def test_reset_symbol_flush_flag_controls_residual_handling(self):
        sampler = AdaptiveTickSamplerEngine(target_max_rate_per_sec=1)
        sampler.ingest_tick("AAPL", 1, 100.0, 1.0, timestamp=10.0)
        sampler.ingest_tick("AAPL", 2, 106.0, 4.0, timestamp=10.1)

        residual = sampler.reset_symbol("AAPL", flush=True)

        self.assertIsNotNone(residual)
        self.assertTrue(residual.is_flush)
        self.assertEqual(residual.sequence_id, -1)
        self.assertAlmostEqual(residual.volume, 4.0)
        self.assertAlmostEqual(residual.price, 106.0)
        self.assertEqual(sampler.tracked_symbol_count, 0)
        self.assertNotIn("AAPL", sampler.last_sequence_id)

        sampler.ingest_tick("MSFT", 1, 200.0, 1.0, timestamp=10.0)
        sampler.ingest_tick("MSFT", 2, 201.0, 1.0, timestamp=10.1)

        self.assertIsNone(sampler.reset_symbol("MSFT", flush=False))
        self.assertEqual(sampler.tracked_symbol_count, 0)

    def test_sequence_enforcement_can_be_disabled_for_non_monotonic_feeds(self):
        permissive = AdaptiveTickSamplerEngine(
            target_max_rate_per_sec=10, enforce_monotonic_sequence=False
        )
        permissive.ingest_tick("AAPL", 7, 100.0, 1.0, timestamp=1.0)
        repeated = permissive.ingest_tick("AAPL", 7, 102.0, 1.0, timestamp=1.0)

        self.assertIsNotNone(repeated)
        self.assertAlmostEqual(repeated.price, 102.0)

        self.sampler.ingest_tick("AAPL", 7, 100.0, 1.0, timestamp=1.0)
        with self.assertRaises(ValueError):
            self.sampler.ingest_tick("AAPL", 7, 102.0, 1.0, timestamp=1.0)

    def test_concurrent_ingestion_of_one_symbol_conserves_volume_and_notional(self):
        """Threads sharing a symbol contend on the same accumulator; a lost
        read-modify-write would show up as missing volume, notional, or count."""
        sampler = AdaptiveTickSamplerEngine(
            target_max_rate_per_sec=100, enforce_monotonic_sequence=False
        )
        thread_count = 4
        ticks_per_thread = 500
        emitted = []
        emitted_lock = threading.Lock()
        errors = []

        def producer(thread_index):
            local_output = []
            try:
                for offset in range(ticks_per_thread):
                    result = sampler.ingest_tick(
                        "AAPL",
                        thread_index * ticks_per_thread + offset,
                        100.0 + (offset % 7),
                        1.0,
                        timestamp=1.0,
                    )
                    if result is not None:
                        local_output.append(result)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)
            with emitted_lock:
                emitted.extend(local_output)

        threads = [
            threading.Thread(target=producer, args=(index,))
            for index in range(thread_count)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        emitted.extend(sampler.flush_all())

        expected_volume = float(thread_count * ticks_per_thread)
        expected_notional = thread_count * sum(
            100.0 + (offset % 7) for offset in range(ticks_per_thread)
        )
        self.assertEqual(errors, [])
        self.assertEqual(sampler.tracked_symbol_count, 1)
        self.assertAlmostEqual(
            sum(tick.volume for tick in emitted), expected_volume, delta=1e-6
        )
        self.assertAlmostEqual(
            sum(tick.price * tick.volume for tick in emitted),
            expected_notional,
            delta=1e-6,
        )
        self.assertEqual(
            sum(tick.aggregated_tick_count for tick in emitted),
            thread_count * ticks_per_thread,
        )


if __name__ == "__main__":
    unittest.main()

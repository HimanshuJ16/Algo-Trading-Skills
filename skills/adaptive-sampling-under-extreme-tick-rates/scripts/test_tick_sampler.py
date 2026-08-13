"""Behavioral tests for adaptive systematic tick sampling."""

import math
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

    def test_aggregate_overflow_is_rejected_without_corrupting_state(self):
        sampler = AdaptiveTickSamplerEngine(target_max_rate_per_sec=1)
        sampler.ingest_tick("AAPL", 1, 1e154, 1e154, timestamp=1.0)
        sampler.ingest_tick("AAPL", 2, 1e154, 1e154, timestamp=1.1)

        with self.assertRaises(OverflowError):
            sampler.ingest_tick("AAPL", 3, 1e154, 1e154, timestamp=1.2)

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


if __name__ == "__main__":
    unittest.main()

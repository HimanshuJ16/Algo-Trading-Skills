"""
Unit tests for multi-region-active-active-tick-ingestion skill.

All tests drive the ingestor with explicit ``receipt_time`` values so arbitration is
deterministic and independent of the wall clock.
"""
import logging
import threading
import time
import unittest

from active_active_ingest import (
    ArbitrationOutcome,
    MultiRegionActiveActiveIngestor,
    RegionStatus,
)

# The engine deliberately warns on saturation, sequence gaps and inverted arrival
# order; several tests exercise exactly those paths, so keep the suite output clean.
logging.getLogger("active_active_ingest").setLevel(logging.CRITICAL)


class TestArbitrationBasics(unittest.TestCase):

    def setUp(self):
        self.ingestor = MultiRegionActiveActiveIngestor(ttl_seconds=5.0)

    def test_first_arrival_accepted_and_duplicate_dropped(self):
        t0 = time.time()
        res_a = self.ingestor.ingest_regional_tick(
            region_id="us-east-1",
            symbol="AAPL",
            sequence_id=1001,
            timestamp=t0,
            price=150.00,
            volume=10.0,
            receipt_time=t0,
        )

        self.assertFalse(res_a.is_duplicate)
        self.assertEqual(res_a.first_arrived_region, "us-east-1")
        self.assertEqual(res_a.outcome, ArbitrationOutcome.FIRST_ARRIVAL)

        # Region B identical tick arrives 0.005s (5ms) later at t0 + 0.005
        res_b = self.ingestor.ingest_regional_tick(
            region_id="us-west-2",
            symbol="AAPL",
            sequence_id=1001,
            timestamp=t0,
            price=150.00,
            volume=10.0,
            receipt_time=t0 + 0.005,
        )

        self.assertTrue(res_b.is_duplicate)
        self.assertEqual(res_b.first_arrived_region, "us-east-1")
        self.assertEqual(res_b.outcome, ArbitrationOutcome.CROSS_REGION_DUPLICATE)
        self.assertAlmostEqual(res_b.latency_delta_ms, 5.0, places=2)
        self.assertFalse(res_b.arrival_order_inverted)

    def test_regional_win_telemetry(self):
        t0 = time.time()
        # 3 ticks where us-east-1 wins, 1 tick where us-west-2 wins
        for seq in [1, 2, 3]:
            self.ingestor.ingest_regional_tick("us-east-1", "AAPL", seq, t0, 150.0, 1.0, t0)
            self.ingestor.ingest_regional_tick("us-west-2", "AAPL", seq, t0, 150.0, 1.0, t0 + 0.002)

        # 1 tick where us-west-2 arrives first
        self.ingestor.ingest_regional_tick("us-west-2", "AAPL", 4, t0, 150.0, 1.0, t0)
        self.ingestor.ingest_regional_tick("us-east-1", "AAPL", 4, t0, 150.0, 1.0, t0 + 0.002)

        stats = self.ingestor.get_regional_win_statistics()
        self.assertEqual(stats["us-east-1"]["wins"], 3)
        self.assertEqual(stats["us-west-2"]["wins"], 1)
        self.assertEqual(stats["us-east-1"]["win_percentage"], 75.0)
        # Every region saw all four ticks, whether it won them or not.
        self.assertEqual(stats["us-east-1"]["messages"], 4)
        self.assertEqual(stats["us-west-2"]["messages"], 4)
        self.assertEqual(stats["us-east-1"]["duplicates"], 1)
        self.assertEqual(stats["us-west-2"]["duplicates"], 3)

    def test_region_and_symbol_are_normalised(self):
        res_a = self.ingestor.ingest_regional_tick(" US-East-1 ", " aapl ", 1, 0.0, 1.5, 1.0, 100.0)
        res_b = self.ingestor.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 1.5, 1.0, 100.1)
        self.assertEqual(res_a.symbol, "AAPL")
        self.assertEqual(res_a.tick.region_id, "us-east-1")
        self.assertTrue(res_b.is_duplicate)
        self.assertEqual(res_b.outcome, ArbitrationOutcome.SAME_REGION_DUPLICATE)


class TestReceiptTimeHandling(unittest.TestCase):
    """Regression: ``receipt_time or time.time()`` treated a legitimate 0.0 as absent."""

    def test_receipt_time_zero_is_honoured_not_replaced_by_wall_clock(self):
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=10.0)
        first = ing.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=0.0)
        self.assertEqual(first.tick.local_receipt_time, 0.0)

        dup = ing.ingest_regional_tick("us-west-2", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=0.005)
        self.assertTrue(dup.is_duplicate)
        self.assertAlmostEqual(dup.latency_delta_ms, 5.0, places=6)

    def test_receipt_time_defaults_to_wall_clock_only_when_omitted(self):
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=10.0)
        before = time.time()
        res = ing.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, 1.0)
        self.assertGreaterEqual(res.tick.local_receipt_time, before)

    def test_out_of_order_processing_is_flagged_not_silently_negative(self):
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=10.0)
        ing.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=5.0)
        inverted = ing.ingest_regional_tick("us-west-2", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=4.0)
        self.assertTrue(inverted.is_duplicate)
        self.assertTrue(inverted.arrival_order_inverted)
        self.assertLess(inverted.latency_delta_ms, 0.0)
        self.assertIn("clock domain", inverted.message)


class TestSignatureIdentity(unittest.TestCase):
    """Regression: fixed 4-decimal formatting merged distinct ticks onto one signature."""

    def setUp(self):
        self.ingestor = MultiRegionActiveActiveIngestor(ttl_seconds=10.0)

    def test_sub_tick_price_differences_are_not_merged(self):
        # Under ``f"{price:.4f}"`` both render as "0.0000" and the second tick vanishes.
        self.ingestor.ingest_regional_tick("us-east-1", "SHIB", 7, 0.0, 0.00001234, 1.0, 1.0)
        second = self.ingestor.ingest_regional_tick("us-east-1", "SHIB", 7, 0.0, 0.00004321, 1.0, 1.001)
        self.assertFalse(second.is_duplicate)

    def test_eight_decimal_volume_differences_are_not_merged(self):
        self.ingestor.ingest_regional_tick("us-east-1", "BTCUSD", 11, 0.0, 50000.0, 0.00000001, 1.0)
        second = self.ingestor.ingest_regional_tick("us-east-1", "BTCUSD", 11, 0.0, 50000.0, 0.00000009, 1.001)
        self.assertFalse(second.is_duplicate)

    def test_identical_payloads_still_produce_identical_signatures(self):
        a = self.ingestor.compute_tick_signature("AAPL", 5, 150.25, 100.0)
        b = self.ingestor.compute_tick_signature("aapl", 5, 150.25, 100)
        self.assertEqual(a, b)

    def test_integer_and_float_payloads_agree(self):
        # A region delivering 150 and a region delivering 150.0 is the same tick.
        self.assertEqual(
            self.ingestor.compute_tick_signature("AAPL", 5, 150, 100),
            self.ingestor.compute_tick_signature("AAPL", 5, 150.0, 100.0),
        )

    def test_negative_zero_normalises(self):
        self.assertEqual(
            self.ingestor.compute_tick_signature("AAPL", 5, -0.0, 1.0),
            self.ingestor.compute_tick_signature("AAPL", 5, 0.0, 1.0),
        )

    def test_exchange_timestamp_is_not_part_of_the_signature(self):
        # Documented contract: regions that re-stamp arrival time must still dedup.
        first = self.ingestor.ingest_regional_tick("us-east-1", "AAPL", 3, 1000.0, 150.0, 1.0, 1.0)
        second = self.ingestor.ingest_regional_tick("us-west-2", "AAPL", 3, 1000.9, 150.0, 1.0, 1.002)
        self.assertFalse(first.is_duplicate)
        self.assertTrue(second.is_duplicate)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.ingestor = MultiRegionActiveActiveIngestor(ttl_seconds=10.0)

    def test_non_finite_price_rejected(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(ValueError):
                self.ingestor.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, bad, 1.0, 1.0)

    def test_non_finite_volume_rejected(self):
        with self.assertRaises(ValueError):
            self.ingestor.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, float("nan"), 1.0)

    def test_empty_symbol_and_region_rejected(self):
        with self.assertRaises(ValueError):
            self.ingestor.ingest_regional_tick("us-east-1", "   ", 1, 0.0, 150.0, 1.0, 1.0)
        with self.assertRaises(ValueError):
            self.ingestor.ingest_regional_tick("", "AAPL", 1, 0.0, 150.0, 1.0, 1.0)

    def test_non_integer_sequence_id_rejected(self):
        with self.assertRaises(ValueError):
            self.ingestor.ingest_regional_tick("us-east-1", "AAPL", 1.5, 0.0, 150.0, 1.0, 1.0)
        with self.assertRaises(ValueError):
            self.ingestor.ingest_regional_tick("us-east-1", "AAPL", True, 0.0, 150.0, 1.0, 1.0)

    def test_non_finite_receipt_time_rejected(self):
        with self.assertRaises(ValueError):
            self.ingestor.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, 1.0, float("nan"))

    def test_rejected_tick_leaves_no_state_behind(self):
        with self.assertRaises(ValueError):
            self.ingestor.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, float("nan"), 1.0, 1.0)
        self.assertEqual(self.ingestor.get_dedup_cache_stats()["size"], 0)
        self.assertEqual(self.ingestor.get_regional_win_statistics(), {})

    def test_invalid_constructor_arguments_rejected(self):
        for kwargs in (
            {"ttl_seconds": 0},
            {"ttl_seconds": -1.0},
            {"ttl_seconds": float("inf")},
            {"max_signatures": 0},
            {"win_rate_window": 0},
            {"silence_threshold_seconds": 0},
        ):
            with self.assertRaises(ValueError):
                MultiRegionActiveActiveIngestor(**kwargs)


class TestDeduplicationWindow(unittest.TestCase):

    def test_signature_survives_until_ttl_boundary_then_expires(self):
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=10.0)
        ing.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=100.0)

        just_inside = ing.ingest_regional_tick(
            "us-west-2", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=109.999)
        self.assertTrue(just_inside.is_duplicate)

        # At exactly ttl_seconds after the recorded arrival the entry is evicted, so the
        # twin re-enters as a fresh first arrival -- the documented cost of a finite
        # dedup window, and the reason ttl_seconds must exceed the worst-case spread.
        at_boundary = ing.ingest_regional_tick(
            "us-west-2", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=110.0)
        self.assertFalse(at_boundary.is_duplicate)

    def test_expired_entries_are_evicted_from_the_cache(self):
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=1.0)
        for seq in range(50):
            ing.ingest_regional_tick("us-east-1", "AAPL", seq, 0.0, 150.0 + seq, 1.0, receipt_time=100.0)
        self.assertEqual(ing.get_dedup_cache_stats()["size"], 50)

        ing.ingest_regional_tick("us-east-1", "AAPL", 999, 0.0, 1.0, 1.0, receipt_time=200.0)
        self.assertEqual(ing.get_dedup_cache_stats()["size"], 1)

    def test_cache_is_hard_bounded_and_reports_saturation(self):
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=1e9, max_signatures=100)
        for seq in range(500):
            ing.ingest_regional_tick("us-east-1", "AAPL", seq, 0.0, 150.0 + seq, 1.0, receipt_time=1.0)
        stats = ing.get_dedup_cache_stats()
        self.assertEqual(stats["size"], 100)
        self.assertTrue(stats["saturated"])
        self.assertEqual(stats["capacity_evictions"], 400)

    def test_saturated_cache_lets_an_in_window_duplicate_through(self):
        # Documents the failure mode the capacity bound trades memory safety for.
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=1e9, max_signatures=2)
        ing.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=1.0)
        ing.ingest_regional_tick("us-east-1", "AAPL", 2, 0.0, 151.0, 1.0, receipt_time=1.001)
        ing.ingest_regional_tick("us-east-1", "AAPL", 3, 0.0, 152.0, 1.0, receipt_time=1.002)
        leaked = ing.ingest_regional_tick("us-west-2", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=1.003)
        self.assertFalse(leaked.is_duplicate)


class TestDuplicateClassification(unittest.TestCase):

    def test_same_region_retransmission_is_not_an_arbitration_win(self):
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=10.0)
        ing.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=1.0)
        dup = ing.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=1.002)

        self.assertTrue(dup.is_duplicate)
        self.assertEqual(dup.outcome, ArbitrationOutcome.SAME_REGION_DUPLICATE)
        self.assertNotIn("faster than", dup.message)
        # A region cannot beat itself: no second win is credited.
        self.assertEqual(ing.get_regional_win_statistics()["us-east-1"]["wins"], 1)


class TestEmittedSequenceContinuity(unittest.TestCase):
    """Arbitration cannot repair a message lost in every region -- it must surface it."""

    def test_gap_surviving_arbitration_is_reported(self):
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=10.0)
        ing.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=1.0)
        ing.ingest_regional_tick("us-west-2", "AAPL", 2, 0.0, 151.0, 1.0, receipt_time=1.001)
        gapped = ing.ingest_regional_tick("us-east-1", "AAPL", 6, 0.0, 152.0, 1.0, receipt_time=1.002)

        self.assertEqual(gapped.emitted_sequence_gap, 3)
        self.assertFalse(gapped.emitted_out_of_order)
        self.assertIn("missing", gapped.message)

    def test_contiguous_emissions_report_no_gap(self):
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=10.0)
        ing.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=1.0)
        clean = ing.ingest_regional_tick("us-west-2", "AAPL", 2, 0.0, 151.0, 1.0, receipt_time=1.001)
        self.assertEqual(clean.emitted_sequence_gap, 0)
        self.assertFalse(clean.emitted_out_of_order)

    def test_arrival_order_is_not_sequence_order(self):
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=10.0)
        ing.ingest_regional_tick("us-east-1", "AAPL", 5, 0.0, 150.0, 1.0, receipt_time=1.0)
        regressed = ing.ingest_regional_tick("us-west-2", "AAPL", 4, 0.0, 149.0, 1.0, receipt_time=1.001)
        self.assertTrue(regressed.emitted_out_of_order)
        self.assertEqual(regressed.emitted_sequence_gap, 0)

    def test_continuity_is_tracked_per_symbol(self):
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=10.0)
        ing.ingest_regional_tick("us-east-1", "AAPL", 100, 0.0, 150.0, 1.0, receipt_time=1.0)
        other = ing.ingest_regional_tick("us-east-1", "MSFT", 1, 0.0, 300.0, 1.0, receipt_time=1.001)
        self.assertEqual(other.emitted_sequence_gap, 0)
        self.assertFalse(other.emitted_out_of_order)


class TestRegionalHealth(unittest.TestCase):

    def test_win_rate_is_not_liveness(self):
        """A dark region leaves the survivor at 100% -- identical to healthy operation."""
        ing = MultiRegionActiveActiveIngestor(
            ttl_seconds=10.0, expected_regions=["us-east-1", "us-west-2"],
            silence_threshold_seconds=2.0)
        t = 100.0
        for seq in range(20):
            ing.ingest_regional_tick("us-east-1", "AAPL", seq, 0.0, 150.0 + seq, 1.0, t + seq * 0.01)
            ing.ingest_regional_tick("us-west-2", "AAPL", seq, 0.0, 150.0 + seq, 1.0, t + seq * 0.01 + 0.002)

        # us-east-1 already wins everything while both regions are perfectly healthy.
        stats = ing.get_regional_win_statistics()
        self.assertEqual(stats["us-east-1"]["win_percentage"], 100.0)
        healthy = ing.get_regional_health(now=t + 0.25)
        self.assertEqual(healthy["us-west-2"].status, RegionStatus.ACTIVE)

        # us-west-2 goes dark; the win rate is unchanged, health is not.
        for seq in range(20, 40):
            ing.ingest_regional_tick("us-east-1", "AAPL", seq, 0.0, 150.0 + seq, 1.0, t + 10.0 + seq * 0.01)
        self.assertEqual(
            ing.get_regional_win_statistics()["us-east-1"]["win_percentage"], 100.0)

        degraded = ing.get_regional_health(now=t + 10.5)
        self.assertEqual(degraded["us-west-2"].status, RegionStatus.SILENT)
        self.assertEqual(degraded["us-east-1"].status, RegionStatus.ACTIVE)
        self.assertGreater(degraded["us-west-2"].seconds_since_last_message, 2.0)

    def test_declared_region_that_never_connects_is_reported(self):
        ing = MultiRegionActiveActiveIngestor(
            ttl_seconds=10.0, expected_regions=["us-east-1", "eu-west-1"])
        ing.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=100.0)
        health = ing.get_regional_health(now=100.0)
        self.assertEqual(health["eu-west-1"].status, RegionStatus.NEVER_SEEN)
        self.assertIsNone(health["eu-west-1"].seconds_since_last_message)
        self.assertEqual(health["eu-west-1"].messages, 0)

    def test_rolling_win_percentage_tracks_recent_window(self):
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=1e9, win_rate_window=4)
        t = 1.0
        for seq in range(4):  # us-east-1 wins the first four
            ing.ingest_regional_tick("us-east-1", "AAPL", seq, 0.0, 150.0 + seq, 1.0, t + seq)
        for seq in range(4, 8):  # us-west-2 wins the next four
            ing.ingest_regional_tick("us-west-2", "AAPL", seq, 0.0, 150.0 + seq, 1.0, t + seq)

        stats = ing.get_regional_win_statistics()
        self.assertEqual(stats["us-east-1"]["win_percentage"], 50.0)      # lifetime
        self.assertEqual(stats["us-east-1"]["rolling_win_percentage"], 0.0)  # last 4
        self.assertEqual(stats["us-west-2"]["rolling_win_percentage"], 100.0)


class TestConcurrency(unittest.TestCase):
    """Regression: the ingestor is called from one feed-handler thread per region."""

    def test_concurrent_regions_emit_each_tick_exactly_once(self):
        n = 4000
        ing = MultiRegionActiveActiveIngestor(ttl_seconds=1e9, max_signatures=10 * n)
        emitted = []
        emitted_lock = threading.Lock()
        errors = []
        start = threading.Barrier(2)

        def worker(region: str, offset: float) -> None:
            local = []
            try:
                start.wait(timeout=10)
                for seq in range(n):
                    res = ing.ingest_regional_tick(
                        region, "AAPL", seq, 0.0, 100.0 + seq, 1.0,
                        receipt_time=1.0 + seq * 1e-6 + offset)
                    if not res.is_duplicate:
                        local.append(seq)
            except Exception as exc:  # pragma: no cover - surfaced via assertion below
                errors.append(exc)
            with emitted_lock:
                emitted.extend(local)

        threads = [
            threading.Thread(target=worker, args=("us-east-1", 0.0)),
            threading.Thread(target=worker, args=("us-west-2", 1e-7)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(errors, [], f"concurrent ingest raised: {errors}")
        self.assertEqual(len(emitted), n, "every tick must be emitted exactly once")
        self.assertEqual(len(set(emitted)), n, "no sequence may be emitted twice")


class TestReset(unittest.TestCase):

    def test_reset_clears_arbitration_state(self):
        ing = MultiRegionActiveActiveIngestor(
            ttl_seconds=1e9, expected_regions=["us-east-1"])
        ing.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=1.0)
        ing.reset()

        self.assertEqual(ing.get_dedup_cache_stats()["size"], 0)
        self.assertEqual(ing.get_regional_win_statistics(), {})
        self.assertEqual(ing.get_regional_health(now=1.0)["us-east-1"].status,
                         RegionStatus.NEVER_SEEN)

        # A recycled sequence number after a sequence-space reset is a fresh tick,
        # not a duplicate, and must not be reported as a sequence regression.
        again = ing.ingest_regional_tick("us-east-1", "AAPL", 1, 0.0, 150.0, 1.0, receipt_time=2.0)
        self.assertFalse(again.is_duplicate)
        self.assertFalse(again.emitted_out_of_order)


if __name__ == "__main__":
    unittest.main()

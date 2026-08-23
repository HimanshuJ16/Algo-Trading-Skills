import decimal
import unittest

from cross_vendor_timestamp_precision_reconciliation import (
    INT64_MAX,
    CrossVendorTimestampReconciler,
    TimestampReconciliationReport,
    VendorTickRecord,
)

# 1700000000 s = 2023-11-14T22:13:20Z. Expected nanosecond values below are
# derived by hand from the decimal digits of the input, not by re-running the
# implementation's own arithmetic.
BASE_S = 1_700_000_000
BASE_NS = 1_700_000_000_000_000_000


class TestTimestampNormalization(unittest.TestCase):

    def setUp(self):
        self.reconciler = CrossVendorTimestampReconciler(max_allowed_vendor_drift_ms=5.0)

    def test_millisecond_scaling_is_exact(self):
        # Regression: int(1700000000123 * 1e6) == 1700000000123000064 in float64
        # (spacing at 1.7e18 is 256 ns). Exact arithmetic must land on ...000.
        ns, _, tier = self.reconciler.normalize_timestamp_to_ns(1_700_000_000_123, "MILLISECONDS")
        self.assertEqual(ns, BASE_NS + 123_000_000)
        self.assertEqual(ns % 1_000_000, 0, "a millisecond input must have no sub-ms residue")
        self.assertEqual(tier, "MILLISECONDS")

    def test_microsecond_and_nanosecond_scaling_are_exact(self):
        ns_us, _, tier_us = self.reconciler.normalize_timestamp_to_ns(
            1_700_000_000_123_456, "MICROSECONDS")
        self.assertEqual(ns_us, BASE_NS + 123_456_000)
        self.assertEqual(tier_us, "MICROSECONDS")

        ns_ns, _, tier_ns = self.reconciler.normalize_timestamp_to_ns(
            "1700000000123456789", "NANOSECONDS")
        self.assertEqual(ns_ns, BASE_NS + 123_456_789)
        self.assertEqual(tier_ns, "NANOSECONDS")

    def test_float_seconds_reconstruct_the_intended_decimal(self):
        # Regression: int(1700000000.123 * 1e9) == 1700000000122999808 (192 ns low,
        # and truncation biases every such value downward).
        ns, _, _ = self.reconciler.normalize_timestamp_to_ns(1_700_000_000.123, "SECONDS")
        self.assertEqual(ns, BASE_NS + 123_000_000)

        for seconds, expected in (
            (1_700_000_000.1, BASE_NS + 100_000_000),
            (1_700_000_000.999999, BASE_NS + 999_999_000),
            (1_700_000_000.000001, BASE_NS + 1_000),
        ):
            with self.subTest(seconds=seconds):
                self.assertEqual(
                    self.reconciler.normalize_timestamp_to_ns(seconds, "SECONDS")[0], expected)

    def test_decimal_string_seconds_are_exact(self):
        ns, _, _ = self.reconciler.normalize_timestamp_to_ns("1700000000.123456789", "SECONDS")
        self.assertEqual(ns, BASE_NS + 123_456_789)

    def test_nanosecond_float_input_is_rejected(self):
        # 1.70000000012345678e18 silently became 1700000000123456768 before.
        with self.assertRaises(ValueError):
            self.reconciler.normalize_timestamp_to_ns(1.70000000012345678e18, "NANOSECONDS")

    def test_iso8601_nanosecond_digits_survive(self):
        # datetime caps at microseconds; the last three digits used to vanish.
        ns, iso_str, tier = self.reconciler.normalize_timestamp_to_ns(
            "2023-11-14T22:13:20.123456789Z", "ISO8601")
        self.assertEqual(ns, BASE_NS + 123_456_789)
        self.assertEqual(tier, "NANOSECONDS")
        self.assertEqual(iso_str, "2023-11-14T22:13:20.123456789Z")

    def test_iso8601_millisecond_string_is_exact_and_tiered_correctly(self):
        # Regression: '.123Z' produced 1700000000122999808 ns and was labelled
        # MICROSECONDS regardless of how many fractional digits were present.
        ns, _, tier = self.reconciler.normalize_timestamp_to_ns(
            "2023-11-14T22:13:20.123Z", "ISO8601")
        self.assertEqual(ns, BASE_NS + 123_000_000)
        self.assertEqual(tier, "MILLISECONDS")

    def test_iso8601_precision_tier_tracks_fractional_digits(self):
        cases = {
            "2023-11-14T22:13:20Z": "SECONDS",
            "2023-11-14T22:13:20.1Z": "MILLISECONDS",
            "2023-11-14T22:13:20.123456Z": "MICROSECONDS",
            "2023-11-14T22:13:20.1234567Z": "NANOSECONDS",
        }
        for value, expected in cases.items():
            with self.subTest(value=value):
                self.assertEqual(
                    self.reconciler.normalize_timestamp_to_ns(value, "ISO8601")[2], expected)

    def test_iso8601_offset_is_applied(self):
        # 17:13:20.123456-05:00 is the same instant as 22:13:20.123456Z.
        ns_offset, _, _ = self.reconciler.normalize_timestamp_to_ns(
            "2023-11-14T17:13:20.123456-05:00", "ISO8601")
        ns_utc, _, _ = self.reconciler.normalize_timestamp_to_ns(
            "2023-11-14T22:13:20.123456Z", "ISO8601")
        self.assertEqual(ns_offset, ns_utc)
        self.assertEqual(ns_offset, BASE_NS + 123_456_000)

    def test_iso8601_comma_separator_and_naive_input(self):
        self.assertEqual(
            self.reconciler.normalize_timestamp_to_ns("2023-11-14T22:13:20,500Z", "ISO8601")[0],
            BASE_NS + 500_000_000)
        # Naive input is treated as UTC (and warns).
        with self.assertLogs(
            "cross_vendor_timestamp_precision_reconciliation", level="WARNING"
        ) as captured:
            ns, _, _ = self.reconciler.normalize_timestamp_to_ns(
                "2023-11-14T22:13:20.123Z".replace("Z", ""), "ISO8601")
        self.assertEqual(ns, BASE_NS + 123_000_000)
        self.assertTrue(any("no UTC offset" in line for line in captured.output))

    def test_sub_nanosecond_iso_precision_is_rejected(self):
        with self.assertRaises(ValueError):
            self.reconciler.normalize_timestamp_to_ns(
                "2023-11-14T22:13:20.1234567891Z", "ISO8601")
        # Surplus zero padding is harmless.
        self.assertEqual(
            self.reconciler.normalize_timestamp_to_ns(
                "2023-11-14T22:13:20.1234567890Z", "ISO8601")[0],
            BASE_NS + 123_456_789)

    def test_iso_round_trip_preserves_the_value(self):
        for ns in (BASE_NS + 123_456_789, BASE_NS, 0, 1):
            with self.subTest(ns=ns):
                iso_str = CrossVendorTimestampReconciler.ns_to_iso_utc(ns)
                self.assertEqual(
                    self.reconciler.normalize_timestamp_to_ns(iso_str, "ISO8601")[0], ns)

    def test_invalid_inputs_raise(self):
        for raw, fmt in (
            ("abc", "SECONDS"),
            ("", "SECONDS"),
            (float("nan"), "SECONDS"),
            (float("inf"), "MILLISECONDS"),
            (1700000000, "PICOSECONDS"),
            (1700000000, ""),
            ("2023-11-14 22:13", "ISO8601"),
            ("not-a-date", "ISO8601"),
            (1700000000, "ISO8601"),
        ):
            with self.subTest(raw=raw, fmt=fmt):
                with self.assertRaises(ValueError):
                    self.reconciler.normalize_timestamp_to_ns(raw, fmt)

    def test_hostile_global_decimal_context_cannot_corrupt_results(self):
        # Regression: decimal.getcontext() is process-global and caller-mutable.
        # With prec=6, 1_700_000_000_123 ms normalized to 1700000000000000000.
        original = decimal.getcontext().prec
        decimal.getcontext().prec = 6
        try:
            ns, _, _ = self.reconciler.normalize_timestamp_to_ns(
                1_700_000_000_123, "MILLISECONDS")
        finally:
            decimal.getcontext().prec = original
        self.assertEqual(ns, BASE_NS + 123_000_000)

    def test_int64_range_is_enforced(self):
        # int64 nanoseconds saturate in 2262; a year-3000 value must not be
        # written into an int64 column silently.
        with self.assertRaises(ValueError):
            self.reconciler.normalize_timestamp_to_ns(32_503_680_000, "SECONDS")
        self.assertEqual(
            self.reconciler.normalize_timestamp_to_ns(INT64_MAX, "NANOSECONDS")[0], INT64_MAX)


class TestReconciliation(unittest.TestCase):

    def setUp(self):
        self.reconciler = CrossVendorTimestampReconciler(max_allowed_vendor_drift_ms=5.0)

    @staticmethod
    def _tick(tick_id, ns, vendor="V1", symbol="AAPL", event_key=None):
        return VendorTickRecord(
            tick_id, vendor, symbol, 180.5, 100, str(ns), "NANOSECONDS", event_key=event_key
        )

    def test_multi_vendor_normalization_and_stable_sort(self):
        ticks = [
            VendorTickRecord("T1", "BLOOMBERG", "AAPL", 180.50, 100,
                             1_700_000_000.100, "SECONDS"),
            VendorTickRecord("T2", "REFINITIV", "AAPL", 180.51, 200,
                             "2023-11-14T22:13:20.102Z", "ISO8601"),
            VendorTickRecord("T3", "DATABENTO", "AAPL", 180.55, 300,
                             1_700_000_000_120_000_000, "NANOSECONDS"),
        ]
        report = self.reconciler.reconcile_vendor_ticks(ticks)
        self.assertIsInstance(report, TimestampReconciliationReport)
        self.assertEqual(report.total_ticks_processed, 3)
        self.assertEqual(
            [t.normalized_ns_utc for t in report.normalized_ticks],
            [BASE_NS + 100_000_000, BASE_NS + 102_000_000, BASE_NS + 120_000_000],
        )
        self.assertEqual([t.tick_id for t in report.normalized_ticks], ["T1", "T2", "T3"])
        self.assertEqual(report.out_of_order_count, 0)
        self.assertEqual(
            report.precision_counts,
            {"SECONDS": 1, "MILLISECONDS": 1, "MICROSECONDS": 0, "NANOSECONDS": 1},
        )

    def test_out_of_order_counts_every_late_arrival(self):
        # Arrivals [5, 1, 2, 3]: three ticks arrive behind an already-seen later
        # timestamp. Adjacent-inversion-after-sorting reported only 1.
        report = self.reconciler.reconcile_vendor_ticks(
            [self._tick(f"T{i}", ns) for i, ns in enumerate([5, 1, 2, 3])]
        )
        self.assertEqual(report.out_of_order_count, 3)
        flagged = {t.tick_id for t in report.normalized_ticks if t.is_out_of_order}
        self.assertEqual(flagged, {"T1", "T2", "T3"})

    def test_monotonic_arrivals_are_never_flagged(self):
        report = self.reconciler.reconcile_vendor_ticks(
            [self._tick(f"T{i}", ns) for i, ns in enumerate([1, 2, 2, 7])]
        )
        self.assertEqual(report.out_of_order_count, 0)

    def test_equal_timestamps_break_ties_by_arrival_order(self):
        report = self.reconciler.reconcile_vendor_ticks(
            [self._tick("B", 10), self._tick("A", 10)]
        )
        self.assertEqual([t.tick_id for t in report.normalized_ticks], ["B", "A"])

    def test_duplicate_tick_ids_rejected(self):
        with self.assertRaises(ValueError):
            self.reconciler.reconcile_vendor_ticks(
                [self._tick("SAME", 3), self._tick("SAME", 1)]
            )

    def test_empty_input_is_a_clean_empty_report(self):
        report = self.reconciler.reconcile_vendor_ticks([])
        self.assertEqual(report.total_ticks_processed, 0)
        self.assertEqual(report.normalized_ticks, [])
        self.assertEqual(report.out_of_order_count, 0)
        self.assertEqual(report.vendor_drift_warnings, [])
        self.assertEqual(report.skew_pairs_evaluated, 0)

    def test_malformed_records_rejected(self):
        with self.assertRaises(TypeError):
            self.reconciler.reconcile_vendor_ticks([{"tick_id": "T1"}])
        with self.assertRaises(TypeError):
            self.reconciler.reconcile_vendor_ticks("not-a-list")
        bad = VendorTickRecord("T1", "", "AAPL", 1.0, 1.0, "1", "NANOSECONDS")
        with self.assertRaises(ValueError):
            self.reconciler.reconcile_vendor_ticks([bad])

    # --- cross-vendor skew ------------------------------------------------

    def test_skew_requires_matched_events(self):
        # Two consecutive ticks 20ms apart from different vendors describe two
        # DIFFERENT events; that interval is not clock drift and must not warn.
        report = self.reconciler.reconcile_vendor_ticks([
            self._tick("T1", BASE_NS, vendor="BLOOMBERG"),
            self._tick("T2", BASE_NS + 20_000_000, vendor="DATABENTO"),
        ])
        self.assertEqual(report.vendor_drift_warnings, [])
        self.assertEqual(report.skew_pairs_evaluated, 0)

    def test_matched_event_skew_is_signed_and_thresholded(self):
        report = self.reconciler.reconcile_vendor_ticks([
            self._tick("T1", BASE_NS, vendor="BLOOMBERG", event_key="SEQ_42"),
            self._tick("T2", BASE_NS + 20_000_000, vendor="DATABENTO", event_key="SEQ_42"),
            self._tick("T3", BASE_NS + 1_000_000, vendor="REFINITIV", event_key="SEQ_42"),
        ])
        self.assertEqual(report.skew_pairs_evaluated, 3)  # 3 vendors -> 3 pairs
        skews = {
            (o.vendor_a, o.vendor_b): o.skew_ns for o in report.vendor_skew_observations
        }
        # Signed, vendors ordered lexicographically: BLOOMBERG < DATABENTO < REFINITIV.
        self.assertEqual(skews[("BLOOMBERG", "DATABENTO")], 20_000_000)
        self.assertEqual(skews[("BLOOMBERG", "REFINITIV")], 1_000_000)
        self.assertEqual(skews[("DATABENTO", "REFINITIV")], -19_000_000)
        # Only the pairs exceeding 5ms warn.
        self.assertEqual(len(report.vendor_drift_warnings), 2)

    def test_skew_exactly_at_threshold_does_not_warn(self):
        report = self.reconciler.reconcile_vendor_ticks([
            self._tick("T1", BASE_NS, vendor="A", event_key="SEQ_1"),
            self._tick("T2", BASE_NS + 5_000_000, vendor="B", event_key="SEQ_1"),
        ])
        self.assertEqual(report.vendor_skew_observations[0].skew_ns, 5_000_000)
        self.assertFalse(report.vendor_skew_observations[0].exceeds_threshold)
        self.assertEqual(report.vendor_drift_warnings, [])

    def test_same_vendor_is_never_compared_against_itself(self):
        report = self.reconciler.reconcile_vendor_ticks([
            self._tick("T1", BASE_NS, vendor="A", event_key="SEQ_1"),
            self._tick("T2", BASE_NS + 60_000_000, vendor="A", event_key="SEQ_1"),
        ])
        self.assertEqual(report.skew_pairs_evaluated, 0)
        self.assertEqual(report.vendor_drift_warnings, [])

    def test_same_event_key_across_symbols_is_not_matched(self):
        report = self.reconciler.reconcile_vendor_ticks([
            self._tick("T1", BASE_NS, vendor="A", symbol="AAPL", event_key="SEQ_1"),
            self._tick("T2", BASE_NS + 60_000_000, vendor="B", symbol="MSFT", event_key="SEQ_1"),
        ])
        self.assertEqual(report.skew_pairs_evaluated, 0)

    # --- precision SLA ----------------------------------------------------

    def test_precision_shortfall_flagged_against_required_tier(self):
        reconciler = CrossVendorTimestampReconciler(required_precision_tier="MICROSECONDS")
        report = reconciler.reconcile_vendor_ticks([
            VendorTickRecord("T1", "V1", "AAPL", 1.0, 1.0,
                             "2023-11-14T22:13:20.123Z", "ISO8601"),
            VendorTickRecord("T2", "V2", "AAPL", 1.0, 1.0,
                             "1700000000123456789", "NANOSECONDS"),
        ])
        self.assertEqual(report.precision_violation_count, 1)
        self.assertEqual(report.required_precision_tier, "MICROSECONDS")
        by_id = {t.tick_id: t for t in report.normalized_ticks}
        self.assertFalse(by_id["T1"].meets_precision_requirement)
        self.assertTrue(by_id["T2"].meets_precision_requirement)

    def test_no_requirement_means_no_violations(self):
        report = self.reconciler.reconcile_vendor_ticks([self._tick("T1", BASE_NS)])
        self.assertEqual(report.precision_violation_count, 0)
        self.assertIsNone(report.required_precision_tier)

    def test_invalid_reconciler_configuration_rejected(self):
        with self.assertRaises(ValueError):
            CrossVendorTimestampReconciler(required_precision_tier="PICOSECONDS")
        with self.assertRaises(ValueError):
            CrossVendorTimestampReconciler(max_allowed_vendor_drift_ms=-1.0)
        with self.assertRaises(TypeError):
            CrossVendorTimestampReconciler(max_allowed_vendor_drift_ms="5")


if __name__ == '__main__':
    unittest.main()

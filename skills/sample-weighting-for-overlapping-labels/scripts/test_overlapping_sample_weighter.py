"""
Unit tests for the sample-weighting-for-overlapping-labels skill.

Expected values are derived by hand in the docstrings below from the definitions
in Lopez de Prado, *Advances in Financial Machine Learning* (Snippets 4.1, 4.2,
4.10, 4.11), never by re-running the module's own expressions.

Tests annotated "Regression" fail against a naive implementation; each one
names the old behavior it catches.
"""
import logging
import math
import unittest

from overlapping_sample_weighter import (
    LabelSpan,
    SampleWeightingError,
    SampleWeightingForOverlappingLabelsEngine,
    SampleWeightingReport,
    WeightingMethod,
)


class TestConcurrencyAndUniqueness(unittest.TestCase):
    def setUp(self):
        self.engine = SampleWeightingForOverlappingLabelsEngine()

    def test_non_overlapping_spans_have_full_uniqueness(self):
        """[1,5] and [6,10] share no bar, so c_t == 1 everywhere and u_i == 1."""
        spans = [LabelSpan("S1", 1, 5, 0.02), LabelSpan("S2", 6, 10, -0.01)]
        report = self.engine.compute_sample_weights(spans, WeightingMethod.UNIQUENESS_ONLY)
        self.assertEqual(report.average_dataset_uniqueness, 1.0)
        self.assertEqual(report.sample_results[0].average_uniqueness, 1.0)
        self.assertEqual(report.sample_results[1].average_uniqueness, 1.0)
        self.assertAlmostEqual(report.sample_results[0].normalized_weight, 1.0, places=12)
        self.assertAlmostEqual(report.sample_results[1].normalized_weight, 1.0, places=12)

    def test_fully_overlapping_spans_halve_uniqueness(self):
        """Two identical spans give c_t == 2 on every bar, so u_i == 1/2."""
        spans = [LabelSpan("S1", 1, 10, 0.05), LabelSpan("S2", 1, 10, 0.03)]
        report = self.engine.compute_sample_weights(spans, WeightingMethod.UNIQUENESS_ONLY)
        self.assertEqual(report.average_dataset_uniqueness, 0.5)
        self.assertEqual(report.sample_results[0].average_uniqueness, 0.5)
        self.assertEqual(report.sample_results[1].average_uniqueness, 0.5)

    def test_partial_overlap_matches_hand_computed_uniqueness(self):
        """
        Spans [0,2], [1,3], [2,4].

        c = {0:1, 1:2, 2:3, 3:2, 4:1}
        u_1 = (1/1 + 1/2 + 1/3)/3 = (11/6)/3 = 11/18
        u_2 = (1/2 + 1/3 + 1/2)/3 = (4/3)/3  = 4/9
        u_3 = (1/3 + 1/2 + 1/1)/3 = (11/6)/3 = 11/18
        sum(u) = 5/3, so normalized w_i = u_i * 3 / (5/3) = u_i * 9/5
              -> [1.1, 0.8, 1.1], which sums to 3 == N.
        """
        spans = [LabelSpan("A", 0, 2), LabelSpan("B", 1, 3), LabelSpan("C", 2, 4)]
        concurrency = self.engine.compute_concurrency(spans)
        self.assertEqual(concurrency, {0: 1, 1: 2, 2: 3, 3: 2, 4: 1})

        report = self.engine.compute_sample_weights(spans, WeightingMethod.UNIQUENESS_ONLY)
        self.assertAlmostEqual(report.sample_results[0].average_uniqueness, 11 / 18, places=12)
        self.assertAlmostEqual(report.sample_results[1].average_uniqueness, 4 / 9, places=12)
        self.assertAlmostEqual(report.sample_results[2].average_uniqueness, 11 / 18, places=12)
        self.assertAlmostEqual(report.sample_results[0].normalized_weight, 1.1, places=12)
        self.assertAlmostEqual(report.sample_results[1].normalized_weight, 0.8, places=12)
        self.assertAlmostEqual(report.sample_results[2].normalized_weight, 1.1, places=12)

    def test_endpoints_are_inclusive_on_both_sides(self):
        """[0,5] and [5,9] share exactly bar 5, so neither is perfectly unique."""
        spans = [LabelSpan("A", 0, 5), LabelSpan("B", 5, 9)]
        concurrency = self.engine.compute_concurrency(spans)
        self.assertEqual(concurrency[5], 2)
        u = self.engine.compute_sample_uniqueness(spans, concurrency)
        # u_A = (5 bars at c=1 + 1 bar at c=2) / 6 = 5.5/6
        self.assertAlmostEqual(u[0], 5.5 / 6, places=12)
        self.assertAlmostEqual(u[1], 4.5 / 5, places=12)

    def test_uniqueness_keeps_full_precision(self):
        """
        Regression: uniqueness used to be rounded to 4 decimals before being used
        as a weight, so three identical spans reported 0.3333 instead of 1/3.
        """
        spans = [LabelSpan("A", 0, 5), LabelSpan("B", 0, 5), LabelSpan("C", 0, 5)]
        report = self.engine.compute_sample_weights(spans, WeightingMethod.UNIQUENESS_ONLY)
        u = report.sample_results[0].average_uniqueness
        self.assertAlmostEqual(u, 1 / 3, places=12)
        self.assertNotEqual(u, round(u, 4))

    def test_normalized_weights_sum_to_n_exactly(self):
        """
        Regression: weights used to be rounded to 4 decimals on output, so the
        sum(w) == N invariant stated in references/standards.md held only
        approximately. These six two-bar spans summed to 5.9998 under the old
        implementation.
        """
        spans = [LabelSpan(f"S{i}", i, i + 1, 0.01 * (i + 1)) for i in range(6)]
        for method in WeightingMethod:
            with self.subTest(method=method):
                report = self.engine.compute_sample_weights(spans, method)
                total = sum(r.normalized_weight for r in report.sample_results)
                self.assertAlmostEqual(total, 6.0, places=12)

    def test_uniqueness_is_bounded_by_one(self):
        spans = [LabelSpan(f"S{i}", i, i + 4) for i in range(25)]
        report = self.engine.compute_sample_weights(spans)
        for result in report.sample_results:
            self.assertGreater(result.average_uniqueness, 0.0)
            self.assertLessEqual(result.average_uniqueness, 1.0)

    def test_input_order_is_preserved_in_report(self):
        spans = [LabelSpan("late", 50, 55), LabelSpan("early", 0, 5)]
        report = self.engine.compute_sample_weights(spans)
        self.assertEqual([r.sample_id for r in report.sample_results], ["late", "early"])

    def test_mismatched_concurrency_map_is_rejected(self):
        """
        Regression: a bar missing from the concurrency map used to default to
        c_t = 1, silently reporting an overlapping label as perfectly unique.
        """
        spans = [LabelSpan("A", 0, 4)]
        with self.assertRaises(SampleWeightingError):
            self.engine.compute_sample_uniqueness(spans, {0: 1, 1: 1})


class TestReturnAttribution(unittest.TestCase):
    def setUp(self):
        self.engine = SampleWeightingForOverlappingLabelsEngine()

    def test_exact_attribution_matches_snippet_4_10(self):
        """
        Spans [0,2] and [1,3]; c = {0:1, 1:2, 2:2, 3:1}.
        r = {0: 0.01, 1: 0.02, 2: -0.01, 3: 0.03}

        w_1 = |0.01/1 + 0.02/2 - 0.01/2| = |0.010 + 0.010 - 0.005| = 0.015
        w_2 = |0.02/2 - 0.01/2 + 0.03/1| = |0.010 - 0.005 + 0.030| = 0.035
        sum = 0.05, so normalized = [0.015, 0.035] * 2 / 0.05 = [0.6, 1.4].
        """
        spans = [LabelSpan("S1", 0, 2, 0.02), LabelSpan("S2", 1, 3, 0.04)]
        bar_log_returns = {0: 0.01, 1: 0.02, 2: -0.01, 3: 0.03}
        report = self.engine.compute_sample_weights(
            spans, WeightingMethod.RETURN_ATTRIBUTED, bar_log_returns
        )
        self.assertTrue(report.return_attribution_is_exact)
        self.assertAlmostEqual(report.sample_results[0].raw_weight, 0.015, places=12)
        self.assertAlmostEqual(report.sample_results[1].raw_weight, 0.035, places=12)
        self.assertAlmostEqual(report.sample_results[0].normalized_weight, 0.6, places=12)
        self.assertAlmostEqual(report.sample_results[1].normalized_weight, 1.4, places=12)

    def test_exact_and_approximate_attribution_differ(self):
        """
        The u_i * |r_i| fallback is only a uniform-return approximation. With the
        same spans as above it gives u = 2/3 for both, hence raw weights
        (2/3)*0.02 = 0.013333 and (2/3)*0.04 = 0.026667 -- different numbers, and
        a different weight ratio (2.0 vs 0.035/0.015 = 2.333).
        """
        spans = [LabelSpan("S1", 0, 2, 0.02), LabelSpan("S2", 1, 3, 0.04)]
        approx = self.engine.compute_sample_weights(spans, WeightingMethod.RETURN_ATTRIBUTED)
        exact = self.engine.compute_sample_weights(
            spans, WeightingMethod.RETURN_ATTRIBUTED, {0: 0.01, 1: 0.02, 2: -0.01, 3: 0.03}
        )
        self.assertFalse(approx.return_attribution_is_exact)
        self.assertAlmostEqual(approx.sample_results[0].raw_weight, (2 / 3) * 0.02, places=12)
        self.assertNotAlmostEqual(
            approx.sample_results[0].normalized_weight,
            exact.sample_results[0].normalized_weight,
            places=6,
        )

    def test_approximation_is_flagged_in_notes(self):
        spans = [LabelSpan("S1", 0, 2, 0.02), LabelSpan("S2", 1, 3, 0.04)]
        report = self.engine.compute_sample_weights(spans, WeightingMethod.RETURN_ATTRIBUTED)
        self.assertIn("APPROXIMATION", report.audit_notes)

    def test_larger_absolute_return_earns_a_larger_weight(self):
        """Equal uniqueness, so the 10% label must outweigh the 2% label."""
        spans = [LabelSpan("S1", 1, 5, 0.10), LabelSpan("S2", 1, 5, 0.02)]
        report = self.engine.compute_sample_weights(spans, WeightingMethod.RETURN_ATTRIBUTED)
        w1 = report.sample_results[0].normalized_weight
        w2 = report.sample_results[1].normalized_weight
        self.assertGreater(w1, w2)
        self.assertAlmostEqual(w1 / w2, 5.0, places=12)
        self.assertAlmostEqual(w1 + w2, 2.0, places=12)

    def test_negative_returns_use_absolute_magnitude(self):
        """Snippet 4.10 takes the absolute value: direction must not cancel size."""
        spans = [LabelSpan("S1", 0, 1, -0.10), LabelSpan("S2", 2, 3, 0.10)]
        report = self.engine.compute_sample_weights(spans, WeightingMethod.RETURN_ATTRIBUTED)
        self.assertAlmostEqual(
            report.sample_results[0].normalized_weight,
            report.sample_results[1].normalized_weight,
            places=12,
        )

    def test_missing_bar_return_is_rejected(self):
        spans = [LabelSpan("S1", 0, 3, 0.02)]
        with self.assertRaises(SampleWeightingError):
            self.engine.compute_sample_weights(
                spans, WeightingMethod.RETURN_ATTRIBUTED, {0: 0.01, 1: 0.01}
            )

    def test_non_finite_bar_return_is_rejected(self):
        spans = [LabelSpan("S1", 0, 1, 0.02)]
        with self.assertRaises(SampleWeightingError):
            self.engine.compute_sample_weights(
                spans, WeightingMethod.RETURN_ATTRIBUTED, {0: 0.01, 1: float("nan")}
            )

    def test_all_zero_returns_fall_back_to_uniform_with_a_warning(self):
        spans = [LabelSpan("S1", 0, 2, 0.0), LabelSpan("S2", 3, 5, 0.0)]
        with self.assertLogs("overlapping_sample_weighter", level=logging.WARNING):
            report = self.engine.compute_sample_weights(spans, WeightingMethod.RETURN_ATTRIBUTED)
        self.assertTrue(report.degenerate_uniform_fallback)
        self.assertEqual([r.normalized_weight for r in report.sample_results], [1.0, 1.0])
        self.assertIn("DEGENERATE", report.audit_notes)


class TestTimeDecay(unittest.TestCase):
    def setUp(self):
        self.engine = SampleWeightingForOverlappingLabelsEngine()  # last weight 0.5

    def test_decay_matches_hand_computed_snippet_4_11(self):
        """
        Three non-overlapping single-bar spans, so u = [1, 1, 1] and the
        cumulative uniqueness is [1, 2, 3] with total T = 3.

        clfLastW = 0.5 -> slope = (1 - 0.5)/3 = 1/6, const = 1 - (1/6)*3 = 0.5
        factors = 0.5 + [1, 2, 3]/6 = [2/3, 5/6, 1]
        raw = u * factor = the same, sum = 2.5
        normalized = raw * 3 / 2.5 = [0.8, 1.0, 1.2].
        """
        spans = [LabelSpan("a", 0, 0), LabelSpan("b", 1, 1), LabelSpan("c", 2, 2)]
        factors = self.engine.compute_time_decay_factors(spans, [1.0, 1.0, 1.0])
        self.assertAlmostEqual(factors[0], 2 / 3, places=12)
        self.assertAlmostEqual(factors[1], 5 / 6, places=12)
        self.assertAlmostEqual(factors[2], 1.0, places=12)

        report = self.engine.compute_sample_weights(spans, WeightingMethod.TIME_DECAY)
        got = [r.normalized_weight for r in report.sample_results]
        for value, expected in zip(got, [0.8, 1.0, 1.2]):
            self.assertAlmostEqual(value, expected, places=12)

    def test_decay_follows_chronology_not_argument_order(self):
        """
        Regression: decay used to be exp(-c * (n-1-i)/n) over the caller's *list
        position*, so passing the same spans newest-first reversed the decay and
        handed the oldest label the largest weight.
        """
        chronological = [LabelSpan("a", 0, 0), LabelSpan("b", 1, 1), LabelSpan("c", 2, 2)]
        shuffled = [chronological[1], chronological[2], chronological[0]]

        by_id_chrono = {
            r.sample_id: r.normalized_weight
            for r in self.engine.compute_sample_weights(
                chronological, WeightingMethod.TIME_DECAY
            ).sample_results
        }
        by_id_shuffled = {
            r.sample_id: r.normalized_weight
            for r in self.engine.compute_sample_weights(
                shuffled, WeightingMethod.TIME_DECAY
            ).sample_results
        }
        self.assertEqual(set(by_id_chrono), set(by_id_shuffled))
        for sample_id, weight in by_id_chrono.items():
            self.assertAlmostEqual(weight, by_id_shuffled[sample_id], places=12)
        self.assertGreater(by_id_shuffled["c"], by_id_shuffled["a"])

    def test_newest_span_always_has_decay_factor_one(self):
        spans = [LabelSpan("a", 0, 3), LabelSpan("b", 2, 6), LabelSpan("c", 5, 9)]
        for last_weight in (1.0, 0.5, 0.0, -0.5):
            with self.subTest(last_weight=last_weight):
                engine = SampleWeightingForOverlappingLabelsEngine(last_weight)
                uniqueness = engine.compute_sample_uniqueness(
                    spans, engine.compute_concurrency(spans)
                )
                factors = engine.compute_time_decay_factors(spans, uniqueness)
                self.assertAlmostEqual(factors[-1], 1.0, places=12)

    def test_last_weight_one_is_no_decay(self):
        """clfLastW = 1 must reproduce the plain uniqueness weights exactly."""
        spans = [LabelSpan("a", 0, 4), LabelSpan("b", 3, 7), LabelSpan("c", 6, 10)]
        engine = SampleWeightingForOverlappingLabelsEngine(1.0)
        decayed = engine.compute_sample_weights(spans, WeightingMethod.TIME_DECAY)
        plain = engine.compute_sample_weights(spans, WeightingMethod.UNIQUENESS_ONLY)
        for a, b in zip(decayed.sample_results, plain.sample_results):
            self.assertAlmostEqual(a.normalized_weight, b.normalized_weight, places=12)

    def test_negative_last_weight_zeroes_the_oldest_portion(self):
        """
        Four non-overlapping single-bar spans, u = [1,1,1,1], cumulative
        [1,2,3,4], T = 4. clfLastW = -0.5 ->
            slope = 1 / ((-0.5 + 1) * 4) = 0.5, const = 1 - 0.5*4 = -1
            factors = max(-1 + 0.5*[1,2,3,4], 0) = [0, 0, 0.5, 1]
        raw = [0, 0, 0.5, 1], sum = 1.5, normalized = raw * 4/1.5.
        """
        engine = SampleWeightingForOverlappingLabelsEngine(-0.5)
        spans = [LabelSpan(f"s{i}", i, i) for i in range(4)]
        factors = engine.compute_time_decay_factors(spans, [1.0, 1.0, 1.0, 1.0])
        for value, expected in zip(factors, [0.0, 0.0, 0.5, 1.0]):
            self.assertAlmostEqual(value, expected, places=12)

        report = engine.compute_sample_weights(spans, WeightingMethod.TIME_DECAY)
        got = [r.normalized_weight for r in report.sample_results]
        for value, expected in zip(got, [0.0, 0.0, 0.5 * 4 / 1.5, 1.0 * 4 / 1.5]):
            self.assertAlmostEqual(value, expected, places=12)

    def test_invalid_last_weight_is_rejected(self):
        for bad in (-1.0, -2.0, 1.5, float("nan"), float("inf"), "0.5", None):
            with self.subTest(bad=bad):
                with self.assertRaises(SampleWeightingError):
                    SampleWeightingForOverlappingLabelsEngine(bad)

    def test_uniqueness_length_mismatch_is_rejected(self):
        spans = [LabelSpan("a", 0, 1), LabelSpan("b", 2, 3)]
        with self.assertRaises(SampleWeightingError):
            self.engine.compute_time_decay_factors(spans, [1.0])


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = SampleWeightingForOverlappingLabelsEngine()

    def test_empty_spans_raises_error(self):
        with self.assertRaises(SampleWeightingError):
            self.engine.compute_sample_weights([])

    def test_error_type_stays_a_valueerror(self):
        """Callers written against an earlier ValueError contract still work."""
        self.assertTrue(issubclass(SampleWeightingError, ValueError))
        with self.assertRaises(ValueError):
            self.engine.compute_sample_weights([])

    def test_inverted_span_is_rejected(self):
        """
        Regression: a span with end < start covered no bars, so it contributed
        nothing to concurrency and was then scored u = 1.0 -- the malformed
        sample received the *largest* weight in the dataset.
        """
        spans = [LabelSpan("good", 0, 5), LabelSpan("inverted", 9, 3)]
        with self.assertRaises(SampleWeightingError):
            self.engine.compute_sample_weights(spans)

    def test_non_finite_realized_return_is_rejected(self):
        """
        Regression: one NaN return made sum(raw) NaN; `NaN <= 0` is False, so
        normalization ran and every returned weight was NaN.
        """
        for bad in (float("nan"), float("inf"), -float("inf")):
            with self.subTest(bad=bad):
                spans = [LabelSpan("S1", 0, 2, bad), LabelSpan("S2", 3, 5, 0.01)]
                with self.assertRaises(SampleWeightingError):
                    self.engine.compute_sample_weights(spans, WeightingMethod.RETURN_ATTRIBUTED)

    def test_duplicate_sample_ids_are_rejected(self):
        spans = [LabelSpan("dup", 0, 2), LabelSpan("dup", 3, 5)]
        with self.assertRaises(SampleWeightingError):
            self.engine.compute_sample_weights(spans)

    def test_non_integer_bar_index_is_rejected(self):
        for bad_span in (
            LabelSpan("A", 0.5, 5),
            LabelSpan("A", 0, "5"),
            LabelSpan("A", True, 5),
        ):
            with self.subTest(span=bad_span):
                with self.assertRaises(SampleWeightingError):
                    self.engine.compute_sample_weights([bad_span])

    def test_blank_sample_id_is_rejected(self):
        with self.assertRaises(SampleWeightingError):
            self.engine.compute_sample_weights([LabelSpan("   ", 0, 2)])

    def test_unknown_method_is_rejected(self):
        """
        Regression: an unrecognised method fell through to `else: w_i = u_i`.
        Any enum member carrying a `.value` -- a caller's own WeightingMethod
        look-alike, say -- therefore received uniqueness-only weights with no
        error at all, while a plain mis-cased string got as far as the audit-note
        f-string and died there on `AttributeError: 'str' object has no attribute
        'value'`. Neither told the caller the method was unknown.
        """
        spans = [LabelSpan("A", 0, 2, 0.01), LabelSpan("B", 1, 3, 0.05)]
        for bad in ("time_decay", "RETURN ATTRIBUTED", "", None, 3):
            with self.subTest(bad=bad):
                with self.assertRaises(SampleWeightingError):
                    self.engine.compute_sample_weights(spans, bad)

    def test_bar_returns_with_a_non_attribution_method_warns(self):
        """Silently ignoring them would let a caller believe attribution ran."""
        spans = [LabelSpan("A", 0, 2, 0.01), LabelSpan("B", 3, 5, 0.05)]
        with self.assertLogs("overlapping_sample_weighter", level=logging.WARNING):
            report = self.engine.compute_sample_weights(
                spans, WeightingMethod.UNIQUENESS_ONLY, {i: 0.01 for i in range(6)}
            )
        self.assertIsNone(report.return_attribution_is_exact)

    def test_method_accepts_the_plain_string_value(self):
        spans = [LabelSpan("A", 0, 2, 0.01), LabelSpan("B", 1, 3, 0.05)]
        report = self.engine.compute_sample_weights(spans, "TIME_DECAY")
        self.assertEqual(report.weighting_method, WeightingMethod.TIME_DECAY)

    def test_negative_bar_indices_are_accepted(self):
        """Bar indices are ordinals, not counts; a caller may zero them anywhere."""
        spans = [LabelSpan("A", -5, -3), LabelSpan("B", -2, 0)]
        report = self.engine.compute_sample_weights(spans)
        self.assertEqual(report.average_dataset_uniqueness, 1.0)


class TestReport(unittest.TestCase):
    def setUp(self):
        self.engine = SampleWeightingForOverlappingLabelsEngine()

    def test_report_shape_and_notes(self):
        spans = [LabelSpan("A", 0, 2, 0.01), LabelSpan("B", 1, 3, 0.05)]
        report = self.engine.compute_sample_weights(spans, WeightingMethod.UNIQUENESS_ONLY)
        self.assertIsInstance(report, SampleWeightingReport)
        self.assertEqual(report.total_samples, 2)
        self.assertEqual(report.weighting_method, WeightingMethod.UNIQUENESS_ONLY)
        self.assertIsNone(report.return_attribution_is_exact)
        self.assertFalse(report.degenerate_uniform_fallback)
        self.assertIn("UNIQUENESS_ONLY", report.audit_notes)
        self.assertIn("max_samples", report.audit_notes)

    def test_average_dataset_uniqueness_is_the_mean_of_sample_uniqueness(self):
        spans = [LabelSpan("A", 0, 2), LabelSpan("B", 1, 3), LabelSpan("C", 2, 4)]
        report = self.engine.compute_sample_weights(spans)
        expected = sum(r.average_uniqueness for r in report.sample_results) / 3
        self.assertAlmostEqual(report.average_dataset_uniqueness, expected, places=12)
        self.assertAlmostEqual(expected, (11 / 18 + 4 / 9 + 11 / 18) / 3, places=12)

    def test_weights_are_finite_for_every_method(self):
        spans = [LabelSpan(f"S{i}", i, i + 3, 0.001 * (i + 1)) for i in range(8)]
        for method in WeightingMethod:
            with self.subTest(method=method):
                report = self.engine.compute_sample_weights(spans, method)
                for result in report.sample_results:
                    self.assertTrue(math.isfinite(result.normalized_weight))
                    self.assertGreaterEqual(result.normalized_weight, 0.0)


if __name__ == "__main__":
    unittest.main()

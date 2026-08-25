"""Unit tests for feature-engineering-cost-benefit-tracking."""
import unittest

from feature_cost_benefit import (
    FeatureCostBenefitError,
    FeatureCostBenefitTracker,
    Recommendation,
)


class TestSingleFeatureVerdicts(unittest.TestCase):
    def setUp(self):
        self.tracker = FeatureCostBenefitTracker(
            min_importance_threshold=0.01, max_acceptable_cost_usd=50.0
        )

    def test_prunes_expensive_low_value_feature(self):
        # 0.2% importance, $500/mo -> below threshold AND above cost budget.
        rec = self.tracker.evaluate_feature(
            "satellite_imagery_v1", importance_score=0.002, monthly_cost_usd=500.0
        )
        self.assertEqual(rec.recommendation, "PRUNE")

    def test_keeps_high_value_affordable_feature(self):
        rec = self.tracker.evaluate_feature(
            "rsi_14d", importance_score=0.05, monthly_cost_usd=10.0
        )
        self.assertEqual(rec.recommendation, "KEEP")

    def test_low_importance_low_cost_is_review_not_prune(self):
        rec = self.tracker.evaluate_feature(
            "cheap_noise_feature", importance_score=0.001, monthly_cost_usd=5.0
        )
        self.assertEqual(rec.recommendation, "REVIEW")

    def test_high_importance_very_expensive_is_review(self):
        # Above the importance threshold but past high_cost_review_usd ($500 default).
        rec = self.tracker.evaluate_feature(
            "premium_alt_data", importance_score=0.08, monthly_cost_usd=750.0
        )
        self.assertEqual(rec.recommendation, "REVIEW")

    def test_negative_importance_is_accepted_and_pruned(self):
        # Permuting a noise feature can improve the score; sklearn reports this as a
        # negative importance. It must be treated as a prune signal, not an error.
        rec = self.tracker.evaluate_feature(
            "leaky_noise", importance_score=-0.004, monthly_cost_usd=200.0
        )
        self.assertEqual(rec.recommendation, "PRUNE")

    def test_threshold_boundary_is_inclusive_keep(self):
        # importance == threshold is NOT "below threshold": rule is `<`, not `<=`.
        at_threshold = self.tracker.evaluate_feature("edge_keep", 0.01, 500.0)
        just_below = self.tracker.evaluate_feature("edge_prune", 0.009999, 500.0)
        self.assertEqual(at_threshold.recommendation, "KEEP")
        self.assertEqual(just_below.recommendation, "PRUNE")

    def test_cost_boundary_is_exclusive(self):
        # cost == max_acceptable_cost_usd is within budget -> REVIEW, not PRUNE.
        at_budget = self.tracker.evaluate_feature("at_budget", 0.001, 50.0)
        over_budget = self.tracker.evaluate_feature("over_budget", 0.001, 50.01)
        self.assertEqual(at_budget.recommendation, "REVIEW")
        self.assertEqual(over_budget.recommendation, "PRUNE")

    def test_recommendation_enum_compares_equal_to_plain_string(self):
        rec = self.tracker.evaluate_feature("rsi_14d", 0.05, 10.0)
        self.assertEqual(rec.recommendation, Recommendation.KEEP.value)
        self.assertEqual(rec.recommendation, "KEEP")


class TestRoiRatio(unittest.TestCase):
    def setUp(self):
        self.tracker = FeatureCostBenefitTracker()

    def test_roi_matches_independently_derived_value(self):
        # 0.05 * 100 / max(1.0, 250.0) = 5.0 / 250.0 = 0.02
        rec = self.tracker.evaluate_feature("f", 0.05, 250.0)
        self.assertAlmostEqual(rec.roi_ratio, 0.02, places=6)

    def test_roi_denominator_is_floored_for_near_free_features(self):
        # Cost below the $1 floor must not produce an unbounded ROI:
        # 0.05 * 100 / max(1.0, 0.01) = 5.0
        free = self.tracker.evaluate_feature("free", 0.05, 0.0)
        cheap = self.tracker.evaluate_feature("cheap", 0.05, 0.01)
        self.assertAlmostEqual(free.roi_ratio, 5.0, places=6)
        self.assertAlmostEqual(cheap.roi_ratio, 5.0, places=6)

    def test_roi_is_monotone_decreasing_in_cost_above_the_floor(self):
        cheap = self.tracker.evaluate_feature("a", 0.05, 10.0)
        dear = self.tracker.evaluate_feature("b", 0.05, 100.0)
        self.assertGreater(cheap.roi_ratio, dear.roi_ratio)


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.tracker = FeatureCostBenefitTracker()

    def test_nan_importance_is_rejected_not_silently_kept(self):
        # Regression: NaN < threshold is False, so an unvalidated NaN fell through
        # every prune branch and was reported as KEEP.
        with self.assertRaises(FeatureCostBenefitError):
            self.tracker.evaluate_feature("nan_feature", float("nan"), 1000.0)

    def test_infinite_cost_is_rejected(self):
        with self.assertRaises(FeatureCostBenefitError):
            self.tracker.evaluate_feature("inf_cost", 0.05, float("inf"))

    def test_negative_cost_is_rejected(self):
        with self.assertRaises(FeatureCostBenefitError):
            self.tracker.evaluate_feature("negative_cost", 0.05, -100.0)

    def test_negative_latency_is_rejected(self):
        with self.assertRaises(FeatureCostBenefitError):
            self.tracker.evaluate_feature("bad_latency", 0.05, 10.0, compute_latency_ms=-1.0)

    def test_non_numeric_importance_is_rejected(self):
        with self.assertRaises(FeatureCostBenefitError):
            self.tracker.evaluate_feature("stringy", "0.05", 10.0)

    def test_empty_feature_name_is_rejected(self):
        with self.assertRaises(FeatureCostBenefitError):
            self.tracker.evaluate_feature("   ", 0.05, 10.0)

    def test_negative_importance_std_is_rejected(self):
        with self.assertRaises(FeatureCostBenefitError):
            self.tracker.evaluate_feature("f", 0.05, 10.0, importance_std=-0.01)

    def test_contradictory_cost_thresholds_are_rejected(self):
        with self.assertRaises(FeatureCostBenefitError):
            FeatureCostBenefitTracker(
                max_acceptable_cost_usd=500.0, high_cost_review_usd=100.0
            )

    def test_zero_roi_cost_floor_is_rejected(self):
        with self.assertRaises(FeatureCostBenefitError):
            FeatureCostBenefitTracker(roi_cost_floor_usd=0.0)

    def test_non_positive_latency_budget_is_rejected(self):
        with self.assertRaises(FeatureCostBenefitError):
            FeatureCostBenefitTracker(max_latency_ms=0.0)


class TestLatencyBudget(unittest.TestCase):
    def test_latency_alone_prunes_a_cheap_low_value_feature(self):
        # Regression: latency was collected and then ignored entirely, so a
        # $0/mo feature costing 40 ms of the inference budget was REVIEWed forever.
        tracker = FeatureCostBenefitTracker(max_latency_ms=5.0)
        rec = tracker.evaluate_feature(
            "deep_book_convolution", 0.002, monthly_cost_usd=0.0, compute_latency_ms=40.0
        )
        self.assertEqual(rec.recommendation, "PRUNE")
        self.assertIn("latency", rec.rationale.lower())

    def test_valuable_but_slow_feature_is_reviewed_not_pruned(self):
        tracker = FeatureCostBenefitTracker(max_latency_ms=5.0)
        rec = tracker.evaluate_feature("slow_but_good", 0.09, 10.0, compute_latency_ms=40.0)
        self.assertEqual(rec.recommendation, "REVIEW")

    def test_latency_budget_disabled_by_default(self):
        tracker = FeatureCostBenefitTracker()
        rec = tracker.evaluate_feature("slow_cheap", 0.002, 1.0, compute_latency_ms=5000.0)
        self.assertEqual(rec.recommendation, "REVIEW")

    def test_latency_at_budget_is_within_budget(self):
        tracker = FeatureCostBenefitTracker(max_latency_ms=5.0)
        rec = tracker.evaluate_feature("exactly_at_budget", 0.002, 1.0, compute_latency_ms=5.0)
        self.assertEqual(rec.recommendation, "REVIEW")


class TestUncertaintyGate(unittest.TestCase):
    def setUp(self):
        self.tracker = FeatureCostBenefitTracker(prune_confidence_sigma=1.0)

    def test_noisy_estimate_near_threshold_is_not_pruned(self):
        # importance 0.008 + 1 sigma * 0.005 = 0.013 >= 0.01 -> not confidently below.
        rec = self.tracker.evaluate_feature("noisy", 0.008, 400.0, importance_std=0.005)
        self.assertEqual(rec.recommendation, "REVIEW")
        self.assertIn("sigma", rec.rationale)

    def test_confidently_below_threshold_still_prunes(self):
        # 0.001 + 1 sigma * 0.0005 = 0.0015 < 0.01 -> confidently below.
        rec = self.tracker.evaluate_feature("stable_dud", 0.001, 400.0, importance_std=0.0005)
        self.assertEqual(rec.recommendation, "PRUNE")

    def test_gate_is_inert_when_std_is_absent(self):
        rec = self.tracker.evaluate_feature("no_std", 0.008, 400.0)
        self.assertEqual(rec.recommendation, "PRUNE")

    def test_sigma_zero_disables_the_margin_requirement(self):
        tracker = FeatureCostBenefitTracker(prune_confidence_sigma=0.0)
        rec = tracker.evaluate_feature("noisy", 0.008, 400.0, importance_std=0.005)
        self.assertEqual(rec.recommendation, "PRUNE")


class TestPipelineAudit(unittest.TestCase):
    def setUp(self):
        self.tracker = FeatureCostBenefitTracker(
            min_importance_threshold=0.01, max_acceptable_cost_usd=50.0
        )

    def test_pipeline_audit_savings_calculation(self):
        features = [
            {"name": "rsi_14d", "importance": 0.05, "cost_usd": 10.0},
            {"name": "expensive_news_api", "importance": 0.003, "cost_usd": 300.0},
        ]
        report = self.tracker.audit_pipeline(features)
        self.assertEqual(report.total_features_analyzed, 2)
        self.assertEqual(report.pruned_features_count, 1)
        self.assertEqual(report.potential_monthly_savings_usd, 300.0)
        self.assertEqual(report.total_monthly_cost_usd, 310.0)

    def test_counts_separate_keep_from_review(self):
        # Regression: REVIEW features were counted as "kept" and vanished from the
        # summary, so the report claimed a verdict the tracker never reached.
        features = [
            {"name": "keeper", "importance": 0.05, "cost_usd": 10.0},
            {"name": "reviewer", "importance": 0.001, "cost_usd": 5.0},
            {"name": "prunee", "importance": 0.001, "cost_usd": 300.0},
        ]
        report = self.tracker.audit_pipeline(features)
        self.assertEqual(report.kept_features_count, 1)
        self.assertEqual(report.review_features_count, 1)
        self.assertEqual(report.pruned_features_count, 1)
        self.assertEqual(
            report.kept_features_count
            + report.review_features_count
            + report.pruned_features_count,
            report.total_features_analyzed,
        )

    def test_latency_totals_are_reported(self):
        features = [
            {"name": "fast_keeper", "importance": 0.05, "cost_usd": 10.0, "latency_ms": 0.5},
            {"name": "slow_dud", "importance": 0.001, "cost_usd": 300.0, "latency_ms": 12.0},
        ]
        report = self.tracker.audit_pipeline(features)
        self.assertAlmostEqual(report.total_compute_latency_ms, 12.5, places=3)
        self.assertAlmostEqual(report.pruned_latency_ms, 12.0, places=3)

    def test_empty_pipeline_returns_zeroed_report(self):
        report = self.tracker.audit_pipeline([])
        self.assertEqual(report.total_features_analyzed, 0)
        self.assertEqual(report.total_monthly_cost_usd, 0.0)
        self.assertEqual(report.potential_monthly_savings_usd, 0.0)

    def test_missing_required_key_raises_a_named_error(self):
        with self.assertRaises(FeatureCostBenefitError):
            self.tracker.audit_pipeline([{"name": "f", "importance": 0.05}])

    def test_duplicate_feature_names_are_rejected(self):
        # Duplicates would double-count the licence cost and inflate savings.
        features = [
            {"name": "dupe", "importance": 0.001, "cost_usd": 300.0},
            {"name": "dupe", "importance": 0.001, "cost_usd": 300.0},
        ]
        with self.assertRaises(FeatureCostBenefitError):
            self.tracker.audit_pipeline(features)

    def test_non_mapping_entry_is_rejected(self):
        with self.assertRaises(FeatureCostBenefitError):
            self.tracker.audit_pipeline([["name", "f"]])

    def test_non_sequence_input_is_rejected(self):
        with self.assertRaises(FeatureCostBenefitError):
            self.tracker.audit_pipeline("rsi_14d")


class TestCorrelationGroups(unittest.TestCase):
    def setUp(self):
        self.tracker = FeatureCostBenefitTracker(
            min_importance_threshold=0.01, max_acceptable_cost_usd=50.0
        )

    def test_group_dilution_blocks_the_prune_of_a_correlated_pair(self):
        # Each member is individually below 0.01 and expensive, so the marginal rule
        # prunes both. Their aggregate 0.014 clears the threshold: permutation
        # importance is diluted across correlated features, so both must be reviewed
        # jointly instead.
        features = [
            {"name": "ma_20", "importance": 0.007, "cost_usd": 200.0, "group": "trend_cluster"},
            {"name": "ma_21", "importance": 0.007, "cost_usd": 200.0, "group": "trend_cluster"},
        ]
        report = self.tracker.audit_pipeline(features)
        self.assertEqual(report.pruned_features_count, 0)
        self.assertEqual(report.review_features_count, 2)
        self.assertEqual(report.potential_monthly_savings_usd, 0.0)
        self.assertIn("diluted", report.feature_records[0].rationale)

    def test_group_below_threshold_in_aggregate_is_still_pruned(self):
        features = [
            {"name": "junk_a", "importance": 0.002, "cost_usd": 200.0, "group": "junk_cluster"},
            {"name": "junk_b", "importance": 0.003, "cost_usd": 200.0, "group": "junk_cluster"},
        ]
        report = self.tracker.audit_pipeline(features)
        self.assertEqual(report.pruned_features_count, 2)
        self.assertEqual(report.potential_monthly_savings_usd, 400.0)

    def test_single_member_group_behaves_like_an_ungrouped_feature(self):
        features = [
            {"name": "lonely", "importance": 0.001, "cost_usd": 300.0, "group": "solo_cluster"},
        ]
        report = self.tracker.audit_pipeline(features)
        self.assertEqual(report.pruned_features_count, 1)
        self.assertEqual(report.potential_monthly_savings_usd, 300.0)

    def test_groups_do_not_leak_across_clusters(self):
        features = [
            {"name": "a1", "importance": 0.009, "cost_usd": 100.0, "group": "cluster_a"},
            {"name": "a2", "importance": 0.009, "cost_usd": 100.0, "group": "cluster_a"},
            {"name": "b1", "importance": 0.001, "cost_usd": 100.0, "group": "cluster_b"},
            {"name": "b2", "importance": 0.002, "cost_usd": 100.0, "group": "cluster_b"},
        ]
        report = self.tracker.audit_pipeline(features)
        verdicts = {r.feature_name: r.recommendation for r in report.feature_records}
        self.assertEqual(verdicts["a1"], "REVIEW")
        self.assertEqual(verdicts["a2"], "REVIEW")
        self.assertEqual(verdicts["b1"], "PRUNE")
        self.assertEqual(verdicts["b2"], "PRUNE")
        self.assertEqual(report.potential_monthly_savings_usd, 200.0)


class TestSharedCostPools(unittest.TestCase):
    def setUp(self):
        self.tracker = FeatureCostBenefitTracker(
            min_importance_threshold=0.01, max_acceptable_cost_usd=50.0
        )

    def test_partially_pruned_shared_licence_yields_no_realizable_saving(self):
        # Regression: savings were the sum of every pruned feature's cost, which
        # over-promises when one licence still has to be paid for a surviving
        # feature. These two are uncorrelated (no shared `group`) but billed
        # together, so the dilution guard must not be what blocks the prune.
        features = [
            {
                "name": "feed_valuable",
                "importance": 0.05,
                "cost_usd": 150.0,
                "cost_pool": "vendor_x",
            },
            {
                "name": "feed_dud",
                "importance": 0.0001,
                "cost_usd": 150.0,
                "cost_pool": "vendor_x",
            },
        ]
        report = self.tracker.audit_pipeline(features)
        self.assertEqual(report.pruned_features_count, 1)
        self.assertEqual(report.potential_monthly_savings_usd, 0.0)
        self.assertIn("not realizable", report.message)

    def test_fully_pruned_cost_pool_realizes_the_whole_licence(self):
        features = [
            {"name": "dud_a", "importance": 0.001, "cost_usd": 150.0, "cost_pool": "vendor_y"},
            {"name": "dud_b", "importance": 0.002, "cost_usd": 150.0, "cost_pool": "vendor_y"},
        ]
        report = self.tracker.audit_pipeline(features)
        self.assertEqual(report.pruned_features_count, 2)
        self.assertEqual(report.potential_monthly_savings_usd, 300.0)
        self.assertNotIn("not realizable", report.message)

    def test_cost_pool_and_correlation_group_are_independent(self):
        # Two correlated features (same `group`) billed on separate licences: the
        # dilution guard must block the prune on statistical grounds even though
        # each licence would individually be cancellable.
        features = [
            {
                "name": "ma_20",
                "importance": 0.007,
                "cost_usd": 200.0,
                "group": "trend_cluster",
                "cost_pool": "vendor_a",
            },
            {
                "name": "ma_21",
                "importance": 0.007,
                "cost_usd": 200.0,
                "group": "trend_cluster",
                "cost_pool": "vendor_b",
            },
        ]
        report = self.tracker.audit_pipeline(features)
        self.assertEqual(report.pruned_features_count, 0)
        self.assertEqual(report.review_features_count, 2)

    def test_blank_cost_pool_is_rejected(self):
        with self.assertRaises(FeatureCostBenefitError):
            self.tracker.evaluate_feature("f", 0.05, 10.0, cost_pool="  ")


class TestUnitMismatchGuard(unittest.TestCase):
    def test_percent_scale_importance_emits_a_warning(self):
        # 5.0 meaning "5%" against a 0.01 fraction threshold silently keeps
        # everything; the tracker must say so.
        tracker = FeatureCostBenefitTracker(min_importance_threshold=0.01)
        with self.assertLogs("feature_cost_benefit", level="WARNING") as logs:
            rec = tracker.evaluate_feature("percent_scaled", 5.0, 10.0)
        self.assertEqual(rec.recommendation, "KEEP")
        self.assertTrue(any("unit mismatch" in line for line in logs.output))


if __name__ == "__main__":
    unittest.main()

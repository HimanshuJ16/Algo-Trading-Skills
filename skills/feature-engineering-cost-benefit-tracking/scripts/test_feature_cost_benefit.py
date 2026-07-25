"""Unit tests for feature-engineering-cost-benefit-tracking."""
import unittest
from feature_cost_benefit import FeatureCostBenefitTracker


class TestFeatureCostBenefitTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = FeatureCostBenefitTracker(min_importance_threshold=0.01, max_acceptable_cost_usd=50.0)

    def test_prunes_expensive_low_value_feature(self):
        # Feature with 0.2% importance but $500/mo cost -> PRUNE
        rec = self.tracker.evaluate_feature("satellite_imagery_v1", importance_score=0.002, monthly_cost_usd=500.0)
        self.assertEqual(rec.recommendation, "PRUNE")

    def test_keeps_high_value_affordable_feature(self):
        # Feature with 5% importance and $10/mo cost -> KEEP
        rec = self.tracker.evaluate_feature("rsi_14d", importance_score=0.05, monthly_cost_usd=10.0)
        self.assertEqual(rec.recommendation, "KEEP")

    def test_pipeline_audit_savings_calculation(self):
        features = [
            {"name": "rsi_14d", "importance": 0.05, "cost_usd": 10.0},
            {"name": "expensive_news_api", "importance": 0.003, "cost_usd": 300.0},
        ]
        report = self.tracker.audit_pipeline(features)
        self.assertEqual(report.total_features_analyzed, 2)
        self.assertEqual(report.pruned_features_count, 1)
        self.assertEqual(report.potential_monthly_savings_usd, 300.0)


if __name__ == "__main__":
    unittest.main()

import unittest
from feature_stability_analyzer import (
    FeatureStabilityAnalyzerEngine, FeatureStabilityAuditReport
)

class TestFeatureStabilityAnalyzerEngine(unittest.TestCase):

    def setUp(self):
        self.engine = FeatureStabilityAnalyzerEngine(
            min_nogueira_stability_threshold=0.70,
            min_inclusion_threshold=0.80
        )
        self.candidates = [f"f{i}" for i in range(1, 11)] # f1..f10

    def test_perfectly_stable_feature_selection(self):
        # All 5 folds select the exact same 3 features {"f1", "f2", "f3"} -> Phi = 1.0
        fold_selections = [
            {"f1", "f2", "f3"},
            {"f1", "f2", "f3"},
            {"f1", "f2", "f3"},
            {"f1", "f2", "f3"},
            {"f1", "f2", "f3"}
        ]
        report = self.engine.audit_feature_stability(self.candidates, fold_selections)

        self.assertEqual(report.nogueira_stability_index_phi, 1.0)
        self.assertEqual(report.status, "STABLE_FEATURE_SET")
        self.assertEqual(report.consensus_features_count, 3)
        self.assertEqual(report.pruned_unstable_features_count, 7)
        self.assertIn("f1", report.consensus_feature_names)

    def test_unstable_erratic_feature_selection_prunes_unstable(self):
        # Erratic selections across 5 folds (M=10 features) -> Phi < 0.70!
        fold_selections = [
            {"f1", "f2", "f3"},
            {"f4", "f5", "f6"},
            {"f7", "f8"},
            {"f9", "f10"},
            {"f1", "f4", "f7"}
        ]
        report = self.engine.audit_feature_stability(self.candidates, fold_selections)

        self.assertLess(report.nogueira_stability_index_phi, 0.70)
        self.assertEqual(report.status, "UNSTABLE_OVERFITTED_FEATURE_SET")
        self.assertEqual(report.consensus_features_count, 0)
        self.assertEqual(report.pruned_unstable_features_count, 10)

if __name__ == '__main__':
    unittest.main()

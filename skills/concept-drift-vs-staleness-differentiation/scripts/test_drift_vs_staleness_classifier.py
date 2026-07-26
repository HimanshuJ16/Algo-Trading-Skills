import unittest
import numpy as np
from drift_vs_staleness_classifier import DriftVsStalenessClassifier

class TestDriftVsStalenessClassifier(unittest.TestCase):

    def setUp(self):
        self.classifier = DriftVsStalenessClassifier(
            max_staleness_sec=300.0,
            psi_threshold=0.25,
            error_ratio_threshold=1.50
        )
        np.random.seed(42)

    def test_data_staleness_detection(self):
        # Feature timestamp is 600s old (exceeds 300s limit)
        res = self.classifier.diagnose(
            feature_timestamp_sec=1000.0,
            current_timestamp_sec=1600.0,
            X_ref=np.random.randn(100, 2),
            X_curr=np.random.randn(100, 2),
            residuals_ref=np.random.randn(100),
            residuals_curr=np.random.randn(100)
        )
        self.assertEqual(res.status, "DATA_STALENESS")
        self.assertEqual(res.staleness_age_sec, 600.0)

    def test_covariate_shift_detection(self):
        # Feature distribution shifted significantly (mean shifts from 0 to 5)
        # Residual error ratio remains stable (~1.0)
        X_ref = np.random.normal(0, 1, size=(500, 1))
        X_curr = np.random.normal(5, 1, size=(500, 1))
        
        residuals_ref = np.random.normal(0, 1, size=500)
        residuals_curr = np.random.normal(0, 1, size=500)
        
        res = self.classifier.diagnose(
            feature_timestamp_sec=1000.0,
            current_timestamp_sec=1010.0,
            X_ref=X_ref, X_curr=X_curr,
            residuals_ref=residuals_ref, residuals_curr=residuals_curr
        )
        self.assertEqual(res.status, "COVARIATE_SHIFT")
        self.assertGreater(res.feature_psi_score, 0.25)
        self.assertLess(res.error_mse_ratio, 1.50)

    def test_concept_drift_detection(self):
        # Feature distribution identical (PSI ~ 0)
        # Target residual error spikes 3x (MSE ratio = 3.0)
        X_ref = np.random.normal(0, 1, size=(500, 1))
        X_curr = np.random.normal(0, 1, size=(500, 1))
        
        residuals_ref = np.random.normal(0, 1, size=500)
        residuals_curr = np.random.normal(0, 3, size=500) # Variance 3x higher -> MSE ~9x
        
        res = self.classifier.diagnose(
            feature_timestamp_sec=1000.0,
            current_timestamp_sec=1010.0,
            X_ref=X_ref, X_curr=X_curr,
            residuals_ref=residuals_ref, residuals_curr=residuals_curr
        )
        self.assertEqual(res.status, "CONCEPT_DRIFT")
        self.assertGreater(res.error_mse_ratio, 1.50)

    def test_stable_state(self):
        # Both features and residuals remain statistically identical
        X_ref = np.random.normal(0, 1, size=(500, 1))
        X_curr = np.random.normal(0, 1, size=(500, 1))
        
        residuals_ref = np.random.normal(0, 1, size=500)
        residuals_curr = np.random.normal(0, 1, size=500)
        
        res = self.classifier.diagnose(
            feature_timestamp_sec=1000.0,
            current_timestamp_sec=1010.0,
            X_ref=X_ref, X_curr=X_curr,
            residuals_ref=residuals_ref, residuals_curr=residuals_curr
        )
        self.assertEqual(res.status, "STABLE")

if __name__ == '__main__':
    unittest.main()

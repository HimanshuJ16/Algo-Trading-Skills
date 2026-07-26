import unittest
import numpy as np
from cross_asset_correlation_regime_shifts import (
    CrossAssetCorrelationRegimeDetector, CorrelationRegimeReport
)

class TestCrossAssetCorrelationRegimeDetector(unittest.TestCase):

    def setUp(self):
        self.detector = CrossAssetCorrelationRegimeDetector(
            shift_threshold=0.30, crisis_threshold=0.60, high_corr_threshold=0.65
        )
        np.random.seed(42)

    def test_stable_normal_regime(self):
        # Baseline and short-term returns generated from identical independent distribution
        base_returns = np.random.normal(0, 0.01, (100, 4))
        short_returns = np.random.normal(0, 0.01, (20, 4))
        
        report = self.detector.detect_regime(short_returns, base_returns)
        self.assertIn(report.regime, ["STABLE_NORMAL", "CORRELATION_SHIFT"])
        self.assertGreaterEqual(report.recommended_leverage_multiplier, 0.80)

    def test_crisis_convergence_regime(self):
        # Baseline: 4 assets with independent noise (low correlation)
        base_returns = np.random.normal(0, 0.01, (100, 4))
        
        # Short-term: Market Crash! All assets move in lockstep (corr ~ 1.0)
        market_factor = np.random.normal(-0.03, 0.02, (20, 1))
        short_returns = np.repeat(market_factor, 4, axis=1) + np.random.normal(0, 0.0001, (20, 4))
        
        report = self.detector.detect_regime(short_returns, base_returns)
        self.assertEqual(report.regime, "CRISIS_CONVERGENCE")
        self.assertGreaterEqual(report.frobenius_distance, 0.60)
        self.assertEqual(report.recommended_leverage_multiplier, 0.50)

    def test_frobenius_distance_calculation(self):
        c1 = np.eye(3)
        c2 = np.ones((3, 3))
        # Raw Diff norm = sqrt(6) = ~2.4495. Normalized by K=3 -> 2.4495 / 3 = 0.8165
        dist = self.detector.calculate_frobenius_distance(c1, c2)
        self.assertAlmostEqual(dist, np.sqrt(6.0) / 3.0, places=3)

if __name__ == '__main__':
    unittest.main()

import math
import unittest
import numpy as np
from cross_asset_correlation_regime_shifts import (
    CrossAssetCorrelationRegimeDetector
)

class TestCrossAssetCorrelationRegimeDetector(unittest.TestCase):

    def setUp(self):
        self.detector = CrossAssetCorrelationRegimeDetector(
            shift_threshold=0.30, crisis_threshold=0.60, high_corr_threshold=0.65
        )
        # Orthogonal sign vectors (zero mean, pairwise dot product 0):
        # stacking them as columns gives a sample correlation matrix of exact identity.
        self.signs = np.array([
            [1.0,  1.0,  1.0],
            [1.0, -1.0, -1.0],
            [-1.0, 1.0, -1.0],
            [-1.0, -1.0, 1.0],
        ])
        # Lockstep rows: every asset identical per row -> exact ones(K) correlation.
        self.lockstep = np.array([
            [2.0, 2.0, 2.0],
            [-1.0, -1.0, -1.0],
            [3.0, 3.0, 3.0],
            [-2.0, -2.0, -2.0],
        ])

    def test_stable_normal_regime_exact(self):
        # Identical orthogonal data in both windows: D_F = 0, avg corr = 0
        report = self.detector.detect_regime(self.signs, self.signs)
        self.assertEqual(report.regime, "STABLE_NORMAL")
        self.assertEqual(report.frobenius_distance, 0.0)
        self.assertEqual(report.short_term_avg_correlation, 0.0)
        self.assertEqual(report.recommended_leverage_multiplier, 1.00)

    def test_crisis_convergence_regime_exact(self):
        # Lockstep short window (ones(3)) vs orthogonal baseline (eye(3)):
        # raw diff = sqrt(6), D_F = sqrt(6)/3 = 0.8165 >= 0.60 -> CRISIS;
        # avg short corr = 1.0 >= 0.65 also triggers
        report = self.detector.detect_regime(self.lockstep, self.signs)
        self.assertEqual(report.regime, "CRISIS_CONVERGENCE")
        self.assertAlmostEqual(report.frobenius_distance, math.sqrt(6.0) / 3.0, places=4)
        self.assertEqual(report.short_term_avg_correlation, 1.0)
        self.assertEqual(report.recommended_leverage_multiplier, 0.50)

    def test_correlation_shift_regime_exact(self):
        # Base: orthogonal sign columns -> exact identity. Short window:
        # s1 = x, s2 = 0.5x + sqrt(0.75)e1, s3 = 0.5x + sqrt(0.75)e2 with
        # x, e1, e2 mutually orthogonal zero-mean sign vectors (length 8):
        # exact pairwise correlations (0.5, 0.5, 0.25), avg = 1.25/3 < 0.65.
        # D_F = sqrt(2*(0.25+0.25+0.0625))/3 = 1/(2*sqrt(2)) = 0.3536 -> SHIFT
        x = np.array([1.0, 1.0, 1.0, 1.0, -1.0, -1.0, -1.0, -1.0])
        e1 = np.array([1.0, 1.0, -1.0, -1.0, 1.0, 1.0, -1.0, -1.0])
        e2 = np.array([1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0])
        base = np.column_stack([x, e1, e2])
        short = np.column_stack([x, 0.5 * x + math.sqrt(0.75) * e1,
                                    0.5 * x + math.sqrt(0.75) * e2])
        report = self.detector.detect_regime(short, base)
        self.assertEqual(report.regime, "CORRELATION_SHIFT")
        self.assertAlmostEqual(report.frobenius_distance, 1.0 / (2.0 * math.sqrt(2.0)), places=4)
        self.assertAlmostEqual(report.short_term_avg_correlation, 1.25 / 3.0, places=4)
        self.assertEqual(report.recommended_leverage_multiplier, 0.80)

    def test_frobenius_distance_calculation(self):
        c1 = np.eye(3)
        c2 = np.ones((3, 3))
        # Raw diff norm = sqrt(6) = ~2.4495. Normalized by K=3 -> 2.4495 / 3 = 0.8165
        dist = self.detector.calculate_frobenius_distance(c1, c2)
        self.assertAlmostEqual(dist, np.sqrt(6.0) / 3.0, places=6)

    def test_frobenius_symmetry_and_single_pair_sensitivity(self):
        a = np.eye(4)
        b = np.eye(4)
        b[0, 1] = b[1, 0] = 0.4
        # Single pairwise flip of 0.40 in K=4 moves D by sqrt(2)*0.40/4 = 0.1414
        expected = math.sqrt(2.0) * 0.4 / 4.0
        self.assertAlmostEqual(
            self.detector.calculate_frobenius_distance(a, b), expected, places=6
        )
        self.assertAlmostEqual(
            self.detector.calculate_frobenius_distance(a, b),
            self.detector.calculate_frobenius_distance(b, a), places=12,
        )
        self.assertEqual(self.detector.calculate_frobenius_distance(a, a), 0.0)

    def test_regime_thresholds_are_inclusive(self):
        # K=2 matrices with off-diagonal t give D_F = t*sqrt(2)/2.
        # t = 0.30*sqrt(2) -> D_F = 0.30 exactly -> CORRELATION_SHIFT (>=)
        t_shift = 0.30 * math.sqrt(2.0)
        c = np.array([[1.0, t_shift], [t_shift, 1.0]])
        self.assertAlmostEqual(
            self.detector.calculate_frobenius_distance(np.eye(2), c), 0.30, places=9
        )
        self.assertGreaterEqual(
            self.detector.calculate_frobenius_distance(np.eye(2), c),
            self.detector.shift_threshold,
        )
        # t = 0.60*sqrt(2) -> D_F = 0.60 exactly -> crisis threshold met
        t_crisis = 0.60 * math.sqrt(2.0)
        c2 = np.array([[1.0, t_crisis], [t_crisis, 1.0]])
        self.assertAlmostEqual(
            self.detector.calculate_frobenius_distance(np.eye(2), c2), 0.60, places=9
        )

    def test_average_off_diagonal_correlation(self):
        self.assertEqual(self.detector.calculate_average_off_diagonal_correlation(np.eye(4)), 0.0)
        self.assertEqual(
            self.detector.calculate_average_off_diagonal_correlation(np.ones((4, 4))), 1.0
        )
        mixed = np.array([[1.0, 0.5, 0.2], [0.5, 1.0, 0.3], [0.2, 0.3, 1.0]])
        # Off-diagonal sum = 2*(0.5+0.2+0.3) = 2.0 over 6 entries -> 1/3
        self.assertAlmostEqual(
            self.detector.calculate_average_off_diagonal_correlation(mixed), 1.0 / 3.0,
            places=12,
        )

    def test_returns_matrix_input_validation(self):
        with self.assertRaises(ValueError):
            self.detector.compute_correlation_matrix(np.ones((1, 3)))      # N < 2
        with self.assertRaises(ValueError):
            self.detector.compute_correlation_matrix(np.ones((10, 1)))     # K < 2
        with self.assertRaises(ValueError):
            self.detector.compute_correlation_matrix(np.ones(10))          # 1-D
        with self.assertRaises(ValueError):
            self.detector.compute_correlation_matrix(np.array([[1.0, np.nan], [2.0, 1.0]]))
        with self.assertRaises(ValueError):
            self.detector.compute_correlation_matrix(np.array([[1.0, 2.0], [np.inf, 1.0]]))
        with self.assertRaises(ValueError):
            # Zero-variance (stale/flat) column: correlation undefined, must raise
            self.detector.compute_correlation_matrix(np.array([[5.0, 1.0], [5.0, -1.0], [5.0, 2.0]]))

    def test_list_input_accepted(self):
        # Second column is exactly 2x the first -> perfect correlation
        corr = self.detector.compute_correlation_matrix([[1.0, 2.0], [2.0, 4.0], [-1.0, -2.0]])
        self.assertEqual(corr.shape, (2, 2))
        self.assertAlmostEqual(corr[0, 1], 1.0, places=12)

    def test_matrix_validation(self):
        with self.assertRaises(ValueError):
            self.detector.calculate_frobenius_distance(np.eye(3), np.eye(4))   # shape mismatch
        with self.assertRaises(ValueError):
            self.detector.calculate_frobenius_distance(np.eye(1), np.eye(1))   # K < 2
        with self.assertRaises(ValueError):
            self.detector.calculate_average_off_diagonal_correlation(np.ones((2, 3)))
        with self.assertRaises(ValueError):
            self.detector.calculate_frobenius_distance(
                np.array([[1.0, np.nan], [np.nan, 1.0]]), np.eye(2)
            )

    def test_detector_threshold_validation(self):
        with self.assertRaises(ValueError):
            CrossAssetCorrelationRegimeDetector(shift_threshold=0.70, crisis_threshold=0.60)
        with self.assertRaises(ValueError):
            CrossAssetCorrelationRegimeDetector(shift_threshold=-0.1)
        with self.assertRaises(ValueError):
            CrossAssetCorrelationRegimeDetector(high_corr_threshold=1.5)
        with self.assertRaises(ValueError):
            CrossAssetCorrelationRegimeDetector(crisis_threshold=float("nan"))

    def test_detect_regime_shape_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            self.detector.detect_regime(self.signs, np.eye(4))

if __name__ == '__main__':
    unittest.main()

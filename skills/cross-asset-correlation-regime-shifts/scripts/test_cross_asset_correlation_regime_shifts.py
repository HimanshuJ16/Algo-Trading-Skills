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

    def test_two_row_window_rejected_as_degenerate(self):
        # Any two observations are perfectly correlated, so a 2-row window
        # produces off-diagonal entries of exactly +/-1 regardless of the data
        # and previously classified as CRISIS_CONVERGENCE (0.5x leverage).
        two_rows = np.array([[0.01, -0.02, 0.003], [0.004, 0.005, -0.001]])
        self.assertTrue(
            np.allclose(np.abs(np.corrcoef(two_rows, rowvar=False)), 1.0),
            "premise: 2-row windows are algebraically degenerate",
        )
        with self.assertRaises(ValueError):
            self.detector.compute_correlation_matrix(two_rows)
        with self.assertRaises(ValueError):
            self.detector.detect_regime(two_rows, self.signs)

    def test_min_observations_floor_is_configurable(self):
        strict = CrossAssetCorrelationRegimeDetector(min_observations=30)
        rng = np.random.default_rng(7)
        short_20d = rng.normal(size=(20, 3))
        long_100d = rng.normal(size=(100, 3))
        with self.assertRaises(ValueError):
            strict.detect_regime(short_20d, long_100d)      # 20 < 30 -> raises
        # The same 20-row window is accepted at the default floor.
        self.assertEqual(
            self.detector.compute_correlation_matrix(short_20d).shape, (3, 3)
        )
        with self.assertRaises(ValueError):
            CrossAssetCorrelationRegimeDetector(min_observations=2)
        with self.assertRaises(ValueError):
            CrossAssetCorrelationRegimeDetector(min_observations=30.5)

    def test_non_correlation_matrix_rejected(self):
        # A covariance matrix silently inflates D_F into CRISIS_CONVERGENCE.
        covariance = np.array([[4.0e-4, 1.0e-4], [1.0e-4, 9.0e-4]])
        with self.assertRaises(ValueError):
            self.detector.calculate_frobenius_distance(np.eye(2), covariance)
        with self.assertRaises(ValueError):
            self.detector.calculate_average_off_diagonal_correlation(covariance)
        asymmetric = np.array([[1.0, 0.5], [0.2, 1.0]])
        with self.assertRaises(ValueError):
            self.detector.calculate_frobenius_distance(np.eye(2), asymmetric)
        out_of_range = np.array([[1.0, 1.4], [1.4, 1.0]])
        with self.assertRaises(ValueError):
            self.detector.calculate_frobenius_distance(np.eye(2), out_of_range)
        # Float noise of a few ULPs on the diagonal/off-diagonal is tolerated.
        noisy = np.array([[1.0 + 1e-15, 1.0 + 1e-15], [1.0 + 1e-15, 1.0]])
        self.assertAlmostEqual(
            self.detector.calculate_average_off_diagonal_correlation(noisy), 1.0, places=12
        )

    def test_documented_single_pair_stock_bond_flip_is_not_crisis(self):
        # SKILL.md verification claim: a stock-bond flip from -0.40 to +0.75
        # alone, K=4, gives D_F = sqrt(2)*1.15/4 ~ 0.4066 -> SHIFT, not crisis.
        base = np.eye(4)
        base[0, 1] = base[1, 0] = -0.40
        short = np.eye(4)
        short[0, 1] = short[1, 0] = 0.75
        dist = self.detector.calculate_frobenius_distance(short, base)
        self.assertAlmostEqual(dist, math.sqrt(2.0) * 1.15 / 4.0, places=9)
        self.assertAlmostEqual(dist, 0.4066, places=4)
        self.assertGreaterEqual(dist, self.detector.shift_threshold)
        self.assertLess(dist, self.detector.crisis_threshold)

    def test_swapped_window_arguments_warn(self):
        rng = np.random.default_rng(11)
        short_window = rng.normal(size=(20, 3))
        baseline = rng.normal(size=(60, 3))
        with self.assertLogs(
            "cross_asset_correlation_regime_shifts", level="WARNING"
        ) as captured:
            self.detector.detect_regime(baseline, short_window)   # swapped
        self.assertTrue(
            any("may be swapped" in line for line in captured.output), captured.output
        )

if __name__ == '__main__':
    unittest.main()

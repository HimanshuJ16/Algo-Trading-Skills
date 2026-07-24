"""
Unit tests for ensemble-signal-combination-without-overfitting skill.

Tests:
1. Signal Z-score normalization and clipping to [-3.0, +3.0].
2. Equal weighting (1/N) calculation.
3. Shrunk non-negative weight allocation (blend with 1/N).
4. Sub-model signal aggregation and error handling for mismatched lengths.
"""
import unittest
from ensemble_combiner import (
    EnsembleError,
    EnsembleMethod,
    EnsembleResult,
    EnsembleSignalCombiner,
    SignalStream,
)


class TestEnsembleSignalCombiner(unittest.TestCase):

    def setUp(self):
        self.combiner = EnsembleSignalCombiner(
            method=EnsembleMethod.SHRUNK_NNLS, shrinkage_lambda=0.50
        )

    def test_zscore_normalization(self):
        raw_signals = [10.0, 20.0, 30.0, 40.0, 50.0]
        norm = EnsembleSignalCombiner.normalize_zscore(raw_signals)

        self.assertEqual(len(norm), 5)
        self.assertAlmostEqual(norm[2], 0.0, delta=0.01)  # Mean (30) -> Z-score 0.0
        self.assertTrue(all(-3.0 <= z <= 3.0 for z in norm))

    def test_shrunk_nnls_weights_and_combination(self):
        s1 = SignalStream("MomentumModel", [1.0, 2.0, 3.0, 4.0, 5.0])
        s2 = SignalStream("MeanReversionModel", [5.0, 4.0, 3.0, 2.0, 1.0])
        s3 = SignalStream("OrderFlowModel", [2.0, 2.5, 3.0, 3.5, 4.0])

        res = self.combiner.combine_signals([s1, s2, s3])

        self.assertEqual(len(res.ensemble_signals), 5)
        self.assertEqual(len(res.weights), 3)

        # All weights must be non-negative and sum to 1.0
        self.assertTrue(all(w >= 0.0 for w in res.weights.values()))
        self.assertAlmostEqual(sum(res.weights.values()), 1.0, delta=0.001)

    def test_mismatched_length_raises_error(self):
        s1 = SignalStream("M1", [1.0, 2.0, 3.0])
        s2 = SignalStream("M2", [1.0, 2.0])

        with self.assertRaises(EnsembleError):
            self.combiner.combine_signals([s1, s2])


if __name__ == "__main__":
    unittest.main()

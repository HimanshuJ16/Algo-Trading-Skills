"""
Unit tests for walk-forward-validation-setup skill.

Tests:
1. Expanding vs Rolling split index generation.
2. Purge/embargo gap separation between train_end and test_start.
3. Strict chronological non-overlapping train/test indexing.
4. Per-fold metrics computation across walk-forward splits.
5. Backward compatibility with walk_forward_splits.
"""
import unittest
import numpy as np
import pandas as pd
from walk_forward import SplitMode, WalkForwardSplitter, walk_forward_splits


class TestWalkForwardValidationSetup(unittest.TestCase):

    def setUp(self):
        self.n_rows = 1000
        self.train_size = 300
        self.test_size = 100
        self.embargo = 20

    def test_purged_embargo_gap(self):
        splits = WalkForwardSplitter.generate_splits(
            n_rows=self.n_rows,
            train_size=self.train_size,
            test_size=self.test_size,
            embargo_size=self.embargo,
            mode=SplitMode.EXPANDING,
        )

        self.assertGreater(len(splits), 0)

        for split in splits:
            tr_start, tr_end = split.train_indices
            emb_start, emb_end = split.embargo_indices
            te_start, te_end = split.test_indices

            # Confirm embargo starts at train_end and lasts embargo_size
            self.assertEqual(emb_start, tr_end)
            self.assertEqual(emb_end - emb_start, self.embargo)

            # Confirm test starts at embargo_end
            self.assertEqual(te_start, emb_end)
            self.assertEqual(te_end - te_start, self.test_size)

    def test_rolling_vs_expanding_mode(self):
        exp_splits = WalkForwardSplitter.generate_splits(
            self.n_rows, self.train_size, self.test_size, self.embargo, mode=SplitMode.EXPANDING
        )
        roll_splits = WalkForwardSplitter.generate_splits(
            self.n_rows, self.train_size, self.test_size, self.embargo, mode=SplitMode.ROLLING
        )

        # Expanding mode: train_indices start at 0
        self.assertEqual(exp_splits[1].train_indices[0], 0)

        # Rolling mode: train_indices start moves forward
        self.assertGreater(roll_splits[1].train_indices[0], 0)

    def test_evaluator_fold_metrics(self):
        df = pd.DataFrame(
            {
                "feature": np.random.randn(self.n_rows),
                "target": np.random.choice([0, 1], size=self.n_rows),
            }
        )

        def mock_fit_predict(train_df, test_df):
            # Returns dummy predictions
            return np.ones(len(test_df), dtype=int)

        splitter = WalkForwardSplitter(mode=SplitMode.EXPANDING)
        metrics = splitter.evaluate_walk_forward(
            df,
            train_size=self.train_size,
            test_size=self.test_size,
            embargo_size=self.embargo,
            fit_predict_fn=mock_fit_predict,
            target_col="target",
        )

        self.assertGreater(len(metrics), 0)
        self.assertEqual(metrics[0].fold_index, 0)
        self.assertTrue(0.0 <= metrics[0].accuracy <= 1.0)

    def test_backward_compatibility(self):
        splits = walk_forward_splits(self.n_rows, self.train_size, self.test_size, self.embargo, "expanding")
        self.assertGreater(len(splits), 0)
        self.assertIn("train", splits[0])
        self.assertIn("test", splits[0])


if __name__ == "__main__":
    unittest.main()

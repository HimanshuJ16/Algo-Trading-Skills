import unittest
from transfer_learning_bootstrap import (
    Dataset,
    TransferConfig,
    ModelParameters,
    FinancialTransferLearningEngine,
    MLOpsError,
)


class TestFinancialTransferLearningEngine(unittest.TestCase):
    def setUp(self):
        self.engine = FinancialTransferLearningEngine()

        # Generate synthetic source dataset (100 samples, 2 features)
        self.source_X = [[float(i), float(i * 2)] for i in range(100)]
        self.source_y = [2.0 * self.source_X[i][0] + 0.5 * self.source_X[i][1] + 1.0 for i in range(100)]
        self.source_data = Dataset("SPY", self.source_X, self.source_y, ["feat1", "feat2"])

        # Generate sparse target dataset (10 samples, highly correlated with source)
        self.target_X = [[float(i), float(i * 2)] for i in range(10)]
        self.target_y = [2.0 * self.target_X[i][0] + 0.5 * self.target_X[i][1] + 1.0 for i in range(10)]
        self.target_data = Dataset("NEW_ETF", self.target_X, self.target_y, ["feat1", "feat2"])

    def test_correlation_calculation(self):
        x = [1.0, 2.0, 3.0, 4.0, 5.0]
        y = [2.0, 4.0, 6.0, 8.0, 10.0]
        corr = FinancialTransferLearningEngine.calculate_correlation(x, y)
        self.assertAlmostEqual(corr, 1.0)

    def test_covariate_shift_calculation(self):
        src = [[1.0, 2.0], [2.0, 3.0], [3.0, 4.0]]
        tgt = [[1.1, 2.1], [2.1, 3.1], [3.1, 4.1]]
        shift = FinancialTransferLearningEngine.calculate_covariate_shift(src, tgt)
        self.assertTrue(shift < 0.5)

    def test_fit_source_model(self):
        params = self.engine.fit_source_model(self.source_data)
        self.assertEqual(len(params.weights), 2)
        preds = self.engine.predict(params, self.source_X[:5])
        self.assertEqual(len(preds), 5)

    def test_fine_tune_target_model(self):
        src_params = self.engine.fit_source_model(self.source_data)
        cfg = TransferConfig(source_symbol="SPY", target_symbol="NEW_ETF", min_correlation=0.5)
        ft_params = self.engine.fine_tune_target_model(src_params, self.target_data, cfg)
        self.assertEqual(len(ft_params.weights), 2)

    def test_evaluate_transfer_performance_positive_gain(self):
        cfg = TransferConfig(source_symbol="SPY", target_symbol="NEW_ETF", min_correlation=0.5)
        eval_res = self.engine.evaluate_transfer_performance(self.source_data, self.target_data, cfg)
        self.assertEqual(eval_res.source_symbol, "SPY")
        self.assertEqual(eval_res.target_symbol, "NEW_ETF")
        self.assertTrue(eval_res.correlation > 0.9)
        self.assertTrue(len(eval_res.audit_trail) >= 4)

    def test_evaluate_transfer_performance_uncorrelated_rejection(self):
        # Uncorrelated target dataset
        uncorr_y = [10.0, -5.0, 3.0, -8.0, 12.0, -2.0, 4.0, -1.0, 0.0, 5.0]
        uncorr_target_data = Dataset("UNCORR_ETF", self.target_X, uncorr_y, ["feat1", "feat2"])

        cfg = TransferConfig(source_symbol="SPY", target_symbol="UNCORR_ETF", min_correlation=0.80)
        eval_res = self.engine.evaluate_transfer_performance(self.source_data, uncorr_target_data, cfg)
        self.assertFalse(eval_res.is_transfer_recommended)


if __name__ == "__main__":
    unittest.main()


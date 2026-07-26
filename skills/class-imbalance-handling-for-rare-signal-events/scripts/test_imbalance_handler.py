import unittest
import numpy as np
from imbalance_handler import ImbalanceHandler

class TestImbalanceHandler(unittest.TestCase):

    def setUp(self):
        # Create a mock imbalanced dataset
        # Class 0: 990 samples (Noise)
        # Class 1: 10 samples (Rare Event)
        self.y = np.concatenate([np.zeros(990, dtype=int), np.ones(10, dtype=int)])
        self.X = np.random.rand(1000, 5) # 1000 rows, 5 features

    def test_compute_class_weights(self):
        weights = ImbalanceHandler.compute_class_weights(self.y)
        
        self.assertIn(0, weights)
        self.assertIn(1, weights)
        
        # Total samples = 1000, n_classes = 2
        # Class 0 weight = 1000 / (2 * 990) = ~0.505
        # Class 1 weight = 1000 / (2 * 10) = 50.0
        self.assertAlmostEqual(weights[0], 1000 / (2 * 990), places=4)
        self.assertAlmostEqual(weights[1], 50.0, places=4)
        
        # Verify that class 1 is weighted much higher than class 0
        self.assertGreater(weights[1], weights[0] * 90)

    def test_random_undersample(self):
        X_bal, y_bal = ImbalanceHandler.random_undersample(self.X, self.y, random_state=42)
        
        # Since class 1 has 10 samples, the balanced dataset should have exactly 20 samples (10 of each)
        self.assertEqual(len(y_bal), 20)
        self.assertEqual(len(X_bal), 20)
        
        counts = np.bincount(y_bal)
        self.assertEqual(counts[0], 10)
        self.assertEqual(counts[1], 10)

    def test_undersample_determinism(self):
        # Ensuring that the same random_state yields the exact same indices
        X_bal1, y_bal1 = ImbalanceHandler.random_undersample(self.X, self.y, random_state=123)
        X_bal2, y_bal2 = ImbalanceHandler.random_undersample(self.X, self.y, random_state=123)
        
        np.testing.assert_array_equal(X_bal1, X_bal2)
        np.testing.assert_array_equal(y_bal1, y_bal2)

if __name__ == '__main__':
    unittest.main()

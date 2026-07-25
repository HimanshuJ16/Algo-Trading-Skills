import unittest
import numpy as np
from benchmark_selector import BenchmarkSelector

class TestBenchmarkSelector(unittest.TestCase):
    def setUp(self):
        # 10 days of data
        self.strat_returns = [0.01, -0.005, 0.02, 0.001, -0.01, 0.015, -0.002, 0.008, 0.004, -0.001]
        
        # SPY is highly correlated
        self.spy_returns = [0.009, -0.004, 0.018, 0.002, -0.009, 0.012, -0.001, 0.007, 0.003, 0.000]
        
        # TLT is negatively correlated
        self.tlt_returns = [-0.005, 0.01, -0.01, -0.002, 0.015, -0.008, 0.005, -0.003, -0.002, 0.004]
        
        # Risk-free is flat
        self.rf_returns = [0.0001] * 10
        
        self.benchmarks = {
            "SPY": self.spy_returns,
            "TLT": self.tlt_returns,
            "RF": self.rf_returns
        }
        self.selector = BenchmarkSelector(self.benchmarks)

    def test_evaluate_benchmarks(self):
        metrics = self.selector.evaluate_benchmarks(self.strat_returns)
        self.assertEqual(len(metrics), 3)
        
        # Should be sorted by correlation descending
        self.assertEqual(metrics[0].name, "SPY")
        self.assertTrue(metrics[0].correlation > 0.9)
        
        self.assertEqual(metrics[-1].name, "TLT")
        self.assertTrue(metrics[-1].correlation < 0.0)

    def test_recommend_benchmark(self):
        recommendation = self.selector.recommend_benchmark(self.strat_returns)
        self.assertEqual(recommendation, "SPY")

if __name__ == '__main__':
    unittest.main()

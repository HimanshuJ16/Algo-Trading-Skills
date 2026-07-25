import unittest
import numpy as np
from multi_strategy_benchmarker import MultiStrategyBenchmarker

class TestMultiStrategyBenchmarker(unittest.TestCase):
    
    def setUp(self):
        self.benchmarker = MultiStrategyBenchmarker(risk_free_rate_annual=0.04, periods_per_year=252)

    def test_pure_beta_replication(self):
        # Portfolio is exactly 2x the benchmark
        # No alpha generated, just pure leverage.
        benchmark = np.array([0.01, -0.005, 0.02, -0.01, 0.015])
        portfolio = benchmark * 2.0
        
        res = self.benchmarker.evaluate_performance(portfolio, benchmark)
        
        self.assertAlmostEqual(res["Beta"], 2.0, places=3)
        self.assertAlmostEqual(res["Correlation"], 1.0, places=3)
        # Because we used 2x leverage without borrowing at the risk-free rate in this simplified test array,
        # the formula: alpha = (Rp - Rf) - Beta * (Rb - Rf)
        # If Rp = 2*Rb, then alpha = (2*Rb - Rf) - 2*(Rb - Rf) = 2Rb - Rf - 2Rb + 2Rf = Rf.
        # Thus, alpha_annual = 0.04. 
        self.assertAlmostEqual(res["Annualized Alpha"], 0.04, places=3)
        self.assertTrue(res["Tracking Error (Ann)"] > 0)

    def test_pure_alpha_generation(self):
        # Benchmark is entirely flat (no returns).
        # Portfolio generates a steady 10 bps a day.
        # Beta must be 0, Correlation must be NaN/0, Alpha must be positive.
        benchmark = np.array([0.0, 0.0, 0.0, 0.0, 0.0])
        portfolio = np.array([0.001, 0.001, 0.001, 0.001, 0.001])
        
        res = self.benchmarker.evaluate_performance(portfolio, benchmark)
        
        self.assertAlmostEqual(res["Beta"], 0.0, places=3)
        self.assertTrue(res["Annualized Alpha"] > 0.0)

    def test_information_ratio(self):
        # Portfolio beats benchmark by exactly 10 bps every day
        benchmark = np.array([0.01, -0.005, 0.02])
        portfolio = benchmark + 0.001
        
        res = self.benchmarker.evaluate_performance(portfolio, benchmark)
        
        # Tracking error is 0 because active return is exactly constant (0.001 standard deviation is 0)
        # Handled by division by zero safety
        self.assertEqual(res["Tracking Error (Ann)"], 0.0)
        self.assertEqual(res["Information Ratio"], 0.0)

        # Now add variance to the active return
        portfolio2 = benchmark + np.array([0.001, 0.003, 0.002])
        res2 = self.benchmarker.evaluate_performance(portfolio2, benchmark)
        self.assertTrue(res2["Tracking Error (Ann)"] > 0)
        self.assertTrue(res2["Information Ratio"] > 0)

if __name__ == '__main__':
    unittest.main()

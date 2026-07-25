import unittest
import numpy as np
from tearsheet_generator import StandardizedTearsheetGenerator

class TestStandardizedTearsheetGenerator(unittest.TestCase):
    def setUp(self):
        self.generator = StandardizedTearsheetGenerator(risk_free_rate=0.0, periods_per_year=252)

    def test_generate_tearsheet(self):
        returns = np.array([0.01, 0.02, -0.01, -0.02, 0.03, 0.01, -0.01])
        report = self.generator.generate(returns)
        
        self.assertIn("Total Return", report)
        self.assertIn("Sharpe Ratio", report)
        self.assertIn("Max Drawdown", report)
        self.assertIn("Hit Rate", report)
        self.assertIn("Profit Factor", report)
        
        self.assertAlmostEqual(report["Hit Rate"], 4/7)
        self.assertTrue(report["Max Drawdown"] <= 0)

    def test_empty_returns(self):
        report = self.generator.generate(np.array([]))
        self.assertEqual(report, {})

if __name__ == '__main__':
    unittest.main()

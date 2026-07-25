"""
Unit tests for demo-account-realism-gap-assessment skill.
"""
import unittest
from realism_assessor import DemoRealismAssessor, ExecutionLog


class TestDemoRealismAssessor(unittest.TestCase):

    def setUp(self):
        # Demo logs: instant fills (10ms latency, 0 bps slippage, 100% fill rate)
        self.demo_logs = [
            ExecutionLog("DEMO", "AAPL", 150.0, 150.0, 100, 100, 1000.0, 1000.01),
            ExecutionLog("DEMO", "AAPL", 150.0, 150.0, 100, 100, 2000.0, 2000.01),
        ]
        # Realistic Live logs: 50ms latency, 5 bps slippage, 90% fill rate
        self.live_logs = [
            ExecutionLog("LIVE", "AAPL", 150.0, 150.075, 100, 90, 1000.0, 1000.05),
            ExecutionLog("LIVE", "AAPL", 150.0, 150.075, 100, 90, 2000.0, 2000.05),
        ]

    def test_realism_assessment_score_calculation(self):
        assessor = DemoRealismAssessor(self.demo_logs, self.live_logs)
        result = assessor.assess_realism(unadjusted_demo_sharpe=2.5)

        self.assertGreater(result.realism_score, 0.0)
        self.assertLess(result.realism_score, 1.0)
        self.assertLess(result.adjusted_sharpe_ratio, 2.5)
        self.assertAlmostEqual(result.mean_demo_latency_ms, 10.0, places=1)
        self.assertAlmostEqual(result.mean_live_latency_ms, 50.0, places=1)

    def test_perfect_parity_high_score(self):
        # When live logs match demo logs perfectly
        assessor = DemoRealismAssessor(self.demo_logs, self.demo_logs)
        result = assessor.assess_realism(unadjusted_demo_sharpe=2.0)

        self.assertAlmostEqual(result.realism_score, 1.0, places=2)
        self.assertAlmostEqual(result.adjusted_sharpe_ratio, 2.0, places=2)


if __name__ == "__main__":
    unittest.main()

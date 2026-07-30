import unittest
from execution_algorithm_regression_testing_suite import (
    ExecutionAlgoRegressionTestSuite, ScenarioTestResult, RegressionTestSuiteAuditReport
)

class TestExecutionAlgoRegressionTestSuite(unittest.TestCase):

    def setUp(self):
        self.suite = ExecutionAlgoRegressionTestSuite(
            max_allowed_is_degradation_bps=2.0,
            min_allowed_fill_ratio=0.98,
            max_allowed_participation_rate=0.20
        )

    def test_passing_candidate_build(self):
        scenarios = [
            ScenarioTestResult("SC_01", "NORMAL_VOLATILITY", baseline_is_bps=12.0, candidate_is_bps=12.5, baseline_fill_rate=1.0, candidate_fill_rate=1.0, candidate_max_participation_rate=0.10, passed=True),
            ScenarioTestResult("SC_02", "VOLATILITY_SHOCK", baseline_is_bps=25.0, candidate_is_bps=26.0, baseline_fill_rate=0.99, candidate_fill_rate=0.99, candidate_max_participation_rate=0.15, passed=True)
        ]
        report = self.suite.run_regression_suite("VWAP_ALGO", "v2.4.1", scenarios)

        self.assertEqual(report.cicd_gate_status, "PASS_REGRESSION_APPROVED")
        self.assertEqual(report.scenarios_passed_count, 2)
        self.assertEqual(report.scenarios_failed_count, 0)
        self.assertEqual(report.avg_is_degradation_bps, 0.75)

    def test_regressed_candidate_build_rejected(self):
        # Scenario 2 has severe IS degradation (+4.5 bps > +2.0 bps limit) -> REJECTED!
        scenarios = [
            ScenarioTestResult("SC_01", "NORMAL_VOLATILITY", baseline_is_bps=12.0, candidate_is_bps=12.5, baseline_fill_rate=1.0, candidate_fill_rate=1.0, candidate_max_participation_rate=0.10, passed=True),
            ScenarioTestResult("SC_02", "LIQUIDITY_CRUNCH", baseline_is_bps=20.0, candidate_is_bps=24.5, baseline_fill_rate=1.0, candidate_fill_rate=0.95, candidate_max_participation_rate=0.15, passed=True)
        ]
        report = self.suite.run_regression_suite("VWAP_ALGO", "v2.4.2-BAD", scenarios)

        self.assertEqual(report.cicd_gate_status, "FAIL_REGRESSION_REJECTED")
        self.assertEqual(report.scenarios_failed_count, 1)
        self.assertFalse(report.scenario_details[1].passed)
        self.assertIn("IS degradation +4.5bps", report.scenario_details[1].failure_reason)

if __name__ == '__main__':
    unittest.main()

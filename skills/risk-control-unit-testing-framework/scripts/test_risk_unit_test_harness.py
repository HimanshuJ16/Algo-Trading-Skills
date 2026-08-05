import unittest
from risk_unit_test_harness import (
    InputData, RiskUnitTestHarness,
    RiskControlUnitTestFrameworkEngine, ProposedOrder, RiskRuleConfig, PreTradeRiskEngine
)

class TestLegacyRiskUnitTestHarness(unittest.TestCase):
    def setUp(self):
        self.engine = RiskUnitTestHarness()

    def test_process_valid(self):
        data = InputData("test", 10.0)
        self.assertTrue(self.engine.process(data))

    def test_process_invalid(self):
        data = InputData("test", -10.0)
        self.assertFalse(self.engine.process(data))

    def test_history(self):
        data = InputData("test", 10.0)
        self.engine.process(data)
        self.assertEqual(len(self.engine.history), 1)

class TestRiskControlUnitTestFrameworkEngine(unittest.TestCase):

    def setUp(self):
        self.framework = RiskControlUnitTestFrameworkEngine()

    def test_standard_suite_all_pass(self):
        report = self.framework.run_standard_suite()
        self.assertEqual(report.status, "ALL_RISK_TESTS_PASSED")
        self.assertEqual(report.failed_tests, 0)
        self.assertEqual(report.passed_tests, 5)
        self.assertEqual(report.pass_rate_pct, 100.0)

    def test_single_test_case_pass(self):
        order = ProposedOrder("TEST_O1", "MSFT", "BUY", 100.0, 300.0, current_mid_price=300.0)
        res = self.framework.run_test_case("Normal MSFT Buy", order, expected_allowed=True)
        self.assertTrue(res.passed)

    def test_single_test_case_fail_detected(self):
        # Order should breach max order size (2000 > 1000), but we expect it to be allowed -> should report test case failure
        order = ProposedOrder("TEST_O2", "MSFT", "BUY", 2000.0, 300.0, current_mid_price=300.0)
        res = self.framework.run_test_case("Incorrect Expectation Test", order, expected_allowed=True)
        self.assertFalse(res.passed)
        self.assertIn("FAILED", res.detail)

if __name__ == '__main__':
    unittest.main()

import unittest
from model_ab_tester import (
    ModelABTesterEngine, ABTestConfig, ModelExecutionResult, ABTestReport
)

class TestModelABTesterEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ModelABTesterEngine()
        self.config = ABTestConfig(
            experiment_id="EXP_001",
            champion_model_id="CHAMPION_V1",
            challenger_model_id="CHALLENGER_V2",
            traffic_split_ratio=0.80,
            test_mode="LIVE_SPLIT",
            min_sample_size=30
        )

    def test_deterministic_traffic_routing(self):
        # Hash routing check
        route_1 = self.engine.route_request(self.config, "AAPL")
        route_2 = self.engine.route_request(self.config, "AAPL")
        self.assertEqual(route_1, route_2) # Deterministic!

        # Shadow mode routing check
        shadow_cfg = ABTestConfig("EXP_002", "CHAMPION_V1", "CHALLENGER_V2", test_mode="SHADOW")
        self.assertEqual(self.engine.route_request(shadow_cfg, "MSFT"), "CHAMPION_V1")

    def test_promote_challenger_statistical_significance(self):
        # 50 Champion samples @ 2.0 bps | 50 Challenger samples @ 8.0 bps -> PROMOTE_CHALLENGER_TO_CHAMPION!
        champ_res = [ModelExecutionResult("CHAMPION_V1", float(i), "AAPL", signal_return_bps=2.0 + (i % 3) * 0.1, latency_ms=5.0) for i in range(50)]
        chall_res = [ModelExecutionResult("CHALLENGER_V2", float(i), "AAPL", signal_return_bps=8.0 + (i % 3) * 0.1, latency_ms=5.0) for i in range(50)]

        report = self.engine.evaluate_ab_test_results(self.config, champ_res, chall_res)

        self.assertEqual(report.status, "AB_TEST_COMPLETED")
        self.assertTrue(report.is_statistically_significant)
        self.assertEqual(report.recommended_action, "PROMOTE_CHALLENGER_TO_CHAMPION")
        self.assertGreater(report.welch_t_statistic, 10.0)
        self.assertLess(report.p_value, 0.01)

    def test_reject_underperforming_challenger(self):
        # 50 Champion samples @ 5.0 bps | 50 Challenger samples @ -2.0 bps -> REJECT_CHALLENGER!
        champ_res = [ModelExecutionResult("CHAMPION_V1", float(i), "AAPL", signal_return_bps=5.0, latency_ms=5.0) for i in range(50)]
        chall_res = [ModelExecutionResult("CHALLENGER_V2", float(i), "AAPL", signal_return_bps=-2.0, latency_ms=5.0) for i in range(50)]

        report = self.engine.evaluate_ab_test_results(self.config, champ_res, chall_res)

        self.assertEqual(report.recommended_action, "REJECT_CHALLENGER")
        self.assertTrue(report.is_statistically_significant)
        self.assertLess(report.mean_difference_bps, 0.0)

if __name__ == '__main__':
    unittest.main()

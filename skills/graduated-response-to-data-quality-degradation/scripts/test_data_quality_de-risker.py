import unittest
import sys
import os
import importlib

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

module = importlib.import_module("data_quality_de-risker")
DataQualityDeRiskerEngine = getattr(module, "DataQualityDeRiskerEngine")
DataQualityMetrics = getattr(module, "DataQualityMetrics")
Result = getattr(module, "Result")

class TestDataQualityDeRiskerEngine(unittest.TestCase):
    def setUp(self):
        self.engine = DataQualityDeRiskerEngine()

    def test_execute_backward_compatibility(self):
        res = self.engine.execute(True)
        self.assertTrue(res.success)
        res_false = self.engine.execute(False)
        self.assertFalse(res_false.success)

    def test_tier_0_normal_quality_allows_full_trading(self):
        metrics = DataQualityMetrics("AAPL", stale_time_seconds=0.1, missing_sequence_count=0)
        report = self.engine.audit_and_de_risk(metrics)

        self.assertEqual(report.de_risking_tier, 0)
        self.assertEqual(report.action_mandate, "ALLOW_FULL_TRADING")
        self.assertEqual(report.position_sizing_factor, 1.0)

    def test_tier_1_minor_degradation_applies_50_pct_size_haircut(self):
        # Stale time = 2.5s -> Penalty = 15.0%. Score = 85.0% -> Tier 1 (50% size)
        metrics = DataQualityMetrics("AAPL", stale_time_seconds=2.5, missing_sequence_count=0)
        report = self.engine.audit_and_de_risk(metrics)

        self.assertEqual(report.de_risking_tier, 1)
        self.assertEqual(report.action_mandate, "REDUCE_SIZE_50_PCT")
        self.assertEqual(report.position_sizing_factor, 0.50)

    def test_tier_3_severe_outage_triggers_emergency_flatten(self):
        # Crossed book -> Penalty = 50%. Stale time = 3.0s -> Penalty = 20%. Score = 30.0% -> Tier 3 Emergency Flatten!
        metrics = DataQualityMetrics("AAPL", stale_time_seconds=3.0, crossed_book_detected=True)
        report = self.engine.audit_and_de_risk(metrics)

        self.assertEqual(report.de_risking_tier, 3)
        self.assertEqual(report.action_mandate, "EMERGENCY_HALT_AND_FLATTEN")
        self.assertEqual(report.position_sizing_factor, 0.0)

if __name__ == '__main__':
    unittest.main()

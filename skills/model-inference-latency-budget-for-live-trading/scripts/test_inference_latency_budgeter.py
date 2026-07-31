import unittest
from inference_latency_budgeter import (
    ModelInferenceLatencyBudgeterEngine, InferenceBudgetConfig, InferenceBudgetReport
)

class TestModelInferenceLatencyBudgeterEngine(unittest.TestCase):

    def setUp(self):
        self.engine = ModelInferenceLatencyBudgeterEngine()

    def test_normal_inference_latency_compliance(self):
        # 100 samples ranging from 0.2ms to 0.6ms (P99 ~ 0.59ms <= 1.0ms budget) -> INFERENCE_LATENCY_NORMAL!
        samples = [0.2 + (i * 0.004) for i in range(100)]
        cfg = InferenceBudgetConfig("XGB_ALPHA_01", max_inference_budget_ms=1.0, warning_threshold_ms=0.8)

        report = self.engine.evaluate_inference_latency_budget(cfg, samples)

        self.assertTrue(report.is_sla_compliant)
        self.assertEqual(report.status, "INFERENCE_LATENCY_NORMAL")
        self.assertLess(report.percentiles.p99_ms, 0.8)
        self.assertIsNone(report.recommended_fallback_action)

    def test_sla_breach_and_quantized_onnx_fallback(self):
        # 95 samples @ 0.3ms + 5 spike samples @ 2.5ms (P99 = 2.5ms > 1.0ms budget) -> INFERENCE_LATENCY_SLA_BREACH!
        samples = [0.3] * 95 + [2.5] * 5
        cfg = InferenceBudgetConfig("LSTM_NEURAL_02", max_inference_budget_ms=1.0, fallback_action="QUANTIZED_ONNX_FALLBACK")

        report = self.engine.evaluate_inference_latency_budget(cfg, samples)

        self.assertFalse(report.is_sla_compliant)
        self.assertEqual(report.status, "INFERENCE_LATENCY_SLA_BREACH")
        self.assertEqual(report.recommended_fallback_action, "QUANTIZED_ONNX_FALLBACK")
        self.assertEqual(report.percentiles.p99_ms, 2.5)

if __name__ == '__main__':
    unittest.main()

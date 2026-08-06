import unittest
from strategy_latency_budget_decomposition import (
    Config, Engine,
    StrategyLatencyBudgetDecompositionEngine, LatencyPipelineStage,
    LatencyDecompositionReport, StageLatencyMeasurement
)

class TestEngineLegacy(unittest.TestCase):
    def test_process_true(self):
        engine = Engine(Config(threshold=1.0))
        self.assertTrue(engine.process(2.0))

    def test_process_false(self):
        engine = Engine(Config(threshold=1.0))
        self.assertFalse(engine.process(0.5))

class TestStrategyLatencyBudgetDecompositionEngineAdvanced(unittest.TestCase):

    def setUp(self):
        self.engine = StrategyLatencyBudgetDecompositionEngine()

    def test_slo_compliant_latency_decomposition(self):
        # Total SLA Budget = 2 + 3 + 10 + 5 + 5 = 25 us
        stage_latencies = {
            LatencyPipelineStage.INGRESS_NETWORK: 1.5,
            LatencyPipelineStage.MARKET_DATA_DECODE: 2.0,
            LatencyPipelineStage.SIGNAL_COMPUTATION: 7.0,
            LatencyPipelineStage.PRE_TRADE_RISK: 3.5,
            LatencyPipelineStage.EGRESS_ORDER_ENCODE: 4.0,
        }
        report = self.engine.decompose_tick_to_trade("TRADE_001", stage_latencies)

        self.assertTrue(report.is_within_budget)
        self.assertEqual(report.total_tick_to_trade_latency_us, 18.0)
        self.assertEqual(report.total_sla_budget_us, 25.0)
        self.assertEqual(len(report.breached_stages), 0)
        self.assertEqual(report.primary_bottleneck_stage, LatencyPipelineStage.SIGNAL_COMPUTATION)

    def test_sla_budget_breach_and_bottleneck_detection(self):
        stage_latencies = {
            LatencyPipelineStage.INGRESS_NETWORK: 1.5,
            LatencyPipelineStage.MARKET_DATA_DECODE: 2.0,
            LatencyPipelineStage.SIGNAL_COMPUTATION: 25.0, # BREACH! 25us > 10us SLA
            LatencyPipelineStage.PRE_TRADE_RISK: 4.0,
            LatencyPipelineStage.EGRESS_ORDER_ENCODE: 4.0,
        }
        report = self.engine.decompose_tick_to_trade("TRADE_002", stage_latencies)

        self.assertFalse(report.is_within_budget)
        self.assertEqual(report.total_tick_to_trade_latency_us, 36.5)
        self.assertEqual(len(report.breached_stages), 1)
        self.assertEqual(report.breached_stages[0], LatencyPipelineStage.SIGNAL_COMPUTATION)
        self.assertEqual(report.primary_bottleneck_stage, LatencyPipelineStage.SIGNAL_COMPUTATION)

if __name__ == '__main__':
    unittest.main()

import unittest
from fire_drill_simulator import (
    FireDrillSimulator, FireDrillSimulatorConfig,
    FireDrillScenario, FireDrillReport
)

class TestFireDrillSimulator(unittest.TestCase):

    def setUp(self):
        self.config = FireDrillSimulatorConfig(enabled=True, max_allowed_risk_latency_ms=5.0)
        self.instance = FireDrillSimulator(self.config)

    def test_initialization_legacy(self):
        self.assertTrue(self.instance.config.enabled)

    def test_execute_legacy(self):
        result = self.instance.execute({"mock": "data"})
        self.assertTrue(result)

    def test_execute_disabled_legacy(self):
        self.instance.config.enabled = False
        result = self.instance.execute({"mock": "data"})
        self.assertFalse(result)

    def test_fire_drill_exchange_limit_success(self):
        # Ingest 12,000 contracts vs 10,000 limit with 1.2ms risk latency
        scenario = FireDrillScenario(
            scenario_id="DRILL_CFTC_001",
            breach_type="EXCHANGE_LIMIT",
            target_symbol="CL_FUT",
            injected_position_qty=12000.0,
            limit_threshold=10000.0,
            simulated_latency_ms=1.2
        )
        report = self.instance.run_fire_drill(scenario)

        self.assertEqual(report.status, "BREACH_BLOCKED_KILL_SWITCH_ENGAGED")
        self.assertTrue(report.order_blocked_by_gateway)
        self.assertTrue(report.kill_switch_tripped)
        self.assertTrue(report.compliance_alert_logged)
        self.assertTrue(report.latency_sla_passed)

    def test_fire_drill_latency_sla_failure(self):
        # Ingest breach with 15.0ms risk latency (> 5.0ms SLA)
        scenario = FireDrillScenario(
            scenario_id="DRILL_CFTC_SLOW",
            breach_type="ROGUE_ALGO",
            target_symbol="NQ_FUT",
            injected_position_qty=5000.0,
            limit_threshold=1000.0,
            simulated_latency_ms=15.0
        )
        report = self.instance.run_fire_drill(scenario)

        self.assertEqual(report.status, "LATENCY_SLA_FAILED")
        self.assertFalse(report.latency_sla_passed)

if __name__ == '__main__':
    unittest.main()

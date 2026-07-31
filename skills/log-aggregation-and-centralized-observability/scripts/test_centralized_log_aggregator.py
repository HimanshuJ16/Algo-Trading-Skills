import json
import unittest
from centralized_log_aggregator import (
    CentralizedLogAggregatorEngine, RawLogRecord, ObservabilityReport
)

class TestCentralizedLogAggregatorEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CentralizedLogAggregatorEngine(error_spike_threshold_count=5)

    def test_log_redaction_and_json_formatting(self):
        records = [
            RawLogRecord(
                subsystem="ORDER_ROUTER",
                level="INFO",
                message="Order submitted to CME",
                correlation_id="trace-101",
                metadata={"order_id": "ORD-999", "api_key": "SUPER_SECRET_KEY_123"}
            )
        ]
        report = self.engine.process_and_aggregate_logs(records)

        self.assertEqual(report.status, "LOGS_AGGREGATED_NORMAL")
        self.assertEqual(report.redacted_keys_count, 1)

        payload_dict = json.loads(report.formatted_json_payloads[0])
        self.assertEqual(payload_dict["metadata"]["api_key"], "[REDACTED]")
        self.assertEqual(payload_dict["correlation_id"], "trace-101")

    def test_error_spike_alert_trigger(self):
        # 6 ERROR logs (>= 5 threshold) -> OBSERVABILITY_ERROR_SPIKE_ALERT!
        records = [
            RawLogRecord(
                subsystem="RISK_GATEWAY",
                level="ERROR",
                message="Margin check failed",
                correlation_id=f"trace-{i}"
            )
            for i in range(6)
        ]
        report = self.engine.process_and_aggregate_logs(records)

        self.assertEqual(report.status, "OBSERVABILITY_ERROR_SPIKE_ALERT")
        self.assertTrue(report.has_error_spike_alert)
        self.assertEqual(report.error_logs_count, 6)

if __name__ == '__main__':
    unittest.main()

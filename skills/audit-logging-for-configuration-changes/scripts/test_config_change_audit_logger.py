import unittest
import json
from config_change_audit_logger import ConfigurationAuditLogger, ConfigChangeRequest

class TestConfigChangeAuditLogger(unittest.TestCase):
    def setUp(self):
        self.logger = ConfigurationAuditLogger()

    def test_valid_change_request(self):
        request = ConfigChangeRequest(
            parameter_name="max_position_size",
            old_value=1000,
            new_value=5000,
            user_id="trader_smith",
            justification="Expanding capacity for earnings season."
        )
        record = self.logger.process_change_request(request)
        
        self.assertTrue(record.is_approved)
        self.assertEqual(record.user_id, "trader_smith")
        
        # Verify JSON serialization for SIEM ingestion
        json_str = record.to_json()
        parsed = json.loads(json_str)
        self.assertEqual(parsed["parameter_name"], "max_position_size")
        self.assertTrue("timestamp_utc" in parsed)

    def test_missing_justification_rejected(self):
        request = ConfigChangeRequest(
            parameter_name="max_position_size",
            old_value=1000,
            new_value=5000,
            user_id="trader_smith",
            justification="ok" # Too short / insufficient
        )
        record = self.logger.process_change_request(request)
        self.assertFalse(record.is_approved)

    def test_identical_values_rejected(self):
        request = ConfigChangeRequest(
            parameter_name="risk_multiplier",
            old_value=1.5,
            new_value=1.5,
            user_id="risk_admin",
            justification="Updating risk multiplier."
        )
        record = self.logger.process_change_request(request)
        self.assertFalse(record.is_approved)

if __name__ == '__main__':
    unittest.main()

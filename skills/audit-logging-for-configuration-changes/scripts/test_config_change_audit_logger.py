"""
Tests for audit-logging-for-configuration-changes.

Covers: valid changes, validation rejection paths (justification, user_id,
no-op), tamper-evident hash chaining integrity, sequence monotonicity,
deterministic JSON serialization, and non-JSON-serializable config values.
"""
import json
import unittest

from config_change_audit_logger import (
    ConfigChangeRecord,
    ConfigChangeRequest,
    ConfigurationAuditLogger,
)


class TestConfigChangeAuditLogger(unittest.TestCase):
    def setUp(self):
        self.logger = ConfigurationAuditLogger()

    def _valid_request(self, **overrides):
        defaults = dict(
            parameter_name="max_position_size",
            old_value=1000,
            new_value=5000,
            user_id="trader_smith",
            justification="Expanding capacity for earnings season.",
        )
        defaults.update(overrides)
        return ConfigChangeRequest(**defaults)

    # --- Valid path ----------------------------------------------------------
    def test_valid_change_request(self):
        request = self._valid_request()
        record = self.logger.process_change_request(request)

        self.assertTrue(record.is_approved)
        self.assertEqual(record.user_id, "trader_smith")
        self.assertEqual(record.rejection_reason, "")
        self.assertEqual(record.sequence_number, 1)
        self.assertEqual(record.prev_hash, "")
        self.assertTrue(record.record_hash)

        # JSON serialization for SIEM ingestion round-trips with all fields.
        parsed = json.loads(record.to_json())
        self.assertEqual(parsed["parameter_name"], "max_position_size")
        self.assertIn("timestamp_utc", parsed)
        self.assertIn("record_hash", parsed)
        self.assertIn("sequence_number", parsed)

    # --- Validation rejections ----------------------------------------------
    def test_missing_justification_rejected(self):
        request = self._valid_request(justification="ok")  # < MIN_JUSTIFICATION_LENGTH
        record = self.logger.process_change_request(request)
        self.assertFalse(record.is_approved)
        self.assertEqual(record.rejection_reason, "missing or insufficient justification")

    def test_whitespace_only_justification_rejected(self):
        request = self._valid_request(justification="      ")
        record = self.logger.process_change_request(request)
        self.assertFalse(record.is_approved)
        self.assertEqual(record.rejection_reason, "missing or insufficient justification")

    def test_empty_justification_rejected(self):
        request = self._valid_request(justification="")
        record = self.logger.process_change_request(request)
        self.assertFalse(record.is_approved)

    def test_empty_user_id_rejected(self):
        request = self._valid_request(user_id="")
        record = self.logger.process_change_request(request)
        self.assertFalse(record.is_approved)
        self.assertEqual(record.rejection_reason, "missing user_id")

    def test_whitespace_user_id_rejected(self):
        request = self._valid_request(user_id="   ")
        record = self.logger.process_change_request(request)
        self.assertFalse(record.is_approved)
        self.assertEqual(record.rejection_reason, "missing user_id")

    def test_identical_values_rejected(self):
        request = self._valid_request(
            parameter_name="risk_multiplier",
            old_value=1.5,
            new_value=1.5,
            user_id="risk_admin",
            justification="Updating risk multiplier.",
        )
        record = self.logger.process_change_request(request)
        self.assertFalse(record.is_approved)
        self.assertEqual(record.rejection_reason, "new value identical to old value")

    # --- Hash chaining + sequence integrity ---------------------------------
    def test_sequence_numbers_are_monotonic(self):
        r1 = self.logger.process_change_request(self._valid_request(new_value=1100))
        r2 = self.logger.process_change_request(self._valid_request(new_value=1200))
        r3 = self.logger.process_change_request(self._valid_request(new_value=1300))
        self.assertEqual([r1.sequence_number, r2.sequence_number, r3.sequence_number], [1, 2, 3])

    def test_prev_hash_chains_to_previous_record_hash(self):
        r1 = self.logger.process_change_request(self._valid_request(new_value=1100))
        r2 = self.logger.process_change_request(self._valid_request(new_value=1200))
        self.assertEqual(r1.prev_hash, "")
        self.assertEqual(r2.prev_hash, r1.record_hash)

    def test_record_hash_is_recomputable(self):
        """compute_hash must match the stored hash (independent tamper check)."""
        record = self.logger.process_change_request(self._valid_request())
        self.assertEqual(record.record_hash, record.compute_hash())

    def test_hash_changes_when_field_changes(self):
        """Tampering with any audited field must change the hash."""
        r1 = self.logger.process_change_request(self._valid_request(new_value=1100, justification="Reason one here."))
        original_hash = r1.record_hash
        r1.new_value = 9999  # mutate after emission
        self.assertNotEqual(original_hash, r1.compute_hash())

    def test_rejected_attempts_also_get_sequence_and_hash(self):
        """Rejected attempts are still auditable and chained."""
        r_approved = self.logger.process_change_request(self._valid_request(new_value=1100))
        r_rejected = self.logger.process_change_request(self._valid_request(justification="no"))
        self.assertTrue(r_approved.is_approved)
        self.assertFalse(r_rejected.is_approved)
        self.assertEqual(r_rejected.sequence_number, 2)
        self.assertEqual(r_rejected.prev_hash, r_approved.record_hash)
        self.assertTrue(r_rejected.record_hash)

    def test_records_property_returns_independent_copy(self):
        self.logger.process_change_request(self._valid_request())
        snapshot = self.logger.records
        snapshot.clear()
        self.assertEqual(len(self.logger.records), 1)

    # --- Determinism + robustness ------------------------------------------
    def test_json_serialization_is_deterministic(self):
        record = self.logger.process_change_request(self._valid_request())
        self.assertEqual(record.to_json(), record.to_json())
        # Sorted keys: 'record_hash' appears before 'rejection_reason' etc.
        parsed = json.loads(record.to_json())
        self.assertEqual(list(parsed.keys()), sorted(parsed.keys()))

    def test_non_serializable_value_does_not_raise(self):
        """Config values such as sets must not drop the audit record."""
        request = self._valid_request(old_value={1, 2, 3}, new_value={1, 2, 3, 4})
        record = self.logger.process_change_request(request)
        self.assertTrue(record.is_approved)
        # Should serialize without raising.
        parsed = json.loads(record.to_json())
        self.assertIn("new_value", parsed)

    def test_environment_is_captured(self):
        request = self._valid_request(environment="staging")
        record = self.logger.process_change_request(request)
        self.assertEqual(record.environment, "staging")

    def test_logger_default_environment_used_when_request_omits(self):
        request = ConfigChangeRequest(
            parameter_name="max_position_size",
            old_value=1000,
            new_value=2000,
            user_id="trader_smith",
            justification="Capacity increase.",
            environment="",
        )
        logger_prod = ConfigurationAuditLogger(environment="production")
        record = logger_prod.process_change_request(request)
        self.assertEqual(record.environment, "production")


if __name__ == "__main__":
    unittest.main()

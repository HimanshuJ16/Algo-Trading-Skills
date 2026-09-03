"""
Tests for audit-logging-for-configuration-changes.

Covers: valid changes, validation rejection paths (parameter name, justification,
user_id, no-op), configurable justification floor, tamper-evident hash chaining,
chain verification against edits / middle deletions / reordering / relinking,
offline verification from emitted JSON, chain-head publication, sequence
monotonicity under concurrency, deterministic JSON serialization, and config
values that resist serialization or comparison.
"""
import json
import sys
import threading
import unittest

from config_change_audit_logger import (
    GENESIS_PREV_HASH,
    MIN_JUSTIFICATION_LENGTH,
    REASON_JUSTIFICATION,
    REASON_NO_OP,
    REASON_PARAMETER_NAME,
    REASON_USER_ID,
    ConfigChangeRecord,
    ConfigChangeRequest,
    ConfigurationAuditLogger,
    verify_chain,
)


class _RaisingEq:
    """A config value whose comparison is undecidable, as a numpy array's is."""

    def __eq__(self, other):
        raise TypeError("ambiguous comparison")

    def __hash__(self):
        return 0

    def __str__(self):
        return "<raising-eq>"


class _RaisingStr:
    """A config value that cannot be rendered at all."""

    def __str__(self):
        raise RuntimeError("cannot render")

    __repr__ = __str__


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
        self.assertEqual(record.prev_hash, GENESIS_PREV_HASH)
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
        self.assertEqual(record.rejection_reason, REASON_JUSTIFICATION)

    def test_whitespace_only_justification_rejected(self):
        request = self._valid_request(justification="      ")
        record = self.logger.process_change_request(request)
        self.assertFalse(record.is_approved)
        self.assertEqual(record.rejection_reason, REASON_JUSTIFICATION)

    def test_empty_justification_rejected(self):
        request = self._valid_request(justification="")
        record = self.logger.process_change_request(request)
        self.assertFalse(record.is_approved)

    def test_empty_user_id_rejected(self):
        request = self._valid_request(user_id="")
        record = self.logger.process_change_request(request)
        self.assertFalse(record.is_approved)
        self.assertEqual(record.rejection_reason, REASON_USER_ID)

    def test_whitespace_user_id_rejected(self):
        request = self._valid_request(user_id="   ")
        record = self.logger.process_change_request(request)
        self.assertFalse(record.is_approved)
        self.assertEqual(record.rejection_reason, REASON_USER_ID)

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
        self.assertEqual(record.rejection_reason, REASON_NO_OP)

    def test_empty_parameter_name_rejected(self):
        """A record that cannot name what changed cannot support supervision."""
        record = self.logger.process_change_request(self._valid_request(parameter_name=""))
        self.assertFalse(record.is_approved)
        self.assertEqual(record.rejection_reason, REASON_PARAMETER_NAME)

    def test_whitespace_parameter_name_rejected(self):
        record = self.logger.process_change_request(self._valid_request(parameter_name="   "))
        self.assertFalse(record.is_approved)
        self.assertEqual(record.rejection_reason, REASON_PARAMETER_NAME)

    def test_rejected_parameter_name_is_still_recorded(self):
        """A rejected attempt is auditable evidence, not a discarded request."""
        self.logger.process_change_request(self._valid_request(parameter_name=""))
        self.assertEqual(len(self.logger.records), 1)

    # --- Configurable justification floor ------------------------------------
    def test_justification_floor_is_configurable(self):
        """The default length is an engineering choice, not a regulatory one."""
        strict = ConfigurationAuditLogger(min_justification_chars=40)
        record = strict.process_change_request(self._valid_request(justification="Too short."))
        self.assertFalse(record.is_approved)
        self.assertEqual(record.rejection_reason, REASON_JUSTIFICATION)

    def test_justification_floor_of_zero_accepts_empty_justification(self):
        permissive = ConfigurationAuditLogger(min_justification_chars=0)
        record = permissive.process_change_request(self._valid_request(justification=""))
        self.assertTrue(record.is_approved)

    def test_negative_justification_floor_rejected(self):
        with self.assertRaises(ValueError):
            ConfigurationAuditLogger(min_justification_chars=-1)

    def test_default_floor_constant_is_five(self):
        self.assertEqual(MIN_JUSTIFICATION_LENGTH, 5)

    # --- Hash chaining + sequence integrity ---------------------------------
    def test_sequence_numbers_are_monotonic(self):
        r1 = self.logger.process_change_request(self._valid_request(new_value=1100))
        r2 = self.logger.process_change_request(self._valid_request(new_value=1200))
        r3 = self.logger.process_change_request(self._valid_request(new_value=1300))
        self.assertEqual([r1.sequence_number, r2.sequence_number, r3.sequence_number], [1, 2, 3])

    def test_prev_hash_chains_to_previous_record_hash(self):
        r1 = self.logger.process_change_request(self._valid_request(new_value=1100))
        r2 = self.logger.process_change_request(self._valid_request(new_value=1200))
        self.assertEqual(r1.prev_hash, GENESIS_PREV_HASH)
        self.assertEqual(r2.prev_hash, r1.record_hash)

    def test_record_hash_is_recomputable(self):
        """compute_hash must match the stored hash (independent tamper check)."""
        record = self.logger.process_change_request(self._valid_request())
        self.assertEqual(record.record_hash, record.compute_hash())

    def test_hash_changes_when_field_changes(self):
        """Tampering with any audited field must change the hash."""
        r1 = self.logger.process_change_request(
            self._valid_request(new_value=1100, justification="Reason one here.")
        )
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

    def test_records_property_returns_an_independent_list(self):
        """The list is copied; the records in it are deliberately not."""
        self.logger.process_change_request(self._valid_request())
        snapshot = self.logger.records
        snapshot.clear()
        self.assertEqual(len(self.logger.records), 1)
        self.assertIs(self.logger.records[0], self.logger.records[0])

    # --- Chain verification --------------------------------------------------
    def _three_record_chain(self):
        for value in (1100, 1200, 1300):
            self.logger.process_change_request(self._valid_request(new_value=value))
        return self.logger.records

    def test_verify_chain_accepts_an_intact_chain(self):
        self.assertEqual(verify_chain(self._three_record_chain()), (True, None))

    def test_verify_chain_accepts_an_empty_chain(self):
        self.assertEqual(verify_chain([]), (True, None))

    def test_verify_integrity_accepts_an_intact_logger(self):
        self._three_record_chain()
        self.assertEqual(self.logger.verify_integrity(), (True, None))

    def test_verify_chain_detects_an_in_place_edit(self):
        records = self._three_record_chain()
        records[1].new_value = 999_999
        is_intact, reason = verify_chain(records)
        self.assertFalse(is_intact)
        self.assertIn("record 2", reason)
        self.assertIn("does not match its content", reason)

    def test_verify_integrity_detects_an_in_place_edit(self):
        self._three_record_chain()
        # The list is copied but the records in it are live objects, so mutating
        # one through the public accessor is exactly the tampering to detect.
        self.logger.records[1].user_id = "someone_else"
        is_intact, reason = self.logger.verify_integrity()
        self.assertFalse(is_intact)
        self.assertIn("record 2", reason)

    def test_verify_chain_detects_a_deletion_from_the_middle(self):
        records = self._three_record_chain()
        del records[1]
        is_intact, reason = verify_chain(records)
        self.assertFalse(is_intact)
        self.assertIn("expected 2", reason)

    def test_verify_chain_detects_reordering(self):
        records = self._three_record_chain()
        records[0], records[1] = records[1], records[0]
        is_intact, reason = verify_chain(records)
        self.assertFalse(is_intact)
        self.assertIn("sequence_number", reason)

    def test_verify_chain_detects_an_edit_whose_own_hash_was_recomputed(self):
        """The chain, not the per-record hash, is what defeats a careful edit.

        An attacker who edits record 2 and recomputes its ``record_hash`` leaves
        a record that is internally consistent. Only the link from record 3
        exposes it. This test fails if the chain check is ever reduced to a
        per-record hash check.
        """
        records = self._three_record_chain()
        records[1].new_value = 999_999
        records[1].record_hash = records[1].compute_hash()
        is_intact, reason = verify_chain(records)
        self.assertFalse(is_intact)
        self.assertIn("record 3", reason)
        self.assertIn("does not link to its predecessor", reason)

    def test_verify_chain_rejects_a_window_when_genesis_is_expected(self):
        records = self._three_record_chain()[1:]
        is_intact, reason = verify_chain(records)
        self.assertFalse(is_intact)
        self.assertIn("expected 1", reason)

    def test_verify_chain_accepts_a_window_when_genesis_is_not_expected(self):
        records = self._three_record_chain()[1:]
        self.assertEqual(verify_chain(records, expect_genesis=False), (True, None))

    def test_verify_chain_detects_an_edit_inside_an_unanchored_window(self):
        records = self._three_record_chain()[1:]
        records[1].justification = "Rewritten after the fact."
        is_intact, _ = verify_chain(records, expect_genesis=False)
        self.assertFalse(is_intact)

    def test_truncating_the_newest_records_is_not_detected_by_the_chain(self):
        """A documented limit: only an externally held chain head reveals this.

        The engine must not be described as detecting tail truncation, so the
        limit is pinned here rather than left to the reader.
        """
        records = self._three_record_chain()
        head_before = self.logger.chain_head_hash
        truncated = records[:2]
        self.assertEqual(verify_chain(truncated), (True, None))
        # The externally published head is what exposes the loss.
        self.assertNotEqual(truncated[-1].record_hash, head_before)

    # --- Chain head ----------------------------------------------------------
    def test_chain_head_is_genesis_before_the_first_record(self):
        self.assertEqual(self.logger.chain_head_hash, GENESIS_PREV_HASH)

    def test_chain_head_tracks_the_most_recent_record(self):
        records = self._three_record_chain()
        self.assertEqual(self.logger.chain_head_hash, records[-1].record_hash)

    # --- Offline verification from emitted JSON ------------------------------
    def test_chain_verifies_after_a_round_trip_through_emitted_json(self):
        """An examiner works from archived log lines, not from live objects."""
        lines = [record.to_json() for record in self._three_record_chain()]
        rebuilt = [ConfigChangeRecord.from_json(line) for line in lines]
        self.assertEqual(verify_chain(rebuilt), (True, None))

    def test_round_trip_preserves_every_record_hash(self):
        for record in self._three_record_chain():
            rebuilt = ConfigChangeRecord.from_json(record.to_json())
            self.assertEqual(rebuilt.record_hash, record.record_hash)
            self.assertEqual(rebuilt.compute_hash(), record.record_hash)

    def test_tampered_archive_line_fails_verification(self):
        record = self.logger.process_change_request(self._valid_request())
        payload = json.loads(record.to_json())
        payload["new_value"] = 10 ** 9
        rebuilt = ConfigChangeRecord.from_mapping(payload)
        is_intact, reason = verify_chain([rebuilt])
        self.assertFalse(is_intact)
        self.assertIn("does not match its content", reason)

    def test_from_mapping_rejects_a_missing_field(self):
        record = self.logger.process_change_request(self._valid_request())
        payload = json.loads(record.to_json())
        del payload["user_id"]
        with self.assertRaises(ValueError) as ctx:
            ConfigChangeRecord.from_mapping(payload)
        self.assertIn("user_id", str(ctx.exception))

    def test_from_mapping_rejects_an_unrecognised_field(self):
        """Extra keys are data the hash does not cover; do not ignore them."""
        record = self.logger.process_change_request(self._valid_request())
        payload = json.loads(record.to_json())
        payload["approved_by"] = "someone"
        with self.assertRaises(ValueError) as ctx:
            ConfigChangeRecord.from_mapping(payload)
        self.assertIn("approved_by", str(ctx.exception))

    def test_from_json_rejects_a_non_object_line(self):
        with self.assertRaises(ValueError):
            ConfigChangeRecord.from_json("[1, 2, 3]")

    # --- Concurrency ---------------------------------------------------------
    def test_concurrent_requests_do_not_fork_the_chain(self):
        """Every UI, CLI and API path routes through one logger instance.

        Web handlers are concurrent, so unguarded sequence assignment would
        duplicate sequence numbers and fork the chain. The switch interval is
        shortened to widen the interleaving window.
        """
        threads_count, per_thread = 8, 25
        barrier = threading.Barrier(threads_count)

        def worker(worker_id):
            barrier.wait()
            for index in range(per_thread):
                self.logger.process_change_request(
                    self._valid_request(
                        parameter_name="limit_{}".format(worker_id),
                        new_value=index + 1,
                        justification="Concurrent adjustment under test.",
                    )
                )

        original_interval = sys.getswitchinterval()
        sys.setswitchinterval(1e-6)
        try:
            threads = [threading.Thread(target=worker, args=(i,)) for i in range(threads_count)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
        finally:
            sys.setswitchinterval(original_interval)

        records = self.logger.records
        total = threads_count * per_thread
        self.assertEqual(len(records), total)
        self.assertEqual(
            [record.sequence_number for record in records], list(range(1, total + 1))
        )
        self.assertEqual(self.logger.verify_integrity(), (True, None))

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

    def test_unrenderable_value_does_not_drop_the_record(self):
        """A value whose __str__ raises must still leave an audit trail."""
        request = self._valid_request(old_value=_RaisingStr(), new_value=_RaisingStr())
        record = self.logger.process_change_request(request)
        parsed = json.loads(record.to_json())
        self.assertIn("unserializable", parsed["new_value"])
        self.assertEqual(record.record_hash, record.compute_hash())

    def test_undecidable_comparison_is_recorded_as_a_real_change(self):
        """An ambiguous __eq__ must not escape and drop the audit record."""
        request = self._valid_request(old_value=_RaisingEq(), new_value=_RaisingEq())
        record = self.logger.process_change_request(request)
        self.assertTrue(record.is_approved)
        self.assertEqual(len(self.logger.records), 1)

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

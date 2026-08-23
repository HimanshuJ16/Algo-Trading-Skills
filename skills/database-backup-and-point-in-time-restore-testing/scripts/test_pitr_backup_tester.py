import unittest

from pitr_backup_tester import (
    BaseSnapshot,
    ExpectedDatabaseState,
    PitrBackupTesterEngine,
    PitrRestoreError,
    WalRecord,
)


class TestPitrBackupTesterEngine(unittest.TestCase):

    def setUp(self):
        self.engine = PitrBackupTesterEngine(
            database_name="TimescaleDB_Prod", max_rpo_sec=60.0, max_rto_min=15.0
        )

        # Snapshot completed at t = 1000ms holding 1000 orders / 50 positions.
        self.snapshots = [
            BaseSnapshot("SNAP_01", 1000.0, {"trade_orders": 1000, "positions": 50}, "checksum_base_1000")
        ]

        # Contiguous WAL: 5 inserts spanning t = 1100ms to 2500ms.
        self.wal_logs = [
            WalRecord(1, 1100.0, "trade_orders", "INSERT", "ORD_1001", "buy AAPL 100"),
            WalRecord(2, 1200.0, "trade_orders", "INSERT", "ORD_1002", "sell MSFT 50"),
            WalRecord(3, 1500.0, "trade_orders", "INSERT", "ORD_1003", "buy GOOGL 10"),
            WalRecord(4, 1800.0, "trade_orders", "INSERT", "ORD_1004", "buy AMZN 20"),   # exactly on target
            WalRecord(5, 2500.0, "trade_orders", "INSERT", "ORD_1005", "corrupt trade"),  # after target
        ]

    def _restore(self, **overrides):
        kwargs = dict(
            base_snapshots=self.snapshots,
            wal_logs=self.wal_logs,
            target_timestamp_ms=1800.0,
            simulated_restore_time_sec=300.0,
        )
        kwargs.update(overrides)
        return self.engine.perform_pitr_restore(**kwargs)

    # --- restoration semantics ----------------------------------------------

    def test_pitr_restoration_to_exact_target_timestamp(self):
        # Target t = 1800ms replays LSNs 1-4 (inclusive target) -> 1004 orders.
        report = self._restore()

        self.assertEqual(report.snapshot_used_id, "SNAP_01")
        self.assertEqual(report.wal_records_replayed_count, 4)
        self.assertEqual(report.restored_table_rows["trade_orders"], 1004)
        self.assertEqual(report.restored_table_rows["positions"], 50)
        self.assertTrue(report.recovery_target_reached)
        self.assertTrue(report.is_rpo_compliant)
        self.assertTrue(report.is_rto_compliant)
        self.assertTrue(report.is_restoration_successful)

    def test_boundary_record_is_included_matching_recovery_target_inclusive(self):
        # PostgreSQL's recovery_target_inclusive defaults to on: a commit at
        # exactly the target time is replayed. Shifting the target 1ms earlier
        # must drop exactly that record.
        inclusive = self._restore(target_timestamp_ms=1800.0)
        exclusive = self._restore(target_timestamp_ms=1799.0)

        self.assertEqual(inclusive.wal_records_replayed_count, 4)
        self.assertEqual(exclusive.wal_records_replayed_count, 3)
        self.assertEqual(exclusive.restored_table_rows["trade_orders"], 1003)

    def test_records_after_target_are_not_replayed(self):
        # LSN 5 at t = 2500ms is the "corrupt trade" being recovered away from.
        report = self._restore()
        self.assertEqual(report.wal_records_available_count, 5)
        self.assertEqual(report.wal_records_replayed_count, 4)
        self.assertEqual(report.last_recoverable_timestamp_ms, 1800.0)

    def test_unordered_input_is_sorted_before_replay(self):
        shuffled = [self.wal_logs[i] for i in (3, 0, 4, 2, 1)]
        self.assertEqual(
            self._restore(wal_logs=shuffled).restored_checksum,
            self._restore().restored_checksum,
        )

    def test_snapshot_selection_picks_latest_before_target(self):
        snapshots = self.snapshots + [
            BaseSnapshot("SNAP_02", 1500.0, {"trade_orders": 1002, "positions": 50}, "checksum_base_1500"),
            BaseSnapshot("SNAP_03", 2400.0, {"trade_orders": 1004, "positions": 50}, "checksum_base_2400"),
        ]
        report = self._restore(base_snapshots=snapshots)

        # SNAP_03 completes after the target and is ineligible; from SNAP_02
        # only LSN 4 (t=1800ms) remains to replay, so 1002 + 1 = 1003 orders.
        self.assertEqual(report.snapshot_used_id, "SNAP_02")
        self.assertEqual(report.wal_records_replayed_count, 1)
        self.assertEqual(report.restored_table_rows["trade_orders"], 1003)

    def test_update_changes_checksum_but_not_row_count(self):
        wal = [WalRecord(1, 1100.0, "trade_orders", "UPDATE", "ORD_0900", "amend qty 100->120")]
        report = self._restore(wal_logs=wal, target_timestamp_ms=1100.0)

        self.assertEqual(report.restored_table_rows["trade_orders"], 1000)
        self.assertEqual(report.wal_records_replayed_count, 1)
        self.assertNotEqual(
            report.restored_checksum,
            self._restore(wal_logs=[], target_timestamp_ms=1100.0).restored_checksum,
        )

    def test_delete_decrements_and_flags_underflow(self):
        wal = [
            WalRecord(1, 1100.0, "trade_orders", "DELETE", "ORD_0001", ""),
            WalRecord(2, 1200.0, "audit_log", "DELETE", "AUD_0001", ""),  # table absent from snapshot
        ]
        report = self._restore(wal_logs=wal, target_timestamp_ms=1200.0)

        self.assertEqual(report.restored_table_rows["trade_orders"], 999)
        self.assertEqual(report.restored_table_rows["audit_log"], 0)
        self.assertTrue(any("REPLAY ANOMALY" in f for f in report.findings))

    # --- RPO / RTO audit -----------------------------------------------------

    def test_rpo_measures_data_loss_when_archive_stops_short_of_target(self):
        # Regression: the archive stopped advancing 50 minutes before the target.
        # This must be reported as ~3000s of data loss and a failed recovery --
        # the earlier implementation reported RPO 0.0s and "successful".
        snapshots = [BaseSnapshot("SNAP_A", 0.0, {"trade_orders": 1000}, "c0")]
        wal = [WalRecord(1, 600_000.0, "trade_orders", "INSERT", "ORD_1", "buy AAPL 1")]
        report = self.engine.perform_pitr_restore(
            base_snapshots=snapshots, wal_logs=wal, target_timestamp_ms=3_600_000.0
        )

        self.assertAlmostEqual(report.rpo_seconds, 3000.0, places=3)
        self.assertFalse(report.recovery_target_reached)
        self.assertFalse(report.is_rpo_compliant)
        self.assertFalse(report.is_restoration_successful)
        self.assertTrue(any("RECOVERY TARGET UNREACHABLE" in f for f in report.findings))

    def test_rpo_is_zero_when_archive_covers_target(self):
        self.assertEqual(self._restore().rpo_seconds, 0.0)

    def test_rpo_boundary_exactly_at_objective_is_compliant(self):
        # Archive ends exactly 60s before the target, the stated objective.
        snapshots = [BaseSnapshot("SNAP_A", 0.0, {"trade_orders": 1}, "c0")]
        wal = [WalRecord(1, 40_000.0, "trade_orders", "INSERT", "ORD_1", "x")]
        report = self.engine.perform_pitr_restore(
            base_snapshots=snapshots, wal_logs=wal, target_timestamp_ms=100_000.0
        )

        self.assertEqual(report.rpo_seconds, 60.0)
        self.assertTrue(report.is_rpo_compliant)
        # Compliant RPO, but the target still was not reachable.
        self.assertFalse(report.recovery_target_reached)
        self.assertFalse(report.is_restoration_successful)

    def test_rto_breach_fails_the_drill(self):
        report = self._restore(simulated_restore_time_sec=1200.0)  # 20 minutes

        self.assertEqual(report.rto_minutes, 20.0)
        self.assertFalse(report.is_rto_compliant)
        self.assertFalse(report.is_restoration_successful)
        self.assertTrue(any("RTO BREACH" in f for f in report.findings))

    def test_rto_boundary_exactly_at_objective_is_compliant(self):
        report = self._restore(simulated_restore_time_sec=900.0)  # exactly 15 minutes
        self.assertEqual(report.rto_minutes, 15.0)
        self.assertTrue(report.is_rto_compliant)
        self.assertTrue(report.is_restoration_successful)

    # --- WAL continuity ------------------------------------------------------

    def test_wal_gap_truncates_replay_and_fails_the_drill(self):
        gapped = [w for w in self.wal_logs if w.lsn_id != 3]  # LSN 3 missing
        report = self._restore(wal_logs=gapped)

        self.assertTrue(report.wal_gap_detected)
        self.assertEqual(report.first_missing_lsn, 3)
        self.assertEqual(report.wal_records_replayed_count, 2)  # only LSNs 1-2 survive
        self.assertEqual(report.restored_table_rows["trade_orders"], 1002)
        self.assertFalse(report.recovery_target_reached)
        self.assertAlmostEqual(report.rpo_seconds, 0.6, places=3)  # (1800 - 1200)/1000
        self.assertFalse(report.is_restoration_successful)

    def test_gap_immediately_after_snapshot_is_caught_when_snapshot_records_its_lsn(self):
        # LSNs 1-2 were lost between the backup and the surviving archive. The
        # surviving records are contiguous among themselves, so only the
        # snapshot's own LSN watermark exposes the hole.
        snapshots = [
            BaseSnapshot("SNAP_01", 1000.0, {"trade_orders": 1000}, "c", last_lsn_included=0)
        ]
        report = self._restore(
            base_snapshots=snapshots, wal_logs=[w for w in self.wal_logs if w.lsn_id >= 3]
        )

        self.assertTrue(report.wal_gap_detected)
        self.assertEqual(report.first_missing_lsn, 1)
        self.assertEqual(report.wal_records_replayed_count, 0)
        self.assertFalse(report.is_restoration_successful)

    def test_snapshot_lsn_watermark_matching_the_archive_passes(self):
        snapshots = [
            BaseSnapshot("SNAP_01", 1000.0, {"trade_orders": 1000}, "c", last_lsn_included=0)
        ]
        report = self._restore(base_snapshots=snapshots)

        self.assertFalse(report.wal_gap_detected)
        self.assertEqual(report.wal_records_replayed_count, 4)

    def test_expectation_asserting_nothing_raises(self):
        with self.assertRaises(PitrRestoreError):
            self._restore(expected_state=ExpectedDatabaseState(table_rows={}))

    def test_gap_check_can_be_disabled_for_sparse_lsn_schemes(self):
        gapped = [w for w in self.wal_logs if w.lsn_id != 3]
        report = self._restore(wal_logs=gapped, require_contiguous_lsn=False)

        self.assertFalse(report.wal_gap_detected)
        self.assertEqual(report.wal_records_replayed_count, 3)
        self.assertTrue(report.is_restoration_successful)

    # --- integrity verification ---------------------------------------------

    def test_matching_expected_state_verifies_integrity(self):
        expected = ExpectedDatabaseState(table_rows={"trade_orders": 1004, "positions": 50})
        report = self._restore(expected_state=expected)

        self.assertTrue(report.integrity_verified)
        self.assertTrue(report.is_restoration_successful)

    def test_row_count_mismatch_fails_the_drill(self):
        # Broker reconciliation says 1005 orders should exist at the target.
        expected = ExpectedDatabaseState(table_rows={"trade_orders": 1005})
        report = self._restore(expected_state=expected)

        self.assertFalse(report.integrity_verified)
        self.assertFalse(report.is_restoration_successful)
        self.assertTrue(any("ROW PARITY" in f for f in report.findings))

    def test_checksum_mismatch_fails_the_drill(self):
        expected = ExpectedDatabaseState(
            table_rows={"trade_orders": 1004}, state_checksum="0" * 64
        )
        report = self._restore(expected_state=expected)

        self.assertFalse(report.integrity_verified)
        self.assertTrue(any("CHECKSUM MISMATCH" in f for f in report.findings))

    def test_altered_payload_changes_the_checksum(self):
        tampered = list(self.wal_logs)
        tampered[1] = WalRecord(2, 1200.0, "trade_orders", "INSERT", "ORD_1002", "sell MSFT 5000")
        self.assertNotEqual(
            self._restore(wal_logs=tampered).restored_checksum,
            self._restore().restored_checksum,
        )

    def test_omitting_expected_state_leaves_integrity_unverified(self):
        report = self._restore()

        self.assertIsNone(report.integrity_verified)
        self.assertTrue(any("INTEGRITY NOT VERIFIED" in f for f in report.findings))

    # --- input validation ----------------------------------------------------

    def test_no_snapshot_before_target_raises(self):
        with self.assertRaises(PitrRestoreError):
            self._restore(target_timestamp_ms=500.0)

    def test_empty_snapshot_list_raises(self):
        with self.assertRaises(PitrRestoreError):
            self._restore(base_snapshots=[])

    def test_unknown_operation_raises_rather_than_being_ignored(self):
        with self.assertRaises(PitrRestoreError):
            self._restore(wal_logs=[WalRecord(1, 1100.0, "trade_orders", "insert", "ORD_1", "x")])

    def test_duplicate_lsn_raises(self):
        dup = list(self.wal_logs) + [
            WalRecord(2, 1250.0, "trade_orders", "INSERT", "ORD_DUP", "duplicate lsn")
        ]
        with self.assertRaises(PitrRestoreError):
            self._restore(wal_logs=dup)

    def test_lsn_order_contradicting_commit_time_raises(self):
        backwards = [
            WalRecord(1, 1500.0, "trade_orders", "INSERT", "ORD_1", "x"),
            WalRecord(2, 1100.0, "trade_orders", "INSERT", "ORD_2", "y"),
        ]
        with self.assertRaises(PitrRestoreError):
            self._restore(wal_logs=backwards)

    def test_non_finite_target_raises(self):
        with self.assertRaises(PitrRestoreError):
            self._restore(target_timestamp_ms=float("nan"))

    def test_negative_restore_duration_raises(self):
        with self.assertRaises(PitrRestoreError):
            self._restore(simulated_restore_time_sec=-1.0)

    def test_invalid_sla_configuration_raises(self):
        with self.assertRaises(PitrRestoreError):
            PitrBackupTesterEngine(max_rpo_sec=-1.0)
        with self.assertRaises(PitrRestoreError):
            PitrBackupTesterEngine(max_rto_min=float("inf"))


if __name__ == '__main__':
    unittest.main()

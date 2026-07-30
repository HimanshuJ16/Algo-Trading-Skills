import unittest
from pitr_backup_tester import (
    PitrBackupTesterEngine, BaseSnapshot, WalRecord, PitrBackupAuditReport
)

class TestPitrBackupTesterEngine(unittest.TestCase):

    def setUp(self):
        self.engine = PitrBackupTesterEngine(database_name="TimescaleDB_Prod", max_rpo_sec=60.0, max_rto_min=15.0)

        # Snapshots: Snap_1 at t = 1000ms (1000 orders)
        self.snapshots = [
            BaseSnapshot("SNAP_01", 1000.0, {"trade_orders": 1000, "positions": 50}, "checksum_base_1000")
        ]

        # WAL Records: 5 inserts spanning t = 1100ms to 2000ms
        self.wal_logs = [
            WalRecord(1, 1100.0, "trade_orders", "INSERT", "ORD_1001", "buy AAPL 100"),
            WalRecord(2, 1200.0, "trade_orders", "INSERT", "ORD_1002", "sell MSFT 50"),
            WalRecord(3, 1500.0, "trade_orders", "INSERT", "ORD_1003", "buy GOOGL 10"),
            WalRecord(4, 1800.0, "trade_orders", "INSERT", "ORD_1004", "buy AMZN 20"), # Target boundary (t = 1800ms)
            WalRecord(5, 2500.0, "trade_orders", "INSERT", "ORD_1005", "corrupt trade") # After target (t = 2500ms)
        ]

    def test_pitr_restoration_to_exact_target_timestamp(self):
        # Target PITR restore at t = 1800ms -> Should replay WALs 1 to 4 (4 inserts) -> Total trade_orders = 1004
        report = self.engine.perform_pitr_restore(
            base_snapshots=self.snapshots,
            wal_logs=self.wal_logs,
            target_timestamp_ms=1800.0,
            simulated_restore_time_sec=300.0 # 5 min RTO
        )

        self.assertEqual(report.snapshot_used_id, "SNAP_01")
        self.assertEqual(report.wal_records_replayed_count, 4)
        self.assertEqual(report.restored_table_rows["trade_orders"], 1004)
        self.assertTrue(report.is_rpo_compliant)
        self.assertTrue(report.is_rto_compliant)
        self.assertTrue(report.is_restoration_successful)

if __name__ == '__main__':
    unittest.main()

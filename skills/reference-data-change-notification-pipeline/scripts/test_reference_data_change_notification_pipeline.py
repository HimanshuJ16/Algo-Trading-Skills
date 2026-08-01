import unittest
from reference_data_change_notification_pipeline import (
    ReferenceDataChangeNotificationPipelineEngine,
    ReferenceDataChangeNotificationPipelineConfig,
    ReferenceDataChangeReport
)

class TestReferenceDataChangeNotificationPipeline(unittest.TestCase):

    def setUp(self):
        self.engine = ReferenceDataChangeNotificationPipelineEngine(
            ReferenceDataChangeNotificationPipelineConfig()
        )

    def test_legacy_process(self):
        self.assertEqual(self.engine.process("test"), "TEST")

    def test_legacy_disabled(self):
        engine = ReferenceDataChangeNotificationPipelineEngine(
            ReferenceDataChangeNotificationPipelineConfig(enabled=False)
        )
        self.assertEqual(engine.process("test"), "")

    def test_critical_symbol_change_detected(self):
        before = {"symbol": "FB", "exchange": "NASDAQ", "lot_size": "100", "currency": "USD"}
        after = {"symbol": "META", "exchange": "NASDAQ", "lot_size": "100", "currency": "USD"}

        report = self.engine.detect_changes("INST_001", before, after)
        self.assertEqual(report.status, "CHANGES_DETECTED")
        self.assertEqual(report.total_changes, 1)
        self.assertEqual(report.critical_changes, 1)
        sym_notif = report.notifications[0]
        self.assertEqual(sym_notif.field_name, "symbol")
        self.assertEqual(sym_notif.old_value, "FB")
        self.assertEqual(sym_notif.new_value, "META")
        self.assertEqual(sym_notif.severity, "CRITICAL")

    def test_info_lot_size_change(self):
        before = {"symbol": "AAPL", "exchange": "NASDAQ", "lot_size": "100"}
        after = {"symbol": "AAPL", "exchange": "NASDAQ", "lot_size": "200"}

        report = self.engine.detect_changes("INST_002", before, after)
        self.assertEqual(report.status, "CHANGES_DETECTED")
        self.assertEqual(report.critical_changes, 0)
        self.assertEqual(report.info_changes, 1)
        self.assertEqual(report.notifications[0].severity, "INFO")

    def test_no_changes(self):
        snapshot = {"symbol": "TSLA", "exchange": "NASDAQ", "currency": "USD"}

        report = self.engine.detect_changes("INST_003", snapshot, snapshot)
        self.assertEqual(report.status, "NO_CHANGES")
        self.assertEqual(report.total_changes, 0)

if __name__ == '__main__':
    unittest.main()

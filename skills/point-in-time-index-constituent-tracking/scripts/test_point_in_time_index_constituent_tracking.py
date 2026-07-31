import unittest
from point_in_time_index_constituent_tracking import (
    PointInTimeIndexConstituentTrackingEngine,
    PointInTimeIndexConstituentTrackingConfig,
    IndexConstituentEvent,
    PITIndexQuery,
    PITIndexReport
)

class TestPointInTimeIndexConstituentTracking(unittest.TestCase):

    def setUp(self):
        self.config = PointInTimeIndexConstituentTrackingConfig(enabled=True, index_name="SP500")
        self.engine = PointInTimeIndexConstituentTrackingEngine(self.config)

    def test_process_legacy(self):
        self.assertEqual(self.engine.process("test"), "TEST")

    def test_disabled_legacy(self):
        engine = PointInTimeIndexConstituentTrackingEngine(
            PointInTimeIndexConstituentTrackingConfig(enabled=False)
        )
        self.assertEqual(engine.process("test"), "")

    def test_pit_universe_resolution_and_survivorship_bias(self):
        # ENRON added 1990-01-01, deleted 2001-12-02
        # TSLA added 2020-12-21
        events = [
            IndexConstituentEvent("SP500", "ENRON", "ADDITION", "1990-01-01"),
            IndexConstituentEvent("SP500", "ENRON", "DELETION", "2001-12-02"),
            IndexConstituentEvent("SP500", "AAPL", "ADDITION", "1982-11-30"),
            IndexConstituentEvent("SP500", "TSLA", "ADDITION", "2020-12-21"),
        ]
        self.engine.insert_events(events)

        # Query in 2000-06-01 -> Should include AAPL, ENRON; Exclude TSLA
        query_2000 = PITIndexQuery("SP500", "2000-06-01")
        report_2000 = self.engine.query_pit_universe(query_2000)
        self.assertIn("ENRON", report_2000.active_constituents)
        self.assertIn("AAPL", report_2000.active_constituents)
        self.assertNotIn("TSLA", report_2000.active_constituents)

        # Query in 2023-01-01 -> Should include AAPL, TSLA; Exclude ENRON
        query_2023 = PITIndexQuery("SP500", "2023-01-01")
        current_static_2026 = {"AAPL", "TSLA", "MSFT", "NVDA"} # Static current universe
        report_2000_audit = self.engine.query_pit_universe(query_2000, current_static_universe=current_static_2026)

        # In 2000, ENRON was active, but it is missing in current_static_2026 -> Ghost symbol detected!
        self.assertEqual(report_2000_audit.survivorship_bias_ghost_count, 1)

if __name__ == '__main__':
    unittest.main()

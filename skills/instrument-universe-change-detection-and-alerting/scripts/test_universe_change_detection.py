import unittest
from universe_change_detection import (
    UniverseChangeDetectionEngine, InstrumentRecord, UniverseChangeReport
)

class TestUniverseChangeDetectionEngine(unittest.TestCase):

    def setUp(self):
        self.engine = UniverseChangeDetectionEngine()

    def test_universe_change_detection(self):
        # Previous Snapshot: FB (BBG000MM82B1), AAPL (BBG000B9XRY4), TWTR (BBG006G7T421)
        prev_snapshot = [
            InstrumentRecord("BBG000MM82B1", "FB", "Meta Platforms Inc", "NASDAQ", "ACTIVE"),
            InstrumentRecord("BBG000B9XRY4", "AAPL", "Apple Inc", "NASDAQ", "ACTIVE"),
            InstrumentRecord("BBG006G7T421", "TWTR", "Twitter Inc", "NYSE", "ACTIVE")
        ]

        # Current Snapshot: META (BBG000MM82B1 - Ticker Rename), AAPL (BBG000B9XRY4), PLTR (BBG001S5N8V8 - Addition)
        # TWTR deleted!
        curr_snapshot = [
            InstrumentRecord("BBG000MM82B1", "META", "Meta Platforms Inc", "NASDAQ", "ACTIVE"),
            InstrumentRecord("BBG000B9XRY4", "AAPL", "Apple Inc", "NASDAQ", "ACTIVE"),
            InstrumentRecord("BBG001S5N8V8", "PLTR", "Palantir Technologies", "NYSE", "ACTIVE")
        ]

        report = self.engine.detect_universe_changes(prev_snapshot, curr_snapshot)

        self.assertEqual(report.status, "UNIVERSE_CHANGES_DETECTED")
        self.assertEqual(report.additions_count, 1) # PLTR
        self.assertEqual(report.deletions_count, 1) # TWTR
        self.assertEqual(report.renames_count, 1)   # FB -> META

        # Verify alert recommendations
        actions = {a.change_type: a.recommended_action for a in report.alerts}
        self.assertEqual(actions["ADDITION"], "INITIATE_COVERAGE")
        self.assertEqual(actions["DELETION"], "LIQUIDATE_POSITION_AND_UNSUBSCRIBE")
        self.assertEqual(actions["TICKER_RENAME"], "UPDATE_SYMBOL_MAPPER")

    def test_no_universe_changes(self):
        snapshot = [
            InstrumentRecord("BBG000B9XRY4", "AAPL", "Apple Inc", "NASDAQ", "ACTIVE")
        ]
        report = self.engine.detect_universe_changes(snapshot, snapshot)

        self.assertEqual(report.status, "UNIVERSE_NO_CHANGES")
        self.assertEqual(len(report.alerts), 0)

if __name__ == '__main__':
    unittest.main()

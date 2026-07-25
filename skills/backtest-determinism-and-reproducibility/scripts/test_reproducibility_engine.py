"""
Unit tests for backtest-determinism-and-reproducibility skill.
"""
import unittest
from reproducibility_engine import BacktestDeterminismEngine, TradeExecutionRecord


class TestBacktestDeterminismEngine(unittest.TestCase):

    def setUp(self):
        self.engine = BacktestDeterminismEngine(master_seed=42)

    def test_event_stream_sorting_determinism(self):
        unsorted = [
            {"timestamp": 100.5, "symbol": "MSFT", "sequence_id": 2},
            {"timestamp": 100.0, "symbol": "AAPL", "sequence_id": 1},
            {"timestamp": 100.0, "symbol": "AAPL", "sequence_id": 0},
        ]

        sorted_events = self.engine.sort_event_stream(unsorted)

        self.assertEqual(sorted_events[0]["symbol"], "AAPL")
        self.assertEqual(sorted_events[0]["sequence_id"], 0)
        self.assertEqual(sorted_events[1]["sequence_id"], 1)

    def test_bit_identical_audit_checksum_verification(self):
        trades1 = [
            TradeExecutionRecord(1000.0, "AAPL", "BUY", 100.0, 150.0),
            TradeExecutionRecord(1005.0, "AAPL", "SELL", 100.0, 155.0),
        ]
        trades2 = [
            TradeExecutionRecord(1000.0, "AAPL", "BUY", 100.0, 150.0),
            TradeExecutionRecord(1005.0, "AAPL", "SELL", 100.0, 155.0),
        ]

        report = self.engine.audit_reproducibility(trades1, trades2)

        self.assertTrue(report.is_bit_identical)
        self.assertEqual(len(report.trade_log_checksum), 64)  # SHA256 hex string length


if __name__ == "__main__":
    unittest.main()

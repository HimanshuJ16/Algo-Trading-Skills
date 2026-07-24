"""
Unit tests for tradestation-websocket-order-updates skill.

Tests:
1. Parsing real-time JSON stream messages and filtering heartbeat frames.
2. Deduplicating identical order fill events.
3. Executing REST gap-reconciliation query on reconnect.
4. Filtering duplicate events returned during REST catch-up queries.
"""
import time
import unittest
from tradestation_stream import TradeStationStreamManager, TradeStationStreamError


class TestTradeStationWebSocketStream(unittest.TestCase):

    def setUp(self):
        self.mgr = TradeStationStreamManager(account_id="SIM123456")

    def test_parse_valid_stream_message(self):
        msg = '{"OrderID": "TS999", "Status": "FLL", "FilledQuantity": 100, "AveragePrice": 150.25}'
        update = self.mgr.parse_stream_message(msg)
        self.assertIsNotNone(update)
        self.assertEqual(update.order_id, "TS999")
        self.assertEqual(update.filled_quantity, 100)
        self.assertEqual(update.status, "FLL")

    def test_filter_heartbeat_message(self):
        hb_msg = '{"Heartbeat": 1, "Timestamp": "2026-07-24T12:00:00Z"}'
        update = self.mgr.parse_stream_message(hb_msg)
        self.assertIsNone(update)

    def test_stream_deduplication(self):
        msg = '{"OrderID": "TS888", "Status": "FLL", "FilledQuantity": 50, "AveragePrice": 200.00}'
        first = self.mgr.parse_stream_message(msg)
        self.assertIsNotNone(first)

        # Second message with identical signature should be skipped
        second = self.mgr.parse_stream_message(msg)
        self.assertIsNone(second)

    def test_rest_gap_reconciliation_on_reconnect(self):
        # Initial stream update processed before disconnect
        msg1 = '{"OrderID": "TS101", "Status": "ACK", "FilledQuantity": 0, "AveragePrice": 0.0}'
        self.mgr.parse_stream_message(msg1)

        # Mock REST catch-up returns 1 old event (TS101 ACK) and 1 missed event (TS102 FLL)
        def mock_rest_query(since_ts):
            return [
                {"OrderID": "TS101", "Status": "ACK", "FilledQuantity": 0, "AveragePrice": 0.0},
                {"OrderID": "TS102", "Status": "FLL", "FilledQuantity": 25, "AveragePrice": 300.5},
            ]

        reconciled = self.mgr.reconcile_missed_orders(mock_rest_query)

        # Should only apply TS102 (TS101 was already processed)
        self.assertEqual(len(reconciled), 1)
        self.assertEqual(reconciled[0].order_id, "TS102")


if __name__ == "__main__":
    unittest.main()

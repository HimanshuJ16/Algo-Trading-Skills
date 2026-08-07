"""
Unit tests for structured-logging-for-post-incident-forensics skill.
"""
import json
import unittest
from structured_logger import EventType, ForensicLogger


class TestForensicLogger(unittest.TestCase):

    def test_structured_event_emission(self):
        """Events should be emitted with correct JSON schema."""
        flogger = ForensicLogger(component="test-bot")
        event = flogger.emit(
            EventType.ORDER_PLACED,
            "Buy 100 AAPL @ market",
            metadata={"symbol": "AAPL", "qty": 100},
        )

        self.assertEqual(event.event_type, "ORDER_PLACED")
        self.assertEqual(event.component, "test-bot")
        self.assertEqual(event.sequence_number, 1)

        # Verify JSON is valid
        parsed = json.loads(event.to_json())
        self.assertEqual(parsed["event_type"], "ORDER_PLACED")
        self.assertEqual(parsed["metadata"]["symbol"], "AAPL")

    def test_correlation_id_timeline(self):
        """Events with same correlation_id should reconstruct a timeline."""
        flogger = ForensicLogger()
        cid = flogger.new_correlation_id()

        flogger.emit(EventType.STRATEGY_SIGNAL, "Long signal", correlation_id=cid)
        flogger.emit(EventType.ORDER_PLACED, "Buy 100 AAPL", correlation_id=cid)
        flogger.emit(EventType.FILL_RECEIVED, "Filled 100 @ 150.00", correlation_id=cid)

        timeline = flogger.reconstruct_timeline(cid)
        self.assertEqual(len(timeline), 3)
        self.assertEqual(timeline[0]["event_type"], "STRATEGY_SIGNAL")
        self.assertEqual(timeline[1]["event_type"], "ORDER_PLACED")
        self.assertEqual(timeline[2]["event_type"], "FILL_RECEIVED")

    def test_monotonic_sequence_numbers(self):
        """Sequence numbers should be strictly monotonically increasing."""
        flogger = ForensicLogger()
        e1 = flogger.emit(EventType.ORDER_PLACED, "Order 1")
        e2 = flogger.emit(EventType.FILL_RECEIVED, "Fill 1")
        e3 = flogger.emit(EventType.RISK_BREACH, "Breach!")

        self.assertEqual(e1.sequence_number, 1)
        self.assertEqual(e2.sequence_number, 2)
        self.assertEqual(e3.sequence_number, 3)

    def test_query_by_event_type(self):
        """Should filter events by type correctly."""
        flogger = ForensicLogger()
        flogger.emit(EventType.ORDER_PLACED, "Order 1")
        flogger.emit(EventType.FILL_RECEIVED, "Fill 1")
        flogger.emit(EventType.ORDER_PLACED, "Order 2")

        orders = flogger.query_by_event_type(EventType.ORDER_PLACED)
        self.assertEqual(len(orders), 2)


if __name__ == "__main__":
    unittest.main()

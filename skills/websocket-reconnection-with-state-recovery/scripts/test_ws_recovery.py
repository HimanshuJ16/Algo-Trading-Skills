"""
Unit tests for websocket-reconnection-with-state-recovery skill.
"""
import unittest
from ws_recovery import ConnectionState, WebSocketStateRecoveryManager, WSMessage


def mock_rest_gap_fill(symbol, start_seq, end_seq):
    """Mock REST gap fill returning missing messages."""
    missing = []
    for s in range(start_seq, end_seq + 1):
        missing.append(WSMessage(symbol=symbol, sequence_id=s, data={"gap_filled": True}))
    return missing


class TestWebSocketStateRecoveryManager(unittest.TestCase):

    def setUp(self):
        self.mgr = WebSocketStateRecoveryManager(
            base_backoff_sec=1.0,
            max_backoff_sec=30.0,
            rest_gap_fill_fn=mock_rest_gap_fill,
        )
        self.mgr.register_symbol_subscription("BTCUSDT")

    def test_exponential_backoff_with_jitter(self):
        delay1 = self.mgr.on_connection_lost("Drop 1")
        self.assertGreaterEqual(delay1, 1.0)

        delay2 = self.mgr.on_connection_lost("Drop 2")
        self.assertGreaterEqual(delay2, 2.0)

    def test_resubscription_list(self):
        symbols = self.mgr.on_connection_established()
        self.assertIn("BTCUSDT", symbols)
        self.assertEqual(self.mgr.state, ConnectionState.SUBSCRIBED)

    def test_sequence_gap_recovery(self):
        # 1st message: seq 100
        msg1 = WSMessage("BTCUSDT", 100, {"price": 60000.0})
        res1 = self.mgr.process_incoming_message(msg1)
        self.assertEqual(len(res1), 1)

        # 2nd message: seq 104 (gap of 101, 102, 103)
        msg2 = WSMessage("BTCUSDT", 104, {"price": 60050.0})
        res2 = self.mgr.process_incoming_message(msg2)

        # Should return 3 gap filled messages + 1 current = 4 messages total
        self.assertEqual(len(res2), 4)
        self.assertEqual(res2[0].sequence_id, 101)
        self.assertEqual(res2[1].sequence_id, 102)
        self.assertEqual(res2[2].sequence_id, 103)
        self.assertEqual(res2[3].sequence_id, 104)
        self.assertEqual(self.mgr.state, ConnectionState.STREAMING)


if __name__ == "__main__":
    unittest.main()

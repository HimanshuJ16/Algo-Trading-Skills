import struct
import unittest
from nasdaq_totalview_itch_feed_parsing import (
    NasdaqITCH50ParserEngine, ITCHParsedMessage, ITCHParserReport
)

class TestNasdaqITCH50ParserEngine(unittest.TestCase):

    def setUp(self):
        self.engine = NasdaqITCH50ParserEngine()

    def _pack_6byte_ts(self, ts_ns: int) -> bytes:
        high = (ts_ns >> 32) & 0xFFFF
        low = ts_ns & 0xFFFFFFFF
        return struct.pack(">HI", high, low)

    def test_add_order_and_execute(self):
        # Pack Add Order ('A') for AAPL: 100 shares @ $150.00 (1500000 int price), Ref 1001
        ts_bytes = self._pack_6byte_ts(123456789)
        raw_a = b"A" + struct.pack(
            ">HH6sQ1sI8sI",
            1, 2, ts_bytes, 1001, b"B", 100, b"AAPL    ", 1500000
        )

        msg_a = self.engine.parse_message(raw_a)
        self.assertEqual(msg_a.message_type, "A")
        self.assertEqual(msg_a.order_ref_number, 1001)
        self.assertEqual(msg_a.stock, "AAPL")
        self.assertEqual(msg_a.price_usd, 150.00)
        self.assertEqual(msg_a.shares, 100)
        self.assertEqual(len(self.engine.active_orders), 1)

        # Pack Order Executed ('E'): 40 shares executed for Ref 1001
        raw_e = b"E" + struct.pack(
            ">HH6sQIQ",
            1, 2, ts_bytes, 1001, 40, 9999
        )
        msg_e = self.engine.parse_message(raw_e)
        self.assertEqual(msg_e.message_type, "E")
        self.assertEqual(msg_e.executed_shares, 40)
        # Order 1001 should now have 60 remaining shares
        self.assertEqual(self.engine.active_orders[1001].shares, 60)

    def test_order_delete(self):
        # Add order 2002 and then Delete ('D')
        ts_bytes = self._pack_6byte_ts(123456789)
        raw_a = b"A" + struct.pack(
            ">HH6sQ1sI8sI",
            1, 2, ts_bytes, 2002, b"S", 50, b"MSFT    ", 3000000
        )
        self.engine.parse_message(raw_a)
        self.assertEqual(len(self.engine.active_orders), 1)

        raw_d = b"D" + struct.pack(
            ">HH6sQ",
            1, 2, ts_bytes, 2002
        )
        msg_d = self.engine.parse_message(raw_d)
        self.assertEqual(msg_d.message_type, "D")
        self.assertEqual(len(self.engine.active_orders), 0)

if __name__ == '__main__':
    unittest.main()

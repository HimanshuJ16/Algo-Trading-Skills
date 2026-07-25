"""
Unit tests for binary-protocol-parsing-for-low-latency-feeds skill.
"""
import time
import unittest
from binary_parser import BinaryFeedParserEngine, ITCHAddOrderMessage


class TestBinaryFeedParserEngine(unittest.TestCase):

    def setUp(self):
        self.parser = BinaryFeedParserEngine()

    def test_pack_unpack_roundtrip(self):
        # Pack Add Order message
        raw = self.parser.pack_itch_add_order(
            stock_locate=1,
            tracking_number=42,
            timestamp_ns=1784948000000000,
            order_ref_id=987654321,
            buy_sell="B",
            shares=500,
            stock="AAPL",
            price=175.50,
        )

        # 38 bytes binary frame
        self.assertEqual(len(raw), 38)

        # Unpack binary frame
        msg = self.parser.unpack_itch_add_order(raw)

        self.assertEqual(msg.message_type, "A")
        self.assertEqual(msg.stock_locate, 1)
        self.assertEqual(msg.tracking_number, 42)
        self.assertEqual(msg.order_ref_id, 987654321)
        self.assertEqual(msg.buy_sell, "B")
        self.assertEqual(msg.shares, 500)
        self.assertEqual(msg.stock, "AAPL")
        self.assertAlmostEqual(msg.price, 175.50, places=4)

    def test_invalid_frame_length_rejection(self):
        short_frame = b"\x00" * 20
        with self.assertRaises(ValueError) as ctx:
            self.parser.unpack_itch_add_order(short_frame)
        self.assertIn("Invalid ITCH frame size", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

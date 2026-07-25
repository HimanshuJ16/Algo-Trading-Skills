import unittest
from binary_parser import BinaryFeedParserEngine, ITCHAddOrderMessage

class TestBinaryFeedParserEngine(unittest.TestCase):
    
    def test_pack_unpack_roundtrip(self):
        # Pack Add Order message
        raw = BinaryFeedParserEngine.pack_itch_add_order(
            stock_locate=123,
            tracking_number=456,
            timestamp_ns=36000000000000,
            order_ref_id=987654321,
            buy_sell="S",
            shares=1000,
            stock="NVDA",
            price=950.25,
        )

        # 36 bytes binary frame per ITCH 5.0 specs
        self.assertEqual(len(raw), 36)

        # Use memoryview to simulate zero-copy buffer slicing
        mem_view = memoryview(raw)

        # Unpack binary frame
        msg = BinaryFeedParserEngine.unpack_itch_add_order(mem_view)

        self.assertEqual(msg.message_type, "A")
        self.assertEqual(msg.stock_locate, 123)
        self.assertEqual(msg.tracking_number, 456)
        self.assertEqual(msg.timestamp_ns, 36000000000000)
        self.assertEqual(msg.order_ref_id, 987654321)
        self.assertEqual(msg.buy_sell, "S")
        self.assertEqual(msg.shares, 1000)
        self.assertEqual(msg.stock, "NVDA")
        self.assertAlmostEqual(msg.price, 950.25, places=4)

    def test_invalid_frame_length_rejection(self):
        short_frame = memoryview(b"\x00" * 20)
        with self.assertRaises(ValueError) as ctx:
            BinaryFeedParserEngine.unpack_itch_add_order(short_frame)
        self.assertIn("Invalid ITCH frame size", str(ctx.exception))

if __name__ == "__main__":
    unittest.main()

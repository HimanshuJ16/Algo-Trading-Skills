import struct
import unittest

from binary_parser import (
    BinaryFeedParserEngine,
    ITCHAddOrderMessage,
    ITCHFrameError,
)


def _frame(msg_type=b"A", locate=1, track=2, ts=3, ref=4,
           side=b"B", shares=5, stock=b"AAPL    ", ticks=1500000):
    """Build an arbitrary 36-byte frame directly, bypassing pack validation."""
    return struct.pack(
        ">cHH6sQcI8sI", msg_type, locate, track,
        ts.to_bytes(6, "big"), ref, side, shares, stock, ticks,
    )


class TestPackUnpackRoundtrip(unittest.TestCase):

    def test_pack_unpack_roundtrip(self):
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
        msg = BinaryFeedParserEngine.unpack_itch_add_order(memoryview(raw))

        self.assertEqual(msg.message_type, "A")
        self.assertEqual(msg.stock_locate, 123)
        self.assertEqual(msg.tracking_number, 456)
        self.assertEqual(msg.timestamp_ns, 36000000000000)
        self.assertEqual(msg.order_ref_id, 987654321)
        self.assertEqual(msg.buy_sell, "S")
        self.assertEqual(msg.shares, 1000)
        self.assertEqual(msg.stock, "NVDA")
        self.assertAlmostEqual(msg.price, 950.25, places=4)

    def test_price_ticks_are_authoritative_and_exact(self):
        """Ticks are the wire value; price is a derived float."""
        raw = BinaryFeedParserEngine.pack_itch_add_order(
            1, 2, 3, 4, "B", 5, "AAPL", 150.1234)
        msg = BinaryFeedParserEngine.unpack_itch_add_order(memoryview(raw))
        # 150.1234 * 10_000 -> 1_501_234 ticks, independently derived.
        self.assertEqual(msg.price_ticks, 1501234)
        self.assertEqual(msg.price, 150.1234)

    def test_uint48_timestamp_boundary(self):
        """Max representable 48-bit nanosecond timestamp survives a roundtrip."""
        max_ts = 2**48 - 1
        raw = BinaryFeedParserEngine.pack_itch_add_order(
            1, 2, max_ts, 4, "B", 5, "AAPL", 1.0)
        msg = BinaryFeedParserEngine.unpack_itch_add_order(memoryview(raw))
        self.assertEqual(msg.timestamp_ns, max_ts)

    def test_symbol_padding_is_stripped(self):
        raw = BinaryFeedParserEngine.pack_itch_add_order(
            1, 2, 3, 4, "B", 5, "F", 9.99)
        msg = BinaryFeedParserEngine.unpack_itch_add_order(memoryview(raw))
        self.assertEqual(msg.stock, "F")


class TestFrameValidation(unittest.TestCase):

    def test_invalid_frame_length_rejection(self):
        short_frame = memoryview(b"\x00" * 20)
        with self.assertRaises(ValueError) as ctx:
            BinaryFeedParserEngine.unpack_itch_add_order(short_frame)
        self.assertIn("Invalid ITCH frame size", str(ctx.exception))

    def test_wrong_message_type_is_rejected(self):
        """
        Regression: a non-'A' frame previously decoded silently into a
        plausible ITCHAddOrderMessage, corrupting downstream book state.
        A type 'P' (Non-Cross Trade) frame must be refused, not decoded.
        """
        p_frame = _frame(msg_type=b"P", stock=b"MSFT    ", shares=300)
        with self.assertRaises(ITCHFrameError) as ctx:
            BinaryFeedParserEngine.unpack_itch_add_order(memoryview(p_frame))
        self.assertIn("Expected ITCH message type", str(ctx.exception))

    def test_invalid_side_byte_is_rejected(self):
        """A misaligned frame often shows up first as a bad Buy/Sell byte."""
        with self.assertRaises(ITCHFrameError) as ctx:
            BinaryFeedParserEngine.unpack_itch_add_order(
                memoryview(_frame(side=b"X")))
        self.assertIn("Buy/Sell", str(ctx.exception))

    def test_non_ascii_field_normalised_to_itch_frame_error(self):
        """
        Corrupt bytes in a character field previously surfaced a bare
        UnicodeDecodeError with no frame position. It is normalised to
        ITCHFrameError (still a ValueError, so old handlers keep working)
        and the message names the offending offset.
        """
        corrupt = bytearray(_frame())
        corrupt[24] = 0xFF  # first byte of the 8-char Stock field
        with self.assertRaises(ITCHFrameError) as ctx:
            BinaryFeedParserEngine.unpack_itch_add_order(
                memoryview(bytes(corrupt)))
        self.assertNotIsInstance(ctx.exception, UnicodeDecodeError)
        self.assertIn("offset 0", str(ctx.exception))
        self.assertTrue(issubclass(ITCHFrameError, ValueError))

    def test_negative_offset_rejected(self):
        with self.assertRaises(ITCHFrameError):
            BinaryFeedParserEngine.unpack_itch_add_order(
                memoryview(_frame()), offset=-1)


class TestStreamOffsetParsing(unittest.TestCase):

    def test_parses_concatenated_messages_without_slicing(self):
        """Walk several messages in one buffer via offset, as a feed handler does."""
        expected = [("AAPL", 1500000), ("MSFT", 4207500), ("NVDA", 9502500)]
        buf = b"".join(
            _frame(stock=sym.ljust(8).encode(), ticks=ticks, ref=i)
            for i, (sym, ticks) in enumerate(expected)
        )
        view = memoryview(buf)
        size = BinaryFeedParserEngine.ADD_ORDER_FORMAT.size

        parsed = [
            BinaryFeedParserEngine.unpack_itch_add_order(view, offset=i * size)
            for i in range(len(expected))
        ]

        self.assertEqual(
            [(m.stock, m.price_ticks) for m in parsed], expected)
        self.assertEqual([m.order_ref_id for m in parsed], [0, 1, 2])

    def test_truncated_trailing_message_reports_available_bytes(self):
        buf = _frame() + _frame()[:10]
        size = BinaryFeedParserEngine.ADD_ORDER_FORMAT.size
        with self.assertRaises(ITCHFrameError) as ctx:
            BinaryFeedParserEngine.unpack_itch_add_order(
                memoryview(buf), offset=size)
        self.assertIn("10 bytes", str(ctx.exception))


class TestPackValidation(unittest.TestCase):

    def test_oversized_symbol_is_rejected_not_truncated(self):
        """Regression: 'TOOLONGSYM' was silently truncated to 'TOOLONGS'."""
        with self.assertRaises(ValueError) as ctx:
            BinaryFeedParserEngine.pack_itch_add_order(
                1, 2, 3, 4, "B", 5, "TOOLONGSYM", 1.0)
        self.assertIn("1-8 characters", str(ctx.exception))

    def test_invalid_side_rejected(self):
        with self.assertRaises(ValueError):
            BinaryFeedParserEngine.pack_itch_add_order(
                1, 2, 3, 4, "X", 5, "AAPL", 1.0)

    def test_out_of_range_fields_raise_valueerror(self):
        cases = {
            "timestamp_ns": dict(timestamp_ns=2**48),
            "stock_locate": dict(stock_locate=2**16),
            "shares": dict(shares=2**32),
            "order_ref_id": dict(order_ref_id=2**64),
        }
        base = dict(stock_locate=1, tracking_number=2, timestamp_ns=3,
                    order_ref_id=4, buy_sell="B", shares=5, stock="AAPL",
                    price=1.0)
        for field, override in cases.items():
            with self.subTest(field=field):
                with self.assertRaises(ValueError) as ctx:
                    BinaryFeedParserEngine.pack_itch_add_order(
                        **{**base, **override})
                self.assertIn(field, str(ctx.exception))

    def test_negative_timestamp_rejected(self):
        with self.assertRaises(ValueError):
            BinaryFeedParserEngine.pack_itch_add_order(
                1, 2, -1, 4, "B", 5, "AAPL", 1.0)

    def test_price_above_uint32_ticks_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            BinaryFeedParserEngine.pack_itch_add_order(
                1, 2, 3, 4, "B", 5, "AAPL", 429496.7296)
        self.assertIn("price", str(ctx.exception))

    def test_negative_price_rejected(self):
        with self.assertRaises(ValueError):
            BinaryFeedParserEngine.pack_itch_add_order(
                1, 2, 3, 4, "B", 5, "AAPL", -1.0)


class TestLayoutInvariants(unittest.TestCase):

    def test_add_order_layout_is_36_bytes_big_endian(self):
        """ITCH 5.0 Add Order is 36 bytes; '>' also guarantees no padding."""
        self.assertEqual(BinaryFeedParserEngine.ADD_ORDER_FORMAT.size, 36)
        self.assertEqual(
            BinaryFeedParserEngine.ADD_ORDER_FORMAT.format[0], ">")

    def test_field_offsets_match_itch_specification(self):
        """
        Independently assert wire offsets rather than trusting the format
        string: MsgType@0, Locate@1, Tracking@3, Timestamp@5, OrderRef@11,
        BuySell@19, Shares@20, Stock@24, Price@32.
        """
        raw = BinaryFeedParserEngine.pack_itch_add_order(
            stock_locate=0x1122,
            tracking_number=0x3344,
            timestamp_ns=0x556677889900,
            order_ref_id=0x0102030405060708,
            buy_sell="S",
            shares=0x0A0B0C0D,
            stock="AAPL",
            price=1.0,
        )
        self.assertEqual(raw[0:1], b"A")
        self.assertEqual(raw[1:3], bytes.fromhex("1122"))
        self.assertEqual(raw[3:5], bytes.fromhex("3344"))
        self.assertEqual(raw[5:11], bytes.fromhex("556677889900"))
        self.assertEqual(raw[11:19], bytes.fromhex("0102030405060708"))
        self.assertEqual(raw[19:20], b"S")
        self.assertEqual(raw[20:24], bytes.fromhex("0A0B0C0D"))
        self.assertEqual(raw[24:32], b"AAPL    ")
        self.assertEqual(raw[32:36], (10000).to_bytes(4, "big"))

    def test_message_uses_slots(self):
        msg = BinaryFeedParserEngine.unpack_itch_add_order(
            memoryview(_frame()))
        self.assertIsInstance(msg, ITCHAddOrderMessage)
        self.assertFalse(hasattr(msg, "__dict__"))


if __name__ == "__main__":
    unittest.main()

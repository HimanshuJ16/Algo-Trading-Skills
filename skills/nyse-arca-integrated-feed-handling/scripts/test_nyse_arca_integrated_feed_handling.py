"""Unit tests for the NYSE Arca Integrated Feed (XDP/Pillar) decoder.

Test packets are assembled by writing each field at the **absolute byte offset given
in the spec's field table**, not by reusing the engine's ``struct`` format strings.
That keeps the tests an independent check on the layout: if a format string in the
engine drifts from the spec, these tests fail rather than drifting with it.

Offsets are transcribed from Pillar Integrated Feed Client Specification v2.5
(2022-05-16) and Pillar Equities Common Client Specification v2.4k (2024-07-25).
"""

import logging
import struct
import unittest

from nyse_arca_integrated_feed_handling import (
    MSG_ADD_ORDER,
    MSG_ADD_ORDER_REFRESH,
    MSG_DELETE_ORDER,
    MSG_MODIFY_ORDER,
    MSG_ORDER_EXECUTION,
    MSG_REPLACE_ORDER,
    NYSEArcaIntegratedFeedEngine,
    XDPProtocolError,
)

MODULE_LOGGER = "nyse_arca_integrated_feed_handling"


# ----------------------------------------------------------------------------------
# Packet construction helpers - offsets straight from the spec field tables
# ----------------------------------------------------------------------------------
def _message(msg_type, msg_size, fields, trailing=b""):
    """Build one message: 4-byte header plus fields placed at their spec offsets.

    ``fields`` is a sequence of ``(offset, raw_bytes)`` with offsets relative to the
    start of the message (i.e. offset 0 is the MsgSize field), exactly as printed in
    the spec tables. ``trailing`` simulates a newer spec revision appending fields.
    """
    buf = bytearray(msg_size)
    struct.pack_into("<HH", buf, 0, msg_size + len(trailing), msg_type)
    for offset, raw in fields:
        end = offset + len(raw)
        if end > msg_size:
            raise AssertionError(
                f"field at offset {offset} ({len(raw)}B) overruns MsgSize {msg_size}"
            )
        buf[offset:end] = raw
    return bytes(buf) + trailing


def _packet(messages, seq_num=1, delivery_flag=11, num_msgs=None, pkt_size=None):
    """Build an XDP packet header (Common v2.4k 2.1.1) followed by ``messages``."""
    body = b"".join(messages)
    count = len(messages) if num_msgs is None else num_msgs
    size = (16 + len(body)) if pkt_size is None else pkt_size
    header = struct.pack("<HBBIII", size, delivery_flag, count, seq_num, 1_700_000_000, 250)
    return header + body


def _u32(value):
    return struct.pack("<I", value)


def _i32(value):
    return struct.pack("<i", value)


def _u64(value):
    return struct.pack("<Q", value)


def add_order(order_id, symbol_index, price_raw, volume, side=b"B", symbol_seq=1,
              firm=b"ARCA ", src_ns=500_000, trailing=b""):
    """Msg Type 100, MsgSize 39 (Integrated Feed v2.5 section 2)."""
    return _message(
        MSG_ADD_ORDER,
        39,
        [
            (4, _u32(src_ns)),          # SourceTimeNS
            (8, _u32(symbol_index)),    # SymbolIndex
            (12, _u32(symbol_seq)),     # SymbolSeqNum
            (16, _u64(order_id)),       # OrderID (8 bytes)
            (24, _i32(price_raw)),      # Price (signed)
            (28, _u32(volume)),         # Volume
            (32, side),                 # Side
            (33, firm),                 # FirmID (5 bytes)
            (38, b"\x00"),              # Reserved 1
        ],
        trailing=trailing,
    )


def modify_order(order_id, symbol_index, price_raw, volume, position_change=0,
                 side=b"B", symbol_seq=2, src_ns=600_000):
    """Msg Type 101, MsgSize 35 (v2.5 section 3)."""
    return _message(
        MSG_MODIFY_ORDER,
        35,
        [
            (4, _u32(src_ns)),
            (8, _u32(symbol_index)),
            (12, _u32(symbol_seq)),
            (16, _u64(order_id)),
            (24, _i32(price_raw)),
            (28, _u32(volume)),
            (32, struct.pack("<B", position_change)),  # PositionChange
            (33, side),                                # Side (added in v2.5)
            (34, b"\x00"),                             # Reserved 2
        ],
    )


def delete_order(order_id, symbol_index, symbol_seq=3, src_ns=700_000):
    """Msg Type 102, MsgSize 25 (v2.5 section 4)."""
    return _message(
        MSG_DELETE_ORDER,
        25,
        [
            (4, _u32(src_ns)),
            (8, _u32(symbol_index)),
            (12, _u32(symbol_seq)),
            (16, _u64(order_id)),
            (24, b"\x00"),  # Reserved 1
        ],
    )


def execution(order_id, symbol_index, price_raw, volume, trade_id=77, printable=1,
              symbol_seq=4, src_ns=800_000):
    """Msg Type 103, MsgSize 42 (v2.5 section 5)."""
    return _message(
        MSG_ORDER_EXECUTION,
        42,
        [
            (4, _u32(src_ns)),
            (8, _u32(symbol_index)),
            (12, _u32(symbol_seq)),
            (16, _u64(order_id)),
            (24, _u32(trade_id)),   # TradeID - absent from the pre-fix layout
            (28, _i32(price_raw)),
            (32, _u32(volume)),
            (36, struct.pack("<B", printable)),  # PrintableFlag
            (37, b"\x00"),                       # Reserved 1
            (38, b"@"),                          # TradeCond1
            (39, b" "),                          # TradeCond2
            (40, b" "),                          # TradeCond3
            (41, b" "),                          # TradeCond4
        ],
    )


def replace_order(order_id, new_order_id, symbol_index, price_raw, volume,
                  side=b"B", symbol_seq=5, src_ns=900_000):
    """Msg Type 104, MsgSize 42 (v2.5 section 6)."""
    return _message(
        MSG_REPLACE_ORDER,
        42,
        [
            (4, _u32(src_ns)),
            (8, _u32(symbol_index)),
            (12, _u32(symbol_seq)),
            (16, _u64(order_id)),
            (24, _u64(new_order_id)),
            (32, _i32(price_raw)),
            (36, _u32(volume)),
            (40, side),
            (41, b"\x00"),
        ],
    )


def add_order_refresh(order_id, symbol_index, price_raw, volume, side=b"B",
                      symbol_seq=6, src_seconds=1_700_000_000, src_ns=123):
    """Msg Type 106, MsgSize 43 (v2.5 section 8). Carries full SourceTime seconds."""
    return _message(
        MSG_ADD_ORDER_REFRESH,
        43,
        [
            (4, _u32(src_seconds)),   # SourceTime
            (8, _u32(src_ns)),        # SourceTimeNS
            (12, _u32(symbol_index)),
            (16, _u32(symbol_seq)),
            (20, _u64(order_id)),
            (28, _i32(price_raw)),
            (32, _u32(volume)),
            (36, side),
            (37, b"ARCA "),
            (42, b"\x00"),
        ],
    )


def symbol_index_mapping(symbol_index, symbol, price_scale_code, market_id=3,
                         exchange_code=b"P"):
    """Msg Type 3, MsgSize 44 (Common v2.4k section 4.3)."""
    return _message(
        3,
        44,
        [
            (4, _u32(symbol_index)),
            (8, symbol.encode("ascii").ljust(11, b"\x00")),  # Symbol, null-padded
            (19, b"\x00"),                                   # Reserved
            (20, struct.pack("<H", market_id)),              # Market ID
            (22, b"\x01"),                                   # System ID
            (23, exchange_code),                             # Exchange Code
            (24, struct.pack("<B", price_scale_code)),       # PriceScaleCode
        ],
    )


def symbol_clear(symbol_index, next_source_seq=1, src_seconds=1_700_000_000, src_ns=0):
    """Msg Type 32, MsgSize 20 (Common v2.4k section 4.4)."""
    return _message(
        32,
        20,
        [
            (4, _u32(src_seconds)),
            (8, _u32(src_ns)),
            (12, _u32(symbol_index)),
            (16, _u32(next_source_seq)),
        ],
    )


def source_time_reference(source_time_seconds, partition_id=1):
    """Msg Type 2, MsgSize 16 (Common v2.4k section 4.2)."""
    return _message(
        2,
        16,
        [
            (4, _u32(partition_id)),          # ID
            (8, _u32(0)),                     # SymbolSeqNum (reserved)
            (12, _u32(source_time_seconds)),  # SourceTime
        ],
    )


def sequence_number_reset(product_id=1, channel_id=1):
    """Msg Type 1, MsgSize 14 (Common v2.4k section 4.1)."""
    return _message(
        1,
        14,
        [
            (4, _u32(1_700_000_000)),
            (8, _u32(0)),
            (12, struct.pack("<B", product_id)),
            (13, struct.pack("<B", channel_id)),
        ],
    )


# ----------------------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------------------
class BaseEngineTest(unittest.TestCase):
    """Seeds SPY at SymbolIndex 101 with PriceScaleCode 4 unless a test says otherwise."""

    def setUp(self):
        logging.getLogger(MODULE_LOGGER).setLevel(logging.ERROR)
        self.engine = NYSEArcaIntegratedFeedEngine(
            symbol_index_map={101: "SPY", 102: "AAPL"},
            price_scale_codes={101: 4, 102: 4},
        )


class TestWireLayout(BaseEngineTest):
    """Field offsets must match the spec tables, not an invented layout."""

    def test_add_order_decodes_every_field_at_its_spec_offset(self):
        msgs = self.engine.parse_xdp_packet(
            _packet([add_order(order_id=88001, symbol_index=101, price_raw=4_500_000,
                               volume=500, side=b"B", firm=b"ARCA ")])
        )

        self.assertEqual(len(msgs), 1)
        add = msgs[0]
        self.assertEqual(add.msg_type, MSG_ADD_ORDER)
        self.assertEqual(add.order_id, 88001)
        self.assertEqual(add.symbol, "SPY")
        self.assertEqual(add.side, "B")
        self.assertEqual(add.volume, 500)
        self.assertEqual(add.price_usd, 450.00)  # 4,500,000 / 10^4, computed by hand
        self.assertEqual(add.symbol_index, 101)

        resting = self.engine.active_orders[88001]
        self.assertEqual(resting.volume, 500)
        self.assertEqual(resting.price_usd, 450.00)
        self.assertEqual(resting.firm_id, "ARCA")

    def test_add_order_uses_full_eight_byte_order_id(self):
        """A pre-Pillar 4-byte OrderID assumption truncates IDs above 2^32."""
        big_id = 0x0000_0001_0000_0007  # 4294967303
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=big_id, symbol_index=101,
                               price_raw=1_000_000, volume=10)])
        )
        self.assertIn(big_id, self.engine.active_orders)

    def test_execution_reads_volume_past_the_trade_id_field(self):
        """Regression: omitting TradeID shifts Price into Volume's slot.

        With TradeID=999999 at offset 24, a decoder missing that field would read
        999,999 as the executed size and wipe the order out. The executed size here
        is 200, leaving 300 resting.
        """
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=88001, symbol_index=101,
                               price_raw=4_500_000, volume=500)], seq_num=1)
        )
        msgs = self.engine.parse_xdp_packet(
            _packet([execution(order_id=88001, symbol_index=101, price_raw=4_500_000,
                               volume=200, trade_id=999999)], seq_num=2)
        )

        self.assertEqual(msgs[0].executed_volume, 200)
        self.assertEqual(msgs[0].trade_id, 999999)
        self.assertEqual(msgs[0].price_usd, 450.00)
        self.assertTrue(msgs[0].printable)
        self.assertEqual(self.engine.active_orders[88001].volume, 300)
        self.assertEqual(self.engine.generate_report().status, "FEED_PARSER_SUCCESS")

    def test_modify_carries_side_at_offset_33(self):
        """v2.5 replaced Reserved 1 with Side at offset 33 of Msg Type 101."""
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=1, symbol_index=101, price_raw=1_000_000,
                               volume=100, side=b"S")], seq_num=1)
        )
        msgs = self.engine.parse_xdp_packet(
            _packet([modify_order(order_id=1, symbol_index=101, price_raw=1_010_000,
                                  volume=80, side=b"S")], seq_num=2)
        )
        self.assertEqual(msgs[0].side, "S")


class TestPriceScaling(BaseEngineTest):
    """Price = Numerator / 10^PriceScaleCode, per symbol (Common v2.4k section 3.5)."""

    def test_same_integer_scales_differently_per_symbol(self):
        engine = NYSEArcaIntegratedFeedEngine(
            symbol_index_map={101: "SPY", 102: "AAPL", 103: "LOWTICK"},
            price_scale_codes={101: 4, 102: 6, 103: 3},
        )
        engine.parse_xdp_packet(
            _packet(
                [
                    add_order(order_id=1, symbol_index=101, price_raw=4_500_000, volume=1),
                    add_order(order_id=2, symbol_index=102, price_raw=4_500_000, volume=1),
                    add_order(order_id=3, symbol_index=103, price_raw=4_500_000, volume=1),
                ]
            )
        )
        # Hand-computed: 4,500,000 / 10^4, / 10^6, / 10^3.
        self.assertEqual(engine.active_orders[1].price_usd, 450.00)
        self.assertEqual(engine.active_orders[2].price_usd, 4.50)
        self.assertEqual(engine.active_orders[3].price_usd, 4500.00)
        self.assertEqual(engine.prices_scaled_with_fallback, 0)

    def test_price_scale_code_is_learned_from_symbol_index_mapping(self):
        engine = NYSEArcaIntegratedFeedEngine()
        engine.parse_xdp_packet(
            _packet(
                [
                    symbol_index_mapping(symbol_index=555, symbol="ARCX",
                                         price_scale_code=6),
                    add_order(order_id=9, symbol_index=555, price_raw=2_147_480,
                              volume=5),
                ]
            )
        )
        self.assertEqual(engine.symbol_index_map[555], "ARCX")
        self.assertEqual(engine.price_scale_codes[555], 6)
        # 2,147,480 / 10^6 = 2.14748 -- the scale-6 maximum quoted in Common v2.4k 3.5.1.
        self.assertAlmostEqual(engine.active_orders[9].price_usd, 2.14748, places=9)
        self.assertEqual(engine.prices_scaled_with_fallback, 0)

    def test_unknown_symbol_falls_back_and_is_counted(self):
        engine = NYSEArcaIntegratedFeedEngine(default_price_scale_code=4)
        engine.parse_xdp_packet(
            _packet([add_order(order_id=1, symbol_index=999, price_raw=4_500_000,
                               volume=1)])
        )
        self.assertEqual(engine.prices_scaled_with_fallback, 1)
        self.assertEqual(engine.active_orders[1].price_usd, 450.00)
        self.assertEqual(engine.active_orders[1].symbol, "SYM_999")
        self.assertEqual(engine.generate_report().prices_scaled_with_fallback, 1)

    def test_rejects_out_of_range_default_scale_code(self):
        with self.assertRaises(ValueError):
            NYSEArcaIntegratedFeedEngine(default_price_scale_code=42)

    def test_ignores_out_of_range_scale_code_on_the_wire(self):
        engine = NYSEArcaIntegratedFeedEngine()
        engine.parse_xdp_packet(
            _packet([symbol_index_mapping(symbol_index=7, symbol="BAD",
                                          price_scale_code=200)])
        )
        self.assertNotIn(7, engine.price_scale_codes)
        self.assertEqual(engine.messages_skipped, 1)


class TestBookMaintenance(BaseEngineTest):
    def test_partial_then_full_execution_removes_the_order(self):
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=42, symbol_index=101, price_raw=1_000_000,
                               volume=500)], seq_num=1)
        )
        self.engine.parse_xdp_packet(
            _packet([execution(order_id=42, symbol_index=101, price_raw=1_000_000,
                               volume=200)], seq_num=2)
        )
        self.assertEqual(self.engine.active_orders[42].volume, 300)

        self.engine.parse_xdp_packet(
            _packet([execution(order_id=42, symbol_index=101, price_raw=1_000_000,
                               volume=300)], seq_num=3)
        )
        self.assertNotIn(42, self.engine.active_orders)
        self.assertEqual(self.engine.book_desync_events, 0)

    def test_execution_does_not_overwrite_the_resting_price(self):
        """v2.5 section 5: remaining shares keep their original price."""
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=42, symbol_index=101, price_raw=1_000_000,
                               volume=500)], seq_num=1)
        )
        self.engine.parse_xdp_packet(
            _packet([execution(order_id=42, symbol_index=101, price_raw=1_500_000,
                               volume=100)], seq_num=2)
        )
        self.assertEqual(self.engine.active_orders[42].price_usd, 100.00)

    def test_over_execution_flags_desync_and_degrades_the_report(self):
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=42, symbol_index=101, price_raw=1_000_000,
                               volume=100)], seq_num=1)
        )
        self.engine.parse_xdp_packet(
            _packet([execution(order_id=42, symbol_index=101, price_raw=1_000_000,
                               volume=250)], seq_num=2)
        )
        self.assertNotIn(42, self.engine.active_orders)
        self.assertEqual(self.engine.book_desync_events, 1)
        report = self.engine.generate_report()
        self.assertEqual(report.status, "FEED_PARSER_DEGRADED")
        self.assertTrue(report.book_is_stale)

    def test_modify_sets_absolute_volume_and_records_lost_queue_position(self):
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=7, symbol_index=101, price_raw=1_000_000,
                               volume=400)], seq_num=1)
        )
        self.engine.parse_xdp_packet(
            _packet([modify_order(order_id=7, symbol_index=101, price_raw=1_020_000,
                                  volume=250, position_change=1)], seq_num=2)
        )
        resting = self.engine.active_orders[7]
        self.assertEqual(resting.volume, 250)   # absolute, not 400-250
        self.assertEqual(resting.price_usd, 102.00)
        self.assertTrue(resting.lost_queue_position)

    def test_modify_to_zero_volume_removes_the_order(self):
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=7, symbol_index=101, price_raw=1_000_000,
                               volume=400)], seq_num=1)
        )
        self.engine.parse_xdp_packet(
            _packet([modify_order(order_id=7, symbol_index=101, price_raw=1_000_000,
                                  volume=0)], seq_num=2)
        )
        self.assertNotIn(7, self.engine.active_orders)

    def test_modify_for_unknown_order_flags_desync(self):
        self.engine.parse_xdp_packet(
            _packet([modify_order(order_id=404, symbol_index=101, price_raw=1_000_000,
                                  volume=10)])
        )
        self.assertEqual(self.engine.book_desync_events, 1)

    def test_delete_removes_the_order(self):
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=99002, symbol_index=102, price_raw=1_800_000,
                               volume=100, side=b"S")], seq_num=1)
        )
        self.assertEqual(len(self.engine.active_orders), 1)

        msgs = self.engine.parse_xdp_packet(
            _packet([delete_order(order_id=99002, symbol_index=102)], seq_num=2)
        )
        self.assertEqual(msgs[0].msg_type, MSG_DELETE_ORDER)
        self.assertEqual(msgs[0].symbol, "AAPL")
        self.assertEqual(msgs[0].side, "S")
        self.assertEqual(len(self.engine.active_orders), 0)

    def test_delete_for_absent_order_is_not_treated_as_desync(self):
        """A fully-executed order is already gone; a trailing Delete is benign."""
        self.engine.parse_xdp_packet(
            _packet([delete_order(order_id=12345, symbol_index=101)])
        )
        self.assertEqual(self.engine.book_desync_events, 0)
        self.assertEqual(self.engine.generate_report().status, "FEED_PARSER_SUCCESS")

    def test_replace_swaps_order_id_without_a_delete(self):
        """v2.5 section 4: replaced orders get no Delete, only Msg Type 104."""
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=500, symbol_index=101, price_raw=1_000_000,
                               volume=300, side=b"S")], seq_num=1)
        )
        msgs = self.engine.parse_xdp_packet(
            _packet([replace_order(order_id=500, new_order_id=501, symbol_index=101,
                                   price_raw=1_050_000, volume=250)], seq_num=2)
        )

        self.assertEqual(msgs[0].order_id, 500)
        self.assertEqual(msgs[0].new_order_id, 501)
        self.assertNotIn(500, self.engine.active_orders)
        replacement = self.engine.active_orders[501]
        self.assertEqual(replacement.volume, 250)
        self.assertEqual(replacement.price_usd, 105.00)
        # Side is inherited from the sitting order (v2.5 section 6).
        self.assertEqual(replacement.side, "S")
        self.assertEqual(len(self.engine.active_orders), 1)

    def test_invalid_side_keeps_the_order_off_the_book(self):
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=1, symbol_index=101, price_raw=1_000_000,
                               volume=10, side=b"\xff")])
        )
        self.assertEqual(self.engine.active_orders, {})
        self.assertEqual(self.engine.messages_skipped, 1)


class TestRefreshAndSymbolClear(BaseEngineTest):
    def test_symbol_clear_drops_only_the_named_symbol(self):
        self.engine.parse_xdp_packet(
            _packet(
                [
                    add_order(order_id=1, symbol_index=101, price_raw=1_000_000, volume=1),
                    add_order(order_id=2, symbol_index=102, price_raw=1_000_000, volume=1),
                ],
                seq_num=1,
            )
        )
        self.assertEqual(len(self.engine.active_orders), 2)

        self.engine.parse_xdp_packet(
            _packet([symbol_clear(symbol_index=101)], seq_num=3)
        )
        self.assertNotIn(1, self.engine.active_orders)
        self.assertIn(2, self.engine.active_orders)

    def test_add_order_refresh_rebuilds_the_book_with_absolute_timestamps(self):
        msgs = self.engine.parse_xdp_packet(
            _packet(
                [add_order_refresh(order_id=900, symbol_index=101, price_raw=1_000_000,
                                   volume=75, src_seconds=1_700_000_000, src_ns=123)],
                seq_num=1,
                delivery_flag=18,  # Start of Refresh sequence
            )
        )
        self.assertEqual(msgs[0].msg_type, MSG_ADD_ORDER_REFRESH)
        self.assertTrue(msgs[0].is_refresh)
        self.assertIsNone(msgs[0].seq_num)
        self.assertEqual(self.engine.active_orders[900].volume, 75)
        # Msg 106 carries its own seconds field, so no Time Reference is needed.
        self.assertEqual(
            self.engine.active_orders[900].source_time_ns, 1_700_000_000_000_000_123
        )

    def test_refresh_traffic_does_not_trigger_gap_detection(self):
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=1, symbol_index=101, price_raw=1_000_000,
                               volume=1)], seq_num=100)
        )
        self.engine.parse_xdp_packet(
            _packet([add_order_refresh(order_id=2, symbol_index=101,
                                       price_raw=1_000_000, volume=1)],
                    seq_num=7, delivery_flag=19)
        )
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=3, symbol_index=101, price_raw=1_000_000,
                               volume=1)], seq_num=101)
        )
        self.assertEqual(self.engine.sequence_gaps, [])


class TestTimestamps(BaseEngineTest):
    def test_timestamp_is_none_until_a_source_time_reference_arrives(self):
        msgs = self.engine.parse_xdp_packet(
            _packet([add_order(order_id=1, symbol_index=101, price_raw=1_000_000,
                               volume=1, src_ns=250_000_000)])
        )
        self.assertIsNone(msgs[0].source_time_ns)
        self.assertEqual(msgs[0].source_time_ns_offset, 250_000_000)

    def test_source_time_reference_resolves_subsequent_offsets(self):
        msgs = self.engine.parse_xdp_packet(
            _packet(
                [
                    source_time_reference(1_700_000_000),
                    add_order(order_id=1, symbol_index=101, price_raw=1_000_000,
                              volume=1, src_ns=250_000_000),
                ]
            )
        )
        # Control message 2 is applied but not returned.
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].source_time_ns, 1_700_000_000_250_000_000)
        self.assertEqual(
            self.engine.active_orders[1].source_time_ns, 1_700_000_000_250_000_000
        )


class TestSequenceIntegrity(BaseEngineTest):
    def _single_add(self, order_id, seq_num, delivery_flag=11):
        return _packet(
            [add_order(order_id=order_id, symbol_index=101, price_raw=1_000_000,
                       volume=1)],
            seq_num=seq_num,
            delivery_flag=delivery_flag,
        )

    def test_contiguous_sequence_produces_no_gap(self):
        self.engine.parse_xdp_packet(self._single_add(1, seq_num=10))
        self.engine.parse_xdp_packet(self._single_add(2, seq_num=11))
        self.assertEqual(self.engine.sequence_gaps, [])
        self.assertEqual(self.engine.generate_report().status, "FEED_PARSER_SUCCESS")

    def test_multi_message_packet_advances_sequence_by_message_count(self):
        packet = _packet(
            [
                add_order(order_id=1, symbol_index=101, price_raw=1_000_000, volume=1),
                add_order(order_id=2, symbol_index=101, price_raw=1_000_000, volume=1),
                add_order(order_id=3, symbol_index=101, price_raw=1_000_000, volume=1),
            ],
            seq_num=10,
        )
        msgs = self.engine.parse_xdp_packet(packet)

        self.assertEqual(len(msgs), 3)
        self.assertEqual([m.seq_num for m in msgs], [10, 11, 12])
        self.assertEqual(self.engine.next_expected_seq, 13)

        self.engine.parse_xdp_packet(self._single_add(4, seq_num=13))
        self.assertEqual(self.engine.sequence_gaps, [])

    def test_missing_sequence_numbers_are_reported_and_degrade_the_book(self):
        self.engine.parse_xdp_packet(self._single_add(1, seq_num=10))
        self.engine.parse_xdp_packet(self._single_add(2, seq_num=15))

        self.assertEqual(len(self.engine.sequence_gaps), 1)
        gap = self.engine.sequence_gaps[0]
        self.assertEqual(gap.expected_seq, 11)
        self.assertEqual(gap.received_seq, 15)
        self.assertEqual(gap.missing_count, 4)

        report = self.engine.generate_report()
        self.assertEqual(report.status, "FEED_PARSER_DEGRADED")
        self.assertTrue(report.book_is_stale)
        self.assertIn("BOOK NOT AUTHORITATIVE", report.audit_notes)

    def test_replayed_sequence_number_is_flagged(self):
        self.engine.parse_xdp_packet(self._single_add(1, seq_num=10))
        self.engine.parse_xdp_packet(self._single_add(2, seq_num=5))

        gap = self.engine.sequence_gaps[0]
        # One message consumed seq 10, so the next expected number is 11.
        self.assertEqual(gap.expected_seq, 11)
        self.assertEqual(gap.received_seq, 5)
        self.assertEqual(gap.missing_count, -6)  # negative => replay, not loss
        self.assertTrue(self.engine.book_is_stale)

    def test_heartbeat_does_not_advance_the_expected_sequence(self):
        """Common v2.4k section 2.2: a heartbeat carries no messages and no sequence."""
        self.engine.parse_xdp_packet(self._single_add(1, seq_num=10))
        heartbeat = _packet([], seq_num=11, delivery_flag=1, num_msgs=0)
        self.engine.parse_xdp_packet(heartbeat)
        self.engine.parse_xdp_packet(self._single_add(2, seq_num=11))
        self.assertEqual(self.engine.sequence_gaps, [])

    def test_sequence_number_reset_packet_is_not_a_gap(self):
        self.engine.parse_xdp_packet(self._single_add(1, seq_num=5000))
        self.engine.parse_xdp_packet(
            _packet([sequence_number_reset()], seq_num=1, delivery_flag=12)
        )
        self.engine.parse_xdp_packet(self._single_add(2, seq_num=2))
        self.assertEqual(self.engine.sequence_gaps, [])

    def test_unrecognised_delivery_flag_keeps_gap_detection_running(self):
        """A future flag must not silently disable the integrity check."""
        self.engine.parse_xdp_packet(self._single_add(1, seq_num=10))
        with self.assertLogs(MODULE_LOGGER, level="WARNING"):
            self.engine.parse_xdp_packet(self._single_add(2, seq_num=40, delivery_flag=99))

        self.assertEqual(len(self.engine.sequence_gaps), 1)
        self.assertEqual(self.engine.sequence_gaps[0].received_seq, 40)

    def test_message_unavailable_flag_marks_the_book_unrecoverable(self):
        self.engine.parse_xdp_packet(
            _packet([], seq_num=50, delivery_flag=21, num_msgs=0)
        )
        self.assertTrue(self.engine.book_is_stale)
        self.assertEqual(self.engine.generate_report().status, "FEED_PARSER_DEGRADED")


class TestMalformedInput(BaseEngineTest):
    def test_packet_shorter_than_the_header_raises(self):
        with self.assertRaises(XDPProtocolError):
            self.engine.parse_xdp_packet(b"\x00" * 15)

    def test_short_packet_error_is_still_a_value_error(self):
        """Callers written against the previous bare-ValueError contract keep working."""
        with self.assertRaises(ValueError):
            self.engine.parse_xdp_packet(b"")

    def test_non_bytes_input_raises_rather_than_crashing_on_slice(self):
        with self.assertRaises(XDPProtocolError):
            self.engine.parse_xdp_packet("not bytes")

    def test_truncated_payload_is_skipped_not_raised(self):
        full = _packet([add_order(order_id=1, symbol_index=101, price_raw=1_000_000,
                                  volume=1)])
        truncated = full[:-10]
        msgs = self.engine.parse_xdp_packet(truncated)

        self.assertEqual(msgs, [])
        self.assertEqual(self.engine.active_orders, {})
        self.assertEqual(self.engine.messages_skipped, 1)
        self.assertEqual(self.engine.generate_report().status, "FEED_PARSER_DEGRADED")

    def test_zero_msg_size_abandons_the_packet_instead_of_spinning(self):
        """A MsgSize below the 4-byte header cannot advance the read cursor."""
        bogus = struct.pack("<HH", 0, 100) + b"\x00" * 40
        packet = _packet([bogus], num_msgs=8)

        msgs = self.engine.parse_xdp_packet(packet)
        self.assertEqual(msgs, [])
        self.assertEqual(self.engine.messages_skipped, 1)

    def test_msg_size_beyond_packet_end_is_rejected(self):
        overlong = struct.pack("<HH", 400, MSG_ADD_ORDER) + b"\x00" * 35
        msgs = self.engine.parse_xdp_packet(_packet([overlong]))
        self.assertEqual(msgs, [])
        self.assertEqual(self.engine.messages_skipped, 1)

    def test_declared_pkt_size_smaller_than_datagram_bounds_the_parse(self):
        """Trailing bytes past PktSize must not be parsed as a second message."""
        body = add_order(order_id=1, symbol_index=101, price_raw=1_000_000, volume=1)
        stray = add_order(order_id=2, symbol_index=101, price_raw=1_000_000, volume=1)
        packet = _packet([body, stray], num_msgs=2, pkt_size=16 + len(body))

        msgs = self.engine.parse_xdp_packet(packet)
        self.assertEqual(len(msgs), 1)
        self.assertIn(1, self.engine.active_orders)
        self.assertNotIn(2, self.engine.active_orders)

    def test_strict_mode_raises_on_a_malformed_message(self):
        engine = NYSEArcaIntegratedFeedEngine(
            symbol_index_map={101: "SPY"}, price_scale_codes={101: 4}, strict=True
        )
        full = _packet([add_order(order_id=1, symbol_index=101, price_raw=1_000_000,
                                  volume=1)])
        with self.assertRaises(XDPProtocolError):
            engine.parse_xdp_packet(full[:-10])

    def test_malformed_message_logs_a_warning(self):
        with self.assertLogs(MODULE_LOGGER, level="WARNING"):
            self.engine.parse_xdp_packet(
                _packet([add_order(order_id=1, symbol_index=101,
                                   price_raw=1_000_000, volume=1)])[:-10]
            )


class TestSpecVersionTolerance(BaseEngineTest):
    """Common v2.4k section 3.1.1: never hard-code message sizes."""

    def test_longer_message_variant_decodes_and_the_next_message_is_found(self):
        """A future revision appending fields must not break an unmodified client."""
        packet = _packet(
            [
                add_order(order_id=1, symbol_index=101, price_raw=1_000_000, volume=50,
                          trailing=b"\x01\x02\x03\x04\x05\x06"),
                delete_order(order_id=1, symbol_index=101),
            ],
            seq_num=1,
        )
        msgs = self.engine.parse_xdp_packet(packet)

        self.assertEqual([m.msg_type for m in msgs], [MSG_ADD_ORDER, MSG_DELETE_ORDER])
        self.assertEqual(msgs[0].volume, 50)
        self.assertEqual(self.engine.active_orders, {})
        self.assertEqual(self.engine.messages_skipped, 0)

    def test_shorter_legacy_variant_is_rejected_not_misread(self):
        """Pre-Pillar Arca v1.16b Msg 100 is 31 bytes with a 4-byte OrderID."""
        legacy = bytearray(31)
        struct.pack_into("<HH", legacy, 0, 31, MSG_ADD_ORDER)
        struct.pack_into("<I", legacy, 4, 500_000)     # SourceTimeNS
        struct.pack_into("<I", legacy, 8, 101)         # SymbolIndex
        struct.pack_into("<I", legacy, 12, 1)          # SymbolSeqNum
        struct.pack_into("<I", legacy, 16, 88001)      # OrderID, 4 bytes in v1.16b
        struct.pack_into("<I", legacy, 20, 4_500_000)  # Price
        struct.pack_into("<I", legacy, 24, 500)        # Volume
        legacy[28:29] = b"B"                           # Side

        msgs = self.engine.parse_xdp_packet(_packet([bytes(legacy)]))

        self.assertEqual(msgs, [])
        self.assertEqual(self.engine.active_orders, {})
        self.assertEqual(self.engine.messages_skipped, 1)

    def test_unhandled_message_type_is_counted_and_iteration_continues(self):
        imbalance = _message(105, 20, [(4, _u32(0))])  # not decoded by this engine
        packet = _packet(
            [
                imbalance,
                add_order(order_id=1, symbol_index=101, price_raw=1_000_000, volume=1),
            ],
            seq_num=1,
        )
        msgs = self.engine.parse_xdp_packet(packet)

        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0].msg_type, MSG_ADD_ORDER)
        self.assertEqual(self.engine.unhandled_message_types, {105: 1})
        self.assertEqual(self.engine.messages_skipped, 0)
        # Unhandled types are informational, not a book-integrity problem.
        self.assertEqual(self.engine.generate_report().status, "FEED_PARSER_SUCCESS")


class TestReporting(BaseEngineTest):
    def test_clean_run_reports_success_with_accurate_counts(self):
        self.engine.parse_xdp_packet(
            _packet(
                [
                    add_order(order_id=1, symbol_index=101, price_raw=1_000_000,
                              volume=10),
                    add_order(order_id=2, symbol_index=101, price_raw=1_000_000,
                              volume=20),
                ],
                seq_num=1,
            )
        )
        report = self.engine.generate_report()

        self.assertEqual(report.status, "FEED_PARSER_SUCCESS")
        self.assertEqual(report.total_messages_parsed, 2)
        self.assertEqual(report.active_orders_count, 2)
        self.assertFalse(report.book_is_stale)
        self.assertEqual(report.sequence_gaps, [])
        self.assertIn("v2.5", report.spec_version)

    def test_report_snapshots_gaps_rather_than_aliasing_engine_state(self):
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=1, symbol_index=101, price_raw=1_000_000,
                               volume=1)], seq_num=1)
        )
        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=2, symbol_index=101, price_raw=1_000_000,
                               volume=1)], seq_num=9)
        )
        report = self.engine.generate_report()
        self.assertEqual(len(report.sequence_gaps), 1)

        self.engine.parse_xdp_packet(
            _packet([add_order(order_id=3, symbol_index=101, price_raw=1_000_000,
                               volume=1)], seq_num=50)
        )
        self.assertEqual(len(report.sequence_gaps), 1)
        self.assertEqual(len(self.engine.sequence_gaps), 2)

    def test_report_carries_the_last_parsed_message(self):
        msgs = self.engine.parse_xdp_packet(
            _packet([add_order(order_id=1, symbol_index=101, price_raw=1_000_000,
                               volume=10)])
        )
        report = self.engine.generate_report(msgs[-1])
        self.assertIs(report.last_parsed_message, msgs[-1])


if __name__ == "__main__":
    unittest.main()

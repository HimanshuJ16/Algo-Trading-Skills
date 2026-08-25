import struct
import unittest

from euronext_optiq_market_data_integration import (
    BookState,
    EuronextOptiqMarketDataEngine,
    MarketDataPacketHeader,
    NULL_PRICE,
    NULL_UINT8,
    OrderEntryQualifier,
    PACKET_HEADER_LENGTH,
    SBE_MESSAGE_HEADER_LENGTH,
    Side,
    TEMPLATE_MARKET_STATUS_CHANGE,
    TEMPLATE_MARKET_UPDATE,
    TEMPLATE_ORDER_UPDATE,
    iter_sbe_messages,
    parse_market_data_packet_header,
    parse_sbe_message_header,
    scale_price,
)

# LVMH on Euronext Paris. Price/Index Level Decimals of 4 means EUR 785.00 is
# carried on the wire as the integer 7_850_000 (spec section 5.4).
LVMH_ISIN = "FR0000121014"
LVMH_SYMBOL_INDEX = 110000
DECIMALS = 4


def build_packet_header(
    packet_time_ns: int = 1_700_000_000_000_000_000,
    psn: int = 1,
    flags: int = 0,
    channel_id: int = 101,
) -> bytes:
    """Encode a packet header exactly as spec section 4.2 lays it out."""
    return struct.pack("<QIHH", packet_time_ns, psn, flags, channel_id)


def build_message(template_id: int, block: bytes, schema_id: int = 1, version: int = 362) -> bytes:
    frame = SBE_MESSAGE_HEADER_LENGTH + len(block)
    return struct.pack("<HHHHH", frame, len(block), template_id, schema_id, version) + block


class TestPacketHeaderParsing(unittest.TestCase):
    """Field order and widths are independently derived from spec section 4.2."""

    def test_header_fields_map_to_documented_offsets(self):
        raw = build_packet_header(
            packet_time_ns=1_700_000_000_123_456_789, psn=4_294_967_000, flags=0, channel_id=1042)
        header = parse_market_data_packet_header(raw)

        self.assertEqual(len(raw), PACKET_HEADER_LENGTH)
        self.assertEqual(header.packet_time_ns, 1_700_000_000_123_456_789)
        self.assertEqual(header.packet_sequence_number, 4_294_967_000)
        self.assertEqual(header.channel_id, 1042)

    def test_packet_time_occupies_the_first_eight_bytes(self):
        # Regression: an earlier layout read the sequence number as a uint64 at
        # offset 4, which silently swallowed the timestamp's high bytes.
        raw = build_packet_header(packet_time_ns=2 ** 63 + 7, psn=9, flags=0, channel_id=1)
        header = parse_market_data_packet_header(raw)
        self.assertEqual(header.packet_time_ns, 2 ** 63 + 7)
        self.assertEqual(header.packet_sequence_number, 9)

    def test_packet_flags_bits_decode_per_spec(self):
        # bit 0 compression, bits 1-3 restart counter, bits 4-6 PSN high bits.
        flags = 0b1 | (0b011 << 1) | (0b101 << 4)
        header = parse_market_data_packet_header(build_packet_header(psn=5, flags=flags))
        self.assertTrue(header.is_compressed)
        self.assertEqual(header.restart_counter, 3)
        self.assertEqual(header.psn_high_bits, 5)
        self.assertEqual(header.effective_sequence_number, (5 << 32) | 5)

    def test_snapshot_flag_bits(self):
        start = parse_market_data_packet_header(build_packet_header(flags=1 << 7))
        end = parse_market_data_packet_header(build_packet_header(flags=1 << 8))
        self.assertTrue(start.contains_snapshot_start)
        self.assertFalse(start.contains_snapshot_end)
        self.assertTrue(end.contains_snapshot_end)

    def test_short_packet_rejected(self):
        with self.assertRaises(ValueError):
            parse_market_data_packet_header(bytes(PACKET_HEADER_LENGTH - 1))

    def test_non_bytes_rejected(self):
        with self.assertRaises(TypeError):
            parse_market_data_packet_header("not bytes")


class TestSbeMessageFraming(unittest.TestCase):

    def test_frame_and_sbe_header_fields(self):
        block = bytes(range(18))
        header = parse_sbe_message_header(build_message(TEMPLATE_MARKET_UPDATE, block))
        self.assertEqual(header.frame_length, SBE_MESSAGE_HEADER_LENGTH + 18)
        self.assertEqual(header.block_length, 18)
        self.assertEqual(header.template_id, TEMPLATE_MARKET_UPDATE)
        self.assertEqual(header.schema_version, 362)

    def test_iterates_multiple_messages_in_one_packet_body(self):
        body = (
            build_message(TEMPLATE_MARKET_UPDATE, bytes(18))
            + build_message(TEMPLATE_MARKET_STATUS_CHANGE, bytes(12))
            + build_message(TEMPLATE_ORDER_UPDATE, bytes(4))
        )
        parsed = list(iter_sbe_messages(body))
        self.assertEqual(
            [h.template_id for h, _ in parsed],
            [TEMPLATE_MARKET_UPDATE, TEMPLATE_MARKET_STATUS_CHANGE, TEMPLATE_ORDER_UPDATE])
        self.assertEqual([len(p) for _, p in parsed], [18, 12, 4])

    def test_truncated_packet_raises_instead_of_yielding_partial_message(self):
        body = build_message(TEMPLATE_MARKET_UPDATE, bytes(18))[:-4]
        with self.assertRaises(ValueError):
            list(iter_sbe_messages(body))

    def test_frame_shorter_than_header_rejected(self):
        with self.assertRaises(ValueError):
            parse_sbe_message_header(struct.pack("<HHHHH", 4, 0, 1001, 1, 362))

    def test_frame_over_message_maximum_rejected(self):
        with self.assertRaises(ValueError):
            parse_sbe_message_header(struct.pack("<HHHHH", 1385, 0, 1001, 1, 362))

    def test_block_length_overrunning_the_frame_rejected(self):
        # Frame 20 leaves 10 bytes for a block that claims 40.
        with self.assertRaises(ValueError):
            parse_sbe_message_header(struct.pack("<HHHHH", 20, 40, 1001, 1, 362))


class TestPriceScaling(unittest.TestCase):

    def test_documented_scaling_example(self):
        # Spec section 5.4: 275600 with 4 decimals is 27.56.
        self.assertAlmostEqual(scale_price(275600, 4), 27.56, places=10)

    def test_zero_decimals_is_identity(self):
        self.assertEqual(scale_price(785, 0), 785.0)

    def test_null_price_is_none_but_zero_is_a_price(self):
        self.assertIsNone(scale_price(NULL_PRICE, 4))
        self.assertEqual(scale_price(0, 4), 0.0)

    def test_negative_price_supported(self):
        # Negative prices are authorised on the ETF NAV order book (EMM 8).
        self.assertAlmostEqual(scale_price(-12500, 4), -1.25, places=10)

    def test_invalid_decimals_rejected(self):
        with self.assertRaises(ValueError):
            scale_price(1, -1)
        with self.assertRaises(ValueError):
            scale_price(1, 19)


class TestBookMaintenance(unittest.TestCase):

    def setUp(self):
        self.engine = EuronextOptiqMarketDataEngine(price_decimals=DECIMALS)

    def test_quantity_zero_deletes_the_limit(self):
        self.engine.apply_limit_update(Side.BID, 7_850_000, 500)
        self.engine.apply_limit_update(Side.BID, 7_845_000, 300)
        self.engine.apply_limit_update(Side.BID, 7_850_000, 0)

        self.assertNotIn(7_850_000, self.engine.bids)
        self.assertEqual(self.engine.best_bid_level().price_raw, 7_845_000)

    def test_deleting_an_unknown_limit_is_a_no_op(self):
        self.engine.apply_limit_update(Side.ASK, 7_855_000, 0)
        self.assertEqual(self.engine.asks, {})

    def test_null_price_update_does_not_create_a_level(self):
        # Market orders and side clears arrive with the null price.
        self.engine.apply_limit_update(Side.BID, NULL_PRICE, 1_000)
        self.assertEqual(self.engine.bids, {})

    def test_zero_price_level_is_kept_and_reported(self):
        # Regression: a truthiness check on the best price dropped a 0.0 limit.
        self.engine.apply_limit_update(Side.BID, 0, 400)
        self.engine.apply_limit_update(Side.ASK, 5_000, 100)
        report = self._report()
        self.assertEqual(report.best_bid, 0.0)
        self.assertAlmostEqual(report.book_imbalance_ratio, 0.6, places=10)

    def test_clear_book_drops_every_limit(self):
        self.engine.apply_limit_update(Side.BID, 7_850_000, 500)
        self.engine.apply_limit_update(Side.ASK, 7_855_000, 200)
        self.engine.clear_book()
        self.assertEqual((self.engine.bids, self.engine.asks), ({}, {}))

    def test_depth_is_ordered_from_the_touch(self):
        for raw, qty in ((7_850_000, 500), (7_845_000, 300), (7_855_000, 100)):
            self.engine.apply_limit_update(Side.BID, raw, qty)
        prices = [level.price_raw for level in self.engine.depth(Side.BID, 3)]
        self.assertEqual(prices, [7_855_000, 7_850_000, 7_845_000])

    def test_ask_depth_ascends(self):
        for raw in (7_860_000, 7_855_000, 7_870_000):
            self.engine.apply_limit_update(Side.ASK, raw, 100)
        prices = [level.price_raw for level in self.engine.depth(Side.ASK, 2)]
        self.assertEqual(prices, [7_855_000, 7_860_000])

    def test_equal_decimal_prices_collapse_to_one_level(self):
        # 0.1 + 0.2 != 0.3 in binary floats; integer keys make this exact.
        engine = EuronextOptiqMarketDataEngine(price_decimals=2)
        engine.update_book_level("BUY", 0.1 + 0.2, 100)
        engine.update_book_level("BUY", 0.3, 250)
        self.assertEqual(len(engine.bids), 1)
        self.assertEqual(engine.best_bid_level().quantity, 250)

    def test_negative_quantity_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.apply_limit_update(Side.BID, 7_850_000, -1)

    def test_float_price_raw_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.apply_limit_update(Side.BID, 7_850_000.0, 100)

    def test_unknown_side_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.update_book_level("LONG", 785.0, 100)

    def test_missing_price_decimals_is_a_hard_error(self):
        with self.assertRaises(TypeError):
            EuronextOptiqMarketDataEngine()  # pylint: disable=no-value-for-parameter

    def _report(self):
        return self.engine.process_optiq_message(
            isin=LVMH_ISIN,
            symbol_index=LVMH_SYMBOL_INDEX,
            template_id=TEMPLATE_MARKET_UPDATE,
            market_data_sequence_number=1,
            event_time_ns=1_700_000_000_000_000_000,
        )


class TestSequenceContinuity(unittest.TestCase):

    def setUp(self):
        self.engine = EuronextOptiqMarketDataEngine(price_decimals=DECIMALS)

    def _observe(self, psn, flags=0):
        return self.engine.observe_packet(
            parse_market_data_packet_header(build_packet_header(psn=psn, flags=flags)))

    def test_contiguous_packets_report_no_gap(self):
        self.engine.mark_book_synchronized()
        for psn in (1, 2, 3):
            observation = self._observe(psn)
            self.assertEqual(observation.gap_size, 0)
        self.assertTrue(self.engine.book_synchronized)

    def test_gap_is_sized_and_desynchronizes_the_book(self):
        self.engine.mark_book_synchronized()
        self._observe(101)
        observation = self._observe(105)

        self.assertEqual(observation.gap_size, 3)  # 102, 103, 104 are missing
        self.assertFalse(self.engine.book_synchronized)

    def test_duplicate_packet_is_flagged_and_not_a_gap(self):
        self.engine.mark_book_synchronized()
        self._observe(7)
        observation = self._observe(7)

        self.assertTrue(observation.is_duplicate)
        self.assertEqual(observation.gap_size, 0)
        self.assertTrue(self.engine.book_synchronized)

    def test_reordered_packet_does_not_manufacture_a_gap(self):
        self.engine.mark_book_synchronized()
        self._observe(10)
        self._observe(11)
        observation = self._observe(10)

        self.assertTrue(observation.is_out_of_order)
        self.assertEqual(observation.gap_size, 0)
        self.assertEqual(self.engine.last_packet_sequence_number, 11)
        self.assertTrue(self.engine.book_synchronized)

    def test_psn_rollover_uses_the_packet_flag_high_bits(self):
        self.engine.mark_book_synchronized()
        self._observe(2 ** 32 - 2, flags=0)
        observation = self._observe(0, flags=(1 << 4))  # high bits become 1

        self.assertEqual(observation.header.effective_sequence_number, 2 ** 32)
        self.assertEqual(observation.gap_size, 1)  # 2^32 - 1 was missed

    def test_mdg_restart_desynchronizes_even_though_psn_moves_backwards(self):
        self.engine.mark_book_synchronized()
        self._observe(5_000)
        observation = self._observe(1, flags=(1 << 1))  # restart counter 0 -> 1

        self.assertTrue(observation.mdg_restart_detected)
        self.assertFalse(observation.is_out_of_order)
        self.assertFalse(self.engine.book_synchronized)
        self.assertEqual(self.engine.last_packet_sequence_number, 1)

    def test_non_header_argument_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.observe_packet((1, 2, 3, 4))

    def test_frozen_header_is_hashable_for_dedup_sets(self):
        header = MarketDataPacketHeader(1, 2, 3, 4)
        self.assertIn(header, {MarketDataPacketHeader(1, 2, 3, 4)})


class TestMicrostructureAndQuotingGate(unittest.TestCase):

    def setUp(self):
        self.engine = EuronextOptiqMarketDataEngine(price_decimals=DECIMALS)
        self.engine.mark_book_synchronized()
        self.engine.apply_market_status_change(
            book_state=BookState.CONTINUOUS, order_entry_qualifier=OrderEntryQualifier.ENABLED)

    def _report(self):
        return self.engine.process_optiq_message(
            isin=LVMH_ISIN,
            symbol_index=LVMH_SYMBOL_INDEX,
            template_id=TEMPLATE_MARKET_UPDATE,
            market_data_sequence_number=1001,
            event_time_ns=1_700_000_000_000_000_000,
        )

    def _seed_lvmh_book(self):
        self.engine.apply_limit_update(Side.BID, 7_850_000, 500)   # EUR 785.00
        self.engine.apply_limit_update(Side.ASK, 7_855_000, 200)   # EUR 785.50

    def test_mid_spread_and_imbalance(self):
        self._seed_lvmh_book()
        report = self._report()

        self.assertTrue(report.is_quoting_allowed)
        self.assertEqual(report.best_bid, 785.00)
        self.assertEqual(report.best_ask, 785.50)
        self.assertEqual(report.mid_price, 785.25)
        self.assertEqual(report.spread, 0.50)
        # (500 - 200) / (500 + 200) = 3/7 = 0.428571...
        self.assertAlmostEqual(report.book_imbalance_ratio, 0.4286, places=4)
        self.assertFalse(report.is_crossed)

    def test_mid_price_is_exact_at_a_half_tick(self):
        # A float sum of 0.07 and 0.08 mid-points drifts; integer maths does not.
        engine = EuronextOptiqMarketDataEngine(price_decimals=2)
        engine.mark_book_synchronized()
        engine.apply_market_status_change(5, 1)
        engine.apply_limit_update(Side.BID, 7, 10)
        engine.apply_limit_update(Side.ASK, 8, 10)
        report = engine.process_optiq_message("X", 1, TEMPLATE_MARKET_UPDATE, 1, 1)
        self.assertEqual(report.mid_price, 0.075)
        self.assertEqual(report.spread, 0.01)

    def test_one_sided_book_has_no_mid_and_saturated_imbalance(self):
        self.engine.apply_limit_update(Side.BID, 7_850_000, 500)
        report = self._report()

        self.assertIsNone(report.mid_price)
        self.assertIsNone(report.spread)
        self.assertEqual(report.book_imbalance_ratio, 1.0)

    def test_empty_book_imbalance_is_zero_not_a_division_error(self):
        report = self._report()
        self.assertEqual(report.book_imbalance_ratio, 0.0)
        self.assertIsNone(report.best_bid)

    def test_halted_book_state_blocks_quoting(self):
        self._seed_lvmh_book()
        self.engine.apply_market_status_change(book_state=BookState.HALTED)
        report = self._report()

        self.assertFalse(report.is_quoting_allowed)
        self.assertEqual(report.trading_status, "HALTED")
        self.assertFalse(report.is_continuous_trading)
        self.assertIn("HALTED", report.audit_notes)

    def test_reserved_and_suspended_states_block_quoting(self):
        self._seed_lvmh_book()
        for state in (BookState.RESERVED, BookState.SUSPENDED, BookState.CALL,
                      BookState.UNCROSSING, BookState.CLOSED, BookState.INACCESSIBLE):
            self.engine.apply_market_status_change(book_state=state)
            self.assertFalse(self._report().is_quoting_allowed, state.name)

    def test_order_entry_disabled_blocks_quoting_even_in_continuous(self):
        self._seed_lvmh_book()
        self.engine.apply_market_status_change(
            order_entry_qualifier=OrderEntryQualifier.CANCEL_ONLY)
        report = self._report()

        self.assertTrue(report.is_continuous_trading)
        self.assertFalse(report.is_order_entry_allowed)
        self.assertFalse(report.is_quoting_allowed)

    def test_unsynchronized_book_blocks_quoting(self):
        self._seed_lvmh_book()
        self.engine.observe_packet(parse_market_data_packet_header(build_packet_header(psn=1)))
        self.engine.observe_packet(parse_market_data_packet_header(build_packet_header(psn=9)))
        report = self._report()

        self.assertFalse(report.is_book_synchronized)
        self.assertFalse(report.is_quoting_allowed)
        self.assertIn("not synchronized", report.audit_notes)

    def test_crossed_book_blocks_quoting_during_continuous_trading(self):
        self.engine.apply_limit_update(Side.BID, 7_860_000, 500)
        self.engine.apply_limit_update(Side.ASK, 7_855_000, 200)
        report = self._report()

        self.assertTrue(report.is_crossed)
        self.assertFalse(report.is_quoting_allowed)

    def test_locked_book_counts_as_crossed(self):
        self.engine.apply_limit_update(Side.BID, 7_855_000, 500)
        self.engine.apply_limit_update(Side.ASK, 7_855_000, 200)
        self.assertTrue(self._report().is_crossed)

    def test_null_status_fields_leave_state_untouched(self):
        self.engine.apply_market_status_change(
            book_state=NULL_UINT8, order_entry_qualifier=NULL_UINT8)
        self.assertIs(self.engine.book_state, BookState.CONTINUOUS)
        self.assertIs(self.engine.order_entry_qualifier, OrderEntryQualifier.ENABLED)

    def test_unknown_book_state_value_raises_rather_than_being_coerced(self):
        with self.assertRaises(ValueError):
            self.engine.apply_market_status_change(book_state=99)

    def test_unset_state_defaults_to_no_quoting(self):
        engine = EuronextOptiqMarketDataEngine(price_decimals=DECIMALS)
        engine.mark_book_synchronized()
        report = engine.process_optiq_message("X", 1, TEMPLATE_MARKET_UPDATE, 0, 0)
        self.assertEqual(report.trading_status, "UNKNOWN")
        self.assertFalse(report.is_quoting_allowed)

    def test_market_data_sequence_number_is_recorded_not_gap_checked(self):
        # The MDSN legitimately jumps on a single channel (spec section 5.3.2).
        self._seed_lvmh_book()
        self.engine.process_optiq_message("X", 1, TEMPLATE_MARKET_UPDATE, 10, 1)
        report = self.engine.process_optiq_message("X", 1, TEMPLATE_MARKET_UPDATE, 9_000, 1)

        self.assertEqual(self.engine.last_market_data_sequence_number, 9_000)
        self.assertTrue(report.is_book_synchronized)
        self.assertTrue(report.is_quoting_allowed)

    def test_negative_sequence_and_time_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.process_optiq_message("X", 1, TEMPLATE_MARKET_UPDATE, -1, 1)
        with self.assertRaises(ValueError):
            self.engine.process_optiq_message("X", 1, TEMPLATE_MARKET_UPDATE, 1, -1)


if __name__ == "__main__":
    unittest.main()

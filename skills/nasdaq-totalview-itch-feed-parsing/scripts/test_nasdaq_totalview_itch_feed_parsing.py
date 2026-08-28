"""Unit tests for the Nasdaq TotalView-ITCH 5.0 parser engine.

Test messages are assembled field-by-field from the offsets and lengths printed
in the ITCH 5.0 specification tables, deliberately *not* by reusing the engine's
own ``struct`` format strings. A test that packs with the same format string the
implementation unpacks with cannot detect a wrong layout.
"""

import logging
import unittest

from nasdaq_totalview_itch_feed_parsing import (
    ITCHBookIntegrityError,
    ITCHParseError,
    ITCHParsedMessage,
    ITCHParserReport,
    MAX_PRICE_TICKS,
    NasdaqITCH50ParserEngine,
)


def _uint(value: int, length: int) -> bytes:
    """Big-endian unsigned integer, per ITCH 5.0 Data Types."""
    return value.to_bytes(length, byteorder="big", signed=False)


def _alpha(value: str, length: int) -> bytes:
    """ASCII alpha field, left justified and right padded with spaces."""
    encoded = value.encode("ascii")
    if len(encoded) > length:
        raise ValueError(f"{value!r} exceeds {length}-byte alpha field")
    return encoded.ljust(length, b" ")


class ITCHMessageBuilder:
    """Builds ITCH 5.0 messages straight from the spec's offset tables."""

    @staticmethod
    def add_order(ref, side="B", shares=100, stock="AAPL", price=1_500_000,
                  ts=123_456_789, locate=1, tracking=2, attribution=None):
        """Spec 1.3.1 'A' (36 bytes) / 1.3.2 'F' (40 bytes)."""
        body = (
            _alpha("F" if attribution is not None else "A", 1)  # offset 0
            + _uint(locate, 2)                                  # offset 1
            + _uint(tracking, 2)                                # offset 3
            + _uint(ts, 6)                                      # offset 5
            + _uint(ref, 8)                                     # offset 11
            + _alpha(side, 1)                                   # offset 19
            + _uint(shares, 4)                                  # offset 20
            + _alpha(stock, 8)                                  # offset 24
            + _uint(price, 4)                                   # offset 32
        )
        if attribution is not None:
            body += _alpha(attribution, 4)                      # offset 36
        return body

    @staticmethod
    def order_executed(ref, exec_shares, match=9999, ts=123_456_789,
                       locate=1, tracking=2):
        """Spec 1.4.1 'E' (31 bytes)."""
        return (
            _alpha("E", 1)
            + _uint(locate, 2)
            + _uint(tracking, 2)
            + _uint(ts, 6)
            + _uint(ref, 8)          # offset 11
            + _uint(exec_shares, 4)  # offset 19
            + _uint(match, 8)        # offset 23
        )

    @staticmethod
    def order_executed_with_price(ref, exec_shares, price, printable="Y",
                                  match=9999, ts=123_456_789, locate=1, tracking=2):
        """Spec 1.4.2 'C' (36 bytes)."""
        return (
            _alpha("C", 1)
            + _uint(locate, 2)
            + _uint(tracking, 2)
            + _uint(ts, 6)
            + _uint(ref, 8)           # offset 11
            + _uint(exec_shares, 4)   # offset 19
            + _uint(match, 8)         # offset 23
            + _alpha(printable, 1)    # offset 31
            + _uint(price, 4)         # offset 32
        )

    @staticmethod
    def order_cancel(ref, cancel_shares, ts=123_456_789, locate=1, tracking=2):
        """Spec 1.4.3 'X' (23 bytes)."""
        return (
            _alpha("X", 1)
            + _uint(locate, 2)
            + _uint(tracking, 2)
            + _uint(ts, 6)
            + _uint(ref, 8)             # offset 11
            + _uint(cancel_shares, 4)   # offset 19
        )

    @staticmethod
    def order_delete(ref, ts=123_456_789, locate=1, tracking=2):
        """Spec 1.4.4 'D' (19 bytes)."""
        return (
            _alpha("D", 1)
            + _uint(locate, 2)
            + _uint(tracking, 2)
            + _uint(ts, 6)
            + _uint(ref, 8)             # offset 11
        )

    @staticmethod
    def order_replace(orig_ref, new_ref, shares, price, ts=123_456_789,
                      locate=1, tracking=2):
        """Spec 1.4.5 'U' (35 bytes)."""
        return (
            _alpha("U", 1)
            + _uint(locate, 2)
            + _uint(tracking, 2)
            + _uint(ts, 6)
            + _uint(orig_ref, 8)   # offset 11
            + _uint(new_ref, 8)    # offset 19
            + _uint(shares, 4)     # offset 27
            + _uint(price, 4)      # offset 31
        )

    @staticmethod
    def trade(shares=250, stock="MSFT", price=3_000_000, ref=0, side="B",
              match=4242, ts=123_456_789, locate=1, tracking=2):
        """Spec 1.5.1 'P' (44 bytes)."""
        return (
            _alpha("P", 1)
            + _uint(locate, 2)
            + _uint(tracking, 2)
            + _uint(ts, 6)
            + _uint(ref, 8)       # offset 11
            + _alpha(side, 1)     # offset 19
            + _uint(shares, 4)    # offset 20
            + _alpha(stock, 8)    # offset 24
            + _uint(price, 4)     # offset 32
            + _uint(match, 8)     # offset 36
        )


B = ITCHMessageBuilder


class TestSpecConformance(unittest.TestCase):
    """The decoder's layouts must match the spec's published message lengths."""

    SPEC_LENGTHS = {"A": 36, "F": 40, "E": 31, "C": 36, "X": 23, "D": 19, "U": 35, "P": 44}

    def test_struct_sizes_match_spec_message_lengths(self):
        for msg_type, total in self.SPEC_LENGTHS.items():
            layout = getattr(NasdaqITCH50ParserEngine, f"STRUCT_{msg_type}")
            self.assertEqual(
                layout.size + 1, total,
                f"'{msg_type}' layout is {layout.size + 1} bytes, spec says {total}",
            )

    def test_builder_produces_spec_length_messages(self):
        built = {
            "A": B.add_order(1),
            "F": B.add_order(1, attribution="NSDQ"),
            "E": B.order_executed(1, 10),
            "C": B.order_executed_with_price(1, 10, 1_500_000),
            "X": B.order_cancel(1, 10),
            "D": B.order_delete(1),
            "U": B.order_replace(1, 2, 10, 1_500_000),
            "P": B.trade(),
        }
        for msg_type, raw in built.items():
            self.assertEqual(len(raw), self.SPEC_LENGTHS[msg_type], msg_type)

    def test_all_layouts_are_big_endian(self):
        for msg_type in self.SPEC_LENGTHS:
            layout = getattr(NasdaqITCH50ParserEngine, f"STRUCT_{msg_type}")
            self.assertTrue(layout.format.startswith(">"), msg_type)

    def test_price_divisor_and_maximum_match_spec(self):
        # Spec, Data Types: Price (4) has 4 implied decimals; max 200,000.0000.
        self.assertEqual(NasdaqITCH50ParserEngine.PRICE_DIVISOR, 10000.0)
        self.assertEqual(MAX_PRICE_TICKS, 0x77359400)
        self.assertEqual(MAX_PRICE_TICKS / NasdaqITCH50ParserEngine.PRICE_DIVISOR, 200_000.0)


class TestNasdaqITCH50ParserEngine(unittest.TestCase):

    def setUp(self):
        self.engine = NasdaqITCH50ParserEngine()
        # Integrity violations log at WARNING; silence them for expected cases.
        logging.disable(logging.CRITICAL)
        self.addCleanup(logging.disable, logging.NOTSET)

    # -- Add / Execute / Delete: original behaviour, preserved ---------------

    def test_add_order_and_execute(self):
        msg_a = self.engine.parse_message(
            B.add_order(1001, side="B", shares=100, stock="AAPL", price=1_500_000)
        )
        self.assertEqual(msg_a.message_type, "A")
        self.assertEqual(msg_a.order_ref_number, 1001)
        self.assertEqual(msg_a.stock, "AAPL")
        self.assertEqual(msg_a.side, "B")
        self.assertEqual(msg_a.shares, 100)
        # $150.00 encoded as Price (4): 1_500_000 / 10_000.
        self.assertEqual(msg_a.price_usd, 150.00)
        self.assertEqual(msg_a.price_ticks, 1_500_000)
        self.assertEqual(msg_a.attribution, None)
        self.assertEqual(len(self.engine.active_orders), 1)

        msg_e = self.engine.parse_message(B.order_executed(1001, 40, match=9999))
        self.assertEqual(msg_e.message_type, "E")
        self.assertEqual(msg_e.executed_shares, 40)
        self.assertEqual(msg_e.match_number, 9999)
        self.assertEqual(self.engine.active_orders[1001].shares, 60)
        self.assertEqual(self.engine.integrity_violation_count, 0)

    def test_order_delete(self):
        self.engine.parse_message(
            B.add_order(2002, side="S", shares=50, stock="MSFT", price=3_000_000)
        )
        self.assertEqual(len(self.engine.active_orders), 1)

        msg_d = self.engine.parse_message(B.order_delete(2002))
        self.assertEqual(msg_d.message_type, "D")
        self.assertEqual(len(self.engine.active_orders), 0)
        self.assertEqual(self.engine.integrity_violation_count, 0)

    def test_timestamp_is_48_bit_big_endian(self):
        # A value that needs all 6 bytes: any 32-bit or truncating decode fails.
        ts = (1 << 47) + 12345
        msg = self.engine.parse_message(B.add_order(1, ts=ts))
        self.assertEqual(msg.timestamp_ns, ts)

    def test_full_execution_removes_order(self):
        self.engine.parse_message(B.add_order(1, shares=100))
        self.engine.parse_message(B.order_executed(1, 100))
        self.assertNotIn(1, self.engine.active_orders)
        self.assertEqual(self.engine.integrity_violation_count, 0)

    def test_partial_cancel_reduces_without_removing(self):
        self.engine.parse_message(B.add_order(1, shares=100))
        msg_x = self.engine.parse_message(B.order_cancel(1, 30))
        self.assertEqual(msg_x.canceled_shares, 30)
        self.assertEqual(self.engine.active_orders[1].shares, 70)

    def test_cumulative_deductions_across_message_types(self):
        # Spec 1.4: multiple Modify messages on one order are cumulative.
        self.engine.parse_message(B.add_order(1, shares=100))
        self.engine.parse_message(B.order_executed(1, 20))
        self.engine.parse_message(B.order_cancel(1, 30))
        self.engine.parse_message(B.order_executed_with_price(1, 10, 1_490_000))
        self.assertEqual(self.engine.active_orders[1].shares, 40)
        self.assertEqual(self.engine.integrity_violation_count, 0)

    # -- Add Order with MPID Attribution ('F') -------------------------------

    def test_add_order_with_mpid_attribution_rests_on_book(self):
        msg_f = self.engine.parse_message(
            B.add_order(3003, side="S", shares=200, stock="NVDA",
                        price=9_000_000, attribution="NSDQ")
        )
        self.assertEqual(msg_f.message_type, "F")
        self.assertEqual(msg_f.attribution, "NSDQ")
        self.assertEqual(msg_f.stock, "NVDA")
        self.assertEqual(msg_f.price_usd, 900.00)
        self.assertEqual(self.engine.active_orders[3003].shares, 200)

    def test_attributed_order_participates_in_normal_lifecycle(self):
        self.engine.parse_message(B.add_order(4004, shares=80, attribution="ABCD"))
        self.engine.parse_message(B.order_executed(4004, 80))
        self.assertNotIn(4004, self.engine.active_orders)
        self.assertEqual(self.engine.integrity_violation_count, 0)

    # -- Order Executed With Price ('C') -------------------------------------

    def test_executed_with_price_deducts_and_reports_price(self):
        self.engine.parse_message(B.add_order(1, shares=100, price=1_500_000))
        msg_c = self.engine.parse_message(
            B.order_executed_with_price(1, 25, 1_499_500, printable="Y")
        )
        self.assertEqual(msg_c.message_type, "C")
        self.assertEqual(msg_c.executed_shares, 25)
        self.assertEqual(msg_c.price_ticks, 1_499_500)
        self.assertEqual(msg_c.price_usd, 149.95)
        self.assertTrue(msg_c.printable)
        self.assertEqual(self.engine.active_orders[1].shares, 75)

    def test_non_printable_execution_flag_is_decoded(self):
        self.engine.parse_message(B.add_order(1, shares=100))
        msg_c = self.engine.parse_message(
            B.order_executed_with_price(1, 10, 1_500_000, printable="N")
        )
        self.assertFalse(msg_c.printable)

    def test_invalid_printable_flag_rejected(self):
        self.engine.parse_message(B.add_order(1, shares=100))
        with self.assertRaises(ITCHParseError):
            self.engine.parse_message(
                B.order_executed_with_price(1, 10, 1_500_000, printable="Z")
            )

    def test_plain_execute_carries_no_price(self):
        self.engine.parse_message(B.add_order(1, shares=100))
        msg_e = self.engine.parse_message(B.order_executed(1, 10))
        self.assertIsNone(msg_e.price_ticks)
        self.assertIsNone(msg_e.price_usd)

    # -- Order Replace ('U') -------------------------------------------------

    def test_replace_moves_order_to_new_reference_number(self):
        self.engine.parse_message(
            B.add_order(5005, side="S", shares=100, stock="TSLA", price=2_000_000)
        )
        msg_u = self.engine.parse_message(
            B.order_replace(5005, 6006, shares=40, price=2_010_000)
        )
        self.assertEqual(msg_u.message_type, "U")
        self.assertEqual(msg_u.order_ref_number, 5005)
        self.assertEqual(msg_u.new_order_ref_number, 6006)
        # Spec 1.4.5: the original ref is dead, the new ref is used henceforth.
        self.assertNotIn(5005, self.engine.active_orders)
        self.assertIn(6006, self.engine.active_orders)

    def test_replace_shares_are_absolute_not_a_deduction(self):
        # Spec 1.4.5: Shares is "The new total displayed quantity".
        self.engine.parse_message(B.add_order(1, shares=100))
        self.engine.parse_message(B.order_replace(1, 2, shares=40, price=1_500_000))
        self.assertEqual(self.engine.active_orders[2].shares, 40)

    def test_replace_inherits_side_and_stock_from_original(self):
        # Spec 1.4.5: side/stock are absent from 'U'; retain them from the Add.
        self.engine.parse_message(
            B.add_order(1, side="S", shares=100, stock="AMZN", price=1_800_000)
        )
        self.engine.parse_message(B.order_replace(1, 2, shares=60, price=1_810_000))
        replaced = self.engine.active_orders[2]
        self.assertEqual(replaced.side, "S")
        self.assertEqual(replaced.stock, "AMZN")
        self.assertEqual(replaced.price_ticks, 1_810_000)

    def test_replaced_order_accepts_later_updates_under_new_reference(self):
        self.engine.parse_message(B.add_order(1, shares=100))
        self.engine.parse_message(B.order_replace(1, 2, shares=60, price=1_500_000))
        self.engine.parse_message(B.order_executed(2, 60))
        self.assertEqual(len(self.engine.active_orders), 0)
        self.assertEqual(self.engine.integrity_violation_count, 0)

    def test_replace_of_unknown_order_invents_no_liquidity(self):
        msg_u = self.engine.parse_message(
            B.order_replace(111, 222, shares=50, price=1_500_000)
        )
        self.assertEqual(self.engine.violations_by_kind["UNKNOWN_ORDER"], 1)
        self.assertNotIn(222, self.engine.active_orders)
        self.assertEqual(len(self.engine.active_orders), 0)
        self.assertIsNone(msg_u.side)
        self.assertIsNone(msg_u.stock)

    # -- Trade Message ('P') -------------------------------------------------

    def test_trade_message_does_not_affect_the_book(self):
        # Spec 1.5.1: "Trade Messages do not affect the book".
        self.engine.parse_message(B.add_order(1, shares=100))
        msg_p = self.engine.parse_message(B.trade(shares=250, stock="MSFT",
                                                  price=3_000_000, match=4242))
        self.assertEqual(msg_p.message_type, "P")
        self.assertFalse(msg_p.affects_book)
        self.assertEqual(msg_p.shares, 250)
        self.assertEqual(msg_p.stock, "MSFT")
        self.assertEqual(msg_p.price_usd, 300.00)
        self.assertEqual(msg_p.match_number, 4242)
        # The resting order is untouched and no gap is reported.
        self.assertEqual(self.engine.active_orders[1].shares, 100)
        self.assertEqual(self.engine.integrity_violation_count, 0)

    def test_trade_message_zero_order_reference_is_not_a_book_gap(self):
        # Nasdaq has populated 'P' Order Reference Number as zero since 2010-12-06.
        msg_p = self.engine.parse_message(B.trade(ref=0))
        self.assertEqual(msg_p.order_ref_number, 0)
        self.assertEqual(self.engine.violations_by_kind["UNKNOWN_ORDER"], 0)

    # -- Book integrity accounting -------------------------------------------

    def test_execute_for_unknown_order_is_counted_not_absorbed(self):
        self.engine.parse_message(B.order_executed(777, 10))
        self.assertEqual(self.engine.violations_by_kind["UNKNOWN_ORDER"], 1)
        self.assertEqual(self.engine.integrity_violation_count, 1)

    def test_cancel_and_delete_for_unknown_order_are_counted(self):
        self.engine.parse_message(B.order_cancel(778, 10))
        self.engine.parse_message(B.order_delete(779))
        self.assertEqual(self.engine.violations_by_kind["UNKNOWN_ORDER"], 2)

    def test_over_execute_is_flagged_and_order_removed(self):
        self.engine.parse_message(B.add_order(1, shares=50))
        self.engine.parse_message(B.order_executed(1, 80))
        self.assertEqual(self.engine.violations_by_kind["OVER_EXECUTE"], 1)
        self.assertNotIn(1, self.engine.active_orders)

    def test_over_cancel_is_flagged_separately_from_over_execute(self):
        self.engine.parse_message(B.add_order(1, shares=50))
        self.engine.parse_message(B.order_cancel(1, 80))
        self.assertEqual(self.engine.violations_by_kind["OVER_CANCEL"], 1)
        self.assertEqual(self.engine.violations_by_kind["OVER_EXECUTE"], 0)

    def test_exact_size_execution_is_not_an_over_execute(self):
        self.engine.parse_message(B.add_order(1, shares=50))
        self.engine.parse_message(B.order_executed(1, 50))
        self.assertEqual(self.engine.violations_by_kind["OVER_EXECUTE"], 0)
        self.assertNotIn(1, self.engine.active_orders)

    def test_duplicate_order_reference_number_is_flagged(self):
        # Spec 1.3: the Order Reference Number is day-unique.
        self.engine.parse_message(B.add_order(1, shares=100))
        self.engine.parse_message(B.add_order(1, shares=200))
        self.assertEqual(self.engine.violations_by_kind["DUPLICATE_ORDER_ID"], 1)
        self.assertEqual(len(self.engine.active_orders), 1)

    def test_replace_onto_a_live_reference_is_flagged(self):
        self.engine.parse_message(B.add_order(1, shares=100))
        self.engine.parse_message(B.add_order(2, shares=100))
        self.engine.parse_message(B.order_replace(1, 2, shares=50, price=1_500_000))
        self.assertEqual(self.engine.violations_by_kind["DUPLICATE_ORDER_ID"], 1)

    def test_backwards_timestamp_is_flagged_but_equal_is_not(self):
        self.engine.parse_message(B.add_order(1, ts=1_000))
        self.engine.parse_message(B.add_order(2, ts=1_000))
        self.assertEqual(self.engine.violations_by_kind["TIMESTAMP_REGRESSION"], 0)
        self.engine.parse_message(B.add_order(3, ts=999))
        self.assertEqual(self.engine.violations_by_kind["TIMESTAMP_REGRESSION"], 1)

    def test_price_above_spec_maximum_is_flagged(self):
        self.engine.parse_message(B.add_order(1, price=MAX_PRICE_TICKS + 1))
        self.assertEqual(self.engine.violations_by_kind["PRICE_OUT_OF_RANGE"], 1)

    def test_price_at_spec_maximum_is_accepted(self):
        self.engine.parse_message(B.add_order(1, price=MAX_PRICE_TICKS))
        self.assertEqual(self.engine.violations_by_kind["PRICE_OUT_OF_RANGE"], 0)
        self.assertEqual(self.engine.active_orders[1].price_usd, 200_000.0)

    def test_strict_mode_raises_on_first_violation(self):
        strict = NasdaqITCH50ParserEngine(strict=True)
        with self.assertRaises(ITCHBookIntegrityError):
            strict.parse_message(B.order_executed(1, 10))

    def test_strict_mode_accepts_a_clean_replay(self):
        strict = NasdaqITCH50ParserEngine(strict=True)
        strict.parse_message(B.add_order(1, shares=100))
        strict.parse_message(B.order_cancel(1, 40))
        strict.parse_message(B.order_delete(1))
        self.assertEqual(strict.integrity_violation_count, 0)

    # -- Malformed input -----------------------------------------------------

    def test_empty_message_rejected(self):
        with self.assertRaises(ITCHParseError):
            self.engine.parse_message(b"")

    def test_unsupported_message_type_rejected(self):
        # 'S' System Event is a real ITCH type this engine deliberately skips.
        with self.assertRaises(ITCHParseError):
            self.engine.parse_message(b"S" + b"\x00" * 11)

    def test_non_printable_type_byte_rejected(self):
        with self.assertRaises(ITCHParseError):
            self.engine.parse_message(b"\x00" + b"\x00" * 35)

    def test_truncated_message_rejected(self):
        truncated = B.add_order(1)[:-1]
        with self.assertRaises(ITCHParseError):
            self.engine.parse_message(truncated)

    def test_message_with_trailing_bytes_rejected(self):
        # A leftover MoldUDP64 length prefix would show up exactly like this.
        with self.assertRaises(ITCHParseError):
            self.engine.parse_message(B.add_order(1) + b"\x00\x24")

    def test_wrong_length_error_names_the_expected_size(self):
        with self.assertRaises(ITCHParseError) as ctx:
            self.engine.parse_message(B.add_order(1) + b"\x00")
        self.assertIn("36 bytes", str(ctx.exception))

    def test_invalid_buy_sell_indicator_rejected(self):
        with self.assertRaises(ITCHParseError):
            self.engine.parse_message(B.add_order(1, side="Z"))

    def test_non_ascii_alpha_field_rejected(self):
        raw = bytearray(B.add_order(1, stock="AAPL"))
        raw[24] = 0xFF  # first byte of the Stock field, offset 24
        with self.assertRaises(ITCHParseError):
            self.engine.parse_message(bytes(raw))

    def test_malformed_message_is_not_counted_as_parsed(self):
        with self.assertRaises(ITCHParseError):
            self.engine.parse_message(B.add_order(1)[:-1])
        self.assertEqual(self.engine.parsed_messages_count, 0)

    def test_stock_symbol_right_padding_stripped_leading_space_preserved(self):
        # Alpha fields are left justified; a leading space signals a bad offset.
        msg = self.engine.parse_message(B.add_order(1, stock="AAPL"))
        self.assertEqual(msg.stock, "AAPL")
        raw = bytearray(B.add_order(2, stock="AAPL"))
        raw[24:32] = b" AAPL   "
        shifted = self.engine.parse_message(bytes(raw))
        self.assertEqual(shifted.stock, " AAPL")

    # -- Reporting -----------------------------------------------------------

    def test_report_is_success_only_when_book_is_consistent(self):
        self.engine.parse_message(B.add_order(1, shares=100))
        last = self.engine.parse_message(B.order_cancel(1, 40))
        report = self.engine.generate_report(last)
        self.assertIsInstance(report, ITCHParserReport)
        self.assertEqual(report.status, "PARSER_SUCCESS")
        self.assertEqual(report.total_messages_parsed, 2)
        self.assertEqual(report.active_orders_count, 1)
        self.assertEqual(report.integrity_violation_count, 0)
        self.assertIsInstance(report.last_parsed_message, ITCHParsedMessage)

    def test_report_flags_integrity_violations(self):
        self.engine.parse_message(B.order_executed(999, 10))
        report = self.engine.generate_report()
        self.assertEqual(report.status, "PARSER_INTEGRITY_VIOLATIONS")
        self.assertEqual(report.integrity_violation_count, 1)
        self.assertEqual(report.violations_by_kind["UNKNOWN_ORDER"], 1)
        self.assertIn("UNKNOWN_ORDER=1", report.audit_notes)

    def test_report_violation_counts_are_a_snapshot(self):
        self.engine.parse_message(B.order_delete(1))
        report = self.engine.generate_report()
        self.engine.parse_message(B.order_delete(2))
        self.assertEqual(report.violations_by_kind["UNKNOWN_ORDER"], 1)
        self.assertEqual(self.engine.violations_by_kind["UNKNOWN_ORDER"], 2)

    def test_engines_do_not_share_state(self):
        other = NasdaqITCH50ParserEngine()
        self.engine.parse_message(B.add_order(1))
        self.assertEqual(len(other.active_orders), 0)
        self.assertEqual(other.parsed_messages_count, 0)

    # -- End-to-end ----------------------------------------------------------

    def test_full_lifecycle_replay_leaves_a_consistent_book(self):
        msgs = [
            B.add_order(1, side="B", shares=500, stock="AAPL", price=1_500_000, ts=10),
            B.add_order(2, side="S", shares=300, stock="AAPL",
                        price=1_501_000, ts=20, attribution="NSDQ"),
            B.order_executed(1, 200, ts=30),
            B.order_executed_with_price(2, 100, 1_500_500, ts=40),
            B.order_cancel(1, 100, ts=50),
            B.order_replace(1, 3, shares=150, price=1_499_000, ts=60),
            B.trade(shares=75, stock="AAPL", price=1_500_500, ts=70),
            B.order_delete(2, ts=80),
        ]
        last = None
        for raw in msgs:
            last = self.engine.parse_message(raw)

        report = self.engine.generate_report(last)
        self.assertEqual(report.status, "PARSER_SUCCESS")
        self.assertEqual(report.integrity_violation_count, 0)
        self.assertEqual(report.total_messages_parsed, 8)
        # Order 1 -> replaced into 3 (150 shares); order 2 deleted; 'P' no-op.
        self.assertEqual(set(self.engine.active_orders), {3})
        order3 = self.engine.active_orders[3]
        self.assertEqual(order3.shares, 150)
        self.assertEqual(order3.side, "B")
        self.assertEqual(order3.stock, "AAPL")
        self.assertEqual(order3.price_ticks, 1_499_000)
        self.assertEqual(order3.price_usd, 149.90)


if __name__ == "__main__":
    unittest.main()

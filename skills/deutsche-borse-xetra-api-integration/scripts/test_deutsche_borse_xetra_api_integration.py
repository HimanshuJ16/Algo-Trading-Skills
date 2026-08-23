import struct
import unittest
from decimal import Decimal

from deutsche_borse_xetra_api_integration import (
    DEPRECATED_TEMPLATE_REPLACEMENTS,
    ETI_REQUEST_HEADER_LEN,
    ORDER_ORIGINATION_DIRECT_ACCESS_CUSTOMER,
    SHORT_CODE_QUALIFIER_ALGO,
    SIDE_BUY,
    SIDE_SELL,
    TEMPLATE_NEW_ORDER_SINGLE,
    TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG,
    TRADING_CAPACITY_MARKET_MAKER,
    TRADING_CAPACITY_PRINCIPAL_PROPRIETARY,
    DeutscheBorseXetraApiEngine,
    XetraOrderRequest,
    XetraOrderValidationError,
    normalise_side,
    price_to_eti_int,
    rts11_tick_size,
    validate_isin,
)

# Mercedes-Benz Group AG. Real ISIN, used because its check digit is genuine.
ISIN_MBG = "DE0007100000"


def order(**overrides):
    spec = dict(
        cl_ord_id=1,
        isin=ISIN_MBG,
        security_id=2505140,
        market_segment_id=52794,
        side="BUY",
        order_qty=500,
        price_eur="62.50",
        liquidity_band=6,
        trading_capacity=TRADING_CAPACITY_PRINCIPAL_PROPRIETARY,
    )
    spec.update(overrides)
    return XetraOrderRequest(**spec)


class TestRts11TickTable(unittest.TestCase):
    """Expected values read directly off the RTS 11 Annex, not recomputed."""

    def test_annex_corner_values(self):
        # (price, liquidity band) -> tick, transcribed from the Annex.
        cases = [
            ("0.05", 1, "0.0005"),    # first row, first column
            ("0.05", 6, "0.0001"),    # first row, last column
            ("62.50", 6, "0.01"),     # 50<=p<100, LB6
            ("62.50", 1, "0.5"),      # 50<=p<100, LB1 -- 50x the LB6 tick
            ("15", 6, "0.002"),       # 10<=p<20, LB6
            ("15", 3, "0.02"),        # 10<=p<20, LB3
            ("1500", 4, "1"),         # 1000<=p<2000, LB4
            ("75000", 1, "500"),      # open-ended last row, LB1
            ("75000", 6, "10"),       # open-ended last row, LB6
        ]
        for price, band, expected in cases:
            with self.subTest(price=price, band=band):
                self.assertEqual(rts11_tick_size(price, band), Decimal(expected))

    def test_price_band_boundaries_are_lower_inclusive(self):
        # 10 <= p < 20 gives 0.005 at LB5; 20 <= p < 50 gives 0.01 at LB5.
        self.assertEqual(rts11_tick_size("19.999", 5), Decimal("0.005"))
        self.assertEqual(rts11_tick_size("20", 5), Decimal("0.01"))
        self.assertEqual(rts11_tick_size("49.999", 5), Decimal("0.01"))
        self.assertEqual(rts11_tick_size("50", 5), Decimal("0.02"))

    def test_tick_never_increases_with_liquidity(self):
        for price in ("0.05", "1.5", "62.50", "1500", "75000"):
            ticks = [rts11_tick_size(price, band) for band in range(1, 7)]
            for finer, coarser in zip(ticks[1:], ticks[:-1]):
                self.assertLessEqual(finer, coarser, f"non-monotonic at {price}")

    def test_liquidity_band_must_be_1_to_6(self):
        for band in (0, 7, -1, 11):
            with self.subTest(band=band), self.assertRaises(XetraOrderValidationError):
                rts11_tick_size("62.50", band)

    def test_non_positive_price_rejected(self):
        for price in ("0", "-62.50"):
            with self.subTest(price=price), self.assertRaises(XetraOrderValidationError):
                rts11_tick_size(price, 6)


class TestTickAudit(unittest.TestCase):

    def setUp(self):
        self.engine = DeutscheBorseXetraApiEngine(sender_sub_id=98765)

    def test_on_and_off_tick_for_liquid_band(self):
        self.assertEqual(self.engine.audit_xetra_tick_size("62.50", 6),
                         (True, Decimal("0.01")))
        self.assertEqual(self.engine.audit_xetra_tick_size("62.503", 6),
                         (False, Decimal("0.01")))

    def test_same_price_differs_by_liquidity_band(self):
        """Regression: the tick is not a function of price alone.

        The pre-fix engine mapped any price >= 50 to a 0.01 tick. Under RTS 11,
        62.53 is on-tick only for bands 5 and 6; for band 1 the tick is 0.50.
        """
        on_tick_b6, tick_b6 = self.engine.audit_xetra_tick_size("62.53", 6)
        on_tick_b1, tick_b1 = self.engine.audit_xetra_tick_size("62.53", 1)

        self.assertTrue(on_tick_b6)
        self.assertEqual(tick_b6, Decimal("0.01"))
        self.assertFalse(on_tick_b1)
        self.assertEqual(tick_b1, Decimal("0.5"))

    def test_old_hardcoded_table_was_wrong_below_50(self):
        """Regression: the pre-fix table claimed a 0.005 tick for 10 <= p < 50.

        RTS 11 gives 0.002 at LB6 for 10 <= p < 20, so 15.005 was accepted before
        and must be rejected now.
        """
        on_tick, tick = self.engine.audit_xetra_tick_size("15.005", 6)

        self.assertFalse(on_tick)
        self.assertEqual(tick, Decimal("0.002"))

    def test_float_input_does_not_leak_binary_artefacts(self):
        # 0.1 as a float is 0.1000000000000000055...; naive Decimal(float) fails.
        self.assertEqual(self.engine.audit_xetra_tick_size(0.1, 1), (True, Decimal("0.001")))

    def test_negative_price_raises_rather_than_passing(self):
        """Regression: Python's float modulo made -5.0 % 0.001 == 0.0, so negative
        prices passed the old tick check and were reported as dispatched."""
        with self.assertRaises(XetraOrderValidationError):
            self.engine.audit_xetra_tick_size(-5.0, 1)


class TestOrderValidation(unittest.TestCase):

    def setUp(self):
        self.engine = DeutscheBorseXetraApiEngine(sender_sub_id=98765)

    def test_valid_order_passes_and_frames_a_header(self):
        report = self.engine.process_xetra_order(order(), body_len=152)

        self.assertTrue(report.ready_to_send)
        self.assertEqual(report.status, "STATUS_OK")
        self.assertEqual(report.required_tick_size, Decimal("0.01"))
        self.assertEqual(report.side_wire_value, SIDE_BUY)
        # 62.50 with 8 implied decimals.
        self.assertEqual(report.price_eti_int, 6_250_000_000)
        self.assertIsNotNone(report.eti_header)
        self.assertEqual(report.eti_header.sender_sub_id, 98765)
        self.assertEqual(report.eti_header.msg_seq_num, 1)

    def test_off_tick_order_rejected_with_the_documented_reason(self):
        report = self.engine.process_xetra_order(order(cl_ord_id=2, price_eur="62.503"))

        self.assertFalse(report.ready_to_send)
        self.assertEqual(report.status, "INVALID_TICK_SIZE")
        self.assertEqual(report.required_tick_size, Decimal("0.01"))
        self.assertIn("238", report.rejection_reason)

    def test_order_valid_in_band_6_rejected_in_band_1(self):
        self.assertTrue(self.engine.process_xetra_order(
            order(price_eur="62.53", liquidity_band=6)).ready_to_send)
        self.assertFalse(self.engine.process_xetra_order(
            order(price_eur="62.53", liquidity_band=1)).ready_to_send)

    def test_negative_price_rejected(self):
        """Regression: previously dispatched with STATUS_OK."""
        report = self.engine.process_xetra_order(order(price_eur="-5.0"))

        self.assertFalse(report.ready_to_send)
        self.assertEqual(report.status, "INVALID_ORDER_FIELD")

    def test_zero_price_rejected(self):
        report = self.engine.process_xetra_order(order(price_eur="0"))
        self.assertFalse(report.ready_to_send)

    def test_non_positive_quantity_rejected(self):
        """Regression: qty 0 and -500 previously dispatched with STATUS_OK."""
        for qty in (0, -500):
            with self.subTest(qty=qty):
                report = self.engine.process_xetra_order(order(order_qty=qty))
                self.assertFalse(report.ready_to_send)
                self.assertEqual(report.status, "INVALID_ORDER_FIELD")

    def test_unknown_side_rejected(self):
        """Regression: 'SIDEWAYS' previously dispatched with STATUS_OK."""
        report = self.engine.process_xetra_order(order(side="SIDEWAYS"))

        self.assertFalse(report.ready_to_send)
        self.assertEqual(report.status, "INVALID_ORDER_FIELD")

    def test_letter_account_codes_are_not_accepted_as_trading_capacity(self):
        """Regression: 'P'/'A'/'M' are Account (tag 1) characters, not
        TradingCapacity (tag 1815), which is numeric 1/5/6/9/10."""
        for capacity in ("P", "A", "M"):
            with self.subTest(capacity=capacity):
                report = self.engine.process_xetra_order(order(trading_capacity=capacity))
                self.assertFalse(report.ready_to_send)
                self.assertEqual(report.status, "INVALID_ORDER_FIELD")

    def test_market_maker_capacity_accepted(self):
        report = self.engine.process_xetra_order(
            order(trading_capacity=TRADING_CAPACITY_MARKET_MAKER))
        self.assertTrue(report.ready_to_send)

    def test_bad_isin_check_digit_rejected(self):
        # Two digits transposed from the real ISIN.
        report = self.engine.process_xetra_order(order(isin="DE0007100001"))

        self.assertFalse(report.ready_to_send)
        self.assertEqual(report.status, "INVALID_ORDER_FIELD")

    def test_dea_short_code_requires_a_qualifier(self):
        report = self.engine.process_xetra_order(order(
            order_origination=ORDER_ORIGINATION_DIRECT_ACCESS_CUSTOMER,
            executing_trader_short_code=99201,
            executing_trader_qualifier=None))

        self.assertFalse(report.ready_to_send)
        self.assertEqual(report.status, "INVALID_ORDER_FIELD")

    def test_dea_order_with_algo_qualifier_accepted(self):
        report = self.engine.process_xetra_order(order(
            order_origination=ORDER_ORIGINATION_DIRECT_ACCESS_CUSTOMER,
            executing_trader_short_code=99201,
            executing_trader_qualifier=SHORT_CODE_QUALIFIER_ALGO))

        self.assertTrue(report.ready_to_send)

    def test_rejected_orders_consume_no_sequence_number(self):
        """A gap in MsgSeqNum is a session-level fault, so rejects must not burn one."""
        self.engine.process_xetra_order(order(cl_ord_id=1))
        self.engine.process_xetra_order(order(cl_ord_id=2, order_qty=-1))
        self.engine.process_xetra_order(order(cl_ord_id=3, price_eur="62.503"))
        report = self.engine.process_xetra_order(order(cl_ord_id=4))

        self.assertEqual(report.eti_header.msg_seq_num, 2)
        self.assertEqual(self.engine.msg_seq_num, 2)


class TestEtiEncoding(unittest.TestCase):

    def test_header_packs_to_the_documented_24_byte_layout(self):
        engine = DeutscheBorseXetraApiEngine(
            sender_sub_id=4242, default_template_id=TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG)
        header = engine.format_t7_eti_header(body_len=152)
        raw = header.pack()

        self.assertEqual(len(raw), ETI_REQUEST_HEADER_LEN)
        # Little endian: BodyLen u32, TemplateID u16, NetworkMsgID 8s, Pad2 2s,
        # MsgSeqNum u32, SenderSubID u32.
        body_len, template_id, _, _, seq, sender = struct.unpack("<IH8s2sII", raw)
        self.assertEqual(body_len, 152)
        self.assertEqual(template_id, 10138)
        self.assertEqual(seq, 1)
        self.assertEqual(sender, 4242)
        # BodyLen sits at offset 0 and TemplateID at offset 4.
        self.assertEqual(raw[0:4], (152).to_bytes(4, "little"))
        self.assertEqual(raw[4:6], (10138).to_bytes(2, "little"))

    def test_body_len_shorter_than_the_header_is_rejected(self):
        engine = DeutscheBorseXetraApiEngine(sender_sub_id=1)
        with self.assertRaises(XetraOrderValidationError):
            engine.format_t7_eti_header(body_len=16)

    def test_fields_wider_than_the_wire_raise_before_packing(self):
        """A struct.error at pack time would not say which field was wrong."""
        with self.assertRaises(XetraOrderValidationError):
            DeutscheBorseXetraApiEngine(sender_sub_id=1, default_template_id=70000)
        with self.assertRaises(XetraOrderValidationError):
            DeutscheBorseXetraApiEngine(sender_sub_id=2 ** 32)
        engine = DeutscheBorseXetraApiEngine(sender_sub_id=1)
        with self.assertRaises(XetraOrderValidationError):
            engine.format_t7_eti_header(body_len=2 ** 32)

    def test_sequence_number_increments_by_one_per_request(self):
        engine = DeutscheBorseXetraApiEngine(sender_sub_id=1)
        seqs = [engine.format_t7_eti_header(body_len=24).msg_seq_num for _ in range(3)]
        self.assertEqual(seqs, [1, 2, 3])

    def test_price_scaling_uses_eight_implied_decimals(self):
        self.assertEqual(price_to_eti_int("62.50"), 6_250_000_000)
        self.assertEqual(price_to_eti_int("0.0001"), 10_000)
        self.assertEqual(price_to_eti_int(Decimal("1")), 100_000_000)

    def test_price_needing_more_than_eight_decimals_is_refused_not_rounded(self):
        with self.assertRaises(XetraOrderValidationError):
            price_to_eti_int("1.123456789")

    def test_deprecated_template_produces_a_warning_but_still_validates(self):
        engine = DeutscheBorseXetraApiEngine(
            sender_sub_id=1, default_template_id=TEMPLATE_NEW_ORDER_SINGLE)
        report = engine.process_xetra_order(order())

        self.assertTrue(report.ready_to_send)
        self.assertIsNotNone(report.warnings)
        self.assertIn("14.1", report.warnings[0])
        self.assertEqual(
            DEPRECATED_TEMPLATE_REPLACEMENTS[TEMPLATE_NEW_ORDER_SINGLE],
            TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG)

    def test_default_template_is_the_successor_message(self):
        engine = DeutscheBorseXetraApiEngine(sender_sub_id=1)
        self.assertEqual(engine.default_template_id, 10138)
        self.assertIsNone(engine.process_xetra_order(order()).warnings)


class TestHelpers(unittest.TestCase):

    def test_side_aliases(self):
        self.assertEqual(normalise_side("buy"), SIDE_BUY)
        self.assertEqual(normalise_side(" SELL "), SIDE_SELL)
        self.assertEqual(normalise_side(1), SIDE_BUY)
        with self.assertRaises(XetraOrderValidationError):
            normalise_side(3)
        with self.assertRaises(XetraOrderValidationError):
            normalise_side(True)

    def test_isin_validation(self):
        self.assertEqual(validate_isin(" de0007100000 "), ISIN_MBG)
        for bad in ("DE000710000", "DE0007100001", "0E0007100000", ""):
            with self.subTest(bad=bad), self.assertRaises(XetraOrderValidationError):
                validate_isin(bad)


if __name__ == '__main__':
    unittest.main()

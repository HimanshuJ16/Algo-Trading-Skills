import struct
import unittest
from decimal import Decimal

from eurex_market_data_and_order_api import (
    DECOMMISSIONED_TEMPLATE_REPLACEMENTS,
    ETI_REQUEST_HEADER_LEN,
    ORD_TYPE_MARKET,
    PRC_PROCEDURE_NON_STANDARD,
    PRC_PROCEDURE_NOT_PERFORMED,
    PRC_PROCEDURE_STANDARD,
    PRICE_VALIDITY_CHECK_MANDATORY,
    PRICE_VALIDITY_CHECK_NONE,
    PRICE_VALIDITY_CHECK_OPTIONAL,
    SIDE_BUY,
    SIDE_SELL,
    STATUS_DUPLICATE_CL_ORD_ID,
    STATUS_INVALID_ORDER_FIELD,
    STATUS_INVALID_TICK_SIZE,
    STATUS_OK,
    STATUS_PRICE_REASONABILITY_BREACH,
    STATUS_UNKNOWN_CONTRACT,
    TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG,
    EurexContractSpec,
    EurexDepthBook,
    EurexMarketDataAndOrderApiEngine,
    EurexOrderExecutionReport,
    EurexOrderRequest,
    EurexOrderValidationError,
    EurexOrderValidationReport,
    PriceRangeTable,
    detect_emdi_sequence_gap,
    default_contract_specs,
    new_order_body_len,
    price_to_eti_int,
    qty_to_eti_int,
    resolve_prc_reference_price,
)

# The price range table from Example 6-1 of the T7 Functional Reference (options
# on NOA3): three intervals plus a Fast Market Percentage of 100. Its published
# worked examples are used below as independently derived expected values.
DOC_EXAMPLE_TABLE = PriceRangeTable.from_rows(
    [("0.00", "1.00", "0.10", "0"),
     ("1.00", "5.00", "0", "10"),
     ("5.00", None, "0.50", "0")],
    fast_market_percentage="100",
)

# A flat +/- 50 index point band for FESX, expressed the way T7 does: an absolute
# price range parameter with no percentage component. Used only as test data --
# the module deliberately ships no default band.
FESX_FLAT_50_TABLE = PriceRangeTable.from_rows([("0", None, "50", "0")])


def fesx_spec(price_range_table=None):
    return EurexContractSpec(
        symbol="FESX", tick_step=Decimal("1"), multiplier_eur=Decimal("10"),
        point_description="index point", price_range_table=price_range_table)


def fesx_order(**overrides):
    kwargs = dict(
        cl_ord_id=1001, contract_symbol="FESX", expiry="202609",
        security_id=4128839, market_segment_id=589, side="BUY",
        order_qty=10, price="4851",
    )
    kwargs.update(overrides)
    return EurexOrderRequest(**kwargs)


def fesx_book(bid="4850", ask="4851", bid_qty="100", ask_qty="300", **kwargs):
    return EurexDepthBook.from_levels([(bid, bid_qty)], [(ask, ask_qty)], **kwargs)


class TestScaledIntegerEncoding(unittest.TestCase):
    """ETI PriceType carries 8 implied decimals, Qty carries 4."""

    def test_price_scaled_by_ten_to_the_eight(self):
        # 4851 index points x 10^8, computed by hand.
        self.assertEqual(price_to_eti_int("4851"), 485_100_000_000)
        # 133.33 percent of par x 10^8.
        self.assertEqual(price_to_eti_int("133.33"), 13_333_000_000)

    def test_float_input_does_not_truncate_a_wire_unit(self):
        # The float route is the regression this guards: int(0.29 * 1e8) is
        # 28999999, one wire unit short, because 0.29 is not exactly representable.
        self.assertEqual(int(0.29 * 1e8), 28_999_999)
        self.assertEqual(price_to_eti_int(0.29), 29_000_000)
        self.assertEqual(price_to_eti_int("0.29"), 29_000_000)

    def test_price_needing_more_than_eight_decimals_is_refused(self):
        with self.assertRaises(EurexOrderValidationError):
            price_to_eti_int("4851.000000001")

    def test_non_finite_and_non_numeric_prices_are_refused(self):
        for bad in ("NaN", "Infinity", None, True, [1]):
            with self.assertRaises(EurexOrderValidationError):
                price_to_eti_int(bad)

    def test_quantity_scaled_by_ten_to_the_four(self):
        self.assertEqual(qty_to_eti_int(10), 100_000)
        self.assertEqual(qty_to_eti_int("2.5"), 25_000)

    def test_quantity_needing_more_than_four_decimals_is_refused(self):
        with self.assertRaises(EurexOrderValidationError):
            qty_to_eti_int("1.00001")


class TestTickStep(unittest.TestCase):

    def setUp(self):
        self.engine = EurexMarketDataAndOrderApiEngine(sender_sub_id=55443)

    def test_fesx_requires_whole_index_points(self):
        self.assertTrue(self.engine.audit_eurex_tick_size("4851", "1"))
        self.assertFalse(self.engine.audit_eurex_tick_size("4851.5", "1"))

    def test_fgbl_requires_hundredths_of_a_percent(self):
        self.assertTrue(self.engine.audit_eurex_tick_size("133.33", "0.01"))
        self.assertFalse(self.engine.audit_eurex_tick_size("133.335", "0.01"))

    def test_float_input_is_normalised_before_the_modulo(self):
        # 133.33 as a float is 133.330000000000012505..., so a raw float modulo
        # against 0.01 yields 9.73e-15 rather than 0.
        self.assertNotEqual(133.33 % 0.01, 0.0)
        self.assertTrue(self.engine.audit_eurex_tick_size(133.33, 0.01))

    def test_negative_price_is_off_tick(self):
        # Regression: float modulo reports -4851.0 % 1.0 == 0.0, so the previous
        # implementation accepted a negative price as on-tick.
        self.assertEqual(-4851.0 % 1.0, 0.0)
        self.assertFalse(self.engine.audit_eurex_tick_size("-4851", "1"))
        self.assertFalse(self.engine.audit_eurex_tick_size("0", "1"))

    def test_non_positive_tick_step_is_an_error(self):
        with self.assertRaises(EurexOrderValidationError):
            self.engine.audit_eurex_tick_size("4851", "0")

    def test_default_specs_match_the_eurex_contract_specifications(self):
        specs = default_contract_specs()
        # FESX: 1 index point minimum price change, EUR 10 per index point.
        self.assertEqual(specs["FESX"].tick_step, Decimal("1"))
        self.assertEqual(specs["FESX"].multiplier_eur, Decimal("10"))
        # FGBL: 0.01 percent minimum price change on EUR 100,000 nominal, so one
        # full point is EUR 1,000 and one tick is EUR 10.
        self.assertEqual(specs["FGBL"].tick_step, Decimal("0.01"))
        self.assertEqual(specs["FGBL"].multiplier_eur, Decimal("1000"))
        self.assertEqual(specs["FGBL"].tick_step * specs["FGBL"].multiplier_eur,
                         Decimal("10.00"))
        # No band is shipped: it is per-instrument RDI reference data.
        self.assertIsNone(specs["FESX"].price_range_table)


class TestPriceRangeTable(unittest.TestCase):
    """Expected values are the worked examples published with the formula."""

    def test_documented_worked_examples(self):
        cases = [("-2.40", "0.24"), ("0.00", "0.10"), ("0.27", "0.10"),
                 ("1.00", "0.10"), ("3.50", "0.35"), ("5.00", "0.50"),
                 ("7.80", "0.50")]
        for reference, expected in cases:
            with self.subTest(reference=reference):
                self.assertEqual(DOC_EXAMPLE_TABLE.price_range(reference),
                                 Decimal(expected))

    def test_fast_market_percentage_of_one_hundred_doubles_the_range(self):
        self.assertEqual(DOC_EXAMPLE_TABLE.price_range("3.50", fast_market=True),
                         Decimal("0.70"))

    def test_reference_price_outside_every_interval_is_an_error(self):
        bounded = PriceRangeTable.from_rows([("0", "10", "1", "0")])
        with self.assertRaises(EurexOrderValidationError):
            bounded.price_range("11")

    def test_empty_table_is_refused(self):
        with self.assertRaises(EurexOrderValidationError):
            PriceRangeTable.from_rows([])


class TestDepthBook(unittest.TestCase):

    def test_best_prices_mid_and_spread(self):
        book = EurexDepthBook.from_levels(
            bids=[("4850", "100"), ("4849", "250")],
            asks=[("4851", "300"), ("4852", "150")])
        self.assertEqual(book.best_bid_price, Decimal("4850"))
        self.assertEqual(book.best_ask_price, Decimal("4851"))
        self.assertEqual(book.mid_price, Decimal("4850.5"))
        self.assertEqual(book.spread, Decimal("1"))

    def test_depth_imbalance_top_of_book_and_multi_level(self):
        book = EurexDepthBook.from_levels(
            bids=[("4850", "100"), ("4849", "250")],
            asks=[("4851", "300"), ("4852", "150")])
        # Top of book: (100 - 300) / 400 = -0.5.
        self.assertEqual(book.depth_imbalance(1), Decimal("-0.5"))
        # Two levels: (350 - 450) / 800 = -0.125.
        self.assertEqual(book.depth_imbalance(2), Decimal("-0.125"))

    def test_imbalance_bounds_and_undefined_case(self):
        bid_only = EurexDepthBook.from_levels(bids=[("4850", "100")], asks=[])
        self.assertEqual(bid_only.depth_imbalance(), Decimal("1"))
        ask_only = EurexDepthBook.from_levels(bids=[], asks=[("4851", "100")])
        self.assertEqual(ask_only.depth_imbalance(), Decimal("-1"))
        # Undefined rather than zero: an empty book is not a balanced one.
        self.assertIsNone(EurexDepthBook().depth_imbalance())

    def test_one_sided_book_has_no_mid_or_spread(self):
        book = EurexDepthBook.from_levels(bids=[], asks=[("4851", "300")])
        self.assertIsNone(book.mid_price)
        self.assertIsNone(book.spread)
        self.assertFalse(book.is_crossed)

    def test_crossed_and_locked_books_are_distinguished(self):
        crossed = EurexDepthBook.from_levels([("4852", "10")], [("4851", "10")])
        self.assertTrue(crossed.is_crossed)
        self.assertFalse(crossed.is_locked)
        locked = EurexDepthBook.from_levels([("4851", "10")], [("4851", "10")])
        self.assertTrue(locked.is_locked)
        self.assertFalse(locked.is_crossed)

    def test_unsorted_or_repeating_ladder_is_refused(self):
        with self.assertRaises(EurexOrderValidationError):
            EurexDepthBook.from_levels([("4849", "10"), ("4850", "10")], [])
        with self.assertRaises(EurexOrderValidationError):
            EurexDepthBook.from_levels([], [("4851", "10"), ("4851", "10")])

    def test_zero_quantity_level_is_refused(self):
        with self.assertRaises(EurexOrderValidationError):
            EurexDepthBook.from_levels([("4850", "0")], [])

    def test_staleness_uses_a_caller_supplied_clock(self):
        book = fesx_book(recv_time_ns=1_000)
        self.assertFalse(book.is_stale(now_ns=1_500, max_age_ns=1_000))
        self.assertTrue(book.is_stale(now_ns=3_000, max_age_ns=1_000))
        # A book with no receive time cannot be shown to be fresh.
        self.assertTrue(fesx_book().is_stale(now_ns=1, max_age_ns=10 ** 12))


class TestEmdiSequenceGapDetection(unittest.TestCase):

    def test_consecutive_sequence_numbers_are_clean(self):
        self.assertIsNone(detect_emdi_sequence_gap(fesx_book(msg_seq_num=41),
                                                   fesx_book(msg_seq_num=42)))

    def test_skipped_message_is_reported(self):
        message = detect_emdi_sequence_gap(fesx_book(msg_seq_num=41),
                                           fesx_book(msg_seq_num=44))
        self.assertIsNotNone(message)
        self.assertIn("gap", message)

    def test_duplicate_or_out_of_order_datagram_is_reported(self):
        message = detect_emdi_sequence_gap(fesx_book(msg_seq_num=41),
                                           fesx_book(msg_seq_num=41))
        self.assertIsNotNone(message)
        self.assertIn("backwards", message)

    def test_missing_sequence_numbers_yield_no_false_alarm(self):
        self.assertIsNone(detect_emdi_sequence_gap(None, fesx_book(msg_seq_num=1)))
        self.assertIsNone(detect_emdi_sequence_gap(fesx_book(), fesx_book()))


class TestPriceReasonabilityReferencePrice(unittest.TestCase):
    """The reference price is the opposite-side best price, not the mid."""

    def setUp(self):
        self.book = fesx_book(bid="4850", ask="4851")
        self.range = Decimal("50")

    def test_standard_procedure_takes_the_opposite_side_best(self):
        reference, procedure = resolve_prc_reference_price(SIDE_BUY, self.book, self.range)
        self.assertEqual(procedure, PRC_PROCEDURE_STANDARD)
        self.assertEqual(reference, Decimal("4851"))       # best ask for a buy
        reference, procedure = resolve_prc_reference_price(SIDE_SELL, self.book, self.range)
        self.assertEqual(procedure, PRC_PROCEDURE_STANDARD)
        self.assertEqual(reference, Decimal("4850"))       # best bid for a sell
        # Neither equals the mid, which would be 4850.5.
        self.assertEqual(self.book.mid_price, Decimal("4850.5"))

    def test_standard_procedure_needs_the_spread_inside_the_price_range(self):
        wide = EurexDepthBook.from_levels([("4800", "10")], [("4900", "10")])
        # Spread 100 > price range 50, so the standard procedure does not apply
        # and, with no alternative reference price, no check is possible.
        reference, procedure = resolve_prc_reference_price(SIDE_BUY, wide, self.range)
        self.assertIsNone(reference)
        self.assertEqual(procedure, PRC_PROCEDURE_NOT_PERFORMED)

    def test_non_standard_takes_the_alternative_when_it_sits_inside_the_book(self):
        wide = EurexDepthBook.from_levels([("4800", "10")], [("4900", "10")])
        for side in (SIDE_BUY, SIDE_SELL):
            reference, procedure = resolve_prc_reference_price(
                side, wide, self.range, alternative_reference_price=Decimal("4860"))
            self.assertEqual(procedure, PRC_PROCEDURE_NON_STANDARD)
            self.assertEqual(reference, Decimal("4860"))

    def test_non_standard_falls_back_to_the_best_prices_when_it_does_not(self):
        wide = EurexDepthBook.from_levels([("4800", "10")], [("4900", "10")])
        for alternative in ("4700", "5000"):
            with self.subTest(alternative=alternative):
                buy_ref, _ = resolve_prc_reference_price(
                    SIDE_BUY, wide, self.range,
                    alternative_reference_price=Decimal(alternative))
                sell_ref, _ = resolve_prc_reference_price(
                    SIDE_SELL, wide, self.range,
                    alternative_reference_price=Decimal(alternative))
                self.assertEqual(buy_ref, Decimal("4900"))
                self.assertEqual(sell_ref, Decimal("4800"))

    def test_empty_ask_side_follows_the_documented_table(self):
        bid_only = EurexDepthBook.from_levels([("4800", "10")], [])
        # Sell always references the best buy price.
        reference, _ = resolve_prc_reference_price(
            SIDE_SELL, bid_only, self.range, alternative_reference_price=Decimal("4900"))
        self.assertEqual(reference, Decimal("4800"))
        # Buy takes the alternative only when BBP <= TP.
        reference, _ = resolve_prc_reference_price(
            SIDE_BUY, bid_only, self.range, alternative_reference_price=Decimal("4900"))
        self.assertEqual(reference, Decimal("4900"))
        reference, _ = resolve_prc_reference_price(
            SIDE_BUY, bid_only, self.range, alternative_reference_price=Decimal("4700"))
        self.assertEqual(reference, Decimal("4800"))

    def test_empty_bid_side_follows_the_documented_table(self):
        ask_only = EurexDepthBook.from_levels([], [("4900", "10")])
        # Buy always references the best sell price.
        reference, _ = resolve_prc_reference_price(
            SIDE_BUY, ask_only, self.range, alternative_reference_price=Decimal("4800"))
        self.assertEqual(reference, Decimal("4900"))
        # Sell takes the alternative only when TP <= BSP.
        reference, _ = resolve_prc_reference_price(
            SIDE_SELL, ask_only, self.range, alternative_reference_price=Decimal("4800"))
        self.assertEqual(reference, Decimal("4800"))
        reference, _ = resolve_prc_reference_price(
            SIDE_SELL, ask_only, self.range, alternative_reference_price=Decimal("5000"))
        self.assertEqual(reference, Decimal("4900"))

    def test_empty_book_uses_the_alternative_reference_price(self):
        reference, procedure = resolve_prc_reference_price(
            SIDE_BUY, EurexDepthBook(), self.range,
            alternative_reference_price=Decimal("4850"))
        self.assertEqual(procedure, PRC_PROCEDURE_NON_STANDARD)
        self.assertEqual(reference, Decimal("4850"))

    def test_smallest_allowed_limit_price_substitutes_for_a_missing_bid(self):
        ask_only = EurexDepthBook.from_levels([], [("0.30", "10")])
        reference, procedure = resolve_prc_reference_price(
            SIDE_BUY, ask_only, Decimal("0.50"),
            smallest_allowed_limit_price=Decimal("0.01"))
        self.assertEqual(procedure, PRC_PROCEDURE_STANDARD)
        self.assertEqual(reference, Decimal("0.30"))


class TestPriceReasonabilityCheck(unittest.TestCase):

    def setUp(self):
        self.engine = EurexMarketDataAndOrderApiEngine(sender_sub_id=55443)
        self.spec = fesx_spec(FESX_FLAT_50_TABLE)
        self.book = fesx_book(bid="4850", ask="4851")

    def _audit(self, side, price, **kwargs):
        return self.engine.audit_price_reasonability(
            side=side, limit_price=price, spec=self.spec, book=self.book, **kwargs)

    def test_aggressive_buy_beyond_the_bound_is_rejected(self):
        # Reference is the best ask 4851; bound is 4851 + 50 = 4901.
        outcome = self._audit(SIDE_BUY, "4902")
        self.assertFalse(outcome.passed)
        self.assertEqual(outcome.reference_price, Decimal("4851"))
        self.assertEqual(outcome.price_range, Decimal("50"))

    def test_passive_buy_far_below_the_market_is_accepted(self):
        # Regression: the check is directional. A buy 100 points below the market
        # is 100 points from the mid, which a symmetric abs() band would reject,
        # but T7 never rejects a buy for being too low.
        outcome = self._audit(SIDE_BUY, "4750")
        self.assertTrue(outcome.passed)
        self.assertEqual(outcome.procedure, PRC_PROCEDURE_STANDARD)

    def test_passive_sell_far_above_the_market_is_accepted(self):
        outcome = self._audit(SIDE_SELL, "4950")
        self.assertTrue(outcome.passed)

    def test_aggressive_sell_below_the_bound_is_rejected(self):
        # Reference is the best bid 4850; bound is 4850 - 50 = 4800.
        self.assertFalse(self._audit(SIDE_SELL, "4799").passed)
        self.assertTrue(self._audit(SIDE_SELL, "4800").passed)

    def test_the_bound_itself_is_accepted_and_one_tick_beyond_is_not(self):
        self.assertTrue(self._audit(SIDE_BUY, "4901").passed)     # exactly ref + range
        self.assertFalse(self._audit(SIDE_BUY, "4902").passed)

    def test_fast_market_widens_the_bound(self):
        spec = fesx_spec(PriceRangeTable.from_rows([("0", None, "50", "0")],
                                                   fast_market_percentage="100"))
        outcome = self.engine.audit_price_reasonability(
            side=SIDE_BUY, limit_price="4951", spec=spec, book=self.book,
            fast_market=True)
        self.assertTrue(outcome.passed)
        self.assertEqual(outcome.price_range, Decimal("100"))
        self.assertFalse(self.engine.audit_price_reasonability(
            side=SIDE_BUY, limit_price="4951", spec=spec, book=self.book).passed)

    def test_percentage_price_range_is_computed_from_the_reference_price(self):
        # 1 percent of the reference price 4851 is 48.51, so the bound is 4899.51.
        spec = fesx_spec(PriceRangeTable.from_rows([("0", None, "0", "1")]))
        outcome = self.engine.audit_price_reasonability(
            side=SIDE_BUY, limit_price="4899.51", spec=spec, book=self.book)
        self.assertTrue(outcome.passed)
        self.assertEqual(outcome.price_range, Decimal("48.51"))
        self.assertFalse(self.engine.audit_price_reasonability(
            side=SIDE_BUY, limit_price="4899.52", spec=spec, book=self.book).passed)

    def test_check_is_skipped_outside_continuous_trading(self):
        outcome = self._audit(SIDE_BUY, "9999", instrument_state="Auction")
        self.assertTrue(outcome.passed)
        self.assertEqual(outcome.procedure, PRC_PROCEDURE_NOT_PERFORMED)
        self.assertIn("Continuous", outcome.reason)

    def test_check_type_none_skips_the_check(self):
        outcome = self._audit(SIDE_BUY, "9999",
                              price_validity_check_type=PRICE_VALIDITY_CHECK_NONE)
        self.assertTrue(outcome.passed)
        self.assertEqual(outcome.procedure, PRC_PROCEDURE_NOT_PERFORMED)

    def test_no_price_range_table_reports_rather_than_guesses(self):
        outcome = self.engine.audit_price_reasonability(
            side=SIDE_BUY, limit_price="9999", spec=fesx_spec(), book=self.book)
        self.assertTrue(outcome.passed)
        self.assertEqual(outcome.procedure, PRC_PROCEDURE_NOT_PERFORMED)
        self.assertIn("PriceRangeRules", outcome.reason)

    def test_mandatory_and_optional_differ_only_without_a_reference_price(self):
        empty = EurexDepthBook()
        mandatory = self.engine.audit_price_reasonability(
            side=SIDE_BUY, limit_price="4851", spec=self.spec, book=empty,
            price_validity_check_type=PRICE_VALIDITY_CHECK_MANDATORY)
        optional = self.engine.audit_price_reasonability(
            side=SIDE_BUY, limit_price="4851", spec=self.spec, book=empty,
            price_validity_check_type=PRICE_VALIDITY_CHECK_OPTIONAL)
        self.assertFalse(mandatory.passed)
        self.assertTrue(optional.passed)
        self.assertEqual(optional.procedure, PRC_PROCEDURE_NOT_PERFORMED)

    def test_a_crossed_local_book_is_refused_as_a_reference(self):
        crossed = EurexDepthBook.from_levels([("4852", "10")], [("4851", "10")])
        outcome = self.engine.audit_price_reasonability(
            side=SIDE_BUY, limit_price="9999", spec=self.spec, book=crossed)
        self.assertEqual(outcome.procedure, PRC_PROCEDURE_NOT_PERFORMED)
        self.assertIn("crossed", outcome.reason)
        # A locked book is a legitimate market state and is still usable.
        locked = EurexDepthBook.from_levels([("4851", "10")], [("4851", "10")])
        self.assertEqual(
            self.engine.audit_price_reasonability(
                side=SIDE_BUY, limit_price="4851", spec=self.spec,
                book=locked).procedure,
            PRC_PROCEDURE_STANDARD)

    def test_non_standard_procedure_is_used_when_the_spread_is_too_wide(self):
        wide = EurexDepthBook.from_levels([("4800", "10")], [("4900", "10")])
        outcome = self.engine.audit_price_reasonability(
            side=SIDE_BUY, limit_price="4860", spec=self.spec, book=wide,
            alternative_reference_price="4855")
        self.assertTrue(outcome.passed)
        self.assertEqual(outcome.procedure, PRC_PROCEDURE_NON_STANDARD)
        self.assertEqual(outcome.reference_price, Decimal("4855"))


class TestEtiRequestHeader(unittest.TestCase):

    def setUp(self):
        self.engine = EurexMarketDataAndOrderApiEngine(sender_sub_id=55443)

    def test_header_packs_to_twenty_four_little_endian_bytes(self):
        header = self.engine.format_t7_eti_header(body_len=new_order_body_len())
        raw = header.pack()
        self.assertEqual(len(raw), ETI_REQUEST_HEADER_LEN)
        # Unpacked field by field rather than with the module's own struct.
        self.assertEqual(struct.unpack_from("<I", raw, 0)[0], 280)      # BodyLen
        self.assertEqual(struct.unpack_from("<H", raw, 4)[0], 10138)    # TemplateID
        self.assertEqual(raw[6:14], b"\x00" * 8)                        # NetworkMsgID
        self.assertEqual(raw[14:16], b"\x00\x00")                       # Pad2
        self.assertEqual(struct.unpack_from("<I", raw, 16)[0], 2)       # MsgSeqNum
        self.assertEqual(struct.unpack_from("<I", raw, 20)[0], 55443)   # SenderSubID

    def test_body_len_includes_the_header_and_the_leg_group(self):
        # 280-byte fixed part; 8 bytes per LegOrdGrp record.
        self.assertEqual(new_order_body_len(), 280)
        self.assertEqual(new_order_body_len(2), 296)
        self.assertEqual(new_order_body_len() % 8, 0)

    def test_default_template_is_the_replacement_not_the_removed_one(self):
        self.assertEqual(self.engine.default_template_id,
                         TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG)
        self.assertEqual(TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG, 10138)
        self.assertIn(10100, DECOMMISSIONED_TEMPLATE_REPLACEMENTS)
        self.assertEqual(DECOMMISSIONED_TEMPLATE_REPLACEMENTS[10100], 10138)

    def test_sequence_starts_after_the_session_logon(self):
        # Session Logon is MsgSeqNum 1, so the first order request is 2.
        self.assertEqual(self.engine.next_msg_seq_num, 2)
        self.assertEqual(self.engine.format_t7_eti_header(body_len=280).msg_seq_num, 2)
        self.assertEqual(self.engine.format_t7_eti_header(body_len=280).msg_seq_num, 3)

    def test_reset_session_returns_to_the_post_logon_number(self):
        self.engine.format_t7_eti_header(body_len=280)
        self.engine.reset_session()
        self.assertEqual(self.engine.format_t7_eti_header(body_len=280).msg_seq_num, 2)

    def test_body_len_below_the_header_length_is_refused(self):
        with self.assertRaises(EurexOrderValidationError):
            self.engine.format_t7_eti_header(body_len=8)


class TestOrderValidation(unittest.TestCase):

    def setUp(self):
        self.engine = EurexMarketDataAndOrderApiEngine(sender_sub_id=55443)
        self.banded = EurexMarketDataAndOrderApiEngine(
            sender_sub_id=55443,
            contract_specs={"FESX": fesx_spec(FESX_FLAT_50_TABLE)})

    def test_valid_fesx_order_is_ready_to_send(self):
        report = self.engine.process_eurex_order(fesx_order(), fesx_book())
        self.assertTrue(report.ready_to_send)
        self.assertEqual(report.status, STATUS_OK)
        # 10 contracts x 4851 index points x EUR 10 per point.
        self.assertEqual(report.contract_value_eur, Decimal("485100"))
        self.assertEqual(report.price_eti_int, 485_100_000_000)
        self.assertEqual(report.order_qty_eti_int, 100_000)
        self.assertEqual(report.side_wire_value, SIDE_BUY)
        self.assertEqual(report.eti_header.template_id, 10138)
        self.assertEqual(report.eti_header.sender_sub_id, 55443)

    def test_fgbl_contract_value_uses_the_percent_of_par_multiplier(self):
        engine = EurexMarketDataAndOrderApiEngine(sender_sub_id=55443)
        report = engine.process_eurex_order(fesx_order(
            contract_symbol="FGBL", price="133.33", order_qty=5))
        # 5 contracts x 133.33 percent x EUR 1,000 per percentage point.
        self.assertEqual(report.contract_value_eur, Decimal("666650.00"))
        self.assertEqual(report.status, STATUS_OK)

    def test_off_tick_price_is_rejected(self):
        report = self.engine.process_eurex_order(fesx_order(price="4851.5"))
        self.assertFalse(report.ready_to_send)
        self.assertEqual(report.status, STATUS_INVALID_TICK_SIZE)
        self.assertEqual(report.required_tick_step, Decimal("1"))

    def test_aggressive_price_beyond_the_band_is_rejected(self):
        report = self.banded.process_eurex_order(
            fesx_order(price="4950"), fesx_book())
        self.assertFalse(report.ready_to_send)
        self.assertEqual(report.status, STATUS_PRICE_REASONABILITY_BREACH)
        self.assertIsNotNone(report.price_reasonability)

    def test_passive_price_far_from_the_mid_is_accepted(self):
        # The previous symmetric implementation rejected this buy for deviating
        # 100.5 points from the mid. T7 does not.
        report = self.banded.process_eurex_order(
            fesx_order(price="4750"), fesx_book())
        self.assertTrue(report.ready_to_send)
        self.assertEqual(report.price_reasonability.procedure, PRC_PROCEDURE_STANDARD)

    def test_missing_price_range_table_surfaces_as_a_warning_not_a_pass(self):
        report = self.engine.process_eurex_order(fesx_order(), fesx_book())
        self.assertTrue(report.ready_to_send)
        self.assertEqual(report.price_reasonability.procedure, PRC_PROCEDURE_NOT_PERFORMED)
        self.assertTrue(any("PriceRangeRules" in w for w in report.warnings))

    def test_no_book_skips_the_check_but_keeps_the_others(self):
        report = self.engine.process_eurex_order(fesx_order(price="4851.5"))
        self.assertEqual(report.status, STATUS_INVALID_TICK_SIZE)
        self.assertIsNone(report.price_reasonability)

    def test_unknown_contract_is_rejected(self):
        report = self.engine.process_eurex_order(fesx_order(contract_symbol="FZZZ"))
        self.assertEqual(report.status, STATUS_UNKNOWN_CONTRACT)
        self.assertFalse(report.ready_to_send)

    def test_invalid_field_domains_are_rejected(self):
        cases = {
            "zero quantity": {"order_qty": 0},
            "fractional quantity": {"order_qty": 1.5},
            "unknown side": {"side": "HOLD"},
            "negative price": {"price": "-4851"},
            "zero price": {"price": "0"},
            "cash-only trading capacity": {"trading_capacity": 9},
            "market order": {"ord_type": ORD_TYPE_MARKET},
            "unknown time in force": {"time_in_force": 99},
            "unknown price validity check": {"price_validity_check_type": 7},
            "non-integer security id": {"security_id": "4128839"},
        }
        for label, override in cases.items():
            with self.subTest(case=label):
                report = self.engine.process_eurex_order(fesx_order(**override))
                self.assertEqual(report.status, STATUS_INVALID_ORDER_FIELD)
                self.assertFalse(report.ready_to_send)

    def test_a_rejected_order_consumes_no_sequence_number(self):
        before = self.engine.next_msg_seq_num
        self.engine.process_eurex_order(fesx_order(price="4851.5"))
        self.engine.process_eurex_order(fesx_order(order_qty=0))
        self.engine.process_eurex_order(fesx_order(contract_symbol="FZZZ"))
        self.assertEqual(self.engine.next_msg_seq_num, before)
        # A gap in MsgSeqNum disconnects the ETI session, so this matters.
        self.assertTrue(self.engine.process_eurex_order(fesx_order()).ready_to_send)
        self.assertEqual(self.engine.next_msg_seq_num, before + 1)

    def test_reusing_a_cl_ord_id_is_refused(self):
        first = self.engine.process_eurex_order(fesx_order(cl_ord_id=7))
        self.assertTrue(first.ready_to_send)
        seq_after_first = self.engine.next_msg_seq_num
        second = self.engine.process_eurex_order(fesx_order(cl_ord_id=7, price="4852"))
        self.assertEqual(second.status, STATUS_DUPLICATE_CL_ORD_ID)
        self.assertFalse(second.ready_to_send)
        self.assertEqual(self.engine.next_msg_seq_num, seq_after_first)

    def test_cl_ord_ids_survive_a_session_reset(self):
        self.engine.process_eurex_order(fesx_order(cl_ord_id=7))
        self.engine.reset_session()
        replay = self.engine.process_eurex_order(fesx_order(cl_ord_id=7))
        self.assertEqual(replay.status, STATUS_DUPLICATE_CL_ORD_ID)

    def test_a_removed_template_is_flagged_with_its_replacement(self):
        report = self.engine.process_eurex_order(fesx_order(), template_id=10100)
        self.assertTrue(report.ready_to_send)
        self.assertTrue(any("10138" in w for w in report.warnings))
        self.assertEqual(report.eti_header.template_id, 10100)

    def test_wrong_request_type_raises_rather_than_reporting(self):
        with self.assertRaises(EurexOrderValidationError):
            self.engine.process_eurex_order({"cl_ord_id": 1})

    def test_deprecated_names_still_resolve(self):
        self.assertIs(EurexOrderExecutionReport, EurexOrderValidationReport)
        report = self.engine.process_eurex_order(fesx_order())
        self.assertTrue(report.is_dispatched)
        self.assertEqual(report.is_dispatched, report.ready_to_send)


if __name__ == "__main__":
    unittest.main()

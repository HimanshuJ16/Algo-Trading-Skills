import unittest

from exchange_self_match_prevention_configuration import (
    CANCEL_AGGRESSIVE,
    CANCEL_BOTH,
    CANCEL_RESTING,
    DECREMENT_AND_CANCEL,
    DECREMENT_SYMMETRIC,
    ExchangeSelfMatchPreventionEngine,
    RestingBookOrder,
    SmpAuditReport,
    SmpConfigurationError,
    SmpOrderRequest,
    SmpVenueProfile,
)

# A venue that supports symmetric decrement, used to exercise the Nasdaq-style
# model without asserting a per-order wire value Nasdaq does not actually expose.
SYMMETRIC_VENUE = SmpVenueProfile(
    venue="TEST_SYMMETRIC",
    smp_id_tag="group_id",
    smp_instruction_tag="stp_action",
    wire_values={
        CANCEL_RESTING: "1",
        CANCEL_AGGRESSIVE: "2",
        CANCEL_BOTH: "3",
        DECREMENT_AND_CANCEL: "4",
    },
    default_instruction=None,
    decrement_model=DECREMENT_SYMMETRIC,
)

# Declares decrement support but no model - must be refused, not guessed at.
MODEL_LESS_VENUE = SmpVenueProfile(
    venue="TEST_NO_MODEL",
    smp_id_tag="group_id",
    smp_instruction_tag="stp_action",
    wire_values={CANCEL_RESTING: "1", DECREMENT_AND_CANCEL: "4"},
    default_instruction=None,
    decrement_model=None,
)


def resting(cl_ord_id, side, qty, price, smp_id="SMP_PROP_100", symbol="AAPL", seq=None):
    return RestingBookOrder(cl_ord_id, symbol, side, qty, price, smp_id, seq)


def request(
    cl_ord_id="ORD_IN",
    side="BUY",
    qty=100,
    price=185.00,
    smp_id="SMP_PROP_100",
    instruction=CANCEL_RESTING,
    symbol="AAPL",
):
    return SmpOrderRequest(cl_ord_id, symbol, side, qty, price, smp_id, instruction)


class TestWireEncoding(unittest.TestCase):
    """The report's wire values must be what the venue actually accepts."""

    def test_cme_ilink2_uses_tag_7928_and_8000_with_char_values(self):
        engine = ExchangeSelfMatchPreventionEngine(venue="CME_ILINK2")
        fields = engine.encode_smp_fields("8810123", CANCEL_RESTING)
        self.assertEqual(fields.smp_id_tag, "7928")
        self.assertEqual(fields.smp_instruction_tag, "8000")
        self.assertEqual(fields.smp_instruction_wire_value, "O")
        self.assertEqual(
            engine.encode_smp_fields("8810123", CANCEL_AGGRESSIVE).smp_instruction_wire_value,
            "N",
        )

    def test_cme_ilink3_moves_the_id_to_tag_2362(self):
        engine = ExchangeSelfMatchPreventionEngine(venue="CME_ILINK3")
        fields = engine.encode_smp_fields("8810123", CANCEL_RESTING)
        self.assertEqual(fields.smp_id_tag, "2362")
        self.assertEqual(fields.smp_instruction_tag, "8000")

    def test_fix_latest_uses_tag_2964_integer_enum(self):
        engine = ExchangeSelfMatchPreventionEngine(
            venue="FIX_LATEST", default_smp_instruction=None
        )
        self.assertEqual(
            engine.encode_smp_fields("G1", CANCEL_AGGRESSIVE).smp_instruction_wire_value, "1"
        )
        self.assertEqual(
            engine.encode_smp_fields("G1", CANCEL_RESTING).smp_instruction_wire_value, "2"
        )
        fields = engine.encode_smp_fields("G1", CANCEL_BOTH)
        self.assertEqual(fields.smp_instruction_tag, "2964")
        self.assertEqual(fields.smp_instruction_wire_value, "3")

    def test_coinbase_uses_stp_short_codes(self):
        engine = ExchangeSelfMatchPreventionEngine(
            venue="COINBASE_EXCHANGE", default_smp_instruction=None
        )
        self.assertEqual(engine.profile.smp_instruction_tag, "stp")
        for instruction, expected in (
            (DECREMENT_AND_CANCEL, "dc"),
            (CANCEL_RESTING, "co"),
            (CANCEL_AGGRESSIVE, "cn"),
            (CANCEL_BOTH, "cb"),
        ):
            self.assertEqual(
                engine.encode_smp_fields("P1", instruction).smp_instruction_wire_value,
                expected,
            )

    def test_instruction_is_case_and_whitespace_insensitive(self):
        engine = ExchangeSelfMatchPreventionEngine()
        self.assertEqual(
            engine.encode_smp_fields("S1", "  cancel_resting ").smp_instruction_wire_value,
            "O",
        )

    def test_unknown_venue_is_rejected(self):
        with self.assertRaises(SmpConfigurationError):
            ExchangeSelfMatchPreventionEngine(venue="NYSE_PILLAR")

    def test_shipped_profile_supported_set_cannot_be_widened_at_runtime(self):
        # A frozen dataclass holding a plain dict would still let a caller teach
        # the Globex profile an instruction Globex rejects.
        engine = ExchangeSelfMatchPreventionEngine(venue="CME_ILINK2")
        with self.assertRaises(TypeError):
            engine.profile.wire_values[CANCEL_BOTH] = "Z"
        self.assertEqual(
            engine.profile.supported_instructions(),
            (CANCEL_RESTING, CANCEL_AGGRESSIVE),
        )

    def test_venue_that_does_not_offer_an_instruction_rejects_it(self):
        # Globex offers cancel-resting and cancel-aggressing only. Silently
        # downgrading CANCEL_BOTH would leave one side of a wash trade live.
        engine = ExchangeSelfMatchPreventionEngine(venue="CME_ILINK2")
        with self.assertRaises(SmpConfigurationError):
            engine.encode_smp_fields("S1", CANCEL_BOTH)
        with self.assertRaises(SmpConfigurationError):
            engine.encode_smp_fields("S1", DECREMENT_AND_CANCEL)


class TestInstructionResolution(unittest.TestCase):

    def test_unknown_instruction_raises_instead_of_defaulting(self):
        # Regression: a typo previously fell through to CANCEL_RESTING and the
        # aggressive order was reported as dispatched.
        engine = ExchangeSelfMatchPreventionEngine()
        book = [resting("R1", "SELL", 100, 185.00)]
        with self.assertRaises(SmpConfigurationError):
            engine.audit_and_apply_smp(request(instruction="CANCEL_RESTNG"), book)

    def test_blank_instruction_inherits_the_engine_default(self):
        # Regression: default_smp_instruction was stored and never consulted.
        engine = ExchangeSelfMatchPreventionEngine(
            default_smp_instruction=CANCEL_AGGRESSIVE
        )
        book = [resting("R1", "SELL", 100, 185.00)]
        report = engine.audit_and_apply_smp(request(instruction=""), book)
        self.assertEqual(report.smp_instruction, CANCEL_AGGRESSIVE)
        self.assertEqual(report.instruction_source, "ENGINE_DEFAULT")
        self.assertFalse(report.is_order_dispatched)

    def test_request_instruction_overrides_the_engine_default(self):
        engine = ExchangeSelfMatchPreventionEngine(
            default_smp_instruction=CANCEL_AGGRESSIVE
        )
        report = engine.audit_and_apply_smp(
            request(instruction=CANCEL_RESTING), [resting("R1", "SELL", 100, 185.00)]
        )
        self.assertEqual(report.smp_instruction, CANCEL_RESTING)
        self.assertEqual(report.instruction_source, "REQUEST")

    def test_no_default_makes_the_instruction_mandatory(self):
        engine = ExchangeSelfMatchPreventionEngine(default_smp_instruction=None)
        with self.assertRaises(SmpConfigurationError):
            engine.audit_and_apply_smp(request(instruction=""), [])

    def test_engine_default_must_be_supported_by_the_venue(self):
        with self.assertRaises(SmpConfigurationError):
            ExchangeSelfMatchPreventionEngine(
                venue="CME_ILINK2", default_smp_instruction=CANCEL_BOTH
            )


class TestSmpIdScoping(unittest.TestCase):

    def test_blank_smp_id_is_rejected_by_default(self):
        engine = ExchangeSelfMatchPreventionEngine()
        with self.assertRaises(SmpConfigurationError):
            engine.audit_and_apply_smp(request(smp_id="   "), [])

    def test_two_blank_smp_ids_are_not_a_self_match(self):
        # Regression: an absent SMP ID means SMP is off at the venue. Matching
        # blank against blank previously produced a false collision.
        engine = ExchangeSelfMatchPreventionEngine(require_smp_id=False)
        report = engine.audit_and_apply_smp(
            request(smp_id=""), [resting("R1", "SELL", 100, 185.00, smp_id="")]
        )
        self.assertFalse(report.has_self_collision)
        self.assertTrue(report.is_order_dispatched)

    def test_different_smp_id_does_not_collide(self):
        engine = ExchangeSelfMatchPreventionEngine()
        report = engine.audit_and_apply_smp(
            request(), [resting("R1", "SELL", 100, 185.00, smp_id="SMP_OTHER")]
        )
        self.assertFalse(report.has_self_collision)
        self.assertEqual(report.dispatched_qty, 100)
        self.assertEqual(report.collisions, ())
        self.assertIsNone(report.colliding_resting_ord_id)

    def test_surrounding_whitespace_in_smp_ids_still_matches(self):
        engine = ExchangeSelfMatchPreventionEngine()
        report = engine.audit_and_apply_smp(
            request(smp_id=" SMP_PROP_100 "),
            [resting("R1", "SELL", 100, 185.00, smp_id="SMP_PROP_100 ")],
        )
        self.assertTrue(report.has_self_collision)


class TestCollisionDetection(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeSelfMatchPreventionEngine()

    def test_same_side_resting_order_never_collides(self):
        report = self.engine.audit_and_apply_smp(
            request(side="BUY"), [resting("R1", "BUY", 100, 185.00)]
        )
        self.assertFalse(report.has_self_collision)

    def test_other_symbol_never_collides(self):
        report = self.engine.audit_and_apply_smp(
            request(symbol="AAPL"), [resting("R1", "SELL", 100, 185.00, symbol="MSFT")]
        )
        self.assertFalse(report.has_self_collision)

    def test_non_crossing_buy_does_not_collide(self):
        report = self.engine.audit_and_apply_smp(
            request(side="BUY", price=184.99), [resting("R1", "SELL", 100, 185.00)]
        )
        self.assertFalse(report.has_self_collision)

    def test_buy_at_the_ask_collides(self):
        report = self.engine.audit_and_apply_smp(
            request(side="BUY", price=185.00), [resting("R1", "SELL", 100, 185.00)]
        )
        self.assertTrue(report.has_self_collision)

    def test_non_crossing_sell_does_not_collide(self):
        report = self.engine.audit_and_apply_smp(
            request(side="SELL", price=185.01), [resting("R1", "BUY", 100, 185.00)]
        )
        self.assertFalse(report.has_self_collision)

    def test_sell_at_the_bid_collides(self):
        report = self.engine.audit_and_apply_smp(
            request(side="SELL", price=185.00), [resting("R1", "BUY", 100, 185.00)]
        )
        self.assertTrue(report.has_self_collision)

    def test_market_buy_collides_with_every_own_offer(self):
        # Regression: an unpriced order previously raised TypeError comparing
        # None to a float, so the highest-risk order type was never audited.
        book = [resting("R1", "SELL", 100, 190.00), resting("R2", "SELL", 100, 185.00)]
        report = self.engine.audit_and_apply_smp(
            request(side="BUY", price=None), book
        )
        self.assertTrue(report.has_self_collision)
        self.assertEqual(
            [c.resting_cl_ord_id for c in report.collisions], ["R2", "R1"]
        )

    def test_market_sell_collides_with_every_own_bid(self):
        book = [resting("R1", "BUY", 100, 180.00), resting("R2", "BUY", 100, 185.00)]
        report = self.engine.audit_and_apply_smp(
            request(side="SELL", price=None), book
        )
        self.assertEqual(
            [c.resting_cl_ord_id for c in report.collisions], ["R2", "R1"]
        )

    def test_empty_book_is_accepted(self):
        report = self.engine.audit_and_apply_smp(request(), [])
        self.assertFalse(report.has_self_collision)
        self.assertIsNone(self.engine.audit_and_apply_smp(request()).colliding_resting_ord_id)


class TestMatchOrdering(unittest.TestCase):
    """Match order must be price-then-time, not the caller's list order."""

    def setUp(self):
        self.engine = ExchangeSelfMatchPreventionEngine()

    def test_buy_reaches_the_lowest_own_offer_first(self):
        # Regression: the engine returned whichever collision appeared first in
        # the input list, so the reported trigger depended on list order.
        book = [resting("FAR", "SELL", 10, 186.00), resting("NEAR", "SELL", 10, 185.00)]
        report = self.engine.audit_and_apply_smp(
            request(side="BUY", price=187.00, qty=30), book
        )
        self.assertEqual(report.colliding_resting_ord_id, "NEAR")
        self.assertEqual([c.resting_cl_ord_id for c in report.collisions], ["NEAR", "FAR"])

    def test_sell_reaches_the_highest_own_bid_first(self):
        book = [resting("LOW", "BUY", 10, 184.00), resting("HIGH", "BUY", 10, 185.00)]
        report = self.engine.audit_and_apply_smp(
            request(side="SELL", price=183.00, qty=30), book
        )
        self.assertEqual([c.resting_cl_ord_id for c in report.collisions], ["HIGH", "LOW"])

    def test_entry_seq_breaks_ties_within_a_price_level(self):
        book = [
            resting("LATE", "SELL", 10, 185.00, seq=9),
            resting("EARLY", "SELL", 10, 185.00, seq=2),
        ]
        report = self.engine.audit_and_apply_smp(
            request(side="BUY", price=185.00, qty=30), book
        )
        self.assertEqual([c.resting_cl_ord_id for c in report.collisions], ["EARLY", "LATE"])

    def test_input_order_breaks_ties_when_entry_seq_is_absent(self):
        book = [resting("FIRST", "SELL", 10, 185.00), resting("SECOND", "SELL", 10, 185.00)]
        report = self.engine.audit_and_apply_smp(
            request(side="BUY", price=185.00, qty=30), book
        )
        self.assertEqual([c.resting_cl_ord_id for c in report.collisions], ["FIRST", "SECOND"])


class TestCancelResting(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeSelfMatchPreventionEngine()

    def test_single_collision_cancels_the_resting_order_and_dispatches(self):
        report = self.engine.audit_and_apply_smp(
            request(instruction=CANCEL_RESTING), [resting("R1", "SELL", 100, 185.00)]
        )
        self.assertTrue(report.has_self_collision)
        self.assertEqual(report.colliding_resting_ord_id, "R1")
        self.assertTrue(report.is_order_dispatched)
        self.assertEqual(report.dispatched_qty, 100)
        self.assertTrue(report.resting_order_cancelled)
        self.assertEqual(report.resting_cl_ord_ids_cancelled, ("R1",))
        self.assertEqual(report.collisions[0].resting_qty_cancelled, 100)
        self.assertEqual(report.collisions[0].resting_qty_remaining, 0)

    def test_a_sweep_cancels_every_reachable_own_order(self):
        # Regression: only the first collision was reported, understating a
        # sweep that the venue cancels in full at each executable price level.
        book = [
            resting("R1", "SELL", 10, 185.00),
            resting("R2", "SELL", 10, 185.50),
            resting("R3", "SELL", 10, 190.00),  # out of reach at 186.00
        ]
        report = self.engine.audit_and_apply_smp(
            request(side="BUY", price=186.00, qty=25, instruction=CANCEL_RESTING), book
        )
        self.assertEqual(report.resting_cl_ord_ids_cancelled, ("R1", "R2"))
        self.assertEqual(report.dispatched_qty, 25)

    def test_aggressor_survives_in_full_because_the_venue_pulls_only_the_book(self):
        report = self.engine.audit_and_apply_smp(
            request(qty=500, instruction=CANCEL_RESTING),
            [resting("R1", "SELL", 100, 185.00)],
        )
        self.assertEqual(report.dispatched_qty, 500)
        self.assertEqual(report.collisions[0].aggressor_qty_cancelled, 0)


class TestCancelAggressiveAndBoth(unittest.TestCase):

    def test_cancel_aggressive_blocks_the_incoming_order_and_keeps_the_book(self):
        engine = ExchangeSelfMatchPreventionEngine()
        report = engine.audit_and_apply_smp(
            request(instruction=CANCEL_AGGRESSIVE), [resting("R1", "SELL", 100, 185.00)]
        )
        self.assertTrue(report.has_self_collision)
        self.assertFalse(report.is_order_dispatched)
        self.assertEqual(report.dispatched_qty, 0)
        self.assertFalse(report.resting_order_cancelled)
        self.assertEqual(report.resting_cl_ord_ids_cancelled, ())
        self.assertEqual(report.collisions[0].resting_qty_remaining, 100)

    def test_cancel_aggressive_reports_only_the_first_contact(self):
        # Once the aggressor is pulled it never reaches the deeper own orders.
        engine = ExchangeSelfMatchPreventionEngine()
        book = [resting("R1", "SELL", 10, 185.00), resting("R2", "SELL", 10, 185.50)]
        report = engine.audit_and_apply_smp(
            request(side="BUY", price=186.00, qty=25, instruction=CANCEL_AGGRESSIVE), book
        )
        self.assertEqual([c.resting_cl_ord_id for c in report.collisions], ["R1"])

    def test_cancel_both_cancels_the_incoming_and_the_first_resting_order(self):
        engine = ExchangeSelfMatchPreventionEngine(
            venue="FIX_LATEST", default_smp_instruction=None
        )
        report = engine.audit_and_apply_smp(
            request(instruction=CANCEL_BOTH), [resting("R1", "SELL", 100, 185.00)]
        )
        self.assertEqual(report.dispatched_qty, 0)
        self.assertEqual(report.resting_cl_ord_ids_cancelled, ("R1",))
        self.assertEqual(report.collisions[0].aggressor_qty_cancelled, 100)
        self.assertEqual(report.smp_instruction_wire_value, "3")


class TestDecrementAndCancel(unittest.TestCase):

    def test_symmetric_model_removes_the_smaller_side_from_both(self):
        engine = ExchangeSelfMatchPreventionEngine(
            venue=SYMMETRIC_VENUE, default_smp_instruction=None
        )
        report = engine.audit_and_apply_smp(
            request(qty=25, price=186.00, instruction=DECREMENT_AND_CANCEL),
            [resting("R1", "SELL", 10, 185.00), resting("R2", "SELL", 20, 185.50)],
        )
        first, second = report.collisions
        self.assertEqual((first.resting_qty_cancelled, first.resting_qty_remaining), (10, 0))
        self.assertEqual((second.resting_qty_cancelled, second.resting_qty_remaining), (15, 5))
        self.assertEqual(report.dispatched_qty, 0)
        self.assertEqual(
            sum(c.aggressor_qty_cancelled for c in report.collisions) + report.dispatched_qty,
            25,
        )

    def test_symmetric_model_leaves_the_aggressor_remainder_live(self):
        engine = ExchangeSelfMatchPreventionEngine(
            venue=SYMMETRIC_VENUE, default_smp_instruction=None
        )
        report = engine.audit_and_apply_smp(
            request(qty=30, instruction=DECREMENT_AND_CANCEL),
            [resting("R1", "SELL", 10, 185.00)],
        )
        self.assertEqual(report.dispatched_qty, 20)
        self.assertTrue(report.is_order_dispatched)

    def test_symmetric_model_cancels_both_when_sizes_are_equal(self):
        engine = ExchangeSelfMatchPreventionEngine(
            venue=SYMMETRIC_VENUE, default_smp_instruction=None
        )
        report = engine.audit_and_apply_smp(
            request(qty=10, instruction=DECREMENT_AND_CANCEL),
            [resting("R1", "SELL", 10, 185.00)],
        )
        self.assertEqual(report.dispatched_qty, 0)
        self.assertEqual(report.collisions[0].resting_qty_remaining, 0)

    def test_coinbase_model_cancels_the_taker_and_decrements_the_maker(self):
        # Coinbase 'dc' is not the symmetric model: the taker always goes.
        engine = ExchangeSelfMatchPreventionEngine(
            venue="COINBASE_EXCHANGE", default_smp_instruction=None
        )
        report = engine.audit_and_apply_smp(
            SmpOrderRequest("T1", "BTC-USD", "BUY", 4, 100.0, "P1", DECREMENT_AND_CANCEL),
            [RestingBookOrder("M1", "BTC-USD", "SELL", 10, 100.0, "P1")],
        )
        self.assertEqual(report.dispatched_qty, 0)
        self.assertEqual(report.collisions[0].aggressor_qty_cancelled, 4)
        self.assertEqual(report.collisions[0].resting_qty_remaining, 6)

    def test_venue_without_a_decrement_model_refuses_to_simulate_it(self):
        engine = ExchangeSelfMatchPreventionEngine(
            venue=MODEL_LESS_VENUE, default_smp_instruction=None
        )
        with self.assertRaises(SmpConfigurationError):
            engine.audit_and_apply_smp(
                request(instruction=DECREMENT_AND_CANCEL),
                [resting("R1", "SELL", 100, 185.00)],
            )


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = ExchangeSelfMatchPreventionEngine()
        self.book = [resting("R1", "SELL", 100, 185.00)]

    def test_unknown_side_is_rejected_rather_than_treated_as_sell(self):
        # Regression: any side other than 'BUY' was silently treated as a sell.
        with self.assertRaises(SmpConfigurationError):
            self.engine.audit_and_apply_smp(request(side="SHORT"), self.book)

    def test_lowercase_side_is_accepted(self):
        report = self.engine.audit_and_apply_smp(request(side="buy"), self.book)
        self.assertTrue(report.has_self_collision)

    def test_non_positive_quantity_is_rejected(self):
        for qty in (0, -5):
            with self.assertRaises(SmpConfigurationError):
                self.engine.audit_and_apply_smp(request(qty=qty), self.book)

    def test_non_integer_quantity_is_rejected(self):
        for qty in (10.5, "10", True):
            with self.assertRaises(SmpConfigurationError):
                self.engine.audit_and_apply_smp(request(qty=qty), self.book)

    def test_non_finite_or_non_positive_price_is_rejected(self):
        for price in (float("nan"), float("inf"), 0.0, -1.0, "185.00"):
            with self.assertRaises(SmpConfigurationError):
                self.engine.audit_and_apply_smp(request(price=price), self.book)

    def test_empty_client_order_id_is_rejected(self):
        with self.assertRaises(SmpConfigurationError):
            self.engine.audit_and_apply_smp(request(cl_ord_id="  "), self.book)

    def test_empty_symbol_is_rejected(self):
        with self.assertRaises(SmpConfigurationError):
            self.engine.audit_and_apply_smp(request(symbol=""), self.book)

    def test_resting_order_with_no_price_is_rejected(self):
        book = [RestingBookOrder("R1", "AAPL", "SELL", 100, None, "SMP_PROP_100")]
        with self.assertRaises(SmpConfigurationError):
            self.engine.audit_and_apply_smp(request(), book)

    def test_resting_order_with_bad_quantity_is_rejected(self):
        with self.assertRaises(SmpConfigurationError):
            self.engine.audit_and_apply_smp(request(), [resting("R1", "SELL", 0, 185.00)])

    def test_non_resting_order_entry_is_rejected(self):
        with self.assertRaises(SmpConfigurationError):
            self.engine.audit_and_apply_smp(request(), [{"cl_ord_id": "R1"}])

    def test_non_string_smp_id_is_rejected(self):
        with self.assertRaises(SmpConfigurationError):
            self.engine.audit_and_apply_smp(request(smp_id=8810123), self.book)


class TestReportShape(unittest.TestCase):

    def test_report_carries_the_wire_fields_and_the_venue(self):
        engine = ExchangeSelfMatchPreventionEngine(venue="CME_ILINK2")
        report = engine.audit_and_apply_smp(request(), [resting("R1", "SELL", 100, 185.00)])
        self.assertIsInstance(report, SmpAuditReport)
        self.assertEqual(report.venue, "CME_ILINK2")
        self.assertEqual(report.smp_id_tag, "7928")
        self.assertEqual(report.smp_id_wire_value, "SMP_PROP_100")
        self.assertEqual(report.smp_instruction_tag, "8000")
        self.assertEqual(report.smp_instruction_wire_value, "O")

    def test_notes_state_that_the_venue_performs_the_cancels(self):
        engine = ExchangeSelfMatchPreventionEngine()
        report = engine.audit_and_apply_smp(request(), [resting("R1", "SELL", 100, 185.00)])
        self.assertIn("do not issue them locally", report.audit_notes)

    def test_report_is_immutable(self):
        engine = ExchangeSelfMatchPreventionEngine()
        report = engine.audit_and_apply_smp(request(), [])
        with self.assertRaises(Exception):
            report.is_order_dispatched = False


if __name__ == "__main__":
    unittest.main()

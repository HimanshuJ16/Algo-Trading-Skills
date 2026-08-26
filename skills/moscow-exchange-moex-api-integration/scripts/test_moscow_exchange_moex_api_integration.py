"""Behavioural tests for the MOEX pre-dispatch validation engine.

Reference data used in these tests is real, taken from the MOEX ISS endpoints on
2026-08-26 and recorded in ``references/standards.md``:

  * TQBR SBER   lot 1,     step 0.01,  decimals 2
  * TQBR GAZP   lot 10,    step 0.01,  decimals 2
  * TQBR VTBR   lot 10000, step 0.005, decimals 3
  * TQBR LKOH   lot 1,     step 0.5,   decimals 1
  * CETS CNYRUB_TOM lot 1000, step 0.0005, decimals 5
  * RFUD 92Q6   step 10,   decimals 0, LOWLIMIT 70240, HIGHLIMIT 77680
"""

import unittest
from datetime import date
from decimal import Decimal

from moscow_exchange_moex_api_integration import (
    MOEXApiIntegrationEngine,
    MOEXInstrument,
    MOEXOrderRequest,
    MOEXSessionConfig,
    SanctionsScreening,
    STATUS_BOARD_NOT_ON_ASTS_MFIX,
    STATUS_FIELD_LENGTH_BREACH,
    STATUS_INSTRUMENT_BOARD_MISMATCH,
    STATUS_NO_PRICE_CONTROL,
    STATUS_PRICE_LIMIT_BREACH,
    STATUS_PRICE_POLICY_BREACH,
    STATUS_PRICE_STEP_BREACH,
    STATUS_SANCTIONS_GATE_NOT_CLEARED,
    STATUS_UNKNOWN_BOARD,
    STATUS_VALIDATED,
)

CLEARED = SanctionsScreening(
    cleared=True,
    regimes=("OFAC-SDN", "EU", "UK-OFSI"),
    screened_on=date(2026, 8, 26),
    reference="COMP-2026-0826",
)

SBER = MOEXInstrument(
    secid="SBER", board="TQBR", lot_size=1, min_step="0.01", decimals=2,
    source="ISS /engines/stock/markets/shares/boards/TQBR", as_of=date(2026, 8, 26),
)
GAZP = MOEXInstrument(
    secid="GAZP", board="TQBR", lot_size=10, min_step="0.01", decimals=2,
)
VTBR = MOEXInstrument(
    secid="VTBR", board="TQBR", lot_size=10000, min_step="0.005", decimals=3,
)
LKOH = MOEXInstrument(
    secid="LKOH", board="TQBR", lot_size=1, min_step="0.5", decimals=1,
)
CNYRUB_TOM = MOEXInstrument(
    secid="CNYRUB_TOM", board="CETS", lot_size=1000, min_step="0.0005", decimals=5,
    currency="CNY",
)
FUT_92Q6 = MOEXInstrument(
    secid="92Q6", board="RFUD", lot_size=1, min_step="10", decimals=0,
    low_limit="70240", high_limit="77680",
    source="ISS /engines/futures/markets/forts/boards/RFUD", as_of=date(2026, 8, 26),
)


def session(**kwargs):
    params = dict(account="ACC_MOEX_01", sanctions_screening=CLEARED)
    params.update(kwargs)
    return MOEXSessionConfig(**params)


def order(**kwargs):
    params = dict(
        cl_ord_id="ORD-20260826-0001", secid="SBER", board="TQBR", side="BUY",
        quantity_lots=100, price="280.50", reference_price="280.00",
        max_price_deviation="0.05",
    )
    params.update(kwargs)
    return MOEXOrderRequest(**params)


class TestSanctionsGate(unittest.TestCase):
    """The gate must fail closed: only an explicit cleared attestation passes."""

    def setUp(self):
        self.engine = MOEXApiIntegrationEngine()

    def test_absent_screening_blocks_the_order(self):
        report = self.engine.validate_and_serialize_order(
            MOEXSessionConfig(account="ACC_MOEX_01"), order(), SBER
        )
        self.assertFalse(report.ready_to_send)
        self.assertEqual(report.status, STATUS_SANCTIONS_GATE_NOT_CLEARED)
        self.assertEqual(report.fix_fields, [])

    def test_uncleared_screening_blocks_the_order(self):
        cfg = session(sanctions_screening=SanctionsScreening(cleared=False))
        report = self.engine.validate_and_serialize_order(cfg, order(), SBER)
        self.assertFalse(report.ready_to_send)
        self.assertEqual(report.status, STATUS_SANCTIONS_GATE_NOT_CLEARED)

    def test_cleared_screening_requires_regimes_and_a_date(self):
        with self.assertRaises(ValueError):
            SanctionsScreening(cleared=True, screened_on=date(2026, 8, 26))
        with self.assertRaises(ValueError):
            SanctionsScreening(cleared=True, regimes=("OFAC-SDN",))

    def test_stale_screening_blocks_when_a_max_age_is_configured(self):
        old = SanctionsScreening(
            cleared=True, regimes=("OFAC-SDN",), screened_on=date(2026, 6, 1)
        )
        cfg = session(sanctions_screening=old, max_screening_age_days=30)
        # 2026-06-01 to 2026-08-26 is 86 days, well past the 30-day limit.
        report = self.engine.validate_and_serialize_order(
            cfg, order(), SBER, as_of=date(2026, 8, 26)
        )
        self.assertEqual(report.status, STATUS_SANCTIONS_GATE_NOT_CLEARED)

    def test_no_max_age_means_no_staleness_check(self):
        old = SanctionsScreening(
            cleared=True, regimes=("OFAC-SDN",), screened_on=date(2020, 1, 1)
        )
        cfg = session(sanctions_screening=old)
        report = self.engine.validate_and_serialize_order(cfg, order(), SBER)
        self.assertTrue(report.ready_to_send)

    def test_exact_max_age_boundary_still_passes(self):
        screened = SanctionsScreening(
            cleared=True, regimes=("OFAC-SDN",), screened_on=date(2026, 8, 1)
        )
        cfg = session(sanctions_screening=screened, max_screening_age_days=25)
        # 2026-08-01 to 2026-08-26 is exactly 25 days: not *older than* 25.
        self.assertTrue(self.engine.validate_and_serialize_order(
            cfg, order(), SBER, as_of=date(2026, 8, 26)).ready_to_send)
        self.assertFalse(self.engine.validate_and_serialize_order(
            cfg, order(), SBER, as_of=date(2026, 8, 27)).ready_to_send)

    def test_staleness_check_without_as_of_is_an_error_not_a_silent_pass(self):
        cfg = session(max_screening_age_days=30)
        with self.assertRaises(ValueError):
            self.engine.validate_and_serialize_order(cfg, order(), SBER)


class TestLotQuantity(unittest.TestCase):
    """Tag 38 is in lots; lot size is per Symbol + Board."""

    def setUp(self):
        self.engine = MOEXApiIntegrationEngine()

    def test_lot_size_one_leaves_units_equal_to_lots(self):
        report = self.engine.validate_and_serialize_order(session(), order(), SBER)
        self.assertEqual(report.quantity_lots, 100)
        self.assertEqual(report.quantity_units, 100)
        self.assertEqual(report.fix_field_map[38], "100")

    def test_gazp_100_lots_is_1000_shares(self):
        report = self.engine.validate_and_serialize_order(
            session(),
            order(secid="GAZP", quantity_lots=100, price="83.00",
                  reference_price="83.09"),
            GAZP,
        )
        self.assertEqual(report.quantity_units, 1000)
        self.assertEqual(report.fix_field_map[38], "100")

    def test_vtbr_units_convert_to_lots_not_the_other_way_round(self):
        # VTBR lot size is 10,000. 100 lots is a million shares -- the regression
        # a lot-unaware engine introduces by putting 100 in Tag 38 verbatim.
        report = self.engine.validate_and_serialize_order(
            session(),
            order(secid="VTBR", quantity_lots=None, quantity_units=1000000,
                  price="51.165", reference_price="51.165"),
            VTBR,
        )
        self.assertEqual(report.quantity_lots, 100)
        self.assertEqual(report.quantity_units, 1000000)

    def test_units_that_are_not_a_whole_number_of_lots_raise(self):
        with self.assertRaises(ValueError) as ctx:
            VTBR.units_to_lots(15000)
        self.assertIn("10000", str(ctx.exception))
        self.assertIn("20000", str(ctx.exception))

    def test_exactly_one_of_lots_or_units_is_required(self):
        with self.assertRaises(ValueError):
            self.engine.validate_and_serialize_order(
                session(), order(quantity_lots=100, quantity_units=100), SBER)
        with self.assertRaises(ValueError):
            self.engine.validate_and_serialize_order(
                session(), order(quantity_lots=None, quantity_units=None), SBER)

    def test_non_positive_and_non_integer_quantities_are_refused(self):
        with self.assertRaises(ValueError):
            SBER.units_to_lots(0)
        with self.assertRaises(ValueError):
            SBER.units_to_lots(-100)
        with self.assertRaises(TypeError):
            SBER.units_to_lots(100.5)
        with self.assertRaises(TypeError):
            self.engine.validate_and_serialize_order(
                session(), order(quantity_lots=100.0), SBER)


class TestPriceStep(unittest.TestCase):
    """MOEX rejects a price that does not fit the minimal price step levels."""

    def setUp(self):
        self.engine = MOEXApiIntegrationEngine()

    def test_off_step_price_is_rejected_not_silently_rounded(self):
        report = self.engine.validate_and_serialize_order(
            session(), order(price="280.505"), SBER)
        self.assertFalse(report.ready_to_send)
        self.assertEqual(report.status, STATUS_PRICE_STEP_BREACH)
        # The caller's price is reported back unchanged.
        self.assertEqual(report.price, Decimal("280.505"))

    def test_lkoh_half_rouble_step_rejects_a_kopek_price(self):
        # LKOH steps in 0.5 roubles: 4126.01 is off-step even though it is a
        # perfectly ordinary two-decimal rouble price.
        report = self.engine.validate_and_serialize_order(
            session(),
            order(secid="LKOH", price="4126.01", reference_price="4126.0"),
            LKOH,
        )
        self.assertEqual(report.status, STATUS_PRICE_STEP_BREACH)

    def test_alignment_never_moves_the_price_toward_the_market(self):
        self.assertEqual(SBER.align_price_to_step("280.505", "BUY"), Decimal("280.50"))
        self.assertEqual(SBER.align_price_to_step("280.505", "SELL"), Decimal("280.51"))
        self.assertEqual(LKOH.align_price_to_step("4126.01", "BUY"), Decimal("4126.0"))
        self.assertEqual(LKOH.align_price_to_step("4126.01", "SELL"), Decimal("4126.5"))

    def test_aligned_price_then_passes_the_engine(self):
        aligned = SBER.align_price_to_step("280.505", "BUY")
        report = self.engine.validate_and_serialize_order(
            session(), order(price=aligned), SBER)
        self.assertTrue(report.ready_to_send)
        self.assertEqual(report.fix_field_map[44], "280.50")

    def test_alignment_below_one_step_raises_rather_than_returning_zero(self):
        # Flooring 0.3 to the LKOH 0.5 step gives 0, which is not a sendable
        # price; it must not be returned as one.
        with self.assertRaises(ValueError):
            LKOH.align_price_to_step("0.3", "BUY")
        self.assertEqual(LKOH.align_price_to_step("0.3", "SELL"), Decimal("0.5"))

    def test_negative_price_is_caught_by_the_positivity_check(self):
        # -280.50 % 0.01 == 0, so the step test alone would pass it.
        self.assertTrue(SBER.is_on_step(Decimal("-280.50")))
        report = self.engine.validate_and_serialize_order(
            session(), order(price="-280.50"), SBER)
        self.assertEqual(report.status, STATUS_PRICE_STEP_BREACH)

    def test_float_input_does_not_leak_binary_representation_error(self):
        # 0.005 as a float is 0.005000000000000000104...; a naive modulo against
        # a float step turns a valid VTBR price into an off-step rejection.
        report = self.engine.validate_and_serialize_order(
            session(),
            order(secid="VTBR", quantity_lots=1, price=51.165,
                  reference_price=51.165),
            VTBR,
        )
        self.assertTrue(report.ready_to_send)
        self.assertEqual(report.fix_field_map[44], "51.165")

    def test_non_finite_and_non_numeric_prices_raise(self):
        for bad in ("NaN", "Infinity", float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.engine.validate_and_serialize_order(
                    session(), order(price=bad), SBER)
        with self.assertRaises(ValueError):
            self.engine.validate_and_serialize_order(
                session(), order(price="not-a-price"), SBER)
        with self.assertRaises(TypeError):
            self.engine.validate_and_serialize_order(
                session(), order(price=True), SBER)


class TestPriceControls(unittest.TestCase):
    """Exchange-published limits where they exist; a declared policy band otherwise."""

    def setUp(self):
        self.engine = MOEXApiIntegrationEngine()

    def test_a_limit_order_with_no_price_control_at_all_is_refused(self):
        report = self.engine.validate_and_serialize_order(
            session(),
            order(reference_price=None, max_price_deviation=None),
            SBER,
        )
        self.assertFalse(report.ready_to_send)
        self.assertEqual(report.status, STATUS_NO_PRICE_CONTROL)

    def test_zero_reference_price_no_longer_disables_the_check(self):
        # The previous implementation skipped the collar entirely when the
        # reference price was <= 0, approving any price at all.
        with self.assertRaises(ValueError):
            self.engine.validate_and_serialize_order(
                session(), order(reference_price="0"), SBER)

    def test_client_policy_band_is_directional_agnostic_and_uses_the_declared_limit(self):
        # |310.00 - 280.00| / 280.00 = 0.107142857..., independently > 0.05.
        report = self.engine.validate_and_serialize_order(
            session(), order(price="310.00"), SBER)
        self.assertEqual(report.status, STATUS_PRICE_POLICY_BREACH)
        self.assertEqual(report.price_control, "CLIENT_POLICY")

    def test_policy_band_boundary_is_inclusive(self):
        # 280.00 * 1.05 = 294.00 exactly; 294.01 is over.
        self.assertTrue(self.engine.validate_and_serialize_order(
            session(), order(price="294.00"), SBER).ready_to_send)
        self.assertFalse(self.engine.validate_and_serialize_order(
            session(), order(price="294.01"), SBER).ready_to_send)

    def test_published_limits_are_absolute_not_a_fixed_percentage(self):
        # 92Q6 publishes 70240 / 77680 against a 73960 settlement: +/-5.03%.
        # Other RFUD instruments on the same board publish bands over 11% wide
        # (A2U6: 1548.70 / 1939.10 against 1743.9), so no single percentage
        # reproduces the exchange's bounds. The band must be consumed, not
        # derived. The boundary itself is inclusive.
        for price, expected in (("77680", True), ("77680.01", False),
                                ("70240", True), ("70239.99", False),
                                ("73960", True)):
            self.assertIs(FUT_92Q6.is_within_exchange_limits(price), expected,
                          f"price {price}")

    def test_no_published_band_is_distinguishable_from_being_inside_one(self):
        self.assertIsNone(SBER.is_within_exchange_limits("280.50"))

    def test_published_limits_win_over_a_permissive_policy_band(self):
        instrument = MOEXInstrument(
            secid="SBER", board="TQBR", lot_size=1, min_step="0.01", decimals=2,
            low_limit="270.00", high_limit="290.00",
        )
        report = self.engine.validate_and_serialize_order(
            session(),
            order(price="291.00", reference_price="280.00",
                  max_price_deviation="0.50"),
            instrument,
        )
        self.assertEqual(report.status, STATUS_PRICE_LIMIT_BREACH)
        self.assertEqual(report.price_control, "EXCHANGE_LIMITS")

    def test_one_sided_published_limit_is_not_treated_as_a_band(self):
        instrument = MOEXInstrument(
            secid="SBER", board="TQBR", lot_size=1, min_step="0.01", decimals=2,
            high_limit="290.00",
        )
        self.assertFalse(instrument.has_exchange_price_limits)
        report = self.engine.validate_and_serialize_order(
            session(), order(reference_price=None, max_price_deviation=None),
            instrument)
        self.assertEqual(report.status, STATUS_NO_PRICE_CONTROL)


class TestBoardRouting(unittest.TestCase):
    def setUp(self):
        self.engine = MOEXApiIntegrationEngine()

    def test_unknown_board_is_refused(self):
        report = self.engine.validate_and_serialize_order(
            session(),
            order(board="TQZZ"),
            MOEXInstrument(secid="SBER", board="TQZZ", lot_size=1,
                           min_step="0.01", decimals=2),
        )
        self.assertEqual(report.status, STATUS_UNKNOWN_BOARD)

    def test_reference_data_from_the_wrong_board_is_refused(self):
        report = self.engine.validate_and_serialize_order(
            session(), order(board="CETS", secid="SBER"), SBER)
        self.assertEqual(report.status, STATUS_INSTRUMENT_BOARD_MISMATCH)

    def test_reference_data_for_the_wrong_symbol_is_refused(self):
        report = self.engine.validate_and_serialize_order(
            session(), order(secid="GAZP"), SBER)
        self.assertEqual(report.status, STATUS_INSTRUMENT_BOARD_MISMATCH)

    def test_derivatives_board_does_not_get_an_asts_mfix_message(self):
        report = self.engine.validate_and_serialize_order(
            session(),
            MOEXOrderRequest(cl_ord_id="F-1", secid="92Q6", board="RFUD",
                             side="BUY", quantity_lots=1, price="73960"),
            FUT_92Q6,
        )
        self.assertEqual(report.status, STATUS_BOARD_NOT_ON_ASTS_MFIX)
        self.assertEqual(report.fix_fields, [])

    def test_fx_board_order_is_built(self):
        report = self.engine.validate_and_serialize_order(
            session(),
            MOEXOrderRequest(cl_ord_id="FX-20260826-01", secid="CNYRUB_TOM",
                             board="CETS", side="SELL", quantity_units=100000,
                             price="12.4925", reference_price="12.49",
                             max_price_deviation="0.01"),
            CNYRUB_TOM,
        )
        self.assertTrue(report.ready_to_send)
        self.assertEqual(report.quantity_lots, 100)
        self.assertEqual(report.fix_field_map[336], "CETS")
        self.assertEqual(report.fix_field_map[54], "2")
        self.assertEqual(report.fix_field_map[44], "12.49250")


class TestFixFields(unittest.TestCase):
    def setUp(self):
        self.engine = MOEXApiIntegrationEngine()

    def test_board_travels_in_the_386_336_group_not_a_boardid_field(self):
        report = self.engine.validate_and_serialize_order(session(), order(), SBER)
        tags = [tag for tag, _ in report.fix_fields]
        self.assertIn(386, tags)
        self.assertIn(336, tags)
        # 386 must be immediately followed by 336, with nothing in between.
        self.assertEqual(tags[tags.index(386) + 1], 336)
        self.assertEqual(report.fix_field_map[386], "1")
        self.assertEqual(report.fix_field_map[336], "TQBR")

    def test_no_securityexchange_tag_is_emitted(self):
        # Tag 207 / 'MISX' does not appear anywhere in the MOEX FIX 4.4
        # interface specification. MISX is the ISO 10383 MIC, not an MFIX field.
        report = self.engine.validate_and_serialize_order(session(), order(), SBER)
        self.assertNotIn(207, report.fix_field_map)
        self.assertNotIn("MISX", [value for _, value in report.fix_fields])

    def test_client_code_travels_in_the_parties_group(self):
        report = self.engine.validate_and_serialize_order(
            session(client_code="CLIENT_99"), order(), SBER)
        tags = [tag for tag, _ in report.fix_fields]
        start = tags.index(453)
        self.assertEqual(tags[start:start + 4], [453, 448, 447, 452])
        self.assertEqual(report.fix_field_map[448], "CLIENT_99")
        self.assertEqual(report.fix_field_map[447], "D")
        self.assertEqual(report.fix_field_map[452], "3")

    def test_no_parties_group_when_no_client_code(self):
        report = self.engine.validate_and_serialize_order(session(), order(), SBER)
        self.assertNotIn(453, report.fix_field_map)

    def test_price_is_formatted_at_the_instrument_decimals(self):
        sber = self.engine.validate_and_serialize_order(session(), order(), SBER)
        self.assertEqual(sber.fix_field_map[44], "280.50")   # DECIMALS 2
        vtbr = self.engine.validate_and_serialize_order(
            session(),
            order(secid="VTBR", quantity_lots=1, price="51.165",
                  reference_price="51.165"),
            VTBR,
        )
        self.assertEqual(vtbr.fix_field_map[44], "51.165")   # DECIMALS 3
        lkoh = self.engine.validate_and_serialize_order(
            session(),
            order(secid="LKOH", price="4126.0", reference_price="4126.0"),
            LKOH,
        )
        self.assertEqual(lkoh.fix_field_map[44], "4126.0")   # DECIMALS 1

    def test_market_order_carries_a_zero_price(self):
        report = self.engine.validate_and_serialize_order(
            session(),
            order(ord_type="MARKET", price=None, reference_price=None,
                  max_price_deviation=None),
            SBER,
        )
        self.assertTrue(report.ready_to_send)
        self.assertEqual(report.fix_field_map[40], "1")
        self.assertEqual(report.fix_field_map[44], "0.00")
        self.assertEqual(report.price_control, "NOT_APPLICABLE")

    def test_market_order_with_a_price_is_a_caller_error(self):
        with self.assertRaises(ValueError):
            self.engine.validate_and_serialize_order(
                session(), order(ord_type="MARKET"), SBER)

    def test_optional_tags_appear_only_when_supplied(self):
        plain = self.engine.validate_and_serialize_order(session(), order(), SBER)
        self.assertNotIn(60, plain.fix_field_map)
        self.assertNotIn(59, plain.fix_field_map)
        full = self.engine.validate_and_serialize_order(
            session(),
            order(transact_time="20260826-09:59:59.123", time_in_force="IOC"),
            SBER,
        )
        self.assertEqual(full.fix_field_map[60], "20260826-09:59:59.123")
        self.assertEqual(full.fix_field_map[59], "3")

    def test_no_session_header_or_trailer_is_fabricated(self):
        report = self.engine.validate_and_serialize_order(session(), order(), SBER)
        for session_tag in (8, 9, 35, 34, 49, 56, 10):
            self.assertNotIn(session_tag, report.fix_field_map)


class TestFieldWidths(unittest.TestCase):
    def setUp(self):
        self.engine = MOEXApiIntegrationEngine()

    def test_cl_ord_id_over_20_characters_is_refused(self):
        # 'MOEX_CETS_CNYRUB_TOM_1000' -- the shape the previous implementation
        # generated -- is 25 characters and would be rejected on the wire.
        report = self.engine.validate_and_serialize_order(
            session(), order(cl_ord_id="MOEX_CETS_CNYRUB_TOM_1000"), SBER)
        self.assertEqual(report.status, STATUS_FIELD_LENGTH_BREACH)

    def test_cl_ord_id_at_exactly_20_characters_passes(self):
        twenty = "A" * 20
        self.assertEqual(len(twenty), 20)
        report = self.engine.validate_and_serialize_order(
            session(), order(cl_ord_id=twenty), SBER)
        self.assertTrue(report.ready_to_send)

    def test_cl_ord_id_starting_with_hash_is_refused(self):
        report = self.engine.validate_and_serialize_order(
            session(), order(cl_ord_id="#ORD-1"), SBER)
        self.assertEqual(report.status, STATUS_FIELD_LENGTH_BREACH)
        # A hash elsewhere in the string is allowed.
        self.assertTrue(self.engine.validate_and_serialize_order(
            session(), order(cl_ord_id="ORD#1"), SBER).ready_to_send)

    def test_empty_cl_ord_id_is_refused(self):
        report = self.engine.validate_and_serialize_order(
            session(), order(cl_ord_id=""), SBER)
        self.assertEqual(report.status, STATUS_FIELD_LENGTH_BREACH)

    def test_account_over_12_characters_is_refused(self):
        report = self.engine.validate_and_serialize_order(
            session(account="ACCOUNT_TOO_LONG"), order(), SBER)
        self.assertEqual(report.status, STATUS_FIELD_LENGTH_BREACH)

    def test_price_longer_than_ten_characters_is_refused(self):
        # A five-decimal FX quote at a six-figure price renders as 12 characters.
        instrument = MOEXInstrument(
            secid="WIDE", board="CETS", lot_size=1, min_step="0.00001", decimals=5,
        )
        report = self.engine.validate_and_serialize_order(
            session(),
            order(secid="WIDE", board="CETS", price="123456.00000",
                  reference_price="123456.00000"),
            instrument,
        )
        self.assertEqual(report.status, STATUS_FIELD_LENGTH_BREACH)

    def test_malformed_transact_time_is_refused(self):
        report = self.engine.validate_and_serialize_order(
            session(), order(transact_time="2026-08-26 09:59:59"), SBER)
        self.assertEqual(report.status, STATUS_FIELD_LENGTH_BREACH)

    def test_fix_delimiters_in_string_fields_are_refused(self):
        # A value carrying SOH or '=' would split the message into fields the
        # caller never wrote.
        for kwargs in ({"cl_ord_id": "ORD\x011"}, {"cl_ord_id": "ORD=1"}):
            report = self.engine.validate_and_serialize_order(
                session(), order(**kwargs), SBER)
            self.assertEqual(report.status, STATUS_FIELD_LENGTH_BREACH)
        report = self.engine.validate_and_serialize_order(
            session(account="ACC=1"), order(), SBER)
        self.assertEqual(report.status, STATUS_FIELD_LENGTH_BREACH)
        report = self.engine.validate_and_serialize_order(
            session(client_code="CLI\x01"), order(), SBER)
        self.assertEqual(report.status, STATUS_FIELD_LENGTH_BREACH)

    def test_unknown_time_in_force_is_refused(self):
        report = self.engine.validate_and_serialize_order(
            session(), order(time_in_force="GTC"), SBER)
        self.assertEqual(report.status, STATUS_FIELD_LENGTH_BREACH)


class TestReferenceData(unittest.TestCase):
    def test_reference_data_must_be_internally_consistent(self):
        with self.assertRaises(ValueError):
            # A 0.005 step cannot be expressed at 2 decimal places.
            MOEXInstrument(secid="X", board="TQBR", lot_size=1,
                           min_step="0.005", decimals=2)
        with self.assertRaises(ValueError):
            MOEXInstrument(secid="X", board="TQBR", lot_size=0,
                           min_step="0.01", decimals=2)
        with self.assertRaises(ValueError):
            MOEXInstrument(secid="X", board="TQBR", lot_size=1,
                           min_step="0", decimals=2)
        with self.assertRaises(ValueError):
            MOEXInstrument(secid="X", board="TQBR", lot_size=1, min_step="0.01",
                           decimals=2, low_limit="290", high_limit="280")

    def test_invalid_side_and_ord_type_raise(self):
        engine = MOEXApiIntegrationEngine()
        with self.assertRaises(ValueError):
            engine.validate_and_serialize_order(session(), order(side="BANANA"), SBER)
        with self.assertRaises(ValueError):
            engine.validate_and_serialize_order(
                session(), order(ord_type="STOP"), SBER)


class TestApprovedOrderEndToEnd(unittest.TestCase):
    def test_full_report_for_a_validated_sber_order(self):
        engine = MOEXApiIntegrationEngine()
        report = engine.validate_and_serialize_order(
            session(client_code="CLIENT_99"),
            order(transact_time="20260826-09:59:59"),
            SBER,
        )
        self.assertTrue(report.ready_to_send)
        self.assertEqual(report.status, STATUS_VALIDATED)
        self.assertEqual(report.price, Decimal("280.50"))
        self.assertEqual(report.price_control, "CLIENT_POLICY")
        self.assertEqual(
            report.fix_fields,
            [
                (11, "ORD-20260826-0001"),
                (453, "1"), (448, "CLIENT_99"), (447, "D"), (452, "3"),
                (1, "ACC_MOEX_01"),
                (386, "1"), (336, "TQBR"),
                (55, "SBER"),
                (54, "1"),
                (60, "20260826-09:59:59"),
                (38, "100"),
                (40, "2"),
                (44, "280.50"),
            ],
        )


if __name__ == "__main__":
    unittest.main()

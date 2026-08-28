import unittest
from datetime import datetime, timezone
from decimal import Decimal

from new_zealand_exchange_nzx_api import (
    SOH,
    NewZealandExchangeNZXEngine,
    NZXFixSessionConfig,
    NZXMarketPhase,
    NZXOrderRequest,
    NZXSecurityType,
    NZXSessionSchedule,
    NZXSide,
    NZXTickSchedule,
)

# Fixed instant so every generated message is byte-for-byte reproducible.
FIXED_CLOCK = datetime(2026, 3, 4, 21, 30, 15, 123000, tzinfo=timezone.utc)

# Placeholder identifiers. The real BeginString and CompIDs come from the FIX
# specification NZX issues to a Participant and are deliberately not defaulted
# by the engine, so tests must supply their own.
TEST_SESSION = NZXFixSessionConfig(
    sender_comp_id="TESTFIRM",
    target_comp_id="TESTNZX",
    begin_string="FIX.4.4",
)


def _auckland(h, m, s=0):
    """Naive datetime, interpreted by the schedule as Auckland wall-clock."""
    return datetime(2026, 3, 4, h, m, s)  # date arbitrary; only time-of-day matters


def _tags(payload):
    """Split a framed FIX message into an ordered list of (tag, value) pairs."""
    return [f.split("=", 1) for f in payload.split(SOH) if f]


def _tag_dict(payload):
    return {int(t): v for t, v in _tags(payload)}


class TestNZXPriceSteps(unittest.TestCase):
    """NZX published schedule: funds $0.001 flat; equities $0.001/$0.005/$0.01 by band."""

    def setUp(self):
        self.engine = NewZealandExchangeNZXEngine(TEST_SESSION, clock=lambda: FIXED_CLOCK)

    def test_equity_band_steps(self):
        self.assertEqual(self.engine.get_nzx_tick_size("0.15"), Decimal("0.001"))
        self.assertEqual(self.engine.get_nzx_tick_size("1.50"), Decimal("0.005"))
        self.assertEqual(self.engine.get_nzx_tick_size("25.00"), Decimal("0.01"))

    def test_equity_band_boundaries_are_exact(self):
        # $0.199 is the last price in the sub-$0.20 band; $0.20 is the first in the next.
        self.assertEqual(self.engine.get_nzx_tick_size("0.199"), Decimal("0.001"))
        self.assertEqual(self.engine.get_nzx_tick_size("0.20"), Decimal("0.005"))
        # $1.995 is the last $0.005 multiple below $2.00; $2.00 itself moves to $0.01.
        self.assertEqual(self.engine.get_nzx_tick_size("1.995"), Decimal("0.005"))
        self.assertEqual(self.engine.get_nzx_tick_size("2.00"), Decimal("0.01"))
        # Both readings of the $1.995/$2.00 boundary accept $2.00.
        self.assertTrue(self.engine.validate_price_tick("2.00")[0])

    def test_valid_and_invalid_equity_ticks(self):
        self.assertTrue(self.engine.validate_price_tick("1.50")[0])
        self.assertTrue(self.engine.validate_price_tick("0.155")[0])
        self.assertTrue(self.engine.validate_price_tick("30.00")[0])
        # $0.005 increments are rejected at and above $2.00.
        self.assertFalse(self.engine.validate_price_tick("25.005")[0])
        self.assertFalse(self.engine.validate_price_tick("30.005")[0])
        # $0.001 increments are rejected inside the $0.005 band.
        self.assertFalse(self.engine.validate_price_tick("1.501")[0])
        # $1.9975 is not a multiple of $0.005 under either boundary reading.
        self.assertFalse(self.engine.validate_price_tick("1.9975")[0])

    def test_listed_funds_tick_at_a_tenth_of_a_cent_at_any_price(self):
        # Regression: the band schedule must NOT be applied to listed funds.
        # A fund at $5.001 is valid; the same price on an equity is not.
        self.assertEqual(
            self.engine.get_nzx_tick_size("5.001", NZXSecurityType.FUND), Decimal("0.001")
        )
        self.assertTrue(self.engine.validate_price_tick("5.001", NZXSecurityType.FUND)[0])
        self.assertFalse(self.engine.validate_price_tick("5.001", NZXSecurityType.EQUITY)[0])

    def test_non_positive_and_non_finite_prices_are_never_valid(self):
        for bad in ("0", "0.00", "-1.50"):
            self.assertFalse(self.engine.validate_price_tick(bad)[0], bad)
        with self.assertRaises(ValueError):
            self.engine.validate_price_tick("NaN")
        with self.assertRaises(ValueError):
            self.engine.validate_price_tick("not-a-price")

    def test_float_repr_noise_is_not_a_tick_violation(self):
        # Regression: 0.1 + 0.2 == 0.30000000000000004 must still validate as $0.300.
        self.assertTrue(self.engine.validate_price_tick(0.1 + 0.2)[0])
        # ... but a genuine sub-tick price is still rejected.
        self.assertFalse(self.engine.validate_price_tick(0.3005)[0])

    def test_out_of_range_price_fails_validation_instead_of_raising_arithmetic(self):
        # Regression: Decimal.quantize raises InvalidOperation (NOT a ValueError)
        # past the context precision, which would escape the order builder.
        with self.assertRaises(ValueError):
            self.engine.validate_price_tick("1e30")
        report = self.engine.build_fix_new_order_single(
            NZXOrderRequest("NZX_BIG", "FPH", "BUY", 1, "1e30", "LIMIT", "DAY"), seq_num=1
        )
        self.assertEqual(report.status, "REJECTED")
        self.assertIn("representable range", report.rejection_reason)

    def test_yield_quoted_debt_is_refused_rather_than_mis_validated(self):
        with self.assertRaises(ValueError):
            self.engine.get_nzx_tick_size("5.00", NZXSecurityType.DEBT_YIELD_QUOTED)

    def test_tick_schedule_is_configurable(self):
        # Rule 11.9.1 lets NZX respecify steps, so the schedule must be overridable.
        flat = NZXTickSchedule(
            equity_bands=(), equity_top_step=Decimal("0.01"), fund_step=Decimal("0.01")
        )
        engine = NewZealandExchangeNZXEngine(
            TEST_SESSION, tick_schedule=flat, clock=lambda: FIXED_CLOCK
        )
        self.assertEqual(engine.get_nzx_tick_size("0.15"), Decimal("0.01"))
        self.assertFalse(engine.validate_price_tick("0.155")[0])


class TestNZXSessionSchedule(unittest.TestCase):
    """NZSX phases: Enquiry 17:30-08:30, Pre-Open 08:30, continuous 10:00-16:45."""

    def test_phase_boundaries(self):
        cases = [
            (_auckland(3, 0), NZXMarketPhase.ENQUIRY),
            (_auckland(8, 29, 59), NZXMarketPhase.ENQUIRY),
            (_auckland(8, 30), NZXMarketPhase.PRE_OPEN),
            (_auckland(9, 59, 29), NZXMarketPhase.PRE_OPEN),
            (_auckland(9, 59, 30), NZXMarketPhase.OPENING_AUCTION),
            (_auckland(10, 0, 30), NZXMarketPhase.NORMAL),
            (_auckland(12, 0), NZXMarketPhase.NORMAL),
            (_auckland(16, 44, 59), NZXMarketPhase.NORMAL),
            (_auckland(16, 45), NZXMarketPhase.PRE_CLOSE),
            (_auckland(16, 59, 30), NZXMarketPhase.CLOSING_AUCTION),
            (_auckland(17, 0, 30), NZXMarketPhase.ADJUST),
            (_auckland(17, 29, 59), NZXMarketPhase.ADJUST),
            (_auckland(17, 30), NZXMarketPhase.ENQUIRY),
        ]
        for dt, expected in cases:
            self.assertIs(NZXSessionSchedule.phase_at(dt), expected, dt)

    def test_order_entry_and_cancel_windows(self):
        # New orders: Pre-Open, Normal, Pre-Close only.
        self.assertTrue(NZXSessionSchedule.is_order_entry_window(_auckland(9, 0)))
        self.assertTrue(NZXSessionSchedule.is_order_entry_window(_auckland(12, 0)))
        self.assertTrue(NZXSessionSchedule.is_order_entry_window(_auckland(16, 50)))
        self.assertFalse(NZXSessionSchedule.is_order_entry_window(_auckland(7, 0)))
        self.assertFalse(NZXSessionSchedule.is_order_entry_window(_auckland(17, 10)))
        # Adjust permits withdraw but not new orders.
        self.assertTrue(NZXSessionSchedule.is_cancel_window(_auckland(17, 10)))
        self.assertFalse(NZXSessionSchedule.is_cancel_window(_auckland(3, 0)))

    def test_tz_aware_input_is_converted_across_the_nzdt_nzst_switch(self):
        # 21:30 UTC is 10:30 NZDT (UTC+13) in January and 09:30 NZST (UTC+12) in July,
        # so the same UTC instant is mid-session in one and still pre-open in the other.
        january = datetime(2026, 1, 15, 21, 30, tzinfo=timezone.utc)
        july = datetime(2026, 7, 15, 21, 30, tzinfo=timezone.utc)
        self.assertIs(NZXSessionSchedule.phase_at(january), NZXMarketPhase.NORMAL)
        self.assertIs(NZXSessionSchedule.phase_at(july), NZXMarketPhase.PRE_OPEN)


class TestNewOrderSingle(unittest.TestCase):

    def setUp(self):
        self.engine = NewZealandExchangeNZXEngine(TEST_SESSION, clock=lambda: FIXED_CLOCK)

    def _order(self, **overrides):
        kwargs = dict(
            cl_ord_id="NZX_001",
            symbol="FPH",
            side="BUY",
            quantity=1000,
            price="30.00",
            order_type="LIMIT",
            time_in_force="DAY",
        )
        kwargs.update(overrides)
        return NZXOrderRequest(**kwargs)

    def test_valid_limit_order_message_content(self):
        report = self.engine.build_fix_new_order_single(self._order(), seq_num=7)
        self.assertEqual(report.status, "NEW")
        self.assertEqual(report.fix_msg_type, "D")
        tags = _tag_dict(report.fix_raw_payload)
        self.assertEqual(tags[35], "D")
        self.assertEqual(tags[34], "7")
        self.assertEqual(tags[49], "TESTFIRM")
        self.assertEqual(tags[56], "TESTNZX")
        self.assertEqual(tags[11], "NZX_001")
        self.assertEqual(tags[55], "FPH")
        self.assertEqual(tags[54], "1")       # Buy
        self.assertEqual(tags[38], "1000")
        self.assertEqual(tags[40], "2")       # Limit
        self.assertEqual(tags[44], "30.00")
        self.assertEqual(tags[59], "0")       # Day
        self.assertEqual(tags[15], "NZD")

    def test_message_is_correctly_framed(self):
        report = self.engine.build_fix_new_order_single(self._order(), seq_num=1)
        payload = report.fix_raw_payload
        ordered = _tags(payload)
        # Standard header order: 8, 9, 35 first; CheckSum last.
        self.assertEqual([t for t, _ in ordered[:3]], ["8", "9", "35"])
        self.assertEqual(ordered[-1][0], "10")
        self.assertTrue(payload.endswith(SOH))

        tags = _tag_dict(payload)
        # BodyLength (9) counts bytes from after 9's delimiter to the delimiter before 10.
        start = payload.index(f"{SOH}35=") + 1
        end = payload.rindex(f"{SOH}10=") + 1
        self.assertEqual(int(tags[9]), len(payload[start:end].encode("ascii")))
        # CheckSum (10) is the byte sum of everything before it, mod 256.
        self.assertEqual(tags[10], f"{sum(payload[:end].encode('ascii')) % 256:03d}")
        self.assertEqual(len(tags[10]), 3)

    def test_transact_time_is_a_fix_utc_timestamp_not_epoch_millis(self):
        # Regression: tag 60 must be YYYYMMDD-HH:MM:SS.sss, not an integer.
        tags = _tag_dict(
            self.engine.build_fix_new_order_single(self._order(), seq_num=1).fix_raw_payload
        )
        self.assertEqual(tags[60], "20260304-21:30:15.123")
        self.assertEqual(tags[52], "20260304-21:30:15.123")
        self.assertFalse(tags[60].isdigit())

    def test_naive_clock_is_refused(self):
        engine = NewZealandExchangeNZXEngine(
            TEST_SESSION, clock=lambda: datetime(2026, 3, 4, 21, 30)
        )
        with self.assertRaises(ValueError):
            engine.build_fix_new_order_single(self._order(), seq_num=1)

    def test_sub_tick_price_is_rejected_without_building_a_message(self):
        report = self.engine.build_fix_new_order_single(
            self._order(cl_ord_id="NZX_002", price="30.005"), seq_num=2
        )
        self.assertEqual(report.status, "REJECTED")
        self.assertEqual(report.fix_raw_payload, "")
        # Regression: a local reject must not claim to be an ExecutionReport ('8').
        self.assertEqual(report.fix_msg_type, "")
        self.assertIn("price step schedule", report.rejection_reason)
        self.assertEqual(report.tick_size, Decimal("0.01"))

    def test_fund_priced_at_a_tenth_cent_is_accepted_only_as_a_fund(self):
        as_equity = self.engine.build_fix_new_order_single(
            self._order(symbol="SPY", price="5.001"), seq_num=3
        )
        self.assertEqual(as_equity.status, "REJECTED")
        as_fund = self.engine.build_fix_new_order_single(
            self._order(symbol="SPY", price="5.001", security_type=NZXSecurityType.FUND),
            seq_num=3,
        )
        self.assertEqual(as_fund.status, "NEW")
        self.assertEqual(_tag_dict(as_fund.fix_raw_payload)[44], "5.001")

    def test_unrecognised_side_is_rejected_not_silently_sold(self):
        # Regression: a typo'd side previously fell through to 54=2 (Sell).
        for bad in ("SEL", "sel1", "", "S"):
            report = self.engine.build_fix_new_order_single(
                self._order(side=bad), seq_num=4
            )
            self.assertEqual(report.status, "REJECTED", bad)
            self.assertIn("side", report.rejection_reason)
        # The accepted spellings still work in both cases.
        self.assertEqual(
            self.engine.build_fix_new_order_single(
                self._order(side="sell"), seq_num=4
            ).status, "NEW")
        self.assertEqual(
            _tag_dict(self.engine.build_fix_new_order_single(
                self._order(side=NZXSide.SELL), seq_num=4).fix_raw_payload)[54], "2")

    def test_unrecognised_order_type_is_rejected_not_silently_marketed(self):
        # Regression: 'LIMT' previously became a MARKET order that skipped tick checks.
        report = self.engine.build_fix_new_order_single(
            self._order(order_type="LIMT", price="30.005"), seq_num=5
        )
        self.assertEqual(report.status, "REJECTED")
        self.assertIn("order_type", report.rejection_reason)

    def test_unrecognised_time_in_force_is_rejected_not_defaulted_to_day(self):
        for bad in ("GTD", "OPG", "DAYY"):
            report = self.engine.build_fix_new_order_single(
                self._order(time_in_force=bad), seq_num=6
            )
            self.assertEqual(report.status, "REJECTED", bad)
            self.assertIn("time_in_force", report.rejection_reason)
        self.assertEqual(
            _tag_dict(self.engine.build_fix_new_order_single(
                self._order(time_in_force="FOK"), seq_num=6).fix_raw_payload)[59], "4")

    def test_invalid_quantities_are_rejected(self):
        for bad in (0, -100, 10.5, "1000", True):
            report = self.engine.build_fix_new_order_single(
                self._order(quantity=bad), seq_num=7
            )
            self.assertEqual(report.status, "REJECTED", bad)
            self.assertIn("quantity", report.rejection_reason)

    def test_market_order_omits_price_and_skips_tick_validation(self):
        report = self.engine.build_fix_new_order_single(
            self._order(order_type="MARKET", price=None, time_in_force="IOC"), seq_num=8
        )
        self.assertEqual(report.status, "NEW")
        tags = _tag_dict(report.fix_raw_payload)
        self.assertEqual(tags[40], "1")
        self.assertNotIn(44, tags)            # Price is meaningless on a market order
        self.assertEqual(tags[59], "3")       # IOC / fill-and-kill
        self.assertIsNone(report.normalized_price)

    def test_market_order_ignores_a_supplied_price(self):
        report = self.engine.build_fix_new_order_single(
            self._order(order_type="MARKET", price="30.005"), seq_num=9
        )
        self.assertEqual(report.status, "NEW")
        self.assertNotIn(44, _tag_dict(report.fix_raw_payload))

    def test_limit_order_without_a_price_is_rejected(self):
        report = self.engine.build_fix_new_order_single(
            self._order(price=None), seq_num=10
        )
        self.assertEqual(report.status, "REJECTED")
        self.assertIn("requires a price", report.rejection_reason)

    def test_yield_quoted_debt_order_is_rejected(self):
        report = self.engine.build_fix_new_order_single(
            self._order(security_type=NZXSecurityType.DEBT_YIELD_QUOTED), seq_num=11
        )
        self.assertEqual(report.status, "REJECTED")
        self.assertIn("yield", report.rejection_reason)

    def test_vendor_symbology_is_refused(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.build_fix_new_order_single(self._order(symbol="FPH.NZ"), seq_num=12)
        self.assertIn("vendor symbology", str(ctx.exception))

    def test_fix_field_injection_is_refused(self):
        # A ClOrdID carrying SOH would inject arbitrary tags into the message.
        with self.assertRaises(ValueError):
            self.engine.build_fix_new_order_single(
                self._order(cl_ord_id=f"A{SOH}54=2"), seq_num=13
            )
        with self.assertRaises(ValueError):
            self.engine.build_fix_new_order_single(
                self._order(cl_ord_id="A=B"), seq_num=13
            )
        with self.assertRaises(ValueError):
            self.engine.build_fix_new_order_single(self._order(symbol="FPHÉ"), seq_num=13)

    def test_missing_sequence_number_is_an_error(self):
        # MsgSeqNum (34) belongs to the session layer and must never be invented.
        with self.assertRaises(ValueError):
            self.engine.build_fix_new_order_single(self._order())
        for bad in (0, -1, "1", True):
            with self.assertRaises(ValueError):
                self.engine.build_fix_new_order_single(self._order(), seq_num=bad)

    def test_sequence_provider_is_used_when_no_explicit_seq_num(self):
        counter = iter([11, 12])
        engine = NewZealandExchangeNZXEngine(
            TEST_SESSION, clock=lambda: FIXED_CLOCK, seq_num_provider=lambda: next(counter)
        )
        first = engine.build_fix_new_order_single(self._order())
        second = engine.build_fix_new_order_single(self._order(cl_ord_id="NZX_002"))
        self.assertEqual(_tag_dict(first.fix_raw_payload)[34], "11")
        self.assertEqual(_tag_dict(second.fix_raw_payload)[34], "12")

    def test_session_gate_rejects_out_of_phase_orders(self):
        closed = self.engine.build_fix_new_order_single(
            self._order(), seq_num=14, at_time=_auckland(3, 0)
        )
        self.assertEqual(closed.status, "REJECTED")
        self.assertIn("ENQUIRY", closed.rejection_reason)
        adjust = self.engine.build_fix_new_order_single(
            self._order(), seq_num=14, at_time=_auckland(17, 10)
        )
        self.assertEqual(adjust.status, "REJECTED")
        self.assertIn("ADJUST", adjust.rejection_reason)
        open_market = self.engine.build_fix_new_order_single(
            self._order(), seq_num=14, at_time=_auckland(12, 0)
        )
        self.assertEqual(open_market.status, "NEW")


class TestSessionConfig(unittest.TestCase):

    def test_comp_ids_have_no_defaults(self):
        # Regression: the engine previously shipped a fabricated TargetCompID.
        with self.assertRaises(TypeError):
            NewZealandExchangeNZXEngine()
        with self.assertRaises(TypeError):
            NZXFixSessionConfig(sender_comp_id="A", target_comp_id="B")

    def test_blank_and_unsafe_comp_ids_are_refused(self):
        with self.assertRaises(ValueError):
            NZXFixSessionConfig(sender_comp_id="  ", target_comp_id="B", begin_string="FIX.4.4")
        with self.assertRaises(ValueError):
            NZXFixSessionConfig(
                sender_comp_id=f"A{SOH}", target_comp_id="B", begin_string="FIX.4.4"
            )

    def test_pipe_delimiter_is_available_for_logs_and_stays_self_consistent(self):
        engine = NewZealandExchangeNZXEngine(
            TEST_SESSION, clock=lambda: FIXED_CLOCK, field_delimiter="|"
        )
        payload = engine.build_fix_new_order_single(
            NZXOrderRequest("NZX_001", "FPH", "BUY", 100, "1.50", "LIMIT", "DAY"), seq_num=1
        ).fix_raw_payload
        self.assertNotIn(SOH, payload)
        tags = {int(t): v for t, v in (f.split("=", 1) for f in payload.split("|") if f)}
        end = payload.rindex("|10=") + 1
        self.assertEqual(tags[10], f"{sum(payload[:end].encode('ascii')) % 256:03d}")

    def test_unsupported_delimiter_is_refused(self):
        with self.assertRaises(ValueError):
            NewZealandExchangeNZXEngine(TEST_SESSION, field_delimiter=",")


class TestOrderCancelRequest(unittest.TestCase):

    def setUp(self):
        self.engine = NewZealandExchangeNZXEngine(TEST_SESSION, clock=lambda: FIXED_CLOCK)

    def test_cancel_request_content(self):
        report = self.engine.build_fix_order_cancel_request(
            orig_cl_ord_id="NZX_001", cl_ord_id="NZX_001_C", symbol="FPH",
            side="BUY", quantity=1000, seq_num=20,
        )
        self.assertEqual(report.fix_msg_type, "F")
        self.assertEqual(report.status, "PENDING_CANCEL")
        tags = _tag_dict(report.fix_raw_payload)
        self.assertEqual(tags[35], "F")
        self.assertEqual(tags[41], "NZX_001")
        self.assertEqual(tags[11], "NZX_001_C")
        self.assertEqual(tags[55], "FPH")
        self.assertEqual(tags[54], "1")
        self.assertEqual(tags[38], "1000")
        self.assertEqual(tags[60], "20260304-21:30:15.123")

    def test_reusing_the_original_clordid_is_rejected(self):
        report = self.engine.build_fix_order_cancel_request(
            orig_cl_ord_id="NZX_001", cl_ord_id="NZX_001", symbol="FPH",
            side="BUY", seq_num=21,
        )
        self.assertEqual(report.status, "REJECTED")
        self.assertIn("NEW ClOrdID", report.rejection_reason)

    def test_quantity_is_optional_but_validated_when_supplied(self):
        without = self.engine.build_fix_order_cancel_request(
            "NZX_001", "NZX_001_C", "FPH", "SELL", seq_num=22
        )
        self.assertNotIn(38, _tag_dict(without.fix_raw_payload))
        bad = self.engine.build_fix_order_cancel_request(
            "NZX_001", "NZX_001_C", "FPH", "SELL", quantity=0, seq_num=22
        )
        self.assertEqual(bad.status, "REJECTED")


class TestExecutionReportParsing(unittest.TestCase):

    def setUp(self):
        self.engine = NewZealandExchangeNZXEngine(TEST_SESSION, clock=lambda: FIXED_CLOCK)

    @staticmethod
    def _framed(body_fields, delimiter=SOH):
        body = "".join(f"{t}={v}{delimiter}" for t, v in body_fields)
        prefix = f"8=FIX.4.4{delimiter}9={len(body.encode('ascii'))}{delimiter}{body}"
        checksum = sum(prefix.encode("ascii")) % 256
        return f"{prefix}10={checksum:03d}{delimiter}"

    def test_parses_a_partial_fill(self):
        raw = self._framed([
            (35, "8"), (34, "5"), (49, "TESTNZX"), (56, "TESTFIRM"),
            (37, "NZXORD1"), (11, "NZX_001"), (17, "EXEC1"), (150, "F"), (39, "1"),
            (55, "FPH"), (54, "1"), (32, "400"), (31, "30.00"),
            (14, "400"), (151, "600"), (6, "30.00"), (15, "NZD"),
        ])
        report = self.engine.parse_execution_report(raw)
        self.assertEqual(report.cl_ord_id, "NZX_001")
        self.assertEqual(report.exec_id, "EXEC1")
        self.assertEqual(report.order_id, "NZXORD1")
        self.assertEqual(report.ord_status, "1")
        self.assertEqual(report.ord_status_name, "PARTIALLY_FILLED")
        self.assertEqual(report.last_qty, Decimal("400"))
        self.assertEqual(report.cum_qty, Decimal("400"))
        self.assertEqual(report.leaves_qty, Decimal("600"))
        self.assertEqual(report.avg_px, Decimal("30.00"))
        self.assertEqual(report.currency, "NZD")
        self.assertFalse(report.poss_dup)

    def test_parses_a_rejection_and_a_cancel(self):
        rejected = self.engine.parse_execution_report(self._framed([
            (35, "8"), (11, "NZX_002"), (17, "EXEC2"), (39, "8"),
            (103, "3"), (58, "Price not a valid tick"),
        ]))
        self.assertEqual(rejected.ord_status_name, "REJECTED")
        self.assertEqual(rejected.ord_rej_reason, "3")
        self.assertEqual(rejected.text, "Price not a valid tick")

        canceled = self.engine.parse_execution_report(self._framed([
            (35, "8"), (11, "NZX_003"), (17, "EXEC3"), (39, "4"), (151, "0"),
        ]))
        self.assertEqual(canceled.ord_status_name, "CANCELED")

    def test_round_trips_a_pipe_delimited_message(self):
        raw = self._framed(
            [(35, "8"), (11, "NZX_001"), (17, "EXEC1"), (39, "2")], delimiter="|"
        )
        self.assertEqual(self.engine.parse_execution_report(raw).ord_status_name, "FILLED")

    def test_possdup_resend_is_flagged(self):
        raw = self._framed([
            (35, "8"), (43, "Y"), (11, "NZX_001"), (17, "EXEC1"), (39, "1"),
            (32, "400"), (14, "400"),
        ])
        self.assertTrue(self.engine.parse_execution_report(raw).poss_dup)

    def test_a_session_reject_is_not_an_execution_report(self):
        raw = self._framed([(35, "3"), (45, "5"), (58, "Invalid tag")])
        with self.assertRaises(ValueError) as ctx:
            self.engine.parse_execution_report(raw)
        self.assertIn("35=8", str(ctx.exception))

    def test_missing_mandatory_fields_raise(self):
        for omit in (11, 17, 39):
            fields = [(35, "8"), (11, "NZX_001"), (17, "EXEC1"), (39, "1")]
            raw = self._framed([f for f in fields if f[0] != omit])
            with self.assertRaises(ValueError):
                self.engine.parse_execution_report(raw)

    def test_corrupt_checksum_is_rejected(self):
        raw = self._framed([(35, "8"), (11, "NZX_001"), (17, "EXEC1"), (39, "2")])
        tampered = raw.replace("39=2", "39=4")
        with self.assertRaises(ValueError) as ctx:
            self.engine.parse_execution_report(tampered)
        self.assertIn("CheckSum mismatch", str(ctx.exception))
        # The same message parses when checksum verification is disabled.
        self.assertEqual(
            self.engine.parse_execution_report(tampered, verify_checksum=False).ord_status,
            "4",
        )

    def test_malformed_and_empty_payloads_raise(self):
        for bad in ("", "   ", "not-fix", f"35=8{SOH}garbage{SOH}", f"abc=8{SOH}"):
            with self.assertRaises(ValueError):
                self.engine.parse_execution_report(bad)

    def test_non_ascii_text_field_does_not_crash_checksum_verification(self):
        # Regression: an exchange may put a non-ASCII byte in Text (58). Encoding
        # the payload as ASCII raised UnicodeEncodeError from inside the parser.
        body = "".join(f"{t}={v}\x01" for t, v in [
            (35, "8"), (11, "NZX_001"), (17, "EXEC1"), (39, "8"), (58, "caf\xe9"),
        ])
        pre = f"8=FIX.4.4{SOH}9={len(body.encode('latin-1'))}{SOH}{body}"
        raw = f"{pre}10={sum(pre.encode('latin-1')) % 256:03d}{SOH}"
        report = self.engine.parse_execution_report(raw)
        self.assertEqual(report.text, "caf\xe9")
        self.assertEqual(report.ord_status_name, "REJECTED")

    def test_undecodable_payload_reports_a_checksum_limitation_not_a_crash(self):
        body = "".join(f"{t}={v}\x01" for t, v in [
            (35, "8"), (11, "NZX_001"), (17, "EXEC1"), (39, "1"), (58, "中"),
        ])
        raw = f"8=FIX.4.4{SOH}9={len(body)}{SOH}{body}10=000{SOH}"
        with self.assertRaises(ValueError) as ctx:
            self.engine.parse_execution_report(raw)
        self.assertIn("byte count", str(ctx.exception))
        # It still parses when the caller opts out of checksum verification.
        self.assertEqual(
            self.engine.parse_execution_report(raw, verify_checksum=False).text, "中"
        )

    def test_unknown_ord_status_is_surfaced_not_guessed(self):
        raw = self._framed([(35, "8"), (11, "NZX_001"), (17, "EXEC1"), (39, "Z")])
        report = self.engine.parse_execution_report(raw)
        self.assertEqual(report.ord_status, "Z")
        self.assertEqual(report.ord_status_name, "UNKNOWN")

    def test_round_trip_of_a_message_this_engine_built(self):
        # A NewOrderSingle is not an ExecutionReport and must not decode as one.
        built = self.engine.build_fix_new_order_single(
            NZXOrderRequest("NZX_001", "FPH", "BUY", 100, "1.50", "LIMIT", "DAY"), seq_num=1
        )
        with self.assertRaises(ValueError):
            self.engine.parse_execution_report(built.fix_raw_payload)


if __name__ == "__main__":
    unittest.main()

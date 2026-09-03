import unittest
from datetime import datetime, timezone
from decimal import Decimal

from cme_group_fix_api_for_futures import (
    SOH,
    CmeFixOrderParams,
    CmeFixSessionEngine,
    FixSessionError,
    to_ilink3_order_fields,
)

FIXED_CLOCK = lambda: datetime(2026, 8, 21, 14, 30, 15, 123456, tzinfo=timezone.utc)


def order(**overrides) -> CmeFixOrderParams:
    base = dict(
        symbol="ESZ5",
        side="1",
        quantity=10,
        price="5050.25",
        cl_ord_id="CL_12345",
        operator_id="OP_99",
        smp_id="SMP888",
        smp_instruction="O",
        is_automated=True,
        account="ACC1",
    )
    base.update(overrides)
    return CmeFixOrderParams(**base)


def fields(fix_str: str) -> dict:
    out = {}
    for part in fix_str.split(SOH):
        if not part:
            continue
        tag, _, val = part.partition("=")
        out[int(tag)] = val
    return out


class TestFixFraming(unittest.TestCase):
    """The framing fields a FIX counterparty needs to parse the message at all."""

    def setUp(self):
        self.engine = CmeFixSessionEngine("FIRM_ALGO_01", "CME", clock=FIXED_CLOCK)

    def test_message_starts_with_begin_string_body_length_msgtype_and_ends_with_checksum(self):
        msg = self.engine.create_new_order_single(order())

        self.assertTrue(msg.startswith(f"8=FIX.4.2{SOH}9="))
        self.assertRegex(msg, rf"^8=[^{SOH}]+{SOH}9=\d+{SOH}35=D{SOH}")
        self.assertRegex(msg, rf"{SOH}10=\d{{3}}{SOH}$")

    def test_body_length_counts_bytes_between_body_length_field_and_checksum(self):
        msg = self.engine.create_new_order_single(order())
        declared = int(fields(msg)[9])

        start = msg.index(f"{SOH}35=") + 1
        end = msg.index(f"{SOH}10=") + 1
        self.assertEqual(declared, len(msg[start:end].encode("ascii")))

    def test_checksum_is_independently_derived_sum_of_preceding_bytes_mod_256(self):
        msg = self.engine.create_new_order_single(order())
        body = msg[: msg.index(f"{SOH}10=") + 1]

        expected = sum(body.encode("ascii")) % 256
        self.assertEqual(fields(msg)[10], f"{expected:03d}")

    def test_sending_time_is_utc_with_millisecond_precision(self):
        msg = self.engine.create_new_order_single(order())
        self.assertEqual(fields(msg)[52], "20260821-14:30:15.123")

    def test_naive_clock_is_rejected_rather_than_silently_treated_as_utc(self):
        engine = CmeFixSessionEngine("F", clock=lambda: datetime(2026, 8, 21, 14, 30, 15))
        with self.assertRaises(ValueError):
            engine.create_new_order_single(order())

    def test_delimiter_in_a_field_value_is_rejected_instead_of_forging_fields(self):
        with self.assertRaises(ValueError):
            self.engine.create_new_order_single(order(cl_ord_id=f"CL{SOH}55=ZNZ5"))

    def test_equals_sign_in_a_field_value_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.create_new_order_single(order(symbol="ES=Z5"))

    def test_non_ascii_field_value_is_rejected_as_value_error(self):
        with self.assertRaises(ValueError):
            self.engine.create_new_order_single(order(account="ACCÜ1"))


class TestOrderEncoding(unittest.TestCase):
    def setUp(self):
        self.engine = CmeFixSessionEngine("FIRM_ALGO_01", "CME", clock=FIXED_CLOCK)

    def test_cme_order_tags_are_serialised(self):
        f = fields(self.engine.create_new_order_single(order()))

        self.assertEqual(f[35], "D")
        self.assertEqual(f[34], "1")
        self.assertEqual(f[49], "FIRM_ALGO_01")
        self.assertEqual(f[56], "CME")
        self.assertEqual(f[50], "OP_99")
        self.assertEqual(f[1028], "N")
        self.assertEqual(f[7928], "SMP888")
        self.assertEqual(f[8000], "O")
        self.assertEqual(f[55], "ESZ5")
        self.assertEqual(f[54], "1")
        self.assertEqual(f[38], "10")
        self.assertEqual(f[1], "ACC1")

    def test_manual_order_sets_tag_1028_to_y(self):
        f = fields(self.engine.create_new_order_single(order(is_automated=False)))
        self.assertEqual(f[1028], "Y")

    def test_five_decimal_fx_price_is_not_rounded(self):
        # Regression: a fixed four-decimal format silently repriced 1.05125 to
        # 1.0513 — a full tick away on a five-decimal FX future.
        f = fields(self.engine.create_new_order_single(order(symbol="6EZ5", price="1.05125")))
        self.assertEqual(f[44], "1.05125")

    def test_decimal_price_is_serialised_without_scientific_notation(self):
        f = fields(self.engine.create_new_order_single(order(price=Decimal("0.000005"))))
        self.assertEqual(f[44], "0.000005")

    def test_float_price_does_not_leak_binary_representation_artifacts(self):
        f = fields(self.engine.create_new_order_single(order(price=0.1 + 0.2)))
        self.assertEqual(f[44], "0.30000000000000004")

    def test_non_finite_price_is_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.subTest(price=bad), self.assertRaises(ValueError):
                self.engine.create_new_order_single(order(price=bad))

    def test_invalid_smp_instruction_is_rejected(self):
        # 'R' appeared in earlier documentation of this skill. CME defines only
        # 'O' (cancel oldest/resting) and 'N' (cancel newest/aggressing).
        with self.assertRaises(ValueError):
            self.engine.create_new_order_single(order(smp_instruction="R"))

    def test_both_documented_smp_instructions_are_accepted(self):
        for value in ("O", "N"):
            engine = CmeFixSessionEngine("F", clock=FIXED_CLOCK)
            with self.subTest(smp_instruction=value):
                f = fields(engine.create_new_order_single(order(smp_instruction=value)))
                self.assertEqual(f[8000], value)

    def test_omitted_smp_instruction_omits_tag_8000(self):
        f = fields(self.engine.create_new_order_single(order(smp_instruction=None)))
        self.assertNotIn(8000, f)
        self.assertIn(7928, f)

    def test_missing_account_is_rejected_rather_than_defaulted(self):
        with self.assertRaises(ValueError):
            self.engine.create_new_order_single(order(account=None))

    def test_invalid_side_quantity_and_smp_id_are_rejected(self):
        cases = [
            dict(side="B"),
            dict(quantity=0),
            dict(quantity=-5),
            dict(quantity=1.5),
            dict(smp_id="SMP 888"),
            dict(smp_id=""),
            dict(operator_id=""),
            dict(cl_ord_id="  "),
            dict(symbol=""),
        ]
        for case in cases:
            with self.subTest(**case), self.assertRaises(ValueError):
                self.engine.create_new_order_single(order(**case))

    def test_outbound_sequence_numbers_increment_per_message(self):
        f1 = fields(self.engine.create_new_order_single(order()))
        f2 = fields(self.engine.create_new_order_single(order(cl_ord_id="CL_2")))

        self.assertEqual(f1[34], "1")
        self.assertEqual(f2[34], "2")
        self.assertEqual(self.engine.outbound_seq_num, 3)

    def test_rejected_order_does_not_consume_a_sequence_number(self):
        with self.assertRaises(ValueError):
            self.engine.create_new_order_single(order(side="X"))
        self.assertEqual(self.engine.outbound_seq_num, 1)


class TestSequenceRecovery(unittest.TestCase):
    def setUp(self):
        self.engine = CmeFixSessionEngine("FIRM_ALGO_01", "CME", clock=FIXED_CLOCK)

    def test_in_sequence_message_advances_expectation_and_emits_nothing(self):
        self.assertIsNone(self.engine.process_inbound_message({35: "8", 34: "1"}))
        self.assertEqual(self.engine.expected_inbound_seq_num, 2)

    def test_gap_emits_one_resend_request_for_the_closed_range(self):
        resend = self.engine.process_inbound_message({35: "8", 34: "5"})
        f = fields(resend)

        self.assertEqual(f[35], "2")
        self.assertEqual(f[7], "1")
        self.assertEqual(f[16], "4")
        self.assertEqual(self.engine.expected_inbound_seq_num, 1)

    def test_further_high_sequences_during_recovery_do_not_re_request(self):
        # Regression: re-requesting on every out-of-sequence message during
        # recovery produces a resend storm the peer answers with a disconnect.
        self.assertIsNotNone(self.engine.process_inbound_message({35: "8", 34: "5"}))
        for seq in ("6", "7", "8"):
            with self.subTest(seq=seq):
                self.assertIsNone(self.engine.process_inbound_message({35: "8", 34: seq}))
        self.assertEqual(self.engine.outbound_seq_num, 2)  # exactly one message emitted

    def test_recovery_clears_once_the_requested_range_is_filled(self):
        self.engine.process_inbound_message({35: "8", 34: "4"})
        for seq in ("1", "2", "3"):
            self.engine.process_inbound_message({35: "8", 34: seq, 43: "Y"})

        self.assertIsNone(self.engine.resend_requested_through)
        self.assertEqual(self.engine.expected_inbound_seq_num, 4)
        # A later gap is a new gap, so a fresh ResendRequest is due.
        self.assertIsNotNone(self.engine.process_inbound_message({35: "8", 34: "9"}))

    def test_gap_fill_advances_the_expectation_to_new_seq_no(self):
        self.engine.process_inbound_message({35: "8", 34: "10"})
        self.engine.process_inbound_message({35: "4", 34: "1", 123: "Y", 36: "8"})

        self.assertEqual(self.engine.expected_inbound_seq_num, 8)

    def test_sequence_reset_is_honoured_regardless_of_its_own_seq_num(self):
        self.engine.expected_inbound_seq_num = 50
        self.assertIsNone(self.engine.process_inbound_message({35: "4", 34: "1", 36: "100"}))
        self.assertEqual(self.engine.expected_inbound_seq_num, 100)
        self.assertFalse(self.engine.session_terminated)

    def test_backwards_sequence_reset_is_rejected(self):
        self.engine.expected_inbound_seq_num = 50
        with self.assertRaises(FixSessionError):
            self.engine.process_inbound_message({35: "4", 34: "1", 36: "10"})

    def test_sequence_reset_without_new_seq_no_is_rejected(self):
        with self.assertRaises(FixSessionError):
            self.engine.process_inbound_message({35: "4", 34: "1"})

    def test_poss_dup_below_expectation_is_discarded_quietly(self):
        self.engine.expected_inbound_seq_num = 10
        self.assertIsNone(self.engine.process_inbound_message({35: "8", 34: "4", 43: "Y"}))
        self.assertEqual(self.engine.expected_inbound_seq_num, 10)
        self.assertFalse(self.engine.session_terminated)

    def test_too_low_sequence_without_poss_dup_emits_logout_and_terminates(self):
        # Regression: this was previously logged at debug and ignored, so a
        # desynchronised peer could silently drop ExecutionReports.
        self.engine.expected_inbound_seq_num = 10
        logout = self.engine.process_inbound_message({35: "8", 34: "4"})

        self.assertEqual(fields(logout)[35], "5")
        self.assertIn("too low", fields(logout)[58])
        self.assertTrue(self.engine.session_terminated)
        with self.assertRaises(FixSessionError):
            self.engine.process_inbound_message({35: "8", 34: "10"})

    def test_missing_or_malformed_msg_seq_num_is_rejected(self):
        for msg in ({35: "8"}, {35: "8", 34: "abc"}, {35: "8", 34: "0"}, {35: "8", 34: " 5"},
                    {35: "8", 34: "+5"}, {35: "8", 34: None}):
            with self.subTest(msg=msg), self.assertRaises(ValueError):
                self.engine.process_inbound_message(msg)

    def test_only_in_sequence_messages_are_flagged_for_business_processing(self):
        # A None return covers three different situations; the caller must not
        # read it as "safe to apply". Out-of-sequence and duplicate messages
        # would replay or reorder fills.
        self.assertIsNone(self.engine.process_inbound_message({35: "8", 34: "1"}))
        self.assertTrue(self.engine.last_inbound_accepted)

        self.engine.process_inbound_message({35: "8", 34: "9"})          # gap -> ResendRequest
        self.assertFalse(self.engine.last_inbound_accepted)

        self.engine.process_inbound_message({35: "8", 34: "10"})         # arrives during recovery
        self.assertFalse(self.engine.last_inbound_accepted)

        self.engine.process_inbound_message({35: "8", 34: "1", 43: "Y"})  # duplicate
        self.assertFalse(self.engine.last_inbound_accepted)


class TestILink3Mapping(unittest.TestCase):
    def test_fields_map_onto_ilink3_tags_and_types(self):
        mapped = to_ilink3_order_fields(order(smp_id="8881234"))

        self.assertEqual(mapped[5392], "OP_99")   # SenderID replaces Tag 50
        self.assertEqual(mapped[2362], 8881234)   # uInt64 replaces Tag 7928
        self.assertEqual(mapped[1028], 0)         # boolean replaces 'N'
        self.assertEqual(mapped[8000], "O")

    def test_manual_order_maps_to_boolean_one(self):
        self.assertEqual(to_ilink3_order_fields(order(smp_id="1", is_automated=False))[1028], 1)

    def test_non_numeric_smp_id_cannot_be_represented_in_ilink3(self):
        with self.assertRaises(ValueError):
            to_ilink3_order_fields(order(smp_id="SMP888"))

    def test_operator_id_longer_than_the_sender_id_field_is_rejected(self):
        with self.assertRaises(ValueError):
            to_ilink3_order_fields(order(smp_id="1", operator_id="X" * 21))


if __name__ == "__main__":
    unittest.main()

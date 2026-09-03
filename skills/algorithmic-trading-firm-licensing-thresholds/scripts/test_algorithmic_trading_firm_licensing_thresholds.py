import dataclasses
import unittest

import algorithmic_trading_firm_licensing_thresholds as mod
from algorithmic_trading_firm_licensing_thresholds import (
    FirmTradingActivity,
    LicensingThresholdEvaluator,
)


def make_activity(**overrides):
    """A US exchange member with no customers, no off-exchange flow, no orders.

    This baseline is deliberately the one shape that crosses no modelled
    threshold in any jurisdiction, so every test below varies exactly the
    field it is about.
    """
    base = dict(
        jurisdiction="US",
        is_exchange_member=True,
        has_customers=False,
        off_exchange_volume_usd=0.0,
        peak_orders_per_second=0,
    )
    base.update(overrides)
    return FirmTradingActivity(**base)


class TestFirmTradingActivityValidation(unittest.TestCase):
    def test_normalizes_jurisdiction(self):
        self.assertEqual(make_activity(jurisdiction="  us  ").jurisdiction, "US")

    def test_rejects_unsupported_jurisdiction(self):
        with self.assertRaises(ValueError):
            make_activity(jurisdiction="UK")

    def test_rejects_empty_jurisdiction(self):
        with self.assertRaises(ValueError):
            make_activity(jurisdiction="   ")

    def test_rejects_negative_volume(self):
        with self.assertRaises(ValueError):
            make_activity(off_exchange_volume_usd=-1.0)

    def test_rejects_nan_volume(self):
        with self.assertRaises(ValueError):
            make_activity(off_exchange_volume_usd=float("nan"))

    def test_rejects_infinite_volume(self):
        with self.assertRaises(ValueError):
            make_activity(off_exchange_volume_usd=float("inf"))

    def test_rejects_boolean_volume(self):
        # Regression: bool is a subclass of int, so True once coerced to 1.0
        # and was screened as a one-dollar off-exchange notional.
        with self.assertRaises(ValueError):
            make_activity(off_exchange_volume_usd=True)

    def test_rejects_non_numeric_volume(self):
        with self.assertRaises(ValueError):
            make_activity(off_exchange_volume_usd="500000")

    def test_rejects_negative_ops(self):
        with self.assertRaises(ValueError):
            make_activity(peak_orders_per_second=-1)

    def test_rejects_non_integer_ops(self):
        with self.assertRaises(ValueError):
            make_activity(peak_orders_per_second=1.5)

    def test_rejects_boolean_ops(self):
        with self.assertRaises(ValueError):
            make_activity(peak_orders_per_second=True)

    def test_rejects_non_boolean_flags(self):
        with self.assertRaises(ValueError):
            make_activity(is_exchange_member="yes")
        with self.assertRaises(ValueError):
            make_activity(has_customers=1)
        with self.assertRaises(ValueError):
            make_activity(is_retail_api_algo_flow=1)

    def test_rejects_exempt_volume_exceeding_total(self):
        # Claiming more Rule 15b9-1(c) exempt volume than was traded
        # off-exchange would net to a negative non-exempt figure and hide a
        # condition (c) breach.
        with self.assertRaises(ValueError):
            make_activity(
                off_exchange_volume_usd=100.0, exempt_off_exchange_volume_usd=100.01
            )

    def test_rejects_negative_exempt_volume(self):
        with self.assertRaises(ValueError):
            make_activity(
                off_exchange_volume_usd=100.0, exempt_off_exchange_volume_usd=-1.0
            )

    def test_rejects_negative_message_rate(self):
        with self.assertRaises(ValueError):
            make_activity(
                jurisdiction="EU", avg_messages_per_second_per_instrument=-0.5
            )

    def test_rejects_nan_message_rate(self):
        with self.assertRaises(ValueError):
            make_activity(
                jurisdiction="EU",
                avg_messages_per_second_all_instruments=float("nan"),
            )

    def test_message_rates_default_to_none_not_zero(self):
        activity = make_activity(jurisdiction="EU")
        self.assertIsNone(activity.avg_messages_per_second_per_instrument)
        self.assertIsNone(activity.avg_messages_per_second_all_instruments)

    def test_non_exempt_volume_is_the_remainder(self):
        activity = make_activity(
            off_exchange_volume_usd=1_000.0, exempt_off_exchange_volume_usd=250.0
        )
        self.assertAlmostEqual(activity.non_exempt_off_exchange_volume_usd, 750.0)

    def test_sub_cent_netting_residue_collapses_to_zero(self):
        # 0.1 + 0.2 - 0.3 leaves ~5.6e-17 in binary floating point, which at
        # the default 0.00 screening floor was reported as a condition (c)
        # breach of "0.00 USD".
        activity = make_activity(
            off_exchange_volume_usd=0.1 + 0.2, exempt_off_exchange_volume_usd=0.3
        )
        self.assertEqual(activity.non_exempt_off_exchange_volume_usd, 0.0)
        report = LicensingThresholdEvaluator().evaluate(activity)
        self.assertFalse(report.requires_registration)

    def test_a_full_cent_of_residue_still_breaches(self):
        activity = make_activity(
            off_exchange_volume_usd=100.01, exempt_off_exchange_volume_usd=100.0
        )
        self.assertAlmostEqual(activity.non_exempt_off_exchange_volume_usd, 0.01)
        self.assertTrue(
            LicensingThresholdEvaluator().evaluate(activity).requires_registration
        )

    def test_is_frozen(self):
        with self.assertRaises(dataclasses.FrozenInstanceError):
            make_activity().jurisdiction = "EU"


class TestUnitedStates15b9_1(unittest.TestCase):
    """17 CFR 240.15b9-1 as amended, 88 FR 61893 (Sept. 7, 2023)."""

    def setUp(self):
        self.evaluator = LicensingThresholdEvaluator()

    def test_exchange_member_with_no_off_exchange_flow_is_clear(self):
        report = self.evaluator.evaluate(make_activity())
        self.assertTrue(report.is_clear)
        self.assertFalse(report.requires_registration)
        self.assertIn("No modelled threshold crossed", report.reason)

    def test_any_non_exempt_off_exchange_volume_defeats_condition_c(self):
        # The amendments removed the de minimis allowance, so the default
        # floor is 0.0 and a single dollar breaches condition (c).
        report = self.evaluator.evaluate(make_activity(off_exchange_volume_usd=1.0))
        self.assertTrue(report.requires_registration)
        self.assertIn("condition (c)", report.reason)
        self.assertIn("section 15(b)(8)", report.reason)
        self.assertEqual(report.rule_id, "US/15b9-1")

    def test_zero_off_exchange_volume_does_not_breach_condition_c(self):
        # Boundary: the comparison is strictly greater than the floor.
        report = self.evaluator.evaluate(make_activity(off_exchange_volume_usd=0.0))
        self.assertEqual(report.violations, ())

    def test_wholly_exempt_off_exchange_volume_is_not_a_violation(self):
        # Rule 15b9-1(c)(1)-(2): exchange-routed Rule 611 flow and the stock
        # leg of a stock-option order remain permitted.
        report = self.evaluator.evaluate(
            make_activity(
                off_exchange_volume_usd=750_000.0,
                exempt_off_exchange_volume_usd=750_000.0,
            )
        )
        self.assertFalse(report.requires_registration)
        # ...but the (c)(2) policies-and-procedures obligation is surfaced.
        self.assertTrue(report.manual_review_required)
        self.assertIn("17a-4", report.reason)

    def test_partially_exempt_volume_screens_only_the_remainder(self):
        report = self.evaluator.evaluate(
            make_activity(
                off_exchange_volume_usd=900_000.0,
                exempt_off_exchange_volume_usd=400_000.0,
            )
        )
        self.assertTrue(report.requires_registration)
        self.assertIn("500000.00 USD", report.reason)

    def test_non_membership_alone_defeats_the_exemption(self):
        # Regression: previously a non-member was only flagged when its
        # off-exchange volume was exactly zero, so the worst case - a
        # non-member trading off-exchange - was reported under the wrong rule.
        report = self.evaluator.evaluate(
            make_activity(is_exchange_member=False, off_exchange_volume_usd=5_000.0)
        )
        self.assertTrue(report.requires_registration)
        self.assertIn("condition (a)", report.reason)
        self.assertEqual(len(report.violations), 2)

    def test_non_member_with_no_off_exchange_flow_still_flagged(self):
        report = self.evaluator.evaluate(make_activity(is_exchange_member=False))
        self.assertTrue(report.requires_registration)
        self.assertIn("condition (a)", report.reason)

    def test_no_order_rate_test_applies_in_the_us(self):
        # Rule 15b9-1 turns on membership, customer accounts and venue of
        # execution. It contains no message-rate trigger, so a member with
        # only on-exchange flow stays clear at any order rate.
        report = self.evaluator.evaluate(make_activity(peak_orders_per_second=5_000))
        self.assertTrue(report.is_clear)

    def test_firm_screening_floor_can_raise_the_triage_threshold(self):
        evaluator = LicensingThresholdEvaluator(sec_off_exchange_floor_usd=100.0)
        self.assertFalse(
            evaluator.evaluate(
                make_activity(off_exchange_volume_usd=100.0)
            ).requires_registration
        )
        self.assertTrue(
            evaluator.evaluate(
                make_activity(off_exchange_volume_usd=100.01)
            ).requires_registration
        )


class TestEuropeanUnionMiFIDII(unittest.TestCase):
    """MiFID II Art. 4(1)(40) with Art. 19 Del. Reg. (EU) 2017/565."""

    def setUp(self):
        self.evaluator = LicensingThresholdEvaluator()

    def _eu(self, **overrides):
        overrides.setdefault("jurisdiction", "EU")
        overrides.setdefault("is_exchange_member", False)
        return make_activity(**overrides)

    def test_single_instrument_limb_fires_at_two_messages_per_second(self):
        report = self.evaluator.evaluate(
            self._eu(
                avg_messages_per_second_per_instrument=2.0,
                avg_messages_per_second_all_instruments=0.0,
            )
        )
        self.assertTrue(report.requires_registration)
        self.assertIn("Article 19(1)(a)", report.reason)
        self.assertIn("Article 2(1)(d)(iii)", report.reason)
        self.assertEqual(report.rule_id, "EU/MiFID-II-HFT")

    def test_single_instrument_limb_is_clear_just_below_two(self):
        report = self.evaluator.evaluate(
            self._eu(
                avg_messages_per_second_per_instrument=1.99,
                avg_messages_per_second_all_instruments=3.99,
            )
        )
        self.assertTrue(report.is_clear)

    def test_all_instruments_limb_fires_at_four_messages_per_second(self):
        report = self.evaluator.evaluate(
            self._eu(
                avg_messages_per_second_per_instrument=0.5,
                avg_messages_per_second_all_instruments=4.0,
            )
        )
        self.assertTrue(report.requires_registration)
        self.assertIn("Article 19(1)(b)", report.reason)

    def test_both_limbs_report_separately(self):
        report = self.evaluator.evaluate(
            self._eu(
                avg_messages_per_second_per_instrument=9.0,
                avg_messages_per_second_all_instruments=40.0,
            )
        )
        self.assertEqual(len(report.violations), 2)

    def test_low_order_rate_does_not_prove_the_limbs_unmet(self):
        # An order rate cannot bound a message rate: one order per second
        # cancel-replaced five times is roughly six messages per second, above
        # the 2 msg/s limb. A low peak must therefore not yield a clear report.
        report = self.evaluator.evaluate(self._eu(peak_orders_per_second=1))
        self.assertFalse(report.is_clear)
        self.assertTrue(report.manual_review_required)

    def test_high_peak_without_averages_is_indeterminate_not_compliant(self):
        # Regression: the previous default flagged the EU only above 50 peak
        # orders/second, so a firm well inside the Article 19 definition was
        # reported compliant. It must now come back as undetermined.
        report = self.evaluator.evaluate(self._eu(peak_orders_per_second=40))
        self.assertFalse(report.requires_registration)
        self.assertTrue(report.manual_review_required)
        self.assertFalse(report.is_clear)
        self.assertIn("average message rates were not supplied", report.reason)

    def test_order_rate_is_never_an_eu_input(self):
        # Same averages, wildly different order rates, identical outcome.
        low = self._eu(
            peak_orders_per_second=0,
            avg_messages_per_second_per_instrument=1.0,
            avg_messages_per_second_all_instruments=1.0,
        )
        high = self._eu(
            peak_orders_per_second=100_000,
            avg_messages_per_second_per_instrument=1.0,
            avg_messages_per_second_all_instruments=1.0,
        )
        self.assertTrue(self.evaluator.evaluate(low).is_clear)
        self.assertTrue(self.evaluator.evaluate(high).is_clear)

    def test_partial_averages_still_require_the_missing_one(self):
        report = self.evaluator.evaluate(
            self._eu(
                peak_orders_per_second=30,
                avg_messages_per_second_per_instrument=0.1,
            )
        )
        self.assertTrue(report.manual_review_required)

    def test_stricter_override_lowers_both_limbs(self):
        strict = LicensingThresholdEvaluator(
            mifid_ii_msgs_per_sec_single_instrument=0.5,
            mifid_ii_msgs_per_sec_all_instruments=1.0,
        )
        activity = self._eu(
            avg_messages_per_second_per_instrument=0.6,
            avg_messages_per_second_all_instruments=0.9,
        )
        self.assertTrue(strict.evaluate(activity).requires_registration)
        self.assertTrue(self.evaluator.evaluate(activity).is_clear)


class TestIndiaSebiTops(unittest.TestCase):
    """SEBI circular of 2025-02-04 with NSE/INVG/67858 implementation standards."""

    def setUp(self):
        self.evaluator = LicensingThresholdEvaluator()

    def _retail(self, ops):
        return make_activity(
            jurisdiction="IN",
            is_exchange_member=False,
            is_retail_api_algo_flow=True,
            peak_orders_per_second=ops,
        )

    def test_above_tops_requires_algo_registration(self):
        report = self.evaluator.evaluate(self._retail(11))
        self.assertTrue(report.requires_registration)
        self.assertIn("registered with each exchange", report.reason)
        self.assertEqual(report.rule_id, "IN/SEBI-TOPS")

    def test_exactly_at_tops_is_within_the_threshold(self):
        # NSE sets TOPS at "not exceeding 10 orders per second per exchange",
        # so registration is required above 10, not at 10. The previous
        # ``>=`` comparison flagged a compliant retail algo.
        report = self.evaluator.evaluate(self._retail(10))
        self.assertFalse(report.requires_registration)
        self.assertTrue(report.is_clear)

    def test_below_tops_is_clear(self):
        self.assertTrue(self.evaluator.evaluate(self._retail(3)).is_clear)

    def test_member_flow_is_out_of_scope_not_compliant(self):
        # A proprietary or member firm is not governed by TOPS at all, and the
        # exchange algo-approval regime that does govern it is not modelled.
        # It must not come back clear.
        report = self.evaluator.evaluate(
            make_activity(jurisdiction="IN", peak_orders_per_second=3)
        )
        self.assertFalse(report.requires_registration)
        self.assertTrue(report.manual_review_required)
        self.assertIn("does not model", report.reason)

    def test_stricter_tops_override_is_honoured(self):
        evaluator = LicensingThresholdEvaluator(sebi_tops_orders_per_second=2)
        self.assertTrue(evaluator.evaluate(self._retail(3)).requires_registration)

    def test_zero_tops_override_is_not_swallowed(self):
        # Regression: ``limit or DEFAULT`` discarded a valid 0 override and
        # silently screened at the class default of 10 instead.
        evaluator = LicensingThresholdEvaluator(sebi_tops_orders_per_second=0)
        self.assertEqual(evaluator._sebi_tops, 0)
        self.assertTrue(evaluator.evaluate(self._retail(1)).requires_registration)

    def test_zero_mifid_override_is_not_swallowed(self):
        evaluator = LicensingThresholdEvaluator(
            mifid_ii_msgs_per_sec_single_instrument=0.0,
            mifid_ii_msgs_per_sec_all_instruments=0.0,
        )
        self.assertEqual(evaluator._mifid_single, 0.0)
        self.assertEqual(evaluator._mifid_all, 0.0)


class TestGlobalAndReportContract(unittest.TestCase):
    def setUp(self):
        self.evaluator = LicensingThresholdEvaluator()

    def test_customer_accounts_dominate_the_rule_id(self):
        report = self.evaluator.evaluate(
            make_activity(has_customers=True, peak_orders_per_second=1)
        )
        self.assertTrue(report.requires_registration)
        self.assertEqual(report.rule_id, "GLOBAL/CUSTOMER-ACCOUNTS")
        self.assertIn("15b9-1(b)", report.reason)

    def test_customer_accounts_do_not_suppress_jurisdiction_violations(self):
        report = self.evaluator.evaluate(
            make_activity(has_customers=True, off_exchange_volume_usd=1_000.0)
        )
        self.assertEqual(report.rule_id, "GLOBAL/CUSTOMER-ACCOUNTS")
        self.assertEqual(len(report.violations), 2)

    def test_violations_preserve_evaluation_order_not_alphabetical_order(self):
        # The customer-account check runs first and must stay first even when
        # its text sorts after the jurisdiction violation that follows it -
        # here the EU Article 19 text, which begins "Average ...".
        report = self.evaluator.evaluate(
            make_activity(
                jurisdiction="EU",
                is_exchange_member=False,
                has_customers=True,
                avg_messages_per_second_per_instrument=8.0,
                avg_messages_per_second_all_instruments=0.0,
            )
        )
        self.assertEqual(len(report.violations), 2)
        self.assertTrue(report.violations[0].startswith("Firm carries customer"))
        self.assertIn("Article 19(1)(a)", report.violations[1])
        self.assertNotEqual(list(report.violations), sorted(report.violations))

    def test_duplicate_violations_are_deduped(self):
        deduped = LicensingThresholdEvaluator._dedupe(["a", "b", "a", "", "b", "c"])
        self.assertEqual(deduped, ("a", "b", "c"))

    def test_unknown_jurisdiction_is_fail_closed_as_manual_review(self):
        # The dataclass rejects unknown jurisdictions, so build one by other
        # means to exercise the defensive dispatch arm.
        raw = FirmTradingActivity.__new__(FirmTradingActivity)
        for field, value in (
            ("jurisdiction", "ZZ"),
            ("is_exchange_member", True),
            ("has_customers", False),
            ("off_exchange_volume_usd", 0.0),
            ("exempt_off_exchange_volume_usd", 0.0),
            ("peak_orders_per_second", 1),
            ("avg_messages_per_second_per_instrument", None),
            ("avg_messages_per_second_all_instruments", None),
            ("is_retail_api_algo_flow", False),
        ):
            object.__setattr__(raw, field, value)
        with self.assertLogs(mod.logger, level="WARNING"):
            report = self.evaluator.evaluate(raw)
        self.assertFalse(report.is_clear)
        self.assertTrue(report.manual_review_required)
        self.assertIsNone(report.rule_id)
        self.assertIn("manual legal review", report.reason)

    def test_report_has_utc_timestamp_and_schema(self):
        report = self.evaluator.evaluate(make_activity(off_exchange_volume_usd=5.0))
        self.assertEqual(report.schema_version, mod._REPORT_SCHEMA_VERSION)
        self.assertRegex(report.evaluated_at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_report_is_frozen(self):
        report = self.evaluator.evaluate(make_activity())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            report.requires_registration = False

    def test_is_clear_requires_both_flags_false(self):
        # A report carrying only review items is not a clean bill of health.
        report = self.evaluator.evaluate(
            make_activity(jurisdiction="IN", peak_orders_per_second=1)
        )
        self.assertFalse(report.requires_registration)
        self.assertFalse(report.is_clear)


class TestEvaluatorConfigurationValidation(unittest.TestCase):
    def test_rejects_negative_thresholds(self):
        with self.assertRaises(ValueError):
            LicensingThresholdEvaluator(sebi_tops_orders_per_second=-1)
        with self.assertRaises(ValueError):
            LicensingThresholdEvaluator(mifid_ii_msgs_per_sec_single_instrument=-1.0)
        with self.assertRaises(ValueError):
            LicensingThresholdEvaluator(sec_off_exchange_floor_usd=-0.01)

    def test_rejects_non_finite_thresholds(self):
        with self.assertRaises(ValueError):
            LicensingThresholdEvaluator(sec_off_exchange_floor_usd=float("nan"))
        with self.assertRaises(ValueError):
            LicensingThresholdEvaluator(
                mifid_ii_msgs_per_sec_all_instruments=float("inf")
            )

    def test_rejects_non_integer_tops(self):
        with self.assertRaises(ValueError):
            LicensingThresholdEvaluator(sebi_tops_orders_per_second=10.5)
        with self.assertRaises(ValueError):
            LicensingThresholdEvaluator(sebi_tops_orders_per_second=True)

    def test_defaults_match_the_published_figures(self):
        evaluator = LicensingThresholdEvaluator()
        self.assertEqual(evaluator._sebi_tops, 10)
        self.assertEqual(evaluator._mifid_single, 2.0)
        self.assertEqual(evaluator._mifid_all, 4.0)
        self.assertEqual(evaluator._sec_off_ex_floor, 0.0)


if __name__ == "__main__":
    unittest.main()

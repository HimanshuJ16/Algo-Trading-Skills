import math
import unittest

import algorithmic_trading_firm_licensing_thresholds as mod
from algorithmic_trading_firm_licensing_thresholds import (
    FirmTradingActivity,
    LicensingThresholdEvaluator,
)


def make_activity(**overrides):
    base = dict(
        jurisdiction="US",
        is_exchange_member=True,
        has_customers=False,
        off_exchange_volume_usd=0.0,
        peak_orders_per_second=0,
    )
    base.update(overrides)
    return FirmTradingActivity(**base)


class TestFirmLicensingThresholds(unittest.TestCase):
    def setUp(self):
        self.evaluator = LicensingThresholdEvaluator()

    # ------------------------------- original behavioural coverage

    def test_us_15b9_1_exemption_loss(self):
        # US prop trader routes primarily to dark pools; 2023 SEC amendments
        # remove the de minimis off-exchange carve-out and require FINRA.
        activity = make_activity(off_exchange_volume_usd=500_000.0)
        report = self.evaluator.evaluate(activity)
        self.assertTrue(report.requires_registration)
        self.assertIn("FINRA broker-dealer registration required", report.reason)
        self.assertEqual(report.jurisdiction, "US")
        self.assertEqual(report.rule_id, "US/15b9-1+HFT")

    def test_eu_mifid2_hft_designation(self):
        activity = make_activity(
            jurisdiction="EU", is_exchange_member=False, peak_orders_per_second=100
        )
        report = self.evaluator.evaluate(activity)
        self.assertTrue(report.requires_registration)
        self.assertIn("MiFID II HFT designation threshold", report.reason)

    def test_in_sebi_retail_algo_limit(self):
        activity = make_activity(
            jurisdiction="IN", is_exchange_member=False, peak_orders_per_second=12
        )
        report = self.evaluator.evaluate(activity)
        self.assertTrue(report.requires_registration)
        self.assertIn("Algo Registration required", report.reason)

    def test_compliant_us_prop_firm(self):
        # Pure on-exchange member with no off-exchange flow and modest OPS.
        activity = make_activity(peak_orders_per_second=5)
        report = self.evaluator.evaluate(activity)
        self.assertFalse(report.requires_registration)
        self.assertIn("Activity within configured thresholds", report.reason)

    # ------------------------------- dataclass validation

    def test_firm_activity_normalizes_jurisdiction(self):
        activity = make_activity(jurisdiction="  us  ")
        self.assertEqual(activity.jurisdiction, "US")

    def test_firm_activity_rejects_unsupported_jurisdiction(self):
        with self.assertRaises(ValueError):
            make_activity(jurisdiction="UK")

    def test_firm_activity_rejects_empty_jurisdiction(self):
        with self.assertRaises(ValueError):
            make_activity(jurisdiction="   ")

    def test_firm_activity_rejects_negative_volume(self):
        with self.assertRaises(ValueError):
            make_activity(off_exchange_volume_usd=-1.0)

    def test_firm_activity_rejects_nan_volume(self):
        with self.assertRaises(ValueError):
            make_activity(off_exchange_volume_usd=float("nan"))

    def test_firm_activity_rejects_infinite_volume(self):
        with self.assertRaises(ValueError):
            make_activity(off_exchange_volume_usd=float("inf"))

    def test_firm_activity_rejects_negative_ops(self):
        with self.assertRaises(ValueError):
            make_activity(peak_orders_per_second=-1)

    def test_firm_activity_rejects_non_integer_ops(self):
        with self.assertRaises(ValueError):
            make_activity(peak_orders_per_second=1.5)

    def test_firm_activity_rejects_boolean_ops(self):
        # bool is a subclass of int but must not be silently accepted
        # as a meaningful OPS count.
        with self.assertRaises(ValueError):
            make_activity(peak_orders_per_second=True)

    def test_firm_activity_rejects_boolean_flags(self):
        with self.assertRaises(ValueError):
            make_activity(is_exchange_member="yes")
        with self.assertRaises(ValueError):
            make_activity(has_customers=1)

    def test_firm_activity_is_frozen(self):
        activity = make_activity()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            activity.jurisdiction = "EU"

    # ------------------------------- evaluator branches

    def test_global_customer_funds_branch(self):
        activity = make_activity(
            is_exchange_member=True,
            has_customers=True,
            off_exchange_volume_usd=0.0,
            peak_orders_per_second=1,
        )
        report = self.evaluator.evaluate(activity)
        self.assertTrue(report.requires_registration)
        self.assertEqual(report.rule_id, "GLOBAL/CUSTOMER-FUNDS")
        self.assertIn("broker-dealer or investment-firm license", report.reason)

    def test_global_customer_funds_overrides_other_violations(self):
        # Customer-funds check should be evaluated first and dominate the
        # rule_id, but jurisdiction violations remain recorded.
        activity = make_activity(has_customers=True, peak_orders_per_second=200)
        report = self.evaluator.evaluate(activity)
        self.assertEqual(report.rule_id, "GLOBAL/CUSTOMER-FUNDS")
        self.assertGreaterEqual(len(report.violations), 2)

    def test_unknown_jurisdiction_is_fail_closed(self):
        # The dataclass will reject unknown jurisdiction at construction,
        # but the evaluator's _dispatch path is also defensively fail-closed.
        evaluator = LicensingThresholdEvaluator()
        # Bypass dataclass check to exercise dispatch path directly.
        raw = FirmTradingActivity.__new__(FirmTradingActivity)
        object.__setattr__(raw, "jurisdiction", "ZZ")
        object.__setattr__(raw, "is_exchange_member", True)
        object.__setattr__(raw, "has_customers", False)
        object.__setattr__(raw, "off_exchange_volume_usd", 0.0)
        object.__setattr__(raw, "peak_orders_per_second", 1)
        report = evaluator.evaluate(raw)
        self.assertTrue(report.requires_registration)
        self.assertEqual(report.rule_id, None)
        self.assertIn("manual legal review", report.reason)

    def test_eu_under_threshold(self):
        activity = make_activity(
            jurisdiction="EU", is_exchange_member=False, peak_orders_per_second=10
        )
        report = self.evaluator.evaluate(activity)
        self.assertFalse(report.requires_registration)

    def test_in_exactly_at_threshold_still_compliant(self):
        # Boundary: ``>=`` is used for HFT-style signals; retail rule is also
        # ``>=``. SEBI retail default = 10, so 10 should be flagged.
        activity = make_activity(
            jurisdiction="IN", is_exchange_member=False, peak_orders_per_second=10
        )
        report = self.evaluator.evaluate(activity)
        self.assertTrue(report.requires_registration)

    def test_in_under_threshold(self):
        activity = make_activity(
            jurisdiction="IN", is_exchange_member=False, peak_orders_per_second=9
        )
        report = self.evaluator.evaluate(activity)
        self.assertFalse(report.requires_registration)

    def test_us_non_member_with_no_off_exchange(self):
        # A US firm that isn't an exchange member is itself a registration
        # signal under traditional SEC interpretation.
        activity = make_activity(is_exchange_member=False)
        report = self.evaluator.evaluate(activity)
        self.assertTrue(report.requires_registration)
        self.assertIn("broker-dealer registration is required", report.reason)

    def test_us_high_ops_adds_secondary_violation(self):
        # US firm with on-exchange flow but HFT-level OPS should add a
        # secondary HFT-style signal without displacing the primary rule.
        activity = make_activity(peak_orders_per_second=500)
        report = self.evaluator.evaluate(activity)
        self.assertTrue(report.requires_registration)
        self.assertEqual(report.rule_id, "US/15b9-1+HFT")
        self.assertGreaterEqual(len(report.violations), 1)

    def test_us_off_exchange_floor_below_threshold_compliant(self):
        # Off-exchange volume strictly below the configured floor should not
        # trigger 15b9-1 evaluation.
        evaluator = LicensingThresholdEvaluator(sec_off_exchange_floor_usd=100.0)
        activity = make_activity(off_exchange_volume_usd=10.0)
        report = evaluator.evaluate(activity)
        self.assertFalse(report.requires_registration)

    def test_multiple_violations_are_collected(self):
        # US firm with HFT-grade OPS plus off-exchange flow and customers.
        activity = make_activity(
            is_exchange_member=False,
            has_customers=False,
            off_exchange_volume_usd=1_000_000.0,
            peak_orders_per_second=500,
        )
        report = self.evaluator.evaluate(activity)
        self.assertGreaterEqual(len(report.violations), 2)

    # ------------------------------- report metadata

    def test_report_has_utc_timestamp_and_schema(self):
        report = self.evaluator.evaluate(make_activity(off_exchange_volume_usd=5))
        self.assertTrue(report.evaluated_at.endswith("Z"))
        self.assertEqual(report.schema_version, mod._REPORT_SCHEMA_VERSION)
        # Stable timestamp format makes reports diffable across runs.
        self.assertRegex(report.evaluated_at, r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

    def test_report_is_frozen(self):
        report = self.evaluator.evaluate(make_activity(off_exchange_volume_usd=5))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            report.requires_registration = False

    def test_violations_are_deduped_and_ordered(self):
        activity = make_activity(off_exchange_volume_usd=50_000.0)
        report = self.evaluator.evaluate(activity)
        self.assertEqual(list(report.violations), sorted(set(report.violations)))

    # ------------------------------- config / injection

    def test_constructor_rejects_negative_thresholds(self):
        with self.assertRaises(ValueError):
            LicensingThresholdEvaluator(sebi_retail_ops_limit=-1)
        with self.assertRaises(ValueError):
            LicensingThresholdEvaluator(mifid_ii_hft_ops_limit=-1)

    def test_constructor_rejects_nan_floor(self):
        with self.assertRaises(ValueError):
            LicensingThresholdEvaluator(sec_off_exchange_floor_usd=float("nan"))

    def test_overrides_change_decision(self):
        strict = LicensingThresholdEvaluator(mifid_ii_hft_ops_limit=5)
        permissive = LicensingThresholdEvaluator(mifid_ii_hft_ops_limit=5000)
        activity = make_activity(jurisdiction="EU", peak_orders_per_second=20)
        self.assertTrue(strict.evaluate(activity).requires_registration)
        self.assertFalse(permissive.evaluate(activity).requires_registration)


# A tiny dataclass import for the frozen-instance tests above.
import dataclasses  # noqa: E402


if __name__ == "__main__":
    unittest.main()

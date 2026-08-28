"""Unit tests for the CFTC large-trader reporting / Part 150 limit engine.

Expected values here are derived from the regulation, not from the
implementation:

  * 17 CFR 15.00(p)(1) - reportable position, "equals or exceeds", tested on
    "any one future" and separately on options exercising into that future,
    long or short.
  * 17 CFR 150.2 - "net long or net short, in excess of" the specified level.

Several tests are explicit regressions against the previous implementation,
which summed long+short for the reporting test and compared a single scalar
limit to the all-months net. Each is marked REGRESSION and fails against that
behaviour.
"""

import unittest

from position_limit_reporting_cftc_large_trader import (
    CFTCLargeTraderReport,
    CFTCLimitSpec,
    Config,
    ContractMonthPosition,
    FUTURE,
    LIMIT_ALL_MONTHS_COMBINED,
    LIMIT_SINGLE_MONTH,
    LIMIT_SPOT_MONTH,
    OPTION,
    PositionLimitReportingCFTCLargeTraderEngine,
    SIDE_BOTH,
    SIDE_LONG,
    SIDE_NONE,
    SIDE_SHORT,
    TraderAccountPosition,
)

# NYMEX Light Sweet Crude Oil: reporting level 350 contracts per 17 CFR
# 15.03(b). Non-legacy contract, so there is a federal spot-month limit but no
# federal single-month or all-months-combined limit.
CL_REPORTING_LEVEL = 350.0

# A legacy agricultural contract shape: all three federal limits exist.
LEGACY_SPEC = CFTCLimitSpec(
    commodity_code="ZC",
    reporting_threshold_contracts=600.0,
    spot_month_limit=600.0,
    single_month_limit=35_000.0,
    all_months_combined_limit=35_000.0,
)


def cl_spec(**overrides):
    """Crude-oil-shaped spec: reporting level only, no non-spot federal limit."""
    kwargs = dict(
        commodity_code="CL",
        reporting_threshold_contracts=CL_REPORTING_LEVEL,
    )
    kwargs.update(overrides)
    return CFTCLimitSpec(**kwargs)


def pos(account_id, month="2026-12", long_position=0.0, short_position=0.0,
        entity="ACME_FUND", commodity="CL", instrument=FUTURE, hedge=False):
    return TraderAccountPosition(
        account_id=account_id,
        entity_name=entity,
        commodity_code=commodity,
        contract_month=month,
        long_position=long_position,
        short_position=short_position,
        instrument_class=instrument,
        is_bona_fide_hedge=hedge,
    )


class TestReportingLevelArithmetic(unittest.TestCase):
    """17 CFR 15.00(p)(1): per bucket, per side, 'equals or exceeds'."""

    def setUp(self):
        self.engine = PositionLimitReportingCFTCLargeTraderEngine(Config(enabled=True))

    def test_aggregates_across_sister_accounts(self):
        # Two sub-accounts of one entity, 200 long each in the same future.
        # 400 >= 350 on the long side -> reportable. Neither account alone is.
        report = self.engine.evaluate_entity_cftc_compliance(
            "ACME_FUND",
            [pos("ACC_1", long_position=200.0), pos("ACC_2", long_position=200.0)],
            cl_spec(),
        )
        self.assertTrue(report.is_reportable)
        self.assertEqual(report.status, "FORM_102A_REPORTABLE")
        self.assertEqual(report.reportable_buckets, ("2026-12/FUTURE",))
        self.assertEqual(report.aggregated_gross_long, 400.0)
        self.assertEqual(report.aggregated_net_position, 400.0)
        self.assertEqual(report.month_detail[0].reportable_side, SIDE_LONG)

    def test_offsetting_sides_are_not_summed(self):
        # REGRESSION. 200 long + 200 short: neither side reaches 350, so no
        # bucket is reportable. The previous implementation computed
        # max(|net|, long+short) = 400 and flagged this.
        report = self.engine.evaluate_entity_cftc_compliance(
            "ACME_FUND",
            [pos("ACC_1", long_position=200.0), pos("ACC_2", short_position=200.0)],
            cl_spec(),
        )
        self.assertFalse(report.is_reportable)
        self.assertEqual(report.status, "BELOW_REPORTING_LEVEL")
        self.assertEqual(report.aggregated_net_position, 0.0)
        self.assertEqual(report.month_detail[0].reportable_side, SIDE_NONE)

    def test_gross_long_reportable_despite_small_net(self):
        # 400 long vs 300 short nets to 100, well under 350, but the long side
        # of 400 is reportable under 15.00(p)(1). The previous API accepted a
        # caller-supplied net with long/short defaulting to 0, so an entity
        # reporting only "net 100" was scored False - a missed special-account
        # identification. That input shape is no longer expressible: net is
        # derived from the legs, so the two can never disagree.
        report = self.engine.evaluate_entity_cftc_compliance(
            "ACME_FUND",
            [pos("ACC_1", long_position=400.0), pos("ACC_2", short_position=300.0)],
            cl_spec(),
        )
        self.assertTrue(report.is_reportable)
        self.assertEqual(report.aggregated_net_position, 100.0)
        self.assertEqual(report.month_detail[0].reportable_side, SIDE_LONG)

    def test_both_sides_reportable(self):
        report = self.engine.evaluate_entity_cftc_compliance(
            "ACME_FUND",
            [pos("ACC_1", long_position=400.0, short_position=380.0)],
            cl_spec(),
        )
        self.assertEqual(report.month_detail[0].reportable_side, SIDE_BOTH)

    def test_short_side_alone_is_reportable(self):
        report = self.engine.evaluate_entity_cftc_compliance(
            "ACME_FUND",
            [pos("ACC_1", short_position=350.0)],
            cl_spec(),
        )
        self.assertTrue(report.is_reportable)
        self.assertEqual(report.month_detail[0].reportable_side, SIDE_SHORT)
        self.assertEqual(report.aggregated_net_position, -350.0)

    def test_months_are_not_pooled_for_reporting(self):
        # REGRESSION. 200 in Dec and 200 in Jan is 400 across the commodity but
        # 15.00(p)(1)(i) tests "any one future". Neither month reaches 350.
        report = self.engine.evaluate_entity_cftc_compliance(
            "ACME_FUND",
            [
                pos("ACC_1", month="2026-12", long_position=200.0),
                pos("ACC_1", month="2027-01", long_position=200.0),
            ],
            cl_spec(),
        )
        self.assertFalse(report.is_reportable)
        self.assertEqual(len(report.month_detail), 2)
        self.assertEqual(report.aggregated_gross_long, 400.0)

    def test_options_bucket_separately_from_futures(self):
        # 15.00(p)(1)(ii) treats options exercising into the same future as
        # their own bucket. 200 futures + 200 options is not 400 in one bucket.
        report = self.engine.evaluate_entity_cftc_compliance(
            "ACME_FUND",
            [
                pos("ACC_1", long_position=200.0, instrument=FUTURE),
                pos("ACC_1", long_position=200.0, instrument=OPTION),
            ],
            cl_spec(),
        )
        self.assertFalse(report.is_reportable)
        self.assertEqual(
            sorted(m.instrument_class for m in report.month_detail),
            [FUTURE, OPTION],
        )

    def test_exactly_at_reporting_level_is_reportable(self):
        # "equals or exceeds" -> inclusive.
        report = self.engine.evaluate_entity_cftc_compliance(
            "ACME_FUND", [pos("ACC_1", long_position=350.0)], cl_spec()
        )
        self.assertTrue(report.is_reportable)

    def test_one_contract_below_level_is_not_reportable(self):
        report = self.engine.evaluate_entity_cftc_compliance(
            "ACME_FUND", [pos("ACC_1", long_position=349.0)], cl_spec()
        )
        self.assertFalse(report.is_reportable)

    def test_hedge_positions_remain_reportable(self):
        # Sec. 15.00(p) has no hedging carve-out; Sec. 150.3 exempts hedges from
        # limits only.
        report = self.engine.evaluate_entity_cftc_compliance(
            "ACME_FUND",
            [pos("ACC_1", long_position=400.0, hedge=True)],
            cl_spec(spot_month_limit=100.0),
            spot_month="2026-12",
        )
        self.assertTrue(report.is_reportable)
        self.assertFalse(report.is_limit_breached)
        self.assertEqual(report.hedge_exempt_contracts_excluded, 400.0)


class TestPart150LimitArithmetic(unittest.TestCase):
    """17 CFR 150.2: net, 'in excess of', spot / single / all-months."""

    def setUp(self):
        self.engine = PositionLimitReportingCFTCLargeTraderEngine()

    def test_spot_month_breach_uses_only_the_spot_month(self):
        # 6,500 net in the spot month against a 6,000 spot limit is a breach.
        # The 20,000 in the deferred month is irrelevant to the spot test and,
        # for a non-legacy contract, has no federal limit at all.
        report = self.engine.evaluate_entity_cftc_compliance(
            "APEX_CAPITAL",
            [
                pos("A1", entity="APEX_CAPITAL", month="2026-12", long_position=6_500.0),
                pos("A1", entity="APEX_CAPITAL", month="2027-06", long_position=20_000.0),
            ],
            cl_spec(spot_month_limit=6_000.0),
            spot_month="2026-12",
        )
        self.assertTrue(report.is_limit_breached)
        self.assertEqual(report.status, "SPECULATIVE_LIMIT_BREACHED")
        self.assertEqual(len(report.breaches), 1)
        breach = report.breaches[0]
        self.assertEqual(breach.limit_type, LIMIT_SPOT_MONTH)
        self.assertEqual(breach.contract_month, "2026-12")
        self.assertEqual(breach.net_position, 6_500.0)
        self.assertEqual(breach.excess, 500.0)

    def test_no_federal_non_spot_limit_is_not_tested(self):
        # REGRESSION. A single scalar limit compared to the all-months net
        # would flag this 20,000-contract deferred position. For a non-legacy
        # contract there is no federal all-months limit, so nothing is tested -
        # and the report says so rather than implying compliance.
        report = self.engine.evaluate_entity_cftc_compliance(
            "APEX_CAPITAL",
            [pos("A1", entity="APEX_CAPITAL", month="2027-06", long_position=20_000.0)],
            cl_spec(spot_month_limit=6_000.0),
            spot_month="2026-12",
        )
        self.assertFalse(report.is_limit_breached)
        self.assertEqual(
            report.limits_not_tested,
            (LIMIT_SINGLE_MONTH, LIMIT_ALL_MONTHS_COMBINED),
        )
        self.assertIn("Limits NOT tested", report.audit_notes)

    def test_exactly_at_limit_is_not_a_breach(self):
        # "in excess of" -> strict.
        report = self.engine.evaluate_entity_cftc_compliance(
            "APEX_CAPITAL",
            [pos("A1", entity="APEX_CAPITAL", long_position=6_000.0)],
            cl_spec(spot_month_limit=6_000.0),
            spot_month="2026-12",
        )
        self.assertFalse(report.is_limit_breached)

    def test_one_contract_over_limit_is_a_breach(self):
        report = self.engine.evaluate_entity_cftc_compliance(
            "APEX_CAPITAL",
            [pos("A1", entity="APEX_CAPITAL", long_position=6_001.0)],
            cl_spec(spot_month_limit=6_000.0),
            spot_month="2026-12",
        )
        self.assertTrue(report.is_limit_breached)
        self.assertEqual(report.breaches[0].excess, 1.0)

    def test_net_short_breaches_symmetrically(self):
        report = self.engine.evaluate_entity_cftc_compliance(
            "APEX_CAPITAL",
            [pos("A1", entity="APEX_CAPITAL", short_position=6_500.0)],
            cl_spec(spot_month_limit=6_000.0),
            spot_month="2026-12",
        )
        self.assertTrue(report.is_limit_breached)
        self.assertEqual(report.breaches[0].net_position, -6_500.0)
        self.assertEqual(report.breaches[0].excess, 500.0)

    def test_legacy_contract_reports_all_three_limit_types(self):
        # 40,000 net long spread over two months of a legacy contract: the
        # all-months limit of 35,000 is breached, neither single month is, and
        # the spot month is flat.
        positions = [
            pos("A1", entity="G", commodity="ZC", month="2026-12", long_position=20_000.0),
            pos("A1", entity="G", commodity="ZC", month="2027-03", long_position=20_000.0),
        ]
        report = self.engine.evaluate_entity_cftc_compliance(
            "G", positions, LEGACY_SPEC, spot_month="2026-09"
        )
        self.assertTrue(report.is_limit_breached)
        self.assertEqual([b.limit_type for b in report.breaches],
                         [LIMIT_ALL_MONTHS_COMBINED])
        self.assertEqual(report.breaches[0].net_position, 40_000.0)
        self.assertEqual(report.breaches[0].excess, 5_000.0)
        self.assertEqual(report.limits_not_tested, ())

    def test_single_month_and_all_months_can_both_breach(self):
        positions = [
            pos("A1", entity="G", commodity="ZC", month="2027-03", long_position=40_000.0),
        ]
        report = self.engine.evaluate_entity_cftc_compliance(
            "G", positions, LEGACY_SPEC, spot_month="2026-09"
        )
        self.assertEqual(
            sorted(b.limit_type for b in report.breaches),
            sorted([LIMIT_SINGLE_MONTH, LIMIT_ALL_MONTHS_COMBINED]),
        )

    def test_breach_status_does_not_hide_reportability(self):
        report = self.engine.evaluate_entity_cftc_compliance(
            "APEX_CAPITAL",
            [pos("A1", entity="APEX_CAPITAL", long_position=12_000.0)],
            cl_spec(spot_month_limit=6_000.0),
            spot_month="2026-12",
        )
        self.assertEqual(report.status, "SPECULATIVE_LIMIT_BREACHED")
        self.assertTrue(report.is_reportable)
        self.assertTrue(report.is_limit_breached)

    def test_zero_limit_is_tested_not_ignored(self):
        # 0.0 means "no position permitted" and is distinct from None.
        report = self.engine.evaluate_entity_cftc_compliance(
            "APEX_CAPITAL",
            [pos("A1", entity="APEX_CAPITAL", long_position=1.0)],
            cl_spec(spot_month_limit=0.0),
            spot_month="2026-12",
        )
        self.assertTrue(report.is_limit_breached)
        self.assertNotIn(LIMIT_SPOT_MONTH, report.limits_not_tested)

    def test_configured_spot_limit_without_spot_month_raises(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_entity_cftc_compliance(
                "APEX_CAPITAL",
                [pos("A1", entity="APEX_CAPITAL", long_position=1.0)],
                cl_spec(spot_month_limit=6_000.0),
            )
        self.assertIn("silently skipped", str(ctx.exception))


class TestAggregationIntegrity(unittest.TestCase):
    """Inputs that would corrupt an aggregation must raise, not be absorbed."""

    def setUp(self):
        self.engine = PositionLimitReportingCFTCLargeTraderEngine()

    def test_foreign_entity_position_raises(self):
        # REGRESSION. The previous implementation summed whatever list it was
        # given, so another entity's position silently entered the audit.
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_entity_cftc_compliance(
                "ACME_FUND",
                [pos("ACC_1", long_position=100.0),
                 pos("ACC_9", long_position=300.0, entity="OTHER_FUND")],
                cl_spec(),
            )
        self.assertIn("OTHER_FUND", str(ctx.exception))

    def test_mismatched_commodity_raises(self):
        # REGRESSION. Crude oil and corn pooled into one limit test.
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_entity_cftc_compliance(
                "ACME_FUND",
                [pos("ACC_1", long_position=100.0, commodity="ZC")],
                cl_spec(),
            )
        self.assertIn("must not be pooled", str(ctx.exception))

    def test_duplicate_account_bucket_raises(self):
        # The same account/month/instrument supplied twice double counts.
        with self.assertRaises(ValueError) as ctx:
            self.engine.evaluate_entity_cftc_compliance(
                "ACME_FUND",
                [pos("ACC_1", long_position=200.0), pos("ACC_1", long_position=200.0)],
                cl_spec(),
            )
        self.assertIn("double counted", str(ctx.exception))

    def test_distinct_accounts_same_month_do_aggregate(self):
        report = self.engine.evaluate_entity_cftc_compliance(
            "ACME_FUND",
            [pos("ACC_1", long_position=200.0), pos("ACC_2", long_position=200.0)],
            cl_spec(),
        )
        self.assertEqual(report.aggregated_gross_long, 400.0)

    def test_empty_position_list_is_flagged_not_silently_clean(self):
        report = self.engine.evaluate_entity_cftc_compliance(
            "ACME_FUND", [], cl_spec()
        )
        self.assertEqual(report.status, "BELOW_REPORTING_LEVEL")
        self.assertIn("not a verified-flat report", report.audit_notes)


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = PositionLimitReportingCFTCLargeTraderEngine()

    def test_negative_leg_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_entity_cftc_compliance(
                "ACME_FUND", [pos("ACC_1", short_position=-500.0)], cl_spec()
            )

    def test_nan_leg_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_entity_cftc_compliance(
                "ACME_FUND", [pos("ACC_1", long_position=float("nan"))], cl_spec()
            )

    def test_infinite_leg_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_entity_cftc_compliance(
                "ACME_FUND", [pos("ACC_1", long_position=float("inf"))], cl_spec()
            )

    def test_empty_entity_name_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_entity_cftc_compliance("  ", [], cl_spec())

    def test_blank_contract_month_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_entity_cftc_compliance(
                "ACME_FUND", [pos("ACC_1", month="", long_position=1.0)], cl_spec()
            )

    def test_unknown_instrument_class_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_entity_cftc_compliance(
                "ACME_FUND",
                [pos("ACC_1", long_position=1.0, instrument="SWAP")],
                cl_spec(),
            )

    def test_negative_reporting_threshold_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_entity_cftc_compliance(
                "ACME_FUND", [], cl_spec(reporting_threshold_contracts=-1.0)
            )

    def test_negative_limit_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_entity_cftc_compliance(
                "ACME_FUND", [], cl_spec(spot_month_limit=-1.0), spot_month="2026-12"
            )

    def test_non_position_object_raises(self):
        with self.assertRaises(ValueError):
            self.engine.evaluate_entity_cftc_compliance(
                "ACME_FUND", [{"long_position": 400.0}], cl_spec()
            )


class TestDisabledEngine(unittest.TestCase):

    def test_disabled_engine_asserts_nothing(self):
        engine = PositionLimitReportingCFTCLargeTraderEngine(Config(enabled=False))
        report = engine.evaluate_entity_cftc_compliance(
            "ACME_FUND",
            [pos("ACC_1", long_position=99_999.0)],
            cl_spec(spot_month_limit=6_000.0),
        )
        self.assertEqual(report.status, "ENGINE_DISABLED")
        self.assertFalse(report.is_reportable)
        self.assertFalse(report.is_limit_breached)
        self.assertIn("not evidence of compliance", report.audit_notes)
        self.assertEqual(
            report.limits_not_tested,
            (LIMIT_SPOT_MONTH, LIMIT_SINGLE_MONTH, LIMIT_ALL_MONTHS_COMBINED),
        )


class TestReportShape(unittest.TestCase):

    def test_derived_net_matches_legs(self):
        p = TraderAccountPosition(
            account_id="A", entity_name="E", commodity_code="CL",
            contract_month="2026-12", long_position=400.0, short_position=150.0,
        )
        self.assertEqual(p.net_position, 250.0)

    def test_report_is_the_documented_type(self):
        engine = PositionLimitReportingCFTCLargeTraderEngine()
        report = engine.evaluate_entity_cftc_compliance(
            "ACME_FUND", [pos("ACC_1", long_position=10.0)], cl_spec()
        )
        self.assertIsInstance(report, CFTCLargeTraderReport)
        self.assertTrue(
            all(isinstance(m, ContractMonthPosition) for m in report.month_detail)
        )
        self.assertEqual(report.as_of, "")

    def test_as_of_is_recorded(self):
        engine = PositionLimitReportingCFTCLargeTraderEngine()
        report = engine.evaluate_entity_cftc_compliance(
            "ACME_FUND", [pos("ACC_1", long_position=10.0)], cl_spec(),
            as_of="2026-08-27 close",
        )
        self.assertEqual(report.as_of, "2026-08-27 close")


if __name__ == "__main__":
    unittest.main()

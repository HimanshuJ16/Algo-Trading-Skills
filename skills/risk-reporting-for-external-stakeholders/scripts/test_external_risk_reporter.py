"""Unit tests for the external risk disclosure engine.

Expected values are derived by hand (125,000,000 / 50,000,000 = 2.5) rather than
by re-running the implementation's arithmetic. Several tests are explicit
regressions against version 1 defects and say so in their docstrings; each of
those is written so that it fails against the old behaviour and passes against
the fix, not merely so that it passes today.
"""

import hashlib
import hmac
import unittest

from external_risk_reporter import (
    AUTHENTICATION_HMAC_SHA256,
    AUTHENTICATION_NONE,
    DIGEST_COVERED_FIELDS,
    DisclosurePolicyError,
    ExternalRiskReport,
    LiquidityConvention,
    PortfolioRiskState,
    RedactionError,
    ReportInputError,
    RiskReportingForExternalStakeholdersEngine,
    StakeholderType,
    canonical_report_bytes,
    verify_report,
)

# A Form PF Q32-shaped bucketed profile: each investment in exactly one bucket,
# totalling 100%.
_LIQUIDITY = {
    "1 day or less": 60.0,
    "2 days - 7 days": 25.0,
    "8 days - 30 days": 10.0,
    "31 days - 90 days": 5.0,
}

# Deliberately NOT sorted by size. Version 1 sliced insertion order, so a caller
# handing over an unsorted mapping received an arbitrary subset under a "top N"
# heading.
_SECTORS = {
    "ENERGY": 8.0,
    "TECH": 25.0,
    "MISC": 2.0,
    "HEALTHCARE": 10.0,
    "FINANCE": 15.0,
    "CRYPTO": 5.0,
}


def _state(**overrides):
    """A 2.5x gross / 0.3x net long-short book with a USD 50m NAV."""
    kwargs = dict(
        fund_name="ALPHA_QUANT_FUND_LP",
        report_date_iso="2026-08-05",
        total_aum_usd=50_000_000.0,
        net_asset_value_usd=50_000_000.0,
        gross_exposure_usd=125_000_000.0,   # 125m / 50m = 2.5x
        net_exposure_usd=15_000_000.0,      # 15m / 50m = 0.3x
        var_pct_of_nav=1.85,
        var_confidence_pct=99.0,
        var_horizon_days=1,
        annualized_sharpe_ratio=2.1,
        max_drawdown_pct=6.5,
        top_sector_concentrations=dict(_SECTORS),
        liquidity_days_to_liquidate_pct=dict(_LIQUIDITY),
        proprietary_positions=[
            {"symbol": "SECRETCO", "qty": 10_000},
            {"ticker": "ZXQV", "qty": -4_000},
        ],
    )
    kwargs.update(overrides)
    return PortfolioRiskState(**kwargs)


class TestLeverageArithmetic(unittest.TestCase):
    def setUp(self):
        self.engine = RiskReportingForExternalStakeholdersEngine()

    def test_gross_and_net_leverage(self):
        report = self.engine.generate_external_report(_state(), StakeholderType.LIMITED_PARTNER)
        self.assertAlmostEqual(report.gross_leverage, 2.5, places=10)
        self.assertAlmostEqual(report.net_leverage, 0.3, places=10)

    def test_net_short_book_reports_negative_net_leverage(self):
        """A net-short book is legitimate and must not be clamped or rejected."""
        report = self.engine.generate_external_report(
            _state(net_exposure_usd=-20_000_000.0), StakeholderType.PRIME_BROKER
        )
        self.assertAlmostEqual(report.net_leverage, -0.4, places=10)

    def test_leverage_is_not_pre_rounded(self):
        """Regression: version 1 rounded both ratios to 2dp before reporting.

        1/3 of NAV is 0.3333...; rounding it to 0.33 in the deliverable loses
        precision the caller computed and cannot be recovered downstream.
        """
        report = self.engine.generate_external_report(
            _state(gross_exposure_usd=50_000_000.0, net_exposure_usd=50_000_000.0 / 3.0),
            StakeholderType.AUDITOR,
        )
        self.assertNotEqual(report.net_leverage, 0.33)
        self.assertAlmostEqual(report.net_leverage, 1.0 / 3.0, places=12)


class TestInputValidation(unittest.TestCase):
    """Version 1 validated nothing; every figure below reached a sealed report."""

    def test_zero_nav_rejected(self):
        """Regression: version 1 divided by max(nav, 1.0).

        A zero-NAV fund reported gross leverage of 125,000,000x -- exposure
        denominated in one dollar -- rather than saying the ratio is undefined.
        """
        with self.assertRaises(ReportInputError):
            _state(net_asset_value_usd=0.0)

    def test_negative_nav_rejected(self):
        with self.assertRaises(ReportInputError):
            _state(net_asset_value_usd=-1_000_000.0)

    def test_nan_nav_rejected(self):
        """Regression: max(float('nan'), 1.0) is NaN, which version 1 propagated."""
        with self.assertRaises(ReportInputError):
            _state(net_asset_value_usd=float("nan"))

    def test_nan_var_rejected(self):
        with self.assertRaises(ReportInputError):
            _state(var_pct_of_nav=float("nan"))

    def test_infinite_exposure_rejected(self):
        with self.assertRaises(ReportInputError):
            _state(gross_exposure_usd=float("inf"))

    def test_var_above_100_pct_of_nav_rejected(self):
        with self.assertRaises(ReportInputError):
            _state(var_pct_of_nav=140.0)

    def test_negative_var_rejected(self):
        """VaR is reported as a positive loss magnitude (Form PF Q40(b)(vii))."""
        with self.assertRaises(ReportInputError):
            _state(var_pct_of_nav=-1.85)

    def test_var_horizon_must_be_a_positive_int(self):
        with self.assertRaises(ReportInputError):
            _state(var_horizon_days=0)
        with self.assertRaises(ReportInputError):
            _state(var_horizon_days=1.5)

    def test_drawdown_outside_0_100_rejected(self):
        with self.assertRaises(ReportInputError):
            _state(max_drawdown_pct=-2.0)
        with self.assertRaises(ReportInputError):
            _state(max_drawdown_pct=101.0)

    def test_gross_below_absolute_net_rejected(self):
        """gross = |long| + |short| >= |long - short| = |net|, always."""
        with self.assertRaises(ReportInputError):
            _state(gross_exposure_usd=10_000_000.0, net_exposure_usd=20_000_000.0)

    def test_gross_equal_to_net_accepted(self):
        """A pure long book has gross == net; the boundary must not be a failure."""
        state = _state(
            gross_exposure_usd=40_000_000.0,
            net_exposure_usd=40_000_000.0,
            top_sector_concentrations={"TECH": 50.0},
        )
        self.assertAlmostEqual(state.gross_exposure_usd, state.net_exposure_usd)

    def test_sector_exposure_exceeding_gross_rejected(self):
        """Sector percentages are of NAV, so they are bounded by gross leverage."""
        with self.assertRaises(ReportInputError):
            _state(
                gross_exposure_usd=25_000_000.0,   # 0.5x gross => 50% of NAV
                net_exposure_usd=25_000_000.0,
                top_sector_concentrations={"TECH": 60.0, "ENERGY": 30.0},
            )

    def test_bad_date_rejected(self):
        for bad in ("2026-13-01", "05/08/2026", "20260805", "", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ReportInputError):
                    _state(report_date_iso=bad)

    def test_empty_fund_name_rejected(self):
        with self.assertRaises(ReportInputError):
            _state(fund_name="   ")

    def test_non_string_concentration_key_rejected(self):
        with self.assertRaises(ReportInputError):
            _state(top_sector_concentrations={1: 25.0})

    def test_engine_rejects_non_state_argument(self):
        engine = RiskReportingForExternalStakeholdersEngine()
        with self.assertRaises(ReportInputError):
            engine.generate_external_report({"nav": 1.0}, StakeholderType.AUDITOR)


class TestLiquidityConventions(unittest.TestCase):
    """Version 1 carried no convention, so 85/100 was unreadable either way."""

    def test_bucketed_profile_must_sum_to_about_100(self):
        with self.assertRaises(ReportInputError):
            _state(liquidity_days_to_liquidate_pct={"1_DAY": 85.0, "7_DAYS": 100.0})

    def test_bucketed_tolerance_accepts_rounding_slack(self):
        state = _state(liquidity_days_to_liquidate_pct={"1 day or less": 99.6, "2 days - 7 days": 0.2})
        self.assertAlmostEqual(sum(state.liquidity_days_to_liquidate_pct.values()), 99.8, places=10)

    def test_negative_bucket_rejected(self):
        with self.assertRaises(ReportInputError):
            _state(liquidity_days_to_liquidate_pct={"1 day or less": 110.0, "2 days - 7 days": -10.0})

    def test_cumulative_profile_accepted_when_declared(self):
        state = _state(
            liquidity_days_to_liquidate_pct={"1_DAY": 85.0, "7_DAYS": 100.0},
            liquidity_convention=LiquidityConvention.CUMULATIVE,
        )
        self.assertIs(state.liquidity_convention, LiquidityConvention.CUMULATIVE)

    def test_cumulative_profile_must_not_decrease(self):
        with self.assertRaises(ReportInputError):
            _state(
                liquidity_days_to_liquidate_pct={"1_DAY": 85.0, "7_DAYS": 40.0},
                liquidity_convention=LiquidityConvention.CUMULATIVE,
            )

    def test_cumulative_profile_capped_at_100(self):
        with self.assertRaises(ReportInputError):
            _state(
                liquidity_days_to_liquidate_pct={"1_DAY": 85.0, "7_DAYS": 130.0},
                liquidity_convention=LiquidityConvention.CUMULATIVE,
            )

    def test_empty_profile_means_not_reported(self):
        state = _state(liquidity_days_to_liquidate_pct={})
        self.assertEqual(state.liquidity_days_to_liquidate_pct, {})

    def test_unknown_convention_rejected(self):
        with self.assertRaises(ReportInputError):
            _state(liquidity_convention="ROLLING")

    def test_convention_is_carried_onto_the_report(self):
        engine = RiskReportingForExternalStakeholdersEngine()
        report = engine.generate_external_report(_state(), StakeholderType.LIMITED_PARTNER)
        self.assertIs(report.liquidity_convention, LiquidityConvention.BUCKETED)


class TestDisclosurePolicy(unittest.TestCase):
    def setUp(self):
        self.engine = RiskReportingForExternalStakeholdersEngine()

    def test_limited_partner_receives_top_five(self):
        report = self.engine.generate_external_report(_state(), StakeholderType.LIMITED_PARTNER)
        self.assertEqual(len(report.disclosed_concentrations), 5)

    def test_prime_broker_receives_top_three(self):
        report = self.engine.generate_external_report(_state(), StakeholderType.PRIME_BROKER)
        self.assertEqual(len(report.disclosed_concentrations), 3)

    def test_regulator_and_auditor_receive_every_sector(self):
        for stakeholder in (StakeholderType.REGULATOR, StakeholderType.AUDITOR):
            with self.subTest(stakeholder=stakeholder):
                report = self.engine.generate_external_report(_state(), stakeholder)
                self.assertEqual(len(report.disclosed_concentrations), len(_SECTORS))

    def test_top_n_ranks_rather_than_slicing(self):
        """Regression: version 1 returned the first N in insertion order.

        ``_SECTORS`` is supplied unsorted, so version 1's LP disclosure would
        have been ENERGY/TECH/MISC/HEALTHCARE/FINANCE -- dropping CRYPTO's 5.0
        while keeping MISC's 2.0 under a "top five" heading.
        """
        report = self.engine.generate_external_report(_state(), StakeholderType.LIMITED_PARTNER)
        self.assertEqual(
            list(report.disclosed_concentrations),
            ["TECH", "FINANCE", "HEALTHCARE", "ENERGY", "CRYPTO"],
        )
        self.assertNotIn("MISC", report.disclosed_concentrations)

    def test_ranking_uses_absolute_exposure(self):
        """A -40% net short outranks a +5% long: concentration is about size."""
        report = self.engine.generate_external_report(
            _state(top_sector_concentrations={"LONG_TECH": 5.0, "SHORT_ENERGY": -40.0}),
            StakeholderType.PRIME_BROKER,
        )
        self.assertEqual(list(report.disclosed_concentrations), ["SHORT_ENERGY", "LONG_TECH"])

    def test_ranking_ties_break_deterministically(self):
        report = self.engine.generate_external_report(
            _state(top_sector_concentrations={"ZULU": 10.0, "ALPHA": 10.0, "MIKE": 10.0}),
            StakeholderType.PRIME_BROKER,
        )
        self.assertEqual(list(report.disclosed_concentrations), ["ALPHA", "MIKE", "ZULU"])

    def test_unknown_stakeholder_fails_closed(self):
        """Regression: version 1's ``else`` branch disclosed everything.

        A recipient type nobody wrote a policy for received the widest
        disclosure in the module, silently.
        """
        with self.assertRaises(DisclosurePolicyError):
            self.engine.generate_external_report(_state(), "PROSPECTIVE_INVESTOR")

    def test_equivalent_string_stakeholder_is_accepted(self):
        report = self.engine.generate_external_report(_state(), "REGULATOR")
        self.assertIs(report.stakeholder_type, StakeholderType.REGULATOR)

    def test_regulator_notice_says_this_is_not_a_filing(self):
        """The single most consequential documentation claim in this skill."""
        report = self.engine.generate_external_report(_state(), StakeholderType.REGULATOR)
        self.assertIn("NOT A STATUTORY FILING", report.disclosure_notice)
        self.assertIn("Q35", report.disclosure_notice)


class TestRedaction(unittest.TestCase):
    def setUp(self):
        self.engine = RiskReportingForExternalStakeholdersEngine()

    def test_positions_never_appear_in_the_report(self):
        report = self.engine.generate_external_report(_state(), StakeholderType.LIMITED_PARTNER)
        serialized = repr(report)
        self.assertNotIn("SECRETCO", serialized)
        self.assertNotIn("ZXQV", serialized)
        self.assertTrue(report.are_proprietary_positions_redacted)

    def test_redaction_verified_and_counted(self):
        report = self.engine.generate_external_report(_state(), StakeholderType.LIMITED_PARTNER)
        self.assertTrue(report.redaction_verified)
        self.assertEqual(report.positions_withheld_count, 2)
        self.assertIn("VERIFIED", report.redaction_note)

    def test_identifier_leaking_through_a_sector_label_raises(self):
        """Regression: version 1 asserted redaction unconditionally.

        A sector breakdown keyed by ticker is a position disclosure wearing a
        sector label, and version 1 would have shipped it with
        ``are_proprietary_positions_redacted=True``.
        """
        with self.assertRaises(RedactionError) as ctx:
            self.engine.generate_external_report(
                _state(top_sector_concentrations={"SECRETCO": 25.0, "TECH": 10.0}),
                StakeholderType.LIMITED_PARTNER,
            )
        self.assertIn("SECRETCO", str(ctx.exception))

    def test_identifier_leaking_through_a_liquidity_label_raises(self):
        with self.assertRaises(RedactionError):
            self.engine.generate_external_report(
                _state(liquidity_days_to_liquidate_pct={"ZXQV 1 day": 100.0}),
                StakeholderType.PRIME_BROKER,
            )

    def test_leak_detection_is_case_insensitive(self):
        with self.assertRaises(RedactionError):
            self.engine.generate_external_report(
                _state(top_sector_concentrations={"secretco": 25.0, "TECH": 10.0}),
                StakeholderType.AUDITOR,
            )

    def test_substring_match_is_not_a_leak(self):
        """Whole-word matching: a ticker 'F' must not condemn 'FINANCE'."""
        report = self.engine.generate_external_report(
            _state(proprietary_positions=[{"symbol": "F", "qty": 100}]),
            StakeholderType.LIMITED_PARTNER,
        )
        self.assertTrue(report.redaction_verified)

    def test_standalone_short_ticker_is_a_leak(self):
        with self.assertRaises(RedactionError):
            self.engine.generate_external_report(
                _state(
                    proprietary_positions=[{"symbol": "F", "qty": 100}],
                    top_sector_concentrations={"F": 25.0, "TECH": 10.0},
                ),
                StakeholderType.LIMITED_PARTNER,
            )

    def test_no_positions_supplied_means_unverified_not_verified(self):
        """``False`` here means 'not checked', and the note must say so."""
        report = self.engine.generate_external_report(
            _state(proprietary_positions=None), StakeholderType.LIMITED_PARTNER
        )
        self.assertFalse(report.redaction_verified)
        self.assertIsNone(report.positions_withheld_count)
        self.assertIn("NOT VERIFIED", report.redaction_note)

    def test_unrecognised_identifier_field_is_reported_as_unverified(self):
        report = self.engine.generate_external_report(
            _state(proprietary_positions=[{"internal_code": "SECRETCO", "qty": 1}]),
            StakeholderType.LIMITED_PARTNER,
        )
        self.assertFalse(report.redaction_verified)
        self.assertEqual(report.positions_withheld_count, 1)
        self.assertIn("NOT VERIFIED", report.redaction_note)

    def test_identifier_fields_can_be_extended(self):
        engine = RiskReportingForExternalStakeholdersEngine(identifier_fields=["internal_code"])
        with self.assertRaises(RedactionError):
            engine.generate_external_report(
                _state(
                    proprietary_positions=[{"internal_code": "SECRETCO", "qty": 1}],
                    top_sector_concentrations={"SECRETCO": 25.0},
                ),
                StakeholderType.LIMITED_PARTNER,
            )

    def test_non_mapping_position_rejected(self):
        with self.assertRaises(ReportInputError):
            _state(proprietary_positions=["SECRETCO"])


class TestIntegrityEnvelope(unittest.TestCase):
    def setUp(self):
        self.engine = RiskReportingForExternalStakeholdersEngine()
        self.report = self.engine.generate_external_report(_state(), StakeholderType.REGULATOR)

    def _tamper(self, **changes) -> ExternalRiskReport:
        fields = {f.name: getattr(self.report, f.name) for f in self.report.__dataclass_fields__.values()}
        fields.update(changes)
        return ExternalRiskReport(**fields)

    def test_digest_is_sha256_of_the_canonical_bytes(self):
        covered = {name: getattr(self.report, name) for name in DIGEST_COVERED_FIELDS}
        self.assertEqual(
            self.report.content_digest,
            hashlib.sha256(canonical_report_bytes(covered)).hexdigest(),
        )

    def test_digest_is_deterministic_across_runs(self):
        again = self.engine.generate_external_report(_state(), StakeholderType.REGULATOR)
        self.assertEqual(self.report.content_digest, again.content_digest)

    def test_verify_accepts_an_untampered_report(self):
        self.assertTrue(verify_report(self.report))

    def test_digest_covers_var(self):
        """Regression: version 1 hashed only fund, stakeholder, date, NAV and gross leverage.

        Every risk metric the report exists to communicate -- VaR, Sharpe,
        drawdown, concentrations, liquidity -- could be altered with the digest
        still verifying.
        """
        self.assertIn("var_pct_of_nav", self.report.digest_covers)
        self.assertFalse(verify_report(self._tamper(var_pct_of_nav=0.10)))

    def test_digest_covers_concentrations_sharpe_drawdown_and_liquidity(self):
        for field_name, value in (
            ("annualized_sharpe", 9.9),
            ("max_drawdown_pct", 0.1),
            ("disclosed_concentrations", {"TECH": 1.0}),
            ("disclosed_liquidity", {"1 day or less": 100.0}),
        ):
            with self.subTest(field=field_name):
                self.assertIn(field_name, self.report.digest_covers)
                self.assertFalse(verify_report(self._tamper(**{field_name: value})))

    def test_digest_is_independent_of_mapping_order(self):
        """Canonical form sorts keys, so a recipient's JSON round-trip verifies."""
        reordered = dict(reversed(list(self.report.disclosed_concentrations.items())))
        self.assertNotEqual(list(reordered), list(self.report.disclosed_concentrations))
        self.assertTrue(verify_report(self._tamper(disclosed_concentrations=reordered)))

    def test_swapped_report_id_fails_verification(self):
        """``report_id`` is outside the digest payload but not outside the seal.

        It is derived from the digest, so ``verify_report`` re-derives it. Found
        by adversarial review: without this, an attacker could swap the
        identifier that ties a report to the dispatch log and audit trail while
        the digest still verified.
        """
        self.assertFalse(
            verify_report(self._tamper(report_id="RPT-AUDITOR-1999-01-01-OTHER-DEADBEEFCAFE"))
        )

    def test_unkeyed_report_is_labelled_as_integrity_only(self):
        self.assertEqual(self.report.authentication, AUTHENTICATION_NONE)
        self.assertIsNone(self.report.authentication_tag)
        self.assertIn("not authenticity", self.report.authentication)

    def test_hmac_key_produces_an_authentication_tag(self):
        keyed = RiskReportingForExternalStakeholdersEngine(hmac_key=b"shared-secret-key")
        report = keyed.generate_external_report(_state(), StakeholderType.REGULATOR)
        self.assertEqual(report.authentication, AUTHENTICATION_HMAC_SHA256)
        covered = {name: getattr(report, name) for name in DIGEST_COVERED_FIELDS}
        self.assertEqual(
            report.authentication_tag,
            hmac.new(b"shared-secret-key", canonical_report_bytes(covered), hashlib.sha256).hexdigest(),
        )
        self.assertTrue(verify_report(report, hmac_key=b"shared-secret-key"))

    def test_hmac_verification_fails_under_the_wrong_key(self):
        keyed = RiskReportingForExternalStakeholdersEngine(hmac_key=b"shared-secret-key")
        report = keyed.generate_external_report(_state(), StakeholderType.REGULATOR)
        self.assertFalse(verify_report(report, hmac_key=b"wrong-key"))

    def test_verifying_a_tagged_report_without_a_key_raises(self):
        """Silently downgrading to an integrity check would report a false success."""
        keyed = RiskReportingForExternalStakeholdersEngine(hmac_key=b"shared-secret-key")
        report = keyed.generate_external_report(_state(), StakeholderType.REGULATOR)
        with self.assertRaises(ReportInputError):
            verify_report(report)

    def test_empty_hmac_key_rejected(self):
        with self.assertRaises(ReportInputError):
            RiskReportingForExternalStakeholdersEngine(hmac_key=b"")

    def test_string_hmac_key_rejected(self):
        with self.assertRaises(ReportInputError):
            RiskReportingForExternalStakeholdersEngine(hmac_key="shared-secret-key")


class TestReportIdentity(unittest.TestCase):
    def setUp(self):
        self.engine = RiskReportingForExternalStakeholdersEngine()

    def test_report_id_distinguishes_two_funds_on_the_same_date(self):
        """Regression: version 1's id was stakeholder + date only.

        Two funds, or an original and its restatement, collided on one id --
        breaking the audit trail the skill claims to provide.
        """
        first = self.engine.generate_external_report(_state(), StakeholderType.LIMITED_PARTNER)
        second = self.engine.generate_external_report(
            _state(fund_name="BETA_QUANT_FUND_LP"), StakeholderType.LIMITED_PARTNER
        )
        self.assertNotEqual(first.report_id, second.report_id)

    def test_restatement_of_one_figure_changes_the_report_id(self):
        original = self.engine.generate_external_report(_state(), StakeholderType.LIMITED_PARTNER)
        restated = self.engine.generate_external_report(
            _state(var_pct_of_nav=2.40), StakeholderType.LIMITED_PARTNER
        )
        self.assertNotEqual(original.report_id, restated.report_id)

    def test_regenerating_the_same_report_is_idempotent(self):
        first = self.engine.generate_external_report(_state(), StakeholderType.LIMITED_PARTNER)
        second = self.engine.generate_external_report(_state(), StakeholderType.LIMITED_PARTNER)
        self.assertEqual(first.report_id, second.report_id)

    def test_report_id_ends_with_the_digest_prefix(self):
        report = self.engine.generate_external_report(_state(), StakeholderType.LIMITED_PARTNER)
        self.assertTrue(report.report_id.endswith(report.content_digest[:12].upper()))
        self.assertNotIn("report_id", report.digest_covers)

    def test_preparer_is_named_on_the_report_and_covered_by_the_digest(self):
        engine = RiskReportingForExternalStakeholdersEngine(firm_name="Northwind Capital")
        report = engine.generate_external_report(_state(), StakeholderType.AUDITOR)
        self.assertEqual(report.preparer_firm_name, "Northwind Capital")
        self.assertIn("preparer_firm_name", report.digest_covers)

    def test_empty_firm_name_rejected(self):
        with self.assertRaises(ReportInputError):
            RiskReportingForExternalStakeholdersEngine(firm_name="  ")


class TestAuditNotes(unittest.TestCase):
    def test_audit_notes_state_the_var_confidence_and_horizon(self):
        """A VaR figure without its confidence level and horizon is not readable.

        Form PF Q40(b) makes the filer state both because they vary by filer.
        """
        engine = RiskReportingForExternalStakeholdersEngine()
        report = engine.generate_external_report(
            _state(var_confidence_pct=95.0, var_horizon_days=10), StakeholderType.REGULATOR
        )
        self.assertIn("95% confidence", report.audit_notes)
        self.assertIn("10d", report.audit_notes)
        self.assertEqual(report.var_confidence_pct, 95.0)
        self.assertEqual(report.var_horizon_days, 10)

    def test_audit_notes_record_how_much_was_withheld(self):
        engine = RiskReportingForExternalStakeholdersEngine()
        report = engine.generate_external_report(_state(), StakeholderType.PRIME_BROKER)
        self.assertIn("Sectors disclosed: 3 of 6", report.audit_notes)


if __name__ == "__main__":
    unittest.main()

"""Behavioural tests for the post-FTX venue counterparty-risk gate.

Expected risk scores are derived by hand from the published weights rather than
by re-running the engine's own summation, so a change to the weighting is a
test failure rather than a silent re-baseline.
"""
import math
import unittest

from ftx_style_exchange_post_collapse_risk_lessons import (
    MAX_RISK_SCORE,
    WEIGHT_NATIVE_TOKEN_CONCENTRATION,
    WEIGHT_NAV_CONCENTRATION,
    WEIGHT_NO_INDEPENDENT_ATTESTATION,
    WEIGHT_NO_OFF_EXCHANGE_SETTLEMENT,
    WEIGHT_POR_SHORTFALL,
    WEIGHT_STALE_OR_UNDATED_POR,
    ExchangeCounterpartyRiskError,
    ExchangePostCollapseRiskEngine,
    ExchangeSolvencyMetrics,
)


def clean_metrics(**overrides):
    """A venue that passes every dimension; override one field per test."""
    base = dict(
        venue_id="VENUE_A",
        exchange_name="Compliant Venue",
        proof_of_reserves_ratio=1.05,
        native_token_collateral_pct=0.02,
        uses_off_exchange_settlement=True,
        nav_exposure_pct=0.15,
        has_independent_attestation=True,
        por_snapshot_age_days=30,
    )
    base.update(overrides)
    return ExchangeSolvencyMetrics(**base)


class TestWeighting(unittest.TestCase):
    def test_weights_total_the_maximum_score(self):
        # If the weights sum past MAX_RISK_SCORE the top of the scale is
        # degenerate: a venue failing one dimension and a venue failing all of
        # them both report the maximum, and the score stops discriminating.
        total = (
            WEIGHT_POR_SHORTFALL
            + WEIGHT_NATIVE_TOKEN_CONCENTRATION
            + WEIGHT_NAV_CONCENTRATION
            + WEIGHT_NO_OFF_EXCHANGE_SETTLEMENT
            + WEIGHT_NO_INDEPENDENT_ATTESTATION
            + WEIGHT_STALE_OR_UNDATED_POR
        )
        self.assertTrue(math.isclose(total, MAX_RISK_SCORE))


class TestVenueAudit(unittest.TestCase):
    def setUp(self):
        self.engine = ExchangePostCollapseRiskEngine()

    def test_fully_compliant_venue_scores_zero_and_clears(self):
        report = self.engine.audit_exchange_counterparty_risk(clean_metrics())

        self.assertEqual(report.status, "VENUE_RISK_ACCEPTABLE")
        self.assertFalse(report.is_derisking_triggered)
        self.assertTrue(report.is_por_valid)
        self.assertTrue(report.is_por_snapshot_current)
        self.assertTrue(report.is_native_token_safe)
        self.assertTrue(report.is_nav_exposure_safe)
        self.assertEqual(report.risk_score_0_to_100, 0.0)
        self.assertEqual(report.recommended_capital_withdrawal_pct, 0.0)
        self.assertEqual(report.findings, [])

    def test_ftx_style_venue_scores_the_maximum_and_disqualifies(self):
        # PoR 85% (35) + undated snapshot (5) + 35% native token (25)
        # + 40% NAV (15) + no OES (12) + no attestation (8) = 100.
        metrics = ExchangeSolvencyMetrics(
            venue_id="VENUE_B",
            exchange_name="Opaque Crypto Exchange",
            proof_of_reserves_ratio=0.85,
            native_token_collateral_pct=0.35,
            uses_off_exchange_settlement=False,
            nav_exposure_pct=0.40,
            has_independent_attestation=False,
            por_snapshot_age_days=None,
        )
        report = self.engine.audit_exchange_counterparty_risk(metrics)

        self.assertEqual(report.status, "EXCHANGE_DERISK_TRIGGERED")
        self.assertTrue(report.is_derisking_triggered)
        self.assertFalse(report.is_por_valid)
        self.assertFalse(report.is_native_token_safe)
        self.assertFalse(report.is_nav_exposure_safe)
        self.assertFalse(report.is_por_snapshot_current)
        self.assertEqual(report.risk_score_0_to_100, 100.0)
        self.assertEqual(report.recommended_capital_withdrawal_pct, 100.0)
        self.assertEqual(len(report.findings), 6)

    def test_trim_to_residual_target_at_the_exact_score_threshold(self):
        # Undated (5) + 50% NAV (15) + no OES (12) + no attestation (8) = 40,
        # exactly the default threshold, so de-risking must fire on >=.
        # PoR and native token are clean, so the venue is trimmed, not
        # disqualified: withdraw (0.50 - 0.05) / 0.50 = 90% of the venue balance.
        metrics = clean_metrics(
            proof_of_reserves_ratio=1.02,
            native_token_collateral_pct=0.01,
            uses_off_exchange_settlement=False,
            nav_exposure_pct=0.50,
            has_independent_attestation=False,
            por_snapshot_age_days=None,
        )
        report = self.engine.audit_exchange_counterparty_risk(metrics)

        self.assertEqual(report.risk_score_0_to_100, 40.0)
        self.assertTrue(report.is_derisking_triggered)
        self.assertEqual(report.recommended_capital_withdrawal_pct, 90.0)

    def test_soft_native_token_breach_alone_does_not_derisk(self):
        # 6% native token breaches the 5% soft cap (a finding) but sits under
        # the 10% hard trigger, and 25 < 40, so the venue is not de-risked.
        # Pinned deliberately: an operator who expects any breach to force a
        # withdrawal must configure hard_native_token_trigger, not assume it.
        report = self.engine.audit_exchange_counterparty_risk(
            clean_metrics(native_token_collateral_pct=0.06)
        )

        self.assertEqual(report.risk_score_0_to_100, 25.0)
        self.assertFalse(report.is_native_token_safe)
        self.assertFalse(report.is_derisking_triggered)
        self.assertEqual(report.status, "VENUE_RISK_ACCEPTABLE")


class TestWithdrawalArithmetic(unittest.TestCase):
    """Regressions on the withdrawal formula.

    Both cases below are reachable from ordinary inputs and both produce an
    unusable answer under an unguarded ``(nav - residual) / nav`` formula.
    """

    def setUp(self):
        self.engine = ExchangePostCollapseRiskEngine()

    def test_zero_nav_exposure_does_not_divide_by_zero(self):
        # A disqualified venue the desk has already emptied: 30% native token
        # fires the hard trigger while nav_exposure_pct is 0.0.
        metrics = clean_metrics(
            native_token_collateral_pct=0.30, nav_exposure_pct=0.0
        )
        report = self.engine.audit_exchange_counterparty_risk(metrics)

        self.assertTrue(report.is_derisking_triggered)
        self.assertEqual(report.status, "EXCHANGE_DERISK_TRIGGERED")
        # Nothing is deployed, so there is nothing to withdraw. 0.0 here is not
        # a safety signal -- is_derisking_triggered is the safety signal.
        self.assertEqual(report.recommended_capital_withdrawal_pct, 0.0)

    def test_exposure_below_residual_target_never_returns_a_negative_pct(self):
        # 6% native token (25) + no OES (12) + no attestation (8)
        # + undated (5) = 50 -> de-risked by score, but only 2% of NAV is at
        # the venue, already inside the 5% residual target.
        metrics = clean_metrics(
            native_token_collateral_pct=0.06,
            uses_off_exchange_settlement=False,
            nav_exposure_pct=0.02,
            has_independent_attestation=False,
            por_snapshot_age_days=None,
        )
        report = self.engine.audit_exchange_counterparty_risk(metrics)

        self.assertEqual(report.risk_score_0_to_100, 50.0)
        self.assertTrue(report.is_derisking_triggered)
        self.assertEqual(report.recommended_capital_withdrawal_pct, 0.0)

    def test_por_shortfall_disqualifies_regardless_of_residual_target(self):
        report = self.engine.audit_exchange_counterparty_risk(
            clean_metrics(proof_of_reserves_ratio=0.999, nav_exposure_pct=0.03)
        )

        self.assertFalse(report.is_por_valid)
        self.assertEqual(report.recommended_capital_withdrawal_pct, 100.0)


class TestBoundaries(unittest.TestCase):
    def setUp(self):
        self.engine = ExchangePostCollapseRiskEngine()

    def test_por_exactly_at_minimum_is_valid(self):
        report = self.engine.audit_exchange_counterparty_risk(
            clean_metrics(proof_of_reserves_ratio=1.00)
        )
        self.assertTrue(report.is_por_valid)
        self.assertEqual(report.risk_score_0_to_100, 0.0)

    def test_native_token_exactly_at_cap_is_safe(self):
        report = self.engine.audit_exchange_counterparty_risk(
            clean_metrics(native_token_collateral_pct=0.05)
        )
        self.assertTrue(report.is_native_token_safe)

    def test_native_token_exactly_at_hard_trigger_is_not_disqualified(self):
        # The hard trigger is strict (>), so 10.0% breaches the soft cap only.
        report = self.engine.audit_exchange_counterparty_risk(
            clean_metrics(native_token_collateral_pct=0.10)
        )
        self.assertFalse(report.is_native_token_safe)
        self.assertFalse(report.is_derisking_triggered)

    def test_native_token_just_above_hard_trigger_disqualifies(self):
        report = self.engine.audit_exchange_counterparty_risk(
            clean_metrics(native_token_collateral_pct=0.1001)
        )
        self.assertTrue(report.is_derisking_triggered)
        self.assertEqual(report.recommended_capital_withdrawal_pct, 100.0)

    def test_nav_exactly_at_cap_is_safe(self):
        report = self.engine.audit_exchange_counterparty_risk(
            clean_metrics(nav_exposure_pct=0.20)
        )
        self.assertTrue(report.is_nav_exposure_safe)
        self.assertEqual(report.risk_score_0_to_100, 0.0)

    def test_snapshot_exactly_at_max_age_is_current(self):
        report = self.engine.audit_exchange_counterparty_risk(
            clean_metrics(por_snapshot_age_days=90)
        )
        self.assertTrue(report.is_por_snapshot_current)
        self.assertEqual(report.risk_score_0_to_100, 0.0)

    def test_snapshot_one_day_past_max_age_is_stale(self):
        report = self.engine.audit_exchange_counterparty_risk(
            clean_metrics(por_snapshot_age_days=91)
        )
        self.assertFalse(report.is_por_snapshot_current)
        self.assertEqual(report.risk_score_0_to_100, WEIGHT_STALE_OR_UNDATED_POR)
        self.assertIn("POR_SNAPSHOT_STALE", report.findings[0])

    def test_undated_snapshot_is_scored_as_a_gap_not_a_pass(self):
        report = self.engine.audit_exchange_counterparty_risk(
            clean_metrics(por_snapshot_age_days=None)
        )
        self.assertFalse(report.is_por_snapshot_current)
        self.assertEqual(report.risk_score_0_to_100, WEIGHT_STALE_OR_UNDATED_POR)
        self.assertIn("POR_SNAPSHOT_UNDATED", report.findings[0])


class TestInputValidation(unittest.TestCase):
    def setUp(self):
        self.engine = ExchangePostCollapseRiskEngine()

    def _assert_rejected(self, **overrides):
        with self.assertRaises(ExchangeCounterpartyRiskError):
            self.engine.audit_exchange_counterparty_risk(clean_metrics(**overrides))

    def test_nan_native_token_raises_instead_of_scoring(self):
        # NaN defeats both guards: nan <= 0.05 is False (scored as a breach)
        # and nan > 0.10 is False (hard trigger skipped). The engine must not
        # produce a verdict from it.
        self._assert_rejected(native_token_collateral_pct=float("nan"))

    def test_nan_and_infinite_ratios_raise(self):
        self._assert_rejected(proof_of_reserves_ratio=float("nan"))
        self._assert_rejected(proof_of_reserves_ratio=float("inf"))
        self._assert_rejected(nav_exposure_pct=float("nan"))

    def test_percentage_supplied_where_a_fraction_was_meant_raises(self):
        # 5 meaning "5%" is 500% as a fraction.
        self._assert_rejected(native_token_collateral_pct=5.0)
        self._assert_rejected(nav_exposure_pct=40.0)

    def test_percentage_supplied_where_a_coverage_ratio_was_meant_raises(self):
        # 105 meaning "105%" would otherwise read as a wildly over-reserved
        # venue and clear every check.
        self._assert_rejected(proof_of_reserves_ratio=105.0)

    def test_negative_figures_raise(self):
        self._assert_rejected(proof_of_reserves_ratio=-0.5)
        self._assert_rejected(nav_exposure_pct=-0.01)
        self._assert_rejected(por_snapshot_age_days=-1)

    def test_truthy_non_boolean_attestation_raises(self):
        # "no" is truthy; scoring it as an attestation that exists is the one
        # direction of error this engine must never make.
        self._assert_rejected(has_independent_attestation="no")
        self._assert_rejected(uses_off_exchange_settlement="false")

    def test_blank_identifiers_raise(self):
        self._assert_rejected(venue_id="")
        self._assert_rejected(exchange_name="   ")

    def test_non_integer_snapshot_age_raises(self):
        self._assert_rejected(por_snapshot_age_days=1.5)

    def test_non_metrics_argument_raises(self):
        with self.assertRaises(ExchangeCounterpartyRiskError):
            self.engine.audit_exchange_counterparty_risk({"venue_id": "X"})


class TestEngineConfiguration(unittest.TestCase):
    def test_hard_trigger_below_soft_cap_raises(self):
        # Otherwise the hard trigger fires first and the soft cap is dead code.
        with self.assertRaises(ExchangeCounterpartyRiskError):
            ExchangePostCollapseRiskEngine(
                max_native_token_ratio=0.20, hard_native_token_trigger=0.10
            )

    def test_residual_above_venue_nav_cap_raises(self):
        with self.assertRaises(ExchangeCounterpartyRiskError):
            ExchangePostCollapseRiskEngine(
                max_single_venue_nav_pct=0.10, derisk_residual_nav_pct=0.20
            )

    def test_score_threshold_outside_the_scale_raises(self):
        with self.assertRaises(ExchangeCounterpartyRiskError):
            ExchangePostCollapseRiskEngine(derisk_score_threshold=0.0)
        with self.assertRaises(ExchangeCounterpartyRiskError):
            ExchangePostCollapseRiskEngine(derisk_score_threshold=150.0)

    def test_non_positive_por_requirement_raises(self):
        with self.assertRaises(ExchangeCounterpartyRiskError):
            ExchangePostCollapseRiskEngine(min_por_coverage_ratio=0.0)

    def test_negative_max_snapshot_age_raises(self):
        with self.assertRaises(ExchangeCounterpartyRiskError):
            ExchangePostCollapseRiskEngine(max_por_snapshot_age_days=-1)

    def test_stricter_policy_tightens_the_verdict(self):
        strict = ExchangePostCollapseRiskEngine(
            min_por_coverage_ratio=1.10,
            max_native_token_ratio=0.01,
            max_single_venue_nav_pct=0.10,
            hard_native_token_trigger=0.02,
            max_por_snapshot_age_days=7,
        )
        report = strict.audit_exchange_counterparty_risk(clean_metrics())

        # Under the default policy this same venue scores 0.0.
        self.assertFalse(report.is_por_valid)
        self.assertFalse(report.is_native_token_safe)
        self.assertFalse(report.is_nav_exposure_safe)
        self.assertFalse(report.is_por_snapshot_current)
        self.assertTrue(report.is_derisking_triggered)
        self.assertEqual(report.recommended_capital_withdrawal_pct, 100.0)


if __name__ == "__main__":
    unittest.main()

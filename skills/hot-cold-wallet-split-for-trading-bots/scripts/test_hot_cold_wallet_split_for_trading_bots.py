"""Unit tests for the Hot/Cold wallet split treasury engine.

Expected transfer amounts are derived by hand in each test's comment rather than
by re-running the engine's own arithmetic, so a formula regression cannot make a
test agree with a broken implementation.
"""
import logging
import unittest

from hot_cold_wallet_split_for_trading_bots import (
    ACTION_HOLD_BALANCES,
    ACTION_REFILL_HOT_FROM_COLD,
    ACTION_SECURITY_ALERT,
    ACTION_SWEEP_TO_COLD,
    STATUS_BALANCED,
    STATUS_REBALANCE_REQUIRED,
    STATUS_REGULATORY_BREACH,
    STATUS_SECURITY_ALERT,
    HotColdWalletError,
    HotColdWalletManagerEngine,
    WalletBalances,
)

# The engine logs a CRITICAL line on every security-alert path; silence it so the
# test output stays readable without suppressing real failures.
logging.disable(logging.CRITICAL)


class TestRebalanceDecisions(unittest.TestCase):
    """Normal-path sweep / refill / hold behaviour."""

    def setUp(self):
        self.engine = HotColdWalletManagerEngine(
            target_hot_ratio=0.15, max_hot_ratio_threshold=0.25, min_hot_ratio_threshold=0.05
        )

    def test_sweep_to_cold_when_hot_ratio_exceeds_max(self):
        # Total $1,000,000. Hot $300,000 = 30% > 25%. Target hot = 15% x 1M = $150,000.
        # Sweep = 300,000 - 150,000 = $150,000.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(hot_wallet_usd=300_000.0, cold_vault_usd=700_000.0)
        )
        self.assertEqual(report.status, STATUS_REBALANCE_REQUIRED)
        self.assertEqual(report.rebalance_action, ACTION_SWEEP_TO_COLD)
        self.assertEqual(report.proposed_transfer_usd, 150_000.0)
        self.assertTrue(report.is_api_key_secure)
        self.assertTrue(report.is_transfer_fully_fundable)

    def test_refill_hot_when_hot_ratio_below_min(self):
        # Total $1,000,000. Hot $30,000 = 3% < 5%. Target hot = $150,000.
        # Refill = 150,000 - 30,000 = $120,000, fundable from a $970,000 vault.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(hot_wallet_usd=30_000.0, cold_vault_usd=970_000.0)
        )
        self.assertEqual(report.status, STATUS_REBALANCE_REQUIRED)
        self.assertEqual(report.rebalance_action, ACTION_REFILL_HOT_FROM_COLD)
        self.assertEqual(report.proposed_transfer_usd, 120_000.0)
        self.assertTrue(report.is_transfer_fully_fundable)

    def test_hold_when_ratio_inside_band(self):
        # Hot $150,000 of $1,000,000 = 15%, squarely inside [5%, 25%].
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(hot_wallet_usd=150_000.0, cold_vault_usd=850_000.0)
        )
        self.assertEqual(report.status, STATUS_BALANCED)
        self.assertEqual(report.rebalance_action, ACTION_HOLD_BALANCES)
        self.assertEqual(report.proposed_transfer_usd, 0.0)

    def test_warm_buffer_counts_toward_total_and_is_reported(self):
        # Warm buffer is part of the denominator, so it must also be visible in the
        # report -- otherwise hot + cold != total and reconciliation silently fails.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=150_000.0, cold_vault_usd=350_000.0, warm_buffer_usd=500_000.0
            )
        )
        self.assertEqual(report.total_portfolio_usd, 1_000_000.0)
        self.assertEqual(report.warm_buffer_usd, 500_000.0)
        self.assertAlmostEqual(
            report.hot_wallet_usd + report.cold_vault_usd + report.warm_buffer_usd,
            report.total_portfolio_usd,
        )
        self.assertEqual(report.rebalance_action, ACTION_HOLD_BALANCES)

    def test_report_records_the_policy_it_was_evaluated_against(self):
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(hot_wallet_usd=150_000.0, cold_vault_usd=850_000.0)
        )
        self.assertEqual(report.target_hot_ratio, 0.15)
        self.assertEqual(report.max_hot_ratio_threshold, 0.25)
        self.assertEqual(report.min_hot_ratio_threshold, 0.05)
        self.assertIsNone(report.regulatory_max_hot_ratio)


class TestThresholdBoundaries(unittest.TestCase):
    """Exact-threshold behaviour, including the rounding regression."""

    def setUp(self):
        self.engine = HotColdWalletManagerEngine()

    def test_marginal_breach_is_not_rounded_away(self):
        # Regression: the ratio used to be rounded to 4dp *before* comparison, so
        # 250,040 / 1,000,000 = 0.25004 rounded to exactly 0.25 and failed the
        # strict `> 0.25` test -- a real breach of the cap reported as balanced.
        # Correct sweep = 250,040 - 150,000 = $100,040.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(hot_wallet_usd=250_040.0, cold_vault_usd=749_960.0)
        )
        self.assertEqual(report.rebalance_action, ACTION_SWEEP_TO_COLD)
        self.assertEqual(report.proposed_transfer_usd, 100_040.0)

    def test_marginal_shortfall_is_not_rounded_away(self):
        # 49,960 / 1,000,000 = 0.04996, which used to round up to exactly 0.05 and
        # miss the refill. Correct refill = 150,000 - 49,960 = $100,040.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(hot_wallet_usd=49_960.0, cold_vault_usd=950_040.0)
        )
        self.assertEqual(report.rebalance_action, ACTION_REFILL_HOT_FROM_COLD)
        self.assertEqual(report.proposed_transfer_usd, 100_040.0)

    def test_exactly_at_max_threshold_holds(self):
        # The band is documented as a strict `>` trigger: 25.0% exactly is not a breach.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(hot_wallet_usd=250_000.0, cold_vault_usd=750_000.0)
        )
        self.assertEqual(report.rebalance_action, ACTION_HOLD_BALANCES)

    def test_exactly_at_min_threshold_holds(self):
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(hot_wallet_usd=50_000.0, cold_vault_usd=950_000.0)
        )
        self.assertEqual(report.rebalance_action, ACTION_HOLD_BALANCES)


class TestInputValidation(unittest.TestCase):
    """Malformed balances must fail loudly, never resolve to a 'balanced' verdict."""

    def setUp(self):
        self.engine = HotColdWalletManagerEngine()

    def test_nan_balance_raises_instead_of_reporting_balanced(self):
        # Regression: NaN compares False against every threshold, so the engine used
        # to fall through to HOLD_BALANCES / PORTFOLIO_BALANCED on corrupt input.
        with self.assertRaises(HotColdWalletError):
            self.engine.audit_and_rebalance_treasury(
                WalletBalances(hot_wallet_usd=float("nan"), cold_vault_usd=850_000.0)
            )

    def test_infinite_balance_raises(self):
        with self.assertRaises(HotColdWalletError):
            self.engine.audit_and_rebalance_treasury(
                WalletBalances(hot_wallet_usd=float("inf"), cold_vault_usd=850_000.0)
            )

    def test_negative_balance_raises_instead_of_proposing_a_transfer(self):
        # Regression: hot = -50,000 produced a -5% ratio and a confident $200,000
        # refill proposal built on an impossible balance.
        with self.assertRaises(HotColdWalletError):
            self.engine.audit_and_rebalance_treasury(
                WalletBalances(hot_wallet_usd=-50_000.0, cold_vault_usd=1_050_000.0)
            )

    def test_negative_pending_transfer_raises(self):
        with self.assertRaises(HotColdWalletError):
            self.engine.audit_and_rebalance_treasury(
                WalletBalances(
                    hot_wallet_usd=150_000.0,
                    cold_vault_usd=850_000.0,
                    pending_transfer_to_cold_usd=-1.0,
                )
            )

    def test_summed_overflow_raises(self):
        # Each balance is individually finite, but the total overflows to inf, which
        # drives every ratio to 0.0 and used to propose an absurd $1e308 refill.
        with self.assertRaises(HotColdWalletError):
            self.engine.audit_and_rebalance_treasury(
                WalletBalances(hot_wallet_usd=1e308, cold_vault_usd=1e308)
            )

    def test_pending_sweep_exceeding_hot_balance_raises(self):
        # A balance feed that already debited the broadcast sweep, plus the same
        # amount passed as pending, double-counts it: effective hot collapses to 0
        # and the engine used to propose a large spurious refill.
        with self.assertRaises(HotColdWalletError):
            self.engine.audit_and_rebalance_treasury(
                WalletBalances(
                    hot_wallet_usd=100_000.0,
                    cold_vault_usd=900_000.0,
                    pending_transfer_to_cold_usd=500_000.0,
                )
            )

    def test_pending_refill_exceeding_cold_balance_raises(self):
        # The mirror case: effective hot inflates past the cap and the engine used
        # to propose a sweep on the strength of money the vault does not hold.
        with self.assertRaises(HotColdWalletError):
            self.engine.audit_and_rebalance_treasury(
                WalletBalances(
                    hot_wallet_usd=10_000.0,
                    cold_vault_usd=20_000.0,
                    warm_buffer_usd=970_000.0,
                    pending_transfer_to_hot_usd=900_000.0,
                )
            )

    def test_empty_treasury_raises(self):
        with self.assertRaises(HotColdWalletError):
            self.engine.audit_and_rebalance_treasury(
                WalletBalances(hot_wallet_usd=0.0, cold_vault_usd=0.0)
            )

    def test_error_is_a_valueerror_for_backward_compatibility(self):
        with self.assertRaises(ValueError):
            self.engine.audit_and_rebalance_treasury(
                WalletBalances(hot_wallet_usd=0.0, cold_vault_usd=0.0)
            )


class TestEngineConfiguration(unittest.TestCase):
    """A policy band that cannot converge must be rejected at construction."""

    def test_target_above_max_threshold_raises(self):
        # Regression: target 30% with a 25% cap produced a "sweep" that left the
        # treasury at 30% -- still in breach of the very cap that triggered it.
        with self.assertRaises(HotColdWalletError):
            HotColdWalletManagerEngine(
                target_hot_ratio=0.30, max_hot_ratio_threshold=0.25, min_hot_ratio_threshold=0.05
            )

    def test_target_below_min_threshold_raises(self):
        with self.assertRaises(HotColdWalletError):
            HotColdWalletManagerEngine(
                target_hot_ratio=0.02, max_hot_ratio_threshold=0.25, min_hot_ratio_threshold=0.05
            )

    def test_target_equal_to_max_threshold_raises(self):
        with self.assertRaises(HotColdWalletError):
            HotColdWalletManagerEngine(
                target_hot_ratio=0.25, max_hot_ratio_threshold=0.25, min_hot_ratio_threshold=0.05
            )

    def test_ratio_outside_unit_interval_raises(self):
        with self.assertRaises(HotColdWalletError):
            HotColdWalletManagerEngine(target_hot_ratio=1.5)

    def test_nan_threshold_raises(self):
        with self.assertRaises(HotColdWalletError):
            HotColdWalletManagerEngine(max_hot_ratio_threshold=float("nan"))


class TestPendingTransferIdempotency(unittest.TestCase):
    """Repeated audits must not stack duplicate transfers."""

    def setUp(self):
        self.engine = HotColdWalletManagerEngine()

    def test_in_flight_sweep_is_not_re_proposed(self):
        # Regression: a scheduled audit re-reading an unsettled hot balance used to
        # propose the same $150,000 sweep on every run. Netting the in-flight
        # transfer leaves effective hot at 300k - 150k = 150k = 15%, i.e. on target.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=300_000.0,
                cold_vault_usd=700_000.0,
                pending_transfer_to_cold_usd=150_000.0,
            )
        )
        self.assertEqual(report.rebalance_action, ACTION_HOLD_BALANCES)
        self.assertEqual(report.proposed_transfer_usd, 0.0)
        # The observed ratio is still reported for the audit trail.
        self.assertEqual(report.current_hot_ratio, 0.30)
        self.assertEqual(report.effective_hot_ratio, 0.15)

    def test_partial_in_flight_sweep_yields_incremental_top_up(self):
        # Effective hot = 300,000 - 40,000 = 260,000 = 26% > 25%.
        # Incremental sweep = 260,000 - 150,000 = $110,000, so the two transfers
        # together move exactly the $150,000 a single full sweep would have.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=300_000.0,
                cold_vault_usd=700_000.0,
                pending_transfer_to_cold_usd=40_000.0,
            )
        )
        self.assertEqual(report.rebalance_action, ACTION_SWEEP_TO_COLD)
        self.assertEqual(report.proposed_transfer_usd, 110_000.0)

    def test_in_flight_refill_is_not_re_proposed(self):
        # Effective hot = 30,000 + 120,000 = 150,000 = 15%, already on target.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=30_000.0,
                cold_vault_usd=970_000.0,
                pending_transfer_to_hot_usd=120_000.0,
            )
        )
        self.assertEqual(report.rebalance_action, ACTION_HOLD_BALANCES)
        self.assertEqual(report.proposed_transfer_usd, 0.0)


class TestTransferFunding(unittest.TestCase):
    """A proposal must never exceed what the funding wallet actually holds."""

    def setUp(self):
        self.engine = HotColdWalletManagerEngine()

    def test_refill_capped_at_available_cold_vault(self):
        # Regression: the engine proposed the full $120,000 refill from a vault
        # holding $20,000 -- an unfundable instruction to an automated executor.
        # Total = 30k + 20k + 950k = 1M, hot 3% < 5%, requested 120k, available 20k.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=30_000.0, cold_vault_usd=20_000.0, warm_buffer_usd=950_000.0
            )
        )
        self.assertEqual(report.rebalance_action, ACTION_REFILL_HOT_FROM_COLD)
        self.assertEqual(report.proposed_transfer_usd, 20_000.0)
        self.assertFalse(report.is_transfer_fully_fundable)

    def test_capped_proposal_never_exceeds_availability(self):
        # Amounts are floored to whole cents, so a capped proposal can never round
        # up past the balance it was capped against.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=30_000.0,
                cold_vault_usd=20_000.006,
                warm_buffer_usd=949_999.994,
            )
        )
        self.assertEqual(report.rebalance_action, ACTION_REFILL_HOT_FROM_COLD)
        self.assertLessEqual(report.proposed_transfer_usd, 20_000.006)
        self.assertEqual(report.proposed_transfer_usd, 20_000.0)

    def test_refill_from_empty_vault_proposes_nothing_but_still_flags(self):
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=30_000.0, cold_vault_usd=0.0, warm_buffer_usd=970_000.0
            )
        )
        self.assertEqual(report.rebalance_action, ACTION_REFILL_HOT_FROM_COLD)
        self.assertEqual(report.proposed_transfer_usd, 0.0)
        self.assertFalse(report.is_transfer_fully_fundable)
        self.assertEqual(report.status, STATUS_REBALANCE_REQUIRED)

    def test_cold_committed_to_an_in_flight_refill_is_not_double_committed(self):
        # Total = 10k + 30k + 960k = $1M. Effective hot = 10k + 5k in flight = 15k
        # = 1.5%, still under the 5% floor, so a further refill is warranted.
        # Requested = 150k - 15k = 135k, but the vault holds 30k of which 5k is
        # already committed to the in-flight refill => only $25,000 may be moved.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=10_000.0,
                cold_vault_usd=30_000.0,
                warm_buffer_usd=960_000.0,
                pending_transfer_to_hot_usd=5_000.0,
            )
        )
        self.assertEqual(report.rebalance_action, ACTION_REFILL_HOT_FROM_COLD)
        self.assertEqual(report.proposed_transfer_usd, 25_000.0)
        self.assertFalse(report.is_transfer_fully_fundable)

    def test_in_flight_refill_lifting_hot_above_the_floor_stops_further_refills(self):
        # Effective hot = 30k + 45k = 75k = 7.5%, above the 5% floor: the in-flight
        # transfer has already resolved the shortfall, so nothing more is proposed.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=30_000.0,
                cold_vault_usd=50_000.0,
                warm_buffer_usd=920_000.0,
                pending_transfer_to_hot_usd=45_000.0,
            )
        )
        self.assertEqual(report.rebalance_action, ACTION_HOLD_BALANCES)
        self.assertEqual(report.proposed_transfer_usd, 0.0)


class TestApiKeySecurityAudit(unittest.TestCase):
    """Every fund-moving permission is a critical finding, not just withdrawals."""

    def setUp(self):
        self.engine = HotColdWalletManagerEngine()

    def test_critical_alert_when_withdrawal_enabled(self):
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=150_000.0,
                cold_vault_usd=850_000.0,
                api_key_withdraw_permission_enabled=True,
            )
        )
        self.assertEqual(report.status, STATUS_SECURITY_ALERT)
        self.assertEqual(report.rebalance_action, ACTION_SECURITY_ALERT)
        self.assertFalse(report.is_api_key_secure)
        self.assertEqual(report.proposed_transfer_usd, 0.0)

    def test_universal_transfer_alone_is_a_critical_finding(self):
        # Regression: a key with withdrawals off but permitsUniversalTransfer on can
        # still move funds, yet used to be reported as `is_api_key_secure=True`.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=150_000.0,
                cold_vault_usd=850_000.0,
                api_key_universal_transfer_enabled=True,
            )
        )
        self.assertEqual(report.status, STATUS_SECURITY_ALERT)
        self.assertFalse(report.is_api_key_secure)
        self.assertTrue(
            any("permitsUniversalTransfer" in f for f in report.security_findings)
        )

    def test_internal_transfer_alone_is_a_critical_finding(self):
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=150_000.0,
                cold_vault_usd=850_000.0,
                api_key_internal_transfer_enabled=True,
            )
        )
        self.assertEqual(report.status, STATUS_SECURITY_ALERT)
        self.assertFalse(report.is_api_key_secure)

    def test_security_alert_still_reports_the_observed_ratio(self):
        # The halt must not blind the operator to a concurrent exposure breach.
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=400_000.0,
                cold_vault_usd=600_000.0,
                api_key_withdraw_permission_enabled=True,
            )
        )
        self.assertEqual(report.current_hot_ratio, 0.40)
        self.assertEqual(report.status, STATUS_SECURITY_ALERT)

    def test_unassessed_ip_restriction_is_flagged_but_not_critical(self):
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(hot_wallet_usd=150_000.0, cold_vault_usd=850_000.0)
        )
        self.assertTrue(report.is_api_key_secure)
        self.assertEqual(report.status, STATUS_BALANCED)
        self.assertTrue(any("IP restriction not assessed" in f for f in report.security_findings))

    def test_confirmed_ip_restriction_produces_no_finding(self):
        report = self.engine.audit_and_rebalance_treasury(
            WalletBalances(
                hot_wallet_usd=150_000.0, cold_vault_usd=850_000.0, api_key_ip_restricted=True
            )
        )
        self.assertEqual(report.security_findings, [])


class TestRegulatoryHotCap(unittest.TestCase):
    """A mandated ceiling binds ahead of the engine's own operating band."""

    def test_hong_kong_two_percent_cap_escalates_distinctly(self):
        # SFC VATP Guidelines cap client assets in hot storage at 2%.
        # Total $1,000,000, hot $30,000 = 3% > 2%. Effective target = min(1%, 2%) = 1%
        # => target hot $10,000, sweep = 30,000 - 10,000 = $20,000, landing at 1%.
        engine = HotColdWalletManagerEngine(
            target_hot_ratio=0.01,
            max_hot_ratio_threshold=0.05,
            min_hot_ratio_threshold=0.005,
            regulatory_max_hot_ratio=0.02,
        )
        report = engine.audit_and_rebalance_treasury(
            WalletBalances(hot_wallet_usd=30_000.0, cold_vault_usd=970_000.0)
        )
        self.assertEqual(report.status, STATUS_REGULATORY_BREACH)
        self.assertEqual(report.rebalance_action, ACTION_SWEEP_TO_COLD)
        self.assertEqual(report.proposed_transfer_usd, 20_000.0)
        self.assertEqual(report.regulatory_max_hot_ratio, 0.02)

    def test_breach_of_engine_band_only_is_not_a_regulatory_breach(self):
        # Japan's 5% cap with a 3% operating target. Hot 4% breaches neither, but
        # a 4.5% hot ratio would sit above target and still under the cap.
        engine = HotColdWalletManagerEngine(
            target_hot_ratio=0.03,
            max_hot_ratio_threshold=0.04,
            min_hot_ratio_threshold=0.01,
            regulatory_max_hot_ratio=0.05,
        )
        report = engine.audit_and_rebalance_treasury(
            WalletBalances(hot_wallet_usd=45_000.0, cold_vault_usd=955_000.0)
        )
        # 4.5% > 4% engine trigger but < 5% mandated cap: ordinary rebalance.
        self.assertEqual(report.status, STATUS_REBALANCE_REQUIRED)
        self.assertEqual(report.rebalance_action, ACTION_SWEEP_TO_COLD)
        # Target = min(3%, 5%) = 3% => 45,000 - 30,000 = $15,000.
        self.assertEqual(report.proposed_transfer_usd, 15_000.0)

    def test_cap_at_or_below_operating_floor_is_rejected(self):
        # A 5% floor under a 2% legal ceiling guarantees a permanent breach.
        with self.assertRaises(HotColdWalletError):
            HotColdWalletManagerEngine(regulatory_max_hot_ratio=0.02)


if __name__ == "__main__":
    unittest.main()

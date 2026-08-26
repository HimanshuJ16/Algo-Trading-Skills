import math
import unittest

from insurance_coverage_assessment_for_custodied_crypto import (
    CustodyInsuranceAssessmentEngine,
    CustodyInsuranceError,
    CustodyInsuranceSpec,
)


class TestCustodyInsuranceAssessmentEngine(unittest.TestCase):

    def setUp(self):
        self.engine = CustodyInsuranceAssessmentEngine()

    @staticmethod
    def _bitgo_style_spec(**overrides):
        """Firm Hot $2M / Cold $20M at a custodian with a $250M limit over a $1B pool."""
        params = dict(
            custodian_name="BitGo Custody",
            firm_hot_wallet_aum_usd=2_000_000.0,
            firm_cold_wallet_aum_usd=20_000_000.0,
            hot_crime_policy_limit_usd=5_000_000.0,
            cold_specie_policy_limit_usd=250_000_000.0,
            total_custodian_cold_aum_usd=1_000_000_000.0,
        )
        params.update(overrides)
        return CustodyInsuranceSpec(**params)

    # -- core pooled-dilution arithmetic -----------------------------------------

    def test_pooled_dilution_and_shortfall_calculation(self):
        # Hot: limit $5M > $2M AUM -> fully covered.
        # Cold: $250M / $1B pool = 25% dilution; $20M * 0.25 = $5M recovered.
        # Pooled total = $2M + $5M = $7M of $22M = 31.82%; shortfall $15M.
        report = self.engine.audit_custody_insurance(self._bitgo_style_spec())

        self.assertEqual(report.status, "PARTIALLY_INSURED_SHORTFALL")
        self.assertEqual(report.hot_wallet_coverage_pct, 100.0)
        self.assertEqual(report.cold_wallet_pro_rata_dilution_pct, 25.0)
        self.assertEqual(report.cold_wallet_effective_coverage_usd, 5_000_000.0)
        self.assertEqual(report.total_uninsured_shortfall_usd, 15_000_000.0)
        self.assertEqual(report.net_insured_coverage_pct, 31.82)

    def test_isolated_loss_scenario_brackets_the_pooled_scenario(self):
        # If only this firm is hit, no sharing applies: the $250M per-occurrence limit
        # covers all $20M cold plus $2M hot = $22M, i.e. the whole book.
        report = self.engine.audit_custody_insurance(self._bitgo_style_spec())

        self.assertEqual(report.isolated_loss_total_recovery_usd, 22_000_000.0)
        self.assertEqual(report.isolated_loss_net_coverage_pct, 100.0)
        self.assertEqual(report.isolated_loss_uninsured_shortfall_usd, 0.0)
        # Invariant: pooled <= isolated <= total AUM.
        pooled = report.total_firm_aum_usd - report.total_uninsured_shortfall_usd
        self.assertLessEqual(pooled, report.isolated_loss_total_recovery_usd)
        self.assertLessEqual(report.isolated_loss_total_recovery_usd, report.total_firm_aum_usd)

    def test_critical_hot_wallet_uninsured_warning(self):
        # Hot AUM $10M against a $2M limit -> 20% covered.
        spec = CustodyInsuranceSpec(
            custodian_name="Hot Wallet Custodian",
            firm_hot_wallet_aum_usd=10_000_000.0,
            firm_cold_wallet_aum_usd=0.0,
            hot_crime_policy_limit_usd=2_000_000.0,
            cold_specie_policy_limit_usd=100_000_000.0,
            total_custodian_cold_aum_usd=100_000_000.0,
        )
        report = self.engine.audit_custody_insurance(spec)

        self.assertEqual(report.status, "CRITICAL_HOT_WALLET_UNINSURED")
        self.assertEqual(report.hot_wallet_coverage_pct, 20.0)

    def test_dilution_factor_is_capped_at_one_when_limit_exceeds_pool(self):
        # A $500M limit over a $100M pool cannot cover more than 100% of any balance.
        spec = self._bitgo_style_spec(
            firm_hot_wallet_aum_usd=0.0,
            firm_cold_wallet_aum_usd=10_000_000.0,
            cold_specie_policy_limit_usd=500_000_000.0,
            total_custodian_cold_aum_usd=100_000_000.0,
        )
        report = self.engine.audit_custody_insurance(spec)

        self.assertEqual(report.cold_wallet_pro_rata_dilution_pct, 100.0)
        self.assertEqual(report.cold_wallet_effective_coverage_usd, 10_000_000.0)
        self.assertEqual(report.status, "FULLY_INSURED")

    # -- regression: crashes and silent corruption in the previous revision --------

    def test_hot_only_firm_with_unknown_cold_pool_does_not_crash(self):
        # Regression: a hot-only firm at a custodian whose cold pool is 0/unknown
        # previously raised ZeroDivisionError instead of producing an audit.
        spec = CustodyInsuranceSpec(
            custodian_name="Hot Only Custodian",
            firm_hot_wallet_aum_usd=5_000_000.0,
            firm_cold_wallet_aum_usd=0.0,
            hot_crime_policy_limit_usd=5_000_000.0,
            cold_specie_policy_limit_usd=0.0,
            total_custodian_cold_aum_usd=0.0,
        )
        report = self.engine.audit_custody_insurance(spec)

        self.assertEqual(report.status, "FULLY_INSURED")
        self.assertEqual(report.hot_wallet_coverage_pct, 100.0)
        self.assertEqual(report.total_uninsured_shortfall_usd, 0.0)

    def test_nan_aum_is_rejected_rather_than_reported_as_fully_insured(self):
        # Regression: NaN propagated through every comparison and yielded a
        # FULLY_INSURED verdict for a book of unknown size.
        spec = self._bitgo_style_spec(firm_hot_wallet_aum_usd=float("nan"))
        with self.assertRaises(CustodyInsuranceError):
            self.engine.audit_custody_insurance(spec)

    def test_infinite_policy_limit_is_rejected(self):
        spec = self._bitgo_style_spec(cold_specie_policy_limit_usd=math.inf)
        with self.assertRaises(CustodyInsuranceError):
            self.engine.audit_custody_insurance(spec)

    def test_negative_policy_limit_is_rejected(self):
        # Regression: a negative limit previously produced -50% "coverage" and a
        # shortfall larger than the firm's own book.
        spec = self._bitgo_style_spec(hot_crime_policy_limit_usd=-1_000_000.0)
        with self.assertRaises(CustodyInsuranceError):
            self.engine.audit_custody_insurance(spec)

    def test_firm_cold_aum_exceeding_custodian_pool_is_rejected(self):
        # Regression: the pool was silently clamped up to the firm's own balance,
        # reporting 100% dilution and full coverage from an impossible spec.
        spec = self._bitgo_style_spec(
            firm_cold_wallet_aum_usd=50_000_000.0,
            total_custodian_cold_aum_usd=1_000.0,
        )
        with self.assertRaises(CustodyInsuranceError):
            self.engine.audit_custody_insurance(spec)

    def test_shared_cold_limit_requires_a_positive_pool(self):
        spec = self._bitgo_style_spec(total_custodian_cold_aum_usd=0.0)
        with self.assertRaises(CustodyInsuranceError):
            self.engine.audit_custody_insurance(spec)

    def test_zero_total_firm_aum_is_rejected(self):
        spec = self._bitgo_style_spec(
            firm_hot_wallet_aum_usd=0.0, firm_cold_wallet_aum_usd=0.0
        )
        with self.assertRaises(CustodyInsuranceError):
            self.engine.audit_custody_insurance(spec)

    def test_blank_custodian_name_is_rejected(self):
        with self.assertRaises(CustodyInsuranceError):
            self.engine.audit_custody_insurance(self._bitgo_style_spec(custodian_name="   "))

    def test_sub_threshold_hot_coverage_is_not_rounded_into_a_clean_verdict(self):
        # 999,960 / 1,000,000 = 99.996%, which rounds to 100.0 for display. The
        # previous revision classified on the rounded value and returned
        # FULLY_INSURED; classification now runs on the unrounded ratio.
        spec = CustodyInsuranceSpec(
            custodian_name="Rounding Edge Custodian",
            firm_hot_wallet_aum_usd=1_000_000.0,
            firm_cold_wallet_aum_usd=0.0,
            hot_crime_policy_limit_usd=999_960.0,
            cold_specie_policy_limit_usd=0.0,
            total_custodian_cold_aum_usd=0.0,
        )
        report = self.engine.audit_custody_insurance(spec)

        self.assertEqual(report.hot_wallet_coverage_pct, 100.0)
        self.assertEqual(report.status, "CRITICAL_HOT_WALLET_UNINSURED")

    def test_exactly_full_hot_coverage_is_not_flagged_critical(self):
        spec = CustodyInsuranceSpec(
            custodian_name="Exact Match Custodian",
            firm_hot_wallet_aum_usd=1_000_000.0,
            firm_cold_wallet_aum_usd=0.0,
            hot_crime_policy_limit_usd=1_000_000.0,
            cold_specie_policy_limit_usd=0.0,
            total_custodian_cold_aum_usd=0.0,
        )
        report = self.engine.audit_custody_insurance(spec)

        self.assertEqual(report.hot_wallet_coverage_pct, 100.0)
        self.assertEqual(report.status, "FULLY_INSURED")

    # -- deductibles ---------------------------------------------------------------

    def test_deductible_reduces_recoverable_capital(self):
        # A $1M retention against a $2M hot book leaves $1M recoverable -> 50%.
        spec = self._bitgo_style_spec(
            firm_cold_wallet_aum_usd=0.0,
            hot_policy_deductible_usd=1_000_000.0,
        )
        report = self.engine.audit_custody_insurance(spec)

        self.assertEqual(report.hot_wallet_coverage_pct, 50.0)
        self.assertEqual(report.status, "CRITICAL_HOT_WALLET_UNINSURED")
        self.assertEqual(report.isolated_loss_total_recovery_usd, 1_000_000.0)

    def test_deductible_exceeding_the_loss_yields_zero_recovery_not_a_negative(self):
        spec = self._bitgo_style_spec(
            firm_cold_wallet_aum_usd=0.0,
            hot_policy_deductible_usd=5_000_000.0,
        )
        report = self.engine.audit_custody_insurance(spec)

        self.assertEqual(report.hot_wallet_coverage_pct, 0.0)
        self.assertEqual(report.isolated_loss_total_recovery_usd, 0.0)
        self.assertEqual(report.total_uninsured_shortfall_usd, 2_000_000.0)

    def test_deductible_erodes_the_shared_limit_before_pro_rata_split(self):
        # Net proceeds ($250M - $50M) / $1B pool = 20% dilution; $20M * 0.20 = $4M.
        spec = self._bitgo_style_spec(
            firm_hot_wallet_aum_usd=0.0,
            cold_policy_deductible_usd=50_000_000.0,
        )
        report = self.engine.audit_custody_insurance(spec)

        self.assertEqual(report.cold_wallet_pro_rata_dilution_pct, 20.0)
        self.assertEqual(report.cold_wallet_effective_coverage_usd, 4_000_000.0)

    def test_retention_larger_than_the_balance_inverts_the_two_scenarios(self):
        # A $50M retention swallows a loss confined to this firm's $20M book entirely,
        # so the isolated recovery is $0. A pool-wide loss still erodes the retention at
        # the tower level and pays a pro-rata $4M. The inversion is real: a firm smaller
        # than its custodian's retention is uninsured against incidents affecting only it.
        report = self.engine.audit_custody_insurance(
            self._bitgo_style_spec(
                firm_hot_wallet_aum_usd=0.0,
                cold_policy_deductible_usd=50_000_000.0,
            )
        )

        self.assertEqual(report.isolated_loss_total_recovery_usd, 0.0)
        self.assertEqual(report.isolated_loss_uninsured_shortfall_usd, 20_000_000.0)
        self.assertEqual(report.cold_wallet_effective_coverage_usd, 4_000_000.0)
        self.assertGreater(
            report.cold_wallet_effective_coverage_usd,
            report.isolated_loss_total_recovery_usd,
        )

    # -- dedicated limits and hot-tier dilution ------------------------------------

    def test_dedicated_cold_limit_is_not_diluted_pro_rata(self):
        # Excess specie naming the firm as dedicated loss payee: the $250M limit is
        # reserved, so all $20M is recoverable rather than the pooled $5M.
        spec = self._bitgo_style_spec(
            firm_hot_wallet_aum_usd=0.0,
            cold_limit_is_dedicated_to_firm=True,
        )
        report = self.engine.audit_custody_insurance(spec)

        self.assertEqual(report.cold_wallet_pro_rata_dilution_pct, 100.0)
        self.assertEqual(report.cold_wallet_effective_coverage_usd, 20_000_000.0)
        self.assertEqual(report.status, "FULLY_INSURED")

        # The same spec on a shared limit recovers a quarter of that.
        shared = self.engine.audit_custody_insurance(
            self._bitgo_style_spec(firm_hot_wallet_aum_usd=0.0)
        )
        self.assertEqual(shared.cold_wallet_effective_coverage_usd, 5_000_000.0)
        self.assertEqual(shared.status, "PARTIALLY_INSURED_SHORTFALL")

    def test_hot_pool_dilutes_the_hot_tier_when_supplied(self):
        # $100M hot limit over a $1B hot pool = 10%; $10M * 0.10 = $1M recoverable,
        # against $10M that looks fully covered on the headline limit alone.
        spec = CustodyInsuranceSpec(
            custodian_name="Omnibus Hot Custodian",
            firm_hot_wallet_aum_usd=10_000_000.0,
            firm_cold_wallet_aum_usd=0.0,
            hot_crime_policy_limit_usd=100_000_000.0,
            cold_specie_policy_limit_usd=0.0,
            total_custodian_cold_aum_usd=0.0,
            total_custodian_hot_aum_usd=1_000_000_000.0,
        )
        report = self.engine.audit_custody_insurance(spec)

        self.assertEqual(report.hot_wallet_pro_rata_dilution_pct, 10.0)
        self.assertEqual(report.hot_wallet_coverage_pct, 10.0)
        self.assertEqual(report.status, "CRITICAL_HOT_WALLET_UNINSURED")
        # Isolated loss still recovers the full $10M from the $100M limit.
        self.assertEqual(report.isolated_loss_total_recovery_usd, 10_000_000.0)

    def test_hot_tier_is_undiluted_and_disclosed_when_pool_is_unknown(self):
        report = self.engine.audit_custody_insurance(
            self._bitgo_style_spec(firm_cold_wallet_aum_usd=0.0)
        )

        self.assertEqual(report.hot_wallet_pro_rata_dilution_pct, 100.0)
        self.assertTrue(
            any("total_custodian_hot_aum_usd not supplied" in a for a in report.assumptions),
            "an undiluted hot tier must be disclosed as an optimistic assumption",
        )

    def test_firm_hot_aum_exceeding_custodian_hot_pool_is_rejected(self):
        spec = self._bitgo_style_spec(total_custodian_hot_aum_usd=1_000.0)
        with self.assertRaises(CustodyInsuranceError):
            self.engine.audit_custody_insurance(spec)

    # -- disclosure and configuration ----------------------------------------------

    def test_every_report_discloses_the_insolvency_and_named_insured_limits(self):
        report = self.engine.audit_custody_insurance(self._bitgo_style_spec())
        joined = " ".join(report.assumptions)

        self.assertIn("insolvency", joined)
        self.assertIn("loss-payee", joined)
        self.assertTrue(any("independent towers" in a for a in report.assumptions))

    def test_thresholds_are_configurable(self):
        # A firm accepting 30% net pooled coverage sees the same book as adequate.
        engine = CustodyInsuranceAssessmentEngine(min_net_coverage_ratio=0.30)
        report = engine.audit_custody_insurance(self._bitgo_style_spec())

        self.assertEqual(report.net_insured_coverage_pct, 31.82)
        self.assertEqual(report.status, "FULLY_INSURED")

    def test_invalid_engine_thresholds_are_rejected(self):
        for kwargs in ({"min_hot_coverage_ratio": 1.5}, {"min_net_coverage_ratio": -0.1}):
            with self.subTest(**kwargs):
                with self.assertRaises(CustodyInsuranceError):
                    CustodyInsuranceAssessmentEngine(**kwargs)


if __name__ == "__main__":
    unittest.main()

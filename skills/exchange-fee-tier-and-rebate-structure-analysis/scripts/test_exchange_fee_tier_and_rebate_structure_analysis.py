"""
Tests for exchange-fee-tier-and-rebate-structure-analysis.

Every expected USD figure below is derived by hand in the comment above its
assertion rather than by re-running the implementation's own expression, so a
sign-flip or a mis-tiered rate fails the test instead of propagating through it.
"""
import unittest

from exchange_fee_tier_and_rebate_structure_analysis import (
    REG_NMS_610C_ACCESS_FEE_CAP_USD,
    REG_NMS_610C_AMENDED_CAP_USD,
    ExchangeFeeTierAnalyzerEngine,
    FeeScheduleError,
    FeeTierDefinition,
    TierQualificationBasis,
    VenueVolumeSummary,
    check_reg_nms_access_fee_cap,
)


def maker_taker_tiers():
    """Two-tier maker-taker schedule. Rates: negative = rebate, positive = fee."""
    return [
        FeeTierDefinition("Tier 1", 0, maker_rate_per_share=-0.0020,
                          taker_rate_per_share=0.0030),
        FeeTierDefinition("Tier 2 VIP", 10_000_000, maker_rate_per_share=-0.0024,
                          taker_rate_per_share=0.0025),
    ]


class TestNetCostArithmetic(unittest.TestCase):
    """Signed net execution cost on a maker-taker venue."""

    def setUp(self):
        self.engine = ExchangeFeeTierAnalyzerEngine(
            "NASDAQ_EQUITIES", "MAKER_TAKER", maker_taker_tiers(),
            qualification_basis=TierQualificationBasis.ROLLING_CURRENT,
        )

    def test_tier1_net_rebate_capture(self):
        # 5,000,000 maker @ -$0.0020 = -$10,000.00 (credit)
        # 3,000,000 taker @ +$0.0030 = +$9,000.00  (charge)
        # net = -$1,000.00 -> net rebate capture
        # effective = -1000 / 8,000,000 = -$0.000125/sh
        summary = VenueVolumeSummary(
            "NASDAQ_EQUITIES", "MAKER_TAKER",
            rolling_30d_maker_volume_shares=5_000_000,
            rolling_30d_taker_volume_shares=3_000_000,
        )
        report = self.engine.analyze_fee_tier_and_rebates(summary)

        self.assertEqual(report.active_tier_name, "Tier 1")
        self.assertEqual(report.next_tier_name, "Tier 2 VIP")
        self.assertAlmostEqual(report.maker_side_cost_usd, -10_000.0, places=2)
        self.assertAlmostEqual(report.taker_side_cost_usd, 9_000.0, places=2)
        self.assertAlmostEqual(report.net_transaction_cost_usd, -1_000.0, places=2)
        self.assertAlmostEqual(report.gross_taker_fees_usd, 9_000.0, places=2)
        self.assertAlmostEqual(report.gross_maker_rebates_usd, 10_000.0, places=2)
        self.assertAlmostEqual(report.effective_cost_per_share, -0.000125, places=6)
        self.assertEqual(report.volume_needed_for_next_tier_shares, 2_000_000)

    def test_zero_priced_volume_does_not_divide_by_zero(self):
        summary = VenueVolumeSummary(
            "NASDAQ_EQUITIES", "MAKER_TAKER",
            rolling_30d_maker_volume_shares=0,
            rolling_30d_taker_volume_shares=0,
        )
        report = self.engine.analyze_fee_tier_and_rebates(summary)
        self.assertEqual(report.effective_cost_per_share, 0.0)
        self.assertEqual(report.net_transaction_cost_usd, 0.0)

    def test_threshold_is_inclusive_at_the_exact_boundary(self):
        # Exactly 10,000,000 qualifying shares must reach Tier 2 (threshold is >=).
        summary = VenueVolumeSummary(
            "NASDAQ_EQUITIES", "MAKER_TAKER",
            rolling_30d_maker_volume_shares=6_000_000,
            rolling_30d_taker_volume_shares=4_000_000,
        )
        report = self.engine.analyze_fee_tier_and_rebates(summary)
        self.assertEqual(report.active_tier_name, "Tier 2 VIP")
        self.assertIsNone(report.next_tier_name)
        self.assertEqual(report.volume_needed_for_next_tier_shares, 0)

    def test_one_share_below_threshold_stays_in_tier1(self):
        summary = VenueVolumeSummary(
            "NASDAQ_EQUITIES", "MAKER_TAKER",
            rolling_30d_maker_volume_shares=6_000_000,
            rolling_30d_taker_volume_shares=3_999_999,
        )
        report = self.engine.analyze_fee_tier_and_rebates(summary)
        self.assertEqual(report.active_tier_name, "Tier 1")
        self.assertEqual(report.volume_needed_for_next_tier_shares, 1)


class TestRule610dQualificationBasis(unittest.TestCase):
    """
    Reg NMS Rule 610(d): on US NMS stocks the tier applied to a fill is fixed by a
    completed prior period, so volume traded now cannot reprice fills now.
    """

    def setUp(self):
        self.prior = ExchangeFeeTierAnalyzerEngine(
            "CBOE_EDGX", "MAKER_TAKER", maker_taker_tiers(),
            qualification_basis=TierQualificationBasis.PRIOR_PERIOD,
        )

    def test_prior_period_requires_explicit_qualifying_volume(self):
        # The priced volume must NOT be silently substituted for the prior period's.
        summary = VenueVolumeSummary(
            "CBOE_EDGX", "MAKER_TAKER",
            rolling_30d_maker_volume_shares=5_000_000,
            rolling_30d_taker_volume_shares=3_000_000,
        )
        with self.assertRaises(FeeScheduleError) as ctx:
            self.prior.analyze_fee_tier_and_rebates(summary)
        self.assertIn("610(d)", str(ctx.exception))

    def test_tier_comes_from_prior_period_not_from_priced_volume(self):
        # Regression against current-month tiering: only 2,000,000 shares are being
        # priced, but the prior month did 12,000,000, so Tier 2 VIP rates apply.
        # 1,000,000 maker @ -$0.0024 = -$2,400.00
        # 1,000,000 taker @ +$0.0025 = +$2,500.00
        # net = +$100.00
        summary = VenueVolumeSummary(
            "CBOE_EDGX", "MAKER_TAKER",
            rolling_30d_maker_volume_shares=1_000_000,
            rolling_30d_taker_volume_shares=1_000_000,
            qualifying_volume_shares=12_000_000,
        )
        report = self.prior.analyze_fee_tier_and_rebates(summary)

        self.assertEqual(report.active_tier_name, "Tier 2 VIP")
        self.assertEqual(report.qualifying_volume_shares, 12_000_000)
        self.assertEqual(report.total_volume_shares, 2_000_000)
        self.assertAlmostEqual(report.net_transaction_cost_usd, 100.0, places=2)
        self.assertEqual(
            report.tier_qualification_basis, TierQualificationBasis.PRIOR_PERIOD.value
        )

    def test_low_prior_volume_keeps_base_tier_despite_heavy_current_volume(self):
        # The mirror case: 20,000,000 shares traded now, but the prior period was
        # below the threshold, so Tier 1 rates still apply to every fill.
        summary = VenueVolumeSummary(
            "CBOE_EDGX", "MAKER_TAKER",
            rolling_30d_maker_volume_shares=12_000_000,
            rolling_30d_taker_volume_shares=8_000_000,
            qualifying_volume_shares=500_000,
        )
        report = self.prior.analyze_fee_tier_and_rebates(summary)
        # 12,000,000 @ -$0.0020 = -$24,000.00 ; 8,000,000 @ +$0.0030 = +$24,000.00
        self.assertEqual(report.active_tier_name, "Tier 1")
        self.assertAlmostEqual(report.net_transaction_cost_usd, 0.0, places=2)

    def test_prior_period_benefit_lands_in_the_next_period(self):
        # 5,000,000 maker / 3,000,000 taker priced; prior period 8,000,000 -> Tier 1.
        # current net              = -$1,000.00   (see TestNetCostArithmetic)
        # at Tier 2: 5M*-0.0024 + 3M*0.0025 = -$12,000 + $7,500 = -$4,500.00
        # gross savings            = -1,000 - (-4,500) = +$3,500.00
        # gap = 10,000,000 - 8,000,000 = 2,000,000 sh at the observed 62.5% maker mix
        #   -> 1,250,000 maker, 750,000 taker, billed at the CURRENT tier because
        #      today's volume cannot reprice today's fills:
        #      1,250,000*-0.0020 + 750,000*0.0030 = -$2,500 + $2,250 = -$250.00
        # net benefit = 3,500 - (-250) = +$3,750.00
        summary = VenueVolumeSummary(
            "CBOE_EDGX", "MAKER_TAKER",
            rolling_30d_maker_volume_shares=5_000_000,
            rolling_30d_taker_volume_shares=3_000_000,
            qualifying_volume_shares=8_000_000,
        )
        report = self.prior.analyze_fee_tier_and_rebates(summary)

        self.assertEqual(report.tier_jump_benefit_period, "NEXT_PERIOD")
        self.assertAlmostEqual(
            report.estimated_monthly_savings_at_next_tier_usd, 3_500.0, places=2
        )
        self.assertAlmostEqual(report.incremental_volume_cost_usd, -250.0, places=2)
        self.assertAlmostEqual(report.net_tier_jump_benefit_usd, 3_750.0, places=2)

    def test_rolling_current_benefit_lands_in_the_current_period(self):
        # Same numbers, rolling-window venue: the incremental 2,000,000 shares are
        # billed at the NEXT tier because crossing reprices the whole window.
        #   1,250,000*-0.0024 + 750,000*0.0025 = -$3,000 + $1,875 = -$1,125.00
        # net benefit = 3,500 - (-1,125) = +$4,625.00
        engine = ExchangeFeeTierAnalyzerEngine(
            "BINANCE_SPOT", "MAKER_TAKER", maker_taker_tiers(),
            qualification_basis=TierQualificationBasis.ROLLING_CURRENT,
        )
        summary = VenueVolumeSummary(
            "BINANCE_SPOT", "MAKER_TAKER",
            rolling_30d_maker_volume_shares=5_000_000,
            rolling_30d_taker_volume_shares=3_000_000,
        )
        report = engine.analyze_fee_tier_and_rebates(summary)

        self.assertEqual(report.tier_jump_benefit_period, "CURRENT_PERIOD")
        self.assertAlmostEqual(report.incremental_volume_cost_usd, -1_125.0, places=2)
        self.assertAlmostEqual(report.net_tier_jump_benefit_usd, 4_625.0, places=2)

    def test_qualifying_volume_equal_to_priced_volume_is_flagged(self):
        # The likeliest way to defeat the guard is to satisfy it by handing back the
        # priced volume, which reinstates the current-period tiering Rule 610(d)
        # prohibits. It can be coincidence, so it warns rather than raising.
        summary = VenueVolumeSummary(
            "CBOE_EDGX", "MAKER_TAKER",
            rolling_30d_maker_volume_shares=5_000_000,
            rolling_30d_taker_volume_shares=3_000_000,
            qualifying_volume_shares=8_000_000,
        )
        report = self.prior.analyze_fee_tier_and_rebates(summary)
        self.assertTrue(
            any("COMPLETED PRIOR" in w for w in report.warnings),
            f"expected a same-volume caveat, got {report.warnings}",
        )

    def test_distinct_qualifying_volume_is_not_flagged(self):
        summary = VenueVolumeSummary(
            "CBOE_EDGX", "MAKER_TAKER",
            rolling_30d_maker_volume_shares=5_000_000,
            rolling_30d_taker_volume_shares=3_000_000,
            qualifying_volume_shares=9_500_000,
        )
        report = self.prior.analyze_fee_tier_and_rebates(summary)
        self.assertFalse(any("COMPLETED PRIOR" in w for w in report.warnings))

    def test_qualification_basis_is_required_and_validated(self):
        with self.assertRaises(TypeError):
            ExchangeFeeTierAnalyzerEngine("V", "MAKER_TAKER", maker_taker_tiers())
        with self.assertRaises(FeeScheduleError):
            ExchangeFeeTierAnalyzerEngine(
                "V", "MAKER_TAKER", maker_taker_tiers(),
                qualification_basis="CURRENT_MONTH",
            )


class TestInvertedVenue(unittest.TestCase):
    """
    Inverted (taker-maker) venues charge makers and credit takers. Cboe BYX is a
    current example; Cboe EDGA is NOT -- it replaced its inverted model with
    maker-taker effective 1 Nov 2024.
    """

    def setUp(self):
        self.tiers = [
            FeeTierDefinition("BYX Base", 0, maker_rate_per_share=0.0020,
                              taker_rate_per_share=-0.0018),
        ]
        self.engine = ExchangeFeeTierAnalyzerEngine(
            "CBOE_BYX", "TAKER_MAKER", self.tiers,
            qualification_basis=TierQualificationBasis.ROLLING_CURRENT,
        )

    def test_inverted_signed_economics(self):
        # 4,000,000 maker @ +$0.0020 = +$8,000.00  (the maker PAYS on an inverted venue)
        # 6,000,000 taker @ -$0.0018 = -$10,800.00 (the taker is CREDITED)
        # net = -$2,800.00 ; effective = -2,800 / 10,000,000 = -$0.00028/sh
        summary = VenueVolumeSummary(
            "CBOE_BYX", "TAKER_MAKER",
            rolling_30d_maker_volume_shares=4_000_000,
            rolling_30d_taker_volume_shares=6_000_000,
        )
        report = self.engine.analyze_fee_tier_and_rebates(summary)

        self.assertAlmostEqual(report.maker_side_cost_usd, 8_000.0, places=2)
        self.assertAlmostEqual(report.taker_side_cost_usd, -10_800.0, places=2)
        self.assertAlmostEqual(report.net_transaction_cost_usd, -2_800.0, places=2)
        self.assertAlmostEqual(report.effective_cost_per_share, -0.00028, places=6)

    def test_maker_taker_named_fields_are_not_negative_on_an_inverted_venue(self):
        # Regression: a taker rebate must never surface as a negative "taker fee",
        # and the report must say so rather than leaving the reader to infer it.
        summary = VenueVolumeSummary(
            "CBOE_BYX", "TAKER_MAKER",
            rolling_30d_maker_volume_shares=4_000_000,
            rolling_30d_taker_volume_shares=6_000_000,
        )
        report = self.engine.analyze_fee_tier_and_rebates(summary)

        self.assertGreaterEqual(report.gross_taker_fees_usd, 0.0)
        self.assertGreaterEqual(report.gross_maker_rebates_usd, 0.0)
        self.assertEqual(report.gross_taker_fees_usd, 0.0)
        self.assertEqual(report.gross_maker_rebates_usd, 0.0)
        self.assertTrue(
            any("Inverted venue" in w for w in report.warnings),
            f"expected an inverted-venue caveat, got {report.warnings}",
        )

    def test_schedule_contradicting_declared_model_raises(self):
        # Declaring MAKER_TAKER while supplying an inverted schedule is the exact
        # failure this skill exists to catch: passive flow routed expecting a rebate
        # to a venue that in fact charges makers.
        with self.assertRaises(FeeScheduleError) as ctx:
            ExchangeFeeTierAnalyzerEngine(
                "MISLABELLED", "MAKER_TAKER", self.tiers,
                qualification_basis=TierQualificationBasis.ROLLING_CURRENT,
            )
        self.assertIn("inverted", str(ctx.exception).lower())

    def test_inverted_declaration_over_maker_taker_schedule_raises(self):
        with self.assertRaises(FeeScheduleError):
            ExchangeFeeTierAnalyzerEngine(
                "MISLABELLED", "TAKER_MAKER", maker_taker_tiers(),
                qualification_basis=TierQualificationBasis.ROLLING_CURRENT,
            )

    def test_maker_taker_tier_that_charges_makers_is_warned_not_silent(self):
        tiers = [
            FeeTierDefinition("No-rebate base", 0, maker_rate_per_share=0.0005,
                              taker_rate_per_share=0.0030),
        ]
        engine = ExchangeFeeTierAnalyzerEngine(
            "V", "MAKER_TAKER", tiers,
            qualification_basis=TierQualificationBasis.ROLLING_CURRENT,
        )
        report = engine.analyze_fee_tier_and_rebates(
            VenueVolumeSummary("V", "MAKER_TAKER", 1_000_000, 0)
        )
        self.assertTrue(
            any("CHARGES makers" in w for w in report.warnings),
            f"expected a maker-charge warning, got {report.warnings}",
        )
        # 1,000,000 @ +$0.0005 = +$500.00 charged, and no rebate reported.
        self.assertAlmostEqual(report.net_transaction_cost_usd, 500.0, places=2)
        self.assertEqual(report.gross_maker_rebates_usd, 0.0)


class TestTierJumpEconomics(unittest.TestCase):

    def test_a_worse_next_tier_reports_negative_savings(self):
        # Regression against clamping savings at zero: for a maker-heavy desk a
        # higher tier can be WORSE (smaller rebate), and hiding that inverts the
        # routing decision.
        # Tier 1: 8,000,000 maker @ -0.0030 = -$24,000 ; 1,000,000 taker @ +0.0030 = +$3,000
        #         net = -$21,000.00
        # Tier 2: 8,000,000 maker @ -0.0010 =  -$8,000 ; 1,000,000 taker @ +0.0029 = +$2,900
        #         net = -$5,100.00
        # gross savings = -21,000 - (-5,100) = -$15,900.00  (a LOSS of $15,900)
        tiers = [
            FeeTierDefinition("Tier 1", 0, maker_rate_per_share=-0.0030,
                              taker_rate_per_share=0.0030),
            FeeTierDefinition("Tier 2", 10_000_000, maker_rate_per_share=-0.0010,
                              taker_rate_per_share=0.0029),
        ]
        engine = ExchangeFeeTierAnalyzerEngine(
            "V", "MAKER_TAKER", tiers,
            qualification_basis=TierQualificationBasis.ROLLING_CURRENT,
        )
        report = engine.analyze_fee_tier_and_rebates(
            VenueVolumeSummary("V", "MAKER_TAKER", 8_000_000, 1_000_000)
        )

        self.assertAlmostEqual(report.net_transaction_cost_usd, -21_000.0, places=2)
        self.assertAlmostEqual(
            report.estimated_monthly_savings_at_next_tier_usd, -15_900.0, places=2
        )
        self.assertLess(report.net_tier_jump_benefit_usd, 0.0)
        self.assertTrue(
            any("not economic" in w for w in report.warnings),
            f"expected an uneconomic-jump warning, got {report.warnings}",
        )

    def test_incremental_mix_can_be_overridden(self):
        # Forcing the 2,000,000-share gap as 100% taker on a rolling venue:
        # 2,000,000 @ +$0.0025 (Tier 2) = +$5,000.00 incremental cost.
        # gross savings is unchanged at +$3,500.00, so net benefit = 3,500 - 5,000
        #   = -$1,500.00 and the jump stops being worth chasing.
        engine = ExchangeFeeTierAnalyzerEngine(
            "V", "MAKER_TAKER", maker_taker_tiers(),
            qualification_basis=TierQualificationBasis.ROLLING_CURRENT,
        )
        report = engine.analyze_fee_tier_and_rebates(
            VenueVolumeSummary("V", "MAKER_TAKER", 5_000_000, 3_000_000),
            incremental_maker_fraction=0.0,
        )
        self.assertAlmostEqual(report.incremental_volume_cost_usd, 5_000.0, places=2)
        self.assertAlmostEqual(report.net_tier_jump_benefit_usd, -1_500.0, places=2)

    def test_invalid_incremental_mix_raises(self):
        engine = ExchangeFeeTierAnalyzerEngine(
            "V", "MAKER_TAKER", maker_taker_tiers(),
            qualification_basis=TierQualificationBasis.ROLLING_CURRENT,
        )
        with self.assertRaises(FeeScheduleError):
            engine.analyze_fee_tier_and_rebates(
                VenueVolumeSummary("V", "MAKER_TAKER", 5_000_000, 3_000_000),
                incremental_maker_fraction=1.5,
            )


class TestScheduleValidation(unittest.TestCase):

    def _engine(self, tiers):
        return ExchangeFeeTierAnalyzerEngine(
            "V", "MAKER_TAKER", tiers,
            qualification_basis=TierQualificationBasis.ROLLING_CURRENT,
        )

    def test_empty_schedule_raises_instead_of_index_error(self):
        with self.assertRaises(FeeScheduleError):
            self._engine([])

    def test_schedule_without_a_base_tier_raises(self):
        # Regression: a schedule whose lowest threshold is above zero previously
        # assigned the lowest tier to volume that did not qualify for it, and
        # reported no next tier at all.
        tiers = [
            FeeTierDefinition("Tier 1", 1_000_000, maker_rate_per_share=-0.0020,
                              taker_rate_per_share=0.0030),
            FeeTierDefinition("Tier 2", 10_000_000, maker_rate_per_share=-0.0024,
                              taker_rate_per_share=0.0025),
        ]
        with self.assertRaises(FeeScheduleError) as ctx:
            self._engine(tiers)
        self.assertIn("base tier", str(ctx.exception))

    def test_duplicate_thresholds_raise(self):
        tiers = maker_taker_tiers() + [
            FeeTierDefinition("Tier 2 dupe", 10_000_000, maker_rate_per_share=-0.0025,
                              taker_rate_per_share=0.0024),
        ]
        with self.assertRaises(FeeScheduleError):
            self._engine(tiers)

    def test_unsorted_input_is_ordered_before_classification(self):
        engine = self._engine(list(reversed(maker_taker_tiers())))
        report = engine.analyze_fee_tier_and_rebates(
            VenueVolumeSummary("V", "MAKER_TAKER", 5_000_000, 3_000_000)
        )
        self.assertEqual(report.active_tier_name, "Tier 1")

    def test_non_finite_rate_raises(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(FeeScheduleError):
                FeeTierDefinition("T", 0, maker_rate_per_share=bad,
                                  taker_rate_per_share=0.0030)

    def test_negative_threshold_raises(self):
        with self.assertRaises(FeeScheduleError):
            FeeTierDefinition("T", -1, maker_rate_per_share=-0.002,
                              taker_rate_per_share=0.003)

    def test_negative_volume_raises(self):
        with self.assertRaises(FeeScheduleError):
            VenueVolumeSummary("V", "MAKER_TAKER", -1, 1_000)
        with self.assertRaises(FeeScheduleError):
            VenueVolumeSummary("V", "MAKER_TAKER", 1_000, 1_000,
                               qualifying_volume_shares=-5)

    def test_invalid_pricing_model_raises(self):
        with self.assertRaises(FeeScheduleError):
            ExchangeFeeTierAnalyzerEngine(
                "V", "MAKER-TAKER-ISH", maker_taker_tiers(),
                qualification_basis=TierQualificationBasis.ROLLING_CURRENT,
            )

    def test_summary_model_contradicting_engine_raises(self):
        engine = self._engine(maker_taker_tiers())
        with self.assertRaises(FeeScheduleError):
            engine.analyze_fee_tier_and_rebates(
                VenueVolumeSummary("V", "TAKER_MAKER", 1_000, 1_000)
            )


class TestRegNmsAccessFeeCap(unittest.TestCase):
    """
    Rule 610(c) caps the access (taker) fee for US NMS stocks priced at or above
    $1.00. $0.0030/sh is the cap in force; the amended $0.0010/sh cap has a
    compliance date of the first business day of November 2027.
    """

    def test_cap_constants(self):
        self.assertEqual(REG_NMS_610C_ACCESS_FEE_CAP_USD, 0.0030)
        self.assertEqual(REG_NMS_610C_AMENDED_CAP_USD, 0.0010)

    def test_fee_at_the_cap_is_not_a_breach(self):
        # The cap is a ceiling, so exactly $0.0030 is compliant.
        self.assertEqual(check_reg_nms_access_fee_cap(maker_taker_tiers()), [])

    def test_fee_above_the_cap_is_flagged(self):
        tiers = [
            FeeTierDefinition("Tier 1", 0, maker_rate_per_share=-0.0020,
                              taker_rate_per_share=0.0035),
        ]
        breaches = check_reg_nms_access_fee_cap(tiers)
        self.assertEqual(len(breaches), 1)
        self.assertIn("Tier 1", breaches[0])

    def test_schedule_can_be_tested_against_the_amended_cap(self):
        # A $0.0030 taker fee is compliant today but breaches the amended cap.
        breaches = check_reg_nms_access_fee_cap(
            maker_taker_tiers(), cap_per_share=REG_NMS_610C_AMENDED_CAP_USD
        )
        self.assertEqual(len(breaches), 2)

    def test_invalid_cap_raises(self):
        with self.assertRaises(FeeScheduleError):
            check_reg_nms_access_fee_cap(maker_taker_tiers(), cap_per_share=0.0)


if __name__ == "__main__":
    unittest.main()

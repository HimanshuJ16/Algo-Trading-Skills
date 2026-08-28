"""Unit tests for the IRC Sec. 1256 contract tax engine.

Expected values are derived by hand from the statute (Sec. 1256(a)(2), (a)(3),
Sec. 1211(b), Sec. 1212(b), Sec. 1212(c)) and from the Instructions for Form 6781,
never by re-running the implementation's own expression. Where a figure also
appears in `currency-gain-loss-tax-treatment-for-forex-trading` (the $9,150 and
$3,790 loss benefits, the $555 MFS benefit) the two skills agree independently.
"""
import logging
import math
import unittest

from section_1256_contract_tax_treatment_us_futures import (
    DEFAULT_MAX_LONG_TERM_RATE,
    DEFAULT_MAX_SHORT_TERM_RATE,
    FILING_STATUS_MFS,
    FILING_STATUS_SINGLE,
    FORM_6781_LINE_8_DESTINATION,
    FORM_6781_LINE_9_DESTINATION,
    NET_INVESTMENT_INCOME_TAX_RATE,
    SECTION_1211B_LIMIT_MFS_USD,
    SECTION_1211B_LIMIT_USD,
    SECTION_1212C_CARRYBACK_YEARS,
    SECTION_1256_QUALIFYING_TYPES,
    Section1256ContractTaxTreatmentUsFuturesEngine,
    Section1256ContractType,
    Section1256Trade,
    is_section_1256_contract,
)

_MODULE_LOGGER = "section_1256_contract_tax_treatment_us_futures"


def setUpModule():
    """The engine warns by design on exclusions and caps; keep suite output quiet."""
    logging.getLogger(_MODULE_LOGGER).addHandler(logging.NullHandler())
    logging.getLogger(_MODULE_LOGGER).propagate = False


def futures_trade(trade_id="TRD_001", symbol="ES", realized=0.0, **kwargs):
    """A regulated futures contract, the archetypal Sec. 1256(b)(1)(A) position."""
    params = dict(
        contract_type=Section1256ContractType.REGULATED_FUTURES,
        realized_pnl_usd=realized,
    )
    params.update(kwargs)
    return Section1256Trade(trade_id=trade_id, symbol=symbol, **params)


class TestSixtyFortySplit(unittest.TestCase):
    """Sec. 1256(a)(3) character split and Form 6781 Part I line map."""

    def setUp(self):
        self.engine = Section1256ContractTaxTreatmentUsFuturesEngine()

    def test_gain_splits_60_40_and_quantifies_rate_advantage(self):
        # $100,000 on CME E-mini futures. Sec. 1256(a)(3): 40% short-term,
        # 60% long-term, regardless of holding period.
        #   line 9 long-term  = 100,000 x 0.60 = 60,000  -> 60,000 x 0.20 = 12,000
        #   line 8 short-term = 100,000 x 0.40 = 40,000  -> 40,000 x 0.37 = 14,800
        #   total 26,800 against 100,000 x 0.37 = 37,000 all-short-term
        self.engine.add_trade(futures_trade(realized=100000.0))
        summary = self.engine.generate_form_6781_summary()

        self.assertEqual(summary.net_section_1256_pnl_usd, 100000.0)
        self.assertEqual(summary.net_after_carryback_usd, 100000.0)
        self.assertEqual(summary.long_term_60_pct_usd, 60000.0)
        self.assertEqual(summary.short_term_40_pct_usd, 40000.0)
        self.assertEqual(summary.estimated_tax_usd, 26800.0)
        self.assertEqual(summary.estimated_tax_if_all_short_term_usd, 37000.0)
        self.assertEqual(summary.tax_savings_vs_short_term_usd, 10200.0)
        self.assertEqual(summary.blended_rate_applied, 0.268)
        self.assertEqual(summary.section_1256_trade_count, 1)
        self.assertEqual(summary.excluded_trade_count, 0)

    def test_line_8_and_line_9_carry_their_schedule_d_destinations(self):
        self.engine.add_trade(futures_trade(realized=1000.0))
        summary = self.engine.generate_form_6781_summary()
        self.assertEqual(summary.form_6781_line_8_destination, FORM_6781_LINE_8_DESTINATION)
        self.assertEqual(summary.form_6781_line_9_destination, FORM_6781_LINE_9_DESTINATION)
        self.assertIn("Schedule D (Form 1040) line 4", summary.form_6781_line_8_destination)
        self.assertIn("Schedule D (Form 1040) line 11", summary.form_6781_line_9_destination)

    def test_niit_raises_tax_but_leaves_the_60_40_saving_unchanged(self):
        # Sec. 1411 is character-blind: 3.8% lands on both scenarios, so the
        # saving stays 100,000 x 0.60 x (0.37 - 0.20) = 10,200.
        engine = Section1256ContractTaxTreatmentUsFuturesEngine(
            net_investment_income_tax_rate=NET_INVESTMENT_INCOME_TAX_RATE)
        engine.add_trade(futures_trade(realized=100000.0))
        summary = engine.generate_form_6781_summary()

        self.assertEqual(summary.estimated_tax_usd, 30600.0)  # 26,800 + 3,800
        self.assertEqual(summary.estimated_tax_if_all_short_term_usd, 40800.0)
        self.assertEqual(summary.tax_savings_vs_short_term_usd, 10200.0)

    def test_niit_omission_is_disclosed_rather_than_left_implicit(self):
        self.engine.add_trade(futures_trade(realized=100000.0))
        summary = self.engine.generate_form_6781_summary()
        self.assertTrue(any("1411" in w for w in summary.warnings))

    def test_losses_split_60_40_exactly_as_gains_do(self):
        # Sec. 1256(a)(3) does not distinguish gain from loss.
        self.engine.add_trade(futures_trade(realized=-50000.0))
        summary = self.engine.generate_form_6781_summary()
        self.assertEqual(summary.long_term_60_pct_usd, -30000.0)
        self.assertEqual(summary.short_term_40_pct_usd, -20000.0)
        self.assertEqual(summary.estimated_tax_usd, 0.0)


class TestYearEndMark(unittest.TestCase):
    """Sec. 1256(a)(1) mandatory mark and Sec. 1256(a)(2) proper adjustment."""

    def setUp(self):
        self.engine = Section1256ContractTaxTreatmentUsFuturesEngine()

    def test_open_contract_mark_joins_realized_pnl(self):
        # SPX broad-based index option: $20,000 realized + $30,000 year-end mark.
        self.engine.add_trade(Section1256Trade(
            trade_id="TRD_002",
            symbol="SPX",
            contract_type=Section1256ContractType.NONEQUITY_OPTION,
            realized_pnl_usd=20000.0,
            year_end_mark_pnl_usd=30000.0,
            is_open_at_year_end=True,
        ))
        summary = self.engine.generate_form_6781_summary()

        self.assertEqual(summary.total_realized_pnl_usd, 20000.0)
        self.assertEqual(summary.total_year_end_mark_pnl_usd, 30000.0)
        self.assertEqual(summary.net_section_1256_pnl_usd, 50000.0)
        self.assertEqual(summary.long_term_60_pct_usd, 30000.0)
        self.assertEqual(summary.short_term_40_pct_usd, 20000.0)

    def test_mark_on_a_position_flagged_closed_is_rejected_not_dropped(self):
        # Regression: silently ignoring the mark on a position whose
        # is_open_at_year_end flag was left at its default understated the year's
        # Sec. 1256 income by the whole mark.
        with self.assertRaises(ValueError) as ctx:
            self.engine.add_trade(futures_trade(
                realized=20000.0, year_end_mark_pnl_usd=30000.0))
        self.assertIn("is_open_at_year_end", str(ctx.exception))

    def test_prior_year_mark_is_removed_under_section_1256_a_2(self):
        # Contract opened in the prior year, marked at +$30,000 that 12/31, closed
        # this year with $50,000 of inception-to-date gain. Sec. 1256(a)(2)
        # requires "proper adjustment": this year reports 50,000 - 30,000 = 20,000,
        # not 50,000. LT 12,000 / ST 8,000.
        self.engine.add_trade(futures_trade(
            realized=50000.0, prior_year_end_cumulative_mark_usd=30000.0))
        summary = self.engine.generate_form_6781_summary()

        self.assertEqual(summary.total_realized_pnl_usd, 50000.0)
        self.assertEqual(summary.prior_year_mark_adjustment_usd, -30000.0)
        self.assertEqual(summary.net_section_1256_pnl_usd, 20000.0)
        self.assertEqual(summary.long_term_60_pct_usd, 12000.0)
        self.assertEqual(summary.short_term_40_pct_usd, 8000.0)
        self.assertTrue(any("1256(a)(2)" in w for w in summary.warnings))

    def test_contract_opened_this_year_needs_no_adjustment(self):
        self.engine.add_trade(futures_trade(realized=50000.0))
        summary = self.engine.generate_form_6781_summary()
        self.assertEqual(summary.prior_year_mark_adjustment_usd, 0.0)
        self.assertEqual(summary.net_section_1256_pnl_usd, 50000.0)
        self.assertFalse(any("1256(a)(2)" in w for w in summary.warnings))

    def test_prior_year_loss_mark_increases_this_years_reportable_gain(self):
        # Marked at -$10,000 last year, inception-to-date +$5,000 now:
        # 5,000 - (-10,000) = 15,000 reportable this year.
        self.engine.add_trade(futures_trade(
            realized=5000.0, prior_year_end_cumulative_mark_usd=-10000.0))
        summary = self.engine.generate_form_6781_summary()
        self.assertEqual(summary.prior_year_mark_adjustment_usd, 10000.0)
        self.assertEqual(summary.net_section_1256_pnl_usd, 15000.0)

    def test_open_position_with_a_zero_mark_is_flagged(self):
        self.engine.add_trade(futures_trade(realized=0.0, is_open_at_year_end=True))
        summary = self.engine.generate_form_6781_summary()
        self.assertTrue(any("LAST BUSINESS DAY" in w for w in summary.warnings))


class TestEligibilityPartitioning(unittest.TestCase):
    """Sec. 1256(b)(1) inclusions and Sec. 1256(b)(2) / (e) / (a)(4) exclusions."""

    def setUp(self):
        self.engine = Section1256ContractTaxTreatmentUsFuturesEngine()

    def test_qualifying_set_is_exactly_the_five_statutory_types(self):
        self.assertEqual(len(SECTION_1256_QUALIFYING_TYPES), 5)
        for member in (
            Section1256ContractType.REGULATED_FUTURES,
            Section1256ContractType.FOREIGN_CURRENCY_CONTRACT,
            Section1256ContractType.NONEQUITY_OPTION,
            Section1256ContractType.DEALER_EQUITY_OPTION,
            Section1256ContractType.DEALER_SECURITIES_FUTURES_CONTRACT,
        ):
            self.assertTrue(is_section_1256_contract(member))
        for member in (
            Section1256ContractType.EQUITY_OPTION,
            Section1256ContractType.SECURITIES_FUTURES_CONTRACT,
            Section1256ContractType.SWAP_OR_NOTIONAL_PRINCIPAL_CONTRACT,
            Section1256ContractType.OTHER_NON_SECTION_1256,
        ):
            self.assertFalse(is_section_1256_contract(member))

    def test_equity_option_is_excluded_but_still_reported(self):
        # An AAPL or QQQ option is an option on stock (Sec. 1256(g)(6)), so it is
        # an equity option, not a nonequity option. Excluding it from the 60/40
        # split must not make its P&L vanish from the report.
        self.engine.add_trade(Section1256Trade(
            trade_id="TRD_003",
            symbol="AAPL",
            contract_type=Section1256ContractType.EQUITY_OPTION,
            realized_pnl_usd=50000.0,
        ))
        summary = self.engine.generate_form_6781_summary()

        self.assertEqual(summary.net_section_1256_pnl_usd, 0.0)
        self.assertEqual(summary.excluded_non_section_1256_pnl_usd, 50000.0)
        self.assertEqual(summary.excluded_trade_count, 1)
        self.assertEqual(summary.section_1256_trade_count, 0)
        self.assertTrue(any("Form 8949" in w for w in summary.warnings))

    def test_swap_is_excluded_under_section_1256_b_2(self):
        self.engine.add_trade(Section1256Trade(
            trade_id="TRD_004",
            symbol="USD-SOFR-5Y",
            contract_type=Section1256ContractType.SWAP_OR_NOTIONAL_PRINCIPAL_CONTRACT,
            realized_pnl_usd=12000.0,
        ))
        summary = self.engine.generate_form_6781_summary()
        self.assertEqual(summary.net_section_1256_pnl_usd, 0.0)
        self.assertEqual(summary.excluded_non_section_1256_pnl_usd, 12000.0)

    def test_identified_hedge_is_routed_out_as_ordinary(self):
        # Sec. 1256(e): a properly identified hedge is not marked to market and
        # its gain or loss is ordinary, not 60/40.
        self.engine.add_trade(futures_trade(
            trade_id="HEDGE_1", symbol="ZC", realized=40000.0,
            is_identified_hedging_transaction=True))
        summary = self.engine.generate_form_6781_summary()

        self.assertEqual(summary.net_section_1256_pnl_usd, 0.0)
        self.assertEqual(summary.excluded_hedging_pnl_usd, 40000.0)
        self.assertEqual(summary.excluded_non_section_1256_pnl_usd, 0.0)
        self.assertTrue(any("ORDINARY" in w for w in summary.warnings))

    def test_mixed_straddle_leg_is_routed_out(self):
        # Sec. 1256(a)(4) disapplies Sec. 1092 only for an all-Sec.-1256 straddle.
        self.engine.add_trade(futures_trade(
            trade_id="MS_1", symbol="ES", realized=-25000.0,
            is_part_of_mixed_straddle=True))
        summary = self.engine.generate_form_6781_summary()

        self.assertEqual(summary.net_section_1256_pnl_usd, 0.0)
        self.assertEqual(summary.excluded_mixed_straddle_pnl_usd, -25000.0)
        self.assertTrue(any("Part II" in w for w in summary.warnings))

    def test_dealer_only_type_warns_about_dealer_status_and_se_tax(self):
        self.engine.add_trade(Section1256Trade(
            trade_id="DLR_1",
            symbol="AAPL",
            contract_type=Section1256ContractType.DEALER_EQUITY_OPTION,
            realized_pnl_usd=10000.0,
        ))
        summary = self.engine.generate_form_6781_summary()
        self.assertEqual(summary.net_section_1256_pnl_usd, 10000.0)
        self.assertTrue(any("1402(i)" in w for w in summary.warnings))

    def test_mixed_blotter_keeps_the_buckets_separate(self):
        self.engine.add_trade(futures_trade(trade_id="A", realized=100000.0))
        self.engine.add_trade(Section1256Trade(
            "B", "SPY", Section1256ContractType.EQUITY_OPTION, 20000.0))
        self.engine.add_trade(futures_trade(
            trade_id="C", symbol="CL", realized=5000.0,
            is_identified_hedging_transaction=True))
        summary = self.engine.generate_form_6781_summary()

        self.assertEqual(summary.net_section_1256_pnl_usd, 100000.0)
        self.assertEqual(summary.excluded_non_section_1256_pnl_usd, 20000.0)
        self.assertEqual(summary.excluded_hedging_pnl_usd, 5000.0)
        self.assertEqual(summary.section_1256_trade_count, 1)
        self.assertEqual(summary.excluded_trade_count, 2)


class TestLossWaterfall(unittest.TestCase):
    """Sec. 1212(c) carryback, then Sec. 1211(b) cap, then Sec. 1212(b) carryforward."""

    def setUp(self):
        self.engine = Section1256ContractTaxTreatmentUsFuturesEngine()
        self.engine.add_trade(futures_trade(realized=-50000.0))

    def test_carryback_period_is_three_years(self):
        self.assertEqual(SECTION_1212C_CARRYBACK_YEARS, 3)

    def test_loss_without_election_is_capped_but_not_forfeited(self):
        # $50,000 loss, single, no other capital gains, box D not checked:
        #   Sec. 1211(b) allows $3,000 against ordinary income
        #   Sec. 1212(b) carries $47,000 forward indefinitely
        #   benefit = 3,000 x 0.37 = 1,110
        summary = self.engine.generate_form_6781_summary()

        self.assertEqual(summary.net_section_1256_pnl_usd, -50000.0)
        self.assertEqual(summary.carryback_elected_usd, 0.0)
        self.assertEqual(summary.ordinary_income_deduction_usd, SECTION_1211B_LIMIT_USD)
        self.assertEqual(summary.capital_loss_carryforward_usd, 47000.0)
        self.assertEqual(summary.estimated_loss_tax_benefit_usd, 1110.0)
        self.assertTrue(any("deferred, not forfeited" in w for w in summary.warnings))

    def test_section_1212c_carryback_against_prior_section_1256_gains(self):
        # Net section 1256 contracts loss = 50,000 - 3,000 = 47,000 (Form 6781
        # box D, first prong). Prior Sec. 1256 gains cap it at 30,000.
        #   line 6 = 30,000 (positive number)
        #   line 7 = -50,000 + 30,000 = -20,000
        #   line 8 = -8,000, line 9 = -12,000
        #   remaining 20,000 -> 3,000 ordinary, 17,000 carried forward
        #   benefit = 30,000 x 0.268 + 3,000 x 0.37 = 8,040 + 1,110 = 9,150
        summary = self.engine.generate_form_6781_summary(
            elect_section_1212c_carryback=True,
            prior_section_1256_gains_usd=30000.0,
        )

        self.assertEqual(summary.net_section_1256_contracts_loss_usd, 47000.0)
        self.assertEqual(summary.carryback_elected_usd, 30000.0)
        self.assertEqual(summary.net_after_carryback_usd, -20000.0)
        self.assertEqual(summary.short_term_40_pct_usd, -8000.0)
        self.assertEqual(summary.long_term_60_pct_usd, -12000.0)
        self.assertEqual(summary.ordinary_income_deduction_usd, 3000.0)
        self.assertEqual(summary.capital_loss_carryforward_usd, 17000.0)
        self.assertEqual(summary.estimated_loss_tax_benefit_usd, 9150.0)

    def test_carryback_is_capped_by_prior_gains_not_by_the_loss(self):
        # Prior gains of 80,000 exceed the 47,000 net section 1256 contracts loss.
        summary = self.engine.generate_form_6781_summary(
            elect_section_1212c_carryback=True,
            prior_section_1256_gains_usd=80000.0,
        )
        self.assertEqual(summary.carryback_elected_usd, 47000.0)
        self.assertEqual(summary.net_after_carryback_usd, -3000.0)
        self.assertEqual(summary.ordinary_income_deduction_usd, 3000.0)
        self.assertEqual(summary.capital_loss_carryforward_usd, 0.0)

    def test_election_with_no_prior_gains_carries_nothing_back_and_says_so(self):
        summary = self.engine.generate_form_6781_summary(
            elect_section_1212c_carryback=True)
        self.assertEqual(summary.carryback_elected_usd, 0.0)
        self.assertTrue(any("nothing is carried back" in w for w in summary.warnings))

    def test_other_capital_gains_absorb_the_loss_before_the_3000_cap(self):
        # Sec. 1211(b) allows losses "to the extent of the gains" first:
        #   10,000 offset, 3,000 ordinary, 37,000 carried forward
        #   benefit = 10,000 x 0.268 + 3,000 x 0.37 = 2,680 + 1,110 = 3,790
        summary = self.engine.generate_form_6781_summary(other_capital_gains_usd=10000.0)

        self.assertEqual(summary.loss_offset_against_other_capital_gains_usd, 10000.0)
        self.assertEqual(summary.ordinary_income_deduction_usd, 3000.0)
        self.assertEqual(summary.capital_loss_carryforward_usd, 37000.0)
        self.assertEqual(summary.estimated_loss_tax_benefit_usd, 3790.0)

    def test_married_filing_separately_uses_the_1500_allowance(self):
        engine = Section1256ContractTaxTreatmentUsFuturesEngine(
            filing_status=FILING_STATUS_MFS)
        engine.add_trade(futures_trade(realized=-50000.0))
        summary = engine.generate_form_6781_summary()

        self.assertEqual(engine.capital_loss_allowance_usd, SECTION_1211B_LIMIT_MFS_USD)
        self.assertEqual(summary.net_section_1256_contracts_loss_usd, 48500.0)
        self.assertEqual(summary.ordinary_income_deduction_usd, 1500.0)
        self.assertEqual(summary.capital_loss_carryforward_usd, 48500.0)
        self.assertEqual(summary.estimated_loss_tax_benefit_usd, 555.0)

    def test_small_loss_within_the_allowance_has_no_carryback_capacity(self):
        engine = Section1256ContractTaxTreatmentUsFuturesEngine()
        engine.add_trade(futures_trade(realized=-2000.0))
        summary = engine.generate_form_6781_summary(
            elect_section_1212c_carryback=True, prior_section_1256_gains_usd=100000.0)

        self.assertEqual(summary.net_section_1256_contracts_loss_usd, 0.0)
        self.assertEqual(summary.carryback_elected_usd, 0.0)
        self.assertEqual(summary.ordinary_income_deduction_usd, 2000.0)
        self.assertEqual(summary.capital_loss_carryforward_usd, 0.0)

    def test_estate_or_trust_cannot_check_box_d(self):
        with self.assertRaises(ValueError) as ctx:
            self.engine.generate_form_6781_summary(
                elect_section_1212c_carryback=True,
                prior_section_1256_gains_usd=30000.0,
                taxpayer_is_estate_or_trust=True,
            )
        self.assertIn("estate or trust", str(ctx.exception))

    def test_gain_year_ignores_the_carryback_election(self):
        engine = Section1256ContractTaxTreatmentUsFuturesEngine()
        engine.add_trade(futures_trade(realized=100000.0))
        summary = engine.generate_form_6781_summary(
            elect_section_1212c_carryback=True, prior_section_1256_gains_usd=50000.0)
        self.assertEqual(summary.carryback_elected_usd, 0.0)
        self.assertEqual(summary.net_after_carryback_usd, 100000.0)


class TestInputValidation(unittest.TestCase):
    """Malformed input is rejected rather than absorbed into a filing figure."""

    def setUp(self):
        self.engine = Section1256ContractTaxTreatmentUsFuturesEngine()

    def test_percentage_rate_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            Section1256ContractTaxTreatmentUsFuturesEngine(
                short_term_capital_gains_rate=37.0)
        self.assertIn("0.37", str(ctx.exception))

    def test_negative_rate_is_rejected(self):
        with self.assertRaises(ValueError):
            Section1256ContractTaxTreatmentUsFuturesEngine(
                long_term_capital_gains_rate=-0.2)

    def test_unknown_filing_status_is_rejected(self):
        with self.assertRaises(ValueError):
            Section1256ContractTaxTreatmentUsFuturesEngine(filing_status="SINGLE_ISH")

    def test_nan_pnl_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.add_trade(futures_trade(realized=float("nan")))

    def test_infinite_pnl_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.add_trade(futures_trade(realized=math.inf))

    def test_boolean_pnl_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.add_trade(futures_trade(realized=True))

    def test_duplicate_trade_id_is_rejected(self):
        self.engine.add_trade(futures_trade(trade_id="DUP", realized=1000.0))
        with self.assertRaises(ValueError) as ctx:
            self.engine.add_trade(futures_trade(trade_id="DUP", realized=1000.0))
        self.assertIn("duplicate", str(ctx.exception).lower())

    def test_non_enum_contract_type_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.add_trade(Section1256Trade(
                "X", "ES", "REGULATED_FUTURES", 1000.0))

    def test_hedge_cannot_also_be_a_mixed_straddle_leg(self):
        with self.assertRaises(ValueError):
            self.engine.add_trade(futures_trade(
                realized=1000.0,
                is_identified_hedging_transaction=True,
                is_part_of_mixed_straddle=True,
            ))

    def test_identified_hedge_cannot_carry_a_year_end_mark(self):
        # Sec. 1256(e) removes an identified hedge from the mark entirely, so a
        # mark on one would recognise income the statute does not.
        with self.assertRaises(ValueError) as ctx:
            self.engine.add_trade(futures_trade(
                realized=1000.0,
                year_end_mark_pnl_usd=5000.0,
                is_open_at_year_end=True,
                is_identified_hedging_transaction=True,
            ))
        self.assertIn("not marked to market", str(ctx.exception))

    def test_negative_prior_section_1256_gains_is_rejected(self):
        self.engine.add_trade(futures_trade(realized=-50000.0))
        with self.assertRaises(ValueError):
            self.engine.generate_form_6781_summary(
                elect_section_1212c_carryback=True,
                prior_section_1256_gains_usd=-30000.0,
            )

    def test_non_finite_other_capital_gains_is_rejected(self):
        self.engine.add_trade(futures_trade(realized=-50000.0))
        with self.assertRaises(ValueError):
            self.engine.generate_form_6781_summary(
                other_capital_gains_usd=float("nan"))

    def test_rejected_trade_does_not_enter_the_blotter(self):
        with self.assertRaises(ValueError):
            self.engine.add_trade(futures_trade(realized=float("nan")))
        self.assertEqual(len(self.engine.trades), 0)


class TestReportShape(unittest.TestCase):
    """Behaviour of the summary itself."""

    def test_empty_blotter_produces_a_zero_report(self):
        engine = Section1256ContractTaxTreatmentUsFuturesEngine()
        summary = engine.generate_form_6781_summary()
        self.assertEqual(summary.net_section_1256_pnl_usd, 0.0)
        self.assertEqual(summary.short_term_40_pct_usd, 0.0)
        self.assertEqual(summary.long_term_60_pct_usd, 0.0)
        self.assertEqual(summary.estimated_tax_usd, 0.0)
        self.assertEqual(summary.warnings, [])

    def test_many_small_trades_sum_without_drift(self):
        # 10,000 x $0.01 == $100.00 exactly under math.fsum; naive addition drifts.
        engine = Section1256ContractTaxTreatmentUsFuturesEngine()
        for i in range(10000):
            engine.add_trade(futures_trade(trade_id=f"T{i}", realized=0.01))
        summary = engine.generate_form_6781_summary()
        self.assertEqual(summary.net_section_1256_pnl_usd, 100.0)
        self.assertEqual(summary.long_term_60_pct_usd, 60.0)
        self.assertEqual(summary.short_term_40_pct_usd, 40.0)

    def test_default_rates_are_the_documented_statutory_maxima(self):
        self.assertEqual(DEFAULT_MAX_LONG_TERM_RATE, 0.20)
        self.assertEqual(DEFAULT_MAX_SHORT_TERM_RATE, 0.37)
        self.assertEqual(NET_INVESTMENT_INCOME_TAX_RATE, 0.038)

    def test_audit_notes_reference_the_form_6781_lines(self):
        engine = Section1256ContractTaxTreatmentUsFuturesEngine()
        engine.add_trade(futures_trade(realized=100000.0))
        summary = engine.generate_form_6781_summary()
        self.assertIn("line 5", summary.audit_notes)
        self.assertIn("line 8", summary.audit_notes)
        self.assertIn("line 9", summary.audit_notes)

    def test_engine_defaults_to_single_filing_status(self):
        engine = Section1256ContractTaxTreatmentUsFuturesEngine()
        self.assertEqual(engine.filing_status, FILING_STATUS_SINGLE)
        self.assertEqual(engine.capital_loss_allowance_usd, SECTION_1211B_LIMIT_USD)


if __name__ == "__main__":
    unittest.main()

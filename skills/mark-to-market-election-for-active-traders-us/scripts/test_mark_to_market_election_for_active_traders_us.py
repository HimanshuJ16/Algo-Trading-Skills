"""Unit tests for the IRC Sec. 475(f) mark-to-market tax engine.

Expected values are derived by hand from the statute (Sec. 475(a), Sec. 1211(b),
Sec. 1212(b), Sec. 461(l)(3)(A)) and from the published threshold amounts, never
by re-running the implementation's own expression.
"""
import logging
import math
import unittest

from mark_to_market_election_for_active_traders_us import (
    EBL_STATUS_LIMITED,
    EBL_STATUS_NOT_APPLICABLE,
    EBL_STATUS_NOT_EVALUATED,
    EBL_STATUS_WITHIN_THRESHOLD,
    FILING_STATUS_MFJ,
    FILING_STATUS_MFS,
    FILING_STATUS_SINGLE,
    FORM_4797_PART_II,
    FORM_8949_SCHEDULE_D,
    STATUS_CAPITAL,
    STATUS_MTM,
    MarkToMarketTaxEngine,
    RealizedTrade,
    TaxLot,
)


_MODULE_LOGGER = "mark_to_market_election_for_active_traders_us"


def setUpModule():
    """The engine warns by design on capped wash-sale inputs; keep suite output quiet."""
    logging.getLogger(_MODULE_LOGGER).addHandler(logging.NullHandler())
    logging.getLogger(_MODULE_LOGGER).propagate = False


def loss_trade(trade_id="T1", symbol="AAPL", wash=0.0, **kwargs):
    """Sell 100 @ 150 against a 200 basis => -$5,000 realized loss."""
    params = dict(
        sell_date="2026-03-15", sell_price=150.0, cost_basis=200.0,
        quantity=100.0, potential_wash_sale_loss=wash,
    )
    params.update(kwargs)
    return RealizedTrade(trade_id, symbol, **params)


class TestBaselineBranches(unittest.TestCase):
    """Behaviour that must not regress: the two accounting branches."""

    def setUp(self):
        self.engine = MarkToMarketTaxEngine(max_capital_loss_deduction_usd=3000.0)
        self.realized_trades = [loss_trade()]
        # NVDA 100 @ 100 marked to 150 => +$5,000 unrealized.
        self.open_lots = [
            TaxLot("L1", "NVDA", "2026-11-01", buy_price=100.0, quantity=100.0,
                   year_end_fmv_price=150.0)
        ]

    def test_section_475f_mtm_elected_tax_calculation(self):
        # Realized -$5,000 + MTM +$5,000 = $0.00 ordinary, Form 4797 Part II.
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True,
            realized_trades=self.realized_trades,
            open_tax_lots=self.open_lots,
        )
        self.assertEqual(report.status, STATUS_MTM)
        self.assertTrue(report.is_mtm_elected)
        self.assertEqual(report.wash_sale_disallowed_usd, 0.0)
        self.assertEqual(report.total_realized_pl_usd, -5000.0)
        self.assertEqual(report.unrealized_mtm_pl_usd, 5000.0)
        self.assertEqual(report.total_reportable_taxable_pl_usd, 0.0)
        self.assertEqual(report.marked_lot_count, 1)
        self.assertEqual(report.tax_form_mapping, FORM_4797_PART_II)
        self.assertEqual(report.capital_loss_carryforward_usd, 0.0)

    def test_standard_capital_accounting_wash_sale_and_loss_cap(self):
        # Sell 100 @ 100 against a 200 basis => -$10,000; Sec. 1211(b) caps the
        # deduction at $3,000 and Sec. 1212(b) carries $7,000 forward.
        heavy_loss = [loss_trade("T2", "TSLA", sell_price=100.0, sell_date="2026-05-10")]
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=False,
            realized_trades=heavy_loss,
            open_tax_lots=self.open_lots,
        )
        self.assertEqual(report.status, STATUS_CAPITAL)
        self.assertFalse(report.is_mtm_elected)
        self.assertEqual(report.unrealized_mtm_pl_usd, 0.0)  # securities are not marked
        self.assertEqual(report.total_reportable_taxable_pl_usd, -10000.0)
        self.assertEqual(report.reportable_loss_deduction_usd, -3000.0)
        self.assertEqual(report.capital_loss_carryforward_usd, -7000.0)
        self.assertEqual(report.tax_form_mapping, FORM_8949_SCHEDULE_D)
        self.assertEqual(report.excess_business_loss_status, EBL_STATUS_NOT_APPLICABLE)

    def test_mtm_gain_reports_no_loss_deduction(self):
        gain = [loss_trade("T3", sell_price=250.0)]  # +$5,000
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=gain, open_tax_lots=[], tax_year=2026)
        self.assertEqual(report.total_reportable_taxable_pl_usd, 5000.0)
        self.assertEqual(report.reportable_loss_deduction_usd, 0.0)
        self.assertEqual(report.excess_business_loss_status, EBL_STATUS_NOT_APPLICABLE)


class TestSection475BasisAdjustment(unittest.TestCase):
    """Sec. 475(a): a re-marked lot marks from the prior year-end mark."""

    def setUp(self):
        self.engine = MarkToMarketTaxEngine()

    def test_lot_carried_across_year_end_marks_from_prior_mark(self):
        # Bought at 100, marked to 140 last 12/31, worth 150 this 12/31.
        # Only this year's $10/share of appreciation is recognized: +$1,000.
        # Marking from the 100 purchase price would double-count $4,000.
        lot = TaxLot("L1", "MSFT", "2025-06-01", buy_price=100.0, quantity=100.0,
                     year_end_fmv_price=150.0, prior_year_end_mark_price=140.0)
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=[], open_tax_lots=[lot], tax_year=2026)
        self.assertEqual(report.unrealized_mtm_pl_usd, 1000.0)
        self.assertEqual(report.total_reportable_taxable_pl_usd, 1000.0)

    def test_new_lot_without_prior_mark_uses_purchase_price(self):
        lot = TaxLot("L2", "MSFT", "2026-06-01", buy_price=100.0, quantity=100.0,
                     year_end_fmv_price=150.0)
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=[], open_tax_lots=[lot], tax_year=2026)
        self.assertEqual(report.unrealized_mtm_pl_usd, 5000.0)
        self.assertEqual(report.warnings, [])

    def test_carryover_lot_missing_prior_mark_is_flagged(self):
        lot = TaxLot("L3", "MSFT", "2024-06-01", buy_price=100.0, quantity=100.0,
                     year_end_fmv_price=150.0)
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=[], open_tax_lots=[lot], tax_year=2026)
        self.assertTrue(any("prior_year_end_mark_price" in w for w in report.warnings))

    def test_open_short_marks_in_the_opposite_direction(self):
        # Shorted 100 @ 200; FMV 150 at year end => the short is up $5,000.
        # Applying the long formula would report a $5,000 loss.
        lot = TaxLot("S1", "GME", "2026-10-01", buy_price=200.0, quantity=100.0,
                     year_end_fmv_price=150.0, is_short=True)
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=[], open_tax_lots=[lot], tax_year=2026)
        self.assertEqual(report.unrealized_mtm_pl_usd, 5000.0)

    def test_open_short_against_prior_mark(self):
        # Shorted at 200, marked to 180 last year, FMV 150 now => +$3,000 this year.
        lot = TaxLot("S2", "GME", "2025-10-01", buy_price=200.0, quantity=100.0,
                     year_end_fmv_price=150.0, prior_year_end_mark_price=180.0,
                     is_short=True)
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=[], open_tax_lots=[lot], tax_year=2026)
        self.assertEqual(report.unrealized_mtm_pl_usd, 3000.0)

    def test_closed_short_needs_no_flag(self):
        # Shorted at 200, covered at 150, 100 units => +$5,000.
        covered = RealizedTrade("S3", "GME", "2026-11-01", sell_price=200.0,
                                cost_basis=150.0, quantity=100.0)
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=[covered], open_tax_lots=[], tax_year=2026)
        self.assertEqual(report.total_realized_pl_usd, 5000.0)

    def test_mark_can_be_a_loss_below_prior_mark(self):
        lot = TaxLot("L4", "MSFT", "2025-06-01", buy_price=100.0, quantity=50.0,
                     year_end_fmv_price=80.0, prior_year_end_mark_price=140.0)
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=[], open_tax_lots=[lot], tax_year=2026)
        self.assertEqual(report.unrealized_mtm_pl_usd, -3000.0)  # (80-140)*50


class TestExcessBusinessLossLimitation(unittest.TestCase):
    """Sec. 461(l)(3)(A): the ordinary loss deduction is not unlimited."""

    @staticmethod
    def _big_loss(amount_usd):
        # Sell `quantity` units at a $1 loss each.
        return [RealizedTrade("T", "SPY", "2026-12-01", sell_price=99.0,
                              cost_basis=100.0, quantity=float(amount_usd))]

    def test_2026_single_filer_loss_is_capped_at_published_threshold(self):
        # Rev. Proc. 2025-32 sec. .31: $256,000 for 2026, other than joint.
        # -$600,000 aggregate => $344,000 disallowed, $256,000 deductible.
        engine = MarkToMarketTaxEngine(filing_status=FILING_STATUS_SINGLE)
        report = engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=self._big_loss(600_000),
            open_tax_lots=[], tax_year=2026)
        self.assertEqual(report.total_reportable_taxable_pl_usd, -600_000.0)
        self.assertEqual(report.excess_business_loss_threshold_usd, 256_000.0)
        self.assertEqual(report.excess_business_loss_disallowed_usd, 344_000.0)
        self.assertEqual(report.reportable_loss_deduction_usd, -256_000.0)
        self.assertEqual(report.excess_business_loss_status, EBL_STATUS_LIMITED)

    def test_2026_joint_filer_threshold_is_doubled(self):
        engine = MarkToMarketTaxEngine(filing_status=FILING_STATUS_MFJ)
        report = engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=self._big_loss(600_000),
            open_tax_lots=[], tax_year=2026)
        self.assertEqual(report.excess_business_loss_threshold_usd, 512_000.0)
        self.assertEqual(report.excess_business_loss_disallowed_usd, 88_000.0)
        self.assertEqual(report.reportable_loss_deduction_usd, -512_000.0)

    def test_2025_threshold_uses_the_published_2025_amount(self):
        # Instructions for Form 461 (2025): $313,000 / $626,000.
        engine = MarkToMarketTaxEngine(filing_status=FILING_STATUS_SINGLE)
        report = engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=self._big_loss(400_000),
            open_tax_lots=[], tax_year=2025)
        self.assertEqual(report.excess_business_loss_threshold_usd, 313_000.0)
        self.assertEqual(report.excess_business_loss_disallowed_usd, 87_000.0)
        self.assertEqual(report.reportable_loss_deduction_usd, -313_000.0)

    def test_loss_below_threshold_is_fully_deductible(self):
        engine = MarkToMarketTaxEngine(filing_status=FILING_STATUS_SINGLE)
        report = engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=self._big_loss(100_000),
            open_tax_lots=[], tax_year=2026)
        self.assertEqual(report.excess_business_loss_status, EBL_STATUS_WITHIN_THRESHOLD)
        self.assertEqual(report.excess_business_loss_disallowed_usd, 0.0)
        self.assertEqual(report.reportable_loss_deduction_usd, -100_000.0)

    def test_exact_threshold_boundary_is_not_an_excess_loss(self):
        engine = MarkToMarketTaxEngine(filing_status=FILING_STATUS_SINGLE)
        report = engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=self._big_loss(256_000),
            open_tax_lots=[], tax_year=2026)
        self.assertEqual(report.excess_business_loss_status, EBL_STATUS_WITHIN_THRESHOLD)
        self.assertEqual(report.excess_business_loss_disallowed_usd, 0.0)

    def test_one_dollar_over_threshold_disallows_one_dollar(self):
        engine = MarkToMarketTaxEngine(filing_status=FILING_STATUS_SINGLE)
        report = engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=self._big_loss(256_001),
            open_tax_lots=[], tax_year=2026)
        self.assertEqual(report.excess_business_loss_status, EBL_STATUS_LIMITED)
        self.assertEqual(report.excess_business_loss_disallowed_usd, 1.0)
        self.assertEqual(report.reportable_loss_deduction_usd, -256_000.0)

    def test_other_business_income_offsets_the_trading_loss(self):
        # -$300,000 trading + $100,000 other = -$200,000 aggregate, inside the
        # $256,000 threshold, so the whole trading loss stays deductible.
        engine = MarkToMarketTaxEngine(filing_status=FILING_STATUS_SINGLE)
        report = engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=self._big_loss(300_000),
            open_tax_lots=[], tax_year=2026, other_net_business_income_usd=100_000.0)
        self.assertEqual(report.excess_business_loss_status, EBL_STATUS_WITHIN_THRESHOLD)
        self.assertEqual(report.reportable_loss_deduction_usd, -300_000.0)

    def test_other_business_loss_aggregates_into_the_limitation(self):
        # -$300,000 trading + -$100,000 other = -$400,000 aggregate;
        # $400,000 - $256,000 = $144,000 disallowed.
        engine = MarkToMarketTaxEngine(filing_status=FILING_STATUS_SINGLE)
        report = engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=self._big_loss(300_000),
            open_tax_lots=[], tax_year=2026, other_net_business_income_usd=-100_000.0)
        self.assertEqual(report.excess_business_loss_disallowed_usd, 144_000.0)
        self.assertEqual(report.reportable_loss_deduction_usd, -156_000.0)

    def test_unknown_tax_year_reports_not_evaluated_rather_than_guessing(self):
        engine = MarkToMarketTaxEngine(filing_status=FILING_STATUS_SINGLE)
        report = engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=self._big_loss(600_000),
            open_tax_lots=[], tax_year=2099)
        self.assertEqual(report.excess_business_loss_status, EBL_STATUS_NOT_EVALUATED)
        self.assertIsNone(report.excess_business_loss_threshold_usd)
        self.assertEqual(report.reportable_loss_deduction_usd, -600_000.0)
        self.assertTrue(any("Form 461" in w for w in report.warnings))

    def test_missing_tax_year_reports_not_evaluated(self):
        engine = MarkToMarketTaxEngine()
        report = engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=self._big_loss(600_000), open_tax_lots=[])
        self.assertEqual(report.excess_business_loss_status, EBL_STATUS_NOT_EVALUATED)

    def test_explicit_threshold_override_wins_over_the_table(self):
        engine = MarkToMarketTaxEngine(excess_business_loss_threshold_usd=50_000.0)
        report = engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=self._big_loss(80_000),
            open_tax_lots=[], tax_year=2026)
        self.assertEqual(report.excess_business_loss_threshold_usd, 50_000.0)
        self.assertEqual(report.excess_business_loss_disallowed_usd, 30_000.0)


class TestCapitalLossLimitationAndCarryforward(unittest.TestCase):
    """Sec. 1211(b) allowance and Sec. 1212(b) carryforward."""

    def test_married_filing_separately_allowance_is_1500(self):
        engine = MarkToMarketTaxEngine(filing_status=FILING_STATUS_MFS)
        report = engine.calculate_tax_liability(
            is_mtm_elected=False, realized_trades=[loss_trade()], open_tax_lots=[])
        self.assertEqual(report.total_reportable_taxable_pl_usd, -5000.0)
        self.assertEqual(report.reportable_loss_deduction_usd, -1500.0)
        self.assertEqual(report.capital_loss_carryforward_usd, -3500.0)

    def test_default_allowance_is_3000_for_other_statuses(self):
        engine = MarkToMarketTaxEngine(filing_status=FILING_STATUS_SINGLE)
        report = engine.calculate_tax_liability(
            is_mtm_elected=False, realized_trades=[loss_trade()], open_tax_lots=[])
        self.assertEqual(report.reportable_loss_deduction_usd, -3000.0)
        self.assertEqual(report.capital_loss_carryforward_usd, -2000.0)

    def test_loss_smaller_than_allowance_leaves_no_carryforward(self):
        engine = MarkToMarketTaxEngine()
        small = [loss_trade(sell_price=199.0)]  # -$100
        report = engine.calculate_tax_liability(
            is_mtm_elected=False, realized_trades=small, open_tax_lots=[])
        self.assertEqual(report.reportable_loss_deduction_usd, -100.0)
        self.assertEqual(report.capital_loss_carryforward_usd, 0.0)

    def test_capital_gains_absorb_losses_before_the_allowance(self):
        # -$5,000 loss netted against a +$4,000 gain leaves -$1,000, fully allowed.
        gain = RealizedTrade("G1", "QQQ", "2026-04-01", sell_price=140.0,
                             cost_basis=100.0, quantity=100.0)
        engine = MarkToMarketTaxEngine()
        report = engine.calculate_tax_liability(
            is_mtm_elected=False, realized_trades=[loss_trade(), gain], open_tax_lots=[])
        self.assertEqual(report.total_reportable_taxable_pl_usd, -1000.0)
        self.assertEqual(report.reportable_loss_deduction_usd, -1000.0)
        self.assertEqual(report.capital_loss_carryforward_usd, 0.0)


class TestWashSaleInputHandling(unittest.TestCase):
    """Sec. 1091(a) disallows a loss, and never more than the loss realized."""

    def setUp(self):
        self.engine = MarkToMarketTaxEngine()

    def test_disallowance_is_capped_at_the_realized_loss(self):
        # -$5,000 realized but $8,000 claimed disallowed: capping keeps net at $0,
        # not the +$3,000 of phantom income an uncapped add-back would create.
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=False, realized_trades=[loss_trade(wash=8000.0)], open_tax_lots=[])
        self.assertEqual(report.wash_sale_disallowed_usd, 5000.0)
        self.assertEqual(report.total_reportable_taxable_pl_usd, 0.0)
        self.assertTrue(any("capped at the realized loss" in w for w in report.warnings))

    def test_disallowance_on_a_gain_trade_is_ignored(self):
        gain = loss_trade("T9", sell_price=250.0, wash=1000.0)  # +$5,000 gain
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=False, realized_trades=[gain], open_tax_lots=[])
        self.assertEqual(report.wash_sale_disallowed_usd, 0.0)
        self.assertEqual(report.total_reportable_taxable_pl_usd, 5000.0)

    def test_partial_disallowance_within_the_loss_is_honoured(self):
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=False, realized_trades=[loss_trade(wash=2000.0)], open_tax_lots=[])
        self.assertEqual(report.wash_sale_disallowed_usd, 2000.0)
        self.assertEqual(report.total_reportable_taxable_pl_usd, -3000.0)

    def test_negative_disallowance_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_tax_liability(
                is_mtm_elected=False, realized_trades=[loss_trade(wash=-500.0)],
                open_tax_lots=[])

    def test_mtm_ignores_wash_sale_input_and_says_so(self):
        # Sec. 475(d)(1) disapplies Sec. 1091 to losses recognized under Sec. 475(a).
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=[loss_trade(wash=5000.0)],
            open_tax_lots=[], tax_year=2026)
        self.assertEqual(report.wash_sale_disallowed_usd, 0.0)
        self.assertEqual(report.total_reportable_taxable_pl_usd, -5000.0)
        self.assertTrue(any("Sec. 1091" in w for w in report.warnings))


class TestScopeExclusions(unittest.TestCase):
    """Sec. 475(f)(1)(B) investment identification and Sec. 475(f)(2) commodities."""

    def setUp(self):
        self.engine = MarkToMarketTaxEngine()

    def test_identified_investment_trade_is_excluded_from_ordinary_income(self):
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True,
            realized_trades=[loss_trade(is_identified_investment_security=True)],
            open_tax_lots=[], tax_year=2026)
        self.assertEqual(report.total_reportable_taxable_pl_usd, 0.0)
        self.assertEqual(report.excluded_investment_pl_usd, -5000.0)
        self.assertTrue(any("Sec. 1091 still applies" in w for w in report.warnings))

    def test_identified_investment_lot_is_not_marked(self):
        lot = TaxLot("L1", "BRK", "2020-01-01", buy_price=100.0, quantity=100.0,
                     year_end_fmv_price=500.0, is_identified_investment_security=True)
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=[], open_tax_lots=[lot], tax_year=2026)
        self.assertEqual(report.unrealized_mtm_pl_usd, 0.0)
        self.assertEqual(report.marked_lot_count, 0)

    def test_section_1256_trade_is_routed_out_without_a_commodities_election(self):
        futures = loss_trade("F1", "ESZ6", is_section_1256_contract=True)
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=[futures], open_tax_lots=[], tax_year=2026)
        self.assertEqual(report.total_reportable_taxable_pl_usd, 0.0)
        self.assertEqual(report.excluded_section_1256_pl_usd, -5000.0)
        self.assertTrue(any("Form 6781" in w for w in report.warnings))

    def test_section_1256_trade_becomes_ordinary_under_the_475f2_election(self):
        futures = loss_trade("F1", "ESZ6", is_section_1256_contract=True)
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=[futures], open_tax_lots=[],
            tax_year=2026, elects_commodities_475f2=True)
        self.assertEqual(report.total_reportable_taxable_pl_usd, -5000.0)
        self.assertEqual(report.excluded_section_1256_pl_usd, 0.0)

    def test_open_section_1256_lot_in_the_capital_branch_is_flagged(self):
        lot = TaxLot("L1", "ESZ6", "2026-11-01", buy_price=100.0, quantity=10.0,
                     year_end_fmv_price=150.0, is_section_1256_contract=True)
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=False, realized_trades=[], open_tax_lots=[lot])
        self.assertEqual(report.unrealized_mtm_pl_usd, 0.0)
        self.assertTrue(any("Sec. 1256(a)" in w for w in report.warnings))

    def test_incoherent_flag_combination_is_rejected(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_tax_liability(
                is_mtm_elected=True,
                realized_trades=[loss_trade(is_identified_investment_security=True,
                                            is_section_1256_contract=True)],
                open_tax_lots=[])


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = MarkToMarketTaxEngine()

    def test_non_finite_price_is_rejected(self):
        for bad in (float("nan"), float("inf")):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.engine.calculate_tax_liability(
                        is_mtm_elected=True, realized_trades=[loss_trade(sell_price=bad)],
                        open_tax_lots=[])

    def test_non_finite_fmv_is_rejected(self):
        lot = TaxLot("L1", "X", "2026-01-01", buy_price=1.0, quantity=1.0,
                     year_end_fmv_price=math.nan)
        with self.assertRaises(ValueError):
            self.engine.calculate_tax_liability(
                is_mtm_elected=True, realized_trades=[], open_tax_lots=[lot])

    def test_non_positive_quantity_is_rejected(self):
        for bad in (0.0, -100.0):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    self.engine.calculate_tax_liability(
                        is_mtm_elected=True, realized_trades=[loss_trade(quantity=bad)],
                        open_tax_lots=[])

    def test_non_numeric_price_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.calculate_tax_liability(
                is_mtm_elected=True, realized_trades=[loss_trade(sell_price="150")],
                open_tax_lots=[])

    def test_non_boolean_election_flag_is_rejected(self):
        with self.assertRaises(TypeError):
            self.engine.calculate_tax_liability(
                is_mtm_elected=1, realized_trades=[], open_tax_lots=[])

    def test_invalid_filing_status_is_rejected(self):
        with self.assertRaises(ValueError):
            MarkToMarketTaxEngine(filing_status="SINGLE_FILER")

    def test_negative_capital_allowance_is_rejected(self):
        with self.assertRaises(ValueError):
            MarkToMarketTaxEngine(max_capital_loss_deduction_usd=-1.0)

    def test_election_cannot_predate_its_first_effective_year(self):
        with self.assertRaises(ValueError):
            self.engine.calculate_tax_liability(
                is_mtm_elected=True, realized_trades=[], open_tax_lots=[],
                tax_year=2025, election_effective_first_tax_year=2026)

    def test_election_year_equal_to_tax_year_is_accepted(self):
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=[], open_tax_lots=[],
            tax_year=2026, election_effective_first_tax_year=2026)
        self.assertEqual(report.status, STATUS_MTM)

    def test_empty_inputs_produce_a_zero_report(self):
        report = self.engine.calculate_tax_liability(
            is_mtm_elected=True, realized_trades=[], open_tax_lots=[], tax_year=2026)
        self.assertEqual(report.total_reportable_taxable_pl_usd, 0.0)
        self.assertEqual(report.reportable_loss_deduction_usd, 0.0)
        self.assertEqual(report.warnings, [])


if __name__ == "__main__":
    unittest.main()

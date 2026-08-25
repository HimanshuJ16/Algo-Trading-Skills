"""Behavioural tests for the IRC section 6654 estimated tax scheduling engine.

Expected values are derived independently of the implementation: safe harbor
targets are hand-computed from the statutory percentages, and the section 7503
due dates are checked against IRS-published deadlines (April 18, 2017) and against
calendar facts verified with ``datetime`` rather than by re-running the engine's
own date arithmetic.
"""
import datetime as dt
import unittest

from estimated_tax_payment_scheduling_for_active_trading_income import (
    BASIS_CURRENT_YEAR_90PCT,
    BASIS_PRIOR_YEAR,
    DE_MINIMIS_THRESHOLD_USD,
    EXCEPTION_DE_MINIMIS,
    EXCEPTION_NO_PRIOR_YEAR_LIABILITY,
    FILING_STATUS_MARRIED_FILING_JOINTLY,
    FILING_STATUS_MARRIED_FILING_SEPARATELY,
    FILING_STATUS_SINGLE,
    HIGH_AGI_THRESHOLD_MFS_USD,
    HIGH_AGI_THRESHOLD_USD,
    EstimatedTaxError,
    EstimatedTaxSchedulerEngine,
    STATUS_OVERDUE,
    STATUS_PAID,
    STATUS_SCHEDULED,
    adjusted_installment_due_dates,
    apply_section_7503,
    dc_legal_holidays,
    statutory_installment_due_dates,
)


class TestSafeHarborSelection(unittest.TestCase):
    """Section 6654(d)(1)(B) required annual payment."""

    def setUp(self):
        self.engine = EstimatedTaxSchedulerEngine()

    def test_high_agi_110pct_safe_harbor_is_the_lesser_amount(self):
        # Prior AGI $200,000 > $150,000 -> 110% of $50,000 = $55,000.
        # Current-year limb: 90% of $90,000 = $81,000.
        # Section 6654(d)(1)(B) takes the LESSER: $55,000, i.e. $13,750 a quarter.
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="TRADER_QUANT_01",
            tax_year=2026,
            prior_year_agi_usd=200_000.0,
            prior_year_tax_usd=50_000.0,
            projected_current_year_tax_usd=90_000.0,
        )
        self.assertEqual(report.applied_safe_harbor_pct, 110.0)
        self.assertEqual(report.safe_harbor_prior_year_usd, 55_000.0)
        self.assertEqual(report.safe_harbor_current_year_90pct_usd, 81_000.0)
        self.assertEqual(report.required_annual_tax_payment_usd, 55_000.0)
        self.assertEqual(report.safe_harbor_basis, BASIS_PRIOR_YEAR)
        self.assertEqual(report.quarterly_installment_usd, 13_750.0)
        self.assertEqual(len(report.installments), 4)

    def test_standard_100pct_safe_harbor_below_threshold(self):
        # Prior AGI $120,000 <= $150,000 -> 100% of $30,000 = $30,000,
        # versus 90% of $80,000 = $72,000. Lesser is $30,000 -> $7,500 a quarter.
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="TRADER_QUANT_02",
            tax_year=2026,
            prior_year_agi_usd=120_000.0,
            prior_year_tax_usd=30_000.0,
            projected_current_year_tax_usd=80_000.0,
        )
        self.assertEqual(report.applied_safe_harbor_pct, 100.0)
        self.assertEqual(report.required_annual_tax_payment_usd, 30_000.0)
        self.assertEqual(report.quarterly_installment_usd, 7_500.0)

    def test_agi_exactly_at_threshold_stays_at_100pct(self):
        # Section 6654(d)(1)(C) applies only where AGI "exceeds" $150,000.
        # Exactly $150,000 does not exceed it.
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=HIGH_AGI_THRESHOLD_USD,
            prior_year_tax_usd=40_000.0,
            projected_current_year_tax_usd=500_000.0,
        )
        self.assertEqual(report.applied_safe_harbor_pct, 100.0)
        self.assertEqual(report.required_annual_tax_payment_usd, 40_000.0)

    def test_one_cent_above_threshold_triggers_110pct(self):
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=HIGH_AGI_THRESHOLD_USD + 0.01,
            prior_year_tax_usd=40_000.0,
            projected_current_year_tax_usd=500_000.0,
        )
        self.assertEqual(report.applied_safe_harbor_pct, 110.0)
        self.assertEqual(report.required_annual_tax_payment_usd, 44_000.0)

    def test_current_year_limb_governs_when_it_is_smaller(self):
        # Prior-year tax was large, current year is a losing year:
        # 110% of $200,000 = $220,000 vs 90% of $20,000 = $18,000. Lesser wins.
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=900_000.0,
            prior_year_tax_usd=200_000.0,
            projected_current_year_tax_usd=20_000.0,
        )
        self.assertEqual(report.required_annual_tax_payment_usd, 18_000.0)
        self.assertEqual(report.safe_harbor_basis, BASIS_CURRENT_YEAR_90PCT)


class TestMarriedFilingSeparatelyThreshold(unittest.TestCase):
    """Section 6654(d)(1)(C) substitutes $75,000 for a separate return."""

    def test_mfs_at_120k_agi_requires_110pct(self):
        # Regression: the pre-2.0 engine hard-coded $150,000 for every filing
        # status, so this trader was scheduled at 100% ($30,000) and would have
        # under-funded the safe harbor by $3,000.
        report = EstimatedTaxSchedulerEngine().generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=120_000.0,
            prior_year_tax_usd=30_000.0,
            projected_current_year_tax_usd=400_000.0,
            filing_status=FILING_STATUS_MARRIED_FILING_SEPARATELY,
        )
        self.assertEqual(report.high_agi_threshold_usd, HIGH_AGI_THRESHOLD_MFS_USD)
        self.assertEqual(report.applied_safe_harbor_pct, 110.0)
        self.assertEqual(report.required_annual_tax_payment_usd, 33_000.0)

    def test_joint_filer_at_120k_agi_stays_at_100pct(self):
        report = EstimatedTaxSchedulerEngine().generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=120_000.0,
            prior_year_tax_usd=30_000.0,
            projected_current_year_tax_usd=400_000.0,
            filing_status=FILING_STATUS_MARRIED_FILING_JOINTLY,
        )
        self.assertEqual(report.high_agi_threshold_usd, HIGH_AGI_THRESHOLD_USD)
        self.assertEqual(report.applied_safe_harbor_pct, 100.0)

    def test_explicit_threshold_override_suppresses_mfs_substitution(self):
        engine = EstimatedTaxSchedulerEngine(high_agi_threshold_usd=150_000.0)
        report = engine.generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=120_000.0,
            prior_year_tax_usd=30_000.0,
            projected_current_year_tax_usd=400_000.0,
            filing_status=FILING_STATUS_MARRIED_FILING_SEPARATELY,
        )
        self.assertEqual(report.high_agi_threshold_usd, 150_000.0)
        self.assertEqual(report.applied_safe_harbor_pct, 100.0)


class TestPriorYearOptionAvailability(unittest.TestCase):
    """Flush text of section 6654(d)(1)(B): 12-month year AND a return filed."""

    def setUp(self):
        self.engine = EstimatedTaxSchedulerEngine()

    def test_no_prior_return_forces_the_90pct_current_year_limb(self):
        # Regression: the pre-2.0 engine applied the prior-year limb
        # unconditionally, so a first-year trader with no prior return was
        # scheduled at $0 instead of 90% of $120,000 = $108,000.
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="FIRST_YEAR", tax_year=2026,
            prior_year_agi_usd=0.0,
            prior_year_tax_usd=0.0,
            projected_current_year_tax_usd=120_000.0,
            prior_year_return_filed=False,
        )
        self.assertFalse(report.prior_year_safe_harbor_available)
        self.assertIsNone(report.applied_safe_harbor_pct)
        self.assertIsNone(report.safe_harbor_prior_year_usd)
        self.assertEqual(report.required_annual_tax_payment_usd, 108_000.0)
        self.assertEqual(report.safe_harbor_basis, BASIS_CURRENT_YEAR_90PCT)

    def test_short_prior_year_forces_the_90pct_current_year_limb(self):
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=500_000.0,
            prior_year_tax_usd=10_000.0,
            projected_current_year_tax_usd=120_000.0,
            prior_year_was_12_months=False,
        )
        self.assertFalse(report.prior_year_safe_harbor_available)
        self.assertEqual(report.required_annual_tax_payment_usd, 108_000.0)


class TestSection6654Exceptions(unittest.TestCase):

    def setUp(self):
        self.engine = EstimatedTaxSchedulerEngine()

    def test_de_minimis_under_1000_after_withholding(self):
        # Section 6654(e)(1): $3,500 tax less $2,600 withheld = $900 < $1,000.
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=50_000.0,
            prior_year_tax_usd=3_000.0,
            projected_current_year_tax_usd=3_500.0,
            current_year_withholding_usd=2_600.0,
        )
        self.assertTrue(report.penalty_exception_applies)
        self.assertEqual(report.penalty_exception_basis, EXCEPTION_DE_MINIMIS)

    def test_exactly_1000_shortfall_is_not_de_minimis(self):
        # "less than $1,000" is strict; exactly $1,000 does not qualify.
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=50_000.0,
            prior_year_tax_usd=3_000.0,
            projected_current_year_tax_usd=3_600.0,
            current_year_withholding_usd=3_600.0 - DE_MINIMIS_THRESHOLD_USD,
        )
        self.assertFalse(report.penalty_exception_applies)

    def test_zero_prior_year_liability_exception(self):
        # Section 6654(e)(2): 12-month prior year, no prior liability, US person.
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=0.0,
            prior_year_tax_usd=0.0,
            projected_current_year_tax_usd=400_000.0,
        )
        self.assertTrue(report.penalty_exception_applies)
        self.assertEqual(
            report.penalty_exception_basis, EXCEPTION_NO_PRIOR_YEAR_LIABILITY)
        # The prior-year limb still governs the arithmetic and yields $0.
        self.assertEqual(report.required_annual_tax_payment_usd, 0.0)

    def test_zero_prior_liability_without_us_person_status_is_not_excepted(self):
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=0.0,
            prior_year_tax_usd=0.0,
            projected_current_year_tax_usd=400_000.0,
            us_person_entire_prior_year=False,
        )
        self.assertFalse(report.penalty_exception_applies)


class TestWithholdingCredit(unittest.TestCase):
    """Section 6654(g): withholding is deemed paid in four equal parts."""

    def setUp(self):
        self.engine = EstimatedTaxSchedulerEngine()

    def test_withholding_reduces_the_amount_to_remit(self):
        # Required annual $55,000; withholding $20,000 credited $5,000 a quarter.
        # Regression: the pre-2.0 engine ignored withholding entirely and would
        # have told this trader to wire $55,000 rather than $35,000.
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=200_000.0,
            prior_year_tax_usd=50_000.0,
            projected_current_year_tax_usd=90_000.0,
            current_year_withholding_usd=20_000.0,
        )
        self.assertEqual(report.total_estimated_tax_to_schedule_usd, 35_000.0)
        for inst in report.installments:
            self.assertEqual(inst.required_payment_usd, 13_750.0)
            self.assertEqual(inst.withholding_credit_usd, 5_000.0)
            self.assertEqual(inst.estimated_tax_due_usd, 8_750.0)

    def test_withholding_exceeding_the_requirement_schedules_nothing(self):
        report = self.engine.generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=200_000.0,
            prior_year_tax_usd=50_000.0,
            projected_current_year_tax_usd=90_000.0,
            current_year_withholding_usd=60_000.0,
        )
        self.assertEqual(report.total_estimated_tax_to_schedule_usd, 0.0)
        self.assertTrue(report.is_safe_harbor_compliant)
        for inst in report.installments:
            self.assertEqual(inst.estimated_tax_due_usd, 0.0)
            self.assertEqual(inst.status, STATUS_PAID)


class TestComplianceAndShortfall(unittest.TestCase):

    def setUp(self):
        self.engine = EstimatedTaxSchedulerEngine()

    def _base(self, **kw):
        params = dict(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=200_000.0,
            prior_year_tax_usd=50_000.0,
            projected_current_year_tax_usd=90_000.0,
        )
        params.update(kw)
        return self.engine.generate_estimated_tax_schedule(**params)

    def test_unfunded_plan_is_not_reported_compliant(self):
        # Regression: the pre-2.0 engine hard-coded is_safe_harbor_compliant=True,
        # so a trader who had paid nothing was told they were in safe harbor.
        report = self._base()
        self.assertFalse(report.is_safe_harbor_compliant)
        self.assertEqual(report.max_cumulative_shortfall_usd, 55_000.0)

    def test_fully_funded_plan_is_compliant(self):
        report = self._base(payments_made_usd=[13_750.0] * 4)
        self.assertTrue(report.is_safe_harbor_compliant)
        self.assertEqual(report.max_cumulative_shortfall_usd, 0.0)
        for inst in report.installments:
            self.assertEqual(inst.status, STATUS_PAID)

    def test_short_payment_is_detected_not_just_a_missing_one(self):
        # A count-based tracker records four payments and reports full compliance.
        # Four payments that are each $250 short are still an underpayment.
        report = self._base(payments_made_usd=[13_500.0] * 4)
        self.assertFalse(report.is_safe_harbor_compliant)
        self.assertEqual(report.max_cumulative_shortfall_usd, 1_000.0)
        self.assertEqual(report.installments[0].shortfall_usd, 250.0)
        self.assertEqual(report.installments[3].shortfall_usd, 1_000.0)

    def test_early_overpayment_carries_forward(self):
        # Section 6654(b)(2): a surplus in Q1 is credited against Q2.
        report = self._base(payments_made_usd=[27_500.0, 0.0, 13_750.0, 13_750.0])
        self.assertTrue(report.is_safe_harbor_compliant)
        self.assertEqual(report.installments[1].shortfall_usd, 0.0)

    def test_late_catch_up_does_not_cure_the_earlier_installment(self):
        # Paying the whole year in Q4 leaves Q1-Q3 short: the addition to tax is
        # computed per instalment, so the cumulative shortfall must persist.
        report = self._base(payments_made_usd=[0.0, 0.0, 0.0, 55_000.0])
        self.assertEqual(report.installments[0].shortfall_usd, 13_750.0)
        self.assertEqual(report.installments[2].shortfall_usd, 41_250.0)
        self.assertEqual(report.installments[3].shortfall_usd, 0.0)
        self.assertFalse(report.is_safe_harbor_compliant)

    def test_status_is_overdue_only_after_the_due_date(self):
        # Q1 2026 is due April 15, 2026 (a Wednesday, no shift).
        on_time = self._base(as_of_date=dt.date(2026, 4, 15))
        self.assertEqual(on_time.installments[0].status, STATUS_SCHEDULED)
        late = self._base(as_of_date=dt.date(2026, 4, 16))
        self.assertEqual(late.installments[0].status, STATUS_OVERDUE)
        self.assertEqual(late.installments[1].status, STATUS_SCHEDULED)

    def test_compliance_only_considers_installments_already_due(self):
        # Q1 paid in full, nothing else paid, evaluated in May: still compliant,
        # because Q2 is not due until June.
        report = self._base(
            payments_made_usd=[13_750.0], as_of_date=dt.date(2026, 5, 1))
        self.assertTrue(report.is_safe_harbor_compliant)
        self.assertEqual(report.installments[0].status, STATUS_PAID)
        self.assertEqual(report.installments[1].status, STATUS_SCHEDULED)


class TestCentAllocation(unittest.TestCase):

    def test_installments_sum_exactly_to_the_required_annual_payment(self):
        # $10,000.01 / 4 = $2,500.0025, which does not divide into whole cents.
        # No instalment may be short of its 25% share, and the four must still
        # total the required annual payment.
        report = EstimatedTaxSchedulerEngine().generate_estimated_tax_schedule(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=10_000.0,
            prior_year_tax_usd=10_000.01,
            projected_current_year_tax_usd=1_000_000.0,
        )
        self.assertEqual(report.required_annual_tax_payment_usd, 10_000.01)
        total_cents = sum(
            round(i.required_payment_usd * 100) for i in report.installments)
        self.assertEqual(total_cents, 1_000_001)
        for idx, inst in enumerate(report.installments):
            # Cumulative requirement is never below the exact 25% share.
            exact = 10_000.01 * (idx + 1) / 4
            self.assertGreaterEqual(inst.cumulative_required_usd + 1e-9, exact)


class TestDueDateAdjustment(unittest.TestCase):
    """Section 7503 weekend and DC legal-holiday shift."""

    def test_2026_dates_need_no_adjustment(self):
        self.assertEqual(
            adjusted_installment_due_dates(2026),
            (dt.date(2026, 4, 15), dt.date(2026, 6, 15),
             dt.date(2026, 9, 15), dt.date(2027, 1, 15)),
        )
        for d in adjusted_installment_due_dates(2026):
            self.assertLess(d.weekday(), 5)

    def test_2017_q1_shifted_to_april_18(self):
        # IRS-published outcome: April 15, 2017 was a Saturday and Emancipation
        # Day (Sunday April 16) was observed Monday April 17, so the Q1 payment
        # was not due until Tuesday April 18, 2017.
        self.assertEqual(dt.date(2017, 4, 15).weekday(), 5)
        self.assertEqual(apply_section_7503(dt.date(2017, 4, 15)),
                         dt.date(2017, 4, 18))

    def test_2011_q1_shifted_to_april_18(self):
        # April 15, 2011 was a Friday but Emancipation Day (Saturday April 16)
        # was observed on it, pushing the date to Monday April 18, 2011.
        self.assertEqual(dt.date(2011, 4, 15).weekday(), 4)
        self.assertEqual(apply_section_7503(dt.date(2011, 4, 15)),
                         dt.date(2011, 4, 18))

    def test_q4_shifts_when_january_15_is_martin_luther_king_day(self):
        # January 15, 2029 is the third Monday of January.
        jan15 = dt.date(2029, 1, 15)
        self.assertEqual(jan15.weekday(), 0)
        self.assertIn(jan15, dc_legal_holidays(2029))
        self.assertEqual(adjusted_installment_due_dates(2028)[3], dt.date(2029, 1, 16))

    def test_statutory_dates_are_reported_unadjusted(self):
        self.assertEqual(
            statutory_installment_due_dates(2017)[0], dt.date(2017, 4, 15))
        report = EstimatedTaxSchedulerEngine().generate_estimated_tax_schedule(
            trader_id="T", tax_year=2017,
            prior_year_agi_usd=200_000.0,
            prior_year_tax_usd=50_000.0,
            projected_current_year_tax_usd=90_000.0,
        )
        q1 = report.installments[0]
        self.assertEqual(q1.statutory_due_date, dt.date(2017, 4, 15))
        self.assertEqual(q1.due_date, dt.date(2017, 4, 18))
        self.assertEqual(q1.due_date_description, "April 18, 2017")

    def test_no_adjusted_date_ever_lands_on_a_weekend_or_holiday(self):
        for year in range(2005, 2061):
            for d in adjusted_installment_due_dates(year):
                self.assertLess(d.weekday(), 5, f"{d} is a weekend")
                self.assertNotIn(d, dc_legal_holidays(d.year), f"{d} is a holiday")


class TestInputValidation(unittest.TestCase):

    def setUp(self):
        self.engine = EstimatedTaxSchedulerEngine()

    def _call(self, **kw):
        params = dict(
            trader_id="T", tax_year=2026,
            prior_year_agi_usd=200_000.0,
            prior_year_tax_usd=50_000.0,
            projected_current_year_tax_usd=90_000.0,
        )
        params.update(kw)
        return self.engine.generate_estimated_tax_schedule(**params)

    def test_negative_tax_rejected(self):
        with self.assertRaises(EstimatedTaxError):
            self._call(prior_year_tax_usd=-1.0)

    def test_nan_rejected(self):
        with self.assertRaises(EstimatedTaxError):
            self._call(projected_current_year_tax_usd=float("nan"))

    def test_infinity_rejected(self):
        with self.assertRaises(EstimatedTaxError):
            self._call(prior_year_agi_usd=float("inf"))

    def test_empty_trader_id_rejected(self):
        with self.assertRaises(EstimatedTaxError):
            self._call(trader_id="   ")

    def test_unknown_filing_status_rejected(self):
        with self.assertRaises(EstimatedTaxError):
            self._call(filing_status="SINGLE_TRADER")

    def test_too_many_payments_rejected(self):
        with self.assertRaises(EstimatedTaxError):
            self._call(payments_made_usd=[1.0, 2.0, 3.0, 4.0, 5.0])

    def test_negative_payment_rejected(self):
        with self.assertRaises(EstimatedTaxError):
            self._call(payments_made_usd=[1.0, -2.0])

    def test_unsupported_tax_year_rejected(self):
        with self.assertRaises(EstimatedTaxError):
            self._call(tax_year=1999)

    def test_non_integer_tax_year_rejected(self):
        with self.assertRaises(EstimatedTaxError):
            self._call(tax_year=2026.0)

    def test_bad_as_of_date_type_rejected(self):
        with self.assertRaises(EstimatedTaxError):
            self._call(as_of_date="2026-04-15")

    def test_valid_call_with_all_defaults_succeeds(self):
        report = self._call(filing_status=FILING_STATUS_SINGLE)
        self.assertEqual(report.tax_year, 2026)
        self.assertIn("6654", report.audit_notes)


if __name__ == "__main__":
    unittest.main()

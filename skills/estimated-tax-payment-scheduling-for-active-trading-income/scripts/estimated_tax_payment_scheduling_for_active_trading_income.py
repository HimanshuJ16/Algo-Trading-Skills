import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class QuarterlyTaxInstallment:
    quarter_name: str                   # 'Q1', 'Q2', 'Q3', 'Q4'
    due_date_description: str           # e.g. 'April 15'
    required_payment_usd: float
    status: str                         # 'SCHEDULED', 'PAID', 'OVERDUE'

@dataclass
class EstimatedTaxScheduleReport:
    trader_id: str
    tax_year: int
    prior_year_agi_usd: float
    prior_year_tax_usd: float
    projected_current_year_tax_usd: float
    applied_safe_harbor_pct: float       # 100.0% or 110.0%
    safe_harbor_prior_year_usd: float
    safe_harbor_current_year_90pct_usd: float
    required_annual_tax_payment_usd: float
    quarterly_installment_usd: float
    installments: List[QuarterlyTaxInstallment]
    is_safe_harbor_compliant: bool
    audit_notes: str

class EstimatedTaxSchedulerEngine:
    """
    Quantitative tax accounting engine for calculating IRS quarterly estimated tax payments (Form 1040-ES),
    prior-year safe harbor rules (100%/110%), and trading capital tax reserve scheduling.
    """
    def __init__(self, high_agi_threshold_usd: float = 150_000.0):
        self.high_agi_threshold_usd = high_agi_threshold_usd

    def generate_estimated_tax_schedule(
        self,
        trader_id: str,
        tax_year: int,
        prior_year_agi_usd: float,
        prior_year_tax_usd: float,
        projected_current_year_tax_usd: float,
        paid_installments_count: int = 0
    ) -> EstimatedTaxScheduleReport:
        """
        Calculates IRS safe harbor targets and quarterly estimated tax installment schedule.
        """
        # Rule 1: High AGI (> $150k) requires 110% of prior year tax; else 100%
        safe_harbor_pct = 110.0 if prior_year_agi_usd > self.high_agi_threshold_usd else 100.0
        safe_harbor_prior = round(prior_year_tax_usd * (safe_harbor_pct / 100.0), 2)

        # Rule 2: Current year safe harbor is 90% of projected current year tax
        safe_harbor_current = round(projected_current_year_tax_usd * 0.90, 2)

        # Total required annual payment is the lesser of the two safe harbors
        required_annual = min(safe_harbor_prior, safe_harbor_current)
        quarterly_amt = round(required_annual / 4.0, 2)

        deadlines = [
            ("Q1", "April 15"),
            ("Q2", "June 15"),
            ("Q3", "September 15"),
            ("Q4", f"January 15, {tax_year + 1}")
        ]

        installments: List[QuarterlyTaxInstallment] = []
        for idx, (q_name, due_str) in enumerate(deadlines):
            st = "PAID" if idx < paid_installments_count else "SCHEDULED"
            installments.append(QuarterlyTaxInstallment(
                quarter_name=q_name,
                due_date_description=due_str,
                required_payment_usd=quarterly_amt,
                status=st
            ))

        notes = (
            f"ESTIMATED TAX SCHEDULE [{trader_id} - {tax_year}]: Required annual payment = ${required_annual:,.2f} "
            f"(${quarterly_amt:,.2f}/quarter). Applied Safe Harbor: {safe_harbor_pct}% Prior Year (${safe_harbor_prior:,.2f}) vs 90% Current Year (${safe_harbor_current:,.2f})."
        )
        logger.info(notes)

        return EstimatedTaxScheduleReport(
            trader_id=trader_id,
            tax_year=tax_year,
            prior_year_agi_usd=prior_year_agi_usd,
            prior_year_tax_usd=prior_year_tax_usd,
            projected_current_year_tax_usd=projected_current_year_tax_usd,
            applied_safe_harbor_pct=safe_harbor_pct,
            safe_harbor_prior_year_usd=safe_harbor_prior,
            safe_harbor_current_year_90pct_usd=safe_harbor_current,
            required_annual_tax_payment_usd=required_annual,
            quarterly_installment_usd=quarterly_amt,
            installments=installments,
            is_safe_harbor_compliant=True,
            audit_notes=notes
        )

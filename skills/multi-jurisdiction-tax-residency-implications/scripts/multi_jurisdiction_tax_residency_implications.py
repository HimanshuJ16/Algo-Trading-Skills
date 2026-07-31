import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class TaxEntityProfile:
    entity_id: str
    primary_incorporation_country: str
    days_spent_per_country: Dict[str, int]
    has_permanent_establishment: Dict[str, bool] = field(default_factory=dict)
    poem_country: Optional[str] = None    # Place of Effective Management

@dataclass
class CrossBorderTradeIncome:
    source_country: str
    destination_country: str
    income_type: str                      # 'CAPITAL_GAINS', 'DIVIDEND', 'INTEREST'
    gross_income_usd: float
    statutory_foreign_wht_rate: float     # e.g. 0.30 (30% US statutory WHT)
    dtaa_treaty_wht_rate: float           # e.g. 0.15 (15% US-UK treaty WHT)
    destination_corp_tax_rate: float     # e.g. 0.25 (25% UK corporate tax)

@dataclass
class TaxResidencyReport:
    entity_id: str
    tax_resident_countries: List[str]
    dual_residency_flag: bool
    poem_exposure_flag: bool
    gross_income_usd: float
    foreign_tax_paid_usd: float
    domestic_tax_liability_usd: float
    foreign_tax_credit_usd: float
    net_tax_payable_usd: float
    status: str                          # 'TAX_AUDIT_SUCCESS'
    audit_notes: str

class MultiJurisdictionTaxEngine:
    """
    Multi-jurisdiction tax residency audit engine evaluating physical presence (183-day rule),
    Place of Effective Management (POEM), DTAA treaty withholding tax, and Foreign Tax Credit (FTC) offsets.
    """
    def __init__(self):
        pass

    def evaluate_tax_residency(
        self, profile: TaxEntityProfile, income_events: List[CrossBorderTradeIncome]
    ) -> TaxResidencyReport:
        """
        Audits physical presence 183-day rule, POEM exposure, DTAA treaty WHT reductions,
        and computes Foreign Tax Credit (FTC) offsets.
        """
        # 1. 183-Day Physical Presence Test & POEM Audit
        residency_countries: List[str] = [profile.primary_incorporation_country.upper()]

        for country, days in profile.days_spent_per_country.items():
            cc = country.upper()
            if days >= 183 and cc not in residency_countries:
                residency_countries.append(cc)

        poem_cc = profile.poem_country.upper() if profile.poem_country else None
        if poem_cc and poem_cc not in residency_countries:
            residency_countries.append(poem_cc)

        dual_residency = len(residency_countries) > 1
        poem_exposure = bool(poem_cc and poem_cc != profile.primary_incorporation_country.upper())

        # 2. Income & Foreign Tax Credit (FTC) Computations
        total_gross_usd = 0.0
        total_foreign_tax_usd = 0.0
        total_domestic_liability_usd = 0.0
        total_ftc_usd = 0.0
        total_net_payable_usd = 0.0

        for inc in income_events:
            total_gross_usd += inc.gross_income_usd

            # Apply DTAA Treaty WHT rate if treaty exists, else statutory rate
            effective_wht_rate = min(inc.statutory_foreign_wht_rate, inc.dtaa_treaty_wht_rate)
            foreign_tax = inc.gross_income_usd * effective_wht_rate
            total_foreign_tax_usd += foreign_tax

            # Domestic Tax Liability on Gross Foreign Income
            dom_tax = inc.gross_income_usd * inc.destination_corp_tax_rate
            total_domestic_liability_usd += dom_tax

            # FTC = min(Foreign Tax Paid, Domestic Tax Liability)
            ftc = min(foreign_tax, dom_tax)
            total_ftc_usd += ftc

            net_payable = max(0.0, dom_tax - ftc)
            total_net_payable_usd += net_payable

        notes = (
            f"TAX RESIDENCY AUDITED [{profile.entity_id}]: Resident Countries = {residency_countries}, "
            f"Dual Residency = {dual_residency}, POEM Exposure = {poem_exposure}. "
            f"Gross Income = ${total_gross_usd:,.2f}, Foreign WHT Paid = ${total_foreign_tax_usd:,.2f}, "
            f"FTC Relief = ${total_ftc_usd:,.2f}, Net Domestic Tax Payable = ${total_net_payable_usd:,.2f}."
        )
        logger.info(notes)

        return TaxResidencyReport(
            entity_id=profile.entity_id,
            tax_resident_countries=residency_countries,
            dual_residency_flag=dual_residency,
            poem_exposure_flag=poem_exposure,
            gross_income_usd=round(total_gross_usd, 2),
            foreign_tax_paid_usd=round(total_foreign_tax_usd, 2),
            domestic_tax_liability_usd=round(total_domestic_liability_usd, 2),
            foreign_tax_credit_usd=round(total_ftc_usd, 2),
            net_tax_payable_usd=round(total_net_payable_usd, 2),
            status="TAX_AUDIT_SUCCESS",
            audit_notes=notes
        )

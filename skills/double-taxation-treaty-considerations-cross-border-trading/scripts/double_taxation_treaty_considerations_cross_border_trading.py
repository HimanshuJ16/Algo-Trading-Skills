import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class DttTreatySpec:
    residence_country: str               # e.g. 'UK', 'SG', 'CA', 'KY'
    source_country: str                  # e.g. 'US', 'DE', 'IN', 'JP'
    statutory_wht_pct: float            # e.g. 0.30 (30% US statutory dividend WHT)
    treaty_wht_pct: float               # e.g. 0.15 (15% US-UK DTT reduced rate)
    required_documentation: str         # e.g. 'Form W-8BEN-E' or 'TRC'

@dataclass
class CrossBorderIncomePayment:
    payment_id: str
    residence_country: str
    source_country: str
    income_type: str                     # 'EQUITY_DIVIDEND', 'INTEREST', 'SECTION_871M_SWAP'
    gross_income_usd: float
    has_valid_tax_documentation: bool   # True if active W-8BEN-E / TRC on file
    resident_country_effective_tax_rate: float = 0.20

@dataclass
class DoubleTaxationAuditReport:
    payment_id: str
    residence_country: str
    source_country: str
    gross_income_usd: float
    applied_wht_pct: float
    wht_tax_paid_usd: float
    statutory_wht_usd: float
    wht_tax_leakage_saved_usd: float
    eligible_foreign_tax_credit_usd: float
    required_action: str
    applied_treaty_notes: str

class DoubleTaxationTreatyEngine:
    """
    Quantitative cross-border tax accounting engine for evaluating bilateral Double Taxation Treaties (DTT/DTAA),
    calculating dividend withholding tax (WHT) reductions, tax leakage savings, and Foreign Tax Credit (FTC) claims.
    """
    def __init__(self):
        self.treaties: Dict[str, DttTreatySpec] = {}

    def register_treaty(self, treaty: DttTreatySpec):
        key = f"{treaty.residence_country.upper()}_{treaty.source_country.upper()}"
        self.treaties[key] = treaty

    def evaluate_cross_border_payment(self, payment: CrossBorderIncomePayment) -> DoubleTaxationAuditReport:
        """
        Audits cross-border income payment against DTT rules and calculates WHT and Foreign Tax Credit (FTC).
        """
        key = f"{payment.residence_country.upper()}_{payment.source_country.upper()}"
        treaty = self.treaties.get(key)

        if treaty is None:
            # Fallback default: 30% statutory WHT, no treaty relief
            applied_wht_pct = 0.30
            notes = f"NO DTT TREATY FOUND between {payment.residence_country} and {payment.source_country}. Standard 30% WHT applied."
            req_action = "Inquire with tax counsel regarding bilateral tax treaty eligibility."
        else:
            if payment.has_valid_tax_documentation:
                applied_wht_pct = treaty.treaty_wht_pct
                notes = f"DTT TREATY APPLIED: Reduced WHT rate of {applied_wht_pct*100:.1f}% confirmed under {payment.residence_country}-{payment.source_country} treaty."
                req_action = "Maintain active tax documentation on file."
            else:
                applied_wht_pct = treaty.statutory_wht_pct
                notes = f"MISSING TAX DOCUMENTATION ({treaty.required_documentation}): Fallback to statutory WHT rate of {applied_wht_pct*100:.1f}%."
                req_action = f"File {treaty.required_documentation} immediately to unlock reduced {treaty.treaty_wht_pct*100:.1f}% DTT rate."

        gross = payment.gross_income_usd
        wht_paid = round(gross * applied_wht_pct, 2)
        stat_wht = round(gross * (treaty.statutory_wht_pct if treaty else 0.30), 2)
        savings = max(0.0, round(stat_wht - wht_paid, 2))

        # Foreign Tax Credit (FTC) is capped at the lesser of foreign WHT paid or resident country tax limit
        resident_tax_limit = round(gross * payment.resident_country_effective_tax_rate, 2)
        eligible_ftc = round(min(wht_paid, resident_tax_limit), 2)

        if savings > 0:
            logger.info(f"DOUBLE TAXATION RELIEF [{payment.payment_id}]: Saved ${savings:,.2f} USD WHT via DTT rate ({applied_wht_pct*100:.1f}%).")

        return DoubleTaxationAuditReport(
            payment_id=payment.payment_id,
            residence_country=payment.residence_country,
            source_country=payment.source_country,
            gross_income_usd=gross,
            applied_wht_pct=applied_wht_pct,
            wht_tax_paid_usd=wht_paid,
            statutory_wht_usd=stat_wht,
            wht_tax_leakage_saved_usd=savings,
            eligible_foreign_tax_credit_usd=eligible_ftc,
            required_action=req_action,
            applied_treaty_notes=notes
        )

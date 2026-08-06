import logging
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class ServiceCategory(Enum):
    EXCHANGE_EXECUTION_FEE = "EXCHANGE_EXECUTION_FEE"  # Exempt financial service in UK/EU
    CLEARING_FEE = "CLEARING_FEE"                      # Exempt financial service
    BROKERAGE_COMMISSION = "BROKERAGE_COMMISSION"      # Exempt financial intermediation
    COLOCATION_DATA_FEED = "COLOCATION_DATA_FEED"      # Standard-rated IT/data service
    SOFTWARE_LICENSE = "SOFTWARE_LICENSE"              # Standard-rated software service
    MANAGEMENT_FEE = "MANAGEMENT_FEE"                  # Standard-rated advisory/management


class VATTreatment(Enum):
    EXEMPT = "EXEMPT"                     # 0% VAT, no input tax recovery
    STANDARD_RATED = "STANDARD_RATED"     # Standard rate (e.g. 20% UK, 19% DE)
    ZERO_RATED = "ZERO_RATED"             # 0% VAT, 100% input tax recovery
    REVERSE_CHARGE = "REVERSE_CHARGE"     # Self-assessed RCM for cross-border services
    OUT_OF_SCOPE = "OUT_OF_SCOPE"         # Outside scope of VAT (e.g. US vendor to US entity)


class Jurisdiction(Enum):
    UK_HMRC = "UK_HMRC"                 # Standard VAT 20%
    EU_GERMANY = "EU_GERMANY"           # Standard VAT 19%
    EU_IRELAND = "EU_IRELAND"           # Standard VAT 23%
    SINGAPORE_IRAS = "SINGAPORE_IRAS"   # Standard GST 9%
    AUSTRALIA_ATO = "AUSTRALIA_ATO"     # Standard GST 10%
    US_NON_VAT = "US_NON_VAT"           # US State Sales Tax regime (no VAT)


class VATEngineError(Exception):
    """Base exception for VAT/GST tax engine processing errors."""
    pass


@dataclass
class TradingExpenseInvoice:
    invoice_id: str
    vendor_name: str
    vendor_jurisdiction: Jurisdiction
    entity_jurisdiction: Jurisdiction
    service_category: ServiceCategory
    net_amount_usd: float
    invoice_date: datetime.date = field(default_factory=datetime.date.today)


@dataclass
class VATTaxAssessment:
    invoice_id: str
    vat_treatment: VATTreatment
    vat_rate_pct: float
    output_vat_usd: float
    input_vat_paid_usd: float
    reverse_charge_vat_usd: float
    recoverable_input_vat_usd: float
    unrecoverable_vat_expense_usd: float


@dataclass
class VATReturnSummary:
    total_net_expense_usd: float
    total_input_vat_paid_usd: float
    total_reverse_charge_vat_usd: float
    total_recoverable_input_vat_usd: float
    total_unrecoverable_vat_expense_usd: float
    partial_exemption_recovery_ratio_pct: float


class VATGSTTradingServicesEngine:
    """
    Institutional VAT/GST Tax Assessment Engine for Trading-Related Services.
    
    Determines VAT/GST tax treatment (Exempt, Standard-Rated, Reverse Charge Mechanism - RCM),
    applies jurisdiction rules (UK HMRC, EU Directives, Singapore IRAS, Australia ATO),
    calculates Partial Exemption recovery ratios, and generates VAT return reports.
    """

    # Jurisdiction standard VAT/GST rates
    STANDARD_RATES = {
        Jurisdiction.UK_HMRC: 20.0,
        Jurisdiction.EU_GERMANY: 19.0,
        Jurisdiction.EU_IRELAND: 23.0,
        Jurisdiction.SINGAPORE_IRAS: 9.0,
        Jurisdiction.AUSTRALIA_ATO: 10.0,
        Jurisdiction.US_NON_VAT: 0.0,
    }

    # Financial services exempt from VAT under UK VATA 1994 Group 5 & EU Directive Art 135(1)
    EXEMPT_FINANCIAL_SERVICES = {
        ServiceCategory.EXCHANGE_EXECUTION_FEE,
        ServiceCategory.CLEARING_FEE,
        ServiceCategory.BROKERAGE_COMMISSION,
    }

    def __init__(self, partial_exemption_recovery_ratio_pct: float = 15.0):
        """
        :param partial_exemption_recovery_ratio_pct: Pro-rata input VAT recovery % for trading entity
                                                    (e.g. 15% recovery due to high exempt trading income).
        """
        if not (0.0 <= partial_exemption_recovery_ratio_pct <= 100.0):
            raise VATEngineError("Partial Exemption Recovery Ratio must be between 0% and 100%.")

        self.recovery_ratio_pct = partial_exemption_recovery_ratio_pct
        logger.info(f"Initialized VAT/GST Engine (Partial Exemption Recovery Ratio={self.recovery_ratio_pct:.2f}%)")

    def set_partial_exemption_ratio(self, taxable_supplies_usd: float, exempt_supplies_usd: float) -> float:
        """Calculates and sets the Pro-Rata Partial Exemption Recovery Ratio."""
        total = taxable_supplies_usd + exempt_supplies_usd
        if total <= 0:
            raise VATEngineError("Total supplies must be greater than zero.")

        ratio = (taxable_supplies_usd / total) * 100.0
        self.recovery_ratio_pct = ratio
        logger.info(f"Updated Partial Exemption Recovery Ratio: {ratio:.2f}% (Taxable=${taxable_supplies_usd:,.2f}, Exempt=${exempt_supplies_usd:,.2f})")
        return ratio

    def assess_invoice_tax(self, invoice: TradingExpenseInvoice) -> VATTaxAssessment:
        """
        Assesses VAT/GST tax treatment for a trading expense invoice.
        """
        if invoice.net_amount_usd <= 0:
            raise VATEngineError("Invoice net amount must be positive.")

        entity_jur = invoice.entity_jurisdiction
        vendor_jur = invoice.vendor_jurisdiction
        std_rate = self.STANDARD_RATES.get(entity_jur, 0.0)

        # 1. Exempt Financial Services (Exchange fees, Clearing, Brokerage)
        if invoice.service_category in self.EXEMPT_FINANCIAL_SERVICES:
            logger.info(f"VAT Assessment [{invoice.invoice_id}]: EXEMPT Financial Service ({invoice.service_category.value})")
            return VATTaxAssessment(
                invoice_id=invoice.invoice_id,
                vat_treatment=VATTreatment.EXEMPT,
                vat_rate_pct=0.0,
                output_vat_usd=0.0,
                input_vat_paid_usd=0.0,
                reverse_charge_vat_usd=0.0,
                recoverable_input_vat_usd=0.0,
                unrecoverable_vat_expense_usd=0.0,
            )

        # 2. Cross-Border Supply of Taxable Services -> Reverse Charge Mechanism (RCM)
        is_cross_border = (vendor_jur != entity_jur) and (entity_jur != Jurisdiction.US_NON_VAT)

        if is_cross_border:
            # Recipient self-assesses Output VAT and Input VAT under RCM
            rcm_vat = invoice.net_amount_usd * (std_rate / 100.0)
            recoverable = rcm_vat * (self.recovery_ratio_pct / 100.0)
            unrecoverable = rcm_vat - recoverable

            logger.info(
                f"VAT Assessment [{invoice.invoice_id}]: REVERSE CHARGE MECHANISM (RCM) "
                f"[{vendor_jur.value} -> {entity_jur.value}]: RCM VAT=${rcm_vat:,.2f}, Recoverable=${recoverable:,.2f}"
            )
            return VATTaxAssessment(
                invoice_id=invoice.invoice_id,
                vat_treatment=VATTreatment.REVERSE_CHARGE,
                vat_rate_pct=std_rate,
                output_vat_usd=0.0,
                input_vat_paid_usd=0.0,
                reverse_charge_vat_usd=rcm_vat,
                recoverable_input_vat_usd=recoverable,
                unrecoverable_vat_expense_usd=unrecoverable,
            )

        # 3. Domestic Standard-Rated Taxable Services (Co-location, Software, Management)
        if entity_jur == Jurisdiction.US_NON_VAT:
            treatment = VATTreatment.OUT_OF_SCOPE
            rate = 0.0
            vat_paid = 0.0
            recoverable = 0.0
            unrecoverable = 0.0
        else:
            treatment = VATTreatment.STANDARD_RATED
            rate = std_rate
            vat_paid = invoice.net_amount_usd * (rate / 100.0)
            recoverable = vat_paid * (self.recovery_ratio_pct / 100.0)
            unrecoverable = vat_paid - recoverable

        logger.info(
            f"VAT Assessment [{invoice.invoice_id}]: STANDARD RATED [{entity_jur.value}]: "
            f"VAT Paid=${vat_paid:,.2f}, Recoverable=${recoverable:,.2f}, Unrecoverable=${unrecoverable:,.2f}"
        )
        return VATTaxAssessment(
            invoice_id=invoice.invoice_id,
            vat_treatment=treatment,
            vat_rate_pct=rate,
            output_vat_usd=0.0,
            input_vat_paid_usd=vat_paid,
            reverse_charge_vat_usd=0.0,
            recoverable_input_vat_usd=recoverable,
            unrecoverable_vat_expense_usd=unrecoverable,
        )

    def generate_vat_return_summary(self, invoices: List[TradingExpenseInvoice]) -> VATReturnSummary:
        """
        Generates VAT/GST return summary across a batch of trading expense invoices.
        """
        total_net = 0.0
        total_input_paid = 0.0
        total_rcm = 0.0
        total_recoverable = 0.0
        total_unrecoverable = 0.0

        for inv in invoices:
            ass = self.assess_invoice_tax(inv)
            total_net += inv.net_amount_usd
            total_input_paid += ass.input_vat_paid_usd
            total_rcm += ass.reverse_charge_vat_usd
            total_recoverable += ass.recoverable_input_vat_usd
            total_unrecoverable += ass.unrecoverable_vat_expense_usd

        return VATReturnSummary(
            total_net_expense_usd=total_net,
            total_input_vat_paid_usd=total_input_paid,
            total_reverse_charge_vat_usd=total_rcm,
            total_recoverable_input_vat_usd=total_recoverable,
            total_unrecoverable_vat_expense_usd=total_unrecoverable,
            partial_exemption_recovery_ratio_pct=self.recovery_ratio_pct,
        )


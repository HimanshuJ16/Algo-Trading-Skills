"""VAT/GST determination for trading-related service expenditure.

Classifies accounts-payable invoices for a trading entity into exempt financial
supplies, standard-rated supplies and cross-border reverse-charge supplies,
applies a partial exemption recovery ratio to the input tax, and aggregates the
period figures needed to populate a VAT/GST return.

Jurisdictions modelled: UK (HMRC), Germany and Ireland (EU VAT Directive
2006/112/EC), Singapore (IRAS) and Australia (ATO). US entities are treated as
outside the scope of VAT/GST.

The engine produces *indicative* determinations from the category and the two
jurisdictions supplied on each invoice. It is not a substitute for a
place-of-supply analysis of the underlying contract, and it does not convert
currencies: every amount is assumed to be expressed in one currency chosen by
the caller, whereas a return is filed in the currency of the tax jurisdiction.
"""

import collections.abc
import datetime
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Iterable, List, Tuple

logger = logging.getLogger(__name__)


class ServiceCategory(Enum):
    """Category of the service being invoiced.

    The financial-supply categories reflect the exemptions in UK VATA 1994
    Sch 9 Grp 5 and EU Directive 2006/112/EC Art 135(1)(d)/(f). Exchange
    *membership and connectivity* charges are a separate, standard-rated
    category: HMRC VAT Notice 701/49 para 6.9 states that basic admission or
    membership charges to an exchange are taxable at the standard rate, and
    that the liability of other exchange charges depends on what the exchange
    actually does for the fee.
    """

    EXCHANGE_EXECUTION_FEE = "EXCHANGE_EXECUTION_FEE"                              # Exempt financial intermediation
    CLEARING_FEE = "CLEARING_FEE"                                                  # Exempt (Notice 701/49 para 2.8)
    BROKERAGE_COMMISSION = "BROKERAGE_COMMISSION"                                  # Exempt financial intermediation
    EXCHANGE_MEMBERSHIP_CONNECTIVITY_FEE = "EXCHANGE_MEMBERSHIP_CONNECTIVITY_FEE"  # Standard-rated (para 6.9)
    COLOCATION_DATA_FEED = "COLOCATION_DATA_FEED"                                  # Standard-rated IT/data service
    SOFTWARE_LICENSE = "SOFTWARE_LICENSE"                                          # Standard-rated software service
    MANAGEMENT_FEE = "MANAGEMENT_FEE"                                              # Standard-rated advisory/management


class VATTreatment(Enum):
    EXEMPT = "EXEMPT"                     # 0% VAT; attributable input tax is not recoverable
    STANDARD_RATED = "STANDARD_RATED"     # Domestic standard rate (e.g. 20% UK, 19% DE)
    ZERO_RATED = "ZERO_RATED"             # 0% VAT with full recovery (not produced by this engine)
    REVERSE_CHARGE = "REVERSE_CHARGE"     # Recipient self-assesses output and input VAT
    OUT_OF_SCOPE = "OUT_OF_SCOPE"         # Outside the scope of VAT/GST for the recipient


class Jurisdiction(Enum):
    UK_HMRC = "UK_HMRC"                 # Standard VAT 20%
    EU_GERMANY = "EU_GERMANY"           # Standard VAT 19%
    EU_IRELAND = "EU_IRELAND"           # Standard VAT 23%
    SINGAPORE_IRAS = "SINGAPORE_IRAS"   # Standard GST 9% (since 1 Jan 2024)
    AUSTRALIA_ATO = "AUSTRALIA_ATO"     # Standard GST 10%
    US_NON_VAT = "US_NON_VAT"           # US state sales tax regime (no federal VAT)


class PartialExemptionRounding(Enum):
    """Statutory rounding applied to a computed pro-rata recovery percentage.

    ``NONE``
        No rounding. Appropriate for Singapore and Australia, which do not
        prescribe the EU/UK rounding-up rule.
    ``UP_WHOLE_PERCENT``
        Round up to the next whole percentage point. UK VAT Regulations 1995
        (SI 1995/2518) reg 101(4); EU Directive 2006/112/EC Art 175(1)
        ("rounded up to a figure not exceeding the next whole number").
    ``UP_TWO_DECIMALS``
        Round up to two decimal places. UK reg 101(5) applies this instead of
        reg 101(4) where residual input tax exceeds GBP 400,000 per month on
        average.
    """

    NONE = "NONE"
    UP_WHOLE_PERCENT = "UP_WHOLE_PERCENT"
    UP_TWO_DECIMALS = "UP_TWO_DECIMALS"


class VATEngineError(Exception):
    """Base exception for VAT/GST tax engine processing errors."""
    pass


@dataclass
class TradingExpenseInvoice:
    """One purchase invoice from the accounts-payable ledger.

    :param net_amount_usd: VAT-exclusive amount. Must be finite and positive;
        credit notes and refunds are out of scope for this engine.
    :param vendor_jurisdiction: Where the *supplier* belongs.
    :param entity_jurisdiction: Where the *recipient* (the trading entity)
        belongs. This drives the rate, because the modelled services follow the
        B2B general place-of-supply rule (VATA 1994 s.7A; Directive Art 44).
    """

    invoice_id: str
    vendor_name: str
    vendor_jurisdiction: Jurisdiction
    entity_jurisdiction: Jurisdiction
    service_category: ServiceCategory
    net_amount_usd: float
    invoice_date: datetime.date = field(default_factory=datetime.date.today)


@dataclass
class VATTaxAssessment:
    """Determination for a single invoice.

    :param output_vat_usd: VAT the *recipient* must declare as output tax
        (UK return Box 1). Non-zero only under the reverse charge.
    :param input_vat_paid_usd: VAT actually charged by the supplier on the
        invoice. Zero under the reverse charge, where the supplier charges no
        VAT and the recipient self-assesses instead.
    :param reverse_charge_vat_usd: Self-assessed reverse-charge VAT. Equal to
        ``output_vat_usd`` when the reverse charge applies; kept as a separate
        field so reverse-charge volume can be reported on its own.
    :param recoverable_input_vat_usd: Input tax deductible after the partial
        exemption ratio (UK return Box 4).
    :param unrecoverable_vat_expense_usd: Irrecoverable balance, posted to PnL.
    :param warnings: Determination caveats the caller must resolve manually.
    """

    invoice_id: str
    vat_treatment: VATTreatment
    vat_rate_pct: float
    output_vat_usd: float
    input_vat_paid_usd: float
    reverse_charge_vat_usd: float
    recoverable_input_vat_usd: float
    unrecoverable_vat_expense_usd: float
    warnings: Tuple[str, ...] = ()


@dataclass
class VATReturnSummary:
    """Period aggregate across an invoice batch.

    :param net_vat_payable_usd: ``total_output_vat_usd`` less
        ``total_recoverable_input_vat_usd``. This covers the purchase ledger
        only - output VAT on the entity's own sales is not modelled here and
        must be added before the return is filed. A negative figure is a
        repayment position.
    :param assessments: Per-invoice determinations, in input order, for the
        audit trail.
    """

    total_net_expense_usd: float
    total_input_vat_paid_usd: float
    total_reverse_charge_vat_usd: float
    total_recoverable_input_vat_usd: float
    total_unrecoverable_vat_expense_usd: float
    partial_exemption_recovery_ratio_pct: float
    total_output_vat_usd: float = 0.0
    net_vat_payable_usd: float = 0.0
    assessments: Tuple[VATTaxAssessment, ...] = ()


class VATGSTTradingServicesEngine:
    """VAT/GST assessment engine for trading-related service expenditure.

    Determines the treatment (exempt, standard-rated, reverse charge, out of
    scope), applies the entity's partial exemption recovery ratio to input tax,
    and aggregates a period return summary.
    """

    # Standard VAT/GST rates, verified current for 2026.
    STANDARD_RATES: Dict[Jurisdiction, float] = {
        Jurisdiction.UK_HMRC: 20.0,
        Jurisdiction.EU_GERMANY: 19.0,
        Jurisdiction.EU_IRELAND: 23.0,
        Jurisdiction.SINGAPORE_IRAS: 9.0,
        Jurisdiction.AUSTRALIA_ATO: 10.0,
        Jurisdiction.US_NON_VAT: 0.0,
    }

    # Financial services exempt under UK VATA 1994 Sch 9 Grp 5 and EU Art 135(1).
    EXEMPT_FINANCIAL_SERVICES = frozenset({
        ServiceCategory.EXCHANGE_EXECUTION_FEE,
        ServiceCategory.CLEARING_FEE,
        ServiceCategory.BROKERAGE_COMMISSION,
    })

    # Jurisdictions whose reverse charge is conditional on the recipient NOT
    # being entitled to a full input tax credit: Singapore (reverse charge
    # regime for imported services) and Australia (GST Act Div 84, s.84-5,
    # which requires the acquisition to be other than solely for a creditable
    # purpose). UK s.8 and EU Art 196 apply regardless of recovery position.
    CONDITIONAL_REVERSE_CHARGE_JURISDICTIONS = frozenset({
        Jurisdiction.SINGAPORE_IRAS,
        Jurisdiction.AUSTRALIA_ATO,
    })

    def __init__(self, partial_exemption_recovery_ratio_pct: float = 15.0):
        """
        :param partial_exemption_recovery_ratio_pct: Pro-rata input VAT recovery
            percentage for the entity (e.g. 15% where trading income is largely
            exempt). Must be a finite number between 0 and 100.
        """
        self.recovery_ratio_pct = self._validate_ratio(
            partial_exemption_recovery_ratio_pct, "partial_exemption_recovery_ratio_pct"
        )
        logger.info(
            "Initialized VAT/GST Engine (Partial Exemption Recovery Ratio=%.2f%%)",
            self.recovery_ratio_pct,
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _require_finite(value: float, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise VATEngineError(f"{label} must be a real number, got {type(value).__name__}.")
        value = float(value)
        if math.isnan(value) or math.isinf(value):
            raise VATEngineError(f"{label} must be finite, got {value!r}.")
        return value

    @classmethod
    def _validate_ratio(cls, value: float, label: str) -> float:
        value = cls._require_finite(value, label)
        if not (0.0 <= value <= 100.0):
            raise VATEngineError(f"{label} must be between 0% and 100%, got {value!r}.")
        return value

    @staticmethod
    def _require_jurisdiction(value: Jurisdiction, label: str, invoice_id: str) -> Jurisdiction:
        """Reject a jurisdiction passed as a raw string or any non-enum value.

        A raw string compares unequal to every enum member, which silently
        routes a domestic invoice down the cross-border branch.
        """
        if not isinstance(value, Jurisdiction):
            raise VATEngineError(
                f"Invoice '{invoice_id}': {label} must be a Jurisdiction enum member, "
                f"got {type(value).__name__}."
            )
        return value

    @classmethod
    def _standard_rate(cls, jurisdiction: Jurisdiction, invoice_id: str) -> float:
        """Look up the standard rate, failing closed on an unmapped jurisdiction.

        A missing rate must never fall back to 0%: that silently converts a
        taxable supply into a nil assessment and under-declares the return.
        """
        cls._require_jurisdiction(jurisdiction, "entity_jurisdiction", invoice_id)
        try:
            return cls.STANDARD_RATES[jurisdiction]
        except KeyError:
            raise VATEngineError(
                f"Invoice '{invoice_id}': no standard VAT/GST rate configured for "
                f"{jurisdiction.value}. Add the rate to STANDARD_RATES rather than "
                f"assessing the supply at 0%."
            ) from None

    def _validate_invoice(self, invoice: TradingExpenseInvoice) -> None:
        if not isinstance(invoice, TradingExpenseInvoice):
            raise VATEngineError(
                f"Expected a TradingExpenseInvoice, got {type(invoice).__name__}."
            )
        if not isinstance(invoice.invoice_id, str) or not invoice.invoice_id.strip():
            raise VATEngineError("invoice_id must be a non-empty string.")
        if not isinstance(invoice.service_category, ServiceCategory):
            raise VATEngineError(
                f"Invoice '{invoice.invoice_id}': service_category must be a ServiceCategory "
                f"enum member, got {type(invoice.service_category).__name__}."
            )
        amount = self._require_finite(
            invoice.net_amount_usd, f"Invoice '{invoice.invoice_id}': net_amount_usd"
        )
        if amount <= 0:
            raise VATEngineError(
                f"Invoice '{invoice.invoice_id}': net_amount_usd must be positive, got {amount!r}. "
                f"Credit notes and refunds are out of scope for this engine."
            )

    # ------------------------------------------------------------------
    # Partial exemption
    # ------------------------------------------------------------------

    def set_partial_exemption_ratio(
        self,
        taxable_supplies_usd: float,
        exempt_supplies_usd: float,
        rounding: "PartialExemptionRounding" = PartialExemptionRounding.NONE,
    ) -> float:
        """Compute and store the pro-rata partial exemption recovery ratio.

        ``ratio = taxable / (taxable + exempt) * 100``, per UK VAT Regulations
        1995 reg 101(2)(d) and EU Directive 2006/112/EC Art 174(1).

        :param taxable_supplies_usd: VAT-exclusive turnover carrying a right of
            deduction. For a UK entity this includes exempt Group 5 financial
            supplies made to customers **outside the UK**, which are "specified
            supplies" carrying input tax recovery under the VAT (Input Tax)
            (Specified Supplies) Order 1999 (SI 1999/3121, made under VATA 1994
            s.26(2)(c)). Excluding them understates recovery.
        :param exempt_supplies_usd: VAT-exclusive turnover carrying no right of
            deduction.
        :param rounding: Statutory rounding to apply. Defaults to ``NONE``;
            UK and EU entities should pass ``UP_WHOLE_PERCENT`` (or
            ``UP_TWO_DECIMALS`` where UK reg 101(5) applies).
        :returns: The stored recovery percentage.
        """
        taxable = self._require_finite(taxable_supplies_usd, "taxable_supplies_usd")
        exempt = self._require_finite(exempt_supplies_usd, "exempt_supplies_usd")
        if taxable < 0 or exempt < 0:
            raise VATEngineError(
                f"Supplies must be non-negative (taxable={taxable!r}, exempt={exempt!r})."
            )
        if not isinstance(rounding, PartialExemptionRounding):
            raise VATEngineError(
                f"rounding must be a PartialExemptionRounding member, "
                f"got {type(rounding).__name__}."
            )

        total = taxable + exempt
        if total <= 0:
            raise VATEngineError("Total supplies must be greater than zero.")

        ratio = self._apply_rounding((taxable / total) * 100.0, rounding)
        self.recovery_ratio_pct = self._validate_ratio(ratio, "recovery ratio")
        logger.info(
            "Updated Partial Exemption Recovery Ratio: %.2f%% "
            "(Taxable=%.2f, Exempt=%.2f, rounding=%s)",
            self.recovery_ratio_pct, taxable, exempt, rounding.value,
        )
        return self.recovery_ratio_pct

    @staticmethod
    def _apply_rounding(ratio: float, rounding: "PartialExemptionRounding") -> float:
        """Round a percentage per the selected statutory rule, capped at 100%."""
        if rounding is PartialExemptionRounding.NONE:
            return ratio
        # Guard against binary-float residue turning an exact 20% into
        # 20.0000000001, which would otherwise gain a whole percentage point.
        if rounding is PartialExemptionRounding.UP_WHOLE_PERCENT:
            rounded = float(math.ceil(round(ratio, 9)))
        else:  # UP_TWO_DECIMALS
            rounded = math.ceil(round(ratio * 100.0, 7)) / 100.0
        return min(rounded, 100.0)

    # ------------------------------------------------------------------
    # Assessment
    # ------------------------------------------------------------------

    def assess_invoice_tax(self, invoice: TradingExpenseInvoice) -> VATTaxAssessment:
        """Assess the VAT/GST treatment of one trading expense invoice."""
        self._validate_invoice(invoice)

        entity_jur = invoice.entity_jurisdiction
        vendor_jur = invoice.vendor_jurisdiction
        # The recipient's jurisdiction sets the rate under the B2B general rule,
        # so only it needs a mapped rate; the vendor side only needs to be a
        # real enum member so the cross-border comparison below is meaningful.
        std_rate = self._standard_rate(entity_jur, invoice.invoice_id)
        self._require_jurisdiction(vendor_jur, "vendor_jurisdiction", invoice.invoice_id)
        warnings: List[str] = []

        # 1. Exempt financial services (execution, clearing, brokerage). An
        #    exempt supply received from abroad carries no reverse charge, so
        #    this branch is decided before the cross-border test.
        if invoice.service_category in self.EXEMPT_FINANCIAL_SERVICES:
            if invoice.service_category is ServiceCategory.EXCHANGE_EXECUTION_FEE:
                warnings.append(
                    "Exchange invoices commonly bundle standard-rated membership, port, "
                    "connectivity and technology charges with exempt intermediation "
                    "(HMRC VAT Notice 701/49 para 6.9). Split the invoice and book the "
                    "standard-rated element as EXCHANGE_MEMBERSHIP_CONNECTIVITY_FEE."
                )
            logger.info(
                "VAT Assessment [%s]: EXEMPT financial service (%s)",
                invoice.invoice_id, invoice.service_category.value,
            )
            return VATTaxAssessment(
                invoice_id=invoice.invoice_id,
                vat_treatment=VATTreatment.EXEMPT,
                vat_rate_pct=0.0,
                output_vat_usd=0.0,
                input_vat_paid_usd=0.0,
                reverse_charge_vat_usd=0.0,
                recoverable_input_vat_usd=0.0,
                unrecoverable_vat_expense_usd=0.0,
                warnings=tuple(warnings),
            )

        # 2. Cross-border supply of a taxable service -> reverse charge.
        is_cross_border = (vendor_jur != entity_jur) and (entity_jur != Jurisdiction.US_NON_VAT)

        if is_cross_border and self._reverse_charge_applies(entity_jur, warnings):
            if invoice.service_category is ServiceCategory.COLOCATION_DATA_FEED:
                warnings.append(
                    "Co-location reverse-charged on the B2B general rule. Where the contract "
                    "grants exclusive use of a defined space (a dedicated cage or suite), it "
                    "may instead be a service connected with immovable property, taxable "
                    "where the data centre sits (CJEU C-215/19 A Oy, 2 July 2020), which "
                    "requires local VAT registration rather than a reverse charge."
                )
            rcm_vat = invoice.net_amount_usd * (std_rate / 100.0)
            recoverable = rcm_vat * (self.recovery_ratio_pct / 100.0)
            unrecoverable = rcm_vat - recoverable

            logger.info(
                "VAT Assessment [%s]: REVERSE CHARGE [%s -> %s]: RCM VAT=%.2f, Recoverable=%.2f",
                invoice.invoice_id, vendor_jur.value, entity_jur.value, rcm_vat, recoverable,
            )
            return VATTaxAssessment(
                invoice_id=invoice.invoice_id,
                vat_treatment=VATTreatment.REVERSE_CHARGE,
                vat_rate_pct=std_rate,
                output_vat_usd=rcm_vat,
                input_vat_paid_usd=0.0,
                reverse_charge_vat_usd=rcm_vat,
                recoverable_input_vat_usd=recoverable,
                unrecoverable_vat_expense_usd=unrecoverable,
                warnings=tuple(warnings),
            )

        # 3. Domestic standard-rated service, or a supply outside the scope of
        #    VAT/GST for this recipient.
        if entity_jur == Jurisdiction.US_NON_VAT or is_cross_border:
            treatment = VATTreatment.OUT_OF_SCOPE
            rate = 0.0
            vat_paid = 0.0
            recoverable = 0.0
            unrecoverable = 0.0
            if entity_jur == Jurisdiction.US_NON_VAT:
                warnings.append(
                    "US recipient: outside the scope of VAT/GST, but US state and local "
                    "sales and use tax is not modelled by this engine and may still apply."
                )
        else:
            treatment = VATTreatment.STANDARD_RATED
            rate = std_rate
            vat_paid = invoice.net_amount_usd * (rate / 100.0)
            recoverable = vat_paid * (self.recovery_ratio_pct / 100.0)
            unrecoverable = vat_paid - recoverable

        logger.info(
            "VAT Assessment [%s]: %s [%s]: VAT Paid=%.2f, Recoverable=%.2f, Unrecoverable=%.2f",
            invoice.invoice_id, treatment.value, entity_jur.value,
            vat_paid, recoverable, unrecoverable,
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
            warnings=tuple(warnings),
        )

    def _reverse_charge_applies(self, entity_jur: Jurisdiction, warnings: List[str]) -> bool:
        """Whether the recipient's jurisdiction imposes a reverse charge here.

        Singapore's reverse charge regime for imported services and Australia's
        GST Act Div 84 both bite only where the recipient is *not* entitled to a
        full input tax credit. A 100% recovery ratio is the proxy used here for
        full entitlement; a partially exempt trading entity sits inside both
        regimes. The UK (VATA 1994 s.8) and the EU (Directive Art 196) impose
        the reverse charge irrespective of the recipient's recovery position.
        """
        if (entity_jur in self.CONDITIONAL_REVERSE_CHARGE_JURISDICTIONS
                and self.recovery_ratio_pct >= 100.0):
            warnings.append(
                f"{entity_jur.value}: reverse charge not applied because the entity is "
                f"modelled as entitled to full input tax credit (recovery ratio 100%). "
                f"Confirm entitlement against the local test (IRAS reverse charge regime / "
                f"GST Act s.84-5) rather than relying on the ratio alone."
            )
            return False
        return True

    # ------------------------------------------------------------------
    # Return summary
    # ------------------------------------------------------------------

    def generate_vat_return_summary(
        self, invoices: Iterable[TradingExpenseInvoice]
    ) -> VATReturnSummary:
        """Aggregate a batch of purchase invoices into period return figures.

        Applies whatever recovery ratio the engine currently holds, so call
        :meth:`set_partial_exemption_ratio` first.
        """
        if isinstance(invoices, (str, bytes)) or not isinstance(invoices, collections.abc.Iterable):
            raise VATEngineError(
                f"invoices must be an iterable of TradingExpenseInvoice, "
                f"got {type(invoices).__name__}."
            )

        total_net = 0.0
        total_input_paid = 0.0
        total_output = 0.0
        total_rcm = 0.0
        total_recoverable = 0.0
        total_unrecoverable = 0.0
        assessments: List[VATTaxAssessment] = []

        for inv in invoices:
            assessment = self.assess_invoice_tax(inv)
            assessments.append(assessment)
            total_net += inv.net_amount_usd
            total_input_paid += assessment.input_vat_paid_usd
            total_output += assessment.output_vat_usd
            total_rcm += assessment.reverse_charge_vat_usd
            total_recoverable += assessment.recoverable_input_vat_usd
            total_unrecoverable += assessment.unrecoverable_vat_expense_usd

        return VATReturnSummary(
            total_net_expense_usd=total_net,
            total_input_vat_paid_usd=total_input_paid,
            total_reverse_charge_vat_usd=total_rcm,
            total_recoverable_input_vat_usd=total_recoverable,
            total_unrecoverable_vat_expense_usd=total_unrecoverable,
            partial_exemption_recovery_ratio_pct=self.recovery_ratio_pct,
            total_output_vat_usd=total_output,
            net_vat_payable_usd=total_output - total_recoverable,
            assessments=tuple(assessments),
        )

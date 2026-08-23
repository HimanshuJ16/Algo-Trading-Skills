"""
Cross-border withholding tax (WHT) and Foreign Tax Credit (FTC) audit engine for
bilateral Double Taxation Treaties (DTT/DTAA).

Scope note: this engine models the *arithmetic* of treaty relief and credit
limitation from rates a user registers. It is not a treaty database and it
invents no rates. Statutory WHT is jurisdiction-specific -- 0% on UK dividends,
25% plus solidarity surcharge in Germany, 30% on US FDAP, 35% in Switzerland --
so an unregistered (residence, source, income type) triple resolves to
REVIEW_REQUIRED rather than a guessed number.

Entitlement to treaty benefits also depends on beneficial ownership and any
limitation-on-benefits article, which are legal determinations this engine does
not make. Output is decision support for a tax adviser, not a filing position.
"""
import logging
import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Income classes -------------------------------------------------------
# Treaties assign separate articles and separate rates to each: dividends
# (OECD Model Art. 10), interest (Art. 11), royalties (Art. 12). Applying a
# dividend rate to interest is a rate error, not a rounding difference -- under
# the US-UK treaty, for instance, dividends are generally 15% while interest is
# generally exempt at source.
INCOME_TYPE_DIVIDEND = "EQUITY_DIVIDEND"
INCOME_TYPE_INTEREST = "INTEREST"
INCOME_TYPE_ROYALTY = "ROYALTY"
INCOME_TYPE_871M_SWAP = "SECTION_871M_SWAP"
VALID_INCOME_TYPES = frozenset({
    INCOME_TYPE_DIVIDEND,
    INCOME_TYPE_INTEREST,
    INCOME_TYPE_ROYALTY,
    INCOME_TYPE_871M_SWAP,
})

# --- Audit outcomes -------------------------------------------------------
STATUS_TREATY_APPLIED = "TREATY_APPLIED"
STATUS_DOCUMENTATION_MISSING = "DOCUMENTATION_MISSING"
STATUS_STATUTORY_NO_TREATY = "STATUTORY_NO_TREATY"
STATUS_REVIEW_REQUIRED = "REVIEW_REQUIRED"

_CENTS = Decimal("0.01")


def _money(value: float) -> float:
    """
    Rounds a monetary amount to cents using ROUND_HALF_UP.

    Python's built-in round() is round-half-even, so round(0.125, 2) is 0.12 --
    a tax figure that will not tie out against a broker statement or a filing
    prepared under conventional half-up rounding.
    """
    return float(Decimal(str(value)).quantize(_CENTS, rounding=ROUND_HALF_UP))


def _money_mul(amount: float, rate: float) -> float:
    """
    Multiplies a money amount by a rate in exact decimal arithmetic, then rounds
    half-up to cents.

    Rounding a binary float product is not sufficient: 1.50 * 0.15 evaluates to
    0.22499999999999998 in float, which rounds to 0.22 even under half-up, while
    the exact decimal product 0.2250 rounds to 0.23. Withholding figures are
    computed on decimal amounts and decimal rates, so the multiplication itself
    has to happen in Decimal.
    """
    product = Decimal(str(amount)) * Decimal(str(rate))
    return float(product.quantize(_CENTS, rounding=ROUND_HALF_UP))


@dataclass
class DttTreatySpec:
    """
    One treaty rate for one (residence, source, income type) triple.

    `treaty_wht_pct` is the reduced source-country rate available under the
    treaty for this income class; `statutory_wht_pct` is the source country's
    domestic rate that applies when treaty benefits are not claimed.
    """
    residence_country: str               # e.g. 'UK', 'SG', 'CA', 'KY'
    source_country: str                  # e.g. 'US', 'DE', 'IN', 'JP'
    statutory_wht_pct: float             # e.g. 0.30 (US statutory FDAP rate)
    treaty_wht_pct: float                # e.g. 0.15 (US-UK portfolio dividend rate)
    required_documentation: str          # e.g. 'Form W-8BEN-E' or 'TRC'
    income_type: str = INCOME_TYPE_DIVIDEND


@dataclass
class CrossBorderIncomePayment:
    payment_id: str
    residence_country: str
    source_country: str
    income_type: str
    gross_income_usd: float
    has_valid_tax_documentation: bool    # active W-8BEN-E / TRC on file
    # Residence-country rate used to approximate the credit ceiling. There is no
    # defensible default: a Cayman entity's rate is 0.00 and a credit ceiling of
    # zero is the whole point of the zero-tax-jurisdiction pitfall. None means
    # "unknown" and suppresses the FTC figure rather than inventing one.
    resident_country_effective_tax_rate: Optional[float] = None
    # Optional explicit credit ceiling. Supply this when the residence country's
    # real limitation has been computed properly (e.g. IRC s.904, which works on
    # net foreign-source taxable income per separate category, not on gross).
    ftc_limitation_usd: Optional[float] = None


@dataclass
class DoubleTaxationAuditReport:
    payment_id: str
    residence_country: str
    source_country: str
    income_type: str
    gross_income_usd: float
    status: str
    applied_wht_pct: Optional[float]
    wht_tax_paid_usd: Optional[float]
    statutory_wht_usd: Optional[float]
    wht_tax_leakage_saved_usd: Optional[float]
    # Foreign tax that is *eligible* to be credited before the residence-country
    # ceiling: tax withheld in excess of an available treaty rate is generally
    # not creditable (US noncompulsory payment rule).
    creditable_foreign_tax_usd: Optional[float]
    ftc_limitation_usd: Optional[float]
    eligible_foreign_tax_credit_usd: Optional[float]
    # Withheld tax that will never be recovered, through credit or refund claim.
    non_creditable_wht_usd: Optional[float]
    required_action: str
    applied_treaty_notes: str


class DoubleTaxationTreatyEngine:
    """
    Evaluates cross-border income payments against registered treaty and
    statutory withholding rates, and computes the recoverable / unrecoverable
    split of the tax withheld.

    Rates must be registered by the caller. The engine never falls back to a
    default rate: a hard-coded 30% is the US FDAP rate and is wrong for most
    other source countries.
    """

    def __init__(self, *, limit_credit_to_treaty_rate: bool = True):
        """
        limit_credit_to_treaty_rate: when True (default), foreign tax withheld
            above a treaty rate that *was available* is treated as
            non-creditable. This follows the US noncompulsory payment rule
            (Treas. Reg. s.1.901-2(e)(5)); the IRS LB&I practice unit on reduced
            foreign taxes under treaty provisions states that where a lower
            treaty rate applies, the credit is figured using the treaty rate
            regardless of what was actually withheld. Other residence countries
            apply their own rules -- set False only if the residence
            jurisdiction demonstrably credits the full amount withheld.
        """
        self.limit_credit_to_treaty_rate = limit_credit_to_treaty_rate
        self.treaties: Dict[str, DttTreatySpec] = {}
        self.statutory_rates: Dict[str, float] = {}

    # -- registration ------------------------------------------------------

    @staticmethod
    def _norm_country(value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
        cleaned = value.strip().upper()
        if not cleaned:
            raise ValueError(f"{field_name} must be a non-empty string")
        return cleaned

    @staticmethod
    def _norm_income_type(value: object, field_name: str) -> str:
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string, got {type(value).__name__}")
        cleaned = value.strip().upper()
        if cleaned not in VALID_INCOME_TYPES:
            raise ValueError(
                f"{field_name} {value!r} is not one of {sorted(VALID_INCOME_TYPES)}"
            )
        return cleaned

    @staticmethod
    def _norm_rate(value: object, field_name: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{field_name} must be a number, got {type(value).__name__}")
        rate = float(value)
        if not 0.0 <= rate <= 1.0:
            raise ValueError(
                f"{field_name} must be a decimal fraction within [0.0, 1.0], got {rate}. "
                "Rates are fractions (0.15), not percentages (15)."
            )
        return rate

    def _treaty_key(self, residence: str, source: str, income_type: str) -> str:
        return f"{residence}_{source}_{income_type}"

    def register_treaty(self, treaty: DttTreatySpec) -> None:
        """
        Registers one treaty rate. Rejects a treaty rate above the statutory
        rate: that is a data-entry inversion, and silently clamping the
        resulting negative saving to zero hides it.
        """
        if not isinstance(treaty, DttTreatySpec):
            raise TypeError(f"treaty must be DttTreatySpec, got {type(treaty).__name__}")
        residence = self._norm_country(treaty.residence_country, "treaty.residence_country")
        source = self._norm_country(treaty.source_country, "treaty.source_country")
        income_type = self._norm_income_type(treaty.income_type, "treaty.income_type")
        statutory = self._norm_rate(treaty.statutory_wht_pct, "treaty.statutory_wht_pct")
        reduced = self._norm_rate(treaty.treaty_wht_pct, "treaty.treaty_wht_pct")
        if reduced > statutory:
            raise ValueError(
                f"treaty_wht_pct ({reduced}) exceeds statutory_wht_pct ({statutory}) for "
                f"{residence}->{source} {income_type}; a treaty rate cannot be worse than "
                "the domestic rate it replaces."
            )
        self.treaties[self._treaty_key(residence, source, income_type)] = DttTreatySpec(
            residence_country=residence,
            source_country=source,
            statutory_wht_pct=statutory,
            treaty_wht_pct=reduced,
            required_documentation=treaty.required_documentation,
            income_type=income_type,
        )

    def register_statutory_rate(
        self, source_country: str, income_type: str, statutory_wht_pct: float
    ) -> None:
        """
        Registers a source-country domestic WHT rate for payments with no
        treaty coverage. Needed because statutory rates are jurisdiction- and
        income-type-specific; there is no universal default.
        """
        source = self._norm_country(source_country, "source_country")
        income = self._norm_income_type(income_type, "income_type")
        rate = self._norm_rate(statutory_wht_pct, "statutory_wht_pct")
        self.statutory_rates[f"{source}_{income}"] = rate

    # -- evaluation --------------------------------------------------------

    def _resolve_rates(
        self, residence: str, source: str, income_type: str, has_docs: bool
    ) -> Tuple[Optional[float], Optional[float], Optional[float], str, str, str]:
        """
        Returns (applied_pct, statutory_pct, best_treaty_pct, status, notes, action).
        Any element may be None when the rate is unknown; nothing is guessed.
        """
        treaty = self.treaties.get(self._treaty_key(residence, source, income_type))

        if treaty is not None:
            if has_docs:
                return (
                    treaty.treaty_wht_pct, treaty.statutory_wht_pct, treaty.treaty_wht_pct,
                    STATUS_TREATY_APPLIED,
                    f"Treaty rate applied: {treaty.treaty_wht_pct * 100:.2f}% on {income_type} "
                    f"under the {residence}-{source} treaty. Entitlement still depends on "
                    "beneficial ownership and any limitation-on-benefits article.",
                    "Maintain active tax documentation; re-verify before it lapses.",
                )
            return (
                treaty.statutory_wht_pct, treaty.statutory_wht_pct, treaty.treaty_wht_pct,
                STATUS_DOCUMENTATION_MISSING,
                f"Missing {treaty.required_documentation}: statutory rate "
                f"{treaty.statutory_wht_pct * 100:.2f}% applied instead of the available "
                f"{treaty.treaty_wht_pct * 100:.2f}% treaty rate.",
                f"File {treaty.required_documentation} to obtain the "
                f"{treaty.treaty_wht_pct * 100:.2f}% rate, and pursue a source-country refund "
                "claim for the over-withheld amount -- the excess is generally NOT creditable.",
            )

        statutory = self.statutory_rates.get(f"{source}_{income_type}")
        if statutory is not None:
            return (
                statutory, statutory, None,
                STATUS_STATUTORY_NO_TREATY,
                f"No treaty registered for {residence}-{source} on {income_type}; "
                f"registered statutory rate {statutory * 100:.2f}% applied.",
                "Confirm with tax counsel whether a treaty covers this income class.",
            )

        return (
            None, None, None,
            STATUS_REVIEW_REQUIRED,
            f"No treaty and no statutory rate registered for {residence}-{source} on "
            f"{income_type}. No rate is assumed: statutory withholding is "
            "jurisdiction-specific (0% on UK dividends, 30% on US FDAP, 35% in Switzerland).",
            f"Register the {source} statutory rate for {income_type}, and any applicable "
            "treaty rate, before relying on this payment's tax treatment.",
        )

    def evaluate_cross_border_payment(
        self, payment: CrossBorderIncomePayment
    ) -> DoubleTaxationAuditReport:
        """
        Audits one cross-border income payment.

        Computes the withheld amount, the saving versus the statutory rate, and
        the split between creditable and permanently lost tax. Raises on
        malformed input; an unknown rate is reported as REVIEW_REQUIRED with
        None amounts rather than as a number.
        """
        if not isinstance(payment, CrossBorderIncomePayment):
            raise TypeError(
                f"payment must be CrossBorderIncomePayment, got {type(payment).__name__}"
            )
        if not isinstance(payment.payment_id, str) or not payment.payment_id.strip():
            raise ValueError("payment.payment_id must be a non-empty string")
        residence = self._norm_country(payment.residence_country, "payment.residence_country")
        source = self._norm_country(payment.source_country, "payment.source_country")
        income_type = self._norm_income_type(payment.income_type, "payment.income_type")
        if isinstance(payment.gross_income_usd, bool) or not isinstance(
            payment.gross_income_usd, (int, float)
        ):
            raise TypeError("payment.gross_income_usd must be a number")
        gross = float(payment.gross_income_usd)
        if not math.isfinite(gross):
            # NaN/Inf would reach Decimal.quantize and raise InvalidOperation
            # deep inside the money helpers; reject it at the boundary instead.
            raise ValueError("payment.gross_income_usd must be a finite number")
        if gross < 0:
            raise ValueError("payment.gross_income_usd must not be negative")
        if not isinstance(payment.has_valid_tax_documentation, bool):
            raise TypeError("payment.has_valid_tax_documentation must be a bool")

        applied_pct, statutory_pct, best_treaty_pct, status, notes, action = self._resolve_rates(
            residence, source, income_type, payment.has_valid_tax_documentation
        )

        if applied_pct is None:
            logger.warning(
                "UNRESOLVED CROSS-BORDER TAX [%s]: %s->%s %s has no registered rate.",
                payment.payment_id, residence, source, income_type,
            )
            return DoubleTaxationAuditReport(
                payment_id=payment.payment_id, residence_country=residence,
                source_country=source, income_type=income_type, gross_income_usd=gross,
                status=status, applied_wht_pct=None, wht_tax_paid_usd=None,
                statutory_wht_usd=None, wht_tax_leakage_saved_usd=None,
                creditable_foreign_tax_usd=None, ftc_limitation_usd=None,
                eligible_foreign_tax_credit_usd=None, non_creditable_wht_usd=None,
                required_action=action, applied_treaty_notes=notes,
            )

        wht_paid = _money_mul(gross, applied_pct)
        statutory_wht = _money_mul(gross, statutory_pct)
        savings = _money(max(0.0, statutory_wht - wht_paid))

        # Tax withheld above an available treaty rate is generally a
        # noncompulsory payment and is not creditable; it must be recovered
        # from the source country by refund claim, if at all.
        if self.limit_credit_to_treaty_rate and best_treaty_pct is not None:
            creditable = min(wht_paid, _money_mul(gross, best_treaty_pct))
        else:
            creditable = wht_paid

        limitation = self._resolve_limitation(payment, gross)

        if limitation is None:
            eligible_ftc = None
            non_creditable = None
            action = (
                f"{action} Supply resident_country_effective_tax_rate or ftc_limitation_usd "
                "to quantify the credit; none is assumed."
            )
        else:
            eligible_ftc = _money(min(creditable, limitation))
            non_creditable = _money(wht_paid - eligible_ftc)

        if savings > 0:
            logger.info(
                "TREATY RELIEF [%s]: %s saved versus statutory rate on %s.",
                payment.payment_id, f"${savings:,.2f}", income_type,
            )
        if non_creditable:
            logger.warning(
                "UNRECOVERABLE TAX [%s]: %s withheld cannot be credited in %s.",
                payment.payment_id, f"${non_creditable:,.2f}", residence,
            )

        return DoubleTaxationAuditReport(
            payment_id=payment.payment_id,
            residence_country=residence,
            source_country=source,
            income_type=income_type,
            gross_income_usd=gross,
            status=status,
            applied_wht_pct=applied_pct,
            wht_tax_paid_usd=wht_paid,
            statutory_wht_usd=statutory_wht,
            wht_tax_leakage_saved_usd=savings,
            creditable_foreign_tax_usd=creditable,
            ftc_limitation_usd=limitation,
            eligible_foreign_tax_credit_usd=eligible_ftc,
            non_creditable_wht_usd=non_creditable,
            required_action=action,
            applied_treaty_notes=notes,
        )

    def _resolve_limitation(
        self, payment: CrossBorderIncomePayment, gross: float
    ) -> Optional[float]:
        """
        Resolves the residence-country credit ceiling.

        An explicit `ftc_limitation_usd` wins, because a properly computed
        limitation (IRC s.904 works on net foreign-source taxable income per
        separate category) cannot be derived from this payment's gross amount.
        The rate-times-gross path is an approximation and is documented as one.
        """
        if payment.ftc_limitation_usd is not None:
            if isinstance(payment.ftc_limitation_usd, bool) or not isinstance(
                payment.ftc_limitation_usd, (int, float)
            ):
                raise TypeError("payment.ftc_limitation_usd must be a number")
            if not math.isfinite(float(payment.ftc_limitation_usd)):
                raise ValueError("payment.ftc_limitation_usd must be a finite number")
            if payment.ftc_limitation_usd < 0:
                raise ValueError("payment.ftc_limitation_usd must not be negative")
            return _money(float(payment.ftc_limitation_usd))

        rate = payment.resident_country_effective_tax_rate
        if rate is None:
            return None
        return _money_mul(gross, self._norm_rate(rate, "payment.resident_country_effective_tax_rate"))

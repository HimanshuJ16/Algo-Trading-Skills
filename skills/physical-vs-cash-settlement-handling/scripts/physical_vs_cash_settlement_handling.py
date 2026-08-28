"""Expiry settlement handling for cash-settled and physically delivered contracts.

The question this module answers is not "what is my P&L at expiry" but "what
does this contract actually oblige my account to do, by when, and can the
account do it". Those obligations are **asymmetric in the position's
direction**, and that asymmetry is the reason a single "days to first notice
date" cutoff is the wrong control.

Who owes what, on a physically delivered futures contract
---------------------------------------------------------
CME Clearing's delivery process is initiated by the **short**. On position day
the short holder notifies CME Clearing of its intention to deliver and
registers a shipping certificate or warehouse receipt; CME Clearing ranks open
long positions by age and assigns the oldest long to that short. The long then
"makes payment to CME Clearing, and CME Clearing simultaneously transfers the
payment from the long to the short position holder and transfers the shipping
certificate from the short to the long" (CME Group, *Understanding the Grain
Delivery Process*).

So the two sides fail in different ways:

* **A long must pay cash and be able to receive the deliverable.** The binding
  constraint is the invoice amount and somewhere to put 1,000 barrels of crude.
* **A short must produce the deliverable instrument.** The binding constraint
  is possession of a registered warehouse receipt or shipping certificate, or
  the ability to buy one. A cash balance does not discharge a short's delivery
  obligation, so testing a short against "cash >= notional" tests nothing that
  can fail in the way the position actually fails.

The two sides also run on different clocks
------------------------------------------
First Notice Date is the date from which a *long* can be assigned a delivery
notice. It is not the short's deadline, and it is not always the last date the
contract trades:

* **COMEX Gold (GC)**: first notice day is the last business day of the month
  *prior* to the delivery month; last trading day is the third-last business
  day *of* the delivery month. First notice day therefore falls weeks
  **before** last trading day, so a long can be assigned while the contract is
  still actively trading.
* **NYMEX WTI Crude Oil (CL)**: trading terminates three business days before
  the 25th calendar day of the month preceding the contract month, and crude
  flows across the whole delivery month. Here the contract stops trading
  **before** the delivery period opens.

Brokers reflect this asymmetry directly. Interactive Brokers' published futures
close-out policy sets the deadline for **long** holders at the end of the
second business day prior to the exchange-specified **first notice day**, and
for **short** holders at the end of trading on the second business day prior to
the exchange-specified **last trading day**, after which IBKR may liquidate the
position without further notification.

This module therefore takes two separate clocks and selects the one that binds
the position's direction. It refuses to audit a physical contract whose
relevant clock is unknown rather than reporting a reassuring default.

Cash settlement is not the absence of risk
------------------------------------------
A cash-settled contract cannot deliver anything, but the *price* it settles at
is often one that never printed in the regular session. CME's E-mini S&P 500
final settlement price is a Special Opening Quotation built from the opening
prices of the index components on the third Friday, regardless of when those
stocks open on expiration day. A position marked at the last regular-session
trade is marked at the wrong number. Set ``settlement_price_is_final=True``
only once the exchange has published the final settlement value; until then the
report carries a ``PROVISIONAL_SETTLEMENT_PRICE`` warning. An adverse cash
settlement is also a real funding event, which is why the cash branch checks
the account balance rather than assuming zero exposure.

Scope and deliberate non-claims
-------------------------------
* **It is not a calendar.** The day counts are business-day numbers the caller
  supplies. This module does not know exchange holidays, the contract's
  delivery calendar, or the broker's own close-out deadline, and the broker's
  deadline rather than the exchange's first notice day is what actually binds a
  customer account.
* **It is not reference data.** ``settlement_type``, ``multiplier`` and
  ``strike_price`` are contract terms the caller must source. A corporate
  action changes an option's deliverable.
* **It does not model assignment probability or exercise elections.** Whether
  an expiring option is exercised at all, including a holder's contrary
  exercise advice against the OCC exercise-by-exception default, belongs to
  ``options-pin-risk-management-at-expiry``. This module prices and funds the
  delivery that an exercise produces.
* **Invoice amounts are the deliverable's principal only.** Grade and location
  differentials, storage, demurrage and load-out charges are contract-specific
  and are not modelled.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "ContractSettlementSpec",
    "PositionState",
    "SettlementPolicyConfig",
    "SettlementReport",
    "PortfolioSettlementReport",
    "PhysicalVsCashSettlementHandlingEngine",
]

# --- Vocabulary --------------------------------------------------------------

SETTLEMENT_CASH = "CASH"
SETTLEMENT_PHYSICAL = "PHYSICAL"
VALID_SETTLEMENT_TYPES = (SETTLEMENT_CASH, SETTLEMENT_PHYSICAL)

INSTRUMENT_FUTURE = "FUTURE"
INSTRUMENT_OPTION = "OPTION"
VALID_INSTRUMENT_KINDS = (INSTRUMENT_FUTURE, INSTRUMENT_OPTION)

# Delivery obligations, by direction.
OBLIGATION_NONE = "NONE"
OBLIGATION_TAKE_DELIVERY_AND_PAY = "TAKE_DELIVERY_AND_PAY"
OBLIGATION_MAKE_DELIVERY_OF_UNDERLYING = "MAKE_DELIVERY_OF_UNDERLYING"

# Which date binds this position. A long is exposed from first notice day; a
# short must be flat by last trading day or it will be delivering.
DEADLINE_FIRST_NOTICE_DATE = "FIRST_NOTICE_DATE"
DEADLINE_LAST_TRADING_DAY = "LAST_TRADING_DAY"
DEADLINE_NONE = "NONE"

# What the delivery invoice is priced at. A futures delivery is invoiced off the
# contract's final settlement price; an exercised physically settled option
# delivers shares against the *strike*, which is the number that must be funded.
BASIS_FINAL_SETTLEMENT_PRICE = "FINAL_SETTLEMENT_PRICE"
BASIS_STRIKE_PRICE = "STRIKE_PRICE"
BASIS_NOT_APPLICABLE = "NOT_APPLICABLE"

# Report statuses
STATUS_FLAT = "FLAT_NO_OBLIGATION"
STATUS_CASH_SETTLED = "CASH_SETTLED_NO_DELIVERY"
STATUS_CASH_SHORTFALL = "CASH_SETTLEMENT_FUNDING_SHORTFALL"
STATUS_PHYSICAL_PROVISIONED = "PHYSICAL_DELIVERY_PROVISIONED"
STATUS_PHYSICAL_NOT_PROVISIONED = "PHYSICAL_DELIVERY_NOT_PROVISIONED"
STATUS_PHYSICAL_BREACH = "PHYSICAL_DELIVERY_RISK_BREACH"
STATUS_DEADLINE_PASSED = "PHYSICAL_DELIVERY_DEADLINE_PASSED"

# Required actions
ACTION_NONE = "NO_ACTION"
ACTION_MONITOR = "MONITOR"
ACTION_FUND_ACCOUNT = "FUND_ACCOUNT_BEFORE_SETTLEMENT"
ACTION_CLOSE_OR_ROLL = "CLOSE_OR_ROLL_BEFORE_DEADLINE"
ACTION_ESCALATE = "ESCALATE_TO_DELIVERY_OPERATIONS"

# Warning flags
WARN_PROVISIONAL_PRICE = "PROVISIONAL_SETTLEMENT_PRICE"
WARN_NO_PRIOR_SETTLEMENT = "NO_PRIOR_SETTLEMENT_PRICE_LIFETIME_PNL_ONLY"
WARN_NO_DELIVERY_FACILITY = "NO_DELIVERY_FACILITY"
WARN_INSUFFICIENT_CASH = "INSUFFICIENT_CASH_FOR_DELIVERY_INVOICE"
WARN_INSUFFICIENT_CASH_SETTLEMENT = "INSUFFICIENT_CASH_FOR_SETTLEMENT_DEBIT"
WARN_INSUFFICIENT_DELIVERABLE = "INSUFFICIENT_DELIVERABLE_UNITS"
WARN_PORTFOLIO_CASH_SHORTFALL = "AGGREGATE_DELIVERY_INVOICE_EXCEEDS_CASH"

# --- Engineering defaults (tunable; none of these are regulatory constants) ---

# No regulator or exchange mandates a close-out buffer. Two business days
# mirrors Interactive Brokers' published futures close-out policy, which is a
# broker house rule and differs between brokers and between products. Override
# it with your own broker's published deadline; do not treat it as a standard.
DEFAULT_LONG_CLOSE_OUT_BUFFER_DAYS = 2
DEFAULT_SHORT_CLOSE_OUT_BUFFER_DAYS = 2


def _require_finite(value: float, name: str) -> float:
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"{name} must be a finite number, got {value!r}.")
    return numeric


def _require_finite_positive(value: float, name: str) -> float:
    numeric = _require_finite(value, name)
    if numeric <= 0.0:
        raise ValueError(f"{name} must be a finite positive number, got {value!r}.")
    return numeric


def _require_finite_non_negative(value: float, name: str) -> float:
    numeric = _require_finite(value, name)
    if numeric < 0.0:
        raise ValueError(f"{name} must not be negative, got {value!r}.")
    return numeric


def _whole_days(value: object, name: str) -> Optional[int]:
    """Business-day counts may be negative (the date has passed) but not fractional."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(
            f"{name} must be a whole number of business days, got {value!r}."
        )
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError(
                f"{name} must be a whole number of business days, got {value!r}."
            )
        return int(value)
    raise ValueError(f"{name} must be a whole number of business days, got {value!r}.")


@dataclass
class ContractSettlementSpec:
    """Contract terms governing what happens to this position at expiry.

    Both day counts are **business days from today**, and both may be negative
    to mean the date has already passed. They are separate fields on purpose:
    first notice day and last trading day are different dates in a
    contract-specific order (first notice day precedes last trading day for
    COMEX Gold; last trading day precedes the delivery period entirely for
    NYMEX WTI), and each binds a different side of the market. Only the one
    that binds the position's direction is required.
    """

    symbol: str
    underlying_asset: str
    settlement_type: str                                     # 'CASH' | 'PHYSICAL'
    multiplier: float = 100.0                                # deliverable units per contract
    instrument_kind: str = INSTRUMENT_FUTURE                 # 'FUTURE' | 'OPTION'
    strike_price: Optional[float] = None                     # required for physical OPTION
    business_days_to_first_notice: Optional[int] = None      # binds a LONG physical position
    business_days_to_last_trading_day: Optional[int] = None  # binds a SHORT physical position
    deliverable_description: str = ""                        # e.g. "1,000 bbl FOB Cushing, OK"

    def __post_init__(self) -> None:
        if not str(self.symbol).strip():
            raise ValueError("symbol must be a non-empty string.")

        settlement = str(self.settlement_type).strip().upper()
        if settlement not in VALID_SETTLEMENT_TYPES:
            # Never fall through to a delivery obligation on an unrecognised
            # code. A vendor string this module does not understand is a
            # reference-data defect, not a physical contract.
            raise ValueError(
                f"settlement_type must be one of {VALID_SETTLEMENT_TYPES}, "
                f"got {self.settlement_type!r}."
            )
        self.settlement_type = settlement

        kind = str(self.instrument_kind).strip().upper()
        if kind not in VALID_INSTRUMENT_KINDS:
            raise ValueError(
                f"instrument_kind must be one of {VALID_INSTRUMENT_KINDS}, "
                f"got {self.instrument_kind!r}."
            )
        self.instrument_kind = kind

        self.multiplier = _require_finite_positive(self.multiplier, "multiplier")

        if self.strike_price is not None:
            self.strike_price = _require_finite_positive(
                self.strike_price, "strike_price"
            )
        if (
            self.settlement_type == SETTLEMENT_PHYSICAL
            and self.instrument_kind == INSTRUMENT_OPTION
            and self.strike_price is None
        ):
            # An exercised physically settled option is funded at the strike.
            # Substituting the settlement price understates the cash required by
            # exactly the intrinsic value, i.e. it is most wrong when the option
            # is deepest in the money.
            raise ValueError(
                "strike_price is required for a physically settled OPTION: the "
                "delivery invoice is funded at the strike, not at the "
                "settlement price."
            )

        self.business_days_to_first_notice = _whole_days(
            self.business_days_to_first_notice, "business_days_to_first_notice"
        )
        self.business_days_to_last_trading_day = _whole_days(
            self.business_days_to_last_trading_day,
            "business_days_to_last_trading_day",
        )


@dataclass
class PositionState:
    """Account-side state determining whether the obligation can be met.

    ``account_cash_balance`` funds a *long* delivery invoice. It is deliberately
    not used to clear a short: a short discharges its obligation by delivering a
    registered warehouse receipt or shipping certificate, which is what
    ``deliverable_units_available`` counts, in the same units as ``multiplier``
    (barrels, ounces, shares) rather than in contracts.
    """

    position_qty: float                                # + long, - short, 0 flat
    entry_price: float
    account_cash_balance: float
    has_delivery_facility: bool = False
    deliverable_units_available: float = 0.0
    prior_settlement_price: Optional[float] = None

    def __post_init__(self) -> None:
        self.position_qty = _require_finite(self.position_qty, "position_qty")
        # Deliberately not required to be positive: a futures price can settle
        # below zero. NYMEX WTI settled at -$37.63 on 20 April 2020, driven by
        # longs unable to take delivery at Cushing -- precisely the failure this
        # module screens for. Rejecting negative prices would refuse the case
        # that matters most.
        self.entry_price = _require_finite(self.entry_price, "entry_price")
        self.account_cash_balance = _require_finite(
            self.account_cash_balance, "account_cash_balance"
        )
        self.deliverable_units_available = _require_finite_non_negative(
            self.deliverable_units_available, "deliverable_units_available"
        )
        if self.prior_settlement_price is not None:
            self.prior_settlement_price = _require_finite(
                self.prior_settlement_price, "prior_settlement_price"
            )

    @property
    def is_long(self) -> bool:
        return self.position_qty > 0.0

    @property
    def is_flat(self) -> bool:
        return self.position_qty == 0.0


@dataclass
class SettlementPolicyConfig:
    """House close-out buffers. Not regulatory constants; see module docstring."""

    long_close_out_buffer_days: int = DEFAULT_LONG_CLOSE_OUT_BUFFER_DAYS
    short_close_out_buffer_days: int = DEFAULT_SHORT_CLOSE_OUT_BUFFER_DAYS

    def __post_init__(self) -> None:
        for name in ("long_close_out_buffer_days", "short_close_out_buffer_days"):
            raw = getattr(self, name)
            parsed = _whole_days(raw, name)
            if parsed is None or parsed < 0:
                raise ValueError(
                    f"{name} must be a non-negative whole number, got {raw!r}."
                )
            setattr(self, name, parsed)


@dataclass
class SettlementReport:
    """Per-position expiry settlement audit.

    ``final_variation_cashflow`` and ``lifetime_pnl`` are different numbers and
    both are reported. A futures position is marked to market every day, so the
    money that actually moves *at* expiry is the final variation margin against
    the prior settlement price; the move against the entry price is the
    position's lifetime result, most of which has already been paid or
    collected. The two coincide only when the entry price is the prior
    settlement price.
    """

    symbol: str
    settlement_type: str
    instrument_kind: str
    position_qty: float
    delivery_obligation: str
    delivery_invoice_usd: float          # cash a long must pay; 0 for a short
    deliverable_units_required: float    # units a short must deliver; 0 for a long
    delivery_price_basis: str
    final_variation_cashflow: float
    lifetime_pnl: float
    binding_deadline: str
    business_days_to_deadline: Optional[int]
    is_past_deadline: bool
    status: str
    required_action: str
    audit_notes: str
    warnings: Tuple[str, ...] = ()


@dataclass
class PortfolioSettlementReport:
    """Book-level view.

    Exists because per-position funding checks are individually satisfiable and
    collectively false: three long deliveries can each pass "cash >= invoice"
    against the same balance while their sum exceeds it.
    """

    positions: List[SettlementReport] = field(default_factory=list)
    aggregate_delivery_invoice_usd: float = 0.0
    account_cash_balance: float = 0.0
    aggregate_final_variation_cashflow: float = 0.0
    breaching_symbols: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()


class PhysicalVsCashSettlementHandlingEngine:
    """Classifies expiring positions and audits their settlement obligations.

    The engine is stateless apart from its policy config; every report is a
    pure function of the spec, the position and the settlement price.
    """

    def __init__(self, config: Optional[SettlementPolicyConfig] = None) -> None:
        self.config = config or SettlementPolicyConfig()

    # -- public API ----------------------------------------------------------

    def evaluate_settlement_risk(
        self,
        spec: ContractSettlementSpec,
        pos: PositionState,
        settlement_price: float,
        settlement_price_is_final: bool = False,
    ) -> SettlementReport:
        """Audit one expiring position.

        Raises ``ValueError`` if a physically settled position's binding day
        count is missing: reporting a physical contract as compliant while its
        delivery clock is unknown is the failure this module exists to prevent.
        """
        # May be negative; see the note on PositionState.entry_price.
        price = _require_finite(settlement_price, "settlement_price")

        warnings: List[str] = []
        if not settlement_price_is_final:
            warnings.append(WARN_PROVISIONAL_PRICE)

        lifetime_pnl = pos.position_qty * spec.multiplier * (price - pos.entry_price)
        if pos.prior_settlement_price is None:
            final_variation = lifetime_pnl
            warnings.append(WARN_NO_PRIOR_SETTLEMENT)
        else:
            final_variation = (
                pos.position_qty
                * spec.multiplier
                * (price - pos.prior_settlement_price)
            )

        if pos.is_flat:
            return self._flat_report(spec, tuple(warnings))
        if spec.settlement_type == SETTLEMENT_CASH:
            return self._cash_report(
                spec, pos, price, lifetime_pnl, final_variation, warnings
            )
        return self._physical_report(
            spec, pos, price, lifetime_pnl, final_variation, warnings
        )

    def audit_portfolio_settlement(
        self,
        items: Sequence[Tuple[ContractSettlementSpec, PositionState, float]],
        account_cash_balance: float,
        settlement_price_is_final: bool = False,
    ) -> PortfolioSettlementReport:
        """Audit a book of expiring positions against one shared cash balance.

        ``account_cash_balance`` is the book-level balance and is the figure the
        aggregate invoice is tested against; the per-position balance on each
        ``PositionState`` still drives that position's own report.
        """
        balance = _require_finite(account_cash_balance, "account_cash_balance")

        reports = [
            self.evaluate_settlement_risk(spec, pos, price, settlement_price_is_final)
            for spec, pos, price in items
        ]
        aggregate_invoice = sum(r.delivery_invoice_usd for r in reports)
        aggregate_variation = sum(r.final_variation_cashflow for r in reports)
        breaching = tuple(
            r.symbol
            for r in reports
            if r.status in (STATUS_PHYSICAL_BREACH, STATUS_DEADLINE_PASSED)
        )

        warnings: List[str] = []
        if aggregate_invoice > 0.0 and aggregate_invoice > balance:
            warnings.append(WARN_PORTFOLIO_CASH_SHORTFALL)
            logger.error(
                "AGGREGATE DELIVERY FUNDING SHORTFALL: invoices total $%.2f against "
                "a cash balance of $%.2f across %d expiring position(s).",
                aggregate_invoice,
                balance,
                len(reports),
            )

        return PortfolioSettlementReport(
            positions=reports,
            aggregate_delivery_invoice_usd=round(aggregate_invoice, 2),
            account_cash_balance=round(balance, 2),
            aggregate_final_variation_cashflow=round(aggregate_variation, 2),
            breaching_symbols=breaching,
            warnings=tuple(warnings),
        )

    # -- branches ------------------------------------------------------------

    @staticmethod
    def _flat_report(
        spec: ContractSettlementSpec, warnings: Tuple[str, ...]
    ) -> SettlementReport:
        """A flat position carries no obligation, whatever the contract terms.

        Reported explicitly rather than run through the physical branch, where
        an account with no delivery facility would otherwise raise a delivery
        breach against a zero-unit obligation.
        """
        notes = f"FLAT [{spec.symbol}]: no open position, no settlement obligation."
        logger.info("%s", notes)
        return SettlementReport(
            symbol=spec.symbol,
            settlement_type=spec.settlement_type,
            instrument_kind=spec.instrument_kind,
            position_qty=0.0,
            delivery_obligation=OBLIGATION_NONE,
            delivery_invoice_usd=0.0,
            deliverable_units_required=0.0,
            delivery_price_basis=BASIS_NOT_APPLICABLE,
            final_variation_cashflow=0.0,
            lifetime_pnl=0.0,
            binding_deadline=DEADLINE_NONE,
            business_days_to_deadline=None,
            is_past_deadline=False,
            status=STATUS_FLAT,
            required_action=ACTION_NONE,
            audit_notes=notes,
            warnings=warnings,
        )

    @staticmethod
    def _cash_report(
        spec: ContractSettlementSpec,
        pos: PositionState,
        price: float,
        lifetime_pnl: float,
        final_variation: float,
        warnings: List[str],
    ) -> SettlementReport:
        """Cash settlement: no deliverable, but an adverse settlement is a debit."""
        shortfall = (
            final_variation < 0.0
            and pos.account_cash_balance < abs(final_variation)
        )
        status = STATUS_CASH_SHORTFALL if shortfall else STATUS_CASH_SETTLED
        action = ACTION_FUND_ACCOUNT if shortfall else ACTION_NONE
        if shortfall:
            warnings.append(WARN_INSUFFICIENT_CASH_SETTLEMENT)

        notes = (
            f"CASH SETTLEMENT [{spec.symbol} - {status}]: Qty = {pos.position_qty:g}, "
            f"Settlement Price = ${price:,.2f}, "
            f"Final Variation = ${final_variation:+,.2f}, "
            f"Lifetime PnL = ${lifetime_pnl:+,.2f}, "
            f"Cash Balance = ${pos.account_cash_balance:,.2f}. No deliverable."
        )
        if shortfall:
            logger.error("CASH SETTLEMENT FUNDING SHORTFALL: %s", notes)
        else:
            logger.info("%s", notes)

        return SettlementReport(
            symbol=spec.symbol,
            settlement_type=SETTLEMENT_CASH,
            instrument_kind=spec.instrument_kind,
            position_qty=pos.position_qty,
            delivery_obligation=OBLIGATION_NONE,
            delivery_invoice_usd=0.0,
            deliverable_units_required=0.0,
            delivery_price_basis=BASIS_NOT_APPLICABLE,
            final_variation_cashflow=round(final_variation, 2),
            lifetime_pnl=round(lifetime_pnl, 2),
            binding_deadline=DEADLINE_NONE,
            business_days_to_deadline=None,
            is_past_deadline=False,
            status=status,
            required_action=action,
            audit_notes=notes,
            warnings=tuple(warnings),
        )

    def _physical_report(
        self,
        spec: ContractSettlementSpec,
        pos: PositionState,
        price: float,
        lifetime_pnl: float,
        final_variation: float,
        warnings: List[str],
    ) -> SettlementReport:
        """Physical settlement: obligation, deadline and provisioning all depend on side."""
        units = abs(pos.position_qty) * spec.multiplier

        # A futures delivery is invoiced off the final settlement price; an
        # exercised physically settled option delivers against the strike.
        if spec.instrument_kind == INSTRUMENT_OPTION:
            delivery_price = float(spec.strike_price)
            basis = BASIS_STRIKE_PRICE
        else:
            delivery_price = price
            basis = BASIS_FINAL_SETTLEMENT_PRICE

        if pos.is_long:
            obligation = OBLIGATION_TAKE_DELIVERY_AND_PAY
            deadline_name = DEADLINE_FIRST_NOTICE_DATE
            days = spec.business_days_to_first_notice
            buffer_days = self.config.long_close_out_buffer_days
            invoice = units * delivery_price
            units_required = 0.0
            missing_field = "business_days_to_first_notice"
        else:
            obligation = OBLIGATION_MAKE_DELIVERY_OF_UNDERLYING
            deadline_name = DEADLINE_LAST_TRADING_DAY
            days = spec.business_days_to_last_trading_day
            buffer_days = self.config.short_close_out_buffer_days
            # A short is paid the invoice; it does not fund one. What it must
            # produce is the deliverable itself.
            invoice = 0.0
            units_required = units
            missing_field = "business_days_to_last_trading_day"

        if days is None:
            raise ValueError(
                f"{spec.symbol}: a {'long' if pos.is_long else 'short'} physically "
                f"settled position is bound by its {deadline_name}, so "
                f"{missing_field} is required. Auditing it without that date "
                "would report a delivery obligation as compliant on an unknown "
                "clock."
            )

        # Provisioning: the long needs somewhere to receive the deliverable and
        # the cash to pay for it; the short needs the deliverable in hand.
        if pos.is_long:
            has_resources = pos.account_cash_balance >= invoice
            if not has_resources:
                warnings.append(WARN_INSUFFICIENT_CASH)
        else:
            has_resources = pos.deliverable_units_available >= units_required
            if not has_resources:
                warnings.append(WARN_INSUFFICIENT_DELIVERABLE)
        if not pos.has_delivery_facility:
            warnings.append(WARN_NO_DELIVERY_FACILITY)
        is_provisioned = pos.has_delivery_facility and has_resources

        is_past_deadline = days < 0
        inside_buffer = days <= buffer_days

        if is_past_deadline and not is_provisioned:
            # Past the deadline a roll no longer avoids the obligation: for a
            # long, delivery notices may already have been assigned.
            status, action = STATUS_DEADLINE_PASSED, ACTION_ESCALATE
        elif is_past_deadline:
            status, action = STATUS_PHYSICAL_PROVISIONED, ACTION_ESCALATE
        elif not is_provisioned and inside_buffer:
            status, action = STATUS_PHYSICAL_BREACH, ACTION_CLOSE_OR_ROLL
        elif not is_provisioned:
            status, action = STATUS_PHYSICAL_NOT_PROVISIONED, ACTION_CLOSE_OR_ROLL
        else:
            status, action = STATUS_PHYSICAL_PROVISIONED, ACTION_MONITOR

        deliverable = spec.deliverable_description or spec.underlying_asset
        if pos.is_long:
            resource_note = (
                f"Invoice = ${invoice:,.2f} ({basis}), "
                f"Cash Balance = ${pos.account_cash_balance:,.2f}"
            )
        else:
            resource_note = (
                f"Must deliver {units_required:,.4g} unit(s) of {deliverable}, "
                f"available = {pos.deliverable_units_available:,.4g}"
            )
        notes = (
            f"PHYSICAL SETTLEMENT AUDIT [{spec.symbol} - {status}]: "
            f"Qty = {pos.position_qty:g} ({obligation}), {resource_note}, "
            f"Delivery Facility = {pos.has_delivery_facility}, "
            f"{deadline_name} in {days} business day(s), "
            f"close-out buffer = {buffer_days}."
        )

        if status in (STATUS_PHYSICAL_BREACH, STATUS_DEADLINE_PASSED):
            logger.error("DELIVERY RISK ALERT: %s", notes)
        elif status == STATUS_PHYSICAL_NOT_PROVISIONED:
            logger.warning("%s", notes)
        else:
            logger.info("%s", notes)

        return SettlementReport(
            symbol=spec.symbol,
            settlement_type=SETTLEMENT_PHYSICAL,
            instrument_kind=spec.instrument_kind,
            position_qty=pos.position_qty,
            delivery_obligation=obligation,
            delivery_invoice_usd=round(invoice, 2),
            deliverable_units_required=round(units_required, 6),
            delivery_price_basis=basis,
            final_variation_cashflow=round(final_variation, 2),
            lifetime_pnl=round(lifetime_pnl, 2),
            binding_deadline=deadline_name,
            business_days_to_deadline=days,
            is_past_deadline=is_past_deadline,
            status=status,
            required_action=action,
            audit_notes=notes,
            warnings=tuple(warnings),
        )

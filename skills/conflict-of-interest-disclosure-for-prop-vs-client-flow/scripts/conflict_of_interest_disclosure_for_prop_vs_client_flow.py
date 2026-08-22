"""
conflict-of-interest-disclosure-for-prop-vs-client-flow: pre-trade gate that audits a
proposed proprietary order against the firm's own unexecuted customer orders under
FINRA Rule 5320 (Prohibition Against Trading Ahead of Customer Orders, the "Manning"
rule).

Scope: US equities only - NMS stocks and OTC Equity Securities. Rule 5320 does not
apply to options, futures or fixed income, and it is not a substitute for the EU/UK
pending-client-order provisions (see references/standards.md).

Direction of the test (the part implementations most often get backwards): the rule
prohibits trading for the firm's own account "at a price that would satisfy the
customer order". A pending customer BUY limit at $150.00 is satisfied by a purchase
at $150.00 *or lower*; a pending customer SELL limit at $150.00 is satisfied by a
sale at $150.00 *or higher*.

The engine is deliberately fail-closed: any order it cannot fully evaluate is
reported as a violation rather than approved.
"""
import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import List, Optional, Sequence, Union

logger = logging.getLogger(__name__)

Numeric = Union[Decimal, int, str, float]

#: Default round lot used by the Rule 5320.05 odd lot exception. Override per symbol
#: where the security's round lot is not 100 shares.
DEFAULT_ROUND_LOT = 100

#: Rule 5320.01 large order thresholds. Both must be met: "orders of 10,000 shares or
#: more (unless such orders are less than $100,000 in value)".
LARGE_ORDER_MIN_SHARES = 10_000
LARGE_ORDER_MIN_VALUE = Decimal("100000")


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class SecurityType(Enum):
    """
    Rule 5320.02 draws a line between the two US equity categories.

    NMS_STOCK  - the no-knowledge exception is available to any trading unit.
    OTC_EQUITY - the no-knowledge exception is available only to a *non-market-making*
                 trading unit; it does not extend to the market-making desk.
    """
    NMS_STOCK = "NMS_STOCK"
    OTC_EQUITY = "OTC_EQUITY"


class TradingUnitType(Enum):
    MARKET_MAKING = "MARKET_MAKING"
    NON_MARKET_MAKING = "NON_MARKET_MAKING"


class ViolationCode(Enum):
    TRADING_AHEAD_5320 = "FINRA_RULE_5320_TRADING_AHEAD"
    INVALID_ORDER_PARAMETERS = "INVALID_ORDER_PARAMETERS"


class ExceptionCode(Enum):
    """
    Reason a client order that the proprietary order *would* have traded ahead of did
    not block it.

    Rule 5320.06 minimum price improvement is deliberately absent: the increment widens
    the prohibited price range rather than excusing a trade inside it, so clearing it
    means there was no conflict to except in the first place.
    """
    NO_KNOWLEDGE_BARRIER = "NO_KNOWLEDGE_BARRIER"                        # Rule 5320.02
    LARGE_ORDER_NEGATIVE_CONSENT = "LARGE_ORDER_NEGATIVE_CONSENT"        # Rule 5320.01
    INSTITUTIONAL_NEGATIVE_CONSENT = "INSTITUTIONAL_NEGATIVE_CONSENT"    # Rule 5320.01
    ODD_LOT = "ODD_LOT"                                                  # Rule 5320.05


def _to_decimal(value: Numeric, field_name: str) -> Decimal:
    """
    Convert a price to Decimal. Floats are routed through str() so that a literal such
    as 150.10 compares as the price a human wrote, not as its binary approximation.
    Rule 5320 turns on exact equality with a customer limit price, so binary float
    comparison at the threshold is not acceptable here.
    """
    try:
        dec = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field_name} is not a valid decimal: {value!r}") from exc
    if not dec.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    return dec


def _coerce_side(side: Union[OrderSide, str], field_name: str) -> OrderSide:
    if isinstance(side, OrderSide):
        return side
    try:
        return OrderSide(str(side).strip().upper())
    except ValueError as exc:
        raise ValueError(f"{field_name} must be 'BUY' or 'SELL', got {side!r}") from exc


@dataclass
class ClientOrder:
    """
    An unexecuted customer order held by the firm.

    is_institutional_account:   the account meets the FINRA Rule 4512(c) definition of
                                an institutional account (bank, savings and loan,
                                insurance company, registered investment company,
                                registered investment adviser, or any other person with
                                total assets of at least $50 million).
    negative_consent_disclosed: the firm gave this customer the clear and comprehensive
                                written disclosure required by Rule 5320.01 at account
                                opening and annually thereafter, together with a
                                meaningful opportunity to opt in.
    opted_in_5320:              the customer took up that opportunity and opted *in* to
                                Rule 5320 protection for this order. When True the
                                Rule 5320.01 exception is unavailable regardless of
                                order size or account type.
    """
    order_id: str
    symbol: str
    side: Union[OrderSide, str]
    quantity: int
    limit_price: Numeric
    info_barrier_id: str
    is_institutional_account: bool = False
    negative_consent_disclosed: bool = False
    opted_in_5320: bool = False


@dataclass
class PropOrder:
    """
    A proposed proprietary (principal) order awaiting the Rule 5320 gate.

    trading_unit_type:  whether the desk originating this order is the firm's
                        market-making unit. Only consulted for OTC Equity Securities,
                        where Rule 5320.02 withholds the no-knowledge exception from
                        the market-making desk.
    barriers_effective: the firm has implemented and utilises an effective system of
                        internal controls (information barriers) for this desk. A
                        distinct `info_barrier_id` alone is a label, not a control; set
                        this from the firm's attested barrier inventory.
    """
    order_id: str
    symbol: str
    side: Union[OrderSide, str]
    quantity: int
    price: Numeric
    info_barrier_id: str
    security_type: SecurityType = SecurityType.NMS_STOCK
    trading_unit_type: TradingUnitType = TradingUnitType.NON_MARKET_MAKING
    barriers_effective: bool = True


@dataclass
class ConflictFinding:
    """One unexecuted client order that the proprietary order would trade ahead of."""
    client_order_id: str
    client_limit_price: Decimal
    prop_price: Decimal
    reason: str


@dataclass
class ConflictAuditResult:
    is_approved: bool
    violation_type: Optional[str]
    exception_applied: Optional[str]
    reason: str
    conflicts: List[ConflictFinding] = field(default_factory=list)
    exceptions_applied: List[str] = field(default_factory=list)


class Rule5320ViolationError(Exception):
    """Raised by PropVsClientConflictEngine.enforce_prop_order when an order is blocked."""

    def __init__(self, result: ConflictAuditResult) -> None:
        super().__init__(result.reason)
        self.result = result


def minimum_price_improvement(
    limit_price: Decimal,
    security_type: SecurityType,
    inside_spread: Optional[Decimal] = None,
) -> Decimal:
    """
    Rule 5320.06 minimum price improvement increment for a held customer limit order.

    For NMS stocks priced at or above $1.00 the increment is a flat $0.01. In every
    other tier - and for OTC Equity Securities at any price - the increment is the
    lesser of the tier increment and one half of the current inside spread.

    `inside_spread` is optional. When it is not supplied the tier increment is used on
    its own, which is the stricter (larger) threshold and therefore the safe default:
    an unknown spread must not widen the set of proprietary trades the gate permits.
    """
    if limit_price >= Decimal("1.00"):
        tier = Decimal("0.01")
        if security_type is SecurityType.NMS_STOCK:
            return tier
    elif limit_price >= Decimal("0.01"):
        tier = Decimal("0.01")
    elif limit_price >= Decimal("0.001"):
        tier = Decimal("0.001")
    elif limit_price >= Decimal("0.0001"):
        tier = Decimal("0.0001")
    elif limit_price >= Decimal("0.00001"):
        tier = Decimal("0.00001")
    else:
        tier = Decimal("0.000001")

    if inside_spread is not None and inside_spread > 0:
        return min(tier, inside_spread / Decimal(2))
    return tier


class PropVsClientConflictEngine:
    """
    Audits a proposed proprietary order against every unexecuted customer order the
    firm holds in the same security and on the same side, and applies the Rule 5320
    supplementary-material exceptions.

    The engine holds no mutable state beyond the client-order book supplied to it, so
    an instance may be shared across threads provided that book is not mutated
    concurrently. Callers maintaining a live book should pass a snapshot per call.

    `inside_spread` is a single value applied to every order in the book, so construct
    one engine per security when auditing sub-$1.00 or OTC names, where the Rule 5320.06
    increment depends on that security's own spread.
    """

    def __init__(
        self,
        pending_client_orders: Optional[Sequence[ClientOrder]] = None,
        round_lot: int = DEFAULT_ROUND_LOT,
        inside_spread: Optional[Numeric] = None,
    ) -> None:
        self.pending_client_orders: List[ClientOrder] = list(pending_client_orders or [])
        if round_lot <= 0:
            raise ValueError(f"round_lot must be positive, got {round_lot}")
        self.round_lot = round_lot
        self.inside_spread = (
            _to_decimal(inside_spread, "inside_spread") if inside_spread is not None else None
        )

    def evaluate_prop_order(self, prop_order: PropOrder) -> ConflictAuditResult:
        """
        Audit `prop_order` against all pending client orders.

        Returns an approved result only when *every* matching client order is either
        non-conflicting or covered by an exception. Unparseable input is reported as
        INVALID_ORDER_PARAMETERS and is never approved.
        """
        try:
            prop_side = _coerce_side(prop_order.side, "prop_order.side")
            prop_price = _to_decimal(prop_order.price, "prop_order.price")
        except ValueError as exc:
            return self._invalid(str(exc))

        if prop_order.quantity <= 0:
            return self._invalid(
                f"prop_order.quantity must be positive, got {prop_order.quantity}"
            )
        if prop_price <= 0:
            return self._invalid(f"prop_order.price must be positive, got {prop_order.price}")

        conflicts: List[ConflictFinding] = []
        exceptions: List[str] = []

        for client_order in self.pending_client_orders:
            try:
                client_side = _coerce_side(client_order.side, "client_order.side")
                client_limit = _to_decimal(client_order.limit_price, "client_order.limit_price")
            except ValueError as exc:
                # Fail closed: a client order we cannot parse may be one we would be
                # trading ahead of.
                return self._invalid(f"client order {client_order.order_id}: {exc}")

            if client_order.symbol != prop_order.symbol or client_side is not prop_side:
                continue
            if client_order.quantity <= 0 or client_limit <= 0:
                return self._invalid(
                    f"client order {client_order.order_id} has non-positive quantity or price"
                )

            improvement = minimum_price_improvement(
                client_limit, prop_order.security_type, self.inside_spread
            )
            # A proprietary trade is permitted only when it is worse for the firm than
            # the customer's limit by at least the Rule 5320.06 increment.
            if prop_side is OrderSide.BUY:
                conflicting = prop_price < client_limit + improvement
            else:
                conflicting = prop_price > client_limit - improvement

            if not conflicting:
                continue

            applied = self._applicable_exception(prop_order, client_order, client_limit)
            if applied is not None:
                logger.info(
                    "Rule 5320 exception %s applied: prop order %s vs client order %s",
                    applied.value, prop_order.order_id, client_order.order_id,
                )
                exceptions.append(applied.value)
                continue

            satisfies = (
                prop_price <= client_limit if prop_side is OrderSide.BUY
                else prop_price >= client_limit
            )
            reason = (
                "price would satisfy the customer limit"
                if satisfies
                else f"price improvement is below the Rule 5320.06 increment of {improvement}"
            )
            logger.critical(
                "FINRA Rule 5320 trading ahead: prop order %s (%s %s @ %s) vs unexecuted "
                "client order %s (limit %s) - %s",
                prop_order.order_id, prop_side.value, prop_order.quantity, prop_price,
                client_order.order_id, client_limit, reason,
            )
            conflicts.append(
                ConflictFinding(
                    client_order_id=client_order.order_id,
                    client_limit_price=client_limit,
                    prop_price=prop_price,
                    reason=reason,
                )
            )

        if conflicts:
            blocked = ", ".join(c.client_order_id for c in conflicts)
            return ConflictAuditResult(
                is_approved=False,
                violation_type=ViolationCode.TRADING_AHEAD_5320.value,
                exception_applied=None,
                reason=(
                    f"Proprietary order trades ahead of unexecuted client order(s) {blocked} "
                    f"on the same side with no applicable Rule 5320 exception."
                ),
                conflicts=conflicts,
                exceptions_applied=exceptions,
            )

        return ConflictAuditResult(
            is_approved=True,
            violation_type=None,
            # Back-compatible scalar: the first exception that permitted the order.
            exception_applied=exceptions[0] if exceptions else None,
            reason=(
                f"Order approved under Rule 5320 exception(s): {', '.join(exceptions)}."
                if exceptions
                else "Order approved; no conflicting pending client orders."
            ),
            exceptions_applied=exceptions,
        )

    def enforce_prop_order(self, prop_order: PropOrder) -> ConflictAuditResult:
        """
        Same audit as `evaluate_prop_order`, but raises `Rule5320ViolationError` instead
        of returning a rejected result, so a caller that forgets to branch on
        `is_approved` cannot leak a blocked order to the market.
        """
        result = self.evaluate_prop_order(prop_order)
        if not result.is_approved:
            raise Rule5320ViolationError(result)
        return result

    def _applicable_exception(
        self,
        prop_order: PropOrder,
        client_order: ClientOrder,
        client_limit: Decimal,
    ) -> Optional[ExceptionCode]:
        # Rule 5320.05 - odd lot: the obligation does not attach to a customer order
        # for less than one round lot.
        if client_order.quantity < self.round_lot:
            return ExceptionCode.ODD_LOT

        # Rule 5320.02 - no-knowledge. Requires a genuinely effective barrier, and is
        # withheld from the market-making desk in OTC Equity Securities.
        barrier_available = (
            prop_order.security_type is SecurityType.NMS_STOCK
            or prop_order.trading_unit_type is TradingUnitType.NON_MARKET_MAKING
        )
        if (
            barrier_available
            and prop_order.barriers_effective
            and prop_order.info_barrier_id != client_order.info_barrier_id
        ):
            return ExceptionCode.NO_KNOWLEDGE_BARRIER

        # Rule 5320.01 - large orders and institutional accounts, on negative consent.
        if client_order.negative_consent_disclosed and not client_order.opted_in_5320:
            if client_order.is_institutional_account:
                return ExceptionCode.INSTITUTIONAL_NEGATIVE_CONSENT
            order_value = client_limit * Decimal(client_order.quantity)
            if (
                client_order.quantity >= LARGE_ORDER_MIN_SHARES
                and order_value >= LARGE_ORDER_MIN_VALUE
            ):
                return ExceptionCode.LARGE_ORDER_NEGATIVE_CONSENT
        return None

    @staticmethod
    def _invalid(reason: str) -> ConflictAuditResult:
        logger.error("Rule 5320 audit could not be completed: %s", reason)
        return ConflictAuditResult(
            is_approved=False,
            violation_type=ViolationCode.INVALID_ORDER_PARAMETERS.value,
            exception_applied=None,
            reason=f"Audit failed closed: {reason}",
        )

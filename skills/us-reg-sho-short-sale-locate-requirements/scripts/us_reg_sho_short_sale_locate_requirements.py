"""Pre-trade compliance gate for US SEC Regulation SHO (17 CFR 242.200-204).

Scope and non-scope are deliberate:

* Rule 200(g) marking is *validated*, not *determined*. Deciding that a sale is
  "long" requires net-long-position and deliverability facts (17 CFR 242.200(a)-(f))
  that live in the books-and-records system, not here.
* Rule 203(b)(1) locate verification is enforced for every SHORT and SHORT_EXEMPT
  order. The Rule 203(b)(2) exceptions (broker-to-broker reliance, deemed ownership,
  bona fide market making) are intentionally NOT implemented -- claiming one is a
  documented firm decision, not something an order gate should infer.
* Rule 201 is a price test for *covered securities* (NMS stocks) only, and the 10%
  trigger determination is made by the listing market under 242.201(b)(3), not by
  this engine. evaluate_local_trigger is a monitoring aid, never the authority.
* Rule 204 close-out is a clearing-participant obligation on settled fails and is
  out of scope for a pre-trade gate; see references/standards.md.

Failure philosophy: the order path never raises. Structurally invalid or
un-testable orders return a non-compliant RegSHOValidationResult so the rejection
is logged and auditable. RegSHOError is reserved for registry administration
errors (granting/releasing locates), where the caller is firm operations code.
"""

import datetime
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Rule 201(b)(1)(i): the restriction bites at a price "less than or equal to" the
# current national best bid, so a compliant short must be strictly above it. Float
# prices cannot represent every sub-penny increment exactly, so the comparison is
# nudged in the *rejecting* direction: an order within this tolerance of the NBB is
# treated as at-or-below it. Erring toward rejection is the only safe direction for
# a price test -- the opposite error executes a prohibited short sale.
NBB_PRICE_EPSILON = 1e-6

# Rule 201(b)(1)(i) circuit breaker threshold: a decline of 10% or more from the
# prior day's closing price as determined by the listing market.
RULE_201_DECLINE_THRESHOLD = 0.10


def _utcnow() -> datetime.datetime:
    """Timezone-aware UTC now. datetime.utcnow() is naive and deprecated in 3.12+."""
    return datetime.datetime.now(datetime.timezone.utc)


def _as_utc(moment: datetime.datetime) -> datetime.datetime:
    """Normalize a caller-supplied datetime to aware UTC.

    Locate records often arrive from a prime broker feed with naive timestamps.
    Comparing those against an aware now raises TypeError, which in an order gate
    would surface as an unhandled exception on the trading path. Naive input is
    interpreted as UTC, matching how this module stamps its own records.
    """
    if moment.tzinfo is None:
        return moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc)


def _is_finite_number(value: object) -> bool:
    """True only for a real, finite numeric value (rejects NaN, +/-Inf, bool, str)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


class OrderMarking(Enum):
    """17 CFR 242.200(g) order markings. These three are exhaustive."""

    LONG = "LONG"
    SHORT = "SHORT"
    SHORT_EXEMPT = "SHORT_EXEMPT"


class ShortExemptReason(Enum):
    """Permissible bases for a "short exempt" marking under 17 CFR 242.200(g)(2).

    Rule 200(g)(2) permits the marking *only if* the requirements of 242.201(c) or
    242.201(d) are met, so an order marked SHORT_EXEMPT must name which one. Bona
    fide market making is deliberately absent: it is a Rule 203(b)(2)(iii) *locate*
    exception, not a Rule 201 price-test exception, and must never be recorded here.
    """

    PRICED_ABOVE_NBB_AT_SUBMISSION = "PRICED_ABOVE_NBB_AT_SUBMISSION"  # 242.201(c)
    SELLERS_DELAY_IN_DELIVERY = "SELLERS_DELAY_IN_DELIVERY"            # 242.201(d)(1)
    ODD_LOT_MARKET_MAKER = "ODD_LOT_MARKET_MAKER"                      # 242.201(d)(2)
    DOMESTIC_ARBITRAGE = "DOMESTIC_ARBITRAGE"                          # 242.201(d)(3)
    INTERNATIONAL_ARBITRAGE = "INTERNATIONAL_ARBITRAGE"                # 242.201(d)(4)
    OVER_ALLOTMENT_OR_LAY_OFF = "OVER_ALLOTMENT_OR_LAY_OFF"            # 242.201(d)(5)
    RISKLESS_PRINCIPAL = "RISKLESS_PRINCIPAL"                          # 242.201(d)(6)
    VWAP = "VWAP"                                                      # 242.201(d)(7)


class LocateStatus(Enum):
    LOCATE_GRANTED = "LOCATE_GRANTED"
    LOCATE_EXHAUSTED = "LOCATE_EXHAUSTED"
    LOCATE_EXPIRED = "LOCATE_EXPIRED"
    LOCATE_NOT_FOUND = "LOCATE_NOT_FOUND"


class RegSHOError(Exception):
    """Raised on locate-registry administration errors (grant / release).

    Never raised from validate_order_intent: an order gate must return a decision,
    not an exception, so that every rejection lands in the audit log.
    """


@dataclass
class LocateRecord:
    """A locate obtained under 17 CFR 242.203(b)(1) and documented under (b)(1)(iii).

    expires_at reflects firm policy on locate validity (industry practice is
    good-for-the-trading-day); Reg SHO itself prescribes no locate lifetime.
    """

    locate_id: str
    symbol: str
    quantity_allocated: int
    quantity_used: int = 0
    lender_id: str = "PRIME_BROKER_DESK"
    granted_at: datetime.datetime = field(default_factory=_utcnow)
    expires_at: datetime.datetime = field(
        default_factory=lambda: _utcnow() + datetime.timedelta(hours=8)
    )

    @property
    def remaining_quantity(self) -> int:
        return self.quantity_allocated - self.quantity_used

    def expired_as_of(self, as_of: Optional[datetime.datetime] = None) -> bool:
        """Whether the locate has lapsed. Pass as_of for deterministic replay."""
        moment = _as_utc(as_of) if as_of is not None else _utcnow()
        return moment > _as_utc(self.expires_at)

    @property
    def is_expired(self) -> bool:
        return self.expired_as_of()


@dataclass
class SSRRestriction:
    """An active Rule 201 short sale price test restriction for one symbol.

    effective_through is optional and defaults to None, meaning "in force until
    explicitly cleared". Rule 201(b)(1)(ii) runs the restriction for the remainder of
    the trigger day *and the following day*, which requires a trading calendar this
    module does not own. Guessing that calendar could lift a restriction early, so
    the default is to keep it on and let the caller clear it from the authoritative
    SIP Reg SHO price test indicator.
    """

    symbol: str
    triggered_at: datetime.datetime = field(default_factory=_utcnow)
    effective_through: Optional[datetime.datetime] = None
    source: str = "SIP_PRICE_TEST_INDICATOR"


@dataclass
class OrderIntent:
    """A sell order presented to the gate before submission.

    nbb_price must be the current national best bid (242.600(b)(60)) at submission.
    It is only consulted where Rule 201 requires it; when it is required and missing
    or non-positive, the order is rejected rather than passed.
    """

    order_id: str
    symbol: str
    marking: OrderMarking
    quantity: int
    price: float
    nbb_price: float
    nbo_price: float
    locate_id: Optional[str] = None
    short_exempt_reason: Optional[ShortExemptReason] = None


@dataclass
class RegSHOValidationResult:
    order_id: str
    is_compliant: bool
    marking: OrderMarking
    locate_id: Optional[str]
    ssr_active: bool
    rejection_reason: Optional[str]
    audit_timestamp: datetime.datetime = field(default_factory=_utcnow)
    locate_status: Optional[LocateStatus] = None
    short_exempt_reason: Optional[ShortExemptReason] = None
    reserved_quantity: int = 0


@dataclass
class LocateReservation:
    """Locate capacity held against a specific order until released."""

    order_id: str
    locate_id: str
    quantity: int
    reserved_at: datetime.datetime = field(default_factory=_utcnow)
    released_at: Optional[datetime.datetime] = None

    @property
    def is_released(self) -> bool:
        return self.released_at is not None


class RegSHOShortSaleEngine:
    """Pre-trade SEC Regulation SHO gate for equity sell orders.

    Enforces:
      * Rule 200(g) -- an explicit marking, with a named statutory basis whenever
        the marking is SHORT_EXEMPT.
      * Rule 203(b)(1) -- a valid, unexpired, symbol-matched locate with sufficient
        remaining capacity for every SHORT and SHORT_EXEMPT order.
      * Rule 201(b)(1)(i) -- while the price test is in force for a covered security,
        a SHORT order must be priced strictly above the current national best bid.

    The engine is retry-safe: re-validating an order_id already decided returns the
    original decision and reserves nothing, so a timed-out or replayed pre-trade check
    cannot double-count locate inventory.

    Not thread-safe. Serialize calls per instance, or shard by symbol, if the order
    path is concurrent -- validate_order_intent performs a read-modify-write on
    locate inventory.
    """

    def __init__(self) -> None:
        self.locate_registry: Dict[str, LocateRecord] = {}
        self.ssr_restrictions: Dict[str, SSRRestriction] = {}
        self.reservations: Dict[str, LocateReservation] = {}
        self.audit_log: List[RegSHOValidationResult] = []
        self._decisions: Dict[str, RegSHOValidationResult] = {}
        self._fingerprints: Dict[str, tuple] = {}
        logger.info("Initialized SEC Regulation SHO short sale locate engine")

    # ------------------------------------------------------------------
    # Locate registry (Rule 203(b)(1))
    # ------------------------------------------------------------------

    def grant_locate(
        self,
        locate_id: str,
        symbol: str,
        quantity: int,
        lender_id: str = "PRIME_BROKER_1",
        ttl_hours: float = 8,
        granted_at: Optional[datetime.datetime] = None,
    ) -> LocateRecord:
        """Register a locate granted by a prime broker or clearing firm.

        Raises RegSHOError on a duplicate locate_id: silently replacing a record
        would reset quantity_used to zero and re-open already-consumed capacity,
        which is the locate double-count this gate exists to prevent.
        """
        if not locate_id or not str(locate_id).strip():
            raise RegSHOError("Locate ID must be a non-empty string.")
        if not symbol or not str(symbol).strip():
            raise RegSHOError("Locate symbol must be a non-empty string.")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise RegSHOError(
                f"Locate quantity must be a positive integer, got {quantity!r}."
            )
        if not _is_finite_number(ttl_hours) or ttl_hours <= 0:
            raise RegSHOError(
                f"Locate ttl_hours must be positive and finite, got {ttl_hours!r}."
            )
        if locate_id in self.locate_registry:
            raise RegSHOError(
                f"Locate ID '{locate_id}' already registered; "
                "re-granting would discard consumed capacity."
            )

        now = _as_utc(granted_at) if granted_at is not None else _utcnow()
        record = LocateRecord(
            locate_id=locate_id,
            symbol=symbol.strip().upper(),
            quantity_allocated=quantity,
            quantity_used=0,
            lender_id=lender_id,
            granted_at=now,
            expires_at=now + datetime.timedelta(hours=ttl_hours),
        )
        self.locate_registry[locate_id] = record
        logger.info(
            "Reg SHO locate granted: locate_id=%s symbol=%s quantity=%d lender=%s expires_at=%s",
            locate_id,
            record.symbol,
            quantity,
            lender_id,
            record.expires_at.isoformat(),
        )
        return record

    def release_locate_reservation(self, order_id: str) -> LocateReservation:
        """Return reserved locate capacity after an order is cancelled or rejected.

        A pre-trade approval reserves capacity; if the order never reaches the market
        (venue rejection, client cancel, session drop) that capacity would otherwise
        leak and block legitimate shorts for the rest of the locate's life.

        The order's recorded decision is deliberately kept. Re-submitting the same
        order_id after a release still returns the original decision and reserves
        nothing -- a genuinely new short sale needs a new order ID. Re-applying a
        locate to a later short sale is only permissible under the narrow conditions
        in SEC Reg SHO FAQ 4.4 and never for threshold or hard-to-borrow securities,
        so it is a firm policy decision made outside this gate.
        """
        reservation = self.reservations.get(order_id)
        if reservation is None:
            raise RegSHOError(f"No locate reservation recorded for order '{order_id}'.")
        if reservation.is_released:
            raise RegSHOError(
                f"Locate reservation for order '{order_id}' already released."
            )

        locate = self.locate_registry.get(reservation.locate_id)
        if locate is None:
            raise RegSHOError(
                f"Locate '{reservation.locate_id}' for order '{order_id}' "
                "is no longer registered."
            )

        locate.quantity_used -= reservation.quantity
        reservation.released_at = _utcnow()
        logger.info(
            "Reg SHO locate reservation released: order_id=%s locate_id=%s quantity=%d remaining=%d",
            order_id,
            reservation.locate_id,
            reservation.quantity,
            locate.remaining_quantity,
        )
        return reservation

    # ------------------------------------------------------------------
    # Rule 201 short sale price test state
    # ------------------------------------------------------------------

    def trigger_rule_201_ssr(
        self,
        symbol: str,
        effective_through: Optional[datetime.datetime] = None,
        triggered_at: Optional[datetime.datetime] = None,
        source: str = "SIP_PRICE_TEST_INDICATOR",
    ) -> SSRRestriction:
        """Record that the Rule 201 price test is in force for symbol.

        Under 242.201(b)(3) the *listing market* determines the 10% decline and makes
        it available under 242.603(b); the authoritative input is the SIP Reg SHO
        price test indicator, not a locally computed decline. source records which
        input was used so an examiner can tell the two apart.

        Leaving effective_through as None keeps the restriction in force until it is
        explicitly cleared, which is the safe default -- see SSRRestriction.
        """
        sym = symbol.strip().upper()
        restriction = SSRRestriction(
            symbol=sym,
            triggered_at=_as_utc(triggered_at) if triggered_at is not None else _utcnow(),
            effective_through=(
                _as_utc(effective_through) if effective_through is not None else None
            ),
            source=source,
        )
        self.ssr_restrictions[sym] = restriction
        logger.warning(
            "Rule 201 short sale price test ACTIVE: symbol=%s source=%s effective_through=%s",
            sym,
            source,
            restriction.effective_through.isoformat()
            if restriction.effective_through
            else "until-cleared",
        )
        return restriction

    def deactivate_rule_201_ssr(self, symbol: str) -> None:
        """Clear the Rule 201 restriction, normally on the SIP indicator turning off."""
        sym = symbol.strip().upper()
        if self.ssr_restrictions.pop(sym, None) is not None:
            logger.info("Rule 201 short sale price test cleared: symbol=%s", sym)

    def is_ssr_active(
        self, symbol: str, as_of: Optional[datetime.datetime] = None
    ) -> bool:
        """Whether the Rule 201 price test applies to symbol."""
        restriction = self.ssr_restrictions.get(symbol.strip().upper())
        if restriction is None:
            return False
        if restriction.effective_through is None:
            return True
        moment = _as_utc(as_of) if as_of is not None else _utcnow()
        return moment < restriction.effective_through

    def evaluate_local_trigger(self, prior_close: float, last_trade_price: float) -> bool:
        """Advisory 10% decline check -- NOT the compliance trigger.

        The listing market makes the determination under 242.201(b)(3). Use this only
        to detect that the SIP indicator looks stale or missing, and escalate; never
        to decide that an order may be shorted at or below the bid.
        """
        if not _is_finite_number(prior_close) or prior_close <= 0:
            raise RegSHOError(
                f"prior_close must be positive and finite, got {prior_close!r}."
            )
        if not _is_finite_number(last_trade_price) or last_trade_price < 0:
            raise RegSHOError(
                f"last_trade_price must be non-negative and finite, got {last_trade_price!r}."
            )
        threshold = prior_close * (1.0 - RULE_201_DECLINE_THRESHOLD)
        # Tolerance leans toward flagging: a false trigger prompts a check, a missed
        # one hides a live restriction.
        return last_trade_price <= threshold + 1e-9

    # ------------------------------------------------------------------
    # Pre-trade gate
    # ------------------------------------------------------------------

    def validate_order_intent(
        self, order: OrderIntent, as_of: Optional[datetime.datetime] = None
    ) -> RegSHOValidationResult:
        """Validate a sell order against Rule 200(g), Rule 203(b)(1) and Rule 201.

        Returns a decision; never raises on the order path. On approval of a short
        sale, locate capacity is reserved against order.order_id -- release it with
        release_locate_reservation if the order does not reach the market.

        Only decisions that actually reserved capacity are remembered. A rejection
        reserved nothing, so an order re-submitted after its cause is fixed (a locate
        granted, a restriction lifted) is evaluated afresh rather than being frozen
        at its first refusal.
        """
        if not isinstance(order.order_id, str) or not order.order_id.strip():
            logger.error("Reg SHO REJECTION: order_id must be a non-empty string.")
            return self._record(
                RegSHOValidationResult(
                    order_id=str(order.order_id),
                    is_compliant=False,
                    marking=order.marking,
                    locate_id=order.locate_id,
                    ssr_active=False,
                    rejection_reason="Invalid order: order_id must be a non-empty string; "
                    "a short sale cannot be reserved or audited without one.",
                )
            )

        fingerprint = self._fingerprint(order)
        prior = self._decisions.get(order.order_id)
        if prior is not None:
            if self._fingerprints.get(order.order_id) != fingerprint:
                logger.error(
                    "Reg SHO REJECTION [%s]: order ID reused with different terms.",
                    order.order_id,
                )
                return self._record(
                    self._reject(
                        order,
                        isinstance(order.symbol, str)
                        and self.is_ssr_active(order.symbol, as_of),
                        "Duplicate order_id submitted with different order terms; "
                        "no locate reserved. Use a unique order_id per short sale.",
                    )
                )
            logger.warning(
                "Reg SHO duplicate validation for order_id=%s; returning original decision "
                "without reserving locate capacity.",
                order.order_id,
            )
            return prior

        result = self._evaluate(order, as_of)
        if result.reserved_quantity > 0:
            self._decisions[order.order_id] = result
            self._fingerprints[order.order_id] = fingerprint
        return self._record(result)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _fingerprint(order: OrderIntent) -> tuple:
        return (
            order.symbol.strip().upper() if isinstance(order.symbol, str) else order.symbol,
            order.marking,
            order.quantity,
            order.price,
            order.nbb_price,
            order.locate_id,
            order.short_exempt_reason,
        )

    def _record(self, result: RegSHOValidationResult) -> RegSHOValidationResult:
        self.audit_log.append(result)
        return result

    @staticmethod
    def _reject(
        order: OrderIntent,
        ssr_active: bool,
        reason: str,
        locate_status: Optional[LocateStatus] = None,
    ) -> RegSHOValidationResult:
        return RegSHOValidationResult(
            order_id=order.order_id,
            is_compliant=False,
            marking=order.marking,
            locate_id=order.locate_id,
            ssr_active=ssr_active,
            rejection_reason=reason,
            locate_status=locate_status,
            short_exempt_reason=order.short_exempt_reason,
        )

    def _evaluate(
        self, order: OrderIntent, as_of: Optional[datetime.datetime]
    ) -> RegSHOValidationResult:
        if not isinstance(order.symbol, str) or not order.symbol.strip():
            return self._reject(
                order, False, "Invalid order: symbol must be a non-empty string."
            )
        sym = order.symbol.strip().upper()
        ssr_active = self.is_ssr_active(sym, as_of)

        if not isinstance(order.marking, OrderMarking):
            return self._reject(
                order,
                ssr_active,
                "Rule 200(g) Violation: order marking must be LONG, SHORT or SHORT_EXEMPT.",
            )
        if (
            not isinstance(order.quantity, int)
            or isinstance(order.quantity, bool)
            or order.quantity <= 0
        ):
            # A negative quantity would *credit* the locate pool; a zero quantity
            # produces a compliance approval for an order that does not exist.
            return self._reject(
                order,
                ssr_active,
                f"Invalid order: quantity must be a positive integer, got {order.quantity!r}.",
            )
        if not _is_finite_number(order.price) or order.price < 0:
            # NaN silently defeats every price comparison below, so it is caught here
            # rather than being allowed to pass the Rule 201 test by default.
            return self._reject(
                order,
                ssr_active,
                f"Invalid order: price must be a non-negative finite number, "
                f"got {order.price!r}.",
            )

        if order.marking == OrderMarking.LONG:
            if order.locate_id:
                logger.warning(
                    "Order %s marked LONG carries locate_id=%s; a long sale needs no locate. "
                    "Check the marking determination upstream.",
                    order.order_id,
                    order.locate_id,
                )
            logger.info("Reg SHO PASS [%s]: LONG sale, no locate required.", order.order_id)
            return RegSHOValidationResult(
                order_id=order.order_id,
                is_compliant=True,
                marking=order.marking,
                locate_id=None,
                ssr_active=ssr_active,
                rejection_reason=None,
            )

        # Rule 200(g)(2): "short exempt" is permissible only where 242.201(c) or (d)
        # is satisfied, so the basis must be named and recorded.
        if order.marking == OrderMarking.SHORT_EXEMPT and not isinstance(
            order.short_exempt_reason, ShortExemptReason
        ):
            logger.error(
                "Reg SHO REJECTION [%s]: SHORT_EXEMPT without a Rule 201(c)/(d) basis.",
                order.order_id,
            )
            return self._reject(
                order,
                ssr_active,
                "Rule 200(g)(2) Violation: SHORT_EXEMPT requires a named 17 CFR 242.201(c) "
                "or 242.201(d) basis (ShortExemptReason).",
            )
        if order.marking == OrderMarking.SHORT and order.short_exempt_reason is not None:
            return self._reject(
                order,
                ssr_active,
                "Invalid order: short_exempt_reason supplied on an order marked SHORT.",
            )

        # Rule 203(b)(1): a locate is required for every short sale, including one
        # marked "short exempt" -- the Rule 201 marking does not reach Rule 203
        # (SEC Division of Trading and Markets, Rule 201 FAQ).
        locate_rejection = self._check_locate(order, sym, ssr_active, as_of)
        if locate_rejection is not None:
            return locate_rejection
        locate = self.locate_registry[order.locate_id]

        price_rejection = self._check_rule_201(order, ssr_active)
        if price_rejection is not None:
            return price_rejection

        locate.quantity_used += order.quantity
        self.reservations[order.order_id] = LocateReservation(
            order_id=order.order_id,
            locate_id=locate.locate_id,
            quantity=order.quantity,
        )
        logger.info(
            "Reg SHO COMPLIANT [%s]: marking=%s locate_id=%s reserved=%d remaining=%d ssr_active=%s",
            order.order_id,
            order.marking.value,
            order.locate_id,
            order.quantity,
            locate.remaining_quantity,
            ssr_active,
        )
        return RegSHOValidationResult(
            order_id=order.order_id,
            is_compliant=True,
            marking=order.marking,
            locate_id=order.locate_id,
            ssr_active=ssr_active,
            rejection_reason=None,
            locate_status=LocateStatus.LOCATE_GRANTED,
            short_exempt_reason=order.short_exempt_reason,
            reserved_quantity=order.quantity,
        )

    def _check_locate(
        self,
        order: OrderIntent,
        sym: str,
        ssr_active: bool,
        as_of: Optional[datetime.datetime],
    ) -> Optional[RegSHOValidationResult]:
        """Rule 203(b)(1). Returns a rejection, or None if the locate is good."""
        if not order.locate_id:
            logger.error(
                "Reg SHO REJECTION [%s]: short sale with no locate identifier.",
                order.order_id,
            )
            return self._reject(
                order,
                ssr_active,
                "Rule 203(b)(1) Violation: short sale missing mandatory locate identifier.",
                LocateStatus.LOCATE_NOT_FOUND,
            )

        locate = self.locate_registry.get(order.locate_id)
        if locate is None:
            logger.error(
                "Reg SHO REJECTION [%s]: locate_id=%s not registered.",
                order.order_id,
                order.locate_id,
            )
            return self._reject(
                order,
                ssr_active,
                f"Rule 203(b)(1) Violation: locate ID '{order.locate_id}' invalid or not found.",
                LocateStatus.LOCATE_NOT_FOUND,
            )

        if locate.symbol != sym:
            logger.error(
                "Reg SHO REJECTION [%s]: locate symbol %s does not match order symbol %s.",
                order.order_id,
                locate.symbol,
                sym,
            )
            return self._reject(
                order,
                ssr_active,
                f"Rule 203(b)(1) Violation: locate symbol '{locate.symbol}' does not match "
                f"order symbol '{sym}'.",
                LocateStatus.LOCATE_NOT_FOUND,
            )

        if locate.expired_as_of(as_of):
            logger.error(
                "Reg SHO REJECTION [%s]: locate_id=%s expired at %s.",
                order.order_id,
                order.locate_id,
                _as_utc(locate.expires_at).isoformat(),
            )
            return self._reject(
                order,
                ssr_active,
                "Rule 203(b)(1) Violation: short sale locate has expired.",
                LocateStatus.LOCATE_EXPIRED,
            )

        if locate.remaining_quantity < order.quantity:
            logger.error(
                "Reg SHO REJECTION [%s]: insufficient locate capacity requested=%d available=%d.",
                order.order_id,
                order.quantity,
                locate.remaining_quantity,
            )
            return self._reject(
                order,
                ssr_active,
                f"Rule 203(b)(1) Violation: insufficient locate quantity available "
                f"({locate.remaining_quantity} < {order.quantity}).",
                LocateStatus.LOCATE_EXHAUSTED,
            )

        return None

    def _check_rule_201(
        self, order: OrderIntent, ssr_active: bool
    ) -> Optional[RegSHOValidationResult]:
        """Rule 201 price test. Returns a rejection, or None if the order may proceed."""
        price_test_applies = ssr_active and order.marking == OrderMarking.SHORT

        # 242.201(c) lets a broker-dealer mark an order "short exempt" because it is
        # priced above the current NBB at submission, and requires written policies
        # reasonably designed to prevent an incorrect such identification. Verifying
        # the claim is that control; the 242.201(d) bases need no price check.
        verify_above_nbb = (
            ssr_active
            and order.marking == OrderMarking.SHORT_EXEMPT
            and order.short_exempt_reason
            == ShortExemptReason.PRICED_ABOVE_NBB_AT_SUBMISSION
        )

        if not (price_test_applies or verify_above_nbb):
            return None

        if not _is_finite_number(order.nbb_price) or order.nbb_price <= 0:
            # Rule 201 is enforced against the *current* national best bid. Without
            # one, the test cannot be evaluated, and passing the order through would
            # turn a market data outage into a silent compliance bypass.
            logger.error(
                "Reg SHO REJECTION [%s]: Rule 201 in force but national best bid is "
                "unavailable (nbb_price=%r).",
                order.order_id,
                order.nbb_price,
            )
            return self._reject(
                order,
                ssr_active,
                "Rule 201 price test cannot be evaluated: no valid current national best bid "
                f"(nbb_price={order.nbb_price!r}). Order rejected rather than passed.",
            )

        if order.price <= order.nbb_price + NBB_PRICE_EPSILON:
            if verify_above_nbb:
                logger.error(
                    "Reg SHO REJECTION [%s]: SHORT_EXEMPT claimed under 242.201(c) but "
                    "price %.4f is not above NBB %.4f.",
                    order.order_id,
                    order.price,
                    order.nbb_price,
                )
                return self._reject(
                    order,
                    ssr_active,
                    f"Rule 201(c) Violation: order marked SHORT_EXEMPT as priced above the "
                    f"national best bid, but price ${order.price:.4f} <= NBB "
                    f"${order.nbb_price:.4f}.",
                )
            logger.error(
                "Reg SHO REJECTION [%s]: Rule 201 price test -- short price %.4f must be "
                "above NBB %.4f.",
                order.order_id,
                order.price,
                order.nbb_price,
            )
            return self._reject(
                order,
                ssr_active,
                f"Rule 201(b)(1)(i) Violation: short sale price ${order.price:.4f} is at or "
                f"below the national best bid ${order.nbb_price:.4f}.",
            )

        return None

"""
cme-globex-futures-api-integration:
Pre-trade validation and iLink 3 *New Order - Single* field assembly for CME
Globex futures order entry.

What this module is
-------------------
It is the *validation and field-assembly* layer, not a session handler. It takes
an intended order plus the market state it will be judged against, applies the
checks CME Globex applies at the gateway, and returns the fields an iLink 3
``NewOrderSingle`` (template 514) must carry. Encoding those fields as SBE and
running the FIXP session is out of scope - see
``fix-protocol-session-management-across-venues``.

The four exchange rules it encodes
----------------------------------

**1. Operator ID / Tag 50 (CME Rule 576).**
Every message sent to CME Globex must carry a registered Operator ID. In the
FIX-based iLink sessions this is tag 50 ``SenderSubID``; in iLink 3 SBE it is
the ``SenderID`` field of the same message, and CME documents it identically as
"Operator ID. Should be unique per Firm ID". CME's published requirements:

  - between 2 and 18 characters,
  - alphanumeric characters are strongly encouraged; only a short list of
    non-alphanumeric characters is permitted (CME's Operator ID registration
    documents ``_ - : @``), and that list has been narrowed by advisory notice
    before - see ``references/standards.md``,
  - **not case sensitive**, so uniqueness may not be achieved by case alone,
  - the value sent must *exactly match* the ID registered in EFS.

Because the list of permitted symbols is a policy that CME has changed,
``permitted_operator_id_symbols`` is a constructor parameter rather than a
hard-coded constant. Set it to the list in the advisory notice in force for
your firm.

**2. Manual Order Indicator / Tag 1028 (CME Rule 536.B.).**
CME requires tag 1028 on in-scope order entry messages; a message without it,
or with an invalid value, is rejected. ``Y`` means a human entered the order,
``N`` means it was generated or routed without direct human interaction -
anything produced by an execution algorithm is ``N``. This module refuses to
assemble a message unless the caller states which it is; there is no default,
because guessing the audit-trail value of a regulatory field is exactly the
error the field exists to prevent.

Registration type interacts with this: a Team/ATS Operator ID may only submit
automated messages, and a manually entered order must carry an individual
Operator ID. Pass the team-registered IDs as ``team_operator_ids`` and the
engine enforces the pairing; leave it empty and the check is skipped rather
than guessed at.

**3. Market with Protection.**
CME Globex fills a market order only within a protected range. For a buy, the
protection price limit is the current **best offer plus** the product's
protection points; for a sell it is the current **best bid minus** them.
Quantity that cannot be filled inside that range does not keep sweeping the
book - it rests as a limit order *at the limit of the protected range*.

The exchange applies this itself. This module does not "convert" a market order
into a limit order behind the caller's back: ``ord_type`` stays ``MARKET`` and
``protection_price_limit`` reports where the residual will rest, so position and
risk logic can account for a resting order it never explicitly placed. Protection
points are usually about half the product's non-reviewable range and are
published per product, not derived.

**4. Price banding.**
CME rejects orders priced outside a band computed as the Banding Reference Price
(BRP) plus/minus the product's static Price Band Variation (PBV). The band is
**one-sided per order side**: it rejects buys *above* BRP + PBV and sells *below*
BRP - PBV. It deliberately does not stop a bid below the market or an offer above
it, which are ordinary passive orders. Checking both sides - the obvious-looking
implementation - rejects legitimate resting orders the exchange would accept, so
``_check_price_band`` is directional.

Banding is applied to price-based orders. A market order carries no price, so
this module does **not** reject one whose computed protection limit falls outside
the band; it sets ``protection_limit_outside_band`` and logs a warning, leaving
the decision to the caller's risk layer.

Tick conformance
----------------
An order price must be a multiple of the product's minimum price increment. Two
places break this in practice: a limit price carried over from a different
product's tick, and a protection limit computed from protection points that are
not themselves a whole number of ticks. Limit prices are rejected when off-tick,
because silently moving a price the caller specified is worse than refusing it.
Computed protection limits are rounded *toward* the market - down for a buy, up
for a sell - which tightens protection rather than loosening it.

Tick arithmetic runs in ``decimal.Decimal``: ``5000.1 % 0.05`` is not 0 in binary
floating point, and a naive modulo check rejects prices that are perfectly valid.

Not handled here
----------------
ClOrdID uniqueness and retry safety (``order-placement-idempotency``), session
sequencing and gap recovery, self-match prevention
(``exchange-self-match-prevention-configuration``), and the daily refresh of
tick, PBV and protection values from CME's product reference files.
"""
from __future__ import annotations

import logging
import math
import numbers
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation
from typing import Dict, FrozenSet, Iterable, Optional

logger = logging.getLogger(__name__)

#: CME Rule 576: Operator IDs are between two and 18 characters in length.
OPERATOR_ID_MIN_LEN = 2
OPERATOR_ID_MAX_LEN = 18

#: Non-alphanumeric characters accepted by CME's Operator ID registration.
#: CME has narrowed this list by advisory notice before; override it on the
#: engine rather than editing this default.
DEFAULT_OPERATOR_ID_SYMBOLS = frozenset("_-:@")

VALID_SIDES = frozenset({"BUY", "SELL"})
VALID_ORDER_TYPES = frozenset({"LIMIT", "MARKET"})


class CmeOrderValidationError(ValueError):
    """Base class for pre-trade rejections raised before transmission."""


class OperatorIdError(CmeOrderValidationError):
    """Tag 50 / SenderID fails CME Rule 576 requirements."""


class ManualOrderIndicatorError(CmeOrderValidationError):
    """Tag 1028 is absent or inconsistent with the Operator ID registration type."""


class PriceBandingError(CmeOrderValidationError):
    """Limit price falls outside the CME price band on the constrained side."""


class TickConformanceError(CmeOrderValidationError):
    """Price is not a multiple of the product's minimum price increment."""


class ContractSpecError(ValueError):
    """Contract specification is internally inconsistent."""


def _require_finite(value: float, label: str) -> float:
    """Reject NaN/Inf before they propagate silently through price comparisons."""
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise CmeOrderValidationError(f"{label} must be a real number, got {value!r}.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise CmeOrderValidationError(f"{label} must be finite, got {value!r}.")
    return numeric


def _decimal(value: float) -> Decimal:
    """Convert via ``str`` so 0.05 is 0.05 and not its binary expansion."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:  # pragma: no cover - guarded upstream
        raise CmeOrderValidationError(f"Cannot interpret {value!r} as a price.") from exc


def is_on_tick(price: float, tick_size: float) -> bool:
    """True when ``price`` is an exact multiple of ``tick_size``.

    Uses decimal arithmetic: ``5000.1 % 0.05`` is 0.049999... in binary floating
    point, which would reject a valid price.
    """
    return _decimal(price) % _decimal(tick_size) == 0


def round_toward_market(price: float, tick_size: float, side: str) -> float:
    """Snap a computed price to a tick in the conservative direction.

    A buy protection limit rounds *down* and a sell protection limit rounds *up*,
    so rounding can only tighten the protected range, never widen it past what
    the caller's protection points allow.
    """
    quantum = _decimal(tick_size)
    ticks = _decimal(price) / quantum
    snapped = ticks.to_integral_value(rounding=ROUND_FLOOR if side == "BUY" else ROUND_CEILING)
    return float(snapped * quantum)


@dataclass
class ContractSpec:
    """Per-product parameters from CME's published product reference files.

    ``price_band_points`` is the Price Band Variation (PBV): a static per-product
    value applied symmetrically around the Banding Reference Price.
    ``protection_points`` is the Market-with-Protection offset, usually about half
    the product's non-reviewable range. Neither is derived from the other, and
    neither is guessable - load both per product and refresh them daily.
    """

    symbol: str
    tick_size: float
    price_band_points: float        # Price Band Variation (PBV)
    protection_points: float        # Market-with-Protection offset

    def __post_init__(self) -> None:
        if not self.symbol or not isinstance(self.symbol, str):
            raise ContractSpecError("ContractSpec.symbol must be a non-empty string.")
        if not isinstance(self.tick_size, (int, float)) or not math.isfinite(self.tick_size) or self.tick_size <= 0:
            raise ContractSpecError(
                f"{self.symbol}: tick_size must be a finite positive number, got {self.tick_size!r}.")
        for field_name in ("price_band_points", "protection_points"):
            value = getattr(self, field_name)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ContractSpecError(
                    f"{self.symbol}: {field_name} must be a finite non-negative number, got {value!r}.")


@dataclass
class CmeOrder:
    """An intended order, before exchange validation.

    ``manual_order_indicator`` is tag 1028 and has no default: ``True`` when a
    human entered the order, ``False`` when it was generated or routed without
    direct human interaction. Leaving it ``None`` is rejected at submission.
    """

    symbol: str
    side: str                                    # 'BUY' or 'SELL'
    quantity: int
    order_type: str                              # 'LIMIT' or 'MARKET'
    operator_id: str                             # Tag 50 / SenderID (Rule 576)
    account: str
    price: Optional[float] = None                # Limit price (LIMIT orders only)
    manual_order_indicator: Optional[bool] = None  # Tag 1028 (Rule 536.B.)


@dataclass
class FormattedIlinkMessage:
    """Fields for an iLink 3 New Order - Single (template 514)."""

    msg_type: str
    cl_ord_id: str
    symbol: str
    side: str
    order_qty: int
    price: Optional[float]           # Tag 44. None for MARKET: tag 44 is not sent
    operator_id: str                 # Tag 50 / SenderID
    account: str
    is_mwp_converted: bool
    ord_type: str = "LIMIT"
    manual_order_indicator: bool = False          # Tag 1028
    protection_price_limit: Optional[float] = None
    protection_limit_outside_band: bool = False


class CmeGlobexOrderEngine:
    """Pre-trade gate for CME Globex futures order entry.

    Enforces Rule 576 (Operator ID), Rule 536.B. (Manual Order Indicator),
    tick conformance, directional price banding, and computes the
    Market-with-Protection price limit.

    Args:
        contract_specs: per-symbol tick, PBV and protection points.
        team_operator_ids: Operator IDs registered to a team/ATS rather than an
            individual. Compared case-insensitively, since CME Operator IDs are
            not case sensitive. Empty (the default) disables the manual/ATS
            pairing check rather than guessing a registration type.
        permitted_operator_id_symbols: non-alphanumeric characters accepted in an
            Operator ID, per the advisory notice in force for your firm.
    """

    def __init__(
        self,
        contract_specs: Dict[str, ContractSpec],
        team_operator_ids: Optional[Iterable[str]] = None,
        permitted_operator_id_symbols: Optional[Iterable[str]] = None,
    ) -> None:
        self.contract_specs = contract_specs
        self.team_operator_ids: FrozenSet[str] = frozenset(
            oid.strip().upper() for oid in (team_operator_ids or ()))
        self.permitted_operator_id_symbols: FrozenSet[str] = frozenset(
            permitted_operator_id_symbols if permitted_operator_id_symbols is not None
            else DEFAULT_OPERATOR_ID_SYMBOLS)

    # ---------------------------------------------------------------- Rule 576

    def validate_operator_id(self, operator_id: str) -> bool:
        """True when ``operator_id`` satisfies CME Rule 576's format requirements.

        Length 2-18, and every character either alphanumeric or in the permitted
        symbol set. Whitespace is not permitted: the value transmitted must match
        the EFS registration exactly, and a padded ID does not.
        """
        if not operator_id or not isinstance(operator_id, str):
            return False
        if not OPERATOR_ID_MIN_LEN <= len(operator_id) <= OPERATOR_ID_MAX_LEN:
            return False
        return all(c.isascii() and (c.isalnum() or c in self.permitted_operator_id_symbols)
                   for c in operator_id)

    def _check_manual_order_indicator(self, order: CmeOrder) -> bool:
        if order.manual_order_indicator is None:
            raise ManualOrderIndicatorError(
                "Rule 536.B. Violation: Manual Order Indicator (Tag 1028) is required on "
                "iLink order entry messages. Set manual_order_indicator=True for an order a "
                "human entered, False for one generated or routed without human interaction.")
        if not isinstance(order.manual_order_indicator, bool):
            raise ManualOrderIndicatorError(
                f"Manual Order Indicator (Tag 1028) must be a bool, got "
                f"{order.manual_order_indicator!r}.")
        if order.manual_order_indicator and order.operator_id.upper() in self.team_operator_ids:
            raise ManualOrderIndicatorError(
                f"Rule 536.B. Violation: Operator ID '{order.operator_id}' is registered to a "
                "team/ATS and may only submit automated messages (Tag 1028 = N). A manually "
                "entered order must carry an individual Operator ID.")
        return order.manual_order_indicator

    # ------------------------------------------------------------- price checks

    def _check_price_band(
        self, price: float, side: str, spec: ContractSpec, reference_price: float
    ) -> bool:
        """Directional band test. Returns True when ``price`` breaches the band.

        Buys are constrained above BRP + PBV, sells below BRP - PBV. The
        unconstrained side is deliberately not tested: a bid far below the market
        or an offer far above it is an ordinary passive order that CME accepts.
        """
        if side == "BUY":
            return price > reference_price + spec.price_band_points
        return price < reference_price - spec.price_band_points

    def _band_text(self, side: str, spec: ContractSpec, reference_price: float) -> str:
        if side == "BUY":
            return f"buy limit <= {reference_price + spec.price_band_points:.6g}"
        return f"sell limit >= {reference_price - spec.price_band_points:.6g}"

    # ------------------------------------------------------------------ process

    def process_order(
        self,
        order: CmeOrder,
        cl_ord_id: str,
        current_bid: Optional[float] = None,
        current_ask: Optional[float] = None,
        reference_price: Optional[float] = None,
    ) -> FormattedIlinkMessage:
        """Validate an order and return the iLink 3 New Order - Single fields.

        Args:
            order: the intended order.
            cl_ord_id: client order ID (tag 11). Uniqueness is the caller's
                responsibility - see ``order-placement-idempotency``.
            current_bid / current_ask: top of book. Required for a MARKET order on
                the side the protection limit is computed from; unused for LIMIT.
            reference_price: CME Banding Reference Price - the last trade, else
                the best bid/offer, else the settlement price. Required for a
                LIMIT order; optional for MARKET, where it only enables the
                advisory ``protection_limit_outside_band`` flag.

        Raises:
            OperatorIdError: Tag 50 fails Rule 576.
            ManualOrderIndicatorError: Tag 1028 absent, or a team/ATS ID on a
                manual order.
            TickConformanceError: limit price is not a multiple of the tick.
            PriceBandingError: limit price breaches the band on its constrained side.
            CmeOrderValidationError: any other malformed field or market input.
            KeyError: no contract specification loaded for the symbol.
        """
        if not cl_ord_id or not isinstance(cl_ord_id, str) or not cl_ord_id.strip():
            raise CmeOrderValidationError("cl_ord_id (Tag 11) must be a non-empty string.")

        # 1. Rule 576 Tag 50 validation. Checked first: an unregistered Operator
        #    ID invalidates the message regardless of anything else in it.
        if not isinstance(order.operator_id, str) or not self.validate_operator_id(order.operator_id):
            raise OperatorIdError(
                f"Rule 576 Violation: Invalid Operator ID (Tag 50) {order.operator_id!r}. Must be "
                f"{OPERATOR_ID_MIN_LEN}-{OPERATOR_ID_MAX_LEN} characters, alphanumeric or one of "
                f"{''.join(sorted(self.permitted_operator_id_symbols))}, with no whitespace.")

        # 2. Rule 536.B. Tag 1028 validation.
        manual = self._check_manual_order_indicator(order)

        # 3. Order field validation. An unrecognised order type must not fall
        #    through to the LIMIT path and be sent as something the caller did
        #    not ask for.
        side = order.side.upper() if isinstance(order.side, str) else order.side
        if side not in VALID_SIDES:
            raise CmeOrderValidationError(
                f"Side must be one of {sorted(VALID_SIDES)}, got {order.side!r}.")

        order_type = order.order_type.upper() if isinstance(order.order_type, str) else order.order_type
        if order_type not in VALID_ORDER_TYPES:
            raise CmeOrderValidationError(
                f"Order type must be one of {sorted(VALID_ORDER_TYPES)}, got {order.order_type!r}. "
                "Stop and stop-limit orders carry their own protection semantics and are not "
                "handled by this module.")

        # numbers.Integral rather than int, so an integral quantity arriving from
        # numpy is not rejected as a type error. bool is Integral and is not a quantity.
        if (isinstance(order.quantity, bool) or not isinstance(order.quantity, numbers.Integral)
                or order.quantity <= 0):
            raise CmeOrderValidationError(
                f"OrderQty (Tag 38) must be a positive integer number of contracts, got "
                f"{order.quantity!r}.")

        if not order.account or not isinstance(order.account, str):
            raise CmeOrderValidationError("Account must be a non-empty string.")

        # 4. Spec lookup.
        if order.symbol not in self.contract_specs:
            raise KeyError(f"Unknown contract specification for symbol: {order.symbol}")
        spec = self.contract_specs[order.symbol]

        # A crossed top of book means the quote is stale or mis-assembled. Warn
        # rather than reject: the protection limit still comes off one side, and
        # a hard rejection here would block trading on a transient data artefact.
        if current_bid is not None and current_ask is not None:
            if _require_finite(current_bid, "current_bid") > _require_finite(current_ask, "current_ask"):
                logger.warning(
                    "Crossed top of book for %s: bid %.6g > ask %.6g. Protection limits computed "
                    "from a crossed quote may be wrong.", order.symbol, current_bid, current_ask)

        is_mwp = order_type == "MARKET"
        protection_limit: Optional[float] = None
        protection_outside_band = False

        if is_mwp:
            if order.price is not None:
                logger.warning(
                    "MARKET order for %s carries price=%r. A market order is transmitted without "
                    "tag 44; the price is ignored.", order.symbol, order.price)
            # 5. Market with Protection: CME fills within a protected range and
            #    rests the residual at its limit. Compute where that is.
            if side == "BUY":
                if current_ask is None:
                    raise CmeOrderValidationError(
                        "A MARKET buy needs current_ask: the protection price limit is the best "
                        "offer plus the product's protection points.")
                raw_limit = _require_finite(current_ask, "current_ask") + spec.protection_points
            else:
                if current_bid is None:
                    raise CmeOrderValidationError(
                        "A MARKET sell needs current_bid: the protection price limit is the best "
                        "bid minus the product's protection points.")
                raw_limit = _require_finite(current_bid, "current_bid") - spec.protection_points

            if raw_limit <= 0:
                raise CmeOrderValidationError(
                    f"Computed protection price limit {raw_limit:.6g} is not positive; "
                    f"protection_points ({spec.protection_points}) exceeds the market price.")

            protection_limit = round_toward_market(raw_limit, spec.tick_size, side)
            # A market order carries no tag 44. The protection limit is reported
            # separately so nobody encodes it as a price on a MARKET message.
            target_price = None

            if reference_price is not None:
                reference_price = _require_finite(reference_price, "reference_price")
                protection_outside_band = self._check_price_band(
                    protection_limit, side, spec, reference_price)
                if protection_outside_band:
                    # Advisory, not a rejection: banding applies to price-based
                    # orders, and a market order carries no price.
                    logger.warning(
                        "MWP protection limit %.6g for %s %s falls outside the price band (%s); "
                        "residual quantity would rest outside the band.",
                        protection_limit, side, order.symbol,
                        self._band_text(side, spec, reference_price))

            logger.info(
                "MARKET %s %s: protection price limit %.6g (best %s %.6g %s %.6g protection points)",
                side, order.symbol, protection_limit,
                "offer" if side == "BUY" else "bid",
                current_ask if side == "BUY" else current_bid,
                "+" if side == "BUY" else "-", spec.protection_points)
        else:
            # 6. LIMIT: tick conformance, then the directional band.
            if order.price is None:
                raise CmeOrderValidationError("LIMIT order must specify a price.")
            target_price = _require_finite(order.price, "price")
            if target_price <= 0:
                raise CmeOrderValidationError(f"Limit price must be positive, got {target_price!r}.")

            if not is_on_tick(target_price, spec.tick_size):
                raise TickConformanceError(
                    f"Tick Conformance Violation: price {target_price:.6g} is not a multiple of the "
                    f"{order.symbol} minimum price increment {spec.tick_size}.")

            if reference_price is None:
                raise CmeOrderValidationError(
                    "reference_price (the CME Banding Reference Price) is required to band-check a "
                    "LIMIT order.")
            reference_price = _require_finite(reference_price, "reference_price")

            if self._check_price_band(target_price, side, spec, reference_price):
                raise PriceBandingError(
                    f"Price Banding Violation: {side} limit {target_price:.6g} breaches the price "
                    f"band for {order.symbol} (allowed: {self._band_text(side, spec, reference_price)}, "
                    f"reference {reference_price:.6g}, PBV {spec.price_band_points}).")

        return FormattedIlinkMessage(
            msg_type="NewOrderSingle",
            cl_ord_id=cl_ord_id,
            symbol=order.symbol,
            side=side,
            order_qty=order.quantity,
            price=target_price,
            operator_id=order.operator_id,
            account=order.account,
            is_mwp_converted=is_mwp,
            ord_type=order_type,
            manual_order_indicator=manual,
            protection_price_limit=protection_limit,
            protection_limit_outside_band=protection_outside_band,
        )

"""
eurex-market-data-and-order-api: client-side pre-dispatch validation for Eurex
derivatives orders on T7, and the T7 ETI request header that carries them.

What this module is and is not
------------------------------
It is a **client-side pre-trade gate** for Eurex (MIC ``XEUR``) order entry over
the T7 Enhanced Trading Interface (ETI). It

* holds a price-level aggregated order book of the kind T7 EMDI publishes, and
  derives best bid/ask, mid price and depth imbalance from it;
* mirrors the venue's **Price Reasonability Check** (PRC) locally, so an order
  T7 would reject is caught before it consumes a transaction slot;
* validates the contract's minimum price change in exact decimal arithmetic;
* encodes prices and quantities as the scaled integers ETI actually carries; and
* packs the 24-byte T7 ETI request header.

It is **not** a transport: nothing here opens a socket, logs on to a gateway, or
sends an order. ``ready_to_send`` means "passed local validation", never "the
exchange has it".

It is **not** a FAST decoder. T7 EMDI carries FIX 5.0 SP2 semantics in FAST
encoding over UDP multicast; this module models the *book state* an EMDI decoder
produces, not the wire decoding itself.

It is **not** a full message encoder. It frames the header, not the message body.
Body offsets are release-specific -- take them from the T7 ETI Derivatives Message
Reference for the release you are certified against.

Why the price reasonability check is the centre of this module
--------------------------------------------------------------
The Eurex PRC is *directional* and is referenced to the **opposite-side best
price**, not to the mid price. Per the T7 Release 14.0 Functional Reference,
chapter 6.2.1.1, the condition for rejection is::

    Buy  Limit Price > Reference Price + Price Range(Reference Price)
    Sell Limit Price < Reference Price - Price Range(Reference Price)

so a buy far *below* the market and a sell far *above* it never fail the check.
A symmetric ``abs(price - mid) > band`` test -- the shape this module used before
-- rejects exactly those harmless passive orders, while being referenced to a
price T7 does not use.

The price range is not a constant either. It is calculated from a price range
table published per product and instrument by the T7 Reference Data Interface
(RDI group message ``PriceRangeRules``) as

    Price Range(Reference Price) = APR + |Reference Price| * PPR / 100

and is widened by ``FastMarketPercentage`` during fast or stressed markets. This
module therefore takes the table as an input and refuses to guess: there is no
published, universal "50 index points" band for any Eurex product.

References: see ``references/standards.md``.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, Final, List, Optional, Sequence, Set, Tuple, Union

logger = logging.getLogger(__name__)

PriceInput = Union[Decimal, str, int, float]

# --- T7 ETI template IDs (derivatives) ---------------------------------------
#: New Order Single or Multi Leg -- the current order-add request.
TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG: Final[int] = 10138
#: New Order Single or Multi Leg (short layout).
TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG_SHORT: Final[int] = 10139
TEMPLATE_REPLACE_ORDER_SINGLE_OR_MULTI_LEG: Final[int] = 10140
TEMPLATE_REPLACE_ORDER_SINGLE_OR_MULTI_LEG_SHORT: Final[int] = 10141
TEMPLATE_CANCEL_ORDER_SINGLE_OR_MULTI_LEG: Final[int] = 10142

#: Production date on which T7 Release 14.1 removed the requests listed below.
DECOMMISSIONED_TEMPLATES_REMOVED_ON: Final[str] = "2026-05-18"

#: Order management requests removed with ETI 14.1, mapped to their replacement.
#: The Eurex readiness newsflash for T7 Release 14.1 names the removed single-leg
#: requests (10100, 10125, 10106, 10126, 10109) and the removed derivatives
#: multi-leg requests (10113, 10129, 10114, 10130, 10123) alongside the five
#: replacements; the pairing below follows that listing order and the request
#: names. Re-confirm against the message reference for your release.
DECOMMISSIONED_TEMPLATE_REPLACEMENTS: Final[Dict[int, int]] = {
    10100: TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG,            # New Order Single
    10125: TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG_SHORT,      # New Order Single (short)
    10106: TEMPLATE_REPLACE_ORDER_SINGLE_OR_MULTI_LEG,        # Replace Order Single
    10126: TEMPLATE_REPLACE_ORDER_SINGLE_OR_MULTI_LEG_SHORT,  # Replace Order Single (short)
    10109: TEMPLATE_CANCEL_ORDER_SINGLE_OR_MULTI_LEG,         # Cancel Order Single
    10113: TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG,            # New Order Multi Leg
    10129: TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG_SHORT,      # New Order Multi Leg (short)
    10114: TEMPLATE_REPLACE_ORDER_SINGLE_OR_MULTI_LEG,        # Replace Order Multi Leg
    10130: TEMPLATE_REPLACE_ORDER_SINGLE_OR_MULTI_LEG_SHORT,  # Replace Order Multi Leg (short)
    10123: TEMPLATE_CANCEL_ORDER_SINGLE_OR_MULTI_LEG,         # Cancel Order Multi Leg
}

# --- T7 ETI field domains (derivatives) --------------------------------------
#: Side (tag 54): an unsigned int on the wire, not a string.
SIDE_BUY: Final[int] = 1
SIDE_SELL: Final[int] = 2
_SIDE_ALIASES: Final[Dict[str, int]] = {"BUY": SIDE_BUY, "B": SIDE_BUY,
                                        "SELL": SIDE_SELL, "S": SIDE_SELL}

#: OrdType (tag 40).
ORD_TYPE_MARKET: Final[int] = 1
ORD_TYPE_LIMIT: Final[int] = 2
ORD_TYPE_STOP: Final[int] = 3
ORD_TYPE_STOP_LIMIT: Final[int] = 4
_ORD_TYPE_NAMES: Final[Dict[int, str]] = {
    ORD_TYPE_MARKET: "Market", ORD_TYPE_LIMIT: "Limit",
    ORD_TYPE_STOP: "Stop", ORD_TYPE_STOP_LIMIT: "Stop Limit",
}

#: TimeInForce (tag 59). GTC and GTD are standard orders only.
TIME_IN_FORCE_DAY: Final[int] = 0
TIME_IN_FORCE_GTC: Final[int] = 1
TIME_IN_FORCE_IOC: Final[int] = 3
TIME_IN_FORCE_FOK: Final[int] = 4
TIME_IN_FORCE_GTD: Final[int] = 6
_TIME_IN_FORCE_NAMES: Final[Dict[int, str]] = {
    TIME_IN_FORCE_DAY: "Day (GFD)", TIME_IN_FORCE_GTC: "Good Till Cancelled",
    TIME_IN_FORCE_IOC: "Immediate or Cancel", TIME_IN_FORCE_FOK: "Fill or Kill",
    TIME_IN_FORCE_GTD: "Good Till Date",
}

#: TradingCapacity (tag 1815). The derivatives message reference lists three
#: values; the cash-market domain is different and adds Riskless Principal (9)
#: and Retail Customer (10). Do not copy the cash domain onto a Eurex order.
TRADING_CAPACITY_CUSTOMER_AGENCY: Final[int] = 1
TRADING_CAPACITY_PRINCIPAL_PROPRIETARY: Final[int] = 5
TRADING_CAPACITY_MARKET_MAKER: Final[int] = 6
_TRADING_CAPACITY_NAMES: Final[Dict[int, str]] = {
    TRADING_CAPACITY_CUSTOMER_AGENCY: "Customer (Agency)",
    TRADING_CAPACITY_PRINCIPAL_PROPRIETARY: "Principal (Proprietary)",
    TRADING_CAPACITY_MARKET_MAKER: "Market Maker",
}

#: PriceValidityCheckType (tag 28710) -- how the participant asks T7 to run the
#: Price Reasonability Check on this order. "Optional" and "Mandatory" differ only
#: in what happens when no reference price can be determined: optional accepts the
#: order unchecked, mandatory rejects it.
PRICE_VALIDITY_CHECK_NONE: Final[int] = 0
PRICE_VALIDITY_CHECK_OPTIONAL: Final[int] = 1
PRICE_VALIDITY_CHECK_MANDATORY: Final[int] = 2
_PRICE_VALIDITY_CHECK_NAMES: Final[Dict[int, str]] = {
    PRICE_VALIDITY_CHECK_NONE: "None",
    PRICE_VALIDITY_CHECK_OPTIONAL: "Optional",
    PRICE_VALIDITY_CHECK_MANDATORY: "Mandatory",
}

#: ProductComplex (tag 1227): 1 = Simple instrument. This module scopes itself to
#: outright (simple) instruments; futures spreads and strategies have their own
#: ProductComplex values, price gradations and leg groups.
PRODUCT_COMPLEX_SIMPLE_INSTRUMENT: Final[int] = 1

#: The instrument state in which T7 performs the Price Reasonability Check.
INSTRUMENT_STATE_CONTINUOUS: Final[str] = "Continuous"

# --- ETI scaled-integer encodings --------------------------------------------
#: ETI ``PriceType`` is "Price in integer format including 8 decimals", an 8-byte
#: signed int. ``Qty`` is the same width with 4 implied decimals.
PRICE_SCALE_EXPONENT: Final[int] = 8
QTY_SCALE_EXPONENT: Final[int] = 4
PRICE_SCALE: Final[Decimal] = Decimal(10) ** PRICE_SCALE_EXPONENT
QTY_SCALE: Final[Decimal] = Decimal(10) ** QTY_SCALE_EXPONENT
_INT64_MIN: Final[int] = -(2 ** 63)
_INT64_MAX: Final[int] = 2 ** 63 - 1
_UINT16_MAX: Final[int] = 2 ** 16 - 1
_UINT32_MAX: Final[int] = 2 ** 32 - 1

#: Wire layout of MessageHeaderIn + RequestHeader, little endian:
#: BodyLen(u32) TemplateID(u16) NetworkMsgID(8B) Pad2(2B) MsgSeqNum(u32) SenderSubID(u32).
_ETI_HEADER_STRUCT: Final[struct.Struct] = struct.Struct("<IH8s2sII")
ETI_REQUEST_HEADER_LEN: Final[int] = 24

#: Length of the fixed part of New Order Single or Multi Leg (10138) in T7
#: Release 14.0: the last fixed field is Pad2_3 at offset 278, width 2. The
#: LegOrdGrp repeating group follows at offset 280 with 8-byte records.
#: Offsets change between releases -- re-derive these for yours.
NEW_ORDER_FIXED_LEN_R14: Final[int] = 280
NEW_ORDER_LEG_RECORD_LEN_R14: Final[int] = 8


class EurexOrderValidationError(ValueError):
    """Raised when an order or engine argument is structurally invalid.

    Subclasses ``ValueError``. Construction fails loudly rather than coercing: a
    mistyped quantity or an unscaled price is a defect, not an input to fix up.
    """


def _to_decimal(value: PriceInput, name: str) -> Decimal:
    """Convert a price-like input to Decimal without inheriting float artefacts."""
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, bool):
        raise EurexOrderValidationError(f"{name} must be a number, got {value!r}")
    elif isinstance(value, (int, str)):
        try:
            candidate = Decimal(value)
        except InvalidOperation as exc:
            raise EurexOrderValidationError(f"{name} is not a valid decimal: {value!r}") from exc
    elif isinstance(value, float):
        # str() first: Decimal(133.33) is 133.330000000000012505552149377... ,
        # which is not a multiple of any tick and does not scale exactly.
        candidate = Decimal(str(value))
    else:
        raise EurexOrderValidationError(f"{name} must be a number, got {type(value).__name__}")

    if not candidate.is_finite():
        raise EurexOrderValidationError(f"{name} must be finite, got {value!r}")
    return candidate


def _require_int(value: object, name: str, *, minimum: Optional[int] = None,
                 maximum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise EurexOrderValidationError(f"{name} must be an int, got {value!r}")
    if minimum is not None and value < minimum:
        raise EurexOrderValidationError(f"{name} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise EurexOrderValidationError(f"{name} must be <= {maximum}, got {value}")
    return value


def normalise_side(side: object) -> int:
    """Map 'BUY'/'SELL' (or the wire values 1/2) to the ETI Side (tag 54) value."""
    if isinstance(side, bool):
        raise EurexOrderValidationError(f"side must be 'BUY'/'SELL' or 1/2, got {side!r}")
    if isinstance(side, int):
        if side in (SIDE_BUY, SIDE_SELL):
            return side
        raise EurexOrderValidationError(
            f"side must be {SIDE_BUY} (Buy) or {SIDE_SELL} (Sell), got {side}")
    if isinstance(side, str):
        try:
            return _SIDE_ALIASES[side.strip().upper()]
        except KeyError:
            raise EurexOrderValidationError(
                f"side must be one of {sorted(_SIDE_ALIASES)}, got {side!r}") from None
    raise EurexOrderValidationError(f"side must be a string or int, got {type(side).__name__}")


def price_to_eti_int(price: PriceInput) -> int:
    """Encode a price as the ETI ``PriceType``: signed integer, 8 implied decimals.

    Raises:
        EurexOrderValidationError: the price needs more than 8 decimal places to
            represent exactly, or overflows a signed 64-bit integer. Rounding here
            would transmit a price the caller did not ask for.
    """
    decimal_price = _to_decimal(price, "price")
    scaled = decimal_price * PRICE_SCALE
    if scaled != scaled.to_integral_value():
        raise EurexOrderValidationError(
            f"price {decimal_price} cannot be represented exactly with "
            f"{PRICE_SCALE_EXPONENT} decimals")
    as_int = int(scaled)
    if not _INT64_MIN <= as_int <= _INT64_MAX:
        raise EurexOrderValidationError(f"price {decimal_price} overflows a signed 64-bit integer")
    return as_int


def qty_to_eti_int(quantity: PriceInput) -> int:
    """Encode a quantity as the ETI ``Qty`` type: signed integer, 4 implied decimals."""
    decimal_qty = _to_decimal(quantity, "quantity")
    scaled = decimal_qty * QTY_SCALE
    if scaled != scaled.to_integral_value():
        raise EurexOrderValidationError(
            f"quantity {decimal_qty} cannot be represented exactly with "
            f"{QTY_SCALE_EXPONENT} decimals")
    as_int = int(scaled)
    if not _INT64_MIN <= as_int <= _INT64_MAX:
        raise EurexOrderValidationError(
            f"quantity {decimal_qty} overflows a signed 64-bit integer")
    return as_int


def new_order_body_len(num_legs: int = 0) -> int:
    """BodyLen for New Order Single or Multi Leg (10138) in T7 Release 14.0.

    ``BodyLen`` is "Number of bytes for the message, including this field", so this
    is the whole message: the 280-byte fixed part plus one 8-byte ``LegOrdGrp``
    record per leg. A simple instrument has no legs and yields 280.
    """
    legs = _require_int(num_legs, "num_legs", minimum=0, maximum=144)
    return NEW_ORDER_FIXED_LEN_R14 + legs * NEW_ORDER_LEG_RECORD_LEN_R14


# --- Price range tables ------------------------------------------------------

@dataclass(frozen=True)
class PriceRangeRule:
    """One row of a T7 price range table.

    ``interval_start`` is inclusive and ``interval_end`` exclusive; pass
    ``interval_end=None`` for the open-ended final row.
    """

    interval_start: Decimal
    interval_end: Optional[Decimal]
    absolute_param: Decimal
    percent_param: Decimal

    def contains(self, reference_price: Decimal) -> bool:
        """True when this row's interval covers ``reference_price``.

        Price range tables are defined for positive prices only, so a negative
        reference price is matched on its absolute value.
        """
        magnitude = abs(reference_price)
        if magnitude < self.interval_start:
            return False
        return self.interval_end is None or magnitude < self.interval_end


@dataclass(frozen=True)
class PriceRangeTable:
    """A T7 standard price range table, as published per product and instrument.

    The rows come from the T7 Reference Data Interface product and instrument
    snapshot messages (RDI group message ``PriceRangeRules``), or from the Trading
    Parameters File in the Products and Instruments Files. There is no universal
    default and this class does not invent one -- construct it from reference data
    for the instrument you are trading.

    ``fast_market_percentage`` is the RDI ``FastMarketPercentage`` for the product.
    """

    rules: Tuple[PriceRangeRule, ...]
    fast_market_percentage: Decimal = Decimal(0)

    @classmethod
    def from_rows(cls, rows: Sequence[Tuple[PriceInput, Optional[PriceInput],
                                            PriceInput, PriceInput]],
                  fast_market_percentage: PriceInput = 0) -> "PriceRangeTable":
        """Build a table from ``(start, end, absolute_param, percent_param)`` rows."""
        if not rows:
            raise EurexOrderValidationError("a price range table needs at least one row")
        rules = tuple(
            PriceRangeRule(
                interval_start=_to_decimal(start, "interval_start"),
                interval_end=None if end is None else _to_decimal(end, "interval_end"),
                absolute_param=_to_decimal(apr, "absolute_param"),
                percent_param=_to_decimal(ppr, "percent_param"),
            )
            for start, end, apr, ppr in rows
        )
        return cls(rules=rules,
                   fast_market_percentage=_to_decimal(fast_market_percentage,
                                                      "fast_market_percentage"))

    def price_range(self, reference_price: PriceInput, *,
                    fast_market: bool = False) -> Decimal:
        """Price range for a reference price.

        Implements the T7 Functional Reference formula

            Price Range = APR + |Reference Price| * PPR / 100

        taking APR and PPR from the row whose interval contains the reference
        price. During fast or stressed market conditions the result is scaled by
        ``(1 + FastMarketPercentage / 100)``.

        The result is deliberately not rounded: T7 applies the calculated range
        with its exact value.

        Raises:
            EurexOrderValidationError: no row covers the reference price.
        """
        reference = _to_decimal(reference_price, "reference_price")
        for rule in self.rules:
            if rule.contains(reference):
                price_range = (rule.absolute_param
                               + abs(reference) * rule.percent_param / Decimal(100))
                if fast_market:
                    price_range *= (Decimal(1) + self.fast_market_percentage / Decimal(100))
                return price_range
        raise EurexOrderValidationError(
            f"no price range table row covers reference price {reference}")


# --- Contract specifications -------------------------------------------------

@dataclass(frozen=True)
class EurexContractSpec:
    """Trading conventions for a Eurex product.

    ``tick_step`` is the product's on-book minimum price change and
    ``multiplier_eur`` the EUR value of one full price point:

    * ``FESX`` (EURO STOXX 50 Index Futures) is quoted in index points; one index
      point is EUR 10 and the minimum price change is 1 index point.
    * ``FGBL`` (Euro-Bund Futures) is quoted in percent of par on a EUR 100,000
      nominal, so one full point (one percent) is EUR 1,000 and the minimum price
      change of 0.01 percent is EUR 10.

    ``price_range_table`` is per-instrument reference data and defaults to None.
    Without it the Price Reasonability Check cannot be evaluated locally, which is
    reported honestly rather than approximated.
    """

    symbol: str
    tick_step: Decimal
    multiplier_eur: Decimal
    #: What one full price point means, for reports and rejection messages.
    point_description: str = "index point"
    price_range_table: Optional[PriceRangeTable] = None
    currency: str = "EUR"


def default_contract_specs() -> Dict[str, EurexContractSpec]:
    """Tick and multiplier conventions for two flagship Eurex futures.

    The values are the on-book (order book) minimum price changes from the Eurex
    contract specifications. Off-book standardised futures strategies use a finer
    price gradation -- for FESX, 0.01 index points since 24 June 2024 -- so these
    are not the right numbers for an off-book (TES) entry.
    """
    return {
        "FESX": EurexContractSpec(
            symbol="FESX",
            tick_step=Decimal("1"),
            multiplier_eur=Decimal("10"),
            point_description="index point",
        ),
        "FGBL": EurexContractSpec(
            symbol="FGBL",
            tick_step=Decimal("0.01"),
            multiplier_eur=Decimal("1000"),
            point_description="percent of par (EUR 100,000 nominal)",
        ),
    }


# --- T7 EMDI order book ------------------------------------------------------

@dataclass(frozen=True)
class EurexDepthLevel:
    """One price level of a price-level aggregated order book."""

    price: Decimal
    quantity: Decimal

    @classmethod
    def of(cls, price: PriceInput, quantity: PriceInput) -> "EurexDepthLevel":
        level_price = _to_decimal(price, "price")
        level_qty = _to_decimal(quantity, "quantity")
        if level_qty <= 0:
            raise EurexOrderValidationError(
                f"a depth level quantity must be > 0, got {level_qty}")
        return cls(price=level_price, quantity=level_qty)


@dataclass(frozen=True)
class EurexDepthBook:
    """Price-level aggregated book state of the kind T7 EMDI publishes.

    EMDI is the *un-netted* interface: it disseminates every order book change up
    to the configured depth, and reports every on-exchange trade individually. T7
    MDI is the netted one, aggregating changes over ``MarketDepthTimeInterval``
    and carrying fewer price levels. Both are price-level aggregated;
    order-by-order data is EOBI.

    ``bids`` are ordered best-first (descending price) and ``asks`` best-first
    (ascending price). ``msg_seq_num`` is the sequence number of the depth
    incremental message that produced this state, for gap detection.
    """

    bids: Tuple[EurexDepthLevel, ...] = ()
    asks: Tuple[EurexDepthLevel, ...] = ()
    msg_seq_num: Optional[int] = None
    #: Receive timestamp supplied by the caller. Nothing in this module reads the
    #: clock, so staleness is evaluated against a time the caller provides.
    recv_time_ns: Optional[int] = None

    @classmethod
    def from_levels(cls, bids: Sequence[Tuple[PriceInput, PriceInput]],
                    asks: Sequence[Tuple[PriceInput, PriceInput]],
                    msg_seq_num: Optional[int] = None,
                    recv_time_ns: Optional[int] = None) -> "EurexDepthBook":
        """Build a book from ``(price, quantity)`` pairs, validating the ordering.

        Raises:
            EurexOrderValidationError: a side is not strictly ordered best-first.
                A decoder emitting an unsorted or price-repeating ladder has lost
                book state, and silently sorting it here would hide that.
        """
        bid_levels = tuple(EurexDepthLevel.of(p, q) for p, q in bids)
        ask_levels = tuple(EurexDepthLevel.of(p, q) for p, q in asks)
        for name, levels, descending in (("bids", bid_levels, True),
                                         ("asks", ask_levels, False)):
            for previous, current in zip(levels, levels[1:]):
                ordered = (previous.price > current.price if descending
                           else previous.price < current.price)
                if not ordered:
                    raise EurexOrderValidationError(
                        f"{name} must be strictly ordered best-first, got "
                        f"{previous.price} before {current.price}")
        if msg_seq_num is not None:
            _require_int(msg_seq_num, "msg_seq_num", minimum=0)
        if recv_time_ns is not None:
            _require_int(recv_time_ns, "recv_time_ns", minimum=0)
        return cls(bids=bid_levels, asks=ask_levels, msg_seq_num=msg_seq_num,
                   recv_time_ns=recv_time_ns)

    @property
    def best_bid_price(self) -> Optional[Decimal]:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask_price(self) -> Optional[Decimal]:
        return self.asks[0].price if self.asks else None

    @property
    def spread(self) -> Optional[Decimal]:
        if self.best_bid_price is None or self.best_ask_price is None:
            return None
        return self.best_ask_price - self.best_bid_price

    @property
    def mid_price(self) -> Optional[Decimal]:
        """Arithmetic mid, or None when either side is empty.

        The mid is a display and analytics figure. It is **not** the reference
        price for the Price Reasonability Check -- see
        ``resolve_prc_reference_price``.
        """
        if self.best_bid_price is None or self.best_ask_price is None:
            return None
        return (self.best_bid_price + self.best_ask_price) / Decimal(2)

    @property
    def is_crossed(self) -> bool:
        """True when the best bid is strictly above the best ask."""
        if self.best_bid_price is None or self.best_ask_price is None:
            return False
        return self.best_bid_price > self.best_ask_price

    @property
    def is_locked(self) -> bool:
        """True when the best bid equals the best ask."""
        if self.best_bid_price is None or self.best_ask_price is None:
            return False
        return self.best_bid_price == self.best_ask_price

    def depth_imbalance(self, levels: int = 1) -> Optional[Decimal]:
        """Signed volume imbalance over the top ``levels`` price levels.

        ``(bid_qty - ask_qty) / (bid_qty + ask_qty)``, in ``[-1, +1]``: +1 is
        bid-only depth, -1 ask-only. Returns None when both sides are empty, since
        the ratio is then undefined rather than zero.
        """
        depth = _require_int(levels, "levels", minimum=1)
        bid_qty = sum((level.quantity for level in self.bids[:depth]), Decimal(0))
        ask_qty = sum((level.quantity for level in self.asks[:depth]), Decimal(0))
        total = bid_qty + ask_qty
        if total == 0:
            return None
        return (bid_qty - ask_qty) / total

    def is_stale(self, now_ns: int, max_age_ns: int) -> bool:
        """True when the book carries no receive time, or is older than ``max_age_ns``."""
        _require_int(now_ns, "now_ns", minimum=0)
        _require_int(max_age_ns, "max_age_ns", minimum=0)
        if self.recv_time_ns is None:
            return True
        return now_ns - self.recv_time_ns > max_age_ns


def detect_emdi_sequence_gap(previous: Optional[EurexDepthBook],
                             current: EurexDepthBook) -> Optional[str]:
    """Describe an EMDI depth sequence anomaly between two book states, else None.

    Messages on the T7 EMDI depth incremental feed carry their own ``MsgSeqNum``
    range per product. A skipped number means the book is missing updates and is
    no longer a faithful copy of T7's; a repeated or lower number means a
    duplicate or an out-of-order datagram.

    EMDI is published live-live on two identical services (A and B) with different
    multicast addresses, so the first move on a gap is to take the message from
    the other service. Only when both are missing it does the snapshot feed --
    linked to the incremental feed by ``LastMsgSeqNumProcessed`` -- become the
    recovery path. A book with a gap must not be used as a price reasonability
    reference until it has been rebuilt.
    """
    if previous is None or previous.msg_seq_num is None or current.msg_seq_num is None:
        return None
    expected = previous.msg_seq_num + 1
    if current.msg_seq_num == expected:
        return None
    if current.msg_seq_num <= previous.msg_seq_num:
        return (f"EMDI depth MsgSeqNum went backwards: {current.msg_seq_num} after "
                f"{previous.msg_seq_num} (duplicate or out-of-order datagram).")
    return (f"EMDI depth sequence gap: expected MsgSeqNum {expected}, got "
            f"{current.msg_seq_num} ({current.msg_seq_num - expected} message(s) missing).")


# --- Price Reasonability Check ----------------------------------------------

#: How the reference price was determined, or that it could not be.
PRC_PROCEDURE_STANDARD: Final[str] = "standard"
PRC_PROCEDURE_NON_STANDARD: Final[str] = "non_standard"
PRC_PROCEDURE_NOT_PERFORMED: Final[str] = "not_performed"


@dataclass(frozen=True)
class PriceReasonabilityOutcome:
    """Result of the local Price Reasonability Check pre-check.

    ``passed`` False means T7 would reject this limit price. ``passed`` True with
    ``procedure == PRC_PROCEDURE_NOT_PERFORMED`` means the check did not apply --
    not that the price was verified.
    """

    passed: bool
    procedure: str
    reference_price: Optional[Decimal] = None
    price_range: Optional[Decimal] = None
    reason: Optional[str] = None


def resolve_prc_reference_price(side: int, book: EurexDepthBook, price_range: Decimal, *,
                                alternative_reference_price: Optional[Decimal] = None,
                                smallest_allowed_limit_price: Optional[Decimal] = None,
                                ) -> Tuple[Optional[Decimal], str]:
    """Determine the PRC reference price, per T7 Functional Reference 6.2.1.2.

    Standard procedure: the reference price is the best price on the side
    *opposite* the order -- best ask for a buy, best bid for a sell. It applies
    only when both best prices are available and the bid/ask spread is less than
    or equal to the price range being applied. Where there is no best bid, the
    instrument's smallest allowed limit price stands in for it, if supplied; the
    documented substitution exists for instruments priced near zero, such as
    out-of-the-money option series.

    Non-standard procedure: an alternative reference price -- the last trade
    price or a theoretical price, or failing those the previous day's settlement
    price -- is combined with whichever best prices exist, per the documented
    table.

    Returns ``(reference_price, procedure)``. A None reference price means neither
    procedure could be applied and no check is possible.
    """
    raw_bid = book.best_bid_price
    best_ask = book.best_ask_price

    standard_bid = raw_bid if raw_bid is not None else smallest_allowed_limit_price
    if standard_bid is not None and best_ask is not None:
        if best_ask - standard_bid <= price_range:
            return (best_ask if side == SIDE_BUY else standard_bid), PRC_PROCEDURE_STANDARD

    # Non-standard procedure. The substitution above belongs to the standard
    # procedure only, so the table is read against the book's own best prices.
    alternative = alternative_reference_price
    if alternative is None:
        return None, PRC_PROCEDURE_NOT_PERFORMED

    if raw_bid is not None and best_ask is not None:
        if raw_bid <= alternative <= best_ask:
            return alternative, PRC_PROCEDURE_NON_STANDARD
        return (best_ask if side == SIDE_BUY else raw_bid), PRC_PROCEDURE_NON_STANDARD
    if raw_bid is None and best_ask is not None:
        if side == SIDE_BUY:
            return best_ask, PRC_PROCEDURE_NON_STANDARD
        return (alternative if alternative <= best_ask else best_ask), PRC_PROCEDURE_NON_STANDARD
    if raw_bid is not None and best_ask is None:
        if side == SIDE_SELL:
            return raw_bid, PRC_PROCEDURE_NON_STANDARD
        return (alternative if raw_bid <= alternative else raw_bid), PRC_PROCEDURE_NON_STANDARD
    return alternative, PRC_PROCEDURE_NON_STANDARD


# --- Orders and reports ------------------------------------------------------

@dataclass
class EurexOrderRequest:
    """A Eurex derivatives order, in the terms T7 ETI actually uses.

    ``contract_symbol`` and ``expiry`` are carried for human readability; the wire
    identifies the instrument numerically by ``security_id`` (tag 48) together
    with ``market_segment_id`` (tag 1300), both of which come from the T7
    Reference Data Interface. ``cl_ord_id`` is the ETI ``ClOrdID`` (tag 11), an
    unsigned integer -- the free-form string identifier is the separate
    ``FIXClOrdID`` (tag 30011).
    """

    cl_ord_id: int
    contract_symbol: str                 # e.g. 'FESX'
    expiry: str                          # e.g. '202609'
    security_id: int                     # SecurityID (tag 48)
    market_segment_id: int               # MarketSegmentID (tag 1300)
    side: Union[str, int]                # 'BUY'/'SELL' or Side (tag 54) 1/2
    order_qty: int                       # OrderQty (tag 38)
    price: PriceInput                    # Price (tag 44); pass str/Decimal to stay exact
    trading_capacity: int = TRADING_CAPACITY_PRINCIPAL_PROPRIETARY
    ord_type: int = ORD_TYPE_LIMIT
    time_in_force: int = TIME_IN_FORCE_DAY
    #: PriceValidityCheckType (tag 28710).
    price_validity_check_type: int = PRICE_VALIDITY_CHECK_MANDATORY


@dataclass(frozen=True)
class T7EtiRequestHeader:
    """The 24-byte T7 ETI inbound request header.

    Layout, little endian, per the T7 Release 14.0 ETI Derivatives Message
    Reference::

        MessageHeaderIn : BodyLen (u32, ofs 0)      -- whole message, incl. this field
                          TemplateID (u16, ofs 4)
                          NetworkMsgID (8 bytes, ofs 6, not used)
                          Pad2 (2 bytes, ofs 14, not used)
        RequestHeader   : MsgSeqNum (u32, ofs 16)
                          SenderSubID (u32, ofs 20)  -- T7 User ID

    There is no session identifier and no sending timestamp in this header. The
    session is established separately, and inbound requests carry no clock value.
    """

    body_len: int
    template_id: int
    msg_seq_num: int
    sender_sub_id: int
    network_msg_id: bytes = b"\x00" * 8

    def pack(self) -> bytes:
        """Serialise to the 24 wire bytes."""
        return _ETI_HEADER_STRUCT.pack(
            self.body_len, self.template_id,
            self.network_msg_id.ljust(8, b"\x00")[:8], b"\x00\x00",
            self.msg_seq_num, self.sender_sub_id)


@dataclass
class EurexOrderValidationReport:
    """Outcome of local pre-dispatch validation.

    ``ready_to_send`` means the order passed every check implemented here. It does
    not mean anything was transmitted -- this module has no transport.
    """

    cl_ord_id: int
    contract: str                        # e.g. 'FESX 202609'
    status: str
    ready_to_send: bool
    eti_header: Optional[T7EtiRequestHeader]
    rejection_reason: Optional[str]
    #: Quantity x price x multiplier, in the contract currency. For FGBL this is
    #: the market value implied by the percent-of-par price, not the EUR 100,000
    #: nominal per contract.
    contract_value_eur: Optional[Decimal] = None
    required_tick_step: Optional[Decimal] = None
    price_eti_int: Optional[int] = None
    order_qty_eti_int: Optional[int] = None
    side_wire_value: Optional[int] = None
    price_reasonability: Optional[PriceReasonabilityOutcome] = None
    warnings: Optional[List[str]] = None

    @property
    def is_dispatched(self) -> bool:
        """Deprecated alias for ``ready_to_send``.

        Retained for callers written against the previous API. The name was
        misleading: nothing in this module dispatches anything.
        """
        return self.ready_to_send


#: Deprecated alias. The previous name suggested a venue Execution Report (8),
#: which this is not -- it is a local pre-dispatch verdict.
EurexOrderExecutionReport = EurexOrderValidationReport


STATUS_OK: Final[str] = "STATUS_OK"
STATUS_INVALID_TICK_SIZE: Final[str] = "INVALID_TICK_SIZE"
STATUS_PRICE_REASONABILITY_BREACH: Final[str] = "PRICE_REASONABILITY_BREACH"
STATUS_INVALID_ORDER_FIELD: Final[str] = "INVALID_ORDER_FIELD"
STATUS_UNKNOWN_CONTRACT: Final[str] = "UNKNOWN_CONTRACT"
STATUS_DUPLICATE_CL_ORD_ID: Final[str] = "DUPLICATE_CL_ORD_ID"


class EurexMarketDataAndOrderApiEngine:
    """Local pre-dispatch validator and T7 ETI header framer for Eurex orders.

    Validation order: field domains, then duplicate ``ClOrdID``, then the minimum
    price change, then the Price Reasonability Check. An out-of-domain field makes
    the tick question meaningless, and an off-tick price makes the reasonability
    question meaningless.

    Sequence numbers: ``MsgSeqNum`` must increase by exactly one per request on an
    ETI session, and the Session Logon is number 1. ETI has no sequence number
    recovery -- a gap or a duplicate is rejected and the session disconnected, and
    every reconnection starts again at 1. The engine therefore advances the
    counter only when a header is actually produced, and ``reset_session()`` must
    be called after any reconnect and re-logon.

    Idempotency: the engine refuses to frame a second header for a ``ClOrdID`` it
    has already framed one for. A retry after an ambiguous timeout must reuse the
    original ``ClOrdID`` and go through the venue's order state, not through a
    fresh submission.

    This class is not thread-safe. One instance per ETI session, driven by that
    session's single sender thread, matches how ETI sequence numbers work.
    """

    #: The Session Logon consumes MsgSeqNum 1, so ordinary requests start at 2.
    FIRST_REQUEST_MSG_SEQ_NUM: Final[int] = 2

    def __init__(self, sender_sub_id: int,
                 default_template_id: int = TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG,
                 contract_specs: Optional[Dict[str, EurexContractSpec]] = None) -> None:
        """
        Args:
            sender_sub_id: the T7 User ID placed in ``SenderSubID``.
            default_template_id: template for order requests. Defaults to New Order
                Single or Multi Leg (10138); the previous default, New Order Single
                (10100), was removed from production with T7 Release 14.1.
            contract_specs: overrides the built-in FESX/FGBL conventions. Supply
                specs carrying a ``price_range_table`` built from RDI reference
                data to enable the Price Reasonability Check.
        """
        self.sender_sub_id = _require_int(sender_sub_id, "sender_sub_id",
                                          minimum=0, maximum=_UINT32_MAX)
        self.default_template_id = _require_int(default_template_id, "default_template_id",
                                                minimum=0, maximum=_UINT16_MAX)
        self.contract_specs: Dict[str, EurexContractSpec] = (
            dict(contract_specs) if contract_specs is not None else default_contract_specs())
        self._next_msg_seq_num = self.FIRST_REQUEST_MSG_SEQ_NUM
        self._framed_cl_ord_ids: Set[int] = set()

    @property
    def next_msg_seq_num(self) -> int:
        """The ``MsgSeqNum`` the next framed request will carry."""
        return self._next_msg_seq_num

    def reset_session(self) -> None:
        """Reset sequence state after a reconnect and Session Logon.

        ETI treats every connection as new: the Logon is MsgSeqNum 1 again. Framed
        ``ClOrdID`` s are deliberately kept, because order identity outlives the
        session and a reconnect is exactly when a duplicate submission is most
        likely.
        """
        self._next_msg_seq_num = self.FIRST_REQUEST_MSG_SEQ_NUM

    # -- minimum price change -------------------------------------------------

    def audit_eurex_tick_size(self, price: PriceInput, tick_step: PriceInput) -> bool:
        """True when ``price`` is a positive exact multiple of ``tick_step``.

        Exact decimal arithmetic. Float modulo is unusable here on two counts:
        ``-4851.0 % 1.0 == 0.0`` accepts a negative price, and ``133.33 % 0.01``
        is 0.00999999999999... rather than zero.
        """
        decimal_price = _to_decimal(price, "price")
        decimal_tick = _to_decimal(tick_step, "tick_step")
        if decimal_tick <= 0:
            raise EurexOrderValidationError(f"tick_step must be > 0, got {decimal_tick}")
        if decimal_price <= 0:
            return False
        return decimal_price % decimal_tick == 0

    # -- price reasonability --------------------------------------------------

    def audit_price_reasonability(self, *, side: Union[str, int], limit_price: PriceInput,
                                  spec: EurexContractSpec, book: EurexDepthBook,
                                  alternative_reference_price: Optional[PriceInput] = None,
                                  smallest_allowed_limit_price: Optional[PriceInput] = None,
                                  price_validity_check_type: int = PRICE_VALIDITY_CHECK_MANDATORY,
                                  instrument_state: str = INSTRUMENT_STATE_CONTINUOUS,
                                  fast_market: bool = False) -> PriceReasonabilityOutcome:
        """Pre-check a limit price against the Eurex Price Reasonability Check.

        The rejection condition, per T7 Functional Reference 6.2.1.1, is

            Buy  Limit Price > Reference Price + Price Range(Reference Price)
            Sell Limit Price < Reference Price - Price Range(Reference Price)

        so the check bites only on the aggressive side. The price range is always
        calculated from the reference price, never from the limit price.

        This is a *local mirror* of the venue rule, evaluated against your book.
        T7 evaluates it against its own, which is ahead of yours by at least one
        network hop; a price sitting on the boundary can still be rejected.
        """
        wire_side = normalise_side(side)
        price = _to_decimal(limit_price, "limit_price")

        if price_validity_check_type == PRICE_VALIDITY_CHECK_NONE:
            return PriceReasonabilityOutcome(
                passed=True, procedure=PRC_PROCEDURE_NOT_PERFORMED,
                reason="PriceValidityCheckType is 0 (None): no check requested.")

        if instrument_state != INSTRUMENT_STATE_CONTINUOUS:
            return PriceReasonabilityOutcome(
                passed=True, procedure=PRC_PROCEDURE_NOT_PERFORMED,
                reason=(f"Instrument state is {instrument_state!r}; T7 performs the "
                        f"Price Reasonability Check exclusively in Continuous."))

        if spec.price_range_table is None:
            return PriceReasonabilityOutcome(
                passed=True, procedure=PRC_PROCEDURE_NOT_PERFORMED,
                reason=(f"No price range table for {spec.symbol}. Load PriceRangeRules "
                        f"from the T7 Reference Data Interface; the band is not a "
                        f"constant and is not guessed here."))

        if book.is_crossed:
            # T7's own book is never crossed, so a crossed local copy means the
            # decoder has lost state -- typically an unrecovered EMDI gap. Answering
            # from it would be worse than not answering: it would report a verdict
            # the venue never reaches.
            return PriceReasonabilityOutcome(
                passed=True, procedure=PRC_PROCEDURE_NOT_PERFORMED,
                reason=(f"Local book is crossed (best bid {book.best_bid_price} > best "
                        f"ask {book.best_ask_price}); it cannot mirror T7's book. "
                        f"Rebuild from the snapshot feed before relying on this check."))

        alternative = (None if alternative_reference_price is None else
                       _to_decimal(alternative_reference_price, "alternative_reference_price"))
        smallest_allowed = (None if smallest_allowed_limit_price is None else
                            _to_decimal(smallest_allowed_limit_price,
                                        "smallest_allowed_limit_price"))

        # The standard procedure's applicability test compares the spread against
        # "the price range being applied", which is itself derived from the
        # reference price. It is evaluated here against this side's candidate
        # reference -- the opposite-side best price -- falling back to the
        # alternative reference price when that candidate is missing.
        candidate = book.best_ask_price if wire_side == SIDE_BUY else book.best_bid_price
        if candidate is None and wire_side == SIDE_SELL:
            candidate = smallest_allowed
        probe = candidate if candidate is not None else alternative
        if probe is None:
            return self._prc_no_reference(price_validity_check_type)
        price_range = spec.price_range_table.price_range(probe, fast_market=fast_market)

        reference, procedure = resolve_prc_reference_price(
            wire_side, book, price_range,
            alternative_reference_price=alternative,
            smallest_allowed_limit_price=smallest_allowed)
        if reference is None:
            return self._prc_no_reference(price_validity_check_type)

        if reference != probe:
            price_range = spec.price_range_table.price_range(reference, fast_market=fast_market)

        if wire_side == SIDE_BUY:
            bound = reference + price_range
            breached = price > bound
            direction = "above"
        else:
            bound = reference - price_range
            breached = price < bound
            direction = "below"

        if breached:
            return PriceReasonabilityOutcome(
                passed=False, procedure=procedure, reference_price=reference,
                price_range=price_range,
                reason=(f"Limit price {price} is {direction} the reasonability bound "
                        f"{bound} (reference {reference} from the {procedure} procedure, "
                        f"price range {price_range})."))
        return PriceReasonabilityOutcome(
            passed=True, procedure=procedure, reference_price=reference,
            price_range=price_range)

    @staticmethod
    def _prc_no_reference(price_validity_check_type: int) -> PriceReasonabilityOutcome:
        """Outcome when no reference price can be determined.

        With PriceValidityCheckType 'Optional' T7 accepts the order unchecked;
        with 'Mandatory' it rejects it. That is the only difference between them.
        """
        mandatory = price_validity_check_type == PRICE_VALIDITY_CHECK_MANDATORY
        verdict = "rejects the order." if mandatory else "accepts it unchecked."
        name = _PRICE_VALIDITY_CHECK_NAMES.get(price_validity_check_type, "unknown")
        return PriceReasonabilityOutcome(
            passed=not mandatory, procedure=PRC_PROCEDURE_NOT_PERFORMED,
            reason=("No reference price available: neither the standard nor the "
                    "non-standard procedure applies. PriceValidityCheckType "
                    f"{price_validity_check_type} ({name}) {verdict}"))

    # -- header framing -------------------------------------------------------

    def format_t7_eti_header(self, body_len: int,
                             template_id: Optional[int] = None) -> T7EtiRequestHeader:
        """Advance ``MsgSeqNum`` and build the ETI request header.

        Args:
            body_len: total message length in bytes **including** the BodyLen field
                itself. ``new_order_body_len()`` computes it for template 10138 in
                Release 14.0. Passing a body-only length produces a message the
                gateway cannot frame.
            template_id: overrides ``default_template_id``.
        """
        resolved_template = self.default_template_id if template_id is None else _require_int(
            template_id, "template_id", minimum=0, maximum=_UINT16_MAX)
        _require_int(body_len, "body_len", minimum=ETI_REQUEST_HEADER_LEN, maximum=_UINT32_MAX)
        if self._next_msg_seq_num > _UINT32_MAX:
            raise EurexOrderValidationError(
                "MsgSeqNum would exceed the 32-bit wire field; the session must be "
                "re-established rather than wrapping the sequence number")

        header = T7EtiRequestHeader(
            body_len=body_len,
            template_id=resolved_template,
            msg_seq_num=self._next_msg_seq_num,
            sender_sub_id=self.sender_sub_id,
        )
        self._next_msg_seq_num += 1
        return header

    # -- order validation -----------------------------------------------------

    def process_eurex_order(self, req: EurexOrderRequest,
                            book: Optional[EurexDepthBook] = None,
                            *, body_len: Optional[int] = None,
                            template_id: Optional[int] = None,
                            alternative_reference_price: Optional[PriceInput] = None,
                            smallest_allowed_limit_price: Optional[PriceInput] = None,
                            instrument_state: str = INSTRUMENT_STATE_CONTINUOUS,
                            fast_market: bool = False) -> EurexOrderValidationReport:
        """Validate an order and, if it passes, frame its ETI request header.

        ``book`` is the current T7 EMDI depth state for the instrument. Without it
        the Price Reasonability Check is skipped and the report says so; the
        minimum price change and field domain checks still run.
        """
        if not isinstance(req, EurexOrderRequest):
            raise EurexOrderValidationError(
                f"req must be a EurexOrderRequest, got {type(req).__name__}")

        warnings: List[str] = []
        resolved_template = self.default_template_id if template_id is None else template_id
        replacement = DECOMMISSIONED_TEMPLATE_REPLACEMENTS.get(resolved_template)
        if replacement is not None:
            warning = (f"Template {resolved_template} was removed from production with T7 "
                       f"Release 14.1 on {DECOMMISSIONED_TEMPLATES_REMOVED_ON}; use "
                       f"{replacement} instead.")
            warnings.append(warning)
            logger.warning(warning)

        contract_label = f"{req.contract_symbol} {req.expiry}"

        def _reject(status: str, message: str, *,
                    tick: Optional[Decimal] = None,
                    prc: Optional[PriceReasonabilityOutcome] = None,
                    ) -> EurexOrderValidationReport:
            logger.error("EUREX ORDER REJECTED [%s]: %s", req.cl_ord_id, message)
            return EurexOrderValidationReport(
                cl_ord_id=req.cl_ord_id, contract=contract_label, status=status,
                ready_to_send=False, eti_header=None, rejection_reason=message,
                required_tick_step=tick, price_reasonability=prc,
                warnings=warnings or None)

        spec = self.contract_specs.get(str(req.contract_symbol).upper())
        if spec is None:
            return _reject(
                STATUS_UNKNOWN_CONTRACT,
                f"Eurex symbol {req.contract_symbol!r} is not in the contract "
                f"specification registry {sorted(self.contract_specs)}.")
        contract_label = f"{spec.symbol} {req.expiry}"

        # 1. Field domains, before anything that depends on them.
        try:
            _require_int(req.cl_ord_id, "cl_ord_id", minimum=0)
            _require_int(req.security_id, "security_id")
            _require_int(req.market_segment_id, "market_segment_id")
            side_value = normalise_side(req.side)
            _require_int(req.order_qty, "order_qty", minimum=1)
            if req.trading_capacity not in _TRADING_CAPACITY_NAMES:
                raise EurexOrderValidationError(
                    f"trading_capacity must be one of {sorted(_TRADING_CAPACITY_NAMES)} "
                    f"(TradingCapacity tag 1815, derivatives domain), got "
                    f"{req.trading_capacity!r}")
            if req.ord_type not in _ORD_TYPE_NAMES:
                raise EurexOrderValidationError(
                    f"ord_type must be one of {sorted(_ORD_TYPE_NAMES)} (OrdType tag 40), "
                    f"got {req.ord_type!r}")
            if req.ord_type == ORD_TYPE_MARKET:
                raise EurexOrderValidationError(
                    "ord_type Market (1) carries no limit price, so neither the minimum "
                    "price change nor the Price Reasonability Check applies. Market "
                    "orders are bounded by the Market Order Matching Range instead.")
            if req.time_in_force not in _TIME_IN_FORCE_NAMES:
                raise EurexOrderValidationError(
                    f"time_in_force must be one of {sorted(_TIME_IN_FORCE_NAMES)} "
                    f"(TimeInForce tag 59), got {req.time_in_force!r}")
            if req.price_validity_check_type not in _PRICE_VALIDITY_CHECK_NAMES:
                raise EurexOrderValidationError(
                    f"price_validity_check_type must be one of "
                    f"{sorted(_PRICE_VALIDITY_CHECK_NAMES)} (tag 28710), got "
                    f"{req.price_validity_check_type!r}")
            price = _to_decimal(req.price, "price")
            if price <= 0:
                raise EurexOrderValidationError(f"price must be > 0, got {price}")
            price_int = price_to_eti_int(price)
            qty_int = qty_to_eti_int(req.order_qty)
        except EurexOrderValidationError as exc:
            return _reject(STATUS_INVALID_ORDER_FIELD, str(exc))

        # 2. Idempotency: a ClOrdID is framed at most once per engine.
        if req.cl_ord_id in self._framed_cl_ord_ids:
            return _reject(
                STATUS_DUPLICATE_CL_ORD_ID,
                f"ClOrdID {req.cl_ord_id} already has a framed request. Resolve the "
                f"original order's state through the venue before submitting again; "
                f"re-sending under a fresh ClOrdID after an ambiguous timeout is how "
                f"one intent becomes two positions.")

        # 3. Minimum price change.
        if not self.audit_eurex_tick_size(price, spec.tick_step):
            return _reject(
                STATUS_INVALID_TICK_SIZE,
                f"Price {price} is off-tick for {spec.symbol}: the on-book minimum "
                f"price change is {spec.tick_step} {spec.point_description}.",
                tick=spec.tick_step)

        # 4. Price Reasonability Check, when a book is available.
        prc: Optional[PriceReasonabilityOutcome] = None
        if book is not None:
            prc = self.audit_price_reasonability(
                side=side_value, limit_price=price, spec=spec, book=book,
                alternative_reference_price=alternative_reference_price,
                smallest_allowed_limit_price=smallest_allowed_limit_price,
                price_validity_check_type=req.price_validity_check_type,
                instrument_state=instrument_state, fast_market=fast_market)
            if not prc.passed:
                return _reject(STATUS_PRICE_REASONABILITY_BREACH,
                               prc.reason or "Price Reasonability Check failed.",
                               tick=spec.tick_step, prc=prc)
            if prc.procedure == PRC_PROCEDURE_NOT_PERFORMED and prc.reason:
                warnings.append(prc.reason)

        # 5. Frame the header. Only now is a sequence number consumed.
        resolved_body_len = new_order_body_len() if body_len is None else body_len
        header = self.format_t7_eti_header(body_len=resolved_body_len, template_id=template_id)
        self._framed_cl_ord_ids.add(req.cl_ord_id)

        contract_value = req.order_qty * price * spec.multiplier_eur
        logger.info(
            "EUREX ORDER VALIDATED [%s]: side=%d qty=%d %s @ %s (tick=%s, value=%s %s, "
            "capacity=%s, template=%d, seq=%d). Not yet sent.",
            req.cl_ord_id, side_value, req.order_qty, contract_label, price,
            spec.tick_step, spec.currency, contract_value,
            _TRADING_CAPACITY_NAMES[req.trading_capacity], header.template_id,
            header.msg_seq_num)

        return EurexOrderValidationReport(
            cl_ord_id=req.cl_ord_id,
            contract=contract_label,
            status=STATUS_OK,
            ready_to_send=True,
            eti_header=header,
            rejection_reason=None,
            contract_value_eur=contract_value,
            required_tick_step=spec.tick_step,
            price_eti_int=price_int,
            order_qty_eti_int=qty_int,
            side_wire_value=side_value,
            price_reasonability=prc,
            warnings=warnings or None,
        )

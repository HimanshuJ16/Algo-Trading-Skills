"""SGX pre-trade order validation: contract specs, tick increments, minimum bid sizes.

This module validates an order *before* it is handed to a Singapore Exchange session.
It is a contract-specification validator, not a gateway: it opens no sockets, speaks
neither Titan OUCH nor Titan FIX order entry, holds no session, assigns no ClOrdID and
tracks no order state.

Scope and sourcing (verified 2026-08-28 against exchange and SGX member-broker sources;
see ``references/standards.md`` for the per-figure citations):

* **SGX runs two trading engines, not one.** Derivatives trade on **Titan-DT**
  (Nasdaq Genium INET; OUCH order entry, ITCH/GLIMPSE market data, FIX order entry).
  Securities trade on **Reach-ST**, which SGX RegCo will replace with **Iris-ST** in
  H2 2027. An equity order does not go to Titan, and the two markets do not share a
  tick regime - so ``SGXMarket`` is part of every validation here.

* **Tick sizes are per contract and they move.** The SGX FTSE China A50 (``CN``)
  minimum price fluctuation was cut from 2.5 index points to **1 index point** on
  5 October 2020, and SGX's own 2018-vintage contract-specification PDFs still on
  api2.sgx.com show the superseded 2.5. The SGX Mini Nikkei (``NS``) went from
  JPY 100 x index / 1-point ticks to JPY 10 x index / 2.5-point ticks on 22 June 2026
  **under the same product code**. Every spec here therefore carries ``source`` and
  ``verified_on``, and a stale table is treated as a defect, not a rounding detail.

* **MSCI Taiwan futures are no longer an SGX product.** The MSCI licence moved, and
  SGX listed **SGX FTSE Taiwan Index Futures (``TWN``, US$40 per index point, 0.25
  index point outright tick)** on 20 July 2020 in its place. The retired MSCI contract
  (``TW``, US$100 per point, 0.1 point tick) is not in this table; ``TW`` resolves to
  nothing and raises rather than validating an order against a delisted contract.

* **One contract has several minimum price fluctuations.** SGX publishes separate
  increments for outright, strategy/calendar-spread, Negotiated Large Trade and
  Trade-At-Index-Close prices. Nikkei 225 (``NK``) is 5 index points outright but
  1 point on a calendar spread and 0.25 on a T@IC trade; validating a spread leg
  against the outright tick rejects legal prices. Where an increment is not published
  in a source that could be verified, it is absent and
  :class:`TickSizeUnavailableError` is raised - the table never interpolates one.

* **SGX-ST minimum bid size is price-tiered, not a flat cent.** An ordinary share
  below S$0.20 bids in S$0.001 and from S$0.20 in S$0.005; only at S$1.00 and above
  is it S$0.01. Structured warrants keep S$0.005 all the way to S$1.995. ETFs/ETNs
  bid in S$0.01 *or* S$0.001 as SGX-ST determines per fund, so that class raises
  instead of guessing.

* **Decimal arithmetic, no tolerance.** ``100.03 % 0.05`` is ``0.0299999999999956`` in
  binary float and ``1.005 % 0.005`` is ``0.004999999999999873``. Any tolerance that
  absorbs those also accepts prices the matching engine rejects, and a tolerance
  expressed in price units silently loosens as the tick coarsens.

Deliberately NOT implemented: session/transport (use a real Titan FIX or OUCH client),
board-lot and minimum-quantity sizing (see ``minimum-fill-size-and-lot-rounding-logic``),
the SGX-ST Forced Order Range / circuit breaker band and Clearing Member pre-execution
limits (see ``mas-singapore-algo-trading-guidelines``), and daily price limits.

Sources:
  SGX Titan-DT connectivity (OUCH order entry, ITCH/GLIMPSE market data)
    https://api2.sgx.com/sites/default/files/2018-09/Titan%20ITCH%20&%20GLIMPSE%20Protocol%20Specifications.pdf
  SGX RegCo consultation on the Iris-ST trading engine replacing Reach-ST (H2 2027)
    https://www.rajahtannasia.com/viewpoints/sgx-regco-consults-on-details-of-new-trading-engine-iris-st-for-singapore-stock-market-with-new-and-enhanced-trading-functionalities/
  SGX FTSE China A50 Index Futures contract specification (1 index point tick)
    https://www.kgieworld.sg/futures/sgx-FTSE-China-A50-index-futures-contract-specifications
  SGX Nikkei 225 Index Futures and Options contract specification (5 / 1 / 0.25)
    https://www.kgieworld.sg/futures/sgx-nikkei-225-index-futures-and-options-contract-specifications
  SGX FTSE Taiwan Index Futures and Options suite (TWN, US$40, 0.25 / 0.01 / 0.05)
    https://www.sgx.com/derivatives/products/twnfc
  SGX Iron Ore CFR China (62% Fe Fines) Index Futures (FEF, 100 t, US$0.01/t)
    https://api2.sgx.com/sites/default/files/2018-12/SGX%20TSI%20Iron%20Ore%20CFR%20China%20(62%25%20FE%20Fines)%20Index%20Futures.pdf
  SGX-ST minimum bid size (Regulatory Notice 8.5.2), as published by SGX members
    https://www.poems.com.sg/faq/trading/general/what-is-the-minimum-bid-size-for-trading/
    https://www.iocbc.com/help-and-support/market-information/singapore
  SGX board lot reduction from 5 October 2026 (news release, 1 July 2026)
    https://links.sgx.com/FileOpen/20260701%20SGX%20custody%20structure%20enhancements%20to%20take%20effect%20from%20July%20board%20lot%20reduction%20in%20October.ashx?App=Announcement&FileID=894992
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType
from typing import Dict, Mapping, Optional, Tuple, Union

logger = logging.getLogger(__name__)

#: Any numeric form accepted for a price. ``str``, ``int`` and ``Decimal`` are exact;
#: a ``float`` is read through its shortest round-tripping repr, so ``0.005`` reads as
#: ``Decimal("0.005")`` and not as the binary value actually stored.
PriceInput = Union[Decimal, float, int, str]

STATUS_VALIDATED = "ORDER_VALIDATED"
STATUS_INVALID_PRICE = "INVALID_PRICE"
STATUS_MISSING_LIMIT_PRICE = "MISSING_LIMIT_PRICE"
STATUS_INVALID_TICK = "INVALID_TICK_SIZE"
STATUS_INVALID_QUANTITY = "INVALID_QUANTITY"

#: Order of precedence when an order breaches more than one rule. ``status`` reports
#: the first match; ``violations`` reports every one of them.
_STATUS_PRECEDENCE: Tuple[str, ...] = (
    STATUS_INVALID_PRICE,
    STATUS_MISSING_LIMIT_PRICE,
    STATUS_INVALID_TICK,
    STATUS_INVALID_QUANTITY,
)


class SGXOrderError(ValueError):
    """Raised for an order that cannot be evaluated against SGX rules at all."""


class UnknownContractError(SGXOrderError):
    """Raised when a product code is not in the contract table supplied.

    Not a soft failure: an unrecognised code has no tick size, so there is nothing to
    validate against. Silently passing the order through is how a delisted contract
    (``TW``) or a typo reaches the gateway unchecked.
    """


class TickSizeUnavailableError(SGXOrderError):
    """Raised when no minimum price fluctuation is published for this combination.

    Two cases reach here: a trade type whose increment SGX publishes per contract but
    which is not recorded for this one, and a security class whose bid size SGX-ST sets
    per instrument (ETFs and ETNs bid in S$0.01 or S$0.001 at SGX-ST's determination).
    Both are resolved from reference data, never by interpolating a neighbouring value.
    """


class PriceOutOfRangeError(SGXOrderError):
    """Raised when a price has no applicable band in the minimum bid size table."""


class SGXMarket(str, Enum):
    """Which SGX trading engine an order is destined for.

    The two engines are separate systems with separate connectivity, separate
    membership and separate tick regimes.
    """

    #: SGX derivatives - Titan-DT (Nasdaq Genium INET; OUCH / FIX order entry).
    DERIVATIVES_TITAN_DT = "DERIVATIVES_TITAN_DT"
    #: SGX securities - Reach-ST, to be replaced by Iris-ST in H2 2027.
    SECURITIES_REACH_ST = "SECURITIES_REACH_ST"


class SGXTradeType(str, Enum):
    """Which published minimum price fluctuation applies to a derivatives price.

    SGX publishes these separately per contract. They are not interchangeable: the
    Nikkei 225 outright increment is 20x its Trade-At-Index-Close increment.
    """

    OUTRIGHT = "OUTRIGHT"
    #: Strategy / calendar spread prices (the spread differential, not a leg price).
    CALENDAR_SPREAD = "CALENDAR_SPREAD"
    #: Trade-At-Index-Close, entered under the T@IC ticker (``NKTI``, ``TWNTI``).
    TRADE_AT_INDEX_CLOSE = "TRADE_AT_INDEX_CLOSE"
    #: Negotiated Large Trade, reported off-book under SGX's NLT facility.
    NEGOTIATED_LARGE_TRADE = "NEGOTIATED_LARGE_TRADE"


class SGXSecurityClass(str, Enum):
    """SGX-ST minimum bid size classes (Regulatory Notice 8.5.2), SGD-denominated."""

    #: Stocks excluding preference shares, REITs, business trusts, company warrants.
    ORDINARY = "ORDINARY"
    #: Structured warrants - S$0.005 runs to S$1.995, not to S$0.995.
    STRUCTURED_WARRANT = "STRUCTURED_WARRANT"
    #: Bonds, debentures, loan stocks and preference shares - flat S$0.001.
    DEBT = "DEBT"
    #: ETFs and ETNs - S$0.01 or S$0.001 as determined by SGX-ST, per instrument.
    ETF_ETN = "ETF_ETN"


class SGXOrderType(str, Enum):
    """How the validator reads an order's price fields.

    This is a price-shape classification, not SGX's order-type catalogue, and it is
    not a time-in-force. IOC, FOK and day are TIF qualifiers that ride alongside an
    order type; the old ``SGXOrderType.IOC`` member conflated the two and is gone.
    """

    #: Requires a limit price, which must be on tick.
    LIMIT = "LIMIT"
    #: Carries no price; nothing to tick-check.
    MARKET = "MARKET"
    #: Requires both a stop trigger price and a limit price; both must be on tick.
    STOP_LIMIT = "STOP_LIMIT"


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


def to_decimal(value: PriceInput) -> Decimal:
    """Convert a price input to ``Decimal`` without importing binary float error.

    A ``float`` is routed through ``repr`` so ``0.005`` becomes ``Decimal("0.005")``
    rather than ``Decimal("0.005000000000000000104083408558...")``. Prefer ``str`` or
    ``Decimal`` at the call site; exchange prices should not round-trip through
    binary floats at all.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a price.
        raise SGXOrderError(f"Price must be numeric, got {value!r}.")
    if isinstance(value, Decimal):
        converted = value
    elif isinstance(value, float):
        converted = Decimal(repr(value))
    else:
        try:
            converted = Decimal(str(value).strip())
        except (InvalidOperation, ValueError, ArithmeticError) as exc:
            raise SGXOrderError(f"Price {value!r} is not a valid decimal.") from exc
    if not converted.is_finite():
        # NaN reaches here from a missing quote and would otherwise propagate: it
        # compares False against every bound, so it is neither on tick nor off tick.
        raise SGXOrderError(f"Price {value!r} is not a finite decimal.")
    return converted


def is_on_tick(price: PriceInput, tick_size: PriceInput) -> bool:
    """Return whether ``price`` is an exact multiple of ``tick_size``.

    Exact decimal remainder, no tolerance. ``tick_size`` must be positive: a zero tick
    would divide by zero, and Python's ``%`` returns ``0`` for a negative modulus
    (``12500 % -2.5 == 0``), which would make every price look on tick.
    """
    tick = to_decimal(tick_size)
    if tick <= 0:
        raise SGXOrderError(f"Tick size must be positive, got {tick}.")
    return to_decimal(price) % tick == 0


@dataclass(frozen=True)
class SGXContractSpec:
    """A single SGX derivatives contract's published trading specification.

    ``tick_sizes`` holds only the increments actually published for this contract.
    A missing key means "not verified", and :meth:`tick_size_for` raises rather than
    substituting the outright increment.
    """

    product_code: str
    name: str
    market: SGXMarket
    currency: str
    #: Contract size per unit of price: US$1 per index point, JPY 500 per index point,
    #: 100 metric tonnes per US$1/tonne of price.
    contract_multiplier: Decimal
    multiplier_unit: str
    tick_sizes: Mapping[SGXTradeType, Decimal]
    source: str
    #: ISO date the figures above were last reconciled against ``source``.
    verified_on: str

    def __post_init__(self) -> None:
        # The specs are module-level shared state. Freezing the dataclass does not
        # freeze the mapping inside it, and a table that can be edited at runtime is a
        # tick size that can change between two orders in the same session.
        object.__setattr__(self, "tick_sizes", MappingProxyType(dict(self.tick_sizes)))

    def tick_size_for(self, trade_type: SGXTradeType = SGXTradeType.OUTRIGHT) -> Decimal:
        """Return the published minimum price fluctuation for ``trade_type``."""
        try:
            return self.tick_sizes[trade_type]
        except KeyError:
            raise TickSizeUnavailableError(
                f"No published minimum price fluctuation recorded for {self.product_code} "
                f"{trade_type.value}. Resolve it from your Titan-DT reference data or the "
                f"current SGX contract specification; do not reuse the outright tick."
            ) from None

    def tick_value(self, trade_type: SGXTradeType = SGXTradeType.OUTRIGHT) -> Decimal:
        """Cash value of one tick, in :attr:`currency`."""
        return self.tick_size_for(trade_type) * self.contract_multiplier

    def notional(self, price: PriceInput) -> Decimal:
        """Contract notional at ``price``, in :attr:`currency`.

        Index futures: multiplier x index level. Iron ore: tonnes x US$/tonne. This is
        a contract value, not a margin requirement - SGX margins are set by SGX-DC and
        are not derivable from the contract size.
        """
        return to_decimal(price) * self.contract_multiplier


_KGI_A50 = "SGX FTSE China A50 Index Futures contract specification (SGX member broker)"
_KGI_NIKKEI = "SGX Nikkei 225 Index Futures and Options contract specification (SGX member broker)"
_SGX_TWN = "SGX FTSE Taiwan Index Futures and Options suite, sgx.com/derivatives/products/twnfc"
_SGX_FEF = "SGX Iron Ore CFR China (62% Fe Fines) Index Futures contract specification"

#: Titan-DT contracts whose figures were verified on the date each spec records.
#: This is a starting table, not a security master: SGX lists far more contracts, and
#: a code's multiplier and tick can change under it (``NS``, 22 June 2026). Load the
#: full set from your own reference data and pass it as ``contracts=``.
SGX_DERIVATIVES_CONTRACTS: Dict[str, SGXContractSpec] = {
    "CN": SGXContractSpec(
        product_code="CN",
        name="SGX FTSE China A50 Index Futures",
        market=SGXMarket.DERIVATIVES_TITAN_DT,
        currency="USD",
        contract_multiplier=Decimal("1"),
        multiplier_unit="USD per index point",
        # Cut from 2.5 index points to 1 index point on 5 October 2020. SGX's archived
        # 2018 specification PDF still shows 2.5 and must not be used.
        tick_sizes={SGXTradeType.OUTRIGHT: Decimal("1")},
        source=_KGI_A50,
        verified_on="2026-08-28",
    ),
    "NK": SGXContractSpec(
        product_code="NK",
        name="SGX Nikkei 225 Index Futures",
        market=SGXMarket.DERIVATIVES_TITAN_DT,
        currency="JPY",
        contract_multiplier=Decimal("500"),
        multiplier_unit="JPY per index point",
        tick_sizes={
            SGXTradeType.OUTRIGHT: Decimal("5"),          # JPY 2,500
            SGXTradeType.CALENDAR_SPREAD: Decimal("1"),   # JPY 500
            SGXTradeType.TRADE_AT_INDEX_CLOSE: Decimal("0.25"),  # JPY 125, ticker NKTI
        },
        source=_KGI_NIKKEI,
        verified_on="2026-08-28",
    ),
    "TWN": SGXContractSpec(
        product_code="TWN",
        name="SGX FTSE Taiwan Index Futures",
        market=SGXMarket.DERIVATIVES_TITAN_DT,
        currency="USD",
        contract_multiplier=Decimal("40"),
        multiplier_unit="USD per index point",
        tick_sizes={
            SGXTradeType.OUTRIGHT: Decimal("0.25"),               # US$10
            SGXTradeType.CALENDAR_SPREAD: Decimal("0.25"),        # US$10
            SGXTradeType.NEGOTIATED_LARGE_TRADE: Decimal("0.01"), # US$0.40
            SGXTradeType.TRADE_AT_INDEX_CLOSE: Decimal("0.05"),   # US$2, ticker TWNTI
        },
        source=_SGX_TWN,
        verified_on="2026-08-28",
    ),
    "FEF": SGXContractSpec(
        product_code="FEF",
        name="SGX Iron Ore CFR China (62% Fe Fines) Index Futures",
        market=SGXMarket.DERIVATIVES_TITAN_DT,
        currency="USD",
        contract_multiplier=Decimal("100"),
        multiplier_unit="metric tonnes per contract (price quoted in USD per tonne)",
        tick_sizes={SGXTradeType.OUTRIGHT: Decimal("0.01")},  # US$1 per contract
        source=_SGX_FEF,
        verified_on="2026-08-28",
    ),
}

#: SGX-ST minimum bid size bands, SGD-denominated (Regulatory Notice 8.5.2), as
#: ``(inclusive lower bound, bid size)`` in descending order. The published table's
#: upper edges (``0.995``, ``1.995``) are the last on-tick price in the band, not an
#: exclusive bound - a price of ``0.9975`` is off tick, and is rejected as such rather
#: than being pushed into the band above.
_SGX_ST_BID_SIZE_BANDS: Dict[SGXSecurityClass, Tuple[Tuple[Decimal, Decimal], ...]] = {
    SGXSecurityClass.ORDINARY: (
        (Decimal("1.00"), Decimal("0.01")),
        (Decimal("0.20"), Decimal("0.005")),
        (Decimal("0"), Decimal("0.001")),
    ),
    SGXSecurityClass.STRUCTURED_WARRANT: (
        (Decimal("2.00"), Decimal("0.01")),
        (Decimal("0.20"), Decimal("0.005")),
        (Decimal("0"), Decimal("0.001")),
    ),
    SGXSecurityClass.DEBT: (
        (Decimal("0"), Decimal("0.001")),
    ),
}


def get_sgx_st_minimum_bid_size(
    price: PriceInput,
    security_class: SGXSecurityClass = SGXSecurityClass.ORDINARY,
) -> Decimal:
    """Return the SGX-ST minimum bid size for an SGD-denominated security.

    The scale is price-tiered per security class. An ordinary share at S$0.95 bids in
    S$0.005 and the same share at S$1.00 bids in S$0.01, so the bid size has to be
    re-derived from the *order's* price, not cached per symbol.

    Raises:
        TickSizeUnavailableError: for ETFs and ETNs, whose bid size SGX-ST sets per
            instrument (S$0.01 or S$0.001) and which therefore cannot be derived.
        PriceOutOfRangeError: for a price of zero or below, which has no band.
    """
    px = to_decimal(price)
    if px <= 0:
        raise PriceOutOfRangeError(
            f"Price {px} has no SGX-ST minimum bid size band; prices must be positive."
        )
    try:
        bands = _SGX_ST_BID_SIZE_BANDS[security_class]
    except KeyError:
        raise TickSizeUnavailableError(
            f"SGX-ST sets the minimum bid size for {security_class.value} per instrument "
            f"(S$0.01 or S$0.001 for ETFs and ETNs). Read it from reference data."
        ) from None
    for lower_bound, bid_size in bands:
        if px >= lower_bound:
            return bid_size
    raise PriceOutOfRangeError(f"No SGX-ST minimum bid size band matched price {px}.")


@dataclass(frozen=True)
class SGXOrderValidation:
    """Outcome of a pre-trade check. ``status`` routes; ``violations`` explains.

    An order can breach several rules at once - an off-tick price *and* a fractional
    quantity. ``status`` carries the highest-precedence breach so routing logic can
    branch on one value; ``violations`` carries all of them so a fix-and-resubmit loop
    does not burn one round trip per rule.
    """

    market: SGXMarket
    instrument: str
    status: str
    violations: Tuple[str, ...] = ()
    #: The increment the price was checked against: the contract's published minimum
    #: price fluctuation for the trade type, or the SGX-ST minimum bid size for the
    #: limit price's band.
    tick_size: Optional[Decimal] = None
    #: Gross order notional in :attr:`currency` - contract multiplier x price x
    #: contracts for derivatives, price x units for securities. ``None`` unless both
    #: price and quantity are valid, and never a margin requirement.
    order_notional: Optional[Decimal] = None
    currency: Optional[str] = None

    @property
    def is_valid(self) -> bool:
        return self.status == STATUS_VALIDATED


def _resolve_status(violations: Tuple[str, ...]) -> str:
    for candidate in _STATUS_PRECEDENCE:
        if candidate in violations:
            return candidate
    return STATUS_VALIDATED


def _check_prices(
    order_type: SGXOrderType,
    price: Optional[PriceInput],
    stop_price: Optional[PriceInput],
    tick_size: Decimal,
) -> Tuple[str, ...]:
    """Validate whichever price fields ``order_type`` requires, against ``tick_size``."""
    required: Tuple[Optional[PriceInput], ...]
    if order_type is SGXOrderType.LIMIT:
        required = (price,)
    elif order_type is SGXOrderType.STOP_LIMIT:
        required = (price, stop_price)
    else:  # MARKET carries no price to check.
        return ()

    violations: Tuple[str, ...] = ()
    for value in required:
        if value is None:
            violations += (STATUS_MISSING_LIMIT_PRICE,)
            continue
        decimal_price = to_decimal(value)
        if decimal_price <= 0:
            violations += (STATUS_INVALID_PRICE,)
        elif not is_on_tick(decimal_price, tick_size):
            violations += (STATUS_INVALID_TICK,)
    # Preserve precedence order and drop duplicates (both legs off tick is one rule).
    return tuple(v for v in _STATUS_PRECEDENCE if v in violations)


def _check_quantity(quantity: object, unit: str) -> Tuple[str, ...]:
    """Reject a quantity that is not a positive whole number of ``unit``."""
    if isinstance(quantity, bool) or not isinstance(quantity, (int, Decimal, float)):
        return (STATUS_INVALID_QUANTITY,)
    try:
        qty = Decimal(quantity) if isinstance(quantity, int) else to_decimal(quantity)
    except SGXOrderError:  # NaN or infinity reaching a quantity field.
        return (STATUS_INVALID_QUANTITY,)
    if qty <= 0 or qty != qty.to_integral_value():
        logger.debug("Rejected non-integral or non-positive quantity %s (%s).", quantity, unit)
        return (STATUS_INVALID_QUANTITY,)
    return ()


def validate_derivatives_order(
    product_code: str,
    side: OrderSide,
    quantity: int,
    order_type: SGXOrderType = SGXOrderType.LIMIT,
    price: Optional[PriceInput] = None,
    stop_price: Optional[PriceInput] = None,
    trade_type: SGXTradeType = SGXTradeType.OUTRIGHT,
    contracts: Optional[Mapping[str, SGXContractSpec]] = None,
) -> SGXOrderValidation:
    """Validate a Titan-DT derivatives order against its published contract spec.

    Args:
        product_code: SGX product code, e.g. ``"CN"``, ``"NK"``, ``"TWN"``, ``"FEF"``.
            Matched case-insensitively after stripping surrounding whitespace.
        side: :class:`OrderSide`. Anything else raises - a free-text side is how an
            unmapped vendor value becomes a wrong-way order.
        quantity: whole number of contracts, greater than zero.
        order_type: which price fields are required and tick-checked.
        price: limit price, in index points (index futures) or US$/tonne (``FEF``).
        stop_price: stop trigger price; required for ``STOP_LIMIT`` and checked on the
            same increment as the limit price.
        trade_type: which published minimum price fluctuation applies. A calendar
            spread price is *not* checked on the outright increment.
        contracts: contract table to resolve against. Defaults to
            :data:`SGX_DERIVATIVES_CONTRACTS`; pass your own security master in
            production so specification changes arrive with your reference data.

    Raises:
        UnknownContractError: the code is not in ``contracts``.
        TickSizeUnavailableError: no increment is published for this ``trade_type``.
        SGXOrderError: ``side`` is not an :class:`OrderSide`, or a price is not numeric.
    """
    if not isinstance(side, OrderSide):
        raise SGXOrderError(f"side must be an OrderSide, got {side!r}.")
    table = SGX_DERIVATIVES_CONTRACTS if contracts is None else contracts
    code = str(product_code).strip().upper()
    try:
        spec = table[code]
    except KeyError:
        raise UnknownContractError(
            f"No SGX contract specification for product code {code!r}. Note that SGX "
            f"MSCI Taiwan futures ('TW') were replaced by SGX FTSE Taiwan futures "
            f"('TWN') on 20 July 2020."
        ) from None

    tick_size = spec.tick_size_for(trade_type)
    violations = _check_prices(order_type, price, stop_price, tick_size)
    violations += _check_quantity(quantity, "contracts")

    order_notional: Optional[Decimal] = None
    # Only from a price this call actually validated: a price passed alongside a
    # MARKET order was never tick-checked, and reporting a notional off it would
    # dress an unchecked number as a validated one.
    if price is not None and not violations and order_type is not SGXOrderType.MARKET:
        order_notional = spec.notional(price) * to_decimal(quantity)

    result = SGXOrderValidation(
        market=spec.market,
        instrument=spec.product_code,
        status=_resolve_status(violations),
        violations=violations,
        tick_size=tick_size,
        order_notional=order_notional,
        currency=spec.currency,
    )
    logger.info(
        "SGX %s %s validation: %s (%s %s, tick %s, trade type %s).",
        spec.product_code, side.value, result.status, quantity,
        spec.currency, tick_size, trade_type.value,
    )
    return result


def validate_securities_order(
    counter: str,
    side: OrderSide,
    quantity: int,
    price: Optional[PriceInput] = None,
    order_type: SGXOrderType = SGXOrderType.LIMIT,
    security_class: SGXSecurityClass = SGXSecurityClass.ORDINARY,
    currency: str = "SGD",
    stop_price: Optional[PriceInput] = None,
) -> SGXOrderValidation:
    """Validate an SGX-ST securities order's price against the minimum bid size table.

    Board lot and minimum quantity are **not** checked here: SGX-ST board lots become
    price-tiered on 5 October 2026 (100 units, falling to 10 above S$10 and 1 above
    S$100 for the instruments SGX specifies), which is per-security reference data.
    Size the quantity with ``minimum-fill-size-and-lot-rounding-logic`` and validate
    the price here.

    Args:
        counter: SGX-ST counter code, e.g. ``"D05"``. Used for reporting only - the
            bid size comes from the price and the security class, never from the code.
        currency: must be ``"SGD"``. SGX RegCo removed the requirement to align HKD,
            RMB and JPY minimum bid sizes with the home market from 15 July 2026, so
            the SGD scale below cannot be assumed to carry over to a foreign-currency
            counter; read that counter's bid size from reference data.
        stop_price: stop trigger price, required for ``STOP_LIMIT``. Because the scale
            is price-tiered, the trigger is checked against the bid size for *its own*
            price band, which need not be the limit price's band.

    Raises:
        SGXOrderError: non-SGD ``currency``, or ``side`` is not an :class:`OrderSide`.
        TickSizeUnavailableError: ``security_class`` has no derivable bid size
            (ETFs and ETNs).

    A price of zero or below is reported as an ``INVALID_PRICE`` violation rather than
    raised, so it is handled the same way as an off-tick price by a caller that
    branches on ``status``.
    """
    if not isinstance(side, OrderSide):
        raise SGXOrderError(f"side must be an OrderSide, got {side!r}.")
    if currency.upper() != "SGD":
        raise SGXOrderError(
            f"This minimum bid size table is the SGD scale; {currency!r} counters have "
            f"been outside the home-market alignment requirement since 15 July 2026."
        )

    tick_size: Optional[Decimal] = None
    violations: Tuple[str, ...] = ()
    if order_type is not SGXOrderType.MARKET:
        required = (price,) if order_type is SGXOrderType.LIMIT else (price, stop_price)
        for index, value in enumerate(required):
            if value is None:
                violations += (STATUS_MISSING_LIMIT_PRICE,)
                continue
            if to_decimal(value) <= 0:
                violations += (STATUS_INVALID_PRICE,)
                continue
            # Price-tiered scale: each price is checked on the bid size of its own band.
            band_tick = get_sgx_st_minimum_bid_size(value, security_class)
            if index == 0:
                tick_size = band_tick
            if not is_on_tick(value, band_tick):
                violations += (STATUS_INVALID_TICK,)
        violations = tuple(v for v in _STATUS_PRECEDENCE if v in violations)

    violations += _check_quantity(quantity, "units")

    result = SGXOrderValidation(
        market=SGXMarket.SECURITIES_REACH_ST,
        instrument=str(counter).strip().upper(),
        status=_resolve_status(violations),
        violations=violations,
        tick_size=tick_size,
        order_notional=(
            to_decimal(price) * to_decimal(quantity)
            if price is not None
            and not violations
            and order_type is not SGXOrderType.MARKET
            else None
        ),
        currency="SGD",
    )
    logger.info(
        "SGX-ST %s %s validation: %s (%s units, bid size %s, class %s).",
        result.instrument, side.value, result.status, quantity,
        tick_size, security_class.value,
    )
    return result

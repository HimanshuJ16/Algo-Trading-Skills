"""Pre-dispatch order sizing: board-lot rounding, venue minimum quantity, and
the optional FIX minimum-execution constraint (Tag 110 / Tag 1089).

Two distinct concepts are deliberately kept apart, because conflating them is the
most common defect in lot-rounding code:

* **Venue sizing constraints** -- the board lot / step size and the minimum
  tradable quantity of the *instrument*. In FIX these are reference data
  published by the venue (Tag 561 ``RoundLot``, "the trading lot size of a
  security"; Tag 562 ``MinTradeVol``, "the minimum trading volume for a
  security"), carried on Security Definition / Security List messages.
* **Client execution constraints** -- an *instruction the sender adds to its own
  order*: FIX Tag 110 ``MinQty`` ("minimum quantity of an order to be executed")
  and Tag 1089 ``MatchIncrement``. These are optional, they change how the venue
  handles the order, and they are never implied by a board lot size.

All quantity arithmetic uses :class:`decimal.Decimal`. Binary floats silently
corrupt step-size arithmetic -- ``math.floor(0.29 / 0.01) * 0.01`` evaluates to
``0.28``, and ``0.29 % 0.01`` evaluates to ``0.009999999999999974`` rather than
zero -- which on a crypto venue quietly drops part of the order.

This module sizes an order. It does not enforce position or exposure limits and
it does not submit anything.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_UP,
    Decimal,
    InvalidOperation,
    localcontext,
)
from typing import List, Optional, Union

logger = logging.getLogger(__name__)

#: Accepted quantity/price input types. ``str``/``int``/``Decimal`` are exact;
#: ``float`` is converted via ``Decimal(str(value))``, which recovers the decimal
#: literal a caller most likely meant (``0.1`` -> ``Decimal("0.1")``) but cannot
#: recover precision already lost to earlier float arithmetic.
QuantityInput = Union[int, str, Decimal, float]

VALID_SIDES = ("BUY", "SELL")
VALID_ROUNDING_MODES = ("FLOOR", "CEIL", "ROUND_HALF_UP")

_ROUNDING_MAP = {
    "FLOOR": ROUND_FLOOR,
    "CEIL": ROUND_CEILING,
    "ROUND_HALF_UP": ROUND_HALF_UP,
}

# Terminal statuses.
STATUS_SUCCESS = "LOT_ROUNDING_SUCCESS"
STATUS_ODD_LOT_ADJUSTED = "ODD_LOT_ADJUSTED_TO_ROUND_LOT"
STATUS_ODD_LOT_PRESERVED = "ODD_LOT_PRESERVED"
STATUS_REJECTED_BELOW_MIN_QTY = "ORDER_REJECTED_BELOW_MIN_QTY"
STATUS_REJECTED_ABOVE_MAX_QTY = "ORDER_REJECTED_ABOVE_MAX_QTY"
STATUS_REJECTED_BELOW_MIN_NOTIONAL = "ORDER_REJECTED_BELOW_MIN_NOTIONAL"

# Non-terminal warning codes, reported on ``OrderRoundingReport.warnings``.
WARN_DEPTH_UNSATISFIED = "MIN_QTY_DEPTH_UNSATISFIED"
WARN_ROUNDED_UP = "ROUNDED_UP_ABOVE_REQUEST"
WARN_VENUE_MIN_NOT_LOT_MULTIPLE = "VENUE_MIN_QTY_NOT_LOT_MULTIPLE"
WARN_MIN_EXEC_NOT_LOT_MULTIPLE = "MIN_EXECUTION_QTY_NOT_LOT_MULTIPLE"
WARN_MIN_EXEC_SUPPRESSES_DISPLAY = "MIN_EXECUTION_QTY_SUPPRESSES_DISPLAY"
WARN_NOTIONAL_UNCHECKED = "MIN_NOTIONAL_UNCHECKED_NO_PRICE"
WARN_LOT_SIZE_UNSOURCED = "LOT_SIZE_REFERENCE_DATA_UNSOURCED"

_ZERO = Decimal("0")
_ONE = Decimal("1")

#: Working precision for quantity arithmetic. Far above any tradable
#: quantity-to-lot ratio, so exceeding it means the inputs are implausible rather
#: than that the calculation needs more digits.
_ARITHMETIC_PRECISION = 60


def to_quantity(value: QuantityInput, field_name: str) -> Decimal:
    """Convert *value* to a finite :class:`~decimal.Decimal`.

    Raises:
        TypeError: if *value* is a ``bool`` or an unsupported type.
        ValueError: if *value* is not parseable or is NaN/Infinity. ``float('nan')``
            passes a naive ``value <= 0`` guard, so it must be rejected explicitly.
    """
    if isinstance(value, bool):
        raise TypeError(f"{field_name} must be a number, not a bool (got {value!r}).")
    if isinstance(value, Decimal):
        converted = value
    elif isinstance(value, int):
        converted = Decimal(value)
    elif isinstance(value, str):
        try:
            converted = Decimal(value.strip())
        except InvalidOperation as exc:
            raise ValueError(f"{field_name} is not a valid decimal: {value!r}.") from exc
    elif isinstance(value, float):
        converted = Decimal(str(value))
    else:
        raise TypeError(
            f"{field_name} must be int, str, Decimal or float (got {type(value).__name__})."
        )

    if not converted.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}.")
    return converted


def _positive(value: QuantityInput, field_name: str) -> Decimal:
    converted = to_quantity(value, field_name)
    if converted <= _ZERO:
        raise ValueError(f"{field_name} must be strictly positive, got {converted}.")
    return converted


def _optional_positive(value: Optional[QuantityInput], field_name: str) -> Optional[Decimal]:
    return None if value is None else _positive(value, field_name)


def _non_empty(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string.")
    return value.strip()


@dataclass
class OrderRoundingConfig:
    """Venue sizing constraints for one instrument, sourced from reference data.

    ``lot_size`` and ``min_order_quantity`` are required and have no default: a
    board lot is a per-security, time-varying attribute, not a constant. US round
    lots became price-tiered (100/40/10/1 shares) on 3 November 2025 and are
    reassigned semiannually; SGX moves to price-tiered board lots on 5 October
    2026. Hard-coding 100 is a stale-reference-data bug waiting to happen, so
    record where the value came from in ``lot_size_source``/``lot_size_as_of``.

    Attributes:
        lot_size: Board lot / step size the routed quantity must be a multiple of
            (FIX Tag 561 ``RoundLot``).
        min_order_quantity: Smallest quantity the venue will accept for this
            instrument (FIX Tag 562 ``MinTradeVol``). This is a venue constraint,
            *not* FIX Tag 110 -- see :attr:`RawOrderRequest.min_execution_quantity`.
        max_order_quantity: Largest quantity the venue will accept, if any
            (FIX Tag 1140 ``MaxTradeVol``). This is a venue cap, not a risk limit.
        min_notional: Minimum ``price * quantity`` the venue will accept, if any
            (e.g. the Binance ``NOTIONAL`` filter). Rounding down can carry an
            otherwise valid order under this floor.
        rounding_mode: ``'FLOOR'``, ``'CEIL'`` or ``'ROUND_HALF_UP'``.
        allow_odd_lots: When ``True`` a quantity that is not a lot multiple is
            routed unchanged (it must still clear the venue minimum). Only set
            this where the venue actually executes odd lots.
    """

    symbol: str
    lot_size: QuantityInput
    min_order_quantity: QuantityInput
    rounding_mode: str = "FLOOR"
    allow_odd_lots: bool = False
    max_order_quantity: Optional[QuantityInput] = None
    min_notional: Optional[QuantityInput] = None
    lot_size_source: Optional[str] = None
    lot_size_as_of: Optional[str] = None

    def __post_init__(self) -> None:
        self.symbol = _non_empty(self.symbol, "symbol")
        self.lot_size = _positive(self.lot_size, "lot_size")
        self.min_order_quantity = _positive(self.min_order_quantity, "min_order_quantity")

        if self.rounding_mode == "ROUND_NEAREST":
            raise ValueError(
                "rounding_mode 'ROUND_NEAREST' was removed in v2.0.0: it resolved to "
                "Python's banker's rounding, so 250 rounded to 200 while 350 rounded to "
                "400 under a lot size of 100. Use 'ROUND_HALF_UP' for half-up nearest, "
                "or 'FLOOR'/'CEIL' for an explicit direction."
            )
        if self.rounding_mode not in VALID_ROUNDING_MODES:
            raise ValueError(
                f"Unsupported rounding mode {self.rounding_mode!r}; "
                f"expected one of {VALID_ROUNDING_MODES}."
            )
        if not isinstance(self.allow_odd_lots, bool):
            raise TypeError("allow_odd_lots must be a bool.")

        self.max_order_quantity = _optional_positive(self.max_order_quantity, "max_order_quantity")
        self.min_notional = _optional_positive(self.min_notional, "min_notional")

        if self.max_order_quantity is not None and self.max_order_quantity < self.min_order_quantity:
            raise ValueError(
                f"max_order_quantity ({self.max_order_quantity}) is below "
                f"min_order_quantity ({self.min_order_quantity})."
            )


@dataclass
class RawOrderRequest:
    """An unrounded target quantity from a strategy or slicing schedule.

    Attributes:
        raw_quantity: Unrounded target quantity.
        limit_price: Limit price, or ``None`` for a market order. Required to
            evaluate ``min_notional``.
        available_liquidity_depth: Observed depth at the limit price, or ``None``
            if not measured. There is no defensible default -- a fabricated depth
            makes the fill-likelihood check pass silently.
        min_execution_quantity: Optional FIX Tag 110 ``MinQty`` -- "minimum quantity
            of an order to be executed". Attaching it changes venue order handling:
            under Nasdaq Equity 4 Rule 4703(e) a Minimum Quantity order may not be
            displayed, and if it is also marked Display the system accepts it but
            forces a Time-in-Force of IOC. Leave ``None`` unless that is intended.
        match_increment: Optional FIX Tag 1089 ``MatchIncrement`` -- the cumulative
            quantity of every execution must be a multiple of this value.
    """

    order_id: str
    symbol: str
    side: str
    raw_quantity: QuantityInput
    limit_price: Optional[QuantityInput] = None
    available_liquidity_depth: Optional[QuantityInput] = None
    min_execution_quantity: Optional[QuantityInput] = None
    match_increment: Optional[QuantityInput] = None

    def __post_init__(self) -> None:
        self.order_id = _non_empty(self.order_id, "order_id")
        self.symbol = _non_empty(self.symbol, "symbol")

        side = _non_empty(self.side, "side").upper()
        if side not in VALID_SIDES:
            raise ValueError(f"side must be one of {VALID_SIDES}, got {self.side!r}.")
        self.side = side

        self.raw_quantity = _positive(self.raw_quantity, "raw_quantity")
        self.limit_price = _optional_positive(self.limit_price, "limit_price")
        self.min_execution_quantity = _optional_positive(
            self.min_execution_quantity, "min_execution_quantity"
        )
        self.match_increment = _optional_positive(self.match_increment, "match_increment")

        if self.available_liquidity_depth is not None:
            depth = to_quantity(self.available_liquidity_depth, "available_liquidity_depth")
            if depth < _ZERO:
                raise ValueError(f"available_liquidity_depth must not be negative, got {depth}.")
            self.available_liquidity_depth = depth


@dataclass
class OrderRoundingReport:
    """Outcome of sizing one order against one venue's constraints.

    ``status`` is the single terminal outcome; advisory findings are collected in
    ``warnings`` so that a depth or overshoot warning is never overwritten by a
    later rounding outcome.
    """

    order_id: str
    symbol: str
    side: str
    raw_quantity: Decimal
    rounded_quantity: Decimal
    quantity_delta: Decimal
    lot_size: Decimal
    venue_min_order_quantity: Decimal
    fix_tag_110_min_qty: Optional[Decimal]
    fix_tag_1089_match_increment: Optional[Decimal]
    notional: Optional[Decimal]
    is_odd_lot_request: bool
    routes_odd_lot: bool
    is_compliant: bool
    status: str
    audit_notes: str
    warnings: List[str] = field(default_factory=list)


class MinimumFillSizeAndLotRoundingEngine:
    """Rounds an order quantity to a venue's board lot and audits it against the
    venue's minimum/maximum quantity, minimum notional, and the caller's optional
    FIX Tag 110 / Tag 1089 execution constraints.

    The engine is stateless and deterministic: the same config and order always
    produce the same report.
    """

    def apply_lot_rounding_and_min_qty_rules(
        self,
        config: OrderRoundingConfig,
        order: RawOrderRequest,
    ) -> OrderRoundingReport:
        """Size one order for dispatch.

        Args:
            config: Venue sizing constraints for ``order.symbol``.
            order: The unrounded request.

        Returns:
            An :class:`OrderRoundingReport`. A rejected order reports
            ``rounded_quantity == 0`` and ``is_compliant is False``; callers must
            branch on ``is_compliant`` rather than on ``status`` string matching.

        Raises:
            ValueError: if ``config.symbol`` and ``order.symbol`` disagree, if
                ``min_execution_quantity``/``match_increment`` exceed the quantity
                that would actually be routed (such an order can never execute), or
                if the quantity-to-lot ratio is too extreme to evaluate.
        """
        if config.symbol != order.symbol:
            raise ValueError(
                f"Config symbol {config.symbol!r} does not match order symbol "
                f"{order.symbol!r}; lot sizes are per-security and must not be "
                "applied across instruments."
            )

        with localcontext() as ctx:
            ctx.prec = _ARITHMETIC_PRECISION
            try:
                return self._evaluate(config, order)
            except InvalidOperation as exc:
                raise ValueError(
                    f"Quantity arithmetic for {order.symbol} exceeded "
                    f"{_ARITHMETIC_PRECISION} significant digits (raw_quantity="
                    f"{order.raw_quantity}, lot_size={config.lot_size}); the "
                    "quantity-to-lot ratio is implausible for a tradable order."
                ) from exc

    def _evaluate(
        self,
        config: OrderRoundingConfig,
        order: RawOrderRequest,
    ) -> OrderRoundingReport:
        """Size one order. Must be called inside a ``_ARITHMETIC_PRECISION`` context."""
        raw_q: Decimal = order.raw_quantity
        lot_size: Decimal = config.lot_size
        warnings: List[str] = []

        rounded_q = self._round_to_lot(raw_q, lot_size, config.rounding_mode)
        is_odd_lot_request = (raw_q % lot_size) != _ZERO

        if config.allow_odd_lots and is_odd_lot_request:
            effective_q = raw_q
        else:
            effective_q = rounded_q
        routes_odd_lot = (effective_q % lot_size) != _ZERO
        quantity_delta = effective_q - raw_q

        # --- Client execution constraints (FIX Tag 110 / Tag 1089) -------------
        min_exec = order.min_execution_quantity
        match_increment = order.match_increment
        if min_exec is not None:
            if min_exec > effective_q:
                raise ValueError(
                    f"min_execution_quantity (FIX Tag 110 = {min_exec}) exceeds the routed "
                    f"quantity ({effective_q}); such an order can never execute."
                )
            warnings.append(WARN_MIN_EXEC_SUPPRESSES_DISPLAY)
            if (min_exec % lot_size) != _ZERO:
                warnings.append(WARN_MIN_EXEC_NOT_LOT_MULTIPLE)
        if match_increment is not None and match_increment > effective_q:
            raise ValueError(
                f"match_increment (FIX Tag 1089 = {match_increment}) exceeds the routed "
                f"quantity ({effective_q}); no execution could satisfy it."
            )

        # --- Advisory findings --------------------------------------------------
        if quantity_delta > _ZERO:
            warnings.append(WARN_ROUNDED_UP)
        if (config.min_order_quantity % lot_size) != _ZERO:
            warnings.append(WARN_VENUE_MIN_NOT_LOT_MULTIPLE)
        if config.lot_size_source is None and config.lot_size_as_of is None:
            warnings.append(WARN_LOT_SIZE_UNSOURCED)

        depth = order.available_liquidity_depth
        if depth is not None:
            depth_threshold = min_exec if min_exec is not None else effective_q
            if depth < depth_threshold:
                warnings.append(WARN_DEPTH_UNSATISFIED)

        notional: Optional[Decimal] = None
        if order.limit_price is not None:
            notional = effective_q * order.limit_price
        elif config.min_notional is not None:
            warnings.append(WARN_NOTIONAL_UNCHECKED)

        # --- Terminal outcome ---------------------------------------------------
        if effective_q < config.min_order_quantity:
            return self._rejected(
                config, order, raw_q, quantity_delta, is_odd_lot_request, routes_odd_lot,
                notional, warnings, STATUS_REJECTED_BELOW_MIN_QTY,
                f"ORDER REJECTED [{order.symbol}]: routable quantity {effective_q} is below "
                f"the venue minimum order quantity {config.min_order_quantity} "
                f"(raw {raw_q}, lot {lot_size}, mode {config.rounding_mode}).",
            )

        if config.max_order_quantity is not None and effective_q > config.max_order_quantity:
            return self._rejected(
                config, order, raw_q, quantity_delta, is_odd_lot_request, routes_odd_lot,
                notional, warnings, STATUS_REJECTED_ABOVE_MAX_QTY,
                f"ORDER REJECTED [{order.symbol}]: routable quantity {effective_q} exceeds the "
                f"venue maximum order quantity {config.max_order_quantity}. Split the parent order.",
            )

        if config.min_notional is not None and notional is not None and notional < config.min_notional:
            return self._rejected(
                config, order, raw_q, quantity_delta, is_odd_lot_request, routes_odd_lot,
                notional, warnings, STATUS_REJECTED_BELOW_MIN_NOTIONAL,
                f"ORDER REJECTED [{order.symbol}]: notional {notional} is below the venue "
                f"minimum notional {config.min_notional} after lot rounding.",
            )

        if routes_odd_lot:
            status = STATUS_ODD_LOT_PRESERVED
            notes = (
                f"ODD LOT PRESERVED [{order.symbol}]: quantity {effective_q} is not a multiple "
                f"of lot {lot_size} and is routed unchanged because allow_odd_lots is set."
            )
        elif is_odd_lot_request:
            status = STATUS_ODD_LOT_ADJUSTED
            notes = (
                f"LOT ROUNDING [{order.symbol}]: raw quantity {raw_q} adjusted to {effective_q} "
                f"under mode {config.rounding_mode} (lot {lot_size}, delta {quantity_delta})."
            )
        else:
            status = STATUS_SUCCESS
            notes = (
                f"LOT ROUNDING OK [{order.symbol}]: quantity {effective_q} already satisfies "
                f"lot {lot_size} and venue minimum {config.min_order_quantity}."
            )

        logger.info("%s warnings=%s", notes, warnings)
        return OrderRoundingReport(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            raw_quantity=raw_q,
            rounded_quantity=effective_q,
            quantity_delta=quantity_delta,
            lot_size=lot_size,
            venue_min_order_quantity=config.min_order_quantity,
            fix_tag_110_min_qty=min_exec,
            fix_tag_1089_match_increment=match_increment,
            notional=notional,
            is_odd_lot_request=is_odd_lot_request,
            routes_odd_lot=routes_odd_lot,
            is_compliant=True,
            status=status,
            audit_notes=notes,
            warnings=warnings,
        )

    @staticmethod
    def _round_to_lot(raw_q: Decimal, lot_size: Decimal, rounding_mode: str) -> Decimal:
        """Round *raw_q* to a multiple of *lot_size* using exact decimal arithmetic.

        ``ROUND_HALF_UP`` is half-away-from-zero on the lot count, so 250 shares at
        a lot size of 100 rounds to 300 -- not to 200 as Python's banker's-rounding
        ``round()`` would give.
        """
        with localcontext() as ctx:
            ctx.prec = _ARITHMETIC_PRECISION
            lot_units = (raw_q / lot_size).to_integral_value(
                rounding=_ROUNDING_MAP[rounding_mode]
            )
            rounded = lot_units * lot_size
            # Normalise the exponent so 2 * 0.01 reports as 0.02 rather than 2E-2,
            # and 2 * 1E+2 reports as 200 rather than 2E+2.
            exponent = lot_size if lot_size.as_tuple().exponent < 0 else _ONE
            return rounded.quantize(exponent)

    @staticmethod
    def _rejected(
        config: OrderRoundingConfig,
        order: RawOrderRequest,
        raw_q: Decimal,
        quantity_delta: Decimal,
        is_odd_lot_request: bool,
        routes_odd_lot: bool,
        notional: Optional[Decimal],
        warnings: List[str],
        status: str,
        notes: str,
    ) -> OrderRoundingReport:
        """Build a rejection report.

        ``rounded_quantity`` is zeroed so that a caller which ignores
        ``is_compliant`` cannot route the rejected size by accident.
        """
        logger.warning("%s warnings=%s", notes, warnings)
        return OrderRoundingReport(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            raw_quantity=raw_q,
            rounded_quantity=_ZERO,
            quantity_delta=quantity_delta,
            lot_size=config.lot_size,
            venue_min_order_quantity=config.min_order_quantity,
            fix_tag_110_min_qty=order.min_execution_quantity,
            fix_tag_1089_match_increment=order.match_increment,
            notional=notional,
            is_odd_lot_request=is_odd_lot_request,
            routes_odd_lot=routes_odd_lot,
            is_compliant=False,
            status=status,
            audit_notes=notes,
            warnings=warnings,
        )

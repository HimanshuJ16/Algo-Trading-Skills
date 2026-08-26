"""Pre-routing internal netting of opposing orders from multiple strategy books.

The engine takes one batch of internal orders in a single symbol, matches the
opposing quantity at the mid-point of the current quote, and emits a single net
residual order for the dominant side. Its output is an audit artefact: every
internal fill, every order that was *not* crossed and why, and a cost-saving
estimate with its assumptions stated.

Three distinctions drive the design, because collapsing any of them is where
netting engines go wrong:

* **A cross is not always a trade.** When every matched participant maps to the
  same beneficial owner, the internal match moves nothing between owners -- it
  is a book transfer, and nothing was executed on any market for it to be
  reported as. Note the direction of the FINRA rule here: Supplementary Material
  .02 to Rule 5210 treats *unintentional* self-trades as generally bona fide and
  requires controls against a **pattern or practice** of them from related
  algorithms or desks -- netting before routing is one such control. What it
  does not license is manufacturing a transaction record for a movement that
  changed no beneficial ownership. When the matched participants span two or
  more beneficial owners the same arithmetic produces a real execution, which a
  FINRA member must report to a Trade Reporting Facility "as soon as
  practicable, but no later than 10 seconds after execution" (Rule 6380A).
  :attr:`NettingReport.cross_type` and
  :attr:`NettingReport.requires_execution_report` carry that classification, and
  unclassified ownership is treated as reportable rather than as a transfer.

* **A limit price is a constraint, not an annotation.** An order whose limit is
  not marketable at the mid is never crossed and never folded into the net
  residual -- it is returned in :attr:`NettingReport.excluded_orders` for the
  caller to route on its own terms. Netting it away would fill a strategy
  through its own limit; folding it into a market residual would silently
  convert a limit order into a market order.

* **Avoided fees are not all fees.** Crossing internally avoids the venue's
  access/taker fee on both sides. It does not avoid the costs that attach to the
  internalised print itself when the cross is reportable -- the Section 31
  regulatory transaction fee, the FINRA Trading Activity Fee, and TRF/clearing
  charges. Those are supplied through
  ``MarketQuote.retained_internalization_cost_per_share_usd``; when they are
  omitted on a reportable cross the report carries
  ``INTERNALIZATION_COST_UNMODELLED`` and the net saving is the gross figure.

All price and money arithmetic uses :class:`decimal.Decimal`. The mid of a
one-cent spread is a half-cent, and half-cents accumulated in binary floats do
not reconcile against a clearing statement. The mid is deliberately *not* rounded
to a whole penny: rounding it would hand the rounded-toward side a systematic
edge on every cross, and Rule 612 constrains the increments in which orders and
quotations may be displayed, ranked or accepted -- not the price at which an
execution may occur.

Scope: integer share/contract quantities in one symbol, one batch, one quote.
This module matches and sizes. It submits nothing, reports nothing to a trade
reporting facility, and enforces no position or exposure limit.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Dict, List, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

#: Accepted price/fee input types. ``str``/``int``/``Decimal`` are exact;
#: ``float`` is converted via ``Decimal(str(value))``, which recovers the decimal
#: literal the caller most likely meant but cannot recover precision already lost
#: to earlier float arithmetic.
NumericInput = Union[int, str, Decimal, float]

BUY = "BUY"
SELL = "SELL"
VALID_SIDES: Tuple[str, ...] = (BUY, SELL)

#: Pro-rata is the default because a cross allocated by arrival order hands the
#: strategies that happen to be first in the batch the mid-price fill and leaves
#: the rest to pay the spread -- a persistent, invisible transfer between books.
ALLOCATION_PRO_RATA = "PRO_RATA"
ALLOCATION_TIME_PRIORITY = "TIME_PRIORITY"
VALID_ALLOCATION_POLICIES: Tuple[str, ...] = (ALLOCATION_PRO_RATA, ALLOCATION_TIME_PRIORITY)

# Terminal statuses.
STATUS_SUCCESS = "NETTING_SUCCESS"
STATUS_NO_INTERNAL_CROSS = "NO_INTERNAL_CROSS"
STATUS_SKIPPED_STALE_QUOTE = "NETTING_SKIPPED_STALE_QUOTE"
STATUS_SKIPPED_CROSSED_QUOTE = "NETTING_SKIPPED_CROSSED_QUOTE"

# Beneficial-ownership classification of the matched quantity.
CROSS_TYPE_NONE = "NO_CROSS"
CROSS_TYPE_BOOK_TRANSFER = "SAME_BENEFICIAL_OWNER_TRANSFER"
CROSS_TYPE_REPORTABLE = "REPORTABLE_CROSS"
CROSS_TYPE_UNCLASSIFIED = "BENEFICIAL_OWNERSHIP_UNCLASSIFIED"

# Reasons an order was kept out of the internal cross and out of the residual.
EXCLUDED_LIMIT_NOT_MARKETABLE = "LIMIT_PRICE_NOT_MARKETABLE_AT_MID"
EXCLUDED_STALE_QUOTE = "QUOTE_STALE"
EXCLUDED_CROSSED_QUOTE = "QUOTE_CROSSED"

# Advisory findings; they accumulate on ``NettingReport.warnings`` independently
# of the terminal status.
WARN_QUOTE_AGE_UNVERIFIED = "QUOTE_AGE_UNVERIFIED"
WARN_QUOTE_LOCKED = "QUOTE_LOCKED_ZERO_SPREAD"
WARN_SUB_PENNY_MATCH_PRICE = "SUB_PENNY_INTERNAL_MATCH_PRICE"
WARN_OWNERSHIP_UNCLASSIFIED = "BENEFICIAL_OWNERSHIP_UNCLASSIFIED"
WARN_INTERNALIZATION_COST_UNMODELLED = "INTERNALIZATION_COST_UNMODELLED"
WARN_RESIDUAL_MARKET_ORDER = "RESIDUAL_ROUTED_AS_MARKET_ORDER"
WARN_LIMIT_ORDER_EXCLUDED = "LIMIT_ORDER_EXCLUDED_FROM_CROSS"
WARN_BUNCHED_RESIDUAL = "RESIDUAL_BUNCHES_MULTIPLE_ACCOUNTS"

_ZERO = Decimal("0")
_TWO = Decimal("2")
#: A mid with an exponent below this is finer than one cent.
_SUB_PENNY_EXPONENT = -2


def to_decimal(value: NumericInput, field_name: str) -> Decimal:
    """Convert *value* to a finite :class:`~decimal.Decimal`.

    Raises:
        TypeError: if *value* is a ``bool`` or an unsupported type.
        ValueError: if *value* is unparseable or is NaN/Infinity. ``float('nan')``
            satisfies neither ``> 0`` nor ``<= 0``, so it slips through a naive
            positivity guard and has to be rejected explicitly.
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


def _positive_decimal(value: NumericInput, field_name: str) -> Decimal:
    converted = to_decimal(value, field_name)
    if converted <= _ZERO:
        raise ValueError(f"{field_name} must be strictly positive, got {converted}.")
    return converted


def _non_negative_decimal(value: NumericInput, field_name: str) -> Decimal:
    converted = to_decimal(value, field_name)
    if converted < _ZERO:
        raise ValueError(f"{field_name} must not be negative, got {converted}.")
    return converted


def _positive_int(value: object, field_name: str) -> int:
    """Quantities are whole shares/contracts; a float quantity is a caller bug."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{field_name} must be an int number of shares/contracts "
            f"(got {type(value).__name__}: {value!r})."
        )
    if value <= 0:
        raise ValueError(f"{field_name} must be strictly positive, got {value}.")
    return value


@dataclass
class InternalOrder:
    """One strategy's order in the pre-routing batch.

    Attributes:
        limit_price: The strategy's own price constraint. An order whose limit is
            not marketable at the mid is excluded from the cross *and* from the
            residual rather than being crossed through its limit or silently
            promoted to a market order.
        beneficial_owner_id: The account/entity that owns the position. Two
            orders sharing this value cross without any change of beneficial
            ownership; two orders with different values produce a real
            execution. Leaving it ``None`` makes the batch unclassifiable and the
            engine then treats the cross as reportable.
    """

    order_id: str
    strategy_id: str
    symbol: str
    side: str
    quantity: int
    limit_price: Optional[NumericInput] = None
    beneficial_owner_id: Optional[str] = None


@dataclass
class MarketQuote:
    """The reference quote the internal cross is priced from.

    Attributes:
        fee_per_share_usd: The venue access/taker fee per share that crossing
            internally avoids on each side.
        as_of: Epoch seconds at which the quote was captured. Left ``None``, the
            engine cannot age the quote and says so (``QUOTE_AGE_UNVERIFIED``)
            rather than assuming it is fresh.
        retained_internalization_cost_per_share_usd: Per matched share, across
            both sides, of the costs that survive internalisation when the cross
            is reportable -- Section 31 regulatory transaction fee, FINRA Trading
            Activity Fee, TRF/clearing charges. See ``references/standards.md``
            for the conversion from the published ad valorem rates.
    """

    symbol: str
    bid_price: NumericInput
    ask_price: NumericInput
    fee_per_share_usd: NumericInput = 0.003
    as_of: Optional[float] = None
    retained_internalization_cost_per_share_usd: Optional[NumericInput] = None


@dataclass(frozen=True)
class NettingConfig:
    """Engine policy.

    Attributes:
        allocation_policy: How the matched quantity is shared between the orders
            on each side. ``PRO_RATA`` splits by submitted quantity with a
            largest-remainder rule; ``TIME_PRIORITY`` fills in batch order and is
            only defensible where that priority is disclosed to every book.
        max_quote_age_seconds: An **engineering default**, not a regulatory
            figure. It bounds how stale the reference quote may be before the
            engine refuses to cross against it.
    """

    allocation_policy: str = ALLOCATION_PRO_RATA
    max_quote_age_seconds: Optional[float] = 1.0

    def __post_init__(self) -> None:
        if self.allocation_policy not in VALID_ALLOCATION_POLICIES:
            raise ValueError(
                f"allocation_policy must be one of {VALID_ALLOCATION_POLICIES}, "
                f"got {self.allocation_policy!r}."
            )
        if self.max_quote_age_seconds is not None:
            age = to_decimal(self.max_quote_age_seconds, "max_quote_age_seconds")
            if age <= _ZERO:
                raise ValueError(
                    f"max_quote_age_seconds must be strictly positive or None, got {age}."
                )


@dataclass
class InternalFill:
    order_id: str
    strategy_id: str
    symbol: str
    side: str
    filled_quantity: int
    fill_price: Decimal
    beneficial_owner_id: Optional[str] = None


@dataclass
class ExcludedOrder:
    """An order the engine deliberately did not net. The caller still owns it."""

    order_id: str
    strategy_id: str
    side: str
    quantity: int
    reason: str


@dataclass
class ExternalRoutedOrder:
    """The single net residual order for the dominant side.

    ``limit_price`` is the most conservative limit among the orders contributing
    residual quantity, so no contributor can be filled outside its own limit.
    ``order_type`` is ``MARKET`` only when no contributor constrained the price.
    """

    symbol: str
    side: str
    quantity: int
    order_type: str
    limit_price: Optional[Decimal] = None
    contributing_order_ids: Tuple[str, ...] = ()


@dataclass
class NettingReport:
    symbol: str
    total_buy_quantity: int
    total_sell_quantity: int
    eligible_buy_quantity: int
    eligible_sell_quantity: int
    internal_matched_quantity: int
    internal_match_price: Optional[Decimal]
    quoted_spread: Optional[Decimal]
    net_external_quantity: int
    external_order: Optional[ExternalRoutedOrder]
    internal_fills: List[InternalFill]
    excluded_orders: List[ExcludedOrder]
    cross_type: str
    requires_execution_report: bool
    gross_fee_savings_usd: Decimal
    retained_internalization_cost_usd: Decimal
    net_fee_savings_usd: Decimal
    spread_savings_usd: Decimal
    total_cost_savings_usd: Decimal
    status: str
    warnings: List[str] = field(default_factory=list)
    audit_notes: str = ""


class MultiOrderNettingEngine:
    """Match opposing internal orders at the mid and size the net residual.

    The engine is stateless across calls: one batch, one symbol, one quote, one
    report. It holds no open orders and no positions, so it is safe to share an
    instance across threads only to the extent that its :class:`NettingConfig` is
    immutable -- which it is.
    """

    def __init__(self, config: Optional[NettingConfig] = None) -> None:
        self.config = config if config is not None else NettingConfig()

    # ------------------------------------------------------------------ public

    def net_and_route_orders(
        self,
        quote: MarketQuote,
        orders: Sequence[InternalOrder],
        *,
        now: Optional[float] = None,
    ) -> NettingReport:
        """Net *orders* against each other at the mid of *quote*.

        Args:
            quote: The reference quote. Its ``symbol`` defines the batch; an
                order for any other symbol is a caller error, not something to
                net away.
            orders: The internal batch. Duplicate ``order_id`` values are
                rejected -- a replayed batch that double-counts a quantity
                produces a residual order for stock nobody asked to trade.
            now: Epoch seconds, injectable so quote-age behaviour is
                deterministic under test. Defaults to :func:`time.time`.

        Returns:
            A :class:`NettingReport`. Read ``status`` and ``excluded_orders``
            before acting on ``external_order``: on a stale or crossed quote the
            engine crosses nothing and hands every order back.

        Raises:
            ValueError: empty batch, symbol mismatch, duplicate ``order_id``,
                unknown side, non-positive quantity, or a non-positive/NaN/
                infinite price or fee. A stale or crossed *quote* does not raise
                -- it is a market condition, and it comes back as a skipped
                report the caller can act on.
            TypeError: a quantity that is not an ``int``, or a price of an
                unsupported type.
        """
        if not orders:
            raise ValueError("Orders list cannot be empty.")

        symbol = quote.symbol
        bid = _positive_decimal(quote.bid_price, "quote.bid_price")
        ask = _positive_decimal(quote.ask_price, "quote.ask_price")
        avoided_fee = _non_negative_decimal(quote.fee_per_share_usd, "quote.fee_per_share_usd")

        validated = self._validate_orders(symbol, orders)
        warnings: List[str] = []

        # Guard the reference price before anything is matched against it. A
        # crossed book is a dislocated or corrupt quote, and a stale one may sit
        # outside the current NBBO -- crossing at either produces an internal
        # fill nobody can defend post-trade.
        quote_age = self._quote_age_seconds(quote, now)
        if quote_age is None:
            warnings.append(WARN_QUOTE_AGE_UNVERIFIED)
        elif (
            self.config.max_quote_age_seconds is not None
            and quote_age > float(self.config.max_quote_age_seconds)
        ):
            logger.warning(
                "Netting skipped: quote for %s is %.3fs old (limit %.3fs).",
                symbol, quote_age, float(self.config.max_quote_age_seconds),
            )
            return self._skipped_report(
                symbol, validated, STATUS_SKIPPED_STALE_QUOTE, EXCLUDED_STALE_QUOTE, warnings
            )

        if bid > ask:
            logger.warning(
                "Netting skipped: crossed quote for %s (bid %s > ask %s).", symbol, bid, ask
            )
            return self._skipped_report(
                symbol, validated, STATUS_SKIPPED_CROSSED_QUOTE, EXCLUDED_CROSSED_QUOTE, warnings
            )

        # Division by two terminates and needs at most one extra digit, so the
        # mid is exact for any realistic price inside the 28-digit default
        # context. It is deliberately not rounded to a penny: rounding would give
        # the side it moves toward a systematic edge on every single cross.
        mid_price = (bid + ask) / _TWO
        spread = ask - bid
        if spread == _ZERO:
            warnings.append(WARN_QUOTE_LOCKED)
        if mid_price.as_tuple().exponent < _SUB_PENNY_EXPONENT:
            warnings.append(WARN_SUB_PENNY_MATCH_PRICE)

        buy_orders = [o for o in validated if o.side == BUY]
        sell_orders = [o for o in validated if o.side == SELL]
        total_buy_qty = sum(o.quantity for o in buy_orders)
        total_sell_qty = sum(o.quantity for o in sell_orders)

        eligible_buys, excluded = self._partition_by_limit(buy_orders, mid_price)
        eligible_sells, excluded_sells = self._partition_by_limit(sell_orders, mid_price)
        excluded.extend(excluded_sells)
        if excluded:
            warnings.append(WARN_LIMIT_ORDER_EXCLUDED)

        eligible_buy_qty = sum(o.quantity for o in eligible_buys)
        eligible_sell_qty = sum(o.quantity for o in eligible_sells)
        matched_qty = min(eligible_buy_qty, eligible_sell_qty)

        buy_alloc = self._allocate(eligible_buys, matched_qty)
        sell_alloc = self._allocate(eligible_sells, matched_qty)
        internal_fills = self._build_fills(symbol, eligible_buys, buy_alloc, mid_price)
        internal_fills.extend(self._build_fills(symbol, eligible_sells, sell_alloc, mid_price))

        cross_type = self._classify_cross(eligible_buys, buy_alloc, eligible_sells, sell_alloc)
        if cross_type == CROSS_TYPE_UNCLASSIFIED:
            warnings.append(WARN_OWNERSHIP_UNCLASSIFIED)
        requires_report = cross_type in (CROSS_TYPE_REPORTABLE, CROSS_TYPE_UNCLASSIFIED)

        # Exactly one side can carry residual, because matched is the minimum of
        # the two eligible totals.
        residual_buy_qty = eligible_buy_qty - matched_qty
        residual_sell_qty = eligible_sell_qty - matched_qty
        if residual_buy_qty > 0:
            dominant_side, residual_qty = BUY, residual_buy_qty
            residual_source, residual_alloc = eligible_buys, buy_alloc
        elif residual_sell_qty > 0:
            dominant_side, residual_qty = SELL, residual_sell_qty
            residual_source, residual_alloc = eligible_sells, sell_alloc
        else:
            dominant_side, residual_qty = None, 0
            residual_source, residual_alloc = [], {}

        external_order = None
        if residual_qty > 0 and dominant_side is not None:
            external_order = self._build_residual_order(
                symbol, dominant_side, residual_qty, residual_source, residual_alloc
            )
            if external_order.order_type == "MARKET":
                warnings.append(WARN_RESIDUAL_MARKET_ORDER)
            if self._spans_multiple_owners(residual_source, residual_alloc):
                warnings.append(WARN_BUNCHED_RESIDUAL)

        savings = self._compute_savings(
            quote, matched_qty, avoided_fee, spread, requires_report, warnings
        )
        gross_fee_savings, retained_cost, net_fee_savings, spread_savings = savings
        total_savings = net_fee_savings + spread_savings

        status = STATUS_SUCCESS if matched_qty > 0 else STATUS_NO_INTERNAL_CROSS
        notes = (
            f"MULTI-ORDER NETTING [{symbol}] {status}: submitted buy={total_buy_qty} "
            f"sell={total_sell_qty}; eligible buy={eligible_buy_qty} sell={eligible_sell_qty}; "
            f"matched={matched_qty} @ {mid_price}; cross_type={cross_type}; "
            f"residual={residual_qty} {dominant_side or 'NONE'}; "
            f"net fee saving=${net_fee_savings} (gross ${gross_fee_savings} less retained "
            f"${retained_cost}); spread saving=${spread_savings}; total=${total_savings}."
        )
        logger.info(notes)
        if warnings:
            logger.warning("Netting warnings for %s: %s", symbol, ", ".join(warnings))

        return NettingReport(
            symbol=symbol,
            total_buy_quantity=total_buy_qty,
            total_sell_quantity=total_sell_qty,
            eligible_buy_quantity=eligible_buy_qty,
            eligible_sell_quantity=eligible_sell_qty,
            internal_matched_quantity=matched_qty,
            internal_match_price=mid_price,
            quoted_spread=spread,
            net_external_quantity=residual_qty,
            external_order=external_order,
            internal_fills=internal_fills,
            excluded_orders=excluded,
            cross_type=cross_type,
            requires_execution_report=requires_report,
            gross_fee_savings_usd=gross_fee_savings,
            retained_internalization_cost_usd=retained_cost,
            net_fee_savings_usd=net_fee_savings,
            spread_savings_usd=spread_savings,
            total_cost_savings_usd=total_savings,
            status=status,
            warnings=warnings,
            audit_notes=notes,
        )

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _validate_orders(
        symbol: str, orders: Sequence[InternalOrder]
    ) -> List[InternalOrder]:
        """Normalise and validate the batch.

        Every rejection here is a caller error that netting must not paper over:
        an order in another symbol, an unrecognised side (which a filter would
        silently drop, losing the order), a repeated order id (which would
        double-count the quantity), or a non-positive quantity.
        """
        validated: List[InternalOrder] = []
        seen_ids: Dict[str, str] = {}
        for index, order in enumerate(orders):
            label = f"orders[{index}]"
            if order.symbol != symbol:
                raise ValueError(
                    f"{label} ({order.order_id!r}) is for symbol {order.symbol!r} but the "
                    f"batch quote is for {symbol!r}. Netting across symbols would create "
                    f"unintended positions in both."
                )
            side = order.side.strip().upper() if isinstance(order.side, str) else order.side
            if side not in VALID_SIDES:
                raise ValueError(
                    f"{label} ({order.order_id!r}) has side {order.side!r}; expected one of "
                    f"{VALID_SIDES}. An unrecognised side must not be silently dropped."
                )
            if order.order_id in seen_ids:
                raise ValueError(
                    f"{label} repeats order_id {order.order_id!r} (already seen as "
                    f"{seen_ids[order.order_id]}). A duplicated order double-counts its "
                    f"quantity in the net residual."
                )
            seen_ids[order.order_id] = label
            quantity = _positive_int(order.quantity, f"{label}.quantity")
            limit_price = (
                _positive_decimal(order.limit_price, f"{label}.limit_price")
                if order.limit_price is not None
                else None
            )
            validated.append(
                InternalOrder(
                    order_id=order.order_id,
                    strategy_id=order.strategy_id,
                    symbol=order.symbol,
                    side=side,
                    quantity=quantity,
                    limit_price=limit_price,
                    beneficial_owner_id=order.beneficial_owner_id,
                )
            )
        return validated

    @staticmethod
    def _quote_age_seconds(quote: MarketQuote, now: Optional[float]) -> Optional[float]:
        if quote.as_of is None:
            return None
        as_of = float(to_decimal(quote.as_of, "quote.as_of"))
        reference = time.time() if now is None else float(to_decimal(now, "now"))
        # A quote stamped in the future is a clock-skew symptom, not freshness;
        # age 0 keeps it out of the stale branch while the caller investigates.
        return max(0.0, reference - as_of)

    @staticmethod
    def _partition_by_limit(
        orders: Sequence[InternalOrder], mid_price: Decimal
    ) -> Tuple[List[InternalOrder], List[ExcludedOrder]]:
        """Split *orders* into those crossable at *mid_price* and those not.

        A buy crosses only at or below its limit, a sell only at or above it.
        Anything else is handed back untouched: crossing it would fill the
        strategy through its own limit, and folding it into the residual would
        turn a priced order into an unpriced one.
        """
        eligible: List[InternalOrder] = []
        excluded: List[ExcludedOrder] = []
        for order in orders:
            if order.limit_price is None:
                eligible.append(order)
                continue
            limit = order.limit_price  # normalised to Decimal in _validate_orders
            crossable = limit >= mid_price if order.side == BUY else limit <= mid_price
            if crossable:
                eligible.append(order)
            else:
                excluded.append(
                    ExcludedOrder(
                        order_id=order.order_id,
                        strategy_id=order.strategy_id,
                        side=order.side,
                        quantity=order.quantity,
                        reason=EXCLUDED_LIMIT_NOT_MARKETABLE,
                    )
                )
        return eligible, excluded

    def _allocate(
        self, orders: Sequence[InternalOrder], matched_qty: int
    ) -> Dict[str, int]:
        """Split *matched_qty* across *orders*; the result always sums to it."""
        if matched_qty <= 0 or not orders:
            return {order.order_id: 0 for order in orders}
        if self.config.allocation_policy == ALLOCATION_TIME_PRIORITY:
            allocation: Dict[str, int] = {}
            remaining = matched_qty
            for order in orders:
                take = min(order.quantity, remaining)
                allocation[order.order_id] = take
                remaining -= take
            return allocation
        return self._pro_rata(orders, matched_qty)

    @staticmethod
    def _pro_rata(orders: Sequence[InternalOrder], matched_qty: int) -> Dict[str, int]:
        """Largest-remainder pro-rata: exact in whole shares, and deterministic.

        Plain rounding of each share leaves the allocation over- or under-filled
        against the matched quantity. The floor plus largest-remainder rule
        distributes exactly ``matched_qty`` shares, with ``order_id`` breaking
        ties so two identical batches always allocate identically.
        """
        total_qty = sum(order.quantity for order in orders)
        allocation: Dict[str, int] = {}
        remainders: List[Tuple[Decimal, str]] = []
        assigned = 0
        for order in orders:
            exact = (Decimal(matched_qty) * Decimal(order.quantity)) / Decimal(total_qty)
            base = int(exact)  # exact >= 0, so truncation is a floor
            allocation[order.order_id] = base
            assigned += base
            remainders.append((exact - base, order.order_id))
        remainders.sort(key=lambda item: (-item[0], item[1]))
        for _, order_id in remainders[: matched_qty - assigned]:
            allocation[order_id] += 1
        return allocation

    @staticmethod
    def _build_fills(
        symbol: str,
        orders: Sequence[InternalOrder],
        allocation: Dict[str, int],
        mid_price: Decimal,
    ) -> List[InternalFill]:
        return [
            InternalFill(
                order_id=order.order_id,
                strategy_id=order.strategy_id,
                symbol=symbol,
                side=order.side,
                filled_quantity=allocation.get(order.order_id, 0),
                fill_price=mid_price,
                beneficial_owner_id=order.beneficial_owner_id,
            )
            for order in orders
            if allocation.get(order.order_id, 0) > 0
        ]

    @staticmethod
    def _classify_cross(
        buys: Sequence[InternalOrder],
        buy_alloc: Dict[str, int],
        sells: Sequence[InternalOrder],
        sell_alloc: Dict[str, int],
    ) -> str:
        """Classify the matched quantity by beneficial ownership.

        A cross confined to one beneficial owner moves nothing between owners and
        is a book transfer. A cross spanning owners is an execution. Where any
        participant's owner is unknown the engine says so rather than guessing,
        and the caller must resolve it before deciding whether a print is owed.
        """
        participants = [o for o in buys if buy_alloc.get(o.order_id, 0) > 0]
        participants += [o for o in sells if sell_alloc.get(o.order_id, 0) > 0]
        if not participants:
            return CROSS_TYPE_NONE
        owners = {order.beneficial_owner_id for order in participants}
        if None in owners:
            return CROSS_TYPE_UNCLASSIFIED
        return CROSS_TYPE_BOOK_TRANSFER if len(owners) == 1 else CROSS_TYPE_REPORTABLE

    @staticmethod
    def _residual_contributors(
        orders: Sequence[InternalOrder], allocation: Dict[str, int]
    ) -> List[InternalOrder]:
        return [o for o in orders if o.quantity - allocation.get(o.order_id, 0) > 0]

    @classmethod
    def _spans_multiple_owners(
        cls, orders: Sequence[InternalOrder], allocation: Dict[str, int]
    ) -> bool:
        contributors = cls._residual_contributors(orders, allocation)
        return len({o.beneficial_owner_id for o in contributors}) > 1

    @classmethod
    def _build_residual_order(
        cls,
        symbol: str,
        side: str,
        residual_qty: int,
        orders: Sequence[InternalOrder],
        allocation: Dict[str, int],
    ) -> ExternalRoutedOrder:
        """Size the single external order, preserving contributors' limits.

        The residual limit is the most conservative limit among the contributing
        orders -- the lowest for a buy, the highest for a sell -- so no
        contributor can be filled outside its own price. That may leave the more
        aggressive contributors unfilled; the alternative is filling the
        conservative ones through their limit, which is worse.
        """
        contributors = cls._residual_contributors(orders, allocation)
        limits = [o.limit_price for o in contributors if o.limit_price is not None]
        if limits:
            limit_price: Optional[Decimal] = min(limits) if side == BUY else max(limits)
            order_type = "LIMIT"
        else:
            limit_price, order_type = None, "MARKET"
        return ExternalRoutedOrder(
            symbol=symbol,
            side=side,
            quantity=residual_qty,
            order_type=order_type,
            limit_price=limit_price,
            contributing_order_ids=tuple(o.order_id for o in contributors),
        )

    @staticmethod
    def _compute_savings(
        quote: MarketQuote,
        matched_qty: int,
        avoided_fee: Decimal,
        spread: Decimal,
        requires_report: bool,
        warnings: List[str],
    ) -> Tuple[Decimal, Decimal, Decimal, Decimal]:
        """Estimate the cost avoided by crossing *matched_qty* internally.

        The counterfactual is explicit and it is the whole basis of the number:
        **both** sides are assumed to have removed liquidity at the touch. Each
        side then saves the access fee plus half the spread, so across the two
        sides the batch saves ``2 * matched * fee`` and ``matched * spread``. If
        an order would in fact have rested passively, the saving is overstated --
        and where the venue pays a maker rebate, crossing internally forgoes it.

        Reportable crosses keep the costs that attach to the print itself; those
        are subtracted when supplied and flagged as unmodelled when not.
        """
        quantity = Decimal(matched_qty)
        gross_fee_savings = _TWO * quantity * avoided_fee
        spread_savings = quantity * spread

        retained_cost = _ZERO
        raw_retained = quote.retained_internalization_cost_per_share_usd
        if raw_retained is not None:
            per_share = _non_negative_decimal(
                raw_retained, "quote.retained_internalization_cost_per_share_usd"
            )
            retained_cost = quantity * per_share if requires_report else _ZERO
        elif requires_report and matched_qty > 0:
            warnings.append(WARN_INTERNALIZATION_COST_UNMODELLED)

        return (
            gross_fee_savings,
            retained_cost,
            gross_fee_savings - retained_cost,
            spread_savings,
        )

    @staticmethod
    def _skipped_report(
        symbol: str,
        orders: Sequence[InternalOrder],
        status: str,
        reason: str,
        warnings: List[str],
    ) -> NettingReport:
        """Cross nothing and hand every order back.

        Deliberately no residual order: if the reference price cannot be trusted
        the netting decision cannot be trusted either, and emitting a net order
        would commit the batch to a position sized from a quote the engine has
        just rejected.
        """
        excluded = [
            ExcludedOrder(
                order_id=o.order_id,
                strategy_id=o.strategy_id,
                side=o.side,
                quantity=o.quantity,
                reason=reason,
            )
            for o in orders
        ]
        notes = (
            f"MULTI-ORDER NETTING [{symbol}] {status}: no internal cross performed; "
            f"{len(excluded)} order(s) returned to the caller for individual routing."
        )
        return NettingReport(
            symbol=symbol,
            total_buy_quantity=sum(o.quantity for o in orders if o.side == BUY),
            total_sell_quantity=sum(o.quantity for o in orders if o.side == SELL),
            eligible_buy_quantity=0,
            eligible_sell_quantity=0,
            internal_matched_quantity=0,
            internal_match_price=None,
            quoted_spread=None,
            net_external_quantity=0,
            external_order=None,
            internal_fills=[],
            excluded_orders=excluded,
            cross_type=CROSS_TYPE_NONE,
            requires_execution_report=False,
            gross_fee_savings_usd=_ZERO,
            retained_internalization_cost_usd=_ZERO,
            net_fee_savings_usd=_ZERO,
            spread_savings_usd=_ZERO,
            total_cost_savings_usd=_ZERO,
            status=status,
            warnings=list(warnings),
            audit_notes=notes,
        )

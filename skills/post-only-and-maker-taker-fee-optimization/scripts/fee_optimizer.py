"""
post-only-and-maker-taker-fee-optimization: builds a venue-correct post-only limit
order payload, refuses to submit one that would cross the spread, and reports the
maker-vs-taker fee differential as an estimate that is explicitly conditional on
the order filling.

What this module does
---------------------
1. Validates the order and the top-of-book snapshot it is priced against.
2. Decides whether the proposed limit price is marketable (would trade on arrival).
3. Either reprices it passively to the near touch, or refuses to submit it.
4. Emits the post-only payload in the form the *named venue* actually accepts.
5. Estimates the fee differential versus crossing the spread instead.

What this module does NOT do
----------------------------
- **It does not promise savings.** A post-only order that is never filled saves
  nothing and forgoes the trade. Every figure carrying ``_if_filled_`` in its name
  is a counterfactual conditional on a fill that post-only cannot guarantee.
  Realized numbers are accrued only through :meth:`record_maker_fill`, from
  quantity the venue actually filled.
- **It does not model adverse selection, queue position, or fill probability.**
  Fee is one term of passive execution cost and frequently the smaller one; the
  passive order that does fill often fills because the price moved against it. See
  `adverse-selection-measurement-for-passive-orders` and
  `queue-position-modeling-for-passive-orders`.
- **It does not protect an order after arrival.** Post-only is evaluated by the
  matching engine at arrival only. A resting order that the market later trades
  through is still a maker fill; a repriced order can still be rejected because the
  book moved between the snapshot and arrival. That race is
  `post-only-limit-repricing-under-fast-markets`.
- **It does not size against depth.** The taker counterfactual assumes the whole
  quantity would have crossed at the touch, which understates the cost of taking
  for size larger than the displayed top-of-book.

Sign convention (every rate and every USD amount in this module)
----------------------------------------------------------------
::

    rate > 0  ->  fee:    the venue CHARGES the desk
    rate < 0  ->  rebate: the venue CREDITS the desk

so a negative maker rate is a rebate, and a *negative* fee differential means
post-only is the more expensive side. This is the same convention as
`exchange-fee-tier-and-rebate-structure-analysis` and
`market-maker-vs-taker-strategy-classification`.

There is no default fee schedule. Maker and taker rates are not interchangeable
across venues or tiers, and a plausible-looking default is the mechanism by which a
fabricated savings figure reaches a report: Binance's published spot schedule
charges the Regular (VIP 0) tier **0.100% maker and 0.100% taker**, so on that
venue and tier post-only changes the fee bill by exactly zero. Both rates are
required arguments.

Venue post-only parameters (verified against venue documentation)
------------------------------------------------------------------
There is no portable post-only flag, and sending the union of every venue's
spelling is not a safe fallback: an unknown field is commonly ignored, and an
ignored post-only flag submits a plain limit order that crosses and is billed at
the taker rate.

===========================  ==================================================
Venue                        Post-only expression
===========================  ==================================================
``BINANCE_SPOT``             order ``type="LIMIT_MAKER"``; no ``timeInForce``.
                             Spot accepts only GTC/IOC/FOK, so ``GTX`` is not a
                             valid spot value.
``BINANCE_USDM_FUTURES``     ``type="LIMIT"`` with ``timeInForce="GTX"``
                             (Good-Till-Crossing / post-only).
``BYBIT_V5``                 ``timeInForce="PostOnly"``; ``category`` is
                             mandatory and must be supplied by the caller.
``COINBASE_ADVANCED``        ``order_configuration.limit_limit_gtc.post_only``.
``KRAKEN_SPOT``              ``oflags="post"`` on an ``ordertype="limit"``.
``FIX_4_4``                  ``ExecInst`` (tag 18) value ``6``,
                             "Participate don't initiate". Tag 18 is a
                             MultipleValueString, so ``6`` may appear alongside
                             other space-delimited values.
===========================  ==================================================

Rejection semantics differ and the caller must handle both: Binance spot rejects
the request synchronously, Binance USD-M futures accepts it and then emits an
``EXPIRED`` order update asynchronously, and Bybit cancels the order. A submission
that returned success is therefore not evidence that an order is resting.

Interactive Brokers has no general post-only order attribute in the TWS API. The
only documented post-only behaviour is ``Order.notHeld``, which the API reference
describes as tagging orders routed to IBDARK as "post only", *for IBDARK orders
only*. Do not model IBKR as a generic post-only venue; confirm behaviour per
destination.

Limitations (documented, deliberate)
------------------------------------
- Top-of-book only. Nothing here reasons about depth, hidden liquidity, or the
  size resting at the touch, so the fill probability of a repriced order is
  unmodelled.
- Snapshot-based. The book is read once; between the snapshot and arrival the
  touch can move, and the venue - not this module - has the final word.
- Positive prices only. A negative settlement price (WTI futures, April 2020)
  would invert every derived figure, so non-positive prices are rejected.
- No cross-venue aggregation. Realized differentials accrued on one optimizer
  instance assume one fee schedule; run one venue per instance.
"""
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Set

logger = logging.getLogger(__name__)

#: Reported USD amounts are rounded once, at the reporting boundary. Accrued
#: totals are kept in full precision so rounding never compounds.
_USD_DP = 2

#: Serialisation precision for venues whose APIs take numbers as strings. Ten
#: decimal places covers every mainstream crypto quantity increment (8 dp is the
#: common maximum); a finer increment must be pre-formatted by the caller.
_STR_DP = 10


class Venue(str, Enum):
    """Venues whose post-only expression this module knows. See module docstring."""

    BINANCE_SPOT = "BINANCE_SPOT"
    BINANCE_USDM_FUTURES = "BINANCE_USDM_FUTURES"
    BYBIT_V5 = "BYBIT_V5"
    COINBASE_ADVANCED = "COINBASE_ADVANCED"
    KRAKEN_SPOT = "KRAKEN_SPOT"
    FIX_4_4 = "FIX_4_4"


class OrderSide(str, Enum):
    """
    Order side. An enum, not a string: a side that matches neither branch is the
    failure mode that matters here, because an unrecognised side skips the
    spread-crossing check and submits the marketable price unchanged.
    """

    BUY = "BUY"
    SELL = "SELL"


class CrossingPolicy(str, Enum):
    """What to do with a limit price that would trade on arrival."""

    #: Move the price to the near touch on our own side (bid for a buy, ask for a
    #: sell). The order becomes passive - and a different order: it no longer
    #: takes the liquidity the original price was reaching for.
    REPRICE_PASSIVE = "REPRICE_PASSIVE"
    #: Refuse to submit. Correct when the trade is only worth doing at the
    #: original price, and the only alternative is an explicit taker order.
    REJECT = "REJECT"


class PostOnlyStatus(str, Enum):
    """Outcome of payload preparation."""

    READY = "POST_ONLY_PAYLOAD_READY"
    REPRICED = "POST_ONLY_PAYLOAD_REPRICED"
    REJECTED_WOULD_CROSS = "POST_ONLY_REJECTED_WOULD_CROSS"
    #: bid >= ask: there is no price at the touch that does not cross.
    REJECTED_LOCKED_OR_CROSSED_BOOK = "POST_ONLY_REJECTED_LOCKED_OR_CROSSED_BOOK"


class PostOnlyOrderError(ValueError):
    """
    Raised when an order, a book snapshot, or a fee schedule is unusable.

    Subclasses ``ValueError`` so callers already catching ``ValueError`` keep
    working.
    """


def _is_finite_number(value: object) -> bool:
    """
    True for a real int/float that is neither NaN nor infinite.

    ``bool`` is excluded deliberately: ``True`` is a valid ``int`` to Python and
    would otherwise pass as a quantity of 1.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def _require_positive(label: str, value: object) -> float:
    """Validates a strictly positive finite number, or raises."""
    if not _is_finite_number(value):
        raise PostOnlyOrderError(f"{label} must be a finite number, got {value!r}.")
    number = float(value)
    if number <= 0.0:
        raise PostOnlyOrderError(f"{label} must be strictly positive, got {number}.")
    return number


def _num_str(value: float) -> str:
    """
    Serialises a number for venues that take quantities and prices as strings
    (Bybit v5, Coinbase Advanced, Kraken, FIX), without scientific notation.

    Raises when a strictly positive value would serialise to ``"0"``: silently
    sending a zero quantity is worse than refusing to send anything.
    """
    text = f"{value:.{_STR_DP}f}".rstrip("0")
    text = text[:-1] if text.endswith(".") else text
    if float(text) == 0.0:
        raise PostOnlyOrderError(
            f"{value!r} underflows the {_STR_DP}-decimal string serialisation "
            f"this venue requires and would be sent as 0."
        )
    return text


def _fmt_usd(amount: float) -> str:
    """Formats a signed amount as -$1,234.56 rather than $-1,234.56."""
    return f"-${abs(amount):,.2f}" if amount < 0 else f"${amount:,.2f}"


@dataclass(frozen=True)
class FeeSchedule:
    """
    The maker and taker rates actually in force for this account, at this venue,
    at its current tier, as a fraction of notional (0.001 = 0.1% = 10 bps).

    Signed: positive charges the desk, negative credits it. There is no default -
    see the module docstring.
    """

    maker_fee_rate: float
    taker_fee_rate: float

    def __post_init__(self) -> None:
        for label, value in (
            ("maker_fee_rate", self.maker_fee_rate),
            ("taker_fee_rate", self.taker_fee_rate),
        ):
            if not _is_finite_number(value):
                raise PostOnlyOrderError(
                    f"{label} must be a finite number, got {value!r}. Rates are "
                    f"fractions of notional (0.001 = 10 bps), not percentages."
                )

    @property
    def differential_rate(self) -> float:
        """
        ``taker - maker``. Positive means posting is cheaper than crossing; zero
        means post-only changes nothing (Binance spot at the Regular tier);
        negative means posting is the more expensive side and the flag is costing
        the desk money.
        """
        return float(self.taker_fee_rate) - float(self.maker_fee_rate)


@dataclass(frozen=True)
class TopOfBook:
    """
    A single top-of-book snapshot the order is priced against.

    ``tick_size`` is optional. When supplied, a limit price that is not a whole
    multiple of it is rejected here rather than by the venue.
    """

    best_bid: float
    best_ask: float
    tick_size: Optional[float] = None

    def __post_init__(self) -> None:
        _require_positive("best_bid", self.best_bid)
        _require_positive("best_ask", self.best_ask)
        if self.tick_size is not None:
            _require_positive("tick_size", self.tick_size)

    @property
    def is_locked_or_crossed(self) -> bool:
        """
        True when ``best_bid >= best_ask``. Locked (equal) and crossed (inverted)
        books occur transiently and across consolidated feeds; neither leaves a
        price at the touch that does not cross.
        """
        return float(self.best_bid) >= float(self.best_ask)

    def is_on_tick(self, price: float) -> bool:
        """True when ``price`` lies on the tick grid (always true if unknown)."""
        if self.tick_size is None:
            return True
        tick = float(self.tick_size)
        return abs(price - round(price / tick) * tick) <= tick * 1e-6


@dataclass(frozen=True)
class PostOnlyOrderResult:
    """
    Outcome of preparing one post-only order.

    Every ``_if_filled_usd`` field is a counterfactual conditional on the order
    filling in full at ``submitted_limit_price``. Post-only does not guarantee a
    fill, so none of them is a realized amount; use
    :meth:`MakerTakerFeeOptimizer.record_maker_fill` for that.
    """

    status: PostOnlyStatus
    side: OrderSide
    requested_limit_price: float
    submitted_limit_price: Optional[float]
    order_payload: Dict[str, Any]
    repriced: bool
    estimated_maker_fee_if_filled_usd: float
    counterfactual_taker_fee_usd: float
    estimated_fee_differential_if_filled_usd: float
    spread_capture_if_filled_usd: float
    message: str
    warnings: List[str] = field(default_factory=list)

    @property
    def is_accepted(self) -> bool:
        """True when a payload was produced and is safe to submit."""
        return self.status in (PostOnlyStatus.READY, PostOnlyStatus.REPRICED)


class MakerTakerFeeOptimizer:
    """
    Prepares venue-correct post-only limit payloads and accounts for the
    maker-vs-taker fee differential.

    One instance models one venue at one fee tier. ``venue`` and ``fee_schedule``
    are both required: the payload shape depends on the venue, and there is no fee
    schedule that is safe to assume.
    """

    def __init__(self, venue: Venue, fee_schedule: FeeSchedule) -> None:
        if not isinstance(venue, Venue):
            raise PostOnlyOrderError(
                f"venue must be a Venue, got {type(venue).__name__} ({venue!r}). "
                f"Known venues: {', '.join(v.value for v in Venue)}."
            )
        if not isinstance(fee_schedule, FeeSchedule):
            raise PostOnlyOrderError(
                f"fee_schedule must be a FeeSchedule, got "
                f"{type(fee_schedule).__name__}."
            )
        self.venue = venue
        self.fee_schedule = fee_schedule
        self._realized_fee_differential_usd = 0.0
        self._recorded_fill_count = 0
        self._recorded_fill_ids: Set[str] = set()

    @property
    def realized_fee_differential_usd(self) -> float:
        """
        Fee differential accrued from quantity the venue actually filled, via
        :meth:`record_maker_fill`. Preparing a payload never moves this.
        """
        return self._realized_fee_differential_usd

    @property
    def recorded_fill_count(self) -> int:
        """Number of fills accrued into :attr:`realized_fee_differential_usd`."""
        return self._recorded_fill_count

    def prepare_post_only_payload(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        limit_price: float,
        book: TopOfBook,
        crossing_policy: CrossingPolicy = CrossingPolicy.REPRICE_PASSIVE,
        venue_params: Optional[Mapping[str, Any]] = None,
    ) -> PostOnlyOrderResult:
        """
        Validates the order, resolves a non-marketable price, and builds the
        venue's post-only payload.

        A price is marketable when a buy limit is at or above ``best_ask``, or a
        sell limit is at or below ``best_bid``; both bounds are inclusive, because
        a limit exactly equal to the opposite touch trades against it.

        ``venue_params`` is merged into the payload for venue-specific fields
        (Bybit's mandatory ``category``, a client order id, an account id). It may
        not overwrite any key that carries the post-only instruction.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise PostOnlyOrderError("symbol must be a non-empty string.")
        if not isinstance(side, OrderSide):
            raise PostOnlyOrderError(
                f"side must be an OrderSide, got {type(side).__name__} ({side!r}). "
                f"A free-text side matching neither BUY nor SELL would skip the "
                f"spread-crossing check entirely."
            )
        if not isinstance(book, TopOfBook):
            raise PostOnlyOrderError(
                f"book must be a TopOfBook, got {type(book).__name__}."
            )
        if not isinstance(crossing_policy, CrossingPolicy):
            raise PostOnlyOrderError(
                f"crossing_policy must be a CrossingPolicy, got {crossing_policy!r}."
            )
        quantity = _require_positive("quantity", quantity)
        limit_price = _require_positive("limit_price", limit_price)

        warnings: List[str] = []
        if book.is_locked_or_crossed:
            warnings.append(
                f"Book is locked or crossed (bid {book.best_bid} >= ask "
                f"{book.best_ask}); the snapshot may be stale or consolidated "
                f"across venues."
            )
        if not book.is_on_tick(limit_price):
            raise PostOnlyOrderError(
                f"limit_price {limit_price} is not a multiple of tick_size "
                f"{book.tick_size}; the venue would reject it."
            )

        differential_rate = self.fee_schedule.differential_rate
        if differential_rate == 0.0:
            warnings.append(
                "Maker and taker rates are equal at this tier, so post-only "
                "changes the fee bill by zero and only changes fill behaviour."
            )
        elif differential_rate < 0.0:
            warnings.append(
                f"Taker rate {self.fee_schedule.taker_fee_rate} is below maker "
                f"rate {self.fee_schedule.maker_fee_rate} (inverted schedule): "
                f"posting is the MORE expensive side here."
            )

        best_bid = float(book.best_bid)
        best_ask = float(book.best_ask)
        opposite_touch = best_ask if side is OrderSide.BUY else best_bid
        near_touch = best_bid if side is OrderSide.BUY else best_ask

        is_marketable = (
            limit_price >= best_ask if side is OrderSide.BUY else limit_price <= best_bid
        )

        submitted_price = limit_price
        repriced = False
        if is_marketable:
            if crossing_policy is CrossingPolicy.REJECT:
                return self._rejected(
                    PostOnlyStatus.REJECTED_WOULD_CROSS,
                    side,
                    limit_price,
                    warnings,
                    f"Post-only {side.value} not submitted: limit "
                    f"{_fmt_usd(limit_price)} would cross the opposite touch "
                    f"{_fmt_usd(opposite_touch)} and crossing_policy is REJECT.",
                )
            still_marketable = (
                near_touch >= best_ask if side is OrderSide.BUY else near_touch <= best_bid
            )
            if still_marketable:
                # Locked/crossed book: the near touch is itself marketable, so
                # there is no passive price to reprice to. Submitting anyway
                # produces a venue-side cancel, not a resting order.
                return self._rejected(
                    PostOnlyStatus.REJECTED_LOCKED_OR_CROSSED_BOOK,
                    side,
                    limit_price,
                    warnings,
                    f"Post-only {side.value} not submitted: book is locked or "
                    f"crossed (bid {best_bid} >= ask {best_ask}), so the near "
                    f"touch would cross as well.",
                )
            submitted_price = near_touch
            repriced = True
            logger.info(
                "Post-only %s limit %s is marketable against touch %s; repriced "
                "passively to %s. This is a different order: it no longer takes "
                "the liquidity the original price reached for.",
                side.value,
                limit_price,
                opposite_touch,
                submitted_price,
            )

        # The taker counterfactual is priced at the touch the order would have
        # crossed against, not at our own limit: crossing a buy pays the ask.
        maker_fee = quantity * submitted_price * float(self.fee_schedule.maker_fee_rate)
        taker_fee = quantity * opposite_touch * float(self.fee_schedule.taker_fee_rate)
        spread_capture = quantity * (best_ask - best_bid) if repriced else 0.0

        payload = self._build_payload(
            symbol=symbol.strip(),
            side=side,
            quantity=quantity,
            price=submitted_price,
            venue_params=venue_params,
        )

        reprice_note = (
            f" (repriced from {_fmt_usd(limit_price)})" if repriced else ""
        )
        return PostOnlyOrderResult(
            status=PostOnlyStatus.REPRICED if repriced else PostOnlyStatus.READY,
            side=side,
            requested_limit_price=limit_price,
            submitted_limit_price=submitted_price,
            order_payload=payload,
            repriced=repriced,
            estimated_maker_fee_if_filled_usd=round(maker_fee, _USD_DP),
            counterfactual_taker_fee_usd=round(taker_fee, _USD_DP),
            estimated_fee_differential_if_filled_usd=round(taker_fee - maker_fee, _USD_DP),
            spread_capture_if_filled_usd=round(spread_capture, _USD_DP),
            message=(
                f"Post-only {side.value} payload prepared for {self.venue.value} at "
                f"{_fmt_usd(submitted_price)}{reprice_note}. Fee differential if "
                f"filled: {_fmt_usd(taker_fee - maker_fee)} - conditional on a "
                f"fill, which post-only does not guarantee."
            ),
            warnings=warnings,
        )

    def record_maker_fill(
        self,
        filled_quantity: float,
        fill_price: float,
        taker_reference_price: Optional[float] = None,
        fill_id: Optional[str] = None,
    ) -> float:
        """
        Accrues the realized fee differential for quantity the venue actually
        filled as a maker, and returns this fill's contribution.

        ``taker_reference_price`` is the price the order would have crossed at had
        it been sent aggressively. Supply the touch captured at decision time; it
        defaults to ``fill_price``, which understates the taker cost whenever the
        spread was non-zero, so the default is a lower bound and not an estimate.

        ``fill_id`` is the venue's execution id. Supply it: overlapping paginated
        fill fetches are the ordinary way one fill arrives twice, and a
        double-counted fill inflates the realized total with nothing in the output
        to show it. A repeated id is rejected. Without an id no deduplication is
        possible, and the caller owns that risk.
        """
        if fill_id is not None:
            if not isinstance(fill_id, str) or not fill_id.strip():
                raise PostOnlyOrderError("fill_id must be a non-empty string or None.")
            if fill_id in self._recorded_fill_ids:
                raise PostOnlyOrderError(
                    f"fill_id {fill_id!r} has already been recorded; recording it "
                    f"again would double-count the fill."
                )
        filled_quantity = _require_positive("filled_quantity", filled_quantity)
        fill_price = _require_positive("fill_price", fill_price)
        reference = (
            fill_price
            if taker_reference_price is None
            else _require_positive("taker_reference_price", taker_reference_price)
        )

        maker_fee = filled_quantity * fill_price * float(self.fee_schedule.maker_fee_rate)
        taker_fee = filled_quantity * reference * float(self.fee_schedule.taker_fee_rate)
        differential = taker_fee - maker_fee

        self._realized_fee_differential_usd += differential
        self._recorded_fill_count += 1
        if fill_id is not None:
            self._recorded_fill_ids.add(fill_id)
        logger.debug(
            "Recorded maker fill %s @ %s: realized fee differential %s",
            filled_quantity,
            fill_price,
            _fmt_usd(differential),
        )
        return differential

    # -- internals ---------------------------------------------------------

    def _rejected(
        self,
        status: PostOnlyStatus,
        side: OrderSide,
        requested_limit_price: float,
        warnings: List[str],
        message: str,
    ) -> PostOnlyOrderResult:
        """
        Builds a no-payload result. No fee figure is reported for an order that is
        not being submitted.
        """
        logger.info("%s", message)
        return PostOnlyOrderResult(
            status=status,
            side=side,
            requested_limit_price=requested_limit_price,
            submitted_limit_price=None,
            order_payload={},
            repriced=False,
            estimated_maker_fee_if_filled_usd=0.0,
            counterfactual_taker_fee_usd=0.0,
            estimated_fee_differential_if_filled_usd=0.0,
            spread_capture_if_filled_usd=0.0,
            message=message,
            warnings=list(warnings),
        )

    def _build_payload(
        self,
        symbol: str,
        side: OrderSide,
        quantity: float,
        price: float,
        venue_params: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        """
        Builds the payload in the form the named venue documents, then merges
        ``venue_params`` - refusing any key that carries the post-only
        instruction, the price, or the quantity. Overwriting the flag silently
        converts the order into a plain limit order that can cross; overwriting
        the price or quantity makes the payload disagree with the validated,
        crossing-checked result that was returned alongside it.
        """
        venue = self.venue
        if venue is Venue.BINANCE_SPOT:
            payload: Dict[str, Any] = {
                "symbol": symbol,
                "side": side.value,
                "type": "LIMIT_MAKER",
                "quantity": quantity,
                "price": price,
            }
            protected = ("type", "quantity", "price")
        elif venue is Venue.BINANCE_USDM_FUTURES:
            payload = {
                "symbol": symbol,
                "side": side.value,
                "type": "LIMIT",
                "quantity": quantity,
                "price": price,
                "timeInForce": "GTX",
            }
            protected = ("type", "timeInForce", "quantity", "price")
        elif venue is Venue.BYBIT_V5:
            payload = {
                "symbol": symbol,
                "side": side.value.capitalize(),
                "orderType": "Limit",
                "qty": _num_str(quantity),
                "price": _num_str(price),
                "timeInForce": "PostOnly",
            }
            protected = ("orderType", "timeInForce", "qty", "price")
        elif venue is Venue.COINBASE_ADVANCED:
            payload = {
                "product_id": symbol,
                "side": side.value,
                "order_configuration": {
                    "limit_limit_gtc": {
                        "base_size": _num_str(quantity),
                        "limit_price": _num_str(price),
                        "post_only": True,
                    }
                },
            }
            protected = ("order_configuration",)
        elif venue is Venue.KRAKEN_SPOT:
            payload = {
                "pair": symbol,
                "type": side.value.lower(),
                "ordertype": "limit",
                "volume": _num_str(quantity),
                "price": _num_str(price),
                "oflags": "post",
            }
            protected = ("ordertype", "oflags", "volume", "price")
        elif venue is Venue.FIX_4_4:
            # Tags: 55 Symbol, 54 Side (1=Buy, 2=Sell), 40 OrdType (2=Limit),
            # 38 OrderQty, 44 Price, 59 TimeInForce (0=Day),
            # 18 ExecInst (6=Participate don't initiate).
            payload = {
                "55": symbol,
                "54": "1" if side is OrderSide.BUY else "2",
                "40": "2",
                "38": _num_str(quantity),
                "44": _num_str(price),
                "59": "0",
                "18": "6",
            }
            protected = ("40", "18", "38", "44")
        else:  # pragma: no cover - Venue is exhaustive; guards a future member.
            raise PostOnlyOrderError(f"No payload builder for venue {venue!r}.")

        if venue is Venue.BYBIT_V5 and not (venue_params or {}).get("category"):
            raise PostOnlyOrderError(
                "BYBIT_V5 requires 'category' (spot, linear, inverse or option) in "
                "venue_params; the API rejects a create-order request without it."
            )

        if venue_params:
            clashes = sorted(set(venue_params) & set(protected))
            if clashes:
                raise PostOnlyOrderError(
                    f"venue_params may not override the post-only instruction, "
                    f"price or quantity for {venue.value}: {clashes}. Overwriting "
                    f"the flag submits a plain limit order that can cross and be "
                    f"billed as a taker; overwriting the price or quantity "
                    f"bypasses the crossing check that produced this payload."
                )
            payload.update(venue_params)
        return payload

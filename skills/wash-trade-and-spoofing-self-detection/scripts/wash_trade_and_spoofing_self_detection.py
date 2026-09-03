"""Self-surveillance for wash trades / self-matches and layering-spoofing patterns.

This module screens a firm's *own* order and execution stream for the two
market-integrity patterns a regulator would look for, so the firm sees them
first. It emits **alerts requiring human review**, never violation findings:

* A wash sale under CEA section 4c(a)(1),(2)(A) (7 U.S.C. 6c(a)(1),(2)(A))
  turns on intent. CME Rule 534 applies a "knew or should have known"
  standard; FINRA Rule 5210.02 treats self-trades from *unrelated*
  algorithms as generally bona fide and targets a *pattern or practice*.
* Spoofing under CEA section 4c(a)(5)(C) requires scienter -- the CFTC's
  Antidisruptive Practices interpretive guidance (78 FR 31890, 28 May 2013)
  states that reckless conduct does not violate the provision. FINRA Rule
  5210.03 likewise requires "a frequent pattern".

Intent is not observable in an order stream. Every output of this module is
an indicator for a compliance analyst, mirroring the human-analysis
requirement that EU MAR's STOR RTS imposes and that MiFID II RTS 6
Article 13 assumes when it requires an automated surveillance system to
generate "alerts and reports".

Detection parameters are calibrated heuristics. No regulator prescribes a
cancellation ratio, an order lifespan or a size ratio; see
``references/standards.md``.
"""

from __future__ import annotations

import datetime
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Deque, Dict, List, Mapping, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

#: Quantities are floats; a residual below this counts as fully filled.
_QTY_EPS = 1e-12

__all__ = [
    "OrderAction",
    "OrderSide",
    "AlertType",
    "ViolationType",
    "PatternShape",
    "SurveillanceError",
    "OrderEvent",
    "SurveillanceAlert",
    "SurveillanceViolation",
    "TraderMetrics",
    "WashTradeAndSpoofingDetectionEngine",
]


class OrderAction(Enum):
    PLACE = "PLACE"
    CANCEL = "CANCEL"
    FILL = "FILL"


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class AlertType(Enum):
    """Indicator raised, named for the pattern -- not for a legal conclusion."""

    WASH_TRADE = "WASH_TRADE"                        # Self-match: own buy and own sell would cross
    SPOOFING_LAYERING = "SPOOFING_LAYERING"          # Opposite-side execution surrounded by cancellations
    HIGH_CANCELLATION_RATIO = "HIGH_CANCELLATION"    # Cancel/place ratio above the configured heuristic


class PatternShape(Enum):
    """Which layering shape produced the alert."""

    CANCEL_AFTER_FILL = "CANCEL_AFTER_FILL"    # FINRA 5210.03 Type 1: cancels follow the opposite-side execution
    CANCEL_BEFORE_FILL = "CANCEL_BEFORE_FILL"  # Weaker shape: cancels immediately precede the execution


#: Backwards-compatible aliases. The engine reports indicators, so the
#: preferred names are ``AlertType`` / ``SurveillanceAlert``.
ViolationType = AlertType


class SurveillanceError(Exception):
    """Raised when an event cannot be screened (bad input, duplicate, bad clock)."""


def _is_finite_number(value: object) -> bool:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (OverflowError, ValueError):
        # An int too large to convert is not a usable price or quantity.
        return False


def _utc_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


@dataclass
class OrderEvent:
    """One order-lifecycle event.

    :param price: Limit price. ``None`` means unpriced (market order), which
        crosses every price level on the opposite side, and is also the
        correct value for a CANCEL that carries no price.
    :param strategy_id: Originating algorithm / trading desk. Used for the
        FINRA Rule 5210.02 relatedness test. ``None`` means unknown, which
        is treated conservatively as *related*.
    :param timestamp: Must be timezone-aware. MiFID II RTS 25
        (Delegated Regulation (EU) 2017/574) bounds business-clock divergence
        from UTC at 100 microseconds for HFT; sub-second lifespan logic is
        only meaningful on a synchronised, unambiguous clock, and a naive
        datetime has no defined offset.
    """

    event_id: str
    order_id: str
    trader_id: str
    account_id: str
    symbol: str
    side: OrderSide
    quantity: float
    action: OrderAction
    price: Optional[float] = None
    strategy_id: Optional[str] = None
    timestamp: datetime.datetime = field(default_factory=_utc_now)


@dataclass
class SurveillanceAlert:
    """An indicator for compliance review. Never a determination of a breach."""

    alert_id: str
    beneficial_owner_id: str
    trader_id: str
    symbol: str
    alert_type: AlertType
    severity: str                                # "MEDIUM", "HIGH", "CRITICAL" -- review priority
    description: str
    indicator_reference: str                     # The rule/indicator the pattern maps to
    related_event_ids: List[str] = field(default_factory=list)
    pattern_shape: Optional[PatternShape] = None
    requires_human_review: bool = True
    timestamp: datetime.datetime = field(default_factory=_utc_now)


#: Backwards-compatible alias; see :class:`SurveillanceAlert`.
SurveillanceViolation = SurveillanceAlert


@dataclass
class TraderMetrics:
    """Counters for one beneficial owner.

    ``cancellation_ratio_pct`` is cancels / placements. It is **not** the
    MiFID II RTS 9 order-to-trade ratio: RTS 9
    (Delegated Regulation (EU) 2017/566) places the OTR duty on the trading
    venue, calculated per member and per instrument on both volumes and
    numbers of orders.
    """

    trader_id: str
    total_orders_placed: int
    total_orders_canceled: int
    total_orders_filled: int
    cancellation_ratio_pct: float
    avg_order_lifespan_ms: float
    unmatched_cancels: int = 0


@dataclass
class _OrderState:
    """Resting state of one order id."""

    order_id: str
    owner_id: str
    trader_id: str
    strategy_id: Optional[str]
    symbol: str
    side: OrderSide
    price: Optional[float]
    quantity: float
    placed_at: datetime.datetime
    place_event_id: str


@dataclass
class _OwnerCounters:
    placed: int = 0
    canceled: int = 0
    filled: int = 0
    lifespan_sum_ms: float = 0.0
    lifespan_count: int = 0
    unmatched_cancels: int = 0


@dataclass
class _LayeringContext:
    """An opposite-side execution, kept open so later cancellations can attach.

    FINRA Rule 5210.03 Type 1 describes the cancellations as happening
    *following* the execution, so the fill cannot be scored at the moment it
    arrives -- the evidence has not been produced yet.
    """

    owner_id: str
    trader_id: str
    symbol: str
    fill_side: OrderSide
    fill_qty: float
    fill_event_id: str
    fill_time: datetime.datetime
    layered_order_ids: Set[str]
    canceled_qty: float = 0.0
    canceled_event_ids: List[str] = field(default_factory=list)
    alerted: bool = False


class WashTradeAndSpoofingDetectionEngine:
    """Streaming self-surveillance over a firm's own order events.

    Screens for two patterns and one hygiene metric:

    1. **Self-match / wash trade** -- an incoming order that would cross the
       firm's own resting order on the opposite side of the same instrument
       under the same beneficial owner.
    2. **Layering / spoofing** -- an execution on one side accompanied by the
       withdrawal of materially larger same-owner resting size on the other.
    3. **Cancellation ratio** -- a calibrated hygiene threshold.

    All three emit :class:`SurveillanceAlert` objects flagged
    ``requires_human_review=True``.
    """

    def __init__(
        self,
        beneficial_owner_map: Optional[Mapping[str, str]] = None,
        wash_trade_window_seconds: Optional[float] = None,
        spoofing_lifespan_threshold_ms: float = 1000.0,
        cancellation_ratio_threshold_pct: float = 90.0,
        min_orders_for_cancel_ratio: int = 10,
        layering_size_ratio: float = 3.0,
        min_layered_orders: int = 2,
        require_related_origin: bool = True,
        price_tolerance: float = 1e-9,
        max_history_per_owner: int = 10_000,
        max_tracked_event_ids: int = 1_000_000,
    ) -> None:
        """
        :param beneficial_owner_map: ``account_id`` (or ``trader_id``) ->
            beneficial owner id. Required wherever sub-accounts or multiple
            desks trade under one owner: without it, ownership is a string
            comparison and same-owner/different-account self-crossing is
            invisible. Ownership is never inferred.
        :param wash_trade_window_seconds: Optional maximum age of a resting
            order to consider. ``None`` (default) considers every resting
            order, which is the correct self-match semantic -- an order that
            has rested for an hour still self-matches when the firm's own
            aggressor reaches it. Set a value only to model a narrow
            "matched trade" pattern window, and understand that it creates
            false negatives.
        :param spoofing_lifespan_threshold_ms: Window on either side of an
            execution within which a same-owner opposite-side cancellation is
            attached to it, and the lifespan below which a cancelled order is
            counted as fast.
        :param cancellation_ratio_threshold_pct: Cancels/placements percentage
            at or above which the hygiene alert fires. A heuristic; no
            regulator prescribes a number.
        :param min_orders_for_cancel_ratio: Minimum placements before the
            ratio is treated as evidence.
        :param layering_size_ratio: Withdrawn opposite-side quantity must be
            at least this multiple of the executed quantity. Without a size
            test, a two-sided market maker refreshing quotes trips the
            detector on every fill.
        :param min_layered_orders: Minimum number of distinct withdrawn
            orders. FINRA Rule 5210.03 Type 1 describes *multiple* limit
            orders on the one side.
        :param require_related_origin: When ``True``, a self-match between two
            *known and different* ``strategy_id`` values is reported at MEDIUM
            rather than CRITICAL, reflecting FINRA Rule 5210.02 (self-trades
            from unrelated algorithms are generally bona fide). It is never
            suppressed: CME Rule 534 carries no such carve-out, and an
            unknown ``strategy_id`` is treated as related.
        :param price_tolerance: Absolute tolerance for the crossing test.
            Feed venue-tick-aligned prices from the book's own source.
        :param max_history_per_owner: Ring-buffer bound on retained events per
            owner. Counters are incremental, so bounding history does not
            distort the metrics.
        :param max_tracked_event_ids: Bound on the duplicate-detection window.
            Duplicate rejection is therefore *windowed*, not absolute: a replay
            of an event older than this many events is no longer recognised.
            An unbounded set would grow for the life of the process.
        :raises SurveillanceError: on a non-sensical parameter.
        """
        if wash_trade_window_seconds is not None and (
            not _is_finite_number(wash_trade_window_seconds) or wash_trade_window_seconds < 0
        ):
            raise SurveillanceError("wash_trade_window_seconds must be None or a finite value >= 0.")
        if not _is_finite_number(spoofing_lifespan_threshold_ms) or spoofing_lifespan_threshold_ms <= 0:
            raise SurveillanceError("spoofing_lifespan_threshold_ms must be a finite value > 0.")
        if not _is_finite_number(cancellation_ratio_threshold_pct) or not (
            0 < cancellation_ratio_threshold_pct <= 100
        ):
            raise SurveillanceError("cancellation_ratio_threshold_pct must be in (0, 100].")
        if not isinstance(min_orders_for_cancel_ratio, int) or min_orders_for_cancel_ratio < 1:
            raise SurveillanceError("min_orders_for_cancel_ratio must be an integer >= 1.")
        if not _is_finite_number(layering_size_ratio) or layering_size_ratio <= 0:
            raise SurveillanceError("layering_size_ratio must be a finite value > 0.")
        if not isinstance(min_layered_orders, int) or min_layered_orders < 1:
            raise SurveillanceError("min_layered_orders must be an integer >= 1.")
        if not _is_finite_number(price_tolerance) or price_tolerance < 0:
            raise SurveillanceError("price_tolerance must be a finite value >= 0.")
        if not isinstance(max_history_per_owner, int) or max_history_per_owner < 1:
            raise SurveillanceError("max_history_per_owner must be an integer >= 1.")
        if not isinstance(max_tracked_event_ids, int) or max_tracked_event_ids < 1:
            raise SurveillanceError("max_tracked_event_ids must be an integer >= 1.")

        self.beneficial_owner_map: Dict[str, str] = dict(beneficial_owner_map or {})
        self.wash_trade_window_seconds = wash_trade_window_seconds
        self.spoofing_lifespan_threshold_ms = float(spoofing_lifespan_threshold_ms)
        self.cancellation_ratio_threshold_pct = float(cancellation_ratio_threshold_pct)
        self.min_orders_for_cancel_ratio = min_orders_for_cancel_ratio
        self.layering_size_ratio = float(layering_size_ratio)
        self.min_layered_orders = min_layered_orders
        self.require_related_origin = require_related_origin
        self.price_tolerance = float(price_tolerance)
        self.max_history_per_owner = max_history_per_owner
        self.max_tracked_event_ids = max_tracked_event_ids

        # order_id -> resting state (removed on CANCEL / full FILL)
        self._orders: Dict[str, _OrderState] = {}
        # (owner_id, symbol) -> resting order ids, so a self-cross scan touches
        # only the firm's own book in that instrument rather than every order.
        self._resting: Dict[Tuple[str, str], Set[str]] = {}
        self._counters: Dict[str, _OwnerCounters] = {}
        self._history: Dict[str, Deque[OrderEvent]] = {}
        self._owner_aliases: Dict[str, str] = {}
        self._layering_contexts: List[_LayeringContext] = []
        # Bounded duplicate-detection window: the deque evicts, the set answers.
        self._seen_event_ids: Set[str] = set()
        self._seen_event_order: Deque[str] = deque()
        self._cancel_ratio_alerted: Set[str] = set()

        self.alerts: List[SurveillanceAlert] = []

        logger.info(
            "Initialised self-surveillance engine "
            "(cancel_ratio>=%.1f%%, layering_size_ratio=%.1fx, window=%.0fms, owners_mapped=%d)",
            self.cancellation_ratio_threshold_pct,
            self.layering_size_ratio,
            self.spoofing_lifespan_threshold_ms,
            len(self.beneficial_owner_map),
        )

    # ------------------------------------------------------------------ #
    # Backwards-compatible view of the alert log.
    # ------------------------------------------------------------------ #
    @property
    def violations(self) -> List[SurveillanceAlert]:
        """Deprecated alias for :attr:`alerts`; the engine emits indicators."""
        return self.alerts

    # ------------------------------------------------------------------ #
    # Ownership and validation
    # ------------------------------------------------------------------ #
    def resolve_beneficial_owner(self, trader_id: str, account_id: str) -> str:
        """Resolve an event to its beneficial owner.

        Account id is consulted first because beneficial ownership attaches to
        the account, then trader id, then the account id itself as the
        fallback identity. Ownership is only ever *supplied*: the engine
        cannot discover that two accounts share an owner.
        """
        if account_id in self.beneficial_owner_map:
            return self.beneficial_owner_map[account_id]
        if trader_id in self.beneficial_owner_map:
            return self.beneficial_owner_map[trader_id]
        return account_id

    def _validate(self, event: OrderEvent) -> None:
        if not isinstance(event, OrderEvent):
            raise SurveillanceError("event must be an OrderEvent.")
        if not isinstance(event.action, OrderAction):
            raise SurveillanceError("event.action must be an OrderAction.")
        if not isinstance(event.side, OrderSide):
            raise SurveillanceError("event.side must be an OrderSide.")
        for name in ("event_id", "order_id", "trader_id", "account_id", "symbol"):
            value = getattr(event, name)
            if not isinstance(value, str) or not value.strip():
                raise SurveillanceError(f"event.{name} must be a non-empty string.")
        if not _is_finite_number(event.quantity) or event.quantity <= 0:
            raise SurveillanceError("event.quantity must be a finite value > 0.")
        # A market order and a bare cancel legitimately carry no price; a
        # present price must still be a real, positive number.
        if event.price is not None and (not _is_finite_number(event.price) or event.price <= 0):
            raise SurveillanceError("event.price must be None or a finite value > 0.")
        if event.action == OrderAction.PLACE and event.price is None:
            logger.debug("Unpriced PLACE [%s]: treated as marketable against every own level.", event.order_id)
        if not isinstance(event.timestamp, datetime.datetime):
            raise SurveillanceError("event.timestamp must be a datetime.")
        if event.timestamp.tzinfo is None or event.timestamp.tzinfo.utcoffset(event.timestamp) is None:
            raise SurveillanceError(
                "event.timestamp must be timezone-aware. A naive timestamp has no defined "
                "offset, and sub-second lifespan logic on an ambiguous clock is not defensible "
                "(cf. MiFID II RTS 25 business-clock accuracy)."
            )
        if event.event_id in self._seen_event_ids:
            raise SurveillanceError(
                f"Duplicate event_id '{event.event_id}'. A replayed event inflates the "
                "cancellation ratio and the withdrawn-size test, so it is an error, not a warning."
            )

    def _remember_event_id(self, event_id: str) -> None:
        self._seen_event_ids.add(event_id)
        self._seen_event_order.append(event_id)
        while len(self._seen_event_order) > self.max_tracked_event_ids:
            self._seen_event_ids.discard(self._seen_event_order.popleft())

    # ------------------------------------------------------------------ #
    # Ingestion
    # ------------------------------------------------------------------ #
    def ingest_order_event(self, event: OrderEvent) -> List[SurveillanceAlert]:
        """Screen one order event and return any alerts it raised.

        :raises SurveillanceError: on invalid input, a naive timestamp, or a
            duplicate ``event_id``.
        """
        self._validate(event)
        self._remember_event_id(event.event_id)

        owner_id = self.resolve_beneficial_owner(event.trader_id, event.account_id)
        self._owner_aliases[event.account_id] = owner_id
        self._owner_aliases.setdefault(event.trader_id, owner_id)

        history = self._history.setdefault(owner_id, deque(maxlen=self.max_history_per_owner))
        history.append(event)
        counters = self._counters.setdefault(owner_id, _OwnerCounters())

        alerts: List[SurveillanceAlert] = []

        if event.action == OrderAction.PLACE:
            # Screen against the resting book *before* adding the incoming
            # order, so an order can never be matched against itself.
            wash_alert = self.check_wash_trade_self_cross(event)
            if wash_alert:
                alerts.append(wash_alert)
            self._add_resting(event, owner_id)
            counters.placed += 1

        elif event.action == OrderAction.FILL:
            counters.filled += 1
            spoof_alert = self.check_spoofing_pattern_on_fill(event)
            if spoof_alert:
                alerts.append(spoof_alert)
            self._remove_resting(event.order_id, filled_qty=event.quantity)

        elif event.action == OrderAction.CANCEL:
            counters.canceled += 1
            state = self._orders.get(event.order_id)
            if state is not None:
                lifespan_ms = (event.timestamp - state.placed_at).total_seconds() * 1000.0
                if lifespan_ms < 0:
                    # The cancel predates its own placement: a clock or sequencing
                    # defect. Averaging it in would drag the mean towards zero and
                    # manufacture the very "fast cancellation" signal being measured.
                    counters.unmatched_cancels += 1
                    logger.warning(
                        "Negative order lifespan for [%s] (%.1fms): cancel precedes placement, "
                        "excluded from the average. Check business-clock synchronisation.",
                        event.order_id,
                        lifespan_ms,
                    )
                else:
                    counters.lifespan_sum_ms += lifespan_ms
                    counters.lifespan_count += 1
                    if lifespan_ms < self.spoofing_lifespan_threshold_ms:
                        logger.info(
                            "Fast cancellation [%s] lifespan=%.1fms (< %.0fms)",
                            event.order_id,
                            lifespan_ms,
                            self.spoofing_lifespan_threshold_ms,
                        )
            else:
                # The PLACE fell outside the retained window or was never seen.
                # Excluding it under-reports rather than fabricating a lifespan.
                counters.unmatched_cancels += 1

            layering_alert = self.check_layering_on_cancel(event)
            if layering_alert:
                alerts.append(layering_alert)
            self._remove_resting(event.order_id)

        ratio_alert = self._check_cancellation_ratio(event, owner_id, counters)
        if ratio_alert:
            alerts.append(ratio_alert)

        self._prune_layering_contexts(event.timestamp)
        self.alerts.extend(alerts)
        return alerts

    # ------------------------------------------------------------------ #
    # Resting-book bookkeeping
    # ------------------------------------------------------------------ #
    def _add_resting(self, event: OrderEvent, owner_id: str) -> None:
        if event.order_id in self._orders:
            raise SurveillanceError(
                f"Order id '{event.order_id}' is already resting. Re-using a live order id "
                "makes the audit trail ambiguous about which order was cancelled or filled."
            )
        state = _OrderState(
            order_id=event.order_id,
            owner_id=owner_id,
            trader_id=event.trader_id,
            strategy_id=event.strategy_id,
            symbol=event.symbol,
            side=event.side,
            price=event.price,
            quantity=float(event.quantity),
            placed_at=event.timestamp,
            place_event_id=event.event_id,
        )
        self._orders[event.order_id] = state
        self._resting.setdefault((owner_id, event.symbol), set()).add(event.order_id)

    def _remove_resting(self, order_id: str, filled_qty: Optional[float] = None) -> None:
        state = self._orders.get(order_id)
        if state is None:
            return
        if filled_qty is not None:
            # A partial fill leaves the remainder working and still capable of
            # self-matching; only a fully filled order leaves the book.
            state.quantity -= float(filled_qty)
            if state.quantity > _QTY_EPS:
                return
        self._orders.pop(order_id, None)
        key = (state.owner_id, state.symbol)
        resting = self._resting.get(key)
        if resting is not None:
            resting.discard(order_id)
            if not resting:
                self._resting.pop(key, None)

    def _crosses(self, incoming_side: OrderSide, incoming_price: Optional[float], resting_price: Optional[float]) -> bool:
        """Would the incoming order reach the resting price?

        An unpriced order on either side reaches every level. A priced buy
        reaches a resting sell at or below its limit; a priced sell reaches a
        resting buy at or above its limit. Equal prices cross -- that is the
        ordinary self-match -- but so does a *better* resting price, which an
        exact-equality test misses entirely.
        """
        if incoming_price is None or resting_price is None:
            return True
        if incoming_side == OrderSide.BUY:
            return incoming_price >= resting_price - self.price_tolerance
        return incoming_price <= resting_price + self.price_tolerance

    # ------------------------------------------------------------------ #
    # Detector 1 -- self-match / wash trade
    # ------------------------------------------------------------------ #
    def check_wash_trade_self_cross(self, event: OrderEvent) -> Optional[SurveillanceAlert]:
        """Detect an incoming order that would cross the owner's own resting order.

        Scans only the same beneficial owner's resting orders in the same
        instrument. Returns the alert for the resting order the incoming order
        would reach first (best price for the aggressor, then earliest
        placement), so repeat runs over the same stream produce the same alert.
        """
        owner_id = self.resolve_beneficial_owner(event.trader_id, event.account_id)
        opposite = OrderSide.SELL if event.side == OrderSide.BUY else OrderSide.BUY

        candidates: List[_OrderState] = []
        for order_id in self._resting.get((owner_id, event.symbol), set()):
            state = self._orders.get(order_id)
            if state is None or state.side != opposite:
                continue
            if not self._crosses(event.side, event.price, state.price):
                continue
            if self.wash_trade_window_seconds is not None:
                age = abs((event.timestamp - state.placed_at).total_seconds())
                if age > self.wash_trade_window_seconds:
                    continue
            candidates.append(state)

        if not candidates:
            return None

        # Best price for the aggressor first: an incoming buy reaches the
        # cheapest own offer, an incoming sell the richest own bid. Unpriced
        # resting orders are reached first of all.
        def _rank(state: _OrderState) -> Tuple[float, datetime.datetime, str]:
            if state.price is None:
                price_key = -math.inf
            else:
                price_key = state.price if event.side == OrderSide.BUY else -state.price
            return (price_key, state.placed_at, state.order_id)

        match = min(candidates, key=_rank)

        related = self._is_related_origin(event.strategy_id, match.strategy_id)
        if related:
            severity = "CRITICAL"
            origin_note = (
                f"strategy '{event.strategy_id}'" if event.strategy_id else "an unidentified strategy"
            )
            relatedness = (
                f"Both orders originate from {origin_note}; FINRA Rule 5210.02 requires "
                "policies preventing a pattern or practice of self-trades from a single or "
                "related algorithm or desk."
            )
        else:
            severity = "MEDIUM"
            relatedness = (
                f"Orders originate from distinct strategies '{match.strategy_id}' and "
                f"'{event.strategy_id}'; FINRA Rule 5210.02 generally treats such self-trades "
                "as bona fide. CME Rule 534 carries no equivalent carve-out, so review anyway."
            )

        price_text = "market" if event.price is None else f"{event.price:.4f}"
        resting_text = "market" if match.price is None else f"{match.price:.4f}"
        logger.warning(
            "SELF-MATCH INDICATOR [%s] owner=%s: incoming %s @ %s would cross own resting %s @ %s [%s]",
            event.symbol,
            owner_id,
            event.side.value,
            price_text,
            match.side.value,
            resting_text,
            match.order_id,
        )
        return SurveillanceAlert(
            alert_id=f"ALT-WASH-{match.place_event_id}-{event.event_id}",
            beneficial_owner_id=owner_id,
            trader_id=event.trader_id,
            symbol=event.symbol,
            alert_type=AlertType.WASH_TRADE,
            severity=severity,
            description=(
                f"Potential self-match: incoming {event.side.value} @ {price_text} would cross the "
                f"same owner's resting {match.side.value} @ {resting_text} (order {match.order_id}). "
                f"{relatedness} Intent is not observable here -- CEA s.4c(a)(1),(2)(A) turns on it and "
                "CME Rule 534 applies a 'knew or should have known' standard."
            ),
            indicator_reference=(
                "CEA s.4c(a)(1),(2)(A) (7 U.S.C. 6c(a)); CME Rule 534; FINRA Rule 5210.02; "
                "MAR Annex I Section A (no change in beneficial ownership)"
            ),
            related_event_ids=[match.place_event_id, event.event_id],
            timestamp=event.timestamp,
        )

    def _is_related_origin(self, a: Optional[str], b: Optional[str]) -> bool:
        """Unknown origin is treated as related -- the conservative reading."""
        if not self.require_related_origin:
            return True
        if a is None or b is None:
            return True
        return a == b

    # ------------------------------------------------------------------ #
    # Detector 2 -- layering / spoofing
    # ------------------------------------------------------------------ #
    def check_spoofing_pattern_on_fill(self, fill_event: OrderEvent) -> Optional[SurveillanceAlert]:
        """Open a layering context on an execution, and score the weaker shape.

        FINRA Rule 5210.03 Type 1 places the cancellations *after* the
        opposite-side execution, so the decisive evidence does not exist when
        the fill arrives. The context opened here is settled later by
        :meth:`check_layering_on_cancel`.

        The alert this method can return covers only the weaker
        ``CANCEL_BEFORE_FILL`` shape -- opposite-side size withdrawn in the
        window immediately preceding the execution.
        """
        owner_id = self.resolve_beneficial_owner(fill_event.trader_id, fill_event.account_id)
        opposite = OrderSide.SELL if fill_event.side == OrderSide.BUY else OrderSide.BUY

        layered = {
            order_id
            for order_id in self._resting.get((owner_id, fill_event.symbol), set())
            if (state := self._orders.get(order_id)) is not None
            and state.side == opposite
            and state.order_id != fill_event.order_id
        }
        context = _LayeringContext(
            owner_id=owner_id,
            trader_id=fill_event.trader_id,
            symbol=fill_event.symbol,
            fill_side=fill_event.side,
            fill_qty=float(fill_event.quantity),
            fill_event_id=fill_event.event_id,
            fill_time=fill_event.timestamp,
            layered_order_ids=layered,
        )
        self._layering_contexts.append(context)

        prior_cancels = [
            ev
            for ev in self._history.get(owner_id, ())
            if ev.action == OrderAction.CANCEL
            and ev.side == opposite
            and ev.symbol == fill_event.symbol
            and 0.0
            <= (fill_event.timestamp - ev.timestamp).total_seconds() * 1000.0
            <= self.spoofing_lifespan_threshold_ms
        ]
        return self._score_layering(
            owner_id=owner_id,
            trader_id=fill_event.trader_id,
            symbol=fill_event.symbol,
            fill_side=fill_event.side,
            fill_qty=float(fill_event.quantity),
            fill_event_id=fill_event.event_id,
            canceled_qty=sum(float(ev.quantity) for ev in prior_cancels),
            canceled_event_ids=[ev.event_id for ev in prior_cancels],
            n_canceled=len(prior_cancels),
            shape=PatternShape.CANCEL_BEFORE_FILL,
            alert_timestamp=fill_event.timestamp,
        )

    def check_layering_on_cancel(self, cancel_event: OrderEvent) -> Optional[SurveillanceAlert]:
        """Attach a cancellation to an open execution context and score it.

        This is the FINRA Rule 5210.03 Type 1 shape: multiple limit orders on
        one side, an opposite-side order executed, and the original orders
        cancelled *following* that execution.
        """
        owner_id = self.resolve_beneficial_owner(cancel_event.trader_id, cancel_event.account_id)
        for context in self._layering_contexts:
            if context.alerted or context.owner_id != owner_id or context.symbol != cancel_event.symbol:
                continue
            if cancel_event.side == context.fill_side:
                continue
            if cancel_event.order_id not in context.layered_order_ids:
                continue
            elapsed_ms = (cancel_event.timestamp - context.fill_time).total_seconds() * 1000.0
            if not 0.0 <= elapsed_ms <= self.spoofing_lifespan_threshold_ms:
                continue

            context.layered_order_ids.discard(cancel_event.order_id)
            context.canceled_qty += float(cancel_event.quantity)
            context.canceled_event_ids.append(cancel_event.event_id)

            alert = self._score_layering(
                owner_id=owner_id,
                trader_id=context.trader_id,
                symbol=context.symbol,
                fill_side=context.fill_side,
                fill_qty=context.fill_qty,
                fill_event_id=context.fill_event_id,
                canceled_qty=context.canceled_qty,
                canceled_event_ids=list(context.canceled_event_ids),
                n_canceled=len(context.canceled_event_ids),
                shape=PatternShape.CANCEL_AFTER_FILL,
                alert_timestamp=cancel_event.timestamp,
            )
            if alert is not None:
                context.alerted = True
                return alert
        return None

    def _score_layering(
        self,
        *,
        owner_id: str,
        trader_id: str,
        symbol: str,
        fill_side: OrderSide,
        fill_qty: float,
        fill_event_id: str,
        canceled_qty: float,
        canceled_event_ids: Sequence[str],
        n_canceled: int,
        shape: PatternShape,
        alert_timestamp: datetime.datetime,
    ) -> Optional[SurveillanceAlert]:
        """Apply the count and size tests that separate layering from quoting.

        A two-sided market maker cancels opposite-side quotes around nearly
        every fill. Only a *materially larger* withdrawal across *multiple*
        orders resembles the Rule 5210.03 shape.
        """
        if n_canceled < self.min_layered_orders:
            return None
        if fill_qty <= 0 or canceled_qty < self.layering_size_ratio * fill_qty:
            return None

        opposite = OrderSide.SELL if fill_side == OrderSide.BUY else OrderSide.BUY
        severity = "HIGH" if shape == PatternShape.CANCEL_AFTER_FILL else "MEDIUM"
        logger.warning(
            "LAYERING INDICATOR [%s] owner=%s shape=%s: %s fill of %.4f alongside %.4f withdrawn "
            "across %d %s orders (%.1fx)",
            symbol,
            owner_id,
            shape.value,
            fill_side.value,
            fill_qty,
            canceled_qty,
            n_canceled,
            opposite.value,
            canceled_qty / fill_qty,
        )
        return SurveillanceAlert(
            alert_id=f"ALT-LAYER-{shape.value}-{fill_event_id}",
            beneficial_owner_id=owner_id,
            trader_id=trader_id,
            symbol=symbol,
            alert_type=AlertType.SPOOFING_LAYERING,
            severity=severity,
            description=(
                f"Potential layering ({shape.value}): {fill_side.value} execution of {fill_qty:g} "
                f"accompanied by {canceled_qty:g} withdrawn across {n_canceled} {opposite.value} "
                f"orders ({canceled_qty / fill_qty:.1f}x the executed size). Spoofing under "
                "CEA s.4c(a)(5)(C) requires scienter, and FINRA Rule 5210.03 requires a frequent "
                "pattern -- a single occurrence is an indicator, not a finding."
            ),
            indicator_reference=(
                "CEA s.4c(a)(5)(C) (7 U.S.C. 6c(a)(5)(C), Dodd-Frank s.747); CME Rule 575; "
                "FINRA Rule 5210.03; Exchange Act s.9(a)(2) and s.10(b)/Rule 10b-5; "
                "Delegated Regulation (EU) 2016/522 Annex II (layering/spoofing)"
            ),
            related_event_ids=[fill_event_id, *canceled_event_ids],
            pattern_shape=shape,
            timestamp=alert_timestamp,
        )

    def _prune_layering_contexts(self, now: datetime.datetime) -> None:
        """Drop contexts no cancellation can still attach to."""
        cutoff_ms = self.spoofing_lifespan_threshold_ms
        self._layering_contexts = [
            ctx
            for ctx in self._layering_contexts
            if not ctx.alerted
            and (now - ctx.fill_time).total_seconds() * 1000.0 <= cutoff_ms
        ]

    # ------------------------------------------------------------------ #
    # Detector 3 -- cancellation-ratio hygiene
    # ------------------------------------------------------------------ #
    def _check_cancellation_ratio(
        self, event: OrderEvent, owner_id: str, counters: _OwnerCounters
    ) -> Optional[SurveillanceAlert]:
        if owner_id in self._cancel_ratio_alerted:
            return None  # Latched: re-emitting on every subsequent event is an alert storm.
        if counters.placed < self.min_orders_for_cancel_ratio:
            return None
        ratio = counters.canceled / counters.placed * 100.0
        if ratio < self.cancellation_ratio_threshold_pct:
            return None

        self._cancel_ratio_alerted.add(owner_id)
        logger.info(
            "Cancellation-ratio threshold reached: owner=%s ratio=%.1f%% (%d/%d)",
            owner_id,
            ratio,
            counters.canceled,
            counters.placed,
        )
        return SurveillanceAlert(
            alert_id=f"ALT-CXLRATIO-{owner_id}",
            beneficial_owner_id=owner_id,
            trader_id=event.trader_id,
            symbol=event.symbol,
            alert_type=AlertType.HIGH_CANCELLATION_RATIO,
            severity="MEDIUM",
            description=(
                f"Cancellation ratio {ratio:.1f}% ({counters.canceled}/{counters.placed}) at or above "
                f"the configured {self.cancellation_ratio_threshold_pct:.1f}% heuristic. This is a "
                "calibrated hygiene threshold, not a regulatory limit: MiFID II RTS 9 "
                "(Delegated Regulation (EU) 2017/566) places the order-to-trade duty on the trading "
                "venue, per member and per instrument, on both volumes and numbers."
            ),
            indicator_reference="Calibrated heuristic; cf. Delegated Regulation (EU) 2017/566 (RTS 9)",
            related_event_ids=[event.event_id],
            timestamp=event.timestamp,
        )

    def reset_cancellation_ratio_alert(self, owner_or_alias_id: str) -> None:
        """Re-arm the latched cancellation-ratio alert for one owner."""
        owner_id = self._owner_aliases.get(owner_or_alias_id, owner_or_alias_id)
        self._cancel_ratio_alerted.discard(owner_id)

    # ------------------------------------------------------------------ #
    # Metrics
    # ------------------------------------------------------------------ #
    def get_trader_metrics(self, trader_id: str) -> TraderMetrics:
        """Counters for the beneficial owner behind ``trader_id``.

        Accepts a trader id, an account id, or an owner id: all three resolve
        to the same owner, because grouping by raw trader id would dilute a
        single manipulator's activity across the accounts it trades.
        """
        owner_id = self._owner_aliases.get(
            trader_id, self.beneficial_owner_map.get(trader_id, trader_id)
        )
        counters = self._counters.get(owner_id, _OwnerCounters())
        ratio = (counters.canceled / counters.placed * 100.0) if counters.placed else 0.0
        avg_lifespan = (
            counters.lifespan_sum_ms / counters.lifespan_count if counters.lifespan_count else 0.0
        )
        return TraderMetrics(
            trader_id=owner_id,
            total_orders_placed=counters.placed,
            total_orders_canceled=counters.canceled,
            total_orders_filled=counters.filled,
            cancellation_ratio_pct=round(ratio, 2),
            avg_order_lifespan_ms=round(avg_lifespan, 2),
            unmatched_cancels=counters.unmatched_cancels,
        )

    def get_open_orders(self, owner_or_alias_id: Optional[str] = None) -> List[str]:
        """Resting order ids, optionally narrowed to one beneficial owner."""
        if owner_or_alias_id is None:
            return sorted(self._orders)
        owner_id = self._owner_aliases.get(owner_or_alias_id, owner_or_alias_id)
        return sorted(oid for oid, st in self._orders.items() if st.owner_id == owner_id)

    def expire_orders_before(self, cutoff: datetime.datetime) -> int:
        """Drop resting orders placed before ``cutoff``; returns how many.

        A streaming engine that never hears the terminal event for an order
        would otherwise retain it forever. Call this on a session boundary
        with the venue's own end-of-day state, not on a guess.
        """
        if cutoff.tzinfo is None or cutoff.tzinfo.utcoffset(cutoff) is None:
            raise SurveillanceError("cutoff must be timezone-aware.")
        stale = [oid for oid, st in self._orders.items() if st.placed_at < cutoff]
        for order_id in stale:
            self._remove_resting(order_id)
        if stale:
            logger.info("Expired %d resting order(s) placed before %s", len(stale), cutoff.isoformat())
        return len(stale)

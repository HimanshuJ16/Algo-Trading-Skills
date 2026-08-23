import logging
import math
import threading
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Quantity epsilon: fills/residuals smaller than this are treated as complete.
QTY_EPSILON = 1e-4

STATUS_SYNCHRONIZED_OK = "SYNCHRONIZED_OK"
STATUS_SYNC_DELAY_BREACH = "SYNC_DELAY_BREACH"
STATUS_PARTIALLY_FILLED = "PARTIALLY_FILLED"
STATUS_UNHEDGED_TIMEOUT_UNWIND = "UNHEDGED_TIMEOUT_UNWIND"


@dataclass
class PrimaryFillEvent:
    strategy_id: str
    fill_id: str
    primary_symbol: str
    fill_qty: float                    # Positive for Buy, Negative for Sell
    fill_price: float
    timestamp_ms: float


@dataclass
class HedgeOrder:
    hedge_order_id: str
    primary_fill_id: str
    hedge_symbol: str
    target_hedge_qty: float            # Offset quantity calculated via hedge ratio
    side: str                          # 'BUY' or 'SELL'
    created_timestamp_ms: float
    dispatched_timestamp_ms: Optional[float] = None   # Set via mark_dispatched()
    filled_hedge_qty: float = 0.0      # Cumulative filled quantity across partial fills


@dataclass
class HedgeSynchronizationStatus:
    strategy_id: str
    primary_fill_id: str
    hedge_order_id: str
    sync_delay_ms: float
    is_sync_sla_met: bool
    hedge_status: str                  # SYNCHRONIZED_OK / SYNC_DELAY_BREACH / PARTIALLY_FILLED / UNHEDGED_TIMEOUT_UNWIND
    unhedged_exposure_qty: float
    filled_hedge_qty: float = 0.0      # Cumulative hedge quantity filled so far


class CrossAssetHedgeSynchronizer:
    """
    Execution synchronization engine for multi-leg strategies to eliminate legging risk
    and enforce sub-second hedge latency SLAs.

    Lifecycle of one hedge:
      1. ``generate_hedge_order`` on each primary fill (incremental — never wait for
         the primary order to fill completely).
      2. ``mark_dispatched`` when the OMS/gateway sends the hedge order (measures the
         dispatch-latency SLA separately from fill latency).
      3. ``process_hedge_fill`` for every hedge fill callback, including partial fills;
         the order stays pending until the cumulative fill reaches the target.
      4. ``enforce_unhedged_timeouts`` on a timer: any hedge still incomplete after
         ``unhedged_timeout_ms`` is force-flagged UNHEDGED_TIMEOUT_UNWIND and routed to
         the ``unwind_callback`` (wire this to the primary-leg emergency unwind /
         kill mechanism).

    A hedge that breaches ``unhedged_timeout_ms`` routes to ``unwind_callback``
    through EXACTLY ONE path regardless of which event observes it first — a late
    fill callback or the timer sweep. The two must not diverge: whichever arrives
    first decides only the timing, never whether the primary leg gets unwound.

    Thread safety: all public methods are guarded by a reentrant lock, so the
    gateway thread delivering fill callbacks and the timer thread driving
    ``enforce_unhedged_timeouts`` may be different threads. ``unwind_callback`` is
    invoked OUTSIDE the lock so a slow or blocking callback cannot stall fill
    processing, and so a callback that re-enters this synchronizer from another
    thread cannot deadlock. The corollary is that the callback runs after the
    hedge has already been removed from ``pending_hedges``.
    """

    def __init__(
        self,
        strategy_id: str,
        primary_symbol: str,
        hedge_symbol: str,
        hedge_ratio: float = 1.0,      # hedge units per primary unit, sign applied internally;
                                       # e.g. 0.50 delta x 100-share contract multiplier = 50.0
        max_sync_delay_ms: float = 100.0,
        unhedged_timeout_ms: float = 500.0,
        unwind_callback: Optional[Callable[[HedgeOrder], None]] = None
    ):
        if not math.isfinite(hedge_ratio) or hedge_ratio == 0.0:
            raise ValueError(f"hedge_ratio must be finite and non-zero, got {hedge_ratio!r}")
        if not math.isfinite(max_sync_delay_ms) or max_sync_delay_ms <= 0.0:
            raise ValueError(f"max_sync_delay_ms must be positive and finite, got {max_sync_delay_ms!r}")
        if not math.isfinite(unhedged_timeout_ms) or unhedged_timeout_ms < max_sync_delay_ms:
            raise ValueError(
                "unhedged_timeout_ms must be finite and >= max_sync_delay_ms "
                f"(got {unhedged_timeout_ms!r} < {max_sync_delay_ms!r}); otherwise a fill could "
                "time out before a sync-delay breach is even observable"
            )

        self.strategy_id = strategy_id
        self.primary_symbol = primary_symbol
        self.hedge_symbol = hedge_symbol
        self.hedge_ratio = hedge_ratio
        self.max_sync_delay_ms = max_sync_delay_ms
        self.unhedged_timeout_ms = unhedged_timeout_ms
        self.unwind_callback = unwind_callback
        self.pending_hedges: Dict[str, HedgeOrder] = {}
        # Grows one entry per hedge for the lifetime of the object; a long-running
        # high-frequency hedger should periodically recycle the synchronizer or
        # prune this set rather than relying on it to stay small.
        self.completed_hedge_ids: set = set()
        self._lock = threading.RLock()

    def generate_hedge_order(self, primary_fill: PrimaryFillEvent) -> HedgeOrder:
        """
        Calculates required hedge quantity and creates a HedgeOrder.
        Hedge Qty = -1.0 * Primary Fill Qty * Hedge Ratio

        Idempotency: ``fill_id`` is the deduplication key. A primary fill event
        redelivered under an id this synchronizer has already hedged raises rather
        than producing a second hedge order — a resent execution report must never
        become a second live order. FIX places this check on the application: a
        PossResend message has to be matched against business identifiers, because
        the session layer cannot tell a resend from a genuinely new fill. SEC Rule
        15c3-5(c)(1)(ii) likewise requires controls that reject orders "that
        indicate duplicative orders" before they reach the market.

        Resolve the raise against the OMS — confirm whether the venue already holds
        the hedge — instead of catching it and re-dispatching.
        """
        if primary_fill.strategy_id != self.strategy_id:
            raise ValueError(
                f"Strategy ID mismatch: fill belongs to {primary_fill.strategy_id!r}, "
                f"synchronizer manages {self.strategy_id!r}"
            )
        if primary_fill.primary_symbol != self.primary_symbol:
            raise ValueError(f"Primary symbol mismatch: {primary_fill.primary_symbol} vs {self.primary_symbol}")
        if not primary_fill.fill_id:
            raise ValueError("Primary fill event is missing fill_id")
        if not math.isfinite(primary_fill.fill_qty) or primary_fill.fill_qty == 0.0:
            raise ValueError(f"fill_qty must be finite and non-zero, got {primary_fill.fill_qty!r}")
        if not math.isfinite(primary_fill.fill_price) or primary_fill.fill_price < 0.0:
            raise ValueError(f"fill_price must be finite and non-negative, got {primary_fill.fill_price!r}")
        if not math.isfinite(primary_fill.timestamp_ms):
            raise ValueError(f"timestamp_ms must be finite, got {primary_fill.timestamp_ms!r}")

        target_qty = -1.0 * primary_fill.fill_qty * self.hedge_ratio
        if abs(target_qty) < QTY_EPSILON:
            raise ValueError(
                f"Hedge quantity for fill {primary_fill.fill_id} rounds to zero "
                f"({target_qty!r} from qty {primary_fill.fill_qty!r} x ratio "
                f"{self.hedge_ratio!r}); refusing to emit a zero-quantity order"
            )
        side = "BUY" if target_qty > 0 else "SELL"
        hedge_order_id = f"HEDGE_{primary_fill.fill_id}"

        with self._lock:
            if hedge_order_id in self.pending_hedges:
                existing = self.pending_hedges[hedge_order_id]
                raise ValueError(
                    f"Duplicate primary fill {primary_fill.fill_id}: hedge order "
                    f"{hedge_order_id} is already live (target {existing.target_hedge_qty}, "
                    f"filled {existing.filled_hedge_qty}). Generating again would discard "
                    "that fill state and dispatch a second hedge — reconcile against the "
                    "OMS instead of re-dispatching"
                )
            if hedge_order_id in self.completed_hedge_ids:
                raise ValueError(
                    f"Duplicate primary fill {primary_fill.fill_id}: hedge order "
                    f"{hedge_order_id} was already finalized (completed or timed out). "
                    "Re-hedging a fill that is already hedged doubles the position — "
                    "reconcile against the OMS instead of re-dispatching"
                )

            hedge_order = HedgeOrder(
                hedge_order_id=hedge_order_id,
                primary_fill_id=primary_fill.fill_id,
                hedge_symbol=self.hedge_symbol,
                target_hedge_qty=round(target_qty, 4),
                side=side,
                created_timestamp_ms=primary_fill.timestamp_ms
            )
            self.pending_hedges[hedge_order_id] = hedge_order

        logger.info(
            f"Generated Hedge Order [{hedge_order_id}]: {side} {abs(target_qty)} {self.hedge_symbol} "
            f"for Primary Fill {primary_fill.fill_id} (+{primary_fill.fill_qty} {primary_fill.primary_symbol})"
        )
        return hedge_order

    def mark_dispatched(self, hedge_order_id: str, dispatch_timestamp_ms: float) -> float:
        """
        Records when the OMS/gateway actually dispatched the hedge order and returns the
        dispatch latency (dispatch_timestamp_ms - primary fill timestamp). Logs an SLA
        breach if dispatch latency exceeds max_sync_delay_ms.

        Calling twice with the same timestamp is an idempotent no-op (gateway ack
        resend); a second call with a different timestamp raises, since it indicates a
        duplicate submission of an already-sent hedge order.
        """
        if not math.isfinite(dispatch_timestamp_ms):
            raise ValueError(f"dispatch_timestamp_ms must be finite, got {dispatch_timestamp_ms!r}")

        with self._lock:
            if hedge_order_id not in self.pending_hedges:
                raise ValueError(self._unknown_order_message(hedge_order_id))
            h_order = self.pending_hedges[hedge_order_id]
            if h_order.dispatched_timestamp_ms is not None:
                if dispatch_timestamp_ms == h_order.dispatched_timestamp_ms:
                    return dispatch_timestamp_ms - h_order.created_timestamp_ms
                raise ValueError(
                    f"Hedge order {hedge_order_id} already dispatched at "
                    f"{h_order.dispatched_timestamp_ms}ms; refusing duplicate dispatch at {dispatch_timestamp_ms}ms"
                )

            h_order.dispatched_timestamp_ms = dispatch_timestamp_ms
            dispatch_latency = dispatch_timestamp_ms - h_order.created_timestamp_ms

        if dispatch_latency > self.max_sync_delay_ms:
            logger.warning(
                f"Hedge Dispatch SLA Breached [{hedge_order_id}]: Dispatch latency = "
                f"{dispatch_latency:.1f}ms > {self.max_sync_delay_ms}ms"
            )
        return dispatch_latency

    def process_hedge_fill(
        self,
        hedge_order_id: str,
        filled_hedge_qty: float,
        hedge_fill_timestamp_ms: float
    ) -> HedgeSynchronizationStatus:
        """
        Processes one hedge fill callback (call once per fill event — quantities are
        accumulated across partial fills). The order remains pending until the
        cumulative fill reaches the target quantity, so residual exposure is always
        visible to enforce_unhedged_timeouts().

        A fill arriving after unhedged_timeout_ms is routed to unwind_callback here,
        exactly as the timer sweep would have done had it observed the hedge first.
        This matters most for a LATE PARTIAL fill: the order is finalized, so the
        sweep will never see it again, and without routing it the residual exposure
        would leave tracking with the primary leg still unprotected.
        """
        if not math.isfinite(filled_hedge_qty) or filled_hedge_qty == 0.0:
            raise ValueError(f"filled_hedge_qty must be finite and non-zero, got {filled_hedge_qty!r}")
        if not math.isfinite(hedge_fill_timestamp_ms):
            raise ValueError(f"hedge_fill_timestamp_ms must be finite, got {hedge_fill_timestamp_ms!r}")

        unwind_target: Optional[HedgeOrder] = None
        with self._lock:
            if hedge_order_id not in self.pending_hedges:
                raise ValueError(self._unknown_order_message(hedge_order_id))
            h_order = self.pending_hedges[hedge_order_id]
            target = h_order.target_hedge_qty
            if (filled_hedge_qty > 0) != (target > 0):
                raise ValueError(
                    f"Wrong-side hedge fill for {hedge_order_id}: target {target} but fill "
                    f"{filled_hedge_qty}; a sign flip here means position books disagree — investigate before any retry"
                )
            if hedge_fill_timestamp_ms < h_order.created_timestamp_ms:
                raise ValueError(
                    f"Hedge fill timestamp {hedge_fill_timestamp_ms} precedes primary fill "
                    f"timestamp {h_order.created_timestamp_ms} for {hedge_order_id}"
                )

            h_order.filled_hedge_qty += filled_hedge_qty
            cumulative_filled = h_order.filled_hedge_qty
            sync_delay = float(hedge_fill_timestamp_ms - h_order.created_timestamp_ms)
            unhedged_qty = abs(target) - abs(cumulative_filled)
            is_complete = unhedged_qty <= QTY_EPSILON

            if unhedged_qty < -QTY_EPSILON:
                logger.warning(
                    f"Hedge overfill [{hedge_order_id}]: filled {cumulative_filled} vs target "
                    f"{target}; hedge overshot and exposure flipped sign"
                )

            if sync_delay > self.unhedged_timeout_ms:
                status = STATUS_UNHEDGED_TIMEOUT_UNWIND
                is_sla_met = False
                self._finalize(h_order)
                unwind_target = h_order
                logger.critical(
                    f"UNHEDGED TIMEOUT ({sync_delay:.1f}ms, {unhedged_qty} still unhedged): "
                    f"Triggering emergency primary leg unwind for {hedge_order_id}!"
                )
            elif is_complete:
                if sync_delay <= self.max_sync_delay_ms:
                    status = STATUS_SYNCHRONIZED_OK
                    is_sla_met = True
                else:
                    status = STATUS_SYNC_DELAY_BREACH
                    is_sla_met = False
                    logger.warning(
                        f"Hedge Sync SLA Breached [{hedge_order_id}]: Delay = {sync_delay:.1f}ms > {self.max_sync_delay_ms}ms"
                    )
                self._finalize(h_order)
            else:
                # Partial fill inside the timeout window: keep tracking the residual.
                status = STATUS_PARTIALLY_FILLED
                is_sla_met = sync_delay <= self.max_sync_delay_ms

            result = HedgeSynchronizationStatus(
                strategy_id=self.strategy_id,
                primary_fill_id=h_order.primary_fill_id,
                hedge_order_id=hedge_order_id,
                sync_delay_ms=round(sync_delay, 2),
                is_sync_sla_met=is_sla_met,
                hedge_status=status,
                unhedged_exposure_qty=round(unhedged_qty, 4),
                filled_hedge_qty=round(cumulative_filled, 4)
            )

        # Outside the lock: user code must not run while hedge state is held.
        if unwind_target is not None:
            self._invoke_unwind(unwind_target)
        return result

    def enforce_unhedged_timeouts(self, now_ms: float) -> List[HedgeSynchronizationStatus]:
        """
        Sweep of all pending hedges against the wall clock. Any hedge still incomplete
        after unhedged_timeout_ms from its primary fill is flagged
        UNHEDGED_TIMEOUT_UNWIND, removed from pending tracking, and passed to the
        unwind_callback (if configured) so the primary leg can be emergency-unwound.

        Call this from a periodic timer or event-loop tick — without it, a hedge that
        never fills would otherwise stay pending forever.
        """
        if not math.isfinite(now_ms):
            raise ValueError(f"now_ms must be finite, got {now_ms!r}")

        expired: List[HedgeSynchronizationStatus] = []
        to_unwind: List[Tuple[HedgeOrder, HedgeSynchronizationStatus]] = []

        with self._lock:
            for hedge_order_id in list(self.pending_hedges.keys()):
                h_order = self.pending_hedges[hedge_order_id]
                sync_delay = float(now_ms - h_order.created_timestamp_ms)
                unhedged_qty = abs(h_order.target_hedge_qty) - abs(h_order.filled_hedge_qty)
                if sync_delay <= self.unhedged_timeout_ms or unhedged_qty <= QTY_EPSILON:
                    continue

                status = HedgeSynchronizationStatus(
                    strategy_id=self.strategy_id,
                    primary_fill_id=h_order.primary_fill_id,
                    hedge_order_id=hedge_order_id,
                    sync_delay_ms=round(sync_delay, 2),
                    is_sync_sla_met=False,
                    hedge_status=STATUS_UNHEDGED_TIMEOUT_UNWIND,
                    unhedged_exposure_qty=round(unhedged_qty, 4),
                    filled_hedge_qty=round(h_order.filled_hedge_qty, 4)
                )
                expired.append(status)
                self._finalize(h_order)
                to_unwind.append((h_order, status))
                logger.critical(
                    f"UNHEDGED TIMEOUT ({sync_delay:.1f}ms, {unhedged_qty} still unhedged): "
                    f"Triggering emergency primary leg unwind for {hedge_order_id}!"
                )

        # Outside the lock: a slow or blocking unwind callback must not stall the
        # gateway thread delivering fill callbacks.
        for h_order, _status in to_unwind:
            self._invoke_unwind(h_order)
        return expired

    def _invoke_unwind(self, h_order: HedgeOrder) -> None:
        """
        Routes one timed-out hedge to the emergency unwind callback. Shared by the
        timer sweep and the late-fill path so the same event cannot produce
        different outcomes depending on which observer saw it first.

        A failing callback is logged and swallowed: the caller is mid-sweep over
        other hedges, and one broken unwind must not prevent the rest from being
        flagged. The primary leg is unprotected at that point — escalate manually.
        """
        if self.unwind_callback is None:
            return
        try:
            self.unwind_callback(h_order)
        except Exception:
            logger.exception(
                f"Unwind callback failed for {h_order.hedge_order_id}; primary leg "
                "may remain unhedged — escalate manually"
            )

    def _finalize(self, h_order: HedgeOrder) -> None:
        """Caller must hold self._lock."""
        del self.pending_hedges[h_order.hedge_order_id]
        self.completed_hedge_ids.add(h_order.hedge_order_id)

    def _unknown_order_message(self, hedge_order_id: str) -> str:
        if hedge_order_id in self.completed_hedge_ids:
            return (f"Hedge order {hedge_order_id} already finalized (completed or timed out); "
                    f"duplicate fill/dispatch callbacks must not be re-processed")
        return f"Unknown hedge order ID {hedge_order_id}"

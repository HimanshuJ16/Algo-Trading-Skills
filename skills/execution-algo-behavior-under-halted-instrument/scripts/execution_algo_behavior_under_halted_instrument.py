"""Execution algorithm state machine for instrument trading halts.

Governs what a parent TWAP/VWAP/POV algo does when its instrument stops trading
continuously: which child orders must be pulled, whether the venue will even
accept the cancel, when slicing may resume, and at what rate the unexecuted
backlog may be worked once the instrument reopens.

Two facts drive the design and are the reason the naive version of this engine
is unsafe:

1. A cancel is a *request*, not a state change. The order stays live until the
   venue acknowledges it, and it can fill in the meantime. Resting orders
   persist through a US LULD pause and are eligible interest for the reopening
   auction, so an algo that marks them cancelled locally believes it is flat
   while it still has working exposure into the most volatile print of the day.
2. Cancellation is not always permitted. CME Globex accepts cancels in `Pause`
   but forbids them in `Pre-Open - No Cancel`; Eurex T7 holds deletions as
   `pending` during the freeze phase of an extended volatility interruption.

See ``references/standards.md`` for the venue-by-venue citations.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Instrument trading status vocabulary.
#
# These are normalised internal tokens, not venue wire values. Map your feed's
# native codes (Nasdaq trade-action codes, CME SecurityTradingStatus, Eurex T7
# trading phases) onto these before calling the engine.
# ---------------------------------------------------------------------------

CONTINUOUS_STATUSES = frozenset({"TRADING_CONTINUOUS"})

#: NBB/NBO at a LULD band (limit state) or outside it (straddle state). The
#: instrument is still trading, but marketable orders are rejected.
BAND_STRESS_STATUSES = frozenset({"LIMIT_STATE", "STRADDLE_STATE"})

#: No matching. Cancellation is permitted.
HALT_STATUSES = frozenset({
    "HALTED_LULD",
    "HALTED_NEWS",
    "HALTED_REGULATORY",
    "HALTED_MWCB",
    "HALTED_VOLATILITY_INTERRUPTION",
    "PAUSED_VELOCITY_LOGIC",
})

#: Auction / price-discovery phases. Order maintenance is permitted but there is
#: no continuous matching, so continuous child slicing must not run.
AUCTION_STATUSES = frozenset({"AUCTION_REOPENING", "PRE_OPEN", "AUCTION_CLOSING"})

#: Phases in which the venue will NOT action a cancel. Any order still live here
#: is committed to the auction print.
NO_CANCEL_STATUSES = frozenset({"PRE_OPEN_NO_CANCEL", "VOLATILITY_FREEZE"})

CLOSED_STATUSES = frozenset({"CLOSED", "CLOSE_FINAL"})

_KNOWN_STATUSES = (
    CONTINUOUS_STATUSES
    | BAND_STRESS_STATUSES
    | HALT_STATUSES
    | AUCTION_STATUSES
    | NO_CANCEL_STATUSES
    | CLOSED_STATUSES
)

# Child order lifecycle.
CHILD_RESTING = "RESTING"
CHILD_PENDING_CANCEL = "PENDING_CANCEL"
CHILD_CANCELLED = "CANCELLED"
CHILD_FILLED = "FILLED"

#: Statuses in which the child order can still execute against the book.
_LIVE_CHILD_STATUSES = frozenset({CHILD_RESTING, CHILD_PENDING_CANCEL})

# Parent algo states.
STATE_RUNNING = "RUNNING"
STATE_PAUSED_HALTED = "PAUSED_HALTED"
STATE_REBALANCING = "REBALANCING_POST_RESUMPTION"

# Venue responses to a cancel request.
ACK_CANCELLED = "CANCELLED"
ACK_CANCEL_REJECTED = "CANCEL_REJECTED"
ACK_FILLED = "FILLED"
_VALID_ACKS = frozenset({ACK_CANCELLED, ACK_CANCEL_REJECTED, ACK_FILLED})


class HaltEngineError(ValueError):
    """Raised when engine inputs are structurally invalid."""


@dataclass
class ActiveChildOrder:
    """A child order the parent algo has working at a venue.

    ``status`` follows RESTING -> PENDING_CANCEL -> CANCELLED/FILLED. It is only
    moved to a terminal status by
    :meth:`ExecutionAlgoHaltEngine.apply_cancel_ack`, i.e. on a venue
    acknowledgement, never optimistically.
    """

    child_ord_id: str
    venue_id: str
    side: str
    price: float
    order_qty: int
    status: str = CHILD_RESTING
    filled_qty: int = 0

    def is_live(self) -> bool:
        """True while the order can still execute against the book."""
        return self.status in _LIVE_CHILD_STATUSES


@dataclass
class ParentAlgoInstanceState:
    """Mutable state of one parent algo instance.

    The schedule fields are optional: time-based algos (TWAP, Implementation
    Shortfall) should supply them so the post-halt backlog guard can run, while
    purely volume-driven algos (POV) may leave them unset -- the engine then
    reports ``rebenchmark_applied=False`` rather than silently guessing a rate.
    """

    parent_algo_id: str
    symbol: str
    algo_type: str                                  # 'TWAP', 'VWAP', 'POV'
    total_target_qty: int
    executed_qty: int
    algo_state: str
    active_child_orders: List[ActiveChildOrder] = field(default_factory=list)
    schedule_start_ts: Optional[float] = None       # epoch seconds
    schedule_end_ts: Optional[float] = None         # epoch seconds, extended on resume
    hard_end_ts: Optional[float] = None             # e.g. session close; never extended past
    halt_started_ts: Optional[float] = None         # set by the engine, not the caller

    def remaining_qty(self) -> int:
        """Unexecuted quantity, floored at zero.

        Over-execution is not silently absorbed here -- the engine detects
        ``executed_qty > total_target_qty`` separately and flags a
        reconciliation breach.
        """
        return max(0, self.total_target_qty - self.executed_qty)

    def live_child_orders(self) -> List[ActiveChildOrder]:
        """Child orders that can still execute against the book."""
        return [c for c in self.active_child_orders if c.is_live()]


@dataclass
class AlgoHaltAuditReport:
    """Structured audit record for one instrument status transition.

    ``cancelled_child_orders_count`` counts venue-**confirmed** cancels only. At
    the moment a halt is detected it is therefore ``0`` and
    ``orders_still_live_count`` carries the exposure the desk actually has.
    Gate downstream logic on ``orders_still_live_count``.
    """

    parent_algo_id: str
    symbol: str
    previous_algo_state: str
    new_algo_state: str
    instrument_trading_status: str
    cancel_requests_issued: int
    cancelled_child_orders_count: int
    orders_still_live_count: int
    remaining_qty: int
    is_slicing_active: bool
    marketable_child_orders_permitted: bool
    cancel_permitted: bool
    audit_notes: str
    status_recognised: bool = True
    reconciliation_breach: bool = False
    halt_duration_s: Optional[float] = None
    rebenchmark_applied: bool = False
    rebenchmarked_end_ts: Optional[float] = None
    original_rate_qty_per_s: Optional[float] = None
    required_rate_qty_per_s: Optional[float] = None
    rebenchmark_breach: bool = False


@dataclass(frozen=True)
class HaltEngineConfig:
    """Firm-calibrated engine parameters.

    These are **not** regulatory or exchange standards; no regulator or venue
    publishes a halt-reaction latency SLA or a post-halt catch-up rate limit.
    Calibrate them against your own impact model and record the rationale.
    """

    #: The post-halt required rate may exceed the original scheduled rate by at
    #: most this multiple before the engine refuses to resume slicing.
    max_rate_multiple: float = 1.5
    #: Below this many seconds of remaining horizon the residual cannot be
    #: worked as a schedule at all, and is escalated instead.
    min_remaining_seconds: float = 1.0

    def __post_init__(self) -> None:
        if not math.isfinite(self.max_rate_multiple) or self.max_rate_multiple < 1.0:
            raise HaltEngineError("max_rate_multiple must be finite and >= 1.0")
        if not math.isfinite(self.min_remaining_seconds) or self.min_remaining_seconds <= 0:
            raise HaltEngineError("min_remaining_seconds must be finite and > 0")


@dataclass(frozen=True)
class _Rebenchmark:
    """Internal result of the post-halt schedule recalculation."""

    applied: bool
    breach: bool
    new_end_ts: Optional[float] = None
    original_rate: Optional[float] = None
    required_rate: Optional[float] = None


class ExecutionAlgoHaltEngine:
    """Drives parent-algo state transitions across instrument halt events.

    The engine is deterministic and reads no wall clock: every call takes an
    explicit ``event_ts`` so that halt durations and re-benchmarked schedules
    are reproducible in backtests and replay harnesses.
    """

    def __init__(self, config: Optional[HaltEngineConfig] = None) -> None:
        self.config = config or HaltEngineConfig()

    # -- public API --------------------------------------------------------

    def handle_trading_status_change(
        self,
        algo: ParentAlgoInstanceState,
        instrument_status: str,
        event_ts: float,
    ) -> AlgoHaltAuditReport:
        """Apply an instrument trading status update to a parent algo.

        Returns an audit report describing the resulting state. Cancel requests
        are *issued* (RESTING -> PENDING_CANCEL); they are not completed until
        :meth:`apply_cancel_ack` receives the venue's response.
        """
        self._validate(algo, instrument_status, event_ts)

        status = instrument_status.strip().upper()
        prev_state = algo.algo_state

        if status not in _KNOWN_STATUSES:
            return self._handle_unknown(algo, status, prev_state)
        if status in HALT_STATUSES:
            return self._handle_halt(algo, status, prev_state, event_ts)
        if status in NO_CANCEL_STATUSES:
            return self._handle_no_cancel(algo, status, prev_state, event_ts)
        if status in AUCTION_STATUSES:
            return self._handle_auction(algo, status, prev_state, event_ts)
        if status in CLOSED_STATUSES:
            return self._handle_closed(algo, status, prev_state)
        if status in BAND_STRESS_STATUSES:
            return self._handle_band_stress(algo, status, prev_state)
        return self._handle_continuous(algo, status, prev_state, event_ts)

    def apply_cancel_ack(
        self,
        algo: ParentAlgoInstanceState,
        child_ord_id: str,
        outcome: str,
        filled_qty: int = 0,
    ) -> ActiveChildOrder:
        """Apply a venue acknowledgement to an outstanding cancel request.

        ``CANCELLED`` retires the order (optionally with a partial fill that
        printed before the cancel landed). ``CANCEL_REJECTED`` returns it to
        ``RESTING`` -- the order is **still live**, which is the case operators
        most often miss. ``FILLED`` records that the order executed in full
        before the cancel took effect.
        """
        if outcome not in _VALID_ACKS:
            raise HaltEngineError(
                f"unknown cancel ack outcome {outcome!r}; "
                f"expected one of {sorted(_VALID_ACKS)}"
            )
        if filled_qty < 0:
            raise HaltEngineError("filled_qty must be >= 0")

        order = self._find_child(algo, child_ord_id)
        if not order.is_live():
            raise HaltEngineError(
                f"child order {child_ord_id!r} is already terminal ({order.status}); "
                "duplicate acknowledgement rejected"
            )

        unfilled = order.order_qty - order.filled_qty
        if outcome == ACK_FILLED:
            # FILLED is terminal-by-execution and must consume the whole
            # remainder. Accepting a smaller quantity here would silently
            # under-report executed_qty for the unaccounted balance; a partial
            # that printed before the cancel landed is CANCELLED + filled_qty.
            if filled_qty and filled_qty != unfilled:
                raise HaltEngineError(
                    f"child order {child_ord_id!r}: FILLED must consume the full "
                    f"unfilled {unfilled}, got {filled_qty}; use CANCELLED with "
                    "filled_qty for a partial fill that raced the cancel"
                )
            filled_qty = unfilled
        if filled_qty > unfilled:
            raise HaltEngineError(
                f"child order {child_ord_id!r} over-fill: {filled_qty} exceeds "
                f"unfilled {unfilled}"
            )

        if filled_qty:
            order.filled_qty += filled_qty
            algo.executed_qty += filled_qty
            logger.warning(
                "HALT CANCEL RACE [%s]: child %s filled %d before the cancel took effect.",
                algo.parent_algo_id, child_ord_id, filled_qty,
            )

        if outcome == ACK_CANCEL_REJECTED:
            order.status = CHILD_RESTING
            logger.critical(
                "HALT CANCEL REJECTED [%s]: child %s on %s remains LIVE on the book.",
                algo.parent_algo_id, child_ord_id, order.venue_id,
            )
        elif outcome == ACK_FILLED:
            order.status = CHILD_FILLED
        else:
            order.status = CHILD_CANCELLED
            logger.info(
                "HALT CANCEL CONFIRMED [%s]: child %s on %s.",
                algo.parent_algo_id, child_ord_id, order.venue_id,
            )
        return order

    # -- status handlers ---------------------------------------------------

    def _handle_halt(
        self,
        algo: ParentAlgoInstanceState,
        status: str,
        prev_state: str,
        event_ts: float,
    ) -> AlgoHaltAuditReport:
        algo.algo_state = STATE_PAUSED_HALTED
        # Do not restamp on a duplicate halt message: the halt clock must span
        # the whole outage, and resetting it would understate the duration used
        # to extend the schedule.
        if algo.halt_started_ts is None:
            algo.halt_started_ts = event_ts

        issued = self._issue_cancel_requests(algo)
        live = len(algo.live_child_orders())
        notes = (
            f"ALGO HALTED [{algo.parent_algo_id} - {algo.symbol}]: status={status}. "
            f"{prev_state} -> {STATE_PAUSED_HALTED}. {issued} cancel request(s) issued; "
            f"{live} child order(s) STILL LIVE pending venue acknowledgement. Slicing paused."
        )
        logger.critical(notes)
        return self._report(
            algo, status, prev_state, notes,
            cancel_requests_issued=issued,
            is_slicing_active=False,
            marketable_permitted=False,
            cancel_permitted=True,
        )

    def _handle_no_cancel(
        self,
        algo: ParentAlgoInstanceState,
        status: str,
        prev_state: str,
        event_ts: float,
    ) -> AlgoHaltAuditReport:
        """Pre-auction no-cancel window -- the venue will not action a cancel."""
        algo.algo_state = STATE_PAUSED_HALTED
        if algo.halt_started_ts is None:
            algo.halt_started_ts = event_ts

        live = len(algo.live_child_orders())
        notes = (
            f"ALGO FROZEN [{algo.parent_algo_id} - {algo.symbol}]: status={status}. "
            f"{prev_state} -> {STATE_PAUSED_HALTED}. Venue does NOT accept cancels in this "
            f"phase; {live} child order(s) are committed to the auction print."
        )
        if live:
            logger.critical(notes)
        else:
            logger.warning(notes)
        return self._report(
            algo, status, prev_state, notes,
            cancel_requests_issued=0,
            is_slicing_active=False,
            marketable_permitted=False,
            cancel_permitted=False,
        )

    def _handle_auction(
        self,
        algo: ParentAlgoInstanceState,
        status: str,
        prev_state: str,
        event_ts: float,
    ) -> AlgoHaltAuditReport:
        """Reopening / pre-open auction: order maintenance yes, slicing no."""
        if algo.algo_state in (STATE_RUNNING, STATE_PAUSED_HALTED):
            algo.algo_state = STATE_REBALANCING
        # The halt clock keeps running: the instrument still is not trading
        # continuously, so the schedule has not started recovering yet.
        if algo.halt_started_ts is None:
            algo.halt_started_ts = event_ts

        issued = self._issue_cancel_requests(algo)
        live = len(algo.live_child_orders())
        notes = (
            f"ALGO IN AUCTION [{algo.parent_algo_id} - {algo.symbol}]: status={status}. "
            f"{prev_state} -> {algo.algo_state}. Continuous slicing suppressed during price "
            f"discovery; {issued} residual cancel request(s) issued; {live} still live."
        )
        logger.warning(notes)
        return self._report(
            algo, status, prev_state, notes,
            cancel_requests_issued=issued,
            is_slicing_active=False,
            marketable_permitted=False,
            cancel_permitted=True,
        )

    def _handle_continuous(
        self,
        algo: ParentAlgoInstanceState,
        status: str,
        prev_state: str,
        event_ts: float,
    ) -> AlgoHaltAuditReport:
        if prev_state not in (STATE_PAUSED_HALTED, STATE_REBALANCING):
            notes = (
                f"ALGO CONTINUING [{algo.parent_algo_id} - {algo.symbol}]: state remains "
                f"{algo.algo_state}."
            )
            logger.debug(notes)
            running = algo.algo_state == STATE_RUNNING
            return self._report(
                algo, status, prev_state, notes,
                cancel_requests_issued=0,
                is_slicing_active=running,
                marketable_permitted=running,
                cancel_permitted=True,
            )

        halt_duration: Optional[float] = None
        if algo.halt_started_ts is not None:
            halt_duration = max(0.0, event_ts - algo.halt_started_ts)

        rb = self._rebenchmark(algo, event_ts, halt_duration)
        live = len(algo.live_child_orders())

        if rb.breach:
            algo.algo_state = STATE_REBALANCING
            slicing = False
            if rb.required_rate is not None and rb.original_rate is not None:
                verdict = (
                    f"BACKLOG GUARD TRIPPED: required rate {rb.required_rate:.4f}/s exceeds "
                    f"{self.config.max_rate_multiple}x the scheduled "
                    f"{rb.original_rate:.4f}/s. Holding for operator decision rather than "
                    "dumping the backlog into the reopening."
                )
            else:
                verdict = (
                    "BACKLOG GUARD TRIPPED: insufficient remaining horizon to work the "
                    "residual as a schedule."
                )
            log = logger.critical
        else:
            algo.algo_state = STATE_RUNNING
            algo.halt_started_ts = None
            slicing = True
            if rb.applied:
                algo.schedule_end_ts = rb.new_end_ts
                verdict = (
                    f"Schedule extended by {halt_duration or 0.0:.1f}s of halt; "
                    f"{algo.remaining_qty():,} share(s) re-benchmarked at "
                    f"{rb.required_rate:.4f}/s."
                )
            else:
                verdict = (
                    "No schedule horizon supplied -- the backlog smoothing guard did NOT "
                    "run; the parent algo is responsible for rate limiting the residual."
                )
            log = logger.info

        if live:
            logger.critical(
                "RESUMPTION WITH LIVE ORDERS [%s]: %d child order(s) still unconfirmed at "
                "resumption; reconcile before dispatching new slices.",
                algo.parent_algo_id, live,
            )

        notes = (
            f"ALGO RESUMED [{algo.parent_algo_id} - {algo.symbol}]: status={status}. "
            f"{prev_state} -> {algo.algo_state}. {verdict}"
        )
        log(notes)
        return self._report(
            algo, status, prev_state, notes,
            cancel_requests_issued=0,
            is_slicing_active=slicing,
            marketable_permitted=slicing,
            cancel_permitted=True,
            halt_duration_s=halt_duration,
            rebenchmark_applied=rb.applied,
            rebenchmarked_end_ts=rb.new_end_ts,
            original_rate_qty_per_s=rb.original_rate,
            required_rate_qty_per_s=rb.required_rate,
            rebenchmark_breach=rb.breach,
        )

    def _handle_band_stress(
        self, algo: ParentAlgoInstanceState, status: str, prev_state: str
    ) -> AlgoHaltAuditReport:
        """LULD limit or straddle state: still trading, but marketable orders bounce."""
        notes = (
            f"ALGO BAND-STRESSED [{algo.parent_algo_id} - {algo.symbol}]: status={status}. "
            "Marketable child orders are rejected at the band; passive slicing only. "
            "A limit state persisting 15s escalates to a trading pause."
        )
        logger.warning(notes)
        return self._report(
            algo, status, prev_state, notes,
            cancel_requests_issued=0,
            is_slicing_active=(algo.algo_state == STATE_RUNNING),
            marketable_permitted=False,
            cancel_permitted=True,
        )

    def _handle_closed(
        self, algo: ParentAlgoInstanceState, status: str, prev_state: str
    ) -> AlgoHaltAuditReport:
        live = len(algo.live_child_orders())
        notes = (
            f"ALGO CLOSED-OUT [{algo.parent_algo_id} - {algo.symbol}]: status={status}. "
            f"{algo.remaining_qty():,} share(s) unexecuted. Cancels are not accepted after "
            f"the close; {live} order(s) will be dispositioned by the venue's own "
            "end-of-session handling."
        )
        logger.warning(notes)
        return self._report(
            algo, status, prev_state, notes,
            cancel_requests_issued=0,
            is_slicing_active=False,
            marketable_permitted=False,
            cancel_permitted=False,
        )

    def _handle_unknown(
        self, algo: ParentAlgoInstanceState, status: str, prev_state: str
    ) -> AlgoHaltAuditReport:
        """Fail safe, not fail open.

        An unrecognised status stops slicing but deliberately does not cancel:
        cancelling on a malformed or newly-added feed token would itself be an
        unrequested trading action. The condition is escalated instead.
        """
        notes = (
            f"ALGO HELD [{algo.parent_algo_id} - {algo.symbol}]: UNRECOGNISED instrument "
            f"status {status!r}. Slicing suspended pending operator review; no cancels "
            "issued on an unmapped status."
        )
        logger.critical(notes)
        return self._report(
            algo, status, prev_state, notes,
            cancel_requests_issued=0,
            is_slicing_active=False,
            marketable_permitted=False,
            cancel_permitted=False,
            status_recognised=False,
        )

    # -- helpers -----------------------------------------------------------

    def _issue_cancel_requests(self, algo: ParentAlgoInstanceState) -> int:
        """Move RESTING children to PENDING_CANCEL.

        Never re-requests a cancel that is already pending: duplicate cancel
        requests for the same order are rejected by most venues and pollute the
        order-to-trade ratio.
        """
        issued = 0
        for child in algo.active_child_orders:
            if child.status == CHILD_RESTING:
                child.status = CHILD_PENDING_CANCEL
                issued += 1
                logger.warning(
                    "HALT CANCEL REQUEST [%s]: child %s on %s -> PENDING_CANCEL.",
                    algo.parent_algo_id, child.child_ord_id, child.venue_id,
                )
        return issued

    def _rebenchmark(
        self,
        algo: ParentAlgoInstanceState,
        event_ts: float,
        halt_duration: Optional[float],
    ) -> _Rebenchmark:
        """Extend the schedule by the halt duration and rate-check the residual.

        Freezing the slice timer means the horizon lost to the halt is given
        back, capped at ``hard_end_ts`` (the session close cannot be extended).
        If the residual still cannot be worked within ``max_rate_multiple`` of
        the original scheduled rate, the guard trips.
        """
        if algo.schedule_start_ts is None or algo.schedule_end_ts is None:
            return _Rebenchmark(applied=False, breach=False)

        span = algo.schedule_end_ts - algo.schedule_start_ts
        original_rate = algo.total_target_qty / span
        remaining = algo.remaining_qty()

        new_end = algo.schedule_end_ts + (halt_duration or 0.0)
        if algo.hard_end_ts is not None:
            new_end = min(new_end, algo.hard_end_ts)

        if remaining <= 0:
            return _Rebenchmark(
                applied=True, breach=False, new_end_ts=new_end,
                original_rate=original_rate, required_rate=0.0,
            )

        remaining_seconds = new_end - event_ts
        if remaining_seconds < self.config.min_remaining_seconds:
            return _Rebenchmark(
                applied=True, breach=True, new_end_ts=new_end,
                original_rate=original_rate, required_rate=None,
            )

        required_rate = remaining / remaining_seconds
        breach = required_rate > self.config.max_rate_multiple * original_rate
        return _Rebenchmark(
            applied=True, breach=breach, new_end_ts=new_end,
            original_rate=original_rate, required_rate=required_rate,
        )

    @staticmethod
    def _find_child(
        algo: ParentAlgoInstanceState, child_ord_id: str
    ) -> ActiveChildOrder:
        for child in algo.active_child_orders:
            if child.child_ord_id == child_ord_id:
                return child
        raise HaltEngineError(
            f"child order {child_ord_id!r} not found on parent {algo.parent_algo_id!r}"
        )

    def _report(
        self,
        algo: ParentAlgoInstanceState,
        status: str,
        prev_state: str,
        notes: str,
        *,
        cancel_requests_issued: int,
        is_slicing_active: bool,
        marketable_permitted: bool,
        cancel_permitted: bool,
        status_recognised: bool = True,
        halt_duration_s: Optional[float] = None,
        rebenchmark_applied: bool = False,
        rebenchmarked_end_ts: Optional[float] = None,
        original_rate_qty_per_s: Optional[float] = None,
        required_rate_qty_per_s: Optional[float] = None,
        rebenchmark_breach: bool = False,
    ) -> AlgoHaltAuditReport:
        breach = algo.executed_qty > algo.total_target_qty
        if breach:
            is_slicing_active = False
            notes = (
                f"{notes} RECONCILIATION BREACH: executed {algo.executed_qty:,} exceeds "
                f"target {algo.total_target_qty:,}; slicing forced off."
            )
            logger.critical(notes)

        confirmed = sum(
            1 for c in algo.active_child_orders if c.status == CHILD_CANCELLED
        )
        return AlgoHaltAuditReport(
            parent_algo_id=algo.parent_algo_id,
            symbol=algo.symbol,
            previous_algo_state=prev_state,
            new_algo_state=algo.algo_state,
            instrument_trading_status=status,
            cancel_requests_issued=cancel_requests_issued,
            cancelled_child_orders_count=confirmed,
            orders_still_live_count=len(algo.live_child_orders()),
            remaining_qty=algo.remaining_qty(),
            is_slicing_active=is_slicing_active,
            marketable_child_orders_permitted=marketable_permitted,
            cancel_permitted=cancel_permitted,
            audit_notes=notes,
            status_recognised=status_recognised,
            reconciliation_breach=breach,
            halt_duration_s=halt_duration_s,
            rebenchmark_applied=rebenchmark_applied,
            rebenchmarked_end_ts=rebenchmarked_end_ts,
            original_rate_qty_per_s=original_rate_qty_per_s,
            required_rate_qty_per_s=required_rate_qty_per_s,
            rebenchmark_breach=rebenchmark_breach,
        )

    @staticmethod
    def _validate(
        algo: ParentAlgoInstanceState, instrument_status: str, event_ts: float
    ) -> None:
        if not isinstance(instrument_status, str) or not instrument_status.strip():
            raise HaltEngineError("instrument_status must be a non-empty string")
        if not algo.parent_algo_id or not algo.symbol:
            raise HaltEngineError("parent_algo_id and symbol are required")
        if algo.total_target_qty <= 0:
            raise HaltEngineError("total_target_qty must be > 0")
        if algo.executed_qty < 0:
            raise HaltEngineError("executed_qty must be >= 0")
        if isinstance(event_ts, bool) or not isinstance(event_ts, (int, float)):
            raise HaltEngineError("event_ts must be a finite epoch-seconds value")
        if not math.isfinite(float(event_ts)):
            raise HaltEngineError("event_ts must be a finite epoch-seconds value")
        for name in ("schedule_start_ts", "schedule_end_ts", "hard_end_ts"):
            value = getattr(algo, name)
            if value is not None and not math.isfinite(float(value)):
                raise HaltEngineError(f"{name} must be finite when supplied")
        if (
            algo.schedule_start_ts is not None
            and algo.schedule_end_ts is not None
            and algo.schedule_end_ts <= algo.schedule_start_ts
        ):
            raise HaltEngineError(
                "schedule_end_ts must be strictly after schedule_start_ts"
            )
        if (
            algo.hard_end_ts is not None
            and algo.schedule_end_ts is not None
            and algo.hard_end_ts < algo.schedule_end_ts
        ):
            raise HaltEngineError("hard_end_ts must not precede schedule_end_ts")
        for child in algo.active_child_orders:
            if child.order_qty <= 0:
                raise HaltEngineError(
                    f"child order {child.child_ord_id!r} has non-positive order_qty"
                )
            if child.filled_qty < 0 or child.filled_qty > child.order_qty:
                raise HaltEngineError(
                    f"child order {child.child_ord_id!r} has filled_qty outside "
                    "[0, order_qty]"
                )

"""Execution-algorithm kill switch: order-entry lockout plus venue mass cancel.

The engine does two things, in this order, and never in the other order:

1. **Latch the lockout locally.** Blocking further order entry is the only part
   of a kill switch that cannot fail: it needs no network. It is latched first
   so a runaway algorithm cannot refill the book through the gap opened while
   cancels are in flight.
2. **Dispatch a cancel to every venue** and then *track what was actually
   confirmed*. Requesting a cancel is not the same event as the order being
   dead, and this module keeps the two apart.

Regulatory anchors (full citations in ``references/standards.md``):

* Commission Delegated Regulation (EU) 2017/589 (MiFID II RTS 6) Art. 12(1):
  the firm "shall be able to cancel immediately, as an emergency measure, any
  or all of its unexecuted orders submitted to any or all trading venues to
  which the investment firm is connected"; Art. 12(2) extends this to orders of
  individual traders, desks and clients; Art. 12(3) requires the firm to be
  able to identify which algorithm and which trader/desk/client is responsible
  for each order sent to a venue -- which is why every order carries
  ``strategy_id`` and every audit record carries it through.
* RTS 6 Art. 15(1) requires pre-trade controls including maximum order values,
  maximum order volumes and *maximum message limits*. It mandates the control,
  not a number: no numeric message rate appears in the regulation.
* SEC Rule 17 CFR 240.15c3-5(b) requires documented risk management controls;
  (c)(1)(i) prevention of orders exceeding pre-set credit or capital
  thresholds; (d) places those controls under the "direct and exclusive
  control" of the broker-dealer. The rule contains neither the phrase "kill
  switch" nor any latency figure.

Design notes:

* **No regulator publishes a kill-switch latency.** RTS 6 says "immediately";
  15c3-5 says "reasonably designed". Any millisecond budget in your runbook is
  a firm engineering target, not a rule.
* **A mass cancel can be refused.** FIX ``MassCancelRejectReason`` (tag 532)
  value 0 is literally "Mass Cancel Not Supported", and venues exclude some
  orders from cancellation by rule (NYSE Pillar keeps MOO/LOO orders alive
  after the auction cutoff). An engine that reports every order as cancelled
  the instant it sends the request is producing a false audit record.
* **FIX tag 530 has no per-strategy scope.** Its FIX 4.4 enumeration is
  security / underlying / product / CFICode / SecurityType / trading session /
  all orders. Value 1 cancels every order in a *security*, across every
  strategy -- so using it for a strategy-scoped kill both over-cancels other
  strategies and under-cancels that strategy's other symbols. Strategy scope
  therefore cancels order-by-order here. (FIX 5.0 SP2 adds a ``TargetParties``
  component to ``OrderMassCancelRequest``; where a venue supports it, a
  party-scoped mass cancel is the faster path -- implement it in your gateway
  adapter, not by mislabelling tag 530.)
* **This module holds no positions and no venue state.** PnL, order rate and
  exposure arrive in a ``RiskSnapshot`` from the caller; order outcomes arrive
  through ``apply_execution_report``. State is in memory and does not survive a
  restart -- persist it or fail closed on start-up.
"""

from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import (
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

logger = logging.getLogger(__name__)

# --- Scopes -----------------------------------------------------------------
SCOPE_GLOBAL = "GLOBAL"
SCOPE_STRATEGY = "STRATEGY"
SCOPE_NONE = "NONE"

# --- Report status codes (stable contract for alerting/surveillance) --------
STATUS_NORMAL = "NORMAL_OPERATIONS"
STATUS_ENGAGED = "KILL_SWITCH_ENGAGED"
STATUS_REJECTED_KILL_SWITCH = "REJECTED_KILL_SWITCH_ACTIVE"
STATUS_REJECTED_RISK_DATA = "REJECTED_RISK_DATA_INVALID"
STATUS_RESET = "KILL_SWITCH_RESET"

# --- Trigger reason codes ---------------------------------------------------
TRIGGER_MAX_LOSS_BREACH = "MAX_LOSS_BREACH"
TRIGGER_MAX_EXPOSURE_BREACH = "MAX_EXPOSURE_BREACH"
TRIGGER_RUNAWAY_ORDER_RATE = "RUNAWAY_ORDER_RATE"
TRIGGER_MANUAL_OVERRIDE = "MANUAL_OVERRIDE"
TRIGGER_KILL_SWITCH_ACTIVE = "KILL_SWITCH_ALREADY_ACTIVE"
TRIGGER_RISK_DATA_INVALID = "RISK_DATA_INVALID"
TRIGGER_RISK_DATA_STALE = "RISK_DATA_STALE"

# --- Order states -----------------------------------------------------------
ORDER_NEW = "NEW"
ORDER_PARTIALLY_FILLED = "PARTIALLY_FILLED"
ORDER_PENDING_CANCEL = "PENDING_CANCEL"
ORDER_CANCELLED = "CANCELLED"
ORDER_FILLED = "FILLED"
ORDER_REJECTED = "REJECTED"

#: Statuses in which the venue may still trade the order.
LIVE_ORDER_STATUSES = frozenset({ORDER_NEW, ORDER_PARTIALLY_FILLED})
TERMINAL_ORDER_STATUSES = frozenset({ORDER_CANCELLED, ORDER_FILLED, ORDER_REJECTED})
#: Statuses a kill switch must still chase. ``PENDING_CANCEL`` is deliberately
#: included: a cancel request that has not been acknowledged has killed nothing,
#: so re-firing the switch re-requests it. Mass cancel is idempotent -- asking a
#: venue twice to cancel the same order costs a message and risks nothing.
CANCELLABLE_ORDER_STATUSES = LIVE_ORDER_STATUSES | {ORDER_PENDING_CANCEL}
KNOWN_ORDER_STATUSES = CANCELLABLE_ORDER_STATUSES | TERMINAL_ORDER_STATUSES

# --- FIX MassCancelRequestType (tag 530), FIX 4.4 enumeration ---------------
MASS_CANCEL_SECURITY = "1"
MASS_CANCEL_TRADING_SESSION = "6"
MASS_CANCEL_ALL_ORDERS = "7"

# --- Dispatch outcome codes -------------------------------------------------
DISPATCH_ACCEPTED = "ACCEPTED"
DISPATCH_REJECTED = "REJECTED"
DISPATCH_ERROR = "ERROR"
DISPATCH_NO_GATEWAY = "NO_GATEWAY"

DEFAULT_MAX_SNAPSHOT_AGE_SECONDS = 5.0


class KillSwitchError(Exception):
    """Raised on misconfiguration or misuse -- never for a risk breach.

    A breach is a normal, expected outcome and is reported in a
    :class:`KillSwitchAuditReport`. An exception here means the *caller* is
    wrong (unknown scope, malformed order, strategy scope with no strategy id),
    and it is raised rather than swallowed so that a mistyped kill-switch call
    can never look like "no action required".
    """


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(ts: datetime) -> str:
    """UTC ISO-8601 with millisecond precision."""
    if ts.tzinfo is None:
        raise KillSwitchError("timestamps must be timezone-aware UTC")
    return ts.astimezone(timezone.utc).isoformat(timespec="milliseconds")


def _require_finite(value: float, label: str) -> float:
    """Reject NaN/Inf risk inputs.

    This is not defensive decoration. Every threshold in this module is a
    comparison, and *every* comparison against NaN is ``False`` -- so a NaN PnL
    silently passes a loss limit and the kill switch never fires. Non-finite
    risk data means the limit cannot be evaluated at all, which is a fail-closed
    condition, not a pass.
    """
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise KillSwitchError(f"{label} must be numeric, got {value!r}") from exc
    if not math.isfinite(numeric):
        raise KillSwitchError(f"{label} must be finite, got {numeric!r}")
    return numeric


def _require_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KillSwitchError(f"{label} must be a non-empty string, got {value!r}")
    return value.strip()


@dataclass(frozen=True)
class FirmRiskLimits:
    """Firm-calibrated hard limits. None of these are regulatory constants.

    RTS 6 Art. 15(1) mandates that maximum order values, maximum order volumes
    and maximum message limits *exist*; it publishes no numbers. SEC Rule
    15c3-5(c)(1)(i) requires credit/capital thresholds to be "appropriate" and
    pre-set. Calibrate every value below against your own capital, venue
    message allowances and observed steady-state rates, and record the
    rationale -- the calibration is the audit artefact, not the number.

    Args:
        max_daily_loss_usd: Loss magnitude, expressed positive. Breach is
            evaluated against realized + unrealized PnL for the session.
        max_order_rate_per_sec: Order-entry attempts per second above which the
            algorithm is treated as looping. A house default of 100 is a
            reasonable starting point for a single strategy on a single venue
            and is wrong for a market maker; no rule sets this.
        max_net_exposure_usd: Absolute net exposure cap, expressed positive.
            Compared against ``abs(net_exposure_usd)`` so a runaway short
            breaches on the same limit as a runaway long.
        max_snapshot_age_seconds: Reject new orders when the risk snapshot is
            older than this. ``None`` disables the staleness gate, which means
            trusting the caller to only ever pass fresh risk data.
    """

    max_daily_loss_usd: float
    max_order_rate_per_sec: int
    max_net_exposure_usd: float
    max_snapshot_age_seconds: Optional[float] = DEFAULT_MAX_SNAPSHOT_AGE_SECONDS

    def __post_init__(self) -> None:
        for label in ("max_daily_loss_usd", "max_net_exposure_usd"):
            value = _require_finite(getattr(self, label), label)
            if value <= 0:
                raise KillSwitchError(f"{label} must be > 0, got {value}")
        if not isinstance(self.max_order_rate_per_sec, int) or isinstance(
            self.max_order_rate_per_sec, bool
        ):
            raise KillSwitchError("max_order_rate_per_sec must be an int")
        if self.max_order_rate_per_sec <= 0:
            raise KillSwitchError(
                f"max_order_rate_per_sec must be > 0, got {self.max_order_rate_per_sec}"
            )
        if self.max_snapshot_age_seconds is not None:
            age = _require_finite(self.max_snapshot_age_seconds, "max_snapshot_age_seconds")
            if age <= 0:
                raise KillSwitchError(f"max_snapshot_age_seconds must be > 0, got {age}")


@dataclass
class ActiveStrategyOrder:
    """An order the firm believes is live at a venue.

    ``venue`` matters: a mass cancel is scoped to one FIX session at one venue,
    so the engine must know where each order lives to know which gateway can
    kill it -- and which orders no configured gateway can reach.
    """

    cl_ord_id: str
    strategy_id: str
    symbol: str
    side: str
    order_qty: int
    price: float
    order_status: str = ORDER_NEW
    venue: str = "DEFAULT"
    filled_qty: int = 0

    def __post_init__(self) -> None:
        self.cl_ord_id = _require_id(self.cl_ord_id, "cl_ord_id")
        self.strategy_id = _require_id(self.strategy_id, "strategy_id")
        self.symbol = _require_id(self.symbol, "symbol")
        self.venue = _require_id(self.venue, "venue")
        side = _require_id(self.side, "side").upper()
        if side not in {"BUY", "SELL"}:
            raise KillSwitchError(f"side must be BUY or SELL, got {self.side!r}")
        self.side = side
        if not isinstance(self.order_qty, int) or isinstance(self.order_qty, bool):
            raise KillSwitchError("order_qty must be an int")
        if self.order_qty <= 0:
            raise KillSwitchError(f"order_qty must be > 0, got {self.order_qty}")
        if not isinstance(self.filled_qty, int) or isinstance(self.filled_qty, bool):
            raise KillSwitchError("filled_qty must be an int")
        if not 0 <= self.filled_qty <= self.order_qty:
            raise KillSwitchError(
                f"filled_qty must be within 0..order_qty, got {self.filled_qty}"
            )
        _require_finite(self.price, "price")
        self.order_status = _require_id(self.order_status, "order_status").upper()

    @property
    def remaining_qty(self) -> int:
        return self.order_qty - self.filled_qty

    @property
    def is_live(self) -> bool:
        """True while the venue may still trade this order."""
        return self.order_status in LIVE_ORDER_STATUSES

    @property
    def is_cancellable(self) -> bool:
        """True while a kill switch should still be chasing this order."""
        return self.order_status in CANCELLABLE_ORDER_STATUSES


@dataclass
class NewOrderRequest:
    """A child order the strategy wants to send."""

    cl_ord_id: str
    strategy_id: str
    symbol: str
    side: str
    order_qty: int
    price: float
    venue: str = "DEFAULT"

    def __post_init__(self) -> None:
        self.cl_ord_id = _require_id(self.cl_ord_id, "cl_ord_id")
        self.strategy_id = _require_id(self.strategy_id, "strategy_id")
        self.symbol = _require_id(self.symbol, "symbol")
        self.venue = _require_id(self.venue, "venue")
        self.side = _require_id(self.side, "side").upper()
        if not isinstance(self.order_qty, int) or isinstance(self.order_qty, bool):
            raise KillSwitchError("order_qty must be an int")
        if self.order_qty <= 0:
            raise KillSwitchError(f"order_qty must be > 0, got {self.order_qty}")
        _require_finite(self.price, "price")


@dataclass(frozen=True)
class RiskSnapshot:
    """Risk state as of a point in time, supplied by the caller.

    Args:
        daily_pnl_usd: **Signed** session PnL, realized + unrealized. Negative
            is a loss. This sign convention is the one field most likely to be
            wired up backwards; passing a positive loss magnitude here means
            the loss limit can never trigger, so the engine rejects an
            unambiguously-wrong reading rather than trusting it (see
            ``validate``).
        net_exposure_usd: Signed net exposure. Compared as an absolute value.
        order_rate_per_sec: Observed order-entry rate. ``None`` asks the engine
            to use its own sliding-window count of order requests it has seen,
            which is the safer default: a looping algorithm cannot under-report
            a rate the risk gate measures itself.
        as_of: When this snapshot was taken. Must be timezone-aware UTC.
    """

    daily_pnl_usd: float
    net_exposure_usd: float = 0.0
    order_rate_per_sec: Optional[int] = None
    as_of: Optional[datetime] = None

    def validate(self) -> None:
        _require_finite(self.daily_pnl_usd, "daily_pnl_usd")
        _require_finite(self.net_exposure_usd, "net_exposure_usd")
        if self.order_rate_per_sec is not None:
            if not isinstance(self.order_rate_per_sec, int) or isinstance(
                self.order_rate_per_sec, bool
            ):
                raise KillSwitchError("order_rate_per_sec must be an int or None")
            if self.order_rate_per_sec < 0:
                raise KillSwitchError(
                    f"order_rate_per_sec must be >= 0, got {self.order_rate_per_sec}"
                )
        if self.as_of is not None and self.as_of.tzinfo is None:
            raise KillSwitchError("RiskSnapshot.as_of must be timezone-aware UTC")


@dataclass(frozen=True)
class MassCancelRequest:
    """The FIX ``OrderMassCancelRequest`` (MsgType ``q``) this engine asks for.

    ``mass_cancel_req_id`` is FIX tag 11 (ClOrdID of the mass cancel itself),
    ``mass_cancel_request_type`` is tag 530, ``transact_time`` is tag 60.
    """

    mass_cancel_req_id: str
    mass_cancel_request_type: str
    venue: str
    transact_time: datetime
    symbol: Optional[str] = None


@dataclass(frozen=True)
class MassCancelOutcome:
    """What the venue said back, from ``OrderMassCancelReport`` (MsgType ``r``).

    Args:
        accepted: ``MassCancelResponse`` (tag 531) was not 0.
        mass_cancel_response: Tag 531 as sent by the venue, when known.
        reject_reason: ``MassCancelRejectReason`` (tag 532) when rejected.
            Value ``"0"`` is "Mass Cancel Not Supported" -- a venue answering
            that has cancelled nothing.
        text: Tag 58 free text.
    """

    accepted: bool
    mass_cancel_response: Optional[str] = None
    reject_reason: Optional[str] = None
    text: Optional[str] = None


@dataclass(frozen=True)
class MassCancelDispatch:
    """One cancel attempt against one venue, and what came back."""

    venue: str
    status: str
    mass_cancel_req_id: Optional[str] = None
    mass_cancel_request_type: Optional[str] = None
    orders_in_scope: int = 0
    reject_reason: Optional[str] = None
    detail: str = ""


class KillSwitchGateway(Protocol):
    """Venue adapter. One instance per venue/FIX session.

    Implementations must not raise for a *rejected* cancel -- return a
    ``MassCancelOutcome`` with ``accepted=False``. Exceptions are treated as
    transport failures, which are recorded as ``DISPATCH_ERROR`` and escalated.
    """

    def mass_cancel(self, request: MassCancelRequest) -> MassCancelOutcome:
        """Send ``OrderMassCancelRequest`` and return the venue's report."""

    def cancel_orders(self, cl_ord_ids: Sequence[str]) -> Mapping[str, bool]:
        """Send an ``OrderCancelRequest`` per id; map id -> accepted-for-cancel."""


class InMemoryKillSwitchGateway:
    """Reference/simulation gateway. Records what it was asked to do.

    Not a venue: it acknowledges the *request*, exactly as a real venue does.
    Confirmation that an order is dead still arrives separately, through
    ``ExecutionReport`` -> :meth:`ExecutionAlgoKillSwitchEngine.apply_execution_report`.
    """

    def __init__(
        self,
        venue: str = "DEFAULT",
        *,
        outcome: Optional[MassCancelOutcome] = None,
        raises: Optional[Exception] = None,
        unsupported_order_ids: Iterable[str] = (),
    ) -> None:
        self.venue = _require_id(venue, "venue")
        self._outcome = outcome or MassCancelOutcome(accepted=True, mass_cancel_response="7")
        self._raises = raises
        self._unsupported_order_ids = frozenset(unsupported_order_ids)
        self.mass_cancel_requests: List[MassCancelRequest] = []
        self.cancelled_order_ids: List[str] = []

    def mass_cancel(self, request: MassCancelRequest) -> MassCancelOutcome:
        if self._raises is not None:
            raise self._raises
        self.mass_cancel_requests.append(request)
        return self._outcome

    def cancel_orders(self, cl_ord_ids: Sequence[str]) -> Mapping[str, bool]:
        if self._raises is not None:
            raise self._raises
        result: Dict[str, bool] = {}
        for cl_ord_id in cl_ord_ids:
            accepted = cl_ord_id not in self._unsupported_order_ids
            if accepted:
                self.cancelled_order_ids.append(cl_ord_id)
            result[cl_ord_id] = accepted
        return result


@dataclass(frozen=True)
class KillSwitchAuditReport:
    """The record of one decision. Approvals, breaches and denials all produce one.

    ``cancel_requested_count`` counts orders a cancel was *sent for*.
    ``pending_cancel_order_ids`` are the orders still unconfirmed, and
    ``uncancelled_order_ids`` are the ones no gateway accepted a cancel for --
    those two lists, not the request count, are what an operator must act on.
    """

    event_id: str
    timestamp_utc: str
    is_kill_switch_active: bool
    trigger_reason: Optional[str]
    trigger_reason_code: Optional[str]
    trigger_scope: str
    scope_target: Optional[str]
    cancel_requested_count: int
    fix_mass_cancel_tag_530: Optional[str]
    is_new_order_blocked: bool
    status: str
    audit_notes: str
    triggered_by: str = "SYSTEM"
    dispatches: Tuple[MassCancelDispatch, ...] = ()
    pending_cancel_order_ids: Tuple[str, ...] = ()
    uncancelled_order_ids: Tuple[str, ...] = ()
    manual_intervention_required: bool = False
    is_repeat_trigger: bool = False

    @property
    def is_fully_dispatched(self) -> bool:
        """True when every in-scope order had a cancel accepted by its venue."""
        return not self.uncancelled_order_ids


class ExecutionAlgoKillSwitchEngine:
    """Multi-level kill switch: order-entry lockout plus venue cancellation.

    Thread-safe: every public method takes a re-entrant lock. A kill switch
    sits in the order-submission path of a concurrent system, so a check-then-act
    race here means orders leaking out after the switch is engaged.

    Args:
        limits: Firm-calibrated hard limits.
        gateways: Venue adapters keyed by venue id. Omitted entirely, the engine
            still latches the lockout (which needs no network) but reports every
            cancel as undispatched and escalates -- it never reports success it
            did not get.
        clock: Injectable UTC clock, for deterministic tests and replay.
    """

    def __init__(
        self,
        limits: FirmRiskLimits,
        gateways: Optional[Mapping[str, KillSwitchGateway]] = None,
        *,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if not isinstance(limits, FirmRiskLimits):
            raise KillSwitchError("limits must be a FirmRiskLimits instance")
        self.limits = limits
        self._gateways: Dict[str, KillSwitchGateway] = dict(gateways or {})
        self._clock = clock or _utc_now
        self._lock = threading.RLock()

        self._global_kill_engaged = False
        self._global_kill_reason: Optional[str] = None
        self._global_kill_engaged_at: Optional[datetime] = None
        self._strategy_kills: Dict[str, str] = {}
        self._active_orders: Dict[str, ActiveStrategyOrder] = {}
        self._manual_intervention_required = False
        self._audit_trail: List[KillSwitchAuditReport] = []
        self._event_seq = 0
        self._order_entry_times: List[datetime] = []

    # ------------------------------------------------------------------ state

    @property
    def is_global_kill_switch_engaged(self) -> bool:
        with self._lock:
            return self._global_kill_engaged

    @property
    def engaged_strategy_kills(self) -> Dict[str, str]:
        with self._lock:
            return dict(self._strategy_kills)

    @property
    def active_orders(self) -> Dict[str, ActiveStrategyOrder]:
        with self._lock:
            return dict(self._active_orders)

    @property
    def manual_intervention_required(self) -> bool:
        """True when a cancel was rejected, errored, or had nowhere to go."""
        with self._lock:
            return self._manual_intervention_required

    @property
    def pending_cancel_order_ids(self) -> Tuple[str, ...]:
        """Orders a cancel was accepted for, still awaiting an ExecutionReport."""
        with self._lock:
            return tuple(
                sorted(
                    cl_ord_id
                    for cl_ord_id, order in self._active_orders.items()
                    if order.order_status == ORDER_PENDING_CANCEL
                )
            )

    @property
    def audit_trail(self) -> Tuple[KillSwitchAuditReport, ...]:
        with self._lock:
            return tuple(self._audit_trail)

    def is_blocked(self, strategy_id: Optional[str] = None) -> bool:
        """True when order entry is locked globally or for ``strategy_id``."""
        with self._lock:
            if self._global_kill_engaged:
                return True
            return bool(strategy_id) and strategy_id in self._strategy_kills

    # ------------------------------------------------------------- order book

    def register_active_order(self, order: ActiveStrategyOrder) -> None:
        """Record an order believed live at a venue.

        Rejects a duplicate ``cl_ord_id``: silently overwriting one loses the
        engine's only handle on the original order, which is still live at the
        venue and would never be cancelled.
        """
        if not isinstance(order, ActiveStrategyOrder):
            raise KillSwitchError("order must be an ActiveStrategyOrder instance")
        with self._lock:
            existing = self._active_orders.get(order.cl_ord_id)
            if existing is not None and existing.order_status not in TERMINAL_ORDER_STATUSES:
                raise KillSwitchError(
                    f"duplicate live cl_ord_id {order.cl_ord_id!r}; "
                    "ClOrdID must be unique per order"
                )
            self._active_orders[order.cl_ord_id] = order
            if self.is_blocked(order.strategy_id):
                # Almost always a late ack for an order that was already in
                # flight when the latch closed. It is live at the venue and the
                # cancel sweep has already run, so it will sit there untouched
                # until the switch is re-fired.
                logger.critical(
                    "Order %s registered while the kill switch is ENGAGED "
                    "(strategy=%s venue=%s). It was NOT covered by the cancel "
                    "sweep -- re-trigger the kill switch or cancel it by hand.",
                    order.cl_ord_id,
                    order.strategy_id,
                    order.venue,
                )

    def apply_execution_report(
        self,
        cl_ord_id: str,
        order_status: str,
        filled_qty: Optional[int] = None,
    ) -> ActiveStrategyOrder:
        """Apply a venue ``ExecutionReport``. This is what confirms a cancel.

        A fill arriving for an order in ``PENDING_CANCEL`` is logged at warning
        level: the order traded in the race between the cancel request and the
        matching engine, so post-kill exposure changed and the flattening
        decision has to be revisited by a human.
        """
        cl_ord_id = _require_id(cl_ord_id, "cl_ord_id")
        status = _require_id(order_status, "order_status").upper()
        if status not in KNOWN_ORDER_STATUSES:
            # An unrecognised status is neither live nor terminal, so the order
            # would silently drop out of every cancel sweep while still working
            # at the venue. Map the venue's status in the adapter instead.
            raise KillSwitchError(
                f"unknown order_status {order_status!r}; expected one of "
                f"{sorted(KNOWN_ORDER_STATUSES)}"
            )
        with self._lock:
            order = self._active_orders.get(cl_ord_id)
            if order is None:
                raise KillSwitchError(f"unknown cl_ord_id {cl_ord_id!r}")
            if status == ORDER_FILLED and order.order_status == ORDER_PENDING_CANCEL:
                logger.warning(
                    "Order %s FILLED after a cancel request was accepted "
                    "(strategy=%s venue=%s): post-kill exposure changed.",
                    cl_ord_id,
                    order.strategy_id,
                    order.venue,
                )
            if filled_qty is not None:
                if not isinstance(filled_qty, int) or isinstance(filled_qty, bool):
                    raise KillSwitchError("filled_qty must be an int")
                if not 0 <= filled_qty <= order.order_qty:
                    raise KillSwitchError(
                        f"filled_qty must be within 0..{order.order_qty}, got {filled_qty}"
                    )
                order.filled_qty = filled_qty
            order.order_status = status
            return order

    # -------------------------------------------------------------- triggers

    def trigger_kill_switch(
        self,
        scope: str,
        reason: str,
        strategy_id: Optional[str] = None,
        *,
        triggered_by: str = "SYSTEM",
        reason_code: Optional[str] = None,
    ) -> KillSwitchAuditReport:
        """Latch the lockout, then dispatch cancels, then report what happened.

        Raises:
            KillSwitchError: for an unknown scope, or ``SCOPE_STRATEGY`` with no
                ``strategy_id``. A kill-switch call that cannot be understood
                must fail loudly -- returning a "no action taken" report would
                let a typo read as normal operations.
        """
        scope_clean = _require_id(scope, "scope").upper()
        reason = _require_id(reason, "reason")
        triggered_by = _require_id(triggered_by, "triggered_by")
        if scope_clean not in (SCOPE_GLOBAL, SCOPE_STRATEGY):
            raise KillSwitchError(
                f"unknown kill switch scope {scope!r}; expected "
                f"{SCOPE_GLOBAL!r} or {SCOPE_STRATEGY!r}"
            )
        if scope_clean == SCOPE_STRATEGY:
            if not strategy_id or not str(strategy_id).strip():
                raise KillSwitchError("strategy_id is required for STRATEGY scope")
            strategy_id = str(strategy_id).strip()

        with self._lock:
            now = self._now()
            event_id = self._next_event_id(now)

            # 1. Latch the lockout FIRST. It cannot fail and it closes the gap
            #    through which a looping algorithm would refill the book while
            #    cancels are in flight.
            if scope_clean == SCOPE_GLOBAL:
                is_repeat = self._global_kill_engaged
                self._global_kill_engaged = True
                if not is_repeat:
                    self._global_kill_reason = reason
                    self._global_kill_engaged_at = now
                in_scope = [o for o in self._active_orders.values() if o.is_cancellable]
            else:
                is_repeat = strategy_id in self._strategy_kills
                if not is_repeat:
                    self._strategy_kills[strategy_id] = reason
                in_scope = [
                    o
                    for o in self._active_orders.values()
                    if o.is_cancellable and o.strategy_id == strategy_id
                ]

            # 2. Dispatch cancellation and record only what the venues accepted.
            if scope_clean == SCOPE_GLOBAL:
                dispatches, uncancelled = self._dispatch_global_cancel(
                    in_scope, event_id, now
                )
                tag_530: Optional[str] = MASS_CANCEL_ALL_ORDERS
            else:
                dispatches, uncancelled = self._dispatch_order_by_order_cancel(
                    in_scope, event_id
                )
                # Deliberately None: FIX tag 530 has no per-strategy scope, and
                # claiming 530=1 here would describe a security-wide cancel.
                tag_530 = None

            notes = self._trigger_notes(
                scope_clean, strategy_id, reason, len(in_scope), uncancelled, is_repeat
            )
            log = logger.critical if scope_clean == SCOPE_GLOBAL else logger.warning
            log(notes)

            report = KillSwitchAuditReport(
                event_id=event_id,
                timestamp_utc=_iso(now),
                is_kill_switch_active=True,
                trigger_reason=reason,
                trigger_reason_code=reason_code or TRIGGER_MANUAL_OVERRIDE,
                trigger_scope=scope_clean,
                scope_target=strategy_id if scope_clean == SCOPE_STRATEGY else None,
                cancel_requested_count=len(in_scope),
                fix_mass_cancel_tag_530=tag_530,
                is_new_order_blocked=True,
                status=STATUS_ENGAGED,
                audit_notes=notes,
                triggered_by=triggered_by,
                dispatches=tuple(dispatches),
                pending_cancel_order_ids=self.pending_cancel_order_ids,
                uncancelled_order_ids=tuple(sorted(uncancelled)),
                manual_intervention_required=self._manual_intervention_required,
                is_repeat_trigger=is_repeat,
            )
            self._audit_trail.append(report)
            return report

    def evaluate_risk_state(
        self,
        snapshot: RiskSnapshot,
        *,
        triggered_by: str = "RISK_MONITOR",
    ) -> KillSwitchAuditReport:
        """Evaluate limits without an order in hand -- the supervisory path.

        A kill switch that only evaluates risk when the strategy submits an
        order cannot fire once the strategy stops submitting, which is exactly
        what a stuck algorithm holding a losing position does. Call this on a
        timer as well as on the order path.
        """
        snapshot.validate()
        with self._lock:
            breach = self._first_breach(snapshot)
            if breach is None:
                return self._normal_report(
                    "Risk limits within tolerance.", triggered_by=triggered_by
                )
            reason_code, reason = breach
            return self.trigger_kill_switch(
                SCOPE_GLOBAL,
                reason,
                triggered_by=triggered_by,
                reason_code=reason_code,
            )

    def audit_and_validate_new_order(
        self,
        req: NewOrderRequest,
        snapshot: RiskSnapshot,
        *,
        count_toward_rate: bool = True,
    ) -> KillSwitchAuditReport:
        """Gate one order: kill-switch state first, then live risk limits.

        Order-entry attempts are counted before any decision, so an algorithm
        whose orders are all being rejected still trips the runaway-rate limit
        -- a rejection loop is a message-rate problem (RTS 6 Art. 15(1) maximum
        message limits) even though none of it reaches the book.
        """
        if not isinstance(req, NewOrderRequest):
            raise KillSwitchError("req must be a NewOrderRequest instance")

        with self._lock:
            now = self._now()
            if count_toward_rate:
                self._record_order_attempt(now)

            if self._global_kill_engaged:
                msg = (
                    f"ORDER REJECTED [{req.cl_ord_id}]: global kill switch engaged "
                    f"({self._global_kill_reason})."
                )
                logger.critical(msg)
                return self._rejection_report(
                    msg,
                    reason=self._global_kill_reason,
                    reason_code=TRIGGER_KILL_SWITCH_ACTIVE,
                    scope=SCOPE_GLOBAL,
                    scope_target=None,
                    status=STATUS_REJECTED_KILL_SWITCH,
                )

            if req.strategy_id in self._strategy_kills:
                reason = self._strategy_kills[req.strategy_id]
                msg = (
                    f"ORDER REJECTED [{req.cl_ord_id}]: strategy "
                    f"{req.strategy_id!r} kill switch engaged ({reason})."
                )
                logger.warning(msg)
                return self._rejection_report(
                    msg,
                    reason=reason,
                    reason_code=TRIGGER_KILL_SWITCH_ACTIVE,
                    scope=SCOPE_STRATEGY,
                    scope_target=req.strategy_id,
                    status=STATUS_REJECTED_KILL_SWITCH,
                )

            # Unusable risk data is a fail-closed condition for this order, not
            # a firm-wide kill: a broken telemetry feed is not evidence that the
            # firm is losing money, but it is proof the limit cannot be checked.
            try:
                snapshot.validate()
            except KillSwitchError as exc:
                msg = (
                    f"ORDER REJECTED [{req.cl_ord_id}]: risk data unusable ({exc}). "
                    "Limits cannot be evaluated; failing closed."
                )
                logger.error(msg)
                return self._rejection_report(
                    msg,
                    reason=str(exc),
                    reason_code=TRIGGER_RISK_DATA_INVALID,
                    scope=SCOPE_NONE,
                    scope_target=None,
                    status=STATUS_REJECTED_RISK_DATA,
                )

            stale = self._staleness_reason(snapshot, now)
            if stale is not None:
                msg = f"ORDER REJECTED [{req.cl_ord_id}]: {stale}"
                logger.error(msg)
                return self._rejection_report(
                    msg,
                    reason=stale,
                    reason_code=TRIGGER_RISK_DATA_STALE,
                    scope=SCOPE_NONE,
                    scope_target=None,
                    status=STATUS_REJECTED_RISK_DATA,
                )

            breach = self._first_breach(snapshot, now=now)
            if breach is not None:
                reason_code, reason = breach
                return self.trigger_kill_switch(
                    SCOPE_GLOBAL,
                    reason,
                    triggered_by="PRE_TRADE_RISK_GATE",
                    reason_code=reason_code,
                )

            notes = (
                f"ORDER APPROVED [{req.cl_ord_id}]: strategy {req.strategy_id!r}, "
                f"venue {req.venue!r}, risk limits within tolerance."
            )
            return self._normal_report(notes, triggered_by="PRE_TRADE_RISK_GATE")

    def reset(
        self,
        scope: str,
        *,
        authorized_by: str,
        reason: str,
        strategy_id: Optional[str] = None,
        acknowledge_unconfirmed: bool = False,
    ) -> KillSwitchAuditReport:
        """Re-arm order entry after a kill switch. Never automatic.

        Refuses while any cancel is unconfirmed or a dispatch failed, unless
        ``acknowledge_unconfirmed=True`` records that a named human checked the
        venue book by hand. Resuming into orders you believe are dead and are
        not is how a kill switch turns into a double position.

        Resetting this engine does **not** lift a venue-side kill switch. Nasdaq
        Rule 6130, for instance, disables the MPID's order-entry ports and
        requires the participant to ask Nasdaq operations to reactivate them.
        """
        scope_clean = _require_id(scope, "scope").upper()
        authorized_by = _require_id(authorized_by, "authorized_by")
        reason = _require_id(reason, "reason")
        if scope_clean not in (SCOPE_GLOBAL, SCOPE_STRATEGY):
            raise KillSwitchError(f"unknown reset scope {scope!r}")
        if scope_clean == SCOPE_STRATEGY:
            if not strategy_id or not str(strategy_id).strip():
                raise KillSwitchError("strategy_id is required for STRATEGY scope")
            strategy_id = str(strategy_id).strip()

        with self._lock:
            pending = self.pending_cancel_order_ids
            blocked = bool(pending) or self._manual_intervention_required
            if blocked and not acknowledge_unconfirmed:
                raise KillSwitchError(
                    "cannot reset while cancels are unconfirmed "
                    f"(pending={list(pending)}, "
                    f"manual_intervention_required={self._manual_intervention_required}); "
                    "reconcile the venue book, then pass acknowledge_unconfirmed=True"
                )

            if scope_clean == SCOPE_GLOBAL:
                self._global_kill_engaged = False
                self._global_kill_reason = None
                self._global_kill_engaged_at = None
                self._manual_intervention_required = False
            else:
                self._strategy_kills.pop(strategy_id, None)

            now = self._now()
            notes = (
                f"KILL SWITCH RESET [{scope_clean}"
                f"{':' + strategy_id if strategy_id else ''}] by {authorized_by}: {reason}. "
                f"Unconfirmed cancels acknowledged: {acknowledge_unconfirmed}. "
                "Venue-side kill switches are NOT lifted by this reset."
            )
            logger.critical(notes)
            report = KillSwitchAuditReport(
                event_id=self._next_event_id(now),
                timestamp_utc=_iso(now),
                is_kill_switch_active=self.is_blocked(strategy_id),
                trigger_reason=reason,
                trigger_reason_code=None,
                trigger_scope=scope_clean,
                scope_target=strategy_id if scope_clean == SCOPE_STRATEGY else None,
                cancel_requested_count=0,
                fix_mass_cancel_tag_530=None,
                is_new_order_blocked=self.is_blocked(strategy_id),
                status=STATUS_RESET,
                audit_notes=notes,
                triggered_by=authorized_by,
                pending_cancel_order_ids=pending,
                manual_intervention_required=self._manual_intervention_required,
            )
            self._audit_trail.append(report)
            return report

    # -------------------------------------------------------------- internals

    def _now(self) -> datetime:
        ts = self._clock()
        if not isinstance(ts, datetime):
            raise KillSwitchError("clock must return a datetime")
        if ts.tzinfo is None:
            raise KillSwitchError("clock must return a timezone-aware UTC datetime")
        return ts.astimezone(timezone.utc)

    def _next_event_id(self, now: datetime) -> str:
        self._event_seq += 1
        return f"KS-{now.strftime('%Y%m%dT%H%M%S')}-{self._event_seq:06d}"

    def _record_order_attempt(self, now: datetime) -> None:
        """Keep a 1-second sliding window of order-entry attempts."""
        self._order_entry_times.append(now)
        cutoff = now.timestamp() - 1.0
        if len(self._order_entry_times) > 1 and self._order_entry_times[0].timestamp() <= cutoff:
            self._order_entry_times = [
                ts for ts in self._order_entry_times if ts.timestamp() > cutoff
            ]

    def _observed_order_rate(self, now: datetime) -> int:
        cutoff = now.timestamp() - 1.0
        return sum(1 for ts in self._order_entry_times if ts.timestamp() > cutoff)

    def _staleness_reason(self, snapshot: RiskSnapshot, now: datetime) -> Optional[str]:
        max_age = self.limits.max_snapshot_age_seconds
        if max_age is None or snapshot.as_of is None:
            return None
        age = (now - snapshot.as_of).total_seconds()
        if age > max_age:
            return (
                f"RISK DATA STALE: snapshot age {age:.3f}s exceeds "
                f"max_snapshot_age_seconds {max_age:.3f}s"
            )
        if age < -max_age:
            # A snapshot from the future means the risk feed's clock runs ahead
            # of ours. Left unchecked, that clock makes arbitrarily old data
            # look fresh forever, which silently disables the staleness gate.
            return (
                f"RISK DATA CLOCK SKEW: snapshot is {-age:.3f}s in the future, "
                f"beyond max_snapshot_age_seconds {max_age:.3f}s; the staleness "
                "gate cannot be trusted"
            )
        return None

    def _first_breach(
        self, snapshot: RiskSnapshot, now: Optional[datetime] = None
    ) -> Optional[Tuple[str, str]]:
        """Return the first breached limit as ``(reason_code, human reason)``.

        Comparisons are ``>=``: a loss that *reaches* the limit has breached it.
        A control that waits for strictly-greater lets the limit itself be the
        one loss you never stop.
        """
        loss = -snapshot.daily_pnl_usd
        if loss >= self.limits.max_daily_loss_usd:
            return (
                TRIGGER_MAX_LOSS_BREACH,
                f"MAX DAILY LOSS BREACH: session PnL ${snapshot.daily_pnl_usd:,.2f} "
                f"(loss ${loss:,.2f}) >= limit ${self.limits.max_daily_loss_usd:,.2f}",
            )

        exposure = abs(snapshot.net_exposure_usd)
        if exposure >= self.limits.max_net_exposure_usd:
            return (
                TRIGGER_MAX_EXPOSURE_BREACH,
                f"MAX EXPOSURE BREACH: |net exposure| ${exposure:,.2f} >= limit "
                f"${self.limits.max_net_exposure_usd:,.2f}",
            )

        rate = snapshot.order_rate_per_sec
        source = "reported"
        if rate is None:
            rate = self._observed_order_rate(now or self._now())
            source = "observed"
        if rate >= self.limits.max_order_rate_per_sec:
            return (
                TRIGGER_RUNAWAY_ORDER_RATE,
                f"RUNAWAY ORDER RATE: {source} rate {rate}/sec >= limit "
                f"{self.limits.max_order_rate_per_sec}/sec",
            )
        return None

    def _dispatch_global_cancel(
        self, in_scope: Sequence[ActiveStrategyOrder], event_id: str, now: datetime
    ) -> Tuple[List[MassCancelDispatch], List[str]]:
        """Send ``MassCancelRequestType=7`` to every configured gateway.

        Every gateway, not only those with orders in the local book: the local
        book is the firm's *belief* about what is live, and a missed
        ExecutionReport means an order exists that this engine cannot see. RTS 6
        Art. 12(1) is about the orders at the venue, not the ones in your map.
        """
        dispatches: List[MassCancelDispatch] = []
        uncancelled: List[str] = []
        by_venue: Dict[str, List[ActiveStrategyOrder]] = {}
        for order in in_scope:
            by_venue.setdefault(order.venue, []).append(order)

        if not self._gateways:
            self._manual_intervention_required = True
            detail = (
                "No gateway configured: order entry is LOCKED locally but NO cancel "
                "was sent. Cancel by hand at every venue."
            )
            logger.critical(detail)
            dispatches.append(
                MassCancelDispatch(
                    venue="*",
                    status=DISPATCH_NO_GATEWAY,
                    orders_in_scope=len(in_scope),
                    detail=detail,
                )
            )
            uncancelled.extend(order.cl_ord_id for order in in_scope)
            return dispatches, uncancelled

        for venue in sorted(set(self._gateways) | set(by_venue)):
            venue_orders = by_venue.get(venue, [])
            gateway = self._gateways.get(venue)
            if gateway is None:
                self._manual_intervention_required = True
                detail = (
                    f"No gateway configured for venue {venue!r}; "
                    f"{len(venue_orders)} order(s) cannot be cancelled by this engine."
                )
                logger.critical(detail)
                dispatches.append(
                    MassCancelDispatch(
                        venue=venue,
                        status=DISPATCH_NO_GATEWAY,
                        orders_in_scope=len(venue_orders),
                        detail=detail,
                    )
                )
                uncancelled.extend(o.cl_ord_id for o in venue_orders)
                continue

            request = MassCancelRequest(
                mass_cancel_req_id=f"{event_id}-{venue}",
                mass_cancel_request_type=MASS_CANCEL_ALL_ORDERS,
                venue=venue,
                transact_time=now,
            )
            dispatch, accepted = self._send_mass_cancel(gateway, request, len(venue_orders))
            dispatches.append(dispatch)
            if accepted:
                for order in venue_orders:
                    order.order_status = ORDER_PENDING_CANCEL
            else:
                uncancelled.extend(o.cl_ord_id for o in venue_orders)
        return dispatches, uncancelled

    def _send_mass_cancel(
        self, gateway: KillSwitchGateway, request: MassCancelRequest, orders_in_scope: int
    ) -> Tuple[MassCancelDispatch, bool]:
        try:
            outcome = gateway.mass_cancel(request)
        except Exception as exc:  # one venue's transport failure must not stop the rest
            self._manual_intervention_required = True
            logger.critical(
                "Mass cancel transport failure at venue %s: %s",
                request.venue,
                exc,
                exc_info=True,
            )
            return (
                MassCancelDispatch(
                    venue=request.venue,
                    status=DISPATCH_ERROR,
                    mass_cancel_req_id=request.mass_cancel_req_id,
                    mass_cancel_request_type=request.mass_cancel_request_type,
                    orders_in_scope=orders_in_scope,
                    detail=f"{type(exc).__name__}: {exc}",
                ),
                False,
            )

        if not isinstance(outcome, MassCancelOutcome):
            self._manual_intervention_required = True
            raise KillSwitchError(
                f"gateway for venue {request.venue!r} returned "
                f"{type(outcome).__name__}, expected MassCancelOutcome"
            )

        if outcome.accepted:
            return (
                MassCancelDispatch(
                    venue=request.venue,
                    status=DISPATCH_ACCEPTED,
                    mass_cancel_req_id=request.mass_cancel_req_id,
                    mass_cancel_request_type=request.mass_cancel_request_type,
                    orders_in_scope=orders_in_scope,
                    detail=outcome.text or "OrderMassCancelReport accepted (tag 531 != 0).",
                ),
                True,
            )

        self._manual_intervention_required = True
        logger.critical(
            "Venue %s REJECTED the mass cancel (tag 532=%s). %d order(s) may still be live.",
            request.venue,
            outcome.reject_reason,
            orders_in_scope,
        )
        return (
            MassCancelDispatch(
                venue=request.venue,
                status=DISPATCH_REJECTED,
                mass_cancel_req_id=request.mass_cancel_req_id,
                mass_cancel_request_type=request.mass_cancel_request_type,
                orders_in_scope=orders_in_scope,
                reject_reason=outcome.reject_reason,
                detail=outcome.text or "OrderMassCancelReport rejected (tag 531=0).",
            ),
            False,
        )

    def _dispatch_order_by_order_cancel(
        self, in_scope: Sequence[ActiveStrategyOrder], event_id: str
    ) -> Tuple[List[MassCancelDispatch], List[str]]:
        """Cancel a strategy's orders individually.

        FIX tag 530 cannot express "this strategy": its narrowest scope is a
        security, which would cancel other strategies' orders in that symbol and
        miss this strategy's orders elsewhere. Where a venue supports the FIX
        5.0 SP2 ``TargetParties`` component on ``OrderMassCancelRequest``, a
        party-scoped mass cancel is faster -- put that in the gateway adapter.
        """
        dispatches: List[MassCancelDispatch] = []
        uncancelled: List[str] = []
        by_venue: Dict[str, List[ActiveStrategyOrder]] = {}
        for order in in_scope:
            by_venue.setdefault(order.venue, []).append(order)

        for venue, venue_orders in sorted(by_venue.items()):
            gateway = self._gateways.get(venue)
            if gateway is None:
                self._manual_intervention_required = True
                detail = (
                    f"No gateway configured for venue {venue!r}; "
                    f"{len(venue_orders)} order(s) cannot be cancelled by this engine."
                )
                logger.critical(detail)
                dispatches.append(
                    MassCancelDispatch(
                        venue=venue,
                        status=DISPATCH_NO_GATEWAY,
                        orders_in_scope=len(venue_orders),
                        detail=detail,
                    )
                )
                uncancelled.extend(o.cl_ord_id for o in venue_orders)
                continue

            ids = [o.cl_ord_id for o in venue_orders]
            try:
                results = gateway.cancel_orders(ids)
            except Exception as exc:
                self._manual_intervention_required = True
                logger.critical(
                    "Order-by-order cancel transport failure at venue %s: %s",
                    venue,
                    exc,
                    exc_info=True,
                )
                dispatches.append(
                    MassCancelDispatch(
                        venue=venue,
                        status=DISPATCH_ERROR,
                        mass_cancel_req_id=f"{event_id}-{venue}",
                        orders_in_scope=len(venue_orders),
                        detail=f"{type(exc).__name__}: {exc}",
                    )
                )
                uncancelled.extend(ids)
                continue

            accepted_ids = [i for i in ids if results.get(i)]
            refused_ids = [i for i in ids if not results.get(i)]
            for order in venue_orders:
                if order.cl_ord_id in accepted_ids:
                    order.order_status = ORDER_PENDING_CANCEL
            if refused_ids:
                self._manual_intervention_required = True
                logger.critical(
                    "Venue %s refused cancellation of %d order(s): %s",
                    venue,
                    len(refused_ids),
                    refused_ids,
                )
            uncancelled.extend(refused_ids)
            dispatches.append(
                MassCancelDispatch(
                    venue=venue,
                    status=DISPATCH_ACCEPTED if not refused_ids else DISPATCH_REJECTED,
                    mass_cancel_req_id=f"{event_id}-{venue}",
                    orders_in_scope=len(venue_orders),
                    detail=(
                        f"{len(accepted_ids)} OrderCancelRequest(s) accepted, "
                        f"{len(refused_ids)} refused."
                    ),
                )
            )
        return dispatches, uncancelled

    def _trigger_notes(
        self,
        scope: str,
        strategy_id: Optional[str],
        reason: str,
        in_scope_count: int,
        uncancelled: Sequence[str],
        is_repeat: bool,
    ) -> str:
        target = f" [{strategy_id}]" if strategy_id else ""
        repeat = " (repeat trigger)" if is_repeat else ""
        head = (
            f"{scope} KILL SWITCH ENGAGED{target}{repeat}: reason = {reason!r}. "
            f"Order entry LOCKED. Cancel requested for {in_scope_count} live order(s)."
        )
        if uncancelled:
            return (
                f"{head} {len(uncancelled)} order(s) had NO cancel accepted and may still "
                f"be live: {list(uncancelled)}. MANUAL INTERVENTION REQUIRED."
            )
        return (
            f"{head} Cancels are REQUESTED, not confirmed: orders are dead only when "
            "the venue's ExecutionReports arrive."
        )

    def _normal_report(self, notes: str, *, triggered_by: str) -> KillSwitchAuditReport:
        now = self._now()
        return KillSwitchAuditReport(
            event_id=self._next_event_id(now),
            timestamp_utc=_iso(now),
            is_kill_switch_active=False,
            trigger_reason=None,
            trigger_reason_code=None,
            trigger_scope=SCOPE_NONE,
            scope_target=None,
            cancel_requested_count=0,
            fix_mass_cancel_tag_530=None,
            is_new_order_blocked=False,
            status=STATUS_NORMAL,
            audit_notes=notes,
            triggered_by=triggered_by,
            manual_intervention_required=self._manual_intervention_required,
        )

    def _rejection_report(
        self,
        notes: str,
        *,
        reason: Optional[str],
        reason_code: str,
        scope: str,
        scope_target: Optional[str],
        status: str,
    ) -> KillSwitchAuditReport:
        now = self._now()
        return KillSwitchAuditReport(
            event_id=self._next_event_id(now),
            timestamp_utc=_iso(now),
            is_kill_switch_active=status == STATUS_REJECTED_KILL_SWITCH,
            trigger_reason=reason,
            trigger_reason_code=reason_code,
            trigger_scope=scope,
            scope_target=scope_target,
            cancel_requested_count=0,
            fix_mass_cancel_tag_530=None,
            is_new_order_blocked=True,
            status=status,
            audit_notes=notes,
            triggered_by="PRE_TRADE_RISK_GATE",
            pending_cancel_order_ids=self.pending_cancel_order_ids,
            manual_intervention_required=self._manual_intervention_required,
        )

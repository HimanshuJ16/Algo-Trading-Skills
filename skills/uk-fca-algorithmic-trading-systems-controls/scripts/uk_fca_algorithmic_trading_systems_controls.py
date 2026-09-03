"""UK FCA algorithmic trading systems and controls - pre-trade order-entry gate.

Implements the order-entry controls that MiFID RTS 6 (Commission Delegated
Regulation (EU) 2017/589, as it forms part of UK law) Article 15 requires an
investment firm engaged in algorithmic trading to carry out, together with the
Article 12 kill functionality, under the FCA Handbook rule that sits above them
(MAR 7A.3.2R).

Article map (verified against the FCA Handbook rendering of MiFID RTS 6 and the
Commission text of Regulation (EU) 2017/589):

    Art. 9   Annual self-assessment and validation      (governance, out of scope)
    Art. 10  Stress testing                             (out of scope)
    Art. 12  Kill functionality                         -> trigger_kill_switch()
    Art. 13  Automated surveillance for market abuse    (out of scope)
    Art. 14  Business continuity arrangements           (out of scope)
    Art. 15  Pre-trade controls on order entry          -> evaluate_pre_trade_controls()
    Art. 16  Real-time monitoring                       (out of scope)
    Art. 17  Post-trade controls                        (out of scope)

RTS 6 prescribes NO numeric value for any of these controls. Every default in
``RTS6ControlConfig`` is an engineering placeholder that a firm must replace with
its own calibrated limit under Art. 15(4) ("based on its capital base, its
clearing arrangements, its trading strategy, its risk tolerance ...").
"""

from __future__ import annotations

import datetime
import logging
import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Deque, Dict, List, Optional

logger = logging.getLogger(__name__)

#: Kill-switch key meaning "every algorithm" (RTS 6 Art. 12(1), "any or all").
GLOBAL_SCOPE = "*"


def _utc_now() -> datetime.datetime:
    """Timezone-aware UTC timestamp (``datetime.utcnow`` is deprecated and naive)."""
    return datetime.datetime.now(datetime.timezone.utc)


def _is_finite_positive(value: object) -> bool:
    """True only for a real, finite, strictly positive number (bools excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and float(value) > 0.0


def _is_finite_non_negative(value: object) -> bool:
    """True only for a real, finite, non-negative number (bools excluded)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and float(value) >= 0.0


class ControlStatus(Enum):
    PASSED = "PASSED"
    REJECTED = "REJECTED"
    THROTTLED = "THROTTLED"
    KILL_SWITCH_ACTIVATED = "KILL_SWITCH_ACTIVATED"


class ViolationType(Enum):
    NONE = "NONE"
    # RTS 6 Art. 12 - emergency halt latched, no order may pass.
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    # MAR 7A.3.2R(3) - erroneous order fields (NaN/Inf/non-positive/unknown side).
    INVALID_ORDER = "INVALID_ORDER"
    # RTS 6 Art. 15(1)(a) - no usable reference price, so the collar cannot be evaluated.
    INVALID_REFERENCE_PRICE = "INVALID_REFERENCE_PRICE"
    # MAR 7A.3.2R(1) - message/capacity state unusable, so the ceiling cannot be evaluated.
    INVALID_CAPACITY_STATE = "INVALID_CAPACITY_STATE"
    # RTS 6 Art. 15(1)(a) - order outside the firm's set price parameters.
    PRICE_COLLAR = "PRICE_COLLAR"
    # RTS 6 Art. 15(1)(b) - uncommonly large order value.
    MAX_ORDER_VALUE = "MAX_ORDER_VALUE"
    # RTS 6 Art. 15(1)(c) - uncommonly large order size.
    MAX_ORDER_VOLUME = "MAX_ORDER_VOLUME"
    # RTS 6 Art. 15(1)(d) / MAR 7A.3.2R(1) - message ceiling reached.
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"
    # RTS 9 (Reg. (EU) 2017/566) Art. 3 - venue-set unexecuted-orders-to-transactions limit.
    ORDER_TO_TRADE_RATIO = "ORDER_TO_TRADE_RATIO"
    # RTS 6 Art. 15(4)-(5) - market and credit risk limits.
    CREDIT_LIMIT_EXCEEDED = "CREDIT_LIMIT_EXCEEDED"
    # RTS 6 Art. 15(3) - repeated automated execution throttle fired.
    REPEATED_EXECUTION_THROTTLE = "REPEATED_EXECUTION_THROTTLE"


class FCAControlError(Exception):
    """Base exception for FCA / RTS 6 compliance control errors."""


@dataclass(frozen=True)
class OrderIntent:
    """A single order the strategy wants to send, before any control has run.

    Carries no risk limit of its own: RTS 6 Art. 15(4) makes limit-setting a firm
    responsibility, and Art. 1(c) requires trading desks to be separated from risk
    control, so a strategy must not be able to widen the limit it is checked against.
    """

    order_id: str
    algo_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    price: float
    quantity: float
    reference_price: float

    @property
    def order_value_gbp(self) -> float:
        return self.price * self.quantity


@dataclass(frozen=True)
class CreditState:
    """Credit already consumed, as reported by the firm's risk or clearing system.

    The engine cannot verify this figure. It must be supplied by the risk or clearing
    system, never computed by the strategy being checked.
    """

    used_gbp: float


@dataclass(frozen=True)
class RTS6ControlConfig:
    """Firm-set control parameters. **Every default below is a placeholder.**

    RTS 6 prescribes no numeric value for any pre-trade control. Art. 15(4) requires
    each limit to be derived from the firm's own capital base, clearing arrangements,
    trading strategy and risk tolerance, and to be adjusted for changing price and
    liquidity levels. Shipping these defaults unchanged is a calibration failure, not
    compliance.
    """

    #: Max |price - reference| as a percentage of the reference price. Art. 15(1)(a).
    max_price_collar_pct: float = 2.5
    #: Max notional per order, in the account currency (GBP here). Art. 15(1)(b).
    max_order_value_gbp: float = 500_000.0
    #: Max quantity per order. Art. 15(1)(c).
    max_order_volume: float = 10_000.0
    #: Firm credit ceiling. Art. 15(4). Set from the clearing arrangement, not the strategy.
    max_credit_limit_gbp: float = 1_000_000.0
    #: Firm self-monitoring limit against the venue's RTS 9 ratio. See ``SystemCapacityState``.
    max_unexecuted_to_transaction_ratio: float = 100.0
    #: Utilisation at which a warning is logged but orders still pass.
    system_capacity_warn_pct: float = 80.0
    #: Utilisation at which order flow is throttled. Art. 15(1)(d) / MAR 7A.3.2R(1).
    system_capacity_kill_pct: float = 95.0
    #: Art. 15(3) repeated automated execution throttle. ``None`` means the throttle is
    #: NOT implemented - Art. 15(3) requires one, so leaving this unset is a known gap.
    max_repeated_executions: Optional[int] = None
    #: Rolling window over which repeated executions are counted.
    repeated_execution_window_seconds: float = 60.0

    def __post_init__(self) -> None:
        """Reject a configuration that would silently disable a mandatory control.

        A NaN limit compares False against every value, so an unvalidated NaN in this
        config turns the corresponding Art. 15 control off without any signal.
        """
        positive_fields = (
            "max_price_collar_pct",
            "max_order_value_gbp",
            "max_order_volume",
            "max_credit_limit_gbp",
            "system_capacity_warn_pct",
            "system_capacity_kill_pct",
            "repeated_execution_window_seconds",
        )
        for name in positive_fields:
            if not _is_finite_positive(getattr(self, name)):
                raise ValueError(f"{name} must be finite and positive, got {getattr(self, name)!r}")
        if not _is_finite_non_negative(self.max_unexecuted_to_transaction_ratio):
            raise ValueError(
                "max_unexecuted_to_transaction_ratio must be finite and non-negative, "
                f"got {self.max_unexecuted_to_transaction_ratio!r}"
            )
        if self.system_capacity_warn_pct > self.system_capacity_kill_pct:
            raise ValueError(
                "system_capacity_warn_pct must not exceed system_capacity_kill_pct"
            )
        if self.max_repeated_executions is not None and (
            isinstance(self.max_repeated_executions, bool)
            or not isinstance(self.max_repeated_executions, int)
            or self.max_repeated_executions < 1
        ):
            # A float NaN here would make ``len(window) >= limit`` permanently False,
            # disabling the Art. 15(3) throttle without any signal.
            raise ValueError(
                f"max_repeated_executions must be an int >= 1 when set, "
                f"got {self.max_repeated_executions!r}"
            )


@dataclass
class SystemCapacityState:
    """Live message-rate and order-flow counters, supplied by the order gateway.

    RTS 6 Art. 15(2) requires all orders sent to a venue to be included in the
    pre-trade limit calculation *immediately*. The engine is stateless with respect
    to order flow: the caller must update these counters before the next evaluation,
    or the limits are computed against stale numbers.
    """

    #: Current outbound message rate.
    current_msg_rate_per_sec: float
    #: The firm's configured message ceiling - the lower of the Art. 15(1)(d) maximum
    #: messages limit it applies to the venue and the capacity its systems were tested
    #: to withstand (Art. 10, MAR 7A.3.2R(1)). Must be strictly positive.
    max_msg_rate_per_sec: float
    total_orders_sent: int
    total_trades_executed: int

    @property
    def capacity_utilization_pct(self) -> float:
        """Message-rate utilisation as a percentage of the configured ceiling.

        Raises rather than returning 0.0 when the ceiling is unusable: a missing
        ceiling would otherwise disable the control silently.
        """
        if not _is_finite_positive(self.max_msg_rate_per_sec):
            raise FCAControlError(
                f"max_msg_rate_per_sec must be finite and positive, "
                f"got {self.max_msg_rate_per_sec!r}"
            )
        if not _is_finite_non_negative(self.current_msg_rate_per_sec):
            raise FCAControlError(
                f"current_msg_rate_per_sec must be finite and non-negative, "
                f"got {self.current_msg_rate_per_sec!r}"
            )
        return (float(self.current_msg_rate_per_sec) / float(self.max_msg_rate_per_sec)) * 100.0

    @property
    def unexecuted_to_transaction_ratio(self) -> float:
        """Ratio of unexecuted orders to transactions, RTS 9 Art. 3 in number terms.

        Reg. (EU) 2017/566 Art. 3 defines it as ``total orders / total transactions - 1``.
        That regulation binds **trading venues**, which calculate the ratio per member at
        least daily and may set a maximum; it is not an RTS 6 firm control. This property
        exists so a firm's own monitor is measured the way the venue measures it.

        With zero transactions the RTS 9 ratio is undefined. The fallback returns
        ``total_orders_sent`` - a deliberately conservative proxy, not the RTS 9 measure -
        so an algorithm that sends orders and never trades is still capped.

        Raises when either counter is unusable: a NaN counter would make the ratio NaN,
        which compares False against the limit and silently disables the control.
        """
        if not _is_finite_non_negative(self.total_orders_sent):
            raise FCAControlError(
                f"total_orders_sent must be finite and non-negative, got {self.total_orders_sent!r}"
            )
        if not _is_finite_non_negative(self.total_trades_executed):
            raise FCAControlError(
                f"total_trades_executed must be finite and non-negative, "
                f"got {self.total_trades_executed!r}"
            )
        if self.total_trades_executed <= 0:
            return float(self.total_orders_sent)
        return self.total_orders_sent / float(self.total_trades_executed) - 1.0


@dataclass(frozen=True)
class ControlCheckResult:
    is_compliant: bool
    status: ControlStatus
    violation_type: ViolationType
    reason: str
    order_id: str
    algo_id: str
    timestamp: datetime.datetime = field(default_factory=_utc_now)


@dataclass(frozen=True)
class KillSwitchResult:
    """Outcome of an Art. 12 kill-switch activation.

    ``is_activated`` reports only that the local block latched. Art. 12(1) requires
    unexecuted orders to be cancelled at the venues, which this engine can only do
    through an injected handler: if ``mass_cancel_invoked`` is False, no venue
    cancellation happened and the firm has not discharged Art. 12.
    """

    is_activated: bool
    timestamp: datetime.datetime
    target_algo_id: Optional[str]
    scope: str
    reason: str
    mass_cancel_invoked: bool = False
    cancelled_orders_count: Optional[int] = None
    mass_cancel_error: Optional[str] = None


@dataclass(frozen=True)
class KillSwitchEvent:
    """Append-only record of a kill-switch activation or reset, for the audit trail."""

    action: str  # "TRIGGER" or "RESET"
    scope: str
    timestamp: datetime.datetime
    reason: str
    authorised_by: Optional[str] = None


class UKFCAAlgoControlsEngine:
    """Pre-trade order-entry gate and kill switch for UK algorithmic trading.

    Fail-closed by construction: anything the engine cannot evaluate - a malformed
    order, an absent reference price, an unusable message ceiling - is a rejection,
    not a pass.

    The engine implements hard blocks only. RTS 6 Art. 15(1) requires controls that
    "automatically block or cancel"; the FCA's 22 May 2024 CGML final notice
    (GBP 27,766,200) turned on overridable soft alerts that a trader dismissed without
    reading. Any Art. 15(6) exception to a block must be handled outside this gate,
    with risk-function verification and a named authoriser.

    Args:
        mass_cancel_handler: Callable invoked on kill-switch activation with the scope
            (an ``algo_id``, or ``None`` for firm-wide) and returning the number of
            orders cancelled. Without it the engine blocks new orders but cancels
            nothing at the venues.
    """

    def __init__(
        self,
        mass_cancel_handler: Optional[Callable[[Optional[str]], int]] = None,
    ) -> None:
        self._mass_cancel_handler = mass_cancel_handler
        #: scope key -> latched. ``GLOBAL_SCOPE`` blocks every algorithm.
        self.active_kill_switches: Dict[str, bool] = {}
        #: Append-only kill-switch audit trail. Persist it; the engine does not.
        self.kill_switch_events: List[KillSwitchEvent] = []
        self._execution_times: Dict[str, Deque[float]] = {}
        if mass_cancel_handler is None:
            logger.warning(
                "UKFCAAlgoControlsEngine constructed without a mass_cancel_handler: "
                "RTS 6 Art. 12 venue cancellation will NOT be performed"
            )
        logger.info("Initialised UK FCA RTS 6 algorithmic trading controls engine")

    # ---------------------------------------------------------------- kill switch

    @staticmethod
    def _scope_key(algo_id: Optional[str]) -> str:
        """Resolve a scope key, rejecting blank identifiers.

        ``None`` means firm-wide. An empty or whitespace ``algo_id`` is a caller bug
        and must never be silently promoted to firm-wide scope - doing so would let a
        blank configuration field halt the whole firm, or lift a firm-wide halt.
        """
        if algo_id is None:
            return GLOBAL_SCOPE
        if not isinstance(algo_id, str) or not algo_id.strip():
            raise ValueError(
                "algo_id must be a non-empty string, or None for firm-wide scope"
            )
        return algo_id

    def trigger_kill_switch(
        self, algo_id: Optional[str] = None, reason: str = ""
    ) -> KillSwitchResult:
        """Latch the Art. 12 kill switch and attempt venue-wide mass cancellation.

        The local block latches *before* the handler runs, so a handler that raises
        leaves the firm halted rather than trading on. The failure is reported in
        ``mass_cancel_error`` and logged, never swallowed.

        Args:
            algo_id: Algorithm to halt, or ``None`` to halt every algorithm.
            reason: Non-empty justification, recorded in the audit trail.
        """
        if not reason or not reason.strip():
            raise ValueError("reason is required when triggering a kill switch")
        key = self._scope_key(algo_id)

        self.active_kill_switches[key] = True
        timestamp = _utc_now()
        logger.critical("RTS 6 Art. 12 KILL SWITCH ACTIVATED [scope=%s]: %s", key, reason)
        self.kill_switch_events.append(
            KillSwitchEvent(action="TRIGGER", scope=key, timestamp=timestamp, reason=reason)
        )

        invoked = False
        cancelled: Optional[int] = None
        error: Optional[str] = None
        if self._mass_cancel_handler is not None:
            try:
                cancelled = int(self._mass_cancel_handler(algo_id))
                invoked = True
            except Exception as exc:  # noqa: BLE001 - handler is caller-supplied
                error = f"{type(exc).__name__}: {exc}"
                logger.critical(
                    "RTS 6 Art. 12 mass cancel FAILED [scope=%s]: %s - "
                    "kill switch remains latched, cancel orders manually",
                    key,
                    error,
                )
        else:
            error = "no mass_cancel_handler configured; no venue cancellation attempted"
            logger.critical("RTS 6 Art. 12 [scope=%s]: %s", key, error)

        return KillSwitchResult(
            is_activated=True,
            timestamp=timestamp,
            target_algo_id=algo_id,
            scope=key,
            reason=reason,
            mass_cancel_invoked=invoked,
            cancelled_orders_count=cancelled,
            mass_cancel_error=error,
        )

    def reset_kill_switch(
        self, algo_id: Optional[str], authorised_by: str, reason: str
    ) -> bool:
        """Lift a latched kill switch. ``authorised_by`` and ``reason`` are mandatory.

        RTS 6 Art. 15(3) re-enables a disabled system only "by a designated staff
        member", and Art. 15(6) requires a named authoriser for any relaxation of a
        pre-trade block. Resetting a scope also clears any Art. 15(3) execution
        counter for it.

        Resetting an ``algo_id`` does **not** lift a firm-wide halt: that must be
        reset explicitly with ``algo_id=None``.

        Returns:
            True if a latched switch was lifted, False if none was set for that scope.
        """
        if not authorised_by or not authorised_by.strip():
            raise ValueError("authorised_by is required to reset a kill switch")
        if not reason or not reason.strip():
            raise ValueError("reason is required to reset a kill switch")
        key = self._scope_key(algo_id)

        was_active = self.active_kill_switches.pop(key, False)
        self._execution_times.pop(key, None)
        self.kill_switch_events.append(
            KillSwitchEvent(
                action="RESET",
                scope=key,
                timestamp=_utc_now(),
                reason=reason,
                authorised_by=authorised_by,
            )
        )
        logger.warning(
            "RTS 6 kill switch RESET [scope=%s] by %s: %s (was_active=%s)",
            key,
            authorised_by,
            reason,
            was_active,
        )
        return bool(was_active)

    def is_kill_switch_active(self, algo_id: str) -> bool:
        """True if a firm-wide or algorithm-specific kill switch is latched."""
        key = self._scope_key(algo_id)
        return self.active_kill_switches.get(
            GLOBAL_SCOPE, False
        ) or self.active_kill_switches.get(key, False)

    # -------------------------------------------------- Art. 15(3) execution throttle

    def record_execution(
        self,
        algo_id: str,
        config: RTS6ControlConfig,
        at: Optional[float] = None,
    ) -> bool:
        """Record one execution of an algorithmic strategy, applying Art. 15(3).

        RTS 6 Art. 15(3): "After a pre-determined number of repeated executions, the
        trading system shall be automatically disabled until re-enabled by a designated
        staff member." When the count within
        ``config.repeated_execution_window_seconds`` reaches
        ``config.max_repeated_executions``, this latches the kill switch for ``algo_id``,
        which then requires an authorised ``reset_kill_switch`` call.

        Both the count and the window are firm parameters; RTS 6 prescribes neither.
        With ``max_repeated_executions=None`` the throttle is disabled and the firm has
        no Art. 15(3) control. Once the throttle has tripped, further executions for the
        same scope return True without re-triggering, so a late fill arriving after the
        halt does not duplicate the audit event or re-issue a mass cancel.

        Args:
            algo_id: Algorithm that executed.
            config: Firm control parameters.
            at: Epoch seconds, for deterministic testing. Defaults to now.

        Returns:
            True if this execution tripped the throttle.
        """
        key = self._scope_key(algo_id)
        if config.max_repeated_executions is None:
            return False
        if self.active_kill_switches.get(key, False):
            # Already disabled by an earlier trip; a late fill must not re-trigger the
            # kill switch or duplicate the audit event.
            return True

        now = at if at is not None else _utc_now().timestamp()
        window = self._execution_times.setdefault(key, deque())
        window.append(now)
        cutoff = now - config.repeated_execution_window_seconds
        while window and window[0] < cutoff:
            window.popleft()

        if len(window) >= config.max_repeated_executions:
            self.trigger_kill_switch(
                algo_id,
                reason=(
                    f"RTS 6 Art. 15(3) repeated execution throttle: {len(window)} executions "
                    f"in {config.repeated_execution_window_seconds:g}s "
                    f">= limit {config.max_repeated_executions}"
                ),
            )
            return True
        return False

    # ----------------------------------------------------------- pre-trade controls

    def _reject(
        self,
        order: OrderIntent,
        status: ControlStatus,
        violation: ViolationType,
        reason: str,
    ) -> ControlCheckResult:
        logger.warning(
            "RTS 6 pre-trade control %s [order=%s algo=%s]: %s",
            violation.value,
            order.order_id,
            order.algo_id,
            reason,
        )
        return ControlCheckResult(
            is_compliant=False,
            status=status,
            violation_type=violation,
            reason=reason,
            order_id=order.order_id,
            algo_id=order.algo_id,
        )

    def evaluate_pre_trade_controls(
        self,
        order: OrderIntent,
        capacity: SystemCapacityState,
        config: RTS6ControlConfig,
        credit: CreditState,
    ) -> ControlCheckResult:
        """Run the RTS 6 Art. 15 order-entry controls. Returns the first failure.

        Order of evaluation: Art. 12 kill switch, then order-field validation, then
        the message ceiling, then the Art. 15(1) collar / value / volume controls, then
        the RTS 9 unexecuted-orders ratio, then the Art. 15(4)-(5) credit limit.

        Every unevaluable input is a rejection. Notional is treated as gross exposure
        regardless of side: the engine performs no netting.
        """
        # Identity first: without a usable algo_id the engine cannot tell which halt
        # applies, and Art. 12(3) requires every order to be attributable.
        if not isinstance(order.algo_id, str) or not order.algo_id.strip():
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.INVALID_ORDER,
                f"Invalid algo_id {order.algo_id!r}: orders must be attributable "
                f"(RTS 6 Art. 12(3))",
            )
        if not isinstance(order.order_id, str) or not order.order_id.strip():
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.INVALID_ORDER,
                f"Invalid order_id {order.order_id!r}: an unidentifiable order cannot be "
                f"recorded or cancelled",
            )

        if self.is_kill_switch_active(order.algo_id):
            return self._reject(
                order,
                ControlStatus.KILL_SWITCH_ACTIVATED,
                ViolationType.KILL_SWITCH_ACTIVE,
                f"RTS 6 Art. 12 kill switch active for algo {order.algo_id}",
            )

        # Erroneous-order prevention (MAR 7A.3.2R(3)). NaN compares false against every
        # threshold below, so an unvalidated NaN would pass every remaining control.
        if order.side not in ("BUY", "SELL"):
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.INVALID_ORDER,
                f"Invalid side {order.side!r}: expected 'BUY' or 'SELL'",
            )
        if not _is_finite_positive(order.price):
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.INVALID_ORDER,
                f"Invalid price {order.price!r}: must be finite and positive",
            )
        if not _is_finite_positive(order.quantity):
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.INVALID_ORDER,
                f"Invalid quantity {order.quantity!r}: must be finite and positive",
            )
        if not _is_finite_positive(order.reference_price):
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.INVALID_REFERENCE_PRICE,
                f"No usable reference price ({order.reference_price!r}): the Art. 15(1)(a) "
                f"price collar cannot be evaluated",
            )
        if not _is_finite_non_negative(credit.used_gbp):
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.INVALID_ORDER,
                f"Invalid credit utilisation {credit.used_gbp!r}: must be finite and "
                f"non-negative",
            )

        # Message ceiling (RTS 6 Art. 15(1)(d) maximum messages limit, and the tested
        # system capacity behind MAR 7A.3.2R(1)).
        try:
            utilisation = capacity.capacity_utilization_pct
        except FCAControlError as exc:
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.INVALID_CAPACITY_STATE,
                f"Message capacity state unusable: {exc}",
            )
        if utilisation >= config.system_capacity_kill_pct:
            return self._reject(
                order,
                ControlStatus.THROTTLED,
                ViolationType.CAPACITY_EXCEEDED,
                f"Message ceiling breach: {utilisation:.1f}% >= throttle "
                f"{config.system_capacity_kill_pct:.1f}%",
            )
        if utilisation >= config.system_capacity_warn_pct:
            logger.warning(
                "Message capacity utilisation %.1f%% >= warn threshold %.1f%% [order=%s]",
                utilisation,
                config.system_capacity_warn_pct,
                order.order_id,
            )

        # Price collar (Art. 15(1)(a)). Cross-multiplied so behaviour exactly at the
        # collar limit does not depend on a division rounding.
        deviation = abs(order.price - order.reference_price)
        if deviation * 100.0 > config.max_price_collar_pct * order.reference_price:
            deviation_pct = deviation / order.reference_price * 100.0
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.PRICE_COLLAR,
                f"Price collar breach: {deviation_pct:.4f}% deviation from reference "
                f"{order.reference_price:,.4f} > limit {config.max_price_collar_pct:.4f}%",
            )

        # Maximum order value (Art. 15(1)(b)).
        if order.order_value_gbp > config.max_order_value_gbp:
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.MAX_ORDER_VALUE,
                f"Max order value breach: {order.order_value_gbp:,.2f} > limit "
                f"{config.max_order_value_gbp:,.2f}",
            )

        # Maximum order volume (Art. 15(1)(c)).
        if order.quantity > config.max_order_volume:
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.MAX_ORDER_VOLUME,
                f"Max order volume breach: {order.quantity:,.0f} > limit "
                f"{config.max_order_volume:,.0f}",
            )

        # Unexecuted orders to transactions (venue limit under RTS 9 Art. 3, self-monitored).
        try:
            ratio = capacity.unexecuted_to_transaction_ratio
        except FCAControlError as exc:
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.INVALID_CAPACITY_STATE,
                f"Order-flow counters unusable: {exc}",
            )
        if ratio > config.max_unexecuted_to_transaction_ratio:
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.ORDER_TO_TRADE_RATIO,
                f"Unexecuted-orders-to-transactions breach: {ratio:.2f} > limit "
                f"{config.max_unexecuted_to_transaction_ratio:.2f}",
            )

        # Credit and market risk limits (Art. 15(4)-(5)).
        projected = credit.used_gbp + order.order_value_gbp
        if projected > config.max_credit_limit_gbp:
            return self._reject(
                order,
                ControlStatus.REJECTED,
                ViolationType.CREDIT_LIMIT_EXCEEDED,
                f"Credit limit breach: projected {projected:,.2f} > limit "
                f"{config.max_credit_limit_gbp:,.2f}",
            )

        logger.debug(
            "RTS 6 pre-trade controls PASSED [order=%s algo=%s]", order.order_id, order.algo_id
        )
        return ControlCheckResult(
            is_compliant=True,
            status=ControlStatus.PASSED,
            violation_type=ViolationType.NONE,
            reason="All RTS 6 Art. 15 pre-trade controls passed",
            order_id=order.order_id,
            algo_id=order.algo_id,
        )

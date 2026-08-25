"""
exchange-gateway-redundancy-and-failover-testing: an Active-Standby order-entry
gateway failover *decision* engine and test harness.

What this module decides
------------------------
Given the health of an active order-entry gateway session, a standby session, and
the set of in-flight orders whose fate is unknown, it answers three questions and
records the answers in an auditable report:

1. **Should we fail over at all, and is it safe to do so right now?**
   Promoting a standby while the failing session can still transmit is the
   split-brain that produces double execution. Failover is therefore *blocked*
   unless the failing session is provably unable to send (socket down) or the
   caller confirms it has been fenced/quiesced.
2. **What outbound sequence number does the standby session start from?**
   Sequence numbers belong to a session, never to a firm. This engine never
   copies one session's sequence into another; the number comes from the venue's
   documented policy (see ``SequenceResetPolicy``).
3. **What happens to each in-flight order?**
   Nothing is auto-resent. Every order whose state is unknown is marked
   ``RECONCILE_REQUIRED``; it becomes resend-eligible only after the caller feeds
   back positive evidence from the venue that it is absent.

Protocol facts this module encodes (verified against primary sources)
---------------------------------------------------------------------
* ``PossDupFlag(43)`` means "possible retransmission of message **with this
  sequence number**" — it belongs to session-layer gap fill, where the original
  MsgSeqNum is reused and ``OrigSendingTime(122)`` is carried. It is the **wrong**
  field for an order re-sent over a different session under a new sequence number.
* ``PossResend(97)`` means the message "may contain information that has been sent
  under another sequence number" — this is the failover case. Handling is a
  *business*-layer decision at the counterparty (typically ClOrdID-based dedup),
  not a session-layer one, which is precisely why the sender must still reconcile.
* Generic FIX venues expose order state via ``OrderStatusRequest(35=H)`` or
  ``OrderMassStatusRequest(35=AF)``, answered by ``ExecutionReport(35=8)`` with
  ``ExecType(150)=I``.
* Eurex T7 ETI is different on all three counts: order status inquiries are not
  supported at all (state is rebuilt from Execution Reports and the order book
  restatement that follows a market reset), every connection *including a
  reconnect* must log on with ``MsgSeqNum=1`` and there is no sequence recovery,
  and non-persistent orders and quotes are deleted outright when the session
  drops — they are never restated, so they must be re-entered as **new** orders
  under a **new** ClOrdID rather than resent.

See ``references/standards.md`` for the citations behind each of these.

Deliberate limitations
----------------------
* **No I/O.** This engine performs no TCP connect, logon, cancel or resend. It
  decides and records; the caller's session layer acts.
* **``decision_latency_ms`` is not an RTO.** It measures this in-process decision
  only. A real recovery time also contains TCP connect, logon, order-book
  restatement and reconciliation, none of which happen here. Supply
  ``standby_activation_ms`` to obtain ``estimated_rto_ms``; without it the report
  makes no RTO claim.
* **Health metrics are inputs, not measurements.** The caller samples them. Stale
  or fabricated inputs produce confident, wrong decisions.
* **Two nodes.** Active-Standby pairs only; N-way gateway pools are out of scope.
"""
import logging
import math
import time
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "GatewayRole",
    "GatewayStatus",
    "SequenceResetPolicy",
    "OrderStateRecoveryMethod",
    "OrderRecoveryAction",
    "FailoverOutcome",
    "ReconciliationVerdict",
    "VenueRecoveryProfile",
    "GENERIC_FIX_PROFILE",
    "EUREX_T7_ETI_PROFILE",
    "GatewayNodeConfig",
    "InFlightOrder",
    "OrderRecoveryDecision",
    "GatewayFailoverAuditReport",
    "ExchangeGatewayRedundancyEngine",
    "heartbeat_timeout_from_interval",
]

#: Order states whose outcome at the venue is unknown after a session loss. An
#: acknowledgement may have been generated and lost, so none of these may be
#: resent without positive evidence from the venue.
UNKNOWN_ORDER_STATES = frozenset(
    {"PENDING_NEW", "PENDING_CANCEL", "PENDING_REPLACE", "PENDING_CANCEL_REPLACE"}
)

#: Venue-confirmed and finished. Nothing about a failover can change them.
COMPLETED_ORDER_STATES = frozenset({"FILLED", "CANCELED", "REJECTED", "EXPIRED", "DONE_FOR_DAY"})

#: Venue-confirmed and still working. No resend risk — but a venue that deletes
#: non-persistent orders on disconnect deletes these too, acknowledgement or not.
RESTING_ORDER_STATES = frozenset({"NEW", "PARTIALLY_FILLED"})

#: Every state the venue has acknowledged, in either sense.
TERMINAL_ORDER_STATES = COMPLETED_ORDER_STATES | RESTING_ORDER_STATES

#: CME iLink 3 designates a fault-tolerant session failed when nothing is received
#: for 2 x KeepAliveInterval. Used as the default multiplier by
#: ``heartbeat_timeout_from_interval``; venues that publish a different rule
#: (1.5 x HeartBtInt is also common in FIX session implementations) override it.
DEFAULT_HEARTBEAT_TIMEOUT_MULTIPLIER = 2.0


class GatewayRole(str, Enum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"


class GatewayStatus(str, Enum):
    ACTIVE = "ACTIVE"            # carrying order flow
    STANDBY = "STANDBY"          # healthy, eligible for promotion
    DEGRADED = "DEGRADED"        # socket alive but breaching an SLA
    QUIESCED = "QUIESCED"        # fenced by the operator; cannot transmit
    DISCONNECTED = "DISCONNECTED"


class SequenceResetPolicy(str, Enum):
    """How the standby session's outbound MsgSeqNum is established on logon."""

    #: Standby continues its *own* outbound sequence (never the failed node's).
    CONTINUE_SESSION = "CONTINUE_SESSION"
    #: Logon carries ResetSeqNumFlag(141)=Y; both sides reset to 1. Bilateral.
    RESET_ON_LOGON = "RESET_ON_LOGON"
    #: Every connection, reconnects included, logs on with MsgSeqNum=1 and there
    #: is no sequence recovery (Eurex T7 ETI).
    RESTART_AT_ONE = "RESTART_AT_ONE"


class OrderStateRecoveryMethod(str, Enum):
    #: FIX OrderStatusRequest(35=H) / OrderMassStatusRequest(35=AF).
    ORDER_STATUS_REQUEST = "ORDER_STATUS_REQUEST"
    #: Venue pushes an order book restatement; no inquiry message exists (ETI).
    RESTATEMENT_BROADCAST = "RESTATEMENT_BROADCAST"
    #: Independent drop-copy session.
    DROP_COPY = "DROP_COPY"


class OrderRecoveryAction(str, Enum):
    #: State unknown; must be reconciled against the venue before anything else.
    RECONCILE_REQUIRED = "RECONCILE_REQUIRED"
    #: Reconciliation proved the order is absent; resend marked PossResend(97)=Y.
    RESEND_AS_POSS_RESEND = "RESEND_AS_POSS_RESEND"
    #: Venue deletes non-persistent orders on disconnect. Gone for certain — must
    #: be re-entered as a NEW order with a NEW ClOrdID, never resent.
    REENTER_AS_NEW_ORDER = "REENTER_AS_NEW_ORDER"
    #: Already in a venue-confirmed state; failover does not touch it.
    NO_ACTION = "NO_ACTION"


class ReconciliationVerdict(str, Enum):
    ABSENT_AT_VENUE = "ABSENT_AT_VENUE"
    PRESENT_AT_VENUE = "PRESENT_AT_VENUE"
    UNKNOWN = "UNKNOWN"


class FailoverOutcome(str, Enum):
    NO_ACTION = "NO_ACTION"                # active gateway healthy
    TEST_REQUEST_REQUIRED = "TEST_REQUEST_REQUIRED"
    FAILOVER_SUCCESS = "FAILOVER_SUCCESS"
    FAILOVER_BLOCKED = "FAILOVER_BLOCKED"  # unsafe: failing node can still send
    FAILOVER_FAILED = "FAILOVER_FAILED"    # no healthy standby to promote


def heartbeat_timeout_from_interval(
    heartbeat_interval_ms: float,
    multiplier: float = DEFAULT_HEARTBEAT_TIMEOUT_MULTIPLIER,
) -> float:
    """
    Derive a heartbeat-loss threshold from the negotiated session heartbeat interval.

    A bare millisecond figure is meaningless without the interval it is measured
    against: 3,000 ms is aggressive against a 1 s KeepAliveInterval and useless
    against the 30 s HeartBtInt typical of a FIX order session. Always derive.
    """
    if not math.isfinite(heartbeat_interval_ms) or heartbeat_interval_ms <= 0.0:
        raise ValueError("heartbeat_interval_ms must be a positive finite number")
    if not math.isfinite(multiplier) or multiplier <= 1.0:
        raise ValueError("multiplier must be a finite number greater than 1.0")
    return heartbeat_interval_ms * multiplier


@dataclass(frozen=True)
class VenueRecoveryProfile:
    """
    The venue-specific facts that determine what a correct failover looks like.

    These differ enough between venues that a single hard-coded procedure is
    guaranteed to be wrong somewhere; see the two shipped profiles below.
    """
    venue_id: str
    sequence_policy: SequenceResetPolicy
    order_state_recovery: OrderStateRecoveryMethod
    #: True when the venue keeps non-persistent orders alive across a session
    #: loss. Eurex T7 deletes them, so this is False there.
    non_persistent_orders_survive_disconnect: bool
    #: Protocol field used to mark a business-level resend, or None when the venue
    #: has no such marking and recovery is restatement-driven.
    resend_marking: Optional[str]
    source_note: str

    def __post_init__(self) -> None:
        if not self.venue_id or not self.venue_id.strip():
            raise ValueError("venue_id must be a non-empty string")


#: Generic FIX 4.4 / 5.0 SP2 order session. Sequence numbers persist per session
#: unless a reset is negotiated on logon; order state is recoverable by inquiry.
GENERIC_FIX_PROFILE = VenueRecoveryProfile(
    venue_id="GENERIC_FIX",
    sequence_policy=SequenceResetPolicy.CONTINUE_SESSION,
    order_state_recovery=OrderStateRecoveryMethod.ORDER_STATUS_REQUEST,
    non_persistent_orders_survive_disconnect=True,
    resend_marking="PossResend(97)=Y",
    source_note=(
        "FIX PossResend(97): 'may contain information that has been sent under "
        "another sequence number'. Order state recoverable via "
        "OrderMassStatusRequest(35=AF) -> ExecutionReport ExecType(150)=I. "
        "Per-venue behaviour still overrides this generic default."
    ),
)

#: Eurex T7 ETI. Every connection logs on at MsgSeqNum=1 with no sequence
#: recovery; order status inquiries do not exist; non-persistent orders and
#: quotes are deleted on session loss and are not restated.
EUREX_T7_ETI_PROFILE = VenueRecoveryProfile(
    venue_id="EUREX_T7_ETI",
    sequence_policy=SequenceResetPolicy.RESTART_AT_ONE,
    order_state_recovery=OrderStateRecoveryMethod.RESTATEMENT_BROADCAST,
    non_persistent_orders_survive_disconnect=False,
    resend_marking=None,
    source_note=(
        "T7 ETI Manual (R13.1) s6.6: all connections including reconnects log on "
        "with MsgSeqNum=1, no sequence recovery. s4.7.11: order status inquiries "
        "are not supported; state is maintained from Execution Reports and the "
        "order book restatement following a market reset. s5.4: quotes and "
        "non-persistent orders are automatically deleted on session loss."
    ),
)


@dataclass
class GatewayNodeConfig:
    """
    One order-entry gateway session and its most recent health sample.

    ``last_sent_seq_num`` is *this session's own* outbound MsgSeqNum. It is never
    derived from, compared against, or overwritten by the peer node's.
    """
    gateway_id: str
    ip_address: str
    port: int
    role: str
    status: str
    last_sent_seq_num: int
    heartbeat_delay_ms: float
    latency_rtt_ms: float
    tcp_connected: bool
    #: True once a FIX TestRequest(35=1) has been sent and left unanswered for the
    #: heartbeat window. The FIX liveness procedure requires this probe before a
    #: session is declared dead; failing over on a missed heartbeat alone
    #: abandons a session that is merely idle.
    test_request_unanswered: bool = False
    #: Number of consecutive latency samples breaching the RTT SLA. A single
    #: sample is noise, and failing over on noise is how a healthy session gets
    #: abandoned mid-flight.
    consecutive_latency_breaches: int = 0

    def __post_init__(self) -> None:
        if not self.gateway_id or not self.gateway_id.strip():
            raise ValueError("gateway_id must be a non-empty string")
        _validate_member(self.role, GatewayRole, f"{self.gateway_id}.role")
        _validate_member(self.status, GatewayStatus, f"{self.gateway_id}.status")
        if not isinstance(self.port, int) or isinstance(self.port, bool):
            raise TypeError(f"{self.gateway_id}.port must be an int")
        if not 1 <= self.port <= 65535:
            raise ValueError(f"{self.gateway_id}.port {self.port} outside 1-65535")
        if not isinstance(self.last_sent_seq_num, int) or isinstance(self.last_sent_seq_num, bool):
            raise TypeError(f"{self.gateway_id}.last_sent_seq_num must be an int")
        if self.last_sent_seq_num < 0:
            raise ValueError(f"{self.gateway_id}.last_sent_seq_num must be >= 0")
        for name in ("heartbeat_delay_ms", "latency_rtt_ms"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{self.gateway_id}.{name} must be finite and >= 0")
            setattr(self, name, value)
        if not isinstance(self.tcp_connected, bool):
            raise TypeError(f"{self.gateway_id}.tcp_connected must be a bool")
        if self.consecutive_latency_breaches < 0:
            raise ValueError(f"{self.gateway_id}.consecutive_latency_breaches must be >= 0")

    @property
    def can_transmit(self) -> bool:
        """
        Whether this node could still put an order on the wire.

        A live socket that has merely missed heartbeats can still transmit, which
        is exactly the condition under which promoting the standby causes
        split-brain.
        """
        return self.tcp_connected and self.status != GatewayStatus.QUIESCED


@dataclass
class InFlightOrder:
    """
    An order the venue may or may not hold. ``order_status`` is what *we* last
    observed, which after a session loss is not what the venue believes.
    """
    cl_ord_id: str
    symbol: str
    side: str
    order_qty: int
    price: float
    order_status: str
    #: False for orders the venue deletes on session loss (Eurex "non-persistent"
    #: / lean orders, quotes). Only meaningful together with the venue profile.
    persistent: bool = True
    #: FIX PossResend(97). Set only on a copy produced by ``build_resend_plan``
    #: after reconciliation proved the order absent. NOT PossDupFlag(43), which
    #: applies to session-layer retransmission reusing the same MsgSeqNum.
    poss_resend: bool = False

    def __post_init__(self) -> None:
        if not self.cl_ord_id or not self.cl_ord_id.strip():
            raise ValueError("cl_ord_id must be a non-empty string")
        known = UNKNOWN_ORDER_STATES | TERMINAL_ORDER_STATES
        if self.order_status not in known:
            raise ValueError(
                f"order '{self.cl_ord_id}': unrecognised order_status "
                f"{self.order_status!r}; expected one of {sorted(known)}"
            )
        if not isinstance(self.order_qty, int) or isinstance(self.order_qty, bool):
            raise TypeError(f"order '{self.cl_ord_id}': order_qty must be an int")
        if self.order_qty <= 0:
            raise ValueError(f"order '{self.cl_ord_id}': order_qty must be > 0")
        price = float(self.price)
        if not math.isfinite(price) or price < 0.0:
            raise ValueError(f"order '{self.cl_ord_id}': price must be finite and >= 0")
        self.price = price


@dataclass
class OrderRecoveryDecision:
    """What must happen to one in-flight order, and why."""
    cl_ord_id: str
    action: str
    rationale: str
    #: Populated only for RECONCILE_REQUIRED — how this venue lets you find out.
    recovery_method: Optional[str] = None


@dataclass
class GatewayFailoverAuditReport:
    """Auditable record of one failover decision. Never a claim that I/O happened."""
    outcome: str
    failed_gateway_id: str
    promoted_gateway_id: Optional[str]
    failover_trigger_reason: str
    #: In-process decision time only. NOT a recovery time objective — it excludes
    #: TCP connect, logon, restatement and reconciliation.
    decision_latency_ms: float
    #: decision_latency_ms + caller-measured standby activation, or None when the
    #: caller supplied no measurement. Absent by default, never estimated.
    estimated_rto_ms: Optional[float]
    #: The standby session's own next outbound MsgSeqNum under the venue policy.
    standby_next_out_seq_num: Optional[int]
    sequence_policy: str
    sequence_policy_note: str
    order_recovery_plan: List[OrderRecoveryDecision] = field(default_factory=list)
    orders_requiring_reconciliation: List[str] = field(default_factory=list)
    orders_to_reenter_as_new: List[str] = field(default_factory=list)
    required_operator_action: Optional[str] = None
    audit_notes: str = ""

    @property
    def failover_executed(self) -> bool:
        return self.outcome == FailoverOutcome.FAILOVER_SUCCESS


def _validate_member(value: str, enum_cls: type, label: str) -> None:
    try:
        enum_cls(value)
    except ValueError:
        valid = sorted(m.value for m in enum_cls)  # type: ignore[attr-defined]
        raise ValueError(f"{label}: {value!r} is not one of {valid}") from None


class ExchangeGatewayRedundancyEngine:
    """
    Active-Standby order-entry gateway failover decision engine.

    The engine owns no sockets. It reads health samples, decides whether failover
    is warranted *and safe*, records the sequence and order-recovery plan, and
    mutates only the two ``GatewayNodeConfig`` objects it was constructed with —
    never the caller's ``InFlightOrder`` objects.
    """

    def __init__(
        self,
        primary_config: GatewayNodeConfig,
        secondary_config: GatewayNodeConfig,
        max_heartbeat_delay_ms: float,
        venue_profile: VenueRecoveryProfile = GENERIC_FIX_PROFILE,
        max_latency_rtt_ms: Optional[float] = None,
        min_consecutive_latency_breaches: int = 3,
    ) -> None:
        """
        Args:
            max_heartbeat_delay_ms: Heartbeat-loss threshold. Derive it from the
                negotiated interval with ``heartbeat_timeout_from_interval`` —
                there is no defensible venue-independent constant.
            venue_profile: Venue recovery facts. The default is a *generic* FIX
                profile; check it against the venue's own specification.
            max_latency_rtt_ms: RTT SLA, or None to disable latency-triggered
                failover. Disabled by default: a latency breach leaves the session
                able to transmit, so it is the trigger most likely to produce
                split-brain, and it demands a fence.
            min_consecutive_latency_breaches: Consecutive breaching samples needed
                before latency counts as a trigger.
        """
        if primary_config.gateway_id == secondary_config.gateway_id:
            raise ValueError("primary and secondary gateways must have distinct gateway_id values")
        if not math.isfinite(max_heartbeat_delay_ms) or max_heartbeat_delay_ms <= 0.0:
            raise ValueError("max_heartbeat_delay_ms must be a positive finite number")
        if max_latency_rtt_ms is not None and (
            not math.isfinite(max_latency_rtt_ms) or max_latency_rtt_ms <= 0.0
        ):
            raise ValueError("max_latency_rtt_ms must be a positive finite number or None")
        if min_consecutive_latency_breaches < 1:
            raise ValueError("min_consecutive_latency_breaches must be >= 1")

        active_nodes = [
            node for node in (primary_config, secondary_config)
            if node.status == GatewayStatus.ACTIVE
        ]
        if len(active_nodes) != 1:
            raise ValueError(
                "exactly one gateway must start in ACTIVE status; got "
                f"{[node.status for node in (primary_config, secondary_config)]}"
            )

        self.primary = primary_config
        self.secondary = secondary_config
        self.venue_profile = venue_profile
        self.max_heartbeat_delay_ms = float(max_heartbeat_delay_ms)
        self.max_latency_rtt_ms = max_latency_rtt_ms
        self.min_consecutive_latency_breaches = min_consecutive_latency_breaches
        self.active_gateway_id = active_nodes[0].gateway_id

    # ------------------------------------------------------------------ health

    def _active_and_standby(self) -> Tuple[GatewayNodeConfig, GatewayNodeConfig]:
        if self.active_gateway_id == self.primary.gateway_id:
            return self.primary, self.secondary
        return self.secondary, self.primary

    def _evaluate_trigger(
        self, node: GatewayNodeConfig
    ) -> Tuple[Optional[str], Optional[str], "FailoverOutcome"]:
        """
        Returns (trigger_reason, required_operator_action, outcome_when_no_trigger).

        A missed heartbeat alone is not a dead session. FIX liveness requires a
        TestRequest probe first; until it goes unanswered the correct action is to
        probe, not to fail over.
        """
        if not node.tcp_connected:
            return (
                f"TCP_DISCONNECT: active gateway '{node.gateway_id}' lost its socket.",
                None,
                FailoverOutcome.NO_ACTION,
            )
        if node.heartbeat_delay_ms > self.max_heartbeat_delay_ms:
            if not node.test_request_unanswered:
                return (
                    None,
                    f"ISSUE_TEST_REQUEST: heartbeat delay {node.heartbeat_delay_ms:.1f}ms "
                    f"exceeds {self.max_heartbeat_delay_ms:.1f}ms on '{node.gateway_id}'. "
                    "Send TestRequest(35=1) and re-audit; do not fail over on a missed "
                    "heartbeat alone.",
                    FailoverOutcome.TEST_REQUEST_REQUIRED,
                )
            return (
                f"HEARTBEAT_TIMEOUT: delay {node.heartbeat_delay_ms:.1f}ms exceeds "
                f"{self.max_heartbeat_delay_ms:.1f}ms and TestRequest went unanswered.",
                None,
                FailoverOutcome.NO_ACTION,
            )
        if self.max_latency_rtt_ms is not None and node.latency_rtt_ms > self.max_latency_rtt_ms:
            if node.consecutive_latency_breaches < self.min_consecutive_latency_breaches:
                return (
                    None,
                    f"CONTINUE_MONITORING: RTT {node.latency_rtt_ms:.1f}ms breaches "
                    f"{self.max_latency_rtt_ms:.1f}ms but only "
                    f"{node.consecutive_latency_breaches} consecutive sample(s) of "
                    f"{self.min_consecutive_latency_breaches} required.",
                    FailoverOutcome.NO_ACTION,
                )
            return (
                f"LATENCY_DEGRADATION: RTT {node.latency_rtt_ms:.1f}ms exceeds SLA "
                f"{self.max_latency_rtt_ms:.1f}ms for "
                f"{node.consecutive_latency_breaches} consecutive samples.",
                None,
                FailoverOutcome.NO_ACTION,
            )
        return None, None, FailoverOutcome.NO_ACTION

    # ------------------------------------------------------------- sequence

    def _resolve_sequence_plan(self, standby: GatewayNodeConfig) -> Tuple[int, str]:
        """
        The standby's own next outbound MsgSeqNum. The failed node's sequence is
        never consulted: sequence numbers are a property of a session (a CompID
        pair / a connection), not of the firm.
        """
        policy = self.venue_profile.sequence_policy
        if policy == SequenceResetPolicy.RESTART_AT_ONE:
            return 1, (
                "Venue restarts every connection at MsgSeqNum=1 with no sequence "
                "recovery; the standby logs on at 1 regardless of prior state."
            )
        if policy == SequenceResetPolicy.RESET_ON_LOGON:
            return 1, (
                "Standby logs on with ResetSeqNumFlag(141)=Y, resetting both "
                "directions to 1. Requires bilateral agreement with the venue; an "
                "unagreed reset is itself a session-level failure."
            )
        return standby.last_sent_seq_num + 1, (
            "Standby continues its own outbound sequence. The failed session's "
            "MsgSeqNum is not copied across: doing so desynchronises the standby "
            "and triggers an immediate counterparty logout."
        )

    # ------------------------------------------------------- order recovery

    def _plan_order_recovery(
        self, in_flight_orders: Sequence[InFlightOrder]
    ) -> List[OrderRecoveryDecision]:
        seen: Dict[str, int] = {}
        plan: List[OrderRecoveryDecision] = []
        for order in in_flight_orders:
            if order.cl_ord_id in seen:
                raise ValueError(
                    f"duplicate cl_ord_id {order.cl_ord_id!r} in in_flight_orders; "
                    "ClOrdID is the dedup key the venue itself relies on"
                )
            seen[order.cl_ord_id] = 1

            if order.order_status in COMPLETED_ORDER_STATES:
                plan.append(OrderRecoveryDecision(
                    cl_ord_id=order.cl_ord_id,
                    action=OrderRecoveryAction.NO_ACTION,
                    rationale=(
                        f"order_status={order.order_status} is venue-confirmed and "
                        "finished; failover cannot change it."
                    ),
                ))
                continue

            # Deletion is a property of the venue and the order, not of what we
            # happen to know: an acknowledged resting non-persistent order is
            # deleted exactly like an unacknowledged one.
            if not order.persistent and not self.venue_profile.non_persistent_orders_survive_disconnect:
                residual = (
                    " Only the residual quantity should be re-entered — this order was "
                    "already partially filled."
                    if order.order_status == "PARTIALLY_FILLED" else ""
                )
                plan.append(OrderRecoveryDecision(
                    cl_ord_id=order.cl_ord_id,
                    action=OrderRecoveryAction.REENTER_AS_NEW_ORDER,
                    rationale=(
                        f"{self.venue_profile.venue_id} deletes non-persistent orders on "
                        "session loss and does not restate them. The order is gone with "
                        "certainty, so it must be re-entered as a NEW order under a NEW "
                        "ClOrdID — resending the original ClOrdID is a rejected or "
                        f"duplicate submission, not a recovery.{residual}"
                    ),
                ))
                continue

            if order.order_status in RESTING_ORDER_STATES:
                plan.append(OrderRecoveryDecision(
                    cl_ord_id=order.cl_ord_id,
                    action=OrderRecoveryAction.NO_ACTION,
                    rationale=(
                        f"order_status={order.order_status} is venue-confirmed and working; "
                        "no resend risk. Still confirm it against the venue's own view "
                        f"({self.venue_profile.order_state_recovery}) before resuming flow — "
                        "it may have traded or been purged during the outage."
                    ),
                ))
                continue

            plan.append(OrderRecoveryDecision(
                cl_ord_id=order.cl_ord_id,
                action=OrderRecoveryAction.RECONCILE_REQUIRED,
                rationale=(
                    f"order_status={order.order_status} is unknown at the venue: the "
                    "acknowledgement may have been generated and lost with the session. "
                    "Resending before reconciliation is the duplicate-execution path."
                ),
                recovery_method=self.venue_profile.order_state_recovery,
            ))
        return plan

    # ------------------------------------------------------------------ main

    def audit_gateway_health_and_failover(
        self,
        in_flight_orders: Sequence[InFlightOrder],
        fence_confirmed: bool = False,
        standby_activation_ms: Optional[float] = None,
    ) -> GatewayFailoverAuditReport:
        """
        Audit the active gateway and, if warranted and safe, promote the standby.

        Args:
            in_flight_orders: Orders whose venue state may be unknown. Not mutated.
            fence_confirmed: The caller has proved the failing session can no
                longer transmit (session logged out, socket closed, credentials
                revoked, or the venue confirmed the disconnect). Required whenever
                the failing node's socket is still up, because promoting a standby
                alongside a live session is split-brain.
            standby_activation_ms: Measured TCP connect + logon + restatement +
                reconciliation time for the standby. Supply it to obtain
                ``estimated_rto_ms``; omit it and the report claims no RTO.

        Returns:
            A ``GatewayFailoverAuditReport`` in every case. ``outcome`` says what
            happened; ``NO_ACTION`` means the gateway is healthy.

        Raises:
            ValueError: on duplicate ClOrdIDs or a negative activation measurement.
        """
        if standby_activation_ms is not None and (
            not math.isfinite(standby_activation_ms) or standby_activation_ms < 0.0
        ):
            raise ValueError("standby_activation_ms must be finite and >= 0, or None")

        start_t = time.perf_counter()
        active, standby = self._active_and_standby()
        trigger_reason, operator_action, quiet_outcome = self._evaluate_trigger(active)

        def _elapsed() -> float:
            return round((time.perf_counter() - start_t) * 1000.0, 4)

        def _report(
            outcome: FailoverOutcome,
            reason: str,
            *,
            promoted: Optional[str] = None,
            seq: Optional[int] = None,
            seq_note: str = "",
            plan: Optional[List[OrderRecoveryDecision]] = None,
            action: Optional[str] = None,
            notes: str = "",
        ) -> GatewayFailoverAuditReport:
            decision_ms = _elapsed()
            plan = plan or []
            return GatewayFailoverAuditReport(
                outcome=outcome,
                failed_gateway_id=active.gateway_id,
                promoted_gateway_id=promoted,
                failover_trigger_reason=reason,
                decision_latency_ms=decision_ms,
                estimated_rto_ms=(
                    round(decision_ms + standby_activation_ms, 4)
                    if standby_activation_ms is not None and outcome == FailoverOutcome.FAILOVER_SUCCESS
                    else None
                ),
                standby_next_out_seq_num=seq,
                sequence_policy=self.venue_profile.sequence_policy,
                sequence_policy_note=seq_note,
                order_recovery_plan=plan,
                orders_requiring_reconciliation=[
                    d.cl_ord_id for d in plan if d.action == OrderRecoveryAction.RECONCILE_REQUIRED
                ],
                orders_to_reenter_as_new=[
                    d.cl_ord_id for d in plan if d.action == OrderRecoveryAction.REENTER_AS_NEW_ORDER
                ],
                required_operator_action=action,
                audit_notes=notes,
            )

        if trigger_reason is None:
            if operator_action is not None:
                logger.warning("GATEWAY ATTENTION [%s]: %s", active.gateway_id, operator_action)
                return _report(
                    quiet_outcome,
                    "No failover trigger confirmed yet.",
                    action=operator_action,
                    notes="Threshold breached but the trigger is unconfirmed; no role change performed.",
                )
            logger.info(
                "GATEWAY HEALTHY [%s]: rtt=%.1fms heartbeat_delay=%.1fms",
                active.gateway_id, active.latency_rtt_ms, active.heartbeat_delay_ms,
            )
            return _report(
                FailoverOutcome.NO_ACTION,
                "Active gateway within all configured thresholds.",
                notes="No failover required.",
            )

        # Split-brain guard. A node whose socket is up can still put orders on the
        # wire; promoting the standby beside it doubles the order flow.
        if active.can_transmit and not fence_confirmed:
            action = (
                f"FENCE_ACTIVE_GATEWAY: '{active.gateway_id}' still holds a live socket. "
                "Log the session out, close the socket, or revoke its credentials, then "
                "re-audit with fence_confirmed=True. Promoting the standby now would put "
                "two sessions on the wire simultaneously."
            )
            logger.error("FAILOVER BLOCKED [%s]: %s", active.gateway_id, action)
            return _report(
                FailoverOutcome.FAILOVER_BLOCKED,
                trigger_reason,
                action=action,
                notes="Failover trigger met but the failing session is not fenced; no role change performed.",
            )

        # Standby pre-flight. Promoting a dead standby is worse than not failing over.
        standby_problems: List[str] = []
        if standby.status != GatewayStatus.STANDBY:
            standby_problems.append(
                f"status={standby.status} (must be STANDBY; restore a repaired node to "
                "STANDBY explicitly before it is eligible again)"
            )
        if not standby.tcp_connected:
            standby_problems.append("tcp_connected=False")
        if standby.heartbeat_delay_ms > self.max_heartbeat_delay_ms:
            standby_problems.append(
                f"heartbeat_delay_ms={standby.heartbeat_delay_ms:.1f} exceeds "
                f"{self.max_heartbeat_delay_ms:.1f}"
            )
        if self.max_latency_rtt_ms is not None and standby.latency_rtt_ms > self.max_latency_rtt_ms:
            standby_problems.append(
                f"latency_rtt_ms={standby.latency_rtt_ms:.1f} exceeds SLA {self.max_latency_rtt_ms:.1f}"
            )
        if standby_problems:
            action = (
                f"NO_HEALTHY_STANDBY: '{standby.gateway_id}' is not promotable "
                f"({'; '.join(standby_problems)}). Escalate to the manual/emergency "
                "procedure — cancel-on-disconnect, phone-to-desk, or venue-side purge."
            )
            logger.error("FAILOVER FAILED: %s", action)
            if active.can_transmit:
                active.status = GatewayStatus.DEGRADED
            else:
                active.status = GatewayStatus.DISCONNECTED
            return _report(
                FailoverOutcome.FAILOVER_FAILED,
                trigger_reason,
                action=action,
                notes="Both gateways unusable; order flow must stop until manual recovery.",
            )

        # Build the order recovery plan *before* touching any gateway state, so a
        # malformed order list cannot leave the engine half-failed-over.
        plan = self._plan_order_recovery(in_flight_orders)

        logger.warning("EXECUTING GATEWAY FAILOVER: %s", trigger_reason)

        # 1. Demote the failing node before anything is promoted.
        active.status = (
            GatewayStatus.QUIESCED if active.tcp_connected else GatewayStatus.DISCONNECTED
        )

        # 2. Promote the standby.
        standby.status = GatewayStatus.ACTIVE
        self.active_gateway_id = standby.gateway_id

        # 3. Sequence plan for the standby's own session.
        next_seq, seq_note = self._resolve_sequence_plan(standby)

        # 4. Order recovery plan (computed above). Nothing is resent, nothing mutated.
        reconcile = [d.cl_ord_id for d in plan if d.action == OrderRecoveryAction.RECONCILE_REQUIRED]
        reenter = [d.cl_ord_id for d in plan if d.action == OrderRecoveryAction.REENTER_AS_NEW_ORDER]

        notes = (
            f"FAILOVER COMPLETE: '{active.gateway_id}' -> '{standby.gateway_id}'. "
            f"Standby next outbound MsgSeqNum={next_seq} under {self.venue_profile.sequence_policy}. "
            f"{len(reconcile)} order(s) require reconciliation before any resend; "
            f"{len(reenter)} order(s) were deleted by the venue and need re-entry as new orders. "
            "No order has been resent by this engine."
        )
        logger.info(notes)
        for cl_ord_id in reconcile:
            logger.warning(
                "ORDER STATE UNKNOWN [%s]: reconcile via %s before any resend; do NOT resend blind.",
                cl_ord_id, self.venue_profile.order_state_recovery,
            )

        return _report(
            FailoverOutcome.FAILOVER_SUCCESS,
            trigger_reason,
            promoted=standby.gateway_id,
            seq=next_seq,
            seq_note=seq_note,
            plan=plan,
            action=(
                f"RECONCILE_THEN_RESEND: resolve {len(reconcile)} unknown order(s) via "
                f"{self.venue_profile.order_state_recovery}, then call build_resend_plan()."
            ) if reconcile else None,
            notes=notes,
        )

    # -------------------------------------------------------------- resend

    def build_resend_plan(
        self,
        in_flight_orders: Sequence[InFlightOrder],
        report: GatewayFailoverAuditReport,
        reconciliation: Mapping[str, str],
    ) -> List[InFlightOrder]:
        """
        Turn reconciliation evidence into the resend list. Second phase only.

        Only orders the venue positively reported as absent are returned, as
        **copies** marked ``poss_resend=True`` (FIX ``PossResend(97)=Y`` — the
        field for content re-sent under a different sequence number; ``PossDupFlag``
        is the session-layer field and is not used here). Orders still ``UNKNOWN``
        are refused, not resent: an unresolved order is the duplicate-execution
        case, and silence from the venue is not evidence of absence.

        Raises:
            ValueError: if any order needing reconciliation has no verdict, if a
                verdict is not a ``ReconciliationVerdict``, or if any verdict is
                ``UNKNOWN``.
            NotImplementedError: if the venue profile defines no resend marking,
                in which case recovery is restatement-driven and a business-level
                resend is not the correct mechanism.
        """
        if self.venue_profile.resend_marking is None:
            raise NotImplementedError(
                f"{self.venue_profile.venue_id} has no business-level resend marking; "
                "recovery is driven by order book restatement and retransmission. "
                f"{self.venue_profile.source_note}"
            )

        required = set(report.orders_requiring_reconciliation)
        by_id = {order.cl_ord_id: order for order in in_flight_orders}

        missing = sorted(required - set(reconciliation))
        if missing:
            raise ValueError(f"no reconciliation verdict supplied for: {missing}")

        for cl_ord_id, verdict in reconciliation.items():
            _validate_member(verdict, ReconciliationVerdict, f"reconciliation[{cl_ord_id!r}]")

        unresolved = sorted(
            cl_ord_id for cl_ord_id in required
            if reconciliation[cl_ord_id] == ReconciliationVerdict.UNKNOWN
        )
        if unresolved:
            raise ValueError(
                f"reconciliation still UNKNOWN for {unresolved}; resending an order whose "
                "venue state is unknown is the duplicate-execution failure this skill "
                "exists to prevent. Escalate rather than guess."
            )

        resend: List[InFlightOrder] = []
        for cl_ord_id in report.orders_requiring_reconciliation:
            if reconciliation[cl_ord_id] != ReconciliationVerdict.ABSENT_AT_VENUE:
                logger.info("ORDER PRESENT AT VENUE [%s]: no resend.", cl_ord_id)
                continue
            source = by_id.get(cl_ord_id)
            if source is None:
                raise ValueError(f"order {cl_ord_id!r} in the recovery plan is absent from in_flight_orders")
            resend.append(replace(source, poss_resend=True))
            logger.warning(
                "RESENDING ORDER [%s]: %s, venue-confirmed absent.",
                cl_ord_id, self.venue_profile.resend_marking,
            )
        return resend

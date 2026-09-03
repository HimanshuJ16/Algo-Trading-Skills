"""
asic-market-integrity-rules-automated-trading:
Enforces ASIC Automated Order Processing (AOP) pre-trade filters and the
Rule 5.6.3(1)(d)-(e) suspension/cancellation controls ("kill switch").

Regulatory basis (verified against the sources below):
    ASIC Market Integrity Rules (Securities Markets) 2017 (F2017L01474),
    Part 5.5 (Participant's trading infrastructure) and Part 5.6 (Automated
    Order Processing - Filters, conduct, and infrastructure), read with ASIC
    Regulatory Guide RG 241 "Electronic trading" (issued 2 August 2022).

    - Rule 5.6.1(a) / 5.6.3(1)(a)-(b): appropriate automated filters for AOP
      (RG 241.33-241.35). RG 241.35 lists the four permissible filter
      outcomes; this module implements outcome (d), reject outright.
    - Rule 5.6.3(1)(a): processes to record any change to the filters or the
      filter parameters, including intra-day changes (RG 241.43-241.44).
      RG 241.45: ASIC would not accept an AOP system as compliant "where
      filters, filter parameters and exception reports could be deactivated".
    - Rule 5.6.3(1)(d): controls enabling immediate suspension, limitation or
      prohibition of ALL AOP, AOP in respect of ACOP, *or AOP in respect of
      one or more authorised persons, clients, financial products or markets*
      (RG 241.52-241.54). The scope granularity is part of the obligation, so
      the halt state here is scoped, not a single global boolean.
    - Rule 5.6.3(1)(e)(i)-(iv): controls enabling immediate suspension of
      further entry of trading messages in a series, *and cancellation of
      messages in that series that have already entered the market*
      (RG 241.55, 241.58). RG 241.56: messages are part of a series where
      they share a common user, account or algorithm and occur in close
      succession. Cancellation of resting messages is delegated to the order
      management system via ``cancel_series_callback`` - this module does not
      maintain an order book.
    - Rule 5.6.3(2): direct participant control over the automated filters
      and their parameters at administrator level (RG 241.47-241.48).
    - Part 5.6 monitoring/recordkeeping: real-time or close to real-time
      monitoring, exception reporting and post-trade analysis
      (RG 241.81-241.87).

Currency note: as at the last verification (September 2026) the in-force
compilation of the Rules was F2024C01108 (15 October 2024) and RG 241
(2 August 2022) was current. ASIC CP 386 (27 August 2025, submissions closed
22 October 2025) proposes amending Rule 5.6.3, inserting Rule 5.6.3B and
repealing Rule 5.6.8B; those proposals were not law at that check. Re-verify
rule numbering before relying on it for a certification.

Numeric input contract: prices, quantities and reference prices must be
``int`` or ``float``. ``decimal.Decimal`` and string inputs are treated as
non-finite and rejected (fail-closed), so convert at the system boundary.
"""
import dataclasses
import logging
import math
import threading
import time
from dataclasses import field
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


def _is_finite(*values: object) -> bool:
    """Return True only if every value is a real, finite int/float.

    Rejects NaN, +/- Inf, ``bool`` (``isinstance(True, int)`` is True, so an
    unguarded ``bool`` would be silently read as 1 by a safety-critical
    filter) and every non-numeric type, including ``decimal.Decimal``.
    """
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        if not math.isfinite(value):
            return False
    return True


def _is_attributed(*values: str) -> bool:
    """Return True only if every attribution string is a non-blank string."""
    return all(isinstance(v, str) and v.strip() for v in values)


def _normalise_scope_value(value: object) -> str:
    """Canonicalise a halt scope value so lookups cannot silently miss.

    A halt keyed ``"BHP.AX"`` that fails to match an order carrying
    ``"bhp.ax"`` is a suspension control that reports itself active while
    letting the messages through - the failure looks identical to no halt at
    all. Case-folding can only ever make a halt match *more* orders, which is
    the fail-safe direction for a control whose purpose is to stop trading.
    Normalise identities at the system boundary if you need them kept
    distinct by case.
    """
    if not isinstance(value, str):
        return ""
    return value.strip().casefold()


class AopRejectionCode(str, Enum):
    """Machine-readable reason codes for ASIC AOP pre-trade decisions (audit)."""
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    AOP_SCOPE_HALTED = "AOP_SCOPE_HALTED"
    INVALID_ORDER_FIELDS = "INVALID_ORDER_FIELDS"
    NON_FINITE_INPUT = "NON_FINITE_INPUT"
    ZERO_REFERENCE_PRICE = "ZERO_REFERENCE_PRICE"
    VOLUME_LIMIT = "VOLUME_LIMIT"
    VALUE_LIMIT = "VALUE_LIMIT"
    PRICE_DEVIATION = "PRICE_DEVIATION"


class AopHaltScope(str, Enum):
    """Scopes over which AOP may be suspended, limited or prohibited.

    Rule 5.6.3(1)(d), as explained at RG 241.52, requires controls enabling
    immediate suspension, limitation or prohibition of all AOP, AOP in
    respect of ACOP, or AOP in respect of one or more authorised persons,
    clients, financial products or markets. RG 241.53 and RG 241.56 identify
    a particular authorised person, account or algorithm as the source
    granularity for a "series" of trading messages.
    """
    ALL_AOP = "ALL_AOP"
    AUTHORISED_PERSON = "AUTHORISED_PERSON"
    CLIENT = "CLIENT"
    FINANCIAL_PRODUCT = "FINANCIAL_PRODUCT"
    MARKET = "MARKET"
    ALGORITHM = "ALGORITHM"


class KillSwitchEvent(str, Enum):
    TRIGGERED = "TRIGGERED"
    RESET = "RESET"
    RESET_REFUSED = "RESET_REFUSED"


class SeriesCancellationStatus(str, Enum):
    """Outcome of the Rule 5.6.3(1)(e)(ii)/(iv) cancellation hand-off."""
    NOT_CONFIGURED = "NOT_CONFIGURED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclasses.dataclass(frozen=True)
class AsicMarketIntegrityConfig:
    """Hard limits for AOP pre-trade filters, approved by the compliance officer.

    Frozen deliberately. Rule 5.6.3(1)(a) requires processes that record any
    change to the filters or the filter parameters, including intra-day
    changes (RG 241.43), and RG 241.45 states ASIC would not accept an AOP
    system as compliant where filters or filter parameters could be
    deactivated. A mutable config would let ``config.max_order_value_aud =
    1e18`` widen a mandatory control after construction, bypassing the
    validation below and leaving no record. Change parameters only through
    :meth:`AsicAopPreTradeFilter.replace_config`, which validates the new
    values and records the change.

    All limits must be positive and finite; a non-positive or non-finite
    limit would silently disable a mandatory control, which is treated as a
    misconfiguration and rejected at construction time.
    """
    max_order_value_aud: float
    max_order_volume: int
    max_price_deviation_pct: float  # e.g., 0.05 for 5%

    def __post_init__(self) -> None:
        if not _is_finite(self.max_order_value_aud) or self.max_order_value_aud <= 0:
            raise ValueError(
                f"max_order_value_aud must be a positive finite number, got {self.max_order_value_aud!r}"
            )
        if (
            isinstance(self.max_order_volume, bool)
            or not isinstance(self.max_order_volume, int)
            or self.max_order_volume <= 0
        ):
            raise ValueError(
                f"max_order_volume must be a positive integer, got {self.max_order_volume!r}"
            )
        if not _is_finite(self.max_price_deviation_pct) or not (
            0.0 < self.max_price_deviation_pct <= 1.0
        ):
            raise ValueError(
                "max_price_deviation_pct must be in the range (0.0, 1.0], got "
                f"{self.max_price_deviation_pct!r}"
            )


@dataclasses.dataclass
class AopOrderRequest:
    """A trading message presented to the AOP pre-trade filters.

    The identity fields carry the scopes over which AOP may be suspended
    under Rule 5.6.3(1)(d) (RG 241.52) and by which a "series" is identified
    under RG 241.56. They default to empty, so an order that does not carry a
    given identity simply cannot be matched by a halt on that scope.
    """
    symbol: str
    price: float
    qty: int
    reference_price: float  # Last traded price or mid-price
    order_id: str = ""  # Optional; used to correlate audit records
    client_id: str = ""  # Rule 5.6.3(1)(d): AOP for one or more clients
    authorised_person_id: str = ""  # ... or one or more authorised persons
    algorithm_id: str = ""  # RG 241.53/241.56: series source granularity
    market: str = ""  # ... or one or more markets


@dataclasses.dataclass
class ComplianceResult:
    """Result of an ASIC AOP pre-trade check.

    ``is_compliant``/``reason`` are retained for backward compatibility;
    ``rejection_code``, ``order_id`` and ``checked_at_unix`` support the
    machine-readable audit trail expected under ASIC Part 5.6 recordkeeping
    (RG 241.81-241.87).

    This is a point-in-time decision. A halt raised after ``checked_at_unix``
    is not reflected here, so the submission path must re-check the kill
    switch immediately before the message is sent to the venue.
    """
    is_compliant: bool
    reason: str
    rejection_code: Optional[AopRejectionCode] = None
    order_id: str = ""
    checked_at_unix: float = field(default_factory=time.time)


@dataclasses.dataclass(frozen=True)
class AopHaltRecord:
    """An active suspension of AOP over a given scope (Rule 5.6.3(1)(d))."""
    scope: AopHaltScope
    scope_value: str  # "" for ALL_AOP
    triggered_at_unix: float
    reason: str
    actor: str


@dataclasses.dataclass
class KillSwitchAuditEntry:
    event: KillSwitchEvent
    timestamp_unix: float
    reason: str
    actor: str
    scope: AopHaltScope = AopHaltScope.ALL_AOP
    scope_value: str = ""
    # Rule 5.6.3(1)(e)(ii)/(iv): what happened to messages already in the market.
    cancellation_status: Optional[SeriesCancellationStatus] = None
    cancelled_message_count: Optional[int] = None
    cancellation_error: str = ""


@dataclasses.dataclass
class FilterParameterChange:
    """Record of a change to the filter parameters (Rule 5.6.3(1)(a), RG 241.43)."""
    timestamp_unix: float
    authorised_by: str
    reason: str
    previous: AsicMarketIntegrityConfig
    replacement: AsicMarketIntegrityConfig


class AsicKillSwitchManager:
    """Manages the ASIC AOP suspension controls (Rule 5.6.3(1)(d)-(e)).

    Two obligations are distinct and both are represented here:

    * **5.6.3(1)(d)** - immediately suspend, limit or prohibit AOP, either in
      full or in respect of one or more authorised persons, clients,
      financial products or markets (RG 241.52). A global-only boolean
      satisfies only part of the rule, so halts are held per scope.
    * **5.6.3(1)(e)** - suspend further entry of trading messages in a series
      *and cancel messages in that series already in the market* (RG 241.55,
      241.58). This module holds no order book, so cancellation is delegated
      to ``cancel_series_callback``, which receives the
      :class:`AopHaltRecord` and returns the number of messages cancelled. If
      no callback is supplied the audit entry records ``NOT_CONFIGURED`` and
      a warning is logged, so the unmet half of the obligation is visible
      rather than silently assumed.

    The halt state is safety-critical shared state: it is read on every order
    by the pre-trade filter thread and may be written at any time by the
    operations team. All transitions are guarded by a lock and recorded in an
    audit log to satisfy ASIC recordkeeping.
    """

    def __init__(
        self,
        cancel_series_callback: Optional[Callable[[AopHaltRecord], int]] = None,
    ) -> None:
        self._halts: Dict[Tuple[AopHaltScope, str], AopHaltRecord] = {}
        self._audit_log: List[KillSwitchAuditEntry] = []
        self._cancel_series_callback = cancel_series_callback
        self._lock = threading.RLock()

    # --- Rule 5.6.3(1)(d): suspend, limit or prohibit AOP -----------------

    def trigger_kill_switch(self, reason: str = "", actor: str = "") -> None:
        """Halt ALL AOP immediately.

        Deliberately never refuses and never raises on a missing reason or
        actor: blocking a halt because its paperwork is incomplete would be a
        worse failure than an incompletely attributed audit record. Missing
        attribution is logged as a warning instead.
        """
        self._trigger(AopHaltScope.ALL_AOP, "", reason, actor)

    def trigger_scoped_halt(
        self,
        scope: AopHaltScope,
        scope_value: str,
        reason: str = "",
        actor: str = "",
    ) -> None:
        """Halt AOP for one authorised person, client, product, market or algorithm.

        Rule 5.6.3(1)(d) / RG 241.52. Use this in preference to a full halt
        when the interference has been traced to a single source: RG 241.53
        contemplates suspending trading messages "from a particular source
        (e.g. a particular authorised person, account or algorithm)".
        """
        if scope is AopHaltScope.ALL_AOP:
            self._trigger(AopHaltScope.ALL_AOP, "", reason, actor)
            return
        if not isinstance(scope_value, str) or not scope_value.strip():
            raise ValueError(
                f"scope_value must be a non-blank string for scope {scope.value}"
            )
        self._trigger(scope, scope_value.strip(), reason, actor)

    def _trigger(
        self, scope: AopHaltScope, scope_value: str, reason: str, actor: str
    ) -> None:
        if not _is_attributed(reason, actor):
            logger.warning(
                "ASIC AOP halt raised without full attribution (reason=%r actor=%r). "
                "The halt is applied regardless; complete the record retrospectively.",
                reason,
                actor,
            )
        key = (scope, _normalise_scope_value(scope_value))
        record = AopHaltRecord(scope, scope_value, time.time(), reason, actor)
        # Apply the halt first and release the lock before calling out to the
        # OMS: the cancellation callback is a network call, and holding the
        # lock across it would stall every concurrent halt_blocking() read -
        # i.e. block the pre-trade gate for unrelated orders while a scoped
        # halt is being processed.
        with self._lock:
            self._halts[key] = record
        status, count, error = self._cancel_series(record)
        with self._lock:
            self._audit_log.append(
                KillSwitchAuditEntry(
                    event=KillSwitchEvent.TRIGGERED,
                    timestamp_unix=record.triggered_at_unix,
                    reason=reason,
                    actor=actor,
                    scope=scope,
                    scope_value=scope_value,
                    cancellation_status=status,
                    cancelled_message_count=count,
                    cancellation_error=error,
                )
            )
        logger.critical(
            "ASIC AOP HALT TRIGGERED. scope=%s value=%s reason=%s actor=%s cancellation=%s",
            scope.value,
            scope_value or "(all)",
            reason or "(unspecified)",
            actor or "(unspecified)",
            status.value,
        )

    def _cancel_series(
        self, record: AopHaltRecord
    ) -> Tuple[SeriesCancellationStatus, Optional[int], str]:
        """Hand off cancellation of already-entered messages (Rule 5.6.3(1)(e)).

        The callback is isolated: a raising callback must not prevent the halt
        from taking effect, because the halt is the control that stops the
        bleeding. A failure is escalated at CRITICAL and recorded, because
        that is the outcome where messages are still live in the market
        during a breach.
        """
        if self._cancel_series_callback is None:
            logger.warning(
                "No cancel_series_callback configured: messages already entered for "
                "scope=%s value=%s were NOT cancelled. Rule 5.6.3(1)(e)(ii)/(iv) "
                "requires the participant to be able to cancel them.",
                record.scope.value,
                record.scope_value or "(all)",
            )
            return SeriesCancellationStatus.NOT_CONFIGURED, None, ""
        try:
            cancelled = int(self._cancel_series_callback(record))
        except Exception as exc:  # the halt must survive any callback failure
            logger.critical(
                "ASIC AOP SERIES CANCELLATION FAILED for scope=%s value=%s: %s. "
                "Trading messages may still be live in the market.",
                record.scope.value,
                record.scope_value or "(all)",
                exc,
                exc_info=True,
            )
            return SeriesCancellationStatus.FAILED, None, f"{type(exc).__name__}: {exc}"
        return SeriesCancellationStatus.COMPLETED, cancelled, ""

    # --- Releasing a halt -------------------------------------------------

    def reset_kill_switch(self, reason: str = "", actor: str = "") -> bool:
        """Release the ALL_AOP halt. Returns True only if a halt was released.

        Refuses a blank reason or actor. RG 241.44 expects changes at the
        administrator level to be implemented only after authorisation by a
        qualified person, and resuming AOP after a halt is the direction that
        can put messages back into the market. Refusals are themselves
        audited. Scoped halts are unaffected and must be released
        individually via :meth:`release_scoped_halt`.
        """
        return self.release_scoped_halt(AopHaltScope.ALL_AOP, "", reason, actor)

    def release_scoped_halt(
        self,
        scope: AopHaltScope,
        scope_value: str = "",
        reason: str = "",
        actor: str = "",
    ) -> bool:
        """Release one halt. Returns True only if a halt was actually released."""
        key = (
            scope,
            "" if scope is AopHaltScope.ALL_AOP else _normalise_scope_value(scope_value),
        )
        if not _is_attributed(reason, actor):
            with self._lock:
                self._audit_log.append(
                    KillSwitchAuditEntry(
                        event=KillSwitchEvent.RESET_REFUSED,
                        timestamp_unix=time.time(),
                        reason=reason,
                        actor=actor,
                        scope=key[0],
                        scope_value=key[1],
                    )
                )
            logger.warning(
                "ASIC AOP halt release REFUSED for scope=%s value=%s: a non-blank "
                "reason and actor are required (RG 241.44). Halt remains in force.",
                key[0].value,
                key[1] or "(all)",
            )
            return False
        with self._lock:
            if key not in self._halts:
                logger.warning(
                    "ASIC AOP halt release requested for scope=%s value=%s, but no "
                    "such halt is active.",
                    key[0].value,
                    key[1] or "(all)",
                )
                return False
            del self._halts[key]
            self._audit_log.append(
                KillSwitchAuditEntry(
                    event=KillSwitchEvent.RESET,
                    timestamp_unix=time.time(),
                    reason=reason,
                    actor=actor,
                    scope=key[0],
                    scope_value=key[1],
                )
            )
        logger.warning(
            "ASIC AOP halt released. scope=%s value=%s reason=%s actor=%s",
            key[0].value,
            key[1] or "(all)",
            reason,
            actor,
        )
        return True

    # --- Reading halt state ----------------------------------------------

    @property
    def is_halted(self) -> bool:
        """True when ALL AOP is halted. Does not reflect scoped halts."""
        with self._lock:
            return (AopHaltScope.ALL_AOP, "") in self._halts

    def halt_blocking(self, order: AopOrderRequest) -> Optional[AopHaltRecord]:
        """Return the halt that blocks this order, or None if none applies."""
        with self._lock:
            global_halt = self._halts.get((AopHaltScope.ALL_AOP, ""))
            if global_halt is not None:
                return global_halt
            for scope, value in (
                (AopHaltScope.AUTHORISED_PERSON, order.authorised_person_id),
                (AopHaltScope.CLIENT, order.client_id),
                (AopHaltScope.FINANCIAL_PRODUCT, order.symbol),
                (AopHaltScope.MARKET, order.market),
                (AopHaltScope.ALGORITHM, order.algorithm_id),
            ):
                normalised = _normalise_scope_value(value)
                if not normalised:
                    continue
                halt = self._halts.get((scope, normalised))
                if halt is not None:
                    return halt
        return None

    @property
    def triggered_at(self) -> Optional[float]:
        """Timestamp of the active ALL_AOP halt, or None."""
        with self._lock:
            halt = self._halts.get((AopHaltScope.ALL_AOP, ""))
            return halt.triggered_at_unix if halt else None

    @property
    def active_halts(self) -> List[AopHaltRecord]:
        """Snapshot of every halt currently in force."""
        with self._lock:
            return list(self._halts.values())

    @property
    def audit_log(self) -> List[KillSwitchAuditEntry]:
        """Return a shallow copy of the audit log for external review."""
        with self._lock:
            return list(self._audit_log)


class AsicAopPreTradeFilter:
    """
    Enforces ASIC pre-trade filters before an order reaches the market
    (Rule 5.6.1(a) / 5.6.3(1)(a)-(b)). Filters actively *reject* non-compliant
    orders rather than merely alerting - RG 241.35 outcome (d).
    """

    def __init__(self, config: AsicMarketIntegrityConfig, kill_switch: AsicKillSwitchManager):
        if not isinstance(config, AsicMarketIntegrityConfig):
            raise TypeError(
                f"config must be an AsicMarketIntegrityConfig, got {type(config).__name__}"
            )
        self._config = config
        self._parameter_audit_log: List[FilterParameterChange] = []
        self._lock = threading.Lock()
        self.kill_switch = kill_switch

    @property
    def config(self) -> AsicMarketIntegrityConfig:
        """Current filter parameters. Read-only: use :meth:`replace_config`."""
        with self._lock:
            return self._config

    @property
    def parameter_audit_log(self) -> List[FilterParameterChange]:
        """Every filter-parameter change made through :meth:`replace_config`."""
        with self._lock:
            return list(self._parameter_audit_log)

    def replace_config(
        self,
        new_config: AsicMarketIntegrityConfig,
        authorised_by: str,
        reason: str,
    ) -> None:
        """Change the filter parameters under recorded, attributed control.

        Rule 5.6.3(2) requires direct participant control over the filters and
        filter parameters at administrator level (RG 241.47-241.48), and Rule
        5.6.3(1)(a) requires processes to record any change to them, including
        intra-day changes (RG 241.43). Raises rather than silently applying an
        unattributed change. Note that a material change may additionally
        require a review under Rule 5.6.8 before it is implemented.
        """
        if not isinstance(new_config, AsicMarketIntegrityConfig):
            raise TypeError(
                f"new_config must be an AsicMarketIntegrityConfig, got {type(new_config).__name__}"
            )
        if not _is_attributed(authorised_by, reason):
            raise ValueError(
                "replace_config requires a non-blank authorised_by and reason "
                "(Rule 5.6.3(1)(a); RG 241.43-241.44)"
            )
        with self._lock:
            change = FilterParameterChange(
                timestamp_unix=time.time(),
                authorised_by=authorised_by,
                reason=reason,
                previous=self._config,
                replacement=new_config,
            )
            self._config = new_config
            self._parameter_audit_log.append(change)
        logger.warning(
            "ASIC AOP filter parameters changed by %s (reason=%s): %s -> %s",
            authorised_by,
            reason,
            change.previous,
            change.replacement,
        )

    def run_checks(self, order: AopOrderRequest) -> ComplianceResult:
        config = self.config

        # 1. Halt check (highest priority) - Rule 5.6.3(1)(d), RG 241.52.
        #    Covers both the full halt and a halt scoped to this order's
        #    authorised person, client, product, market or algorithm.
        halt = self.kill_switch.halt_blocking(order)
        if halt is not None:
            if halt.scope is AopHaltScope.ALL_AOP:
                return ComplianceResult(
                    False,
                    "REJECTED: ASIC AOP Kill Switch is currently active.",
                    AopRejectionCode.KILL_SWITCH_ACTIVE,
                    order.order_id,
                )
            return ComplianceResult(
                False,
                f"REJECTED: AOP suspended for {halt.scope.value}={halt.scope_value}.",
                AopRejectionCode.AOP_SCOPE_HALTED,
                order.order_id,
            )

        # 2. Non-finite input check (NaN/Inf must never pass a safety-critical filter,
        #    since NaN comparisons silently evaluate to False and would bypass limits).
        if not _is_finite(order.price, order.qty, order.reference_price):
            return ComplianceResult(
                False,
                "REJECTED: Order contains non-finite (NaN/Inf) or non-numeric input.",
                AopRejectionCode.NON_FINITE_INPUT,
                order.order_id,
            )

        # 3. Basic Sanity Checks
        if order.qty <= 0 or order.price <= 0:
            return ComplianceResult(
                False,
                "REJECTED: Invalid order quantity or price.",
                AopRejectionCode.INVALID_ORDER_FIELDS,
                order.order_id,
            )

        # A valid, positive reference price is required to compute price deviation.
        # A zero/stale reference price must be rejected rather than causing a
        # ZeroDivisionError, which would take the pre-trade control offline.
        if order.reference_price <= 0:
            return ComplianceResult(
                False,
                "REJECTED: Reference price must be positive to evaluate price deviation.",
                AopRejectionCode.ZERO_REFERENCE_PRICE,
                order.order_id,
            )

        # 4. Maximum Volume Check
        if order.qty > config.max_order_volume:
            return ComplianceResult(
                False,
                f"REJECTED: Order volume ({order.qty}) exceeds AOP limit ({config.max_order_volume}).",
                AopRejectionCode.VOLUME_LIMIT,
                order.order_id,
            )

        # 5. Maximum Value Check
        order_value = order.price * order.qty
        if order_value > config.max_order_value_aud:
            return ComplianceResult(
                False,
                f"REJECTED: Order value (${order_value:,.2f}) exceeds AOP limit (${config.max_order_value_aud:,.2f}).",
                AopRejectionCode.VALUE_LIMIT,
                order.order_id,
            )

        # 6. Price Deviation Check (erroneous / "fat finger" order - RG 241.39-241.41).
        #    Compared by multiplication rather than division: dividing can render a
        #    price at exactly the limit as 0.05000000000000001 and reject an order
        #    that precisely meets the threshold.
        if abs(order.price - order.reference_price) > (
            config.max_price_deviation_pct * order.reference_price
        ):
            deviation = abs(order.price - order.reference_price) / order.reference_price
            return ComplianceResult(
                False,
                f"REJECTED: Price deviation ({deviation:.1%}) exceeds AOP limit ({config.max_price_deviation_pct:.1%}).",
                AopRejectionCode.PRICE_DEVIATION,
                order.order_id,
            )

        logger.info(
            "ASIC Pre-Trade Filter Passed: %s %s @ %s", order.symbol, order.qty, order.price
        )
        return ComplianceResult(
            True,
            "APPROVED: Order passed all ASIC AOP pre-trade filters.",
            None,
            order.order_id,
        )

"""
asic-market-integrity-rules-automated-trading:
Enforces ASIC Automated Order Processing (AOP) pre-trade filters and kill switches.

Regulatory basis:
    ASIC Market Integrity Rules (Securities Markets) 2017, Part 5.6
    (Automated Order Processing - Filters, conduct, and infrastructure),
    together with ASIC Regulatory Guide RG 241 (Electronic trading).

    - Rule 5.6.1 / 5.6.3(1)(a)-(b): appropriate automated pre-trade filters
      under direct participant control (RG 241.33-35, 47-48).
    - Rule 5.6.3(1)(d)-(e): kill-switch controls to suspend/prohibit AOP and
      suspend or cancel a series of related trading messages (RG 241.54-58).
    - Part 5.6 generally: real-time monitoring, exception reporting and
      recordkeeping for auditability (RG 241.81-87).
"""
import dataclasses
import logging
import math
import threading
import time
from dataclasses import field
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


def _is_finite(value: float) -> bool:
    """Return True only for real, finite numbers (rejects NaN and +/- Inf)."""
    return isinstance(value, (int, float)) and math.isfinite(float(value))


class AopRejectionCode(str, Enum):
    """Machine-readable reason codes for ASIC AOP pre-trade decisions (audit)."""
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"
    INVALID_ORDER_FIELDS = "INVALID_ORDER_FIELDS"
    NON_FINITE_INPUT = "NON_FINITE_INPUT"
    ZERO_REFERENCE_PRICE = "ZERO_REFERENCE_PRICE"
    VOLUME_LIMIT = "VOLUME_LIMIT"
    VALUE_LIMIT = "VALUE_LIMIT"
    PRICE_DEVIATION = "PRICE_DEVIATION"


class KillSwitchEvent(str, Enum):
    TRIGGERED = "TRIGGERED"
    RESET = "RESET"


@dataclasses.dataclass
class AsicMarketIntegrityConfig:
    """Hard limits for AOP pre-trade filters, approved by the compliance officer.

    All limits must be positive and finite; a non-positive or non-finite limit
    would silently disable a mandatory control, which is treated as a
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
        if not isinstance(self.max_order_volume, int) or self.max_order_volume <= 0:
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
    symbol: str
    price: float
    qty: int
    reference_price: float  # Last traded price or mid-price
    order_id: str = ""  # Optional; used to correlate audit records


@dataclasses.dataclass
class ComplianceResult:
    """Result of an ASIC AOP pre-trade check.

    ``is_compliant``/``reason`` are retained for backward compatibility;
    ``rejection_code``, ``order_id`` and ``checked_at_unix`` support the
    machine-readable audit trail expected under ASIC Part 5.6 recordkeeping.
    """
    is_compliant: bool
    reason: str
    rejection_code: Optional[AopRejectionCode] = None
    order_id: str = ""
    checked_at_unix: float = field(default_factory=time.time)


@dataclasses.dataclass
class KillSwitchAuditEntry:
    event: KillSwitchEvent
    timestamp_unix: float
    reason: str
    actor: str


class AsicKillSwitchManager:
    """Manages the mandatory ASIC AOP kill switch (Rule 5.6.3(1)(d)-(e)).

    The kill switch is safety-critical shared state: it is read on every order
    by the pre-trade filter thread and may be triggered at any time by the
    operations team. All state transitions are therefore guarded by a lock and
    recorded in an immutable audit log to satisfy ASIC recordkeeping.
    """
    def __init__(self) -> None:
        self._is_triggered = False
        self._triggered_at: Optional[float] = None
        self._audit_log: List[KillSwitchAuditEntry] = []
        self._lock = threading.Lock()

    def trigger_kill_switch(self, reason: str = "", actor: str = "") -> None:
        with self._lock:
            self._is_triggered = True
            self._triggered_at = time.time()
            self._audit_log.append(
                KillSwitchAuditEntry(KillSwitchEvent.TRIGGERED, self._triggered_at, reason, actor)
            )
        logger.critical(
            "ASIC AOP KILL SWITCH TRIGGERED. reason=%s actor=%s All trading halted.",
            reason or "(unspecified)",
            actor or "(unspecified)",
        )

    def reset_kill_switch(self, reason: str = "", actor: str = "") -> None:
        with self._lock:
            self._is_triggered = False
            self._triggered_at = None
            self._audit_log.append(
                KillSwitchAuditEntry(KillSwitchEvent.RESET, time.time(), reason, actor)
            )
        logger.warning(
            "ASIC AOP Kill Switch reset. reason=%s actor=%s Trading resumed.",
            reason or "(unspecified)",
            actor or "(unspecified)",
        )

    @property
    def is_halted(self) -> bool:
        with self._lock:
            return self._is_triggered

    @property
    def triggered_at(self) -> Optional[float]:
        with self._lock:
            return self._triggered_at

    @property
    def audit_log(self) -> List[KillSwitchAuditEntry]:
        """Return a shallow copy of the audit log for external review."""
        with self._lock:
            return list(self._audit_log)


class AsicAopPreTradeFilter:
    """
    Enforces ASIC pre-trade filters before an order reaches the market
    (Rule 5.6.1 / 5.6.3(1)(a)-(b)). Filters actively *reject* non-compliant
    orders rather than merely alerting (RG 241.35).
    """
    def __init__(self, config: AsicMarketIntegrityConfig, kill_switch: AsicKillSwitchManager):
        self.config = config
        self.kill_switch = kill_switch

    def run_checks(self, order: AopOrderRequest) -> ComplianceResult:
        # 1. Kill Switch Check (Highest Priority) - Rule 5.6.3(1)(d)
        if self.kill_switch.is_halted:
            return ComplianceResult(
                False,
                "REJECTED: ASIC AOP Kill Switch is currently active.",
                AopRejectionCode.KILL_SWITCH_ACTIVE,
                order.order_id,
            )

        # 2. Non-finite input check (NaN/Inf must never pass a safety-critical filter,
        #    since NaN comparisons silently evaluate to False and would bypass limits).
        if not (_is_finite(order.price) and _is_finite(order.qty) and _is_finite(order.reference_price)):
            return ComplianceResult(
                False,
                "REJECTED: Order contains non-finite (NaN/Inf) numeric input.",
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
        if order.qty > self.config.max_order_volume:
            return ComplianceResult(
                False,
                f"REJECTED: Order volume ({order.qty}) exceeds AOP limit ({self.config.max_order_volume}).",
                AopRejectionCode.VOLUME_LIMIT,
                order.order_id,
            )

        # 5. Maximum Value Check
        order_value = order.price * order.qty
        if order_value > self.config.max_order_value_aud:
            return ComplianceResult(
                False,
                f"REJECTED: Order value (${order_value:,.2f}) exceeds AOP limit (${self.config.max_order_value_aud:,.2f}).",
                AopRejectionCode.VALUE_LIMIT,
                order.order_id,
            )

        # 6. Price Deviation Check (Fat Finger / Market Integrity)
        deviation = abs(order.price - order.reference_price) / order.reference_price
        if deviation > self.config.max_price_deviation_pct:
            return ComplianceResult(
                False,
                f"REJECTED: Price deviation ({deviation:.1%}) exceeds AOP limit ({self.config.max_price_deviation_pct:.1%}).",
                AopRejectionCode.PRICE_DEVIATION,
                order.order_id,
            )

        logger.info(f"ASIC Pre-Trade Filter Passed: {order.symbol} {order.qty} @ {order.price}")
        return ComplianceResult(
            True,
            "APPROVED: Order passed all ASIC AOP pre-trade filters.",
            None,
            order.order_id,
        )

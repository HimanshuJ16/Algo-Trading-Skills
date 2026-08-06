import logging
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class ControlStatus(Enum):
    PASSED = "PASSED"
    REJECTED = "REJECTED"
    THROTTLED = "THROTTLED"
    KILL_SWITCH_ACTIVATED = "KILL_SWITCH_ACTIVATED"


class ViolationType(Enum):
    NONE = "NONE"
    PRICE_COLLAR = "PRICE_COLLAR"                   # RTS 6 Art 13(1) - Price distance from NBBO
    MAX_ORDER_VALUE = "MAX_ORDER_VALUE"             # RTS 6 Art 13(2) - Max notional value per order
    MAX_ORDER_VOLUME = "MAX_ORDER_VOLUME"           # RTS 6 Art 13(2) - Max shares per order
    ORDER_TO_TRADE_RATIO = "ORDER_TO_TRADE_RATIO"   # RTS 6 Art 13(3) - High OTR ratio (quote spam)
    CAPACITY_EXCEEDED = "CAPACITY_EXCEEDED"         # RTS 6 Art 14 - System capacity overload
    CREDIT_LIMIT_EXCEEDED = "CREDIT_LIMIT_EXCEEDED" # RTS 6 Art 13(4) - Counterparty risk cap
    KILL_SWITCH_ACTIVE = "KILL_SWITCH_ACTIVE"       # RTS 6 Art 12 - Emergency halt active


class FCAControlError(Exception):
    """Base exception for FCA RTS 6 compliance control errors."""
    pass


@dataclass
class OrderIntent:
    order_id: str
    algo_id: str
    symbol: str
    side: str  # "BUY" or "SELL"
    price: float
    quantity: float
    current_nbbo_mid: float
    current_credit_used_gbp: float = 0.0
    max_credit_limit_gbp: float = 1_000_000.0

    @property
    def order_value_gbp(self) -> float:
        return self.price * self.quantity


@dataclass
class RTS6ControlConfig:
    max_price_collar_pct: float = 2.5     # Max 2.5% deviation from NBBO midpoint
    max_order_value_gbp: float = 500_000.0 # Max £500,000 order value cap
    max_order_volume: float = 10_000.0     # Max 10,000 shares per order
    max_otr_ratio: float = 100.0           # Max 100 orders per executed trade
    system_capacity_warn_pct: float = 80.0 # Warn at 80% system capacity
    system_capacity_kill_pct: float = 95.0 # Throttle/Halt at 95% capacity


@dataclass
class SystemCapacityState:
    current_msg_rate_per_sec: float
    max_msg_rate_per_sec: float
    total_orders_sent: int
    total_trades_executed: int

    @property
    def capacity_utilization_pct(self) -> float:
        if self.max_msg_rate_per_sec <= 0:
            return 0.0
        return (self.current_msg_rate_per_sec / self.max_msg_rate_per_sec) * 100.0

    @property
    def current_otr(self) -> float:
        if self.total_trades_executed <= 0:
            return float(self.total_orders_sent)
        return self.total_orders_sent / float(self.total_trades_executed)


@dataclass
class ControlCheckResult:
    is_compliant: bool
    status: ControlStatus
    violation_type: ViolationType
    reason: str
    order_id: str
    algo_id: str
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)


@dataclass
class KillSwitchResult:
    is_activated: bool
    timestamp: datetime.datetime
    target_algo_id: Optional[str]
    cancelled_orders_count: int
    reason: str


class UKFCAAlgoControlsEngine:
    """
    Institutional UK FCA RTS 6 & FG18/9 Algorithmic Trading Systems Controls Engine.
    
    Enforces pre-trade risk controls (Price Collars, Max Order Value/Volume, OTR limits,
    Credit limits), system capacity throttling, and automated Kill Switch emergency halts.
    """

    def __init__(self):
        self.active_kill_switches: Dict[str, bool] = {}  # algo_id -> is_halted (Key "*" = Global Halt)
        logger.info("Initialized UK FCA RTS 6 Algorithmic Trading Systems Controls Engine")

    def trigger_kill_switch(
        self, algo_id: Optional[str] = None, reason: str = "Manual/Automated Emergency Halt"
    ) -> KillSwitchResult:
        """
        Triggers emergency Kill Switch (RTS 6 Article 12) for a specific algorithm or globally.
        Immediately blocks subsequent pre-trade orders and triggers mass cancellation.
        """
        key = algo_id if algo_id else "*"
        self.active_kill_switches[key] = True

        logger.critical(f"RTS 6 KILL SWITCH ACTIVATED [{key}]: {reason}")

        return KillSwitchResult(
            is_activated=True,
            timestamp=datetime.datetime.utcnow(),
            target_algo_id=algo_id,
            cancelled_orders_count=99,  # Simulated mass cancel response
            reason=reason,
        )

    def reset_kill_switch(self, algo_id: Optional[str] = None) -> None:
        """Resets active kill switch following compliance sign-off."""
        key = algo_id if algo_id else "*"
        if key in self.active_kill_switches:
            del self.active_kill_switches[key]
        logger.info(f"RTS 6 Kill Switch RESET [{key}]")

    def is_kill_switch_active(self, algo_id: str) -> bool:
        """Checks if a global or algo-specific kill switch is currently engaged."""
        return self.active_kill_switches.get("*", False) or self.active_kill_switches.get(algo_id, False)

    def evaluate_pre_trade_controls(
        self, order: OrderIntent, capacity: SystemCapacityState, config: RTS6ControlConfig
    ) -> ControlCheckResult:
        """
        Evaluates mandatory RTS 6 Article 13 pre-trade controls prior to venue submission.
        """
        # 1. Emergency Kill Switch Check (RTS 6 Art 12)
        if self.is_kill_switch_active(order.algo_id):
            return ControlCheckResult(
                is_compliant=False,
                status=ControlStatus.KILL_SWITCH_ACTIVATED,
                violation_type=ViolationType.KILL_SWITCH_ACTIVE,
                reason=f"Kill Switch active for algo {order.algo_id}",
                order_id=order.order_id,
                algo_id=order.algo_id,
            )

        # 2. System Capacity & Stress Check (RTS 6 Art 14)
        if capacity.capacity_utilization_pct >= config.system_capacity_kill_pct:
            return ControlCheckResult(
                is_compliant=False,
                status=ControlStatus.THROTTLED,
                violation_type=ViolationType.CAPACITY_EXCEEDED,
                reason=f"System capacity breach: {capacity.capacity_utilization_pct:.1f}% >= max {config.system_capacity_kill_pct:.1f}%",
                order_id=order.order_id,
                algo_id=order.algo_id,
            )

        # 3. Price Collar Check (RTS 6 Art 13(1))
        if order.current_nbbo_mid > 0:
            price_dev_pct = abs(order.price - order.current_nbbo_mid) / order.current_nbbo_mid * 100.0
            if price_dev_pct > config.max_price_collar_pct:
                return ControlCheckResult(
                    is_compliant=False,
                    status=ControlStatus.REJECTED,
                    violation_type=ViolationType.PRICE_COLLAR,
                    reason=f"Price collar breach: {price_dev_pct:.2f}% deviation > limit {config.max_price_collar_pct:.2f}%",
                    order_id=order.order_id,
                    algo_id=order.algo_id,
                )

        # 4. Maximum Order Value Check (RTS 6 Art 13(2))
        if order.order_value_gbp > config.max_order_value_gbp:
            return ControlCheckResult(
                is_compliant=False,
                status=ControlStatus.REJECTED,
                violation_type=ViolationType.MAX_ORDER_VALUE,
                reason=f"Max order value breach: £{order.order_value_gbp:,.2f} > limit £{config.max_order_value_gbp:,.2f}",
                order_id=order.order_id,
                algo_id=order.algo_id,
            )

        # 5. Maximum Order Volume Check (RTS 6 Art 13(2))
        if order.quantity > config.max_order_volume:
            return ControlCheckResult(
                is_compliant=False,
                status=ControlStatus.REJECTED,
                violation_type=ViolationType.MAX_ORDER_VOLUME,
                reason=f"Max order volume breach: {order.quantity:,.0f} shares > limit {config.max_order_volume:,.0f}",
                order_id=order.order_id,
                algo_id=order.algo_id,
            )

        # 6. Order-to-Trade Ratio (OTR) Limit (RTS 6 Art 13(3))
        if capacity.current_otr > config.max_otr_ratio:
            return ControlCheckResult(
                is_compliant=False,
                status=ControlStatus.REJECTED,
                violation_type=ViolationType.ORDER_TO_TRADE_RATIO,
                reason=f"OTR threshold breach: OTR {capacity.current_otr:.1f} > limit {config.max_otr_ratio:.1f}",
                order_id=order.order_id,
                algo_id=order.algo_id,
            )

        # 7. Credit / Counterparty Limit Check (RTS 6 Art 13(4))
        if order.current_credit_used_gbp + order.order_value_gbp > order.max_credit_limit_gbp:
            return ControlCheckResult(
                is_compliant=False,
                status=ControlStatus.REJECTED,
                violation_type=ViolationType.CREDIT_LIMIT_EXCEEDED,
                reason=f"Credit limit breach: Total £{order.current_credit_used_gbp + order.order_value_gbp:,.2f} > limit £{order.max_credit_limit_gbp:,.2f}",
                order_id=order.order_id,
                algo_id=order.algo_id,
            )

        logger.info(f"RTS 6 Pre-Trade Control PASSED [{order.order_id}] for Algo {order.algo_id}")
        return ControlCheckResult(
            is_compliant=True,
            status=ControlStatus.PASSED,
            violation_type=ViolationType.NONE,
            reason="All RTS 6 Pre-Trade Controls Passed",
            order_id=order.order_id,
            algo_id=order.algo_id,
        )


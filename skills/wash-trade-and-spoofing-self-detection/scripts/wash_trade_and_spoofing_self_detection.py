import logging
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class OrderAction(Enum):
    PLACE = "PLACE"
    CANCEL = "CANCEL"
    FILL = "FILL"


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class ViolationType(Enum):
    WASH_TRADE = "WASH_TRADE"                   # Self-cross execution without change in beneficial ownership
    SPOOFING_LAYERING = "SPOOFING_LAYERING"     # Non-bona fide orders canceled before execution after opposite fill
    HIGH_CANCELLATION_RATIO = "HIGH_CANCELLATION" # Abnormally high order cancellation ratio (>= 90%)


class SurveillanceError(Exception):
    """Base exception for Wash Trade and Spoofing Detection Engine errors."""
    pass


@dataclass
class OrderEvent:
    event_id: str
    order_id: str
    trader_id: str
    account_id: str
    symbol: str
    side: OrderSide
    price: float
    quantity: float
    action: OrderAction
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)


@dataclass
class SurveillanceViolation:
    violation_id: str
    trader_id: str
    symbol: str
    violation_type: ViolationType
    severity: str                               # "MEDIUM", "HIGH", "CRITICAL"
    description: str
    related_event_ids: List[str] = field(default_factory=list)
    timestamp: datetime.datetime = field(default_factory=datetime.datetime.utcnow)


@dataclass
class TraderMetrics:
    trader_id: str
    total_orders_placed: int
    total_orders_canceled: int
    total_orders_filled: int
    cancellation_ratio_pct: float
    avg_order_lifespan_ms: float


class WashTradeAndSpoofingDetectionEngine:
    """
    Institutional Wash Trade & Spoofing Self-Detection Engine (CFTC / SEC / FINRA / MiFID II RTS 6).
    
    Monitors real-time order and fill event streams, detects self-cross wash trading patterns,
    flags spoofing/layering behavior (rapid cancellations following opposite-side fills),
    tracks order cancellation ratios and lifespans, and generates compliance alert reports.
    """

    def __init__(
        self,
        wash_trade_window_seconds: float = 2.0,
        spoofing_lifespan_threshold_ms: float = 1000.0,
        cancellation_ratio_threshold_pct: float = 90.0,
    ):
        """
        :param wash_trade_window_seconds: Time window to match opposite self-orders for wash trading.
        :param spoofing_lifespan_threshold_ms: Order lifespan in ms considered suspicious if canceled.
        :param cancellation_ratio_threshold_pct: Order cancellation % threshold triggering alert.
        """
        self.wash_trade_window_seconds = wash_trade_window_seconds
        self.spoofing_lifespan_threshold_ms = spoofing_lifespan_threshold_ms
        self.cancellation_ratio_threshold_pct = cancellation_ratio_threshold_pct

        # Active order book tracking: order_id -> OrderEvent
        self.active_orders: Dict[str, OrderEvent] = {}
        # Order placement timestamps: order_id -> placement datetime
        self.order_placement_times: Dict[str, datetime.datetime] = {}
        # History of event logs per trader
        self.trader_history: Dict[str, List[OrderEvent]] = {}
        # Violation logs
        self.violations: List[SurveillanceViolation] = []

        logger.info("Initialized Wash Trade & Spoofing Self-Detection Engine")

    def ingest_order_event(self, event: OrderEvent) -> List[SurveillanceViolation]:
        """
        Ingests an order event (PLACE, CANCEL, FILL) and runs real-time surveillance checks.
        """
        if event.price <= 0 or event.quantity <= 0:
            raise SurveillanceError("Order price and quantity must be positive.")

        if event.trader_id not in self.trader_history:
            self.trader_history[event.trader_id] = []
        self.trader_history[event.trader_id].append(event)

        detected_violations = []

        if event.action == OrderAction.PLACE:
            self.active_orders[event.order_id] = event
            self.order_placement_times[event.order_id] = event.timestamp

            # Check Wash Trade (Self-Cross Execution Risk)
            wash_viol = self.check_wash_trade_self_cross(event)
            if wash_viol:
                detected_violations.append(wash_viol)

        elif event.action == OrderAction.FILL:
            # Check if fill triggers a spoofing pattern on opposite side orders
            spoof_viol = self.check_spoofing_pattern_on_fill(event)
            if spoof_viol:
                detected_violations.append(spoof_viol)

            if event.order_id in self.active_orders:
                del self.active_orders[event.order_id]

        elif event.action == OrderAction.CANCEL:
            # Check rapid order lifespan cancellation
            if event.order_id in self.order_placement_times:
                placed_at = self.order_placement_times[event.order_id]
                lifespan_ms = (event.timestamp - placed_at).total_seconds() * 1000.0

                if lifespan_ms < self.spoofing_lifespan_threshold_ms:
                    logger.warning(f"Rapid Order Cancellation [{event.order_id}] Lifespan={lifespan_ms:.1f}ms < {self.spoofing_lifespan_threshold_ms}ms")

            if event.order_id in self.active_orders:
                del self.active_orders[event.order_id]

        # Periodically check overall trader metrics for high cancellation ratio
        metrics = self.get_trader_metrics(event.trader_id)
        if metrics.total_orders_placed >= 10 and metrics.cancellation_ratio_pct >= self.cancellation_ratio_threshold_pct:
            canc_viol = SurveillanceViolation(
                violation_id=f"VIOL-CANC-{int(event.timestamp.timestamp())}",
                trader_id=event.trader_id,
                symbol=event.symbol,
                violation_type=ViolationType.HIGH_CANCELLATION_RATIO,
                severity="HIGH",
                description=f"High Order Cancellation Ratio: {metrics.cancellation_ratio_pct:.1f}% ({metrics.total_orders_canceled}/{metrics.total_orders_placed} canceled)",
                related_event_ids=[event.event_id],
                timestamp=event.timestamp,
            )
            detected_violations.append(canc_viol)

        self.violations.extend(detected_violations)
        return detected_violations

    def check_wash_trade_self_cross(self, event: OrderEvent) -> Optional[SurveillanceViolation]:
        """
        Detects potential wash trade when an incoming order matches an active resting order
        from the same trader/account on the opposite side at the same price within time window.
        """
        opposite_side = OrderSide.SELL if event.side == OrderSide.BUY else OrderSide.BUY

        for active_id, active_order in list(self.active_orders.items()):
            if active_id == event.order_id:
                continue

            # Wash trade condition: Same trader/account, opposite side, matching price
            is_same_owner = (active_order.trader_id == event.trader_id) or (active_order.account_id == event.account_id)
            is_opposite = (active_order.side == opposite_side)
            is_same_symbol = (active_order.symbol == event.symbol)
            is_price_match = (active_order.price == event.price)

            time_diff = abs((event.timestamp - active_order.timestamp).total_seconds())

            if is_same_owner and is_opposite and is_same_symbol and is_price_match and time_diff <= self.wash_trade_window_seconds:
                logger.error(
                    f"WASH TRADE SELF-CROSS DETECTED [{event.symbol}]: Trader '{event.trader_id}' "
                    f"placed {event.side.value} @ ${event.price:.2f} matching resting {active_order.side.value} order [{active_id}]"
                )
                return SurveillanceViolation(
                    violation_id=f"VIOL-WASH-{int(event.timestamp.timestamp())}",
                    trader_id=event.trader_id,
                    symbol=event.symbol,
                    violation_type=ViolationType.WASH_TRADE,
                    severity="CRITICAL",
                    description=f"Potential Wash Trade: {event.side.value} order matched resting {active_order.side.value} order at ${event.price:.2f}",
                    related_event_ids=[active_order.event_id, event.event_id],
                    timestamp=event.timestamp,
                )

        return None

    def check_spoofing_pattern_on_fill(self, fill_event: OrderEvent) -> Optional[SurveillanceViolation]:
        """
        Detects spoofing pattern: When a trade is filled on one side (e.g. Ask fill), check if the trader
        rapidly cancels large non-bona fide orders on the opposite side (e.g. Bid layering) within ms.
        """
        history = self.trader_history.get(fill_event.trader_id, [])
        opposite_side = OrderSide.SELL if fill_event.side == OrderSide.BUY else OrderSide.BUY

        # Find recent opposite side cancellations within spoofing window
        recent_opposite_cancels = [
            ev for ev in history
            if ev.action == OrderAction.CANCEL
            and ev.side == opposite_side
            and ev.symbol == fill_event.symbol
            and abs((fill_event.timestamp - ev.timestamp).total_seconds() * 1000.0) <= self.spoofing_lifespan_threshold_ms
        ]

        if recent_opposite_cancels:
            cancel_qty = sum(c.quantity for c in recent_opposite_cancels)
            logger.error(
                f"SPOOFING PATTERN DETECTED [{fill_event.symbol}]: Trader '{fill_event.trader_id}' "
                f"executed {fill_event.side.value} fill ({fill_event.quantity} qty) while canceling {cancel_qty} qty on opposite side."
            )
            return SurveillanceViolation(
                violation_id=f"VIOL-SPOOF-{int(fill_event.timestamp.timestamp())}",
                trader_id=fill_event.trader_id,
                symbol=fill_event.symbol,
                violation_type=ViolationType.SPOOFING_LAYERING,
                severity="CRITICAL",
                description=f"Spoofing Pattern: {fill_event.side.value} fill accompanied by rapid cancellation of {cancel_qty} opposite {opposite_side.value} orders.",
                related_event_ids=[fill_event.event_id] + [c.event_id for c in recent_opposite_cancels],
                timestamp=fill_event.timestamp,
            )

        return None

    def get_trader_metrics(self, trader_id: str) -> TraderMetrics:
        """Calculates order statistics, cancellation ratio, and average lifespan for a trader."""
        events = self.trader_history.get(trader_id, [])
        placed = [e for e in events if e.action == OrderAction.PLACE]
        canceled = [e for e in events if e.action == OrderAction.CANCEL]
        filled = [e for e in events if e.action == OrderAction.FILL]

        n_placed = len(placed)
        n_canceled = len(canceled)
        n_filled = len(filled)

        ratio = (n_canceled / n_placed * 100.0) if n_placed > 0 else 0.0

        # Calculate average order lifespan for canceled orders
        lifespans = []
        for c in canceled:
            if c.order_id in self.order_placement_times:
                p_time = self.order_placement_times[c.order_id]
                lifespans.append((c.timestamp - p_time).total_seconds() * 1000.0)

        avg_lifespan = (sum(lifespans) / len(lifespans)) if lifespans else 0.0

        return TraderMetrics(
            trader_id=trader_id,
            total_orders_placed=n_placed,
            total_orders_canceled=n_canceled,
            total_orders_filled=n_filled,
            cancellation_ratio_pct=round(ratio, 2),
            avg_order_lifespan_ms=round(avg_lifespan, 2),
        )


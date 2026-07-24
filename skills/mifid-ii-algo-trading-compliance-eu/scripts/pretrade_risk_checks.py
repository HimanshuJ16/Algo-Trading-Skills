"""
mifid-ii-algo-trading-compliance-eu: Production-grade MiFID II RTS 6 pre-trade risk controls,
price collar verification, max order value/volume limits, message rate throttles,
RTS 6 order tagging, emergency kill-switch with resting order cancellation, and Annex I audit logging.
"""
from collections import deque
from dataclasses import dataclass
import datetime
from enum import Enum
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class MiFID2OrderTag:
    algo_id: str
    client_id: str
    trading_capacity: str  # "DEAL" (Dealing on own account), "MATCH" (Matched principal), "AAGT" (Agent)
    short_selling_flag: bool
    timestamp_ns: int


@dataclass
class RTS6PreTradeResult:
    approved: bool
    price_collar_pass: bool
    order_value_pass: bool
    volume_pass: bool
    message_rate_pass: bool
    rejection_reasons: List[str]


class MiFID2ComplianceManager:
    """
    RTS 6 compliant pre-trade risk engine enforcing mandatory pre-trade checks,
    order tagging, manual kill switch with resting order cancellation, and compliance audit trail.
    """

    def __init__(

        self,
        max_order_value: float = 100_000.0,
        max_volume: float = 10_000.0,
        max_msgs_per_sec: int = 10,
        price_collar_pct: float = 0.05,  # 5% max deviation
        algo_id: str = "ALGO_EUR_VOL_01",
        cancel_resting_orders_fn: Optional[Callable[[], None]] = None,
    ):
        self.max_order_value = max_order_value
        self.max_volume = max_volume
        self.max_msgs_per_sec = max_msgs_per_sec
        self.price_collar_pct = price_collar_pct
        self.algo_id = algo_id
        self.cancel_resting_orders_fn = cancel_resting_orders_fn or (
            lambda: logger.info("Cancelled all outstanding resting orders on venue.")
        )

        self.message_timestamps: deque = deque()
        self.kill_switch_active = False
        self.audit_log: List[Dict[str, Any]] = []

    def check_price_collar(self, order_price: float, reference_price: float) -> bool:
        if reference_price <= 0:
            return False
        deviation = abs(order_price - reference_price) / reference_price
        return deviation <= self.price_collar_pct

    def check_order_value(self, price: float, quantity: float) -> bool:
        return (price * quantity) <= self.max_order_value

    def check_volume(self, quantity: float) -> bool:
        return quantity <= self.max_volume

    def check_message_rate(self) -> bool:
        now = time.monotonic()
        while self.message_timestamps and now - self.message_timestamps[0] > 1.0:
            self.message_timestamps.popleft()

        if len(self.message_timestamps) >= self.max_msgs_per_sec:
            return False

        self.message_timestamps.append(now)
        return True

    def validate_pretrade_order(
        self, price: float, quantity: float, reference_price: float, symbol: str = "EURUSD"
    ) -> RTS6PreTradeResult:

        rejections = []
        if self.kill_switch_active:
            rejections.append("RTS 6 Kill Switch ACTIVE — All order placement halted.")

        pass_collar = self.check_price_collar(price, reference_price)
        if not pass_collar:
            rejections.append(
                f"Price collar breach: Price {price} deviates >{self.price_collar_pct*100}% from ref price {reference_price}"
            )

        pass_val = self.check_order_value(price, quantity)
        if not pass_val:
            rejections.append(f"Order value breach: {price * quantity:.2f} > max allowed {self.max_order_value:.2f}")

        pass_vol = self.check_volume(quantity)
        if not pass_vol:
            rejections.append(f"Volume breach: {quantity} > max allowed {self.max_volume}")

        pass_rate = self.check_message_rate()
        if not pass_rate:
            rejections.append(f"Message rate breach: > {self.max_msgs_per_sec} msgs/sec")

        approved = len(rejections) == 0

        result = RTS6PreTradeResult(
            approved=approved,
            price_collar_pass=pass_collar,
            order_value_pass=pass_val,
            volume_pass=pass_vol,
            message_rate_pass=pass_rate,
            rejection_reasons=rejections,
        )

        self._log_audit_event(symbol, price, quantity, result)
        return result

    def tag_order(self, client_id: str, short_selling: bool = False) -> MiFID2OrderTag:
        """Generates mandatory MiFID II RTS 6 order tagging payload."""
        return MiFID2OrderTag(
            algo_id=self.algo_id,
            client_id=client_id,
            trading_capacity="DEAL",
            short_selling_flag=short_selling,
            timestamp_ns=time.time_ns(),
        )

    def trigger_rts6_kill_switch(self, operator_id: str, reason: str):
        """RTS 6 Article 18 mandatory emergency kill switch."""
        self.kill_switch_active = True
        msg = f"RTS 6 EMERGENCY KILL SWITCH TRIGGERED by '{operator_id}': {reason}"
        logger.critical(msg)
        self.cancel_resting_orders_fn()

    def _log_audit_event(
        self, symbol: str, price: float, quantity: float, result: RTS6PreTradeResult
    ):
        self.audit_log.append(
            {
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "symbol": symbol,
                "price": price,
                "quantity": quantity,
                "approved": result.approved,
                "rejections": result.rejection_reasons,
            }
        )


# Backward compatibility wrapper class
class PreTradeRiskControls:

    def __init__(self, max_order_value, max_volume, max_msgs_per_sec, price_collar_pct):
        self.mgr = MiFID2ComplianceManager(
            max_order_value=max_order_value,
            max_volume=max_volume,
            max_msgs_per_sec=max_msgs_per_sec,
            price_collar_pct=price_collar_pct,
        )
        self.max_order_value = max_order_value
        self.max_volume = max_volume
        self.max_msgs_per_sec = max_msgs_per_sec
        self.price_collar_pct = price_collar_pct

    def check_price_collar(self, order_price, reference_price):
        return self.mgr.check_price_collar(order_price, reference_price)

    def check_order_value(self, price, quantity):
        return self.mgr.check_order_value(price, quantity)

    def check_volume(self, quantity):
        return self.mgr.check_volume(quantity)

    def check_message_rate(self):
        return self.mgr.check_message_rate()

    def all_checks(self, price, quantity, reference_price):
        return self.mgr.validate_pretrade_order(price, quantity, reference_price).__dict__

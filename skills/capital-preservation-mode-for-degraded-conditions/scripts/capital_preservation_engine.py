import logging
import time
from dataclasses import dataclass
from typing import List
from enum import Enum

logger = logging.getLogger(__name__)

class EngineState(Enum):
    ACTIVE = 1
    DEGRADED_WARNING = 2
    HALTED = 3

@dataclass
class PreservationLimits:
    max_daily_drawdown_usd: float = 50000.0
    max_orders_per_minute: int = 100
    max_consecutive_errors: int = 5

class CapitalPreservationEngine:
    """
    Independent kill-switch middleware.
    Monitors PnL, runaway algorithms, and connectivity degradation.
    """
    def __init__(self, limits: PreservationLimits):
        self.limits = limits
        self.state = EngineState.ACTIVE
        self.current_drawdown = 0.0
        
        self.order_timestamps: List[float] = []
        self.consecutive_errors = 0
        self.halt_reason = ""

    def update_pnl(self, realized_pnl: float, unrealized_pnl: float):
        """Called periodically by the Mark-to-Market engine."""
        total_pnl = realized_pnl + unrealized_pnl
        if total_pnl < 0:
            self.current_drawdown = abs(total_pnl)
            if self.current_drawdown >= self.limits.max_daily_drawdown_usd:
                self._trigger_halt(f"Daily drawdown ${self.current_drawdown:,.2f} exceeded limit ${self.limits.max_daily_drawdown_usd:,.2f}")
        else:
            self.current_drawdown = 0.0

    def register_error(self):
        """Called when an API or FIX reject occurs."""
        self.consecutive_errors += 1
        if self.consecutive_errors >= self.limits.max_consecutive_errors:
            self._trigger_halt(f"{self.consecutive_errors} consecutive API errors. Potential venue degradation.")

    def register_success(self):
        """Resets the error counter on a successful operation."""
        self.consecutive_errors = 0

    def check_order_allowed(self) -> bool:
        """
        Pre-trade check. Evaluates state and order frequency.
        Must be called BEFORE routing to the exchange.
        """
        if self.state == EngineState.HALTED:
            logger.critical(f"ORDER BLOCKED: Engine is HALTED. Reason: {self.halt_reason}")
            return False

        now = time.time()
        
        # Clean up timestamps older than 60 seconds
        self.order_timestamps = [t for t in self.order_timestamps if now - t <= 60.0]
        
        if len(self.order_timestamps) >= self.limits.max_orders_per_minute:
            self._trigger_halt(f"Runaway Algo Protection: Exceeded {self.limits.max_orders_per_minute} orders per minute.")
            return False

        self.order_timestamps.append(now)
        return True

    def _trigger_halt(self, reason: str):
        if self.state != EngineState.HALTED:
            self.state = EngineState.HALTED
            self.halt_reason = reason
            logger.critical(f"*** CAPITAL PRESERVATION KILL-SWITCH ENGAGED *** Reason: {reason}")

    def manual_reset(self, auth_token: str):
        """Requires a secure token to restart the system after a halt."""
        if auth_token != "SECURE_ADMIN_TOKEN":
            logger.error("Invalid authorization for manual reset.")
            raise PermissionError("Invalid authorization")
            
        self.state = EngineState.ACTIVE
        self.current_drawdown = 0.0
        self.order_timestamps.clear()
        self.consecutive_errors = 0
        self.halt_reason = ""
        logger.info("Engine manually reset. Trading resumed.")

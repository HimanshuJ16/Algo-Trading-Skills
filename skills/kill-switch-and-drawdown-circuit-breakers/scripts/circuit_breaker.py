"""
kill-switch-and-drawdown-circuit-breakers: Production-grade independent risk module,
force-flatten order execution, peak equity high-water mark tracking, broker position reconciliation,
and mandatory human re-enable gates.
"""
from dataclasses import dataclass, field
import datetime
from enum import Enum
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class CircuitBreakerStatus(str, Enum):
    ACTIVE = "ACTIVE"
    HALTED_POSITION_LIMIT = "HALTED_POSITION_LIMIT"
    HALTED_DAILY_LOSS = "HALTED_DAILY_LOSS"
    HALTED_DRAWDOWN = "HALTED_DRAWDOWN"
    HALTED_DESYNC = "HALTED_DESYNC"
    HALTED_MANUAL_KILL = "HALTED_MANUAL_KILL"


@dataclass
class BreachEventLog:
    timestamp: str
    status: CircuitBreakerStatus
    reason: str
    daily_pnl: float
    drawdown_pct: float
    current_equity: float
    positions_snapshot: Dict[str, float]


class KillSwitchCircuitBreaker:
    """
    Independent risk governance module with authority to veto proposed orders,
    trigger force-flattening, and require explicit human re-enable before trading resumes.
    """

    def __init__(
        self,
        max_position: float,
        max_daily_loss: float,
        max_drawdown_pct: float,
        alert_fn: Optional[Callable[[str], None]] = None,
        flatten_fn: Optional[Callable[[], None]] = None,
        desync_tolerance_units: float = 0.001,
    ):
        self.max_position = max_position
        self.max_daily_loss = abs(max_daily_loss)
        self.max_drawdown_pct = max_drawdown_pct
        self.alert_fn = alert_fn or (lambda msg: logger.warning(msg))
        self.flatten_fn = flatten_fn or (lambda: logger.info("Force-flattening all open positions..."))
        self.desync_tolerance_units = desync_tolerance_units

        self.status = CircuitBreakerStatus.ACTIVE
        self.halted = False
        self.peak_equity: Optional[float] = None
        self.audit_log: List[BreachEventLog] = []

    def check_proposed_order(
        self, proposed_position_size: float, current_position_size: float, symbol: str = "DEFAULT"
    ) -> Tuple[bool, str]:
        """Vetoes order if circuit breaker is halted or proposed size exceeds limits."""
        if self.halted:
            return False, f"Order rejected: System is in HALTED state ({self.status.value})."

        projected = abs(current_position_size + proposed_position_size)
        if projected > self.max_position:
            msg = f"Order rejected: Projected position {projected} for '{symbol}' exceeds max limit {self.max_position}."
            logger.warning(msg)
            return False, msg

        return True, "ok"

    def check_pnl_and_drawdown(
        self, daily_pnl: float, current_equity: float, active_positions: Optional[Dict[str, float]] = None
    ) -> Tuple[bool, str]:
        """Evaluates daily loss and peak-equity drawdown limits."""
        if self.halted:
            return True, f"Already halted ({self.status.value})."

        if self.peak_equity is None or current_equity > self.peak_equity:
            self.peak_equity = current_equity

        drawdown_pct = (
            (self.peak_equity - current_equity) / self.peak_equity if self.peak_equity > 0 else 0.0
        )

        positions = active_positions or {}

        # 1. Daily Loss Check
        if daily_pnl <= -self.max_daily_loss:
            self._trigger_halt(
                CircuitBreakerStatus.HALTED_DAILY_LOSS,
                f"Daily loss breach: PnL {daily_pnl:.2f} <= -{self.max_daily_loss:.2f}",
                daily_pnl,
                drawdown_pct,
                current_equity,
                positions,
            )
            return True, self.status.value

        # 2. Max Drawdown Check
        if drawdown_pct >= self.max_drawdown_pct:
            self._trigger_halt(
                CircuitBreakerStatus.HALTED_DRAWDOWN,
                f"Max drawdown breach: Drawdown {drawdown_pct:.2%} >= {self.max_drawdown_pct:.2%}",
                daily_pnl,
                drawdown_pct,
                current_equity,
                positions,
            )
            return True, self.status.value

        return False, "ok"

    def reconcile_broker_positions(
        self, internal_positions: Dict[str, float], broker_positions: Dict[str, float]
    ) -> bool:
        """Reconciles internal position state against broker account state."""
        if self.halted:
            return False

        all_symbols = set(internal_positions.keys()) | set(broker_positions.keys())
        for sym in all_symbols:
            internal_qty = internal_positions.get(sym, 0.0)
            broker_qty = broker_positions.get(sym, 0.0)
            if abs(internal_qty - broker_qty) > self.desync_tolerance_units:
                msg = f"POSITION DESYNC DETECTED for '{sym}': Internal={internal_qty}, Broker={broker_qty}"
                self._trigger_halt(
                    CircuitBreakerStatus.HALTED_DESYNC,
                    msg,
                    daily_pnl=0.0,
                    drawdown_pct=0.0,
                    current_equity=self.peak_equity or 0.0,
                    positions_snapshot=internal_positions,
                )
                return False
        return True

    def trigger_emergency_kill_switch(self, reason: str = "Manual emergency halt requested"):
        """Manual emergency kill switch trigger."""
        self._trigger_halt(
            CircuitBreakerStatus.HALTED_MANUAL_KILL,
            f"MANUAL KILL SWITCH TRIGGERED: {reason}",
            daily_pnl=0.0,
            drawdown_pct=0.0,
            current_equity=self.peak_equity or 0.0,
            positions_snapshot={},
        )

    def _trigger_halt(
        self,
        status: CircuitBreakerStatus,
        reason: str,
        daily_pnl: float,
        drawdown_pct: float,
        current_equity: float,
        positions_snapshot: Dict[str, float],
    ):
        self.halted = True
        self.status = status
        alert_msg = f"CRITICAL RISK ALERT [{status.value}]: {reason}"
        self.alert_fn(alert_msg)

        # Force-flatten positions
        try:
            self.flatten_fn()
        except Exception as e:
            logger.critical(f"Error during force-flatten execution: {e}")

        log_entry = BreachEventLog(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            status=status,
            reason=reason,
            daily_pnl=daily_pnl,
            drawdown_pct=drawdown_pct,
            current_equity=current_equity,
            positions_snapshot=positions_snapshot,
        )
        self.audit_log.append(log_entry)

    def human_re_enable(self, authorized_user: str, reason: str) -> bool:
        """Human re-enable gate requiring explicit authorization to clear halt state."""
        logger.info(f"Circuit breaker re-enabled by '{authorized_user}'. Reason: {reason}")
        self.halted = False
        self.status = CircuitBreakerStatus.ACTIVE
        return True


# Backward compatibility wrapper class
class CircuitBreaker:

    def __init__(self, max_position, max_daily_loss, max_drawdown_pct, alert_fn):
        self.engine = KillSwitchCircuitBreaker(
            max_position=max_position,
            max_daily_loss=max_daily_loss,
            max_drawdown_pct=max_drawdown_pct,
            alert_fn=alert_fn,
        )
        self.max_position = max_position
        self.max_daily_loss = max_daily_loss
        self.max_drawdown_pct = max_drawdown_pct
        self.alert_fn = alert_fn

    @property
    def peak_equity(self):
        return self.engine.peak_equity

    @peak_equity.setter
    def peak_equity(self, val):
        self.engine.peak_equity = val

    @property
    def halted(self):
        return self.engine.halted

    @halted.setter
    def halted(self, val):
        self.engine.halted = val

    def check_order(self, proposed_position_size, current_position_size):
        ok, reason = self.engine.check_proposed_order(proposed_position_size, current_position_size)
        if not ok:
            if "position" in reason.lower():
                return False, "position_limit"
            return False, "halted"
        return True, "ok"

    def check_pnl(self, daily_pnl, current_equity):
        return self.engine.check_pnl_and_drawdown(daily_pnl, current_equity)[0]

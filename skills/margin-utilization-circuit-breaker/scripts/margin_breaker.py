"""
margin-utilization-circuit-breaker: Halts new order placement when margin utilization
exceeds defined thresholds, independent of P&L-based circuit breakers.
"""
from dataclasses import dataclass
import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class MarginStatus(Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    HARD_STOP = "HARD_STOP"


@dataclass
class MarginState:
    used_margin: float
    available_margin: float
    account_equity: float
    utilization_pct: float
    status: MarginStatus
    message: str


@dataclass
class MarginOrderCheck:
    approved: bool
    margin_state: MarginState
    rejection_reason: Optional[str] = None


class MarginUtilizationBreaker:
    """
    Monitors margin utilization ratio and enforces circuit breaker thresholds
    to prevent margin calls and forced liquidations.
    """

    def __init__(
        self,
        warning_threshold: float = 0.60,
        hard_stop_threshold: float = 0.80,
    ):
        if warning_threshold >= hard_stop_threshold:
            raise ValueError("Warning threshold must be below hard stop threshold.")
        self.warning_threshold = warning_threshold
        self.hard_stop_threshold = hard_stop_threshold

    def evaluate_margin(
        self,
        used_margin: float,
        account_equity: float,
    ) -> MarginState:
        """Compute margin utilization and determine current status."""
        if account_equity <= 0:
            return MarginState(
                used_margin=used_margin,
                available_margin=0.0,
                account_equity=account_equity,
                utilization_pct=1.0,
                status=MarginStatus.HARD_STOP,
                message="CRITICAL: Account equity <= 0. All trading halted.",
            )

        utilization = used_margin / account_equity
        available = max(account_equity - used_margin, 0.0)

        if utilization >= self.hard_stop_threshold:
            status = MarginStatus.HARD_STOP
            msg = (
                f"MARGIN HARD STOP: Utilization {utilization:.1%} >= "
                f"{self.hard_stop_threshold:.1%}. All new entries BLOCKED."
            )
            logger.critical(msg)
        elif utilization >= self.warning_threshold:
            status = MarginStatus.WARNING
            msg = (
                f"MARGIN WARNING: Utilization {utilization:.1%} >= "
                f"{self.warning_threshold:.1%}. Consider reducing positions."
            )
            logger.warning(msg)
        else:
            status = MarginStatus.NORMAL
            msg = f"Margin utilization {utilization:.1%} — within normal range."

        return MarginState(
            used_margin=used_margin,
            available_margin=available,
            account_equity=account_equity,
            utilization_pct=utilization,
            status=status,
            message=msg,
        )

    def check_order(
        self,
        used_margin: float,
        account_equity: float,
        additional_margin_required: float = 0.0,
    ) -> MarginOrderCheck:
        """Pre-trade check: verify margin utilization allows new order."""
        projected_used = used_margin + additional_margin_required
        state = self.evaluate_margin(projected_used, account_equity)

        if state.status == MarginStatus.HARD_STOP:
            return MarginOrderCheck(
                approved=False,
                margin_state=state,
                rejection_reason=state.message,
            )

        return MarginOrderCheck(approved=True, margin_state=state)

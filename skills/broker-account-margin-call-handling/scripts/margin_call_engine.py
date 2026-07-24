"""
broker-account-margin-call-handling: Production-grade margin utilization calculator,
multi-tiered margin call state machine, new order veto guard, and automated de-leveraging engine.
"""
from dataclasses import dataclass
from enum import Enum
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MarginCallError(RuntimeError):
    """Raised when a margin breach occurs or new orders are vetoed under margin stress."""
    pass


class MarginState(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"          # Margin Ratio >= 85%
    CRITICAL = "CRITICAL"        # Margin Ratio >= 95% (Cancel Open Orders)
    MARGIN_CALL_BREACH = "BREACH"# Margin Ratio >= 100% (De-leverage Positions)


@dataclass
class AccountMarginSnapshot:
    net_liquidation_value: float
    maintenance_margin: float
    excess_liquidity: float
    currency: str = "USD"


@dataclass
class MarginCallEvaluation:
    state: MarginState
    margin_ratio: float
    deficit_amount: float
    action_required: str
    message: str


@dataclass
class PositionMarginInfo:
    symbol: str
    quantity: float
    current_price: float
    margin_requirement: float


class BrokerMarginCallEngine:
    """
    Evaluates account margin metrics, enforces multi-tiered risk gates, vetoes
    new leverage-increasing orders, and plans systematic de-leveraging liquidations.
    """

    def __init__(
        self,
        warning_threshold: float = 0.85,
        critical_threshold: float = 0.95,
        breach_threshold: float = 1.00,
        target_post_deleverage_ratio: float = 0.80,
    ):
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.breach_threshold = breach_threshold
        self.target_ratio = target_post_deleverage_ratio

    def evaluate_margin_health(self, snapshot: AccountMarginSnapshot) -> MarginCallEvaluation:
        """Evaluates margin utilization ratio and returns action required."""
        nlv = max(snapshot.net_liquidation_value, 0.001)
        maint = snapshot.maintenance_margin
        ratio = maint / nlv
        deficit = max(0.0, maint - nlv)

        if ratio >= self.breach_threshold:
            state = MarginState.MARGIN_CALL_BREACH
            action = "DE_LEVERAGE_IMMEDIATELY"
            msg = f"MARGIN CALL BREACH! Maintenance margin (${maint:,.2f}) >= NLV (${nlv:,.2f}). Deficit: ${deficit:,.2f}"
            logger.critical(msg)

        elif ratio >= self.critical_threshold:
            state = MarginState.CRITICAL
            action = "CANCEL_ALL_PENDING_ORDERS"
            msg = f"CRITICAL MARGIN STRESS! Margin ratio {ratio:.1%} >= {self.critical_threshold:.1%}. Cancelling open orders."
            logger.warning(msg)

        elif ratio >= self.warning_threshold:
            state = MarginState.WARNING
            action = "BLOCK_NEW_POSITIONS"
            msg = f"MARGIN WARNING! Margin ratio {ratio:.1%} >= {self.warning_threshold:.1%}. Blocking new position entries."
            logger.warning(msg)

        else:
            state = MarginState.NORMAL
            action = "NONE"
            msg = f"Margin healthy: ratio {ratio:.1%} < {self.warning_threshold:.1%}."

        return MarginCallEvaluation(
            state=state,
            margin_ratio=ratio,
            deficit_amount=deficit,
            action_required=action,
            message=msg,
        )

    def guard_new_order(self, snapshot: AccountMarginSnapshot, is_deleveraging: bool = False) -> bool:
        """Vetoes new orders if account is under margin stress unless order is de-leveraging."""
        if is_deleveraging:
            return True

        eval_res = self.evaluate_margin_health(snapshot)
        if eval_res.state != MarginState.NORMAL:
            raise MarginCallError(
                f"Order vetoed under margin stress! Account state is '{eval_res.state.value}'. {eval_res.message}"
            )
        return True

    def plan_deleveraging(
        self, snapshot: AccountMarginSnapshot, positions: List[PositionMarginInfo]
    ) -> List[Tuple[PositionMarginInfo, float]]:
        """
        Calculates position reductions required to restore margin ratio to target_post_deleverage_ratio.
        Returns list of (PositionMarginInfo, qty_to_liquidate).
        """
        eval_res = self.evaluate_margin_health(snapshot)
        if eval_res.state != MarginState.MARGIN_CALL_BREACH:
            return []

        nlv = snapshot.net_liquidation_value
        target_margin = nlv * self.target_ratio
        required_reduction = snapshot.maintenance_margin - target_margin

        if required_reduction <= 0:
            return []

        # Sort positions by highest margin requirement first
        sorted_pos = sorted(positions, key=lambda p: p.margin_requirement, reverse=True)
        liquidation_plan: List[Tuple[PositionMarginInfo, float]] = []
        accumulated_reduction = 0.0

        for pos in sorted_pos:
            if accumulated_reduction >= required_reduction:
                break

            margin_per_unit = pos.margin_requirement / max(abs(pos.quantity), 1e-5)
            needed_reduction = required_reduction - accumulated_reduction

            units_to_reduce = min(abs(pos.quantity), needed_reduction / max(margin_per_unit, 1e-5))
            reduction_achieved = units_to_reduce * margin_per_unit

            liquidation_plan.append((pos, units_to_reduce))
            accumulated_reduction += reduction_achieved

        logger.info(
            f"De-leveraging plan generated: {len(liquidation_plan)} positions to reduce, targeted margin reduction: ${accumulated_reduction:,.2f}"
        )
        return liquidation_plan

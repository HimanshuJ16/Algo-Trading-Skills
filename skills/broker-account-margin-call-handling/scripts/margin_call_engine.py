import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Tuple, Optional
import math

logger = logging.getLogger(__name__)

class MarginCallError(RuntimeError):
    """Raised when a margin breach occurs or new orders are vetoed under margin stress."""
    pass


class MarginState(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"          # Margin Ratio >= warning_threshold
    CRITICAL = "CRITICAL"        # Margin Ratio >= critical_threshold (Cancel Open Orders)
    MARGIN_CALL_BREACH = "BREACH"# Margin Ratio >= breach_threshold (De-leverage Positions)


@dataclass
class AccountMarginSnapshot:
    """Represents a real-time snapshot of account margin metrics."""
    net_liquidation_value: float
    initial_margin: float
    maintenance_margin: float
    excess_liquidity: float
    available_funds: float
    buying_power: float
    currency: str = "USD"


@dataclass
class MarginCallEvaluation:
    """Outcome of a margin health check."""
    state: MarginState
    maintenance_margin_ratio: float
    initial_margin_ratio: float
    maintenance_deficit: float
    action_required: str
    message: str


@dataclass
class PositionMarginInfo:
    """Detailed position metrics for de-leveraging algorithms."""
    symbol: str
    asset_class: str  # e.g., 'STK', 'OPT', 'FUT'
    quantity: float
    current_price: float
    maintenance_margin_requirement: float
    average_daily_volume: float = 1_000_000.0
    is_short_option: bool = False
    beta_to_benchmark: float = 1.0


class BrokerMarginCallEngine:
    """
    Production-grade margin utilization calculator and automated de-leveraging engine.
    Supports multi-tiered risk gates and sophisticated liquidity-aware position unwinding.
    """

    def __init__(
        self,
        warning_threshold: float = 0.85,
        critical_threshold: float = 0.95,
        breach_threshold: float = 1.00,
        target_post_deleverage_ratio: float = 0.75,
        max_participation_rate: float = 0.10,
        liquidation_buffer_multiplier: float = 1.05
    ):
        """
        :param target_post_deleverage_ratio: Target maintenance margin ratio after forced liquidations.
        :param max_participation_rate: Max % of ADV to trade during a liquidation slice.
        :param liquidation_buffer_multiplier: Multiplier on required reduction to account for slippage.
        """
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.breach_threshold = breach_threshold
        self.target_ratio = target_post_deleverage_ratio
        self.max_participation_rate = max_participation_rate
        self.liquidation_buffer_multiplier = liquidation_buffer_multiplier

    def evaluate_margin_health(self, snapshot: AccountMarginSnapshot) -> MarginCallEvaluation:
        """Evaluates margin utilization ratios (Maintenance and Initial) and determines risk state."""
        nlv = max(snapshot.net_liquidation_value, 0.01)
        maint_ratio = snapshot.maintenance_margin / nlv
        init_ratio = snapshot.initial_margin / nlv
        deficit = max(0.0, snapshot.maintenance_margin - nlv)

        if maint_ratio >= self.breach_threshold:
            state = MarginState.MARGIN_CALL_BREACH
            action = "DE_LEVERAGE_IMMEDIATELY"
            msg = f"BREACH! Maintenance Margin (${snapshot.maintenance_margin:,.2f}) >= NLV (${nlv:,.2f}). Deficit: ${deficit:,.2f}"
            logger.critical(msg)
        elif maint_ratio >= self.critical_threshold:
            state = MarginState.CRITICAL
            action = "CANCEL_ALL_PENDING_ORDERS"
            msg = f"CRITICAL! Margin ratio {maint_ratio:.1%} >= {self.critical_threshold:.1%}. Cancelling open orders."
            logger.warning(msg)
        elif maint_ratio >= self.warning_threshold:
            state = MarginState.WARNING
            action = "BLOCK_NEW_POSITIONS"
            msg = f"WARNING! Margin ratio {maint_ratio:.1%} >= {self.warning_threshold:.1%}."
            logger.warning(msg)
        else:
            state = MarginState.NORMAL
            action = "NONE"
            msg = f"Healthy: ratio {maint_ratio:.1%} < {self.warning_threshold:.1%}."

        return MarginCallEvaluation(
            state=state,
            maintenance_margin_ratio=maint_ratio,
            initial_margin_ratio=init_ratio,
            maintenance_deficit=deficit,
            action_required=action,
            message=msg,
        )

    def guard_new_order(self, snapshot: AccountMarginSnapshot, margin_impact: float, is_deleveraging: bool = False) -> bool:
        """
        Vetoes new orders under margin stress, accounting for the order's estimated margin impact.
        :param margin_impact: Expected increase in maintenance margin from this order.
        """
        if is_deleveraging:
            return True

        eval_res = self.evaluate_margin_health(snapshot)
        
        # Predictive check
        projected_maint_margin = snapshot.maintenance_margin + margin_impact
        projected_ratio = projected_maint_margin / max(snapshot.net_liquidation_value, 0.01)
        
        if projected_ratio >= self.warning_threshold:
            raise MarginCallError(
                f"Order vetoed! Projected margin ratio {projected_ratio:.1%} exceeds warning threshold {self.warning_threshold:.1%}."
            )

        if eval_res.state != MarginState.NORMAL:
            raise MarginCallError(
                f"Order vetoed under margin stress! Account state is '{eval_res.state.value}'. {eval_res.message}"
            )
            
        return True

    def plan_deleveraging(
        self, snapshot: AccountMarginSnapshot, positions: List[PositionMarginInfo]
    ) -> List[Tuple[PositionMarginInfo, float]]:
        """
        Calculates a highly optimized, liquidity-aware position reduction plan to restore margin health.
        Prioritizes:
        1. Short options (unlimited tail risk)
        2. High beta / high margin requirement relative to notional
        3. Highly liquid assets (to minimize slippage)
        """
        eval_res = self.evaluate_margin_health(snapshot)
        if eval_res.state != MarginState.MARGIN_CALL_BREACH:
            return []

        nlv = snapshot.net_liquidation_value
        target_margin = nlv * self.target_ratio
        base_required_reduction = snapshot.maintenance_margin - target_margin
        
        if base_required_reduction <= 0:
            return []
            
        # Add buffer to account for adverse slippage during liquidation
        required_reduction = base_required_reduction * self.liquidation_buffer_multiplier

        # Score positions for liquidation priority
        def liquidation_score(p: PositionMarginInfo) -> float:
            # Higher score = liquidate first
            score = 0.0
            if p.is_short_option:
                score += 1000.0  # Priority 1: Clear tail risk
            
            # Priority 2: Margin density (margin requirement per $ of notional)
            notional = abs(p.quantity * p.current_price)
            margin_density = p.maintenance_margin_requirement / max(notional, 1.0)
            score += margin_density * 100.0
            
            # Priority 3: Liquidity (highly liquid is easier to dump)
            score += min(p.average_daily_volume / 1e6, 50.0) 
            return score

        sorted_pos = sorted(positions, key=liquidation_score, reverse=True)
        liquidation_plan: List[Tuple[PositionMarginInfo, float]] = []
        accumulated_reduction = 0.0

        for pos in sorted_pos:
            if accumulated_reduction >= required_reduction:
                break

            margin_per_unit = pos.maintenance_margin_requirement / max(abs(pos.quantity), 1e-5)
            needed_reduction = required_reduction - accumulated_reduction

            # Calculate max liquidatable units based on required reduction
            units_to_reduce_for_margin = needed_reduction / max(margin_per_unit, 1e-5)
            
            # Liquidity cap: do not exceed max_participation_rate of ADV to avoid crashing the price
            # (In a true breach, we might override this, but in automated tiered deleveraging we slice)
            max_liquidity_units = pos.average_daily_volume * self.max_participation_rate
            
            units_to_reduce = min(
                abs(pos.quantity), 
                units_to_reduce_for_margin,
                max_liquidity_units
            )
            
            if units_to_reduce < 1e-4:
                continue

            reduction_achieved = units_to_reduce * margin_per_unit
            liquidation_plan.append((pos, units_to_reduce))
            accumulated_reduction += reduction_achieved

        logger.info(
            f"De-leveraging plan generated: {len(liquidation_plan)} positions to reduce, targeted margin reduction: ${accumulated_reduction:,.2f}"
        )
        return liquidation_plan

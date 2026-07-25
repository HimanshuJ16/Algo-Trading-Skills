import logging
from dataclasses import dataclass
from typing import Dict

logger = logging.getLogger(__name__)

@dataclass
class StrategyMetrics:
    strategy_id: str
    current_capital: float
    max_capacity: float
    # Recent performance metrics
    win_rate: float
    avg_win: float
    avg_loss: float
    
@dataclass
class ReallocationInstruction:
    strategy_id: str
    delta_capital: float
    new_target_capital: float

class CapitalReallocationEngine:
    """
    Dynamically reallocates capital across strategies using a 
    Fractional Kelly criterion, bounded by capacity limits.
    """
    def __init__(self, total_fund_capital: float, kelly_fraction: float = 0.25):
        """
        kelly_fraction: 0.5 for Half-Kelly, 0.25 for Quarter-Kelly.
        """
        self.total_fund_capital = total_fund_capital
        self.kelly_fraction = kelly_fraction

    def _calculate_kelly_weight(self, metrics: StrategyMetrics) -> float:
        """
        Calculates the theoretical Kelly optimal fraction.
        Kelly = W - [(1 - W) / R]
        Where W = Win Rate, R = Reward/Risk Ratio (Avg Win / Avg Loss)
        """
        if metrics.avg_loss <= 0:
            return 0.0 # Prevent division by zero or infinite edge assumption
            
        r = metrics.avg_win / metrics.avg_loss
        if r <= 0:
            return 0.0
            
        w = metrics.win_rate
        kelly = w - ((1 - w) / r)
        
        # Floor at 0 (don't short the strategy)
        return max(0.0, kelly)

    def reallocate(self, strategies: Dict[str, StrategyMetrics]) -> Dict[str, ReallocationInstruction]:
        raw_weights = {}
        total_raw_weight = 0.0
        
        # 1. Calculate raw Fractional Kelly weights
        for sid, metrics in strategies.items():
            k = self._calculate_kelly_weight(metrics)
            fractional_k = k * self.kelly_fraction
            raw_weights[sid] = fractional_k
            total_raw_weight += fractional_k
            
        instructions = {}
        
        # If all strategies have 0 edge, move to cash
        if total_raw_weight <= 0.0001:
            logger.warning("No edge detected across portfolio. Reverting to cash.")
            for sid, metrics in strategies.items():
                instructions[sid] = ReallocationInstruction(sid, -metrics.current_capital, 0.0)
            return instructions

        # 2. Normalize weights and apply capacity constraints
        remaining_capital = self.total_fund_capital
        final_targets = {}
        
        # First pass: hit capacity limits
        for sid in list(strategies.keys()):
            normalized_weight = raw_weights[sid] / total_raw_weight
            theoretical_target = self.total_fund_capital * normalized_weight
            
            capacity = strategies[sid].max_capacity
            if theoretical_target > capacity:
                final_targets[sid] = capacity
                remaining_capital -= capacity
                # Remove from pool for second pass
                total_raw_weight -= raw_weights[sid]
                del raw_weights[sid]
                
        # Second pass: distribute remaining capital
        if total_raw_weight > 0:
            for sid in raw_weights.keys():
                normalized_weight = raw_weights[sid] / total_raw_weight
                theoretical_target = remaining_capital * normalized_weight
                final_targets[sid] = min(theoretical_target, strategies[sid].max_capacity)

        # 3. Generate Delta Instructions
        for sid, metrics in strategies.items():
            target = final_targets.get(sid, 0.0)
            delta = target - metrics.current_capital
            instructions[sid] = ReallocationInstruction(sid, delta, target)
            logger.info(f"Strategy {sid}: Current=${metrics.current_capital:,.2f} -> Target=${target:,.2f} (Delta: ${delta:,.2f})")
            
        return instructions

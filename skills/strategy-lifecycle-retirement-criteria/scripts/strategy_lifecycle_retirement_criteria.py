import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class StrategyLifecycleRetirementCriteriaConfig:
    """Legacy config container for backward compatibility."""
    enabled: bool = True

class StrategyLifecycleRetirementCriteria:
    """Legacy class retained for 100% backward compatibility."""
    def __init__(self, config: StrategyLifecycleRetirementCriteriaConfig):
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled

class RetirementDecision(str, Enum):
    ACTIVE_HEALTHY = "ACTIVE_HEALTHY"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REDUCE_ALLOCATION = "REDUCE_ALLOCATION"
    MANDATORY_RETIREMENT = "MANDATORY_RETIREMENT"

@dataclass
class StrategyPerformanceMetrics:
    strategy_id: str
    backtest_sharpe: float
    backtest_max_drawdown_pct: float
    live_sharpe: float
    live_max_drawdown_pct: float
    live_information_ratio: float
    live_ic_t_stat: float                  # Information Coefficient t-statistic
    live_realized_annual_return_pct: float
    backtest_annual_return_pct: float

@dataclass
class StrategyRetirementReport:
    strategy_id: str
    decision: RetirementDecision
    is_retired: bool
    breached_criteria: List[str]
    performance_drift_pct: float          # (Live return - Backtest return) / Backtest return * 100
    recommended_action: str
    audit_notes: str

class StrategyLifecycleRetirementEngine:
    """
    Production-grade Strategy Lifecycle Retirement Engine evaluating quantitative alpha decay,
    Information Ratio thresholds, Information Coefficient t-stats, and backtest vs live performance divergence.
    """
    def __init__(
        self,
        min_live_information_ratio: float = 0.50,
        max_drawdown_multiplier: float = 1.50, # Max live DD <= 1.5x max backtest DD
        min_ic_t_stat: float = 1.96,            # 95% statistical significance
        max_allowed_performance_drift_pct: float = -40.0 # Max -40% return drift
    ):
        self.min_live_ir = min_live_information_ratio
        self.max_dd_mult = max_drawdown_multiplier
        self.min_ic_t_stat = min_ic_t_stat
        self.max_allowed_drift = max_allowed_performance_drift_pct

    def evaluate_strategy(self, metrics: StrategyPerformanceMetrics) -> StrategyRetirementReport:
        """
        Evaluates strategy metrics against 4 mandatory retirement criteria:
        1. Live IR Breach (< min_live_information_ratio).
        2. Drawdown Multiplier Breach (Live DD > 1.5 * Backtest DD).
        3. IC t-stat Decay (< 1.96).
        4. Performance Drift Breach (Live vs Backtest return < -40%).
        """
        breaches: List[str] = []

        # 1. Information Ratio Check
        if metrics.live_information_ratio < self.min_live_ir:
            breaches.append(f"ALPHA_DECAY_IR: Live Information Ratio ({metrics.live_information_ratio:.2f}) < Min Threshold ({self.min_live_ir:.2f}).")

        # 2. Drawdown Multiplier Check
        allowed_max_dd = metrics.backtest_max_drawdown_pct * self.max_dd_mult
        if metrics.live_max_drawdown_pct > allowed_max_dd:
            breaches.append(f"DRAWDOWN_BREACH: Live Max Drawdown ({metrics.live_max_drawdown_pct:.1f}%) > Allowed Limit ({allowed_max_dd:.1f}% = {self.max_dd_mult}x backtest DD).")

        # 3. Information Coefficient t-stat Check
        if metrics.live_ic_t_stat < self.min_ic_t_stat:
            breaches.append(f"IC_STATISTICAL_DECAY: Live IC t-stat ({metrics.live_ic_t_stat:.2f}) < Required 95% Confidence Threshold ({self.min_ic_t_stat}).")

        # 4. Performance Drift Check
        if metrics.backtest_annual_return_pct > 0:
            drift = ((metrics.live_realized_annual_return_pct - metrics.backtest_annual_return_pct) / metrics.backtest_annual_return_pct) * 100.0
        else:
            drift = 0.0

        if drift < self.max_allowed_drift:
            breaches.append(f"PERFORMANCE_DRIFT: Live vs Backtest return drift ({drift:.1f}%) < Max Allowed Limit ({self.max_allowed_drift}%).")

        # Decision Logic
        if len(breaches) >= 3 or (metrics.live_max_drawdown_pct > allowed_max_dd and metrics.live_information_ratio < 0.0):
            decision = RetirementDecision.MANDATORY_RETIREMENT
            is_retired = True
            action = "DECOMMISSION_IMMEDIATELY: Initiate hard order entry block and position unwind."
        elif len(breaches) == 2:
            decision = RetirementDecision.REDUCE_ALLOCATION
            is_retired = False
            action = "CUT_CAPITAL_50%: Reduce strategy allocation by 50% pending committee review."
        elif len(breaches) == 1:
            decision = RetirementDecision.NEEDS_REVIEW
            is_retired = False
            action = "FLAG_FOR_REVIEW: Place strategy on high-frequency watch list."
        else:
            decision = RetirementDecision.ACTIVE_HEALTHY
            is_retired = False
            action = "MAINTAIN_ALLOCATION: Strategy operating within healthy quantitative guardrails."

        notes = (
            f"RETIREMENT REPORT [{decision.value}] ({metrics.strategy_id}): Breaches = {len(breaches)}, "
            f"Live IR = {metrics.live_information_ratio:.2f}, Live DD = {metrics.live_max_drawdown_pct:.1f}%, Action = {action}"
        )

        if is_retired:
            logger.error(notes)
        else:
            logger.info(notes)

        return StrategyRetirementReport(
            strategy_id=metrics.strategy_id,
            decision=decision,
            is_retired=is_retired,
            breached_criteria=breaches,
            performance_drift_pct=round(drift, 2),
            recommended_action=action,
            audit_notes=notes
        )

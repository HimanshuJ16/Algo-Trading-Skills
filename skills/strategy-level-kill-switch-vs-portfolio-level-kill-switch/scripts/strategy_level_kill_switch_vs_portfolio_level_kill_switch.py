import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    name: str = "strategy-level-kill-switch-vs-portfolio-level-kill-switch"

class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return True

class KillSwitchScope(str, Enum):
    STRATEGY_LEVEL = "STRATEGY_LEVEL"
    PORTFOLIO_LEVEL = "PORTFOLIO_LEVEL"

class KillSwitchAction(str, Enum):
    SOFT_HALT = "SOFT_HALT"                 # Block new entries; allow passive exits
    HARD_LIQUIDATE = "HARD_LIQUIDATE"       # Cancel all orders; market liquidate positions

@dataclass
class StrategyState:
    strategy_id: str
    peak_equity_usd: float
    current_equity_usd: float
    drawdown_limit_pct: float = 10.0       # 10% max strategy drawdown
    is_tripped: bool = False
    action_taken: Optional[KillSwitchAction] = None
    tripped_time_epoch: float = 0.0

@dataclass
class PortfolioState:
    total_peak_equity_usd: float
    total_current_equity_usd: float
    portfolio_drawdown_limit_pct: float = 15.0  # 15% max portfolio drawdown
    max_tripped_strategies_limit: int = 3       # Trip portfolio if 3 strategies fail
    is_portfolio_tripped: bool = False
    tripped_time_epoch: float = 0.0

@dataclass
class KillSwitchExecutionReport:
    scope: KillSwitchScope
    strategy_id: Optional[str]              # None if portfolio-level
    is_triggered: bool
    drawdown_pct: float
    limit_pct: float
    action: KillSwitchAction
    affected_strategies: List[str]
    audit_notes: str

class HierarchicalKillSwitchEngine:
    """
    Production-grade Hierarchical Kill Switch Engine governing Strategy-Level vs Portfolio-Level circuit breakers,
    drawdown monitoring, order cancellation, and emergency liquidation actions.
    """
    def __init__(
        self,
        portfolio_state: PortfolioState,
        strategies: List[StrategyState],
        cooldown_seconds: float = 86400.0   # 24-hour mandatory cooldown
    ):
        self.portfolio_state = portfolio_state
        self.strategies: Dict[str, StrategyState] = {s.strategy_id: s for s in strategies}
        self.cooldown_seconds = cooldown_seconds

    def evaluate_strategy_kill_switch(
        self,
        strategy_id: str,
        current_equity_usd: float,
        action: KillSwitchAction = KillSwitchAction.HARD_LIQUIDATE
    ) -> KillSwitchExecutionReport:
        """Evaluates strategy-level drawdown against limit."""
        strat = self.strategies.get(strategy_id)
        if not strat:
            raise ValueError(f"Strategy '{strategy_id}' not found.")

        if current_equity_usd > strat.peak_equity_usd:
            strat.peak_equity_usd = current_equity_usd
        strat.current_equity_usd = current_equity_usd

        drawdown = (strat.peak_equity_usd - current_equity_usd) / strat.peak_equity_usd * 100.0 if strat.peak_equity_usd > 0 else 0.0

        is_triggered = drawdown >= strat.drawdown_limit_pct
        if is_triggered and not strat.is_tripped:
            strat.is_tripped = True
            strat.action_taken = action
            strat.tripped_time_epoch = time.time()
            notes = f"STRATEGY KILL SWITCH TRIPPED [{strategy_id}]: Drawdown {drawdown:.2f}% >= Limit {strat.drawdown_limit_pct}%. Action = {action.value}."
            logger.error(notes)
        else:
            notes = f"STRATEGY STATUS [{strategy_id}]: Drawdown = {drawdown:.2f}% (Limit {strat.drawdown_limit_pct}%)."

        return KillSwitchExecutionReport(
            scope=KillSwitchScope.STRATEGY_LEVEL,
            strategy_id=strategy_id,
            is_triggered=is_triggered,
            drawdown_pct=round(drawdown, 2),
            limit_pct=strat.drawdown_limit_pct,
            action=action if is_triggered else KillSwitchAction.SOFT_HALT,
            affected_strategies=[strategy_id] if is_triggered else [],
            audit_notes=notes
        )

    def evaluate_portfolio_kill_switch(
        self,
        total_current_equity_usd: float,
        action: KillSwitchAction = KillSwitchAction.HARD_LIQUIDATE
    ) -> KillSwitchExecutionReport:
        """Evaluates master portfolio-level drawdown and cascaded strategy failures."""
        p = self.portfolio_state
        if total_current_equity_usd > p.total_peak_equity_usd:
            p.total_peak_equity_usd = total_current_equity_usd
        p.total_current_equity_usd = total_current_equity_usd

        portfolio_dd = (p.total_peak_equity_usd - total_current_equity_usd) / p.total_peak_equity_usd * 100.0 if p.total_peak_equity_usd > 0 else 0.0
        tripped_strats = [s.strategy_id for s in self.strategies.values() if s.is_tripped]

        is_dd_breach = portfolio_dd >= p.portfolio_drawdown_limit_pct
        is_cascade_breach = len(tripped_strats) >= p.max_tripped_strategies_limit
        is_triggered = is_dd_breach or is_cascade_breach

        if is_triggered and not p.is_portfolio_tripped:
            p.is_portfolio_tripped = True
            p.tripped_time_epoch = time.time()
            # Trip all active strategies at portfolio level
            for s in self.strategies.values():
                s.is_tripped = True
                s.action_taken = action

            reason = "Drawdown Limit Breach" if is_dd_breach else f"Cascade Failure ({len(tripped_strats)} strategies tripped)"
            notes = f"MASTER PORTFOLIO KILL SWITCH TRIPPED: Reason = {reason}, Portfolio Drawdown = {portfolio_dd:.2f}%. Action = {action.value}."
            logger.critical(notes)
        else:
            notes = f"PORTFOLIO STATUS: Drawdown = {portfolio_dd:.2f}% (Limit {p.portfolio_drawdown_limit_pct}%), Tripped Strategies = {len(tripped_strats)}."

        affected = list(self.strategies.keys()) if is_triggered else tripped_strats

        return KillSwitchExecutionReport(
            scope=KillSwitchScope.PORTFOLIO_LEVEL,
            strategy_id=None,
            is_triggered=is_triggered,
            drawdown_pct=round(portfolio_dd, 2),
            limit_pct=p.portfolio_drawdown_limit_pct,
            action=action if is_triggered else KillSwitchAction.SOFT_HALT,
            affected_strategies=affected,
            audit_notes=notes
        )

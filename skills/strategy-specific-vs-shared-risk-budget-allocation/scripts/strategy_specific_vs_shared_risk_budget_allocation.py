import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
import numpy as np

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    enabled: bool = True

class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return self.config.enabled

@dataclass
class StrategyRiskBudgetSpec:
    strategy_id: str
    target_capital_usd: float
    max_standalone_volatility_pct: float     # Strategy-Specific Risk Limit (e.g. 15%)
    max_shared_risk_contribution_pct: float # Shared Risk Budget (e.g. 30% of total portfolio VaR)

@dataclass
class StrategyRiskBreakdown:
    strategy_id: str
    capital_weight: float
    standalone_volatility_pct: float
    marginal_contribution_to_risk: float   # MCR_i
    component_risk_pct: float              # Contribution to total volatility (%)
    standalone_limit_breached: bool
    shared_budget_breached: bool
    recommended_capital_adjustment_factor: float # e.g. 0.85 to scale down capital

@dataclass
class PortfolioRiskBudgetAllocationReport:
    total_portfolio_capital_usd: float
    total_portfolio_volatility_pct: float
    total_portfolio_var_95_usd: float
    is_euler_decomposition_valid: bool    # Sum of component risks == total risk
    strategy_breakdown: Dict[str, StrategyRiskBreakdown]
    breached_strategies: List[str]
    audit_notes: str

class StrategySpecificVsSharedRiskBudgetEngine:
    """
    Production-grade Strategy-Specific vs Shared Risk Budget Allocation Engine performing
    Euler risk decomposition, Component VaR calculations, and dual-tier risk limit audits.
    """
    def __init__(
        self,
        strategy_specs: List[StrategyRiskBudgetSpec],
        confidence_level_z: float = 1.645 # 95% 1-tailed VaR Z-score
    ):
        self.specs: Dict[str, StrategyRiskBudgetSpec] = {s.strategy_id: s for s in strategy_specs}
        self.z_score = confidence_level_z

    def evaluate_risk_budgets(
        self,
        strategy_returns_cov_matrix: np.ndarray, # N x N covariance matrix
        strategy_ids_order: List[str]
    ) -> PortfolioRiskBudgetAllocationReport:
        """
        Calculates Euler Risk Allocation, Component VaR, and verifies against:
        1. Strategy-Specific Risk Limits (Standalone Volatility).
        2. Shared Portfolio Risk Budgets (Component Risk Contribution %).
        """
        N = len(strategy_ids_order)
        if strategy_returns_cov_matrix.shape != (N, N):
            raise ValueError(f"Covariance matrix shape {strategy_returns_cov_matrix.shape} does not match {N} strategies.")

        capitals = np.array([self.specs[sid].target_capital_usd for sid in strategy_ids_order])
        total_capital = float(np.sum(capitals))
        if total_capital <= 0:
            raise ValueError("Total portfolio capital must be > 0.")

        weights = capitals / total_capital

        # Portfolio Volatility
        port_var = float(weights.T @ strategy_returns_cov_matrix @ weights)
        port_vol = math.sqrt(max(1e-8, port_var))
        ann_port_vol_pct = port_vol * math.sqrt(252) * 100.0
        port_var_95_usd = total_capital * (port_vol * math.sqrt(252) * self.z_score)

        # Standalone Volatility per strategy
        standalone_vols = np.sqrt(np.diag(strategy_returns_cov_matrix)) * math.sqrt(252) * 100.0

        # Euler Marginal Contribution to Risk (MCR) = (Cov * w) / port_vol
        mcr = (strategy_returns_cov_matrix @ weights) / port_vol

        # Component Risk (Risk Contribution %) = (w_i * MCR_i) / port_vol
        component_risk_frac = (weights * mcr) / port_vol
        component_risk_pct = component_risk_frac * 100.0

        # Euler Identity Verification: sum(component_risk_frac) == 1.0
        euler_valid = abs(float(np.sum(component_risk_frac)) - 1.0) < 1e-4

        breakdown_dict: Dict[str, StrategyRiskBreakdown] = {}
        breached: List[str] = []

        for idx, sid in enumerate(strategy_ids_order):
            spec = self.specs[sid]
            stand_vol = float(standalone_vols[idx])
            comp_risk = float(component_risk_pct[idx])
            mcr_val = float(mcr[idx])

            stand_breach = stand_vol > spec.max_standalone_volatility_pct
            shared_breach = comp_risk > spec.max_shared_risk_contribution_pct

            # Calculate adjustment factor
            adj_factor = 1.0
            if stand_breach:
                adj_factor = min(adj_factor, spec.max_standalone_volatility_pct / stand_vol)
            if shared_breach:
                adj_factor = min(adj_factor, spec.max_shared_risk_contribution_pct / comp_risk)

            if stand_breach or shared_breach:
                breached.append(sid)

            breakdown_dict[sid] = StrategyRiskBreakdown(
                strategy_id=sid,
                capital_weight=round(float(weights[idx]), 4),
                standalone_volatility_pct=round(stand_vol, 2),
                marginal_contribution_to_risk=round(mcr_val, 6),
                component_risk_pct=round(comp_risk, 2),
                standalone_limit_breached=stand_breach,
                shared_budget_breached=shared_breach,
                recommended_capital_adjustment_factor=round(adj_factor, 4)
            )

        notes = (
            f"EULER RISK ALLOCATION: Total Capital = ${total_capital:,.2f}, Total Volatility = {ann_port_vol_pct:.2f}%, "
            f"95% VaR = ${port_var_95_usd:,.2f}, Euler Identity Valid = {euler_valid}, Breached Strategies = {breached}."
        )

        if breached:
            logger.warning(notes)
        else:
            logger.info(notes)

        return PortfolioRiskBudgetAllocationReport(
            total_portfolio_capital_usd=round(total_capital, 2),
            total_portfolio_volatility_pct=round(ann_port_vol_pct, 2),
            total_portfolio_var_95_usd=round(port_var_95_usd, 2),
            is_euler_decomposition_valid=euler_valid,
            strategy_breakdown=breakdown_dict,
            breached_strategies=breached,
            audit_notes=notes
        )

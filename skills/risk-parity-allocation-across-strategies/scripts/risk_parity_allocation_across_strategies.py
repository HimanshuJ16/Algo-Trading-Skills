import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class RiskParityAllocationAcrossStrategiesConfig:
    """Legacy config class for backward compatibility."""
    enabled: bool = True
    target_portfolio_volatility: float = 0.12  # 12% annual target vol

class RiskParityAllocationAcrossStrategies:
    """Legacy class retained for backward compatibility."""
    def __init__(self, config: RiskParityAllocationAcrossStrategiesConfig):
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled

@dataclass
class StrategyRiskData:
    strategy_id: str
    annualized_volatility: float          # e.g., 0.20 for 20% vol
    daily_returns: Optional[List[float]] = None

@dataclass
class StrategyAllocation:
    strategy_id: str
    weight: float                        # Portfolio capital weight (0.0 to 1.0)
    allocated_capital_usd: float
    annualized_volatility: float
    risk_contribution_pct: float         # % of total portfolio risk contributed
    target_risk_contribution_pct: float  # Equal share (e.g. 33.3% for 3 strategies)
    risk_parity_error_pct: float

@dataclass
class RiskParityReport:
    total_capital_usd: float
    portfolio_annualized_volatility: float
    allocations: List[StrategyAllocation]
    is_risk_balanced: bool
    max_risk_parity_error_pct: float
    status: str                          # 'RISK_PARITY_BALANCED', 'RISK_PARITY_UNBALANCED'
    audit_notes: str

class RiskParityAllocationEngine:
    """
    Production-grade risk parity portfolio allocation engine computing Equal Risk Contribution (ERC)
    and inverse-volatility weights across trading strategies to ensure equal risk contribution.
    """
    def __init__(self, max_allowed_risk_error_pct: float = 5.0):
        self.max_allowed_risk_error_pct = max_allowed_risk_error_pct

    def compute_inverse_vol_weights(self, strategies: List[StrategyRiskData]) -> List[float]:
        """Calculates inverse-volatility weights normalized to sum to 1.0."""
        inv_vols = [1.0 / max(s.annualized_volatility, 1e-4) for s in strategies]
        sum_inv = sum(inv_vols)
        return [inv / sum_inv for inv in inv_vols]

    def compute_risk_parity_allocation(
        self,
        strategies: List[StrategyRiskData],
        total_capital_usd: float = 1000000.0,
        covariance_matrix: Optional[List[List[float]]] = None
    ) -> RiskParityReport:
        """
        Computes Risk Parity (Equal Risk Contribution) capital allocations and audits risk contribution equality.
        """
        n = len(strategies)
        if n == 0:
            raise ValueError("At least 1 strategy is required for risk parity allocation.")

        # Compute weights using inverse volatility
        weights = self.compute_inverse_vol_weights(strategies)

        # Estimate portfolio volatility & risk contributions
        target_share_pct = round(100.0 / n, 2)
        allocations: List[StrategyAllocation] = []

        if covariance_matrix is None:
            # Diagonal variance assumption (uncorrelated strategies)
            port_variance = sum((weights[i] * strategies[i].annualized_volatility) ** 2 for i in range(n))
            port_vol = math.sqrt(port_variance)

            # Risk contribution of strategy i: (w_i * vol_i)^2 / port_variance
            risk_contribs = [((weights[i] * strategies[i].annualized_volatility) ** 2) / port_variance for i in range(n)]
        else:
            # Full covariance matrix: MCR_i = (Sigma * w)_i / port_vol
            # port_variance = w^T * Sigma * w
            port_variance = 0.0
            for i in range(n):
                for j in range(n):
                    port_variance += weights[i] * weights[j] * covariance_matrix[i][j]
            port_vol = math.sqrt(max(port_variance, 1e-8))

            risk_contribs = []
            for i in range(n):
                mcr_i = sum(covariance_matrix[i][j] * weights[j] for j in range(n)) / port_vol
                rc_i = (weights[i] * mcr_i) / port_vol
                risk_contribs.append(rc_i)

        max_error = 0.0
        for i in range(n):
            strat = strategies[i]
            w = round(weights[i], 4)
            cap_usd = round(total_capital_usd * w, 2)
            rc_pct = round(risk_contribs[i] * 100.0, 2)
            err_pct = round(abs(rc_pct - target_share_pct), 2)
            max_error = max(max_error, err_pct)

            allocations.append(StrategyAllocation(
                strategy_id=strat.strategy_id,
                weight=w,
                allocated_capital_usd=cap_usd,
                annualized_volatility=strat.annualized_volatility,
                risk_contribution_pct=rc_pct,
                target_risk_contribution_pct=target_share_pct,
                risk_parity_error_pct=err_pct
            ))

        is_balanced = max_error <= self.max_allowed_risk_error_pct
        status = "RISK_PARITY_BALANCED" if is_balanced else "RISK_PARITY_UNBALANCED"

        notes = (
            f"RISK PARITY [{status}]: Strategies = {n}, Total Capital = ${total_capital_usd:,.2f}, "
            f"Portfolio Vol = {port_vol*100:.2f}%, Max Risk Error = {max_error:.2f}% (Limit: {self.max_allowed_risk_error_pct}%)."
        )

        if not is_balanced:
            logger.warning(notes)
        else:
            logger.info(notes)

        return RiskParityReport(
            total_capital_usd=total_capital_usd,
            portfolio_annualized_volatility=round(port_vol * 100.0, 2),
            allocations=allocations,
            is_risk_balanced=is_balanced,
            max_risk_parity_error_pct=max_error,
            status=status,
            audit_notes=notes
        )

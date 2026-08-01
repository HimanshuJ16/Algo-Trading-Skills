import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class StrategyReturns:
    strategy_id: str
    daily_returns: List[float]           # e.g. [0.005, -0.002, 0.008, ...]
    risk_free_rate_annual: float = 0.05  # annualized risk-free rate

@dataclass
class RiskAdjustedMetrics:
    strategy_id: str
    total_return: float
    annualized_return: float
    annualized_volatility: float
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float
    calmar_ratio: float
    risk_contribution_pct: float         # % of portfolio risk attributed to this strategy

@dataclass
class PortfolioAttributionReport:
    total_portfolio_return: float
    total_portfolio_volatility: float
    portfolio_sharpe: float
    strategy_attributions: List[RiskAdjustedMetrics]
    status: str                          # 'ATTRIBUTION_COMPLETE'
    audit_notes: str

class RiskAdjustedPerformanceAttributionEngine:
    """
    Risk-adjusted performance attribution engine computing Sharpe, Sortino, Calmar ratios,
    max drawdown, and marginal risk contribution per strategy within a multi-strategy portfolio.
    """
    TRADING_DAYS = 252

    def __init__(self, risk_free_rate_annual: float = 0.05):
        self.risk_free_rate_annual = risk_free_rate_annual

    def _annualized_return(self, daily_returns: List[float]) -> float:
        cumulative = 1.0
        for r in daily_returns:
            cumulative *= (1.0 + r)
        n_days = len(daily_returns)
        if n_days == 0:
            return 0.0
        ann_ret = cumulative ** (self.TRADING_DAYS / n_days) - 1.0
        return round(ann_ret, 6)

    def _annualized_volatility(self, daily_returns: List[float]) -> float:
        if len(daily_returns) < 2:
            return 0.0
        daily_std = statistics.stdev(daily_returns)
        return round(daily_std * math.sqrt(self.TRADING_DAYS), 6)

    def _sharpe_ratio(self, ann_return: float, ann_vol: float) -> float:
        rf = self.risk_free_rate_annual
        if ann_vol <= 0:
            return 0.0
        return round((ann_return - rf) / ann_vol, 4)

    def _sortino_ratio(self, daily_returns: List[float], ann_return: float) -> float:
        rf_daily = self.risk_free_rate_annual / self.TRADING_DAYS
        downside = [min(r - rf_daily, 0.0) ** 2 for r in daily_returns]
        if len(downside) == 0:
            return 0.0
        downside_dev = math.sqrt(sum(downside) / len(downside)) * math.sqrt(self.TRADING_DAYS)
        if downside_dev <= 0:
            return 0.0
        return round((ann_return - self.risk_free_rate_annual) / downside_dev, 4)

    def _max_drawdown(self, daily_returns: List[float]) -> float:
        cumulative = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in daily_returns:
            cumulative *= (1.0 + r)
            peak = max(peak, cumulative)
            dd = (peak - cumulative) / peak
            max_dd = max(max_dd, dd)
        return round(max_dd, 6)

    def _calmar_ratio(self, ann_return: float, max_dd: float) -> float:
        if max_dd <= 0:
            return 0.0
        return round(ann_return / max_dd, 4)

    def compute_strategy_attribution(
        self,
        strategies: List[StrategyReturns],
        weights: Optional[List[float]] = None
    ) -> PortfolioAttributionReport:
        """
        Computes risk-adjusted metrics per strategy and marginal risk contribution to portfolio.
        """
        n = len(strategies)
        if weights is None:
            weights = [1.0 / n] * n

        # Compute per-strategy metrics
        strat_vols: List[float] = []
        attributions: List[RiskAdjustedMetrics] = []

        for i, strat in enumerate(strategies):
            ann_ret = self._annualized_return(strat.daily_returns)
            ann_vol = self._annualized_volatility(strat.daily_returns)
            sharpe = self._sharpe_ratio(ann_ret, ann_vol)
            sortino = self._sortino_ratio(strat.daily_returns, ann_ret)
            max_dd = self._max_drawdown(strat.daily_returns)
            calmar = self._calmar_ratio(ann_ret, max_dd)

            strat_vols.append(ann_vol * weights[i])

            attributions.append(RiskAdjustedMetrics(
                strategy_id=strat.strategy_id,
                total_return=round(sum(strat.daily_returns), 6),
                annualized_return=ann_ret,
                annualized_volatility=ann_vol,
                sharpe_ratio=sharpe,
                sortino_ratio=sortino,
                max_drawdown=max_dd,
                calmar_ratio=calmar,
                risk_contribution_pct=0.0  # placeholder
            ))

        # Compute portfolio-level metrics (equal-weighted blended returns)
        min_len = min(len(s.daily_returns) for s in strategies) if strategies else 0
        portfolio_returns: List[float] = []
        for day_idx in range(min_len):
            blended = sum(weights[i] * strategies[i].daily_returns[day_idx] for i in range(n))
            portfolio_returns.append(blended)

        port_ann_ret = self._annualized_return(portfolio_returns)
        port_ann_vol = self._annualized_volatility(portfolio_returns)
        port_sharpe = self._sharpe_ratio(port_ann_ret, port_ann_vol)

        # Compute marginal risk contribution
        total_weighted_vol = sum(strat_vols)
        if total_weighted_vol > 0:
            for i, attr in enumerate(attributions):
                attr.risk_contribution_pct = round((strat_vols[i] / total_weighted_vol) * 100.0, 2)

        notes = (
            f"RISK-ADJUSTED ATTRIBUTION [ATTRIBUTION_COMPLETE]: "
            f"Strategies = {n}, Portfolio Sharpe = {port_sharpe}, "
            f"Portfolio Vol = {port_ann_vol:.4f}."
        )
        logger.info(notes)

        return PortfolioAttributionReport(
            total_portfolio_return=port_ann_ret,
            total_portfolio_volatility=port_ann_vol,
            portfolio_sharpe=port_sharpe,
            strategy_attributions=attributions,
            status="ATTRIBUTION_COMPLETE",
            audit_notes=notes
        )

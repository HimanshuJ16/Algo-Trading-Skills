import math
import logging
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class StrategyTelemetry:
    strategy_id: str
    strategy_name: str
    allocated_capital_usd: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    daily_returns: List[float]            # Daily decimal returns (e.g. 0.001 = 0.1%)
    max_drawdown_pct: float              # Sub-strategy max drawdown

@dataclass
class ReportingConfig:
    portfolio_name: str = "ALPHA_MULTI_STRAT_FUND"
    risk_free_rate_ann: float = 0.04     # 4% annual risk free rate
    trading_days_per_year: int = 252

@dataclass
class StrategyContribution:
    strategy_id: str
    strategy_name: str
    allocated_capital_usd: float
    weight_pct: float
    net_pnl_usd: float
    pnl_contribution_pct: float
    annualized_volatility_pct: float
    sharpe_ratio: float

@dataclass
class ConsolidatedStakeholderReport:
    portfolio_name: str
    total_allocated_capital_usd: float
    total_net_pnl_usd: float
    portfolio_return_pct: float
    portfolio_annualized_volatility_pct: float
    portfolio_sharpe_ratio: float
    weighted_sum_volatility_pct: float
    diversification_ratio: float
    strategy_contributions: List[StrategyContribution]
    status: str                          # 'REPORT_CONSOLIDATED_SUCCESS'
    audit_notes: str

class MultiStrategyReportingConsolidatorEngine:
    """
    Multi-strategy reporting consolidation engine aggregating sub-strategy PnL,
    computing portfolio-level Sharpe ratios, diversification ratios, and stakeholder attribution.
    """
    def __init__(self, config: Optional[ReportingConfig] = None):
        self.config = config or ReportingConfig()

    def consolidate_reports(
        self, strategies: List[StrategyTelemetry]
    ) -> ConsolidatedStakeholderReport:
        """
        Synthesizes daily joint portfolio return series, computes portfolio annualized volatility,
        portfolio Sharpe ratio, and diversification benefit ratio.
        """
        if not strategies:
            raise ValueError("Strategy telemetry list cannot be empty.")

        total_capital = sum(s.allocated_capital_usd for s in strategies)
        if total_capital <= 0:
            raise ValueError("Total allocated capital must be positive.")

        total_pnl = sum(s.realized_pnl_usd + s.unrealized_pnl_usd for s in strategies)
        portfolio_return = (total_pnl / total_capital) * 100.0

        days_ann = self.config.trading_days_per_year
        rf_daily = self.config.risk_free_rate_ann / days_ann

        # 1. Synthesize Portfolio Daily Weighted Return Series & Sub-strategy Metrics
        weights = [s.allocated_capital_usd / total_capital for s in strategies]
        min_series_len = min(len(s.daily_returns) for s in strategies)

        if min_series_len == 0:
            raise ValueError("Sub-strategy return series cannot be empty.")

        portfolio_daily_returns: List[float] = []
        for t in range(min_series_len):
            ret_t = sum(weights[k] * strategies[k].daily_returns[t] for k in range(len(strategies)))
            portfolio_daily_returns.append(ret_t)

        # 2. Portfolio Annualized Metrics
        mean_p_daily = statistics.mean(portfolio_daily_returns)
        std_p_daily = statistics.stdev(portfolio_daily_returns) if min_series_len > 1 else 0.0001
        if std_p_daily == 0.0:
            std_p_daily = 0.0001

        port_vol_ann = std_p_daily * math.sqrt(days_ann) * 100.0
        port_sharpe = ((mean_p_daily * days_ann) - self.config.risk_free_rate_ann) / (std_p_daily * math.sqrt(days_ann))

        # 3. Individual Strategy Metrics & Weighted Sum Volatility
        contributions: List[StrategyContribution] = []
        sum_weighted_vols = 0.0

        for k, s in enumerate(strategies):
            w = weights[k]
            net_pnl = s.realized_pnl_usd + s.unrealized_pnl_usd
            pnl_contrib = (net_pnl / total_pnl * 100.0) if total_pnl != 0 else 0.0

            std_k_daily = statistics.stdev(s.daily_returns[:min_series_len]) if min_series_len > 1 else 0.0001
            if std_k_daily == 0.0:
                std_k_daily = 0.0001

            strat_vol_ann = std_k_daily * math.sqrt(days_ann) * 100.0
            mean_k_daily = statistics.mean(s.daily_returns[:min_series_len])
            strat_sharpe = ((mean_k_daily * days_ann) - self.config.risk_free_rate_ann) / (std_k_daily * math.sqrt(days_ann))

            sum_weighted_vols += w * strat_vol_ann

            contributions.append(StrategyContribution(
                strategy_id=s.strategy_id,
                strategy_name=s.strategy_name,
                allocated_capital_usd=s.allocated_capital_usd,
                weight_pct=round(w * 100.0, 2),
                net_pnl_usd=round(net_pnl, 2),
                pnl_contribution_pct=round(pnl_contrib, 2),
                annualized_volatility_pct=round(strat_vol_ann, 2),
                sharpe_ratio=round(strat_sharpe, 2)
            ))

        # 4. Diversification Ratio Audit
        diversification_ratio = sum_weighted_vols / port_vol_ann if port_vol_ann > 0 else 1.0

        notes = (
            f"REPORT CONSOLIDATED SUCCESS [{self.config.portfolio_name}]: Total Capital = ${total_capital:,.2f}, "
            f"Total PnL = ${total_pnl:,.2f} ({portfolio_return:.2f}%). Portfolio Vol = {port_vol_ann:.2f}%, "
            f"Portfolio Sharpe = {port_sharpe:.2f}, Diversification Ratio = {diversification_ratio:.2f}x."
        )
        logger.info(notes)

        return ConsolidatedStakeholderReport(
            portfolio_name=self.config.portfolio_name,
            total_allocated_capital_usd=round(total_capital, 2),
            total_net_pnl_usd=round(total_pnl, 2),
            portfolio_return_pct=round(portfolio_return, 2),
            portfolio_annualized_volatility_pct=round(port_vol_ann, 2),
            portfolio_sharpe_ratio=round(port_sharpe, 2),
            weighted_sum_volatility_pct=round(sum_weighted_vols, 2),
            diversification_ratio=round(diversification_ratio, 2),
            strategy_contributions=contributions,
            status="REPORT_CONSOLIDATED_SUCCESS",
            audit_notes=notes
        )

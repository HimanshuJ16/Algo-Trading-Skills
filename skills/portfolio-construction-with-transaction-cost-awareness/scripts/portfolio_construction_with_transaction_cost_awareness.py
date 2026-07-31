import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Config:
    enabled: bool = True
    rebalance_threshold: float = 0.02    # 2% weight shift buffer band
    max_turnover_limit: float = 0.50     # 50% max single-rebalance turnover

@dataclass
class AssetAlphaSpec:
    symbol: str
    expected_return: float               # e.g. 0.10 for 10%
    current_weight: float                # e.g. 0.40
    target_weight: float                 # e.g. 0.45

@dataclass
class CostSpec:
    commission_rate: float = 0.0005      # 5 bps
    spread_cost_bps: float = 5.0          # 5 bps half-spread
    impact_coeff: float = 0.5            # Quadratic market impact penalty

@dataclass
class TCAwarePortfolioReport:
    total_assets: int
    gross_expected_return: float
    total_transaction_cost: float
    net_expected_return: float
    total_turnover: float
    traded_symbols: List[str]
    suppressed_symbols: List[str]
    status: str                          # 'REBALANCED_COST_OPTIMIZED', 'TURNOVER_LIMIT_EXCEEDED'
    audit_notes: str

class Engine:
    """
    Legacy Engine class retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return self.config.enabled

class PortfolioConstructionEngine:
    """
    Transaction-cost aware portfolio construction engine incorporating proportional commissions,
    bid-ask spreads, quadratic market impact, and no-trade buffer bands.
    """
    def __init__(
        self,
        config: Optional[Config] = None,
        cost_spec: Optional[CostSpec] = None
    ):
        self.config = config or Config()
        self.cost_spec = cost_spec or CostSpec()

    def construct_portfolio(
        self, assets: List[AssetAlphaSpec]
    ) -> TCAwarePortfolioReport:
        """
        Calculates optimal final portfolio weights, turnover, and net expected return after deducting transaction costs.
        """
        if not self.config.enabled:
            return TCAwarePortfolioReport(
                total_assets=len(assets),
                gross_expected_return=0.0,
                total_transaction_cost=0.0,
                net_expected_return=0.0,
                total_turnover=0.0,
                traded_symbols=[],
                suppressed_symbols=[a.symbol for a in assets],
                status="ENGINE_DISABLED",
                audit_notes="Engine is disabled."
            )

        traded_symbols: List[str] = []
        suppressed_symbols: List[str] = []
        final_weights: Dict[str, float] = {}
        total_tc = 0.0
        total_turnover = 0.0

        for a in assets:
            delta_w = a.target_weight - a.current_weight

            # Apply No-Trade Buffer Band Filter
            if abs(delta_w) <= self.config.rebalance_threshold:
                final_weights[a.symbol] = a.current_weight
                suppressed_symbols.append(a.symbol)
            else:
                final_weights[a.symbol] = a.target_weight
                traded_symbols.append(a.symbol)

                abs_delta = abs(delta_w)
                total_turnover += abs_delta

                # Linear Commission + Linear Spread + Quadratic Impact
                spread_pct = self.cost_spec.spread_cost_bps / 10000.0
                linear_cost = (self.cost_spec.commission_rate + spread_pct) * abs_delta
                quadratic_impact = self.cost_spec.impact_coeff * (delta_w ** 2)

                tc = linear_cost + quadratic_impact
                total_tc += tc

        # Audit Turnover Limit
        is_turnover_exceeded = total_turnover > self.config.max_turnover_limit

        gross_return = sum(final_weights[a.symbol] * a.expected_return for a in assets)
        net_return = gross_return - total_tc

        status = "TURNOVER_LIMIT_EXCEEDED" if is_turnover_exceeded else "REBALANCED_COST_OPTIMIZED"
        notes = (
            f"TC-AWARE PORTFOLIO REBALANCE [{status}]: Assets = {len(assets)}, Traded = {len(traded_symbols)}, "
            f"Suppressed (No-Trade Band) = {len(suppressed_symbols)}. "
            f"Turnover = {total_turnover:.2%}, Total TC = {total_tc * 10000:.1f} bps. "
            f"Gross Return = {gross_return:.2%}, Net Return = {net_return:.2%}."
        )

        if is_turnover_exceeded:
            logger.warning(f"TURNOVER EXCEEDED: {notes}")
        else:
            logger.info(notes)

        return TCAwarePortfolioReport(
            total_assets=len(assets),
            gross_expected_return=round(gross_return, 4),
            total_transaction_cost=round(total_tc, 6),
            net_expected_return=round(net_return, 4),
            total_turnover=round(total_turnover, 4),
            traded_symbols=traded_symbols,
            suppressed_symbols=suppressed_symbols,
            status=status,
            audit_notes=notes
        )

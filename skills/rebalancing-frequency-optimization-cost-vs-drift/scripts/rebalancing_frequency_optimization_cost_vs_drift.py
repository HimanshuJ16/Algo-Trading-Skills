import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Config:
    enabled: bool = True
    drift_penalty_lambda: float = 1.0     # Penalty multiplier for tracking error variance
    max_drift_threshold_pct: float = 0.05  # 5% max single asset weight drift threshold
    min_trade_threshold_pct: float = 0.01  # 1% minimum drift required to trigger net benefit rebalance

@dataclass
class AssetWeight:
    symbol: str
    target_weight: float                 # e.g. 0.50 for 50%
    current_weight: float                # e.g. 0.60 for 60%
    asset_value_usd: float
    fee_rate_bps: float = 5.0            # 5 bps fee
    estimated_slippage_bps: float = 5.0   # 5 bps slippage

@dataclass
class RebalanceTradeOrder:
    symbol: str
    action: str                          # 'BUY', 'SELL'
    weight_delta_pct: float
    trade_amount_usd: float

@dataclass
class RebalanceOptimizationReport:
    total_portfolio_value_usd: float
    total_drift_cost_usd: float
    total_transaction_cost_usd: float
    net_economic_benefit_usd: float
    max_single_drift_pct: float
    rebalance_recommended: bool
    proposed_trades: List[RebalanceTradeOrder]
    status: str                          # 'REBALANCE_TRIGGERED_NET_BENEFIT', 'REBALANCE_TRIGGERED_MAX_DRIFT', 'NO_REBALANCE_WITHIN_BAND'
    audit_notes: str

class Engine:
    """
    Legacy Engine class retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return self.config.enabled

class RebalancingFrequencyOptimizerEngine:
    """
    Quantitative portfolio rebalancing optimizer evaluating the economic tradeoff between
    tracking error drift penalty and transaction costs using Leland no-trade bands.
    """
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def optimize_rebalancing(
        self, assets: List[AssetWeight]
    ) -> RebalanceOptimizationReport:
        """
        Evaluates drift cost vs transaction cost and returns optimal rebalancing recommendations.
        """
        if not self.config.enabled:
            return RebalanceOptimizationReport(
                total_portfolio_value_usd=0.0,
                total_drift_cost_usd=0.0,
                total_transaction_cost_usd=0.0,
                net_economic_benefit_usd=0.0,
                max_single_drift_pct=0.0,
                rebalance_recommended=False,
                proposed_trades=[],
                status="ENGINE_DISABLED",
                audit_notes="Rebalancing optimizer engine is disabled."
            )

        if not assets:
            return RebalanceOptimizationReport(
                total_portfolio_value_usd=0.0,
                total_drift_cost_usd=0.0,
                total_transaction_cost_usd=0.0,
                net_economic_benefit_usd=0.0,
                max_single_drift_pct=0.0,
                rebalance_recommended=False,
                proposed_trades=[],
                status="NO_ASSETS",
                audit_notes="No assets provided for rebalancing optimization."
            )

        total_portfolio_val = sum(a.asset_value_usd for a in assets)
        if total_portfolio_val <= 0:
            total_portfolio_val = 1.0  # Fallback prevention

        total_drift_sq = 0.0
        total_tx_cost = 0.0
        max_drift = 0.0
        proposed_trades: List[RebalanceTradeOrder] = []

        for a in assets:
            drift = a.current_weight - a.target_weight
            abs_drift = abs(drift)
            if abs_drift > max_drift:
                max_drift = abs_drift

            # Drift Cost = Lambda * Drift^2 * PortfolioValue
            total_drift_sq += drift * drift

            # Tx Cost = |Drift| * PortfolioValue * ((FeeBps + SlippageBps) / 10000)
            total_cost_bps = a.fee_rate_bps + a.estimated_slippage_bps
            tx_cost = abs_drift * total_portfolio_val * (total_cost_bps / 10000.0)
            total_tx_cost += tx_cost

            # Generate trade orders
            trade_amount_usd = abs_drift * total_portfolio_val
            if abs_drift > 0.0001:
                action = "SELL" if drift > 0 else "BUY"
                proposed_trades.append(
                    RebalanceTradeOrder(
                        symbol=a.symbol,
                        action=action,
                        weight_delta_pct=round(drift * 100.0, 2),
                        trade_amount_usd=round(trade_amount_usd, 2)
                    )
                )

        total_drift_cost = self.config.drift_penalty_lambda * total_drift_sq * total_portfolio_val
        net_benefit = total_drift_cost - total_tx_cost

        max_drift_breached = max_drift >= self.config.max_drift_threshold_pct
        net_benefit_positive = (net_benefit > 0.0) and (max_drift >= self.config.min_trade_threshold_pct)

        rebalance_recommended = max_drift_breached or net_benefit_positive

        if max_drift_breached:
            status = "REBALANCE_TRIGGERED_MAX_DRIFT"
        elif net_benefit_positive:
            status = "REBALANCE_TRIGGERED_NET_BENEFIT"
        else:
            status = "NO_REBALANCE_WITHIN_BAND"
            proposed_trades = [] # No trades if within no-trade band

        notes = (
            f"REBALANCING OPTIMIZATION [{status}]: Portfolio Val = ${total_portfolio_val:,.2f}, "
            f"Max Single Drift = {max_drift * 100.0:.2f}%, Total Drift Cost = ${total_drift_cost:,.2f}, "
            f"Total Tx Cost = ${total_tx_cost:,.2f}, Net Benefit = ${net_benefit:,.2f}."
        )

        logger.info(notes)

        return RebalanceOptimizationReport(
            total_portfolio_value_usd=round(total_portfolio_val, 2),
            total_drift_cost_usd=round(total_drift_cost, 2),
            total_transaction_cost_usd=round(total_tx_cost, 2),
            net_economic_benefit_usd=round(net_benefit, 2),
            max_single_drift_pct=round(max_drift * 100.0, 2),
            rebalance_recommended=rebalance_recommended,
            proposed_trades=proposed_trades,
            status=status,
            audit_notes=notes
        )

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class SubStrategySignal:
    strategy_id: str
    symbol: str
    raw_signal: float                   # Direction & strength: [-1.0, +1.0] (e.g. +1.0 Strong Buy, -1.0 Strong Sell)
    conviction_score: float             # Confidence: [0.0, 1.0]
    target_notional_usd: float          # Desired position value (+ for Long, - for Short)
    is_risk_veto: bool = False          # True if emergency risk-off / stop loss trigger

@dataclass
class StrategyWeightConfig:
    strategy_id: str
    weight: float                       # Allocation weight (e.g. 0.50)
    priority_rank: int = 1              # Priority rank (1 = highest priority)

@dataclass
class MetaStrategyArbitrationReport:
    symbol: str
    total_strategies_count: int
    gross_notional_usd: float           # Sum of absolute sub-strategy position requests
    net_executable_notional_usd: float  # Net arbitrated order value
    internal_netting_savings_usd: float # Saved spread/fee costs from internal netting
    consensus_signal: float             # Weighted consensus signal [-1.0, +1.0]
    is_risk_veto_active: bool
    status: str                         # 'ARBITRATION_NETTED_ORDER_GENERATED', 'ARBITRATION_VETO_RISK_OFF', 'DEADBAND_REBALANCING_SUPPRESSED'
    audit_notes: str

class MetaStrategySignalArbitratorEngine:
    """
    Multi-strategy signal arbitration and internal order netting engine,
    resolving conflicting sub-strategy signals, enforcing risk-off vetoes, and eliminating redundant execution spread costs.
    """
    def __init__(self, deadband_threshold: float = 0.05, estimated_transaction_cost_bps: float = 10.0):
        self.deadband_threshold = deadband_threshold
        self.estimated_transaction_cost_bps = estimated_transaction_cost_bps

    def arbitrate_strategy_signals(
        self,
        symbol: str,
        weights: List[StrategyWeightConfig],
        signals: List[SubStrategySignal],
        current_consensus_signal: float = 0.0
    ) -> MetaStrategyArbitrationReport:
        """
        Arbitrates sub-strategy signals, checks risk-off vetoes, nets opposing orders, and computes savings.
        """
        if not signals:
            raise ValueError("Sub-strategy signals list cannot be empty.")

        weight_map = {w.strategy_id: w.weight for w in weights}
        
        # 1. Risk-Off Veto Audit
        risk_veto_signals = [s for s in signals if s.is_risk_veto]
        if risk_veto_signals:
            veto_strat_ids = [s.strategy_id for s in risk_veto_signals]
            notes = f"ARBITRATION VETO [{symbol}]: Emergency Risk-Off Veto triggered by strategy(ies) {veto_strat_ids}! Liquidating/blocking positions."
            logger.critical(notes)
            return MetaStrategyArbitrationReport(
                symbol=symbol,
                total_strategies_count=len(signals),
                gross_notional_usd=sum(abs(s.target_notional_usd) for s in signals),
                net_executable_notional_usd=0.0,
                internal_netting_savings_usd=0.0,
                consensus_signal=-1.0,
                is_risk_veto_active=True,
                status="ARBITRATION_VETO_RISK_OFF",
                audit_notes=notes
            )

        # 2. Weighted Net Signal & Notional Calculation
        total_weight = 0.0
        weighted_signal_sum = 0.0
        gross_notional = 0.0
        net_notional = 0.0

        for s in signals:
            w = weight_map.get(s.strategy_id, 1.0)
            total_weight += w
            weighted_signal_sum += (s.raw_signal * s.conviction_score * w)
            gross_notional += abs(s.target_notional_usd)
            net_notional += s.target_notional_usd

        consensus_signal = round(weighted_signal_sum / total_weight, 4) if total_weight > 0 else 0.0
        net_executable_notional = round(net_notional, 2)

        # 3. Internal Order Netting Transaction Savings
        # Internal Netting Volume = Gross Notional - |Net Executable Notional|
        internal_netted_volume = gross_notional - abs(net_executable_notional)
        netting_savings_usd = round(internal_netted_volume * (self.estimated_transaction_cost_bps / 10000.0), 2)

        # 4. Deadband Filter Audit
        signal_delta = abs(consensus_signal - current_consensus_signal)
        if signal_delta < self.deadband_threshold:
            status = "DEADBAND_REBALANCING_SUPPRESSED"
            notes = (
                f"DEADBAND SUPPRESSED [{symbol}]: Signal delta ({signal_delta:.4f}) is below "
                f"deadband threshold ({self.deadband_threshold}). Order rebalancing skipped."
            )
            logger.info(notes)
            return MetaStrategyArbitrationReport(
                symbol=symbol,
                total_strategies_count=len(signals),
                gross_notional_usd=round(gross_notional, 2),
                net_executable_notional_usd=0.0,
                internal_netting_savings_usd=netting_savings_usd,
                consensus_signal=consensus_signal,
                is_risk_veto_active=False,
                status=status,
                audit_notes=notes
            )

        # 5. Arbitrated Netted Order Approved
        status = "ARBITRATION_NETTED_ORDER_GENERATED"
        notes = (
            f"ARBITRATION SUCCESS [{symbol}]: Consolidated {len(signals)} strategy signals. "
            f"Gross Notional = ${gross_notional:,.2f}, Net Executable Notional = ${net_executable_notional:,.2f}. "
            f"Internal Netting Saved ${netting_savings_usd:,.2f} in transaction costs. "
            f"Consensus Signal = {consensus_signal:+.4f}."
        )
        logger.info(notes)

        return MetaStrategyArbitrationReport(
            symbol=symbol,
            total_strategies_count=len(signals),
            gross_notional_usd=round(gross_notional, 2),
            net_executable_notional_usd=net_executable_notional,
            internal_netting_savings_usd=netting_savings_usd,
            consensus_signal=consensus_signal,
            is_risk_veto_active=False,
            status=status,
            audit_notes=notes
        )

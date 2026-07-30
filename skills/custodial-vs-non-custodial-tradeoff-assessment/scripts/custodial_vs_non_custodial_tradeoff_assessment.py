import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class StrategyRequirements:
    strategy_name: str
    required_latency_ms: float         # e.g. 1.0 ms vs 12000.0 ms
    monthly_volume_usd: float
    max_counterparty_risk_pct: float   # Maximum acceptable CEX default risk %
    gas_sensitivity_score: float       # 0.0 (Low, insensitive) to 1.0 (High sensitivity)
    requires_key_sovereignty: bool

@dataclass
class CustodyArchitectureScore:
    architecture_name: str             # 'CUSTODIAL_CEX', 'HYBRID_OFF_EXCHANGE_SETTLEMENT', 'NON_CUSTODIAL_DEX'
    latency_score: float               # 0.0 to 100.0
    security_score: float              # 0.0 to 100.0
    cost_efficiency_score: float       # 0.0 to 100.0
    composite_suitability_score: float # 0.0 to 100.0
    tradeoff_summary: str

@dataclass
class CustodyTradeoffReport:
    strategy_name: str
    recommended_architecture: str
    recommended_score: float
    rankings: List[CustodyArchitectureScore]
    key_risk_mitigations: List[str]

class CustodialTradeoffAssessorEngine:
    """
    Quantitative framework for evaluating institutional custody and exchange execution trade-offs
    between Custodial CEX, Off-Exchange Settlement (Fireblocks/Copper), and Non-Custodial DEX architectures.
    """
    def __init__(self):
        pass

    def evaluate_strategy_custody(self, req: StrategyRequirements) -> CustodyTradeoffReport:
        """
        Scores candidate custody architectures and returns suitability rankings.
        """
        # 1. Evaluate CUSTODIAL_CEX (Binance, Coinbase, Kraken)
        # Fast latency (100 pts), High counterparty risk penalty
        cex_lat_score = 100.0 if req.required_latency_ms <= 10.0 else 80.0
        cex_sec_score = 30.0 if req.requires_key_sovereignty else max(10.0, 100.0 - req.max_counterparty_risk_pct * 2)
        cex_cost_score = 90.0  # Low execution fees, zero gas fees
        cex_comp = round(0.40 * cex_lat_score + 0.35 * cex_sec_score + 0.25 * cex_cost_score, 2)
        cex_summary = "Ultra-low latency off-chain execution, deep liquidity, but exposes funds to CEX insolvency risk."

        # 2. Evaluate HYBRID_OFF_EXCHANGE_SETTLEMENT (Fireblocks, Copper ClearLoop)
        # Fast latency (90 pts), Reduced CEX risk (85 pts), Low gas (85 pts)
        hybrid_lat_score = 90.0 if req.required_latency_ms <= 50.0 else 85.0
        hybrid_sec_score = 85.0 if not req.requires_key_sovereignty else 70.0
        hybrid_cost_score = 80.0
        hybrid_comp = round(0.40 * hybrid_lat_score + 0.35 * hybrid_sec_score + 0.25 * hybrid_cost_score, 2)
        hybrid_summary = "MPC key sharding with off-exchange settlement; minimizes CEX insolvency risk while maintaining sub-second execution."

        # 3. Evaluate NON_CUSTODIAL_DEX (Uniswap, Aave, Self-Custody MPC)
        # Slow latency (10 pts if HFT required, 95 pts if low-freq), 100% key sovereignty, Gas penalty
        dex_lat_score = 10.0 if req.required_latency_ms <= 100.0 else 90.0
        dex_sec_score = 100.0 if req.requires_key_sovereignty else 90.0
        dex_cost_score = max(10.0, 90.0 - req.gas_sensitivity_score * 70.0)
        dex_comp = round(0.40 * dex_lat_score + 0.35 * dex_sec_score + 0.25 * dex_cost_score, 2)
        dex_summary = "Complete key self-sovereignty and zero counterparty CEX risk, but subject to gas volatility and block inclusion latency."

        scores = [
            CustodyArchitectureScore("CUSTODIAL_CEX", cex_lat_score, cex_sec_score, cex_cost_score, cex_comp, cex_summary),
            CustodyArchitectureScore("HYBRID_OFF_EXCHANGE_SETTLEMENT", hybrid_lat_score, hybrid_sec_score, hybrid_cost_score, hybrid_comp, hybrid_summary),
            CustodyArchitectureScore("NON_CUSTODIAL_DEX", dex_lat_score, dex_sec_score, dex_cost_score, dex_comp, dex_summary)
        ]

        # Rank by composite score
        rankings = sorted(scores, key=lambda s: s.composite_suitability_score, reverse=True)
        top_choice = rankings[0]

        mitigations = []
        if top_choice.architecture_name == "CUSTODIAL_CEX":
            mitigations.append("Enforce strict daily hot-wallet balance caps (e.g. <= 10% total AUM).")
            mitigations.append("Integrate API key withdrawal address whitelisting.")
        elif top_choice.architecture_name == "HYBRID_OFF_EXCHANGE_SETTLEMENT":
            mitigations.append("Deploy Fireblocks/Copper ClearLoop MPC key sharding across 3 independent cloud zones.")
        else:
            mitigations.append("Deploy private RPC endpoints and Flashbots Protect to prevent MEV sandwich attacks.")

        logger.info(f"CUSTODY EVALUATION [{req.strategy_name}]: Recommended -> {top_choice.architecture_name} (Score: {top_choice.composite_suitability_score})")

        return CustodyTradeoffReport(
            strategy_name=req.strategy_name,
            recommended_architecture=top_choice.architecture_name,
            recommended_score=top_choice.composite_suitability_score,
            rankings=rankings,
            key_risk_mitigations=mitigations
        )

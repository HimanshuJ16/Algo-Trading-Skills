import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional
import pandas as pd
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

class DecayClassification(str, Enum):
    HEALTHY = "HEALTHY"
    IDIOSYNCRATIC_ALPHA_DECAY = "IDIOSYNCRATIC_ALPHA_DECAY" # Strategy edge dead; peers healthy
    MARKET_WIDE_REGIME_SHIFT = "MARKET_WIDE_REGIME_SHIFT"   # Entire strategy class suffering
    INCONCLUSIVE = "INCONCLUSIVE"

@dataclass
class StrategyDecayDiagnosticsReport:
    strategy_id: str
    peer_benchmark_id: str
    classification: DecayClassification
    target_sharpe: float
    peer_benchmark_sharpe: float
    relative_excess_sharpe: float
    relative_sharpe_z_score: float
    recommended_action: str
    audit_notes: str

class StrategyPerformanceDecayDiagnosticEngine:
    """
    Production-grade Performance Decay Diagnostic Engine classifying underperformance into
    Idiosyncratic Alpha Decay vs Systematic Market-Wide Regime Shift using peer benchmark attribution.
    """
    def __init__(
        self,
        rolling_window_days: int = 60,
        idiosyncratic_z_threshold: float = -1.96, # 95% confidence underperformance vs peers
        market_wide_sharpe_threshold: float = 0.50 # Threshold for peer benchmark health
    ):
        self.rolling_window = rolling_window_days
        self.z_threshold = idiosyncratic_z_threshold
        self.peer_sharpe_threshold = market_wide_sharpe_threshold

    def evaluate_decay_cause(
        self,
        strategy_id: str,
        peer_benchmark_id: str,
        strategy_returns: pd.Series,
        peer_returns: pd.Series,
        risk_free_rate_annual_pct: float = 2.0
    ) -> StrategyDecayDiagnosticsReport:
        """
        Distinguishes between Idiosyncratic Alpha Decay and Market-Wide Regime Shift:
        1. Calculates rolling Sharpe ratio for target strategy and peer benchmark index.
        2. Calculates relative Sharpe difference: Sharpe_target - Sharpe_peer.
        3. Computes Z-score of relative Sharpe difference.
        """
        df = pd.DataFrame({
            "target": strategy_returns,
            "peer": peer_returns
        }).dropna()

        if len(df) < self.rolling_window:
            raise ValueError(f"Insufficient return history ({len(df)} days). Required window = {self.rolling_window} days.")

        rf_daily = (risk_free_rate_annual_pct / 100.0) / 252.0

        # Calculate annualized Sharpe ratios over the window
        t_ret = df["target"].iloc[-self.rolling_window:]
        p_ret = df["peer"].iloc[-self.rolling_window:]

        t_std = t_ret.std() * math.sqrt(252)
        p_std = p_ret.std() * math.sqrt(252)

        target_sharpe = float((t_ret.mean() * 252 - (risk_free_rate_annual_pct / 100.0)) / t_std) if t_std > 0 else 0.0
        peer_sharpe = float((p_ret.mean() * 252 - (risk_free_rate_annual_pct / 100.0)) / p_std) if p_std > 0 else 0.0

        # Rolling relative Sharpe history for Z-score calculation
        rolling_t_mean = df["target"].rolling(self.rolling_window).mean() * 252
        rolling_t_std = df["target"].rolling(self.rolling_window).std() * math.sqrt(252)
        rolling_t_sharpe = (rolling_t_mean - (risk_free_rate_annual_pct / 100.0)) / rolling_t_std

        rolling_p_mean = df["peer"].rolling(self.rolling_window).mean() * 252
        rolling_p_std = df["peer"].rolling(self.rolling_window).std() * math.sqrt(252)
        rolling_p_sharpe = (rolling_p_mean - (risk_free_rate_annual_pct / 100.0)) / rolling_p_std

        rel_diff = (rolling_t_sharpe - rolling_p_sharpe).dropna()

        current_diff = target_sharpe - peer_sharpe
        if len(rel_diff) >= 30 and rel_diff.std() > 1e-4:
            z_score = float((current_diff - rel_diff.mean()) / rel_diff.std())
        else:
            z_score = 0.0

        # Classification Matrix
        if z_score <= self.z_threshold and peer_sharpe >= self.peer_sharpe_threshold:
            classification = DecayClassification.IDIOSYNCRATIC_ALPHA_DECAY
            action = "DECOMMISSION_OR_RECODE: Strategy alpha is dead while peer benchmark is healthy. Initiate decommissioning."
        elif target_sharpe < self.peer_sharpe_threshold and peer_sharpe < self.peer_sharpe_threshold:
            classification = DecayClassification.MARKET_WIDE_REGIME_SHIFT
            action = "PAUSE_OR_REDUCE_RISK: Entire asset class/strategy peer group is suffering from market-wide regime shift. Reduce capital allocation temporarily."
        elif target_sharpe >= self.peer_sharpe_threshold:
            classification = DecayClassification.HEALTHY
            action = "MAINTAIN_TRADING: Strategy risk-adjusted performance is healthy relative to peer group."
        else:
            classification = DecayClassification.INCONCLUSIVE
            action = "MONITOR_CLOSELY: Mixed quantitative signals; monitor rolling performance."

        notes = (
            f"DECAY DIAGNOSIS [{classification.value}] ({strategy_id} vs {peer_benchmark_id}): "
            f"Target Sharpe = {target_sharpe:.2f}, Peer Sharpe = {peer_sharpe:.2f}, Relative Z-Score = {z_score:.2f}. Action = {action}"
        )

        logger.info(notes)

        return StrategyDecayDiagnosticsReport(
            strategy_id=strategy_id,
            peer_benchmark_id=peer_benchmark_id,
            classification=classification,
            target_sharpe=round(target_sharpe, 2),
            peer_benchmark_sharpe=round(peer_sharpe, 2),
            relative_excess_sharpe=round(current_diff, 2),
            relative_sharpe_z_score=round(z_score, 2),
            recommended_action=action,
            audit_notes=notes
        )

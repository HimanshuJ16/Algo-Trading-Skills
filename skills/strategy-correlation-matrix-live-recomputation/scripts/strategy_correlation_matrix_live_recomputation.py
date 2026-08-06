import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

@dataclass
class StrategyCorrelationMatrixLiveRecomputationConfig:
    """Legacy config container for backward compatibility."""
    enabled: bool = True

class StrategyCorrelationMatrixLiveRecomputation:
    """Legacy class retained for 100% backward compatibility."""
    def __init__(self, config: StrategyCorrelationMatrixLiveRecomputationConfig):
        self.config = config

    def execute(self) -> bool:
        return self.config.enabled

@dataclass
class StrategyCorrelationPair:
    strategy_a: str
    strategy_b: str
    correlation: float
    is_high_correlation_alert: bool

@dataclass
class LiveCorrelationMatrixReport:
    timestamp_iso: str
    strategy_names: List[str]
    correlation_matrix: List[List[float]]   # NxN symmetric matrix
    average_inter_strategy_correlation: float
    high_correlation_pairs: List[StrategyCorrelationPair]
    is_portfolio_diversification_compromised: bool
    audit_notes: str

class StrategyCorrelationMatrixEngine:
    """
    Production-grade Strategy Correlation Matrix Engine featuring live EWMA exponential decay,
    Ledoit-Wolf shrinkage, high correlation pair detection, and diversification breakdown alerting.
    """
    def __init__(
        self,
        ewma_span: int = 60,
        shrinkage_delta: float = 0.15,
        high_correlation_threshold: float = 0.70,
        max_avg_correlation_threshold: float = 0.55
    ):
        self.ewma_span = ewma_span
        self.shrinkage_delta = shrinkage_delta
        self.high_correlation_threshold = high_correlation_threshold
        self.max_avg_correlation_threshold = max_avg_correlation_threshold

    def compute_live_correlation_matrix(
        self,
        returns_df: pd.DataFrame,
        timestamp_iso: str = ""
    ) -> LiveCorrelationMatrixReport:
        """
        Recomputes live shrunken correlation matrix using EWMA and Ledoit-Wolf shrinkage across strategy return streams.
        """
        if returns_df.empty or returns_df.shape[1] < 2:
            raise ValueError("returns_df must contain at least 2 strategy return series.")

        strategy_names = list(returns_df.columns)
        n = len(strategy_names)

        # 1. EWMA Covariance Matrix
        ewma_cov = returns_df.ewm(span=self.ewma_span).cov().iloc[-n:].values

        # 2. Ledoit-Wolf Shrinkage: Shrink towards Identity Matrix I
        # Sigma_shrunken = delta * diag(ewma_cov) + (1 - delta) * ewma_cov
        target = np.diag(np.diag(ewma_cov))
        shrunken_cov = self.shrinkage_delta * target + (1.0 - self.shrinkage_delta) * ewma_cov

        # 3. Convert Covariance to Correlation Matrix
        std_devs = np.sqrt(np.maximum(1e-8, np.diag(shrunken_cov)))
        outer_std = np.outer(std_devs, std_devs)
        corr_matrix_np = shrunken_cov / outer_std

        # Force diagonal to 1.0 and clip to [-1.0, +1.0]
        np.fill_diagonal(corr_matrix_np, 1.0)
        corr_matrix_np = np.clip(corr_matrix_np, -1.0, 1.0)

        # 4. Pairwise Analysis & Breakdown Alerting
        high_corr_pairs: List[StrategyCorrelationPair] = []
        off_diag_corrs: List[float] = []

        for i in range(n):
            for j in range(i + 1, n):
                val = float(round(corr_matrix_np[i, j], 4))
                off_diag_corrs.append(val)

                if val >= self.high_correlation_threshold:
                    high_corr_pairs.append(StrategyCorrelationPair(
                        strategy_a=strategy_names[i],
                        strategy_b=strategy_names[j],
                        correlation=val,
                        is_high_correlation_alert=True
                    ))
                    logger.warning(f"HIGH CORRELATION ALERT: {strategy_names[i]} <-> {strategy_names[j]} = {val:.2f} >= {self.high_correlation_threshold}.")

        avg_inter_corr = float(round(np.mean(off_diag_corrs), 4)) if off_diag_corrs else 0.0
        is_compromised = (avg_inter_corr >= self.max_avg_correlation_threshold) or (len(high_corr_pairs) > 0)

        notes = (
            f"STRATEGY CORRELATION MATRIX [{timestamp_iso}]: Strategies = {n}, Avg Correlation = {avg_inter_corr:.3f}, "
            f"High Correlation Pairs = {len(high_corr_pairs)}, Diversification Compromised = {is_compromised}."
        )

        logger.info(notes)

        return LiveCorrelationMatrixReport(
            timestamp_iso=timestamp_iso,
            strategy_names=strategy_names,
            correlation_matrix=corr_matrix_np.tolist(),
            average_inter_strategy_correlation=avg_inter_corr,
            high_correlation_pairs=high_corr_pairs,
            is_portfolio_diversification_compromised=is_compromised,
            audit_notes=notes
        )

import numpy as np
import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class CorrelationPairBreach:
    strategy_a: str
    strategy_b: str
    correlation: float
    severity: str                       # 'HIGH_CORRELATION', 'REDUNDANT_POD'

@dataclass
class StrategyCorrelationReport:
    strategy_names: List[str]
    correlation_matrix: np.ndarray
    diversification_ratio: float
    high_correlation_breaches: List[CorrelationPairBreach]
    is_diversification_healthy: bool
    recommendations: List[str]

class CrossStrategyCorrelationMonitor:
    """
    Quantitative risk engine for evaluating rolling PnL correlations across strategy pods,
    detecting diversification breakdowns, and computing Portfolio Diversification Ratios.
    """
    def __init__(
        self,
        high_correlation_threshold: float = 0.70,
        redundancy_threshold: float = 0.85,
        min_diversification_ratio: float = 1.20
    ):
        self.high_correlation_threshold = high_correlation_threshold
        self.redundancy_threshold = redundancy_threshold
        self.min_diversification_ratio = min_diversification_ratio

    def compute_correlation_matrix(self, pnl_returns_matrix: np.ndarray) -> np.ndarray:
        """
        Computes Pearson PnL correlation matrix for NxM array (N timestamps, M strategies).
        """
        if pnl_returns_matrix.shape[0] < 2:
            raise ValueError("Insufficient timestamps to compute correlation matrix.")
        corr = np.corrcoef(pnl_returns_matrix, rowvar=False)
        return np.nan_to_num(corr, nan=0.0)

    def calculate_diversification_ratio(
        self,
        pnl_returns_matrix: np.ndarray,
        weights: np.ndarray
    ) -> float:
        """
        Calculates Portfolio Diversification Ratio (DR):
        DR = sum(w_i * sigma_i) / sqrt(w^T * Cov * w)
        """
        cov_matrix = np.cov(pnl_returns_matrix, rowvar=False)
        stdevs = np.std(pnl_returns_matrix, axis=0, ddof=1)
        
        weighted_vol_sum = float(np.sum(weights * stdevs))
        port_variance = float(np.dot(weights.T, np.dot(cov_matrix, weights)))
        
        if port_variance <= 1e-12:
            return 1.0

        port_vol = math.sqrt(max(1e-12, port_variance))
        return round(weighted_vol_sum / port_vol, 4)

    def analyze_strategy_correlations(
        self,
        strategy_names: List[str],
        pnl_returns_matrix: np.ndarray,
        weights: Optional[np.ndarray] = None
    ) -> StrategyCorrelationReport:
        """
        Analyzes cross-strategy PnL correlations and returns a full audit report.
        """
        m = len(strategy_names)
        if pnl_returns_matrix.shape[1] != m:
            raise ValueError("Strategy names count must match return matrix columns.")

        if weights is None:
            weights = np.ones(m) / float(m)
        else:
            weights = np.array(weights) / np.sum(weights)

        corr_matrix = self.compute_correlation_matrix(pnl_returns_matrix)
        cov_matrix = np.cov(pnl_returns_matrix, rowvar=False)
        stdevs = np.std(pnl_returns_matrix, axis=0, ddof=1)
        
        weighted_vol_sum = float(np.sum(weights * stdevs))
        port_variance = float(np.dot(weights.T, np.dot(cov_matrix, weights)))
        port_vol = math.sqrt(max(1e-12, port_variance))
        div_ratio = round(weighted_vol_sum / port_vol, 4)

        breaches: List[CorrelationPairBreach] = []
        recommendations: List[str] = []

        for i in range(m):
            for j in range(i + 1, m):
                rho = float(corr_matrix[i, j])
                s1, s2 = strategy_names[i], strategy_names[j]

                if rho >= self.redundancy_threshold:
                    breaches.append(CorrelationPairBreach(s1, s2, round(rho, 4), "REDUNDANT_POD"))
                    rec = f"REDUNDANT PODS ({s1} & {s2}): PnL correlation = {rho:.2f} >= {self.redundancy_threshold}. Re-allocate capital!"
                    recommendations.append(rec)
                    logger.critical(rec)
                elif rho >= self.high_correlation_threshold:
                    breaches.append(CorrelationPairBreach(s1, s2, round(rho, 4), "HIGH_CORRELATION"))
                    rec = f"HIGH CORRELATION BREACH ({s1} & {s2}): PnL correlation = {rho:.2f} >= {self.high_correlation_threshold}."
                    recommendations.append(rec)
                    logger.warning(rec)

        is_healthy = (div_ratio >= self.min_diversification_ratio) and (len(breaches) == 0)
        if not is_healthy and not recommendations:
            recommendations.append(f"Diversification Ratio {div_ratio:.2f} is below minimum target {self.min_diversification_ratio}.")

        return StrategyCorrelationReport(
            strategy_names=strategy_names,
            correlation_matrix=np.round(corr_matrix, 4),
            diversification_ratio=div_ratio,
            high_correlation_breaches=breaches,
            is_diversification_healthy=is_healthy,
            recommendations=recommendations
        )

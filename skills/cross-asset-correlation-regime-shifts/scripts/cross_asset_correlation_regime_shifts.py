import numpy as np
import logging
from dataclasses import dataclass
from typing import List, Dict, Tuple

logger = logging.getLogger(__name__)

@dataclass
class CorrelationRegimeReport:
    frobenius_distance: float
    short_term_avg_correlation: float
    baseline_avg_correlation: float
    regime: str                         # 'STABLE_NORMAL', 'CORRELATION_SHIFT', 'CRISIS_CONVERGENCE'
    recommended_leverage_multiplier: float
    warning_message: str

class CrossAssetCorrelationRegimeDetector:
    """
    Quantitative macro engine for detecting cross-asset correlation matrix structural shifts
    using normalized Frobenius Matrix Distance and triggering portfolio de-leveraging.
    """
    def __init__(
        self,
        shift_threshold: float = 0.30,
        crisis_threshold: float = 0.60,
        high_corr_threshold: float = 0.65
    ):
        self.shift_threshold = shift_threshold
        self.crisis_threshold = crisis_threshold
        self.high_corr_threshold = high_corr_threshold

    def compute_correlation_matrix(self, returns_matrix: np.ndarray) -> np.ndarray:
        """
        Computes Pearson correlation matrix for NxK returns array (N timestamps, K assets).
        """
        if returns_matrix.shape[0] < 2:
            raise ValueError("Insufficient rows to calculate correlation matrix.")
        corr = np.corrcoef(returns_matrix, rowvar=False)
        return np.nan_to_num(corr, nan=0.0)

    def calculate_frobenius_distance(self, corr1: np.ndarray, corr2: np.ndarray) -> float:
        """
        Calculates normalized Frobenius Norm Distance between two correlation matrices: ||C1 - C2||_F / K
        """
        if corr1.shape != corr2.shape:
            raise ValueError("Correlation matrices must have identical dimensions.")
        k = corr1.shape[0]
        diff = corr1 - corr2
        raw_norm = float(np.linalg.norm(diff, ord='fro'))
        return float(raw_norm / max(1.0, float(k)))

    def calculate_average_off_diagonal_correlation(self, corr: np.ndarray) -> float:
        """
        Computes average off-diagonal correlation.
        """
        k = corr.shape[0]
        if k <= 1:
            return 0.0
        mask = ~np.eye(k, dtype=bool)
        return float(np.mean(corr[mask]))

    def detect_regime(
        self,
        short_term_returns: np.ndarray,
        baseline_returns: np.ndarray
    ) -> CorrelationRegimeReport:
        """
        Analyzes short-term vs baseline return series to classify correlation regime.
        """
        c_short = self.compute_correlation_matrix(short_term_returns)
        c_base = self.compute_correlation_matrix(baseline_returns)

        f_dist = self.calculate_frobenius_distance(c_short, c_base)
        avg_corr_short = self.calculate_average_off_diagonal_correlation(c_short)
        avg_corr_base = self.calculate_average_off_diagonal_correlation(c_base)

        # Classification logic
        if f_dist >= self.crisis_threshold or avg_corr_short >= self.high_corr_threshold:
            regime = "CRISIS_CONVERGENCE"
            multiplier = 0.50          # De-leverage portfolio by 50%
            msg = (
                f"CRITICAL REGIME SHIFT ({regime}): Normalized Frobenius Distance = {f_dist:.2f}, "
                f"Avg Short Corr = {avg_corr_short:.2f}. Cross-asset diversification has collapsed! Downsizing target leverage."
            )
            logger.critical(msg)
        elif f_dist >= self.shift_threshold:
            regime = "CORRELATION_SHIFT"
            multiplier = 0.80          # Downsize leverage by 20%
            msg = f"WARNING REGIME SHIFT ({regime}): Normalized Frobenius Distance = {f_dist:.2f}. Cross-asset correlations shifting."
            logger.warning(msg)
        else:
            regime = "STABLE_NORMAL"
            multiplier = 1.00          # Normal leverage
            msg = f"Regime stable ({regime}): Normalized Frobenius Distance = {f_dist:.2f} within normal bounds."

        return CorrelationRegimeReport(
            frobenius_distance=round(f_dist, 4),
            short_term_avg_correlation=round(avg_corr_short, 4),
            baseline_avg_correlation=round(avg_corr_base, 4),
            regime=regime,
            recommended_leverage_multiplier=multiplier,
            warning_message=msg
        )

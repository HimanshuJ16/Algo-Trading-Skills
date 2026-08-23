"""Cross-strategy PnL correlation monitoring for multi-strategy (pod) portfolios.

Computes pairwise Pearson PnL correlations across strategy pods, flags
diversification breakdown, and computes the Portfolio Diversification Ratio

    DR(P) = sum_i w_i * sigma_i / sqrt(w' Sigma w)

as defined in Choueifaty & Coignard, "Toward Maximum Diversification",
Journal of Portfolio Management 35(1), 2008, pp. 40-51.

Design decisions that matter for a risk monitor:

* Degenerate inputs raise instead of being imputed. A stale (zero-variance)
  pod has an *undefined* correlation to everything; substituting 0.0 makes a
  dead feed look like a perfectly diversifying strategy and inflates DR.
* Breach detection is one-sided (rho >= threshold) by design: negative PnL
  correlation is a diversification benefit, not a breach. Strongly negative
  pairs are a capital-efficiency question, not a concentration risk, and are
  out of scope here.
* The Diversification Ratio is only interpretable for non-negative weights;
  DR >= 1 holds by the triangle inequality only when every w_i >= 0.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# Below 3 observations every off-diagonal Pearson correlation is algebraically
# +/-1 regardless of the data, so no configuration may go under this floor.
MIN_OBSERVATIONS_FLOOR = 3


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
    observations_used: int = 0
    weights: np.ndarray = field(default_factory=lambda: np.empty(0))


class CrossStrategyCorrelationMonitor:
    """
    Quantitative risk engine for evaluating rolling PnL correlations across strategy pods,
    detecting diversification breakdowns, and computing Portfolio Diversification Ratios.

    Args:
        high_correlation_threshold: rho at or above which a pair is flagged
            ``HIGH_CORRELATION``. Policy default, not a calibrated constant.
        redundancy_threshold: rho at or above which a pair is flagged
            ``REDUNDANT_POD``. Must be >= ``high_correlation_threshold``.
        min_diversification_ratio: DR floor below which the portfolio is reported
            unhealthy. Policy default.
        min_observations: minimum rows required to estimate a correlation. The
            sampling error of a Pearson estimate is ~1/sqrt(N-3) on the Fisher-z
            scale, so short windows manufacture breaches; calibrate this to your
            own tolerance rather than relying on the hard floor of 3.
        lookback_window: if set, only the trailing ``lookback_window`` rows of
            each input matrix are used (the rolling window W). ``None`` uses the
            full matrix supplied by the caller.
    """

    def __init__(
        self,
        high_correlation_threshold: float = 0.70,
        redundancy_threshold: float = 0.85,
        min_diversification_ratio: float = 1.20,
        min_observations: int = 30,
        lookback_window: Optional[int] = None,
    ) -> None:
        for label, value in (
            ("high_correlation_threshold", high_correlation_threshold),
            ("redundancy_threshold", redundancy_threshold),
        ):
            if not math.isfinite(value) or not -1.0 <= value <= 1.0:
                raise ValueError(f"{label} must be a finite correlation in [-1, 1], got {value!r}.")
        if redundancy_threshold < high_correlation_threshold:
            raise ValueError(
                "redundancy_threshold must be >= high_correlation_threshold "
                f"({redundancy_threshold} < {high_correlation_threshold})."
            )
        if not math.isfinite(min_diversification_ratio) or min_diversification_ratio < 1.0:
            raise ValueError(
                "min_diversification_ratio must be finite and >= 1.0 (DR >= 1 for any "
                f"long-only portfolio), got {min_diversification_ratio!r}."
            )
        if min_observations < MIN_OBSERVATIONS_FLOOR:
            raise ValueError(
                f"min_observations must be >= {MIN_OBSERVATIONS_FLOOR}; below that every "
                "off-diagonal correlation is algebraically +/-1."
            )
        if lookback_window is not None and lookback_window < min_observations:
            raise ValueError(
                f"lookback_window ({lookback_window}) must be >= min_observations "
                f"({min_observations})."
            )

        self.high_correlation_threshold = high_correlation_threshold
        self.redundancy_threshold = redundancy_threshold
        self.min_diversification_ratio = min_diversification_ratio
        self.min_observations = min_observations
        self.lookback_window = lookback_window

    # ------------------------------------------------------------------ #
    # Input validation
    # ------------------------------------------------------------------ #
    def _prepare_returns(self, pnl_returns_matrix: np.ndarray) -> np.ndarray:
        """
        Validates the PnL return matrix and applies the rolling window.

        Raises ValueError on anything that would fabricate correlation structure
        rather than measure it: wrong shape, non-finite values, too few
        observations, or a zero-variance (stale/flat/idle) strategy column.
        """
        matrix = np.asarray(pnl_returns_matrix, dtype=float)
        if matrix.ndim != 2:
            raise ValueError(f"pnl_returns_matrix must be 2-D (N timestamps x M strategies), got shape {matrix.shape}.")
        if matrix.shape[1] < 2:
            raise ValueError("At least 2 strategy columns are required to compute cross-strategy correlations.")
        if not np.all(np.isfinite(matrix)):
            raise ValueError(
                "pnl_returns_matrix contains NaN/Inf. Repair or drop the affected "
                "observations upstream; imputing them fabricates correlation structure."
            )

        if self.lookback_window is not None:
            matrix = matrix[-self.lookback_window:, :]

        if matrix.shape[0] < self.min_observations:
            raise ValueError(
                f"Insufficient observations: {matrix.shape[0]} rows available, "
                f"min_observations={self.min_observations}."
            )

        stdevs = np.std(matrix, axis=0, ddof=1)
        flat = np.flatnonzero(stdevs <= 0.0)
        if flat.size:
            raise ValueError(
                f"Zero-variance PnL column(s) at index {flat.tolist()}: correlation is undefined "
                "for a flat/stale/idle pod. Exclude the pod or repair the feed upstream."
            )
        return matrix

    @staticmethod
    def _normalize_weights(weights: Optional[Sequence[float]], m: int) -> np.ndarray:
        """
        Validates and normalizes capital allocation weights to sum to 1.

        Weights must be finite and non-negative: the Diversification Ratio is a
        long-only construct and DR >= 1 does not hold once a weight is negative.
        """
        if weights is None:
            return np.full(m, 1.0 / m, dtype=float)

        w = np.asarray(weights, dtype=float).reshape(-1)
        if w.shape[0] != m:
            raise ValueError(f"weights length ({w.shape[0]}) must match strategy count ({m}).")
        if not np.all(np.isfinite(w)):
            raise ValueError("weights contain NaN/Inf.")
        if np.any(w < 0.0):
            raise ValueError(
                "Negative weights are not supported: the Diversification Ratio is defined "
                "for long-only allocations and DR >= 1 does not hold otherwise."
            )
        total = float(np.sum(w))
        if total <= 0.0:
            raise ValueError("weights must sum to a positive value.")
        return w / total

    # ------------------------------------------------------------------ #
    # Estimators
    # ------------------------------------------------------------------ #
    def compute_correlation_matrix(self, pnl_returns_matrix: np.ndarray) -> np.ndarray:
        """
        Computes the Pearson PnL correlation matrix for an NxM array
        (N timestamps, M strategies), after validation and windowing.
        """
        matrix = self._prepare_returns(pnl_returns_matrix)
        return self._correlation_from_validated(matrix)

    @staticmethod
    def _correlation_from_validated(matrix: np.ndarray) -> np.ndarray:
        corr = np.corrcoef(matrix, rowvar=False)
        if not np.all(np.isfinite(corr)):
            # Unreachable given _prepare_returns, but a risk engine must never
            # emit a silently zero-filled correlation matrix.
            raise ValueError("Correlation matrix contains non-finite entries; inputs are degenerate.")
        # Guard against float error pushing |rho| a few ulps outside [-1, 1].
        return np.clip(corr, -1.0, 1.0)

    def calculate_diversification_ratio(
        self,
        pnl_returns_matrix: np.ndarray,
        weights: Optional[Sequence[float]] = None,
    ) -> float:
        """
        Calculates the Portfolio Diversification Ratio (Choueifaty & Coignard, 2008):

            DR = sum(w_i * sigma_i) / sqrt(w' * Cov * w)

        DR = 1.0 means no diversification benefit; DR >= 1 always holds for
        non-negative weights. Returns ``math.inf`` for a perfectly hedged
        portfolio (portfolio variance exactly zero), which is the limit of the
        ratio rather than an error.
        """
        matrix = self._prepare_returns(pnl_returns_matrix)
        w = self._normalize_weights(weights, matrix.shape[1])
        return self._diversification_ratio_from_validated(matrix, w)

    @staticmethod
    def _diversification_ratio_from_validated(matrix: np.ndarray, weights: np.ndarray) -> float:
        cov_matrix = np.cov(matrix, rowvar=False)
        stdevs = np.std(matrix, axis=0, ddof=1)

        weighted_vol_sum = float(np.sum(weights * stdevs))
        port_variance = float(weights @ cov_matrix @ weights)

        if port_variance <= 0.0:
            logger.warning(
                "Portfolio variance is non-positive (%.3e) at the current weights: the pods "
                "offset exactly. Reporting DR=inf; verify the PnL series are genuine and "
                "independent before acting on it.",
                port_variance,
            )
            return math.inf

        port_vol = math.sqrt(port_variance)
        return round(weighted_vol_sum / port_vol, 4)

    # ------------------------------------------------------------------ #
    # Reporting
    # ------------------------------------------------------------------ #
    def analyze_strategy_correlations(
        self,
        strategy_names: List[str],
        pnl_returns_matrix: np.ndarray,
        weights: Optional[Sequence[float]] = None,
    ) -> StrategyCorrelationReport:
        """
        Analyzes cross-strategy PnL correlations and returns a full audit report.

        Every breach and every DR shortfall is surfaced in ``recommendations``;
        the two conditions are reported independently.
        """
        m = len(strategy_names)
        if len(set(strategy_names)) != m:
            raise ValueError("strategy_names must be unique.")

        matrix = self._prepare_returns(pnl_returns_matrix)
        if matrix.shape[1] != m:
            raise ValueError(
                f"Strategy names count ({m}) must match return matrix columns ({matrix.shape[1]})."
            )
        w = self._normalize_weights(weights, m)

        corr_matrix = self._correlation_from_validated(matrix)
        div_ratio = self._diversification_ratio_from_validated(matrix, w)

        breaches: List[CorrelationPairBreach] = []
        recommendations: List[str] = []

        for i in range(m):
            for j in range(i + 1, m):
                rho = float(corr_matrix[i, j])
                s1, s2 = strategy_names[i], strategy_names[j]

                if rho >= self.redundancy_threshold:
                    breaches.append(CorrelationPairBreach(s1, s2, round(rho, 4), "REDUNDANT_POD"))
                    rec = (
                        f"REDUNDANT PODS ({s1} & {s2}): PnL correlation = {rho:.2f} >= "
                        f"{self.redundancy_threshold}. Re-allocate capital!"
                    )
                    recommendations.append(rec)
                    logger.critical(rec)
                elif rho >= self.high_correlation_threshold:
                    breaches.append(CorrelationPairBreach(s1, s2, round(rho, 4), "HIGH_CORRELATION"))
                    rec = (
                        f"HIGH CORRELATION BREACH ({s1} & {s2}): PnL correlation = {rho:.2f} >= "
                        f"{self.high_correlation_threshold}."
                    )
                    recommendations.append(rec)
                    logger.warning(rec)

        dr_breached = div_ratio < self.min_diversification_ratio
        if dr_breached:
            rec = (
                f"Diversification Ratio {div_ratio:.2f} is below minimum target "
                f"{self.min_diversification_ratio}."
            )
            recommendations.append(rec)
            logger.warning(rec)

        is_healthy = (not dr_breached) and (len(breaches) == 0)

        return StrategyCorrelationReport(
            strategy_names=list(strategy_names),
            correlation_matrix=np.round(corr_matrix, 4),
            diversification_ratio=div_ratio,
            high_correlation_breaches=breaches,
            is_diversification_healthy=is_healthy,
            recommendations=recommendations,
            observations_used=int(matrix.shape[0]),
            weights=w,
        )

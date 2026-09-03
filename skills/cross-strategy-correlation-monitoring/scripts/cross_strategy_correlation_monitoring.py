"""Cross-strategy PnL correlation monitoring for multi-strategy (pod) portfolios.

Computes pairwise Pearson PnL correlations across strategy pods -- either
equally weighted over an explicit rolling window or exponentially weighted
(EWMA) toward the latest observation -- flags diversification breakdown, and
computes the Portfolio Diversification Ratio

    DR(P) = sum_i w_i * sigma_i / sqrt(w' Sigma w)

as defined in Choueifaty & Coignard, "Toward Maximum Diversification",
Journal of Portfolio Management 35(1), 2008, pp. 40-51.

Two breakdown surfaces are reported independently:

* pairwise breaches (``HIGH_CORRELATION`` / ``REDUNDANT_POD``),
* portfolio-level breakdown, which is the OR of a Diversification Ratio
  shortfall and an average off-diagonal correlation at or above its threshold.

Design decisions that matter for a risk monitor:

* Degenerate inputs raise instead of being imputed. A stale (zero-variance)
  pod has an *undefined* correlation to everything; substituting 0.0 makes a
  dead feed look like a perfectly diversifying strategy, inflates DR and drags
  the average correlation below its breakdown threshold, masking a real breach
  on the pods that *are* live.
* Breach detection is one-sided (rho >= threshold) by design: negative PnL
  correlation is a diversification benefit, not a breach. Strongly negative
  pairs are a capital-efficiency question, not a concentration risk, and are
  out of scope here.
* The Diversification Ratio is only interpretable for non-negative weights;
  DR >= 1 holds by the triangle inequality only when every w_i >= 0.
* **Alerting runs on the unshrunk correlation estimate.** Linear shrinkage
  toward a diagonal target multiplies every off-diagonal correlation by exactly
  ``(1 - delta)``. Applying a 0.70 threshold to the *shrunken* correlations
  would only fire at a true rho of 0.70 / (1 - delta) -- 0.824 at delta = 0.15
  -- silently under-reporting the breakdown this engine exists to catch. The
  shrunken matrix is emitted as a covariance, where shrinkage belongs.

Estimator references:

* EWMA weights follow the pandas ``ewm(span=..., adjust=True)`` convention,
  ``alpha = 2 / (span + 1)`` with ``w_i ~ (1 - alpha)^i``, normalized to sum to
  1, and the covariance is the debiased weighted second moment at the latest
  observation.
* Linear shrinkage of a sample covariance matrix toward a structured target,
  ``Sigma_hat = delta * F + (1 - delta) * S``, is the shrinkage family of
  O. Ledoit and M. Wolf (single-index, Journal of Empirical Finance 10, 2003,
  pp. 603-621; constant-correlation, Journal of Portfolio Management 30(4),
  2004, pp. 110-119; scaled identity, Journal of Multivariate Analysis 88(2),
  2004, pp. 365-411).
* The target used here is ``F = diag(S)`` -- the diagonal, unequal-variance
  target, "Target D" of J. Schaefer and K. Strimmer, Statistical Applications
  in Genetics and Molecular Biology 4(1), 2005, Article 32.
* This engine uses a **fixed, configured** shrinkage intensity. The defining
  contribution of both Ledoit-Wolf and Schaefer-Strimmer is an intensity
  *estimated from the data*; a constant delta is not their estimator and
  carries none of its optimality guarantees.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Below 3 observations every off-diagonal Pearson correlation is algebraically
# +/-1 regardless of the data, so no configuration may go under this floor.
MIN_OBSERVATIONS_FLOOR = 3

# span = 1 implies alpha = 1: all weight lands on the newest observation and the
# debiased covariance is 0/0. pandas returns an all-NaN matrix there; this
# engine rejects the span instead.
MIN_EWMA_SPAN = 2


@dataclass
class CorrelationPairBreach:
    strategy_a: str
    strategy_b: str
    correlation: float
    severity: str                       # 'HIGH_CORRELATION', 'REDUNDANT_POD'


@dataclass
class StrategyCorrelationReport:
    """Result of one correlation evaluation.

    Attributes:
        correlation_matrix: MxM symmetric **unshrunk** correlation estimate.
            This is the matrix the thresholds are applied to and the one to read
            as "how correlated are these pods right now".
        diversification_ratio: Choueifaty-Coignard DR at the supplied weights;
            ``math.inf`` for a perfectly offsetting portfolio.
        average_inter_strategy_correlation: mean of the M(M-1)/2 unique
            off-diagonal entries. Signed, so a +0.9 pair and a -0.9 pair average
            to zero -- always read ``high_correlation_breaches`` alongside it.
        observations_used: rows actually consumed after windowing.
        effective_observations: honest sample size behind the estimate. Equal to
            ``observations_used`` under equal weighting; the Kish effective
            sample size ``1 / sum(w^2)`` under EWMA weighting, which converges to
            ``ewma_span`` as history grows -- 10,000 rows at ``ewma_span=60`` is
            a 60-observation estimator, not a 10,000-observation one.
        shrunk_covariance_matrix: ``delta * diag(S) + (1 - delta) * S``, positive
            definite for ``delta > 0`` and positive variances. Supply this to a
            mean-variance optimizer that must invert a covariance matrix. Its
            implied correlations are ``(1 - delta)`` times ``correlation_matrix``
            off the diagonal -- never compare them against a correlation
            threshold.
    """
    strategy_names: List[str]
    correlation_matrix: np.ndarray
    diversification_ratio: float
    high_correlation_breaches: List[CorrelationPairBreach]
    is_diversification_healthy: bool
    recommendations: List[str]
    observations_used: int = 0
    weights: np.ndarray = field(default_factory=lambda: np.empty(0))
    average_inter_strategy_correlation: float = 0.0
    effective_observations: float = 0.0
    shrunk_covariance_matrix: np.ndarray = field(default_factory=lambda: np.empty((0, 0)))
    shrinkage_delta: float = 0.0
    ewma_span: Optional[int] = None


class CrossStrategyCorrelationMonitor:
    """
    Quantitative risk engine for evaluating PnL correlations across strategy pods,
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
        ewma_span: ``None`` (default) weights every observation in the window
            equally -- the explicit rolling-window estimate. An integer >= 2
            switches to EWMA weighting with ``alpha = 2 / (span + 1)``, which
            reacts to a live convergence within a handful of observations but
            caps the effective sample size at roughly the span no matter how deep
            the history is. It is a statistical-power knob, not a smoothing
            preference.
        shrinkage_delta: fixed weight on the diagonal target in
            ``delta * diag(S) + (1 - delta) * S``, in [0, 1]. Applied to the
            emitted covariance only; it never moves the reported correlations,
            the Diversification Ratio or the alert thresholds.
        max_avg_correlation_threshold: average off-diagonal rho at or above which
            the portfolio is flagged as diversification-compromised, independently
            of the DR floor. Policy default.
    """

    def __init__(
        self,
        high_correlation_threshold: float = 0.70,
        redundancy_threshold: float = 0.85,
        min_diversification_ratio: float = 1.20,
        min_observations: int = 30,
        lookback_window: Optional[int] = None,
        ewma_span: Optional[int] = None,
        shrinkage_delta: float = 0.0,
        max_avg_correlation_threshold: float = 0.55,
    ) -> None:
        for label, value in (
            ("high_correlation_threshold", high_correlation_threshold),
            ("redundancy_threshold", redundancy_threshold),
            ("max_avg_correlation_threshold", max_avg_correlation_threshold),
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
        if ewma_span is not None:
            if isinstance(ewma_span, bool) or not isinstance(ewma_span, (int, np.integer)):
                raise ValueError(f"ewma_span must be an integer or None, got {ewma_span!r}.")
            if ewma_span < MIN_EWMA_SPAN:
                raise ValueError(
                    f"ewma_span must be >= {MIN_EWMA_SPAN} (span=1 puts all weight on the "
                    f"newest observation, leaving the covariance undefined), got {ewma_span!r}."
                )
        if not math.isfinite(shrinkage_delta) or not 0.0 <= shrinkage_delta <= 1.0:
            raise ValueError(
                f"shrinkage_delta must be a finite value in [0, 1], got {shrinkage_delta!r}."
            )

        self.high_correlation_threshold = high_correlation_threshold
        self.redundancy_threshold = redundancy_threshold
        self.min_diversification_ratio = min_diversification_ratio
        self.min_observations = min_observations
        self.lookback_window = lookback_window
        self.ewma_span = None if ewma_span is None else int(ewma_span)
        self.shrinkage_delta = float(shrinkage_delta)
        self.max_avg_correlation_threshold = max_avg_correlation_threshold

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
    def _ewma_weights(self, n_obs: int) -> np.ndarray:
        """Normalized pandas-convention EWMA weights, newest observation last."""
        alpha = 2.0 / (self.ewma_span + 1.0)
        weights = (1.0 - alpha) ** np.arange(n_obs - 1, -1, -1, dtype=float)
        return weights / weights.sum()

    def _moments(self, matrix: np.ndarray) -> Tuple[np.ndarray, np.ndarray, float]:
        """Returns (covariance, stdevs, effective_observations) for a validated window.

        With ``ewma_span=None`` this is the ordinary sample covariance (ddof=1)
        and the effective sample size is just the row count. With an EWMA span it
        is the debiased weighted covariance at the latest observation,

            S = 1/(1 - sum_t w_t^2) * sum_t w_t (x_t - mu_w)(x_t - mu_w)'

        computed as a single weighted sum of outer products -- positive
        semi-definite by construction and numerically identical to pandas'
        ``ewm(span=...).cov()`` without evaluating every intermediate timestamp.
        """
        n_obs = matrix.shape[0]
        if self.ewma_span is None:
            cov = np.atleast_2d(np.cov(matrix, rowvar=False))
            effective_obs = float(n_obs)
        else:
            weights = self._ewma_weights(n_obs)
            deviations = matrix - (weights @ matrix)
            biased_cov = (deviations * weights[:, None]).T @ deviations
            sum_sq_weights = float(np.sum(weights ** 2))
            debias = 1.0 - sum_sq_weights
            if debias <= 0.0:  # unreachable for span >= 2 and n_obs >= 3; guarded, not assumed
                raise ValueError(
                    "EWMA weights are too concentrated to debias (sum of squared weights = "
                    f"{sum_sq_weights:.6f}); increase ewma_span."
                )
            cov = biased_cov / debias
            cov = 0.5 * (cov + cov.T)  # kill floating-point asymmetry
            effective_obs = 1.0 / sum_sq_weights

        variances = np.diag(cov)
        if not np.all(np.isfinite(variances)) or np.any(variances <= 0.0):
            degenerate = [int(i) for i in np.flatnonzero(~(variances > 0.0))]
            raise ValueError(
                f"Estimated variance is non-positive or non-finite for column(s) {degenerate}: "
                f"these pods are flat over the effective window (~{effective_obs:.0f} "
                "observations). Exclude them rather than reporting rho = 0."
            )
        return cov, np.sqrt(variances), effective_obs

    def compute_correlation_matrix(self, pnl_returns_matrix: np.ndarray) -> np.ndarray:
        """
        Computes the (unshrunk) PnL correlation matrix for an NxM array
        (N timestamps, M strategies), after validation and windowing.
        """
        matrix = self._prepare_returns(pnl_returns_matrix)
        cov, stdevs, _ = self._moments(matrix)
        return self._correlation_from_cov(cov, stdevs)

    @staticmethod
    def _correlation_from_cov(cov: np.ndarray, stdevs: np.ndarray) -> np.ndarray:
        corr = cov / np.outer(stdevs, stdevs)
        np.fill_diagonal(corr, 1.0)
        if not np.all(np.isfinite(corr)):
            # Unreachable given _prepare_returns/_moments, but a risk engine must
            # never emit a silently zero-filled correlation matrix.
            raise ValueError("Correlation matrix contains non-finite entries; inputs are degenerate.")
        # Guard against float error pushing |rho| a few ulps outside [-1, 1].
        return np.clip(corr, -1.0, 1.0)

    def _shrunk_covariance(self, cov: np.ndarray) -> np.ndarray:
        """Fixed-intensity linear shrinkage toward the diagonal (zero-correlation) target.

        ``Sigma_hat = delta * diag(S) + (1 - delta) * S`` is positive definite for
        ``delta > 0``, so it stays invertible even when the pod count exceeds the
        observation count and S is singular. It is emitted as a covariance only:
        it deflates every off-diagonal correlation by exactly ``(1 - delta)``, so
        thresholds must never be applied to it.
        """
        return (
            self.shrinkage_delta * np.diag(np.diag(cov))
            + (1.0 - self.shrinkage_delta) * cov
        )

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
        ratio rather than an error. Uses the same (equal- or EWMA-weighted)
        covariance estimate as the reported correlations.
        """
        matrix = self._prepare_returns(pnl_returns_matrix)
        w = self._normalize_weights(weights, matrix.shape[1])
        cov, stdevs, _ = self._moments(matrix)
        return self._diversification_ratio_from_moments(cov, stdevs, w)

    @staticmethod
    def _diversification_ratio_from_moments(
        cov_matrix: np.ndarray, stdevs: np.ndarray, weights: np.ndarray
    ) -> float:
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

        Every pair breach, every DR shortfall and every average-correlation breach
        is surfaced in ``recommendations``; the three conditions are reported
        independently so none can mask another.
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

        cov, stdevs, effective_obs = self._moments(matrix)
        corr_matrix = self._correlation_from_cov(cov, stdevs)
        div_ratio = self._diversification_ratio_from_moments(cov, stdevs, w)

        if effective_obs < self.min_observations - 1e-9:
            logger.warning(
                "Effective sample size is %.1f (ewma_span=%s) against min_observations=%d: "
                "the estimate is weaker than the %d supplied rows suggest.",
                effective_obs, self.ewma_span, self.min_observations, matrix.shape[0],
            )

        breaches: List[CorrelationPairBreach] = []
        recommendations: List[str] = []
        off_diagonal: List[float] = []

        for i in range(m):
            for j in range(i + 1, m):
                # Compare unrounded, report rounded: rounding first would move the
                # effective threshold to 0.69995.
                rho = float(corr_matrix[i, j])
                off_diagonal.append(rho)
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

        # Signed average of the M(M-1)/2 unique off-diagonal entries: a +0.9 pair
        # and a -0.9 pair net to zero, which is why it is reported and alerted on
        # alongside, never instead of, the pair breaches.
        avg_corr = float(round(float(np.mean(off_diagonal)), 4))
        avg_breached = avg_corr >= self.max_avg_correlation_threshold
        if avg_breached:
            rec = (
                f"AVERAGE CORRELATION BREACH: mean pairwise PnL correlation {avg_corr:.2f} >= "
                f"{self.max_avg_correlation_threshold}; the book is uniformly converged."
            )
            recommendations.append(rec)
            logger.warning(rec)

        is_healthy = (not dr_breached) and (not avg_breached) and (len(breaches) == 0)

        return StrategyCorrelationReport(
            strategy_names=list(strategy_names),
            correlation_matrix=np.round(corr_matrix, 4),
            diversification_ratio=div_ratio,
            high_correlation_breaches=breaches,
            is_diversification_healthy=is_healthy,
            recommendations=recommendations,
            observations_used=int(matrix.shape[0]),
            weights=w,
            average_inter_strategy_correlation=avg_corr,
            effective_observations=round(effective_obs, 4),
            shrunk_covariance_matrix=self._shrunk_covariance(cov),
            shrinkage_delta=self.shrinkage_delta,
            ewma_span=self.ewma_span,
        )

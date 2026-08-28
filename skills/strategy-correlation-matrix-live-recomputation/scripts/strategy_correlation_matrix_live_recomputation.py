"""Live inter-strategy correlation matrix recomputation for multi-strategy portfolios.

Estimates the current correlation structure across strategy return streams with an
exponentially weighted (EWMA) covariance estimator, flags pairs whose correlation has
converged, and reports whether portfolio diversification is compromised. It also emits a
shrunken, well-conditioned covariance matrix for downstream optimizers.

Design decisions that matter for a risk monitor:

* **Alerting runs on the unshrunk EWMA correlation estimate.** Linear shrinkage toward a
  diagonal target multiplies every off-diagonal correlation by exactly ``(1 - delta)``.
  Applying a 0.70 threshold to the *shrunken* correlations would therefore only fire at a
  true rho of 0.70 / (1 - delta) -- 0.824 at the default delta = 0.15 -- silently
  under-reporting exactly the diversification breakdown this engine exists to catch. The
  shrunken matrix is still returned, as a covariance, where shrinkage genuinely belongs.
* **Degenerate inputs raise instead of being imputed.** A flat (zero-variance) strategy has
  an *undefined* correlation to everything; reporting 0.0 turns a dead or idle strategy into
  the portfolio's best diversifier and drags the average correlation below its threshold,
  masking a real breakdown elsewhere in the book.
* **Pair alerting is one-sided (rho >= threshold).** Negative correlation is a
  diversification benefit, not a concentration risk.

Estimator references:

* Linear shrinkage of a sample covariance matrix toward a structured target,
  ``Sigma_hat = delta * F + (1 - delta) * S``, is the shrinkage family of
  O. Ledoit and M. Wolf. Their targets are the single-index matrix (Journal of Empirical
  Finance 10, 2003, pp. 603-621), the constant-correlation matrix ("Honey, I Shrunk the
  Sample Covariance Matrix", Journal of Portfolio Management 30(4), 2004, pp. 110-119) and
  the scaled identity (Journal of Multivariate Analysis 88(2), 2004, pp. 365-411).
* The target used here is ``F = diag(S)`` -- the diagonal, unequal-variance target, "Target
  D" of J. Schaefer and K. Strimmer, Statistical Applications in Genetics and Molecular
  Biology 4(1), 2005, Article 32.
* This engine uses a **fixed, configured** shrinkage intensity. The defining contribution of
  both Ledoit-Wolf and Schaefer-Strimmer is an intensity *estimated from the data*; a
  constant delta is not their estimator and carries none of its optimality guarantees.
"""

import logging
import math
from dataclasses import dataclass, field
from typing import List, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# With 2 observations every column's deviation-from-mean vector is collinear, so every
# off-diagonal correlation is algebraically +/-1 regardless of the data. No configuration
# may go below this floor.
MIN_OBSERVATIONS_FLOOR = 3

# span = 1 implies alpha = 1: all weight lands on the newest observation and the debiased
# covariance is 0/0. pandas returns an all-NaN matrix there; this engine rejects it.
MIN_EWMA_SPAN = 2


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
    """Result of one live recomputation.

    Attributes:
        correlation_matrix: NxN symmetric **EWMA correlation estimate**, unshrunk. This is
            the matrix the thresholds are applied to and the one to read as "how correlated
            are these strategies right now".
        shrunk_covariance_matrix: NxN ``delta * diag(S) + (1 - delta) * S``, positive
            definite for ``delta > 0`` and positive variances. Supply this to a mean-variance
            optimizer that must invert a covariance matrix. Its implied correlations are
            ``(1 - delta)`` times ``correlation_matrix`` off the diagonal -- do not compare
            them against a correlation threshold.
        observations_used: rows actually consumed from ``returns_df``.
        effective_observations: Kish effective sample size ``1 / sum(w^2)`` of the EWMA
            weights. It converges to ``ewma_span`` as history grows, and is the honest
            sample size behind the estimate -- 10,000 rows at ``ewma_span=60`` is a
            60-observation estimator, not a 10,000-observation one.
    """
    timestamp_iso: str
    strategy_names: List[str]
    correlation_matrix: List[List[float]]   # NxN symmetric matrix
    average_inter_strategy_correlation: float
    high_correlation_pairs: List[StrategyCorrelationPair]
    is_portfolio_diversification_compromised: bool
    audit_notes: str
    shrunk_covariance_matrix: List[List[float]] = field(default_factory=list)
    observations_used: int = 0
    effective_observations: float = 0.0
    shrinkage_delta: float = 0.0


class StrategyCorrelationMatrixEngine:
    """
    Live inter-strategy correlation matrix engine: EWMA covariance estimation, fixed-intensity
    linear shrinkage toward a diagonal target, high-correlation pair detection, and
    diversification breakdown alerting.

    Args:
        ewma_span: pandas-convention span, ``alpha = 2 / (span + 1)`` with weights
            ``(1 - alpha)^i`` normalized to sum to 1. Also the effective sample size of the
            estimate once history is long enough, so it is a direct statistical-power knob,
            not just a smoothing preference. Must be >= 2.
        shrinkage_delta: fixed weight on the diagonal target in ``delta * diag(S) +
            (1 - delta) * S``, in [0, 1]. Applied to the covariance only; it does not move
            the reported correlations or the alert thresholds.
        high_correlation_threshold: rho at or above which a pair is flagged (inclusive,
            one-sided). Internal policy default, not an external standard.
        max_avg_correlation_threshold: average off-diagonal rho at or above which the
            portfolio is flagged. Internal policy default.
        min_observations: minimum rows required. A Pearson estimate carries roughly
            ``1/sqrt(N-3)`` standard error on the Fisher-z scale, so short windows manufacture
            breaches; calibrate this rather than relying on the hard floor of 3.
    """

    def __init__(
        self,
        ewma_span: int = 60,
        shrinkage_delta: float = 0.15,
        high_correlation_threshold: float = 0.70,
        max_avg_correlation_threshold: float = 0.55,
        min_observations: int = 30,
    ) -> None:
        if not isinstance(ewma_span, (int, np.integer)) or ewma_span < MIN_EWMA_SPAN:
            raise ValueError(
                f"ewma_span must be an integer >= {MIN_EWMA_SPAN} (span=1 puts all weight on "
                f"the newest observation, leaving the covariance undefined), got {ewma_span!r}."
            )
        if not math.isfinite(shrinkage_delta) or not 0.0 <= shrinkage_delta <= 1.0:
            raise ValueError(
                f"shrinkage_delta must be a finite value in [0, 1], got {shrinkage_delta!r}."
            )
        for label, value in (
            ("high_correlation_threshold", high_correlation_threshold),
            ("max_avg_correlation_threshold", max_avg_correlation_threshold),
        ):
            if not math.isfinite(value) or not -1.0 <= value <= 1.0:
                raise ValueError(f"{label} must be a finite correlation in [-1, 1], got {value!r}.")
        if min_observations < MIN_OBSERVATIONS_FLOOR:
            raise ValueError(
                f"min_observations must be >= {MIN_OBSERVATIONS_FLOOR}; below that every "
                "off-diagonal correlation is algebraically +/-1 regardless of the data."
            )

        self.ewma_span = int(ewma_span)
        self.shrinkage_delta = shrinkage_delta
        self.high_correlation_threshold = high_correlation_threshold
        self.max_avg_correlation_threshold = max_avg_correlation_threshold
        self.min_observations = min_observations

    # ------------------------------------------------------------------ #
    # Validation
    # ------------------------------------------------------------------ #
    def _validate_returns(self, returns_df: pd.DataFrame) -> Tuple[List[str], np.ndarray]:
        """Validates the return panel and returns (strategy_names, float observation matrix).

        Rejects rather than imputes: a risk monitor that silently substitutes a value for
        missing or degenerate data reports a portfolio that does not exist.
        """
        if not isinstance(returns_df, pd.DataFrame):
            raise TypeError(
                f"returns_df must be a pandas DataFrame, got {type(returns_df).__name__}."
            )
        if returns_df.shape[1] < 2:
            raise ValueError(
                "returns_df must contain at least 2 strategy return series, got "
                f"{returns_df.shape[1]}."
            )

        strategy_names = [str(c) for c in returns_df.columns]
        duplicates = sorted({name for name in strategy_names if strategy_names.count(name) > 1})
        if duplicates:
            raise ValueError(
                f"returns_df has duplicate strategy column names {duplicates}; correlation pairs "
                "would not be attributable to a specific strategy."
            )

        # pandas' dtype predicates, not np.issubdtype: the latter raises an opaque
        # "Cannot interpret 'Float64Dtype()' as a data type" on the nullable extension dtypes
        # that read_parquet and convert_dtypes routinely produce. Booleans and complex numbers
        # both pass is_numeric_dtype and are both excluded -- a complex column would otherwise
        # be accepted and have its imaginary part silently discarded on cast.
        non_numeric = [
            name for name, dtype in zip(strategy_names, returns_df.dtypes)
            if not pd.api.types.is_numeric_dtype(dtype)
            or pd.api.types.is_bool_dtype(dtype)
            or pd.api.types.is_complex_dtype(dtype)
        ]
        if non_numeric:
            raise TypeError(
                f"returns_df columns {non_numeric} are not real numeric return series; supply "
                "strategy returns as floats."
            )

        # Nullable extension dtypes carry pd.NA, which casts to NaN here and is then caught by
        # the finiteness check below rather than silently participating in the estimate.
        observations = returns_df.astype("float64").to_numpy(dtype=float, copy=True)
        n_obs = observations.shape[0]
        if n_obs < self.min_observations:
            raise ValueError(
                f"returns_df has {n_obs} observations, below min_observations="
                f"{self.min_observations}. Do not act on a correlation estimated from fewer."
            )

        if not np.isfinite(observations).all():
            bad = [
                name for idx, name in enumerate(strategy_names)
                if not np.isfinite(observations[:, idx]).all()
            ]
            raise ValueError(
                f"returns_df contains NaN or infinite values in {bad}. Correlations must not be "
                "estimated over imputed or partially-missing return series -- fix the feed or "
                "drop the strategy from scope."
            )

        flat = [
            name for idx, name in enumerate(strategy_names)
            if observations[:, idx].max() == observations[:, idx].min()
        ]
        if flat:
            raise ValueError(
                f"returns_df contains zero-variance (flat/stale/idle) strategies {flat}. Their "
                "correlation is undefined; reporting 0.0 would make a dead strategy the "
                "portfolio's best diversifier. Exclude them from scope instead."
            )

        return strategy_names, observations

    # ------------------------------------------------------------------ #
    # Estimation
    # ------------------------------------------------------------------ #
    def _ewma_weights(self, n_obs: int) -> np.ndarray:
        """Normalized pandas-convention EWMA weights, newest observation last."""
        alpha = 2.0 / (self.ewma_span + 1.0)
        weights = (1.0 - alpha) ** np.arange(n_obs - 1, -1, -1, dtype=float)
        return weights / weights.sum()

    def _ewma_covariance(self, observations: np.ndarray, weights: np.ndarray) -> np.ndarray:
        """EWMA covariance at the latest observation, debiased as pandas' ``ewm(...).cov()``.

        Computed directly from the weight vector as a single weighted sum of outer products,
        which is positive semi-definite by construction. Numerically identical to
        ``returns_df.ewm(span=...).cov().iloc[-n:]`` (verified to ~1e-15 relative) without
        evaluating the covariance at every intermediate timestamp.
        """
        deviations = observations - (weights @ observations)
        biased_cov = (deviations * weights[:, None]).T @ deviations
        sum_sq_weights = float(np.sum(weights ** 2))
        debias = 1.0 - sum_sq_weights
        if debias <= 0.0:  # unreachable for ewma_span >= 2 and n_obs >= 3; guarded, not assumed
            raise ValueError(
                "EWMA weights are too concentrated to debias (sum of squared weights = "
                f"{sum_sq_weights:.6f}); increase ewma_span."
            )
        cov = biased_cov / debias
        return 0.5 * (cov + cov.T)  # kill floating-point asymmetry

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #
    def compute_live_correlation_matrix(
        self,
        returns_df: pd.DataFrame,
        timestamp_iso: str = "",
    ) -> LiveCorrelationMatrixReport:
        """Recomputes the live inter-strategy correlation matrix and diversification alerts.

        Args:
            returns_df: strategy return series as columns, one row per synchronized timestamp,
                oldest first. Must be numeric, finite, free of duplicate column names and of
                zero-variance columns, and at least ``min_observations`` rows deep.
            timestamp_iso: caller-supplied "as of" stamp, echoed into the report and audit note.

        Returns:
            A ``LiveCorrelationMatrixReport``. ``correlation_matrix`` is the unshrunk EWMA
            correlation estimate and is what the thresholds are applied to;
            ``shrunk_covariance_matrix`` is the well-conditioned covariance for optimizers.

        Raises:
            TypeError: ``returns_df`` is not a DataFrame or holds non-numeric columns.
            ValueError: fewer than 2 strategies, fewer than ``min_observations`` rows,
                duplicate column names, non-finite values, or a zero-variance strategy.
        """
        strategy_names, observations = self._validate_returns(returns_df)
        n = len(strategy_names)
        n_obs = observations.shape[0]

        # 1. EWMA covariance at the latest observation.
        weights = self._ewma_weights(n_obs)
        effective_obs = 1.0 / float(np.sum(weights ** 2))
        ewma_cov = self._ewma_covariance(observations, weights)

        variances = np.diag(ewma_cov)
        if not np.all(np.isfinite(variances)) or np.any(variances <= 0.0):
            degenerate = [strategy_names[i] for i in range(n) if not variances[i] > 0.0]
            raise ValueError(
                f"EWMA variance is non-positive or non-finite for {degenerate}: these strategies "
                f"are flat over the effective window (~{effective_obs:.0f} observations at "
                f"ewma_span={self.ewma_span}). Exclude them rather than reporting rho = 0."
            )

        # Strict comparison with a rounding hair: span == min_observations sits exactly on
        # the boundary and must not warn on floating-point noise alone.
        if effective_obs < self.min_observations - 1e-9:
            logger.warning(
                "EWMA effective sample size is %.1f (ewma_span=%d) against min_observations=%d: "
                "the estimate is weaker than the %d supplied rows suggest.",
                effective_obs, self.ewma_span, self.min_observations, n_obs,
            )

        # 2. Fixed-intensity linear shrinkage toward the diagonal (zero-correlation) target.
        #    Sigma_shrunk = delta * diag(S) + (1 - delta) * S -- positive definite for
        #    delta > 0, so it stays invertible even when n_obs < n and S is singular.
        #    Reported as a covariance only: it deflates every off-diagonal correlation by
        #    exactly (1 - delta), so thresholds must not be applied to it.
        shrunk_cov = (
            self.shrinkage_delta * np.diag(variances)
            + (1.0 - self.shrinkage_delta) * ewma_cov
        )

        # 3. Correlation matrix from the *unshrunk* covariance -- the quantity the
        #    thresholds describe.
        std_devs = np.sqrt(variances)
        corr_matrix_np = ewma_cov / np.outer(std_devs, std_devs)
        np.fill_diagonal(corr_matrix_np, 1.0)
        corr_matrix_np = np.clip(corr_matrix_np, -1.0, 1.0)

        # 4. Pairwise analysis & breakdown alerting.
        high_corr_pairs: List[StrategyCorrelationPair] = []
        off_diag_corrs: List[float] = []

        for i in range(n):
            for j in range(i + 1, n):
                raw = float(corr_matrix_np[i, j])
                off_diag_corrs.append(raw)

                # Compare unrounded, report rounded: rounding first would move the effective
                # threshold to 0.69995.
                if raw >= self.high_correlation_threshold:
                    high_corr_pairs.append(StrategyCorrelationPair(
                        strategy_a=strategy_names[i],
                        strategy_b=strategy_names[j],
                        correlation=round(raw, 4),
                        is_high_correlation_alert=True,
                    ))
                    logger.warning(
                        "HIGH CORRELATION ALERT: %s <-> %s = %.4f >= %s.",
                        strategy_names[i], strategy_names[j], raw,
                        self.high_correlation_threshold,
                    )

        avg_inter_corr = float(round(float(np.mean(off_diag_corrs)), 4))
        avg_breached = avg_inter_corr >= self.max_avg_correlation_threshold
        is_compromised = avg_breached or bool(high_corr_pairs)

        if avg_breached:
            logger.warning(
                "PORTFOLIO DIVERSIFICATION ALERT: average inter-strategy correlation %.4f >= %s.",
                avg_inter_corr, self.max_avg_correlation_threshold,
            )

        notes = (
            f"STRATEGY CORRELATION MATRIX [{timestamp_iso}]: Strategies = {n}, "
            f"Observations = {n_obs} (effective {effective_obs:.1f} at span {self.ewma_span}), "
            f"Avg Correlation = {avg_inter_corr:.3f}, High Correlation Pairs = "
            f"{len(high_corr_pairs)}, Avg Threshold Breached = {avg_breached}, "
            f"Diversification Compromised = {is_compromised}."
        )
        logger.info(notes)

        return LiveCorrelationMatrixReport(
            timestamp_iso=timestamp_iso,
            strategy_names=strategy_names,
            correlation_matrix=corr_matrix_np.tolist(),
            average_inter_strategy_correlation=avg_inter_corr,
            high_correlation_pairs=high_corr_pairs,
            is_portfolio_diversification_compromised=is_compromised,
            audit_notes=notes,
            shrunk_covariance_matrix=shrunk_cov.tolist(),
            observations_used=n_obs,
            effective_observations=round(effective_obs, 4),
            shrinkage_delta=self.shrinkage_delta,
        )

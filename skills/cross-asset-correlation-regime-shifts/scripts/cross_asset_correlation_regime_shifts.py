"""Cross-asset correlation regime detection via matrix distance.

Compares a short-window Pearson correlation matrix against a baseline
window using the K-normalised Frobenius distance

    D_F = ||C_short - C_base||_F / K

(the root-mean-square per-element difference of the K x K matrices),
plus the average off-diagonal correlation, and classifies the regime as
STABLE_NORMAL / CORRELATION_SHIFT / CRISIS_CONVERGENCE with leverage
multiplier directives (1.0 / 0.8 / 0.5 by default).

Empirical grounding: correlations increase in bear, not bull, markets
(Longin & Solnik, 2001, Journal of Finance 56(2)); stock-bond
correlation flipped positive in the 2022 inflation regime (BIS
Quarterly Review, Dec 2023).

Limitations (documented, deliberate):
- Thresholds (0.30 / 0.60 / 0.65 avg-corr) are defaults, not calibrated
  constants: calibrate them against the empirical distribution of
  rolling D_F on your universe before acting.
- The K-normalised Frobenius distance is not bounded by 1 (unlike
  Herdin et al.'s correlation matrix distance); values scale with K.
- Series with zero sample variance (stale/flat feeds) produce undefined
  correlations and are rejected as data errors rather than imputed.
"""

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


def _validate_returns_matrix(returns_matrix: np.ndarray) -> np.ndarray:
    """Validates an N x K returns matrix: 2-D, N >= 2, K >= 2, all finite."""
    arr = np.asarray(returns_matrix, dtype=float)
    if arr.ndim != 2:
        raise ValueError(f"returns_matrix must be 2-D (N x K), got ndim={arr.ndim}")
    if arr.shape[0] < 2:
        raise ValueError("Insufficient rows to calculate correlation matrix.")
    if arr.shape[1] < 2:
        raise ValueError(f"At least 2 assets required, got K={arr.shape[1]}")
    if not np.all(np.isfinite(arr)):
        raise ValueError("returns_matrix contains non-finite values (NaN/Inf).")
    return arr


def _validate_correlation_matrix(corr: np.ndarray) -> np.ndarray:
    """Validates a square K x K (K >= 2) finite correlation matrix."""
    arr = np.asarray(corr, dtype=float)
    if arr.ndim != 2 or arr.shape[0] != arr.shape[1]:
        raise ValueError(f"Correlation matrix must be square, got shape {arr.shape}.")
    if arr.shape[0] < 2:
        raise ValueError("Correlation matrix requires K >= 2.")
    if not np.all(np.isfinite(arr)):
        raise ValueError("Correlation matrix contains non-finite values.")
    return arr


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
    Quantitative macro engine for detecting cross-asset correlation matrix
    structural shifts using the K-normalised Frobenius distance and
    triggering portfolio de-leveraging directives.
    """
    def __init__(
        self,
        shift_threshold: float = 0.30,
        crisis_threshold: float = 0.60,
        high_corr_threshold: float = 0.65
    ):
        for name, value in (
            ("shift_threshold", shift_threshold),
            ("crisis_threshold", crisis_threshold),
            ("high_corr_threshold", high_corr_threshold),
        ):
            if not np.isfinite(value):
                raise ValueError(f"{name} must be finite, got {value!r}")
        if not 0.0 <= shift_threshold <= crisis_threshold:
            raise ValueError(
                f"Thresholds must satisfy 0 <= shift_threshold <= crisis_threshold, "
                f"got {shift_threshold} / {crisis_threshold}"
            )
        if not -1.0 <= high_corr_threshold <= 1.0:
            raise ValueError(
                f"high_corr_threshold must be within [-1, 1], got {high_corr_threshold}"
            )
        self.shift_threshold = shift_threshold
        self.crisis_threshold = crisis_threshold
        self.high_corr_threshold = high_corr_threshold

    def compute_correlation_matrix(self, returns_matrix: np.ndarray) -> np.ndarray:
        """
        Computes Pearson correlation matrix for N x K returns array
        (N timestamps, K assets). A constant series yields undefined
        correlations and raises ValueError instead of being silently
        imputed — treat it as a stale/flat data feed.
        """
        arr = _validate_returns_matrix(returns_matrix)
        # Zero-variance columns make corrcoef emit invalid-division warnings
        # before producing NaNs; the finiteness check below reports the actual
        # data error, so the transient numpy warning is suppressed here.
        with np.errstate(invalid='ignore', divide='ignore'):
            corr = np.corrcoef(arr, rowvar=False)
        if not np.all(np.isfinite(corr)):
            raise ValueError(
                "Correlation matrix has undefined (non-finite) entries — a series "
                "with zero sample variance (stale/flat feed) was detected."
            )
        return corr

    def calculate_frobenius_distance(self, corr1: np.ndarray, corr2: np.ndarray) -> float:
        """
        K-normalised Frobenius distance: ||C1 - C2||_F / K, the
        root-mean-square per-element difference. NOT bounded by 1; a
        single pairwise flip of delta rho in a K-asset matrix moves it
        by sqrt(2) * |delta| / K.
        """
        m1 = _validate_correlation_matrix(corr1)
        m2 = _validate_correlation_matrix(corr2)
        if m1.shape != m2.shape:
            raise ValueError(
                f"Correlation matrices must have identical dimensions, "
                f"got {m1.shape} vs {m2.shape}."
            )
        k = m1.shape[0]
        diff = m1 - m2
        raw_norm = float(np.linalg.norm(diff, ord='fro'))
        return float(raw_norm / max(1.0, float(k)))

    def calculate_average_off_diagonal_correlation(self, corr: np.ndarray) -> float:
        """
        Average over both off-diagonal triangles: (1/(K(K-1))) * sum_{i != j} C_ij.
        """
        arr = _validate_correlation_matrix(corr)
        k = arr.shape[0]
        mask = ~np.eye(k, dtype=bool)
        return float(np.mean(arr[mask]))

    def detect_regime(
        self,
        short_term_returns: np.ndarray,
        baseline_returns: np.ndarray
    ) -> CorrelationRegimeReport:
        """
        Analyzes short-term vs baseline return series to classify the
        correlation regime. Both windows must cover the same K assets.
        """
        c_short = self.compute_correlation_matrix(short_term_returns)
        c_base = self.compute_correlation_matrix(baseline_returns)

        f_dist = self.calculate_frobenius_distance(c_short, c_base)
        avg_corr_short = self.calculate_average_off_diagonal_correlation(c_short)
        avg_corr_base = self.calculate_average_off_diagonal_correlation(c_base)

        # Classification logic (boundaries inclusive)
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

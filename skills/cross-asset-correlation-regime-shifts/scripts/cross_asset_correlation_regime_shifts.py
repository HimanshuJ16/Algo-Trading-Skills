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
- Windows with fewer than three observations are algebraically degenerate
  (any two points are perfectly correlated, so every off-diagonal entry is
  exactly +/-1 regardless of the data) and are rejected. Callers should
  raise ``min_observations`` to their own calibrated floor so that a data
  gap raises instead of fabricating a crisis signal.
"""

import logging
from dataclasses import dataclass

import numpy as np

logger = logging.getLogger(__name__)


# Two observations always produce off-diagonal correlations of exactly +/-1,
# independent of the underlying data, so anything below three rows carries no
# information about the correlation structure.
DEGENERATE_WINDOW_ROWS = 3


def _validate_returns_matrix(
    returns_matrix: np.ndarray,
    min_observations: int = DEGENERATE_WINDOW_ROWS,
) -> np.ndarray:
    """Validates an N x K returns matrix: 2-D, N >= min_observations, K >= 2, all finite."""
    arr = np.asarray(returns_matrix, dtype=float)
    if arr.ndim != 2:
        raise ValueError(f"returns_matrix must be 2-D (N x K), got ndim={arr.ndim}")
    if arr.shape[0] < max(min_observations, DEGENERATE_WINDOW_ROWS):
        raise ValueError(
            f"Insufficient rows to calculate correlation matrix: got N={arr.shape[0]}, "
            f"require at least {max(min_observations, DEGENERATE_WINDOW_ROWS)}. Windows of "
            "1-2 observations yield off-diagonal correlations of exactly +/-1 regardless "
            "of the data, which would be read as a spurious regime shift."
        )
    if arr.shape[1] < 2:
        raise ValueError(f"At least 2 assets required, got K={arr.shape[1]}")
    if not np.all(np.isfinite(arr)):
        raise ValueError("returns_matrix contains non-finite values (NaN/Inf).")
    return arr


# Absolute tolerance for the structural checks below: sample correlation
# matrices returned by numpy/shrinkage estimators can carry unit-diagonal and
# symmetry error of a few ULPs, and entries marginally outside [-1, 1].
_CORR_ATOL = 1e-8


def _validate_correlation_matrix(corr: np.ndarray) -> np.ndarray:
    """
    Validates a square K x K (K >= 2) finite correlation matrix: symmetric,
    unit diagonal, entries within [-1, 1] (all to ``_CORR_ATOL``).

    The structural checks exist because a covariance matrix passed where a
    correlation matrix is expected is silently accepted by the distance
    arithmetic and inflates D_F by orders of magnitude, which would be
    classified as CRISIS_CONVERGENCE and de-leverage the book.
    """
    arr = np.asarray(corr, dtype=float)
    if arr.ndim != 2 or arr.shape[0] != arr.shape[1]:
        raise ValueError(f"Correlation matrix must be square, got shape {arr.shape}.")
    if arr.shape[0] < 2:
        raise ValueError("Correlation matrix requires K >= 2.")
    if not np.all(np.isfinite(arr)):
        raise ValueError("Correlation matrix contains non-finite values.")
    diagonal = np.diagonal(arr)
    if not np.allclose(diagonal, 1.0, rtol=0.0, atol=_CORR_ATOL):
        raise ValueError(
            "Correlation matrix must have a unit diagonal, got "
            f"{np.array2string(diagonal, precision=4)} — a covariance matrix was "
            "most likely supplied instead of a correlation matrix."
        )
    if not np.allclose(arr, arr.T, rtol=0.0, atol=_CORR_ATOL):
        raise ValueError("Correlation matrix must be symmetric.")
    if np.any(np.abs(arr) > 1.0 + _CORR_ATOL):
        raise ValueError(
            "Correlation matrix entries must lie within [-1, 1], got range "
            f"[{arr.min():.6f}, {arr.max():.6f}]."
        )
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
        high_corr_threshold: float = 0.65,
        min_observations: int = DEGENERATE_WINDOW_ROWS
    ):
        """
        Thresholds are uncalibrated defaults — calibrate them to the empirical
        rolling-D_F distribution of your own universe and window lengths.

        ``min_observations`` is the minimum number of rows accepted in either
        window. The floor of ``DEGENERATE_WINDOW_ROWS`` (3) only rules out the
        algebraically degenerate case; set it to your calibrated window floor
        (e.g. 30, at which sample-correlation noise ~ 1/sqrt(W) is around 0.18)
        so a truncated or gapped feed raises instead of producing a
        noise-driven regime flip.
        """
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
        if isinstance(min_observations, bool) or not isinstance(min_observations, (int, np.integer)):
            raise ValueError(
                f"min_observations must be an int, got {min_observations!r}"
            )
        if min_observations < DEGENERATE_WINDOW_ROWS:
            raise ValueError(
                f"min_observations must be >= {DEGENERATE_WINDOW_ROWS}, got {min_observations}"
            )
        self.min_observations = int(min_observations)
        self.shift_threshold = shift_threshold
        self.crisis_threshold = crisis_threshold
        self.high_corr_threshold = high_corr_threshold

    def compute_correlation_matrix(self, returns_matrix: np.ndarray) -> np.ndarray:
        """
        Computes Pearson correlation matrix for N x K returns array
        (N timestamps, K assets). A constant series yields undefined
        correlations and raises ValueError instead of being silently
        imputed — treat it as a stale/flat data feed. Windows shorter than
        ``min_observations`` are rejected for the same reason: they carry no
        usable correlation structure.
        """
        arr = _validate_returns_matrix(returns_matrix, self.min_observations)
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
        correlation regime. Both windows must cover the same K assets, in the
        same column order — the engine can only verify that K matches, not
        that column 0 is the same instrument in both windows.
        """
        short_arr = _validate_returns_matrix(short_term_returns, self.min_observations)
        base_arr = _validate_returns_matrix(baseline_returns, self.min_observations)
        if short_arr.shape[0] > base_arr.shape[0]:
            logger.warning(
                "Short window (%d rows) is longer than the baseline window (%d rows) — "
                "arguments may be swapped. The high-correlation trigger and the reported "
                "short_term_avg_correlation are taken from the FIRST argument.",
                short_arr.shape[0], base_arr.shape[0],
            )

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

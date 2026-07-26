import numpy as np
import logging
from dataclasses import dataclass
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

@dataclass
class DiagnosticResult:
    status: str                         # 'STABLE', 'DATA_STALENESS', 'COVARIATE_SHIFT', 'CONCEPT_DRIFT'
    staleness_age_sec: float
    feature_psi_score: float
    error_mse_ratio: float
    recommended_action: str

class DriftVsStalenessClassifier:
    """
    ML Monitoring engine for differentiating Data Staleness (timestamp lag),
    Covariate Shift (P(X) distribution shift), and Concept Drift (P(Y|X) alpha decay).
    """
    def __init__(
        self,
        max_staleness_sec: float = 300.0,    # Max allowed feature age (5 minutes)
        psi_threshold: float = 0.25,         # PSI > 0.25 indicates high feature shift
        error_ratio_threshold: float = 1.50  # MSE ratio > 1.5 indicates performance degradation
    ):
        self.max_staleness_sec = max_staleness_sec
        self.psi_threshold = psi_threshold
        self.error_ratio_threshold = error_ratio_threshold

    def calculate_psi(self, ref_data: np.ndarray, curr_data: np.ndarray, num_bins: int = 10) -> float:
        """
        Calculates Population Stability Index (PSI) between reference and current feature series.
        """
        if len(ref_data) == 0 or len(curr_data) == 0:
            return 0.0

        # Create binned distribution based on reference percentiles
        quantiles = np.linspace(0, 100, num_bins + 1)
        bins = np.percentile(ref_data, quantiles)
        bins[0] -= 1e-5
        bins[-1] += 1e-5
        # Ensure strictly increasing bins
        bins = np.unique(bins)
        if len(bins) < 2:
            return 0.0

        ref_counts, _ = np.histogram(ref_data, bins=bins)
        curr_counts, _ = np.histogram(curr_data, bins=bins)

        ref_pcts = ref_counts / len(ref_data)
        curr_pcts = curr_counts / len(curr_data)

        # Replace zero counts with small epsilon to prevent division by zero / log(0)
        eps = 1e-4
        ref_pcts = np.where(ref_pcts == 0, eps, ref_pcts)
        curr_pcts = np.where(curr_pcts == 0, eps, curr_pcts)

        psi = np.sum((curr_pcts - ref_pcts) * np.log(curr_pcts / ref_pcts))
        return round(float(psi), 4)

    def diagnose(
        self,
        feature_timestamp_sec: float,
        current_timestamp_sec: float,
        X_ref: np.ndarray,
        X_curr: np.ndarray,
        residuals_ref: np.ndarray,
        residuals_curr: np.ndarray
    ) -> DiagnosticResult:
        """
        Evaluates system inputs and classifies operational state into 4 regimes.
        """
        # 1. Check Data Staleness
        age_sec = current_timestamp_sec - feature_timestamp_sec
        if age_sec > self.max_staleness_sec:
            logger.error(f"DATA STALENESS DETECTED: Feature age {age_sec:.1f}s exceeds limit {self.max_staleness_sec}s.")
            return DiagnosticResult(
                status="DATA_STALENESS",
                staleness_age_sec=round(age_sec, 2),
                feature_psi_score=0.0,
                error_mse_ratio=1.0,
                recommended_action="Inspect data ingestion pipeline and refresh feature feeds."
            )

        # 2. Compute Feature Shift Score (Average PSI across features)
        if X_ref.ndim == 1:
            X_ref = X_ref.reshape(-1, 1)
            X_curr = X_curr.reshape(-1, 1)

        psi_scores = [
            self.calculate_psi(X_ref[:, col], X_curr[:, col])
            for col in range(X_ref.shape[1])
        ]
        avg_psi = round(float(np.mean(psi_scores)), 4) if psi_scores else 0.0

        # 3. Compute Error MSE Ratio
        mse_ref = float(np.mean(residuals_ref ** 2)) if len(residuals_ref) > 0 else 1.0
        mse_curr = float(np.mean(residuals_curr ** 2)) if len(residuals_curr) > 0 else 1.0
        error_ratio = round(mse_curr / (mse_ref + 1e-8), 4)

        # 4. Classify Regime
        is_high_feature_shift = avg_psi >= self.psi_threshold
        is_high_error_ratio = error_ratio >= self.error_ratio_threshold

        if is_high_feature_shift and not is_high_error_ratio:
            status = "COVARIATE_SHIFT"
            action = "Retrain ML model on updated input distribution P(X)."
        elif is_high_error_ratio:
            status = "CONCEPT_DRIFT"
            action = "Alpha Decay / Structural Shift detected. Refactor model parameters or shorten lookback window."
        else:
            status = "STABLE"
            action = "Model operating within expected performance bounds."

        logger.info(f"Diagnostic Complete: Status={status}, PSI={avg_psi}, ErrorRatio={error_ratio}")
        return DiagnosticResult(
            status=status,
            staleness_age_sec=round(age_sec, 2),
            feature_psi_score=avg_psi,
            error_mse_ratio=error_ratio,
            recommended_action=action
        )

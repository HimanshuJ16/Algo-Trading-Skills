import numpy as np
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class ArchitectureRecommendation:
    selected_architecture: str        # 'CROSS_SECTIONAL' or 'TIME_SERIES'
    rationale: str
    is_market_neutral_enforced: bool
    is_volatility_targeting_enforced: bool

@dataclass
class SignalTransformationResult:
    architecture: str
    raw_factor_matrix: np.ndarray
    standardized_z_scores: np.ndarray
    portfolio_weights: np.ndarray
    net_exposure: float
    gross_exposure: float

class ModelArchitectureSelectorEngine:
    """
    Quantitative architecture selector and factor transformation engine for Cross-Sectional
    (relative ranking, market-neutral) and Time-Series (absolute trend, vol-scaled) models.
    """
    def __init__(self, windsorize_std: float = 3.0, target_volatility_annual: float = 0.15):
        self.windsorize_std = windsorize_std
        self.target_volatility_annual = target_volatility_annual

    def recommend_architecture(
        self,
        universe_size: int,
        require_market_neutrality: bool,
        is_single_asset_trend: bool
    ) -> ArchitectureRecommendation:
        """
        Recommends CROSS_SECTIONAL vs TIME_SERIES model design based on strategy parameters.
        """
        if require_market_neutrality or (universe_size >= 10 and not is_single_asset_trend):
            return ArchitectureRecommendation(
                selected_architecture="CROSS_SECTIONAL",
                rationale="Multi-asset universe requiring peer ranking and dollar-neutral / market-neutral execution.",
                is_market_neutral_enforced=True,
                is_volatility_targeting_enforced=False
            )
        else:
            return ArchitectureRecommendation(
                selected_architecture="TIME_SERIES",
                rationale="Single-asset or un-constrained directional trend strategy requiring time-series volatility scaling.",
                is_market_neutral_enforced=False,
                is_volatility_targeting_enforced=True
            )

    def _windsorize(self, data: np.ndarray) -> np.ndarray:
        mean = np.mean(data)
        std = np.std(data)
        if std < 1e-6:
            return data - mean
        clipped = np.clip(data, mean - self.windsorize_std * std, mean + self.windsorize_std * std)
        return clipped

    def transform_cross_sectional(self, factor_values: np.ndarray) -> SignalTransformationResult:
        """
        Transforms 1D array of factor values for K assets at a single timestamp t:
        1. Windsorizes outliers.
        2. Computes Cross-Sectional Z-scores across assets: Z_i = (X_i - mean_cs) / std_cs.
        3. Demeans and normalizes weights: sum(w_i) = 0, sum(|w_i|) = 1.0.
        """
        if factor_values.ndim != 1 or len(factor_values) < 2:
            raise ValueError("Cross-sectional transformation requires 1D array with at least 2 assets.")

        clean_factors = self._windsorize(factor_values)
        mean_cs = float(np.mean(clean_factors))
        std_cs = float(np.std(clean_factors))

        if std_cs < 1e-6:
            z_scores = np.zeros_like(clean_factors)
        else:
            z_scores = (clean_factors - mean_cs) / std_cs

        # Demean to ensure exact dollar neutrality
        weights_raw = z_scores - np.mean(z_scores)
        gross_sum = np.sum(np.abs(weights_raw))

        if gross_sum < 1e-6:
            weights = np.zeros_like(weights_raw)
        else:
            weights = weights_raw / gross_sum

        net_exp = float(np.sum(weights))
        gross_exp = float(np.sum(np.abs(weights)))

        return SignalTransformationResult(
            architecture="CROSS_SECTIONAL",
            raw_factor_matrix=factor_values,
            standardized_z_scores=np.round(z_scores, 4),
            portfolio_weights=np.round(weights, 4),
            net_exposure=round(net_exp, 6),
            gross_exposure=round(gross_exp, 4)
        )

    def transform_time_series(
        self,
        historical_returns: np.ndarray,
        current_factor: float,
        asset_realized_vol_annual: float
    ) -> float:
        """
        Transforms a single asset factor over time using Time-Series Volatility Scaling:
        1. Computes time-series Z-score relative to asset's own history.
        2. Scales position weight inversely by realized volatility.
        """
        if len(historical_returns) < 5:
            mean_ts = 0.0
            std_ts = 1.0
        else:
            mean_ts = float(np.mean(historical_returns))
            std_ts = float(np.std(historical_returns))

        if std_ts < 1e-6:
            z_score = 0.0
        else:
            z_score = (current_factor - mean_ts) / std_ts

        direction = float(np.sign(z_score))
        realized_vol = max(0.01, asset_realized_vol_annual)
        
        # Volatility-Targeted Weight
        vol_scalar = self.target_volatility_annual / realized_vol
        weight = direction * min(2.0, vol_scalar)  # Max 2x leverage cap
        
        return round(float(weight), 4)

"""Cross-Sectional vs Time-Series alpha model architecture selection and factor transformation.

Cross-sectional transforms standardize a factor across the asset axis at a single
timestamp and produce dollar-neutral (sum(w) = 0) long-short weights. Time-series
transforms standardize an asset's factor against its own history and produce a
directional weight scaled inversely by that asset's realized volatility
(Moskowitz, Ooi & Pedersen 2012: position size = sigma_target / sigma_{t-1}).

Neither transform imputes, floors, or otherwise repairs bad input: non-finite
values, degenerate universes and non-positive volatilities raise rather than
silently producing a position. See references/standards.md.
"""
import logging
from dataclasses import dataclass
from typing import Literal

import numpy as np

logger = logging.getLogger(__name__)

# Below this, a scale estimate (sigma or MAD) is treated as numerically zero and the
# cross-section is reported degenerate (logged, weights flat). Absolute, so factors
# must be on a sane numeric scale -- a factor whose genuine dispersion is below 1e-12
# should be rescaled upstream rather than relying on this guard.
_SCALE_EPS = 1e-12
# Consistency constant making MAD a sigma-equivalent scale estimate for Gaussian
# data: 1 / Phi^{-1}(0.75). See references/standards.md.
_MAD_TO_SIGMA = 1.4826

WeightingScheme = Literal["zscore", "rank"]
WinsorizeMethod = Literal["sigma", "mad"]


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
    net_exposure: float               # sum(w) of the RETURNED weights
    gross_exposure: float             # sum(|w|) of the RETURNED weights


class ModelArchitectureSelectorEngine:
    """
    Quantitative architecture selector and factor transformation engine for Cross-Sectional
    (relative ranking, dollar-neutral) and Time-Series (absolute trend, vol-scaled) models.

    Args:
        windsorize_std: outlier clip threshold in units of the chosen scale estimate.
        target_volatility_annual: annualized volatility target for time-series sizing.
        winsorize_method: 'sigma' clips at mean +/- k * std; 'mad' clips at
            median +/- k * 1.4826 * MAD. 'sigma' CANNOT BIND on small universes --
            for K assets the largest attainable |z| is (K-1)/sqrt(K), so a +/-3.0
            threshold never clips at K <= 10. Use 'mad' or weighting='rank' for
            small cross-sections.
        max_leverage: cap on |weight| returned by transform_time_series. A policy
            choice, not a research result -- MOP (2012) apply no such cap.
        min_history: minimum observations required to form a time-series z-score.
    """

    def __init__(
        self,
        windsorize_std: float = 3.0,
        target_volatility_annual: float = 0.15,
        winsorize_method: WinsorizeMethod = "sigma",
        max_leverage: float = 2.0,
        min_history: int = 5,
    ) -> None:
        if not np.isfinite(windsorize_std) or windsorize_std <= 0:
            raise ValueError("windsorize_std must be a positive finite float.")
        if not np.isfinite(target_volatility_annual) or target_volatility_annual <= 0:
            raise ValueError("target_volatility_annual must be a positive finite float.")
        if winsorize_method not in ("sigma", "mad"):
            raise ValueError("winsorize_method must be 'sigma' or 'mad'.")
        if not np.isfinite(max_leverage) or max_leverage <= 0:
            raise ValueError("max_leverage must be a positive finite float.")
        if min_history < 2:
            raise ValueError("min_history must be at least 2 to estimate a dispersion.")

        self.windsorize_std = float(windsorize_std)
        self.target_volatility_annual = float(target_volatility_annual)
        self.winsorize_method = winsorize_method
        self.max_leverage = float(max_leverage)
        self.min_history = int(min_history)

        if winsorize_method == "sigma":
            logger.debug(
                "sigma winsorization at %.2f std cannot bind for K <= %d assets "
                "(max attainable |z| is (K-1)/sqrt(K))",
                self.windsorize_std,
                self.max_inert_universe_size(),
            )

    def max_inert_universe_size(self) -> int:
        """
        Largest universe size K for which sigma-clipping at `windsorize_std` provably
        never clips, because max attainable |z| = (K-1)/sqrt(K) stays below the threshold.

        Closed form: with c = windsorize_std and s = sqrt(K), the condition
        s - 1/s <= c solves to s <= (c + sqrt(c^2 + 4)) / 2.
        """
        c = self.windsorize_std
        return int(np.floor(((c + np.sqrt(c * c + 4.0)) / 2.0) ** 2))

    def recommend_architecture(
        self,
        universe_size: int,
        require_market_neutrality: bool,
        is_single_asset_trend: bool
    ) -> ArchitectureRecommendation:
        """
        Recommends CROSS_SECTIONAL vs TIME_SERIES model design based on strategy parameters.

        A cross-sectional architecture needs at least 2 assets to rank against, so a
        neutrality mandate over a degenerate universe raises rather than recommending
        an architecture that transform_cross_sectional would then reject.
        """
        if universe_size < 1:
            raise ValueError("universe_size must be at least 1.")
        if require_market_neutrality and universe_size < 2:
            raise ValueError(
                "Market neutrality requires a cross-section of at least 2 assets; "
                f"got universe_size={universe_size}."
            )
        if require_market_neutrality and is_single_asset_trend:
            raise ValueError(
                "Contradictory mandate: a single-asset directional trend cannot be "
                "market neutral. Resolve the mandate before selecting an architecture."
            )

        if require_market_neutrality or (universe_size >= 10 and not is_single_asset_trend):
            return ArchitectureRecommendation(
                selected_architecture="CROSS_SECTIONAL",
                rationale="Multi-asset universe requiring peer ranking and dollar-neutral execution. "
                          "Dollar neutrality (sum(w)=0) does NOT imply beta/market neutrality "
                          "(sum(w*beta)=0); hedge beta explicitly if the mandate requires it.",
                is_market_neutral_enforced=True,
                is_volatility_targeting_enforced=False
            )
        return ArchitectureRecommendation(
            selected_architecture="TIME_SERIES",
            rationale="Single-asset or un-constrained directional trend strategy requiring "
                      "time-series volatility scaling.",
            is_market_neutral_enforced=False,
            is_volatility_targeting_enforced=True
        )

    @staticmethod
    def _require_finite(data: np.ndarray, label: str) -> np.ndarray:
        """Reject NaN/Inf rather than letting them propagate into portfolio weights."""
        arr = np.asarray(data, dtype=float)
        if not np.all(np.isfinite(arr)):
            bad = int(np.count_nonzero(~np.isfinite(arr)))
            raise ValueError(
                f"{label} contains {bad} non-finite value(s). Screen or drop them upstream; "
                "the engine will not impute a factor value."
            )
        return arr

    def _windsorize(self, data: np.ndarray) -> np.ndarray:
        """
        Clip outliers at +/- windsorize_std scale units around a central estimate.

        Returns the input unchanged when the scale estimate is degenerate: a flat
        cross-section has no outliers to clip, and demeaning it here would silently
        alter values the caller never asked to recentre.
        """
        if self.winsorize_method == "mad":
            center = float(np.median(data))
            scale = float(np.median(np.abs(data - center))) * _MAD_TO_SIGMA
        else:
            center = float(np.mean(data))
            scale = float(np.std(data))

        if scale < _SCALE_EPS:
            return data
        return np.clip(
            data,
            center - self.windsorize_std * scale,
            center + self.windsorize_std * scale,
        )

    def transform_cross_sectional(
        self,
        factor_values: np.ndarray,
        weighting: WeightingScheme = "zscore",
    ) -> SignalTransformationResult:
        """
        Transforms a 1D array of factor values for K assets at a single timestamp t.

        1. Winsorizes outliers (see `winsorize_method`).
        2. Computes cross-sectional Z-scores across assets: Z_i = (X_i - mean_cs) / std_cs.
        3. Produces weights normalized to sum(w) = 0, sum(|w|) = 1.0.

        weighting:
            'zscore' -- weights proportional to the demeaned factor. NOTE: Z is already
                zero-mean, and the normalization divides by sum(|.|), so std_cs cancels
                exactly. These weights equal (X - mean)/sum|X - mean| and are invariant
                to the standardization: Z-scoring changes the reported
                `standardized_z_scores`, never the weights.
            'rank'   -- weights proportional to cross-sectional rank minus mean rank
                (Asness, Moskowitz & Pedersen 2013, eq. 1). Influence is bounded, so a
                single extreme factor value cannot dominate the book -- the robust
                choice whenever winsorization cannot bind.

        Gross exposure is normalized to 1.0 ($0.50 long / $0.50 short). AMP (2013)
        instead scale to $1 long / $1 short, i.e. sum(|w|) = 2.0 -- rescale if you are
        reproducing their factor returns.

        Weights are NOT rounded: rounding to 4dp injects a net-exposure error of up to
        K * 5e-5, breaching the 1e-5 dollar-neutrality standard in references/standards.md.
        """
        arr = self._require_finite(factor_values, "factor_values")
        if arr.ndim != 1 or len(arr) < 2:
            raise ValueError("Cross-sectional transformation requires 1D array with at least 2 assets.")
        if weighting not in ("zscore", "rank"):
            raise ValueError("weighting must be 'zscore' or 'rank'.")

        clean_factors = self._windsorize(arr)
        mean_cs = float(np.mean(clean_factors))
        std_cs = float(np.std(clean_factors))

        if std_cs < _SCALE_EPS:
            logger.warning("Degenerate cross-section (std ~ 0): emitting flat weights.")
            z_scores = np.zeros_like(clean_factors)
        else:
            z_scores = (clean_factors - mean_cs) / std_cs

        if weighting == "rank":
            # Average ranks over ties, so duplicate factor values get identical weights
            # instead of an arbitrary tie-break creating spurious dispersion.
            order = np.argsort(np.argsort(clean_factors)).astype(float)
            ranks = np.empty_like(clean_factors)
            for value in np.unique(clean_factors):
                mask = clean_factors == value
                ranks[mask] = order[mask].mean()
            weights_raw = ranks - np.mean(ranks)
        else:
            weights_raw = z_scores - np.mean(z_scores)

        gross_sum = float(np.sum(np.abs(weights_raw)))
        if gross_sum < _SCALE_EPS:
            weights = np.zeros_like(weights_raw)
        else:
            weights = weights_raw / gross_sum

        # Metrics describe the weights actually returned, not a pre-rounding intermediate.
        return SignalTransformationResult(
            architecture="CROSS_SECTIONAL",
            raw_factor_matrix=arr,
            standardized_z_scores=z_scores,
            portfolio_weights=weights,
            net_exposure=float(np.sum(weights)),
            gross_exposure=float(np.sum(np.abs(weights))),
        )

    def transform_time_series(
        self,
        historical_factor_values: np.ndarray,
        current_factor: float,
        asset_realized_vol_annual: float
    ) -> float:
        """
        Sizes a single asset by Time-Series Volatility Scaling:
        w = sign(Z) * min(max_leverage, sigma_target / sigma_realized).

        `historical_factor_values` must be the history of the SAME quantity as
        `current_factor` (e.g. trailing 12-month returns when that is the signal),
        not raw period returns, whose dispersion sits on a different scale.

        `asset_realized_vol_annual` must be an ANNUALIZED volatility estimated from
        data strictly BEFORE the bar being sized. MOP (2012) apply sigma_{t-1} to
        time-t returns precisely to avoid look-ahead bias; a vol computed including
        the current bar leaks future information into position size.

        Only sign(Z) enters the weight -- magnitude is deliberately discarded, per the
        MOP trend rule. A zero Z (flat/degenerate history) yields a zero weight.

        Raises on insufficient history or a non-positive/non-finite volatility rather
        than fabricating a position from a default.
        """
        history = self._require_finite(historical_factor_values, "historical_factor_values")
        if history.ndim != 1:
            raise ValueError("historical_factor_values must be a 1D array.")
        if len(history) < self.min_history:
            raise ValueError(
                f"Time-series standardization requires at least {self.min_history} observations; "
                f"got {len(history)}. Do not size a position off a defaulted z-score."
            )
        if not np.isfinite(current_factor):
            raise ValueError("current_factor must be finite.")
        if not np.isfinite(asset_realized_vol_annual) or asset_realized_vol_annual <= 0:
            raise ValueError(
                "asset_realized_vol_annual must be a positive finite annualized volatility; "
                f"got {asset_realized_vol_annual!r}. A floored bad vol would size the "
                "position at maximum leverage."
            )

        mean_ts = float(np.mean(history))
        std_ts = float(np.std(history))

        if std_ts < _SCALE_EPS:
            logger.warning("Degenerate factor history (std ~ 0): emitting flat weight.")
            z_score = 0.0
        else:
            z_score = (float(current_factor) - mean_ts) / std_ts

        direction = float(np.sign(z_score))
        vol_scalar = self.target_volatility_annual / float(asset_realized_vol_annual)
        return float(direction * min(self.max_leverage, vol_scalar))

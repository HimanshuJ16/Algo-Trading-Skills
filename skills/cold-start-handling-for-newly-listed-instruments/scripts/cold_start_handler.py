"""
cold-start-handling-for-newly-listed-instruments: Maturity checker, sector proxy feature substitute,
and position size scaler for new listings.
"""
from dataclasses import dataclass
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ColdStartEvaluation:
    symbol: str
    bars_available: int
    min_required_bars: int
    is_cold_start: bool
    size_scaling_factor: float
    features_used: Dict[str, float]
    status_message: str


class ColdStartHandler:
    """
    Manages ML feature generation and risk limits for newly listed instruments
    with insufficient historical data.
    """

    def __init__(
        self,
        min_required_bars: int = 30,
        cold_start_size_scale: float = 0.25,
    ):
        self.min_required_bars = min_required_bars
        self.cold_start_size_scale = cold_start_size_scale

    def evaluate_instrument(
        self,
        symbol: str,
        bars_available: int,
        native_features: Dict[str, float],
        sector_peer_proxy_features: Dict[str, float],
    ) -> ColdStartEvaluation:
        is_cold = bars_available < self.min_required_bars

        if is_cold:
            scaling = self.cold_start_size_scale
            # Substitute missing/NaN native features with sector peer proxies
            merged_features = dict(sector_peer_proxy_features)
            for k, v in native_features.items():
                if v is not None and not (isinstance(v, float) and (v != v)):  # check not NaN
                    merged_features[k] = v

            msg = (
                f"COLD START ACTIVE for '{symbol}' ({bars_available}/{self.min_required_bars} bars): "
                f"Applied sector proxy features and scaled size to {scaling * 100:.0f}%."
            )
            logger.info(msg)
        else:
            scaling = 1.0
            merged_features = dict(native_features)
            msg = f"Instrument '{symbol}' MATURE ({bars_available} bars): Full native ML model enabled."

        return ColdStartEvaluation(
            symbol=symbol,
            bars_available=bars_available,
            min_required_bars=self.min_required_bars,
            is_cold_start=is_cold,
            size_scaling_factor=scaling,
            features_used=merged_features,
            status_message=msg,
        )

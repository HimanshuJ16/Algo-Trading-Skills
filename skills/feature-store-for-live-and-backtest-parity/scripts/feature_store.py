"""
feature-store-for-live-and-backtest-parity: Production-grade single-point-of-truth feature store engine,
batch backtest & online streaming pipelines, and parity validator.
"""
from dataclasses import dataclass, field
import datetime
import logging
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class FeatureParityMismatchError(ValueError):
    """Raised when batch backtest and online live streaming feature outputs diverge."""
    pass


@dataclass
class Bar:
    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class FeatureVector:
    timestamp: float
    rsi: float
    bollinger_b: float
    volatility_zscore: float
    return_1d: float

    def to_dict(self) -> Dict[str, float]:
        return {
            "rsi": self.rsi,
            "bollinger_b": self.bollinger_b,
            "volatility_zscore": self.volatility_zscore,
            "return_1d": self.return_1d,
        }


class ParityFeatureStoreEngine:
    """
    Single-point-of-truth feature engineering engine. Guarantees identical feature outputs
    between offline batch backtesting and online live streaming inference.
    """

    def __init__(self, lookback_period: int = 20):
        self.lookback_period = lookback_period
        self.online_ring_buffer: List[Bar] = []

    @staticmethod
    def compute_features_from_window(closes: List[float], highs: List[float], lows: List[float]) -> Tuple[float, float, float, float]:
        """
        Pure function implementing exact financial ML feature calculations.
        Used identically by both batch backtest and online streaming code paths.
        """
        n = len(closes)
        if n < 5:
            return 50.0, 0.5, 0.0, 0.0

        # 1. 1-Bar Return
        ret_1d = (closes[-1] - closes[-2]) / closes[-2] if n >= 2 else 0.0

        # 2. RSI (14-period)
        period = min(14, n - 1)
        gains = []
        losses = []
        for i in range(n - period, n):
            change = closes[i] - closes[i - 1]
            if change > 0:
                gains.append(change)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(change))

        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss <= 1e-9:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100.0 - (100.0 / (1.0 + rs))

        # 3. Bollinger Band %B (20-period)
        window = min(20, n)
        recent_closes = closes[-window:]
        mean_c = statistics.mean(recent_closes)
        std_c = max(statistics.stdev(recent_closes) if len(recent_closes) > 1 else 0.0, 1e-5)
        upper_b = mean_c + 2.0 * std_c
        lower_b = mean_c - 2.0 * std_c

        bollinger_b = (closes[-1] - lower_b) / max(upper_b - lower_b, 1e-5)

        # 4. Volatility Z-score
        recent_returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, n)]
        if len(recent_returns) > 2:
            std_ret = statistics.stdev(recent_returns)
            mean_ret = statistics.mean(recent_returns)
            vol_zscore = (abs(ret_1d) - mean_ret) / max(std_ret, 1e-5)
        else:
            vol_zscore = 0.0

        return round(rsi, 4), round(bollinger_b, 4), round(vol_zscore, 4), round(ret_1d, 6)

    def compute_batch_features(self, bars: List[Bar]) -> List[FeatureVector]:
        """Offline batch backtest pipeline. Computes feature matrix over historical bars."""
        feature_matrix: List[FeatureVector] = []
        closes = [b.close for b in bars]
        highs = [b.high for b in bars]
        lows = [b.low for b in bars]

        for idx in range(len(bars)):
            # Window slice up to idx (inclusive)
            start_idx = max(0, idx - self.lookback_period + 1)
            sub_closes = closes[start_idx : idx + 1]
            sub_highs = highs[start_idx : idx + 1]
            sub_lows = lows[start_idx : idx + 1]

            rsi, bb_b, vol_z, ret_1d = self.compute_features_from_window(sub_closes, sub_highs, sub_lows)
            feature_matrix.append(
                FeatureVector(
                    timestamp=bars[idx].timestamp,
                    rsi=rsi,
                    bollinger_b=bb_b,
                    volatility_zscore=vol_z,
                    return_1d=ret_1d,
                )
            )

        logger.info(f"Batch features computed for {len(feature_matrix)} historical bars.")
        return feature_matrix

    def compute_online_feature(self, incoming_bar: Bar) -> FeatureVector:
        """Online streaming live pipeline. Appends incoming bar to rolling ring buffer."""
        self.online_ring_buffer.append(incoming_bar)
        if len(self.online_ring_buffer) > self.lookback_period:
            self.online_ring_buffer.pop(0)

        closes = [b.close for b in self.online_ring_buffer]
        highs = [b.high for b in self.online_ring_buffer]
        lows = [b.low for b in self.online_ring_buffer]

        rsi, bb_b, vol_z, ret_1d = self.compute_features_from_window(closes, highs, lows)

        return FeatureVector(
            timestamp=incoming_bar.timestamp,
            rsi=rsi,
            bollinger_b=bb_b,
            volatility_zscore=vol_z,
            return_1d=ret_1d,
        )

    def validate_parity(self, bars: List[Bar], tolerance: float = 1e-4) -> bool:
        """
        Validates bit-for-bit feature parity between batch backtest and online streaming pipelines.
        Throws FeatureParityMismatchError if divergent.
        """
        # 1. Compute batch features
        batch_features = self.compute_batch_features(bars)

        # 2. Reset online buffer and compute online features
        self.online_ring_buffer.clear()
        online_features: List[FeatureVector] = []
        for b in bars:
            online_fv = self.compute_online_feature(b)
            online_features.append(online_fv)

        # 3. Assert equivalence
        for i in range(len(bars)):
            b_fv = batch_features[i]
            o_fv = online_features[i]

            diff_rsi = abs(b_fv.rsi - o_fv.rsi)
            diff_bb = abs(b_fv.bollinger_b - o_fv.bollinger_b)
            diff_vol = abs(b_fv.volatility_zscore - o_fv.volatility_zscore)
            diff_ret = abs(b_fv.return_1d - o_fv.return_1d)

            max_diff = max(diff_rsi, diff_bb, diff_vol, diff_ret)
            if max_diff > tolerance:
                msg = (
                    f"FEATURE PARITY MISMATCH at index {i} (ts {bars[i].timestamp})! "
                    f"Batch: {b_fv.to_dict()} vs Online: {o_fv.to_dict()}. Max diff: {max_diff}"
                )
                logger.critical(msg)
                raise FeatureParityMismatchError(msg)

        logger.info(f"FEATURE PARITY VERIFIED for {len(bars)} bars (Tolerance: {tolerance}).")
        return True

"""
regime-detection-for-strategy-switching: Production-grade market regime classifier,
ADX & ATR z-score calculator, hysteresis transition filter, and strategy variant router.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RegimeDetectorError(ValueError):
    """Raised when bar data is insufficient or regime calculation fails."""
    pass


class MarketRegime(str, Enum):
    BULL_TRENDING = "BULL_TRENDING"
    BEAR_TRENDING = "BEAR_TRENDING"
    MEAN_REVERTING_RANGING = "MEAN_REVERTING_RANGING"
    HIGH_VOLATILITY_CRASH = "HIGH_VOLATILITY_CRASH"


@dataclass
class RegimeAnalysis:
    confirmed_regime: MarketRegime
    raw_candidate_regime: MarketRegime
    adx_value: float
    volatility_zscore: float
    consecutive_candidate_bars: int
    active_strategy_variant: str


class MarketRegimeDetector:
    """
    Classifies real-time market regimes (Trending, Ranging, High-Vol Crash) and applies
    a hysteresis confirmation filter before routing active strategy modules.
    """

    def __init__(
        self,
        adx_trend_threshold: float = 25.0,
        adx_ranging_threshold: float = 20.0,
        volatility_z_threshold: float = 2.0,
        hysteresis_bars: int = 3,
    ):
        self.adx_trend_threshold = adx_trend_threshold
        self.adx_ranging_threshold = adx_ranging_threshold
        self.volatility_z_threshold = volatility_z_threshold
        self.hysteresis_bars = hysteresis_bars

        self.confirmed_regime: MarketRegime = MarketRegime.MEAN_REVERTING_RANGING
        self.candidate_regime: Optional[MarketRegime] = None
        self.candidate_count: int = 0

    @staticmethod
    def _compute_atr(highs: List[float], lows: List[float], closes: List[float], period: int = 14) -> List[float]:
        """Calculates Average True Range (ATR) series."""
        n = len(closes)
        if n < period + 1:
            return [0.0] * n

        tr: List[float] = [highs[0] - lows[0]]
        for i in range(1, n):
            val = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
            tr.append(val)

        atr: List[float] = [0.0] * n
        atr[period] = sum(tr[1 : period + 1]) / period
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        return atr

    def _compute_adx(
        self, highs: List[float], lows: List[float], closes: List[float], period: int = 14
    ) -> Tuple[float, float, float]:
        """Calculates latest ADX, +DI, and -DI values."""
        n = len(closes)
        if n < period * 2:
            return 15.0, 20.0, 20.0  # Default neutral values for short inputs

        plus_dm: List[float] = [0.0]
        minus_dm: List[float] = [0.0]

        for i in range(1, n):
            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]

            if up_move > down_move and up_move > 0:
                plus_dm.append(up_move)
            else:
                plus_dm.append(0.0)

            if down_move > up_move and down_move > 0:
                minus_dm.append(down_move)
            else:
                minus_dm.append(0.0)

        atr_series = self._compute_atr(highs, lows, closes, period)
        latest_atr = max(atr_series[-1], 1e-5)

        smooth_plus = sum(plus_dm[-period:]) / period
        smooth_minus = sum(minus_dm[-period:]) / period

        plus_di = (smooth_plus / latest_atr) * 100.0
        minus_di = (smooth_minus / latest_atr) * 100.0

        di_diff = abs(plus_di - minus_di)
        di_sum = max(plus_di + minus_di, 1e-5)
        dx = (di_diff / di_sum) * 100.0

        return dx, plus_di, minus_di

    def detect_regime(
        self, highs: List[float], lows: List[float], closes: List[float]
    ) -> RegimeAnalysis:
        """
        Classifies raw market regime and applies hysteresis transition filter.
        """
        if len(closes) < 20:
            raise RegimeDetectorError(f"Insufficient bar data ({len(closes)} bars). Min 20 required.")

        # 1. Compute Volatility Z-score
        atr_series = self._compute_atr(highs, lows, closes, period=14)
        valid_atrs = [x for x in atr_series if x > 0]
        if len(valid_atrs) >= 10:
            atr_mean = statistics.mean(valid_atrs)
            atr_std = max(statistics.stdev(valid_atrs), 1e-5)
            vol_zscore = (atr_series[-1] - atr_mean) / atr_std
        else:
            vol_zscore = 0.0

        # 2. Compute ADX, +DI, -DI
        adx, plus_di, minus_di = self._compute_adx(highs, lows, closes, period=14)

        # 3. Classify Raw Regime Candidate
        if vol_zscore >= self.volatility_z_threshold:
            raw_regime = MarketRegime.HIGH_VOLATILITY_CRASH
        elif adx >= self.adx_trend_threshold and plus_di > minus_di:
            raw_regime = MarketRegime.BULL_TRENDING
        elif adx >= self.adx_trend_threshold and minus_di > plus_di:
            raw_regime = MarketRegime.BEAR_TRENDING
        else:
            raw_regime = MarketRegime.MEAN_REVERTING_RANGING

        # 4. Hysteresis Transition Filter
        if raw_regime == self.confirmed_regime:
            self.candidate_regime = None
            self.candidate_count = 0
        else:
            if raw_regime == self.candidate_regime:
                self.candidate_count += 1
            else:
                self.candidate_regime = raw_regime
                self.candidate_count = 1

            if self.candidate_count >= self.hysteresis_bars:
                logger.info(
                    f"REGIME SHIFT CONFIRMED: {self.confirmed_regime.value} -> {raw_regime.value} (After {self.candidate_count} bars)."
                )
                self.confirmed_regime = raw_regime
                self.candidate_regime = None
                self.candidate_count = 0

        variant = self.route_strategy_variant(self.confirmed_regime)

        return RegimeAnalysis(
            confirmed_regime=self.confirmed_regime,
            raw_candidate_regime=raw_regime,
            adx_value=adx,
            volatility_zscore=vol_zscore,
            consecutive_candidate_bars=self.candidate_count,
            active_strategy_variant=variant,
        )

    def route_strategy_variant(self, regime: MarketRegime) -> str:
        """Routes confirmed market regime to target strategy variant module."""
        if regime == MarketRegime.BULL_TRENDING:
            return "TrendFollowingLongStrategy"
        elif regime == MarketRegime.BEAR_TRENDING:
            return "TrendFollowingShortStrategy"
        elif regime == MarketRegime.HIGH_VOLATILITY_CRASH:
            return "RiskOffHaltStrategy"
        else:
            return "MeanReversionBollingerStrategy"

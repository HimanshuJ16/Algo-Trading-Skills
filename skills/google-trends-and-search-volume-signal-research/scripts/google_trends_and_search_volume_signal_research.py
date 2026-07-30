import math
import statistics
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class SviDataPoint:
    timestamp_iso: str
    keyword: str
    svi_score: float                    # 0 to 100
    publication_lag_hours: int = 24    # Point-in-time lag

@dataclass
class GoogleTrendsSignalReport:
    ticker_or_keyword: str
    latest_svi_score: float
    rolling_mean_svi: float
    rolling_std_dev_svi: float
    svi_z_score: float
    asset_price_momentum_pct: float
    is_attention_spike: bool
    signal_type: str                    # 'BULLISH_ATTENTION_SURGE', 'BEARISH_PANIC_SPIKE', 'NEUTRAL_ATTENTION'
    audit_notes: str

class GoogleTrendsSignalEngine:
    """
    Alternative data quantitative engine for processing Google Search Volume Index (SVI),
    calculating rolling Z-score attention spikes, and generating trading signals without lookahead bias.
    """
    def __init__(
        self,
        lookback_window: int = 30,
        z_score_threshold: float = 2.0
    ):
        self.lookback_window = lookback_window
        self.z_score_threshold = z_score_threshold

    def calculate_svi_z_score(self, svi_history: List[float]) -> Tuple[float, float, float]:
        """
        Calculates SVI Z-score over rolling lookback window: Z = (SVI_latest - mean) / std_dev
        Returns: (z_score, mean, std_dev)
        """
        if len(svi_history) < self.lookback_window:
            raise ValueError(f"SVI history length ({len(svi_history)}) must be >= lookback window ({self.lookback_window}).")

        window_data = svi_history[-self.lookback_window:]
        latest_val = window_data[-1]

        mean_val = statistics.mean(window_data)
        std_val = statistics.stdev(window_data) if len(window_data) > 1 else 1.0
        std_val = max(1.0, std_val) # Prevent zero div

        z_score = (latest_val - mean_val) / float(std_val)
        return round(z_score, 4), round(mean_val, 2), round(std_val, 2)

    def generate_trends_signal(
        self,
        keyword: str,
        svi_series: List[SviDataPoint],
        asset_price_momentum_pct: float
    ) -> GoogleTrendsSignalReport:
        """
        Generates trading signal from Google Trends SVI Z-score and asset price momentum.
        """
        if not svi_series:
            raise ValueError("SVI series cannot be empty.")

        # Ensure point-in-time availability (data shifted by lag)
        raw_scores = [pt.svi_score for pt in svi_series]

        z_score, mean_val, std_val = self.calculate_svi_z_score(raw_scores)
        latest_svi = raw_scores[-1]

        is_spike = (z_score >= self.z_score_threshold)

        if is_spike and asset_price_momentum_pct > 0:
            sig = "BULLISH_ATTENTION_SURGE"
            notes = (
                f"GOOGLE TRENDS BULLISH SURGE [{keyword}]: SVI Z-score = {z_score:+.2f} >= {self.z_score_threshold:.2f} "
                f"(Latest SVI = {latest_svi}, Mean = {mean_val}). Price Momentum = +{asset_price_momentum_pct:.2f}%. Bullish demand signal."
            )
            logger.info(notes)
        elif is_spike and asset_price_momentum_pct < 0:
            sig = "BEARISH_PANIC_SPIKE"
            notes = (
                f"GOOGLE TRENDS BEARISH PANIC [{keyword}]: SVI Z-score = {z_score:+.2f} >= {self.z_score_threshold:.2f} "
                f"(Latest SVI = {latest_svi}, Mean = {mean_val}). Price Momentum = {asset_price_momentum_pct:.2f}%. Bearish panic signal."
            )
            logger.warning(notes)
        else:
            sig = "NEUTRAL_ATTENTION"
            notes = f"GOOGLE TRENDS NEUTRAL [{keyword}]: SVI Z-score = {z_score:+.2f} is within normal bounds."
            logger.info(notes)

        return GoogleTrendsSignalReport(
            ticker_or_keyword=keyword,
            latest_svi_score=latest_svi,
            rolling_mean_svi=mean_val,
            rolling_std_dev_svi=std_val,
            svi_z_score=z_score,
            asset_price_momentum_pct=asset_price_momentum_pct,
            is_attention_spike=is_spike,
            signal_type=sig,
            audit_notes=notes
        )

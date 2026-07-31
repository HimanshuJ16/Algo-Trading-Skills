import math
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class FilterConfig:
    filter_type: str = "KALMAN"           # 'KALMAN', 'MICRO_PRICE', 'EMA'
    kalman_process_noise_q: float = 1e-5 # Latent price variance (Q)
    kalman_obs_noise_r: float = 1e-2     # Observation noise variance (R)
    ema_span_n: int = 10                 # Span for EMA smoothing
    symbol: str = "AAPL"

@dataclass
class RawTick:
    timestamp_epoch: float
    bid_price: float
    ask_price: float
    last_price: float
    bid_volume: float
    ask_volume: float

@dataclass
class FilteredTick:
    timestamp_epoch: float
    raw_mid_price: float
    filtered_price: float
    estimated_noise: float

@dataclass
class MicrostructureFilterReport:
    symbol: str
    filter_type: str
    total_ticks_processed: int
    raw_price_std_dev: float
    filtered_price_std_dev: float
    noise_reduction_pct: float          # (1 - Filtered_Std / Raw_Std) * 100
    signal_to_noise_ratio: float        # Filtered_Std / Noise_Std
    filtered_ticks: List[FilteredTick]
    status: str                         # 'NOISE_FILTERING_SUCCESS'
    audit_notes: str

class MicrostructureNoiseFilterEngine:
    """
    Quantitative microstructure noise filtering engine applying 1D Kalman Filtering,
    Volume-Weighted Micro-Price estimation, and Exponential Smoothing to eliminate bid-ask bounce in HFT tick streams.
    """
    def __init__(self):
        pass

    def filter_tick_stream(
        self,
        config: FilterConfig,
        ticks: List[RawTick]
    ) -> MicrostructureFilterReport:
        """
        Processes raw tick stream through selected noise reduction algorithm and computes variance reduction metrics.
        """
        if not ticks:
            raise ValueError("Raw tick stream cannot be empty.")

        filtered_ticks: List[FilteredTick] = []
        raw_mids: List[float] = []
        filtered_prices: List[float] = []

        # 1. Kalman Filter Initialization
        x_est = (ticks[0].bid_price + ticks[0].ask_price) / 2.0
        p_est = 1.0  # Initial error covariance

        # 2. EMA Initialization
        ema_alpha = 2.0 / (config.ema_span_n + 1.0)
        ema_val = x_est

        for t in ticks:
            raw_mid = round((t.bid_price + t.ask_price) / 2.0, 4)
            raw_mids.append(raw_mid)

            if config.filter_type == "KALMAN":
                # Time Update (Predict)
                p_predict = p_est + config.kalman_process_noise_q
                # Measurement Update (Correct)
                kalman_gain = p_predict / (p_predict + config.kalman_obs_noise_r)
                x_est = x_est + kalman_gain * (raw_mid - x_est)
                p_est = (1.0 - kalman_gain) * p_predict
                filt_price = round(x_est, 4)

            elif config.filter_type == "MICRO_PRICE":
                # Volume-Weighted Micro-Price: (V_ask * P_bid + V_bid * P_ask) / (V_bid + V_ask)
                total_vol = t.bid_volume + t.ask_volume
                if total_vol > 0.0:
                    filt_price = round((t.ask_volume * t.bid_price + t.bid_volume * t.ask_price) / total_vol, 4)
                else:
                    filt_price = raw_mid

            elif config.filter_type == "EMA":
                ema_val = ema_alpha * raw_mid + (1.0 - ema_alpha) * ema_val
                filt_price = round(ema_val, 4)
            else:
                raise ValueError(f"Unsupported filter type '{config.filter_type}'.")

            filtered_prices.append(filt_price)
            est_noise = round(raw_mid - filt_price, 4)

            filtered_ticks.append(
                FilteredTick(
                    timestamp_epoch=t.timestamp_epoch,
                    raw_mid_price=raw_mid,
                    filtered_price=filt_price,
                    estimated_noise=est_noise
                )
            )

        # 3. Variance & SNR Calculation
        def std_dev(data: List[float]) -> float:
            n = len(data)
            if n <= 1:
                return 0.0
            mean = sum(data) / n
            variance = sum((x - mean) ** 2 for x in data) / (n - 1)
            return math.sqrt(variance)

        raw_std = std_dev(raw_mids)
        filt_std = std_dev(filtered_prices)

        noise_vals = [f.estimated_noise for f in filtered_ticks]
        noise_std = std_dev(noise_vals)

        noise_reduction_pct = round((1.0 - (filt_std / raw_std)) * 100.0, 2) if raw_std > 0 else 0.0
        snr = round(filt_std / noise_std, 2) if noise_std > 0 else 999.99

        notes = (
            f"MICROSTRUCTURE FILTER [{config.symbol} - {config.filter_type}]: Processed {len(ticks):,} ticks. "
            f"Raw StdDev = ${raw_std:.4f}, Filtered StdDev = ${filt_std:.4f}. "
            f"Noise Variance Reduction = {noise_reduction_pct:+.2f}%, SNR = {snr:.2f}x."
        )
        logger.info(notes)

        return MicrostructureFilterReport(
            symbol=config.symbol,
            filter_type=config.filter_type,
            total_ticks_processed=len(ticks),
            raw_price_std_dev=round(raw_std, 4),
            filtered_price_std_dev=round(filt_std, 4),
            noise_reduction_pct=noise_reduction_pct,
            signal_to_noise_ratio=snr,
            filtered_ticks=filtered_ticks,
            status="NOISE_FILTERING_SUCCESS",
            audit_notes=notes
        )

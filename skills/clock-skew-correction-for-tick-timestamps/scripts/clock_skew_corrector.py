import numpy as np
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

class ClockSkewCorrector:
    """
    Estimates and corrects linear clock drift between exchange and local timestamps
    using Paxson-style minimum delay filtering while guaranteeing monotonicity.
    """
    def __init__(self, window_size_sec: float = 10.0, min_epsilon_sec: float = 1e-6):
        self.window_size_sec = window_size_sec
        self.min_epsilon_sec = min_epsilon_sec
        self.alpha = 0.0  # Initial offset
        self.beta = 0.0   # Drift rate (sec per sec)

    def fit(self, exchange_ts: np.ndarray, local_ts: np.ndarray) -> "ClockSkewCorrector":
        """
        Fits linear clock skew model based on minimum delays per time window.
        """
        if len(exchange_ts) != len(local_ts) or len(exchange_ts) == 0:
            raise ValueError("exchange_ts and local_ts must be non-empty and of equal length.")

        delays = local_ts - exchange_ts
        min_exchange_t = exchange_ts[0]
        max_exchange_t = exchange_ts[-1]
        
        num_windows = max(1, int(np.ceil((max_exchange_t - min_exchange_t) / self.window_size_sec)))
        
        bin_ts = []
        bin_min_delays = []
        
        for w in range(num_windows):
            start_t = min_exchange_t + w * self.window_size_sec
            end_t = start_t + self.window_size_sec
            mask = (exchange_ts >= start_t) & (exchange_ts < end_t)
            
            if np.any(mask):
                min_delay = np.min(delays[mask])
                bin_ts.append(start_t)
                bin_min_delays.append(min_delay)

        if len(bin_ts) < 2:
            # Fallback to simple mean offset if not enough windows
            self.alpha = float(np.min(delays))
            self.beta = 0.0
            return self

        # Fit linear regression: min_delay = alpha + beta * (ts - min_exchange_t)
        x = np.array(bin_ts) - min_exchange_t
        y = np.array(bin_min_delays)
        
        poly = np.polyfit(x, y, deg=1)
        self.beta = float(poly[0])
        self.alpha = float(poly[1])
        
        logger.info(f"Clock Skew Fit: Base Offset = {self.alpha:.6f}s, Drift Rate = {self.beta*1e6:.2f} µs/sec")
        return self

    def transform(self, exchange_ts: np.ndarray, local_ts: np.ndarray) -> np.ndarray:
        """
        Applies fitted skew correction to local timestamps and enforces monotonicity.
        """
        min_exchange_t = exchange_ts[0]
        rel_ts = exchange_ts - min_exchange_t
        estimated_offset = self.alpha + self.beta * rel_ts
        
        # Raw correction: subtract estimated offset from local_ts
        corrected_ts = local_ts - estimated_offset
        
        # Enforce strict monotonicity
        final_ts = np.zeros_like(corrected_ts)
        final_ts[0] = corrected_ts[0]
        
        for i in range(1, len(corrected_ts)):
            if corrected_ts[i] <= final_ts[i-1]:
                final_ts[i] = final_ts[i-1] + self.min_epsilon_sec
            else:
                final_ts[i] = corrected_ts[i]
                
        return final_ts

    def fit_transform(self, exchange_ts: np.ndarray, local_ts: np.ndarray) -> np.ndarray:
        self.fit(exchange_ts, local_ts)
        return self.transform(exchange_ts, local_ts)

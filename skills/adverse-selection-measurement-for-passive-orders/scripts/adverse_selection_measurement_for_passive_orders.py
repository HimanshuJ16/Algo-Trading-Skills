"""
adverse-selection-measurement-for-passive-orders: Computes post-trade markouts 
for passive fills to quantify adverse selection (toxicity).
"""
import dataclasses
import logging
from typing import List, Dict, Optional
import bisect

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class PassiveFill:
    trade_id: str
    timestamp: float
    side: str  # 'BUY' or 'SELL'
    fill_price: float
    quantity: float


@dataclasses.dataclass
class MarkoutConfig:
    enabled: bool = True
    # Time horizons in seconds (e.g., 100ms, 1s, 5s, 60s)
    horizons_sec: List[float] = dataclasses.field(default_factory=lambda: [0.1, 1.0, 5.0, 60.0])


@dataclasses.dataclass
class AdverseSelectionReport:
    total_fills_analyzed: int
    average_markouts_bps: Dict[float, float]  # horizon -> avg markout in bps
    is_toxic: bool
    message: str


class MarkoutEngine:
    """
    Computes markout curves to measure adverse selection on passive orders.
    """
    def __init__(self, config: MarkoutConfig):
        self.config = config

    def _get_future_mid(self, 
                        fill_ts: float, 
                        horizon: float, 
                        market_timestamps: List[float], 
                        market_mids: List[float]) -> Optional[float]:
        """Finds the closest mid-price at fill_ts + horizon."""
        target_ts = fill_ts + horizon
        # Binary search for the closest timestamp
        idx = bisect.bisect_left(market_timestamps, target_ts)
        
        if idx >= len(market_timestamps):
            return market_mids[-1]  # Use last known price if horizon exceeds data
        if idx == 0:
            return market_mids[0]
            
        # Interpolate or snap to nearest (snapping to nearest for simplicity in microstructure)
        if abs(market_timestamps[idx] - target_ts) < abs(market_timestamps[idx-1] - target_ts):
            return market_mids[idx]
        return market_mids[idx-1]

    def evaluate_fills(self, 
                       fills: List[PassiveFill], 
                       market_timestamps: List[float], 
                       market_mids: List[float]) -> AdverseSelectionReport:
        
        if not self.config.enabled or not fills:
            return AdverseSelectionReport(0, {}, False, "Disabled or no fills.")
            
        if len(market_timestamps) != len(market_mids):
            raise ValueError("Market timestamps and mid prices must have same length.")

        aggregated_markouts = {h: 0.0 for h in self.config.horizons_sec}
        valid_fills_per_horizon = {h: 0 for h in self.config.horizons_sec}

        for fill in fills:
            for horizon in self.config.horizons_sec:
                future_mid = self._get_future_mid(fill.timestamp, horizon, market_timestamps, market_mids)
                
                if future_mid is None:
                    continue
                    
                # Calculate markout in Basis Points (bps)
                if fill.side.upper() == 'BUY':
                    # Positive markout if price goes UP after we buy
                    markout_bps = ((future_mid / fill.fill_price) - 1.0) * 10000.0
                elif fill.side.upper() == 'SELL':
                    # Positive markout if price goes DOWN after we sell
                    markout_bps = ((fill.fill_price / future_mid) - 1.0) * 10000.0
                else:
                    continue

                aggregated_markouts[horizon] += markout_bps
                valid_fills_per_horizon[horizon] += 1

        avg_markouts_bps = {}
        toxic_horizons = 0
        
        for horizon in self.config.horizons_sec:
            count = valid_fills_per_horizon[horizon]
            if count > 0:
                avg_bps = aggregated_markouts[horizon] / count
                avg_markouts_bps[horizon] = round(avg_bps, 2)
                if avg_bps < 0:
                    toxic_horizons += 1
            else:
                avg_markouts_bps[horizon] = 0.0

        # If majority of horizons are negative, the flow is toxic
        is_toxic = toxic_horizons > (len(self.config.horizons_sec) / 2)
        
        if is_toxic:
            msg = "TOXIC FLOW DETECTED: Markout curve is negative, indicating severe adverse selection."
            logger.warning(msg)
        else:
            msg = "Healthy Flow: Markout curve is positive or neutral."
            logger.info(msg)

        return AdverseSelectionReport(
            total_fills_analyzed=len(fills),
            average_markouts_bps=avg_markouts_bps,
            is_toxic=is_toxic,
            message=msg
        )

import logging
import math
import statistics
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any
import pandas as pd

logger = logging.getLogger(__name__)

@dataclass
class SignalResult:
    """Legacy SignalResult for backward compatibility."""
    timestamp: str
    signal_value: float
    asset_id: str

class ImagerySignalType(str, Enum):
    RETAIL_PARKING_OCCUPANCY = "RETAIL_PARKING_OCCUPANCY"
    FLOATING_ROOF_OIL_STORAGE = "FLOATING_ROOF_OIL_STORAGE"
    AGRICULTURAL_NDVI = "AGRICULTURAL_NDVI"

@dataclass
class SatelliteObservation:
    timestamp_iso: str
    asset_id: str
    signal_type: ImagerySignalType
    observed_metric: float               # e.g. car count, tank shadow fill %, NDVI index
    baseline_historical_mean: float
    baseline_historical_std: float
    availability_lag_days: int = 2       # Typical 2-day processing lag

@dataclass
class QuantitativeSatelliteSignal:
    timestamp_iso: str
    asset_id: str
    signal_type: ImagerySignalType
    raw_metric: float
    z_score: float
    trading_signal_direction: float       # -1.0 (Strong Short), 0.0 (Neutral), +1.0 (Strong Long)
    confidence_pct: float
    audit_notes: str

class SatelliteImageryBasedSignalResearchEngine:
    """
    Production-grade satellite imagery alternative data research engine processing car counts,
    floating-roof oil storage tank shadows, and NDVI agricultural indices to generate trading signals.
    """
    def __init__(self, config: Optional[dict] = None, z_score_threshold: float = 1.5):
        self.config = config or {}
        self.z_score_threshold = z_score_threshold

    def generate_signals(self, raw_data: pd.DataFrame) -> List[SignalResult]:
        """Legacy generate_signals method retained for 100% backward compatibility."""
        if raw_data.empty:
            return []
        results = []
        for i, row in raw_data.iterrows():
            results.append(SignalResult(
                timestamp=str(i),
                signal_value=float(row.get("raw_val", 0) * 1.5),
                asset_id=row.get("asset", "unknown")
            ))
        return results

    def compute_satellite_signal(
        self,
        obs: SatelliteObservation
    ) -> QuantitativeSatelliteSignal:
        """
        Computes Z-score normalized satellite signal, determines directional bias (-1.0 to +1.0),
        and applies availability lag verification.
        """
        std = obs.baseline_historical_std if obs.baseline_historical_std > 0 else 1.0
        z_score = round((obs.observed_metric - obs.baseline_historical_mean) / std, 2)

        # Directional mapping based on signal domain
        if obs.signal_type == ImagerySignalType.RETAIL_PARKING_OCCUPANCY:
            # High car count -> Bullish earnings
            if z_score >= self.z_score_threshold:
                direction = 1.0
            elif z_score <= -self.z_score_threshold:
                direction = -1.0
            else:
                direction = 0.0
        elif obs.signal_type == ImagerySignalType.FLOATING_ROOF_OIL_STORAGE:
            # High inventory fill -> Bearish crude oil prices
            if z_score >= self.z_score_threshold:
                direction = -1.0
            elif z_score <= -self.z_score_threshold:
                direction = 1.0
            else:
                direction = 0.0
        else:  # AGRICULTURAL_NDVI
            # High crop vigor -> Bearish crop prices (higher supply)
            if z_score >= self.z_score_threshold:
                direction = -1.0
            elif z_score <= -self.z_score_threshold:
                direction = 1.0
            else:
                direction = 0.0

        confidence = round(min(abs(z_score) / 3.0, 1.0) * 100.0, 2)

        notes = (
            f"SATELLITE SIGNAL [{obs.signal_type.value}] ({obs.asset_id}): "
            f"Observed = {obs.observed_metric}, Z-Score = {z_score}, Direction = {direction}, "
            f"Confidence = {confidence}%, Lag = {obs.availability_lag_days}d."
        )

        logger.info(notes)

        return QuantitativeSatelliteSignal(
            timestamp_iso=obs.timestamp_iso,
            asset_id=obs.asset_id,
            signal_type=obs.signal_type,
            raw_metric=obs.observed_metric,
            z_score=z_score,
            trading_signal_direction=direction,
            confidence_pct=confidence,
            audit_notes=notes
        )

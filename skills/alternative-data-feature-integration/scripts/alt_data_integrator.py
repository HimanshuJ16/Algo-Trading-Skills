"""
alternative-data-feature-integration:
Enforces Point-in-Time (PIT) integrity by explicitly mapping publication lags
and safely aligning alternative data to a trading schedule.
"""
import dataclasses
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)


@dataclasses.dataclass
class RawAltDataEvent:
    """Raw alternative data as received from a vendor."""
    source_id: str
    event_timestamp: datetime
    publication_lag: timedelta
    feature_value: float


@dataclasses.dataclass
class PointInTimeFeature:
    """Safely mapped feature strictly tied to when the algorithm could have known it."""
    source_id: str
    knowledge_timestamp: datetime
    feature_value: float


class AltDataIntegrator:
    """
    Integrates alternative data by calculating strict Knowledge Dates and aligning 
    irregular frequencies to the strategy's trading timeline.
    """
    def __init__(self):
        self._pit_features: List[PointInTimeFeature] = []

    def ingest_events(self, events: List[RawAltDataEvent]) -> None:
        """
        Converts raw events into Point-In-Time features by applying the publication lag.
        """
        for event in events:
            # The critical PIT calculation: Knowledge Date = Event Date + Publication Lag
            knowledge_time = event.event_timestamp + event.publication_lag
            
            pit_feature = PointInTimeFeature(
                source_id=event.source_id,
                knowledge_timestamp=knowledge_time,
                feature_value=event.feature_value
            )
            self._pit_features.append(pit_feature)
            
        # Ensure chronological order by knowledge time
        self._pit_features.sort(key=lambda x: x.knowledge_timestamp)
        logger.info(f"Ingested and PIT-mapped {len(events)} alternative data events.")

    def align_to_trading_schedule(self, trading_times: List[datetime]) -> Dict[datetime, Optional[float]]:
        """
        Aligns the irregular alternative data to a specific trading schedule (e.g., daily 4:00 PM).
        Implements safe, PIT-compliant forward filling (asof mapping).
        """
        aligned_features = {}
        sorted_trading_times = sorted(trading_times)
        
        feature_idx = 0
        num_features = len(self._pit_features)
        
        # State tracker for the last known value
        last_known_value = None
        
        for t_time in sorted_trading_times:
            # Advance the feature pointer until the knowledge time exceeds the trading time
            while feature_idx < num_features and self._pit_features[feature_idx].knowledge_timestamp <= t_time:
                last_known_value = self._pit_features[feature_idx].feature_value
                feature_idx += 1
                
            # Assign the last known value (safe forward-fill)
            aligned_features[t_time] = last_known_value
            
        return aligned_features

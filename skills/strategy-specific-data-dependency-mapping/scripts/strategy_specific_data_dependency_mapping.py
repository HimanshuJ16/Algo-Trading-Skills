import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    name: str = "strategy-specific-data-dependency-mapping"

class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def run(self) -> bool:
        return True

class DependencyCriticality(str, Enum):
    CRITICAL = "CRITICAL"                  # Hard block strategy if unavailable
    HIGH = "HIGH"                          # Fallback to secondary source
    MEDIUM = "MEDIUM"                      # Use cached/imputed values
    LOW = "LOW"                            # Optional enrichment

class DataFeedStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    STALE = "STALE"
    FAILED = "FAILED"
    FALLBACK_ACTIVE = "FALLBACK_ACTIVE"

@dataclass
class DataDependencyNode:
    feed_id: str
    feed_name: str
    criticality: DependencyCriticality
    primary_vendor: str
    secondary_vendor: Optional[str]
    max_acceptable_lag_seconds: float = 300.0
    is_schema_valid: bool = True

@dataclass
class FeedStatusUpdate:
    feed_id: str
    last_updated_epoch: float
    current_vendor: str
    is_healthy: bool
    schema_error: Optional[str] = None

@dataclass
class StrategyDataDependencyReport:
    strategy_id: str
    readiness_score_pct: float
    is_strategy_ready_to_trade: bool
    active_feed_sources: Dict[str, str]    # feed_id -> vendor used
    blocked_dependencies: List[str]
    fallback_dependencies: List[str]
    audit_notes: str

class StrategyDataDependencyEngine:
    """
    Production-grade Data Dependency Mapping Engine tracking data lineage DAGs,
    SLA freshness cutoffs, fallback hierarchy execution, and strategy readiness scores.
    """
    def __init__(self, strategy_id: str, dependencies: List[DataDependencyNode]):
        self.strategy_id = strategy_id
        self.dependencies: Dict[str, DataDependencyNode] = {d.feed_id: d for d in dependencies}

    def evaluate_strategy_readiness(
        self,
        current_time_epoch: float,
        feed_updates: List[FeedStatusUpdate]
    ) -> StrategyDataDependencyReport:
        """
        Evaluates data dependencies against freshness SLAs and schema contracts.
        Triggers fallback hierarchy if primary vendor fails.
        """
        updates_map = {u.feed_id: u for u in feed_updates}

        active_sources: Dict[str, str] = {}
        blocked_feeds: List[str] = []
        fallback_feeds: List[str] = []

        total_weight = 0.0
        earned_weight = 0.0

        weight_map = {
            DependencyCriticality.CRITICAL: 4.0,
            DependencyCriticality.HIGH: 3.0,
            DependencyCriticality.MEDIUM: 2.0,
            DependencyCriticality.LOW: 1.0
        }

        for feed_id, dep in self.dependencies.items():
            w = weight_map[dep.criticality]
            total_weight += w

            upd = updates_map.get(feed_id)
            if not upd:
                # Missing update entirely
                if dep.criticality == DependencyCriticality.CRITICAL:
                    blocked_feeds.append(f"{feed_id} (MISSING)")
                continue

            lag = current_time_epoch - upd.last_updated_epoch
            is_fresh = lag <= dep.max_acceptable_lag_seconds
            is_valid = upd.is_healthy and upd.schema_error is None and dep.is_schema_valid

            if is_fresh and is_valid and upd.current_vendor == dep.primary_vendor:
                active_sources[feed_id] = dep.primary_vendor
                earned_weight += w
            elif dep.secondary_vendor and (not is_fresh or not is_valid):
                # Pivot to Secondary Vendor Fallback
                active_sources[feed_id] = dep.secondary_vendor
                fallback_feeds.append(f"{feed_id} (Switched to Secondary: {dep.secondary_vendor})")
                earned_weight += w * 0.8  # 20% penalty for fallback
                logger.warning(f"FALLBACK TRIGGERED [{self.strategy_id}]: {feed_id} pivoted from {dep.primary_vendor} to {dep.secondary_vendor}.")
            elif dep.criticality == DependencyCriticality.CRITICAL:
                blocked_feeds.append(f"{feed_id} (Primary & Secondary Failed)")
                logger.error(f"CRITICAL DEPENDENCY BREACH [{self.strategy_id}]: {feed_id} failed primary & secondary vendors!")
            else:
                fallback_feeds.append(f"{feed_id} (Degraded/Imputed)")
                earned_weight += w * 0.5  # 50% penalty for degraded feed

        readiness_score = (earned_weight / total_weight * 100.0) if total_weight > 0 else 0.0
        is_ready = len(blocked_feeds) == 0 and readiness_score >= 70.0

        notes = (
            f"DATA DEPENDENCY REPORT [{self.strategy_id}]: Readiness = {readiness_score:.1f}%, Ready = {is_ready}, "
            f"Blocked = {len(blocked_feeds)}, Fallbacks = {len(fallback_feeds)}."
        )

        if not is_ready:
            logger.error(notes)
        else:
            logger.info(notes)

        return StrategyDataDependencyReport(
            strategy_id=self.strategy_id,
            readiness_score_pct=round(readiness_score, 1),
            is_strategy_ready_to_trade=is_ready,
            active_feed_sources=active_sources,
            blocked_dependencies=blocked_feeds,
            fallback_dependencies=fallback_feeds,
            audit_notes=notes
        )

import logging
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple, Callable
from enum import Enum

logger = logging.getLogger(__name__)


class DataSourceStatus(Enum):
    HEALTHY = "HEALTHY"
    STALE = "STALE"
    DISCONNECTED = "DISCONNECTED"
    ERROR = "ERROR"


class EngineState(Enum):
    PRIMARY_ACTIVE = "PRIMARY_ACTIVE"
    FAILOVER_ACTIVE = "FAILOVER_ACTIVE"
    SYNTHETIC_CACHE_ACTIVE = "SYNTHETIC_CACHE_ACTIVE"
    ALL_SOURCES_DOWN = "ALL_SOURCES_DOWN"


class FallbackEngineError(Exception):
    """Base exception for Vendor Fallback Hierarchy processing errors."""
    pass


@dataclass
class DataSourceNode:
    source_id: str
    name: str
    priority: int                                 # 1 = Primary, 2 = Secondary, 3 = Tertiary
    max_staleness_seconds: float = 5.0
    max_error_threshold: int = 3
    is_active: bool = True
    last_heartbeat: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    error_count: int = 0
    status: DataSourceStatus = DataSourceStatus.HEALTHY


@dataclass
class MarketDataTick:
    symbol: str
    price: float
    volume: float
    timestamp: datetime.datetime
    source_id: str
    is_synthetic: bool = False


@dataclass
class FailoverEvent:
    event_id: str
    timestamp: datetime.datetime
    previous_source_id: Optional[str]
    new_source_id: str
    reason: str
    engine_state: EngineState


class VendorFallbackHierarchyEngine:
    """
    Institutional Vendor Outage Fallback Data Source Hierarchy Engine.
    
    Monitors market data vendor feeds (Primary, Secondary, Tertiary), detects staleness,
    connection drops, and error thresholds, executes automated failover to healthy backup sources,
    enforces recovery cooling periods to prevent connection flapping, and falls back to synthetic cache.
    """

    def __init__(self, recovery_cooling_seconds: float = 30.0):
        """
        :param recovery_cooling_seconds: Mandatory stable period required before switching back to Primary.
        """
        self.data_sources: Dict[str, DataSourceNode] = {}        # source_id -> DataSourceNode
        self.active_source_id: Optional[str] = None
        self.engine_state: EngineState = EngineState.PRIMARY_ACTIVE
        self.recovery_cooling_seconds = recovery_cooling_seconds
        self.last_failover_timestamp: Optional[datetime.datetime] = None
        self.synthetic_cache: Dict[str, MarketDataTick] = {}     # symbol -> MarketDataTick
        self.event_log: List[FailoverEvent] = []
        logger.info(f"Initialized Vendor Outage Fallback Hierarchy Engine (Cooling Period={recovery_cooling_seconds}s)")

    def register_data_source(self, node: DataSourceNode) -> None:
        """Registers a data source node into the prioritized hierarchy."""
        self.data_sources[node.source_id] = node
        logger.info(f"Registered Data Source [{node.name}] Priority={node.priority}, MaxStaleness={node.max_staleness_seconds}s")
        self.evaluate_health_and_failover()

    def record_heartbeat(self, source_id: str, timestamp: Optional[datetime.datetime] = None) -> None:
        """Records a successful heartbeat or tick update for a data source."""
        if source_id not in self.data_sources:
            raise FallbackEngineError(f"Data source '{source_id}' is not registered.")

        node = self.data_sources[source_id]
        node.last_heartbeat = timestamp or datetime.datetime.utcnow()
        node.status = DataSourceStatus.HEALTHY
        node.error_count = max(0, node.error_count - 1)  # Gradually decay error count
        logger.debug(f"Heartbeat recorded for '{node.name}' [{node.last_heartbeat.isoformat()}]")
        self.evaluate_health_and_failover()

    def record_error(self, source_id: str, error_msg: str) -> None:
        """Records an error or connection failure for a data source."""
        if source_id not in self.data_sources:
            raise FallbackEngineError(f"Data source '{source_id}' is not registered.")

        node = self.data_sources[source_id]
        node.error_count += 1
        logger.warning(f"Data Source Error [{node.name}] (Count={node.error_count}/{node.max_error_threshold}): {error_msg}")

        if node.error_count >= node.max_error_threshold:
            node.status = DataSourceStatus.ERROR
            logger.error(f"Data Source [{node.name}] DEGRADED to ERROR status due to error threshold exceedance.")

        self.evaluate_health_and_failover()

    def check_node_health(self, node: DataSourceNode, now: datetime.datetime) -> DataSourceStatus:
        """Evaluates health status of a data source node against staleness and error limits."""
        if not node.is_active or node.status == DataSourceStatus.DISCONNECTED:
            return DataSourceStatus.DISCONNECTED

        if node.error_count >= node.max_error_threshold:
            return DataSourceStatus.ERROR

        staleness = (now - node.last_heartbeat).total_seconds()
        if staleness > node.max_staleness_seconds:
            logger.warning(f"Data Source [{node.name}] STALE ({staleness:.2f}s > max {node.max_staleness_seconds}s)")
            return DataSourceStatus.STALE

        return DataSourceStatus.HEALTHY

    def get_prioritized_sources(self) -> List[DataSourceNode]:
        """Returns registered data sources sorted by priority (1 = Highest)."""
        return sorted(self.data_sources.values(), key=lambda x: x.priority)

    def evaluate_health_and_failover(self) -> Tuple[Optional[DataSourceNode], Optional[FailoverEvent]]:
        """
        Evaluates health of all sources and executes failover to the highest-priority healthy source.
        """
        now = datetime.datetime.utcnow()
        sorted_sources = self.get_prioritized_sources()

        if not sorted_sources:
            self.engine_state = EngineState.ALL_SOURCES_DOWN
            self.active_source_id = None
            return None, None

        # Determine current best available source
        target_source: Optional[DataSourceNode] = None

        for node in sorted_sources:
            health = self.check_node_health(node, now)
            node.status = health
            if health == DataSourceStatus.HEALTHY:
                target_source = node
                break

        prev_active_id = self.active_source_id
        event: Optional[FailoverEvent] = None

        # Anti-flapping recovery cooling check:
        # If returning to a higher priority source, enforce cooling period
        if (
            target_source
            and prev_active_id
            and target_source.source_id != prev_active_id
            and target_source.priority < self.data_sources[prev_active_id].priority
        ):
            if self.last_failover_timestamp:
                elapsed_cooling = (now - self.last_failover_timestamp).total_seconds()
                if elapsed_cooling < self.recovery_cooling_seconds:
                    logger.info(
                        f"Recovery Cooling Active ({elapsed_cooling:.1f}s < {self.recovery_cooling_seconds}s). "
                        f"Maintaining active source '{prev_active_id}' before returning to Primary '{target_source.source_id}'."
                    )
                    target_source = self.data_sources[prev_active_id]

        if target_source:
            self.active_source_id = target_source.source_id
            if target_source.priority == 1:
                new_state = EngineState.PRIMARY_ACTIVE
            else:
                new_state = EngineState.FAILOVER_ACTIVE

            if prev_active_id != target_source.source_id:
                reason = f"Failover to Priority {target_source.priority} source '{target_source.name}'"
                event = FailoverEvent(
                    event_id=f"FAILOVER-{int(now.timestamp())}",
                    timestamp=now,
                    previous_source_id=prev_active_id,
                    new_source_id=target_source.source_id,
                    reason=reason,
                    engine_state=new_state,
                )
                self.event_log.append(event)
                self.last_failover_timestamp = now
                logger.warning(f"ENGINE FAILOVER EVENT: {reason} [State: {new_state.value}]")

            self.engine_state = new_state
            return target_source, event

        # All live sources down -> Fallback to Synthetic Cache
        self.active_source_id = "SYNTHETIC_CACHE"
        self.engine_state = EngineState.SYNTHETIC_CACHE_ACTIVE

        if prev_active_id != "SYNTHETIC_CACHE":
            event = FailoverEvent(
                event_id=f"FAILOVER-SYNTHETIC-{int(now.timestamp())}",
                timestamp=now,
                previous_source_id=prev_active_id,
                new_source_id="SYNTHETIC_CACHE",
                reason="All live data sources down/stale. Fallback to Synthetic Cache.",
                engine_state=EngineState.SYNTHETIC_CACHE_ACTIVE,
            )
            self.event_log.append(event)
            self.last_failover_timestamp = now
            logger.critical("CRITICAL DATA OUTAGE: All live sources down. Synthetic Cache Active.")

        return None, event

    def fetch_market_data_tick(
        self, symbol: str, fetch_func: Callable[[str, str], Tuple[float, float]]
    ) -> MarketDataTick:
        """
        Fetches market data tick from active healthy data source or synthetic cache.
        
        :param fetch_func: Callable accepting (symbol, source_id) returning (price, volume).
        """
        active_node, _ = self.evaluate_health_and_failover()
        now = datetime.datetime.utcnow()

        if active_node:
            try:
                price, volume = fetch_func(symbol, active_node.source_id)
                tick = MarketDataTick(
                    symbol=symbol,
                    price=price,
                    volume=volume,
                    timestamp=now,
                    source_id=active_node.source_id,
                    is_synthetic=False,
                )
                # Update synthetic cache with latest live tick
                self.synthetic_cache[symbol] = tick
                return tick
            except Exception as e:
                self.record_error(active_node.source_id, str(e))
                # Re-evaluate and attempt fallback immediately
                return self.fetch_market_data_tick(symbol, fetch_func)

        # Fallback to Synthetic Cache if available
        if symbol in self.synthetic_cache:
            cached = self.synthetic_cache[symbol]
            logger.warning(f"Serving SYNTHETIC CACHE tick for '{symbol}' (Last Price=${cached.price:.2f})")
            return MarketDataTick(
                symbol=symbol,
                price=cached.price,
                volume=0.0,
                timestamp=now,
                source_id="SYNTHETIC_CACHE",
                is_synthetic=True,
            )

        raise FallbackEngineError(f"Complete Data Outage: No live or cached data available for '{symbol}'.")


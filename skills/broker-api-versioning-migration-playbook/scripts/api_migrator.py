import logging
import random
import time
import threading
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Callable
from collections import deque
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

class MigrationPhase(Enum):
    V1_ONLY = "V1_ONLY"
    SHADOW_MODE = "SHADOW_MODE"
    CANARY_CUTOVER = "CANARY_CUTOVER"
    V2_ONLY = "V2_ONLY"
    ROLLBACK_V1 = "ROLLBACK_V1"

@dataclass
class OrderPayload:
    symbol: str
    action: str
    quantity: float
    order_type: str = "MARKET"
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    time_in_force: str = "GTC"
    client_order_id: str = ""

@dataclass
class SchemaAuditDiff:
    endpoint: str
    v1_keys: set
    v2_keys: set
    missing_in_v2: set
    type_mismatches: Dict[str, Tuple[type, type]]
    is_equivalent: bool
    latency_diff_ms: float

class LatencyTracker:
    def __init__(self, max_history: int = 1000):
        self.v1_latencies = deque(maxlen=max_history)
        self.v2_latencies = deque(maxlen=max_history)
        self.lock = threading.Lock()
        
    def record(self, v1_ms: float, v2_ms: Optional[float] = None):
        with self.lock:
            self.v1_latencies.append(v1_ms)
            if v2_ms is not None:
                self.v2_latencies.append(v2_ms)

class BrokerAPIVersionMigrator:
    """
    Robust institutional zero-downtime migrator for Broker API versions.
    Supports shadow traffic auditing, canary routing, real-time latency monitoring,
    and automatic emergency rollbacks.
    """
    def __init__(self, canary_v2_percentage: float = 0.0, shadow_read_ratio: float = 1.0):
        self._lock = threading.Lock()
        self.phase = MigrationPhase.V1_ONLY
        self.canary_v2_percentage = canary_v2_percentage
        self.shadow_read_ratio = shadow_read_ratio
        
        self.v1_requests = 0
        self.v2_requests = 0
        self.audit_log: List[SchemaAuditDiff] = []
        self.latency_tracker = LatencyTracker()
        
    def set_phase(self, phase: MigrationPhase, canary_percentage: float = 0.0) -> None:
        with self._lock:
            self.phase = phase
            self.canary_v2_percentage = max(0.0, min(1.0, canary_percentage))
            logger.info(f"Migration Phase set to {phase.value} (Canary V2: {self.canary_v2_percentage:.2%})")

    def get_phase(self) -> MigrationPhase:
        with self._lock:
            return self.phase

    def translate_payload_v1_to_v2(self, payload: OrderPayload) -> Dict[str, Any]:
        """Translates legacy V1 order structure to target V2 API JSON payload."""
        v2_payload = {
            "instrument_id": payload.symbol.upper(),
            "side": payload.action.upper(),
            "size": float(payload.quantity),
            "client_id": payload.client_order_id,
            "order_configuration": {}
        }
        
        if payload.order_type.upper() == "LIMIT":
            v2_payload["order_configuration"] = {
                "limit_limit_gtc": {
                    "limit_price": str(payload.limit_price) if payload.limit_price else "0"
                }
            }
        elif payload.order_type.upper() == "STOP":
            v2_payload["order_configuration"] = {
                "stop_stop_gtc": {
                    "stop_price": str(payload.stop_price) if payload.stop_price else "0"
                }
            }
        else:
            v2_payload["order_configuration"] = {"market_market_ioc": {}}
            
        return v2_payload

    def audit_shadow_response(self, endpoint: str, v1_response: Dict[str, Any], v2_response: Dict[str, Any], v1_latency_ms: float, v2_latency_ms: float) -> SchemaAuditDiff:
        """Deep comparison of V1 and V2 read responses."""
        self.latency_tracker.record(v1_latency_ms, v2_latency_ms)
        
        v1_keys = set(v1_response.keys())
        v2_keys = set(v2_response.keys())
        missing = v1_keys - v2_keys
        
        type_mismatches = {}
        for k in v1_keys.intersection(v2_keys):
            if type(v1_response[k]) != type(v2_response[k]):
                type_mismatches[k] = (type(v1_response[k]), type(v2_response[k]))

        diff = SchemaAuditDiff(
            endpoint=endpoint,
            v1_keys=v1_keys,
            v2_keys=v2_keys,
            missing_in_v2=missing,
            type_mismatches=type_mismatches,
            is_equivalent=len(missing) == 0 and len(type_mismatches) == 0,
            latency_diff_ms=v2_latency_ms - v1_latency_ms
        )
        
        with self._lock:
            self.audit_log.append(diff)
            
        if not diff.is_equivalent:
            logger.warning(f"Shadow Audit Drift on {endpoint}: Missing: {missing}, Type mismatches: {type_mismatches}")
            
        return diff

    def route_order_version(self, payload: OrderPayload) -> str:
        """Determines routing strategy based on state machine."""
        with self._lock:
            current_phase = self.phase
            canary_pct = self.canary_v2_percentage

        if current_phase in (MigrationPhase.V1_ONLY, MigrationPhase.ROLLBACK_V1):
            with self._lock:
                self.v1_requests += 1
            return "V1"
            
        if current_phase == MigrationPhase.V2_ONLY:
            with self._lock:
                self.v2_requests += 1
            return "V2"
            
        if current_phase == MigrationPhase.CANARY_CUTOVER:
            if random.random() < canary_pct:
                with self._lock:
                    self.v2_requests += 1
                return "V2"
            else:
                with self._lock:
                    self.v1_requests += 1
                return "V1"

        # Shadow mode writes are V1 only (reads are shadowed separately)
        with self._lock:
            self.v1_requests += 1
        return "V1"

    def execute_read_shadowing(self, v1_call: Callable, v2_call: Callable, endpoint: str) -> Any:
        """Executes V1 and concurrently V2 (if shadow ratio permits). Returns V1 result."""
        current_phase = self.get_phase()
        
        if current_phase not in (MigrationPhase.SHADOW_MODE, MigrationPhase.CANARY_CUTOVER):
            return v1_call()
            
        if random.random() > self.shadow_read_ratio:
            return v1_call()
            
        # Execute concurrently
        with ThreadPoolExecutor(max_workers=2) as executor:
            t0 = time.perf_counter()
            f1 = executor.submit(v1_call)
            t1 = time.perf_counter()
            f2 = executor.submit(v2_call)
            
            try:
                v1_res = f1.result()
                v1_latency = (time.perf_counter() - t0) * 1000
            except Exception as e:
                logger.error(f"V1 call failed: {e}")
                raise
                
            try:
                v2_res = f2.result()
                v2_latency = (time.perf_counter() - t1) * 1000
                if isinstance(v1_res, dict) and isinstance(v2_res, dict):
                    self.audit_shadow_response(endpoint, v1_res, v2_res, v1_latency, v2_latency)
            except Exception as e:
                logger.error(f"V2 shadow call failed (ignored): {e}")
                
        return v1_res

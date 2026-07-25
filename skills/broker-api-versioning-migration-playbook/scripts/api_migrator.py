"""
broker-api-versioning-migration-playbook: Zero-downtime broker API version migrator
supporting shadow traffic auditing, canary traffic cutover, and instant rollback.
"""
from dataclasses import dataclass, field
import logging
import random
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

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
    limit_price: Optional[float] = None


@dataclass
class SchemaAuditDiff:
    endpoint: str
    v1_keys: List[str]
    v2_keys: List[str]
    missing_in_v2: List[str]
    is_equivalent: bool


class BrokerAPIVersionMigrator:
    """
    Executes a zero-downtime migration between legacy V1 and target V2 broker API versions.
    """

    def __init__(self, canary_v2_percentage: float = 0.0):
        self.phase = MigrationPhase.V1_ONLY
        self.canary_v2_percentage = canary_v2_percentage  # 0.0 to 1.0
        self.v1_requests = 0
        self.v2_requests = 0
        self.audit_log: List[SchemaAuditDiff] = []

    def set_phase(self, phase: MigrationPhase, canary_percentage: float = 0.0) -> None:
        """Sets the active migration phase and canary traffic split percentage."""
        self.phase = phase
        self.canary_v2_percentage = max(0.0, min(1.0, canary_percentage))
        logger.info(f"Migration Phase set to {phase.value} (Canary V2: {self.canary_v2_percentage:.0%})")

    def translate_payload_v1_to_v2(self, payload: OrderPayload) -> Dict[str, Any]:
        """Translates legacy V1 order structure to target V2 API JSON payload."""
        return {
            "instrument_id": payload.symbol.upper(),
            "side": payload.action.upper(),
            "size": payload.quantity,
            "order_configuration": {
                "limit_limit_gtc": {
                    "limit_price": str(payload.limit_price) if payload.limit_price else "0"
                }
            } if payload.limit_price else {"market_market_ioc": {}}
        }

    def audit_shadow_response(self, endpoint: str, v1_response: Dict[str, Any], v2_response: Dict[str, Any]) -> SchemaAuditDiff:
        """Compares V1 and V2 read response schemas to detect breaking field drifts."""
        v1_keys = list(v1_response.keys())
        v2_keys = list(v2_response.keys())
        missing = [k for k in v1_keys if k not in v2_keys]

        diff = SchemaAuditDiff(
            endpoint=endpoint,
            v1_keys=v1_keys,
            v2_keys=v2_keys,
            missing_in_v2=missing,
            is_equivalent=len(missing) == 0,
        )
        self.audit_log.append(diff)
        if not diff.is_equivalent:
            logger.warning(f"Shadow Audit Drift on {endpoint}: Missing keys in V2 -> {missing}")
        return diff

    def route_order_version(self, payload: OrderPayload) -> str:
        """
        Determines whether to route order via V1 or V2 based on active phase and canary percentage.
        Returns target version string ("V1" or "V2").
        """
        if self.phase == MigrationPhase.V1_ONLY or self.phase == MigrationPhase.ROLLBACK_V1:
            self.v1_requests += 1
            return "V1"

        if self.phase == MigrationPhase.V2_ONLY:
            self.v2_requests += 1
            return "V2"

        if self.phase == MigrationPhase.CANARY_CUTOVER:
            # Deterministic/random split based on canary percentage
            if random.random() < self.canary_v2_percentage:
                self.v2_requests += 1
                return "V2"
            else:
                self.v1_requests += 1
                return "V1"

        self.v1_requests += 1
        return "V1"

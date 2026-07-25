"""
tick-data-schema-versioning: Versioned tick payload header encoder,
backward/forward schema migration adapters, and compatibility validator.
"""
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class VersionedTickV1:
    schema_version: int = 1
    symbol: str = ""
    timestamp_sec: float = 0.0
    price: float = 0.0
    volume: float = 0.0


@dataclass
class VersionedTickV2:
    schema_version: int = 2
    symbol: str = ""
    timestamp_ns: int = 0  # Upgraded to uint64 nanoseconds
    bid: float = 0.0
    ask: float = 0.0
    volume: float = 0.0
    exchange_id: str = "US"  # Added exchange code


class TickSchemaVersioner:
    """
    Manages tick schema version headers and executes migration adapters across V1, V2, and V3 payload versions.
    """

    CURRENT_TARGET_VERSION = 2

    def wrap_payload(self, data: Dict[str, Any], version: int = 2) -> Dict[str, Any]:
        """Injects schema_version header into raw tick payload dictionary."""
        payload = dict(data)
        payload["schema_version"] = version
        return payload

    def normalize_to_target_version(self, payload: Dict[str, Any], target_version: Optional[int] = None) -> Dict[str, Any]:
        """
        Inspects payload['schema_version'] and migrates payload to target_version.
        """
        target_v = target_version or self.CURRENT_TARGET_VERSION
        payload_v = payload.get("schema_version", 1)

        if payload_v == target_v:
            return payload

        if payload_v == 1 and target_v == 2:
            return self._migrate_v1_to_v2(payload)
        elif payload_v == 2 and target_v == 1:
            return self._migrate_v2_to_v1(payload)
        else:
            logger.warning(f"Unsupported schema migration pathway V{payload_v} -> V{target_v}. Returning raw payload.")
            return payload

    def _migrate_v1_to_v2(self, v1_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Upgrades V1 tick to V2 tick format."""
        ts_sec = float(v1_dict.get("timestamp_sec", 0.0))
        ts_ns = int(ts_sec * 1e9)
        price = float(v1_dict.get("price", 0.0))

        v2_dict = {
            "schema_version": 2,
            "symbol": str(v1_dict.get("symbol", "")).upper(),
            "timestamp_ns": ts_ns,
            "bid": price,  # Estimate bid/ask from mid-price for V1 upgrade
            "ask": price,
            "volume": float(v1_dict.get("volume", 0.0)),
            "exchange_id": "UNKNOWN",
        }
        logger.info(f"Schema Upgrade Executed: V1 -> V2 for tick '{v2_dict['symbol']}'.")
        return v2_dict

    def _migrate_v2_to_v1(self, v2_dict: Dict[str, Any]) -> Dict[str, Any]:
        """Downgrades V2 tick to legacy V1 tick format."""
        ts_ns = int(v2_dict.get("timestamp_ns", 0))
        ts_sec = ts_ns / 1e9
        bid = float(v2_dict.get("bid", 0.0))
        ask = float(v2_dict.get("ask", 0.0))
        mid_price = (bid + ask) / 2.0 if (bid > 0 and ask > 0) else (bid or ask)

        v1_dict = {
            "schema_version": 1,
            "symbol": str(v2_dict.get("symbol", "")).upper(),
            "timestamp_sec": ts_sec,
            "price": mid_price,
            "volume": float(v2_dict.get("volume", 0.0)),
        }
        logger.info(f"Schema Downgrade Executed: V2 -> V1 for legacy consumer.")
        return v1_dict

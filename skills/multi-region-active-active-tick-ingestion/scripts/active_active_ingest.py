"""
multi-region-active-active-tick-ingestion: Cross-region tick deduplicator, first-arrival
latency arbitrator, and regional health telemetry module.
"""
from dataclasses import dataclass, field
import hashlib
import logging
import time
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RegionalTick:
    region_id: str
    symbol: str
    sequence_id: int
    timestamp: float
    price: float
    volume: float
    local_receipt_time: float


@dataclass
class ActiveActiveIngestResult:
    symbol: str
    tick: RegionalTick
    is_duplicate: bool
    first_arrived_region: str
    latency_delta_ms: float
    message: str


class MultiRegionActiveActiveIngestor:
    """
    Ingests active-active market data streams from multiple cloud regions,
    deduplicates identical ticks by signature, and forwards the fastest arrival.
    """

    def __init__(self, ttl_seconds: float = 10.0):
        self.ttl_seconds = ttl_seconds
        # signature_hash -> (first_receipt_time, first_region_id)
        self.seen_signatures: Dict[str, Tuple[float, str]] = {}
        self.region_win_counts: Dict[str, int] = {}

    def compute_tick_signature(self, symbol: str, sequence_id: int, price: float, volume: float) -> str:
        """Computes deterministic hash signature for a tick event."""
        raw_str = f"{symbol.upper()}:{sequence_id}:{price:.4f}:{volume:.4f}"
        return hashlib.md5(raw_str.encode("utf-8")).hexdigest()

    def ingest_regional_tick(
        self,
        region_id: str,
        symbol: str,
        sequence_id: int,
        timestamp: float,
        price: float,
        volume: float,
        receipt_time: Optional[float] = None,
    ) -> ActiveActiveIngestResult:
        """
        Processes an incoming tick from region_id. Deduplicates duplicate ticks
        arriving from secondary regions.
        """
        r_id = region_id.lower()
        now = receipt_time or time.time()
        sym = symbol.upper()

        sig = self.compute_tick_signature(sym, sequence_id, price, volume)

        tick = RegionalTick(
            region_id=r_id,
            symbol=sym,
            sequence_id=sequence_id,
            timestamp=timestamp,
            price=price,
            volume=volume,
            local_receipt_time=now,
        )

        # Cleanup expired signatures periodically
        self._evict_expired_signatures(now)

        if sig in self.seen_signatures:
            first_time, first_region = self.seen_signatures[sig]
            delta_ms = (now - first_time) * 1000.0

            logger.debug(f"DEDUPLICATED TICK on {sym} seq {sequence_id}: Arrived from '{r_id}' {delta_ms:.2f}ms after '{first_region}'.")
            return ActiveActiveIngestResult(
                symbol=sym,
                tick=tick,
                is_duplicate=True,
                first_arrived_region=first_region,
                latency_delta_ms=round(delta_ms, 3),
                message=f"Duplicate tick dropped. '{first_region}' arrived {delta_ms:.2f}ms faster than '{r_id}'.",
            )

        # First arrival of tick signature
        self.seen_signatures[sig] = (now, r_id)
        self.region_win_counts[r_id] = self.region_win_counts.get(r_id, 0) + 1

        logger.info(f"FIRST ARRIVAL TICK on {sym} seq {sequence_id} from region '{r_id}'.")
        return ActiveActiveIngestResult(
            symbol=sym,
            tick=tick,
            is_duplicate=False,
            first_arrived_region=r_id,
            latency_delta_ms=0.0,
            message=f"First arrival accepted from '{r_id}'.",
        )

    def _evict_expired_signatures(self, current_time: float) -> None:
        """Evicts signature hashes older than ttl_seconds."""
        expired_keys = [
            k for k, (rec_t, _) in self.seen_signatures.items()
            if (current_time - rec_t) > self.ttl_seconds
        ]
        for k in expired_keys:
            del self.seen_signatures[k]

    def get_regional_win_statistics(self) -> Dict[str, Any]:
        """Returns regional win counts and telemetry."""
        total = sum(self.region_win_counts.values())
        stats = {}
        for reg, count in self.region_win_counts.items():
            pct = (count / total * 100.0) if total > 0 else 0.0
            stats[reg] = {"wins": count, "win_percentage": round(pct, 2)}
        return stats

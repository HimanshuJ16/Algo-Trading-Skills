import datetime
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

@dataclass
class VendorTickRecord:
    tick_id: str
    vendor_id: str                     # e.g. 'BLOOMBERG', 'REFINITIV', 'DATABENTO', 'ICE'
    symbol: str
    price: float
    volume: float
    raw_timestamp: Union[str, float, int]
    precision_format: str               # 'SECONDS', 'MILLISECONDS', 'MICROSECONDS', 'NANOSECONDS', 'ISO8601'

@dataclass
class NormalizedTickRecord:
    tick_id: str
    vendor_id: str
    symbol: str
    price: float
    volume: float
    raw_timestamp: Union[str, float, int]
    normalized_ns_utc: int              # 64-bit integer nanosecond UTC epoch
    iso_utc_str: str
    precision_tier: str                 # 'NANOSECONDS', 'MICROSECONDS', 'MILLISECONDS', 'SECONDS'
    is_out_of_order: bool

@dataclass
class TimestampReconciliationReport:
    total_ticks_processed: int
    normalized_ticks: List[NormalizedTickRecord]
    out_of_order_count: int
    precision_counts: Dict[str, int]
    vendor_drift_warnings: List[str]

class CrossVendorTimestampReconciler:
    """
    Market data reconciliation engine for normalizing multi-vendor timestamps (s, ms, us, ns, ISO-8601)
    to 64-bit nanosecond UTC epoch integers, detecting clock drift, and aligning tick sequences.
    """
    def __init__(self, max_allowed_vendor_drift_ms: float = 5.0):
        self.max_allowed_vendor_drift_ms = max_allowed_vendor_drift_ms

    def normalize_timestamp_to_ns(
        self,
        raw_ts: Union[str, float, int],
        precision_format: str
    ) -> Tuple[int, str, str]:
        """
        Parses raw timestamp and returns (nanosecond_epoch_int64, iso_utc_string, precision_tier).
        """
        fmt = precision_format.upper()
        
        if fmt == "NANOSECONDS":
            ns = int(raw_ts)
            precision_tier = "NANOSECONDS"
        elif fmt == "MICROSECONDS":
            ns = int(float(raw_ts) * 1_000)
            precision_tier = "MICROSECONDS"
        elif fmt == "MILLISECONDS":
            ns = int(float(raw_ts) * 1_000_000)
            precision_tier = "MILLISECONDS"
        elif fmt == "SECONDS":
            ns = int(float(raw_ts) * 1_000_000_000)
            precision_tier = "SECONDS"
        elif fmt == "ISO8601":
            # Parse ISO-8601 UTC string (e.g., '2023-11-14T21:20:00.123456Z')
            s_str = str(raw_ts).replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(s_str)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=datetime.timezone.utc)
            ns = int(dt.timestamp() * 1_000_000_000)
            precision_tier = "MICROSECONDS" if len(s_str.split(".")) > 1 else "SECONDS"
        else:
            raise ValueError(f"Unsupported precision format: {precision_format}")

        dt_utc = datetime.datetime.fromtimestamp(ns / 1_000_000_000.0, tz=datetime.timezone.utc)
        iso_str = dt_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        return ns, iso_str, precision_tier

    def reconcile_vendor_ticks(self, raw_ticks: List[VendorTickRecord]) -> TimestampReconciliationReport:
        """
        Normalizes multi-vendor tick timestamps, sorts them into a temporal sequence,
        and detects out-of-order tick arrivals.
        """
        normalized_list: List[NormalizedTickRecord] = []
        precision_counts: Dict[str, int] = {"NANOSECONDS": 0, "MICROSECONDS": 0, "MILLISECONDS": 0, "SECONDS": 0}

        for tick in raw_ticks:
            ns_ts, iso_str, tier = self.normalize_timestamp_to_ns(tick.raw_timestamp, tick.precision_format)
            precision_counts[tier] = precision_counts.get(tier, 0) + 1

            normalized_list.append(NormalizedTickRecord(
                tick_id=tick.tick_id,
                vendor_id=tick.vendor_id,
                symbol=tick.symbol,
                price=tick.price,
                volume=tick.volume,
                raw_timestamp=tick.raw_timestamp,
                normalized_ns_utc=ns_ts,
                iso_utc_str=iso_str,
                precision_tier=tier,
                is_out_of_order=False
            ))

        # Sort Ticks globally by 64-bit nanosecond UTC epoch
        sorted_ticks = sorted(normalized_list, key=lambda t: t.normalized_ns_utc)

        # Detect Out-of-Order (OOO) arrival flags relative to original input order
        ooo_count = 0
        original_id_map = {t.tick_id: idx for idx, t in enumerate(raw_ticks)}
        
        drift_warnings = []
        for i in range(1, len(sorted_ticks)):
            prev_t = sorted_ticks[i - 1]
            curr_t = sorted_ticks[i]
            
            # Check if sorted sequence violated arrival order
            if original_id_map[curr_t.tick_id] < original_id_map[prev_t.tick_id]:
                curr_t.is_out_of_order = True
                ooo_count += 1

            # Check cross-vendor drift for same symbol tick pairs
            if curr_t.symbol == prev_t.symbol and curr_t.vendor_id != prev_t.vendor_id:
                drift_ms = abs(curr_t.normalized_ns_utc - prev_t.normalized_ns_utc) / 1_000_000.0
                if drift_ms > self.max_allowed_vendor_drift_ms:
                    msg = (
                        f"CROSS-VENDOR DRIFT [{curr_t.symbol}]: Vendor {curr_t.vendor_id} vs {prev_t.vendor_id} "
                        f"diverges by {drift_ms:.2f}ms >= {self.max_allowed_vendor_drift_ms:.2f}ms!"
                    )
                    drift_warnings.append(msg)
                    logger.warning(msg)

        return TimestampReconciliationReport(
            total_ticks_processed=len(raw_ticks),
            normalized_ticks=sorted_ticks,
            out_of_order_count=ooo_count,
            precision_counts=precision_counts,
            vendor_drift_warnings=drift_warnings
        )

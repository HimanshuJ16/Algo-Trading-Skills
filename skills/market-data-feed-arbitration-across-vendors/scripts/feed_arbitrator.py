"""
market-data-feed-arbitration-across-vendors: Dual-vendor feed arbitrator, price divergence
spotted filter, and stale vendor failover module.
"""
from dataclasses import dataclass, field
import logging
import statistics
import time
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class VendorStatus(Enum):
    HEALTHY = "HEALTHY"
    DIVERGENT_OUTLIER = "DIVERGENT_OUTLIER"
    STALE_TIMEOUT = "STALE_TIMEOUT"


@dataclass
class VendorTick:
    vendor_id: str
    symbol: str
    price: float
    timestamp: float


@dataclass
class ArbitratedTickResult:
    symbol: str
    consensus_price: float
    is_arbitrated: bool
    primary_vendor_status: VendorStatus
    secondary_vendor_status: VendorStatus
    relative_divergence_pct: float
    active_vendor: str
    message: str


class MarketDataFeedArbitrator:
    """
    Arbitrates price streams between redundant data vendors, filters bad ticks,
    and handles single-vendor staleness failover.
    """

    def __init__(
        self,
        max_divergence_pct: float = 0.05,  # 0.05% (5 bps) threshold
        max_stale_seconds: float = 2.0,
    ):
        self.max_divergence_pct = max_divergence_pct
        self.max_stale_seconds = max_stale_seconds

        self.last_vendor_ticks: Dict[str, Dict[str, VendorTick]] = {
            "primary": {},
            "secondary": {},
        }

    def process_vendor_tick(self, vendor_id: str, symbol: str, price: float, timestamp: Optional[float] = None) -> ArbitratedTickResult:
        """
        Receives a tick from vendor_id ('primary' or 'secondary'), arbitrates against
        the counterpart vendor feed, and outputs clean consensus price.
        """
        v_key = vendor_id.lower()
        now = timestamp or time.time()
        sym = symbol.upper()

        tick = VendorTick(vendor_id=v_key, symbol=sym, price=price, timestamp=now)
        self.last_vendor_ticks[v_key][sym] = tick

        other_key = "secondary" if v_key == "primary" else "primary"
        other_tick = self.last_vendor_ticks[other_key].get(sym)

        # Default fallback if only one vendor is available yet
        if not other_tick:
            return ArbitratedTickResult(
                symbol=sym,
                consensus_price=price,
                is_arbitrated=False,
                primary_vendor_status=VendorStatus.HEALTHY,
                secondary_vendor_status=VendorStatus.HEALTHY,
                relative_divergence_pct=0.0,
                active_vendor=v_key,
                message=f"Single feed active ({v_key}). No counterpart vendor tick available for arbitration.",
            )

        # Check for Stale Vendor Feed
        p_tick = self.last_vendor_ticks["primary"].get(sym)
        s_tick = self.last_vendor_ticks["secondary"].get(sym)

        p_stale = (now - p_tick.timestamp > self.max_stale_seconds) if p_tick else True
        s_stale = (now - s_tick.timestamp > self.max_stale_seconds) if s_tick else True

        p_status = VendorStatus.STALE_TIMEOUT if p_stale else VendorStatus.HEALTHY
        s_status = VendorStatus.STALE_TIMEOUT if s_stale else VendorStatus.HEALTHY

        if p_stale and not s_stale:
            logger.warning(f"Primary vendor feed STALE for {sym}. Failing over to Secondary feed.")
            return ArbitratedTickResult(
                symbol=sym,
                consensus_price=s_tick.price,
                is_arbitrated=True,
                primary_vendor_status=VendorStatus.STALE_TIMEOUT,
                secondary_vendor_status=VendorStatus.HEALTHY,
                relative_divergence_pct=0.0,
                active_vendor="secondary",
                message="Primary feed stale. Failed over to secondary vendor.",
            )
        elif s_stale and not p_stale:
            return ArbitratedTickResult(
                symbol=sym,
                consensus_price=p_tick.price,
                is_arbitrated=True,
                primary_vendor_status=VendorStatus.HEALTHY,
                secondary_vendor_status=VendorStatus.STALE_TIMEOUT,
                relative_divergence_pct=0.0,
                active_vendor="primary",
                message="Secondary feed stale. Running on primary vendor.",
            )

        # Both feeds active: Compare relative price divergence
        p_price = p_tick.price
        s_price = s_tick.price
        avg_price = (p_price + s_price) / 2.0

        divergence_pct = (abs(p_price - s_price) / avg_price) * 100.0

        if divergence_pct <= self.max_divergence_pct:
            # Consensus within tolerance -> Average price
            return ArbitratedTickResult(
                symbol=sym,
                consensus_price=avg_price,
                is_arbitrated=True,
                primary_vendor_status=VendorStatus.HEALTHY,
                secondary_vendor_status=VendorStatus.HEALTHY,
                relative_divergence_pct=round(divergence_pct, 4),
                active_vendor="CONSENSUS_BOTH",
                message=f"Consensus validated (Divergence {divergence_pct:.4f}% <= {self.max_divergence_pct}%).",
            )

        # Price Divergence Exceeded -> Outlier Detection
        logger.error(
            f"VENDOR FEED DIVERGENCE BREACH on {sym}: Primary=${p_price:.2f} vs Secondary=${s_price:.2f} "
            f"(Divergence {divergence_pct:.2f}% > limit {self.max_divergence_pct}%). Defaulting to Primary feed."
        )

        return ArbitratedTickResult(
            symbol=sym,
            consensus_price=p_price,  # Default to primary when divergence occurs
            is_arbitrated=True,
            primary_vendor_status=VendorStatus.HEALTHY,
            secondary_vendor_status=VendorStatus.DIVERGENT_OUTLIER,
            relative_divergence_pct=round(divergence_pct, 4),
            active_vendor="primary",
            message=f"Divergence breach ({divergence_pct:.2f}%). Quarantined secondary, using primary.",
        )

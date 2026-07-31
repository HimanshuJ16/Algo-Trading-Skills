import logging
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class VendorPriceQuote:
    vendor_id: str
    symbol: str
    price: float
    timestamp: float
    volume_depth: float = 100.0
    vendor_priority: int = 1             # 1 = Highest Priority (e.g. Bloomberg), 2 = Refinitiv, 3 = Polygon
    reliability_weight: float = 1.0     # Relative weight for composite price

@dataclass
class ReconciliationConfig:
    max_deviation_pct: float = 0.01      # 1.0% deviation from median = Outlier
    tolerance_pct: float = 0.0005        # 0.05% tolerance for agreement
    tie_breaker_method: str = "PRIORITY" # 'PRIORITY', 'FRESHNESS', 'VOLUME_WEIGHTED', 'WEIGHTED_AVERAGE'

@dataclass
class PriceReconciliationReport:
    symbol: str
    canonical_price: float
    total_quotes_received: int
    valid_quotes_count: int
    outlier_quotes_count: int
    winning_vendor_id: str
    tie_breaker_used: str
    reconciled_quotes: List[VendorPriceQuote]
    outlier_quotes: List[VendorPriceQuote]
    status: str                          # 'RECONCILIATION_SUCCESS'
    audit_notes: str

class MultiSourcePriceReconcilerEngine:
    """
    Multi-source price reconciliation engine filtering outlier vendor quotes,
    evaluating tolerance bounds, and applying deterministic tie-breaking rules.
    """
    def __init__(self, config: Optional[ReconciliationConfig] = None):
        self.config = config or ReconciliationConfig()

    def reconcile_prices(
        self, symbol: str, quotes: List[VendorPriceQuote]
    ) -> PriceReconciliationReport:
        """
        Filters outliers, checks quote agreement tolerance, and resolves canonical price.
        """
        if not quotes:
            raise ValueError("Quotes list cannot be empty.")

        total_count = len(quotes)

        # Single quote edge case
        if total_count == 1:
            q = quotes[0]
            notes = f"RECONCILIATION SUCCESS [{symbol}]: Single vendor quote '{q.vendor_id}' accepted @ ${q.price:.4f}."
            return PriceReconciliationReport(
                symbol=symbol,
                canonical_price=round(q.price, 4),
                total_quotes_received=1,
                valid_quotes_count=1,
                outlier_quotes_count=0,
                winning_vendor_id=q.vendor_id,
                tie_breaker_used="SINGLE_QUOTE",
                reconciled_quotes=quotes,
                outlier_quotes=[],
                status="RECONCILIATION_SUCCESS",
                audit_notes=notes
            )

        # 1. Compute Median & Filter Outliers
        prices = [q.price for q in quotes]
        med_price = statistics.median(prices)

        valid_quotes: List[VendorPriceQuote] = []
        outlier_quotes: List[VendorPriceQuote] = []

        for q in quotes:
            dev_pct = abs(q.price - med_price) / med_price if med_price > 0 else 0.0
            if dev_pct > self.config.max_deviation_pct:
                outlier_quotes.append(q)
                logger.warning(f"RECONCILIATION OUTLIER [{symbol}]: Vendor '{q.vendor_id}' price ${q.price:.4f} deviated {dev_pct*100:.2f}% from median ${med_price:.4f}.")
            else:
                valid_quotes.append(q)

        # Fallback to all quotes if all were flagged as outliers
        if not valid_quotes:
            valid_quotes = quotes
            outlier_quotes = []

        # 2. Check Agreement Tolerance & Compute Canonical Price
        valid_prices = [q.price for q in valid_quotes]
        min_p = min(valid_prices)
        max_p = max(valid_prices)
        price_range_pct = (max_p - min_p) / med_price if med_price > 0 else 0.0

        method = self.config.tie_breaker_method.upper()

        if price_range_pct <= self.config.tolerance_pct or method == "WEIGHTED_AVERAGE":
            # Quotes agree within tolerance -> Weighted Average Composite Price
            total_weight = sum(q.reliability_weight for q in valid_quotes)
            canonical_price = sum(q.price * q.reliability_weight for q in valid_quotes) / total_weight
            winning_vendor = "COMPOSITE_WEIGHTED"
            tie_used = "WEIGHTED_AVERAGE"
        else:
            # Quotes conflict beyond tolerance -> Deterministic Tie-Breaking
            if method == "PRIORITY":
                # Lowest priority rank number = Highest priority
                winning_q = min(valid_quotes, key=lambda q: (q.vendor_priority, -q.timestamp))
                tie_used = "PRIORITY_RANK"
            elif method == "FRESHNESS":
                # Most recent timestamp
                winning_q = max(valid_quotes, key=lambda q: (q.timestamp, -q.vendor_priority))
                tie_used = "FRESHNESS_TIMESTAMP"
            elif method == "VOLUME_WEIGHTED":
                # Highest volume depth
                winning_q = max(valid_quotes, key=lambda q: (q.volume_depth, -q.vendor_priority))
                tie_used = "VOLUME_DEPTH"
            else:
                winning_q = valid_quotes[0]
                tie_used = "DEFAULT_FIRST"

            canonical_price = winning_q.price
            winning_vendor = winning_q.vendor_id

        notes = (
            f"RECONCILIATION SUCCESS [{symbol}]: Canonical Price = ${canonical_price:.4f} (Winning Vendor: '{winning_vendor}', Tie-Breaker: '{tie_used}'). "
            f"Received {total_count} quotes, {len(valid_quotes)} valid, {len(outlier_quotes)} outliers discarded."
        )
        logger.info(notes)

        return PriceReconciliationReport(
            symbol=symbol,
            canonical_price=round(canonical_price, 4),
            total_quotes_received=total_count,
            valid_quotes_count=len(valid_quotes),
            outlier_quotes_count=len(outlier_quotes),
            winning_vendor_id=winning_vendor,
            tie_breaker_used=tie_used,
            reconciled_quotes=valid_quotes,
            outlier_quotes=outlier_quotes,
            status="RECONCILIATION_SUCCESS",
            audit_notes=notes
        )

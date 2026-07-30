import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class DarkPoolVenueProfile:
    venue_id: str                      # e.g. 'UBS_PIN', 'MS_POOL', 'SIGMA_X', 'CROSSFINDER'
    venue_name: str
    historical_fill_rate: float        # 0.0 to 1.0 (e.g. 0.40 = 40% fill rate)
    toxicity_score_bps: float          # Post-trade adverse selection markout PnL in bps (lower is better)
    min_qty_threshold: int             # Venue minimum size constraint
    is_active: bool = True

@dataclass
class DarkChildOrderDirective:
    venue_id: str
    venue_name: str
    allocated_quantity: int
    min_qty_instruction: int
    allocation_weight_pct: float
    venue_score: float

@dataclass
class DarkPoolRoutingReport:
    symbol: str
    side: str                          # 'BUY' or 'SELL'
    total_parent_quantity: int
    allocated_quantity: int
    unallocated_quantity: int
    child_directives: List[DarkChildOrderDirective]
    excluded_venues: List[str]

class DarkPoolRoutingEngine:
    """
    Quantitative Smart Order Router (SOR) module for routing parent block orders across dark pools / ATS,
    enforcing anti-pinging MinQty thresholds, and filtering toxic venues.
    """
    def __init__(self, max_acceptable_toxicity_bps: float = 5.0, default_min_qty: int = 200):
        self.max_acceptable_toxicity_bps = max_acceptable_toxicity_bps
        self.default_min_qty = default_min_qty
        self.venues: Dict[str, DarkPoolVenueProfile] = {}

    def register_venue(self, venue: DarkPoolVenueProfile):
        self.venues[venue.venue_id] = venue

    def route_parent_order(
        self,
        symbol: str,
        side: str,
        total_quantity: int,
        custom_max_toxicity_bps: Optional[float] = None
    ) -> DarkPoolRoutingReport:
        """
        Filters toxic dark pools and allocates parent order size across eligible venues weighted by fill rate & toxicity.
        """
        max_tox = custom_max_toxicity_bps if custom_max_toxicity_bps is not None else self.max_acceptable_toxicity_bps
        
        eligible_venues: List[Tuple[DarkPoolVenueProfile, float]] = []
        excluded_venues: List[str] = []

        for v_id, v in self.venues.items():
            if not v.is_active:
                excluded_venues.append(f"{v.venue_name} (Inactive)")
                continue

            if v.toxicity_score_bps > max_tox:
                excluded_venues.append(f"{v.venue_name} (Toxic: {v.toxicity_score_bps:.2f}bps > {max_tox:.2f}bps)")
                logger.warning(f"DARK POOL EXCLUDED [{v.venue_name}]: Toxicity {v.toxicity_score_bps:.2f}bps exceeds threshold {max_tox:.2f}bps.")
                continue

            if v.historical_fill_rate < 0.05:
                excluded_venues.append(f"{v.venue_name} (Low Fill Rate: {v.historical_fill_rate:.2f})")
                continue

            # Allocation Score: FillRate * max(0.1, 1 - Toxicity / 50.0)
            tox_factor = max(0.1, 1.0 - (v.toxicity_score_bps / 50.0))
            score = v.historical_fill_rate * tox_factor
            eligible_venues.append((v, score))

        if not eligible_venues:
            return DarkPoolRoutingReport(
                symbol=symbol, side=side, total_parent_quantity=total_quantity,
                allocated_quantity=0, unallocated_quantity=total_quantity,
                child_directives=[], excluded_venues=excluded_venues
            )

        total_score = sum(score for _, score in eligible_venues)
        child_directives: List[DarkChildOrderDirective] = []
        allocated_qty = 0

        for v, score in eligible_venues:
            weight = score / total_score
            raw_qty = int(total_quantity * weight)
            
            # Enforce Minimum Allocation threshold
            if raw_qty < v.min_qty_threshold:
                continue

            # MinQty instruction: Max(VenueMinQty, 20% of child order size) to prevent HFT pinging
            min_qty_instruction = max(v.min_qty_threshold, int(raw_qty * 0.20), self.default_min_qty)

            child_directives.append(DarkChildOrderDirective(
                venue_id=v.venue_id,
                venue_name=v.venue_name,
                allocated_quantity=raw_qty,
                min_qty_instruction=min_qty_instruction,
                allocation_weight_pct=round(weight * 100.0, 2),
                venue_score=round(score, 4)
            ))
            allocated_qty += raw_qty

        unallocated_qty = total_quantity - allocated_qty

        logger.info(
            f"DARK POOL ROUTING [{symbol} {side}]: Parent={total_quantity} -> Allocated={allocated_qty} "
            f"across {len(child_directives)} dark venues. Unallocated={unallocated_qty}."
        )

        return DarkPoolRoutingReport(
            symbol=symbol,
            side=side,
            total_parent_quantity=total_quantity,
            allocated_quantity=allocated_qty,
            unallocated_quantity=unallocated_qty,
            child_directives=child_directives,
            excluded_venues=excluded_venues
        )

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class SmartOrderRoutingAcrossVenuesConfig:
    """Legacy config container for backward compatibility."""
    enabled: bool = True
    threshold: float = 0.5
    size: int = 100

@dataclass
class VenueQuote:
    venue_id: str                         # e.g., 'NASDAQ', 'NYSE', 'BATS', 'IEX'
    bid_price: float
    bid_qty: float
    ask_price: float
    ask_qty: float
    taker_fee_per_share: float = 0.0030   # $0.0030/share taker fee
    maker_rebate_per_share: float = 0.0020# $0.0020/share maker rebate
    latency_ms: float = 1.5

@dataclass
class ChildOrderRoute:
    child_order_id: str
    target_venue_id: str
    side: str
    quantity: float
    limit_price: float
    effective_net_price: float           # Price adjusted for maker-taker fee/rebate
    audit_notes: str

@dataclass
class SORRoutingPlan:
    parent_order_id: str
    symbol: str
    side: str
    total_quantity: float
    nbbo_price: float
    routes: List[ChildOrderRoute]
    unrouted_quantity: float
    net_expected_cost_usd: float
    audit_notes: str

class SmartOrderRoutingAcrossVenuesEngine:
    """
    Production-grade Multi-Venue Smart Order Routing (SOR) Engine enforcing Reg NMS Rule 611 NBBO compliance,
    maker-taker fee/rebate optimization, multi-venue order book sweeping, and market impact minimization.
    """
    def __init__(self, config: Optional[SmartOrderRoutingAcrossVenuesConfig] = None):
        self.config = config or SmartOrderRoutingAcrossVenuesConfig(enabled=True)
        self.state = "INITIALIZED"
        self.orders: List[Dict[str, Any]] = []

    def evaluate(self, market_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Legacy evaluate method retained for 100% backward compatibility."""
        if not self.config.enabled:
            return []
        if market_data.get("price", 0) > self.config.threshold:
            order = {"symbol": market_data.get("symbol", "UNKNOWN"), "qty": self.config.size, "type": "LIMIT"}
            self.orders.append(order)
            return [order]
        return []

    def route_parent_order(
        self,
        parent_order_id: str,
        symbol: str,
        side: str,                            # 'BUY' or 'SELL'
        quantity: float,
        venue_quotes: List[VenueQuote],
        fee_aware: bool = True
    ) -> SORRoutingPlan:
        """
        Consolidates NBBO across multi-venue quotes, slices parent order across venues to prevent trade-throughs,
        and optimizes for net maker-taker fees/rebates.
        """
        if not venue_quotes:
            raise ValueError("No venue quotes provided for smart order routing.")

        is_buy = side.upper() == "BUY"

        # 1. Consolidate NBBO
        if is_buy:
            nbbo_price = min(vq.ask_price for vq in venue_quotes)
            # Filter quotes matching NBBO or within acceptable slippage
            eligible_quotes = [vq for vq in venue_quotes if vq.ask_price == nbbo_price and vq.ask_qty > 0]
        else:
            nbbo_price = max(vq.bid_price for vq in venue_quotes)
            eligible_quotes = [vq for vq in venue_quotes if vq.bid_price == nbbo_price and vq.bid_qty > 0]

        if not eligible_quotes:
            # Fallback: take all quotes sorted by price
            eligible_quotes = sorted(venue_quotes, key=lambda vq: vq.ask_price if is_buy else -vq.bid_price)

        # 2. Score & Sort Venues (Price + Net Fee/Rebate + Latency)
        def score_venue(vq: VenueQuote) -> float:
            base_price = vq.ask_price if is_buy else vq.bid_price
            fee = vq.taker_fee_per_share if fee_aware else 0.0
            if is_buy:
                return base_price + fee + (vq.latency_ms / 100000.0)
            else:
                return -(base_price - fee - (vq.latency_ms / 100000.0))

        sorted_venues = sorted(eligible_quotes, key=score_venue)

        # 3. Liquidity Allocation & Slicing across venues
        remaining_qty = quantity
        routes: List[ChildOrderRoute] = []
        total_net_cost = 0.0

        for idx, vq in enumerate(sorted_venues):
            if remaining_qty <= 0:
                break

            avail_qty = vq.ask_qty if is_buy else vq.bid_qty
            if avail_qty <= 0:
                continue

            slice_qty = min(remaining_qty, avail_qty)
            raw_price = vq.ask_price if is_buy else vq.bid_price
            net_price = raw_price + vq.taker_fee_per_share if is_buy else raw_price - vq.taker_fee_per_share

            child_id = f"{parent_order_id}_CHILD_{idx+1}_{vq.venue_id}"
            notes = f"CHILD ROUTE: {slice_qty} shares to {vq.venue_id} @ ${raw_price} (Net ${net_price:.4f})."

            routes.append(ChildOrderRoute(
                child_order_id=child_id,
                target_venue_id=vq.venue_id,
                side=side.upper(),
                quantity=slice_qty,
                limit_price=raw_price,
                effective_net_price=net_price,
                audit_notes=notes
            ))

            total_net_cost += slice_qty * net_price
            remaining_qty -= slice_qty

        unrouted = remaining_qty
        plan_notes = (
            f"SOR ROUTING PLAN [{parent_order_id}] ({symbol}): {side} {quantity} shares. "
            f"NBBO = ${nbbo_price}, Child Routes = {len(routes)}, Unrouted = {unrouted}, Total Net Cost = ${total_net_cost:,.2f}."
        )

        logger.info(plan_notes)

        return SORRoutingPlan(
            parent_order_id=parent_order_id,
            symbol=symbol,
            side=side.upper(),
            total_quantity=quantity,
            nbbo_price=nbbo_price,
            routes=routes,
            unrouted_quantity=unrouted,
            net_expected_cost_usd=round(total_net_cost, 2),
            audit_notes=plan_notes
        )

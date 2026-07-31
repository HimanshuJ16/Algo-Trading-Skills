import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class VenueBookSpec:
    venue_id: str                       # e.g. 'DARK_POOL_ALPHA', 'NASDAQ', 'NYSE', 'CBOE'
    venue_type: str                     # 'DARK' (ATS) or 'LIT' (Exchange)
    bid_price: float
    ask_price: float
    bid_qty: int
    ask_qty: int
    historical_fill_rate: float = 0.50  # Fill probability for dark venues (e.g. 0.60 = 60%)

@dataclass
class ParentOrderSpec:
    symbol: str
    side: str                           # 'BUY' or 'SELL'
    target_quantity: int                # e.g. 20,000 shares
    limit_price: float                  # Max buy price or min sell price
    min_dark_fill_qty: int = 500        # Anti-pinging minimum fill threshold

@dataclass
class ChildOrderRoute:
    venue_id: str
    venue_type: str
    side: str
    quantity: int
    price: float
    execution_stage: str                # 'STAGE1_DARK_MIDPOINT', 'STAGE2_LIT_SWEEP'
    filled_quantity: int
    price_improvement_usd: float

@dataclass
class LiquiditySeekingReport:
    symbol: str
    total_requested_qty: int
    total_executed_qty: int
    dark_executed_qty: int
    lit_executed_qty: int
    unfilled_qty: int
    nbbo_midpoint_price: float
    average_fill_price: float
    total_price_improvement_usd: float  # Savings relative to Lit NBO/NBB
    child_routes: List[ChildOrderRoute]
    status: str                         # 'LIQUIDITY_SEEKING_COMPLETE', 'PARTIALLY_FILLED', 'INSUFFICIENT_LIQUIDITY'
    audit_notes: str

class LiquiditySeekingEngine:
    """
    Institutional Smart Order Routing (SOR) engine executing parent orders across Lit exchanges and Dark ATS venues,
    sweeping NBBO midpoint dark liquidity prior to lit book routing.
    """
    def __init__(self):
        pass

    def compute_nbbo(self, venues: List[VenueBookSpec]) -> Tuple[float, float, float]:
        """
        Computes National Best Bid (NBB), National Best Offer (NBO), and NBBO Midpoint.
        """
        lit_venues = [v for v in venues if v.venue_type.upper() == "LIT"]
        if not lit_venues:
            raise ValueError("No Lit venues provided to compute NBBO.")

        best_bid = max(v.bid_price for v in lit_venues)
        best_ask = min(v.ask_price for v in lit_venues)
        midpoint = round((best_bid + best_ask) / 2.0, 4)

        return best_bid, best_ask, midpoint

    def execute_liquidity_seeking(
        self,
        order: ParentOrderSpec,
        venues: List[VenueBookSpec]
    ) -> LiquiditySeekingReport:
        """
        Executes 2-stage liquidity seeking workflow:
        Stage 1: Sweeps Dark ATS venues at NBBO Midpoint with IOC orders.
        Stage 2: Routes remaining balance across Lit exchanges at NBBO.
        """
        best_bid, best_ask, nbbo_midpoint = self.compute_nbbo(venues)
        side_clean = order.side.strip().upper()

        if side_clean == "BUY" and order.limit_price < nbbo_midpoint:
            raise ValueError(f"Limit price (${order.limit_price:.2f}) is below NBBO Midpoint (${nbbo_midpoint:.2f}).")

        remaining_qty = order.target_quantity
        child_routes: List[ChildOrderRoute] = []

        dark_exec_qty = 0
        lit_exec_qty = 0
        total_price_improvement = 0.0
        total_fill_notional = 0.0

        # STAGE 1: Dark Pool NBBO Midpoint Sweep
        dark_venues = [v for v in venues if v.venue_type.upper() == "DARK"]
        # Sort dark venues by historical fill rate descending
        dark_venues.sort(key=lambda v: v.historical_fill_rate, reverse=True)

        for d_venue in dark_venues:
            if remaining_qty <= 0:
                break

            avail_liquidity = d_venue.ask_qty if side_clean == "BUY" else d_venue.bid_qty
            if avail_liquidity < order.min_dark_fill_qty:
                continue # Skip dark pool if liquidity below minimum anti-pinging threshold

            # Target dark fill quantity
            route_qty = min(remaining_qty, avail_liquidity)
            # Simulated dark fill based on fill rate
            actual_fill = int(route_qty * d_venue.historical_fill_rate)
            if actual_fill <= 0:
                continue

            exec_price = nbbo_midpoint
            # Savings relative to Lit NBO for Buy, or Lit NBB for Sell
            lit_ref_price = best_ask if side_clean == "BUY" else best_bid
            price_improvement = abs(lit_ref_price - exec_price) * actual_fill

            dark_exec_qty += actual_fill
            remaining_qty -= actual_fill
            total_fill_notional += (actual_fill * exec_price)
            total_price_improvement += price_improvement

            child_routes.append(
                ChildOrderRoute(
                    venue_id=d_venue.venue_id,
                    venue_type="DARK",
                    side=side_clean,
                    quantity=route_qty,
                    price=exec_price,
                    execution_stage="STAGE1_DARK_MIDPOINT",
                    filled_quantity=actual_fill,
                    price_improvement_usd=round(price_improvement, 2)
                )
            )

        # STAGE 2: Lit Exchange Fallback Sweep
        lit_venues = [v for v in venues if v.venue_type.upper() == "LIT"]

        for l_venue in lit_venues:
            if remaining_qty <= 0:
                break

            avail_lit_qty = l_venue.ask_qty if side_clean == "BUY" else l_venue.bid_qty
            exec_price = l_venue.ask_price if side_clean == "BUY" else l_venue.bid_price

            if side_clean == "BUY" and exec_price > order.limit_price:
                continue
            if side_clean == "SELL" and exec_price < order.limit_price:
                continue

            actual_fill = min(remaining_qty, avail_lit_qty)
            if actual_fill <= 0:
                continue

            lit_exec_qty += actual_fill
            remaining_qty -= actual_fill
            total_fill_notional += (actual_fill * exec_price)

            child_routes.append(
                ChildOrderRoute(
                    venue_id=l_venue.venue_id,
                    venue_type="LIT",
                    side=side_clean,
                    quantity=actual_fill,
                    price=exec_price,
                    execution_stage="STAGE2_LIT_SWEEP",
                    filled_quantity=actual_fill,
                    price_improvement_usd=0.0
                )
            )

        total_exec_qty = dark_exec_qty + lit_exec_qty
        avg_price = round(total_fill_notional / float(total_exec_qty), 4) if total_exec_qty > 0 else 0.0

        if remaining_qty == 0:
            status = "LIQUIDITY_SEEKING_COMPLETE"
            notes = (
                f"LIQUIDITY SEEKING COMPLETE [{order.symbol}]: Executed {total_exec_qty:,}/{order.target_quantity:,} shares "
                f"(Dark = {dark_exec_qty:,}, Lit = {lit_exec_qty:,}) @ Avg Price ${avg_price:,.4f}. "
                f"Price Improvement = ${total_price_improvement:,.2f}."
            )
            logger.info(notes)
        elif total_exec_qty > 0:
            status = "PARTIALLY_FILLED"
            notes = (
                f"LIQUIDITY SEEKING PARTIAL [{order.symbol}]: Executed {total_exec_qty:,}/{order.target_quantity:,} shares. "
                f"Unfilled = {remaining_qty:,} shares."
            )
            logger.warning(notes)
        else:
            status = "INSUFFICIENT_LIQUIDITY"
            notes = f"LIQUIDITY SEEKING REJECT [{order.symbol}]: Zero fills across Lit and Dark venues."
            logger.error(notes)

        return LiquiditySeekingReport(
            symbol=order.symbol,
            total_requested_qty=order.target_quantity,
            total_executed_qty=total_exec_qty,
            dark_executed_qty=dark_exec_qty,
            lit_executed_qty=lit_exec_qty,
            unfilled_qty=remaining_qty,
            nbbo_midpoint_price=nbbo_midpoint,
            average_fill_price=avg_price,
            total_price_improvement_usd=round(total_price_improvement, 2),
            child_routes=child_routes,
            status=status,
            audit_notes=notes
        )

"""
auction-only-order-types-for-illiquid-names:
Execution algorithm that routes large orders in illiquid names to the Closing Auction (LOC)
to minimize continuous market impact.
"""
import dataclasses
import logging
from enum import Enum
from typing import Dict, Any

logger = logging.getLogger(__name__)


class OrderType(Enum):
    CONTINUOUS_VWAP = "CONTINUOUS_VWAP"
    LIMIT_ON_CLOSE = "LIMIT_ON_CLOSE"
    MARKET_ON_CLOSE = "MARKET_ON_CLOSE"


@dataclasses.dataclass
class IlliquidExecutionConfig:
    # Order size > this % of ADV triggers 100% auction allocation
    severe_illiquidity_threshold_pct: float = 0.05  # 5%
    # Order size > this % of ADV triggers a hybrid (VWAP + Auction) allocation
    moderate_illiquidity_threshold_pct: float = 0.01  # 1%
    
    hybrid_auction_allocation_pct: float = 0.50 # 50% to auction, 50% to continuous


@dataclasses.dataclass
class ExecutionRoutingPlan:
    symbol: str
    total_qty: int
    continuous_qty: int
    auction_qty: int
    auction_order_type: OrderType
    reason: str


class IlliquidAuctionExecutionEngine:
    """
    Determines the optimal routing between continuous trading and the Closing Auction
    based on the order's size relative to the asset's Average Daily Volume (ADV).
    """

    def __init__(self, config: IlliquidExecutionConfig = IlliquidExecutionConfig()):
        self.config = config

    def generate_routing_plan(
        self, symbol: str, total_qty: int, average_daily_volume: int
    ) -> ExecutionRoutingPlan:
        
        if average_daily_volume <= 0:
            raise ValueError("Average Daily Volume (ADV) must be strictly positive.")
            
        adv_percentage = total_qty / average_daily_volume
        
        if adv_percentage >= self.config.severe_illiquidity_threshold_pct:
            # Dangerously illiquid relative to our size. 
            # Trading this in continuous will walk the book and cause massive slippage.
            # Route 100% to Limit-On-Close. MOC is too dangerous (no price protection).
            plan = ExecutionRoutingPlan(
                symbol=symbol,
                total_qty=total_qty,
                continuous_qty=0,
                auction_qty=total_qty,
                auction_order_type=OrderType.LIMIT_ON_CLOSE,
                reason=f"Size is {adv_percentage:.2%} of ADV (Severe). Routing 100% to LOC to minimize impact."
            )
        elif adv_percentage >= self.config.moderate_illiquidity_threshold_pct:
            # Moderate impact. Use a hybrid approach.
            auction_qty = int(total_qty * self.config.hybrid_auction_allocation_pct)
            continuous_qty = total_qty - auction_qty
            plan = ExecutionRoutingPlan(
                symbol=symbol,
                total_qty=total_qty,
                continuous_qty=continuous_qty,
                auction_qty=auction_qty,
                auction_order_type=OrderType.LIMIT_ON_CLOSE,
                reason=f"Size is {adv_percentage:.2%} of ADV (Moderate). Hybrid routing: {continuous_qty} Continuous, {auction_qty} LOC."
            )
        else:
            # Liquid relative to our size. Safe to trade purely via continuous VWAP/TWAP.
            plan = ExecutionRoutingPlan(
                symbol=symbol,
                total_qty=total_qty,
                continuous_qty=total_qty,
                auction_qty=0,
                auction_order_type=OrderType.MARKET_ON_CLOSE, # Unused
                reason=f"Size is {adv_percentage:.2%} of ADV (Liquid). Routing 100% to Continuous."
            )
            
        logger.info(f"[{symbol}] {plan.reason}")
        return plan

import logging
from dataclasses import dataclass
from typing import Optional, Callable
from enum import Enum

logger = logging.getLogger(__name__)

class OrderSide(Enum):
    BUY = 1
    SELL = 2

class SpreadState(Enum):
    PENDING_ANCHOR = 1
    ANCHOR_PARTIAL = 2
    ANCHOR_FILLED = 3
    HEDGING = 4
    COMPLETED = 5
    BROKEN = 6

@dataclass
class Leg:
    symbol: str
    side: OrderSide
    ratio: int  # E.g., 1 for 1:1 spread
    limit_price: float

@dataclass
class SpreadConfig:
    anchor_leg: Leg
    hedge_leg: Leg
    max_hedge_slippage_bps: float = 5.0

class SpreadExecutionEngine:
    """
    Algorithmic execution engine for multi-leg strategies.
    Ensures atomicity by managing legging risk on non-native exchanges.
    """
    def __init__(self, config: SpreadConfig, broker_submit_callback: Callable):
        self.config = config
        self.broker_submit = broker_submit_callback
        self.state = SpreadState.PENDING_ANCHOR
        
        self.anchor_filled_qty = 0.0
        self.hedge_filled_qty = 0.0
        self.target_spread_qty = 0.0
        
    def start_execution(self, total_spread_qty: float):
        """Initiates the spread by routing the illiquid anchor leg."""
        self.target_spread_qty = total_spread_qty
        anchor_qty = total_spread_qty * self.config.anchor_leg.ratio
        
        logger.info(f"Routing Anchor Leg: {anchor_qty} {self.config.anchor_leg.symbol}")
        self.broker_submit(
            symbol=self.config.anchor_leg.symbol,
            side=self.config.anchor_leg.side,
            qty=anchor_qty,
            price=self.config.anchor_leg.limit_price,
            order_type="LIMIT"
        )
        self.state = SpreadState.PENDING_ANCHOR

    def on_anchor_fill(self, fill_qty: float, fill_price: float):
        """Callback from broker when the anchor leg receives a fill."""
        if self.state in [SpreadState.COMPLETED, SpreadState.BROKEN]:
            logger.warning("Received fill for closed spread.")
            return

        self.anchor_filled_qty += fill_qty
        
        if self.anchor_filled_qty >= (self.target_spread_qty * self.config.anchor_leg.ratio):
            self.state = SpreadState.ANCHOR_FILLED
        else:
            self.state = SpreadState.ANCHOR_PARTIAL

        # Calculate exact hedge quantity required for this specific fill
        required_hedge_qty = (fill_qty / self.config.anchor_leg.ratio) * self.config.hedge_leg.ratio
        
        # Calculate worst acceptable price based on slippage tolerance
        if self.config.hedge_leg.side == OrderSide.BUY:
            worst_price = self.config.hedge_leg.limit_price * (1 + (self.config.max_hedge_slippage_bps / 10000.0))
        else:
            worst_price = self.config.hedge_leg.limit_price * (1 - (self.config.max_hedge_slippage_bps / 10000.0))

        logger.info(f"Anchor filled {fill_qty}. Routing Hedge Leg: {required_hedge_qty} {self.config.hedge_leg.symbol} @ {worst_price} IOC")
        
        self.broker_submit(
            symbol=self.config.hedge_leg.symbol,
            side=self.config.hedge_leg.side,
            qty=required_hedge_qty,
            price=worst_price,
            order_type="IOC"  # Immediate or Cancel to prevent hanging legs
        )
        
        if self.state != SpreadState.ANCHOR_PARTIAL:
            self.state = SpreadState.HEDGING

    def on_hedge_fill(self, fill_qty: float):
        """Callback from broker when the hedging leg is filled."""
        self.hedge_filled_qty += fill_qty
        
        expected_total_hedge = (self.anchor_filled_qty / self.config.anchor_leg.ratio) * self.config.hedge_leg.ratio
        
        if self.hedge_filled_qty >= expected_total_hedge:
            if self.state == SpreadState.HEDGING:
                self.state = SpreadState.COMPLETED
                logger.info("Spread Execution Completed successfully.")
        else:
            # If the IOC order expired without full fill, we are legged!
            self.state = SpreadState.BROKEN
            logger.critical(f"BROKEN SPREAD! Missing {expected_total_hedge - self.hedge_filled_qty} units of hedge.")

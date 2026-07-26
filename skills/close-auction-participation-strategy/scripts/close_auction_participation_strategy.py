import logging
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, time

logger = logging.getLogger(__name__)

@dataclass
class NoiiMessage:
    symbol: str
    timestamp: datetime
    paired_shares: int
    imbalance_shares: int
    imbalance_direction: str  # 'B' (Buy), 'S' (Sell), 'N' (No Imbalance)
    far_price: float
    near_price: float
    reference_price: float

@dataclass
class LocOrder:
    symbol: str
    side: str          # 'BUY' or 'SELL'
    quantity: int
    limit_price: float
    order_type: str = "LOC" # Limit-On-Close

class CloseAuctionParticipationStrategy:
    """
    Parses NOII feeds and generates contra-side LOC orders to absorb auction imbalances
    prior to venue cutoff times.
    """
    def __init__(self, max_participation_pct: float = 0.10, cutoff_time: time = time(15, 55, 0)):
        self.max_participation_pct = max_participation_pct  # Max 10% of imbalance
        self.cutoff_time = cutoff_time                      # e.g., 3:55 PM ET cutoff

    def generate_auction_order(self, noii: NoiiMessage) -> Optional[LocOrder]:
        """
        Processes NOII message and calculates contra-side LOC order parameters.
        Returns None if past cutoff time or if no actionable imbalance exists.
        """
        # 1. Cutoff Time Check
        msg_time = noii.timestamp.time()
        if msg_time >= self.cutoff_time:
            logger.warning(
                f"NOII message timestamp {msg_time} is at or past cutoff time {self.cutoff_time}. Order entry blocked."
            )
            return None

        # 2. Imbalance Check
        if noii.imbalance_direction == 'N' or noii.imbalance_shares <= 0:
            logger.info(f"No actionable imbalance for {noii.symbol}.")
            return None

        # 3. Size Calculation (Contra-side)
        target_qty = int(noii.imbalance_shares * self.max_participation_pct)
        if target_qty <= 0:
            return None

        # 4. Side & Price Calculation
        if noii.imbalance_direction == 'B':
            # Excess Buy Imbalance -> We provide SELL liquidity at or above Far Price
            order_side = 'SELL'
            limit_price = max(noii.far_price, noii.near_price)
        elif noii.imbalance_direction == 'S':
            # Excess Sell Imbalance -> We provide BUY liquidity at or below Far Price
            order_side = 'BUY'
            limit_price = min(noii.far_price, noii.near_price)
        else:
            return None

        logger.info(
            f"Generated {order_side} LOC order for {noii.symbol}: {target_qty} shares @ ${limit_price:.2f}"
        )
        return LocOrder(
            symbol=noii.symbol,
            side=order_side,
            quantity=target_qty,
            limit_price=limit_price,
            order_type="LOC"
        )

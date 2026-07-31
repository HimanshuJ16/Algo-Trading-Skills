import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any

logger = logging.getLogger(__name__)

@dataclass
class Config:
    param1: float = 1.0
    param2: str = "default"
    max_reprice_attempts: int = 3
    fast_market_velocity_threshold: float = 20.0  # ticks/sec threshold for fast market

@dataclass
class MarketState:
    symbol: str
    best_bid: float
    best_ask: float
    tick_size: float = 0.01
    market_velocity_ticks_per_sec: float = 10.0

@dataclass
class OrderRequest:
    order_id: str
    side: str                            # 'BUY', 'SELL'
    quantity: float
    desired_price: float
    reprice_attempts: int = 0

@dataclass
class FastMarketRepriceReport:
    order_id: str
    symbol: str
    side: str
    original_desired_price: float
    final_limit_price: float
    is_repriced: bool
    is_fast_market: bool
    rejection_churn_prevented: bool
    status: str                          # 'POST_ONLY_ACCEPTED_PASSIVE', 'POST_ONLY_PASSIVE_REPRICED', 'REPRICE_ATTEMPTS_EXCEEDED'
    audit_notes: str

class MainEngine:
    """
    Legacy MainEngine retained for backward compatibility.
    """
    def __init__(self, config: Config):
        self.config = config

    def execute(self) -> bool:
        return True

    def process_data(self, data: float) -> float:
        return data * self.config.param1

class FastMarketPostOnlyRepricer:
    """
    Adaptive fast-market post-only limit repricing engine preventing exchange order rejection churn
    during high-velocity price movements.
    """
    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config()

    def process_order(
        self, market: MarketState, order: OrderRequest
    ) -> FastMarketRepriceReport:
        """
        Evaluates Post-Only order against current market state, repricing passively if spread is crossed.
        """
        is_fast_market = market.market_velocity_ticks_per_sec >= self.config.fast_market_velocity_threshold
        side_upper = order.side.upper()

        if order.reprice_attempts >= self.config.max_reprice_attempts:
            notes = f"REPRICE ATTEMPTS EXCEEDED ({order.reprice_attempts}/{self.config.max_reprice_attempts}). Cancelling order to prevent rate limit churn."
            logger.warning(notes)
            return FastMarketRepriceReport(
                order_id=order.order_id,
                symbol=market.symbol,
                side=side_upper,
                original_desired_price=order.desired_price,
                final_limit_price=order.desired_price,
                is_repriced=False,
                is_fast_market=is_fast_market,
                rejection_churn_prevented=True,
                status="REPRICE_ATTEMPTS_EXCEEDED",
                audit_notes=notes
            )

        final_price = order.desired_price
        is_repriced = False

        if side_upper == "BUY":
            # Buy limit crosses spread if desired_price >= best_ask
            if order.desired_price >= market.best_ask:
                final_price = market.best_bid
                is_repriced = True
        elif side_upper == "SELL":
            # Sell limit crosses spread if desired_price <= best_bid
            if order.desired_price <= market.best_bid:
                final_price = market.best_ask
                is_repriced = True

        # Align with tick size
        final_price = round(round(final_price / market.tick_size) * market.tick_size, 4)

        status = "POST_ONLY_PASSIVE_REPRICED" if is_repriced else "POST_ONLY_ACCEPTED_PASSIVE"
        notes = (
            f"FAST MARKET POST-ONLY REPRICE [{market.symbol} - {status}]: "
            f"Side = {side_upper}, Desired Price = ${order.desired_price:.2f}, Final Limit = ${final_price:.2f} "
            f"(BBO: ${market.best_bid:.2f} / ${market.best_ask:.2f}). "
            f"Fast Market = {is_fast_market} (Velocity = {market.market_velocity_ticks_per_sec:.1f} ticks/s)."
        )

        logger.info(notes)

        return FastMarketRepriceReport(
            order_id=order.order_id,
            symbol=market.symbol,
            side=side_upper,
            original_desired_price=order.desired_price,
            final_limit_price=final_price,
            is_repriced=is_repriced,
            is_fast_market=is_fast_market,
            rejection_churn_prevented=is_repriced,
            status=status,
            audit_notes=notes
        )

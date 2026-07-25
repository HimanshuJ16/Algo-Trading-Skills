"""
post-only-and-maker-taker-fee-optimization: Post-only order flag injector, spread-crossing
passive repricer, and cumulative maker fee savings calculator.
"""
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class FeeSchedule:
    maker_fee_rate: float  # e.g., 0.0005 (5 bps / 0.05%)
    taker_fee_rate: float  # e.g., 0.0025 (25 bps / 0.25%)


@dataclass
class PostOnlyOrderResult:
    is_accepted: bool
    order_payload: Dict[str, Any]
    repriced: bool
    new_limit_price: Optional[float]
    fee_saved_usd: float
    message: str


class MakerTakerFeeOptimizer:
    """
    Injects Post-Only order flags to guarantee Maker fee tiers, handles spread-crossing
    cancellation repricing, and tracks net fee savings.
    """

    def __init__(self, fee_schedule: Optional[FeeSchedule] = None):
        self.fee_schedule = fee_schedule or FeeSchedule(maker_fee_rate=0.0005, taker_fee_rate=0.0025)
        self.total_saved_usd = 0.0

    def prepare_post_only_payload(
        self,
        symbol: str,
        action: str,  # "BUY" or "SELL"
        quantity: float,
        limit_price: float,
        best_bid: float,
        best_ask: float,
        allow_passive_reprice: bool = True,
    ) -> PostOnlyOrderResult:
        """
        Injects post-only parameters and verifies limit price does not cross the spread.
        If price crosses spread, reprices passively to best_bid (for BUY) or best_ask (for SELL).
        """
        action_clean = action.upper()
        target_price = limit_price
        repriced = False

        # Check if proposed limit price crosses the spread (would execute as Taker)
        if action_clean == "BUY" and limit_price >= best_ask:
            if allow_passive_reprice:
                target_price = best_bid
                repriced = True
                logger.info(f"Post-Only Buy price {limit_price} crosses ask {best_ask}. Repriced passively to bid {best_bid}")
            else:
                return PostOnlyOrderResult(
                    is_accepted=False,
                    order_payload={},
                    repriced=False,
                    new_limit_price=None,
                    fee_saved_usd=0.0,
                    message=f"Post-Only Buy rejected: price ${limit_price} crosses ask ${best_ask}.",
                )
        elif action_clean == "SELL" and limit_price <= best_bid:
            if allow_passive_reprice:
                target_price = best_ask
                repriced = True
                logger.info(f"Post-Only Sell price {limit_price} crosses bid {best_bid}. Repriced passively to ask {best_ask}")
            else:
                return PostOnlyOrderResult(
                    is_accepted=False,
                    order_payload={},
                    repriced=False,
                    new_limit_price=None,
                    fee_saved_usd=0.0,
                    message=f"Post-Only Sell rejected: price ${limit_price} crosses bid ${best_bid}.",
                )

        payload = {
            "symbol": symbol,
            "side": action_clean,
            "type": "LIMIT",
            "quantity": quantity,
            "price": target_price,
            "post_only": True,
            "time_in_force": "POC",  # Post-Only Order / Post-or-Cancel
            "execInst": "ParticipateDoNotInitiate",
        }

        # Calculate estimated fee savings compared to Taker execution
        order_value_usd = quantity * target_price
        taker_cost = order_value_usd * self.fee_schedule.taker_fee_rate
        maker_cost = order_value_usd * self.fee_schedule.maker_fee_rate
        saved_usd = max(0.0, taker_cost - maker_cost)
        self.total_saved_usd += saved_usd

        return PostOnlyOrderResult(
            is_accepted=True,
            order_payload=payload,
            repriced=repriced,
            new_limit_price=target_price if repriced else None,
            fee_saved_usd=saved_usd,
            message=f"Post-Only order prepared successfully. Saved ${saved_usd:.2f} in fees.",
        )

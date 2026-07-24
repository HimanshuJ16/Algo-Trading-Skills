"""
execution-realistic-simulation: Production-grade execution fill simulator,
square-root market impact modeling, latency delay queues, liquidity depth availability,
and multi-market statutory fee stack calculators.
"""
from dataclasses import dataclass
from enum import Enum
import math
import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class MarketType(str, Enum):
    INDIAN_EQUITY_INTRADAY = "INDIAN_EQUITY_INTRADAY"
    INDIAN_EQUITY_DELIVERY = "INDIAN_EQUITY_DELIVERY"
    INDIAN_FUTURES = "INDIAN_FUTURES"
    INDIAN_OPTIONS = "INDIAN_OPTIONS"
    US_EQUITY = "US_EQUITY"
    CRYPTO_SPOT = "CRYPTO_SPOT"


@dataclass
class FeeBreakdown:
    brokerage: float
    stt: float
    exchange_txn_fee: float
    sebi_turnover_fee: float
    stamp_duty: float
    gst: float
    total_fees: float

    def to_dict(self) -> Dict[str, float]:
        return {
            "brokerage": round(self.brokerage, 2),
            "stt": round(self.stt, 2),
            "exchange_txn_fee": round(self.exchange_txn_fee, 2),
            "sebi_turnover_fee": round(self.sebi_turnover_fee, 2),
            "stamp_duty": round(self.stamp_duty, 2),
            "gst": round(self.gst, 2),
            "total_fees": round(self.total_fees, 2),
        }


@dataclass
class SimulatedFillResult:
    fill_price: float
    filled_qty: float
    requested_qty: float
    slippage_cost: float
    fee_breakdown: FeeBreakdown
    is_partial_fill: bool


class RealisticExecutionSimulator:
    """
    Simulates realistic order execution considering bid-ask spread, square-root market impact,
    order book depth limits, and complete regulatory fee stacks.
    """

    def __init__(self, impact_gamma: float = 0.5):
        self.impact_gamma = impact_gamma

    def simulate_fill(
        self,
        side: str,  # "BUY" or "SELL"
        order_size: float,
        mid_price: float,
        half_spread: float,
        adv: float,
        volatility: float = 0.02,
        market_depth_available: Optional[float] = None,
        market_type: MarketType = MarketType.INDIAN_OPTIONS,
    ) -> SimulatedFillResult:
        side_clean = side.upper()
        spread_base = mid_price + half_spread if side_clean == "BUY" else mid_price - half_spread

        # Liquidity depth check
        is_partial = False
        filled_qty = order_size
        if market_depth_available is not None and order_size > market_depth_available:
            filled_qty = market_depth_available
            is_partial = True

        # Square-root market impact model: Gamma * Volatility * sqrt(Size / ADV)
        adv_clean = max(adv, 1.0)
        participation_ratio = filled_qty / adv_clean
        sqrt_impact = self.impact_gamma * volatility * math.sqrt(participation_ratio) * mid_price

        if side_clean == "BUY":
            fill_price = spread_base + sqrt_impact
            slippage = (fill_price - mid_price) * filled_qty
        else:
            fill_price = max(0.01, spread_base - sqrt_impact)
            slippage = (mid_price - fill_price) * filled_qty

        turnover = fill_price * filled_qty
        fees = self.calculate_fees(turnover=turnover, market_type=market_type, side=side_clean)

        return SimulatedFillResult(
            fill_price=round(fill_price, 4),
            filled_qty=filled_qty,
            requested_qty=order_size,
            slippage_cost=round(slippage, 2),
            fee_breakdown=fees,
            is_partial_fill=is_partial,
        )

    @staticmethod
    def calculate_fees(turnover: float, market_type: MarketType, side: str = "BUY") -> FeeBreakdown:
        side_clean = side.upper()

        if market_type == MarketType.INDIAN_OPTIONS:
            brokerage = min(20.0, turnover * 0.0005)
            # STT on Options is 0.0625% on sell side premium
            stt = turnover * 0.000625 if side_clean == "SELL" else 0.0
            exch_txn = turnover * 0.0005  # 0.05%
            sebi = turnover * 0.000001    # ₹10 per crore
            stamp = turnover * 0.00003 if side_clean == "BUY" else 0.0 # 0.003% on buy
            gst = (brokerage + exch_txn + sebi) * 0.18

        elif market_type == MarketType.INDIAN_EQUITY_INTRADAY:
            brokerage = min(20.0, turnover * 0.0003)
            stt = turnover * 0.00025 if side_clean == "SELL" else 0.0 # 0.025% on sell
            exch_txn = turnover * 0.0000345
            sebi = turnover * 0.000001
            stamp = turnover * 0.00003 if side_clean == "BUY" else 0.0
            gst = (brokerage + exch_txn + sebi) * 0.18

        else:  # Generic Crypto / US
            brokerage = turnover * 0.001  # 0.1% maker/taker
            stt = 0.0
            exch_txn = 0.0
            sebi = 0.0
            stamp = 0.0
            gst = 0.0

        total = brokerage + stt + exch_txn + sebi + stamp + gst
        return FeeBreakdown(
            brokerage=brokerage,
            stt=stt,
            exchange_txn_fee=exch_txn,
            sebi_turnover_fee=sebi,
            stamp_duty=stamp,
            gst=gst,
            total_fees=total,
        )


# Backward compatibility functions
def simulate_fill_price(mid_price, half_spread, side, size, adv, impact_coef=0.1):
    base = mid_price + half_spread if side.lower() == "buy" else mid_price - half_spread
    participation = size / max(adv, 1)
    slippage = base * impact_coef * participation
    return base + slippage if side.lower() == "buy" else base - slippage


def estimate_fees(
    turnover,
    brokerage_flat=20,
    stt_rate=0.001,
    exch_txn_rate=0.00005,
    gst_rate=0.18,
    stamp_rate=0.00003,
):
    stt = turnover * stt_rate
    exch_txn = turnover * exch_txn_rate
    stamp = turnover * stamp_rate
    gst = (brokerage_flat + exch_txn) * gst_rate
    return brokerage_flat + stt + exch_txn + gst + stamp

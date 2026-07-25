"""
greeks-based-portfolio-hedging-automation: Portfolio net Greeks aggregator,
risk tolerance breach evaluator, and automated hedge order generator.
"""
from dataclasses import dataclass, field
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class OptionPosition:
    symbol: str
    underlying_symbol: str
    quantity: float       # Positive for long, negative for short
    delta: float          # Per share (-1.0 to 1.0)
    vega: float           # Vega per share
    underlying_price: float


@dataclass
class NetGreeksSummary:
    total_positions: int
    net_delta_usd: float
    net_vega_usd: float
    is_delta_breached: bool
    is_vega_breached: bool


@dataclass
class HedgeOrder:
    target_symbol: str
    action: str           # BUY or SELL
    quantity: float
    order_type: str       # MARKET or LIMIT
    rationale: str


@dataclass
class HedgingAuditReport:
    net_greeks: NetGreeksSummary
    recommended_hedge_orders: List[HedgeOrder]
    is_hedging_required: bool
    message: str


class GreeksPortfolioHedgingEngine:
    """
    Aggregates portfolio net Delta & Vega exposures, checks risk tolerance limits,
    and automatically calculates offsetting hedge orders.
    """

    def __init__(
        self,
        max_allowed_delta_usd: float = 50000.0,
        max_allowed_vega_usd: float = 10000.0,
        min_rebalance_delta_usd: float = 10000.0,
    ):
        self.max_allowed_delta_usd = max_allowed_delta_usd
        self.max_allowed_vega_usd = max_allowed_vega_usd
        self.min_rebalance_delta_usd = min_rebalance_delta_usd

    def compute_net_greeks(self, positions: List[OptionPosition]) -> NetGreeksSummary:
        tot_delta_usd = 0.0
        tot_vega_usd = 0.0

        for p in positions:
            # Delta USD = Quantity * Delta * Underlying Price
            pos_delta_usd = p.quantity * p.delta * p.underlying_price
            pos_vega_usd = p.quantity * p.vega * 100.0  # per 1% vol

            tot_delta_usd += pos_delta_usd
            tot_vega_usd += pos_vega_usd

        is_d_breached = abs(tot_delta_usd) > self.max_allowed_delta_usd
        is_v_breached = abs(tot_vega_usd) > self.max_allowed_vega_usd

        return NetGreeksSummary(
            total_positions=len(positions),
            net_delta_usd=round(tot_delta_usd, 2),
            net_vega_usd=round(tot_vega_usd, 2),
            is_delta_breached=is_d_breached,
            is_vega_breached=is_v_breached,
        )

    def evaluate_and_hedge(
        self,
        positions: List[OptionPosition],
        hedge_instrument_symbol: str = "SPY",
        hedge_instrument_price: float = 500.0,
    ) -> HedgingAuditReport:
        summary = self.compute_net_greeks(positions)
        hedge_orders: List[HedgeOrder] = []

        if abs(summary.net_delta_usd) >= self.min_rebalance_delta_usd:
            # Shares needed = - (Net Delta USD / Hedge Price)
            shares_needed = - (summary.net_delta_usd / hedge_instrument_price)
            action = "BUY" if shares_needed > 0 else "SELL"
            qty = abs(round(shares_needed, 0))

            if qty > 0:
                hedge_orders.append(HedgeOrder(
                    target_symbol=hedge_instrument_symbol,
                    action=action,
                    quantity=qty,
                    order_type="MARKET",
                    rationale=f"Delta Hedge: Offset ${summary.net_delta_usd:,.2f} net delta exposure.",
                ))

        needs_hedge = len(hedge_orders) > 0

        if needs_hedge:
            msg = (
                f"HEDGE REQUIRED: Net Delta=${summary.net_delta_usd:,.2f} "
                f"breached threshold. Generated {len(hedge_orders)} rebalancing orders."
            )
            logger.warning(msg)
        else:
            msg = f"PORTFOLIO DELTA NEUTRAL: Net Delta=${summary.net_delta_usd:,.2f} within risk limits."
            logger.info(msg)

        return HedgingAuditReport(
            net_greeks=summary,
            recommended_hedge_orders=hedge_orders,
            is_hedging_required=needs_hedge,
            message=msg,
        )

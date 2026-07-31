import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class InternalOrder:
    order_id: str
    strategy_id: str
    symbol: str
    side: str                            # 'BUY' or 'SELL'
    quantity: int
    limit_price: Optional[float] = None

@dataclass
class MarketQuote:
    symbol: str
    bid_price: float
    ask_price: float
    fee_per_share_usd: float = 0.003     # Exchange taker fee per share

@dataclass
class InternalFill:
    order_id: str
    strategy_id: str
    symbol: str
    side: str
    filled_quantity: int
    fill_price: float

@dataclass
class ExternalRoutedOrder:
    symbol: str
    side: str                            # 'BUY' or 'SELL'
    quantity: int
    order_type: str                      # 'MARKET'

@dataclass
class NettingReport:
    symbol: str
    total_buy_quantity: int
    total_sell_quantity: int
    internal_matched_quantity: int
    internal_match_price: float
    net_external_quantity: int
    external_order: Optional[ExternalRoutedOrder]
    internal_fills: List[InternalFill]
    exchange_fee_savings_usd: float
    spread_savings_usd: float
    total_cost_savings_usd: float
    status: str                          # 'NETTING_SUCCESS'
    audit_notes: str

class MultiOrderNettingEngine:
    """
    Multi-order pre-routing netting engine matching internal buy/sell orders at mid-price
    before routing residual net orders to external exchanges.
    """
    def __init__(self):
        pass

    def net_and_route_orders(
        self, quote: MarketQuote, orders: List[InternalOrder]
    ) -> NettingReport:
        """
        Matches opposing internal orders at mid-price and constructs single external routed order for net residual.
        """
        if not orders:
            raise ValueError("Orders list cannot be empty.")

        symbol = quote.symbol
        mid_price = round((quote.bid_price + quote.ask_price) / 2.0, 4)
        spread = quote.ask_price - quote.bid_price

        buy_orders = [o for o in orders if o.side.upper() == "BUY"]
        sell_orders = [o for o in orders if o.side.upper() == "SELL"]

        total_buy_qty = sum(o.quantity for o in buy_orders)
        total_sell_qty = sum(o.quantity for o in sell_orders)

        matched_qty = min(total_buy_qty, total_sell_qty)
        residual_qty = abs(total_buy_qty - total_sell_qty)

        internal_fills: List[InternalFill] = []

        # 1. Allocate Internal Fills to Buy Orders
        rem_match_buy = matched_qty
        for o in buy_orders:
            fill_q = min(o.quantity, rem_match_buy)
            if fill_q > 0:
                internal_fills.append(InternalFill(
                    order_id=o.order_id,
                    strategy_id=o.strategy_id,
                    symbol=symbol,
                    side="BUY",
                    filled_quantity=fill_q,
                    fill_price=mid_price
                ))
                rem_match_buy -= fill_q

        # 2. Allocate Internal Fills to Sell Orders
        rem_match_sell = matched_qty
        for o in sell_orders:
            fill_q = min(o.quantity, rem_match_sell)
            if fill_q > 0:
                internal_fills.append(InternalFill(
                    order_id=o.order_id,
                    strategy_id=o.strategy_id,
                    symbol=symbol,
                    side="SELL",
                    filled_quantity=fill_q,
                    fill_price=mid_price
                ))
                rem_match_sell -= fill_q

        # 3. Construct External Order for Residual
        external_order: Optional[ExternalRoutedOrder] = None
        if residual_qty > 0:
            dom_side = "BUY" if total_buy_qty > total_sell_qty else "SELL"
            external_order = ExternalRoutedOrder(
                symbol=symbol,
                side=dom_side,
                quantity=residual_qty,
                order_type="MARKET"
            )

        # 4. Compute Cost & Savings Metrics
        fee_savings = 2.0 * matched_qty * quote.fee_per_share_usd
        spread_savings = matched_qty * spread
        total_savings = fee_savings + spread_savings

        notes = (
            f"MULTI-ORDER NETTING SUCCESS [{symbol}]: Buy Qty = {total_buy_qty}, Sell Qty = {total_sell_qty}. "
            f"Internal Matched = {matched_qty} @ ${mid_price:.4f}. Net External Order = {residual_qty} {external_order.side if external_order else 'NONE'}. "
            f"Fee Savings = ${fee_savings:.2f}, Spread Savings = ${spread_savings:.2f}, Total Savings = ${total_savings:.2f}."
        )
        logger.info(notes)

        return NettingReport(
            symbol=symbol,
            total_buy_quantity=total_buy_qty,
            total_sell_quantity=total_sell_qty,
            internal_matched_quantity=matched_qty,
            internal_match_price=mid_price,
            net_external_quantity=residual_qty,
            external_order=external_order,
            internal_fills=internal_fills,
            exchange_fee_savings_usd=round(fee_savings, 2),
            spread_savings_usd=round(spread_savings, 2),
            total_cost_savings_usd=round(total_savings, 2),
            status="NETTING_SUCCESS",
            audit_notes=notes
        )

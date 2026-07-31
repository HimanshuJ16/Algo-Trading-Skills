import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class L3OrderMessage:
    order_id: str
    msg_type: str                       # 'ADD', 'CANCEL', 'EXECUTE', 'REPLACE'
    side: str                           # 'BUY' or 'SELL'
    price: float
    quantity: int
    timestamp_nanos: int

@dataclass
class OrderDetail:
    order_id: str
    side: str
    price: float
    quantity: int

@dataclass
class PriceLevelDepth:
    price: float
    total_quantity: int
    order_count: int

@dataclass
class OrderBookReconstructionReport:
    symbol: str
    best_bid_price: Optional[float]
    best_bid_qty: int
    best_ask_price: Optional[float]
    best_ask_qty: int
    mid_price: Optional[float]
    spread: Optional[float]
    is_crossed_book: bool
    l2_bids_top5: List[PriceLevelDepth]
    l2_asks_top5: List[PriceLevelDepth]
    total_active_l3_orders: int
    audit_notes: str

class HistoricalOrderBookReconstructEngine:
    """
    Quantitative market microstructure engine for replaying raw Level 3 (L3 ITCH) message logs
    (Add, Cancel, Execute, Replace) to reconstruct Level 2 (L2) aggregated order book depth and BBO states.
    """
    def __init__(self, symbol: str = "AAPL"):
        self.symbol = symbol
        self.active_orders: Dict[str, OrderDetail] = {} # order_id -> OrderDetail

    def process_l3_message(self, msg: L3OrderMessage) -> None:
        """
        Updates internal L3 order map state based on incoming event message.
        """
        m_type = msg.msg_type.upper()
        side_clean = msg.side.upper()

        if m_type == "ADD":
            self.active_orders[msg.order_id] = OrderDetail(
                order_id=msg.order_id,
                side=side_clean,
                price=msg.price,
                quantity=msg.quantity
            )
        elif m_type == "CANCEL":
            if msg.order_id in self.active_orders:
                existing = self.active_orders[msg.order_id]
                # Partial or full cancel
                if msg.quantity >= existing.quantity:
                    del self.active_orders[msg.order_id]
                else:
                    existing.quantity -= msg.quantity
        elif m_type == "EXECUTE":
            if msg.order_id in self.active_orders:
                existing = self.active_orders[msg.order_id]
                if msg.quantity >= existing.quantity:
                    del self.active_orders[msg.order_id]
                else:
                    existing.quantity -= msg.quantity
        elif m_type == "REPLACE":
            if msg.order_id in self.active_orders:
                del self.active_orders[msg.order_id]
            self.active_orders[msg.order_id] = OrderDetail(
                order_id=msg.order_id,
                side=side_clean,
                price=msg.price,
                quantity=msg.quantity
            )
        else:
            raise ValueError(f"Unknown message type '{msg.msg_type}'.")

    def get_l2_reconstructed_snapshot(self, top_n_levels: int = 5) -> OrderBookReconstructionReport:
        """
        Aggregates L3 order map into sorted L2 price-level depth snapshot and BBO metrics.
        """
        bid_map: Dict[float, List[int]] = {} # price -> [qty, count]
        ask_map: Dict[float, List[int]] = {}

        for order in self.active_orders.values():
            if order.quantity <= 0:
                continue
            target_map = bid_map if order.side == "BUY" else ask_map
            if order.price not in target_map:
                target_map[order.price] = [0, 0]
            target_map[order.price][0] += order.quantity
            target_map[order.price][1] += 1

        # Sort Bids (descending price) and Asks (ascending price)
        sorted_bid_prices = sorted(bid_map.keys(), reverse=True)
        sorted_ask_prices = sorted(ask_map.keys(), reverse=False)

        l2_bids = [
            PriceLevelDepth(price=p, total_quantity=bid_map[p][0], order_count=bid_map[p][1])
            for p in sorted_bid_prices[:top_n_levels]
        ]
        l2_asks = [
            PriceLevelDepth(price=p, total_quantity=ask_map[p][0], order_count=ask_map[p][1])
            for p in sorted_ask_prices[:top_n_levels]
        ]

        best_bid = l2_bids[0].price if l2_bids else None
        best_bid_qty = l2_bids[0].total_quantity if l2_bids else 0
        best_ask = l2_asks[0].price if l2_asks else None
        best_ask_qty = l2_asks[0].total_quantity if l2_asks else 0

        mid_price = None
        spread = None
        is_crossed = False

        if best_bid is not None and best_ask is not None:
            mid_price = round((best_bid + best_ask) / 2.0, 4)
            spread = round(best_ask - best_bid, 4)
            is_crossed = (best_bid >= best_ask)

        notes = (
            f"L2 BOOK RECONSTRUCTED [{self.symbol}]: "
            f"BBO = ${best_bid} ({best_bid_qty:,}) / ${best_ask} ({best_ask_qty:,}), "
            f"Spread = ${spread}, Mid = ${mid_price}. Active L3 Orders = {len(self.active_orders)}."
        )
        if is_crossed:
            notes += " ALERT: CROSSED ORDER BOOK DETECTED!"
            logger.warning(notes)
        else:
            logger.info(notes)

        return OrderBookReconstructionReport(
            symbol=self.symbol,
            best_bid_price=best_bid,
            best_bid_qty=best_bid_qty,
            best_ask_price=best_ask,
            best_ask_qty=best_ask_qty,
            mid_price=mid_price,
            spread=spread,
            is_crossed_book=is_crossed,
            l2_bids_top5=l2_bids,
            l2_asks_top5=l2_asks,
            total_active_l3_orders=len(self.active_orders),
            audit_notes=notes
        )

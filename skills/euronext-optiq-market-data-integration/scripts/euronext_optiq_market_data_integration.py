import struct
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Optiq Message Types
MSG_TYPE_MARKET_UPDATE = 1001
MSG_TYPE_FULL_BOOK = 1002
MSG_TYPE_TRADE = 1004
MSG_TYPE_SYMBOL_STATUS = 1005

@dataclass
class OrderBookLevel:
    price_eur: float
    quantity: int
    num_orders: int = 1

@dataclass
class OptiqMarketDataAuditReport:
    isin: str
    symbol_id: str
    trading_status: str                 # 'CONTINUOUS_TRADING', 'CALL_AUCTION', 'TRADING_HALTED'
    best_bid_eur: Optional[float]
    best_ask_eur: Optional[float]
    mid_price_eur: Optional[float]
    spread_eur: Optional[float]
    book_imbalance_ratio: float         # -1.0 to +1.0
    sequence_number: int
    book_in_ns: int
    is_quoting_allowed: bool
    audit_notes: str

class EuronextOptiqMarketDataEngine:
    """
    Quantitative venue integration engine for parsing Euronext Optiq Market Data Gateway (MDG) Simple Binary Encoding (SBE) multicast feeds,
    reconstructing L2 order book depth, and monitoring trading state transitions.
    """
    def __init__(self):
        self.bids: Dict[float, OrderBookLevel] = {}
        self.asks: Dict[float, OrderBookLevel] = {}
        self.trading_status = "CONTINUOUS_TRADING"
        self.last_seq_num = 0

    def parse_optiq_sbe_header(self, payload: bytes) -> Tuple[int, int, int, int]:
        """
        Parses Optiq SBE Header (16 bytes):
        - uint16 msg_size
        - uint16 msg_type (1001, 1002, 1004, 1005)
        - uint32 sequence_number
        - uint64 book_in_ns
        """
        if len(payload) < 16:
            raise ValueError(f"Optiq payload length {len(payload)} < 16 byte header size.")

        msg_size, msg_type, seq_num, book_in_ns = struct.unpack("<HHQI", payload[:16])
        return msg_size, msg_type, seq_num, book_in_ns

    def update_book_level(self, side: str, price_eur: float, quantity: int, action: str):
        """
        Updates L2 limit order book depth (ADD, MODIFY, DELETE).
        """
        target = self.bids if side.upper() == "BUY" else self.asks

        if action.upper() in ["ADD", "MODIFY"]:
            if quantity > 0:
                target[price_eur] = OrderBookLevel(price_eur=price_eur, quantity=quantity)
            elif price_eur in target:
                del target[price_eur]
        elif action.upper() == "DELETE":
            if price_eur in target:
                del target[price_eur]

    def process_optiq_message(
        self,
        isin: str,
        symbol_id: str,
        msg_type: int,
        seq_num: int,
        book_in_ns: int,
        status_override: Optional[str] = None
    ) -> OptiqMarketDataAuditReport:
        """
        Reconstructs L2 book, computes microstructure signals (mid, spread, imbalance), and audits trading status.
        """
        self.last_seq_num = seq_num

        if status_override:
            self.trading_status = status_override.upper()

        sorted_bids = sorted(self.bids.keys(), reverse=True)
        sorted_asks = sorted(self.asks.keys())

        best_bid = sorted_bids[0] if sorted_bids else None
        best_ask = sorted_asks[0] if sorted_asks else None

        mid_price = None
        spread = None
        if best_bid is not None and best_ask is not None:
            mid_price = round((best_bid + best_ask) / 2.0, 4)
            spread = round(best_ask - best_bid, 4)

        # Imbalance ratio: (BidVol - AskVol) / (BidVol + AskVol)
        bid_vol = self.bids[best_bid].quantity if best_bid else 0
        ask_vol = self.asks[best_ask].quantity if best_ask else 0
        tot_vol = bid_vol + ask_vol
        imbalance = round((bid_vol - ask_vol) / float(tot_vol), 4) if tot_vol > 0 else 0.0

        is_quoting_ok = (self.trading_status == "CONTINUOUS_TRADING")

        if is_quoting_ok:
            notes = f"OPTIQ L2 BOOK UPDATED [{symbol_id}]: Mid=€{mid_price}, Spread=€{spread}, Imbalance={imbalance:+.2f}."
            logger.info(notes)
        else:
            notes = f"OPTIQ TRADING HALTED [{symbol_id}]: Status = {self.trading_status}. Quoting disabled."
            logger.warning(notes)

        return OptiqMarketDataAuditReport(
            isin=isin,
            symbol_id=symbol_id,
            trading_status=self.trading_status,
            best_bid_eur=best_bid,
            best_ask_eur=best_ask,
            mid_price_eur=mid_price,
            spread_eur=spread,
            book_imbalance_ratio=imbalance,
            sequence_number=seq_num,
            book_in_ns=book_in_ns,
            is_quoting_allowed=is_quoting_ok,
            audit_notes=notes
        )

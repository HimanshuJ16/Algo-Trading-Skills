"""
binary-protocol-parsing-for-low-latency-feeds: High-performance binary struct
unpacker for NASDAQ ITCH 5.0 and similar binary market data feeds.
Engineered for quantitative trading environments where microsecond latency matters.
"""
from dataclasses import dataclass
import struct
import logging
from typing import Optional

logger = logging.getLogger(__name__)

@dataclass(slots=True)
class ITCHAddOrderMessage:
    """
    Data class representing a NASDAQ ITCH 5.0 Add Order (MsgType 'A').
    Uses __slots__ for reduced memory footprint and faster attribute access.
    """
    message_type: str        # 'A' (1 byte)
    stock_locate: int        # uint16 (2 bytes)
    tracking_number: int     # uint16 (2 bytes)
    timestamp_ns: int        # uint48 (6 bytes)
    order_ref_id: int        # uint64 (8 bytes)
    buy_sell: str            # 'B' or 'S' (1 byte)
    shares: int              # uint32 (4 bytes)
    stock: str               # char[8] (8 bytes)
    price: float             # uint32 scaled 1e4 (4 bytes) -> float

class BinaryFeedParserEngine:
    """
    Optimized binary parser for NASDAQ ITCH style protocol messages.
    Pre-compiles struct definitions for minimal parsing overhead.
    """
    # Price scale factor per ITCH specification (prices are scaled by 10,000)
    PRICE_SCALE_FACTOR = 10000.0
    
    # ITCH 5.0 Add Order 'A' Message Layout: 36 bytes total
    # MsgType(c), StockLocate(H), Tracking(H), Timestamp(6s), OrderRef(Q), BuySell(c), Shares(I), Stock(8s), Price(I)
    # Total: 1 + 2 + 2 + 6 + 8 + 1 + 4 + 8 + 4 = 36 bytes
    ADD_ORDER_FORMAT = struct.Struct(">cHH6sQcI8sI")

    @classmethod
    def pack_itch_add_order(
        cls,
        stock_locate: int,
        tracking_number: int,
        timestamp_ns: int,
        order_ref_id: int,
        buy_sell: str,
        shares: int,
        stock: str,
        price: float,
    ) -> bytes:
        """
        Packs an Add Order ('A') message into a 36-byte ITCH binary frame.
        """
        msg_type = b'A'
        bs_byte = buy_sell.upper().encode("ascii")[:1]
        stock_bytes = stock.upper().ljust(8).encode("ascii")[:8]
        price_int = int(round(price * cls.PRICE_SCALE_FACTOR))
        
        # 48-bit unsigned integer packing (big-endian)
        ts_bytes = timestamp_ns.to_bytes(6, byteorder='big', signed=False)

        frame = cls.ADD_ORDER_FORMAT.pack(
            msg_type,
            stock_locate,
            tracking_number,
            ts_bytes,
            order_ref_id,
            bs_byte,
            shares,
            stock_bytes,
            price_int,
        )
        return frame

    @classmethod
    def unpack_itch_add_order(cls, raw_bytes: memoryview) -> ITCHAddOrderMessage:
        """
        Unpacks a 36-byte ITCH Add Order binary frame with minimal latency overhead.
        Expects a memoryview to avoid unnecessary copying during bulk network ingestion.
        """
        if len(raw_bytes) < cls.ADD_ORDER_FORMAT.size:
            raise ValueError(f"Invalid ITCH frame size: {len(raw_bytes)} bytes (Expected {cls.ADD_ORDER_FORMAT.size} bytes)")

        (
            msg_type_b,
            locate,
            track,
            ts_bytes,
            ref_id,
            bs_b,
            shares,
            stock_b,
            price_int
        ) = cls.ADD_ORDER_FORMAT.unpack_from(raw_bytes, 0)

        # 48-bit timestamp unpacking
        timestamp_ns = int.from_bytes(ts_bytes, byteorder='big', signed=False)

        return ITCHAddOrderMessage(
            message_type=msg_type_b.decode("ascii"),
            stock_locate=locate,
            tracking_number=track,
            timestamp_ns=timestamp_ns,
            order_ref_id=ref_id,
            buy_sell=bs_b.decode("ascii"),
            shares=shares,
            stock=stock_b.decode("ascii").strip(),
            price=price_int / cls.PRICE_SCALE_FACTOR,
        )

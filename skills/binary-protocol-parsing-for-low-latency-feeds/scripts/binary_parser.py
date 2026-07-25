"""
binary-protocol-parsing-for-low-latency-feeds: Zero-copy binary struct unpacker
for NASDAQ ITCH / CME MDP style binary market data feeds.
"""
from dataclasses import dataclass, field
import logging
import struct
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ITCHAddOrderMessage:
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
    High-speed binary struct parser for NASDAQ ITCH style protocol messages.
    """

    PRICE_SCALE_FACTOR = 10000.0  # ITCH prices scaled by 10,000 (4 decimals)

    def pack_itch_add_order(
        self,
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
        price_int = int(round(price * self.PRICE_SCALE_FACTOR))

        # Format: >c HH Q Q c I 8s I
        # Total size = 1 + 2 + 2 + 8 + 8 + 1 + 4 + 8 + 4 = 38 bytes
        frame = struct.pack(
            ">cHHQQcI8sI",
            msg_type,
            stock_locate,
            tracking_number,
            timestamp_ns,
            order_ref_id,
            bs_byte,
            shares,
            stock_bytes,
            price_int,
        )
        return frame

    def unpack_itch_add_order(self, raw_bytes: bytes) -> ITCHAddOrderMessage:
        """
        Unpacks a 38-byte ITCH Add Order binary frame with sub-microsecond latency.
        """
        if len(raw_bytes) < 38:
            raise ValueError(f"Invalid ITCH frame size: {len(raw_bytes)} bytes (Expected 38 bytes)")

        msg_type_b, locate, track, timestamp_ns, ref_id, bs_b, shares, stock_b, price_int = struct.unpack_from(
            ">cHHQQcI8sI", raw_bytes, 0
        )

        msg_type = msg_type_b.decode("ascii")
        buy_sell = bs_b.decode("ascii")
        stock = stock_b.decode("ascii").strip()
        price = price_int / self.PRICE_SCALE_FACTOR

        return ITCHAddOrderMessage(
            message_type=msg_type,
            stock_locate=locate,
            tracking_number=track,
            timestamp_ns=timestamp_ns,
            order_ref_id=ref_id,
            buy_sell=buy_sell,
            shares=shares,
            stock=stock,
            price=price,
        )

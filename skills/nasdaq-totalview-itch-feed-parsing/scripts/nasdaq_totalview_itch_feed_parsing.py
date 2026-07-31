import struct
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class ITCHOrderState:
    order_ref_number: int
    stock: str
    side: str                            # 'B' or 'S'
    shares: int
    price_usd: float
    timestamp_ns: int

@dataclass
class ITCHParsedMessage:
    message_type: str                    # 'A', 'E', 'X', 'D', 'P'
    stock_locate: int
    tracking_number: int
    timestamp_ns: int
    order_ref_number: Optional[int] = None
    side: Optional[str] = None
    shares: Optional[int] = None
    stock: Optional[str] = None
    price_usd: Optional[float] = None
    executed_shares: Optional[int] = None
    canceled_shares: Optional[int] = None

@dataclass
class ITCHParserReport:
    total_messages_parsed: int
    active_orders_count: int
    last_parsed_message: Optional[ITCHParsedMessage]
    status: str                          # 'PARSER_SUCCESS'
    audit_notes: str

class NasdaqITCH50ParserEngine:
    """
    Nasdaq TotalView-ITCH 5.0 binary protocol parsing engine unpacking Add Order (A),
    Order Executed (E), Cancel (X), and Delete (D) messages for L3 order book reconstruction.
    """
    # Struct formats (Big-endian >)
    # Type 'A': Type(1s), Locate(2H), Tracking(2H), Timestamp(6s), OrderRef(8Q), Side(1s), Shares(4I), Stock(8s), Price(4I)
    # Total payload size after 1-byte type = 35 bytes (Total 36 bytes)
    STRUCT_A = struct.Struct(">HH6sQ1sI8sI")

    # Type 'E': Locate(2H), Tracking(2H), Timestamp(6s), OrderRef(8Q), ExecutedShares(4I), MatchNum(8Q)
    # Total payload size after type = 30 bytes (Total 31 bytes)
    STRUCT_E = struct.Struct(">HH6sQIQ")

    # Type 'X': Locate(2H), Tracking(2H), Timestamp(6s), OrderRef(8Q), CanceledShares(4I)
    # Total payload size after type = 22 bytes (Total 23 bytes)
    STRUCT_X = struct.Struct(">HH6sQI")

    # Type 'D': Locate(2H), Tracking(2H), Timestamp(6s), OrderRef(8Q)
    # Total payload size after type = 18 bytes (Total 19 bytes)
    STRUCT_D = struct.Struct(">HH6sQ")

    PRICE_DIVISOR = 10000.0              # ITCH 5.0 prices are integer x 10,000

    def __init__(self):
        self.active_orders: Dict[int, ITCHOrderState] = {}
        self.parsed_messages_count: int = 0

    def _unpack_6byte_timestamp(self, ts_bytes: bytes) -> int:
        """Unpacks 6-byte big-endian nanosecond timestamp."""
        high, low = struct.unpack(">HI", ts_bytes)
        return (high << 32) | low

    def parse_message(self, raw_bytes: bytes) -> ITCHParsedMessage:
        """
        Parses a single binary ITCH 5.0 message byte string and updates L3 order book state.
        """
        if not raw_bytes:
            raise ValueError("Raw binary message cannot be empty.")

        msg_type = chr(raw_bytes[0])
        payload = raw_bytes[1:]

        if msg_type == "A":
            locate, tracking, ts_b, ref_num, side_b, shares, stock_b, price_int = self.STRUCT_A.unpack(payload)
            ts_ns = self._unpack_6byte_timestamp(ts_b)
            side = side_b.decode("ascii")
            stock = stock_b.decode("ascii").strip()
            price_usd = price_int / self.PRICE_DIVISOR

            # Store active order state
            self.active_orders[ref_num] = ITCHOrderState(
                order_ref_number=ref_num,
                stock=stock,
                side=side,
                shares=shares,
                price_usd=price_usd,
                timestamp_ns=ts_ns
            )
            self.parsed_messages_count += 1

            return ITCHParsedMessage(
                message_type="A",
                stock_locate=locate,
                tracking_number=tracking,
                timestamp_ns=ts_ns,
                order_ref_number=ref_num,
                side=side,
                shares=shares,
                stock=stock,
                price_usd=price_usd
            )

        elif msg_type == "E":
            locate, tracking, ts_b, ref_num, exec_shares, match_num = self.STRUCT_E.unpack(payload)
            ts_ns = self._unpack_6byte_timestamp(ts_b)

            if ref_num in self.active_orders:
                ord_state = self.active_orders[ref_num]
                ord_state.shares -= exec_shares
                if ord_state.shares <= 0:
                    del self.active_orders[ref_num]

            self.parsed_messages_count += 1

            return ITCHParsedMessage(
                message_type="E",
                stock_locate=locate,
                tracking_number=tracking,
                timestamp_ns=ts_ns,
                order_ref_number=ref_num,
                executed_shares=exec_shares
            )

        elif msg_type == "X":
            locate, tracking, ts_b, ref_num, cancel_shares = self.STRUCT_X.unpack(payload)
            ts_ns = self._unpack_6byte_timestamp(ts_b)

            if ref_num in self.active_orders:
                ord_state = self.active_orders[ref_num]
                ord_state.shares -= cancel_shares
                if ord_state.shares <= 0:
                    del self.active_orders[ref_num]

            self.parsed_messages_count += 1

            return ITCHParsedMessage(
                message_type="X",
                stock_locate=locate,
                tracking_number=tracking,
                timestamp_ns=ts_ns,
                order_ref_number=ref_num,
                canceled_shares=cancel_shares
            )

        elif msg_type == "D":
            locate, tracking, ts_b, ref_num = self.STRUCT_D.unpack(payload)
            ts_ns = self._unpack_6byte_timestamp(ts_b)

            if ref_num in self.active_orders:
                del self.active_orders[ref_num]

            self.parsed_messages_count += 1

            return ITCHParsedMessage(
                message_type="D",
                stock_locate=locate,
                tracking_number=tracking,
                timestamp_ns=ts_ns,
                order_ref_number=ref_num
            )

        else:
            raise ValueError(f"Unsupported ITCH message type: '{msg_type}'")

    def generate_report(self, last_msg: Optional[ITCHParsedMessage] = None) -> ITCHParserReport:
        notes = (
            f"ITCH PARSER SUCCESS: Parsed {self.parsed_messages_count} messages. "
            f"Active L3 Orders in Book = {len(self.active_orders)}."
        )
        logger.info(notes)

        return ITCHParserReport(
            total_messages_parsed=self.parsed_messages_count,
            active_orders_count=len(self.active_orders),
            last_parsed_message=last_msg,
            status="PARSER_SUCCESS",
            audit_notes=notes
        )

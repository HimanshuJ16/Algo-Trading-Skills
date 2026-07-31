import struct
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class NYSEOrderState:
    order_id: int
    symbol: str
    side: str                            # 'B' or 'S'
    volume: int
    price_usd: float
    source_time_ns: int

@dataclass
class NYSEParsedMessage:
    msg_type: int                        # 100 (Add), 101 (Modify), 102 (Delete), 103 (Execution)
    order_id: int
    symbol: str
    side: Optional[str] = None
    volume: Optional[int] = None
    price_usd: Optional[float] = None
    executed_volume: Optional[int] = None

@dataclass
class NYSEFeedReport:
    total_messages_parsed: int
    active_orders_count: int
    last_parsed_message: Optional[NYSEParsedMessage]
    status: str                          # 'FEED_PARSER_SUCCESS'
    audit_notes: str

class NYSEArcaIntegratedFeedEngine:
    """
    NYSE Arca Integrated Feed (XDP) binary protocol parsing engine unpacking Add Order (100),
    Modify Order (101), Delete Order (102), and Execution (103) messages for L3 order book reconstruction.
    """
    # XDP Little-endian < struct layouts
    # Packet Header (16 bytes): PktSize(2H), DeliveryFlag(1B), NumberMsgs(1B), SeqNum(4I), SendTime(4I), SendTimeNS(4I)
    PKT_HEADER = struct.Struct("<HBBIII")

    # Message Header (4 bytes): MsgSize(2H), MsgType(2H)
    MSG_HEADER = struct.Struct("<HH")

    # Payload 100 (Add Order): SourceTime(4I), SourceTimeNS(4I), SymbolIndex(4I), SymbolSeqNum(4I), OrderID(8Q), Price(4I), Volume(4I), Side(1s), FirmID(4s)
    # Size after msg header = 37 bytes
    STRUCT_100 = struct.Struct("<IIIIQIIs4s")

    # Payload 101 (Modify Order): SourceTime(4I), SourceTimeNS(4I), SymbolIndex(4I), SymbolSeqNum(4I), OrderID(8Q), Price(4I), Volume(4I)
    # Size after msg header = 32 bytes
    STRUCT_101 = struct.Struct("<IIIIQII")

    # Payload 102 (Delete Order): SourceTime(4I), SourceTimeNS(4I), SymbolIndex(4I), SymbolSeqNum(4I), OrderID(8Q)
    # Size after msg header = 24 bytes
    STRUCT_102 = struct.Struct("<IIIIQ")

    # Payload 103 (Execution): SourceTime(4I), SourceTimeNS(4I), SymbolIndex(4I), SymbolSeqNum(4I), OrderID(8Q), Price(4I), Volume(4I), PrintableFlag(1s)
    # Size after msg header = 33 bytes
    STRUCT_103 = struct.Struct("<IIIIQII1s")

    PRICE_DIVISOR = 10000.0              # XDP prices are integer x 10,000

    def __init__(self, symbol_index_map: Optional[Dict[int, str]] = None):
        self.symbol_index_map = symbol_index_map or {101: "SPY", 102: "AAPL", 103: "MSFT"}
        self.active_orders: Dict[int, NYSEOrderState] = {}
        self.parsed_messages_count: int = 0

    def parse_xdp_packet(self, packet_bytes: bytes) -> List[NYSEParsedMessage]:
        """
        Parses an XDP packet containing one or more binary messages and updates L3 order book state.
        """
        if len(packet_bytes) < 16:
            raise ValueError("Packet byte length shorter than 16-byte XDP packet header.")

        pkt_size, deliv_flag, num_msgs, seq_num, send_time, send_time_ns = self.PKT_HEADER.unpack(packet_bytes[:16])
        offset = 16
        parsed_msgs: List[NYSEParsedMessage] = []

        for _ in range(num_msgs):
            if offset + 4 > len(packet_bytes):
                break

            msg_size, msg_type = self.MSG_HEADER.unpack(packet_bytes[offset:offset+4])
            payload = packet_bytes[offset+4 : offset+msg_size]
            offset += msg_size

            if msg_type == 100:          # Add Order
                src_t, src_t_ns, sym_idx, sym_seq, order_id, price_int, vol, side_b, firm_b = self.STRUCT_100.unpack(payload)
                symbol = self.symbol_index_map.get(sym_idx, f"SYM_{sym_idx}")
                side = side_b.decode("ascii")
                price_usd = price_int / self.PRICE_DIVISOR
                ts_ns = (src_t * 1000000000) + src_t_ns

                self.active_orders[order_id] = NYSEOrderState(
                    order_id=order_id,
                    symbol=symbol,
                    side=side,
                    volume=vol,
                    price_usd=price_usd,
                    source_time_ns=ts_ns
                )
                self.parsed_messages_count += 1
                parsed_msgs.append(NYSEParsedMessage(
                    msg_type=100, order_id=order_id, symbol=symbol, side=side, volume=vol, price_usd=price_usd
                ))

            elif msg_type == 101:        # Modify Order
                src_t, src_t_ns, sym_idx, sym_seq, order_id, price_int, vol = self.STRUCT_101.unpack(payload)
                symbol = self.symbol_index_map.get(sym_idx, f"SYM_{sym_idx}")
                price_usd = price_int / self.PRICE_DIVISOR

                if order_id in self.active_orders:
                    ord_state = self.active_orders[order_id]
                    ord_state.volume = vol
                    ord_state.price_usd = price_usd

                self.parsed_messages_count += 1
                parsed_msgs.append(NYSEParsedMessage(
                    msg_type=101, order_id=order_id, symbol=symbol, volume=vol, price_usd=price_usd
                ))

            elif msg_type == 102:        # Delete Order
                src_t, src_t_ns, sym_idx, sym_seq, order_id = self.STRUCT_102.unpack(payload)
                symbol = self.symbol_index_map.get(sym_idx, f"SYM_{sym_idx}")

                if order_id in self.active_orders:
                    del self.active_orders[order_id]

                self.parsed_messages_count += 1
                parsed_msgs.append(NYSEParsedMessage(
                    msg_type=102, order_id=order_id, symbol=symbol
                ))

            elif msg_type == 103:        # Execution
                src_t, src_t_ns, sym_idx, sym_seq, order_id, price_int, exec_vol, print_b = self.STRUCT_103.unpack(payload)
                symbol = self.symbol_index_map.get(sym_idx, f"SYM_{sym_idx}")

                if order_id in self.active_orders:
                    ord_state = self.active_orders[order_id]
                    ord_state.volume -= exec_vol
                    if ord_state.volume <= 0:
                        del self.active_orders[order_id]

                self.parsed_messages_count += 1
                parsed_msgs.append(NYSEParsedMessage(
                    msg_type=103, order_id=order_id, symbol=symbol, executed_volume=exec_vol
                ))

        return parsed_msgs

    def generate_report(self, last_msg: Optional[NYSEParsedMessage] = None) -> NYSEFeedReport:
        notes = (
            f"NYSE FEED PARSER SUCCESS: Parsed {self.parsed_messages_count} XDP messages. "
            f"Active L3 Orders in Book = {len(self.active_orders)}."
        )
        logger.info(notes)

        return NYSEFeedReport(
            total_messages_parsed=self.parsed_messages_count,
            active_orders_count=len(self.active_orders),
            last_parsed_message=last_msg,
            status="FEED_PARSER_SUCCESS",
            audit_notes=notes
        )

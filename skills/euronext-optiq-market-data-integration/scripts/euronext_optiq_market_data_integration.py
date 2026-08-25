"""Euronext Optiq Market Data Gateway (MDG) feed-handler primitives.

Wire formats, template IDs and enumerations implemented here follow the
Euronext "Optiq MDG Messages - Interface Specification", version 6.362.3
(9 Feb 2026, SBE template version 362). Section references in the docstrings
below point at that document.

The module deliberately stops where the specification stops being stable: it
parses the fixed 16-byte Market Data Packet Header and the 10-byte message
Frame + SBE header, tracks Packet Sequence Number continuity, maintains an
aggregated (market-by-limit) book, and derives the trading-state gate.
Decoding the variable message blocks and their repeating sections must be
driven by the SBE template XML that Euronext publishes for the segment and SBE
version the client is certified against - those layouts change between template
versions and must not be hard-coded.
"""

import logging
import struct
from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, Iterator, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Wire constants (Optiq MDG spec sections 4.2, 4.3, 6.5) -----------------
PACKET_HEADER_LENGTH = 16       # Packet Time(8) + PSN(4) + Packet Flags(2) + Channel ID(2)
SBE_MESSAGE_HEADER_LENGTH = 10  # Frame(2) + Block Length(2) + Template ID(2) + Schema ID(2) + Schema Version(2)
MAX_PACKET_LENGTH = 1400
MAX_MESSAGE_LENGTH = MAX_PACKET_LENGTH - PACKET_HEADER_LENGTH  # 1384
# Bodies may arrive LZ4 block-compressed (Packet Flags bit 0); spec section 3.4
# caps the extracted packet, which bounds the decompression buffer.
MAX_EXTRACTED_PACKET_LENGTH = 8192

# Null values, spec section 8 "Field Description". A null price is NOT zero.
NULL_PRICE = -(2 ** 63)
NULL_UINT64 = 2 ** 64 - 1
NULL_UINT32 = 2 ** 32 - 1
NULL_UINT16 = 2 ** 16 - 1
NULL_UINT8 = 2 ** 8 - 1

# --- Template IDs (spec section 7, "Application Messages") ------------------
TEMPLATE_MARKET_UPDATE = 1001            # aggregated limits / BBO / short trades
TEMPLATE_ORDER_UPDATE = 1002             # market-by-order (cash central order book)
TEMPLATE_PRICE_UPDATE = 1003
TEMPLATE_FULL_TRADE_INFORMATION = 1004
TEMPLATE_MARKET_STATUS_CHANGE = 1005
TEMPLATE_TIMETABLE = 1006
TEMPLATE_STANDING_DATA = 1007
TEMPLATE_START_OF_DAY = 1101
TEMPLATE_END_OF_DAY = 1102
TEMPLATE_HEALTH_STATUS = 1103
TEMPLATE_START_OF_SNAPSHOT = 2101
TEMPLATE_END_OF_SNAPSHOT = 2102

# Market Data Update Type values used for book maintenance (spec section 7.3.1).
UPDATE_TYPE_BEST_BID = 1
UPDATE_TYPE_BEST_OFFER = 2
UPDATE_TYPE_NEW_BID = 3
UPDATE_TYPE_NEW_OFFER = 4
UPDATE_TYPE_UPDATED_BID = 5
UPDATE_TYPE_UPDATED_OFFER = 6
UPDATE_TYPE_CLEAR_BOOK = 254


class BookState(IntEnum):
    """Market Status Change (1005) "Book State", spec section 8.

    Values are the venue's, not this library's. Continuous is the only state in
    which the matching engine matches incoming orders on arrival.
    """

    INACCESSIBLE = 1
    CLOSED = 2
    CALL = 3
    UNCROSSING = 4
    CONTINUOUS = 5
    HALTED = 6
    CONTINUOUS_UNCROSSING = 7  # Warrants and Certificates only
    SUSPENDED = 8
    RESERVED = 9


class OrderEntryQualifier(IntEnum):
    """Market Status Change (1005) / Timetable (1006) "Order Entry Qualifier"."""

    DISABLED = 0
    ENABLED = 1
    CANCEL_AND_MODIFY_ONLY = 2  # Derivatives only
    CANCEL_ONLY = 3


class Side(IntEnum):
    BID = 1
    ASK = 2


@dataclass(frozen=True)
class MarketDataPacketHeader:
    """The 16-byte Market Data Packet Header (spec section 4.2)."""

    packet_time_ns: int
    packet_sequence_number: int
    packet_flags: int
    channel_id: int

    @property
    def is_compressed(self) -> bool:
        """Packet Flags bit 0. The packet header itself is never compressed."""
        return bool(self.packet_flags & 0b1)

    @property
    def restart_counter(self) -> int:
        """Packet Flags bits 1-3: incremented on each MDG restart within a day."""
        return (self.packet_flags >> 1) & 0b111

    @property
    def psn_high_bits(self) -> int:
        """Packet Flags bits 4-6: high-order bits of a PSN past (2^32)-1."""
        return (self.packet_flags >> 4) & 0b111

    @property
    def effective_sequence_number(self) -> int:
        """The full 35-bit Packet Sequence Number (spec section 4.2)."""
        return (self.psn_high_bits << 32) | self.packet_sequence_number

    @property
    def contains_snapshot_start(self) -> bool:
        return bool(self.packet_flags & (1 << 7))

    @property
    def contains_snapshot_end(self) -> bool:
        return bool(self.packet_flags & (1 << 8))


@dataclass(frozen=True)
class SbeMessageHeader:
    """Frame + SBE header preceding every MDG message (spec sections 4.3, 6.5)."""

    frame_length: int      # total message length, Frame and SBE header included
    block_length: int      # message block, repeating sections excluded
    template_id: int       # the Optiq message type
    schema_id: int
    schema_version: int


@dataclass(frozen=True)
class PacketObservation:
    """Outcome of feeding one packet header to the sequence tracker."""

    header: MarketDataPacketHeader
    is_duplicate: bool
    is_out_of_order: bool
    gap_size: int
    mdg_restart_detected: bool
    book_synchronized: bool


@dataclass
class OrderBookLevel:
    """One aggregated price limit.

    ``price_raw`` is the venue's signed integer price; ``price`` is that value
    divided by 10^(Price/Index Level Decimals) for the instrument.
    """

    price_raw: int
    price: float
    quantity: int
    num_orders: Optional[int] = None


@dataclass
class OptiqMarketDataAuditReport:
    isin: str
    symbol_index: int
    template_id: int
    market_data_sequence_number: int
    event_time_ns: int
    packet_sequence_number: Optional[int]
    book_state: Optional[BookState]
    order_entry_qualifier: Optional[OrderEntryQualifier]
    trading_status: str
    best_bid: Optional[float]
    best_ask: Optional[float]
    mid_price: Optional[float]
    spread: Optional[float]
    book_imbalance_ratio: float          # -1.0 to +1.0, top of book
    is_book_synchronized: bool
    is_crossed: bool
    is_order_entry_allowed: bool
    is_continuous_trading: bool
    is_quoting_allowed: bool
    audit_notes: str


def _validate_decimals(price_decimals: int) -> None:
    if not isinstance(price_decimals, int) or isinstance(price_decimals, bool):
        raise TypeError(f"price_decimals must be int, got {type(price_decimals).__name__}")
    if not 0 <= price_decimals <= 18:
        raise ValueError(f"price_decimals {price_decimals} outside supported range 0..18")


def scale_price(raw_price: int, price_decimals: int) -> Optional[float]:
    """Apply the Optiq price scale (spec section 5.4): price = raw / 10^decimals.

    Returns ``None`` for the null price (-2^63), which Optiq sends for priceless
    orders (Market / Market-to-Limit) and to clear a side of the book. Zero is a
    legitimate price and is never treated as absent.
    """
    if not isinstance(raw_price, int) or isinstance(raw_price, bool):
        raise TypeError(f"raw_price must be int, got {type(raw_price).__name__}")
    _validate_decimals(price_decimals)
    if raw_price == NULL_PRICE:
        return None
    return raw_price / (10 ** price_decimals)


def parse_market_data_packet_header(packet: bytes) -> MarketDataPacketHeader:
    """Parse the 16-byte Market Data Packet Header (spec section 4.2).

    Field order on the wire, little-endian:
        uint64 Packet Time (nanoseconds since 1970-01-01 UTC)
        uint32 Packet Sequence Number
        uint16 Packet Flags
        uint16 Channel ID
    """
    if not isinstance(packet, (bytes, bytearray, memoryview)):
        raise TypeError(f"packet must be bytes-like, got {type(packet).__name__}")
    if len(packet) < PACKET_HEADER_LENGTH:
        raise ValueError(
            f"Optiq packet length {len(packet)} < {PACKET_HEADER_LENGTH} byte packet header")

    packet_time_ns, psn, flags, channel_id = struct.unpack_from("<QIHH", packet, 0)
    return MarketDataPacketHeader(
        packet_time_ns=packet_time_ns,
        packet_sequence_number=psn,
        packet_flags=flags,
        channel_id=channel_id,
    )


def parse_sbe_message_header(buffer: bytes, offset: int = 0) -> SbeMessageHeader:
    """Parse Frame + SBE header at ``offset`` (spec sections 4.3, 6.5).

    Little-endian: uint16 Frame, uint16 Block Length, uint16 Template ID,
    uint16 Schema ID, uint16 Schema Version. Frame carries the total message
    length including these 10 bytes.
    """
    if offset < 0:
        raise ValueError(f"offset {offset} is negative")
    if len(buffer) - offset < SBE_MESSAGE_HEADER_LENGTH:
        raise ValueError(
            f"{len(buffer) - offset} bytes at offset {offset} < "
            f"{SBE_MESSAGE_HEADER_LENGTH} byte message header")

    frame, block_length, template_id, schema_id, schema_version = struct.unpack_from(
        "<HHHHH", buffer, offset)
    if frame < SBE_MESSAGE_HEADER_LENGTH:
        raise ValueError(f"Frame {frame} shorter than the {SBE_MESSAGE_HEADER_LENGTH} byte header")
    if frame > MAX_MESSAGE_LENGTH:
        raise ValueError(f"Frame {frame} exceeds the {MAX_MESSAGE_LENGTH} byte message maximum")
    if SBE_MESSAGE_HEADER_LENGTH + block_length > frame:
        raise ValueError(
            f"Block Length {block_length} does not fit inside Frame {frame}: message is corrupted")
    return SbeMessageHeader(
        frame_length=frame,
        block_length=block_length,
        template_id=template_id,
        schema_id=schema_id,
        schema_version=schema_version,
    )


def iter_sbe_messages(body: bytes) -> Iterator[Tuple[SbeMessageHeader, bytes]]:
    """Walk the packet body, yielding (header, payload) per message.

    ``body`` is the packet with the 16-byte packet header removed, already
    decompressed if Packet Flags bit 0 was set. Per spec section 4.2 the sum of
    the Frame fields must equal the body length; a message that overruns the
    body means the packet is corrupted, and this raises rather than yielding a
    truncated message.
    """
    offset = 0
    total = len(body)
    while offset < total:
        header = parse_sbe_message_header(body, offset)
        end = offset + header.frame_length
        if end > total:
            raise ValueError(
                f"Message at offset {offset} declares Frame {header.frame_length} "
                f"but only {total - offset} bytes remain: packet is corrupted")
        yield header, bytes(body[offset + SBE_MESSAGE_HEADER_LENGTH:end])
        offset = end


class EuronextOptiqMarketDataEngine:
    """Aggregated-limit book, PSN continuity tracking and trading-state gate.

    One instance tracks one instrument (Symbol Index) on one channel. Limits are
    keyed by the venue's integer price so that they are matched exactly; float
    prices are never used as dictionary keys.

    ``price_decimals`` is the instrument's Price/Index Level Decimals from
    Standing Data (1007) and has no default: guessing it silently mis-scales
    every price the engine reports.
    """

    def __init__(self, price_decimals: int) -> None:
        _validate_decimals(price_decimals)
        self.price_decimals: int = price_decimals
        self._scale: int = 10 ** price_decimals
        self.bids: Dict[int, OrderBookLevel] = {}
        self.asks: Dict[int, OrderBookLevel] = {}
        self.book_state: Optional[BookState] = None
        self.order_entry_qualifier: Optional[OrderEntryQualifier] = None
        self.book_synchronized: bool = False
        self.last_packet_sequence_number: Optional[int] = None
        self.last_restart_counter: Optional[int] = None
        self.last_market_data_sequence_number: Optional[int] = None

    # --- Feed continuity ---------------------------------------------------

    def observe_packet(self, header: MarketDataPacketHeader) -> PacketObservation:
        """Track PSN continuity for one channel (spec sections 3.6, 3.7).

        Gap detection uses the Packet Sequence Number only. The Market Data
        Sequence Number carried inside messages increments unevenly on a single
        channel and must never be used for this.

        A detected gap or an MDG restart marks the book unsynchronized: the
        caller must recover the missing packet from the B line, or resynchronize
        from the snapshot channel, then call :meth:`mark_book_synchronized`.

        Feed this the arbitrated stream for one channel: a copy already seen on
        the other line then arrives as a duplicate. The gap verdict is immediate,
        with no reordering window - because UDP can deliver out of order, an
        arbitrator should hold a packet that arrives ahead of the sequence for a
        bounded interval before treating the missing PSNs as lost.
        """
        if not isinstance(header, MarketDataPacketHeader):
            raise TypeError("header must be a MarketDataPacketHeader")

        psn = header.effective_sequence_number
        restart = header.restart_counter
        restart_detected = (
            self.last_restart_counter is not None and restart != self.last_restart_counter)

        is_duplicate = False
        is_out_of_order = False
        gap_size = 0

        if restart_detected:
            # MDG restarted: the PSN restarts at 1 and a book retransmission follows.
            self._desynchronize("MDG restart detected (Packet Flags restart counter changed)")
            self.last_packet_sequence_number = psn
        elif self.last_packet_sequence_number is None:
            self.last_packet_sequence_number = psn
        elif psn == self.last_packet_sequence_number:
            is_duplicate = True
        elif psn < self.last_packet_sequence_number:
            # UDP may reorder; an earlier PSN is a late arrival, not a new gap.
            is_out_of_order = True
        else:
            gap_size = psn - self.last_packet_sequence_number - 1
            self.last_packet_sequence_number = psn
            if gap_size > 0:
                self._desynchronize(
                    f"PSN gap of {gap_size} packet(s) on channel {header.channel_id}")

        self.last_restart_counter = restart
        return PacketObservation(
            header=header,
            is_duplicate=is_duplicate,
            is_out_of_order=is_out_of_order,
            gap_size=gap_size,
            mdg_restart_detected=restart_detected,
            book_synchronized=self.book_synchronized,
        )

    def mark_book_synchronized(self) -> None:
        """Declare the book usable after a snapshot or book retransmission."""
        self.book_synchronized = True

    def _desynchronize(self, reason: str) -> None:
        self.book_synchronized = False
        logger.warning("OPTIQ BOOK DESYNCHRONIZED: %s. Quoting must stop until resync.", reason)

    # --- Book maintenance --------------------------------------------------

    def apply_limit_update(
        self,
        side: Side,
        price_raw: int,
        quantity: int,
        num_orders: Optional[int] = None,
    ) -> None:
        """Apply one Market Update (1001) aggregated-limit update (spec 6.12).

        A quantity of 0 deletes the limit at ``price_raw``; that is how Optiq
        signals a limit deletion. A null price (-2^63) carries no limit - it is
        used for priceless orders and to clear a side - and is ignored here.
        """
        side = Side(side)
        if not isinstance(price_raw, int) or isinstance(price_raw, bool):
            raise TypeError(f"price_raw must be int, got {type(price_raw).__name__}")
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise TypeError(f"quantity must be int, got {type(quantity).__name__}")
        if quantity < 0:
            raise ValueError(f"quantity {quantity} is negative; Optiq quantities are unsigned")
        if num_orders is not None and num_orders < 0:
            raise ValueError(f"num_orders {num_orders} is negative")

        book = self.bids if side is Side.BID else self.asks
        if price_raw == NULL_PRICE:
            logger.debug("Ignoring null-price %s update (priceless order or side clear)", side.name)
            return
        if quantity == 0:
            book.pop(price_raw, None)
            return
        book[price_raw] = OrderBookLevel(
            price_raw=price_raw,
            price=price_raw / self._scale,
            quantity=quantity,
            num_orders=num_orders,
        )

    def update_book_level(
        self,
        side: str,
        price: float,
        quantity: int,
        num_orders: Optional[int] = None,
    ) -> None:
        """Convenience wrapper taking a decimal price instead of the raw integer.

        ``side`` is "BUY"/"BID" or "SELL"/"ASK"/"OFFER". The price is converted
        to the venue's integer representation using ``price_decimals`` so that
        limits are still keyed exactly. Prefer :meth:`apply_limit_update` on the
        decode path - it consumes the wire value without a float round trip.
        """
        normalized = side.strip().upper()
        if normalized in ("BUY", "BID", "B"):
            resolved = Side.BID
        elif normalized in ("SELL", "ASK", "OFFER", "S"):
            resolved = Side.ASK
        else:
            raise ValueError(f"Unknown side {side!r}; expected BUY/BID or SELL/ASK/OFFER")
        self.apply_limit_update(resolved, round(price * self._scale), quantity, num_orders)

    def clear_book(self) -> None:
        """Handle Market Data Update Type 254 (Clear Book) - drop every limit."""
        self.bids.clear()
        self.asks.clear()

    # --- Trading state -----------------------------------------------------

    def apply_market_status_change(
        self,
        book_state: Optional[int] = None,
        order_entry_qualifier: Optional[int] = None,
    ) -> None:
        """Apply a Market Status Change (1005) (spec section 7.3.5).

        Both fields are optional on the wire and are left unchanged when absent
        or null. An unrecognised value raises rather than being coerced, because
        a silently mis-mapped state would gate quoting on a guess.
        """
        if book_state is not None and book_state != NULL_UINT8:
            self.book_state = BookState(book_state)
        if order_entry_qualifier is not None and order_entry_qualifier != NULL_UINT8:
            self.order_entry_qualifier = OrderEntryQualifier(order_entry_qualifier)

    @property
    def is_continuous_trading(self) -> bool:
        return self.book_state is BookState.CONTINUOUS

    @property
    def is_order_entry_allowed(self) -> bool:
        """True only when the venue reports Order Entry/Cancel/Modify Enabled."""
        return self.order_entry_qualifier is OrderEntryQualifier.ENABLED

    # --- Book reads --------------------------------------------------------

    def best_bid_level(self) -> Optional[OrderBookLevel]:
        return self.bids[max(self.bids)] if self.bids else None

    def best_ask_level(self) -> Optional[OrderBookLevel]:
        return self.asks[min(self.asks)] if self.asks else None

    def depth(self, side: Side, levels: int) -> List[OrderBookLevel]:
        """Return up to ``levels`` limits from the top of ``side``."""
        if levels < 0:
            raise ValueError(f"levels {levels} is negative")
        side = Side(side)
        book = self.bids if side is Side.BID else self.asks
        keys = sorted(book, reverse=side is Side.BID)
        return [book[key] for key in keys[:levels]]

    # --- Audit -------------------------------------------------------------

    def process_optiq_message(
        self,
        isin: str,
        symbol_index: int,
        template_id: int,
        market_data_sequence_number: int,
        event_time_ns: int,
    ) -> OptiqMarketDataAuditReport:
        """Emit the audit report for the current book and trading state.

        Quoting is allowed only when all of the following hold: the book is
        synchronized, Book State is Continuous, Order Entry is Enabled, and the
        book is not crossed. That conjunction is this library's conservative
        default policy, not a Euronext rule - each component is reported
        separately so a caller can apply its own policy.
        """
        if market_data_sequence_number < 0:
            raise ValueError(
                f"market_data_sequence_number {market_data_sequence_number} is negative")
        if event_time_ns < 0:
            raise ValueError(f"event_time_ns {event_time_ns} is negative")
        # Recorded only: the MDSN increments unevenly on a single channel
        # (spec 5.3.2) and is never used for gap detection.
        self.last_market_data_sequence_number = market_data_sequence_number

        bid_level = self.best_bid_level()
        ask_level = self.best_ask_level()

        mid_price: Optional[float] = None
        spread: Optional[float] = None
        is_crossed = False
        if bid_level is not None and ask_level is not None:
            # Derived from the integer prices, so no float summation error.
            mid_price = (bid_level.price_raw + ask_level.price_raw) / (2 * self._scale)
            spread = (ask_level.price_raw - bid_level.price_raw) / self._scale
            is_crossed = bid_level.price_raw >= ask_level.price_raw

        bid_vol = bid_level.quantity if bid_level is not None else 0
        ask_vol = ask_level.quantity if ask_level is not None else 0
        total_vol = bid_vol + ask_vol
        imbalance = round((bid_vol - ask_vol) / total_vol, 4) if total_vol > 0 else 0.0

        continuous = self.is_continuous_trading
        order_entry = self.is_order_entry_allowed
        # A crossed book is normal while orders accumulate in a Call phase; it is
        # an anomaly during Continuous trading, and quoting is blocked there.
        quoting_allowed = (
            self.book_synchronized and continuous and order_entry and not is_crossed)

        status = self.book_state.name if self.book_state is not None else "UNKNOWN"
        if quoting_allowed:
            notes = (
                f"OPTIQ BOOK OK [symbol_index={symbol_index}]: "
                f"mid={mid_price}, spread={spread}, imbalance={imbalance:+.4f}.")
            logger.info(notes)
        else:
            reasons = []
            if not self.book_synchronized:
                reasons.append("book not synchronized (gap, restart, or no snapshot applied)")
            if not continuous:
                reasons.append(f"book state {status}")
            if not order_entry:
                qualifier = (
                    self.order_entry_qualifier.name
                    if self.order_entry_qualifier is not None else "UNKNOWN")
                reasons.append(f"order entry {qualifier}")
            if is_crossed:
                reasons.append("book crossed")
            notes = (
                f"OPTIQ QUOTING DISABLED [symbol_index={symbol_index}]: "
                + "; ".join(reasons) + ".")
            logger.warning(notes)

        return OptiqMarketDataAuditReport(
            isin=isin,
            symbol_index=symbol_index,
            template_id=template_id,
            market_data_sequence_number=market_data_sequence_number,
            event_time_ns=event_time_ns,
            packet_sequence_number=self.last_packet_sequence_number,
            book_state=self.book_state,
            order_entry_qualifier=self.order_entry_qualifier,
            trading_status=status,
            best_bid=bid_level.price if bid_level is not None else None,
            best_ask=ask_level.price if ask_level is not None else None,
            mid_price=mid_price,
            spread=spread,
            book_imbalance_ratio=imbalance,
            is_book_synchronized=self.book_synchronized,
            is_crossed=is_crossed,
            is_order_entry_allowed=order_entry,
            is_continuous_trading=continuous,
            is_quoting_allowed=quoting_allowed,
            audit_notes=notes,
        )

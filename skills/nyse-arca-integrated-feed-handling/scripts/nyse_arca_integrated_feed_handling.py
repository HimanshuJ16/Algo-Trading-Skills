"""
nyse-arca-integrated-feed-handling: NYSE Arca Integrated Feed (XDP/Pillar) binary
packet decoder and L3 order-book state machine.

Purpose
-------
Decode raw NYSE Arca Integrated Feed datagrams into typed messages and maintain a
per-order (L3) book keyed by matching-engine ``OrderID``, while keeping the audit
trail honest about *when the book stopped being trustworthy*. A feed handler that
silently keeps parsing after a sequence gap is worse than one that stops: the book
it publishes looks authoritative and is wrong.

Wire format pinned by this module
---------------------------------
Layouts below are transcribed from:

* **Pillar Integrated Feed - Client Specification v2.5, May 16, 2022** - the
  document currently linked from nyse.com for the Integrated Feed; its title page
  lists NYSE ARCA INTEGRATED FEED among the covered markets.
* **Pillar Equities Common Client Specification v2.4k, July 25, 2024** - packet
  header, message header, sequence numbers, price scaling, control messages.

``SPEC_VERSION`` records the pin. **These layouts are version-specific and must be
re-verified against the spec revision your venue actually publishes.** Two concrete
precedents, both from primary sources:

* Msg Type 100 on the pre-Pillar *XDP Integrated Feed Client Specification
  v1.16b* (NYSE Arca, 28 Jul 2016) is 31 bytes with a **4-byte** OrderID and
  ``OrderIDGTCIndicator``/``TradeSession`` trailing fields. On v2.5 it is 39 bytes
  with an **8-byte** OrderID and ``Side``/``FirmID``/``Reserved``.
* Msg Type 101 kept MsgSize 35 from v2.4a to v2.5, but offset 33 changed from
  ``Reserved 1`` to ``Side``. Same length, different meaning.

So message length alone does not identify a layout, and a decoder that assumes one
fixed layout per MsgType will mis-read a venue running a different revision.

Byte order and numeric types
----------------------------
All binary fields are little-endian (Common v2.4k section 3, "Binary fields are
published in Little-Endian ordering"). Price fields are **signed** 32-bit integers
(Common v2.4k section 3.5, "All 'price' fields are published as signed binary
integers. Pillar Equities will not publish negative prices."). ``OrderID`` and
``TradeID`` are unsigned (Common v2.4k section 3.6). The spec does not state a
signedness for ``Volume``; it is decoded here as unsigned, which is safe for a share
count but is an assumption, not a quoted guarantee.

Price scaling - do not hard-code a divisor
------------------------------------------
Common v2.4k section 3.5 defines ``price = Numerator / 10**PriceScaleCode``, where
``PriceScaleCode`` is published **per symbol** in the Symbol Index Mapping message
(Msg Type 3). It is not a feed-wide constant: section 3.5.1 documents live Pillar
Equities symbols at scale codes 3, 4 **and** 6. Applying a fixed ``/10000`` divisor
to a scale-6 symbol overstates its price by 100x, and to a scale-3 symbol
understates it by 10x - silently, with no parse error.

This engine therefore resolves the scale code per SymbolIndex, from (a) Symbol Index
Mapping messages seen on the wire, or (b) codes supplied by the caller. When neither
is available it falls back to ``default_price_scale_code`` (4), counts the event in
``prices_scaled_with_fallback``, and logs it. **4 is this engine's fallback, not a
spec default** - NYSE publishes no feed-wide default.

Timestamps
----------
Msg Types 100/101/102/103/104 carry only ``SourceTimeNS`` - a nanosecond offset
within the current second. The seconds component is published separately, once a
second per matching-engine partition, in the Source Time Reference message (Msg
Type 2) (Common v2.4k section 3.2). A full timestamp is therefore only available
after a Msg Type 2 has been seen on that channel; until then ``source_time_ns`` is
``None`` and only ``source_time_ns_offset`` is populated. Synthesising a timestamp
from the nanosecond field alone produces a value in 1970 and is never done here.

Scope - what this module deliberately does not do
-------------------------------------------------
Transport (UDP multicast joins, A/B line arbitration), the Pillar Request Server
(retransmission and refresh *requests*), and the non-book message types - Imbalance
(105), Non-Displayed Trade (110), Cross Trade (111), Trade Cancel (112), Cross
Correction (113), Retail Price Improvement (114), Stock Summary (223), Security
Status (34) - are out of scope. Unhandled types are counted and skipped by MsgSize,
never guessed at. For gap *recovery* policy see
``sequence-number-gap-detection-for-feeds``.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SPEC_VERSION = (
    "Pillar Integrated Feed v2.5 (2022-05-16) / Pillar Equities Common v2.4k (2024-07-25)"
)


class XDPProtocolError(ValueError):
    """Raised when a datagram cannot be interpreted as an XDP packet at all.

    Subclasses ``ValueError`` so callers written against the previous version of
    this module, which raised a bare ``ValueError`` for short packets, keep working.
    """


# --------------------------------------------------------------------------------------
# Message types (Integrated Feed v2.5 section 1.3; Common v2.4k section 4)
# --------------------------------------------------------------------------------------
MSG_SEQUENCE_NUMBER_RESET = 1
MSG_SOURCE_TIME_REFERENCE = 2
MSG_SYMBOL_INDEX_MAPPING = 3
MSG_SYMBOL_CLEAR = 32
MSG_ADD_ORDER = 100
MSG_MODIFY_ORDER = 101
MSG_DELETE_ORDER = 102
MSG_ORDER_EXECUTION = 103
MSG_REPLACE_ORDER = 104
MSG_ADD_ORDER_REFRESH = 106

# --------------------------------------------------------------------------------------
# DeliveryFlag values (Common v2.4k section 2.1.1)
# --------------------------------------------------------------------------------------
DELIVERY_HEARTBEAT = 1
DELIVERY_FAILOVER = 10
DELIVERY_ORIGINAL = 11
DELIVERY_SEQ_RESET = 12
DELIVERY_RETRANSMISSION_ONLY = 13
DELIVERY_RETRANSMISSION_PART = 15
DELIVERY_REFRESH_ONLY = 17
DELIVERY_REFRESH_START = 18
DELIVERY_REFRESH_PART = 19
DELIVERY_REFRESH_END = 20
DELIVERY_MESSAGE_UNAVAILABLE = 21

#: Flags whose packets carry their own numbering (retransmission / refresh channels)
#: and must not be measured against the real-time channel's expected sequence number.
_OUT_OF_BAND_DELIVERY_FLAGS = frozenset(
    {
        DELIVERY_RETRANSMISSION_ONLY,
        DELIVERY_RETRANSMISSION_PART,
        DELIVERY_REFRESH_ONLY,
        DELIVERY_REFRESH_START,
        DELIVERY_REFRESH_PART,
        DELIVERY_REFRESH_END,
    }
)

#: Common v2.4k section 2.1: "The maximum length of a packet is 1400 bytes".
MAX_PACKET_BYTES = 1400
PACKET_HEADER_BYTES = 16
MESSAGE_HEADER_BYTES = 4


@dataclass
class NYSEOrderState:
    """A single resting order on the L3 book."""

    order_id: int
    symbol: str
    side: str  # 'B' or 'S'
    volume: int
    price_usd: float
    #: Full epoch-nanosecond timestamp, or ``None`` when no Source Time Reference
    #: (Msg Type 2) has been seen yet on this channel. See module docstring.
    source_time_ns: Optional[int] = None
    #: Raw ``SourceTimeNS`` field: nanosecond offset within the reference second.
    source_time_ns_offset: int = 0
    symbol_index: int = 0
    firm_id: str = ""
    #: True once a Modify with ``PositionChange == 1`` moved this order to the back
    #: of its price level (Integrated Feed v2.5 section 3). Queue-position models must
    #: not keep using the original arrival time after this flips.
    lost_queue_position: bool = False


@dataclass
class NYSEParsedMessage:
    """A decoded feed message. Fields not carried by the message type stay ``None``."""

    msg_type: int
    order_id: int
    symbol: str
    side: Optional[str] = None
    volume: Optional[int] = None
    price_usd: Optional[float] = None
    executed_volume: Optional[int] = None
    symbol_index: Optional[int] = None
    symbol_seq_num: Optional[int] = None
    trade_id: Optional[int] = None
    new_order_id: Optional[int] = None
    printable: Optional[bool] = None
    position_change: Optional[int] = None
    source_time_ns: Optional[int] = None
    source_time_ns_offset: Optional[int] = None
    #: Sequence number this message occupies on its channel, derived from the packet
    #: header's ``SeqNum`` plus the message's index within the packet (Common v2.4k
    #: section 3.3). ``None`` for refresh/retransmission traffic.
    seq_num: Optional[int] = None
    #: True when the message arrived on a refresh/retransmission delivery flag.
    is_refresh: bool = False


@dataclass
class SequenceGap:
    """A detected break in the real-time channel's message sequence."""

    expected_seq: int
    received_seq: int
    #: Negative when the packet replays sequence numbers already consumed.
    missing_count: int


@dataclass
class NYSEFeedReport:
    """Audit summary. ``status`` is ``FEED_PARSER_DEGRADED`` whenever the book is stale."""

    total_messages_parsed: int
    active_orders_count: int
    last_parsed_message: Optional[NYSEParsedMessage]
    status: str  # 'FEED_PARSER_SUCCESS' | 'FEED_PARSER_DEGRADED'
    audit_notes: str
    sequence_gaps: List[SequenceGap] = field(default_factory=list)
    book_is_stale: bool = False
    messages_skipped: int = 0
    book_desync_events: int = 0
    prices_scaled_with_fallback: int = 0
    unhandled_message_types: Dict[int, int] = field(default_factory=dict)
    spec_version: str = SPEC_VERSION


class NYSEArcaIntegratedFeedEngine:
    """Decode NYSE Arca Integrated Feed packets and maintain L3 order-book state.

    The decoder is deliberately *prefix-based*: for each message it reads only the
    fields defined by the pinned spec version and then advances by the wire
    ``MsgSize``, exactly as Common v2.4k section 3.1.1 instructs ("clients should
    never hard code msg sizes in feed handlers... use the Msg Size field to determine
    where the next message in a packet begins"). A venue publishing a longer variant
    with extra trailing fields is decoded correctly; a variant *shorter* than the
    pinned layout is skipped and counted rather than mis-read.

    Args:
        symbol_index_map: Optional seed of ``SymbolIndex -> ticker``. Symbol Index
            Mapping messages (Msg Type 3) seen on the wire update this in place.
        price_scale_codes: Optional seed of ``SymbolIndex -> PriceScaleCode``.
            Also learned from Msg Type 3.
        default_price_scale_code: Fallback exponent used only when a symbol's code
            is unknown. Defaults to 4. **Not a spec default** - NYSE publishes none.
        strict: When True, a malformed message raises ``XDPProtocolError`` instead
            of being skipped and counted. Use for offline replay validation; leave
            False for live consumption, where one bad message must not kill the
            handler.

    Not thread-safe: all mutating state lives on the instance. Feed one channel per
    engine - sequence numbers are per-channel (Common v2.4k section 3.3).
    """

    # ---- Packet header, 16 bytes (Common v2.4k section 2.1.1) ------------------------
    # PktSize(H) DeliveryFlag(B) NumberMsgs(B) SeqNum(I) SendTime(I) SendTimeNS(I)
    PKT_HEADER = struct.Struct("<HBBIII")

    # ---- Message header, 4 bytes (Common v2.4k 3.1): MsgSize(H) MsgType(H) -----------
    MSG_HEADER = struct.Struct("<HH")

    # ---- Payload layouts, offsets relative to end of the 4-byte message header -------
    # Msg 100 Add Order, MsgSize 39 -> payload 35 (Integrated Feed v2.5 section 2)
    # SourceTimeNS SymbolIndex SymbolSeqNum OrderID Price Volume Side FirmID Reserved1
    STRUCT_100 = struct.Struct("<IIIQiI1s5sB")

    # Msg 101 Modify Order, MsgSize 35 -> payload 31 (v2.5 section 3)
    # ... OrderID Price Volume PositionChange Side Reserved2
    STRUCT_101 = struct.Struct("<IIIQiIB1sB")

    # Msg 102 Delete Order, MsgSize 25 -> payload 21 (v2.5 section 4)
    # ... OrderID Reserved1
    STRUCT_102 = struct.Struct("<IIIQB")

    # Msg 103 Order Execution, MsgSize 42 -> payload 38 (v2.5 section 5)
    # ... OrderID TradeID Price Volume PrintableFlag Reserved1 TradeCond1..4
    STRUCT_103 = struct.Struct("<IIIQIiIBB1s1s1s1s")

    # Msg 104 Replace Order, MsgSize 42 -> payload 38 (v2.5 section 6)
    # ... OrderID NewOrderID Price Volume Side Reserved2
    STRUCT_104 = struct.Struct("<IIIQQiI1sB")

    # Msg 106 Add Order Refresh, MsgSize 43 -> payload 39 (v2.5 section 8).
    # Note the extra leading SourceTime: refresh messages carry full seconds.
    STRUCT_106 = struct.Struct("<IIIIQiI1s5sB")

    # Msg 3 Symbol Index Mapping, MsgSize 44 (Common v2.4k section 4.3). Only the
    # leading 21 bytes are decoded; trailing referential fields are not used here.
    # SymbolIndex Symbol(11) Reserved MarketID SystemID ExchangeCode PriceScaleCode
    STRUCT_003_PREFIX = struct.Struct("<I11sBHB1sB")

    # Msg 32 Symbol Clear, MsgSize 20 -> payload 16 (Common v2.4k section 4.4)
    STRUCT_032 = struct.Struct("<IIII")

    # Msg 2 Source Time Reference, MsgSize 16 -> payload 12 (Common v2.4k section 4.2)
    STRUCT_002 = struct.Struct("<III")

    # Msg 1 Sequence Number Reset, MsgSize 14 -> payload 10 (Common v2.4k section 4.1)
    STRUCT_001 = struct.Struct("<IIBB")

    def __init__(
        self,
        symbol_index_map: Optional[Dict[int, str]] = None,
        price_scale_codes: Optional[Dict[int, int]] = None,
        default_price_scale_code: int = 4,
        strict: bool = False,
    ) -> None:
        if not 0 <= default_price_scale_code <= 9:
            raise ValueError(
                f"default_price_scale_code must be in 0..9, got {default_price_scale_code}"
            )

        self.symbol_index_map: Dict[int, str] = dict(symbol_index_map or {})
        self.price_scale_codes: Dict[int, int] = dict(price_scale_codes or {})
        self.default_price_scale_code = default_price_scale_code
        self.strict = strict

        self.active_orders: Dict[int, NYSEOrderState] = {}
        self.parsed_messages_count: int = 0

        # Feed-integrity state
        self.next_expected_seq: Optional[int] = None
        self.sequence_gaps: List[SequenceGap] = []
        self.book_is_stale: bool = False
        self.messages_skipped: int = 0
        self.book_desync_events: int = 0
        self.prices_scaled_with_fallback: int = 0
        self.unhandled_message_types: Dict[int, int] = {}

        #: Seconds component from the most recent Source Time Reference (Msg Type 2).
        self.source_time_seconds: Optional[int] = None

    # ---------------------------------------------------------------------------------
    # Helpers
    # ---------------------------------------------------------------------------------
    def _symbol_for(self, symbol_index: int) -> str:
        return self.symbol_index_map.get(symbol_index, f"SYM_{symbol_index}")

    def _scale_price(self, raw_price: int, symbol_index: int) -> float:
        """Apply the symbol's PriceScaleCode (Common v2.4k section 3.5)."""
        code = self.price_scale_codes.get(symbol_index)
        if code is None:
            code = self.default_price_scale_code
            self.prices_scaled_with_fallback += 1
            logger.warning(
                "No PriceScaleCode for SymbolIndex %d; falling back to 10^%d. Ingest the "
                "Symbol Index Mapping (Msg Type 3) spin before decoding prices.",
                symbol_index,
                code,
            )
        return raw_price / (10.0**code)

    def _resolve_timestamp(self, source_time_ns_offset: int) -> Optional[int]:
        """Combine the reference second with a message's nanosecond offset."""
        if self.source_time_seconds is None:
            return None
        return self.source_time_seconds * 1_000_000_000 + source_time_ns_offset

    @staticmethod
    def _decode_side(raw: bytes) -> Optional[str]:
        """Decode a 1-byte ASCII Side field, returning ``None`` if it is not 'B'/'S'."""
        side = raw.decode("ascii", errors="replace")
        return side if side in ("B", "S") else None

    def _reject(self, reason: str, *args: object) -> None:
        """Record a message the decoder refused to trust."""
        self.messages_skipped += 1
        if self.strict:
            raise XDPProtocolError(reason % args if args else reason)
        logger.warning(reason, *args)

    def _flag_desync(self, reason: str, *args: object) -> None:
        """Record evidence that book state no longer matches the exchange's."""
        self.book_desync_events += 1
        self.book_is_stale = True
        logger.warning(reason, *args)

    # ---------------------------------------------------------------------------------
    # Sequence handling
    # ---------------------------------------------------------------------------------
    def _check_sequence(self, delivery_flag: int, seq_num: int, num_msgs: int) -> None:
        """Track the real-time channel sequence and record gaps (Common v2.4k 3.3).

        Heartbeats carry no messages and do not advance the sequence (section 2.2).
        Sequence Number Reset and failover packets restart numbering at 1 (sections 4.1,
        9). Refresh and retransmission packets are numbered on their own channels and
        are excluded from this check.
        """
        if delivery_flag == DELIVERY_HEARTBEAT:
            return

        if delivery_flag in (DELIVERY_SEQ_RESET, DELIVERY_FAILOVER):
            logger.info(
                "Sequence reset / failover packet (DeliveryFlag=%d); resetting expected "
                "sequence to %d.",
                delivery_flag,
                seq_num,
            )
            self.next_expected_seq = seq_num + num_msgs
            return

        if delivery_flag == DELIVERY_MESSAGE_UNAVAILABLE:
            self._flag_desync(
                "DeliveryFlag=21 (Message Unavailable): requested retransmission cannot be "
                "served. Book state is unrecoverable without a refresh."
            )
            return

        if delivery_flag in _OUT_OF_BAND_DELIVERY_FLAGS:
            return

        if delivery_flag != DELIVERY_ORIGINAL:
            # An undocumented flag. Keep gap detection running rather than silently
            # dropping it - a new real-time flag must not disable the integrity check -
            # but say so, because the branch above may now be incomplete.
            logger.warning(
                "Unrecognised DeliveryFlag %d; treating the packet as real-time traffic "
                "for gap detection. Re-check the spec revision against %s.",
                delivery_flag,
                SPEC_VERSION,
            )

        if self.next_expected_seq is None:
            self.next_expected_seq = seq_num + num_msgs
            return

        if seq_num != self.next_expected_seq:
            gap = SequenceGap(
                expected_seq=self.next_expected_seq,
                received_seq=seq_num,
                missing_count=seq_num - self.next_expected_seq,
            )
            self.sequence_gaps.append(gap)
            self._flag_desync(
                "Sequence %s on channel: expected %d, received %d (delta %d). L3 book state "
                "is no longer authoritative until a refresh completes.",
                "gap" if gap.missing_count > 0 else "regression/duplicate",
                gap.expected_seq,
                gap.received_seq,
                gap.missing_count,
            )

        self.next_expected_seq = seq_num + num_msgs

    # ---------------------------------------------------------------------------------
    # Packet parsing
    # ---------------------------------------------------------------------------------
    def parse_xdp_packet(self, packet_bytes: bytes) -> List[NYSEParsedMessage]:
        """Parse one XDP datagram and apply its messages to the L3 book.

        Args:
            packet_bytes: A complete datagram, starting at the 16-byte packet header.

        Returns:
            The messages successfully decoded from this packet, in wire order.
            Control messages (1, 2, 3, 32) update engine state and are not returned.

        Raises:
            XDPProtocolError: If the datagram is too short to contain a packet header,
                or - in ``strict`` mode only - if any message inside it is malformed.
        """
        if not isinstance(packet_bytes, (bytes, bytearray, memoryview)):
            raise XDPProtocolError(
                f"packet_bytes must be a bytes-like object, got {type(packet_bytes).__name__}"
            )

        packet_bytes = bytes(packet_bytes)
        if len(packet_bytes) < PACKET_HEADER_BYTES:
            raise XDPProtocolError(
                f"Datagram of {len(packet_bytes)} bytes is shorter than the "
                f"{PACKET_HEADER_BYTES}-byte XDP packet header."
            )

        (
            pkt_size,
            delivery_flag,
            num_msgs,
            seq_num,
            _send_time,
            _send_time_ns,
        ) = self.PKT_HEADER.unpack_from(packet_bytes, 0)

        if pkt_size > MAX_PACKET_BYTES:
            logger.warning(
                "PktSize %d exceeds the %d-byte XDP maximum; datagram may be corrupt.",
                pkt_size,
                MAX_PACKET_BYTES,
            )

        # PktSize includes the header (Common v2.4k section 2.1.1). Trust the smaller of
        # the declared and actual lengths so a truncated datagram cannot over-read and a
        # padded one cannot drag trailing garbage into the message loop.
        end = len(packet_bytes)
        if pkt_size != len(packet_bytes):
            logger.warning(
                "PktSize %d does not match datagram length %d.",
                pkt_size,
                len(packet_bytes),
            )
            if PACKET_HEADER_BYTES <= pkt_size < len(packet_bytes):
                end = pkt_size

        self._check_sequence(delivery_flag, seq_num, num_msgs)

        is_refresh = delivery_flag in _OUT_OF_BAND_DELIVERY_FLAGS
        parsed_msgs: List[NYSEParsedMessage] = []
        offset = PACKET_HEADER_BYTES

        for msg_index in range(num_msgs):
            if offset + MESSAGE_HEADER_BYTES > end:
                self._reject(
                    "Packet truncated: %d of %d messages decoded before running out of bytes.",
                    msg_index,
                    num_msgs,
                )
                break

            msg_size, msg_type = self.MSG_HEADER.unpack_from(packet_bytes, offset)

            # A MsgSize that does not at least cover its own header cannot advance the
            # cursor; continuing would re-read the same bytes for every remaining message.
            if msg_size < MESSAGE_HEADER_BYTES:
                self._reject(
                    "MsgSize %d for MsgType %d is smaller than the %d-byte message header; "
                    "cannot locate the next message. Abandoning packet.",
                    msg_size,
                    msg_type,
                    MESSAGE_HEADER_BYTES,
                )
                break

            if offset + msg_size > end:
                self._reject(
                    "MsgType %d declares MsgSize %d but only %d bytes remain in the packet.",
                    msg_type,
                    msg_size,
                    end - offset,
                )
                break

            payload_offset = offset + MESSAGE_HEADER_BYTES
            payload_len = msg_size - MESSAGE_HEADER_BYTES
            msg_seq_num = None if is_refresh else seq_num + msg_index

            try:
                parsed = self._dispatch(
                    msg_type,
                    packet_bytes,
                    payload_offset,
                    payload_len,
                    msg_seq_num,
                    is_refresh,
                )
            except struct.error as exc:
                # Guarded by the payload_len checks in _decode_prefix, so reaching here
                # means the layout table and the guard disagree; surface it rather than
                # letting a corrupt decode reach the book.
                self._reject("Failed to unpack MsgType %d: %s", msg_type, exc)
                parsed = None

            if parsed is not None:
                self.parsed_messages_count += 1
                parsed_msgs.append(parsed)

            # Always advance by the wire MsgSize, never by the decoded struct size.
            offset += msg_size

        return parsed_msgs

    def _decode_prefix(
        self,
        layout: struct.Struct,
        buffer: bytes,
        payload_offset: int,
        payload_len: int,
        msg_type: int,
    ) -> Optional[Tuple[object, ...]]:
        """Decode the pinned layout from the head of a payload, or reject the message.

        A payload longer than the pinned layout is fine - the trailing fields belong to
        a newer spec revision and are ignored. A payload *shorter* than the layout is a
        different (older or foreign-market) variant that cannot be safely re-interpreted
        as this one, so it is rejected rather than guessed at.
        """
        if payload_len < layout.size:
            self._reject(
                "MsgType %d payload is %d bytes but %s expects at least %d. This venue is "
                "publishing a message variant this decoder is not pinned to (SPEC_VERSION=%s).",
                msg_type,
                payload_len,
                layout.format,
                layout.size,
                SPEC_VERSION,
            )
            return None
        return layout.unpack_from(buffer, payload_offset)

    def _dispatch(
        self,
        msg_type: int,
        buffer: bytes,
        payload_offset: int,
        payload_len: int,
        msg_seq_num: Optional[int],
        is_refresh: bool,
    ) -> Optional[NYSEParsedMessage]:
        """Route one message to its handler. ``None`` for control/skipped messages."""
        if msg_type == MSG_ADD_ORDER:
            return self._handle_add_order(
                buffer, payload_offset, payload_len, msg_seq_num, is_refresh
            )
        if msg_type == MSG_MODIFY_ORDER:
            return self._handle_modify_order(
                buffer, payload_offset, payload_len, msg_seq_num, is_refresh
            )
        if msg_type == MSG_DELETE_ORDER:
            return self._handle_delete_order(
                buffer, payload_offset, payload_len, msg_seq_num, is_refresh
            )
        if msg_type == MSG_ORDER_EXECUTION:
            return self._handle_execution(
                buffer, payload_offset, payload_len, msg_seq_num, is_refresh
            )
        if msg_type == MSG_REPLACE_ORDER:
            return self._handle_replace_order(
                buffer, payload_offset, payload_len, msg_seq_num, is_refresh
            )
        if msg_type == MSG_ADD_ORDER_REFRESH:
            return self._handle_add_order_refresh(
                buffer, payload_offset, payload_len, msg_seq_num
            )
        if msg_type == MSG_SYMBOL_INDEX_MAPPING:
            self._handle_symbol_index_mapping(buffer, payload_offset, payload_len)
            return None
        if msg_type == MSG_SOURCE_TIME_REFERENCE:
            self._handle_source_time_reference(buffer, payload_offset, payload_len)
            return None
        if msg_type == MSG_SYMBOL_CLEAR:
            self._handle_symbol_clear(buffer, payload_offset, payload_len)
            return None
        if msg_type == MSG_SEQUENCE_NUMBER_RESET:
            self._handle_sequence_number_reset(buffer, payload_offset, payload_len)
            return None

        self.unhandled_message_types[msg_type] = (
            self.unhandled_message_types.get(msg_type, 0) + 1
        )
        logger.debug("Skipping unhandled MsgType %d.", msg_type)
        return None

    # ---------------------------------------------------------------------------------
    # Control messages
    # ---------------------------------------------------------------------------------
    def _handle_symbol_index_mapping(
        self, buffer: bytes, payload_offset: int, payload_len: int
    ) -> None:
        """Msg Type 3: learn ``SymbolIndex -> (ticker, PriceScaleCode)``."""
        fields = self._decode_prefix(
            self.STRUCT_003_PREFIX,
            buffer,
            payload_offset,
            payload_len,
            MSG_SYMBOL_INDEX_MAPPING,
        )
        if fields is None:
            return
        (
            symbol_index,
            symbol_raw,
            _reserved,
            _market_id,
            _system_id,
            _exchange_code,
            scale,
        ) = fields
        symbol = symbol_raw.split(b"\x00", 1)[0].decode("ascii", errors="replace").strip()
        if not 0 <= scale <= 9:
            self._reject(
                "Symbol Index Mapping for SymbolIndex %d carries PriceScaleCode %d, outside "
                "the 0..9 range implied by Common v2.4k section 3.5. Ignoring.",
                symbol_index,
                scale,
            )
            return
        self.symbol_index_map[symbol_index] = symbol
        self.price_scale_codes[symbol_index] = scale
        logger.debug("SymbolIndex %d -> %s (PriceScaleCode %d).", symbol_index, symbol, scale)

    def _handle_source_time_reference(
        self, buffer: bytes, payload_offset: int, payload_len: int
    ) -> None:
        """Msg Type 2: capture the seconds component for subsequent SourceTimeNS fields."""
        fields = self._decode_prefix(
            self.STRUCT_002, buffer, payload_offset, payload_len, MSG_SOURCE_TIME_REFERENCE
        )
        if fields is None:
            return
        _partition_id, _symbol_seq_num, source_time = fields
        self.source_time_seconds = source_time

    def _handle_symbol_clear(
        self, buffer: bytes, payload_offset: int, payload_len: int
    ) -> None:
        """Msg Type 32: drop all state for one symbol ahead of a full refresh.

        Common v2.4k section 4.4: "The client should react to receipt of a Symbol Clear
        message by clearing all state information for the specified symbol". Not doing so
        leaves pre-failover orders in the book that the exchange will never delete.
        """
        fields = self._decode_prefix(
            self.STRUCT_032, buffer, payload_offset, payload_len, MSG_SYMBOL_CLEAR
        )
        if fields is None:
            return
        _source_time, _source_time_ns, symbol_index, _next_source_seq_num = fields
        cleared = [
            oid for oid, o in self.active_orders.items() if o.symbol_index == symbol_index
        ]
        for oid in cleared:
            del self.active_orders[oid]
        logger.info(
            "Symbol Clear for SymbolIndex %d: dropped %d resting orders; awaiting refresh.",
            symbol_index,
            len(cleared),
        )

    def _handle_sequence_number_reset(
        self, buffer: bytes, payload_offset: int, payload_len: int
    ) -> None:
        """Msg Type 1: start-of-day or post-failure restart of channel numbering."""
        fields = self._decode_prefix(
            self.STRUCT_001, buffer, payload_offset, payload_len, MSG_SEQUENCE_NUMBER_RESET
        )
        if fields is None:
            return
        _source_time, _source_time_ns, product_id, channel_id = fields
        logger.info(
            "Sequence Number Reset (ProductID=%d, ChannelID=%d). A full refresh spin "
            "follows; book state is rebuilt from it.",
            product_id,
            channel_id,
        )
        self.source_time_seconds = None

    # ---------------------------------------------------------------------------------
    # Book messages
    # ---------------------------------------------------------------------------------
    def _add_to_book(
        self,
        order_id: int,
        symbol_index: int,
        side: Optional[str],
        volume: int,
        price_usd: float,
        source_time_ns_offset: int,
        firm_id: str,
    ) -> None:
        if side is None:
            self._reject(
                "Add for OrderID %d carries an invalid Side; not adding to the book.",
                order_id,
            )
            return
        if order_id in self.active_orders:
            # Integrated Feed v2.5 section 2 permits OrderID reuse when a routed-away
            # order returns unexecuted, so this is legal - but it also masks a missed
            # Delete.
            logger.debug("Add for OrderID %d replaces an existing book entry.", order_id)
        self.active_orders[order_id] = NYSEOrderState(
            order_id=order_id,
            symbol=self._symbol_for(symbol_index),
            side=side,
            volume=volume,
            price_usd=price_usd,
            source_time_ns=self._resolve_timestamp(source_time_ns_offset),
            source_time_ns_offset=source_time_ns_offset,
            symbol_index=symbol_index,
            firm_id=firm_id,
        )

    def _handle_add_order(
        self,
        buffer: bytes,
        payload_offset: int,
        payload_len: int,
        msg_seq_num: Optional[int],
        is_refresh: bool,
    ) -> Optional[NYSEParsedMessage]:
        fields = self._decode_prefix(
            self.STRUCT_100, buffer, payload_offset, payload_len, MSG_ADD_ORDER
        )
        if fields is None:
            return None
        (
            src_ns,
            symbol_index,
            symbol_seq_num,
            order_id,
            price_raw,
            volume,
            side_raw,
            firm_raw,
            _reserved,
        ) = fields

        side = self._decode_side(side_raw)
        price_usd = self._scale_price(price_raw, symbol_index)
        firm_id = firm_raw.decode("ascii", errors="replace").strip("\x00 ")

        self._add_to_book(order_id, symbol_index, side, volume, price_usd, src_ns, firm_id)
        return NYSEParsedMessage(
            msg_type=MSG_ADD_ORDER,
            order_id=order_id,
            symbol=self._symbol_for(symbol_index),
            side=side,
            volume=volume,
            price_usd=price_usd,
            symbol_index=symbol_index,
            symbol_seq_num=symbol_seq_num,
            source_time_ns=self._resolve_timestamp(src_ns),
            source_time_ns_offset=src_ns,
            seq_num=msg_seq_num,
            is_refresh=is_refresh,
        )

    def _handle_add_order_refresh(
        self,
        buffer: bytes,
        payload_offset: int,
        payload_len: int,
        msg_seq_num: Optional[int],
    ) -> Optional[NYSEParsedMessage]:
        """Msg Type 106: a resting order replayed during a refresh (v2.5 section 8).

        Unlike Msg Type 100 this message carries a full ``SourceTime`` seconds field.
        """
        fields = self._decode_prefix(
            self.STRUCT_106, buffer, payload_offset, payload_len, MSG_ADD_ORDER_REFRESH
        )
        if fields is None:
            return None
        (
            src_seconds,
            src_ns,
            symbol_index,
            symbol_seq_num,
            order_id,
            price_raw,
            volume,
            side_raw,
            firm_raw,
            _reserved,
        ) = fields

        side = self._decode_side(side_raw)
        price_usd = self._scale_price(price_raw, symbol_index)
        firm_id = firm_raw.decode("ascii", errors="replace").strip("\x00 ")

        if side is None:
            self._reject(
                "Add Order Refresh for OrderID %d carries an invalid Side; skipping.",
                order_id,
            )
            return None

        timestamp_ns = src_seconds * 1_000_000_000 + src_ns
        self.active_orders[order_id] = NYSEOrderState(
            order_id=order_id,
            symbol=self._symbol_for(symbol_index),
            side=side,
            volume=volume,
            price_usd=price_usd,
            source_time_ns=timestamp_ns,
            source_time_ns_offset=src_ns,
            symbol_index=symbol_index,
            firm_id=firm_id,
        )
        return NYSEParsedMessage(
            msg_type=MSG_ADD_ORDER_REFRESH,
            order_id=order_id,
            symbol=self._symbol_for(symbol_index),
            side=side,
            volume=volume,
            price_usd=price_usd,
            symbol_index=symbol_index,
            symbol_seq_num=symbol_seq_num,
            source_time_ns=timestamp_ns,
            source_time_ns_offset=src_ns,
            seq_num=msg_seq_num,
            is_refresh=True,
        )

    def _handle_modify_order(
        self,
        buffer: bytes,
        payload_offset: int,
        payload_len: int,
        msg_seq_num: Optional[int],
        is_refresh: bool,
    ) -> Optional[NYSEParsedMessage]:
        """Msg Type 101: Price/Volume are the **new absolute values**, not deltas.

        Integrated Feed v2.5 section 3: "The content of the price and volume fields
        represent the new values after modification."
        """
        fields = self._decode_prefix(
            self.STRUCT_101, buffer, payload_offset, payload_len, MSG_MODIFY_ORDER
        )
        if fields is None:
            return None
        (
            src_ns,
            symbol_index,
            symbol_seq_num,
            order_id,
            price_raw,
            volume,
            position_change,
            side_raw,
            _reserved2,
        ) = fields

        price_usd = self._scale_price(price_raw, symbol_index)
        side = self._decode_side(side_raw)

        order = self.active_orders.get(order_id)
        if order is None:
            self._flag_desync(
                "Modify for unknown OrderID %d: the Add for this order was missed.",
                order_id,
            )
        else:
            order.volume = volume
            order.price_usd = price_usd
            order.source_time_ns = self._resolve_timestamp(src_ns)
            order.source_time_ns_offset = src_ns
            if position_change == 1:
                order.lost_queue_position = True
            if volume == 0:
                # A zero-quantity resting order is not a book entry.
                del self.active_orders[order_id]

        return NYSEParsedMessage(
            msg_type=MSG_MODIFY_ORDER,
            order_id=order_id,
            symbol=self._symbol_for(symbol_index),
            side=side,
            volume=volume,
            price_usd=price_usd,
            symbol_index=symbol_index,
            symbol_seq_num=symbol_seq_num,
            position_change=position_change,
            source_time_ns=self._resolve_timestamp(src_ns),
            source_time_ns_offset=src_ns,
            seq_num=msg_seq_num,
            is_refresh=is_refresh,
        )

    def _handle_delete_order(
        self,
        buffer: bytes,
        payload_offset: int,
        payload_len: int,
        msg_seq_num: Optional[int],
        is_refresh: bool,
    ) -> Optional[NYSEParsedMessage]:
        fields = self._decode_prefix(
            self.STRUCT_102, buffer, payload_offset, payload_len, MSG_DELETE_ORDER
        )
        if fields is None:
            return None
        src_ns, symbol_index, symbol_seq_num, order_id, _reserved = fields

        removed = self.active_orders.pop(order_id, None)
        if removed is None:
            # Not necessarily a desync: a fully-executed order is removed by the
            # Execution handler, and some venues still publish a trailing Delete.
            logger.debug("Delete for OrderID %d, which is not in the book.", order_id)

        return NYSEParsedMessage(
            msg_type=MSG_DELETE_ORDER,
            order_id=order_id,
            symbol=removed.symbol if removed else self._symbol_for(symbol_index),
            side=removed.side if removed else None,
            symbol_index=symbol_index,
            symbol_seq_num=symbol_seq_num,
            source_time_ns=self._resolve_timestamp(src_ns),
            source_time_ns_offset=src_ns,
            seq_num=msg_seq_num,
            is_refresh=is_refresh,
        )

    def _handle_execution(
        self,
        buffer: bytes,
        payload_offset: int,
        payload_len: int,
        msg_seq_num: Optional[int],
        is_refresh: bool,
    ) -> Optional[NYSEParsedMessage]:
        """Msg Type 103 (Integrated Feed v2.5 section 5).

        Spec semantics applied here:

        * ``Volume`` is the executed quantity, so it is *subtracted* from the resting size.
        * "If the Volume field equals the number of shares previously remaining in the
          order, then the order has been fully executed and should be removed from the
          book."
        * "If the Price field is different from the price of the order, any remaining
          shares keep their original price" - so the resting price is never overwritten
          here.
        """
        fields = self._decode_prefix(
            self.STRUCT_103, buffer, payload_offset, payload_len, MSG_ORDER_EXECUTION
        )
        if fields is None:
            return None
        (
            src_ns,
            symbol_index,
            symbol_seq_num,
            order_id,
            trade_id,
            price_raw,
            exec_volume,
            printable_flag,
            _reserved,
            _tc1,
            _tc2,
            _tc3,
            _tc4,
        ) = fields

        exec_price = self._scale_price(price_raw, symbol_index)
        order = self.active_orders.get(order_id)
        symbol = order.symbol if order else self._symbol_for(symbol_index)
        side = order.side if order else None

        if order is None:
            self._flag_desync(
                "Execution for unknown OrderID %d (%d shares): the resting order was never "
                "seen, so displayed size is understated.",
                order_id,
                exec_volume,
            )
        elif exec_volume > order.volume:
            self._flag_desync(
                "Execution of %d shares exceeds the %d remaining on OrderID %d. A Modify or "
                "Add for this order was missed; removing it from the book.",
                exec_volume,
                order.volume,
                order_id,
            )
            del self.active_orders[order_id]
        else:
            order.volume -= exec_volume
            order.source_time_ns = self._resolve_timestamp(src_ns)
            order.source_time_ns_offset = src_ns
            if order.volume == 0:
                del self.active_orders[order_id]

        return NYSEParsedMessage(
            msg_type=MSG_ORDER_EXECUTION,
            order_id=order_id,
            symbol=symbol,
            side=side,
            executed_volume=exec_volume,
            price_usd=exec_price,
            symbol_index=symbol_index,
            symbol_seq_num=symbol_seq_num,
            trade_id=trade_id,
            printable=bool(printable_flag),
            source_time_ns=self._resolve_timestamp(src_ns),
            source_time_ns_offset=src_ns,
            seq_num=msg_seq_num,
            is_refresh=is_refresh,
        )

    def _handle_replace_order(
        self,
        buffer: bytes,
        payload_offset: int,
        payload_len: int,
        msg_seq_num: Optional[int],
        is_refresh: bool,
    ) -> Optional[NYSEParsedMessage]:
        """Msg Type 104 (Integrated Feed v2.5 section 6).

        No Delete is published for a replaced order - v2.5 section 4 states that when an
        order is replaced "a delete order message will not be published, rather a Replace
        Order message". A decoder that ignores 104 therefore leaks the old OrderID into
        the book permanently, inflating displayed depth for the rest of the session.
        """
        fields = self._decode_prefix(
            self.STRUCT_104, buffer, payload_offset, payload_len, MSG_REPLACE_ORDER
        )
        if fields is None:
            return None
        (
            src_ns,
            symbol_index,
            symbol_seq_num,
            order_id,
            new_order_id,
            price_raw,
            volume,
            side_raw,
            _reserved2,
        ) = fields

        price_usd = self._scale_price(price_raw, symbol_index)
        old = self.active_orders.pop(order_id, None)
        # The replacement inherits symbol, side and attribution from the sitting order
        # (v2.5 section 6); the message's own Side is used when the old order is absent.
        side = (old.side if old else None) or self._decode_side(side_raw)
        firm_id = old.firm_id if old else ""

        if old is None:
            self._flag_desync(
                "Replace of unknown OrderID %d (new OrderID %d): the original order was "
                "never seen.",
                order_id,
                new_order_id,
            )

        self._add_to_book(
            new_order_id, symbol_index, side, volume, price_usd, src_ns, firm_id
        )
        return NYSEParsedMessage(
            msg_type=MSG_REPLACE_ORDER,
            order_id=order_id,
            new_order_id=new_order_id,
            symbol=self._symbol_for(symbol_index),
            side=side,
            volume=volume,
            price_usd=price_usd,
            symbol_index=symbol_index,
            symbol_seq_num=symbol_seq_num,
            source_time_ns=self._resolve_timestamp(src_ns),
            source_time_ns_offset=src_ns,
            seq_num=msg_seq_num,
            is_refresh=is_refresh,
        )

    # ---------------------------------------------------------------------------------
    # Reporting
    # ---------------------------------------------------------------------------------
    def generate_report(
        self, last_msg: Optional[NYSEParsedMessage] = None
    ) -> NYSEFeedReport:
        """Summarise parser and book state.

        ``status`` is ``FEED_PARSER_DEGRADED`` whenever a gap, desync, or skipped message
        means the book may not match the exchange's. Do not trade off a degraded book.
        """
        degraded = bool(
            self.book_is_stale
            or self.sequence_gaps
            or self.messages_skipped
            or self.book_desync_events
        )
        status = "FEED_PARSER_DEGRADED" if degraded else "FEED_PARSER_SUCCESS"

        notes = (
            f"NYSE Arca Integrated Feed ({SPEC_VERSION}): parsed "
            f"{self.parsed_messages_count} messages; {len(self.active_orders)} resting "
            f"L3 orders; {len(self.sequence_gaps)} sequence gap(s); "
            f"{self.book_desync_events} book-desync event(s); "
            f"{self.messages_skipped} message(s) skipped; "
            f"{self.prices_scaled_with_fallback} price(s) scaled with the fallback "
            f"PriceScaleCode 10^{self.default_price_scale_code}."
        )
        if degraded:
            notes += " BOOK NOT AUTHORITATIVE - request a refresh before relying on depth."
            logger.warning(notes)
        else:
            logger.info(notes)

        return NYSEFeedReport(
            total_messages_parsed=self.parsed_messages_count,
            active_orders_count=len(self.active_orders),
            last_parsed_message=last_msg,
            status=status,
            audit_notes=notes,
            sequence_gaps=list(self.sequence_gaps),
            book_is_stale=self.book_is_stale,
            messages_skipped=self.messages_skipped,
            book_desync_events=self.book_desync_events,
            prices_scaled_with_fallback=self.prices_scaled_with_fallback,
            unhandled_message_types=dict(self.unhandled_message_types),
        )

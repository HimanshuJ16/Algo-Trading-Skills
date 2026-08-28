"""Nasdaq TotalView-ITCH 5.0 message decoder and L3 order-book state machine.

Decodes the order-lifecycle subset of the ITCH 5.0 message set -- Add Order
``A`` / ``F``, Order Executed ``E`` / ``C``, Order Cancel ``X``, Order Delete
``D``, Order Replace ``U`` -- plus the non-book Trade message ``P``, and
maintains the resulting market-by-order (L3) book keyed by Order Reference
Number.

Field offsets, lengths, endianness and price scaling follow the Nasdaq
TotalView-ITCH 5.0 specification (nasdaqtrader.com, NQTVITCHSpecification.pdf):
"All integer fields are big endian (network byte order) binary encoded numbers.
Unless otherwise noted, they are unsigned." Prices are ``Price (4)`` -- integers
with four implied decimal places, whose maximum value in TotalView-ITCH is
200,000.0000 (0x77359400).

The engine expects a single, already de-framed ITCH message. Transport framing
is the caller's responsibility: a MoldUDP64 downstream packet carries a 20-byte
header followed by message blocks, each prefixed by a 2-byte big-endian message
length that excludes the length field itself.

The dominant production risk in this code is not slowness, it is a *silent*
misparse or a silently absorbed message: both produce a book that looks healthy
and disagrees with the venue. Every message is therefore length-checked before
decode, and every well-formed message that cannot be applied to a consistent
book is counted as an integrity violation rather than ignored.
"""

import logging
import struct
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# Spec, Data Types: Price (4) has four implied decimals; the maximum value of
# Price (4) in TotalView-ITCH is 200,000.0000 (77359400 hex) = 2_000_000_000.
MAX_PRICE_TICKS = 2_000_000_000

# Spec, Add Order / Trade: "B" = Buy Order, "S" = Sell Order.
VALID_SIDES = frozenset({"B", "S"})


class ITCHParseError(ValueError):
    """Raised when raw bytes cannot be decoded as the declared ITCH message."""


class ITCHBookIntegrityError(RuntimeError):
    """Raised in strict mode when a well-formed message cannot be applied."""


@dataclass
class ITCHOrderState:
    """A single displayed order resting on the reconstructed L3 book."""

    order_ref_number: int
    stock: str
    side: str                            # 'B' or 'S'
    shares: int
    price_usd: float
    timestamp_ns: int
    price_ticks: int = 0                 # authoritative wire price; price_usd is derived


@dataclass
class ITCHParsedMessage:
    """One decoded ITCH message. Optional fields are populated per message type."""

    message_type: str                    # 'A', 'F', 'E', 'C', 'X', 'D', 'U', 'P'
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
    price_ticks: Optional[int] = None
    match_number: Optional[int] = None
    attribution: Optional[str] = None    # 'F' only: Nasdaq market participant identifier
    printable: Optional[bool] = None     # 'C' only: include in time-and-sales / volume
    new_order_ref_number: Optional[int] = None  # 'U' only: ref used for all later updates
    affects_book: bool = True            # False for 'P': trade messages do not affect the book


@dataclass
class ITCHParserReport:
    """Audit summary of a parsing run, including book-integrity accounting."""

    total_messages_parsed: int
    active_orders_count: int
    last_parsed_message: Optional[ITCHParsedMessage]
    status: str                          # 'PARSER_SUCCESS' | 'PARSER_INTEGRITY_VIOLATIONS'
    audit_notes: str
    integrity_violation_count: int = 0
    violations_by_kind: Dict[str, int] = field(default_factory=dict)


class NasdaqITCH50ParserEngine:
    """
    Nasdaq TotalView-ITCH 5.0 binary protocol parsing engine unpacking Add Order
    (A/F), Order Executed (E/C), Cancel (X), Delete (D) and Replace (U) messages
    for L3 order book reconstruction, plus non-book Trade (P) messages.

    Malformed bytes raise ``ITCHParseError``. A well-formed message that cannot be
    applied to a consistent book (an execution for an order never added, an
    over-cancel, a duplicate reference number) is an *integrity violation*: it is
    counted in ``violations_by_kind`` and, when ``strict=True``, raises
    ``ITCHBookIntegrityError``. A non-zero violation count invalidates any
    microstructure statistic computed from the run.
    """

    # Struct formats, big-endian ('>'), payload only -- the 1-byte message type
    # is consumed before unpacking. Offsets/lengths per the ITCH 5.0 spec.

    # 'A' Add Order - No MPID Attribution (spec 1.3.1), 36 bytes total.
    # Locate(2) Tracking(2) Timestamp(6) OrderRef(8) Side(1) Shares(4) Stock(8) Price(4)
    STRUCT_A = struct.Struct(">HH6sQ1sI8sI")

    # 'F' Add Order with MPID Attribution (spec 1.3.2), 40 bytes total.
    # ...as 'A', plus Attribution(4)
    STRUCT_F = struct.Struct(">HH6sQ1sI8sI4s")

    # 'E' Order Executed (spec 1.4.1), 31 bytes total.
    # Locate(2) Tracking(2) Timestamp(6) OrderRef(8) ExecutedShares(4) MatchNumber(8)
    STRUCT_E = struct.Struct(">HH6sQIQ")

    # 'C' Order Executed With Price (spec 1.4.2), 36 bytes total.
    # ...as 'E', plus Printable(1) ExecutionPrice(4)
    STRUCT_C = struct.Struct(">HH6sQIQ1sI")

    # 'X' Order Cancel (spec 1.4.3), 23 bytes total.
    # Locate(2) Tracking(2) Timestamp(6) OrderRef(8) CancelledShares(4)
    STRUCT_X = struct.Struct(">HH6sQI")

    # 'D' Order Delete (spec 1.4.4), 19 bytes total.
    # Locate(2) Tracking(2) Timestamp(6) OrderRef(8)
    STRUCT_D = struct.Struct(">HH6sQ")

    # 'U' Order Replace (spec 1.4.5), 35 bytes total.
    # Locate(2) Tracking(2) Timestamp(6) OrigOrderRef(8) NewOrderRef(8) Shares(4) Price(4)
    STRUCT_U = struct.Struct(">HH6sQQII")

    # 'P' Trade Message, Non-Cross (spec 1.5.1), 44 bytes total.
    # Locate(2) Tracking(2) Timestamp(6) OrderRef(8) Side(1) Shares(4) Stock(8)
    # Price(4) MatchNumber(8)
    STRUCT_P = struct.Struct(">HH6sQ1sI8sIQ")

    PRICE_DIVISOR = 10000.0              # ITCH 5.0 Price (4): four implied decimals

    # Total on-the-wire message length including the leading type byte.
    MESSAGE_LENGTHS: Dict[str, int] = {
        "A": 36, "F": 40, "E": 31, "C": 36,
        "X": 23, "D": 19, "U": 35, "P": 44,
    }

    VIOLATION_KINDS: Tuple[str, ...] = (
        "UNKNOWN_ORDER",
        "DUPLICATE_ORDER_ID",
        "OVER_EXECUTE",
        "OVER_CANCEL",
        "TIMESTAMP_REGRESSION",
        "PRICE_OUT_OF_RANGE",
    )

    def __init__(
        self,
        strict: bool = False,
        max_price_ticks: int = MAX_PRICE_TICKS,
    ) -> None:
        """
        Args:
            strict: raise ``ITCHBookIntegrityError`` on the first integrity
                violation instead of counting it and continuing. Use ``True`` for
                a validated production pipeline, the default ``False`` for
                exploratory replay of an imperfect archive.
            max_price_ticks: reject prices above this many ticks as out of range.
                Defaults to the spec's Price (4) maximum of 200,000.0000.
        """
        self.strict: bool = strict
        self.max_price_ticks: int = max_price_ticks
        self.active_orders: Dict[int, ITCHOrderState] = {}
        self.parsed_messages_count: int = 0
        self.integrity_violation_count: int = 0
        self.violations_by_kind: Dict[str, int] = {k: 0 for k in self.VIOLATION_KINDS}
        self._last_timestamp_ns: Optional[int] = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _record_violation(self, kind: str, detail: str) -> None:
        """Counts a book-integrity violation, or raises it in strict mode."""
        if kind not in self.violations_by_kind:
            raise KeyError(f"Unknown integrity violation kind: {kind!r}")
        self.violations_by_kind[kind] += 1
        self.integrity_violation_count += 1
        message = f"ITCH book integrity violation [{kind}]: {detail}"
        if self.strict:
            raise ITCHBookIntegrityError(message)
        logger.warning(message)

    @staticmethod
    def _unpack_6byte_timestamp(ts_bytes: bytes) -> int:
        """Unpacks the 6-byte big-endian nanoseconds-since-midnight timestamp.

        The 48-bit counter tops out at 2**48 - 1 ns (~78.2 hours), so it cannot
        wrap within a trading session and needs no overflow handling.

        The spec states only "Nanoseconds since midnight" and does not name a time
        zone in the message tables; mapping to wall-clock requires the session date
        and the venue's local time zone.
        """
        if len(ts_bytes) != 6:
            raise ITCHParseError(
                f"Timestamp field must be exactly 6 bytes, got {len(ts_bytes)}."
            )
        return int.from_bytes(ts_bytes, byteorder="big", signed=False)

    @staticmethod
    def _decode_alpha(raw: bytes, field_name: str) -> str:
        """Decodes an ITCH Alpha field.

        Spec, Data Types: "All alpha fields are ASCII fields which are left
        justified and padded on the right with spaces." Only right-hand padding is
        stripped -- a *leading* space means the field did not start where the
        layout says it did, and must not be hidden.
        """
        try:
            return raw.decode("ascii").rstrip(" ")
        except UnicodeDecodeError as exc:
            raise ITCHParseError(
                f"Non-ASCII bytes in Alpha field '{field_name}': {raw!r}. "
                "This usually means the message was decoded at the wrong offset."
            ) from exc

    def _validate_side(self, side: str, message_type: str) -> str:
        """Validates the Buy/Sell Indicator against the spec's allowed values."""
        if side not in VALID_SIDES:
            raise ITCHParseError(
                f"Invalid Buy/Sell Indicator {side!r} in '{message_type}' message; "
                f"ITCH 5.0 permits only {sorted(VALID_SIDES)}."
            )
        return side

    def _check_price(self, price_ticks: int, message_type: str) -> None:
        """Flags a Price (4) value outside the spec's representable range."""
        if price_ticks > self.max_price_ticks:
            self._record_violation(
                "PRICE_OUT_OF_RANGE",
                f"'{message_type}' price {price_ticks} ticks exceeds maximum "
                f"{self.max_price_ticks} (a scaling or offset error).",
            )

    def _check_timestamp(self, ts_ns: int) -> None:
        """Flags a backwards timestamp against the session high-water mark.

        Equal timestamps are legal -- several ITCH messages routinely share a
        nanosecond. A regression does not advance the mark, so a single spurious
        far-future timestamp keeps flagging until the stream catches up: that
        noise is the intended signal, since every message in between is of
        unknown order.
        """
        if self._last_timestamp_ns is not None and ts_ns < self._last_timestamp_ns:
            self._record_violation(
                "TIMESTAMP_REGRESSION",
                f"timestamp {ts_ns} precedes previous {self._last_timestamp_ns}; "
                "L3 replay requires chronological order.",
            )
        else:
            self._last_timestamp_ns = ts_ns

    def _unpack(self, msg_type: str, payload: bytes) -> tuple:
        """Length-checks then unpacks a payload against its message layout."""
        layout: struct.Struct = getattr(self, f"STRUCT_{msg_type}")
        if len(payload) != layout.size:
            raise ITCHParseError(
                f"'{msg_type}' message must be {self.MESSAGE_LENGTHS[msg_type]} bytes "
                f"(1 type byte + {layout.size} payload), got {len(payload) + 1}. "
                "Check the transport framing: MoldUDP64 message blocks carry a "
                "2-byte big-endian length that must be stripped before parsing."
            )
        return layout.unpack(payload)

    def _deduct_shares(
        self,
        ref_num: int,
        quantity: int,
        message_type: str,
        over_kind: str,
    ) -> None:
        """Applies the cumulative share deduction shared by 'E', 'C' and 'X'.

        Spec 1.4: subscribers "must deduct the number of shares stated in the
        Modify message from the original number of shares"; "When the number of
        display shares for an order reaches zero, the order is dead and should be
        removed from the book."
        """
        order = self.active_orders.get(ref_num)
        if order is None:
            self._record_violation(
                "UNKNOWN_ORDER",
                f"'{message_type}' for order ref {ref_num}, which is not on the book "
                "(the Add Order message was dropped or never seen).",
            )
            return

        if quantity > order.shares:
            self._record_violation(
                over_kind,
                f"'{message_type}' removes {quantity} shares from order ref {ref_num} "
                f"which has only {order.shares} displayed.",
            )
            del self.active_orders[ref_num]
            return

        order.shares -= quantity
        if order.shares == 0:
            del self.active_orders[ref_num]

    def _add_order(
        self,
        message_type: str,
        ref_num: int,
        stock: str,
        side: str,
        shares: int,
        price_ticks: int,
        ts_ns: int,
    ) -> None:
        """Inserts a resting order, flagging a reused day-unique reference number."""
        if ref_num in self.active_orders:
            self._record_violation(
                "DUPLICATE_ORDER_ID",
                f"'{message_type}' reuses live order ref {ref_num}; ITCH order "
                "reference numbers are day-unique.",
            )
        self.active_orders[ref_num] = ITCHOrderState(
            order_ref_number=ref_num,
            stock=stock,
            side=side,
            shares=shares,
            price_usd=price_ticks / self.PRICE_DIVISOR,
            timestamp_ns=ts_ns,
            price_ticks=price_ticks,
        )

    # ------------------------------------------------------------------
    # Per-message-type handlers
    # ------------------------------------------------------------------

    def _parse_add(self, msg_type: str, payload: bytes) -> ITCHParsedMessage:
        """Handles Add Order 'A' (no MPID) and 'F' (with MPID attribution)."""
        if msg_type == "F":
            (locate, tracking, ts_b, ref_num, side_b,
             shares, stock_b, price_int, attrib_b) = self._unpack("F", payload)
            attribution: Optional[str] = self._decode_alpha(attrib_b, "Attribution")
        else:
            (locate, tracking, ts_b, ref_num, side_b,
             shares, stock_b, price_int) = self._unpack("A", payload)
            attribution = None

        ts_ns = self._unpack_6byte_timestamp(ts_b)
        side = self._validate_side(
            self._decode_alpha(side_b, "Buy/Sell Indicator"), msg_type
        )
        stock = self._decode_alpha(stock_b, "Stock")

        self._check_timestamp(ts_ns)
        self._check_price(price_int, msg_type)
        self._add_order(msg_type, ref_num, stock, side, shares, price_int, ts_ns)

        return ITCHParsedMessage(
            message_type=msg_type,
            stock_locate=locate,
            tracking_number=tracking,
            timestamp_ns=ts_ns,
            order_ref_number=ref_num,
            side=side,
            shares=shares,
            stock=stock,
            price_usd=price_int / self.PRICE_DIVISOR,
            price_ticks=price_int,
            attribution=attribution,
        )

    def _parse_executed(self, msg_type: str, payload: bytes) -> ITCHParsedMessage:
        """Handles Order Executed 'E' and Order Executed With Price 'C'.

        Spec 1.4.2: 'E' and 'C' executions on the same order are cumulative, so a
        parser that ignores 'C' leaves executed shares resting on the book.
        """
        price_ticks: Optional[int] = None
        printable: Optional[bool] = None

        if msg_type == "C":
            (locate, tracking, ts_b, ref_num, exec_shares,
             match_num, printable_b, price_int) = self._unpack("C", payload)
            price_ticks = price_int
            printable_flag = self._decode_alpha(printable_b, "Printable")
            if printable_flag not in {"Y", "N"}:
                raise ITCHParseError(
                    f"Invalid Printable flag {printable_flag!r} in 'C' message; "
                    "ITCH 5.0 permits only 'Y' or 'N'."
                )
            printable = printable_flag == "Y"
        else:
            (locate, tracking, ts_b, ref_num,
             exec_shares, match_num) = self._unpack("E", payload)

        ts_ns = self._unpack_6byte_timestamp(ts_b)
        self._check_timestamp(ts_ns)
        if price_ticks is not None:
            self._check_price(price_ticks, msg_type)
        self._deduct_shares(ref_num, exec_shares, msg_type, "OVER_EXECUTE")

        return ITCHParsedMessage(
            message_type=msg_type,
            stock_locate=locate,
            tracking_number=tracking,
            timestamp_ns=ts_ns,
            order_ref_number=ref_num,
            executed_shares=exec_shares,
            match_number=match_num,
            price_ticks=price_ticks,
            price_usd=None if price_ticks is None else price_ticks / self.PRICE_DIVISOR,
            printable=printable,
        )

    def _parse_cancel(self, msg_type: str, payload: bytes) -> ITCHParsedMessage:
        """Handles Order Cancel 'X' -- a *partial* reduction, not a removal."""
        locate, tracking, ts_b, ref_num, cancel_shares = self._unpack("X", payload)
        ts_ns = self._unpack_6byte_timestamp(ts_b)

        self._check_timestamp(ts_ns)
        self._deduct_shares(ref_num, cancel_shares, msg_type, "OVER_CANCEL")

        return ITCHParsedMessage(
            message_type=msg_type,
            stock_locate=locate,
            tracking_number=tracking,
            timestamp_ns=ts_ns,
            order_ref_number=ref_num,
            canceled_shares=cancel_shares,
        )

    def _parse_delete(self, msg_type: str, payload: bytes) -> ITCHParsedMessage:
        """Handles Order Delete 'D' -- removes all remaining shares."""
        locate, tracking, ts_b, ref_num = self._unpack("D", payload)
        ts_ns = self._unpack_6byte_timestamp(ts_b)

        self._check_timestamp(ts_ns)
        if self.active_orders.pop(ref_num, None) is None:
            self._record_violation(
                "UNKNOWN_ORDER",
                f"'D' for order ref {ref_num}, which is not on the book.",
            )

        return ITCHParsedMessage(
            message_type=msg_type,
            stock_locate=locate,
            tracking_number=tracking,
            timestamp_ns=ts_ns,
            order_ref_number=ref_num,
        )

    def _parse_replace(self, msg_type: str, payload: bytes) -> ITCHParsedMessage:
        """Handles Order Replace 'U'.

        Spec 1.4.5: all remaining shares from the original order "are no longer
        accessible, and must be removed"; the replacement carries a new reference
        number that "the Nasdaq system will use ... for all subsequent updates";
        and because side, stock and attribution cannot change they are absent from
        the message -- "Firms should retain the side, stock symbol and MPID from
        the original Add Order message." Shares is the new *total* displayed
        quantity, not a deduction.
        """
        (locate, tracking, ts_b, orig_ref,
         new_ref, shares, price_int) = self._unpack("U", payload)
        ts_ns = self._unpack_6byte_timestamp(ts_b)

        self._check_timestamp(ts_ns)
        self._check_price(price_int, msg_type)

        original = self.active_orders.pop(orig_ref, None)
        if original is None:
            # Side and stock live only on the original Add Order, so a replacement
            # cannot be synthesised without inventing them.
            self._record_violation(
                "UNKNOWN_ORDER",
                f"'U' replaces order ref {orig_ref}, which is not on the book; "
                f"the replacement ref {new_ref} cannot be created because the "
                "replace message carries neither side nor stock symbol.",
            )
        else:
            self._add_order(
                msg_type, new_ref, original.stock, original.side,
                shares, price_int, ts_ns,
            )

        return ITCHParsedMessage(
            message_type=msg_type,
            stock_locate=locate,
            tracking_number=tracking,
            timestamp_ns=ts_ns,
            order_ref_number=orig_ref,
            new_order_ref_number=new_ref,
            shares=shares,
            price_usd=price_int / self.PRICE_DIVISOR,
            price_ticks=price_int,
            side=None if original is None else original.side,
            stock=None if original is None else original.stock,
        )

    def _parse_trade(self, msg_type: str, payload: bytes) -> ITCHParsedMessage:
        """Handles Trade Message, Non-Cross 'P' -- a print, not a book event.

        Spec 1.5.1: 'P' reports a match between non-displayable order types.
        "Since Trade Messages do not affect the book" the L3 book is untouched.
        The Order Reference Number has been populated as zero since 2010-12-06 and
        the Buy/Sell Indicator has always been "B" since 2014-07-14, so neither
        field identifies a resting order.
        """
        (locate, tracking, ts_b, ref_num, side_b,
         shares, stock_b, price_int, match_num) = self._unpack("P", payload)
        ts_ns = self._unpack_6byte_timestamp(ts_b)
        side = self._decode_alpha(side_b, "Buy/Sell Indicator")
        stock = self._decode_alpha(stock_b, "Stock")

        self._check_timestamp(ts_ns)
        self._check_price(price_int, msg_type)

        return ITCHParsedMessage(
            message_type=msg_type,
            stock_locate=locate,
            tracking_number=tracking,
            timestamp_ns=ts_ns,
            order_ref_number=ref_num,
            side=side,
            shares=shares,
            stock=stock,
            price_usd=price_int / self.PRICE_DIVISOR,
            price_ticks=price_int,
            match_number=match_num,
            affects_book=False,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse_message(self, raw_bytes: bytes) -> ITCHParsedMessage:
        """Parses one de-framed ITCH 5.0 message and updates L3 order book state.

        Args:
            raw_bytes: exactly one ITCH message, starting at the 1-byte Message
                Type. Any MoldUDP64/SoupBinTCP framing must already be stripped.

        Returns:
            The decoded :class:`ITCHParsedMessage`.

        Raises:
            ITCHParseError: the bytes are not a well-formed message of the
                declared type (wrong length, bad Alpha field, invalid enum).
            ITCHBookIntegrityError: strict mode only, when a well-formed message
                cannot be applied to a consistent book.
        """
        if not raw_bytes:
            raise ITCHParseError("Raw binary message cannot be empty.")

        type_byte = raw_bytes[0]
        if not 0x20 <= type_byte <= 0x7E:
            raise ITCHParseError(
                f"Message type byte 0x{type_byte:02X} is not printable ASCII; "
                "the stream is misaligned or the framing is wrong."
            )
        msg_type = chr(type_byte)

        handler: Optional[Callable[[str, bytes], ITCHParsedMessage]] = {
            "A": self._parse_add,
            "F": self._parse_add,
            "E": self._parse_executed,
            "C": self._parse_executed,
            "X": self._parse_cancel,
            "D": self._parse_delete,
            "U": self._parse_replace,
            "P": self._parse_trade,
        }.get(msg_type)

        if handler is None:
            raise ITCHParseError(
                f"Unsupported ITCH message type: '{msg_type}'. This engine decodes "
                f"the order-lifecycle subset {sorted(self.MESSAGE_LENGTHS)}; skip "
                "other types by their transport-declared length rather than "
                "guessing a layout."
            )

        parsed = handler(msg_type, raw_bytes[1:])
        self.parsed_messages_count += 1
        return parsed

    def generate_report(
        self, last_msg: Optional[ITCHParsedMessage] = None
    ) -> ITCHParserReport:
        """Summarises the run. A non-zero violation count invalidates the book."""
        if self.integrity_violation_count == 0:
            status = "PARSER_SUCCESS"
            notes = (
                f"ITCH PARSER SUCCESS: Parsed {self.parsed_messages_count} messages. "
                f"Active L3 Orders in Book = {len(self.active_orders)}."
            )
            logger.info(notes)
        else:
            status = "PARSER_INTEGRITY_VIOLATIONS"
            breakdown = ", ".join(
                f"{kind}={count}"
                for kind, count in sorted(self.violations_by_kind.items())
                if count
            )
            notes = (
                f"ITCH PARSER INTEGRITY VIOLATIONS: Parsed "
                f"{self.parsed_messages_count} messages with "
                f"{self.integrity_violation_count} violation(s) ({breakdown}). "
                f"Active L3 Orders in Book = {len(self.active_orders)}. "
                "The reconstructed book diverges from the venue; do not report "
                "microstructure statistics from this run as clean."
            )
            logger.warning(notes)

        return ITCHParserReport(
            total_messages_parsed=self.parsed_messages_count,
            active_orders_count=len(self.active_orders),
            last_parsed_message=last_msg,
            status=status,
            audit_notes=notes,
            integrity_violation_count=self.integrity_violation_count,
            violations_by_kind=dict(self.violations_by_kind),
        )

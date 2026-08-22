"""CME Group tag=value FIX session and order encoding.

Protocol status (verified 2026-08-21) — read before using this module for live
order entry:

* CME **order entry** no longer speaks tag=value FIX. iLink 2 (the FIX 4.2-based
  order entry protocol these tags come from) was decommissioned on the Market
  Segment Gateway on 2021-03-28 and on the Convenience Gateway on 2025-04-06.
  Live CME order entry is iLink 3: FIX Simple Binary Encoding (SBE) over the
  FIXP session layer, which has no MsgSeqNum(34)/ResendRequest(35=2) semantics
  at all.
* The tag=value FIX surface CME still operates is **post-trade**: CME STP is a
  standard FIX 4.4 API for trade capture.

So this module is a correct tag=value FIX encoder and session-sequence state
machine — useful for CME STP FIX 4.4 sessions, for replaying or simulating
archived iLink 2 flow, and for venues that still accept FIX 4.2/4.4 order
entry. It will **not** reach CME Globex. Use `to_ilink3_order_fields()` to see
how each order field maps onto iLink 3, where the tag numbers and data types
differ.

References: see ../references/standards.md.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable, Dict, Mapping, Optional, Tuple, Union

logger = logging.getLogger(__name__)

SOH = "\x01"

#: Tag 8000 SelfMatchPreventionInstruction — the only two values CME defines.
#: 'O' cancels the *oldest* (resting) order, 'N' cancels the *newest*
#: (aggressing) order. There is no "cancel both", and no 'R'.
SMP_INSTRUCTION_CANCEL_OLDEST = "O"
SMP_INSTRUCTION_CANCEL_NEWEST = "N"
VALID_SMP_INSTRUCTIONS = frozenset({SMP_INSTRUCTION_CANCEL_OLDEST, SMP_INSTRUCTION_CANCEL_NEWEST})

#: iLink 2 (tag=value, decommissioned) -> iLink 3 (SBE) field equivalents.
ILINK2_TO_ILINK3_FIELDS: Mapping[int, Tuple[int, str, str]] = {
    50: (5392, "SenderID", "String(20) — the Rule 576 operator ID moved off SenderSubID(50)"),
    1028: (1028, "ManualOrderIndicator", "Boolean: 0=automated, 1=manual (not 'N'/'Y')"),
    7928: (2362, "SelfMatchPreventionID", "uInt64 — numeric, not a 12-character alphanumeric string"),
    8000: (8000, "SelfMatchPreventionInstruction", "Char: 'O'=CancelOldest, 'N'=CancelNewest"),
}

_ALPHANUMERIC = re.compile(r"^[A-Za-z0-9]+$")
_NUMERIC_STRING = re.compile(r"^-?\d+(\.\d+)?$")

PriceLike = Union[str, Decimal, int, float]


class FixSessionError(Exception):
    """Raised when the peer violates FIX session-level sequencing rules."""


@dataclass
class CmeFixOrderParams:
    """Order parameters for a tag=value NewOrderSingle (35=D).

    `price` accepts a decimal string or `Decimal` and is serialised without
    rounding. A float is permitted but discouraged: it is converted through its
    shortest repr, so tick-precise values survive, but a float is still the
    wrong carrier for a price.
    """

    symbol: str
    side: str                                    # '1' (Buy) or '2' (Sell)
    quantity: int
    price: PriceLike
    cl_ord_id: str
    operator_id: str                             # Tag 50 (Rule 576 operator ID)
    smp_id: str                                  # Tag 7928 (SelfMatchPreventionID)
    smp_instruction: Optional[str] = SMP_INSTRUCTION_CANCEL_OLDEST  # Tag 8000; None omits the tag
    is_automated: bool = True                    # Tag 1028 ('N' automated, 'Y' manual)
    account: Optional[str] = None                # Tag 1 — no default: a placeholder account routes real risk


class CmeFixSessionEngine:
    """Tag=value FIX 4.2/4.4 session and order encoder.

    Emits well-formed messages (BeginString, BodyLength, CheckSum, SOH
    delimiter, SendingTime) and tracks inbound sequencing per the FIX session
    layer: a single outstanding ResendRequest, PossDupFlag-aware duplicate
    handling, SequenceReset support, and a Logout on a too-low sequence number.

    Sequence numbers are held in memory only. A real session must persist both
    counters and restore them on reconnect; restarting at 1 mid-session
    desynchronises the session and the peer will disconnect it.
    """

    def __init__(
        self,
        sender_comp_id: str,
        target_comp_id: str = "CME",
        begin_string: str = "FIX.4.2",
        delimiter: str = SOH,
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        if not sender_comp_id or not sender_comp_id.strip():
            raise ValueError("sender_comp_id must be a non-empty string.")
        if not target_comp_id or not target_comp_id.strip():
            raise ValueError("target_comp_id must be a non-empty string.")
        if len(delimiter) != 1:
            raise ValueError("delimiter must be exactly one character (SOH on the wire).")

        self.sender_comp_id = sender_comp_id
        self.target_comp_id = target_comp_id
        self.begin_string = begin_string
        self.delimiter = delimiter
        self._clock = clock or (lambda: datetime.now(timezone.utc))

        self.outbound_seq_num = 1
        self.expected_inbound_seq_num = 1
        #: Highest sequence number covered by an outstanding ResendRequest, or
        #: None when no recovery is in flight. Guards against a resend storm.
        self.resend_requested_through: Optional[int] = None
        #: Set when a session-fatal sequence error has been detected. The caller
        #: must send the returned Logout and drop the transport connection.
        self.session_terminated = False
        #: True when the message passed to the most recent
        #: process_inbound_message() call was in sequence and its business
        #: content must be applied. False for duplicates and for messages that
        #: arrive out of sequence during recovery — applying those would replay
        #: or reorder fills. A None return alone does not mean "process it".
        self.last_inbound_accepted = False

    # ---------------------------------------------------------------- encoding

    def _validate_field_value(self, tag: int, value: str) -> str:
        text = str(value)
        if self.delimiter in text or SOH in text:
            raise ValueError(
                f"Tag {tag} value contains a field delimiter; an unescaped delimiter lets "
                f"caller-supplied data forge FIX fields."
            )
        if "=" in text:
            raise ValueError(f"Tag {tag} value contains '=', which corrupts field parsing.")
        if not text.isascii():
            raise ValueError(f"Tag {tag} value is not ASCII; FIX tag=value fields are ASCII bytes.")
        return text

    def _sending_time(self) -> str:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("clock() must return a timezone-aware datetime.")
        return now.astimezone(timezone.utc).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]

    def build_fix_message(self, msg_type: str, body_tags: Dict[int, str]) -> str:
        """Serialise a complete FIX message and consume one outbound sequence number.

        Field order follows the FIX standard header: BeginString(8), BodyLength(9)
        and MsgType(35) first, CheckSum(10) last. The returned string must be
        transmitted verbatim — BodyLength and CheckSum are computed over exactly
        these bytes, so any downstream re-encoding invalidates them.

        The sequence number is consumed at build time, so messages must be sent
        in the order they were built.
        """
        if not msg_type:
            raise ValueError("msg_type is required.")

        d = self.delimiter
        header = [
            (35, self._validate_field_value(35, msg_type)),
            (49, self._validate_field_value(49, self.sender_comp_id)),
            (56, self._validate_field_value(56, self.target_comp_id)),
            (34, str(self.outbound_seq_num)),
            (52, self._sending_time()),
        ]
        body = [(tag, self._validate_field_value(tag, val)) for tag, val in body_tags.items()]

        payload = "".join(f"{tag}={val}{d}" for tag, val in header + body)
        prefix = f"8={self.begin_string}{d}9={len(payload.encode('ascii'))}{d}"
        message = prefix + payload
        checksum = sum(message.encode("ascii")) % 256
        message += f"10={checksum:03d}{d}"

        self.outbound_seq_num += 1
        return message

    # ------------------------------------------------------------------ orders

    @staticmethod
    def _format_price(price: PriceLike) -> str:
        if isinstance(price, bool):
            raise ValueError("price must be a decimal value, not a bool.")
        if isinstance(price, str):
            if not _NUMERIC_STRING.match(price.strip()):
                raise ValueError(f"price string {price!r} is not a plain decimal number.")
            candidate = Decimal(price.strip())
        elif isinstance(price, Decimal):
            candidate = price
        elif isinstance(price, (int, float)):
            try:
                candidate = Decimal(repr(price))
            except InvalidOperation as exc:
                raise ValueError(f"price {price!r} is not a finite decimal value.") from exc
        else:
            raise TypeError(f"price must be str, Decimal, int or float, got {type(price).__name__}.")

        if not candidate.is_finite():
            raise ValueError("price must be finite; NaN and Infinity are not representable in FIX.")
        # `f` formatting avoids scientific notation, which FIX price fields do
        # not accept, and preserves every decimal place the caller supplied.
        # Rounding to a fixed precision here would silently mis-price any
        # instrument quoted more finely than that precision — FX futures quote
        # to five decimal places.
        return format(candidate, "f")

    def _validate_order(self, params: CmeFixOrderParams) -> None:
        if not params.symbol or not params.symbol.strip():
            raise ValueError("symbol is required.")
        if not params.cl_ord_id or not params.cl_ord_id.strip():
            raise ValueError("cl_ord_id is required and must be unique per order.")
        if params.side not in ("1", "2"):
            raise ValueError(f"side must be '1' (Buy) or '2' (Sell), got {params.side!r}.")
        if isinstance(params.quantity, bool) or not isinstance(params.quantity, int):
            raise ValueError("quantity must be an integer number of contracts.")
        if params.quantity <= 0:
            raise ValueError(f"quantity must be positive, got {params.quantity}.")
        if not params.operator_id or params.operator_id.strip() != params.operator_id:
            raise ValueError(
                "Tag 50 operator ID (CME Rule 576) is required and must not carry padding whitespace."
            )
        if not params.account or not params.account.strip():
            raise ValueError("Tag 1 account is required; there is no safe default account.")
        if not params.smp_id or not _ALPHANUMERIC.match(params.smp_id):
            raise ValueError("Tag 7928 SMP ID must be a non-empty alphanumeric string with no spaces.")
        if len(params.smp_id) > 12:
            # CME documents SMP IDs as alphanumeric, no spaces, not exceeding 12
            # bytes. Warn rather than raise, so a venue-specific longer ID is not
            # blocked by a limit this module cannot re-verify at runtime.
            logger.warning(
                "SMP ID %r is %d characters; CME documents a 12-byte maximum for Tag 7928.",
                params.smp_id,
                len(params.smp_id),
            )
        if params.smp_instruction is not None and params.smp_instruction not in VALID_SMP_INSTRUCTIONS:
            raise ValueError(
                f"Tag 8000 must be 'O' (cancel oldest/resting) or 'N' (cancel newest/aggressing), "
                f"got {params.smp_instruction!r}."
            )

    def create_new_order_single(self, params: CmeFixOrderParams) -> str:
        """Build a NewOrderSingle (35=D) carrying the CME order-entry tags.

        Tag 8000 is omitted when `smp_instruction` is None; CME's documented
        default is then to cancel the resting order.
        """
        self._validate_order(params)

        body_tags: Dict[int, str] = {
            1: params.account,
            11: params.cl_ord_id,
            50: params.operator_id,
            54: params.side,
            38: str(params.quantity),
            40: "2",                                       # OrdType: Limit
            44: self._format_price(params.price),
            55: params.symbol,
            1028: "N" if params.is_automated else "Y",     # ManualOrderIndicator
            7928: params.smp_id,                           # SelfMatchPreventionID
        }
        if params.smp_instruction is not None:
            body_tags[8000] = params.smp_instruction

        return self.build_fix_message("D", body_tags)

    # -------------------------------------------------------------- sequencing

    def _resend_request(self, through: int) -> str:
        logger.warning(
            "Sequence gap: expected %d, requesting resend of [%d, %d].",
            self.expected_inbound_seq_num,
            self.expected_inbound_seq_num,
            through,
        )
        self.resend_requested_through = through
        return self.build_fix_message("2", {7: str(self.expected_inbound_seq_num), 16: str(through)})

    def _logout(self, reason: str) -> str:
        self.session_terminated = True
        logger.error("Session-fatal sequence error: %s. Emitting Logout (35=5).", reason)
        return self.build_fix_message("5", {58: reason})

    def _apply_sequence_reset(self, msg_parts: Mapping[int, str]) -> None:
        raw = msg_parts.get(36)
        if raw is None:
            raise FixSessionError("SequenceReset (35=4) received without NewSeqNo (tag 36).")
        try:
            new_seq = int(raw)
        except (TypeError, ValueError) as exc:
            raise FixSessionError(f"SequenceReset carried a non-integer NewSeqNo {raw!r}.") from exc
        if new_seq < self.expected_inbound_seq_num:
            raise FixSessionError(
                f"SequenceReset NewSeqNo {new_seq} would move the inbound sequence backwards "
                f"from {self.expected_inbound_seq_num}."
            )
        self.expected_inbound_seq_num = new_seq
        if self.resend_requested_through is not None and new_seq > self.resend_requested_through:
            self.resend_requested_through = None

    def process_inbound_message(self, msg_parts: Mapping[int, str]) -> Optional[str]:
        """Apply FIX session sequencing to one inbound message.

        Returns the message the caller must transmit next, or None:

        * ResendRequest (35=2) on a newly detected gap — and **only** on a newly
          detected gap. While a resend is outstanding, further high sequence
          numbers are the peer working through the range; re-requesting on each
          one produces a resend storm that ends in a disconnect.
        * Logout (35=5) when the peer sends a sequence number below the expected
          one without PossDupFlag(43)=Y. The FIX session layer treats that as
          unrecoverable: send the Logout, then drop the transport connection.
        * None when the message is in sequence, an admissible duplicate, or
          arrives while recovery is already in flight.

        A None return does **not** by itself mean the message may be processed.
        Read `last_inbound_accepted` for that: it is True only for a message
        that arrived in sequence. Applying a duplicate or an out-of-sequence
        message replays or reorders fills.

        Not thread-safe: one engine per session, driven by a single reader.

        Raises FixSessionError on a malformed or backwards SequenceReset, and
        ValueError when MsgSeqNum is missing or unparseable.
        """
        self.last_inbound_accepted = False
        if self.session_terminated:
            raise FixSessionError(
                "Session was terminated by a sequence error; reconnect before processing further messages."
            )
        if 34 not in msg_parts:
            raise ValueError("Inbound FIX message missing Tag 34 (MsgSeqNum).")
        raw_seq = msg_parts[34]
        if not isinstance(raw_seq, str) or not raw_seq.isdigit():
            raise ValueError(f"Tag 34 must be a string of digits, got {raw_seq!r}.")
        inbound_seq = int(raw_seq)
        if inbound_seq <= 0:
            raise ValueError(f"Tag 34 must be positive, got {inbound_seq}.")

        msg_type = msg_parts.get(35, "")
        poss_dup = msg_parts.get(43, "N") == "Y"
        gap_fill = msg_parts.get(123, "N") == "Y"

        # SequenceReset-Reset (35=4 with GapFillFlag != Y) is processed whatever
        # its own MsgSeqNum: it is the one message exempt from the too-low rule.
        if msg_type == "4" and not gap_fill:
            self._apply_sequence_reset(msg_parts)
            logger.info("SequenceReset-Reset applied; now expecting %d.", self.expected_inbound_seq_num)
            return None

        if inbound_seq > self.expected_inbound_seq_num:
            if self.resend_requested_through is not None:
                logger.debug(
                    "Sequence %d arrived while a resend through %d is outstanding; not re-requesting.",
                    inbound_seq,
                    self.resend_requested_through,
                )
                return None
            return self._resend_request(inbound_seq - 1)

        if inbound_seq == self.expected_inbound_seq_num:
            if msg_type == "4" and gap_fill:
                self._apply_sequence_reset(msg_parts)
                logger.debug("GapFill applied; now expecting %d.", self.expected_inbound_seq_num)
            else:
                self.expected_inbound_seq_num += 1
            if (
                self.resend_requested_through is not None
                and self.expected_inbound_seq_num > self.resend_requested_through
            ):
                logger.info("Gap recovered; resend through %d is complete.", self.resend_requested_through)
                self.resend_requested_through = None
            self.last_inbound_accepted = True
            return None

        if poss_dup:
            logger.debug(
                "Discarding PossDup message %d (expecting %d).",
                inbound_seq,
                self.expected_inbound_seq_num,
            )
            return None

        return self._logout(
            f"MsgSeqNum too low, expecting {self.expected_inbound_seq_num} but received {inbound_seq}"
        )


def to_ilink3_order_fields(params: CmeFixOrderParams) -> Dict[int, object]:
    """Map order fields onto their iLink 3 (SBE) equivalents.

    iLink 3 is what CME order entry actually accepts today. The tag numbers and
    data types differ from the tag=value form, and this is where a naive port
    breaks: SelfMatchPreventionID moves from Tag 7928 (alphanumeric string) to
    Tag 2362 (uInt64), the Rule 576 operator ID moves from Tag 50 to Tag 5392
    (SenderID, 20 bytes), and ManualOrderIndicator becomes a boolean rather than
    the characters 'N'/'Y'.

    Raises ValueError when a field cannot be represented in iLink 3 — most
    commonly a non-numeric SMP ID, which is legal in tag=value FIX and is not
    representable in iLink 3.
    """
    if len(params.operator_id) > 20:
        raise ValueError(
            f"iLink 3 SenderID (5392) is 20 bytes; this operator ID is {len(params.operator_id)} characters."
        )
    if not params.smp_id.isdigit():
        raise ValueError(
            f"iLink 3 SelfMatchPreventionID (2362) is a uInt64; {params.smp_id!r} is not numeric. "
            f"Register a numeric SMP ID rather than reusing the tag=value string."
        )
    smp_id = int(params.smp_id)
    if smp_id >= 2 ** 64:
        raise ValueError("SMP ID exceeds the uInt64 range of iLink 3 Tag 2362.")

    fields: Dict[int, object] = {
        5392: params.operator_id,
        1028: 0 if params.is_automated else 1,
        2362: smp_id,
    }
    if params.smp_instruction is not None:
        fields[8000] = params.smp_instruction
    return fields

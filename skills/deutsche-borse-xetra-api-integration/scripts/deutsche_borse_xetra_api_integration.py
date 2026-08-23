"""
deutsche-borse-xetra-api-integration: pre-dispatch validation and T7 ETI header
framing for Deutsche Börse Xetra (MIC XETR) cash-market orders.

What this module is and is not
------------------------------
It is a **client-side pre-trade gate**. It validates an order against the MiFID II
RTS 11 tick size regime and the T7 ETI field domains, converts EUR prices into the
scaled integers the wire format actually uses, and packs the 24-byte T7 ETI request
header.

It is **not** a transport. Nothing here opens a socket, logs on to a gateway, or
sends an order. ``ready_to_send`` on the result means "this order passed local
validation", never "this order reached the exchange". It is also not a full message
encoder: it frames the header, not the message body, which is release-specific and
must come from the ETI reference for the release you are certified against.

The tick size regime is the whole point
---------------------------------------
Xetra (XETR) and Börse Frankfurt (XFRA) "strictly apply the minimum tick size
requirements to orders and quotes in shares and depository receipts as per the
Commission Delegated Regulation (EU) 2017/588 and the corresponding Annex (RTS 11)"
(Xetra Circular 024/19).

RTS 11 is **not** a price-only table. The tick is looked up in a 19-price-band ×
6-liquidity-band matrix, where the liquidity band comes from the instrument's
average daily number of transactions (ADNT) on its most relevant market, published
by ESMA. A tick size cannot be derived from the price alone, which is why
``liquidity_band`` is a required input here rather than something this module
guesses. The band is per-instrument reference data: for Xetra it is distributed in
the Reference Data file, and it changes annually.

Consequences of getting it wrong are concrete. At €62.50 the tick is €0.01 in
liquidity band 6 but €0.50 in band 1 — a price this module would have to reject for
an illiquid name and accept for a DAX name. Off-tick orders surface as
``ExecRestatementReason`` 238 "Invalid limit price" (243 "Invalid stop price"), and
at the annual band change Xetra deletes resting orders whose limits no longer
comply, with exactly those reasons.

Determinism
-----------
Nothing here reads the clock. The T7 ETI request header carries no timestamp field
(see ``T7EtiRequestHeader``), so there is nothing to inject.

References: see ``references/standards.md``.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Dict, Final, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

PriceInput = Union[Decimal, str, int, float]

# --- T7 ETI template IDs -----------------------------------------------------
#: New Order Single. Confirmed in the T7 ETI Cash Message Reference from Release
#: 5.0 through Release 14.0.
TEMPLATE_NEW_ORDER_SINGLE: Final[int] = 10100
#: New Order Single (short layout).
TEMPLATE_NEW_ORDER_SINGLE_SHORT: Final[int] = 10125
#: New Order Single or Multi Leg -- the successor message.
TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG: Final[int] = 10138
#: New Order Single or Multi Leg (short layout).
TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG_SHORT: Final[int] = 10139

#: Templates the R14.0 reference lists for decommissioning with ETI version 14.1
#: ("mid-2026"), mapped to the replacement the same change log names. Building new
#: integrations on these is building on a scheduled removal.
DEPRECATED_TEMPLATE_REPLACEMENTS: Final[Dict[int, int]] = {
    10100: 10138,   # New Order Single
    10125: 10139,   # New Order Single (short layout)
    10106: 10140,   # Replace Order Single
    10126: 10141,   # Replace Order Single (short layout)
}

# --- T7 ETI field domains ----------------------------------------------------
#: TradingCapacity (tag 1815), required on the order message. Numeric, NOT the
#: letters 'P'/'A'/'M' -- those are leading characters of the separate `Account`
#: field (tag 1), which books positions and does not express MiFID capacity.
TRADING_CAPACITY_CUSTOMER_AGENCY: Final[int] = 1
TRADING_CAPACITY_PRINCIPAL_PROPRIETARY: Final[int] = 5
TRADING_CAPACITY_MARKET_MAKER: Final[int] = 6
TRADING_CAPACITY_RISKLESS_PRINCIPAL: Final[int] = 9
TRADING_CAPACITY_RETAIL_CUSTOMER_AGENCY: Final[int] = 10

_TRADING_CAPACITY_NAMES: Final[Dict[int, str]] = {
    TRADING_CAPACITY_CUSTOMER_AGENCY: "Customer (Agency)",
    TRADING_CAPACITY_PRINCIPAL_PROPRIETARY: "Principal (Proprietary)",
    TRADING_CAPACITY_MARKET_MAKER: "Market Maker",
    TRADING_CAPACITY_RISKLESS_PRINCIPAL: "Riskless Principal",
    TRADING_CAPACITY_RETAIL_CUSTOMER_AGENCY: "Retail Customer (Agency)",
}

#: OrderOrigination (tag 1724) -- the MiFID field flagging direct/sponsored access.
#: Omitted entirely when the order is not DEA flow.
ORDER_ORIGINATION_DIRECT_ACCESS_CUSTOMER: Final[int] = 5

#: Qualifiers for the RTS 24 short-code fields (ExecutingTraderQualifier tag 25124,
#: PartyIdInvestmentDecisionMakerQualifier tag 21222).
SHORT_CODE_QUALIFIER_ALGO: Final[int] = 22
SHORT_CODE_QUALIFIER_HUMAN: Final[int] = 24

#: Side (tag 54): unsigned int on the wire, not a string.
SIDE_BUY: Final[int] = 1
SIDE_SELL: Final[int] = 2
_SIDE_ALIASES: Final[Dict[str, int]] = {"BUY": SIDE_BUY, "B": SIDE_BUY,
                                        "SELL": SIDE_SELL, "S": SIDE_SELL}

#: ETI PriceType: "Price in integer format including 8 decimals", 8-byte signed int.
PRICE_SCALE_EXPONENT: Final[int] = 8
PRICE_SCALE: Final[Decimal] = Decimal(10) ** PRICE_SCALE_EXPONENT
_INT64_MIN: Final[int] = -(2 ** 63)
_INT64_MAX: Final[int] = 2 ** 63 - 1

#: Wire layout of MessageHeaderIn + RequestHeader, little endian:
#: BodyLen(4) TemplateID(2) NetworkMsgID(8) Pad2(2) MsgSeqNum(4) SenderSubID(4).
_ETI_HEADER_STRUCT: Final[struct.Struct] = struct.Struct("<IH8s2sII")
ETI_REQUEST_HEADER_LEN: Final[int] = 24
_UINT16_MAX: Final[int] = 2 ** 16 - 1
_UINT32_MAX: Final[int] = 2 ** 32 - 1

# --- RTS 11 tick size regime -------------------------------------------------
#: Upper bound (exclusive) of each RTS 11 Annex price range, in ascending order.
#: The final range is open-ended (50 000 <= price).
_RTS11_PRICE_BAND_UPPER_BOUNDS: Final[Tuple[Decimal, ...]] = tuple(Decimal(b) for b in (
    "0.1", "0.2", "0.5", "1", "2", "5", "10", "20", "50", "100", "200", "500",
    "1000", "2000", "5000", "10000", "20000", "50000",
))

#: The RTS 11 Annex, transcribed row by row. Each row is one price range; the six
#: entries are liquidity bands 1..6 in order. Kept as an explicit literal rather
#: than derived from the diagonal pattern so it can be checked line-by-line
#: against the published Annex.
_RTS11_TICK_TABLE: Final[Tuple[Tuple[Decimal, ...], ...]] = tuple(
    tuple(Decimal(v) for v in row) for row in (
        # LB1      LB2       LB3       LB4       LB5       LB6
        ("0.0005", "0.0002", "0.0001", "0.0001", "0.0001", "0.0001"),  # 0      <= p < 0.1
        ("0.001",  "0.0005", "0.0002", "0.0001", "0.0001", "0.0001"),  # 0.1    <= p < 0.2
        ("0.002",  "0.001",  "0.0005", "0.0002", "0.0001", "0.0001"),  # 0.2    <= p < 0.5
        ("0.005",  "0.002",  "0.001",  "0.0005", "0.0002", "0.0001"),  # 0.5    <= p < 1
        ("0.01",   "0.005",  "0.002",  "0.001",  "0.0005", "0.0002"),  # 1      <= p < 2
        ("0.02",   "0.01",   "0.005",  "0.002",  "0.001",  "0.0005"),  # 2      <= p < 5
        ("0.05",   "0.02",   "0.01",   "0.005",  "0.002",  "0.001"),   # 5      <= p < 10
        ("0.1",    "0.05",   "0.02",   "0.01",   "0.005",  "0.002"),   # 10     <= p < 20
        ("0.2",    "0.1",    "0.05",   "0.02",   "0.01",   "0.005"),   # 20     <= p < 50
        ("0.5",    "0.2",    "0.1",    "0.05",   "0.02",   "0.01"),    # 50     <= p < 100
        ("1",      "0.5",    "0.2",    "0.1",    "0.05",   "0.02"),    # 100    <= p < 200
        ("2",      "1",      "0.5",    "0.2",    "0.1",    "0.05"),    # 200    <= p < 500
        ("5",      "2",      "1",      "0.5",    "0.2",    "0.1"),     # 500    <= p < 1000
        ("10",     "5",      "2",      "1",      "0.5",    "0.2"),     # 1000   <= p < 2000
        ("20",     "10",     "5",      "2",      "1",      "0.5"),     # 2000   <= p < 5000
        ("50",     "20",     "10",     "5",      "2",      "1"),       # 5000   <= p < 10000
        ("100",    "50",     "20",     "10",     "5",      "2"),       # 10000  <= p < 20000
        ("200",    "100",    "50",     "20",     "10",     "5"),       # 20000  <= p < 50000
        ("500",    "200",    "100",    "50",     "20",     "10"),      # 50000  <= p
    )
)

MIN_LIQUIDITY_BAND: Final[int] = 1
MAX_LIQUIDITY_BAND: Final[int] = 6

#: Lower bound (inclusive) of the ADNT range for each RTS 11 liquidity band, used
#: only to explain a band in log and report text.
_RTS11_ADNT_LOWER_BOUNDS: Final[Tuple[int, ...]] = (0, 10, 80, 600, 2000, 9000)


class XetraOrderValidationError(ValueError):
    """Raised when an order or engine argument is structurally invalid.

    Subclasses ``ValueError``. Order construction fails loudly rather than
    producing a message the gateway will reject or, worse, accept with the wrong
    economics: a mistyped quantity or an unscaled price is a defect, not an input
    to be coerced.
    """


def _to_decimal(value: PriceInput, name: str) -> Decimal:
    """Convert a price-like input to Decimal without inheriting float artefacts."""
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, bool):
        raise XetraOrderValidationError(f"{name} must be a number, got {value!r}")
    elif isinstance(value, (int, str)):
        try:
            candidate = Decimal(value)
        except InvalidOperation as exc:
            raise XetraOrderValidationError(f"{name} is not a valid decimal: {value!r}") from exc
    elif isinstance(value, float):
        # str() first: Decimal(0.1) is 0.1000000000000000055511151231257827…,
        # which is never on any RTS 11 tick.
        candidate = Decimal(str(value))
    else:
        raise XetraOrderValidationError(f"{name} must be a number, got {type(value).__name__}")

    if not candidate.is_finite():
        raise XetraOrderValidationError(f"{name} must be finite, got {value!r}")
    return candidate


def _require_int(value: object, name: str, *, minimum: Optional[int] = None,
                 maximum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise XetraOrderValidationError(f"{name} must be an int, got {value!r}")
    if minimum is not None and value < minimum:
        raise XetraOrderValidationError(f"{name} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise XetraOrderValidationError(f"{name} must be <= {maximum}, got {value}")
    return value


def validate_isin(isin: str) -> str:
    """Validate an ISIN's structure and ISO 6166 check digit; return it normalised.

    Catches the transposed-character case that would otherwise route an order to a
    different instrument. Note that the ISIN is a human-facing reference here: T7
    ETI identifies instruments by the numeric ``SecurityID`` (tag 48) together with
    ``MarketSegmentID`` (tag 1300).
    """
    if not isinstance(isin, str):
        raise XetraOrderValidationError(f"isin must be a string, got {type(isin).__name__}")
    candidate = isin.strip().upper()
    if len(candidate) != 12:
        raise XetraOrderValidationError(f"isin must be 12 characters, got {isin!r}")
    if not candidate[:2].isalpha() or not candidate[2:].isalnum():
        raise XetraOrderValidationError(f"isin has an invalid structure: {isin!r}")

    # ISO 6166 check digit: expand letters to two digits, then Luhn.
    digits = "".join(str(ord(c) - 55) if c.isalpha() else c for c in candidate)
    total = 0
    for position, char in enumerate(reversed(digits)):
        digit = int(char)
        if position % 2 == 1:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    if total % 10 != 0:
        raise XetraOrderValidationError(f"isin check digit is invalid: {isin!r}")
    return candidate


def normalise_side(side: object) -> int:
    """Map 'BUY'/'SELL' (or the wire values 1/2) to the ETI Side (tag 54) value."""
    if isinstance(side, bool):
        raise XetraOrderValidationError(f"side must be 'BUY'/'SELL' or 1/2, got {side!r}")
    if isinstance(side, int):
        if side in (SIDE_BUY, SIDE_SELL):
            return side
        raise XetraOrderValidationError(f"side must be {SIDE_BUY} (Buy) or {SIDE_SELL} (Sell), got {side}")
    if isinstance(side, str):
        try:
            return _SIDE_ALIASES[side.strip().upper()]
        except KeyError:
            raise XetraOrderValidationError(
                f"side must be one of {sorted(_SIDE_ALIASES)}, got {side!r}") from None
    raise XetraOrderValidationError(f"side must be a string or int, got {type(side).__name__}")


def rts11_price_band_index(price: Decimal) -> int:
    """Index of the RTS 11 Annex price range containing ``price`` (0-based)."""
    for index, upper in enumerate(_RTS11_PRICE_BAND_UPPER_BOUNDS):
        if price < upper:
            return index
    return len(_RTS11_PRICE_BAND_UPPER_BOUNDS)


def rts11_tick_size(price: PriceInput, liquidity_band: int) -> Decimal:
    """Minimum tick size from the RTS 11 Annex for this price and liquidity band.

    Args:
        price: order limit price, in the instrument's quoting currency.
        liquidity_band: 1..6, the instrument's RTS 11 liquidity band. This is
            reference data published per instrument (ADNT on the most relevant
            market); it cannot be inferred from the price.

    Raises:
        XetraOrderValidationError: price is not a positive finite number, or the
            liquidity band is outside 1..6.
    """
    decimal_price = _to_decimal(price, "price")
    if decimal_price <= 0:
        raise XetraOrderValidationError(f"price must be > 0, got {decimal_price}")
    band = _require_int(liquidity_band, "liquidity_band",
                        minimum=MIN_LIQUIDITY_BAND, maximum=MAX_LIQUIDITY_BAND)
    return _RTS11_TICK_TABLE[rts11_price_band_index(decimal_price)][band - 1]


def describe_liquidity_band(liquidity_band: int) -> str:
    """Human-readable ADNT range for a liquidity band, for logs and reports."""
    band = _require_int(liquidity_band, "liquidity_band",
                        minimum=MIN_LIQUIDITY_BAND, maximum=MAX_LIQUIDITY_BAND)
    lower = _RTS11_ADNT_LOWER_BOUNDS[band - 1]
    if band == MAX_LIQUIDITY_BAND:
        return f"LB{band} (ADNT >= {lower})"
    return f"LB{band} (ADNT {lower} to < {_RTS11_ADNT_LOWER_BOUNDS[band]})"


def price_to_eti_int(price: PriceInput) -> int:
    """Encode a price as the ETI ``PriceType``: integer with 8 implied decimals.

    Raises:
        XetraOrderValidationError: the price needs more than 8 decimal places to
            represent exactly, or does not fit a signed 64-bit integer. Silently
            rounding here would send a different price than the caller asked for.
    """
    decimal_price = _to_decimal(price, "price")
    scaled = decimal_price * PRICE_SCALE
    if scaled != scaled.to_integral_value():
        raise XetraOrderValidationError(
            f"price {decimal_price} cannot be represented exactly with "
            f"{PRICE_SCALE_EXPONENT} decimals")
    as_int = int(scaled)
    if not _INT64_MIN <= as_int <= _INT64_MAX:
        raise XetraOrderValidationError(f"price {decimal_price} overflows a signed 64-bit integer")
    return as_int


@dataclass(frozen=True)
class T7EtiRequestHeader:
    """The 24-byte T7 ETI inbound request header.

    Layout, little endian, per the T7 ETI Cash Message Reference (unchanged from
    Release 5.0 through Release 14.0)::

        MessageHeaderIn : BodyLen (u32, ofs 0)      -- whole message, incl. this field
                          TemplateID (u16, ofs 4)
                          NetworkMsgID (8 bytes, ofs 6, unused)
                          Pad2 (2 bytes, ofs 14, unused)
        RequestHeader   : MsgSeqNum (u32, ofs 16)
                          SenderSubID (u32, ofs 20)  -- User ID

    There is no session identifier and no sending timestamp in this header. A
    session is established separately (``PartyIDSessionID`` is a body field of the
    Connection Gateway Request), and inbound requests carry no clock value.
    """

    body_len: int
    template_id: int
    msg_seq_num: int
    sender_sub_id: int
    network_msg_id: bytes = b"\x00" * 8

    def pack(self) -> bytes:
        """Serialise to the 24 wire bytes."""
        return _ETI_HEADER_STRUCT.pack(
            self.body_len, self.template_id, self.network_msg_id.ljust(8, b"\x00")[:8],
            b"\x00\x00", self.msg_seq_num, self.sender_sub_id)


@dataclass
class XetraOrderRequest:
    """A Xetra cash-market order, in the terms T7 ETI actually uses.

    ``isin`` is carried for human readability and is check-digit validated, but the
    wire identifies the instrument by ``security_id`` (tag 48) plus
    ``market_segment_id`` (tag 1300).
    """

    cl_ord_id: int                       # ClOrdID (tag 11), unsigned int
    isin: str                            # e.g. 'DE0007100000' (Mercedes-Benz Group AG)
    security_id: int                     # SecurityID (tag 48)
    market_segment_id: int               # MarketSegmentID (tag 1300)
    side: Union[str, int]                # 'BUY'/'SELL' or Side (tag 54) 1/2
    order_qty: int                       # OrderQty (tag 38)
    price_eur: PriceInput                # Price (tag 44); pass str/Decimal to stay exact
    #: The instrument's RTS 11 liquidity band (1..6) from venue reference data.
    #: Required: the tick size is not derivable from the price alone.
    liquidity_band: int
    trading_capacity: int                # TradingCapacity (tag 1815)
    #: OrderOrigination (tag 1724). Set to 5 for direct/sponsored access flow;
    #: leave None when the order is not DEA.
    order_origination: Optional[int] = None
    #: ExecutingTrader (tag 25123) short code, with its qualifier (tag 25124).
    executing_trader_short_code: Optional[int] = None
    executing_trader_qualifier: Optional[int] = None


@dataclass
class XetraOrderValidationReport:
    """Outcome of local pre-dispatch validation.

    ``ready_to_send`` means the order passed every check implemented here. It does
    not mean anything was transmitted -- this module has no transport.
    """

    cl_ord_id: int
    isin: str
    status: str
    ready_to_send: bool
    eti_header: Optional[T7EtiRequestHeader]
    rejection_reason: Optional[str]
    #: RTS 11 tick the price was checked against, once it could be determined.
    required_tick_size: Optional[Decimal] = None
    #: Price as it would be encoded in ETI PriceType (8 implied decimals).
    price_eti_int: Optional[int] = None
    side_wire_value: Optional[int] = None
    #: Warnings that do not block dispatch, e.g. use of a deprecated template.
    warnings: Optional[List[str]] = None


STATUS_OK: Final[str] = "STATUS_OK"
STATUS_INVALID_TICK_SIZE: Final[str] = "INVALID_TICK_SIZE"
STATUS_INVALID_ORDER_FIELD: Final[str] = "INVALID_ORDER_FIELD"


class DeutscheBorseXetraApiEngine:
    """Local pre-dispatch validator and T7 ETI header framer for Xetra orders.

    Validation order: field domains (side, quantity, price, capacity, short codes)
    are checked before the tick size, because an out-of-domain field makes the tick
    question meaningless.

    The engine owns the ETI request sequence number. ``MsgSeqNum`` must increase by
    exactly one per request on a session, so it is advanced only when a header is
    actually produced -- a rejected order consumes no sequence number and leaves no
    gap for the gateway to fault on.

    This class is not thread-safe. One instance per ETI session, driven by that
    session's single sender thread, matches how ETI sequence numbers work; sharing
    an instance across threads would interleave ``MsgSeqNum`` values.
    """

    def __init__(self, sender_sub_id: int, default_template_id: int = TEMPLATE_NEW_ORDER_SINGLE_OR_MULTI_LEG) -> None:
        """
        Args:
            sender_sub_id: the T7 User ID placed in ``SenderSubID``.
            default_template_id: template for order requests. Defaults to
                New Order Single or Multi Leg (10138) because the R14.0 change log
                schedules 10100 for decommissioning with ETI 14.1.
        """
        # Bounds match the wire widths: SenderSubID is u32, TemplateID is u16.
        # Catching this here beats an opaque struct.error at pack time.
        self.sender_sub_id = _require_int(sender_sub_id, "sender_sub_id",
                                          minimum=0, maximum=_UINT32_MAX)
        self.default_template_id = _require_int(default_template_id, "default_template_id",
                                                minimum=0, maximum=_UINT16_MAX)
        self._msg_seq_num = 0

    @property
    def msg_seq_num(self) -> int:
        """Sequence number of the most recently framed request (0 before the first)."""
        return self._msg_seq_num

    # -- tick size ------------------------------------------------------------

    def audit_xetra_tick_size(self, price_eur: PriceInput,
                              liquidity_band: int) -> Tuple[bool, Decimal]:
        """Check a price against the RTS 11 tick for its price and liquidity band.

        Returns ``(is_on_tick, required_tick_size)``.

        The check is exact decimal arithmetic. Passing a float is accepted but
        converted via its shortest repr; pass ``str`` or ``Decimal`` for prices that
        must round-trip exactly.

        Note the signature takes the liquidity band. Earlier versions of this skill
        inferred the tick from price alone, which cannot be correct: at €62.50 the
        RTS 11 tick is €0.01 in band 6 and €0.50 in band 1.
        """
        decimal_price = _to_decimal(price_eur, "price_eur")
        if decimal_price <= 0:
            raise XetraOrderValidationError(f"price_eur must be > 0, got {decimal_price}")
        tick = rts11_tick_size(decimal_price, liquidity_band)
        return (decimal_price % tick == 0), tick

    # -- header framing -------------------------------------------------------

    def format_t7_eti_header(self, body_len: int,
                             template_id: Optional[int] = None) -> T7EtiRequestHeader:
        """Advance ``MsgSeqNum`` and build the ETI request header.

        Args:
            body_len: total message length in bytes **including** the BodyLen field
                itself, i.e. 24 + len(message body). The ETI reference defines
                BodyLen as "Number of bytes for the message, including this field";
                passing a body-only length produces a message the gateway cannot
                frame.
            template_id: overrides ``default_template_id``.
        """
        resolved_template = self.default_template_id if template_id is None else _require_int(
            template_id, "template_id", minimum=0, maximum=_UINT16_MAX)
        _require_int(body_len, "body_len", minimum=ETI_REQUEST_HEADER_LEN, maximum=_UINT32_MAX)
        if self._msg_seq_num >= _UINT32_MAX:
            raise XetraOrderValidationError(
                "MsgSeqNum would exceed the 32-bit wire field; the session must be "
                "re-established rather than wrapping the sequence number")

        self._msg_seq_num += 1
        return T7EtiRequestHeader(
            body_len=body_len,
            template_id=resolved_template,
            msg_seq_num=self._msg_seq_num,
            sender_sub_id=self.sender_sub_id,
        )

    # -- order validation -----------------------------------------------------

    def process_xetra_order(self, req: XetraOrderRequest, body_len: int = 128,
                            template_id: Optional[int] = None) -> XetraOrderValidationReport:
        """Validate an order and, if it passes, frame its ETI request header.

        ``body_len`` must be the real total message length for the release and
        layout in use; the default is a placeholder for callers that only want the
        validation verdict.
        """
        if not isinstance(req, XetraOrderRequest):
            raise XetraOrderValidationError(
                f"req must be a XetraOrderRequest, got {type(req).__name__}")

        warnings: List[str] = []
        resolved_template = self.default_template_id if template_id is None else template_id
        replacement = DEPRECATED_TEMPLATE_REPLACEMENTS.get(resolved_template)
        if replacement is not None:
            warning = (f"Template {resolved_template} is listed for decommissioning with ETI "
                       f"version 14.1 (mid-2026); the change log names {replacement} as the "
                       f"replacement.")
            warnings.append(warning)
            logger.warning(warning)

        def _reject(status: str, message: str,
                    tick: Optional[Decimal] = None) -> XetraOrderValidationReport:
            logger.error("XETRA ORDER REJECTED [%s]: %s", req.cl_ord_id, message)
            return XetraOrderValidationReport(
                cl_ord_id=req.cl_ord_id, isin=str(req.isin), status=status,
                ready_to_send=False, eti_header=None, rejection_reason=message,
                required_tick_size=tick, warnings=warnings or None)

        # 1. Field domains. Checked before the tick size: an invalid quantity or
        #    side makes the tick question moot, and reporting the first real defect
        #    is more useful than reporting a downstream symptom.
        try:
            isin = validate_isin(req.isin)
            _require_int(req.cl_ord_id, "cl_ord_id", minimum=0)
            _require_int(req.security_id, "security_id")
            _require_int(req.market_segment_id, "market_segment_id")
            side_value = normalise_side(req.side)
            _require_int(req.order_qty, "order_qty", minimum=1)
            if req.trading_capacity not in _TRADING_CAPACITY_NAMES:
                raise XetraOrderValidationError(
                    f"trading_capacity must be one of "
                    f"{sorted(_TRADING_CAPACITY_NAMES)} (TradingCapacity tag 1815), "
                    f"got {req.trading_capacity!r}")
            if req.order_origination is not None and (
                    req.order_origination != ORDER_ORIGINATION_DIRECT_ACCESS_CUSTOMER):
                raise XetraOrderValidationError(
                    f"order_origination must be {ORDER_ORIGINATION_DIRECT_ACCESS_CUSTOMER} "
                    f"(direct access customer) or None, got {req.order_origination!r}")
            if req.executing_trader_short_code is not None:
                _require_int(req.executing_trader_short_code,
                             "executing_trader_short_code", minimum=0)
                if req.executing_trader_qualifier not in (
                        SHORT_CODE_QUALIFIER_ALGO, SHORT_CODE_QUALIFIER_HUMAN):
                    raise XetraOrderValidationError(
                        "executing_trader_qualifier must be "
                        f"{SHORT_CODE_QUALIFIER_ALGO} (Algo) or "
                        f"{SHORT_CODE_QUALIFIER_HUMAN} (Human) when an executing trader "
                        f"short code is supplied, got {req.executing_trader_qualifier!r}")
            price = _to_decimal(req.price_eur, "price_eur")
            if price <= 0:
                raise XetraOrderValidationError(f"price_eur must be > 0, got {price}")
            price_int = price_to_eti_int(price)
        except XetraOrderValidationError as exc:
            return _reject(STATUS_INVALID_ORDER_FIELD, str(exc))

        # 2. RTS 11 tick size.
        try:
            is_on_tick, tick = self.audit_xetra_tick_size(price, req.liquidity_band)
        except XetraOrderValidationError as exc:
            return _reject(STATUS_INVALID_ORDER_FIELD, str(exc))

        if not is_on_tick:
            message = (
                f"Price {price} is off-tick for {describe_liquidity_band(req.liquidity_band)}: "
                f"RTS 11 requires a multiple of {tick}. Xetra would surface this as "
                f"ExecRestatementReason 238 'Invalid limit price'.")
            return _reject(STATUS_INVALID_TICK_SIZE, message, tick)

        # 3. Frame the header. Only now is a sequence number consumed.
        header = self.format_t7_eti_header(body_len=body_len, template_id=template_id)
        logger.info(
            "XETRA ORDER VALIDATED [%s]: side=%d qty=%d %s @ %s (tick=%s, %s, "
            "capacity=%s, template=%d, seq=%d). Not yet sent.",
            req.cl_ord_id, side_value, req.order_qty, isin, price, tick,
            describe_liquidity_band(req.liquidity_band),
            _TRADING_CAPACITY_NAMES[req.trading_capacity], header.template_id,
            header.msg_seq_num)

        return XetraOrderValidationReport(
            cl_ord_id=req.cl_ord_id,
            isin=isin,
            status=STATUS_OK,
            ready_to_send=True,
            eti_header=header,
            rejection_reason=None,
            required_tick_size=tick,
            price_eti_int=price_int,
            side_wire_value=side_value,
            warnings=warnings or None,
        )

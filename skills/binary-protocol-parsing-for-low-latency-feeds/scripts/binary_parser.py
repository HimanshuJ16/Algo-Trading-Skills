"""
binary-protocol-parsing-for-low-latency-feeds: fixed-layout binary struct
unpacker for length-framed market data feeds, using the Nasdaq TotalView-ITCH
5.0 Add Order message as the worked example.

Engineered for quantitative trading environments where a *silent* misparse is
far more damaging than a loud failure: a frame decoded against the wrong layout
yields a plausible-looking message that corrupts order book state downstream.
Every decode path here therefore validates before it returns.

Endianness note: ITCH is big-endian (`>`). This is NOT universal for binary
market data -- CME MDP 3.0 uses Simple Binary Encoding, which is little-endian.
See references/standards.md before reusing this layout for another venue.
"""
from dataclasses import dataclass
import logging
import struct

logger = logging.getLogger(__name__)

# ITCH field-width bounds, used to reject out-of-range values at encode time
# rather than surfacing a raw struct.error from deep inside the pack call.
_UINT16_MAX = 2**16 - 1
_UINT32_MAX = 2**32 - 1
_UINT48_MAX = 2**48 - 1
_UINT64_MAX = 2**64 - 1

_STOCK_FIELD_WIDTH = 8
_VALID_SIDES = frozenset({"B", "S"})


class ITCHFrameError(ValueError):
    """
    Raised when a byte frame cannot be decoded as the expected ITCH message.

    Subclasses ValueError so existing callers written against the previous
    ``except ValueError`` contract keep working unchanged.
    """


@dataclass(slots=True)
class ITCHAddOrderMessage:
    """
    A Nasdaq ITCH 5.0 Add Order -- No MPID Attribution (message type 'A').

    Uses __slots__ for reduced memory footprint and faster attribute access.

    ``price_ticks`` is the authoritative price: the raw unsigned integer as it
    appeared on the wire, in units of 1/10,000 of a currency unit. Keep
    comparisons, aggregation and book keying on this integer. The ``price``
    property is a float convenience for downstream models that require one.
    """

    message_type: str        # 'A' (1 byte)
    stock_locate: int        # uint16 (2 bytes)
    tracking_number: int     # uint16 (2 bytes)
    timestamp_ns: int        # uint48 (6 bytes), nanoseconds since midnight
    order_ref_id: int        # uint64 (8 bytes)
    buy_sell: str            # 'B' or 'S' (1 byte)
    shares: int              # uint32 (4 bytes)
    stock: str               # char[8] (8 bytes), left-justified, space-padded
    price_ticks: int         # uint32 (4 bytes), scaled by 10,000

    @property
    def price(self) -> float:
        """
        Decimal price derived from ``price_ticks``.

        Lossless over the full ITCH domain (uint32 ticks / 10,000 is exactly
        representable in IEEE-754 double), but float arithmetic downstream is
        not associative -- prefer ``price_ticks`` for summation and equality.
        """
        return self.price_ticks / BinaryFeedParserEngine.PRICE_SCALE_FACTOR


class BinaryFeedParserEngine:
    """
    Fixed-layout binary parser for Nasdaq ITCH 5.0 Add Order messages.

    Pre-compiles the struct definition once at class creation so no format
    string is re-parsed on the hot path.
    """

    # Price scale factor per the ITCH specification (prices are integers
    # scaled by 10,000). Held as an int so tick arithmetic stays exact.
    PRICE_SCALE_FACTOR = 10000

    # The message type this engine decodes. Frames carrying any other type
    # byte are rejected rather than decoded against this layout.
    MESSAGE_TYPE = "A"

    # ITCH 5.0 Add Order 'A' layout, 36 bytes total:
    # MsgType(c) StockLocate(H) Tracking(H) Timestamp(6s) OrderRef(Q)
    # BuySell(c) Shares(I) Stock(8s) Price(I)
    # 1 + 2 + 2 + 6 + 8 + 1 + 4 + 8 + 4 = 36
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
        Pack an Add Order ('A') message into a 36-byte ITCH binary frame.

        This is a test/simulation helper, not a hot-path encoder: it validates
        every field so that malformed fixtures fail loudly at the point of
        construction instead of producing frames that decode into nonsense.

        ``price`` is accepted as a decimal and rounded to the nearest tick via
        Python's round-half-to-even, so an exact half tick rounds to the even
        neighbour (0.00005 -> 0 ticks, 0.00015 -> 1 tick). Production encoders
        should carry integer ticks end to end rather than rely on this.

        Raises:
            ValueError: if any field is out of range for its ITCH field width,
                the side is not 'B'/'S', or the symbol is empty, non-ASCII, or
                longer than 8 characters.
        """
        cls._validate_uint(stock_locate, _UINT16_MAX, "stock_locate")
        cls._validate_uint(tracking_number, _UINT16_MAX, "tracking_number")
        cls._validate_uint(timestamp_ns, _UINT48_MAX, "timestamp_ns")
        cls._validate_uint(order_ref_id, _UINT64_MAX, "order_ref_id")
        cls._validate_uint(shares, _UINT32_MAX, "shares")

        side = buy_sell.upper()
        if side not in _VALID_SIDES:
            raise ValueError(
                f"buy_sell must be 'B' or 'S', got {buy_sell!r}")

        symbol = stock.upper()
        if not symbol or len(symbol) > _STOCK_FIELD_WIDTH:
            # Silent truncation here would mint frames for a *different*
            # instrument than the caller named.
            raise ValueError(
                f"stock must be 1-{_STOCK_FIELD_WIDTH} characters, "
                f"got {stock!r} ({len(symbol)} chars)")
        try:
            stock_bytes = symbol.ljust(_STOCK_FIELD_WIDTH).encode("ascii")
        except UnicodeEncodeError as exc:
            raise ValueError(
                f"stock must be ASCII, got {stock!r}") from exc

        price_ticks = int(round(price * cls.PRICE_SCALE_FACTOR))
        cls._validate_uint(price_ticks, _UINT32_MAX, "price (in ticks)")

        return cls.ADD_ORDER_FORMAT.pack(
            cls.MESSAGE_TYPE.encode("ascii"),
            stock_locate,
            tracking_number,
            timestamp_ns.to_bytes(6, byteorder="big", signed=False),
            order_ref_id,
            side.encode("ascii"),
            shares,
            stock_bytes,
            price_ticks,
        )

    @classmethod
    def unpack_itch_add_order(
        cls,
        raw_bytes: memoryview,
        offset: int = 0,
    ) -> ITCHAddOrderMessage:
        """
        Unpack one 36-byte ITCH Add Order frame at ``offset`` within a buffer.

        Accepts any buffer-protocol object. Pass a ``memoryview`` over the
        whole receive buffer together with ``offset`` to walk a packet
        containing several concatenated messages without slicing -- slicing a
        ``bytes`` object copies, which is exactly what the zero-copy ingestion
        path is trying to avoid.

        The caller is responsible for dispatching on the leading type byte;
        this method independently re-checks it so that a dispatch bug or a
        misaligned offset raises instead of silently decoding a different
        message type against the Add Order layout.

        Raises:
            ITCHFrameError: if the buffer is too short, the offset is
                negative, the type byte is not 'A', the frame contains
                non-ASCII bytes in character fields, or the side byte is
                neither 'B' nor 'S'.
        """
        if offset < 0:
            raise ITCHFrameError(f"Negative buffer offset: {offset}")

        available = max(0, len(raw_bytes) - offset)
        if available < cls.ADD_ORDER_FORMAT.size:
            raise ITCHFrameError(
                f"Invalid ITCH frame size: {available} bytes at offset "
                f"{offset} (Expected {cls.ADD_ORDER_FORMAT.size} bytes)")

        (
            msg_type_b,
            locate,
            track,
            ts_bytes,
            ref_id,
            bs_b,
            shares,
            stock_b,
            price_ticks,
        ) = cls.ADD_ORDER_FORMAT.unpack_from(raw_bytes, offset)

        # Character fields are decoded defensively. A misaligned or corrupt
        # frame otherwise surfaces a bare UnicodeDecodeError carrying no frame
        # position -- technically a ValueError, but indistinguishable from an
        # argument error and useless for locating the bad offset in a packet.
        try:
            message_type = msg_type_b.decode("ascii")
            buy_sell = bs_b.decode("ascii")
            stock = stock_b.decode("ascii")
        except UnicodeDecodeError as exc:
            raise ITCHFrameError(
                f"Non-ASCII bytes in ITCH character field at offset "
                f"{offset}; frame is corrupt or misaligned") from exc

        if message_type != cls.MESSAGE_TYPE:
            raise ITCHFrameError(
                f"Expected ITCH message type {cls.MESSAGE_TYPE!r} at offset "
                f"{offset}, got {message_type!r}; refusing to decode a "
                f"different message against the Add Order layout")

        if buy_sell not in _VALID_SIDES:
            raise ITCHFrameError(
                f"Invalid ITCH Buy/Sell indicator {buy_sell!r} at offset "
                f"{offset}; expected 'B' or 'S'")

        return ITCHAddOrderMessage(
            message_type=message_type,
            stock_locate=locate,
            tracking_number=track,
            # 48-bit big-endian timestamp: nanoseconds since midnight.
            timestamp_ns=int.from_bytes(ts_bytes, byteorder="big", signed=False),
            order_ref_id=ref_id,
            buy_sell=buy_sell,
            shares=shares,
            # ITCH alpha fields are left-justified and space-padded.
            stock=stock.rstrip(" "),
            price_ticks=price_ticks,
        )

    @staticmethod
    def _validate_uint(value: int, maximum: int, field_name: str) -> None:
        """Reject non-integer, negative, or too-wide values for a uint field."""
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                f"{field_name} must be an int, got {type(value).__name__}")
        if not 0 <= value <= maximum:
            raise ValueError(
                f"{field_name} out of range for its ITCH field width: "
                f"{value} (expected 0..{maximum})")

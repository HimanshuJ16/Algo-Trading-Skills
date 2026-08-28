"""
new-zealand-exchange-nzx-api:
NZX Main Board (NZSX) order-entry helper: price-step (tick size) enforcement,
session-phase awareness, FIX NewOrderSingle / OrderCancelRequest serialisation,
and ExecutionReport decoding.

Scope and evidence
------------------
This module owns the NZX-specific decisions that are easy to get wrong:

  * the NZX price-step schedule, including the funds carve-out;
  * the NZSX session timetable in Pacific/Auckland wall-clock time;
  * strict order-field validation, so a malformed field can never be silently
    coerced into a different (and tradeable) order;
  * correctly framed FIX messages (BodyLength, CheckSum, UTCTimestamp).

It deliberately does NOT own the FIX *session* layer: no sockets, no Logon/
Logout, no heartbeating, no sequence-number persistence, no resend handling.
Those belong to a real FIX engine - see `fix-protocol-session-management-across-venues`.
The caller supplies MsgSeqNum (34) from that engine.

Sourcing caveats (read before deploying):

  * The price-step table below is transcribed from NZX's published "Trading
    Information / Price Steps" material. NZX Participant Rule 11.9.1 states that
    minimum price changes "may be specified by NZX from time to time", so the
    schedule is NOT frozen in the Rules. Treat `NZXTickSchedule` as configuration
    to be reconciled against the current NZX notice, not as a constant.
  * `NZXFixSessionConfig` has NO default SenderCompID/TargetCompID/BeginString.
    NZX's order-entry FIX specification is distributed to Participants via the
    Participant Portal and is not public; publicly available descriptions
    indicate NZX runs a Nasdaq matching engine with FIX order entry and ITCH
    market data. Take the exact BeginString and CompIDs from the FIX
    specification NZX issued to your firm. Do not guess them.
  * Session-phase boundaries are the published nominal times. The opening and
    closing auctions fire at a random instant within +/-30s of 10:00 and 17:00
    respectively, so `NZXSessionSchedule` is advisory: the exchange session-state
    message is authoritative.
  * `NZXSessionSchedule` is time-of-day only. It knows nothing about NZ public
    holidays or half-days - gate on a trading calendar as well
    (see `global-exchange-holiday-calendar-handling`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from typing import Callable, Dict, List, Mapping, Optional, Tuple, Union

logger = logging.getLogger(__name__)

#: FIX field separator. Real sessions MUST use SOH; '|' is for logs and tests only.
SOH = "\x01"

#: NZX prices are meaningful to three decimals at most. Caller-supplied floats are
#: normalised to this many places before tick validation so that ordinary binary
#: float noise (0.1 + 0.2 -> 0.30000000000000004) is not mistaken for a sub-tick
#: violation. Six places is far finer than the smallest NZX step (0.001), so a
#: genuine violation can never be rounded away.
_PRICE_NORMALISATION = Decimal("0.000001")

Number = Union[int, float, str, Decimal]


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #
class NZXSecurityType(Enum):
    """
    Price-step class of an NZX-quoted security.

    NZX publishes two distinct price-step regimes for the Main Board:
      * FUND    - every listed fund ticks at $0.001 regardless of price;
      * EQUITY  - every other security uses the price-dependent band schedule.

    DEBT_YIELD_QUOTED covers NZX Debt Market securities quoted in yield (minimum
    yield change 0.005%). This module validates *price* steps and therefore
    refuses to validate yield-quoted debt rather than applying the wrong rule.
    NZDX hybrids quoted as a price per $100 follow the standard price steps and
    should be passed as EQUITY.
    """
    EQUITY = "EQUITY"
    FUND = "FUND"
    DEBT_YIELD_QUOTED = "DEBT_YIELD_QUOTED"


class NZXSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class NZXOrderType(Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"


class NZXTimeInForce(Enum):
    """
    Time-in-force values this module serialises.

    NZX also documents Good-til-Date, which requires ExpireDate (432) /
    ExpireTime (126). That field is not modelled here, so GTD is rejected
    explicitly rather than silently downgraded to DAY.
    """
    DAY = "DAY"
    GTC = "GTC"
    IOC = "IOC"   # NZX "fill-and-kill"
    FOK = "FOK"   # NZX "all or nothing" / fill-or-kill


class NZXMarketPhase(Enum):
    """NZSX cash-market session states (Pacific/Auckland wall-clock)."""
    ENQUIRY = "ENQUIRY"                    # 17:30 - 08:30, read-only
    PRE_OPEN = "PRE_OPEN"                  # 08:30 - opening auction, no matching
    OPENING_AUCTION = "OPENING_AUCTION"    # randomised within 09:59:30 - 10:00:30
    NORMAL = "NORMAL"                      # ~10:00 - 16:45, continuous matching
    PRE_CLOSE = "PRE_CLOSE"                # 16:45 - closing auction, no matching
    CLOSING_AUCTION = "CLOSING_AUCTION"    # randomised within 16:59:30 - 17:00:30
    ADJUST = "ADJUST"                      # ~17:00 - 17:30, amend/withdraw only


# FIX enum encodings (FIX 4.x / 5.x share these values for the tags used here).
_SIDE_FIX: Dict[NZXSide, str] = {NZXSide.BUY: "1", NZXSide.SELL: "2"}
_ORD_TYPE_FIX: Dict[NZXOrderType, str] = {NZXOrderType.MARKET: "1", NZXOrderType.LIMIT: "2"}
_TIF_FIX: Dict[NZXTimeInForce, str] = {
    NZXTimeInForce.DAY: "0",
    NZXTimeInForce.GTC: "1",
    NZXTimeInForce.IOC: "3",
    NZXTimeInForce.FOK: "4",
}

#: OrdStatus (39) values, FIX 4.4 Appendix A.
ORD_STATUS_NAMES: Mapping[str, str] = {
    "0": "NEW",
    "1": "PARTIALLY_FILLED",
    "2": "FILLED",
    "3": "DONE_FOR_DAY",
    "4": "CANCELED",
    "5": "REPLACED",
    "6": "PENDING_CANCEL",
    "7": "STOPPED",
    "8": "REJECTED",
    "9": "SUSPENDED",
    "A": "PENDING_NEW",
    "B": "CALCULATED",
    "C": "EXPIRED",
    "D": "ACCEPTED_FOR_BIDDING",
    "E": "PENDING_REPLACE",
}


# --------------------------------------------------------------------------- #
# Price steps
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class NZXTickSchedule:
    """
    NZX Main Board price steps.

    ``equity_bands`` is an ascending sequence of ``(upper_bound_exclusive, step)``
    pairs; the first band whose upper bound exceeds the price supplies the step.
    ``equity_top_step`` applies at and above the last bound.

    Defaults transcribe NZX's published schedule:

        Funds (any price)     $0.001
        Up to $0.19           $0.001
        $0.20 to $1.995       $0.005
        Above $2.00           $0.01

    The $1.995/$2.00 gap in NZX's wording is immaterial in practice: no price
    strictly between them is a multiple of $0.005, and $2.00 itself is a valid
    multiple of both $0.005 and $0.01, so both readings of the boundary accept
    and reject exactly the same set of prices.

    Rule 11.9.1 lets NZX respecify these steps at any time - reconcile against
    the current NZX notice rather than trusting this default indefinitely.
    """
    equity_bands: Tuple[Tuple[Decimal, Decimal], ...] = (
        (Decimal("0.20"), Decimal("0.001")),
        (Decimal("2.00"), Decimal("0.005")),
    )
    equity_top_step: Decimal = Decimal("0.01")
    fund_step: Decimal = Decimal("0.001")

    def step_for(self, price: Decimal, security_type: NZXSecurityType) -> Decimal:
        """Minimum price step for `price`, given the security's price-step class."""
        if security_type is NZXSecurityType.DEBT_YIELD_QUOTED:
            raise ValueError(
                "NZX Debt Market securities quoted in yield use a minimum yield "
                "change (0.005%), not a price step. This engine validates price "
                "steps only. Pass NZDX hybrids quoted per $100 as EQUITY."
            )
        if security_type is NZXSecurityType.FUND:
            return self.fund_step
        for upper_exclusive, step in self.equity_bands:
            if price < upper_exclusive:
                return step
        return self.equity_top_step


DEFAULT_TICK_SCHEDULE = NZXTickSchedule()


# --------------------------------------------------------------------------- #
# Session schedule
# --------------------------------------------------------------------------- #
_PRE_OPEN_START = time(8, 30)
_OPENING_AUCTION_START = time(9, 59, 30)      # randomised window opens
_OPENING_AUCTION_END = time(10, 0, 30)        # randomised window closes
_PRE_CLOSE_START = time(16, 45)
_CLOSING_AUCTION_START = time(16, 59, 30)
_CLOSING_AUCTION_END = time(17, 0, 30)
_ADJUST_END = time(17, 30)

#: Phases in which NZX accepts *new* orders. Per NZX's session/action matrix,
#: order entry is available in Pre-Open, Normal Trading and Pre-Close, and is
#: NOT available in Enquiry or Adjust. The instantaneous auction matches are
#: treated as restricted here, which is the conservative reading.
_ORDER_ENTRY_PHASES = frozenset(
    {NZXMarketPhase.PRE_OPEN, NZXMarketPhase.NORMAL, NZXMarketPhase.PRE_CLOSE}
)

#: Phases in which an existing order may be withdrawn. Adjust permits amend and
#: withdraw but not new orders; Enquiry permits nothing.
_CANCEL_PHASES = frozenset(_ORDER_ENTRY_PHASES | {NZXMarketPhase.ADJUST})


def _as_auckland_wallclock(dt: datetime) -> time:
    """
    Return the wall-clock time-of-day of `dt` expressed in Pacific/Auckland time.

    A tz-aware datetime is converted; a naive datetime is assumed to already be
    Auckland wall-clock, which keeps offline tests and backtests deterministic.
    """
    if dt.tzinfo is not None:
        try:
            from zoneinfo import ZoneInfo
            dt = dt.astimezone(ZoneInfo("Pacific/Auckland"))
        except Exception:  # pragma: no cover - only when the host lacks IANA tzdata
            # Approximate as NZST (UTC+12). Production hosts must ship tzdata;
            # this fallback exists so a missing-tzdata host degrades visibly
            # rather than raising deep inside order construction.
            logger.warning(
                "IANA tzdata unavailable; approximating Pacific/Auckland as UTC+12. "
                "NZDT (UTC+13) periods will be misclassified by one hour."
            )
            dt = (dt.astimezone(timezone.utc) + timedelta(hours=12)).replace(tzinfo=None)
    return dt.time()


class NZXSessionSchedule:
    """
    Maps an Auckland wall-clock instant to an NZSX cash-market phase.

    Advisory only. The opening and closing auctions fire at a random instant
    within a 60-second window, and NZX may vary session lengths, so production
    code must gate on the exchange's own session-state message. This helper is
    for scheduling, alerting and catching timezone bugs - not for racing the
    auction.
    """

    @staticmethod
    def phase_at(dt: datetime) -> NZXMarketPhase:
        t = _as_auckland_wallclock(dt)
        if t < _PRE_OPEN_START:
            return NZXMarketPhase.ENQUIRY
        if t < _OPENING_AUCTION_START:
            return NZXMarketPhase.PRE_OPEN
        if t < _OPENING_AUCTION_END:
            return NZXMarketPhase.OPENING_AUCTION
        if t < _PRE_CLOSE_START:
            return NZXMarketPhase.NORMAL
        if t < _CLOSING_AUCTION_START:
            return NZXMarketPhase.PRE_CLOSE
        if t < _CLOSING_AUCTION_END:
            return NZXMarketPhase.CLOSING_AUCTION
        if t < _ADJUST_END:
            return NZXMarketPhase.ADJUST
        return NZXMarketPhase.ENQUIRY

    @staticmethod
    def is_order_entry_window(dt: datetime) -> bool:
        """True in phases where NZX accepts new orders (Pre-Open, Normal, Pre-Close)."""
        return NZXSessionSchedule.phase_at(dt) in _ORDER_ENTRY_PHASES

    @staticmethod
    def is_cancel_window(dt: datetime) -> bool:
        """True in phases where an existing order may be withdrawn (adds Adjust)."""
        return NZXSessionSchedule.phase_at(dt) in _CANCEL_PHASES


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class NZXFixSessionConfig:
    """
    FIX session identity, taken from the specification NZX issued to your firm.

    There are deliberately no defaults. Publishing a guessed TargetCompID or
    BeginString would be worse than requiring the caller to look them up: a
    plausible-but-wrong value fails at Logon in the best case and routes to the
    wrong destination in the worst.
    """
    sender_comp_id: str
    target_comp_id: str
    begin_string: str
    sender_sub_id: Optional[str] = None
    target_sub_id: Optional[str] = None

    def __post_init__(self) -> None:
        for name in ("sender_comp_id", "target_comp_id", "begin_string"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"NZXFixSessionConfig.{name} must be a non-empty string")
            _reject_fix_metacharacters(value, f"NZXFixSessionConfig.{name}")
        for name in ("sender_sub_id", "target_sub_id"):
            value = getattr(self, name)
            if value is not None:
                _reject_fix_metacharacters(value, f"NZXFixSessionConfig.{name}")


@dataclass
class NZXOrderRequest:
    """
    An NZX Main Board order, before FIX serialisation.

    `side`, `order_type` and `time_in_force` accept either the enum or its string
    name; anything else is rejected rather than coerced. `price` may be None for
    a market order. Pass `price` as a str or Decimal when exactness matters.
    """
    cl_ord_id: str
    symbol: str                                    # NZX ticker, e.g. 'FPH' (not 'FPH.NZ')
    side: Union[str, NZXSide]
    quantity: int
    price: Optional[Number]                        # limit price in NZD; None for market
    order_type: Union[str, NZXOrderType]
    time_in_force: Union[str, NZXTimeInForce]
    #: Defaults to EQUITY because that is the fail-closed choice: a fund misread
    #: as an equity gets its valid $0.001 price REJECTED, whereas an equity
    #: misread as a fund would have a sub-tick price ACCEPTED and sent.
    security_type: Union[str, NZXSecurityType] = NZXSecurityType.EQUITY


@dataclass
class NZXOrderReport:
    """
    Outcome of building an NZX FIX message.

    `fix_msg_type` is the MsgType of the message actually produced ('D' or 'F').
    It is the empty string for a locally rejected order, because no FIX message
    was built and none was sent - the exchange never saw the order.
    """
    cl_ord_id: str
    symbol: str
    status: str                                    # 'NEW', 'PENDING_CANCEL', 'REJECTED'
    fix_msg_type: str
    fix_raw_payload: str
    audit_notes: str
    rejection_reason: Optional[str] = None
    normalized_price: Optional[Decimal] = None     # price actually placed in tag 44
    tick_size: Optional[Decimal] = None            # step the price was validated against


@dataclass
class NZXExecutionReport:
    """
    Decoded FIX ExecutionReport (35=8).

    Cumulative state must be taken from `cum_qty` / `leaves_qty` / `avg_px`.
    Never accumulate `last_qty` yourself: ExecutionReports can be replayed after
    a reconnect (`poss_dup` is True on a resend), and summing per-fill
    quantities across a duplicate double-counts the position.
    """
    cl_ord_id: str
    exec_id: str
    ord_status: str                                # raw FIX value, e.g. '1'
    ord_status_name: str                           # e.g. 'PARTIALLY_FILLED'
    order_id: Optional[str] = None
    exec_type: Optional[str] = None
    symbol: Optional[str] = None
    side: Optional[str] = None
    last_qty: Optional[Decimal] = None             # tag 32 (LastShares in FIX 4.2)
    last_px: Optional[Decimal] = None              # tag 31
    cum_qty: Optional[Decimal] = None              # tag 14
    leaves_qty: Optional[Decimal] = None           # tag 151
    avg_px: Optional[Decimal] = None               # tag 6
    currency: Optional[str] = None                 # tag 15
    ord_rej_reason: Optional[str] = None           # tag 103
    text: Optional[str] = None                     # tag 58
    poss_dup: bool = False                         # tag 43 == 'Y'
    raw_tags: Optional[Dict[int, str]] = None      # first occurrence of each tag

    def __post_init__(self) -> None:
        if self.raw_tags is None:
            self.raw_tags = {}


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #
def _reject_fix_metacharacters(value: str, label: str) -> None:
    """
    Refuse values that could inject or truncate FIX fields.

    A ClOrdID or Symbol taken from an upstream system and pasted straight into a
    tag=value string is a field-injection vector: an embedded SOH ends the field
    and everything after it is parsed as new tags. Non-ASCII is refused too,
    since BodyLength and CheckSum are byte counts.
    """
    if SOH in value or "=" in value:
        raise ValueError(f"{label} must not contain SOH or '=' (FIX field injection risk)")
    if "|" in value:
        raise ValueError(f"{label} must not contain '|' (reserved as a readable delimiter)")
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        raise ValueError(f"{label} must be ASCII; FIX BodyLength/CheckSum are byte counts")


def _coerce_enum(value: Union[str, Enum], enum_cls, label: str):
    """Accept an enum member or its exact name (case-insensitive); reject anything else."""
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls[value.strip().upper()]
        except KeyError:
            pass
    valid = ", ".join(m.name for m in enum_cls)
    raise ValueError(f"{label} {value!r} is not valid; expected one of: {valid}")


def _to_decimal(value: Number, label: str) -> Decimal:
    """Convert via str() so a float's shortest repr is used, not its binary expansion."""
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{label} {value!r} is not a valid decimal number")


def _normalize_price(value: Number, label: str) -> Decimal:
    price = _to_decimal(value, label)
    if not price.is_finite():
        raise ValueError(f"{label} must be finite, got {value!r}")
    try:
        return price.quantize(_PRICE_NORMALISATION, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        # quantize() raises once the result would exceed the decimal context's
        # precision - i.e. an absurd price such as 1e30. Surface it as a normal
        # validation failure so it becomes a rejected order rather than an
        # exception escaping the order builder.
        raise ValueError(
            f"{label} {value!r} is outside the representable range for an NZX price"
        )


def _format_decimal(value: Decimal) -> str:
    """Plain decimal string with no exponent and no trailing-zero padding."""
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    return format(normalized, "f")


def _utc_timestamp(dt: datetime) -> str:
    """FIX UTCTimestamp: 'YYYYMMDD-HH:MM:SS.sss' in UTC (FIX 4.4 Appendix A)."""
    if dt.tzinfo is None:
        raise ValueError(
            "FIX timestamps must be unambiguous: pass a timezone-aware datetime. "
            "A naive datetime would be silently treated as UTC and could place a "
            "TransactTime up to 13 hours away from the true instant."
        )
    utc = dt.astimezone(timezone.utc)
    return f"{utc:%Y%m%d-%H:%M:%S}.{utc.microsecond // 1000:03d}"


def _fix_checksum(prefix: str) -> str:
    """
    CheckSum (10): sum of all bytes before the CheckSum field, modulo 256.

    FIX checksums are defined over the wire bytes. Outbound values are restricted
    to ASCII, but an inbound message can legitimately carry a non-ASCII byte in a
    free-text field such as Text (58), so `latin-1` is used to map each code point
    back to its single wire byte. A code point above 0xFF means the payload was
    decoded with a codec that did not preserve the byte count, in which case the
    checksum simply cannot be recomputed from the string.
    """
    try:
        return f"{sum(prefix.encode('latin-1')) % 256:03d}"
    except UnicodeEncodeError:
        raise ValueError(
            "Cannot verify CheckSum: this payload was decoded with a codec that does "
            "not preserve the wire byte count. Verify the checksum on the raw bytes, "
            "or pass verify_checksum=False and validate integrity upstream."
        )


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #
class NewZealandExchangeNZXEngine:
    """
    NZX Main Board order-entry helper: price-step enforcement, session-phase
    awareness, FIX NewOrderSingle (35=D) / OrderCancelRequest (35=F)
    serialisation, and ExecutionReport (35=8) decoding.

    Construction requires an `NZXFixSessionConfig` because the CompIDs and
    BeginString come from NZX's Participant FIX specification and must not be
    guessed. Supply `clock` and `seq_num_provider` to make output deterministic
    in tests and to bind sequence numbers to your FIX engine.
    """

    def __init__(
        self,
        session: NZXFixSessionConfig,
        tick_schedule: NZXTickSchedule = DEFAULT_TICK_SCHEDULE,
        clock: Optional[Callable[[], datetime]] = None,
        seq_num_provider: Optional[Callable[[], int]] = None,
        field_delimiter: str = SOH,
    ) -> None:
        if not isinstance(session, NZXFixSessionConfig):
            raise TypeError("session must be an NZXFixSessionConfig")
        if field_delimiter not in (SOH, "|"):
            raise ValueError("field_delimiter must be SOH (wire) or '|' (logs/tests)")
        self.session = session
        self.tick_schedule = tick_schedule
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._seq_num_provider = seq_num_provider
        self.field_delimiter = field_delimiter

    # -- price steps -------------------------------------------------------- #
    def get_nzx_tick_size(
        self,
        price: Number,
        security_type: Union[str, NZXSecurityType] = NZXSecurityType.EQUITY,
    ) -> Decimal:
        """
        Minimum price step for `price` under the configured schedule.

        Listed funds tick at $0.001 at every price level; all other securities
        use the price-dependent bands. Raises ValueError for a yield-quoted debt
        security, which has no price step.
        """
        sec = _coerce_enum(security_type, NZXSecurityType, "security_type")
        return self.tick_schedule.step_for(_normalize_price(price, "price"), sec)

    def validate_price_tick(
        self,
        price: Number,
        security_type: Union[str, NZXSecurityType] = NZXSecurityType.EQUITY,
    ) -> Tuple[bool, Decimal]:
        """
        Check `price` against the NZX price-step schedule.

        Returns ``(is_valid, step)``. The comparison is exact Decimal arithmetic
        - no float modulo and no tolerance window, so a price one thousandth
        below a tick boundary cannot round its way into a valid order.

        A non-positive price is never valid: NZX quotes no security at or below
        zero, and a zero limit price on a sell order would be an instant
        worst-case fill.
        """
        sec = _coerce_enum(security_type, NZXSecurityType, "security_type")
        normalized = _normalize_price(price, "price")
        step = self.tick_schedule.step_for(normalized, sec)
        if normalized <= 0:
            return False, step
        return normalized % step == 0, step

    # -- session ------------------------------------------------------------ #
    @staticmethod
    def market_phase(dt: datetime) -> NZXMarketPhase:
        """NZSX phase at `dt` (advisory - see `NZXSessionSchedule`)."""
        return NZXSessionSchedule.phase_at(dt)

    @staticmethod
    def is_order_entry_window(dt: datetime) -> bool:
        """True when NZX accepts new orders at `dt` (advisory)."""
        return NZXSessionSchedule.is_order_entry_window(dt)

    # -- FIX construction --------------------------------------------------- #
    def build_fix_new_order_single(
        self,
        order: NZXOrderRequest,
        seq_num: Optional[int] = None,
        at_time: Optional[datetime] = None,
    ) -> NZXOrderReport:
        """
        Validate an order and build a framed FIX NewOrderSingle (35=D).

        Every field is validated strictly. An unrecognised side, order type,
        time-in-force, quantity or price produces a REJECTED report - it is never
        coerced into a valid-looking alternative, because coercion here means
        sending a real order that differs from the one the caller asked for.

        Price (44) is emitted only for LIMIT orders: it is meaningful only for
        limit order types, and venues commonly reject a market order that carries
        one. A MARKET order is not tick-validated, because it has no limit price.

        If `at_time` is supplied, the order is additionally gated on the NZSX
        session phase. When it is omitted no session check is performed - this
        method builds a message, it does not send one, and the caller owns the
        send-time gate (`is_order_entry_window`).

        Raises ValueError if `cl_ord_id` or `symbol` is unusable, since there is
        then no safe identity to echo back in a rejection report.
        """
        cl_ord_id = self._require_identifier(order.cl_ord_id, "cl_ord_id")
        symbol = self._require_symbol(order.symbol)

        try:
            side = _coerce_enum(order.side, NZXSide, "side")
            ord_type = _coerce_enum(order.order_type, NZXOrderType, "order_type")
            tif = _coerce_enum(order.time_in_force, NZXTimeInForce, "time_in_force")
            sec_type = _coerce_enum(order.security_type, NZXSecurityType, "security_type")
            quantity = self._require_quantity(order.quantity)
        except ValueError as exc:
            return self._reject(cl_ord_id, symbol, str(exc))

        if sec_type is NZXSecurityType.DEBT_YIELD_QUOTED:
            return self._reject(
                cl_ord_id, symbol,
                "NZX Debt Market securities quoted in yield are out of scope for this "
                "engine: they use a minimum yield change (0.005%), not a price step. "
                "Route them through a yield-aware adapter."
            )

        if at_time is not None and not NZXSessionSchedule.is_order_entry_window(at_time):
            return self._reject(
                cl_ord_id, symbol,
                f"NZSX phase {NZXSessionSchedule.phase_at(at_time).value} does not accept "
                f"new orders (order entry is available in Pre-Open, Normal Trading and "
                f"Pre-Close). Confirm against the exchange session-state message."
            )

        normalized_price: Optional[Decimal] = None
        step: Optional[Decimal] = None
        if ord_type is NZXOrderType.LIMIT:
            if order.price is None:
                return self._reject(cl_ord_id, symbol, "A LIMIT order requires a price")
            try:
                normalized_price = _normalize_price(order.price, "price")
            except ValueError as exc:
                return self._reject(cl_ord_id, symbol, str(exc))
            is_valid_tick, step = self.validate_price_tick(normalized_price, sec_type)
            if not is_valid_tick:
                return self._reject(
                    cl_ord_id, symbol,
                    f"Price {_format_decimal(normalized_price)} NZD violates the NZX price "
                    f"step schedule for a {sec_type.value} security. Required step at this "
                    f"price level = {_format_decimal(step)} NZD.",
                    normalized_price=normalized_price,
                    tick_size=step,
                )
        elif order.price is not None:
            logger.warning(
                "NZX %s: price %s ignored on a MARKET order; Price (44) is omitted.",
                cl_ord_id, order.price,
            )

        sending_time = _utc_timestamp(self._now())
        body: List[Tuple[int, str]] = [(35, "D"), (34, str(self._next_seq_num(seq_num)))]
        body.extend(self._identity_tags())
        body.append((52, sending_time))
        body.extend([
            (11, cl_ord_id),
            (55, symbol),
            (54, _SIDE_FIX[side]),
            (38, str(quantity)),
            (40, _ORD_TYPE_FIX[ord_type]),
        ])
        if normalized_price is not None:
            # Render Price (44) at exactly the precision of the step it was validated
            # against, so the audit trail shows '30.00' rather than an ambiguous '30'.
            body.append((44, format(normalized_price.quantize(step), "f")))
        body.extend([(59, _TIF_FIX[tif]), (15, "NZD"), (60, sending_time)])

        payload = self._frame(body)
        price_note = (
            f"@ {_format_decimal(normalized_price)} NZD"
            if normalized_price is not None else "at market"
        )
        notes = (
            f"NZX FIX NewOrderSingle built for {symbol}: {side.value} {quantity} "
            f"{price_note} ({ord_type.value}/{tif.value}, {sec_type.value})."
        )
        logger.info(notes)
        return NZXOrderReport(
            cl_ord_id=cl_ord_id,
            symbol=symbol,
            status="NEW",
            fix_msg_type="D",
            fix_raw_payload=payload,
            audit_notes=notes,
            normalized_price=normalized_price,
            tick_size=step,
        )

    def build_fix_order_cancel_request(
        self,
        orig_cl_ord_id: str,
        cl_ord_id: str,
        symbol: str,
        side: Union[str, NZXSide],
        quantity: Optional[int] = None,
        seq_num: Optional[int] = None,
    ) -> NZXOrderReport:
        """
        Build a framed FIX OrderCancelRequest (35=F).

        `cl_ord_id` must be a NEW identifier and `orig_cl_ord_id` the id of the
        order being cancelled; reusing the original id is a FIX protocol error
        and NZX will reject it. Requesting a cancel does not cancel anything:
        the order stays live and can still fill until an ExecutionReport
        confirms OrdStatus=4 (CANCELED), so treat this as PENDING_CANCEL and
        keep applying fills until then.
        """
        cl_ord_id = self._require_identifier(cl_ord_id, "cl_ord_id")
        orig_cl_ord_id = self._require_identifier(orig_cl_ord_id, "orig_cl_ord_id")
        symbol = self._require_symbol(symbol)
        if cl_ord_id == orig_cl_ord_id:
            return self._reject(
                cl_ord_id, symbol,
                "OrderCancelRequest requires a NEW ClOrdID (11) distinct from "
                "OrigClOrdID (41); reusing the original id is a FIX protocol error.",
            )
        try:
            side_enum = _coerce_enum(side, NZXSide, "side")
            qty = self._require_quantity(quantity) if quantity is not None else None
        except ValueError as exc:
            return self._reject(cl_ord_id, symbol, str(exc))

        sending_time = _utc_timestamp(self._now())
        body: List[Tuple[int, str]] = [(35, "F"), (34, str(self._next_seq_num(seq_num)))]
        body.extend(self._identity_tags())
        body.append((52, sending_time))
        body.extend([
            (41, orig_cl_ord_id),
            (11, cl_ord_id),
            (55, symbol),
            (54, _SIDE_FIX[side_enum]),
        ])
        if qty is not None:
            body.append((38, str(qty)))
        body.append((60, sending_time))

        payload = self._frame(body)
        notes = (
            f"NZX FIX OrderCancelRequest built for {symbol}: cancelling {orig_cl_ord_id} "
            f"as {cl_ord_id}. Order remains live until an ExecutionReport confirms "
            f"OrdStatus=4."
        )
        logger.info(notes)
        return NZXOrderReport(
            cl_ord_id=cl_ord_id,
            symbol=symbol,
            status="PENDING_CANCEL",
            fix_msg_type="F",
            fix_raw_payload=payload,
            audit_notes=notes,
        )

    # -- FIX parsing -------------------------------------------------------- #
    @staticmethod
    def parse_execution_report(
        raw: str,
        verify_checksum: bool = True,
    ) -> NZXExecutionReport:
        """
        Decode a FIX ExecutionReport (35=8) into an `NZXExecutionReport`.

        Accepts SOH- or '|'-delimited input. Raises ValueError for a malformed
        message, for a MsgType other than '8' (a session-level Reject (35=3) is
        NOT an ExecutionReport and must not be mistaken for one), for a missing
        mandatory field, or for a CheckSum mismatch when `verify_checksum`.

        This is a flat top-level decoder: it keeps the FIRST occurrence of each
        tag and does not descend into repeating groups. A message carrying groups
        that reuse these tags must be handled by a full FIX engine.
        """
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("ExecutionReport payload is empty")

        delimiter = SOH if SOH in raw else "|"
        fields = [f for f in raw.split(delimiter) if f]
        tags: Dict[int, str] = {}
        for fld in fields:
            tag_str, sep, value = fld.partition("=")
            if not sep or not tag_str.isdigit():
                raise ValueError(f"Malformed FIX field {fld!r}")
            tags.setdefault(int(tag_str), value)   # first occurrence wins

        msg_type = tags.get(35)
        if msg_type is None:
            raise ValueError("FIX message is missing MsgType (35)")
        if msg_type != "8":
            raise ValueError(
                f"Expected an ExecutionReport (35=8), got MsgType {msg_type!r}. "
                "A session-level Reject (35=3) or OrderCancelReject (35=9) is not "
                "an ExecutionReport and does not confirm any order state."
            )
        if verify_checksum and 10 in tags:
            idx = raw.rfind(f"{delimiter}10=")
            if idx >= 0:
                expected = _fix_checksum(raw[: idx + 1])
                if expected != tags[10]:
                    raise ValueError(
                        f"FIX CheckSum mismatch: computed {expected}, message carries "
                        f"{tags[10]}. The message is corrupt or truncated - do not act on it."
                    )

        for required, name in ((11, "ClOrdID"), (17, "ExecID"), (39, "OrdStatus")):
            if required not in tags:
                raise ValueError(f"ExecutionReport is missing mandatory {name} ({required})")

        def dec(tag: int) -> Optional[Decimal]:
            if tag not in tags:
                return None
            return _to_decimal(tags[tag], f"tag {tag}")

        ord_status = tags[39]
        report = NZXExecutionReport(
            cl_ord_id=tags[11],
            exec_id=tags[17],
            ord_status=ord_status,
            ord_status_name=ORD_STATUS_NAMES.get(ord_status, "UNKNOWN"),
            order_id=tags.get(37),
            exec_type=tags.get(150),
            symbol=tags.get(55),
            side=tags.get(54),
            last_qty=dec(32),
            last_px=dec(31),
            cum_qty=dec(14),
            leaves_qty=dec(151),
            avg_px=dec(6),
            currency=tags.get(15),
            ord_rej_reason=tags.get(103),
            text=tags.get(58),
            poss_dup=tags.get(43) == "Y",
            raw_tags=tags,
        )
        if report.poss_dup:
            logger.warning(
                "NZX ExecutionReport %s for %s carries PossDupFlag(43)=Y - it is a resend. "
                "Reconcile against CumQty(14); do not add LastQty(32) again.",
                report.exec_id, report.cl_ord_id,
            )
        if report.ord_status_name == "UNKNOWN":
            logger.warning(
                "NZX ExecutionReport %s carries an unrecognised OrdStatus(39)=%r; "
                "treat the order state as unknown rather than assuming it is working.",
                report.exec_id, ord_status,
            )
        return report

    # -- internals ---------------------------------------------------------- #
    def _now(self) -> datetime:
        now = self._clock()
        if not isinstance(now, datetime):
            raise TypeError("clock() must return a datetime")
        return now

    def _next_seq_num(self, seq_num: Optional[int]) -> int:
        if seq_num is None and self._seq_num_provider is not None:
            seq_num = self._seq_num_provider()
        if seq_num is None:
            raise ValueError(
                "MsgSeqNum (34) is mandatory on every FIX message. Pass seq_num, or "
                "give the engine a seq_num_provider bound to your FIX session. Sequence "
                "numbers belong to the session layer - inventing one here would desync "
                "the session on the very first message."
            )
        if isinstance(seq_num, bool) or not isinstance(seq_num, int) or seq_num < 1:
            raise ValueError(f"MsgSeqNum (34) must be an integer >= 1, got {seq_num!r}")
        return seq_num

    def _identity_tags(self) -> List[Tuple[int, str]]:
        tags: List[Tuple[int, str]] = [
            (49, self.session.sender_comp_id),
            (56, self.session.target_comp_id),
        ]
        if self.session.sender_sub_id:
            tags.append((50, self.session.sender_sub_id))
        if self.session.target_sub_id:
            tags.append((57, self.session.target_sub_id))
        return tags

    def _frame(self, body: List[Tuple[int, str]]) -> str:
        """
        Wrap body fields in the FIX standard header and trailer.

        BodyLength (9) counts the bytes from the field after 9 up to and including
        the delimiter preceding 10; CheckSum (10) is the byte sum of everything
        before it, modulo 256. Computing both over the actually-emitted bytes keeps
        a '|'-delimited debug message internally consistent.
        """
        delimiter = self.field_delimiter
        body_str = "".join(f"{tag}={value}{delimiter}" for tag, value in body)
        body_length = len(body_str.encode("ascii"))
        prefix = (
            f"8={self.session.begin_string}{delimiter}"
            f"9={body_length}{delimiter}{body_str}"
        )
        return f"{prefix}10={_fix_checksum(prefix)}{delimiter}"

    @staticmethod
    def _require_identifier(value: str, label: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{label} must be a non-empty string")
        value = value.strip()
        _reject_fix_metacharacters(value, label)
        return value

    @staticmethod
    def _require_symbol(symbol: str) -> str:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("symbol must be a non-empty string")
        symbol = symbol.strip().upper()
        _reject_fix_metacharacters(symbol, "symbol")
        if "." in symbol:
            raise ValueError(
                f"symbol {symbol!r} looks like vendor symbology (e.g. 'FPH.NZ'). FIX "
                f"Symbol (55) carries the bare NZX ticker, e.g. 'FPH' - strip the "
                f"vendor market suffix before routing."
            )
        if not symbol.isalnum():
            raise ValueError(f"symbol {symbol!r} must be alphanumeric")
        return symbol

    @staticmethod
    def _require_quantity(quantity: int) -> int:
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise ValueError(
                f"quantity must be a whole number of securities, got {quantity!r}. "
                "A float quantity is a rounding bug waiting to happen."
            )
        if quantity <= 0:
            raise ValueError(f"quantity must be positive, got {quantity}")
        return quantity

    @staticmethod
    def _reject(
        cl_ord_id: str,
        symbol: str,
        reason: str,
        normalized_price: Optional[Decimal] = None,
        tick_size: Optional[Decimal] = None,
    ) -> NZXOrderReport:
        notes = f"NZX REJECT ({cl_ord_id}): {reason}"
        logger.error(notes)
        return NZXOrderReport(
            cl_ord_id=cl_ord_id,
            symbol=symbol,
            status="REJECTED",
            fix_msg_type="",          # no FIX message was built and none was sent
            fix_raw_payload="",
            audit_notes=notes,
            rejection_reason=reason,
            normalized_price=normalized_price,
            tick_size=tick_size,
        )

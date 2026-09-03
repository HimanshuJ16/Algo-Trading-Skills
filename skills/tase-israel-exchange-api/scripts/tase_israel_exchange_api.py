"""
tase-israel-exchange-api:
Session-calendar, price-denomination and pre-trade risk logic for order
routing to the Tel Aviv Stock Exchange (TASE).

What this module is
-------------------
It is the *calendar, denomination and pre-trade risk* layer, not a FIX engine.
It does not open sockets, does not serialise FIX messages and does not manage
sequence numbers -- use QuickFIX or the venue-supplied gateway library for the
session layer. Keeping transport out is deliberate: it means the price scaling
and the risk decisions can be asserted in a unit test without a gateway.

The 2026 trading-week change (the reason this module exists)
------------------------------------------------------------
Effective **5 January 2026**, TASE moved from a Sunday-Thursday trading week to
a **Monday-Friday** week, with a shortened Friday session ending before Shabbat.
The change was announced by the Israel Securities Authority with the approval of
the Ministry of Finance, and was carried in the operational notices of the major
index providers (MSCI, Solactive) ahead of the effective date.

This matters more than a configuration tweak. A system still carrying the old
calendar fails in *both* directions:

  - It treats **Friday as closed** and stops trading during a live session.
  - It treats **Sunday as open** and routes orders into a closed market.

Neither failure is loud. Both are caught here by making the trading week an
explicit, dated property of ``TASESessionSchedule`` rather than an assumption
baked into a weekday comparison.

Because backtests still run over older history, ``TASESessionSchedule.for_date``
selects the regime that was actually in force on a given date. Replaying 2025
data under today's Monday-Friday calendar reintroduces the same class of error in
reverse.

Timezone
--------
Israel observes daylight saving under the Time Determination Law: clocks advance
one hour from the **Friday before the last Sunday in March** until the **last
Sunday in October** (IST = UTC+2 winter, IDT = UTC+3 summer). That is roughly
seven months of the year, so a hard-coded UTC+2 offset misclassifies the market
phase by a full hour for most of the trading calendar -- enough to mistake the
closing auction for continuous trading.

This module resolves local time through the IANA zone ``Asia/Jerusalem`` and
raises if the zone is unavailable. It deliberately does **not** silently fall
back to a fixed offset, because a silent fallback is precisely the defect it
exists to prevent.

Price denomination
------------------
TASE quotes different instrument classes in different units, and the conversion
is the highest-consequence arithmetic in the whole integration:

  - **Equities / ETFs** quote in **Agorot** (1 ILS = 100 Agorot).
  - **Bonds / Makam** quote as a **percentage of par value**; the cash value
    depends on the instrument's par value, which is *not* recoverable from the
    quoted price alone.
  - **Index derivatives** quote directly in ILS.

Getting this wrong is a 100x notional error in either direction. The module
therefore refuses to price an order whose declared denomination contradicts the
registered security master, and refuses to value a percentage-quoted order for
which no par value has been registered.

Verification status of the schedule constants
---------------------------------------------
TASE's own trading-schedule pages are client-rendered and were not machine
retrievable at the time of writing, so the defaults below carry different levels
of corroboration. Treat them as a starting point to be confirmed against TASE's
published schedule and the venue's session-definition feed, not as authority:

  - Session **open 09:59** and **Mon-Thu close 17:25** / **Fri close 13:50**:
    corroborated by MSCI's index announcement for the 5 Jan 2026 change.
  - **Pre-open from 09:25**: corroborated by market-data vendor session tables
    and contemporaneous press coverage.
  - **Closing-auction start times** (``closing_auction`` below): NOT independently
    corroborated. They are placeholders that preserve a plausible auction window
    and MUST be replaced with the venue's published values before production use.

Holidays are not embedded. TASE's holiday calendar follows the Hebrew calendar
and cannot be derived from a weekday rule; supply it via ``holidays``.
"""

from __future__ import annotations

import datetime
import logging
import math
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, FrozenSet, Optional, Tuple

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError as exc:  # pragma: no cover - Python < 3.9
    raise ImportError(
        "tase_israel_exchange_api requires Python 3.9+ for zoneinfo."
    ) from exc

logger = logging.getLogger(__name__)

ISRAEL_TZ_NAME = "Asia/Jerusalem"

#: Date on which TASE moved from a Sunday-Thursday to a Monday-Friday week.
TRADING_WEEK_CHANGE_DATE = datetime.date(2026, 1, 5)


class TASEError(Exception):
    """Base exception class for TASE integration errors."""


class TASEConfigurationError(TASEError):
    """Raised when engine or schedule configuration is internally inconsistent."""


class TASEConnectionError(TASEError):
    """Raised when the FIX session is not established or fails."""


class TASEValidationError(TASEError):
    """Raised when order parameters or security specifications fail validation."""


class TASERiskLimitError(TASEError):
    """Raised when pre-trade risk controls or order limits are breached."""


class TASEMarketClosedError(TASEError):
    """Raised when order entry is attempted outside an order-accepting phase."""


def _require_finite(value: float, field_name: str) -> float:
    """
    Reject NaN and infinity before any risk comparison sees them.

    This is not defensive boilerplate. Every pre-trade control in this module is
    a comparison, and **every comparison against NaN is False** -- so a NaN
    quantity passes ``quantity <= 0``, passes the quantity cap, and yields a NaN
    notional that passes the value cap and the price collar too. A single NaN
    silently disables the entire risk layer at once, which is why finiteness is
    checked first rather than left to the individual limits.

    Infinity is rejected for the same reason in reverse: it makes limits fire on
    orders that were never well formed, and it raises ``OverflowError`` out of
    the tick-alignment arithmetic instead of a ``TASEError`` the caller handles.
    """
    numeric = float(value)
    if not math.isfinite(numeric):
        raise TASEValidationError(
            f"{field_name} must be a finite number, got {value!r}. Non-finite "
            "values compare False against every risk limit and would pass the "
            "pre-trade controls unchecked."
        )
    return numeric


def _israel_tz() -> ZoneInfo:
    """
    Return the Asia/Jerusalem zone, or raise with an actionable message.

    Deliberately never falls back to a fixed UTC offset: a fixed offset is wrong
    for the ~7 months Israel spends on IDT, and failing loudly at start-up is far
    cheaper than misclassifying the market phase in production.
    """
    try:
        return ZoneInfo(ISRAEL_TZ_NAME)
    except ZoneInfoNotFoundError as exc:  # pragma: no cover - environment dependent
        raise TASEConfigurationError(
            f"IANA timezone {ISRAEL_TZ_NAME!r} is unavailable. Install the "
            "'tzdata' package (required on Windows and on slim containers) or "
            "provide a system tz database. Refusing to guess a fixed UTC offset, "
            "because Israel alternates between UTC+2 (IST) and UTC+3 (IDT)."
        ) from exc


class OrderSide(Enum):
    """Order side. Values are FIX ``Side`` (tag 54) wire codes."""

    BUY = "1"
    SELL = "2"


class OrderType(Enum):
    """
    Order type.

    Values here are *symbolic*, not wire codes -- read ``fix_ord_type`` for the
    FIX ``OrdType`` (tag 40) value. This split is deliberate. Iceberg is not an
    OrdType in FIX at all: an iceberg is a **limit** order (tag 40 = ``2``) whose
    visible size is carried in ``DisplayQty`` (tag 1138 in FIX 5.0; ``MaxFloor``,
    tag 111, in FIX 4.x). Encoding it as an OrdType value transmits a different
    order type than intended -- ``L`` is "Previous Fund Valuation Point", not
    iceberg -- and ``3`` is "Stop / Stop Loss", not stop-limit.
    """

    MARKET = "MARKET"
    LIMIT = "LIMIT"
    STOP_LIMIT = "STOP_LIMIT"
    ICEBERG = "ICEBERG"

    @property
    def fix_ord_type(self) -> str:
        """FIX ``OrdType`` (tag 40) code for this order type."""
        return _FIX_ORD_TYPE[self]


_FIX_ORD_TYPE = {
    OrderType.MARKET: "1",
    OrderType.LIMIT: "2",
    # Tag 40 = 3 is "Stop / Stop Loss"; stop-limit is 4.
    OrderType.STOP_LIMIT: "4",
    # An iceberg is a limit order; DisplayQty (1138) carries the visible slice.
    OrderType.ICEBERG: "2",
}


class OrderStatus(Enum):
    """Order status. Values are FIX ``OrdStatus`` (tag 39) wire codes."""

    NEW = "0"
    PARTIALLY_FILLED = "1"
    FILLED = "2"
    CANCELED = "4"
    REJECTED = "8"


class MarketPhase(Enum):
    """
    TASE session phase.

    Values are symbolic. They are intentionally not FIX ``TradSesStatus``
    (tag 340) codes, whose enumeration differs (2 = Open, 4 = Pre-Open, ...).
    """

    PRE_OPEN = "PRE_OPEN"
    OPENING_AUCTION = "OPENING_AUCTION"
    CONTINUOUS_TRADING = "CONTINUOUS_TRADING"
    CLOSING_AUCTION = "CLOSING_AUCTION"
    CLOSED = "CLOSED"


#: Phases during which the venue accepts order entry, modification and cancellation.
ORDER_ENTRY_PHASES = frozenset(
    {
        MarketPhase.PRE_OPEN,
        MarketPhase.OPENING_AUCTION,
        MarketPhase.CONTINUOUS_TRADING,
        MarketPhase.CLOSING_AUCTION,
    }
)


class PriceDenomination(Enum):
    """
    Unit in which a price is quoted.

    ``PERCENTAGE`` prices cannot be converted to cash without the instrument's
    par value; see :meth:`TASEIntegrationEngine.price_to_ils`.
    """

    AGOROT = "AGOROT"      # 100 Agorot = 1 ILS (equities, ETFs, mutual funds)
    ILS = "ILS"            # Israeli New Shekel, quoted directly (index derivatives)
    PERCENTAGE = "PCT"     # Percentage of par value (bonds, Makam)


class InstrumentType(Enum):
    EQUITY = "EQUITY"
    BOND = "BOND"
    MAKAM = "MAKAM"  # Israeli Treasury Bills
    DERIVATIVE = "DERIVATIVE"
    ETF = "ETF"


@dataclass(frozen=True)
class TASESessionSchedule:
    """
    Session phase boundaries for one TASE trading regime, in Israel local time.

    ``trading_weekdays`` uses ``datetime.date.weekday()`` numbering
    (Monday=0 ... Sunday=6).

    ``holidays`` must be supplied by the caller from TASE's published schedule;
    TASE holidays follow the Hebrew calendar and cannot be derived from a rule.
    """

    trading_weekdays: FrozenSet[int]
    pre_open: datetime.time
    opening_auction: datetime.time
    continuous_open: datetime.time
    closing_auction: datetime.time
    close: datetime.time
    #: Weekdays with an early close (e.g. Friday, before Shabbat).
    short_weekdays: FrozenSet[int] = frozenset()
    short_closing_auction: Optional[datetime.time] = None
    short_close: Optional[datetime.time] = None
    holidays: FrozenSet[datetime.date] = frozenset()
    label: str = ""

    def __post_init__(self) -> None:
        ordered = [
            self.pre_open,
            self.opening_auction,
            self.continuous_open,
            self.closing_auction,
            self.close,
        ]
        if ordered != sorted(ordered):
            raise TASEConfigurationError(
                f"Session boundaries must be non-decreasing, got {ordered}."
            )
        if not self.trading_weekdays:
            raise TASEConfigurationError("trading_weekdays must not be empty.")
        if not self.short_weekdays <= self.trading_weekdays:
            raise TASEConfigurationError(
                "short_weekdays must be a subset of trading_weekdays."
            )
        if self.short_weekdays and (
            self.short_closing_auction is None or self.short_close is None
        ):
            raise TASEConfigurationError(
                "short_weekdays requires short_closing_auction and short_close."
            )
        if self.short_closing_auction is not None and self.short_close is not None:
            if not self.continuous_open <= self.short_closing_auction <= self.short_close:
                raise TASEConfigurationError(
                    "Short-day boundaries must satisfy "
                    "continuous_open <= short_closing_auction <= short_close."
                )

    @classmethod
    def current(
        cls, holidays: FrozenSet[datetime.date] = frozenset()
    ) -> "TASESessionSchedule":
        """
        Monday-Friday regime in force since 5 January 2026.

        Open (09:59) and close (17:25 Mon-Thu / 13:50 Fri) are corroborated by
        MSCI's announcement of the change; pre-open (09:25) by vendor session
        tables. The closing-auction start times are NOT corroborated -- confirm
        them against TASE's published schedule before production use.
        """
        return cls(
            trading_weekdays=frozenset({0, 1, 2, 3, 4}),  # Monday..Friday
            pre_open=datetime.time(9, 25),
            opening_auction=datetime.time(9, 59),
            continuous_open=datetime.time(10, 0),
            closing_auction=datetime.time(17, 15),
            close=datetime.time(17, 25),
            short_weekdays=frozenset({4}),  # Friday closes early before Shabbat
            short_closing_auction=datetime.time(13, 40),
            short_close=datetime.time(13, 50),
            holidays=holidays,
            label="TASE Monday-Friday (from 2026-01-05)",
        )

    @classmethod
    def legacy_sunday_thursday(
        cls, holidays: FrozenSet[datetime.date] = frozenset()
    ) -> "TASESessionSchedule":
        """
        Sunday-Thursday regime in force until 4 January 2026.

        Retained for backtests over older history: replaying 2025 data under
        the current Monday-Friday calendar reintroduces the same calendar error
        this module exists to prevent, merely in the opposite direction.
        """
        return cls(
            trading_weekdays=frozenset({6, 0, 1, 2, 3}),  # Sunday..Thursday
            pre_open=datetime.time(8, 30),
            opening_auction=datetime.time(9, 50),
            continuous_open=datetime.time(10, 0),
            closing_auction=datetime.time(17, 15),
            close=datetime.time(17, 25),
            short_weekdays=frozenset({6}),  # Sunday closed early
            short_closing_auction=datetime.time(15, 50),
            short_close=datetime.time(16, 0),
            holidays=holidays,
            label="TASE Sunday-Thursday (until 2026-01-04)",
        )

    @classmethod
    def for_date(
        cls,
        on_date: datetime.date,
        holidays: FrozenSet[datetime.date] = frozenset(),
    ) -> "TASESessionSchedule":
        """Return whichever regime was actually in force on ``on_date``."""
        if on_date < TRADING_WEEK_CHANGE_DATE:
            return cls.legacy_sunday_thursday(holidays=holidays)
        return cls.current(holidays=holidays)

    def _boundaries(
        self, weekday: int
    ) -> Tuple[
        datetime.time, datetime.time, datetime.time, datetime.time, datetime.time
    ]:
        """Return the five phase boundaries applicable to ``weekday``."""
        if weekday in self.short_weekdays:
            # Both validated non-None in __post_init__ when short_weekdays is set.
            assert self.short_closing_auction is not None
            assert self.short_close is not None
            return (
                self.pre_open,
                self.opening_auction,
                self.continuous_open,
                self.short_closing_auction,
                self.short_close,
            )
        return (
            self.pre_open,
            self.opening_auction,
            self.continuous_open,
            self.closing_auction,
            self.close,
        )

    def phase_at(self, local_dt: datetime.datetime) -> MarketPhase:
        """Resolve the market phase for an Israel-local datetime."""
        local_date = local_dt.date()
        if local_date in self.holidays:
            return MarketPhase.CLOSED

        weekday = local_date.weekday()
        if weekday not in self.trading_weekdays:
            return MarketPhase.CLOSED

        pre_open, opening, continuous, closing, close = self._boundaries(weekday)
        now = local_dt.time()

        if now < pre_open:
            return MarketPhase.CLOSED
        if now < opening:
            return MarketPhase.PRE_OPEN
        if now < continuous:
            return MarketPhase.OPENING_AUCTION
        if now < closing:
            return MarketPhase.CONTINUOUS_TRADING
        if now < close:
            return MarketPhase.CLOSING_AUCTION
        return MarketPhase.CLOSED


@dataclass
class TASEConfig:
    sender_comp_id: str
    target_comp_id: str
    host: str
    port: int
    trader_id: str = "QUAL_TRADER_1"
    heartbeat_interval: int = 30
    protocol_version: str = "FIX.4.4"
    max_order_value_ils: float = 1_000_000.0
    max_order_qty: float = 100_000.0
    max_price_collar_pct: float = 10.0  # Max deviation from reference price (%)
    #: Reject order entry outside an order-accepting session phase.
    enforce_session_calendar: bool = True
    #: Reject orders on securities absent from the local security master.
    require_registered_security: bool = True
    #: Session schedule; defaults to the regime in force today.
    session_schedule: TASESessionSchedule = field(
        default_factory=TASESessionSchedule.current
    )


@dataclass
class TASESecurity:
    symbol: str  # e.g. 'TEVA.TA'
    security_id: str  # TASE 6 or 7 digit code (e.g. '1082511')
    isin: str  # e.g. 'IL0001082511'
    instrument_type: InstrumentType = InstrumentType.EQUITY
    price_denomination: PriceDenomination = PriceDenomination.AGOROT
    tick_size_agorot: float = 0.1
    reference_price_ils: float = 0.0
    #: Par value per unit in ILS. Required for PERCENTAGE-quoted instruments
    #: (bonds, Makam); a percentage price alone carries no cash value.
    par_value_ils: Optional[float] = None


@dataclass
class TASEOrder:
    symbol: str
    security_id: str
    side: OrderSide
    order_type: OrderType
    quantity: float
    #: Quoted in the unit given by ``price_denomination``. ``None`` for MARKET.
    price: Optional[float] = None
    price_denomination: PriceDenomination = PriceDenomination.AGOROT
    #: Trigger price for STOP_LIMIT, in the same unit as ``price``.
    stop_price: Optional[float] = None
    display_qty: Optional[float] = None  # Iceberg visible size (FIX DisplayQty 1138)
    client_order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: OrderStatus = OrderStatus.NEW
    filled_quantity: float = 0.0
    average_price: float = 0.0
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class TASEIntegrationEngine:
    """
    Calendar, denomination and pre-trade risk layer for TASE order routing.

    Order state transitions here model what the venue reports back; the FIX
    session itself is out of scope (see the module docstring).
    """

    AGOROT_PER_ILS = 100.0
    #: Relative tolerance for float comparisons on quantities and tick alignment.
    QTY_TOLERANCE = 1e-9

    def __init__(self, config: TASEConfig):
        self.config = config
        self.is_connected = False
        self.orders: Dict[str, TASEOrder] = {}
        self.securities: Dict[str, TASESecurity] = {}
        self.session_seq_num = 1
        self._tz = _israel_tz()
        logger.info(
            "Initialized TASE engine [sender=%s target=%s schedule=%s]",
            config.sender_comp_id,
            config.target_comp_id,
            config.session_schedule.label or "custom",
        )

    # ------------------------------------------------------------------ #
    # Security master
    # ------------------------------------------------------------------ #

    def register_security(self, security: TASESecurity) -> None:
        """Register security reference metadata into the local master."""
        if (
            security.price_denomination == PriceDenomination.PERCENTAGE
            and not security.par_value_ils
        ):
            raise TASEValidationError(
                f"{security.symbol}: PERCENTAGE-quoted instruments require a "
                "positive par_value_ils; a percentage price has no cash value "
                "without it."
            )
        # A NaN reference price would make the collar comparison False for every
        # order, so the master is checked at load time rather than at order time.
        _require_finite(security.tick_size_agorot, "tick_size_agorot")
        _require_finite(security.reference_price_ils, "reference_price_ils")
        if security.par_value_ils is not None:
            _require_finite(security.par_value_ils, "par_value_ils")
        if security.tick_size_agorot <= 0:
            raise TASEValidationError(
                f"{security.symbol}: tick_size_agorot must be positive, got "
                f"{security.tick_size_agorot}."
            )
        self.securities[security.symbol] = security
        logger.info(
            "Registered TASE security %s (id=%s isin=%s denomination=%s)",
            security.symbol,
            security.security_id,
            security.isin,
            security.price_denomination.value,
        )

    # ------------------------------------------------------------------ #
    # Price denomination
    # ------------------------------------------------------------------ #

    @classmethod
    def convert_price_agorot_to_ils(cls, price_agorot: float) -> float:
        """Convert a price from Agorot to Israeli New Shekels."""
        return price_agorot / cls.AGOROT_PER_ILS

    @classmethod
    def convert_price_ils_to_agorot(cls, price_ils: float) -> float:
        """Convert a price from Israeli New Shekels to Agorot."""
        return price_ils * cls.AGOROT_PER_ILS

    def price_to_ils(
        self, price: float, denomination: PriceDenomination, symbol: str
    ) -> float:
        """
        Convert a quoted price into ILS per unit.

        ``PERCENTAGE`` quotes need the instrument's par value: a bond quoted at
        102.5 is 102.5% of par, not 102.5 ILS. Treating the percentage as a
        shekel price is a 100x notional error, so this raises rather than guess.
        """
        if denomination == PriceDenomination.AGOROT:
            return self.convert_price_agorot_to_ils(price)
        if denomination == PriceDenomination.ILS:
            return price
        if denomination == PriceDenomination.PERCENTAGE:
            security = self.securities.get(symbol)
            if security is None or not security.par_value_ils:
                raise TASEValidationError(
                    f"{symbol}: cannot value a PERCENTAGE-quoted price without a "
                    "registered par_value_ils."
                )
            return price / 100.0 * security.par_value_ils
        raise TASEValidationError(f"Unsupported price denomination {denomination!r}.")

    def calculate_order_value_ils(self, order: TASEOrder) -> float:
        """
        Estimated gross order value in ILS.

        A MARKET order carries no price, so its notional is estimated from the
        security's reference price. If neither a price nor a reference price is
        available the notional is unknown, and this raises rather than return
        zero -- returning zero would let an unbounded market order slip past the
        ``max_order_value_ils`` cap entirely.
        """
        if order.price is not None:
            price_ils = self.price_to_ils(
                order.price, order.price_denomination, order.symbol
            )
            return price_ils * order.quantity

        security = self.securities.get(order.symbol)
        if security is not None and security.reference_price_ils > 0:
            return security.reference_price_ils * order.quantity

        raise TASEValidationError(
            f"Cannot estimate notional for {order.symbol}: order has no price and "
            "no positive reference_price_ils is registered. Refusing to treat the "
            "notional as zero, which would bypass the max order value control."
        )

    # ------------------------------------------------------------------ #
    # Session
    # ------------------------------------------------------------------ #

    def connect(self) -> bool:
        """Establish the FIX session with the TASE gateway."""
        if not self.config.host:
            raise TASEValidationError("Host must be specified in TASEConfig.")
        if not isinstance(self.config.port, int) or not 0 < self.config.port < 65536:
            raise TASEValidationError(
                f"Port must be an integer in 1..65535, got {self.config.port!r}."
            )
        if not self.config.sender_comp_id or not self.config.target_comp_id:
            raise TASEValidationError("SenderCompID and TargetCompID are required.")

        logger.info(
            "Connecting to TASE gateway at %s:%s", self.config.host, self.config.port
        )
        self.is_connected = True
        logger.info("TASE session established (Logon MsgType=A acknowledged).")
        return True

    def disconnect(self) -> bool:
        """Terminate the FIX session gracefully."""
        if self.is_connected:
            logger.info("Sending FIX Logout (MsgType=5).")
            self.is_connected = False
            return True
        return False

    def get_market_phase(self, dt: Optional[datetime.datetime] = None) -> MarketPhase:
        """
        Resolve the TASE market phase for ``dt`` (default: now).

        ``dt`` must be timezone-aware; a naive datetime is ambiguous and is
        rejected rather than assumed to be UTC. Local time is resolved through
        ``Asia/Jerusalem``, so IST/IDT transitions are handled by the tz database
        rather than a fixed offset.
        """
        if dt is None:
            dt = datetime.datetime.now(datetime.timezone.utc)
        if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
            raise TASEValidationError(
                "get_market_phase requires a timezone-aware datetime; a naive "
                "value cannot be mapped to Israel local time unambiguously."
            )

        local_dt = dt.astimezone(self._tz)
        return self.config.session_schedule.phase_at(local_dt)

    def accepts_order_entry(self, dt: Optional[datetime.datetime] = None) -> bool:
        """Whether the venue accepts order entry at ``dt``."""
        return self.get_market_phase(dt) in ORDER_ENTRY_PHASES

    # ------------------------------------------------------------------ #
    # Pre-trade risk
    # ------------------------------------------------------------------ #

    def _check_tick_alignment(self, order: TASEOrder, security: TASESecurity) -> None:
        """Reject limit prices that are not a whole multiple of the tick size."""
        if order.price is None:
            return
        if order.price_denomination != PriceDenomination.AGOROT:
            # Tick sizes are registered in Agorot; other denominations are not
            # comparable without a venue-specific tick table.
            return
        ticks = order.price / security.tick_size_agorot
        if abs(ticks - round(ticks)) > self.QTY_TOLERANCE * max(1.0, abs(ticks)):
            raise TASEValidationError(
                f"{order.symbol}: price {order.price} Agorot is not a multiple of "
                f"tick size {security.tick_size_agorot} Agorot."
            )

    def validate_order(self, order: TASEOrder) -> None:
        """
        Run pre-trade parameter and risk checks, raising on the first breach.

        Every control here fails closed. In particular, a security missing from
        the local master is rejected by default rather than silently skipping the
        price collar -- an unknown symbol is the case where a collar is most
        needed, not least.
        """
        # Finiteness first: a NaN slipping past here would make every subsequent
        # limit comparison False and disable the whole risk layer silently.
        _require_finite(order.quantity, "quantity")
        if order.price is not None:
            _require_finite(order.price, "price")
        if order.stop_price is not None:
            _require_finite(order.stop_price, "stop_price")
        if order.display_qty is not None:
            _require_finite(order.display_qty, "display_qty")

        if order.quantity <= 0:
            raise TASEValidationError(
                f"Order quantity must be positive, got {order.quantity}."
            )

        if order.order_type in (
            OrderType.LIMIT,
            OrderType.STOP_LIMIT,
            OrderType.ICEBERG,
        ):
            if order.price is None or order.price <= 0:
                raise TASEValidationError(
                    f"{order.order_type.name} orders require a positive price, got "
                    f"{order.price}."
                )

        if order.order_type == OrderType.STOP_LIMIT:
            if order.stop_price is None or order.stop_price <= 0:
                raise TASEValidationError(
                    "STOP_LIMIT orders require a positive stop_price."
                )

        if order.order_type == OrderType.ICEBERG:
            if order.display_qty is None or order.display_qty <= 0:
                raise TASEValidationError(
                    "Iceberg orders require a positive display_qty."
                )
            if order.display_qty > order.quantity:
                raise TASEValidationError(
                    "Iceberg display_qty cannot exceed total order quantity."
                )

        security = self.securities.get(order.symbol)
        if security is None:
            if self.config.require_registered_security:
                raise TASEValidationError(
                    f"{order.symbol} is not in the local security master. Refusing "
                    "to route: the price denomination and reference price cannot be "
                    "verified, so the collar and scaling checks would be skipped."
                )
            logger.warning(
                "Routing %s without a security master entry: denomination, tick and "
                "collar checks are skipped.",
                order.symbol,
            )
        else:
            # The headline TASE failure mode: an equity priced in ILS instead of
            # Agorot (or the reverse) is a silent 100x error that passes every
            # other check. Compare against the security master, not a convention.
            if order.price_denomination != security.price_denomination:
                raise TASEValidationError(
                    f"{order.symbol}: order is priced in "
                    f"{order.price_denomination.value} but the security master "
                    f"records {security.price_denomination.value}. Submitting this "
                    "would misprice the order by a factor of 100."
                )
            self._check_tick_alignment(order, security)

        if order.quantity > self.config.max_order_qty:
            raise TASERiskLimitError(
                f"Order quantity {order.quantity} exceeds max limit "
                f"{self.config.max_order_qty}."
            )

        order_val_ils = self.calculate_order_value_ils(order)
        if order_val_ils > self.config.max_order_value_ils:
            raise TASERiskLimitError(
                f"Order value {order_val_ils:.2f} ILS exceeds max allowed limit "
                f"{self.config.max_order_value_ils:.2f} ILS."
            )

        if security is not None and order.price is not None:
            if security.reference_price_ils > 0:
                price_ils = self.price_to_ils(
                    order.price, order.price_denomination, order.symbol
                )
                dev_pct = (
                    abs(price_ils - security.reference_price_ils)
                    / security.reference_price_ils
                    * 100.0
                )
                if dev_pct > self.config.max_price_collar_pct:
                    raise TASERiskLimitError(
                        f"Order price deviation {dev_pct:.2f}% exceeds price collar "
                        f"limit {self.config.max_price_collar_pct}%."
                    )
            else:
                logger.warning(
                    "%s has no positive reference_price_ils; price collar check "
                    "skipped for order %s.",
                    order.symbol,
                    order.client_order_id,
                )

    # ------------------------------------------------------------------ #
    # Order lifecycle
    # ------------------------------------------------------------------ #

    def submit_order(
        self, order: TASEOrder, now: Optional[datetime.datetime] = None
    ) -> str:
        """
        Submit a NewOrderSingle (MsgType=D) after pre-trade validation.

        ``client_order_id`` is the idempotency key: re-submitting an id already
        held locally is rejected rather than silently overwriting the tracked
        order, so a retry after a lost response cannot orphan the original.
        """
        if not self.is_connected:
            raise TASEConnectionError(
                "Cannot submit order: FIX session is disconnected."
            )

        if order.client_order_id in self.orders:
            raise TASEValidationError(
                f"Duplicate client_order_id {order.client_order_id}: an order with "
                "this id is already tracked. Reusing it would lose the original's "
                "state; query the existing order instead of resubmitting."
            )

        if self.config.enforce_session_calendar:
            phase = self.get_market_phase(now)
            if phase not in ORDER_ENTRY_PHASES:
                raise TASEMarketClosedError(
                    f"TASE is {phase.value}; order entry is not accepted. Schedule: "
                    f"{self.config.session_schedule.label or 'custom'}."
                )

        self.validate_order(order)

        self.orders[order.client_order_id] = order
        self.session_seq_num += 1

        logger.info(
            "Submitted TASE order %s: %s %s %s @ %s %s (OrdType=%s)",
            order.client_order_id,
            order.side.name,
            order.quantity,
            order.symbol,
            order.price if order.price is not None else "MKT",
            order.price_denomination.value,
            order.order_type.fix_ord_type,
        )
        return order.client_order_id

    def cancel_order(self, client_order_id: str) -> bool:
        """Submit an OrderCancelRequest (MsgType=F) for an active order."""
        if not self.is_connected:
            raise TASEConnectionError(
                "Cannot cancel order: FIX session is disconnected."
            )

        order = self.orders.get(client_order_id)
        if order is None:
            logger.warning(
                "Cancel request failed: order id %s not found.", client_order_id
            )
            return False

        if order.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
        ):
            logger.warning(
                "Cannot cancel order %s: already in terminal state %s.",
                client_order_id,
                order.status.name,
            )
            return False

        order.status = OrderStatus.CANCELED
        self.session_seq_num += 1
        logger.info("Order %s canceled.", client_order_id)
        return True

    def simulate_execution_report(
        self, client_order_id: str, filled_qty: float, exec_price: float
    ) -> Optional[TASEOrder]:
        """
        Apply an ExecutionReport (MsgType=8), updating fills, VWAP and status.

        ``exec_price`` is in the order's own denomination, so ``average_price``
        stays in that denomination too -- it is not converted to ILS.
        """
        order = self.orders.get(client_order_id)
        if order is None:
            logger.error("Execution report for unknown order id %s.", client_order_id)
            return None

        if order.status in (
            OrderStatus.FILLED,
            OrderStatus.CANCELED,
            OrderStatus.REJECTED,
        ):
            logger.warning(
                "Execution report ignored for order %s in terminal state %s.",
                client_order_id,
                order.status.name,
            )
            return order

        _require_finite(filled_qty, "filled_qty")
        _require_finite(exec_price, "exec_price")

        if filled_qty <= 0:
            raise TASEValidationError("Filled quantity must be positive.")
        if exec_price <= 0:
            raise TASEValidationError(
                f"Execution price must be positive, got {exec_price}. A non-positive "
                "fill price would silently corrupt the running average."
            )

        prev_filled = order.filled_quantity
        new_filled = prev_filled + filled_qty
        # Tolerance keeps a legitimate exact fill from tripping on float error.
        if new_filled > order.quantity * (1.0 + self.QTY_TOLERANCE):
            raise TASEValidationError(
                f"Cumulative filled quantity {new_filled} exceeds order quantity "
                f"{order.quantity}."
            )

        total_val = (order.average_price * prev_filled) + (exec_price * filled_qty)
        order.average_price = total_val / new_filled
        order.filled_quantity = new_filled

        if new_filled >= order.quantity * (1.0 - self.QTY_TOLERANCE):
            order.status = OrderStatus.FILLED
        else:
            order.status = OrderStatus.PARTIALLY_FILLED

        logger.info(
            "ExecutionReport %s: filled %s @ %s (cum %s/%s, status %s)",
            client_order_id,
            filled_qty,
            exec_price,
            order.filled_quantity,
            order.quantity,
            order.status.name,
        )
        return order

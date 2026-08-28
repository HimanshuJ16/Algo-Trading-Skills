"""Northbound Stock Connect order gate for SSE and SZSE Securities.

This module implements the pre-submission checks a Hong Kong / overseas
participant must apply to a Northbound order *before* handing it to CSC (the
China Stock Connect System), and the Northbound Daily Quota accounting that
governs whether further buy orders may be sent at all.

Every rule encoded here is quoted in ``references/standards.md`` from HKEX's
*Information Book for Investors* (Version Date 6 July 2026). Where the source is
silent, this module says so rather than inventing behaviour.

Scope and honesty boundary
--------------------------
This is a **client-side gate**, not the venue. SSE, SZSE and CSC are the
authorities on every rule below; passing this gate means an order is not
*obviously* invalid, never that it will be accepted. Three consequences follow:

1. **The gate is only as true as the reference data.** Board lot, price limit and
   pre-trade position all come from data the caller registers. An unregistered
   security is rejected, not waved through -- see the fail-closed rule below.

2. **Two documented Northbound checks are deliberately absent.** SEHK's *dynamic
   price check* (a buy priced below the current best bid by more than a
   prescribed percentage, 3% at the initial phase, is rejected by CSC) needs the
   live best bid and is applied by CSC itself. Northbound *eligibility* -- which
   securities are Connect Securities at all, and which are sell-only -- is a
   published list maintained by SEHK, not something derivable from a stock code.
   The caller supplies ``buy_eligible``; this module does not guess it.

3. **Nothing here is thread-safe.** The engine holds mutable per-day quota and
   position state. Serialise all calls onto one thread, or wrap it externally.

Fail-closed rule
----------------
Every check in this module rejects when the data it needs is missing. An
unregistered security, an absent market-open position, an unknown board -- each
produces a rejection with an auditable code, never an acceptance. The prior
version of this engine defaulted the other way (a sell order with no purchase
date recorded skipped the day-trading check entirely), which is the single most
dangerous shape a compliance gate can take.

Money and prices are ``Decimal`` throughout. A tick-size check is a modulo
against RMB 0.01, and binary floating point cannot represent 0.01; quota
accounting runs at RMB 10^10 magnitude where float error is not academic.
"""

import logging
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import Enum
from typing import Dict, Mapping, Optional, Union

logger = logging.getLogger(__name__)
# Library convention: emit nothing unless the host application configures
# logging. Every decision is also returned on the result object, so a caller
# with no handlers still gets the full outcome programmatically.
logger.addHandler(logging.NullHandler())

__all__ = [
    "ConnectRuleError",
    "StockConnectChannel",
    "TradingSession",
    "Board",
    "OrderSide",
    "BoardRules",
    "SecurityReference",
    "ConnectOrder",
    "ConnectOrderResult",
    "ShanghaiShenzhenConnectEngine",
    "NORTHBOUND_DAILY_QUOTA_RMB",
    "TICK_SIZE_A_SHARE_RMB",
    "TICK_SIZE_ETF_RMB",
    "FOREIGN_OWNERSHIP_SUSPEND_PCT",
    "FOREIGN_OWNERSHIP_RESUME_PCT",
    "BOARD_RULES",
    "REJECT_SECURITY_NOT_REGISTERED",
    "REJECT_CHANNEL_SYMBOL_MISMATCH",
    "REJECT_ORDER_TYPE_NOT_SUPPORTED",
    "REJECT_INVALID_BOARD_LOT",
    "REJECT_ORDER_SIZE_EXCEEDED",
    "REJECT_INVALID_TICK_SIZE",
    "REJECT_PRICE_LIMIT_BREACH",
    "REJECT_SECURITY_NOT_BUY_ELIGIBLE",
    "REJECT_FOREIGN_OWNERSHIP_SUSPENDED",
    "REJECT_QUOTA_EXHAUSTED",
    "REJECT_PRE_TRADE_CHECK_FAILED",
]

# ---------------------------------------------------------------------------
# Programme-level constants (HKEX Information Book for Investors, 6 July 2026)
# ---------------------------------------------------------------------------

# "The Northbound Daily Quota is set at RMB 52 billion for each of Shanghai
# Connect and Shenzhen Connect" (§3.4). Trading of A shares and ETFs shares the
# same daily quota. The Aggregate Quota was abolished on 16 August 2016.
NORTHBOUND_DAILY_QUOTA_RMB: Decimal = Decimal("52000000000")

# "the tick size is uniformly set at RMB 0.01 for A shares and RMB 0.001 for
# ETFs" (§3.11).
TICK_SIZE_A_SHARE_RMB: Decimal = Decimal("0.01")
TICK_SIZE_ETF_RMB: Decimal = Decimal("0.001")

# "Once SSE/SZSE informs SEHK that the aggregate foreign shareholding of an
# SSE/SZSE Security reaches 28%, further Northbound buy orders in that
# SSE/SZSE Security will not be allowed, until the aggregate foreign
# shareholding of that SSE/SZSE Security is sold down to 26%." (§3.20)
# Note the hysteresis: the resume threshold is *not* the suspend threshold.
FOREIGN_OWNERSHIP_SUSPEND_PCT: Decimal = Decimal("28")
FOREIGN_OWNERSHIP_RESUME_PCT: Decimal = Decimal("26")


class ConnectRuleError(ValueError):
    """Raised for a caller/configuration error, distinct from an order rejection.

    A malformed order or an incoherent reference-data registration raises. An
    order that is well formed but breaks a Connect rule is *rejected* -- it comes
    back as a ``ConnectOrderResult`` with a rejection code, because that is an
    auditable trading event rather than a bug.
    """


class StockConnectChannel(str, Enum):
    """The two Northbound channels. Each carries its own separate Daily Quota."""

    SHANGHAI_CONNECT = "SHANGHAI_CONNECT"  # SSE Northbound
    SHENZHEN_CONNECT = "SHENZHEN_CONNECT"  # SZSE Northbound


class TradingSession(str, Enum):
    """SSE/SZSE session, which changes how quota exhaustion behaves (§3.4).

    The distinction is load-bearing, not cosmetic: a quota block taken during
    the opening call auction lifts if cancellations restore the balance, while
    one taken during either continuous auction or the closing call auction
    stands for the remainder of the day.
    """

    OPENING_CALL_AUCTION = "OPENING_CALL_AUCTION"    # 09:15 - 09:25
    CONTINUOUS_AUCTION = "CONTINUOUS_AUCTION"        # 09:30 - 11:30, 13:00 - 14:57
    CLOSING_CALL_AUCTION = "CLOSING_CALL_AUCTION"    # 14:57 - 15:00


class Board(str, Enum):
    """Listing board. Board lot, order size cap and price limit all vary by board."""

    SSE_MAIN = "SSE_MAIN"            # 600 / 601 / 603 / 605
    SSE_STAR = "SSE_STAR"            # 688 -- institutional professional investors only
    SZSE_MAIN = "SZSE_MAIN"          # 000 / 001 / 002 / 003
    SZSE_CHINEXT = "SZSE_CHINEXT"    # 300 / 301 -- institutional professional investors only


class OrderSide(str, Enum):
    """Order side.

    An enum rather than a free-text string on purpose. The prior version tested
    ``side.upper() == "BUY"`` and fell through to the sell branch otherwise, so a
    side of ``"LONG"`` silently took the sell path and *credited* the Daily
    Quota. An unparseable side must be impossible to construct, not merely
    unlikely.
    """

    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class BoardRules:
    """Per-board order constraints (§3.9, §3.11)."""

    board_lot: int
    min_order_size: int
    max_order_size: int
    price_limit_pct: Decimal


# "SSE and SZSE Securities are subject to the board lot size of 100 shares or
# units (except for STAR shares whose board lot size is 1 share with minimum
# order size of 200 shares). Buy orders must be in board lots. [...] The maximum
# order size is 1 million shares or units (300,000 shares for stocks on SZSE
# ChiNext Market and 100,000 shares for stocks on SSE STAR Market)" (§3.11).
#
# Price limits: "a +/-10% price limit for stocks traded on SSE/SZSE Main Board;
# and a +/-20% for stocks traded on SSE STAR Market and SZSE ChiNext Market"
# (§3.9).
#
# Note that ChiNext is *not* excepted from the 100-share board lot -- only STAR
# is. Assuming otherwise (a natural guess, since both are registration-based
# boards with a 20% price limit) produces valid orders that this gate rejects.
BOARD_RULES: Dict[Board, BoardRules] = {
    Board.SSE_MAIN: BoardRules(
        board_lot=100, min_order_size=100, max_order_size=1_000_000,
        price_limit_pct=Decimal("10"),
    ),
    Board.SSE_STAR: BoardRules(
        board_lot=1, min_order_size=200, max_order_size=100_000,
        price_limit_pct=Decimal("20"),
    ),
    Board.SZSE_MAIN: BoardRules(
        board_lot=100, min_order_size=100, max_order_size=1_000_000,
        price_limit_pct=Decimal("10"),
    ),
    Board.SZSE_CHINEXT: BoardRules(
        board_lot=100, min_order_size=100, max_order_size=300_000,
        price_limit_pct=Decimal("20"),
    ),
}

# Which channel carries which board. A Shanghai-listed security cannot be routed
# over Shenzhen Connect; each channel's quota is separate, so a mis-routed order
# would otherwise debit the wrong pool.
_CHANNEL_BOARDS: Dict[StockConnectChannel, frozenset] = {
    StockConnectChannel.SHANGHAI_CONNECT: frozenset({Board.SSE_MAIN, Board.SSE_STAR}),
    StockConnectChannel.SHENZHEN_CONNECT: frozenset({Board.SZSE_MAIN, Board.SZSE_CHINEXT}),
}

# Rejection codes. Stable strings: they are written to compliance audit trails.
REJECT_SECURITY_NOT_REGISTERED = "SECURITY_NOT_REGISTERED"
REJECT_CHANNEL_SYMBOL_MISMATCH = "CHANNEL_SYMBOL_MISMATCH"
REJECT_ORDER_TYPE_NOT_SUPPORTED = "ORDER_TYPE_NOT_SUPPORTED"
REJECT_INVALID_BOARD_LOT = "INVALID_BOARD_LOT"
REJECT_ORDER_SIZE_EXCEEDED = "ORDER_SIZE_EXCEEDED"
REJECT_INVALID_TICK_SIZE = "INVALID_TICK_SIZE"
REJECT_PRICE_LIMIT_BREACH = "PRICE_LIMIT_BREACH"
REJECT_SECURITY_NOT_BUY_ELIGIBLE = "SECURITY_NOT_BUY_ELIGIBLE"
REJECT_FOREIGN_OWNERSHIP_SUSPENDED = "FOREIGN_OWNERSHIP_SUSPENDED"
REJECT_QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
REJECT_PRE_TRADE_CHECK_FAILED = "PRE_TRADE_CHECK_FAILED"

_SSE_STAR_PREFIXES = ("688",)
_SSE_MAIN_PREFIXES = ("600", "601", "603", "605")
_SZSE_CHINEXT_PREFIXES = ("300", "301")
_SZSE_MAIN_PREFIXES = ("000", "001", "002", "003")

Number = Union[str, int, Decimal]


def _to_decimal(value: Number, label: str) -> Decimal:
    """Coerce to ``Decimal``, refusing ``float`` and non-finite values.

    ``float`` is refused rather than converted because ``Decimal(0.01)`` is
    ``0.01000000000000000020816681711721685...``, which fails a tick-size modulo
    that the same price written as ``"0.01"`` passes. Silently accepting floats
    would make the tick check depend on how the caller happened to spell the
    price.
    """
    if isinstance(value, float):
        raise ConnectRuleError(
            f"{label} must be a str, int or Decimal, not float: binary floating "
            f"point cannot represent RMB tick sizes exactly. Received {value!r}."
        )
    try:
        result = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ConnectRuleError(f"{label} is not a valid decimal: {value!r}") from exc
    if not result.is_finite():
        raise ConnectRuleError(f"{label} must be finite, received {value!r}")
    return result


def infer_board(symbol: str) -> Board:
    """Infer the listing board from a 6-digit SSE/SZSE code with a venue suffix.

    Accepts ``'600519.SH'`` / ``'000858.SZ'`` style symbols. Raises for anything
    it cannot classify: a guessed board would silently apply the wrong board lot
    and the wrong price limit.
    """
    if not isinstance(symbol, str) or "." not in symbol:
        raise ConnectRuleError(
            f"Symbol must be '<6-digit code>.<SH|SZ>', received {symbol!r}"
        )
    code, _, suffix = symbol.partition(".")
    suffix = suffix.upper()
    if len(code) != 6 or not code.isdigit():
        raise ConnectRuleError(
            f"SSE/SZSE stock codes are 6 digits, received {code!r} in {symbol!r}"
        )
    if suffix == "SH":
        if code.startswith(_SSE_STAR_PREFIXES):
            return Board.SSE_STAR
        if code.startswith(_SSE_MAIN_PREFIXES):
            return Board.SSE_MAIN
    elif suffix == "SZ":
        if code.startswith(_SZSE_CHINEXT_PREFIXES):
            return Board.SZSE_CHINEXT
        if code.startswith(_SZSE_MAIN_PREFIXES):
            return Board.SZSE_MAIN
    else:
        raise ConnectRuleError(
            f"Unknown venue suffix {suffix!r} in {symbol!r}; expected 'SH' or 'SZ'"
        )
    raise ConnectRuleError(
        f"Cannot classify {symbol!r} onto a Northbound-eligible board. Pass the "
        f"board explicitly to register_security() if this code is valid."
    )


@dataclass(frozen=True)
class SecurityReference:
    """Reference data for one Connect Security, supplied by the caller.

    ``previous_close`` anchors the price limit and must be the SSE/SZSE previous
    closing price for the current trading day; a stale one shifts both limits.
    ``buy_eligible`` carries SEHK's sell-only designation (§ "Sell-only SSE A
    shares"): a security that falls out of the eligibility criteria, or is placed
    under risk alert, remains sellable but may not be bought.
    """

    symbol: str
    board: Board
    previous_close: Decimal
    is_etf: bool = False
    buy_eligible: bool = True
    price_limit_pct: Optional[Decimal] = None

    @property
    def tick_size(self) -> Decimal:
        return TICK_SIZE_ETF_RMB if self.is_etf else TICK_SIZE_A_SHARE_RMB

    @property
    def effective_price_limit_pct(self) -> Decimal:
        if self.price_limit_pct is not None:
            return self.price_limit_pct
        return BOARD_RULES[self.board].price_limit_pct


@dataclass(frozen=True)
class ConnectOrder:
    """A Northbound order.

    ``limit_price`` is in RMB. Northbound trading is quoted and settled in RMB
    only (§3.7) -- not, as is sometimes assumed, in CNH. A Hong Kong investor
    funds the trade with offshore RMB, but the price, the quota and the money
    settlement with HKSCC are all denominated in RMB.

    There is no order-type field taking anything but a limit: "For Northbound
    trading, only limit orders [...] will be accepted for SSE Securities and SZSE
    Securities throughout the day" (§3.8). ``is_market_order`` exists purely so a
    caller routing from a mixed order flow gets an auditable rejection rather
    than an accidental limit order at whatever price it carried.
    """

    order_id: str
    symbol: str
    channel: StockConnectChannel
    side: OrderSide
    quantity: int
    limit_price: Decimal
    is_market_order: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ConnectRuleError("order_id must be a non-empty string")
        if not isinstance(self.side, OrderSide):
            raise ConnectRuleError(
                f"side must be an OrderSide, received {self.side!r}"
            )
        if not isinstance(self.channel, StockConnectChannel):
            raise ConnectRuleError(
                f"channel must be a StockConnectChannel, received {self.channel!r}"
            )
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int):
            raise ConnectRuleError(
                f"quantity must be a whole number of shares (int), received "
                f"{self.quantity!r}. Fractional A-share quantities do not exist."
            )
        if self.quantity <= 0:
            raise ConnectRuleError(f"quantity must be positive, received {self.quantity}")
        price = _to_decimal(self.limit_price, "limit_price")
        if price <= 0:
            raise ConnectRuleError(f"limit_price must be positive, received {price}")
        object.__setattr__(self, "limit_price", price)

    @property
    def notional_rmb(self) -> Decimal:
        return Decimal(self.quantity) * self.limit_price


@dataclass(frozen=True)
class ConnectOrderResult:
    """Outcome of submitting one order through the gate."""

    order_id: str
    symbol: str
    channel: StockConnectChannel
    accepted: bool
    rejection_code: Optional[str]
    rejection_reason: Optional[str]
    notional_rmb: Decimal
    daily_quota_balance_rmb: Decimal
    northbound_buying_suspended: bool
    audit_notes: str


@dataclass
class _ChannelQuotaState:
    """Daily Quota Balance components for one channel.

    HKEX publishes the balance as an identity, not a running counter:

        Daily Quota Balance = Daily Quota - Buy Orders + Sell Trades + Adjustments

    Holding the three terms separately rather than mutating a single balance
    keeps that identity auditable and makes the asymmetry impossible to lose:
    quota is consumed by buy **orders** at submission, but restored only by sell
    **trades** at execution.
    """

    buy_orders_rmb: Decimal = Decimal("0")
    sell_trades_rmb: Decimal = Decimal("0")
    adjustments_rmb: Decimal = Decimal("0")
    # Latched once the balance hits zero during a continuous or closing call
    # auction: "no further buy orders will be accepted for the remainder of the
    # day." A later sell trade restoring the balance does not lift this.
    buying_suspended_for_day: bool = False

    @property
    def balance_rmb(self) -> Decimal:
        return (
            NORTHBOUND_DAILY_QUOTA_RMB
            - self.buy_orders_rmb
            + self.sell_trades_rmb
            + self.adjustments_rmb
        )


@dataclass
class _LiveOrder:
    """A submitted order still holding a reservation."""

    order: ConnectOrder
    unfilled_quantity: int


class ShanghaiShenzhenConnectEngine:
    """Northbound Stock Connect pre-submission gate and Daily Quota ledger.

    Lifecycle for one trading day::

        engine = ShanghaiShenzhenConnectEngine()
        engine.register_security(...)                  # once per security per day
        engine.start_trading_day({"600519.SH": 1_000}) # market-open shareholding
        engine.submit_order(order, TradingSession.CONTINUOUS_AUCTION)
        engine.record_fill(order_id, filled_quantity)
        engine.cancel_order(order_id)                  # releases the unfilled part

    ``start_trading_day`` must be called before any order. The market-open
    shareholding snapshot is an *input*, mirroring CCASS, which "will take a
    snapshot of the Connect Securities holdings under each SPSA [...] to perform
    pre-trade checking". The engine never infers a position it was not told
    about; an unlisted symbol has an opening position of zero and is therefore
    unsellable, which is the correct fail-closed default.
    """

    def __init__(self) -> None:
        self._securities: Dict[str, SecurityReference] = {}
        self._quotas: Dict[StockConnectChannel, _ChannelQuotaState] = {
            channel: _ChannelQuotaState() for channel in StockConnectChannel
        }
        self._opening_positions: Dict[str, int] = {}
        self._cumulative_sell_quantity: Dict[str, int] = {}
        self._live_orders: Dict[str, _LiveOrder] = {}
        self._foreign_ownership_pct: Dict[str, Decimal] = {}
        self._foreign_ownership_suspended: Dict[str, bool] = {}
        self._day_started = False

    # -- reference data ---------------------------------------------------

    def register_security(
        self,
        symbol: str,
        previous_close: Number,
        *,
        board: Optional[Board] = None,
        is_etf: bool = False,
        buy_eligible: bool = True,
        price_limit_pct: Optional[Number] = None,
    ) -> SecurityReference:
        """Register a Connect Security's reference data for the current day.

        ``previous_close`` is the SSE/SZSE previous closing price, which anchors
        both price limits. ``price_limit_pct`` is required for ETFs: they are
        "+/-10% [...] under normal circumstances, and +/-20% for some ETFs as
        specified by SSE/SZSE" (§3.9), and the specified set is a published list,
        not a property of the code. Defaulting an ETF to 10% would reject valid
        orders in the 20% set, so the caller must state it.
        """
        resolved_board = board if board is not None else infer_board(symbol)
        if not isinstance(resolved_board, Board):
            raise ConnectRuleError(f"board must be a Board, received {board!r}")
        close = _to_decimal(previous_close, "previous_close")
        if close <= 0:
            raise ConnectRuleError(f"previous_close must be positive, received {close}")
        limit_pct: Optional[Decimal] = None
        if price_limit_pct is not None:
            limit_pct = _to_decimal(price_limit_pct, "price_limit_pct")
            if limit_pct <= 0:
                raise ConnectRuleError(
                    f"price_limit_pct must be positive, received {limit_pct}"
                )
        elif is_etf:
            raise ConnectRuleError(
                f"price_limit_pct is required for ETF {symbol!r}: SSE/SZSE ETFs are "
                f"+/-10% under normal circumstances but +/-20% for a published set, "
                f"which cannot be inferred from the stock code."
            )
        reference = SecurityReference(
            symbol=symbol,
            board=resolved_board,
            previous_close=close,
            is_etf=is_etf,
            buy_eligible=buy_eligible,
            price_limit_pct=limit_pct,
        )
        self._securities[symbol] = reference
        return reference

    def set_foreign_shareholding(self, symbol: str, aggregate_pct: Number) -> bool:
        """Record aggregate foreign shareholding and apply the 28% / 26% latch.

        Returns whether Northbound buying in this security is now suspended.
        The thresholds are asymmetric by rule: buying stops at 28% and resumes
        only once the holding is sold down to 26% (§3.20). Treating 28% as both
        thresholds would let buying flap on and off around the boundary.
        """
        pct = _to_decimal(aggregate_pct, "aggregate_pct")
        if pct < 0 or pct > 100:
            raise ConnectRuleError(
                f"aggregate_pct must be a percentage in [0, 100], received {pct}"
            )
        self._foreign_ownership_pct[symbol] = pct
        suspended = self._foreign_ownership_suspended.get(symbol, False)
        if pct >= FOREIGN_OWNERSHIP_SUSPEND_PCT:
            suspended = True
        elif pct <= FOREIGN_OWNERSHIP_RESUME_PCT:
            suspended = False
        self._foreign_ownership_suspended[symbol] = suspended
        return suspended

    # -- day lifecycle ----------------------------------------------------

    def start_trading_day(self, opening_positions: Mapping[str, int]) -> None:
        """Reset per-day state and load the market-open shareholding snapshot.

        Resets the Daily Quota -- "The Daily Quota will be reset every day.
        Unused Daily Quota will NOT be carried over" (§3.4) -- clears the
        continuous-session buying suspension, and replaces the pre-trade
        checking baseline.

        Only call this on an actual Northbound trading day. Northbound trading
        runs when *both* the Hong Kong and Mainland markets are open (§3.6), so
        the Mainland trading calendar alone is not the right trigger.

        The registered security master and the foreign-ownership latches survive
        the reset; both are multi-day state. Re-register any security whose
        previous close has moved, which is every security that traded.
        """
        positions: Dict[str, int] = {}
        for symbol, quantity in opening_positions.items():
            if isinstance(quantity, bool) or not isinstance(quantity, int):
                raise ConnectRuleError(
                    f"Opening position for {symbol!r} must be an int, received "
                    f"{quantity!r}"
                )
            if quantity < 0:
                raise ConnectRuleError(
                    f"Opening position for {symbol!r} must be non-negative, "
                    f"received {quantity}. Northbound holdings cannot be short."
                )
            positions[symbol] = quantity
        self._opening_positions = positions
        self._cumulative_sell_quantity = {}
        self._live_orders = {}
        self._quotas = {channel: _ChannelQuotaState() for channel in StockConnectChannel}
        self._day_started = True
        logger.info(
            "Northbound trading day started: %d securities with an opening position, "
            "Daily Quota reset to RMB %s per channel.",
            len(positions), NORTHBOUND_DAILY_QUOTA_RMB,
        )

    def daily_quota_balance(self, channel: StockConnectChannel) -> Decimal:
        """Current Daily Quota Balance for one channel.

        May legitimately exceed ``NORTHBOUND_DAILY_QUOTA_RMB``: the quota is
        applied "on a 'net buy' basis" (§3.4), so a net-sell day credits more
        than was consumed. Clamping the balance at the Daily Quota -- as the
        prior version did -- silently discards that headroom.
        """
        return self._quotas[channel].balance_rmb

    def is_buying_suspended(self, channel: StockConnectChannel) -> bool:
        """Whether the day's continuous-session quota suspension has latched."""
        return self._quotas[channel].buying_suspended_for_day

    def apply_quota_adjustment(
        self, channel: StockConnectChannel, amount_rmb: Number, reason: str
    ) -> Decimal:
        """Apply an ``Adjustments`` term to the Daily Quota Balance.

        The published formula carries an ``+ Adjustments`` term whose triggers
        HKEX does not enumerate in the Information Book. This hook exists so a
        participant reconciling against SEHK's disseminated balance can record
        one; it is not a licence to invent quota.
        """
        delta = _to_decimal(amount_rmb, "amount_rmb")
        if not reason or not reason.strip():
            raise ConnectRuleError("A quota adjustment requires a non-empty reason")
        state = self._quotas[channel]
        state.adjustments_rmb += delta
        logger.info(
            "Quota adjustment on %s: %s RMB (%s). Balance now %s RMB.",
            channel.value, delta, reason, state.balance_rmb,
        )
        return state.balance_rmb

    # -- order flow -------------------------------------------------------

    def submit_order(
        self, order: ConnectOrder, session: TradingSession
    ) -> ConnectOrderResult:
        """Run every pre-submission check and, if all pass, reserve against limits.

        Checks run cheapest-and-most-structural first so the rejection code names
        the *primary* defect: an order that is both an odd lot and over quota
        reports the odd lot, which is what the caller must fix first.

        Accepting a buy order consumes quota immediately, because the published
        formula deducts buy **orders**, not buy trades. Accepting a sell order
        consumes pre-trade-checking headroom immediately, because SEHK checks
        "the cumulative sell quantity for the day".
        """
        if not self._day_started:
            raise ConnectRuleError(
                "start_trading_day() must be called before submitting orders: the "
                "pre-trade check has no market-open position baseline until then."
            )
        if not isinstance(session, TradingSession):
            raise ConnectRuleError(
                f"session must be a TradingSession, received {session!r}"
            )
        if order.order_id in self._live_orders:
            raise ConnectRuleError(
                f"Duplicate order_id {order.order_id!r} is already live. Reusing an "
                f"id would make its reservation impossible to release correctly."
            )

        state = self._quotas[order.channel]
        reference = self._securities.get(order.symbol)

        if reference is None:
            return self._reject(
                order, state, REJECT_SECURITY_NOT_REGISTERED,
                f"No reference data registered for {order.symbol}. Board lot, price "
                f"limit and buy eligibility are unknown, so the order cannot be "
                f"validated.",
            )

        if reference.board not in _CHANNEL_BOARDS[order.channel]:
            return self._reject(
                order, state, REJECT_CHANNEL_SYMBOL_MISMATCH,
                f"{order.symbol} lists on {reference.board.value}, which is not "
                f"reachable over {order.channel.value}. Each channel carries its "
                f"own separate Daily Quota.",
            )

        if order.is_market_order:
            return self._reject(
                order, state, REJECT_ORDER_TYPE_NOT_SUPPORTED,
                "Only limit orders are accepted for Northbound trading in SSE and "
                "SZSE Securities throughout the day.",
            )

        rules = BOARD_RULES[reference.board]

        size_rejection = self._check_order_size(order, rules)
        if size_rejection is not None:
            return self._reject(order, state, *size_rejection)

        if order.limit_price % reference.tick_size != 0:
            return self._reject(
                order, state, REJECT_INVALID_TICK_SIZE,
                f"Limit price {order.limit_price} is not a multiple of the "
                f"RMB {reference.tick_size} tick size for "
                f"{'ETFs' if reference.is_etf else 'A shares'}.",
            )

        lower, upper = self.price_limits(order.symbol)
        if not lower <= order.limit_price <= upper:
            return self._reject(
                order, state, REJECT_PRICE_LIMIT_BREACH,
                f"Limit price {order.limit_price} is outside the "
                f"+/-{reference.effective_price_limit_pct}% price limit "
                f"[{lower}, {upper}] anchored on the previous close of "
                f"{reference.previous_close}.",
            )

        if order.side is OrderSide.BUY:
            return self._submit_buy(order, reference, state, session)
        return self._submit_sell(order, state)

    def price_limits(self, symbol: str) -> "tuple[Decimal, Decimal]":
        """Daily lower and upper price limit for a registered security.

        Limits are rounded to the security's tick size, half up. SSE and SZSE are
        authoritative on the rounding of their own limit prices; a limit price
        landing exactly on the boundary should be confirmed against the venue's
        published limit rather than against this arithmetic.
        """
        reference = self._securities.get(symbol)
        if reference is None:
            raise ConnectRuleError(f"No reference data registered for {symbol!r}")
        pct = reference.effective_price_limit_pct / Decimal("100")
        tick = reference.tick_size
        lower = (reference.previous_close * (Decimal("1") - pct)).quantize(
            tick, rounding=ROUND_HALF_UP
        )
        upper = (reference.previous_close * (Decimal("1") + pct)).quantize(
            tick, rounding=ROUND_HALF_UP
        )
        return lower, upper

    def record_fill(self, order_id: str, filled_quantity: int) -> Decimal:
        """Record a (partial or full) execution of a live order.

        A buy fill does **not** change the quota: the value was already consumed
        when the order was accepted, and a filled buy is exactly the net buy the
        quota is meant to limit. What the fill changes is how much remains
        releasable if the order is later cancelled.

        A sell fill *does* credit the quota, because the formula's restoring term
        is "Sell Trades" -- executed, not merely submitted. Crediting on
        submission instead, as the prior version did, would let an unfilled sell
        order manufacture buying power that SEHK never granted.

        Returns the channel's Daily Quota Balance after the fill.
        """
        live = self._live_orders.get(order_id)
        if live is None:
            raise ConnectRuleError(
                f"No live order {order_id!r}. A fill for an unknown or already "
                f"completed order cannot be reconciled against its reservation."
            )
        if isinstance(filled_quantity, bool) or not isinstance(filled_quantity, int):
            raise ConnectRuleError(
                f"filled_quantity must be an int, received {filled_quantity!r}"
            )
        if filled_quantity <= 0:
            raise ConnectRuleError(
                f"filled_quantity must be positive, received {filled_quantity}"
            )
        if filled_quantity > live.unfilled_quantity:
            raise ConnectRuleError(
                f"Fill of {filled_quantity} exceeds the {live.unfilled_quantity} "
                f"unfilled shares on order {order_id!r}. An over-fill means the "
                f"local order state has already diverged from the venue's."
            )

        live.unfilled_quantity -= filled_quantity
        state = self._quotas[live.order.channel]
        if live.order.side is OrderSide.SELL:
            credit = Decimal(filled_quantity) * live.order.limit_price
            state.sell_trades_rmb += credit
            logger.info(
                "Sell trade on %s: %d shares of %s credited RMB %s to the %s quota. "
                "Balance now RMB %s.",
                live.order.order_id, filled_quantity, live.order.symbol, credit,
                live.order.channel.value, state.balance_rmb,
            )
        if live.unfilled_quantity == 0:
            del self._live_orders[order_id]
        return state.balance_rmb

    def cancel_order(self, order_id: str) -> Decimal:
        """Cancel a live order, releasing the reservation on its unfilled part.

        For a buy, the unfilled notional is returned to the Daily Quota Balance:
        quota is consumed by orders, so a withdrawn order withdraws its
        consumption. HKEX notes this directly -- during the opening call auction
        "as order cancellation is common [...] the Daily Quota Balance may resume
        to a positive level [...] SEHK will again accept Northbound buy orders."

        For a sell, the unfilled quantity is returned to the pre-trade checking
        headroom. HKEX states the check is against "the cumulative sell quantity
        for the day" without saying explicitly whether a cancelled sell is
        excluded; releasing it matches the buy-side treatment and CSC's
        behaviour of returning reserved shares on cancellation, but a participant
        holding the definitive CSC specification should confirm it.

        Returns the channel's Daily Quota Balance after the release. Note that a
        release never lifts a latched continuous-session suspension.
        """
        live = self._live_orders.pop(order_id, None)
        if live is None:
            raise ConnectRuleError(f"No live order {order_id!r} to cancel")
        state = self._quotas[live.order.channel]
        if live.order.side is OrderSide.BUY:
            release = Decimal(live.unfilled_quantity) * live.order.limit_price
            state.buy_orders_rmb -= release
            logger.info(
                "Cancelled buy order %s: released RMB %s of %s quota. Balance now "
                "RMB %s.",
                order_id, release, live.order.channel.value, state.balance_rmb,
            )
        else:
            symbol = live.order.symbol
            self._cumulative_sell_quantity[symbol] = (
                self._cumulative_sell_quantity.get(symbol, 0) - live.unfilled_quantity
            )
            logger.info(
                "Cancelled sell order %s: released %d shares of %s pre-trade "
                "checking headroom.",
                order_id, live.unfilled_quantity, symbol,
            )
        return state.balance_rmb

    def sellable_quantity(self, symbol: str) -> int:
        """Shares still sellable today under SEHK pre-trade checking.

        This is the market-open shareholding position less the cumulative sell
        quantity already committed today. Shares *bought* today never appear
        here, which is precisely the mechanism by which day trading is
        prohibited: "Hong Kong and overseas investors buying SSE and SZSE
        Securities on T-day can only sell the shares on and after T+1" (§3.12).
        """
        opening = self._opening_positions.get(symbol, 0)
        return opening - self._cumulative_sell_quantity.get(symbol, 0)

    # -- internals --------------------------------------------------------

    @staticmethod
    def _check_order_size(
        order: ConnectOrder, rules: BoardRules
    ) -> Optional["tuple[str, str]"]:
        """Board lot, minimum and maximum order size.

        Board lot and minimum size apply to **buy** orders only: "Buy orders must
        be in board lots. Odd lot trading is only available for sell orders and
        all odd lots should be sold in one single order" (§3.11). Rejecting an
        odd-lot *sell* would strand exactly the holdings -- corporate-action
        remnants -- that the odd-lot sell rule exists to let an investor unwind.

        The maximum order size applies to both sides.
        """
        if order.quantity > rules.max_order_size:
            return (
                REJECT_ORDER_SIZE_EXCEEDED,
                f"Quantity {order.quantity} exceeds the maximum order size of "
                f"{rules.max_order_size} shares for this board.",
            )
        if order.side is OrderSide.BUY:
            if order.quantity < rules.min_order_size:
                return (
                    REJECT_INVALID_BOARD_LOT,
                    f"BUY quantity {order.quantity} is below the minimum order size "
                    f"of {rules.min_order_size} shares for this board.",
                )
            if order.quantity % rules.board_lot != 0:
                return (
                    REJECT_INVALID_BOARD_LOT,
                    f"BUY quantity {order.quantity} is not a multiple of the "
                    f"{rules.board_lot}-share board lot for this board.",
                )
        return None

    def _submit_buy(
        self,
        order: ConnectOrder,
        reference: SecurityReference,
        state: _ChannelQuotaState,
        session: TradingSession,
    ) -> ConnectOrderResult:
        if not reference.buy_eligible:
            return self._reject(
                order, state, REJECT_SECURITY_NOT_BUY_ELIGIBLE,
                f"{order.symbol} is designated sell-only and is restricted from "
                f"buying. Selling remains permitted.",
            )

        if self._foreign_ownership_suspended.get(order.symbol, False):
            pct = self._foreign_ownership_pct.get(order.symbol)
            return self._reject(
                order, state, REJECT_FOREIGN_OWNERSHIP_SUSPENDED,
                f"Northbound buying in {order.symbol} is suspended: aggregate "
                f"foreign shareholding reached {FOREIGN_OWNERSHIP_SUSPEND_PCT}% "
                f"(currently {pct}%) and buying resumes only once it is sold down "
                f"to {FOREIGN_OWNERSHIP_RESUME_PCT}%.",
            )

        # Quota gate. The order that *exhausts* the quota is accepted; what the
        # rule blocks is the order that arrives once the balance is already
        # depleted. Rejecting on `balance < notional` -- as the prior version did
        # -- both refuses an order SEHK would take and makes it impossible for
        # the balance to ever go negative, which is a state HKEX explicitly
        # describes ("or the Daily Quota is exceeded").
        if state.buying_suspended_for_day:
            return self._reject(
                order, state, REJECT_QUOTA_EXHAUSTED,
                f"Northbound buying on {order.channel.value} is suspended for the "
                f"remainder of the day: the Daily Quota was exhausted during a "
                f"continuous or closing call auction session.",
            )
        if state.balance_rmb <= 0:
            # The balance was already depleted when this order arrived -- reached
            # by an adjustment, or by starting a session already exhausted. In a
            # call/continuous auction session that latches for the day; in the
            # opening call auction it does not, because cancellations there can
            # restore the balance and SEHK resumes accepting buy orders.
            if session is not TradingSession.OPENING_CALL_AUCTION:
                state.buying_suspended_for_day = True
            return self._reject(
                order, state, REJECT_QUOTA_EXHAUSTED,
                f"Daily Quota Balance on {order.channel.value} is "
                f"{state.balance_rmb} RMB, so no new Northbound buy order is "
                f"accepted"
                + (
                    " until cancellations restore it to a positive level before the "
                    "opening call auction ends."
                    if session is TradingSession.OPENING_CALL_AUCTION
                    else " for the remainder of the day."
                ),
            )

        state.buy_orders_rmb += order.notional_rmb
        self._live_orders[order.order_id] = _LiveOrder(order, order.quantity)

        if state.balance_rmb <= 0 and session is not TradingSession.OPENING_CALL_AUCTION:
            state.buying_suspended_for_day = True
            logger.warning(
                "Daily Quota exhausted on %s during %s. No further Northbound buy "
                "orders will be accepted for the remainder of the day. Balance: "
                "RMB %s.",
                order.channel.value, session.value, state.balance_rmb,
            )

        return self._accept(
            order, state,
            f"Accepted BUY {order.quantity} {order.symbol} @ RMB "
            f"{order.limit_price} ({session.value}). Consumed RMB "
            f"{order.notional_rmb} of {order.channel.value} quota.",
        )

    def _submit_sell(
        self, order: ConnectOrder, state: _ChannelQuotaState
    ) -> ConnectOrderResult:
        # Sells are never quota-gated: "investors are always allowed to sell their
        # cross-boundary securities regardless of the quota balance" (§3.4).
        available = self.sellable_quantity(order.symbol)
        if order.quantity > available:
            opening = self._opening_positions.get(order.symbol, 0)
            already = self._cumulative_sell_quantity.get(order.symbol, 0)
            return self._reject(
                order, state, REJECT_PRE_TRADE_CHECK_FAILED,
                f"Pre-trade check failed on {order.symbol}: selling "
                f"{order.quantity} would take the day's cumulative sell quantity to "
                f"{already + order.quantity}, above the market-open shareholding "
                f"position of {opening}. Shares bought today are not in that "
                f"position and cannot be sold until T+1.",
            )

        self._cumulative_sell_quantity[order.symbol] = (
            self._cumulative_sell_quantity.get(order.symbol, 0) + order.quantity
        )
        self._live_orders[order.order_id] = _LiveOrder(order, order.quantity)
        return self._accept(
            order, state,
            f"Accepted SELL {order.quantity} {order.symbol} @ RMB "
            f"{order.limit_price}. Remaining sellable today: "
            f"{self.sellable_quantity(order.symbol)} shares.",
        )

    def _accept(
        self, order: ConnectOrder, state: _ChannelQuotaState, notes: str
    ) -> ConnectOrderResult:
        logger.info(notes)
        return ConnectOrderResult(
            order_id=order.order_id,
            symbol=order.symbol,
            channel=order.channel,
            accepted=True,
            rejection_code=None,
            rejection_reason=None,
            notional_rmb=order.notional_rmb,
            daily_quota_balance_rmb=state.balance_rmb,
            northbound_buying_suspended=state.buying_suspended_for_day,
            audit_notes=notes,
        )

    def _reject(
        self,
        order: ConnectOrder,
        state: _ChannelQuotaState,
        code: str,
        reason: str,
    ) -> ConnectOrderResult:
        notes = f"{code}: {reason}"
        logger.warning("Order %s rejected -- %s", order.order_id, notes)
        return ConnectOrderResult(
            order_id=order.order_id,
            symbol=order.symbol,
            channel=order.channel,
            accepted=False,
            rejection_code=code,
            rejection_reason=reason,
            notional_rmb=order.notional_rmb,
            daily_quota_balance_rmb=state.balance_rmb,
            northbound_buying_suspended=state.buying_suspended_for_day,
            audit_notes=notes,
        )

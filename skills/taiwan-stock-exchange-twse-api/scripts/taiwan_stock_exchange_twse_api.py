"""TWSE (Taiwan Stock Exchange) pre-trade order validation.

Encodes the TWSE centralised-market cash-equity rules published on the
exchange's own "集中市場交易制度介紹 / Trading Mechanism Introduction" page,
verified 2 September 2026 against both the Chinese and English editions:

* **Tick size (升降單位) -- TWSE Operating Rules Article 62.** Six bands for
  equities, and a *different* schedule for ETFs/ETNs/REITs and another for
  warrants. Bands are 「10元至未滿50元」 -- lower-inclusive, upper-EXCLUSIVE --
  so a price sitting exactly on a boundary takes the COARSER tick of the band
  above. The equity schedule is 0.01 / 0.05 / 0.10 / 0.50 / 1.00 / 5.00; the
  two-tier 0.01-below-50 / 0.05-at-or-above-50 table belongs to ETFs, ETNs and
  REITs alone. Applying it to a stock accepts prices TWSE rejects: TSMC (2330)
  at NT$1,102.50 is off-grid, the NT$1,000-and-above equity tick being NT$5.

* **Daily price limit (升降幅度) -- Operating Rules Article 63.** +/-10% since
  1 June 2015 (raised from 7%), measured from the day's *auction reference
  price* (開盤競價基準), not unconditionally from the previous close -- see
  ``reference_price`` below. The percentage alone does not give the limit
  price: Article 62 still applies, so the computed bound is moved onto the tick
  grid **toward** the reference price, because the outward neighbour would
  breach the 10% band. TWSE's own worked example: reference 40.60 ->
  40.60 x 1.1 = 44.66 -> limit-up **44.65** (not 44.70); 40.60 x 0.9 = 36.54 ->
  limit-down **36.55** (not 36.50). If the converted amount is under NT$0.01 it
  counts as NT$0.01, and no price may fall below NT$0.01.

* **Trading unit (交易單位).** 1,000 shares/units for stocks, ETFs, ETNs, REITs,
  TDRs and warrants. Secondary listings of foreign stocks and offshore ETFs are
  NOT bound to 1,000 -- pass ``trading_unit`` explicitly for those. Odd lots are
  1-999 shares and trade on their own books.

* **Order type x session.** Market, IOC and FOK exist only in the continuous
  session (9:00-13:25). The opening and closing call auctions accept limit-ROD
  only; anything else is returned (退單). Market orders are additionally barred
  for securities with no price limit (including a new common stock's first five
  sessions) and for short sales of securities restricted below the reference
  price.

* **Short selling.** TWSE has no US-style "locate". A short sale is a distinct
  order-ticket type (委託書種類) -- 融券 (margin short) or 借券賣出 (SBL short) --
  arranged before entry, which is what makes naked shorting structurally
  impossible rather than merely prohibited. The live constraint is the
  平盤以下 rule: a short may not be priced *below* the reference price unless
  the security appears on that day's 平盤下得融(借)券賣出 list. Pricing exactly
  *at* the reference is always allowed. Odd-lot sessions forbid margin and SBL
  trading outright (不得使用信用交易及借券賣出), so an odd-lot short is never
  valid.

This module is a client-side pre-trade filter. The TWSE matching engine is
authoritative and can reject an order this engine approves. It opens no
sockets: there is no public TWSE order-entry API, and orders reach the
matching engine only through a TWSE member securities firm's connection.

Sources:
  TWSE 集中市場交易制度介紹 (tick table, price limits, trading units,
  sessions, order types, odd-lot rules)
    https://www.twse.com.tw/zh/products/system/trading.html
    https://www.twse.com.tw/en/products/system/trading.html
  TWSE 平盤下得融(借)券賣出之證券名單 (daily eligibility list and its notes)
    https://www.twse.com.tw/zh/trading/margin/twt92u.html
  TWSE foreign investor overview (registration and Investor ID)
    https://www.twse.com.tw/en/products/education/foreign/overview.html
"""
import logging
import math
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# Security classes
# --------------------------------------------------------------------------
# The class selects the tick schedule and odd-lot eligibility. It is reference
# data about the instrument -- it can NOT be inferred from the price, and only
# loosely from the code, so the caller must supply it.
#
# EQUITY covers TWSE's "*Equity Product" tick column: common stock, foreign
# stock (both first and second listings), TDRs, closed-end securities
# investment trust fund beneficiary certificates, rights/payment certificates
# and preferred shares with call warrants.
SECURITY_CLASS_EQUITY = "EQUITY"
# ETFs (domestic, foreign-component, futures, leveraged/inverse, offshore,
# active) and REIT beneficiary securities (受益證券).
SECURITY_CLASS_ETF_REIT = "ETF_REIT"
# ETNs share the ETF tick schedule but, like warrants, may NOT trade odd lot.
SECURITY_CLASS_ETN = "ETN"
# Call/put warrants (認購售權證) and company warrants (認股權憑證).
SECURITY_CLASS_WARRANT = "WARRANT"

VALID_SECURITY_CLASSES = (
    SECURITY_CLASS_EQUITY,
    SECURITY_CLASS_ETF_REIT,
    SECURITY_CLASS_ETN,
    SECURITY_CLASS_WARRANT,
)

# Classes barred from both odd-lot sessions:
# 「認購(售)權證及ETN不得進行零股交易」.
ODD_LOT_INELIGIBLE_CLASSES = (SECURITY_CLASS_ETN, SECURITY_CLASS_WARRANT)

# --------------------------------------------------------------------------
# Tick schedules -- (EXCLUSIVE upper bound of the band, tick). A None bound is
# the open-ended top band. Operating Rules Art. 62; bands read 「X元至未滿Y元」.
# --------------------------------------------------------------------------
EQUITY_TICK_SCHEDULE: Tuple[Tuple[Optional[Decimal], Decimal], ...] = (
    (Decimal("10"), Decimal("0.01")),
    (Decimal("50"), Decimal("0.05")),
    (Decimal("100"), Decimal("0.10")),
    (Decimal("500"), Decimal("0.50")),
    (Decimal("1000"), Decimal("1.00")),
    (None, Decimal("5.00")),
)

# ETFs, ETNs and REITs keep a deliberately fine two-tier grid at every price.
ETF_ETN_REIT_TICK_SCHEDULE: Tuple[Tuple[Optional[Decimal], Decimal], ...] = (
    (Decimal("50"), Decimal("0.01")),
    (None, Decimal("0.05")),
)

WARRANT_TICK_SCHEDULE: Tuple[Tuple[Optional[Decimal], Decimal], ...] = (
    (Decimal("5"), Decimal("0.01")),
    (Decimal("10"), Decimal("0.05")),
    (Decimal("50"), Decimal("0.10")),
    (Decimal("100"), Decimal("0.50")),
    (Decimal("500"), Decimal("1.00")),
    (None, Decimal("5.00")),
)

TICK_SCHEDULES: Dict[str, Tuple[Tuple[Optional[Decimal], Decimal], ...]] = {
    SECURITY_CLASS_EQUITY: EQUITY_TICK_SCHEDULE,
    SECURITY_CLASS_ETF_REIT: ETF_ETN_REIT_TICK_SCHEDULE,
    SECURITY_CLASS_ETN: ETF_ETN_REIT_TICK_SCHEDULE,
    SECURITY_CLASS_WARRANT: WARRANT_TICK_SCHEDULE,
}

# --------------------------------------------------------------------------
# Sessions (交易時段)
# --------------------------------------------------------------------------
SESSION_OPENING_CALL_AUCTION = "OPENING_CALL_AUCTION"      # 08:30-09:00 entry
SESSION_CONTINUOUS = "CONTINUOUS"                          # 09:00-13:25 逐筆
SESSION_CLOSING_CALL_AUCTION = "CLOSING_CALL_AUCTION"      # 13:25-13:30
SESSION_INTRADAY_ODD_LOT = "INTRADAY_ODD_LOT"              # 09:00-13:30 盤中零股
SESSION_AFTER_HOURS_ODD_LOT = "AFTER_HOURS_ODD_LOT"        # 13:40-14:30 盤後零股

VALID_SESSIONS = (
    SESSION_OPENING_CALL_AUCTION,
    SESSION_CONTINUOUS,
    SESSION_CLOSING_CALL_AUCTION,
    SESSION_INTRADAY_ODD_LOT,
    SESSION_AFTER_HOURS_ODD_LOT,
)

ODD_LOT_SESSIONS = (SESSION_INTRADAY_ODD_LOT, SESSION_AFTER_HOURS_ODD_LOT)

# Market/IOC/FOK exist only in the continuous session; every other session is a
# call auction that accepts limit-ROD alone.
CONTINUOUS_ONLY_SESSIONS = (SESSION_CONTINUOUS,)

# --------------------------------------------------------------------------
# Order ticket type (委託書種類) -- a real order-entry field, distinct from the
# buy/sell flag (買賣別). A short sale is SELL on a 融券 or 借券 ticket.
# --------------------------------------------------------------------------
TICKET_CASH = "CASH"                    # 現股
TICKET_MARGIN_LONG = "MARGIN_LONG"      # 融資 (margin-financed buy)
TICKET_MARGIN_SHORT = "MARGIN_SHORT"    # 融券 (margin short sale)
TICKET_SBL_SHORT = "SBL_SHORT"          # 借券賣出 (SBL short sale)

VALID_TICKET_TYPES = (
    TICKET_CASH,
    TICKET_MARGIN_LONG,
    TICKET_MARGIN_SHORT,
    TICKET_SBL_SHORT,
)

SHORT_SALE_TICKETS = (TICKET_MARGIN_SHORT, TICKET_SBL_SHORT)
# 信用交易 (margin) and 借券賣出 (SBL) are both barred from odd-lot trading.
CREDIT_TICKETS = (TICKET_MARGIN_LONG, TICKET_MARGIN_SHORT, TICKET_SBL_SHORT)

SIDE_BUY = "BUY"
SIDE_SELL = "SELL"
VALID_SIDES = (SIDE_BUY, SIDE_SELL)

ORDER_TYPE_LIMIT = "LIMIT"
ORDER_TYPE_MARKET = "MARKET"
VALID_ORDER_TYPES = (ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET)

# TWSE durations. "ROD" (Rest of Day) is the exchange's own term -- there is no
# "ROH" order duration on TWSE.
TIF_ROD = "ROD"
TIF_IOC = "IOC"
TIF_FOK = "FOK"
VALID_TIME_IN_FORCE = (TIF_ROD, TIF_IOC, TIF_FOK)

# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------
DEFAULT_TRADING_UNIT = 1000            # shares/units, 普通交易
ODD_LOT_MIN_QUANTITY = 1
ODD_LOT_MAX_QUANTITY = 999
DEFAULT_PRICE_LIMIT_PCT = Decimal("10")   # since 2015-06-01
MINIMUM_QUOTABLE_PRICE = Decimal("0.01")  # 「價格以跌至一分為限」

# Status / reason codes
STATUS_VALIDATED = "TWSE_ORDER_VALIDATED"
REASON_MISSING_INVESTOR_ID = "MISSING_INVESTOR_ID"
REASON_INVALID_TRADING_UNIT = "INVALID_TRADING_UNIT"
REASON_INVALID_ODD_LOT_QUANTITY = "INVALID_ODD_LOT_QUANTITY"
REASON_ODD_LOT_INSTRUMENT_INELIGIBLE = "ODD_LOT_INSTRUMENT_INELIGIBLE"
REASON_CREDIT_TICKET_NOT_PERMITTED_ODD_LOT = "CREDIT_TICKET_NOT_PERMITTED_ODD_LOT"
REASON_ORDER_TYPE_NOT_IN_SESSION = "ORDER_TYPE_NOT_AVAILABLE_IN_SESSION"
REASON_MARKET_ORDER_NOT_PERMITTED = "MARKET_ORDER_NOT_PERMITTED"
REASON_INVALID_TICK_SIZE = "INVALID_TICK_SIZE"
REASON_PRICE_LIMIT_EXCEEDED = "PRICE_LIMIT_EXCEEDED"
REASON_SHORT_SALE_BELOW_REFERENCE = "SHORT_SALE_BELOW_REFERENCE_RESTRICTED"
REASON_TICKET_SIDE_MISMATCH = "TICKET_SIDE_MISMATCH"


def _to_decimal(value: object, field_name: str) -> Decimal:
    """Convert a price-like value to ``Decimal``, rejecting NaN/Inf explicitly.

    A NaN price makes every ``<=`` comparison return ``False``, which would
    report a data-quality failure as a *rule breach*. Fail loudly instead.
    """
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a number, got bool")
    if isinstance(value, Decimal):
        candidate = value
    elif isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError(f"{field_name} must be finite, got {value!r}")
        # str() first: Decimal(0.1) carries the binary float's full error.
        candidate = Decimal(str(value))
    elif isinstance(value, (int, str)):
        try:
            candidate = Decimal(str(value).strip())
        except InvalidOperation as exc:
            raise ValueError(f"{field_name} is not a valid number: {value!r}") from exc
    else:
        raise ValueError(f"{field_name} must be a number, got {type(value).__name__}")

    if not candidate.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    return candidate


def _tick_for(price: Decimal, schedule) -> Decimal:
    """Tick of the band containing ``price``. Upper bounds are exclusive."""
    for upper_bound, tick in schedule:
        if upper_bound is None or price < upper_bound:
            return tick
    raise AssertionError("tick schedule must end in an open-ended band")


def _snap_to_grid(price: Decimal, schedule, downward: bool) -> Decimal:
    """Move ``price`` onto the tick grid, downward or upward.

    Rounding can carry the price across a band boundary (9.999 rounded up on
    the NT$0.01 tick becomes 10.00, where the equity tick is NT$0.05), so
    re-resolve the band until the result is stable. Every TWSE band boundary is
    itself a multiple of both adjacent ticks, so this converges immediately;
    the loop bound is a guard, not an expectation.
    """
    rounding = ROUND_FLOOR if downward else ROUND_CEILING
    candidate = price
    snapped = price
    for _ in range(len(schedule) + 1):
        tick = _tick_for(candidate, schedule)
        steps = (candidate / tick).to_integral_value(rounding=rounding)
        snapped = (steps * tick).quantize(Decimal("0.01"))
        if _tick_for(snapped, schedule) == tick:
            return snapped
        candidate = snapped
    return snapped


@dataclass
class TwseOrderPayload:
    """One TWSE order, in the fields a securities firm actually reports.

    ``reference_price`` is the day's auction reference price (開盤競價基準),
    which is the previous session's closing price ONLY in the ordinary case.
    TWSE substitutes the previous session's best bid or ask when there was no
    close, and an adjusted reference on ex-rights/ex-dividend days, on first
    listing, and on resumption from suspension. Feed the value the exchange
    published for the day, not a close carried forward by the caller.
    """

    symbol: str
    side: str
    quantity: int
    security_class: str = SECURITY_CLASS_EQUITY
    price: Optional[object] = None            # required for LIMIT orders
    reference_price: Optional[object] = None  # required unless price_limit_exempt
    order_type: str = ORDER_TYPE_LIMIT
    time_in_force: str = TIF_ROD
    session: str = SESSION_CONTINUOUS
    ticket_type: str = TICKET_CASH
    investor_id: Optional[str] = None
    # Reference data the caller must supply from that day's TWSE publications:
    price_limit_pct: Optional[object] = None   # 20 for a 2x leveraged ETF, etc.
    price_limit_exempt: bool = False           # first 5 sessions of a new listing,
                                               # foreign-component/offshore ETFs, ...
    below_reference_short_sale_permitted: bool = False
    trading_unit: int = DEFAULT_TRADING_UNIT
    client_order_id: Optional[str] = None


@dataclass
class TwseOrderReport:
    """Structured verdict. A rejection carries enough to reprice, not just fail."""

    symbol: str
    status: str
    accepted: bool
    reason: str = ""
    tick_size: Optional[Decimal] = None
    limit_up_price: Optional[Decimal] = None
    limit_down_price: Optional[Decimal] = None
    nearest_valid_price_below: Optional[Decimal] = None
    nearest_valid_price_above: Optional[Decimal] = None
    client_order_id: Optional[str] = None


class TaiwanStockExchangeTwseEngine:
    """TWSE centralised-market pre-trade order validator.

    ``investor_id`` is the Investor ID TWSE issues on registration under the
    Regulations Governing Investment in Securities by Overseas Chinese and
    Foreign Nationals -- the identifier the market calls a FINI ID for an
    offshore institution. TWSE publishes no check-digit or format rule for it,
    so it is checked for presence only and never defaulted: an order stamped
    with a fabricated registration ID is worse than one rejected for lacking
    it.
    """

    def __init__(self, investor_id: Optional[str] = None) -> None:
        if investor_id is not None and not str(investor_id).strip():
            raise ValueError("investor_id, when supplied, must be non-empty")
        self.investor_id: Optional[str] = (
            str(investor_id).strip() if investor_id is not None else None
        )

    # -- public rule primitives ------------------------------------------

    def get_tick_size(self, price: object, security_class: str) -> Decimal:
        """Tick (升降單位) for ``price`` under the instrument's schedule."""
        security_class = self._normalise_security_class(security_class)
        price_dec = _to_decimal(price, "price")
        if price_dec <= 0:
            raise ValueError(f"price must be strictly positive, got {price!r}")
        return _tick_for(price_dec, TICK_SCHEDULES[security_class])

    def is_price_on_tick(self, price: object, security_class: str) -> bool:
        """True when ``price`` is an exact multiple of its own band's tick."""
        security_class = self._normalise_security_class(security_class)
        price_dec = _to_decimal(price, "price")
        if price_dec <= 0:
            raise ValueError(f"price must be strictly positive, got {price!r}")
        tick = _tick_for(price_dec, TICK_SCHEDULES[security_class])
        return price_dec % tick == 0

    def get_daily_price_limit_bounds(
        self,
        reference_price: object,
        security_class: str = SECURITY_CLASS_EQUITY,
        price_limit_pct: object = DEFAULT_PRICE_LIMIT_PCT,
    ) -> Tuple[Decimal, Decimal]:
        """Return ``(limit_down, limit_up)`` for the day, on the tick grid.

        Article 63 fixes the percentage; Article 62 fixes the grid. The
        computed bound is snapped **toward** the reference price, because the
        outward neighbouring tick would breach the percentage band. TWSE's
        published example -- reference 40.60 -> 44.65 / 36.55, not 44.70 /
        36.50 -- is reproduced by the unit tests.
        """
        security_class = self._normalise_security_class(security_class)
        schedule = TICK_SCHEDULES[security_class]

        ref = _to_decimal(reference_price, "reference_price")
        if ref <= 0:
            raise ValueError(
                f"reference_price must be strictly positive, got {reference_price!r}"
            )
        pct = _to_decimal(price_limit_pct, "price_limit_pct")
        if pct <= 0:
            raise ValueError(
                f"price_limit_pct must be strictly positive, got {price_limit_pct!r}"
            )

        amount = ref * pct / Decimal("100")
        # 「升降幅度經換算後，未滿一分者，以一分計」
        if amount < MINIMUM_QUOTABLE_PRICE:
            amount = MINIMUM_QUOTABLE_PRICE

        upper = _snap_to_grid(ref + amount, schedule, downward=True)
        lower = _snap_to_grid(ref - amount, schedule, downward=False)
        # 「且價格以跌至一分為限」
        if lower < MINIMUM_QUOTABLE_PRICE:
            lower = MINIMUM_QUOTABLE_PRICE
        return lower, upper

    # -- validation ------------------------------------------------------

    def validate_and_route_order(self, payload: TwseOrderPayload) -> TwseOrderReport:
        """Run every implemented TWSE pre-trade rule against ``payload``.

        Malformed input raises ``ValueError``; an exchange-rule breach is
        returned as a non-accepted ``TwseOrderReport``. A caller bug must never
        be indistinguishable from a rule rejection.
        """
        symbol = self._normalise_symbol(payload.symbol)
        security_class = self._normalise_security_class(payload.security_class)
        side = self._normalise_choice(payload.side, VALID_SIDES, "side")
        order_type = self._normalise_choice(
            payload.order_type, VALID_ORDER_TYPES, "order_type"
        )
        time_in_force = self._normalise_choice(
            payload.time_in_force, VALID_TIME_IN_FORCE, "time_in_force"
        )
        session = self._normalise_choice(payload.session, VALID_SESSIONS, "session")
        ticket_type = self._normalise_choice(
            payload.ticket_type, VALID_TICKET_TYPES, "ticket_type"
        )

        if not isinstance(payload.quantity, int) or isinstance(payload.quantity, bool):
            raise ValueError(
                f"quantity must be an int number of shares, got {payload.quantity!r}"
            )
        if payload.quantity <= 0:
            raise ValueError(
                f"quantity must be strictly positive, got {payload.quantity!r}"
            )
        if not isinstance(payload.trading_unit, int) or payload.trading_unit <= 0:
            raise ValueError(
                f"trading_unit must be a positive int, got {payload.trading_unit!r}"
            )

        def reject(reason_code: str, message: str, **extra) -> TwseOrderReport:
            logger.info("TWSE order %s rejected: %s (%s)", symbol, reason_code, message)
            return TwseOrderReport(
                symbol=symbol,
                status=reason_code,
                accepted=False,
                reason=message,
                client_order_id=payload.client_order_id,
                **extra,
            )

        # 1. Investor ID (the FINI / Investor ID TWSE issues on registration).
        investor_id = payload.investor_id or self.investor_id
        if not investor_id or not str(investor_id).strip():
            return reject(
                REASON_MISSING_INVESTOR_ID,
                "no TWSE Investor ID on the order or the engine; a foreign "
                "investor must register with TWSE before trading",
            )

        # 2. Ticket type must agree with the side it implies.
        if ticket_type in SHORT_SALE_TICKETS and side != SIDE_SELL:
            return reject(
                REASON_TICKET_SIDE_MISMATCH,
                f"ticket_type {ticket_type} is a short sale and requires side=SELL",
            )
        if ticket_type == TICKET_MARGIN_LONG and side != SIDE_BUY:
            return reject(
                REASON_TICKET_SIDE_MISMATCH,
                "ticket_type MARGIN_LONG (融資) is a financed purchase and "
                "requires side=BUY",
            )

        is_odd_lot = session in ODD_LOT_SESSIONS

        # 3. Odd-lot session constraints.
        if is_odd_lot:
            if security_class in ODD_LOT_INELIGIBLE_CLASSES:
                return reject(
                    REASON_ODD_LOT_INSTRUMENT_INELIGIBLE,
                    f"{security_class} may not trade in odd lots on TWSE "
                    "(認購售權證及ETN不得進行零股交易)",
                )
            if ticket_type in CREDIT_TICKETS:
                return reject(
                    REASON_CREDIT_TICKET_NOT_PERMITTED_ODD_LOT,
                    "odd-lot trading is cash only; margin (融資融券) and SBL "
                    "short sales (借券賣出) are not permitted",
                )
            if not (ODD_LOT_MIN_QUANTITY <= payload.quantity <= ODD_LOT_MAX_QUANTITY):
                return reject(
                    REASON_INVALID_ODD_LOT_QUANTITY,
                    f"odd-lot quantity must be {ODD_LOT_MIN_QUANTITY}-"
                    f"{ODD_LOT_MAX_QUANTITY} shares, got {payload.quantity}",
                )
        elif payload.quantity % payload.trading_unit != 0:
            return reject(
                REASON_INVALID_TRADING_UNIT,
                f"regular-session quantity must be a multiple of "
                f"{payload.trading_unit:,} shares, got {payload.quantity:,}",
            )

        # 4. Order type / duration must exist in this session.
        if session not in CONTINUOUS_ONLY_SESSIONS and (
            order_type != ORDER_TYPE_LIMIT or time_in_force != TIF_ROD
        ):
            return reject(
                REASON_ORDER_TYPE_NOT_IN_SESSION,
                f"session {session} is a call auction and accepts limit-ROD "
                f"only; {order_type}/{time_in_force} would be returned by TWSE",
            )

        # 5. Market-order carve-outs.
        if order_type == ORDER_TYPE_MARKET:
            if payload.price is not None:
                # A TWSE market order carries no price field at all, and a
                # limit order may not be amended into one (限價不可改為市價).
                # A price on a market order is a caller bug, not a rule breach.
                raise ValueError(
                    "a MARKET order must not carry a price; TWSE converts a "
                    "reference price at each match instead"
                )
            if payload.price_limit_exempt:
                return reject(
                    REASON_MARKET_ORDER_NOT_PERMITTED,
                    "market orders are not accepted for securities with no "
                    "daily price limit (including a new common stock's first "
                    "five sessions)",
                )
            if (
                ticket_type in SHORT_SALE_TICKETS
                and not payload.below_reference_short_sale_permitted
            ):
                return reject(
                    REASON_MARKET_ORDER_NOT_PERMITTED,
                    "a security restricted from below-reference short selling "
                    "may not be shorted with a market order",
                )
            logger.debug("TWSE market order %s accepted; price checks skipped", symbol)
            return TwseOrderReport(
                symbol=symbol,
                status=STATUS_VALIDATED,
                accepted=True,
                reason="passed all implemented TWSE pre-trade checks",
                client_order_id=payload.client_order_id,
            )

        # 6. Limit orders: a price is mandatory from here on.
        if payload.price is None:
            raise ValueError("price is required for a LIMIT order")
        price = _to_decimal(payload.price, "price")
        if price <= 0:
            raise ValueError(f"price must be strictly positive, got {payload.price!r}")

        schedule = TICK_SCHEDULES[security_class]
        tick = _tick_for(price, schedule)
        if price % tick != 0:
            below = _snap_to_grid(price, schedule, downward=True)
            return reject(
                REASON_INVALID_TICK_SIZE,
                f"price {price} is not a multiple of the NT${tick} tick that "
                f"applies to a {security_class} at this price level",
                tick_size=tick,
                # Snapping down can land under NT$0.01, which is not quotable.
                nearest_valid_price_below=(
                    below if below >= MINIMUM_QUOTABLE_PRICE else None
                ),
                nearest_valid_price_above=_snap_to_grid(price, schedule, downward=False),
            )

        # 7. Daily price limit, and the below-reference short-sale rule, are
        #    both anchored on the day's auction reference price.
        if payload.reference_price is None:
            if not payload.price_limit_exempt:
                raise ValueError(
                    "reference_price (開盤競價基準) is required unless the "
                    "security has no daily price limit"
                )
            if ticket_type in SHORT_SALE_TICKETS:
                raise ValueError(
                    "reference_price is required for a short sale even when "
                    "price_limit_exempt is set: the 平盤以下 rule is measured "
                    "against it"
                )
            return TwseOrderReport(
                symbol=symbol,
                status=STATUS_VALIDATED,
                accepted=True,
                reason="passed all implemented TWSE pre-trade checks",
                tick_size=tick,
                client_order_id=payload.client_order_id,
            )

        reference = _to_decimal(payload.reference_price, "reference_price")
        if reference <= 0:
            raise ValueError(
                f"reference_price must be strictly positive, "
                f"got {payload.reference_price!r}"
            )

        limit_down: Optional[Decimal] = None
        limit_up: Optional[Decimal] = None
        if not payload.price_limit_exempt:
            pct = (
                DEFAULT_PRICE_LIMIT_PCT
                if payload.price_limit_pct is None
                else _to_decimal(payload.price_limit_pct, "price_limit_pct")
            )
            limit_down, limit_up = self.get_daily_price_limit_bounds(
                reference, security_class, pct
            )
            if not (limit_down <= price <= limit_up):
                return reject(
                    REASON_PRICE_LIMIT_EXCEEDED,
                    f"price {price} is outside the day's band "
                    f"[{limit_down}, {limit_up}] set by the {pct}% limit on "
                    f"auction reference price {reference}",
                    tick_size=tick,
                    limit_up_price=limit_up,
                    limit_down_price=limit_down,
                )

        # 8. 平盤以下 short-sale restriction. Pricing AT the reference is always
        #    allowed; only strictly below it is restricted.
        if (
            ticket_type in SHORT_SALE_TICKETS
            and price < reference
            and not payload.below_reference_short_sale_permitted
        ):
            return reject(
                REASON_SHORT_SALE_BELOW_REFERENCE,
                f"short sale at {price} is below the auction reference price "
                f"{reference}; permitted only for securities on that day's "
                "平盤下得融(借)券賣出 list",
                tick_size=tick,
                limit_up_price=limit_up,
                limit_down_price=limit_down,
            )

        logger.debug("TWSE order %s validated", symbol)
        return TwseOrderReport(
            symbol=symbol,
            status=STATUS_VALIDATED,
            accepted=True,
            reason="passed all implemented TWSE pre-trade checks",
            tick_size=tick,
            limit_up_price=limit_up,
            limit_down_price=limit_down,
            client_order_id=payload.client_order_id,
        )

    # -- normalisation helpers -------------------------------------------

    @staticmethod
    def _normalise_security_class(security_class: str) -> str:
        if not isinstance(security_class, str):
            raise ValueError(
                f"security_class must be a string, got {type(security_class).__name__}"
            )
        normalised = security_class.strip().upper()
        if normalised not in VALID_SECURITY_CLASSES:
            raise ValueError(
                f"security_class must be one of {VALID_SECURITY_CLASSES}, "
                f"got {security_class!r}"
            )
        return normalised

    @staticmethod
    def _normalise_choice(value: str, allowed: Tuple[str, ...], field_name: str) -> str:
        if not isinstance(value, str):
            raise ValueError(
                f"{field_name} must be a string, got {type(value).__name__}"
            )
        normalised = value.strip().upper()
        if normalised not in allowed:
            raise ValueError(f"{field_name} must be one of {allowed}, got {value!r}")
        return normalised

    @staticmethod
    def _normalise_symbol(symbol: str) -> str:
        """Permissive TWSE code check: four digits, then up to two more digits
        or letters. Covers 2330 (TSMC), 006208, and 00679B / 00400A style ETF
        codes as well as six-character warrant codes. It is deliberately NOT a
        class inference -- pass ``security_class`` from reference data.
        """
        if not isinstance(symbol, str):
            raise ValueError(f"symbol must be a string, got {type(symbol).__name__}")
        normalised = symbol.strip().upper()
        if not (4 <= len(normalised) <= 6):
            raise ValueError(f"TWSE code must be 4-6 characters, got {symbol!r}")
        if not normalised.isalnum() or not normalised[:4].isdigit():
            raise ValueError(
                f"TWSE code must start with four digits and be alphanumeric, "
                f"got {symbol!r}"
            )
        return normalised

"""JSE (Johannesburg Stock Exchange) pre-trade equity order validation.

Encodes the JSE equity market (EQM) order-entry rules published in
*Volume 00E - Trading and Information Overview for Equity Market* v4.09
(28 May 2026), the contract specification of record for the JSE trading system:

* Trading currency is **ZAC (South African Cents)**; 100 ZAC = ZAR 1.
* **Tick size is 1 for every instrument** -- the JSE has no price-tiered tick
  ladder. A limit price must be a whole number of ZAC greater than zero.
* **Lot size is 1 for every instrument**; quantity must be a whole number
  greater than zero, up to the Maximum Order Size of 99,999,999.
* **Price Bands** (ZA01 segment only, +/-90% of the *static* reference price)
  are the only price control that causes the exchange to *reject* an order.
* **Circuit breakers** do NOT reject orders. A breach moves the instrument into
  a 5-minute Volatility Auction Call session; the aggressing order's remainder
  is booked (persistent TIF) or expired (non-persistent TIF).

This is a client-side pre-trade filter, not a substitute for the exchange's own
controls. The JSE trading system remains authoritative and can reject or expire
an order this module approves.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Trading currency for the JSE markets is ZAC (South African Cents).
ZAC_PER_ZAR = 100

# "Tick Size: The minimum possible price/price increment which can be used for
# an order. This is set to 1 for the JSE and NSX markets." (Volume 00E s2,
# reiterated in s3: "Tick size of 1 for every instrument"). There is no
# price-tiered tick ladder on the JSE equity market.
TICK_SIZE_ZAC = 1

# "Lot size of 1 for every instrument" (Volume 00E s3). Order Quantity "should
# be a whole number that is greater than zero and must be a multiple of the
# instrument's Lot Size".
LOT_SIZE = 1

# Maximum Order Size, published per trading segment in Volume 00E s4. Every JSE
# and NSX equity segment carries the same value.
MAX_ORDER_QUANTITY = 99_999_999

VALID_SIDES = ("BUY", "SELL")

# Equity market trading segments (Volume 00E s4). ZA11/ZA12 are NSX segments
# routed through the same trading system.
JSE_TRADING_SEGMENTS = ("ZA01", "ZA02", "ZA03", "ZA04", "ZA06")
NSX_TRADING_SEGMENTS = ("ZA11", "ZA12")
VALID_TRADING_SEGMENTS = JSE_TRADING_SEGMENTS + NSX_TRADING_SEGMENTS

# Trading sessions for which the JSE publishes circuit breaker tolerances.
VALID_TRADING_SESSIONS = (
    "OPENING_AUCTION_CALL",
    "CONTINUOUS_TRADING",
    "INTRADAY_AUCTION_CALL",
    "CLOSING_AUCTION_CALL",
    "FCO_AUCTION_CALL",
    "RE_OPENING_AUCTION_CALL",
)

# Price Bands: "Instruments trading in the Equity Market (ZA01) will be subject
# to Price Bands... Orders entered outside of these defined thresholds will be
# rejected by the trading system." Only ZA01 has a published band, and it is
# measured against the STATIC reference price. Segments without a published band
# are deliberately absent rather than defaulted -- inventing a band for them
# would reject orders the JSE accepts.
PRICE_BAND_OUTER_LIMIT_PCT: Dict[str, float] = {
    "ZA01": 90.0,
}

# EQM circuit breaker tolerances, as (static %, dynamic %) keyed by segment then
# session; None marks a segment/session pair to which the JSE applies no circuit
# breaker. Transcribed from Volume 00E v4.09 s8.6.5. The JSE states these
# "values may be reviewed... from time to time" -- re-verify before production
# use rather than trusting the constants below to still be current.
CIRCUIT_BREAKER_TOLERANCES_PCT: Dict[str, Dict[str, Optional[Tuple[float, float]]]] = {
    "ZA01": {
        "OPENING_AUCTION_CALL": (8.0, 6.0),
        "CONTINUOUS_TRADING": (10.0, 3.0),
        "INTRADAY_AUCTION_CALL": None,
        "CLOSING_AUCTION_CALL": (4.0, 2.0),
        "FCO_AUCTION_CALL": (15.0, 2.0),
        "RE_OPENING_AUCTION_CALL": (8.0, 6.0),
    },
    "ZA02": {
        "OPENING_AUCTION_CALL": (20.0, 10.0),
        "CONTINUOUS_TRADING": (15.0, 5.0),
        "INTRADAY_AUCTION_CALL": None,
        "CLOSING_AUCTION_CALL": (10.0, 5.0),
        "FCO_AUCTION_CALL": (30.0, 4.0),
        "RE_OPENING_AUCTION_CALL": (20.0, 10.0),
    },
    "ZA03": {
        "OPENING_AUCTION_CALL": (50.0, 25.0),
        "CONTINUOUS_TRADING": (50.0, 25.0),
        "INTRADAY_AUCTION_CALL": (50.0, 25.0),
        "CLOSING_AUCTION_CALL": (50.0, 25.0),
        "FCO_AUCTION_CALL": (50.0, 25.0),
        "RE_OPENING_AUCTION_CALL": (50.0, 25.0),
    },
    "ZA04": {
        "OPENING_AUCTION_CALL": (70.0, 50.0),
        "CONTINUOUS_TRADING": (70.0, 50.0),
        "INTRADAY_AUCTION_CALL": None,
        "CLOSING_AUCTION_CALL": None,
        "FCO_AUCTION_CALL": (70.0, 50.0),
        "RE_OPENING_AUCTION_CALL": (70.0, 50.0),
    },
    "ZA06": {
        "OPENING_AUCTION_CALL": (20.0, 8.0),
        "CONTINUOUS_TRADING": (15.0, 5.0),
        "INTRADAY_AUCTION_CALL": None,
        "CLOSING_AUCTION_CALL": (20.0, 8.0),
        "FCO_AUCTION_CALL": (30.0, 4.0),
        "RE_OPENING_AUCTION_CALL": (20.0, 8.0),
    },
}

# Defensive client-side bound on the JSE alpha code (the instrument "Symbol").
# Volume 00E defines Symbol only as "The JSE alpha code assigned to the
# instrument" and publishes no length or character rule, so this is a sanity
# guard, NOT a JSE rule. Six characters covers every code observed, from
# 3-character equity codes (NPN, AGL, S32) to 6-character ETP codes (ETFSWX).
# Pass max_alpha_code_length=None to disable the length guard entirely.
DEFAULT_MAX_ALPHA_CODE_LENGTH = 6

# Statuses that mean "do not send this order to the JSE".
REJECTION_STATUSES = (
    "INVALID_TICK_SIZE",
    "PRICE_BAND_BREACH",
    "ORDER_SIZE_EXCEEDED",
    "HOUSE_LIMIT_EXCEEDED",
)


@dataclass
class JseOrderPayload:
    """A single equity order presented to the JSE trading system.

    Attributes:
        alpha_code: JSE alpha code (instrument Symbol), e.g. 'NPN' (Naspers),
            'AGL' (Anglo American), 'S32' (South32), 'ETFSWX'. Alphanumeric --
            codes containing digits are legitimate JSE instruments.
        side: 'BUY' or 'SELL'.
        price_zac: Limit price in ZAC (South African Cents). Must be a whole
            number of ZAC: the JSE tick size is 1 for every instrument.
        quantity: Order size in shares. Whole number, lot size 1.
        reference_price_zac: The **static** reference price in ZAC -- the
            previous day's closing price or the last auction price. This anchors
            both the ZA01 price band and the static circuit breaker. It is not
            the last traded price; pass that separately below.
        dynamic_reference_price_zac: The **dynamic** reference price in ZAC --
            the last traded price. Optional: when omitted the dynamic circuit
            breaker cannot be evaluated and the report says so rather than
            implying it passed.
        trading_segment: Segment the instrument trades in ('ZA01' Top Companies,
            'ZA02' Medium Liquid, 'ZA03' Less Liquid, 'ZA04' Specialist
            Products, 'ZA06' Exchange Traded Products, 'ZA11'/'ZA12' NSX).
            Drives both the price band and the circuit breaker tolerances.
        trading_session: Session the order is being entered for. Circuit breaker
            tolerances are defined per session, not per day.
    """

    alpha_code: str
    side: str
    price_zac: float
    quantity: int
    reference_price_zac: float
    dynamic_reference_price_zac: Optional[float] = None
    trading_segment: str = "ZA01"
    trading_session: str = "CONTINUOUS_TRADING"


@dataclass
class JseOrderReport:
    """Structured outcome of a pre-trade JSE order audit.

    `is_rejected` is the single authoritative accept/reject signal. A status of
    'VOLATILITY_AUCTION_RISK' is NOT a rejection: the order is valid and the JSE
    will accept it. It flags that a trade at this price would breach a circuit
    breaker tolerance and move the instrument into a Volatility Auction Call
    session, which is a market-impact and fill-certainty concern, not an
    order-validity one.
    """

    alpha_code: str
    side: str
    price_zac: float
    equivalent_price_zar: float
    applicable_tick_size_zac: int
    quantity_shares: int
    notional_value_zar: float
    trading_segment: str
    trading_session: str
    is_price_tick_valid: bool
    is_price_band_valid: bool
    is_order_size_valid: bool
    is_rejected: bool
    status: str
    audit_notes: str
    # ZA01 price band in ZAC; None where the JSE publishes no band for the
    # segment, in which case no band was enforced.
    price_band_lower_zac: Optional[float] = None
    price_band_upper_zac: Optional[float] = None
    # Circuit breaker assessment. `circuit_breaker_would_trigger` is True only
    # when a tolerance was actually evaluated and breached.
    static_circuit_breaker_pct: Optional[float] = None
    dynamic_circuit_breaker_pct: Optional[float] = None
    circuit_breaker_would_trigger: bool = False
    circuit_breaker_notes: List[str] = field(default_factory=list)


class JseSouthAfricaApiEngine:
    """Pre-trade validator for Johannesburg Stock Exchange equity orders.

    Enforces the JSE equity market order-entry rules of Volume 00E v4.09: whole
    ZAC prices on a tick size of 1, whole-share quantities up to the 99,999,999
    Maximum Order Size, and the ZA01 +/-90% price band. It additionally reports
    -- without rejecting -- whether a trade at the order's limit price would
    breach a circuit breaker tolerance and trigger a Volatility Auction Call.

    The JSE trading system remains authoritative: halts, suspensions, session
    state, entitlement and member-level risk limits are not modelled here.
    """

    def __init__(
        self,
        house_price_deviation_limit_pct: Optional[float] = None,
        max_alpha_code_length: Optional[int] = DEFAULT_MAX_ALPHA_CODE_LENGTH,
    ) -> None:
        """
        Args:
            house_price_deviation_limit_pct: Optional in-house fat-finger cap, in
                percent, on the order price's deviation from the static
                reference price. This is a house risk control layered on top of
                the exchange rules -- the JSE publishes no daily price limit for
                equities. Leave as None to audit against JSE rules alone.
            max_alpha_code_length: Defensive maximum length for the JSE alpha
                code. Not a JSE rule (see DEFAULT_MAX_ALPHA_CODE_LENGTH); pass
                None to accept any non-empty alphanumeric code.
        """
        if house_price_deviation_limit_pct is not None:
            if (
                isinstance(house_price_deviation_limit_pct, bool)
                or not isinstance(house_price_deviation_limit_pct, (int, float))
                or not math.isfinite(house_price_deviation_limit_pct)
                or house_price_deviation_limit_pct <= 0
            ):
                raise ValueError(
                    "house_price_deviation_limit_pct must be a positive finite "
                    f"percentage or None, got {house_price_deviation_limit_pct!r}."
                )
        if max_alpha_code_length is not None:
            if (
                isinstance(max_alpha_code_length, bool)
                or not isinstance(max_alpha_code_length, int)
                or max_alpha_code_length < 1
            ):
                raise ValueError(
                    "max_alpha_code_length must be a positive integer or None, "
                    f"got {max_alpha_code_length!r}."
                )
        self.house_price_deviation_limit_pct = house_price_deviation_limit_pct
        self.max_alpha_code_length = max_alpha_code_length

    # ---------------------------------------------------------------- helpers

    def validate_jse_alpha_code(self, code: str) -> str:
        """Normalises and validates a JSE alpha code (instrument Symbol).

        JSE alpha codes are alphanumeric and are not all three letters: 'S32'
        (South32, a Top 40 constituent) contains digits and ETP codes such as
        'ETFSWX' run to six characters. Rejecting anything but three letters
        blocks legitimate instruments, so only the character class and a
        configurable defensive length bound are enforced here.
        """
        if not isinstance(code, str):
            raise TypeError(f"JSE alpha code must be a string, got {type(code).__name__}.")
        clean = code.strip().upper()
        if not clean or not clean.isalnum() or not clean.isascii():
            raise ValueError(
                f"Invalid JSE alpha code {code!r}. Codes must be non-empty ASCII "
                f"alphanumerics (e.g. NPN, AGL, S32, ETFSWX)."
            )
        if self.max_alpha_code_length is not None and len(clean) > self.max_alpha_code_length:
            raise ValueError(
                f"Invalid JSE alpha code {code!r}: longer than the configured "
                f"maximum of {self.max_alpha_code_length} characters."
            )
        return clean

    def get_jse_tick_size_zac(self, price_zac: float) -> int:
        """Returns the JSE tick size in ZAC, which is 1 for every instrument.

        Volume 00E: "Tick Size... is set to 1 for the JSE and NSX markets" and
        "Tick size of 1 for every instrument". The JSE operates no price-tiered
        tick ladder, so the price argument is validated but does not change the
        result; it is retained so callers can validate and size in one call.

        (The 0.5 ZAC half-tick that appears in JSE documentation is the price
        improvement the *system* applies to pegged hidden order executions. It
        is not a price a member may submit.)
        """
        self._require_positive_price(price_zac, "Order price")
        return TICK_SIZE_ZAC

    def get_price_band_zac(
        self, reference_price_zac: float, trading_segment: str = "ZA01"
    ) -> Tuple[Optional[float], Optional[float]]:
        """Returns the (lower, upper) price band in ZAC, or (None, None).

        The JSE publishes a price band only for the ZA01 segment: +/-90% of the
        static reference price, with orders outside it rejected on entry. For
        every other segment this returns (None, None) -- no band is published,
        so none is enforced.
        """
        price = self._require_positive_price(reference_price_zac, "Static reference price")
        segment = self._normalise_segment(trading_segment)
        band_pct = PRICE_BAND_OUTER_LIMIT_PCT.get(segment)
        if band_pct is None:
            return None, None
        return price * (1.0 - band_pct / 100.0), price * (1.0 + band_pct / 100.0)

    def get_circuit_breaker_tolerances_pct(
        self, trading_segment: str, trading_session: str
    ) -> Optional[Tuple[float, float]]:
        """Returns the (static %, dynamic %) circuit breaker tolerances.

        Returns None where the JSE applies no circuit breaker to that
        segment/session pair, and for the NSX segments (ZA11/ZA12), for which
        the JSE publishes no EQM tolerance table.
        """
        segment = self._normalise_segment(trading_segment)
        session = self._normalise_session(trading_session)
        return CIRCUIT_BREAKER_TOLERANCES_PCT.get(segment, {}).get(session)

    @staticmethod
    def _require_positive_price(value: float, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{label} must be numeric, got {type(value).__name__}.")
        price = float(value)
        if not math.isfinite(price) or price <= 0.0:
            raise ValueError(f"{label} must be a finite positive ZAC amount, got {value!r}.")
        return price

    @staticmethod
    def _normalise_segment(trading_segment: str) -> str:
        if not isinstance(trading_segment, str):
            raise TypeError(
                f"trading_segment must be a string, got {type(trading_segment).__name__}."
            )
        segment = trading_segment.strip().upper()
        if segment not in VALID_TRADING_SEGMENTS:
            raise ValueError(
                f"Invalid JSE trading segment {trading_segment!r}. "
                f"Must be one of {VALID_TRADING_SEGMENTS}."
            )
        return segment

    @staticmethod
    def _normalise_session(trading_session: str) -> str:
        if not isinstance(trading_session, str):
            raise TypeError(
                f"trading_session must be a string, got {type(trading_session).__name__}."
            )
        session = trading_session.strip().upper().replace("-", "_").replace(" ", "_")
        if session not in VALID_TRADING_SESSIONS:
            raise ValueError(
                f"Invalid JSE trading session {trading_session!r}. "
                f"Must be one of {VALID_TRADING_SESSIONS}."
            )
        return session

    @staticmethod
    def _normalise_side(side: str) -> str:
        if not isinstance(side, str):
            raise TypeError(f"side must be a string, got {type(side).__name__}.")
        clean = side.strip().upper()
        if clean not in VALID_SIDES:
            raise ValueError(f"Invalid order side {side!r}. Must be one of {VALID_SIDES}.")
        return clean

    @staticmethod
    def _validate_quantity(quantity: int) -> int:
        if isinstance(quantity, bool) or not isinstance(quantity, int):
            raise TypeError(
                "Order quantity must be an integer number of shares, "
                f"got {type(quantity).__name__}."
            )
        if quantity <= 0:
            raise ValueError(f"Order quantity must be greater than zero, got {quantity}.")
        if quantity % LOT_SIZE != 0:
            raise ValueError(
                f"Order quantity {quantity} must be a multiple of the JSE lot size ({LOT_SIZE})."
            )
        return quantity

    # ------------------------------------------------------- circuit breakers

    def assess_circuit_breaker(
        self,
        price_zac: float,
        static_reference_price_zac: float,
        dynamic_reference_price_zac: Optional[float],
        trading_segment: str,
        trading_session: str,
    ) -> Tuple[Optional[float], Optional[float], bool, List[str]]:
        """Assesses whether a trade at `price_zac` would breach a circuit breaker.

        A circuit breaker does not reject an order. If the difference between
        the price of the next trade and the static or dynamic reference price is
        equal to or greater than the tolerance for the session, the instrument
        is moved into a 5-minute Volatility Auction Call session; the remainder
        of the aggressing order is booked if its time in force is persistent and
        expired otherwise. The more restrictive of the two breakers takes
        precedence.

        The JSE evaluates the *next trade* price, which this engine cannot know.
        The order's limit price is used as a conservative proxy: an aggressing
        buy executes at or below its limit and an aggressing sell at or above
        it, so the limit price bounds the worst-case deviation this order can
        cause. A resting order that never aggresses may of course never trade at
        that price at all.

        Returns (static_pct, dynamic_pct, would_trigger, notes). `would_trigger`
        is True only when a published tolerance was evaluated and breached.
        """
        price = self._require_positive_price(price_zac, "Order price")
        static_ref = self._require_positive_price(
            static_reference_price_zac, "Static reference price"
        )
        segment = self._normalise_segment(trading_segment)
        session = self._normalise_session(trading_session)

        tolerances = self.get_circuit_breaker_tolerances_pct(segment, session)
        notes: List[str] = []
        if tolerances is None:
            notes.append(
                f"No circuit breaker published for {segment}/{session}; not evaluated."
            )
            return None, None, False, notes

        static_pct, dynamic_pct = tolerances
        would_trigger = False

        static_dev = abs(price - static_ref) / static_ref * 100.0
        if static_dev >= static_pct:
            would_trigger = True
            notes.append(
                f"Static CB breach: {static_dev:.2f}% from static reference "
                f"{static_ref:,.0f} ZAC >= {static_pct:.1f}%."
            )
        else:
            notes.append(
                f"Within static CB tolerance ({static_dev:.2f}% < {static_pct:.1f}%)."
            )

        if dynamic_reference_price_zac is None:
            notes.append(
                f"Dynamic CB ({dynamic_pct:.1f}%) not evaluated: no last traded price supplied."
            )
        else:
            dynamic_ref = self._require_positive_price(
                dynamic_reference_price_zac, "Dynamic reference price"
            )
            dynamic_dev = abs(price - dynamic_ref) / dynamic_ref * 100.0
            if dynamic_dev >= dynamic_pct:
                would_trigger = True
                notes.append(
                    f"Dynamic CB breach: {dynamic_dev:.2f}% from last traded price "
                    f"{dynamic_ref:,.0f} ZAC >= {dynamic_pct:.1f}%."
                )
            else:
                notes.append(
                    f"Within dynamic CB tolerance ({dynamic_dev:.2f}% < {dynamic_pct:.1f}%)."
                )

        return static_pct, dynamic_pct, would_trigger, notes

    # ------------------------------------------------------------------- main

    def validate_and_route_order(self, payload: JseOrderPayload) -> JseOrderReport:
        """Audits an order against JSE equity market rules and returns a report.

        Structurally invalid input (bad alpha code, side, segment, session,
        quantity or reference price) raises. Genuine exchange-rule breaches are
        returned as a rejection status so the caller can log and route around
        them. Check `report.is_rejected` for the accept/reject decision --
        'VOLATILITY_AUCTION_RISK' is an accepted order carrying a market-impact
        warning, not a rejection.
        """
        code_clean = self.validate_jse_alpha_code(payload.alpha_code)
        side_clean = self._normalise_side(payload.side)
        segment = self._normalise_segment(payload.trading_segment)
        session = self._normalise_session(payload.trading_session)
        quantity = self._validate_quantity(payload.quantity)
        price = self._require_positive_price(payload.price_zac, "Order price")
        static_ref = self._require_positive_price(
            payload.reference_price_zac, "Static reference price"
        )

        # 1. Tick alignment. The tick is 1 ZAC for every JSE instrument, so a
        #    valid price is simply a whole number of cents. Integer arithmetic --
        #    no float tolerance that could wave through 85,500.0001 ZAC.
        is_tick_valid = price.is_integer()

        # 2. ZAC -> ZAR conversion for portfolio and risk aggregation.
        equiv_zar = round(price / ZAC_PER_ZAR, 2)
        notional_zar = round(price * quantity / ZAC_PER_ZAR, 2)

        # 3. ZA01 price band -- the only exchange price control that rejects an
        #    order at entry. Absent for every other segment, so nothing is
        #    enforced there.
        band_lower, band_upper = self.get_price_band_zac(static_ref, segment)
        is_band_valid = band_lower is None or band_lower <= price <= band_upper

        # 4. Maximum Order Size.
        is_size_valid = quantity <= MAX_ORDER_QUANTITY

        # 5. Optional in-house fat-finger cap (not a JSE rule).
        house_deviation_pct = abs(price - static_ref) / static_ref * 100.0
        is_house_limit_valid = (
            self.house_price_deviation_limit_pct is None
            or house_deviation_pct <= self.house_price_deviation_limit_pct
        )

        # 6. Circuit breaker proximity -- informational, never a rejection.
        static_pct, dynamic_pct, cb_would_trigger, cb_notes = self.assess_circuit_breaker(
            price, static_ref, payload.dynamic_reference_price_zac, segment, session
        )

        status, notes = self._classify(
            code=code_clean,
            side=side_clean,
            segment=segment,
            session=session,
            price=price,
            quantity=quantity,
            equiv_zar=equiv_zar,
            notional_zar=notional_zar,
            band_lower=band_lower,
            band_upper=band_upper,
            house_deviation_pct=house_deviation_pct,
            is_tick_valid=is_tick_valid,
            is_band_valid=is_band_valid,
            is_size_valid=is_size_valid,
            is_house_limit_valid=is_house_limit_valid,
            cb_would_trigger=cb_would_trigger,
            cb_notes=cb_notes,
        )

        return JseOrderReport(
            alpha_code=code_clean,
            side=side_clean,
            price_zac=price,
            equivalent_price_zar=equiv_zar,
            applicable_tick_size_zac=TICK_SIZE_ZAC,
            quantity_shares=quantity,
            notional_value_zar=notional_zar,
            trading_segment=segment,
            trading_session=session,
            is_price_tick_valid=is_tick_valid,
            is_price_band_valid=is_band_valid,
            is_order_size_valid=is_size_valid,
            is_rejected=status in REJECTION_STATUSES,
            status=status,
            audit_notes=notes,
            price_band_lower_zac=band_lower,
            price_band_upper_zac=band_upper,
            static_circuit_breaker_pct=static_pct,
            dynamic_circuit_breaker_pct=dynamic_pct,
            circuit_breaker_would_trigger=cb_would_trigger,
            circuit_breaker_notes=cb_notes,
        )

    def _classify(
        self,
        code: str,
        side: str,
        segment: str,
        session: str,
        price: float,
        quantity: int,
        equiv_zar: float,
        notional_zar: float,
        band_lower: Optional[float],
        band_upper: Optional[float],
        house_deviation_pct: float,
        is_tick_valid: bool,
        is_band_valid: bool,
        is_size_valid: bool,
        is_house_limit_valid: bool,
        cb_would_trigger: bool,
        cb_notes: List[str],
    ) -> Tuple[str, str]:
        """Maps the individual audits to a single status plus an audit note."""
        if not is_tick_valid:
            notes = (
                f"JSE REJECT [{code}]: Price {price:,.4f} ZAC is not a whole number of "
                f"cents; the JSE tick size is {TICK_SIZE_ZAC} ZAC for every instrument."
            )
            logger.warning(notes)
            return "INVALID_TICK_SIZE", notes
        if not is_band_valid:
            notes = (
                f"JSE REJECT [{code}]: Price {price:,.0f} ZAC is outside the {segment} "
                f"price band {band_lower:,.0f} - {band_upper:,.0f} ZAC "
                f"(+/-{PRICE_BAND_OUTER_LIMIT_PCT[segment]:.0f}% of the static reference price)."
            )
            logger.warning(notes)
            return "PRICE_BAND_BREACH", notes
        if not is_size_valid:
            notes = (
                f"JSE REJECT [{code}]: Quantity {quantity:,} shares exceeds the JSE "
                f"Maximum Order Size of {MAX_ORDER_QUANTITY:,} shares."
            )
            logger.warning(notes)
            return "ORDER_SIZE_EXCEEDED", notes
        if not is_house_limit_valid:
            notes = (
                f"HOUSE REJECT [{code}]: Price deviation {house_deviation_pct:.2f}% from the "
                f"static reference price exceeds the in-house cap of "
                f"{self.house_price_deviation_limit_pct:.2f}%. This is a house control, "
                f"not a JSE rule."
            )
            logger.warning(notes)
            return "HOUSE_LIMIT_EXCEEDED", notes

        summary = (
            f"[{code} - {segment}/{session}]: {side} {quantity:,} shares @ {price:,.0f} ZAC "
            f"(ZAR {equiv_zar:,.2f}). Notional = ZAR {notional_zar:,.2f} "
            f"(tick {TICK_SIZE_ZAC} ZAC, lot {LOT_SIZE})."
        )
        if cb_would_trigger:
            notes = (
                f"JSE ORDER ACCEPTED WITH VOLATILITY AUCTION RISK {summary} "
                f"A trade at this price would breach a circuit breaker tolerance and move "
                f"the instrument into a Volatility Auction Call session: {' '.join(cb_notes)} "
                f"The order itself is valid and will not be rejected."
            )
            logger.warning(notes)
            return "VOLATILITY_AUCTION_RISK", notes

        notes = f"JSE ORDER VALIDATED {summary} {' '.join(cb_notes)}"
        logger.info(notes)
        return "JSE_ORDER_VALIDATED", notes

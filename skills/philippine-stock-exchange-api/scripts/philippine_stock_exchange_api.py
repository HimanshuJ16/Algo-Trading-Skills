"""PSE (Philippine Stock Exchange) pre-trade order validation.

Encodes the PSE cash-equity order-entry rules in force, verified against PSE
primary sources on **27 August 2026**:

* **Board Lot and Price Fluctuation (tick size)** -- PSE Revised Trading Rules,
  Article IV, Section 8: *"The Board Lot and Price Fluctuation of a Security for
  any Trading Day shall be based on the Security's **Reference Price**."* Both
  the lot and the tick are therefore properties of the *security for the day*,
  fixed by the Reference Price (the previous day's close, or the Last Adjusted
  Closing Price where a corporate action intervened). They are **not** re-derived
  from each order's limit price, and they do **not** change intraday when the
  stock trades through a band boundary.
* **Static Threshold** -- PSE Revised Trading Rules, Article IV, Section 7(b), as
  amended by PSE Circular CN-2020-0028 (21 March 2020, effective 24 March 2020):
  the upper static threshold is **+50%** of the Reference Price and the lower
  static threshold is **-30%**. The band is *asymmetric*. It was symmetric at
  +/-50% only until 23 March 2020.
* **Dynamic Threshold** -- a second, per-security band measured against the
  **Last Traded Price**, set by PSE per trade-frequency cluster and reviewed
  semi-annually (PSE TPA-2022-0036: 20% / 15% / 10% for clusters A / B / C).
  An order can sit comfortably inside the static band and still be rejected by
  the dynamic threshold. Because the percentage is assigned per security by PSE
  circular, this module never guesses it: pass ``dynamic_threshold_pct`` and
  ``last_traded_price`` to have it checked, or leave them unset and the report
  will say the check was not performed.

Band bounds are rounded onto the security's tick lattice -- the ceiling **down**
and the floor **up** -- so both bounds are placeable prices that lie inside the
regulatory percentage. PSE's published worked example for PLDT (TEL) at a
Reference Price of PHP 1,642.00 gives a ceiling of PHP 2,463.00 and a floor of
PHP 1,150.00 (raw 1,149.40 rounded **up**), both on the PHP 1.00 tick of the
Reference Price's band -- note that PHP 2,463.00 is *not* a multiple of the
PHP 2.00 tick belonging to the band the ceiling itself falls in, which is a
direct confirmation that the Reference Price governs the whole day's lattice.

All price arithmetic is done in ``Decimal``. Binary floats break this domain in
three separate ways, each reproducible: ``0.30 * 1.50`` is ``0.4499999999999999``
so an order at exactly the PHP 0.45 ceiling is falsely rejected; and a
scale-and-round tick test (``round(price * 10000) % round(tick * 10000)``)
silently rounds a sub-tick price such as PHP 1,000.00005 *into* validity, and
accepts PHP 0.00005 -- below the PHP 0.0001 minimum -- as a valid tick.

This module is a client-side pre-trade filter. The PSE matching engine remains
authoritative and can reject an order this engine approves.

Not modelled: trading halts and suspensions, the market-wide circuit breaker,
the pre-open / pre-close / run-off (Trading-at-Last) / closing-VWAP session
mechanics, the Odd Lot Market, block sales and cross trades, broker commissions
and taxes, and any credit, margin or position check.
"""
import logging
import math
from dataclasses import dataclass
from decimal import Decimal, DecimalException, InvalidOperation
from typing import Dict, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

VALID_SIDES = ("BUY", "SELL")

# Market segment. Selects the board lot / tick schedule -- it is a property of
# the listing, not something derivable from the price. A dollar-denominated
# security validated against the peso schedule is silently mis-lotted: USD 1.50
# demands a 20-share lot under the DDS schedule and a 1,000-share lot under the
# peso one.
MARKET_PHP = "PHP"   # Peso-denominated securities (the main board).
MARKET_DDS = "DDS"   # Dollar-denominated securities.
VALID_MARKETS = (MARKET_PHP, MARKET_DDS)

CURRENCY_BY_MARKET = {MARKET_PHP: "PHP", MARKET_DDS: "USD"}

# A schedule row: (band_from, band_to, tick_size, board_lot). ``band_to`` is
# None for the open-ended top band. Both bounds are INCLUSIVE -- PSE publishes
# the table with explicit "From" and "To" columns, e.g. "0.5000 | 4.9900".
ScheduleRow = Tuple[Decimal, Optional[Decimal], Decimal, int]

# PSE Revised Trading Rules, Article IV, Section 8 -- peso-denominated
# securities. Reproduced verbatim from the "Existing" column of the PSE
# Consultation Paper circulated as CN-2025-0046 (15 December 2025).
#
# The bands tile the tick lattice exactly: adding one tick to a band's "To"
# lands on the next band's "From" (49.9500 + 0.0500 = 50.0000, 999.5000 + 0.5000
# = 1000.0000, 4998.0000 + 2.0000 = 5000.0000). A price falling strictly between
# two bands is therefore off-lattice and cannot be a real PSE price.
PSE_PHP_SCHEDULE: Tuple[ScheduleRow, ...] = (
    (Decimal("0.0001"), Decimal("0.0099"), Decimal("0.0001"), 1_000_000),
    (Decimal("0.0100"), Decimal("0.0490"), Decimal("0.0010"), 100_000),
    (Decimal("0.0500"), Decimal("0.2490"), Decimal("0.0010"), 10_000),
    (Decimal("0.2500"), Decimal("0.4950"), Decimal("0.0050"), 10_000),
    (Decimal("0.5000"), Decimal("4.9900"), Decimal("0.0100"), 1_000),
    (Decimal("5.0000"), Decimal("9.9900"), Decimal("0.0100"), 100),
    (Decimal("10.0000"), Decimal("19.9800"), Decimal("0.0200"), 100),
    (Decimal("20.0000"), Decimal("49.9500"), Decimal("0.0500"), 100),
    (Decimal("50.0000"), Decimal("99.9500"), Decimal("0.0500"), 10),
    (Decimal("100.0000"), Decimal("199.9000"), Decimal("0.1000"), 10),
    (Decimal("200.0000"), Decimal("499.8000"), Decimal("0.2000"), 10),
    (Decimal("500.0000"), Decimal("999.5000"), Decimal("0.5000"), 10),
    (Decimal("1000.0000"), Decimal("1999.0000"), Decimal("1.0000"), 5),
    (Decimal("2000.0000"), Decimal("4998.0000"), Decimal("2.0000"), 5),
    (Decimal("5000.0000"), None, Decimal("5.0000"), 5),
)

# PSE Rules on Dollar Denominated Securities, Part C, Section 1.a. Also
# reproduced from the "Existing" column of CN-2025-0046. Prices are in USD.
# PSE prints the first band as "DOWN | 0.99"; the USD 0.01 tick makes USD 0.01
# the effective minimum.
PSE_DDS_SCHEDULE: Tuple[ScheduleRow, ...] = (
    (Decimal("0.01"), Decimal("0.99"), Decimal("0.01"), 100),
    (Decimal("1.00"), Decimal("4.99"), Decimal("0.01"), 20),
    (Decimal("5.00"), Decimal("9.99"), Decimal("0.01"), 10),
    (Decimal("10.00"), Decimal("19.98"), Decimal("0.02"), 10),
    (Decimal("20.00"), Decimal("49.95"), Decimal("0.05"), 10),
    (Decimal("50.00"), Decimal("99.95"), Decimal("0.05"), 5),
    (Decimal("100.00"), Decimal("199.90"), Decimal("0.10"), 5),
    (Decimal("200.00"), Decimal("499.80"), Decimal("0.20"), 5),
    (Decimal("500.00"), Decimal("999.50"), Decimal("0.50"), 5),
    (Decimal("1000.00"), None, Decimal("1.00"), 5),
)

SCHEDULES: Dict[str, Tuple[ScheduleRow, ...]] = {
    MARKET_PHP: PSE_PHP_SCHEDULE,
    MARKET_DDS: PSE_DDS_SCHEDULE,
}

# Static Threshold -- Article IV, Section 7(b), as amended by CN-2020-0028
# effective 24 March 2020. ASYMMETRIC: +50% above, -30% below. It was +/-50%
# only until 23 March 2020; a backtest replaying an earlier session needs the
# symmetric figure, which is why both are constructor arguments.
UPPER_STATIC_THRESHOLD_PCT = Decimal("50")
LOWER_STATIC_THRESHOLD_PCT = Decimal("30")

# Statuses emitted by validate_pse_order(). Malformed input is RAISED, never
# folded into a status -- a caller bug must not be mistakable for an
# exchange-rule rejection.
STATUS_VALID = "ORDER_VALID_COMPLIANT"
STATUS_STATIC_BREACH = "PRICE_BAND_BREACH"
STATUS_DYNAMIC_BREACH = "DYNAMIC_THRESHOLD_BREACH"
STATUS_INVALID_LOT = "INVALID_BOARD_LOT"
STATUS_INVALID_TICK = "INVALID_TICK_SIZE"


def _to_decimal(value: object, field_name: str) -> Decimal:
    """Convert an incoming numeric price to an exact ``Decimal``.

    Routes floats through ``str`` so that ``0.45`` becomes ``Decimal('0.45')``
    rather than its binary expansion. Rejects NaN and +/-Inf rather than letting
    them propagate into a comparison: a NaN price makes every ``<=`` test return
    ``False``, so the order would be reported as a *rule breach* rather than as
    the data fault it is.
    """
    if isinstance(value, Decimal):
        candidate = value
    else:
        try:
            numeric = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field_name} must be numeric, got {value!r}.") from exc
        if not math.isfinite(numeric):
            raise ValueError(f"{field_name} must be finite, got {value!r}.")
        try:
            # 1642.0 -> Decimal('1642') rather than Decimal('1642.0'), so the
            # audit notes read as PSE prints prices. Comparisons are unaffected.
            candidate = (
                Decimal(int(numeric)) if numeric.is_integer() else Decimal(str(numeric))
            )
        except InvalidOperation as exc:  # pragma: no cover - guarded by isfinite
            raise ValueError(f"{field_name} is not a valid decimal: {value!r}.") from exc
    if not candidate.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}.")
    return candidate


def _divide_onto_tick(value: Decimal, tick: Decimal) -> Decimal:
    """``value // tick`` as a ``ValueError`` rather than a ``DecimalException``.

    Decimal floor division raises ``DivisionImpossible`` once the quotient needs
    more digits than the active context precision (28 by default). A PSE price
    never does; a mis-scaled feed -- a price sent in the wrong unit, say --
    easily can, and it must surface as the input fault it is rather than as an
    uncaught arithmetic exception in the routing path.
    """
    try:
        return value // tick
    except DecimalException as exc:
        raise ValueError(
            f"Price {value} cannot be placed on the {tick} tick lattice at the "
            f"active decimal precision. A value this large is not a PSE price; "
            f"check the units and scaling of the price feed."
        ) from exc


def _floor_to_tick(value: Decimal, tick: Decimal) -> Decimal:
    """Largest multiple of ``tick`` that is <= ``value`` (``value``, ``tick`` > 0)."""
    return _divide_onto_tick(value, tick) * tick


def _ceil_to_tick(value: Decimal, tick: Decimal) -> Decimal:
    """Smallest multiple of ``tick`` that is >= ``value`` (``value``, ``tick`` > 0)."""
    floored = _divide_onto_tick(value, tick) * tick
    return floored if floored == value else floored + tick


@dataclass
class Config:
    """Connection configuration for a PSE broker or PSEtrade gateway session."""

    api_key: str
    environment: str = "PRODUCTION"


@dataclass
class PSEOrderRequest:
    """A single PSE cash-equity order presented for pre-trade validation.

    Attributes:
        symbol: PSE trading symbol, e.g. 'SM', 'TEL', 'BDO'.
        side: 'BUY' or 'SELL'.
        price: Limit price of the order, in the market's currency.
        quantity: Order size in SHARES. Must be a positive multiple of the
            board lot for the day.
        reference_price: The security's **Reference Price** for the trading day
            -- the previous session's closing price, or the Last Adjusted
            Closing Price (LACP) where a corporate action intervened. Under
            Article IV Section 8 this single number fixes BOTH the board lot and
            the tick size for the whole day, and it anchors the static
            threshold band. It is NOT the order price and NOT the last traded
            price.
        market: 'PHP' (peso-denominated, the main board) or 'DDS'
            (dollar-denominated). Selects the schedule; take it from reference
            data rather than inferring it.
        last_traded_price: Most recent traded price, required only when the
            dynamic threshold is to be checked.
        dynamic_threshold_pct: The security's dynamic threshold percentage as
            published by PSE for the current review period (20 / 15 / 10 for
            trade-frequency clusters A / B / C under TPA-2022-0036). Leave
            None to skip the check -- this module will not guess a cluster.
    """

    symbol: str
    side: str
    price: float
    quantity: int
    reference_price: float
    market: str = MARKET_PHP
    last_traded_price: Optional[float] = None
    dynamic_threshold_pct: Optional[float] = None


@dataclass
class PSEReport:
    """Structured outcome of a pre-trade PSE order audit.

    Carries the applied lattice and **both** band bounds so a rejection can be
    repriced directly rather than forcing the caller to re-derive them.
    """

    symbol: str
    side: str
    price: Decimal
    quantity: int
    market: str
    currency: str                        # 'PHP' or 'USD'
    reference_price: Decimal
    required_board_lot: int
    required_tick_size: Decimal
    price_floor: Decimal                 # Static lower bound, on-tick, INCLUSIVE
    price_ceiling: Decimal               # Static upper bound, on-tick, INCLUSIVE
    dynamic_floor: Optional[Decimal]     # None when the check was not performed
    dynamic_ceiling: Optional[Decimal]
    is_valid_board_lot: bool
    is_valid_tick_size: bool
    is_within_price_band: bool           # Static threshold
    is_within_dynamic_band: Optional[bool]   # None when not checked
    status: str
    audit_notes: str


class PhilippineStockExchangeEngine:
    """Pre-trade validator for PSE cash-equity orders.

    Enforces the three order-entry rules a PSE gateway checks and a client can
    check for itself: board lot divisibility, tick alignment, and the static
    threshold band -- plus the per-security dynamic threshold when the caller
    supplies its published percentage.

    Malformed input (unknown side or market, non-integer or non-positive
    quantity, non-positive or non-finite price or reference price) is raised as
    ``ValueError``, never folded into a status.
    """

    def __init__(
        self,
        config: Optional[Config] = None,
        upper_static_threshold_pct: float = UPPER_STATIC_THRESHOLD_PCT,
        lower_static_threshold_pct: float = LOWER_STATIC_THRESHOLD_PCT,
        schedules: Optional[Dict[str, Sequence[ScheduleRow]]] = None,
    ) -> None:
        """
        Args:
            config: Optional gateway/broker connection configuration.
            upper_static_threshold_pct: Percent above the Reference Price. 50
                since the rule's inception.
            lower_static_threshold_pct: Percent below the Reference Price. 30
                since 24 March 2020; pass 50 to replay a session on or before
                23 March 2020.
            schedules: Optional replacement board lot / tick schedules keyed by
                market, for testing or for adopting a published schedule change
                without editing this module. PSE has circulated a consultation
                paper (CN-2025-0046) proposing a One Lot One Share structure
                with the Nasdaq Eqlipse Trading rollout; when that takes
                effect, inject the new table here rather than forking the file.
        """
        self.config = config or Config(api_key="default_pse_key")

        self.upper_static_threshold_pct = _to_decimal(
            upper_static_threshold_pct, "upper_static_threshold_pct"
        )
        self.lower_static_threshold_pct = _to_decimal(
            lower_static_threshold_pct, "lower_static_threshold_pct"
        )
        if self.upper_static_threshold_pct <= 0:
            raise ValueError(
                f"upper_static_threshold_pct must be strictly positive, "
                f"got {upper_static_threshold_pct!r}."
            )
        if not (0 < self.lower_static_threshold_pct < 100):
            raise ValueError(
                f"lower_static_threshold_pct must lie strictly between 0 and 100, "
                f"got {lower_static_threshold_pct!r}. At 100 the floor collapses to "
                f"zero, which is not a placeable PSE price."
            )

        self.schedules: Dict[str, Tuple[ScheduleRow, ...]] = (
            {k: tuple(v) for k, v in schedules.items()} if schedules else dict(SCHEDULES)
        )

    @staticmethod
    def _normalise_market(market: str) -> str:
        clean = str(market).strip().upper()
        if clean not in VALID_MARKETS:
            raise ValueError(
                f"Unknown market '{market}'. Expected one of {VALID_MARKETS}."
            )
        return clean

    def get_pse_tier(
        self, reference_price: float, market: str = MARKET_PHP
    ) -> Tuple[Decimal, int]:
        """Return ``(tick_size, board_lot)`` for a security's Reference Price.

        Article IV Section 8 keys the board lot and the price fluctuation off the
        **Reference Price**, so pass the previous close / LACP here -- never the
        order price and never the last traded price. The pair returned governs
        every order in the security for the whole trading day.

        Band bounds are inclusive on both sides. PSE's bands tile the tick
        lattice exactly, so a price falling strictly between two bands is
        off-lattice; that happens in practice when an LACP has been adjusted for
        a corporate action. Such a price is assigned the band **below** it --
        the conservative choice, since the lower band never has a coarser tick
        nor a smaller lot -- and a warning is logged.

        Raises:
            ValueError: If the price is non-finite, or below the schedule's
                minimum (PHP 0.0001 / USD 0.01), or the market is unknown.
        """
        market_clean = self._normalise_market(market)
        price = _to_decimal(reference_price, "reference_price")
        schedule = self.schedules[market_clean]
        currency = CURRENCY_BY_MARKET.get(market_clean, market_clean)

        minimum = schedule[0][0]
        if price < minimum:
            raise ValueError(
                f"reference_price {currency} {price} is below the PSE minimum "
                f"{currency} {minimum} for the {market_clean} schedule."
            )

        previous_row: Optional[ScheduleRow] = None
        for row in schedule:
            band_from, band_to, tick, lot = row
            if price < band_from:
                # Off-lattice: the price sits in the gap between two bands.
                # previous_row cannot be None -- the minimum check above ruled
                # out anything below the first band's lower bound.
                _, prev_to, prev_tick, prev_lot = previous_row  # type: ignore[misc]
                logger.warning(
                    "PSE reference price %s %s is off the PSE price lattice "
                    "(falls between %s and %s). Applying the lower band "
                    "(tick %s, lot %s). Verify the Reference Price / LACP.",
                    currency, price, prev_to, band_from, prev_tick, prev_lot,
                )
                return prev_tick, prev_lot
            if band_to is None or price <= band_to:
                return tick, lot
            previous_row = row
        # Unreachable: every schedule terminates in an open-ended (None) band.
        raise ValueError(
            f"No PSE board lot band matched {currency} {price} "
            f"for market '{market_clean}'."
        )

    def get_static_threshold_bounds(
        self, reference_price: float, market: str = MARKET_PHP
    ) -> Tuple[Decimal, Decimal]:
        """Return the on-tick ``(floor, ceiling)`` static threshold band.

        ``ceiling = floor_to_tick(reference x (1 + upper/100))`` and
        ``floor = ceil_to_tick(reference x (1 - lower/100))``, both on the tick
        of the **Reference Price's** band. Rounding inward keeps each bound a
        placeable price that stays inside the regulatory percentage -- rounding
        the floor *down* would publish a bound representing a fall of more than
        the permitted percentage.

        Both bounds are INCLUSIVE: an order at exactly the ceiling is the
        ceiling price and is accepted.

        PSE's published worked example: PLDT (TEL), Reference Price
        PHP 1,642.00 -> ceiling PHP 2,463.00, floor PHP 1,150.00 (raw
        1,149.40 rounded up to the PHP 1.00 tick).
        """
        tick, _ = self.get_pse_tier(reference_price, market)
        reference = _to_decimal(reference_price, "reference_price")
        hundred = Decimal("100")
        raw_ceiling = reference * (hundred + self.upper_static_threshold_pct) / hundred
        raw_floor = reference * (hundred - self.lower_static_threshold_pct) / hundred
        return _ceil_to_tick(raw_floor, tick), _floor_to_tick(raw_ceiling, tick)

    def get_dynamic_threshold_bounds(
        self,
        last_traded_price: float,
        dynamic_threshold_pct: float,
        reference_price: float,
        market: str = MARKET_PHP,
    ) -> Tuple[Decimal, Decimal]:
        """Return the on-tick ``(floor, ceiling)`` dynamic threshold band.

        The dynamic threshold is symmetric about the **Last Traded Price**, at
        the percentage PSE publishes for that security's trade-frequency cluster
        (20 / 15 / 10 for clusters A / B / C under TPA-2022-0036, reviewed
        semi-annually). The tick still comes from the Reference Price's band,
        because that is what fixes the day's lattice.

        Raises:
            ValueError: If the last traded price is non-positive or non-finite,
                or the percentage is not strictly between 0 and 100.
        """
        tick, _ = self.get_pse_tier(reference_price, market)
        ltp = _to_decimal(last_traded_price, "last_traded_price")
        if ltp <= 0:
            raise ValueError(
                f"last_traded_price must be strictly positive, got "
                f"{last_traded_price!r}."
            )
        pct = _to_decimal(dynamic_threshold_pct, "dynamic_threshold_pct")
        if not (0 < pct < 100):
            raise ValueError(
                f"dynamic_threshold_pct must lie strictly between 0 and 100, got "
                f"{dynamic_threshold_pct!r}. PSE publishes 20 / 15 / 10 for "
                f"trade-frequency clusters A / B / C."
            )
        hundred = Decimal("100")
        return (
            _ceil_to_tick(ltp * (hundred - pct) / hundred, tick),
            _floor_to_tick(ltp * (hundred + pct) / hundred, tick),
        )

    def validate_pse_order(self, order: PSEOrderRequest) -> PSEReport:
        """Validate a PSE order against board lot, tick and threshold rules.

        Raises:
            ValueError: On malformed input -- unknown side or market,
                non-integer or non-positive quantity, non-positive or
                non-finite price or reference price, or a dynamic threshold
                percentage supplied without a last traded price (or vice
                versa).
        """
        market = self._normalise_market(order.market)
        currency = CURRENCY_BY_MARKET[market]

        side = str(order.side).strip().upper()
        if side not in VALID_SIDES:
            raise ValueError(
                f"Invalid side '{order.side}'. Expected one of {VALID_SIDES}."
            )

        # bool is a subclass of int; True would otherwise pass as a 1-share order.
        if isinstance(order.quantity, bool) or not isinstance(order.quantity, int):
            raise ValueError(
                f"quantity must be an int number of shares, got {order.quantity!r}."
            )
        if order.quantity <= 0:
            raise ValueError(
                f"quantity must be strictly positive, got {order.quantity!r}."
            )

        price = _to_decimal(order.price, "price")
        if price <= 0:
            raise ValueError(f"price must be strictly positive, got {order.price!r}.")
        reference = _to_decimal(order.reference_price, "reference_price")
        if reference <= 0:
            raise ValueError(
                f"reference_price must be strictly positive, got "
                f"{order.reference_price!r}. Without a Reference Price neither the "
                f"board lot, the tick size, nor the threshold band is defined."
            )

        # The Reference Price -- not the order price -- fixes the day's lattice.
        tick_size, board_lot = self.get_pse_tier(reference, market)

        is_valid_lot = (order.quantity % board_lot) == 0
        # Exact Decimal modulo. No scale-and-round: that accepts sub-tick prices.
        # Routed through the same guard as the band rounding, so an implausibly
        # large price raises ValueError rather than DecimalException.
        is_valid_tick = _floor_to_tick(price, tick_size) == price

        price_floor, price_ceiling = self.get_static_threshold_bounds(reference, market)
        is_within_band = price_floor <= price <= price_ceiling

        # Dynamic threshold: opt-in and all-or-nothing. Half the inputs is a
        # caller bug, and silently skipping the check is its dangerous reading.
        has_ltp = order.last_traded_price is not None
        has_pct = order.dynamic_threshold_pct is not None
        if has_ltp != has_pct:
            raise ValueError(
                "last_traded_price and dynamic_threshold_pct must be supplied "
                "together to check the dynamic threshold, or both omitted to skip "
                "it. PSE assigns the percentage per security by circular; it "
                "cannot be inferred from the price."
            )
        dynamic_floor: Optional[Decimal] = None
        dynamic_ceiling: Optional[Decimal] = None
        is_within_dynamic: Optional[bool] = None
        if has_ltp and has_pct:
            dynamic_floor, dynamic_ceiling = self.get_dynamic_threshold_bounds(
                order.last_traded_price,      # type: ignore[arg-type]
                order.dynamic_threshold_pct,  # type: ignore[arg-type]
                reference,
                market,
            )
            is_within_dynamic = dynamic_floor <= price <= dynamic_ceiling

        if not is_within_band:
            status = STATUS_STATIC_BREACH
        elif is_within_dynamic is False:
            status = STATUS_DYNAMIC_BREACH
        elif not is_valid_lot:
            status = STATUS_INVALID_LOT
        elif not is_valid_tick:
            status = STATUS_INVALID_TICK
        else:
            status = STATUS_VALID

        if is_within_dynamic is None:
            dynamic_note = (
                "Dynamic Threshold: NOT CHECKED (no published percentage supplied)."
            )
        else:
            dynamic_note = (
                f"Dynamic Band: {currency} {dynamic_floor:,} - "
                f"{currency} {dynamic_ceiling:,} (LTP {currency} "
                f"{_to_decimal(order.last_traded_price, 'last_traded_price'):,}, "
                f"+/-{_to_decimal(order.dynamic_threshold_pct, 'dynamic_threshold_pct')}%)."
            )
        notes = (
            f"PSE ORDER AUDIT [{order.symbol} {side} {market} - {status}]: "
            f"Price = {currency} {price:,}, Qty = {order.quantity:,} "
            f"(Ref Price = {currency} {reference:,} => Lot = {board_lot:,}, "
            f"Tick = {tick_size}). "
            f"Static Band: {currency} {price_floor:,} - {currency} {price_ceiling:,} "
            f"(+{self.upper_static_threshold_pct}% / "
            f"-{self.lower_static_threshold_pct}%). {dynamic_note}"
        )

        if status == STATUS_VALID:
            logger.info(notes)
        else:
            logger.warning("PSE ORDER REJECTION: %s", notes)

        return PSEReport(
            symbol=order.symbol,
            side=side,
            price=price,
            quantity=order.quantity,
            market=market,
            currency=currency,
            reference_price=reference,
            required_board_lot=board_lot,
            required_tick_size=tick_size,
            price_floor=price_floor,
            price_ceiling=price_ceiling,
            dynamic_floor=dynamic_floor,
            dynamic_ceiling=dynamic_ceiling,
            is_valid_board_lot=is_valid_lot,
            is_valid_tick_size=is_valid_tick,
            is_within_price_band=is_within_band,
            is_within_dynamic_band=is_within_dynamic,
            status=status,
            audit_notes=notes,
        )

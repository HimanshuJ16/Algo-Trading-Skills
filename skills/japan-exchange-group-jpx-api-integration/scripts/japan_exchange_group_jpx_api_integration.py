"""JPX / Tokyo Stock Exchange (arrowhead4.0) pre-trade order validation.

Encodes the TSE cash-equity trading rules published by Japan Exchange Group on
the "Trading Rules of Domestic Stocks" pages, verified 25 August 2026:

* Tick Size (呼値の単位) -- three separate tables (TOPIX500 constituents,
  single-unit ETFs/ETNs, all other issues), price bands with **inclusive**
  upper bounds (「以下」). See JPX tick size page, updated 6 August 2026.
* Daily Price Limits (制限値幅) -- **absolute yen** amounts keyed to the base
  price (基準値段), price bands with **exclusive** upper bounds (「未満」).
  See JPX daily price limits page, updated 24 August 2026.
* Trading Unit (売買単位) -- 100 shares for domestic stocks since
  1 October 2018. ETFs/ETNs/REITs trade in units of 1 or 10.
* Securities codes -- four characters. Codes assigned before January 2024 are
  four digits; codes assigned from 1 January 2024 may carry a letter in the
  2nd and/or 4th position (Securities Identification Code Committee).

The minimum tick on TSE is JPY 0.1 (TOPIX500 constituents priced at or below
JPY 1,000), so prices are NOT whole yen. Every price comparison in this module
is done in ``Decimal`` to avoid binary-float drift on 0.1 and 0.5 increments.

This module is a client-side pre-trade filter. arrowhead remains authoritative.
"""
import logging
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

VALID_SIDES = ("BUY", "SELL")

# Securities Identification Code Committee (SICC): specific name codes for
# stocks are four characters. Letters may appear in the 2nd and/or 4th position
# only (the 1st and 3rd are always digits), and only 19 uppercase letters are
# used -- "B", "E", "I", "O", "Q", "V" and "Z" are excluded to avoid confusion
# with digits and with each other. Codes assigned before January 2024 remain
# all-numeric and are unchanged (e.g. 7203, 6758, 8697).
SICC_CODE_LETTERS = "ACDFGHJKLMNPRSTUWXY"
SICC_CODE_PATTERN = re.compile(
    r"^[0-9][0-9{letters}][0-9][0-9{letters}]$".format(letters=SICC_CODE_LETTERS)
)

# Tick size table identifiers. TSE publishes three tables and applies them by
# issue, not by price alone -- the price only selects the band WITHIN a table.
TICK_TABLE_TOPIX500 = "TOPIX500"
TICK_TABLE_ETF_SINGLE_UNIT = "ETF_SINGLE_UNIT"
TICK_TABLE_OTHER = "OTHER"
VALID_TICK_TABLES = (TICK_TABLE_TOPIX500, TICK_TABLE_ETF_SINGLE_UNIT, TICK_TABLE_OTHER)

# Tick schedules: (INCLUSIVE upper bound of the price band, tick size).
# A None bound is the open-ended top band. TSE publishes these bands as
# 「1,000円以下」 -- "1,000 yen or less" -- so a price sitting exactly on a
# boundary takes the FINER tick of the lower band. Adjacent bands carrying the
# same tick are collapsed here, matching JPX's own English presentation.

# TOPIX500 constituents (TOPIX100 + TOPIX Mid400), per Rule 14, Paragraph 3,
# Item 1-b of the TSE Business Regulations. Also applied to ETFs, ETNs and
# leveraged products with a trading unit of 10 or above.
TICK_SCHEDULE_TOPIX500: Tuple[Tuple[Optional[int], Decimal], ...] = (
    (1_000, Decimal("0.1")),
    (3_000, Decimal("0.5")),
    (10_000, Decimal("1")),
    (30_000, Decimal("5")),
    (100_000, Decimal("10")),
    (300_000, Decimal("50")),
    (1_000_000, Decimal("100")),
    (3_000_000, Decimal("500")),
    (10_000_000, Decimal("1000")),
    (30_000_000, Decimal("5000")),
    (None, Decimal("10000")),
)

# ETFs, ETNs and leveraged products with a trading unit of one unit.
TICK_SCHEDULE_ETF_SINGLE_UNIT: Tuple[Tuple[Optional[int], Decimal], ...] = (
    (10_000, Decimal("1")),
    (30_000, Decimal("5")),
    (100_000, Decimal("10")),
    (300_000, Decimal("50")),
    (1_000_000, Decimal("100")),
    (3_000_000, Decimal("500")),
    (10_000_000, Decimal("1000")),
    (30_000_000, Decimal("5000")),
    (None, Decimal("10000")),
)

# All other issues -- ordinary domestic stocks outside TOPIX500.
TICK_SCHEDULE_OTHER: Tuple[Tuple[Optional[int], Decimal], ...] = (
    (3_000, Decimal("1")),
    (5_000, Decimal("5")),
    (30_000, Decimal("10")),
    (50_000, Decimal("50")),
    (300_000, Decimal("100")),
    (500_000, Decimal("500")),
    (3_000_000, Decimal("1000")),
    (5_000_000, Decimal("5000")),
    (30_000_000, Decimal("10000")),
    (50_000_000, Decimal("50000")),
    (None, Decimal("100000")),
)

TICK_SCHEDULES = {
    TICK_TABLE_TOPIX500: TICK_SCHEDULE_TOPIX500,
    TICK_TABLE_ETF_SINGLE_UNIT: TICK_SCHEDULE_ETF_SINGLE_UNIT,
    TICK_TABLE_OTHER: TICK_SCHEDULE_OTHER,
}

# Daily Price Limits (制限値幅): (EXCLUSIVE upper bound of the base-price band,
# limit in absolute yen). TSE publishes these bands as 「100円未満」 -- "less
# than 100 yen" -- so the bound is exclusive here, unlike the tick bands above.
# The limit is an absolute yen amount, NOT a percentage: it is roughly 30% of
# the base price at the bottom of the table and roughly 10-20% higher up.
DAILY_PRICE_LIMIT_SCHEDULE: Tuple[Tuple[Optional[int], Decimal], ...] = (
    (100, Decimal("30")),
    (200, Decimal("50")),
    (500, Decimal("80")),
    (700, Decimal("100")),
    (1_000, Decimal("150")),
    (1_500, Decimal("300")),
    (2_000, Decimal("400")),
    (3_000, Decimal("500")),
    (5_000, Decimal("700")),
    (7_000, Decimal("1000")),
    (10_000, Decimal("1500")),
    (15_000, Decimal("3000")),
    (20_000, Decimal("4000")),
    (30_000, Decimal("5000")),
    (50_000, Decimal("7000")),
    (70_000, Decimal("10000")),
    (100_000, Decimal("15000")),
    (150_000, Decimal("30000")),
    (200_000, Decimal("40000")),
    (300_000, Decimal("50000")),
    (500_000, Decimal("70000")),
    (700_000, Decimal("100000")),
    (1_000_000, Decimal("150000")),
    (1_500_000, Decimal("300000")),
    (2_000_000, Decimal("400000")),
    (3_000_000, Decimal("500000")),
    (5_000_000, Decimal("700000")),
    (7_000_000, Decimal("1000000")),
    (10_000_000, Decimal("1500000")),
    (15_000_000, Decimal("3000000")),
    (20_000_000, Decimal("4000000")),
    (30_000_000, Decimal("5000000")),
    (50_000_000, Decimal("7000000")),
    (None, Decimal("10000000")),
)

# Trading units were normalised to 100 shares for all domestic stocks on
# 1 October 2018. ETFs, ETNs, REITs and leveraged products are not domestic
# stocks and trade in units of 1 or 10 -- pass those explicitly.
DEFAULT_TRADING_UNIT = 100


def _to_decimal(value: object, field_name: str) -> Decimal:
    """Convert an incoming numeric price to an exact Decimal.

    Goes through ``str`` so that a float literal such as ``2500.5`` becomes
    ``Decimal('2500.5')`` rather than its binary expansion -- tick alignment at
    JPY 0.1 and JPY 0.5 is only exact if the decimal value is preserved.
    Rejects NaN, +/-Inf and non-numeric input rather than letting them
    propagate silently into a comparison that would quietly return False.
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
            candidate = Decimal(str(numeric))
        except InvalidOperation as exc:  # pragma: no cover - guarded by isfinite
            raise ValueError(f"{field_name} is not a valid decimal: {value!r}.") from exc
    if not candidate.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}.")
    return candidate


@dataclass
class JpxOrderPayload:
    """A single TSE cash-equity order presented to an arrowhead gateway.

    Attributes:
        local_code: Four-character TSE securities code. All-numeric for issues
            coded before January 2024 ('7203' Toyota, '6758' Sony, '9984'
            SoftBank Group); may carry a letter in the 2nd and/or 4th position
            for issues coded from 1 January 2024 ('130A').
        side: 'BUY' or 'SELL'.
        price_jpy: Limit price in yen. May be fractional -- the minimum tick on
            TSE is JPY 0.1 for TOPIX500 constituents priced at or below
            JPY 1,000.
        quantity: Order size in SHARES (not units/lots).
        reference_price_jpy: Base price (基準値段) driving the daily price
            limit -- normally the previous day's closing price, or the last
            special quote. It does NOT drive the tick size: unlike some Asian
            venues, TSE selects the tick from the ORDER price.
        tick_table: Which published tick table applies to this issue --
            'TOPIX500', 'ETF_SINGLE_UNIT' or 'OTHER'. This is a property of the
            ISSUE, announced per-issue by TSE, not something derivable from the
            price. Defaults to 'OTHER'; a TOPIX500 constituent left on the
            default will be validated against ticks that are too coarse.
        trading_unit: Shares per trading unit. 100 for domestic stocks; ETFs,
            ETNs, REITs and leveraged products use 1 or 10.
        daily_price_limit_override_jpy: Effective daily price limit in yen when
            TSE has broadened it for this issue. TSE broadens limits on the
            third business day after two consecutive qualifying sessions, and
            publishes the affected issues; the engine cannot infer this, so
            pass the published figure when it applies.
    """

    local_code: str
    side: str
    price_jpy: float
    quantity: int
    reference_price_jpy: float
    tick_table: str = TICK_TABLE_OTHER
    trading_unit: int = DEFAULT_TRADING_UNIT
    daily_price_limit_override_jpy: Optional[float] = None


@dataclass
class JpxOrderReport:
    """Structured outcome of a pre-trade TSE order audit."""

    local_code: str
    side: str
    price_jpy: Decimal
    tick_table: str
    applicable_tick_size_jpy: Decimal   # JPY 0.1 / 0.5 / 1 / 5 / 10 / 50 / ...
    quantity_shares: int
    trading_unit: int
    board_lots_count: int               # quantity / trading_unit, floored
    daily_price_limit_jpy: Decimal      # Absolute yen limit for the base price
    upper_limit_price_jpy: Decimal      # Base price + limit (inclusive bound)
    lower_limit_price_jpy: Decimal      # Base price - limit (inclusive bound)
    is_price_tick_valid: bool
    is_board_lot_valid: bool
    is_price_limit_valid: bool
    status: str                         # 'JPX_ORDER_VALIDATED', 'INVALID_TICK_SIZE',
                                        # 'INVALID_BOARD_LOT', 'PRICE_LIMIT_EXCEEDED'
    audit_notes: str


class JpxStockExchangeApiEngine:
    """Pre-trade validator for TSE cash-equity orders routed to arrowhead4.0.

    Enforces the three rules an arrowhead gateway checks on entry and that a
    client can check itself: securities code format, tick size alignment,
    trading-unit multiples, and the daily price limit band.

    It deliberately does NOT model: trading halts and suspensions, special
    quotes (特別気配) and their renewal price ranges, the pre-opening and
    closing auction phases, short-sale price restrictions, ToSTNeT off-auction
    trading, or any credit/position check. arrowhead remains authoritative and
    can reject an order this engine approves.
    """

    def __init__(self, tick_schedules: Optional[dict] = None) -> None:
        """
        Args:
            tick_schedules: Optional replacement tick tables, keyed by table
                name, for testing or for adopting a published schedule change
                (TSE moves to a liquidity-based, Spread-to-Tick-Ratio regime on
                1 March 2027) without editing this module.
        """
        self.tick_schedules = dict(tick_schedules) if tick_schedules else dict(TICK_SCHEDULES)

    def validate_tse_local_code(self, code: str) -> str:
        """Validate and normalise a TSE securities code.

        Accepts the all-numeric codes assigned before January 2024 ('7203') and
        the alphanumeric codes assigned from 1 January 2024 onward, which carry
        one of 19 uppercase letters in the 2nd and/or 4th position ('130A',
        '9A76', '9A7A'). Rejecting letters would block every issue listed since
        that date.

        Raises:
            ValueError: If the code is not a valid four-character SICC code.
        """
        if not isinstance(code, str):
            raise ValueError(f"Invalid TSE local code {code!r}: expected a string.")
        clean = code.strip().upper()
        if not SICC_CODE_PATTERN.match(clean):
            raise ValueError(
                f"Invalid TSE local code '{code}'. TSE securities codes are 4 characters: "
                f"digits in positions 1 and 3, and a digit or one of "
                f"'{SICC_CODE_LETTERS}' in positions 2 and 4 (e.g. '7203', '130A')."
            )
        return clean

    def get_tse_tick_size(self, price_jpy: float, tick_table: str = TICK_TABLE_OTHER) -> Decimal:
        """Return the TSE tick size (呼値の単位) for a price under a given table.

        Band bounds are INCLUSIVE: TSE publishes them as 「3,000円以下」, so a
        price of exactly JPY 3,000 takes the finer tick of the lower band.

        Args:
            price_jpy: The ORDER price in yen. TSE selects the tick from the
                order price itself, not from the previous close.
            tick_table: 'TOPIX500', 'ETF_SINGLE_UNIT' or 'OTHER'.

        Raises:
            ValueError: If the price is non-positive or non-finite, or the tick
                table name is unknown.
        """
        table_clean = str(tick_table).strip().upper()
        if table_clean not in self.tick_schedules:
            raise ValueError(
                f"Unknown tick table '{tick_table}'. Expected one of {VALID_TICK_TABLES}."
            )
        price = _to_decimal(price_jpy, "price_jpy")
        if price <= 0:
            raise ValueError(f"price_jpy must be strictly positive, got {price_jpy!r}.")

        for upper_bound, tick in self.tick_schedules[table_clean]:
            if upper_bound is None or price <= upper_bound:
                return tick
        # Unreachable: every schedule terminates in an open-ended (None) band.
        raise ValueError(f"No tick size band matched price JPY {price} in table '{table_clean}'.")

    def get_daily_price_limit(self, base_price_jpy: float) -> Decimal:
        """Return the daily price limit (制限値幅) in ABSOLUTE YEN for a base price.

        TSE sets the limit as a yen amount keyed to the base price (基準値段),
        never as a percentage. Band bounds are EXCLUSIVE (「100円未満」).

        Raises:
            ValueError: If the base price is non-positive or non-finite.
        """
        base_price = _to_decimal(base_price_jpy, "reference_price_jpy")
        if base_price <= 0:
            raise ValueError(
                f"reference_price_jpy (base price) must be strictly positive, "
                f"got {base_price_jpy!r}. A missing or zero previous close cannot "
                f"anchor a daily price limit band."
            )
        for upper_bound, limit in DAILY_PRICE_LIMIT_SCHEDULE:
            if upper_bound is None or base_price < upper_bound:
                return limit
        # Unreachable: the schedule terminates in an open-ended (None) band.
        raise ValueError(f"No daily price limit band matched base price JPY {base_price}.")

    def get_daily_price_limit_bounds(
        self,
        base_price_jpy: float,
        limit_override_jpy: Optional[float] = None,
    ) -> Tuple[Decimal, Decimal, Decimal]:
        """Return ``(limit, lower_bound, upper_bound)`` for a base price, in yen.

        Bounds are INCLUSIVE -- an order at exactly the upper limit is the
        "stop high" price and is tradeable. For base prices below JPY 30 the
        published table yields a negative theoretical lower bound; TSE does not
        publish a floor, so it is returned unclamped and the effective floor is
        whatever your gateway enforces.

        Args:
            base_price_jpy: Base price (基準値段).
            limit_override_jpy: Effective limit when TSE has broadened it for
                this issue. Must be positive if supplied.
        """
        base_price = _to_decimal(base_price_jpy, "reference_price_jpy")
        if limit_override_jpy is None:
            limit = self.get_daily_price_limit(base_price_jpy)
        else:
            limit = _to_decimal(limit_override_jpy, "daily_price_limit_override_jpy")
            if limit <= 0:
                raise ValueError(
                    f"daily_price_limit_override_jpy must be strictly positive, "
                    f"got {limit_override_jpy!r}."
                )
            # Validate the base price even when the limit is supplied.
            self.get_daily_price_limit(base_price_jpy)
        return limit, base_price - limit, base_price + limit

    def validate_and_route_order(self, payload: JpxOrderPayload) -> JpxOrderReport:
        """Validate an order against TSE tick, trading-unit and price-limit rules.

        Raises:
            ValueError: On malformed input -- bad securities code, unknown side
                or tick table, non-positive/non-finite price or base price,
                non-integer or non-positive trading unit. Malformed input is a
                caller bug and is raised rather than folded into a status, so
                it can never be mistaken for an exchange-rule rejection.
        """
        code_clean = self.validate_tse_local_code(payload.local_code)

        side_clean = str(payload.side).strip().upper()
        if side_clean not in VALID_SIDES:
            raise ValueError(f"Invalid side '{payload.side}'. Expected one of {VALID_SIDES}.")

        table_clean = str(payload.tick_table).strip().upper()
        tick_size = self.get_tse_tick_size(payload.price_jpy, table_clean)
        price = _to_decimal(payload.price_jpy, "price_jpy")

        if isinstance(payload.trading_unit, bool) or not isinstance(payload.trading_unit, int):
            raise ValueError(
                f"trading_unit must be an int, got {payload.trading_unit!r}."
            )
        if payload.trading_unit <= 0:
            raise ValueError(
                f"trading_unit must be strictly positive, got {payload.trading_unit!r}."
            )
        if isinstance(payload.quantity, bool) or not isinstance(payload.quantity, int):
            raise ValueError(
                f"quantity must be an int number of shares, got {payload.quantity!r}."
            )

        # 1. Tick alignment. Exact Decimal modulo -- no float tolerance, which
        #    matters because the finest TSE tick (JPY 0.1) has no exact binary
        #    representation.
        is_tick_valid = (price % tick_size) == 0

        # 2. Trading unit (売買単位). 100 shares for domestic stocks since
        #    1 October 2018; ETFs/ETNs/REITs use 1 or 10.
        is_lot_valid = payload.quantity > 0 and payload.quantity % payload.trading_unit == 0
        lots_count = payload.quantity // payload.trading_unit if payload.quantity > 0 else 0

        # 3. Daily price limit (制限値幅) -- absolute yen, inclusive bounds.
        base_price = _to_decimal(payload.reference_price_jpy, "reference_price_jpy")
        limit, lower_bound, upper_bound = self.get_daily_price_limit_bounds(
            payload.reference_price_jpy, payload.daily_price_limit_override_jpy
        )
        is_limit_valid = lower_bound <= price <= upper_bound

        if not is_tick_valid:
            status = "INVALID_TICK_SIZE"
            notes = (
                f"JPX REJECT [{code_clean}]: Price JPY {price:,} is not a multiple of the "
                f"TSE tick size (JPY {tick_size}) for the {table_clean} tick table."
            )
            logger.warning(notes)
        elif not is_lot_valid:
            status = "INVALID_BOARD_LOT"
            notes = (
                f"JPX REJECT [{code_clean}]: Quantity {payload.quantity} shares is not a "
                f"positive multiple of the trading unit ({payload.trading_unit} shares)."
            )
            logger.warning(notes)
        elif not is_limit_valid:
            status = "PRICE_LIMIT_EXCEEDED"
            notes = (
                f"JPX REJECT [{code_clean}]: Price JPY {price:,} is outside the daily price "
                f"limit band JPY {lower_bound:,} - JPY {upper_bound:,} "
                f"(base price JPY {base_price:,}, limit +/- JPY {limit:,})."
            )
            logger.warning(notes)
        else:
            status = "JPX_ORDER_VALIDATED"
            notes = (
                f"JPX ORDER VALIDATED [{code_clean} - TSE arrowhead4.0]: {side_clean} "
                f"{payload.quantity:,} shares ({lots_count:,} units) @ JPY {price:,} "
                f"(tick JPY {tick_size}, {table_clean} table; daily price limit band "
                f"JPY {lower_bound:,} - JPY {upper_bound:,})."
            )
            logger.info(notes)

        return JpxOrderReport(
            local_code=code_clean,
            side=side_clean,
            price_jpy=price,
            tick_table=table_clean,
            applicable_tick_size_jpy=tick_size,
            quantity_shares=payload.quantity,
            trading_unit=payload.trading_unit,
            board_lots_count=lots_count,
            daily_price_limit_jpy=limit,
            upper_limit_price_jpy=upper_bound,
            lower_limit_price_jpy=lower_bound,
            is_price_tick_valid=is_tick_valid,
            is_board_lot_valid=is_lot_valid,
            is_price_limit_valid=is_limit_valid,
            status=status,
            audit_notes=notes,
        )

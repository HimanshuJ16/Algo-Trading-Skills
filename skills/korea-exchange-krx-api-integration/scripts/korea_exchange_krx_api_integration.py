"""KRX (KOSPI / KOSDAQ, EXTURE 3.0) pre-trade order validation.

Encodes the Korea Exchange cash-equity trading rules in force since the
EXTURE 3.0 launch on 25 January 2023, verified 25 August 2026:

* Tick size (호가가격단위) -- the schedule KRX revised on **25 January 2023**,
  the first revision since 2010. Bands are 「이상 ~ 미만」, so the upper bound
  of each band is **exclusive**. KOSPI, KOSDAQ and KONEX now share one
  schedule; before 25 January 2023 KOSDAQ capped its tick at KRW 100.
  ETFs, ETNs and ELWs are exempt and tick at a flat KRW 5 at every price.
* Daily price limit (가격제한폭) -- KRX does **not** compare a percentage
  deviation. It computes the limit *amount* as
  ``base_price x 0.30`` truncated (절사) to the tick unit of the **base
  price's** band, then sets 상한가 = base + amount and 하한가 = base - amount.
  KRX's own worked example: base KRW 9,940 -> 9,940 x 0.3 = 2,982 -> truncated
  to the KRW 10 tick = **2,980** -> upper 12,920, lower 6,960.
* Short codes (단축코드) -- six characters. The first five are digits; the
  sixth may be a digit **or a letter**. Newer preferred lines already carry
  one (`00781K`, `03473K`, `18064K`, `02826K`), and from 1 January 2024 KRX
  assigns letters (I, O and U excluded) in that position for new stock codes.
  An ``isdigit()`` test rejects listed, actively traded issues.
* Trading unit -- 1 share for stocks, ETFs and ETNs, so there is no board-lot
  multiple to enforce; only a positive whole share count.

All price comparisons are done in ``Decimal``. KRX quotes whole KRW, but the
tick amounts reach KRW 1,000 and the price-limit amount is a truncated
product -- a binary-float tolerance test can accept a price KRX will reject.

This module is a client-side pre-trade filter. The KRX matching engine
remains authoritative and can reject an order this engine approves.
"""
import logging
import math
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

VALID_SIDES = ("BUY", "SELL")

# KRX short-code (단축코드) alphabet. KRX announced in May 2023 that codes
# issued from 1 January 2024 may mix letters into the short code, excluding
# "I", "O" and "U" to avoid confusion with the digits 1, 0 and the letter V.
# Codes issued earlier are NOT reissued, so both forms coexist indefinitely.
KRX_CODE_LETTERS = "ABCDEFGHJKLMNPQRSTVWXYZ"

# Stocks (주권): five digits then one digit-or-letter. The sixth character is
# the share-class digit -- "0" for common stock, "5"/"7"/"9" for preferred
# lines issued before 2013, and letters from "K" onward for those issued
# since. Examples: 005930 (Samsung Electronics common), 005935 (Samsung
# Electronics preferred), 03473K (SK preferred), 18064K (Hanjin KAL preferred).
KRX_STOCK_CODE_PATTERN = re.compile(
    r"^[0-9]{{5}}[0-9{letters}]$".format(letters=KRX_CODE_LETTERS)
)

# ETFs / ETNs. KRX's published wording places letters in the 3rd and the
# "5th-7th" positions, which cannot all refer to a six-character short code,
# so this is the deliberately permissive reading: digits in the first two
# positions (KRX reserves 0-4 there for stocks and 5-8 for ETNs on newly
# issued codes), digit-or-letter thereafter. Confirm a specific code against
# the KRX standard code system before relying on this pattern in production.
KRX_ETF_ETN_CODE_PATTERN = re.compile(
    r"^[0-9]{{2}}[0-9{letters}]{{4}}$".format(letters=KRX_CODE_LETTERS)
)

# Security classes. The class selects BOTH the code pattern and the tick
# schedule -- it is a property of the instrument, not something derivable
# from the price.
SECURITY_CLASS_STOCK = "STOCK"
SECURITY_CLASS_ETF_ETN = "ETF_ETN"
VALID_SECURITY_CLASSES = (SECURITY_CLASS_STOCK, SECURITY_CLASS_ETF_ETN)

# Tick schedule: (EXCLUSIVE upper bound of the price band, tick size).
# A None bound is the open-ended top band. KRX publishes these bands as
# 「1,000원 이상 2,000원 미만」 -- "1,000 or above, less than 2,000" -- so a
# price sitting exactly on a boundary takes the COARSER tick of the upper
# band. This is the opposite convention to the Tokyo Stock Exchange, whose
# bands are 「以下」 (inclusive).
#
# In force since 25 January 2023 for KOSPI, KOSDAQ and KONEX stocks. The
# revision narrowed three bands: 1,000-2,000 (KRW 5 -> 1), 10,000-20,000
# (KRW 50 -> 10) and 100,000-200,000 (KRW 500 -> 100), and lifted KOSDAQ's
# 200,000-500,000 band from KRW 100 to KRW 500 so both boards now match.
STOCK_TICK_SCHEDULE: Tuple[Tuple[Optional[int], Decimal], ...] = (
    (2_000, Decimal("1")),
    (5_000, Decimal("5")),
    (20_000, Decimal("10")),
    (50_000, Decimal("50")),
    (200_000, Decimal("100")),
    (500_000, Decimal("500")),
    (None, Decimal("1000")),
)

# ETFs, ETNs and ELWs were excluded from the 2023 revision and keep a flat
# KRW 5 tick at every price level.
ETF_ETN_TICK_SCHEDULE: Tuple[Tuple[Optional[int], Decimal], ...] = (
    (None, Decimal("5")),
)

TICK_SCHEDULES = {
    SECURITY_CLASS_STOCK: STOCK_TICK_SCHEDULE,
    SECURITY_CLASS_ETF_ETN: ETF_ETN_TICK_SCHEDULE,
}

CODE_PATTERNS = {
    SECURITY_CLASS_STOCK: KRX_STOCK_CODE_PATTERN,
    SECURITY_CLASS_ETF_ETN: KRX_ETF_ETN_CODE_PATTERN,
}

# KOSPI and KOSDAQ have used a +/-30% daily price limit since 15 June 2015.
# KONEX remains at +/-15%; pass it explicitly.
DEFAULT_DAILY_PRICE_LIMIT_PCT = Decimal("30")

# Stocks, ETFs and ETNs all trade in units of one share, so there is no
# board-lot multiple to enforce (ELWs trade in units of 10, but ELWs are out
# of scope here -- they carry no daily price limit).
KRX_TRADING_UNIT_SHARES = 1


def _to_decimal(value: object, field_name: str) -> Decimal:
    """Convert an incoming numeric price to an exact Decimal.

    Goes through ``str`` so that a float literal such as ``150200.0`` becomes
    ``Decimal('150200.0')`` rather than its binary expansion. Rejects NaN,
    +/-Inf and non-numeric input rather than letting them propagate into a
    comparison that would quietly return False -- a NaN price silently fails
    every ``<=`` test and would otherwise be reported as a rule breach rather
    than as the caller bug it is.
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
            # KRX quotes whole won, and 150000.0 -> Decimal('150000.0') would
            # otherwise carry a spurious decimal place into the audit notes.
            # Comparisons are unaffected either way.
            candidate = (
                Decimal(int(numeric)) if numeric.is_integer() else Decimal(str(numeric))
            )
        except InvalidOperation as exc:  # pragma: no cover - guarded by isfinite
            raise ValueError(f"{field_name} is not a valid decimal: {value!r}.") from exc
    if not candidate.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}.")
    return candidate


@dataclass
class KrxOrderPayload:
    """A single KRX cash-equity order presented to an EXTURE 3.0 gateway.

    Attributes:
        local_code: Six-character KRX short code (단축코드). Five digits then
            a digit or a letter: '005930' (Samsung Electronics), '000660'
            (SK Hynix), '035420' (NAVER), '03473K' (SK preferred).
        side: 'BUY' or 'SELL'.
        price_krw: Limit price in KRW. KRX quotes whole won.
        quantity: Order size in SHARES. KRX trades stocks, ETFs and ETNs in
            units of one share, so any positive whole number is a valid size.
        reference_price_krw: Base price (기준가격) anchoring the daily price
            limit -- normally the previous session's closing price, adjusted
            for corporate actions, or the auction-determined base price on a
            listing day. It does NOT select the tick size for the order: the
            tick comes from the ORDER price. It DOES select the tick used to
            truncate the limit amount.
        security_class: 'STOCK' or 'ETF_ETN'. Selects the tick schedule and
            the short-code pattern. ETFs and ETNs tick at a flat KRW 5, so
            leaving an ETF on the 'STOCK' default validates it against ticks
            that are far too coarse and rejects valid prices.
        price_limit_exempt: Set True for instruments KRX exempts from the
            daily price limit -- issues in liquidation trading (정리매매) and
            subscription warrants/rights (신주인수권증권·증서). The tick check
            still applies.
        daily_price_limit_pct_override: Effective limit percentage when it is
            not the market default -- KONEX trades at +/-15%. Must be
            positive if supplied.
    """

    local_code: str
    side: str
    price_krw: float
    quantity: int
    reference_price_krw: float
    security_class: str = SECURITY_CLASS_STOCK
    price_limit_exempt: bool = False
    daily_price_limit_pct_override: Optional[float] = None


@dataclass
class KrxOrderReport:
    """Structured outcome of a pre-trade KRX order audit."""

    local_code: str
    side: str
    price_krw: Decimal
    security_class: str
    applicable_tick_size_krw: Decimal   # KRW 1 / 5 / 10 / 50 / 100 / 500 / 1000
    quantity_shares: int
    # Limit AMOUNT (가격제한폭): base x pct, truncated to the base price's
    # tick. Zero when the instrument is exempt.
    daily_price_limit_amount_krw: Decimal
    upper_limit_price_krw: Optional[Decimal]   # 상한가; None when exempt
    lower_limit_price_krw: Optional[Decimal]   # 하한가; None when exempt
    is_price_tick_valid: bool
    is_price_limit_valid: bool
    status: str                         # 'KRX_ORDER_VALIDATED',
                                        # 'INVALID_TICK_SIZE',
                                        # 'PRICE_LIMIT_EXCEEDED'
    audit_notes: str


class KoreaExchangeKrxApiEngine:
    """Pre-trade validator for KRX cash-equity orders routed to EXTURE 3.0.

    Enforces the three rules a KRX gateway checks on entry and that a client
    can check for itself: short-code format, tick size alignment, and the
    daily price limit band.

    It deliberately does NOT model: trading halts and suspensions, the
    Volatility Interruption (변동성완화장치) static and dynamic triggers,
    market-wide circuit breakers and sidecars, the opening and closing call
    auctions, off-hours single-price sessions, short-sale price restrictions,
    the listing-day base price auction, or any credit, margin or position
    check. The KRX matching engine remains authoritative and can reject an
    order this engine approves.

    Malformed input (bad code, unknown side, non-positive price or base
    price, non-integer quantity) is raised as ``ValueError`` rather than
    folded into a status, so a caller bug can never be mistaken for an
    exchange-rule rejection.
    """

    def __init__(
        self,
        max_daily_price_limit_pct: float = 30.0,
        tick_schedules: Optional[dict] = None,
        allow_zero_pad: bool = False,
    ) -> None:
        """
        Args:
            max_daily_price_limit_pct: Default daily price limit percentage.
                30 for KOSPI and KOSDAQ since 15 June 2015; 15 for KONEX.
            tick_schedules: Optional replacement tick schedules keyed by
                security class, for testing or for adopting a published
                schedule change without editing this module.
            allow_zero_pad: When True, a short code of fewer than six
                characters is left-padded with zeros. **Off by default**: a
                mistyped '5' silently becomes '000005', a different listed
                instrument, and the order routes to the wrong issue. Enable
                only when the upstream source is known to strip leading
                zeros (spreadsheets and some CSV exports do).
        """
        self.max_daily_price_limit_pct = _to_decimal(
            max_daily_price_limit_pct, "max_daily_price_limit_pct"
        )
        if self.max_daily_price_limit_pct <= 0:
            raise ValueError(
                f"max_daily_price_limit_pct must be strictly positive, "
                f"got {max_daily_price_limit_pct!r}."
            )
        self.tick_schedules = dict(tick_schedules) if tick_schedules else dict(TICK_SCHEDULES)
        self.allow_zero_pad = allow_zero_pad

    @staticmethod
    def _normalise_security_class(security_class: str) -> str:
        clean = str(security_class).strip().upper()
        if clean not in VALID_SECURITY_CLASSES:
            raise ValueError(
                f"Unknown security_class '{security_class}'. "
                f"Expected one of {VALID_SECURITY_CLASSES}."
            )
        return clean

    def validate_krx_local_code(
        self, code: str, security_class: str = SECURITY_CLASS_STOCK
    ) -> str:
        """Validate and normalise a KRX short code (단축코드).

        Accepts the all-numeric codes that dominate the board ('005930') and
        the codes whose sixth character is a letter ('03473K', '18064K').
        Rejecting letters would block listed, actively traded preferred lines
        today and every stock code KRX issues from 1 January 2024 onward.

        Zero-padding of short input is applied only when the engine was
        constructed with ``allow_zero_pad=True`` -- see ``__init__``.

        Raises:
            ValueError: If the code is not a valid six-character KRX short
                code for the given security class.
        """
        cls_clean = self._normalise_security_class(security_class)
        if not isinstance(code, str):
            raise ValueError(f"Invalid KRX local code {code!r}: expected a string.")
        clean = code.strip().upper()
        if self.allow_zero_pad and 0 < len(clean) < 6 and clean.isdigit():
            clean = clean.zfill(6)
        if not CODE_PATTERNS[cls_clean].match(clean):
            raise ValueError(
                f"Invalid KRX local code '{code}' for security class {cls_clean}. "
                f"KRX short codes are 6 characters (e.g. '005930', '03473K'); "
                f"letters are drawn from '{KRX_CODE_LETTERS}' (I, O and U are "
                f"excluded). Leading zeros are significant and are not restored "
                f"unless the engine was built with allow_zero_pad=True."
            )
        return clean

    def get_krx_tick_size_krw(
        self, price_krw: float, security_class: str = SECURITY_CLASS_STOCK
    ) -> Decimal:
        """Return the KRX tick size (호가가격단위) for a price.

        Band bounds are EXCLUSIVE: KRX publishes them as 「2,000원 미만」, so a
        price of exactly KRW 2,000 takes the coarser KRW 5 tick of the band
        above, not the KRW 1 tick below it.

        Args:
            price_krw: The price whose band is wanted. Pass the ORDER price to
                validate tick alignment; pass the BASE price to obtain the
                tick that truncates the daily price limit amount.
            security_class: 'STOCK' or 'ETF_ETN'.

        Raises:
            ValueError: If the price is non-positive or non-finite, or the
                security class is unknown.
        """
        cls_clean = self._normalise_security_class(security_class)
        price = _to_decimal(price_krw, "price_krw")
        if price <= 0:
            raise ValueError(f"price_krw must be strictly positive, got {price_krw!r}.")

        for upper_bound, tick in self.tick_schedules[cls_clean]:
            if upper_bound is None or price < upper_bound:
                return tick
        # Unreachable: every schedule terminates in an open-ended (None) band.
        raise ValueError(
            f"No tick size band matched price KRW {price} for class '{cls_clean}'."
        )

    def get_daily_price_limit_bounds(
        self,
        base_price_krw: float,
        security_class: str = SECURITY_CLASS_STOCK,
        limit_pct_override: Optional[float] = None,
    ) -> Tuple[Decimal, Decimal, Decimal]:
        """Return ``(limit_amount, lower_bound, upper_bound)`` in KRW.

        Implements the published KRX rule, which is **not** a percentage
        comparison against the order price:

            가격제한폭 = truncate(base_price x pct/100, tick of the BASE price)
            상한가     = base_price + 가격제한폭
            하한가     = base_price - 가격제한폭

        KRX's worked example: a base price of KRW 9,940 sits in the KRW 10
        tick band; 9,940 x 0.3 = 2,982, truncated to 2,980; the band is
        KRW 6,960 - KRW 12,920. A naive ``abs(P - base)/base <= 0.30`` test
        accepts KRW 12,922, which the exchange rejects.

        Both bounds are INCLUSIVE -- an order at exactly 상한가 is the
        limit-up price and is tradeable.

        Note that the truncation is applied to the limit AMOUNT using the
        base price's tick, so the band is symmetric about the base price and
        both bounds stay within the nominal percentage. The bounds themselves
        are not guaranteed to be tick-aligned when the band spans a tick
        boundary; validate tick alignment separately, as
        ``validate_and_route_order`` does.

        Raises:
            ValueError: If the base price is non-positive or non-finite, or
                the override percentage is not strictly positive.
        """
        cls_clean = self._normalise_security_class(security_class)
        base_price = _to_decimal(base_price_krw, "reference_price_krw")
        if base_price <= 0:
            raise ValueError(
                f"reference_price_krw (base price) must be strictly positive, got "
                f"{base_price_krw!r}. A missing or zero base price cannot anchor a "
                f"daily price limit band."
            )

        if limit_pct_override is None:
            limit_pct = self.max_daily_price_limit_pct
        else:
            limit_pct = _to_decimal(
                limit_pct_override, "daily_price_limit_pct_override"
            )
            if limit_pct <= 0:
                raise ValueError(
                    f"daily_price_limit_pct_override must be strictly positive, "
                    f"got {limit_pct_override!r}."
                )

        base_tick = self.get_krx_tick_size_krw(base_price, cls_clean)
        raw_amount = base_price * limit_pct / Decimal("100")
        # 절사 -- discard the sub-tick remainder. Both operands are positive,
        # so Decimal floor division truncates toward zero as KRX specifies.
        limit_amount = (raw_amount // base_tick) * base_tick
        return limit_amount, base_price - limit_amount, base_price + limit_amount

    def validate_and_route_order(self, payload: KrxOrderPayload) -> KrxOrderReport:
        """Validate an order against KRX short-code, tick and price-limit rules.

        Raises:
            ValueError: On malformed input -- bad short code, unknown side or
                security class, non-positive or non-finite price or base
                price, non-integer or non-positive quantity.
        """
        cls_clean = self._normalise_security_class(payload.security_class)
        code_clean = self.validate_krx_local_code(payload.local_code, cls_clean)

        side_clean = str(payload.side).strip().upper()
        if side_clean not in VALID_SIDES:
            raise ValueError(
                f"Invalid side '{payload.side}'. Expected one of {VALID_SIDES}."
            )

        if isinstance(payload.quantity, bool) or not isinstance(payload.quantity, int):
            raise ValueError(
                f"quantity must be an int number of shares, got {payload.quantity!r}."
            )
        if payload.quantity <= 0:
            raise ValueError(
                f"quantity must be strictly positive, got {payload.quantity!r}."
            )

        price = _to_decimal(payload.price_krw, "price_krw")
        tick_size = self.get_krx_tick_size_krw(price, cls_clean)

        # 1. Tick alignment (호가가격단위). Exact Decimal modulo -- no float
        #    tolerance. The tick comes from the ORDER price's band.
        is_tick_valid = (price % tick_size) == 0

        # 2. Daily price limit (가격제한폭). Inclusive bounds around the base
        #    price. Exempt instruments (정리매매, 신주인수권증권·증서) skip it.
        #    Require a real bool: a truthy string such as "no" would otherwise
        #    silently disable the band check on a non-exempt instrument.
        if not isinstance(payload.price_limit_exempt, bool):
            raise ValueError(
                f"price_limit_exempt must be a bool, got "
                f"{payload.price_limit_exempt!r}."
            )
        base_price: Optional[Decimal] = None
        if payload.price_limit_exempt:
            limit_amount = Decimal("0")
            lower_bound: Optional[Decimal] = None
            upper_bound: Optional[Decimal] = None
            is_limit_valid = True
        else:
            base_price = _to_decimal(payload.reference_price_krw, "reference_price_krw")
            limit_amount, lower_bound, upper_bound = self.get_daily_price_limit_bounds(
                payload.reference_price_krw,
                cls_clean,
                payload.daily_price_limit_pct_override,
            )
            is_limit_valid = lower_bound <= price <= upper_bound

        if not is_tick_valid:
            status = "INVALID_TICK_SIZE"
            notes = (
                f"KRX REJECT [{code_clean}]: Price KRW {price:,} is not a multiple of "
                f"the KRX tick size (KRW {tick_size:,}) for the {cls_clean} schedule."
            )
            logger.warning(notes)
        elif not is_limit_valid:
            status = "PRICE_LIMIT_EXCEEDED"
            notes = (
                f"KRX REJECT [{code_clean}]: Price KRW {price:,} is outside the daily "
                f"price limit band KRW {lower_bound:,} - KRW {upper_bound:,} "
                f"(base price KRW {base_price:,}, limit +/- KRW {limit_amount:,})."
            )
            logger.warning(notes)
        else:
            band = (
                "exempt from the daily price limit"
                if payload.price_limit_exempt
                else f"band KRW {lower_bound:,} - KRW {upper_bound:,}"
            )
            status = "KRX_ORDER_VALIDATED"
            notes = (
                f"KRX ORDER VALIDATED [{code_clean} - EXTURE 3.0]: {side_clean} "
                f"{payload.quantity:,} shares @ KRW {price:,} "
                f"(tick KRW {tick_size:,}, {cls_clean} schedule; {band})."
            )
            logger.info(notes)

        return KrxOrderReport(
            local_code=code_clean,
            side=side_clean,
            price_krw=price,
            security_class=cls_clean,
            applicable_tick_size_krw=tick_size,
            quantity_shares=payload.quantity,
            daily_price_limit_amount_krw=limit_amount,
            upper_limit_price_krw=upper_bound,
            lower_limit_price_krw=lower_bound,
            is_price_tick_valid=is_tick_valid,
            is_price_limit_valid=is_limit_valid,
            status=status,
            audit_notes=notes,
        )

"""Client-side pre-dispatch validation for London Metal Exchange (LME) orders.

The LME is not a monthly-expiry futures exchange. Three things about it break
code written against CME/ICE assumptions, and this module models all three:

  * **Lot size is per metal, in metric tonnes.** Copper, Aluminium, Lead and
    Zinc are 25 MT; Aluminium Alloy and NASAAC 20 MT; Nickel 6 MT; Tin 5 MT.
    "10 lots" is 250 MT of copper but 50 MT of tin.
  * **Tick size is per metal, and is not universally $0.50.** Nickel and Tin
    outrights trade in **$5.00/MT** increments on LMEselect and in the Ring.
    Applying $0.50 to Nickel accepts nine prices in ten that LMEselect rejects.
  * **Daily Price Limits are an order-entry control.** The LME accepts no bid
    above, and no offer below, the Daily Price Limit band around the previous
    business day's Closing Price for the 3-month contract. Unlike ICE's
    directional Reasonability Limit, the LME band applies to **both sides**.

Scope: nothing here opens a socket, logs on to LMEselect, or sends an order.
`PRE_TRADE_CHECKS_PASSED` means "passed the checks modelled here", never "the
LME has the order". Price Bands (dynamic and static), Exchange- and Member-set
maximum order size limits, and the order throttle are additional LMEselect
pre-trade controls that this module does not model.

This module also does **not** ship an LME trading calendar. Prompt dates are
checked structurally and against each contract's listed tenor; confirming that a
specific date is a tradeable prompt requires the LME calendar and the
substitute-prompt-date notices. Pass `valid_prompt_dates` to make that check
authoritative.

Primary sources (retrieved 2026-08-25) are listed in `references/standards.md`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import AbstractSet, Dict, List, Mapping, Optional, Tuple, Union

logger = logging.getLogger(__name__)

Number = Union[str, int, float, Decimal]

# Order-level outcomes.
STATUS_PASSED = "PRE_TRADE_CHECKS_PASSED"
STATUS_INVALID_METAL_CODE = "INVALID_METAL_CODE"
STATUS_INVALID_TICK_SIZE = "INVALID_TICK_SIZE"
STATUS_INVALID_PROMPT_DATE = "INVALID_PROMPT_DATE"
STATUS_DAILY_PRICE_LIMIT_BREACH = "DAILY_PRICE_LIMIT_BREACH"
STATUS_NO_DPL_REFERENCE_PRICE = "NO_DPL_REFERENCE_PRICE"

# Rolling prompt designators. Which calendar date each resolves to depends on
# the LME business-day calendar, so this module asserts nothing about that.
PROMPT_CASH = "CASH"
PROMPT_TOM = "TOM"
PROMPT_3M = "3M"
PROMPT_KEYWORDS = frozenset({PROMPT_CASH, PROMPT_TOM, PROMPT_3M})

# LME lists daily prompts out to 3 months, weekly (Wednesdays) from 3 to 6
# months, and monthly (third Wednesday) beyond that.
PROMPT_CLASS_DAILY = "DAILY"
PROMPT_CLASS_WEEKLY = "WEEKLY"
PROMPT_CLASS_MONTHLY = "MONTHLY"
PROMPT_CLASS_ROLLING = "ROLLING_KEYWORD"
PROMPT_CLASS_EXPIRED = "EXPIRED"
PROMPT_CLASS_OUT_OF_TENOR = "OUT_OF_TENOR"
PROMPT_CLASS_UNKNOWN = "UNKNOWN"

_VALID_SIDES = frozenset({"BUY", "SELL"})
_CENT = Decimal("0.01")


def to_decimal(value: Number, field_name: str) -> Decimal:
    """Convert to Decimal without inheriting binary float error.

    Floats are routed through ``str`` so 9250.50 means 9250.50 and not
    9250.4999999999995. Pass prices as strings when exactness matters.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a price.
        raise TypeError(f"{field_name} must be numeric, got bool")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (int, str)):
        try:
            result = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field_name} is not a valid decimal: {value!r}") from exc
    elif isinstance(value, float):
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field_name} is not a valid decimal: {value!r}") from exc
    else:
        raise TypeError(
            f"{field_name} must be str, int, float or Decimal, "
            f"got {type(value).__name__}"
        )

    if not result.is_finite():
        raise ValueError(f"{field_name} must be finite, got {value!r}")
    return result


def is_third_wednesday(day: date) -> bool:
    """True if ``day`` is the third Wednesday of its month.

    LME monthly prompts fall on the third Wednesday. The LME publishes
    substitute dates when that Wednesday is a non-business day, so False here is
    a prompt to check the calendar, not proof the date is untradeable.
    """
    return day.weekday() == 2 and 15 <= day.day <= 21


def months_forward(trade_day: date, prompt_day: date) -> int:
    """Whole calendar months from ``trade_day`` to ``prompt_day``.

    Used only to place a prompt inside the daily / weekly / monthly bands and
    against the contract's furthest listed month. It is a calendar-month delta,
    not a business-day count.
    """
    delta = ((prompt_day.year - trade_day.year) * 12
             + (prompt_day.month - trade_day.month))
    if prompt_day.day < trade_day.day:
        delta -= 1
    return delta


@dataclass(frozen=True)
class LmeContractSpec:
    """One LME physically-settled base metal contract.

    ``outright_tick_usd`` is the LMEselect and Ring minimum price fluctuation
    for an **outright** (single prompt date). Carries trade on a smaller tick,
    and since January 2026 large-tick electronic calendar spreads trade on a
    third tick again; inter-office is $0.01. Only the outright tick is enforced
    here.

    ``daily_price_limit_pct`` is the LME Daily Price Limit for outrights,
    applied to the previous business day's Closing Price for the 3-month
    contract. The LME revises both tick sizes and DPLs by notice, so each spec
    carries its source and retrieval date rather than posing as a constant.
    """

    metal_code: str                     # LME contract code, e.g. 'CA', 'AH', 'NI'
    name: str
    lot_size_mt: Decimal                # metric tonnes per lot
    outright_tick_usd: Decimal          # USD/MT, LMEselect + Ring outright
    carry_tick_usd: Decimal             # USD/MT, LMEselect + Ring carry
    daily_price_limit_pct: Decimal      # e.g. Decimal('0.12') for 12%
    max_monthly_tenor_months: int       # furthest listed monthly prompt
    specs_source: str
    specs_as_of: str

    def __post_init__(self) -> None:
        for name in ("lot_size_mt", "outright_tick_usd", "carry_tick_usd",
                     "daily_price_limit_pct"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{self.metal_code}: {name} must be positive")
        if self.max_monthly_tenor_months <= 0:
            raise ValueError(
                f"{self.metal_code}: max_monthly_tenor_months must be positive")


# LME contract specifications retrieved from lme.com on 2026-08-25. Daily Price
# Limits are those restated in LME Notice 26/138 (effective 8 June 2026). Both
# are revised by LME notice — re-derive them before relying on them.
_SPEC_SRC = "lme.com contract specifications; DPL per LME Notice 26/138"
_SPEC_ASOF = "2026-08-25"

LME_SPECS: Dict[str, LmeContractSpec] = {
    spec.metal_code: spec
    for spec in (
        LmeContractSpec("AH", "Primary Aluminium", Decimal("25"), Decimal("0.50"),
                        Decimal("0.01"), Decimal("0.12"), 123, _SPEC_SRC, _SPEC_ASOF),
        LmeContractSpec("AA", "Aluminium Alloy", Decimal("20"), Decimal("0.50"),
                        Decimal("0.01"), Decimal("0.15"), 27, _SPEC_SRC, _SPEC_ASOF),
        LmeContractSpec("NA", "North American Special Aluminium Alloy (NASAAC)",
                        Decimal("20"), Decimal("0.50"), Decimal("0.01"),
                        Decimal("0.15"), 27, _SPEC_SRC, _SPEC_ASOF),
        LmeContractSpec("CA", "Copper Grade A", Decimal("25"), Decimal("0.50"),
                        Decimal("0.01"), Decimal("0.12"), 123, _SPEC_SRC, _SPEC_ASOF),
        LmeContractSpec("PB", "Standard Lead", Decimal("25"), Decimal("0.50"),
                        Decimal("0.01"), Decimal("0.12"), 63, _SPEC_SRC, _SPEC_ASOF),
        # Nickel and Tin outrights are $5.00/MT, not $0.50/MT.
        LmeContractSpec("NI", "Primary Nickel", Decimal("6"), Decimal("5.00"),
                        Decimal("0.01"), Decimal("0.15"), 63, _SPEC_SRC, _SPEC_ASOF),
        LmeContractSpec("SN", "Tin", Decimal("5"), Decimal("5.00"),
                        Decimal("0.01"), Decimal("0.15"), 15, _SPEC_SRC, _SPEC_ASOF),
        LmeContractSpec("ZS", "Special High Grade Zinc", Decimal("25"), Decimal("0.50"),
                        Decimal("0.01"), Decimal("0.12"), 63, _SPEC_SRC, _SPEC_ASOF),
    )
}


@dataclass(frozen=True)
class LmeOrderPayload:
    """One LME outright order, before it is sent anywhere.

    ``prompt_date`` is either a rolling keyword (``'CASH'``, ``'TOM'``,
    ``'3M'``) or an explicit ``datetime.date`` / ISO ``YYYY-MM-DD`` string.
    ``lots`` is a whole number of LME lots — never tonnes.

    ``previous_close_3m_usd`` is the previous business day's Closing Price for
    the 3-month contract, the reference the Daily Price Limit is measured from.
    It is not the mid, not the top of book, and not the LME Official Price.
    """

    metal_code: str
    prompt_date: Union[str, date]
    side: str                               # 'BUY' or 'SELL'
    price_usd_per_mt: Number
    lots: int
    previous_close_3m_usd: Optional[Number] = None
    trade_date: Optional[date] = None       # defaults to today


@dataclass(frozen=True)
class LmeOrderReport:
    """Outcome of the checks modelled here.

    ``ready_to_send`` is never a claim that the LME accepted, or would accept,
    the order — only that it passed every check this module performs.
    """

    metal_code: str
    metal_name: str
    prompt_date: str
    prompt_class: str
    side: str
    price_usd_per_mt: Decimal
    lots: int
    lot_size_mt: Decimal
    total_tonnage_mt: Decimal
    total_notional_usd: Decimal
    tick_size_usd: Decimal
    is_price_tick_valid: bool
    daily_price_limit_pct: Optional[Decimal]
    dpl_upper_usd: Optional[Decimal]
    dpl_lower_usd: Optional[Decimal]
    status: str
    ready_to_send: bool
    prompt_date_confirmed: bool
    audit_notes: str
    warnings: Tuple[str, ...] = field(default_factory=tuple)


class LmeExchangeApiEngine:
    """Pre-dispatch validation for LME outright orders.

    The catalog is injectable so a caller can supply refreshed specifications
    without editing this module. It is copied on construction, so mutating the
    module-level ``LME_SPECS`` afterwards cannot silently change the behaviour
    of an engine already in use.
    """

    def __init__(self, specs: Optional[Mapping[str, LmeContractSpec]] = None) -> None:
        source = specs if specs is not None else LME_SPECS
        self._specs: Dict[str, LmeContractSpec] = dict(source)
        if not self._specs:
            raise ValueError("specs catalog must not be empty")

    @property
    def specs(self) -> Mapping[str, LmeContractSpec]:
        """A copy of this engine's catalog."""
        return dict(self._specs)

    def validate_and_route_order(
        self,
        payload: LmeOrderPayload,
        valid_prompt_dates: Optional[AbstractSet[date]] = None,
    ) -> LmeOrderReport:
        """Run the LME pre-dispatch checks over one outright order.

        Structurally invalid payloads — a non-positive or fractional lot count,
        an unknown side, a non-positive or non-finite price, an unparseable
        prompt date — raise ``ValueError``/``TypeError`` rather than returning a
        report. A report is returned only for outcomes that are genuine
        exchange-rule verdicts.

        ``valid_prompt_dates``, when supplied, is authoritative: an explicit
        prompt date outside the set is rejected. Without it, prompt dates are
        checked structurally and against the contract's listed tenor only, and
        ``prompt_date_confirmed`` is False.
        """
        code = _require_text(payload.metal_code, "metal_code").upper()
        side = _require_text(payload.side, "side").upper()
        if side not in _VALID_SIDES:
            raise ValueError(
                f"side must be one of {sorted(_VALID_SIDES)}, got {payload.side!r}")

        lots = payload.lots
        if isinstance(lots, bool) or not isinstance(lots, int):
            raise TypeError(
                f"lots must be a whole int number of LME lots, "
                f"got {type(lots).__name__}")
        if lots <= 0:
            raise ValueError(f"lots must be positive, got {lots}")

        price = to_decimal(payload.price_usd_per_mt, "price_usd_per_mt")
        if price <= 0:
            raise ValueError(f"price_usd_per_mt must be positive, got {price}")

        spec = self._specs.get(code)
        if spec is None:
            notes = (
                f"LME REJECT: unknown metal code {payload.metal_code!r}. "
                f"Known codes: {sorted(self._specs)}."
            )
            logger.error(notes)
            return LmeOrderReport(
                metal_code=code, metal_name="UNKNOWN",
                prompt_date=str(payload.prompt_date),
                prompt_class=PROMPT_CLASS_UNKNOWN, side=side,
                price_usd_per_mt=price, lots=lots,
                lot_size_mt=Decimal("0"), total_tonnage_mt=Decimal("0"),
                total_notional_usd=Decimal("0"), tick_size_usd=Decimal("0"),
                is_price_tick_valid=False, daily_price_limit_pct=None,
                dpl_upper_usd=None, dpl_lower_usd=None,
                status=STATUS_INVALID_METAL_CODE, ready_to_send=False,
                prompt_date_confirmed=False, audit_notes=notes,
            )

        warning_list: List[str] = []

        # 1. Prompt date. Raises on an unparseable date; returns a verdict for a
        #    date the contract does not list.
        prompt_label, prompt_class, prompt_ok, prompt_confirmed, prompt_note = (
            _classify_prompt(payload.prompt_date, spec, payload.trade_date,
                             valid_prompt_dates)
        )
        if prompt_note:
            warning_list.append(prompt_note)

        # 2. Tonnage and notional in exact decimal money arithmetic.
        tonnage = Decimal(lots) * spec.lot_size_mt
        notional = (tonnage * price).quantize(_CENT, rounding=ROUND_HALF_UP)

        # 3. Outright tick alignment. Decimal, not float: binary float misreads
        #    a price as off-tick for almost every cent-denominated tick — in
        #    float, 0.03 % 0.01 is not zero — and the catalog is injectable, so
        #    a refreshed spec may well carry one. Positivity was checked above:
        #    the remainder of a negative price against a positive tick is zero,
        #    so the two checks are not interchangeable.
        tick = spec.outright_tick_usd
        tick_ok = (price % tick) == 0

        # 4. Daily Price Limit. Symmetric on price and applied to both sides —
        #    the LME accepts neither a bid above the upper limit nor an offer
        #    below the lower one. This is not ICE's directional Reasonability
        #    Limit; do not port that logic here.
        dpl_upper: Optional[Decimal] = None
        dpl_lower: Optional[Decimal] = None
        dpl_ok: Optional[bool] = None
        if payload.previous_close_3m_usd is not None:
            reference = to_decimal(payload.previous_close_3m_usd,
                                   "previous_close_3m_usd")
            if reference <= 0:
                raise ValueError(
                    f"previous_close_3m_usd must be positive, got {reference}")
            band = reference * spec.daily_price_limit_pct
            dpl_upper = (reference + band).quantize(_CENT, rounding=ROUND_HALF_UP)
            dpl_lower = (reference - band).quantize(_CENT, rounding=ROUND_HALF_UP)
            dpl_ok = dpl_lower <= price <= dpl_upper

        # 5. Verdict, most-blocking first.
        limit_pct = (spec.daily_price_limit_pct * 100).normalize()
        if not prompt_ok:
            status = STATUS_INVALID_PROMPT_DATE
            notes = f"LME REJECT [{code}]: {prompt_note}"
        elif not tick_ok:
            status = STATUS_INVALID_TICK_SIZE
            notes = (
                f"LME REJECT [{code}]: price ${price:,} /MT is not a multiple of "
                f"the ${tick} /MT outright tick for {spec.name}."
            )
        elif dpl_ok is None:
            status = STATUS_NO_DPL_REFERENCE_PRICE
            notes = (
                f"LME INCOMPLETE [{code}]: no previous_close_3m_usd supplied, so "
                f"the {limit_pct}% Daily Price Limit could not be checked. The LME "
                f"rejects orders outside that band; supply the reference price."
            )
        elif not dpl_ok:
            status = STATUS_DAILY_PRICE_LIMIT_BREACH
            notes = (
                f"LME REJECT [{code}]: {side} at ${price:,} /MT is outside the "
                f"{limit_pct}% Daily Price Limit band "
                f"${dpl_lower:,}-${dpl_upper:,} /MT."
            )
        else:
            status = STATUS_PASSED
            notes = (
                f"LME PRE-TRADE CHECKS PASSED [{code} - {spec.name}]: {side} "
                f"{lots:,} lots ({tonnage:,} MT) @ ${price:,} /MT "
                f"[prompt {prompt_label}]. Notional ${notional:,} USD."
            )

        ready = status == STATUS_PASSED
        if ready:
            logger.info(notes)
        else:
            logger.warning(notes)

        if ready and not prompt_confirmed:
            warning_list.append(
                "Prompt date not confirmed against an LME calendar. Pass "
                "valid_prompt_dates to make this check authoritative."
            )

        return LmeOrderReport(
            metal_code=code,
            metal_name=spec.name,
            prompt_date=prompt_label,
            prompt_class=prompt_class,
            side=side,
            price_usd_per_mt=price,
            lots=lots,
            lot_size_mt=spec.lot_size_mt,
            total_tonnage_mt=tonnage,
            total_notional_usd=notional,
            tick_size_usd=tick,
            is_price_tick_valid=tick_ok,
            daily_price_limit_pct=spec.daily_price_limit_pct,
            dpl_upper_usd=dpl_upper,
            dpl_lower_usd=dpl_lower,
            status=status,
            ready_to_send=ready,
            prompt_date_confirmed=prompt_confirmed,
            audit_notes=notes,
            warnings=tuple(warning_list),
        )


def _require_text(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be str, got {type(value).__name__}")
    text = value.strip()
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    return text


def _classify_prompt(
    prompt: Union[str, date],
    spec: LmeContractSpec,
    trade_date: Optional[date],
    valid_prompt_dates: Optional[AbstractSet[date]],
) -> Tuple[str, str, bool, bool, str]:
    """Classify and check one prompt date.

    Returns ``(label, prompt_class, ok, confirmed, note)``. ``ok`` False is a
    rejection; ``confirmed`` False means the date passed the structural checks
    but was not verified against an LME calendar.
    """
    if isinstance(prompt, datetime):
        # datetime subclasses date, so it passes an isinstance(date) check and
        # then raises on comparison against a plain date. A prompt is a day.
        prompt_day = prompt.date()
    elif isinstance(prompt, date):
        prompt_day = prompt
    else:
        raw = _require_text(prompt, "prompt_date").upper()
        if raw in PROMPT_KEYWORDS:
            return (raw, PROMPT_CLASS_ROLLING, True, True, "")
        try:
            prompt_day = date.fromisoformat(raw.lower())
        except ValueError as exc:
            raise ValueError(
                f"prompt_date must be a datetime.date, an ISO YYYY-MM-DD string, "
                f"or one of {sorted(PROMPT_KEYWORDS)}; got {prompt!r}"
            ) from exc

    label = prompt_day.isoformat()
    if trade_date is None:
        today = date.today()
    elif isinstance(trade_date, datetime):
        today = trade_date.date()
    elif isinstance(trade_date, date):
        today = trade_date
    else:
        raise TypeError(
            f"trade_date must be a datetime.date, got {type(trade_date).__name__}")

    if prompt_day <= today:
        return (label, PROMPT_CLASS_EXPIRED, False, False,
                f"prompt date {label} is not after the trade date "
                f"{today.isoformat()}")

    tenor = months_forward(today, prompt_day)
    if tenor > spec.max_monthly_tenor_months:
        return (label, PROMPT_CLASS_OUT_OF_TENOR, False, False,
                f"{spec.metal_code} lists monthly prompts out to "
                f"{spec.max_monthly_tenor_months} months; {label} is ~{tenor} "
                f"months out")

    if tenor < 3:
        prompt_class = PROMPT_CLASS_DAILY
        structural_note = ""
    elif tenor < 6:
        prompt_class = PROMPT_CLASS_WEEKLY
        structural_note = (
            "" if prompt_day.weekday() == 2 else
            f"{label} is a {prompt_day.strftime('%A')}; LME weekly prompts "
            f"(3-6 months) normally fall on a Wednesday. Confirm it is a "
            f"published substitute date."
        )
    else:
        prompt_class = PROMPT_CLASS_MONTHLY
        structural_note = (
            "" if is_third_wednesday(prompt_day) else
            f"{label} is not a third Wednesday; LME monthly prompts (beyond 6 "
            f"months) normally are. Confirm it is a published substitute date."
        )

    if valid_prompt_dates is not None:
        if prompt_day not in valid_prompt_dates:
            return (label, prompt_class, False, True,
                    f"prompt date {label} is not in the supplied LME "
                    f"prompt-date calendar")
        return (label, prompt_class, True, True, "")

    return (label, prompt_class, True, False, structural_note)

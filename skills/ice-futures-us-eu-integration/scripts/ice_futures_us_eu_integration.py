"""Client-side pre-dispatch validation for ICE Futures Europe / ICE Futures U.S. orders.

This module models the two ICE price controls that decide whether a limit order
is *accepted* and whether a resulting trade can later be *broken*:

  * **Reasonability Limits (RL)** - hard limits above and below an Exchange-set
    **anchor price**, outside which the Electronic Trading System will not accept
    a limit order. The check is directional: a buy above the upper limit and a
    sell below the lower limit are refused; a passive buy far below the market is
    not.
  * **No Cancellation Range (NCR)** - a *post-trade* error-trade parameter. A
    trade inside the NCR stands. NCR never rejects an order.

Scope: nothing here opens a socket, logs on to a gateway, or sends an order.
`PRE_TRADE_CHECKS_PASSED` means "passed the checks modelled here", never "ICE has
the order". Interval and Tiered Price Limits, market and stop order protection
limits, and minimum/maximum order value limits are additional ICE controls that
this module does not model.

Primary sources (retrieved 2026-08-25) are listed in `references/standards.md`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, DecimalException, InvalidOperation
from typing import Dict, Mapping, Optional, Tuple, Union

logger = logging.getLogger(__name__)

Number = Union[str, int, float, Decimal]

# Standard futures month codes. ICE lists these for the delivery month; which
# subset is actually listed differs per contract (see `listed_month_codes`).
ICE_MONTH_CODES: Dict[int, str] = {
    1: "F", 2: "G", 3: "H", 4: "J", 5: "K", 6: "M",
    7: "N", 8: "Q", 9: "U", 10: "V", 11: "X", 12: "Z",
}
_MONTH_CODE_TO_NUM: Dict[str, int] = {code: num for num, code in ICE_MONTH_CODES.items()}

ALL_MONTH_CODES = frozenset(ICE_MONTH_CODES.values())
QUARTERLY_MONTH_CODES = frozenset({"H", "M", "U", "Z"})
SUGAR_11_MONTH_CODES = frozenset({"H", "K", "N", "V"})

# FIX Tag 54 (Side) domain. ICE order entry carries the enumerated value, not a word.
_FIX_SIDE = {"BUY": "1", "SELL": "2"}

# Order-level outcomes.
STATUS_PASSED = "PRE_TRADE_CHECKS_PASSED"
STATUS_INVALID_TICK_SIZE = "INVALID_TICK_SIZE"
STATUS_REASONABILITY_LIMIT_BREACH = "REASONABILITY_LIMIT_BREACH"
STATUS_NO_ANCHOR_PRICE = "NO_ANCHOR_PRICE"

# Error-trade exposure of the resulting fill, measured from the anchor price.
NCR_WITHIN = "WITHIN_NCR"
NCR_PRICE_ADJUSTMENT = "OUTSIDE_NCR_PRICE_ADJUSTMENT"
NCR_AUTO_CANCELLATION = "OUTSIDE_NCR_AUTO_CANCELLATION"
NCR_EXCHANGE_DISCRETION = "OUTSIDE_NCR_EXCHANGE_DISCRETION"
NCR_UNKNOWN = "UNKNOWN_NO_ANCHOR"


def to_decimal(value: Number, field: str) -> Decimal:
    """Convert to Decimal without inheriting binary float error.

    Floats are routed through str so 75.50 means 75.50 and not
    75.499999999999996. Pass prices as strings when exactness matters.
    """
    if isinstance(value, bool):  # bool is an int subclass; never a price.
        raise TypeError(f"{field} must be numeric, got bool")
    if isinstance(value, Decimal):
        result = value
    elif isinstance(value, (int, str)):
        try:
            result = Decimal(value)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field} is not a valid decimal: {value!r}") from exc
    elif isinstance(value, float):
        try:
            result = Decimal(str(value))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"{field} is not a valid decimal: {value!r}") from exc
    else:
        raise TypeError(
            f"{field} must be str, int, float or Decimal, got {type(value).__name__}"
        )

    if not result.is_finite():
        raise ValueError(f"{field} must be finite, got {value!r}")
    return result


@dataclass(frozen=True)
class IceContractSpec:
    """One ICE outright futures contract.

    `reasonability_limit` and `no_cancellation_range` are distances from the
    Exchange-set anchor price expressed in `price_unit` - the units ICE publishes
    them in. They are not tick counts, and ICE changes them without notice, so
    they are carried with their source and retrieval date.

    Notional is `price * contract_size * currency_per_price_unit * quantity`.
    `currency_per_price_unit` converts one unit of the quoted price into
    `currency` per underlying unit - 1 for USD per barrel, 0.01 for US cents per
    pound. Encoding the exchange's own quotation convention is what makes a
    wrong-units price fail the tick check instead of silently mis-valuing.
    """

    product_contract_code: str          # ICE PCC, e.g. 'B', 'T', 'TFN', 'SB', 'DX'
    ice_product_id: int                 # ICE numeric product identifier
    name: str
    operating_mic: str                  # ISO 10383 operating MIC: 'IFEU' or 'IFUS'
    currency: str
    price_unit: str                     # human-readable quotation convention
    tick_size: Decimal                  # in quoted price units
    currency_per_price_unit: Decimal
    reasonability_limit: Decimal
    no_cancellation_range: Decimal
    listed_month_codes: frozenset
    limits_source: str
    limits_as_of: str
    contract_size: Optional[Decimal] = None  # underlying units per lot
    auto_cancellation_ncr_multiple: Optional[Decimal] = None
    contract_size_note: str = ""

    def __post_init__(self) -> None:
        for name in ("tick_size", "currency_per_price_unit", "reasonability_limit",
                     "no_cancellation_range"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{self.product_contract_code}: {name} must be positive")
        if self.contract_size is not None and self.contract_size <= 0:
            raise ValueError(f"{self.product_contract_code}: contract_size must be positive")
        if not self.listed_month_codes <= ALL_MONTH_CODES:
            raise ValueError(
                f"{self.product_contract_code}: unknown code in listed_month_codes"
            )


@dataclass(frozen=True)
class IceOrderPayload:
    """A single outright limit order to validate.

    `anchor_price` is the Exchange-set anchor price for the contract - the
    previous session's settlement, the opening call price or the last trade,
    carried to back months by spread differentials. ICE publishes it; it is not
    the mid and it is not derivable from the order book, so this module will not
    guess it. None means "unknown", and the reasonability check then fails closed
    rather than assuming a reference.

    `contract_size` overrides the catalog value and is required for contracts
    whose lot size varies with the delivery period (Dutch TTF).
    """

    root_symbol: str
    month_code: str
    year: int
    side: str
    price: Number
    quantity: int
    anchor_price: Optional[Number] = None
    contract_size: Optional[Number] = None
    limit_multiplier: Number = 1


@dataclass(frozen=True)
class IceFuturesOrderReport:
    """Outcome of the local pre-dispatch checks."""

    contract_name: str
    ice_display_code: str               # vendor-style '<ROOT><MONTH><YY>', e.g. 'BZ26'
    fix_tag_55_symbol: str              # ICE product contract code
    fix_tag_48_security_id: str         # ICE numeric product id
    fix_tag_207_security_exchange: str  # ISO 10383 operating MIC
    fix_tag_200_maturity_month_year: str
    fix_tag_54_side: str
    currency: str
    contract_size: Decimal
    contract_value: Decimal             # value of one lot at the order price
    notional_value: Decimal             # contract_value * quantity
    tick_value: Decimal
    is_price_tick_valid: bool
    passes_reasonability_limit: bool
    reasonability_upper: Optional[Decimal]
    reasonability_lower: Optional[Decimal]
    distance_from_anchor: Optional[Decimal]
    error_trade_exposure: str
    status: str
    audit_notes: str

    @property
    def ready_to_send(self) -> bool:
        """True only when every modelled check passed. Never means ICE accepted it."""
        return self.status == STATUS_PASSED


def _crude_oil_spec(code: str, product_id: int, name: str) -> IceContractSpec:
    """ICE Futures Europe USD/barrel crude contracts share size, tick and limits."""
    return IceContractSpec(
        product_contract_code=code,
        ice_product_id=product_id,
        name=name,
        operating_mic="IFEU",
        currency="USD",
        price_unit="USD per barrel",
        tick_size=Decimal("0.01"),
        contract_size=Decimal("1000"),          # barrels
        currency_per_price_unit=Decimal("1"),
        reasonability_limit=Decimal("0.75"),
        no_cancellation_range=Decimal("0.50"),
        auto_cancellation_ncr_multiple=Decimal("3"),
        listed_month_codes=ALL_MONTH_CODES,
        limits_source="ICE Futures Europe Price Controls workbook, 'Oils' sheet",
        limits_as_of="2026-08-25",
    )


def default_catalog() -> Dict[str, IceContractSpec]:
    """Reference data for a small set of ICE contracts, from ICE's published specs.

    This is a worked example, not a reference-data service. RL and NCR levels
    change without notice; refresh them from the ICE Futures Europe Price Controls
    workbook and the ICE Futures U.S. Reasonability Limits & No Cancellation
    Ranges document rather than trusting these constants in production.
    """
    return {
        "B": _crude_oil_spec("B", 254, "ICE Brent Crude Futures"),
        # ICE product contract code 'T' is ICE WTI Futures - NOT Dutch TTF.
        "T": _crude_oil_spec("T", 425, "ICE WTI Crude Futures"),
        "TFN": IceContractSpec(
            product_contract_code="TFN",
            ice_product_id=28456,
            name="IFEU Dutch TTF Natural Gas Futures",
            operating_mic="IFEU",
            currency="EUR",
            price_unit="EUR per MWh",
            tick_size=Decimal("0.005"),
            contract_size=None,     # 1 MW x hours in the delivery period
            contract_size_note=(
                "1 MW per day in the contract period x 23, 24 or 25 hours, so the "
                "MWh per lot depends on the delivery period and on daylight saving "
                "transitions. Supply it per contract month."
            ),
            currency_per_price_unit=Decimal("1"),
            reasonability_limit=Decimal("0.8"),
            no_cancellation_range=Decimal("0.4"),
            auto_cancellation_ncr_multiple=Decimal("3"),
            listed_month_codes=ALL_MONTH_CODES,
            limits_source="ICE Futures Europe Price Controls workbook, 'Utilities' sheet",
            limits_as_of="2026-08-25",
        ),
        "SB": IceContractSpec(
            product_contract_code="SB",
            ice_product_id=23,
            name="Sugar No. 11 Futures",
            operating_mic="IFUS",
            currency="USD",
            price_unit="US cents per pound",
            tick_size=Decimal("0.01"),                  # 1/100 cent per lb
            contract_size=Decimal("112000"),            # pounds
            currency_per_price_unit=Decimal("0.01"),    # cents -> dollars
            reasonability_limit=Decimal("0.50"),        # $.0050/lb = 0.50 cents/lb
            no_cancellation_range=Decimal("0.20"),      # $.0020/lb = 0.20 cents/lb
            auto_cancellation_ncr_multiple=None,        # IFUS futures: Exchange discretion
            listed_month_codes=SUGAR_11_MONTH_CODES,
            limits_source=(
                "ICE Futures U.S. Reasonability Limits and No Cancellation Ranges, August 2026"
            ),
            limits_as_of="2026-08-25",
        ),
        "DX": IceContractSpec(
            product_contract_code="DX",
            ice_product_id=194,
            name="US Dollar Index Futures",
            operating_mic="IFUS",
            currency="USD",
            price_unit="US Dollar Index points",
            tick_size=Decimal("0.005"),
            contract_size=Decimal("1000"),              # USD 1,000 x index
            currency_per_price_unit=Decimal("1"),
            reasonability_limit=Decimal("0.500"),
            no_cancellation_range=Decimal("0.200"),
            auto_cancellation_ncr_multiple=None,        # IFUS futures: Exchange discretion
            listed_month_codes=QUARTERLY_MONTH_CODES,
            limits_source=(
                "ICE Futures U.S. Reasonability Limits and No Cancellation Ranges, August 2026"
            ),
            limits_as_of="2026-08-25",
        ),
    }


class IceFuturesIntegrationEngine:
    """Validates an ICE outright futures order before it is dispatched.

    Structurally impossible requests (unknown contract, unlisted delivery month,
    non-positive price or quantity, malformed year or side) raise ValueError.
    Outcomes that depend on market state (tick alignment, reasonability limit,
    missing anchor price) come back as a report status, so a caller can log them
    and route around them.
    """

    def __init__(self, catalog: Optional[Mapping[str, IceContractSpec]] = None) -> None:
        source = default_catalog() if catalog is None else catalog
        # Copy so callers cannot mutate the engine's reference data after construction.
        self._catalog: Dict[str, IceContractSpec] = {
            str(k).strip().upper(): v for k, v in source.items()
        }
        if not self._catalog:
            raise ValueError("catalog must contain at least one contract")

    @property
    def supported_codes(self) -> Tuple[str, ...]:
        """ICE product contract codes this engine holds reference data for."""
        return tuple(sorted(self._catalog))

    def get_contract(self, root_symbol: str) -> IceContractSpec:
        """Return the spec for an ICE product contract code."""
        code = str(root_symbol).strip().upper()
        if code not in self._catalog:
            raise ValueError(
                f"Unsupported ICE product contract code {code!r}. "
                f"Supported: {list(self.supported_codes)}"
            )
        return self._catalog[code]

    def format_ice_symbol(self, root: str, month_code: str, year: int) -> Tuple[str, str]:
        """Return (display_code, FIX Tag 200 MaturityMonthYear).

        The display code is the vendor-style `<ROOT><MONTH><YY>` convention
        ('BZ26'). It is a label, not an ICE wire identifier: ICE publishes several
        codes for the same contract (Brent is 'B', 'BRN' and 'BC') and identifies
        products numerically. The two-digit year is also ambiguous across Brent's
        156-month listed curve - 'BZ26' fits both Dec 2026 and Dec 2039. YYYYMM is
        the identifier to reason with.
        """
        code = str(month_code).strip().upper()
        month_num = _MONTH_CODE_TO_NUM.get(code)
        if month_num is None:
            raise ValueError(
                f"Invalid ICE month code {month_code!r}. Valid: {sorted(_MONTH_CODE_TO_NUM)}"
            )
        if isinstance(year, bool) or not isinstance(year, int):
            raise TypeError(f"year must be a 4-digit int, got {type(year).__name__}")
        if not 1000 <= year <= 9999:
            raise ValueError(
                f"year must be a 4-digit calendar year so FIX Tag 200 is well formed, got {year}"
            )

        root_code = str(root).strip().upper()
        display_code = f"{root_code}{code}{year % 100:02d}"
        maturity_yyyymm = f"{year:04d}{month_num:02d}"
        return display_code, maturity_yyyymm

    def _resolve_contract_size(
        self, spec: IceContractSpec, payload: IceOrderPayload
    ) -> Decimal:
        if payload.contract_size is not None:
            size = to_decimal(payload.contract_size, "contract_size")
            if size <= 0:
                raise ValueError(f"contract_size must be positive, got {size}")
            return size
        if spec.contract_size is None:
            raise ValueError(
                f"{spec.product_contract_code} has no fixed lot size, so contract_size must be "
                f"supplied per delivery period. {spec.contract_size_note}"
            )
        return spec.contract_size

    @staticmethod
    def _is_on_tick(price: Decimal, tick: Decimal) -> bool:
        """True when price is an exact multiple of tick.

        Decimal keeps this exact where float division does not: 75.505 / 0.01 is
        7550.499999999999 in binary floating point, so a tolerance-based check has
        to guess how much error to forgive.
        """
        try:
            return price % tick == 0
        except DecimalException:
            return False

    @staticmethod
    def _classify_error_trade_exposure(
        spec: IceContractSpec, distance: Decimal, ncr: Decimal
    ) -> str:
        """Classify where a fill at this price sits against the No Cancellation Range."""
        if distance <= ncr:
            return NCR_WITHIN
        multiple = spec.auto_cancellation_ncr_multiple
        if multiple is None:
            return NCR_EXCHANGE_DISCRETION
        if distance > ncr * multiple:
            return NCR_AUTO_CANCELLATION
        return NCR_PRICE_ADJUSTMENT

    def process_and_route_order(self, payload: IceOrderPayload) -> IceFuturesOrderReport:
        """Run every modelled pre-dispatch check and return a structured report."""
        spec = self.get_contract(payload.root_symbol)

        month_code = str(payload.month_code).strip().upper()
        if month_code not in spec.listed_month_codes:
            raise ValueError(
                f"{spec.name} does not list delivery month {month_code!r}. "
                f"Listed: {sorted(spec.listed_month_codes)}"
            )
        display_code, maturity = self.format_ice_symbol(
            spec.product_contract_code, month_code, payload.year
        )

        side = str(payload.side).strip().upper()
        if side not in _FIX_SIDE:
            raise ValueError(f"side must be one of {sorted(_FIX_SIDE)}, got {payload.side!r}")

        if isinstance(payload.quantity, bool) or not isinstance(payload.quantity, int):
            raise TypeError(f"quantity must be an int, got {type(payload.quantity).__name__}")
        if payload.quantity <= 0:
            raise ValueError(f"quantity must be a positive number of lots, got {payload.quantity}")

        price = to_decimal(payload.price, "price")
        if price <= 0:
            # Checked separately: Decimal('-75.50') % Decimal('0.01') is zero, so a
            # negative price passes the tick test on its own.
            raise ValueError(f"price must be positive, got {price}")

        multiplier = to_decimal(payload.limit_multiplier, "limit_multiplier")
        if multiplier <= 0:
            raise ValueError(f"limit_multiplier must be positive, got {multiplier}")

        contract_size = self._resolve_contract_size(spec, payload)

        contract_value = price * contract_size * spec.currency_per_price_unit
        notional = contract_value * payload.quantity
        tick_value = spec.tick_size * contract_size * spec.currency_per_price_unit

        is_tick_valid = self._is_on_tick(price, spec.tick_size)

        anchor: Optional[Decimal] = None
        if payload.anchor_price is not None:
            anchor = to_decimal(payload.anchor_price, "anchor_price")
            if anchor <= 0:
                raise ValueError(f"anchor_price must be positive when supplied, got {anchor}")

        upper: Optional[Decimal] = None
        lower: Optional[Decimal] = None
        distance: Optional[Decimal] = None
        passes_rl = False
        exposure = NCR_UNKNOWN
        if anchor is not None:
            rl = spec.reasonability_limit * multiplier
            upper = anchor + rl
            lower = anchor - rl
            distance = abs(price - anchor)
            # Directional, per the ICE error policies: a buy above the upper limit
            # or a sell below the lower limit is refused. A passive buy far below
            # the market is accepted.
            passes_rl = price <= upper if side == "BUY" else price >= lower
            exposure = self._classify_error_trade_exposure(
                spec, distance, spec.no_cancellation_range * multiplier
            )

        if not is_tick_valid:
            status = STATUS_INVALID_TICK_SIZE
            notes = (
                f"REJECTED LOCALLY [{display_code}]: price {price} is not a multiple of the "
                f"{spec.tick_size} {spec.price_unit} minimum price fluctuation."
            )
            logger.warning(notes)
        elif anchor is None:
            status = STATUS_NO_ANCHOR_PRICE
            notes = (
                f"CANNOT CHECK [{display_code}]: no Exchange anchor price supplied, so the "
                f"reasonability limit cannot be evaluated. Failing closed."
            )
            logger.warning(notes)
        elif not passes_rl:
            status = STATUS_REASONABILITY_LIMIT_BREACH
            bound = "upper" if side == "BUY" else "lower"
            notes = (
                f"REJECTED LOCALLY [{display_code}]: {side} at {price} breaches the {bound} "
                f"reasonability limit ({lower} to {upper}, anchor {anchor}, "
                f"RL {spec.reasonability_limit} x{multiplier} {spec.price_unit}). "
                f"ICE would not accept this limit order."
            )
            logger.warning(notes)
        else:
            status = STATUS_PASSED
            notes = (
                f"PRE-TRADE CHECKS PASSED [{display_code} - {spec.operating_mic}]: {side} "
                f"{payload.quantity} @ {price} {spec.price_unit} "
                f"(notional {notional} {spec.currency}, Tag 207 {spec.operating_mic}, "
                f"Tag 200 {maturity}, error-trade exposure {exposure})."
            )
            logger.info(notes)

        return IceFuturesOrderReport(
            contract_name=spec.name,
            ice_display_code=display_code,
            fix_tag_55_symbol=spec.product_contract_code,
            fix_tag_48_security_id=str(spec.ice_product_id),
            fix_tag_207_security_exchange=spec.operating_mic,
            fix_tag_200_maturity_month_year=maturity,
            fix_tag_54_side=_FIX_SIDE[side],
            currency=spec.currency,
            contract_size=contract_size,
            contract_value=contract_value,
            notional_value=notional,
            tick_value=tick_value,
            is_price_tick_valid=is_tick_valid,
            passes_reasonability_limit=passes_rl,
            reasonability_upper=upper,
            reasonability_lower=lower,
            distance_from_anchor=distance,
            error_trade_exposure=exposure,
            status=status,
            audit_notes=notes,
        )

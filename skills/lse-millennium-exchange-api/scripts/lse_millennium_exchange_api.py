"""Client-side pre-dispatch validation for London Stock Exchange (Millennium Exchange) orders.

Scope of the checks modelled here, and the source each one comes from:

* **TIDM** - the Exchange-allocated display mnemonic, ``STRING(4)`` in the Reference
  Data Service instrument record (MIT401 s2.7). It is *not* the identifier carried on
  trading messages: Millennium Exchange identifies instruments by a unique
  ``InstrumentID``, sent as FIX Tag 48 ``SecurityID`` (MIT201 s4.6).
* **Trading currency** - per-instrument reference data. The ``Currency`` field is
  ISO 4217 "except that, for SEAQ compatibility, GBX has been retained" (MIT401 s2.7),
  so a GBX line is quoted in pence and a USD line is quoted in dollars.
* **Tick size** - the binding increment is the instrument's own price tick table
  (MIT401 s2.12: ``Min Value`` / ``Max Value`` / ``Tick Value``), which may be static
  or dynamic; "if the price of an order/quote is not a multiple of the tick size on
  entry it will be rejected" (MIT201 s5.5). UK RTS 11 (assimilated Commission
  Delegated Regulation (EU) 2017/588, Article 2(1)) sets a *floor* - venues apply a
  tick "equal to or greater than" the Annex cell - so the Annex is a cross-check on
  reference data, never a substitute for it.

Everything here runs before a message leaves the process. Nothing in this module opens
a session, sends an order, or converts currency.
"""

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext
from enum import Enum
from typing import Dict, Iterable, Optional, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

#: LSE reference data defines TIDM as STRING(4) (MIT401 s2.7). TIDMs are not restricted
#: to A-Z: ``BP.``, ``BT.A``, ``RR.`` and ``3IN`` are all live LSE mnemonics.
TIDM_MAX_LENGTH = 4
TIDM_ALLOWED_CHARACTERS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.")

#: Non-ISO-4217 code the Exchange retained for SEAQ compatibility (MIT401 s2.7).
GBX = "GBX"
GBP = "GBP"
PENCE_PER_POUND = Decimal("100")

STATUS_VALIDATED = "LSE_ORDER_VALIDATED"
STATUS_INVALID_TIDM = "INVALID_TIDM"
STATUS_INVALID_CURRENCY = "INVALID_CURRENCY"
STATUS_INVALID_TICK_SIZE = "INVALID_TICK_SIZE"
STATUS_INVALID_PRICE = "INVALID_PRICE"
STATUS_INVALID_QUANTITY = "INVALID_QUANTITY"
STATUS_INVALID_SIDE = "INVALID_SIDE"
STATUS_REFERENCE_DATA_REQUIRED = "REFERENCE_DATA_REQUIRED"

#: Where the increment used for the tick check came from.
TICK_SOURCE_REFERENCE_DATA = "INSTRUMENT_PRICE_TICK_TABLE"
TICK_SOURCE_RTS11_FLOOR = "RTS11_REGULATORY_FLOOR"

PriceInput = Union[str, int, float, Decimal]


class LseGatewayError(ValueError):
    """Base class for pre-dispatch validation failures raised by this module."""


class InvalidTidmError(LseGatewayError):
    """Raised when a mnemonic cannot be a TIDM as the Exchange defines the field."""


class UnknownInstrumentError(LseGatewayError):
    """Raised when no reference-data record has been registered for a TIDM."""


class LiquidityBandRequiredError(LseGatewayError):
    """Raised when an RTS 11 tick is requested without the instrument's liquidity band."""


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


# --------------------------------------------------------------------------------------
# UK RTS 11 - assimilated Commission Delegated Regulation (EU) 2017/588, Annex.
# Rows are (upper bound exclusive, ticks for liquidity bands 1..6); the liquidity band
# comes from the instrument's average daily number of transactions (ADNT) on the most
# relevant market in terms of liquidity, published annually by the FCA (Article 3).
# --------------------------------------------------------------------------------------
RTS11_LIQUIDITY_BANDS: Tuple[Tuple[int, Decimal, Decimal], ...] = (
    (1, Decimal("0"), Decimal("10")),
    (2, Decimal("10"), Decimal("80")),
    (3, Decimal("80"), Decimal("600")),
    (4, Decimal("600"), Decimal("2000")),
    (5, Decimal("2000"), Decimal("9000")),
    (6, Decimal("9000"), Decimal("Infinity")),
)

RTS11_TICK_TABLE: Tuple[Tuple[str, Tuple[str, str, str, str, str, str]], ...] = (
    ("0.1", ("0.0005", "0.0002", "0.0001", "0.0001", "0.0001", "0.0001")),
    ("0.2", ("0.001", "0.0005", "0.0002", "0.0001", "0.0001", "0.0001")),
    ("0.5", ("0.002", "0.001", "0.0005", "0.0002", "0.0001", "0.0001")),
    ("1", ("0.005", "0.002", "0.001", "0.0005", "0.0002", "0.0001")),
    ("2", ("0.01", "0.005", "0.002", "0.001", "0.0005", "0.0002")),
    ("5", ("0.02", "0.01", "0.005", "0.002", "0.001", "0.0005")),
    ("10", ("0.05", "0.02", "0.01", "0.005", "0.002", "0.001")),
    ("20", ("0.1", "0.05", "0.02", "0.01", "0.005", "0.002")),
    ("50", ("0.2", "0.1", "0.05", "0.02", "0.01", "0.005")),
    ("100", ("0.5", "0.2", "0.1", "0.05", "0.02", "0.01")),
    ("200", ("1", "0.5", "0.2", "0.1", "0.05", "0.02")),
    ("500", ("2", "1", "0.5", "0.2", "0.1", "0.05")),
    ("1000", ("5", "2", "1", "0.5", "0.2", "0.1")),
    ("2000", ("10", "5", "2", "1", "0.5", "0.2")),
    ("5000", ("20", "10", "5", "2", "1", "0.5")),
    ("10000", ("50", "20", "10", "5", "2", "1")),
    ("20000", ("100", "50", "20", "10", "5", "2")),
    ("50000", ("200", "100", "50", "20", "10", "5")),
    ("Infinity", ("500", "200", "100", "50", "20", "10")),
)


def _to_decimal(value: PriceInput, label: str) -> Decimal:
    """Convert to a finite ``Decimal``, reading floats through their shortest repr.

    ``float`` is accepted because callers hold prices as floats, but ``Decimal(0.1)`` is
    0.1000000000000000055511151231257827, which fails an exact tick test the matching
    engine would pass. ``Decimal(repr(0.1))`` is ``0.1``.
    """
    if isinstance(value, bool):
        raise LseGatewayError(f"{label} must be numeric, got bool {value!r}.")
    try:
        if isinstance(value, float):
            converted = Decimal(repr(value))
        elif isinstance(value, Decimal):
            converted = value
        else:
            converted = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, ArithmeticError) as exc:
        raise LseGatewayError(f"{label} {value!r} is not a valid decimal number.") from exc
    if not converted.is_finite():
        raise LseGatewayError(f"{label} must be finite, got {value!r}.")
    return converted


def _digits(value: Decimal) -> int:
    return len(value.as_tuple().digits)


def is_on_tick(price: Decimal, tick: Decimal) -> bool:
    """Exact "is this price a whole number of ticks" test, safe at any magnitude.

    The default 28-significant-digit context makes ``Decimal('1E+40') % Decimal('10')``
    raise ``DivisionImpossible`` - the quotient does not fit. Precision is widened to
    hold the quotient so an implausible price returns a verdict instead of escaping the
    order path as an unhandled exception.
    """
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, price.adjusted() - tick.adjusted() + _digits(tick) + 4)
        return price % tick == 0


def _order_value(price: Decimal, quantity: int) -> Decimal:
    """Exact ``price * quantity``; Decimal multiplication is otherwise context-rounded."""
    qty = Decimal(quantity)
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, _digits(price) + _digits(qty) + 2)
        return price * qty


def _to_money(value: Decimal, divisor: Optional[Decimal] = None) -> Decimal:
    """Scale to a currency unit and round half-up to the minor unit."""
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, value.adjusted() + _digits(value) + 6)
        scaled = value if divisor is None else value / divisor
        return scaled.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class PriceTickBand:
    """One row of the LSE Price Tick File (MIT401 s2.12).

    ``min_price`` is inclusive and ``max_price`` exclusive, matching the ``Min Value`` /
    ``Max Value`` band bounds published per ``Price Tick Table ID``.
    """

    min_price: Decimal
    max_price: Decimal
    tick: Decimal

    def __post_init__(self) -> None:
        for name in ("min_price", "max_price", "tick"):
            object.__setattr__(self, name, _to_decimal(getattr(self, name), name))
        if self.min_price < 0:
            raise LseGatewayError(f"Price tick band lower bound {self.min_price} is negative.")
        if self.max_price <= self.min_price:
            raise LseGatewayError(
                f"Price tick band [{self.min_price}, {self.max_price}) is empty or inverted."
            )
        if self.tick <= 0:
            raise LseGatewayError(f"Price tick band tick {self.tick} must be strictly positive.")

    def contains(self, price: Decimal) -> bool:
        return self.min_price <= price < self.max_price


@dataclass(frozen=True)
class LseInstrument:
    """Reference-data record for one LSE-traded instrument.

    Field names follow the Reference Data Service (MIT401). ``instrument_id`` is the
    identifier actually carried on trading messages (FIX Tag 48); the TIDM is a display
    mnemonic and can change, in which case the instrument is deleted and re-added
    (MIT201 s4.6).

    Supply ``price_tick_table`` when the instrument's Price Tick File rows are known -
    that is the increment the matching engine enforces. ``liquidity_band`` only drives
    the RTS 11 regulatory floor, used as a fallback and as a cross-check.
    """

    tidm: str
    currency: str
    instrument_id: Optional[str] = None
    liquidity_band: Optional[int] = None
    price_tick_table: Tuple[PriceTickBand, ...] = ()
    price_tick_table_id: Optional[str] = None
    reference_source: str = ""
    reference_as_of: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "tidm", normalise_tidm(self.tidm))
        currency = str(self.currency).strip().upper()
        if not currency:
            raise LseGatewayError(f"{self.tidm}: trading currency is required (MIT401 'Currency').")
        object.__setattr__(self, "currency", currency)
        if self.liquidity_band is not None and self.liquidity_band not in range(1, 7):
            raise LseGatewayError(
                f"{self.tidm}: RTS 11 liquidity band must be 1-6, got {self.liquidity_band!r}."
            )
        object.__setattr__(self, "price_tick_table", tuple(self.price_tick_table))
        _validate_tick_table(self.tidm, self.price_tick_table)

    @property
    def is_pence_quoted(self) -> bool:
        return self.currency == GBX


def _validate_tick_table(tidm: str, bands: Sequence[PriceTickBand]) -> None:
    """Reject overlapping or unordered tick tables at registration time.

    A malformed table silently falls through to the regulatory floor at exactly the
    price where the venue increment was meant to apply, so it is checked once, on
    ingest, rather than on every order.
    """
    for previous, current in zip(bands, bands[1:]):
        if current.min_price < previous.max_price:
            raise LseGatewayError(
                f"{tidm}: price tick bands overlap or are unordered - "
                f"[{previous.min_price}, {previous.max_price}) is followed by "
                f"[{current.min_price}, {current.max_price})."
            )


@dataclass
class LseOrderPayload:
    """An order as the strategy proposes it, before any Millennium field mapping."""

    tidm: str
    side: str
    price: PriceInput
    quantity: int
    currency: str = GBX


@dataclass
class LseOrderReport:
    """Structured verdict on one proposed order."""

    tidm: str
    side: str
    price: Optional[Decimal]
    currency: str
    quantity: int
    status: str
    audit_notes: str
    is_price_tick_valid: bool = False
    instrument_id: Optional[str] = None
    applicable_tick_size: Optional[Decimal] = None
    tick_size_source: Optional[str] = None
    rts11_floor_tick: Optional[Decimal] = None
    liquidity_band: Optional[int] = None
    tick_below_rts11_floor: bool = False
    #: Order value in the quoted unit - pence for a GBX line, not pounds.
    notional_quoted: Optional[Decimal] = None
    #: Order value in pounds. ``None`` for a line quoted in any currency but GBX or GBP:
    #: this module holds no FX rate and will not invent one.
    notional_gbp: Optional[Decimal] = None
    warnings: Tuple[str, ...] = field(default_factory=tuple)

    @property
    def ready_to_send(self) -> bool:
        """True only for the checks modelled here - never "the Exchange has the order"."""
        return self.status == STATUS_VALIDATED


def normalise_tidm(tidm: str) -> str:
    """Normalise and validate a TIDM against the Exchange's ``STRING(4)`` field.

    The character set is deliberately wider than A-Z: ``BP.``, ``BT.A`` and ``3IN`` are
    live mnemonics that an ``isalpha()`` check would reject.
    """
    if not isinstance(tidm, str):
        raise InvalidTidmError(f"TIDM must be a string, got {type(tidm).__name__}.")
    clean = tidm.strip().upper()
    if not clean:
        raise InvalidTidmError("TIDM is empty.")
    if len(clean) > TIDM_MAX_LENGTH:
        raise InvalidTidmError(
            f"Invalid TIDM {tidm!r}: {len(clean)} characters. The Exchange defines TIDM as "
            f"STRING({TIDM_MAX_LENGTH}) (MIT401). A longer symbol is usually a vendor code "
            f"such as a RIC ('SHEL.L') or a Bloomberg ticker ('SHEL LN')."
        )
    illegal = sorted(set(clean) - TIDM_ALLOWED_CHARACTERS)
    if illegal:
        raise InvalidTidmError(
            f"Invalid TIDM {tidm!r}: character(s) {illegal} are not used in LSE mnemonics."
        )
    return clean


def liquidity_band_for_adnt(adnt: PriceInput) -> int:
    """Map a published average daily number of transactions onto RTS 11 bands 1-6.

    The ADNT comes from the FCA's annual calculation (UK RTS 11 Article 3), published
    through FITRS; a band change takes effect from the first Monday of April. Deriving
    it from your own observed trade counts will put liquid names in the wrong band.
    """
    value = _to_decimal(adnt, "ADNT")
    if value < 0:
        raise LseGatewayError(f"ADNT must not be negative, got {value}.")
    for band, lower, upper in RTS11_LIQUIDITY_BANDS:
        if lower <= value < upper:
            return band
    return 6


def rts11_floor_tick(price: PriceInput, liquidity_band: int) -> Decimal:
    """Return the RTS 11 Annex tick for a price and liquidity band.

    This is the regulatory *minimum* increment - Article 2(1) requires venues to apply a
    tick "equal to or greater than" this value. The price is read in the unit the
    instrument is quoted in, so a GBX line is looked up in pence.
    """
    if liquidity_band not in range(1, 7):
        raise LiquidityBandRequiredError(
            f"RTS 11 liquidity band must be 1-6, got {liquidity_band!r}."
        )
    value = _to_decimal(price, "Price")
    if value <= 0:
        raise LseGatewayError(f"Price {value} must be strictly positive.")
    column = liquidity_band - 1
    for upper_str, ticks in RTS11_TICK_TABLE:
        if upper_str == "Infinity" or value < Decimal(upper_str):
            return Decimal(ticks[column])
    raise AssertionError("RTS 11 table is unbounded above; this branch is unreachable.")


#: Worked example only. Trading currencies and mnemonics were read from the London Stock
#: Exchange website instrument data on 2026-08-25. The liquidity bands are *inferred*
#: from the published bid/offer increments on that date, not read from an FCA
#: publication - replace them with the FCA FITRS ADNT calculation, and replace this
#: catalog entirely with the Reference Data Service Price Tick File, before relying on it.
DEFAULT_INSTRUMENTS: Tuple[LseInstrument, ...] = (
    LseInstrument(
        tidm="SHEL",
        currency=GBX,
        liquidity_band=6,
        reference_source="LSE published instrument data; band inferred from 0.5 GBX quote increment",
        reference_as_of="2026-08-25",
    ),
    LseInstrument(
        tidm="AZN",
        currency=GBX,
        liquidity_band=6,
        reference_source="LSE published instrument data; band inferred from 2 GBX quote increment",
        reference_as_of="2026-08-25",
    ),
    LseInstrument(
        tidm="BT.A",
        currency=GBX,
        liquidity_band=5,
        reference_source="LSE published instrument data; band inferred from 0.1 GBX quote increment",
        reference_as_of="2026-08-25",
    ),
    # A USD-quoted LSE line, and an ETC rather than a share or depositary receipt, so no
    # RTS 11 share band applies. With no tick table it fails closed rather than guessing.
    LseInstrument(
        tidm="IGLN",
        currency="USD",
        reference_source="LSE published instrument data (iShares Physical Gold ETC)",
        reference_as_of="2026-08-25",
    ),
)


class LseMillenniumExchangeApiEngine:
    """Validates proposed orders against LSE instrument reference data before dispatch.

    The engine is stateless per call apart from its instrument catalog, holds no order
    state, and never mutates a payload. Register instruments from the Reference Data
    Service; ``DEFAULT_INSTRUMENTS`` is a worked example, not a data source.
    """

    def __init__(self, instruments: Optional[Iterable[LseInstrument]] = None) -> None:
        self._instruments: Dict[str, LseInstrument] = {}
        for instrument in (DEFAULT_INSTRUMENTS if instruments is None else instruments):
            self.register_instrument(instrument)

    def register_instrument(self, instrument: LseInstrument) -> None:
        """Add or replace one instrument reference-data record."""
        if not isinstance(instrument, LseInstrument):
            raise LseGatewayError(f"Expected an LseInstrument, got {type(instrument).__name__}.")
        self._instruments[instrument.tidm] = instrument

    def resolve_instrument(self, tidm: str) -> LseInstrument:
        """Look up reference data by TIDM, raising rather than defaulting."""
        clean = normalise_tidm(tidm)
        try:
            return self._instruments[clean]
        except KeyError:
            raise UnknownInstrumentError(
                f"No reference data registered for TIDM {clean!r}. Load the instrument from "
                f"the LSE Reference Data Service (MIT401) rather than assuming GBX quoting."
            ) from None

    def active_tick_size(self, instrument: LseInstrument, price: PriceInput) -> Tuple[Decimal, str]:
        """Return ``(tick, source)`` for a price, preferring the venue's own tick table.

        The instrument's price tick table is what the matching engine enforces. The
        RTS 11 floor is used only when no table has been loaded, and only for an
        instrument carrying a liquidity band - an ETF, ETC or any line without one fails
        closed instead of borrowing a share's increment.
        """
        value = _to_decimal(price, "Price")
        if value <= 0:
            raise LseGatewayError(f"Price {value} must be strictly positive.")
        for band in instrument.price_tick_table:
            if band.contains(value):
                return band.tick, TICK_SOURCE_REFERENCE_DATA
        if instrument.price_tick_table:
            raise LseGatewayError(
                f"{instrument.tidm}: price {value} falls outside every band of price tick "
                f"table {instrument.price_tick_table_id or '(unnamed)'}. Reload the Price "
                f"Tick File rather than extrapolating the top band."
            )
        if instrument.liquidity_band is None:
            raise LiquidityBandRequiredError(
                f"{instrument.tidm}: no price tick table and no RTS 11 liquidity band. The "
                f"tick cannot be derived from price alone."
            )
        return rts11_floor_tick(value, instrument.liquidity_band), TICK_SOURCE_RTS11_FLOOR

    def validate_and_route_order(self, payload: LseOrderPayload) -> LseOrderReport:
        """Run every pre-dispatch check and return a structured report.

        Never raises for a bad payload: an invalid order comes back as a report with a
        rejecting ``status``, so a caller can log and route the verdict rather than
        trapping exceptions on the order path.
        """
        raw_side = str(payload.side).strip().upper() if payload.side is not None else ""
        raw_currency = str(payload.currency).strip().upper() if payload.currency is not None else ""

        try:
            instrument = self.resolve_instrument(payload.tidm)
        except InvalidTidmError as exc:
            return self._reject(payload, raw_side, raw_currency, STATUS_INVALID_TIDM, str(exc))
        except UnknownInstrumentError as exc:
            return self._reject(
                payload, raw_side, raw_currency, STATUS_REFERENCE_DATA_REQUIRED, str(exc)
            )

        try:
            side = OrderSide(raw_side)
        except ValueError:
            return self._reject(
                payload,
                raw_side,
                raw_currency,
                STATUS_INVALID_SIDE,
                f"Side {payload.side!r} is not BUY or SELL.",
                instrument=instrument,
            )

        if raw_currency != instrument.currency:
            hint = ""
            if raw_currency == GBP and instrument.currency == GBX:
                hint = (
                    " A GBX line is quoted in pence: a price scaled for pounds is 100x too "
                    "small, not merely mislabelled."
                )
            return self._reject(
                payload,
                side.value,
                raw_currency,
                STATUS_INVALID_CURRENCY,
                f"Currency {payload.currency!r} does not match the instrument's trading "
                f"currency {instrument.currency!r} (MIT401 'Currency').{hint}",
                instrument=instrument,
            )

        try:
            price = _to_decimal(payload.price, "Price")
        except LseGatewayError as exc:
            return self._reject(
                payload,
                side.value,
                raw_currency,
                STATUS_INVALID_PRICE,
                str(exc),
                instrument=instrument,
            )
        if price <= 0:
            return self._reject(
                payload,
                side.value,
                raw_currency,
                STATUS_INVALID_PRICE,
                f"Price {price} must be greater than zero (MIT201 order 'Price' field).",
                instrument=instrument,
                price=price,
            )

        if not isinstance(payload.quantity, int) or isinstance(payload.quantity, bool):
            return self._reject(
                payload,
                side.value,
                raw_currency,
                STATUS_INVALID_QUANTITY,
                f"Quantity {payload.quantity!r} must be an integer number of units.",
                instrument=instrument,
                price=price,
            )
        if payload.quantity <= 0:
            return self._reject(
                payload,
                side.value,
                raw_currency,
                STATUS_INVALID_QUANTITY,
                f"Quantity {payload.quantity} must be strictly positive.",
                instrument=instrument,
                price=price,
            )

        try:
            tick, tick_source = self.active_tick_size(instrument, price)
        except LseGatewayError as exc:
            return self._reject(
                payload,
                side.value,
                raw_currency,
                STATUS_REFERENCE_DATA_REQUIRED,
                str(exc),
                instrument=instrument,
                price=price,
            )

        floor_tick: Optional[Decimal] = None
        warnings: Tuple[str, ...] = ()
        if instrument.liquidity_band is not None:
            floor_tick = rts11_floor_tick(price, instrument.liquidity_band)
            if tick < floor_tick:
                warnings += (
                    f"Reference-data tick {tick} is finer than the UK RTS 11 floor {floor_tick} "
                    f"for liquidity band {instrument.liquidity_band}. Legitimate only under "
                    f"Article 2(2A), for an instrument first admitted on a third-country venue; "
                    f"otherwise the reference data or the band is stale.",
                )

        # A negative price is congruent to zero modulo the tick, so positivity is checked
        # above and separately - the modulo test alone would pass -3385.0 GBX.
        is_tick_valid = is_on_tick(price, tick)

        notional_quoted = _order_value(price, payload.quantity)
        notional_gbp: Optional[Decimal] = None
        if instrument.currency == GBX:
            notional_gbp = _to_money(notional_quoted, PENCE_PER_POUND)
        elif instrument.currency == GBP:
            notional_gbp = _to_money(notional_quoted)

        if not is_tick_valid:
            status = STATUS_INVALID_TICK_SIZE
            notes = (
                f"LSE REJECT [{instrument.tidm}]: price {price} {instrument.currency} is not a "
                f"multiple of the {tick} {instrument.currency} tick ({tick_source}); Millennium "
                f"Exchange rejects an off-tick price on entry (MIT201 s5.5)."
            )
            logger.warning(notes)
        else:
            status = STATUS_VALIDATED
            gbp_note = f" Notional = GBP {notional_gbp:,}." if notional_gbp is not None else ""
            notes = (
                f"LSE ORDER VALIDATED [{instrument.tidm}]: {side.value} {payload.quantity:,} @ "
                f"{price} {instrument.currency} on a {tick} {instrument.currency} tick "
                f"({tick_source}).{gbp_note}"
            )
            logger.info(notes)
        for warning in warnings:
            logger.warning("LSE WARN [%s]: %s", instrument.tidm, warning)

        return LseOrderReport(
            tidm=instrument.tidm,
            side=side.value,
            price=price,
            currency=instrument.currency,
            quantity=payload.quantity,
            status=status,
            audit_notes=notes,
            is_price_tick_valid=is_tick_valid,
            instrument_id=instrument.instrument_id,
            applicable_tick_size=tick,
            tick_size_source=tick_source,
            rts11_floor_tick=floor_tick,
            liquidity_band=instrument.liquidity_band,
            tick_below_rts11_floor=bool(warnings),
            notional_quoted=notional_quoted,
            notional_gbp=notional_gbp,
            warnings=warnings,
        )

    @staticmethod
    def _reject(
        payload: LseOrderPayload,
        side: str,
        currency: str,
        status: str,
        reason: str,
        instrument: Optional[LseInstrument] = None,
        price: Optional[Decimal] = None,
    ) -> LseOrderReport:
        tidm = instrument.tidm if instrument is not None else str(payload.tidm)
        # Exceptions raised by the public helpers already name the instrument; the
        # bracketed prefix would otherwise repeat it.
        prefix = f"{tidm}: "
        if reason.startswith(prefix):
            reason = reason[len(prefix):]
        notes = f"LSE REJECT [{tidm}]: {reason}"
        logger.error(notes)
        return LseOrderReport(
            tidm=tidm,
            side=side,
            price=price,
            currency=currency,
            quantity=payload.quantity if isinstance(payload.quantity, int) else 0,
            status=status,
            audit_notes=notes,
            is_price_tick_valid=False,
            instrument_id=instrument.instrument_id if instrument is not None else None,
            liquidity_band=instrument.liquidity_band if instrument is not None else None,
        )

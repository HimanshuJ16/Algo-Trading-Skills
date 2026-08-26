"""Client-side pre-dispatch validation and MFIX field construction for Moscow Exchange orders.

This module models the checks that belong on the client side of a MOEX order
path, before a message leaves the process:

  * a **fail-closed sanctions gate**, because Moscow Exchange and its central
    counterparty are currently designated on the OFAC SDN list;
  * **quantity in lots**, because MOEX Tag 38 ``OrderQty`` is "expressed in
    number of lots" and the lot size differs per Symbol + Board combination;
  * **price alignment to the instrument's minimum price step**, because MOEX
    rejects orders "with price that does not fit in minimal price steps levels";
  * **price limits**, using the Exchange-published bounds where the board
    publishes them, and otherwise a caller-declared client-side policy band;
  * **MFIX (FIX 4.4) NewOrderSingle body fields**, with the board carried in the
    Tag 386 / Tag 336 group the specification actually defines.

Scope. Nothing here opens a socket, logs on to a gateway, queries ISS, or sends
an order. ``MOEX_ORDER_VALIDATED`` means "passed the checks modelled here",
never "MOEX has accepted the order". Reference data (lot size, price step,
decimals, price limits) is an **input**: this module does not fetch it, and it
does not invent defaults for it.

This module makes no legal determination about sanctions. The gate records an
attestation that a screening was performed and forces the caller to supply one;
deciding whether a transaction is permitted is the operator's obligation.

Primary sources (retrieved 2026-08-26) are listed in ``references/standards.md``.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_FLOOR
from typing import Dict, List, Mapping, Optional, Tuple, Union

logger = logging.getLogger(__name__)

Number = Union[str, int, float, Decimal]

# --------------------------------------------------------------------------
# Board registry
#
# engine/market/board triples verified against https://iss.moex.com/iss/index.json
# (2026-08-26). `asts_mfix` records whether the MOEX public FIX 4.4 interface
# specification covers the board: that document is "valid for Moscow Exchange FX
# and Securities (Main and T+2) markets only". The Derivatives market (SPECTRA)
# is reached over different interfaces (TWIME SPECTRA, Plaza II) and its order
# entry is NOT described by that specification.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class MOEXBoard:
    """A MOEX trading board and the interface family that serves it."""

    board_id: str
    engine: str
    market: str
    trading_system: str
    asts_mfix: bool
    description: str


MOEX_BOARDS: Mapping[str, MOEXBoard] = {
    "TQBR": MOEXBoard(
        "TQBR", "stock", "shares", "ASTS", True,
        "T+: shares and depositary receipts, order-driven",
    ),
    "CETS": MOEXBoard(
        "CETS", "currency", "selt", "ASTS", True,
        "FX market (SELT) system trades, order-driven",
    ),
    "RFUD": MOEXBoard(
        "RFUD", "futures", "forts", "SPECTRA", False,
        "FORTS futures; served by TWIME SPECTRA / Plaza II, not by ASTS MFIX",
    ),
}

# FIX 4.4 Tag 54 (Side) domain, per the MOEX interface specification.
_FIX_SIDE = {"BUY": "1", "SELL": "2"}

# FIX 4.4 Tag 40 (OrdType). MOEX also accepts 'W' (weighted-average price) on the
# Securities market; this module does not model its extra price semantics.
_FIX_ORD_TYPE = {"LIMIT": "2", "MARKET": "1"}

# FIX 4.4 Tag 59 (TimeInForce) values MOEX documents as valid.
_FIX_TIME_IN_FORCE = {"DAY": "0", "IOC": "3", "FOK": "4"}

# Field widths taken from the MOEX FIX 4.4 interface specification.
MAX_CL_ORD_ID_LEN = 20          # Tag 11, String(20)
MAX_ACCOUNT_LEN = 12            # Tag 1, String(12)
MAX_CLIENT_CODE_LEN = 12        # Tag 448, String(12)
MAX_SYMBOL_LEN = 12             # Tag 55, String(12)
MAX_TRADING_SESSION_ID_LEN = 4  # Tag 336, String(4)
MAX_PRICE_LEN = 10              # Tag 44, "including decimal point"
MAX_ORDER_QTY_LEN = 10          # Tag 38, Qty(10)

_TRANSACT_TIME_RE = re.compile(r"^\d{8}-\d{2}:\d{2}:\d{2}(\.\d{1,6})?$")

# FIX tag/value encoding is `tag=value<SOH>`. A value carrying either delimiter
# would split into fields the caller never wrote, so neither may reach the wire.
_FIX_DELIMITERS = ("\x01", "=")

# Outcome codes.
STATUS_VALIDATED = "MOEX_ORDER_VALIDATED"
STATUS_SANCTIONS_GATE_NOT_CLEARED = "MOEX_SANCTIONS_GATE_NOT_CLEARED"
STATUS_UNKNOWN_BOARD = "MOEX_UNKNOWN_BOARD"
STATUS_BOARD_NOT_ON_ASTS_MFIX = "MOEX_BOARD_NOT_ON_ASTS_MFIX"
STATUS_INSTRUMENT_BOARD_MISMATCH = "MOEX_INSTRUMENT_BOARD_MISMATCH"
STATUS_INVALID_LOT_QUANTITY = "MOEX_INVALID_LOT_QUANTITY"
STATUS_PRICE_STEP_BREACH = "MOEX_PRICE_STEP_BREACH"
STATUS_PRICE_LIMIT_BREACH = "MOEX_PRICE_LIMIT_BREACH"
STATUS_PRICE_POLICY_BREACH = "MOEX_PRICE_POLICY_BREACH"
STATUS_NO_PRICE_CONTROL = "MOEX_NO_PRICE_CONTROL"
STATUS_FIELD_LENGTH_BREACH = "MOEX_FIELD_LENGTH_BREACH"


def to_decimal(value: Number, field_name: str) -> Decimal:
    """Convert to ``Decimal`` without inheriting binary float error.

    Floats are routed through ``str`` so ``0.005`` means ``0.005`` and not
    ``0.005000000000000000104083408558``. Pass prices and steps as strings when
    exactness matters -- a price step of ``1e-05`` is a real MOEX value.
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


def _decimal_places(value: Decimal) -> int:
    """Number of decimal places in ``value`` once trailing zeros are stripped."""
    exponent = value.normalize().as_tuple().exponent
    return -exponent if isinstance(exponent, int) and exponent < 0 else 0


@dataclass(frozen=True)
class SanctionsScreening:
    """Record that a sanctions screening was performed for this order path.

    This is an audit artefact and a fail-closed gate, not a legal opinion. Moscow
    Exchange, National Clearing Center (its central counterparty) and the
    National Settlement Depository are designated on the OFAC SDN list under
    E.O. 14024 with secondary-sanctions risk; other jurisdictions maintain their
    own measures. ``cleared=True`` asserts only that the operator screened this
    order path against ``regimes`` on ``screened_on`` and concluded it may
    proceed.
    """

    cleared: bool
    regimes: Tuple[str, ...] = ()
    screened_on: Optional[date] = None
    reference: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.cleared, bool):
            raise TypeError("SanctionsScreening.cleared must be a bool")
        if not isinstance(self.regimes, tuple):
            raise TypeError("SanctionsScreening.regimes must be a tuple of strings")
        if self.screened_on is not None and not isinstance(self.screened_on, date):
            raise TypeError("SanctionsScreening.screened_on must be a datetime.date")
        if self.cleared:
            if not self.regimes:
                raise ValueError(
                    "a cleared screening must name at least one regime screened "
                    "against (e.g. ('OFAC-SDN', 'EU', 'UK-OFSI'))"
                )
            if self.screened_on is None:
                raise ValueError("a cleared screening must carry screened_on")

    def is_stale(self, as_of: Optional[date], max_age_days: Optional[int]) -> bool:
        """True when the screening is older than ``max_age_days`` at ``as_of``.

        ``max_age_days=None`` disables the check: no public rule fixes a
        re-screening cadence, so this module does not invent one. When a limit
        *is* configured, ``as_of`` is required -- the module reads no clock.
        """
        if max_age_days is None:
            return False
        if max_age_days < 0:
            raise ValueError("max_screening_age_days must be non-negative")
        if as_of is None:
            raise ValueError(
                "as_of is required when max_screening_age_days is configured"
            )
        if self.screened_on is None:
            return True
        return (as_of - self.screened_on).days > max_age_days


@dataclass(frozen=True)
class MOEXInstrument:
    """Reference data for one Symbol + Board combination.

    Field names mirror the MOEX ISS columns they come from so the provenance is
    visible at the call site. ``lot_size``, ``min_step`` and ``decimals`` have no
    defaults: TQBR lot sizes alone span 1 to 1,000,000 and price steps span 0.005
    to 0.5, so a default would be wrong far more often than right.

    ``low_limit`` / ``high_limit`` are the Exchange-published absolute price
    bounds. The FORTS board (RFUD) publishes them per instrument via ISS
    (``LOWLIMIT`` / ``HIGHLIMIT``); the equity and FX boards do not, which is
    why they are optional rather than required.
    """

    secid: str                       # ISS SECID -> FIX Tag 55 Symbol
    board: str                       # ISS BOARDID -> FIX Tag 336 TradingSessionID
    lot_size: int                    # ISS LOTSIZE: units of the security per lot
    min_step: Decimal                # ISS MINSTEP: minimum price increment
    decimals: int                    # ISS DECIMALS: price decimal places
    currency: str = "SUR"            # ISS CURRENCYID / FACEUNIT ('SUR', not 'RUB')
    low_limit: Optional[Decimal] = None   # ISS LOWLIMIT, where published
    high_limit: Optional[Decimal] = None  # ISS HIGHLIMIT, where published
    source: str = ""                 # where the row came from
    as_of: Optional[date] = None     # when it was retrieved

    def __post_init__(self) -> None:
        if not self.secid or not isinstance(self.secid, str):
            raise ValueError("secid must be a non-empty string")
        if not self.board or not isinstance(self.board, str):
            raise ValueError("board must be a non-empty string")
        if not isinstance(self.lot_size, int) or isinstance(self.lot_size, bool):
            raise TypeError("lot_size must be an int (units of security per lot)")
        if self.lot_size < 1:
            raise ValueError(f"lot_size must be >= 1, got {self.lot_size}")
        if not isinstance(self.decimals, int) or isinstance(self.decimals, bool):
            raise TypeError("decimals must be an int")
        if self.decimals < 0:
            raise ValueError(f"decimals must be >= 0, got {self.decimals}")

        object.__setattr__(self, "min_step", to_decimal(self.min_step, "min_step"))
        if self.min_step <= 0:
            raise ValueError(f"min_step must be > 0, got {self.min_step}")
        if _decimal_places(self.min_step) > self.decimals:
            raise ValueError(
                f"min_step {self.min_step} has more decimal places than "
                f"decimals={self.decimals}; the reference data is inconsistent"
            )
        for bound in ("low_limit", "high_limit"):
            value = getattr(self, bound)
            if value is not None:
                object.__setattr__(self, bound, to_decimal(value, bound))
        if (self.low_limit is not None and self.high_limit is not None
                and self.low_limit > self.high_limit):
            raise ValueError(
                f"low_limit {self.low_limit} exceeds high_limit {self.high_limit}"
            )

    @property
    def has_exchange_price_limits(self) -> bool:
        """True only when *both* published bounds are present."""
        return self.low_limit is not None and self.high_limit is not None

    def units_to_lots(self, units: int) -> int:
        """Convert a quantity in units of the security to whole lots.

        Raises when ``units`` is not a whole number of lots. MOEX Tag 38 carries
        lots, so a remainder here is a quantity the exchange cannot express.
        """
        if not isinstance(units, int) or isinstance(units, bool):
            raise TypeError("units must be an int")
        if units <= 0:
            raise ValueError(f"units must be > 0, got {units}")
        lots, remainder = divmod(units, self.lot_size)
        if remainder:
            raise ValueError(
                f"{units} units of {self.secid} is not a whole number of lots "
                f"(lot size {self.lot_size}); the nearest whole-lot quantities are "
                f"{lots * self.lot_size} and {(lots + 1) * self.lot_size} units"
            )
        return lots

    def is_on_step(self, price: Decimal) -> bool:
        """True when ``price`` is an exact multiple of the minimum price step."""
        return price % self.min_step == 0

    def is_within_exchange_limits(self, price: Number) -> Optional[bool]:
        """Test ``price`` against the Exchange-published bounds, inclusive.

        Returns ``None`` when the instrument carries no published band, so a
        caller can tell "no limit published" apart from "inside the limit".
        Useful directly on boards this module does not serialise for, such as
        RFUD.
        """
        if not self.has_exchange_price_limits:
            return None
        value = to_decimal(price, "price")
        return self.low_limit <= value <= self.high_limit

    def align_price_to_step(self, price: Number, side: str) -> Decimal:
        """Snap ``price`` to the price step, never in the aggressive direction.

        A BUY rounds **down** and a SELL rounds **up**, so alignment can only
        make the order less aggressive. This is opt-in: the engine rejects an
        off-step price rather than silently moving a caller's limit.
        """
        normalised_side = _normalise_side(side)
        value = to_decimal(price, "price")
        if value <= 0:
            raise ValueError(f"price must be > 0 to align, got {value}")
        rounding = ROUND_FLOOR if normalised_side == "BUY" else ROUND_CEILING
        steps = (value / self.min_step).to_integral_value(rounding=rounding)
        aligned = steps * self.min_step
        if aligned <= 0:
            raise ValueError(
                f"aligning {value} down to the {self.min_step} step yields "
                f"{aligned}, which is not a sendable price"
            )
        return aligned.quantize(self.min_step)

    def format_price(self, price: Decimal) -> str:
        """Render ``price`` at the instrument's DECIMALS for FIX Tag 44."""
        quantum = Decimal(1).scaleb(-self.decimals)
        return f"{price.quantize(quantum):f}"


@dataclass(frozen=True)
class MOEXSessionConfig:
    """Session-level identity for an MFIX order path.

    ``account`` is FIX Tag 1 (String(12)). ``client_code`` is carried in the
    ``<Parties>`` group as PartyID (448) with PartyIDSource (447) = 'D' and
    PartyRole (452) = '3'; MOEX notes it is meaningful only for a broker's
    client accounts.
    """

    account: str
    client_code: Optional[str] = None
    sanctions_screening: Optional[SanctionsScreening] = None
    max_screening_age_days: Optional[int] = None


@dataclass(frozen=True)
class MOEXOrderRequest:
    """One order to validate and serialise.

    ``cl_ord_id`` is supplied by the caller and never generated here: it is the
    idempotency key for the order's whole lifecycle, and a value derived from the
    order's own fields would collide the moment the same order is sent twice.

    Exactly one of ``quantity_lots`` and ``quantity_units`` must be given.
    ``reference_price`` and ``max_price_deviation`` together declare a
    **client-side policy band**; they are not a MOEX rule.
    """

    cl_ord_id: str
    secid: str
    board: str
    side: str                                   # 'BUY' or 'SELL'
    quantity_lots: Optional[int] = None
    quantity_units: Optional[int] = None
    price: Optional[Number] = None              # required for LIMIT
    ord_type: str = "LIMIT"                     # 'LIMIT' or 'MARKET'
    time_in_force: Optional[str] = None         # 'DAY' | 'IOC' | 'FOK'
    reference_price: Optional[Number] = None
    max_price_deviation: Optional[Number] = None
    transact_time: Optional[str] = None         # FIX UTCTimestamp, if pre-stamped


@dataclass
class MOEXOrderReport:
    """Outcome of the pre-dispatch checks for one order."""

    cl_ord_id: str
    secid: str
    board: str
    side: str
    status: str
    ready_to_send: bool
    audit_notes: str
    quantity_lots: Optional[int] = None
    quantity_units: Optional[int] = None
    price: Optional[Decimal] = None
    price_control: str = "NONE"     # 'EXCHANGE_LIMITS' | 'CLIENT_POLICY' | ...
    fix_fields: List[Tuple[int, str]] = field(default_factory=list)

    @property
    def fix_field_map(self) -> Dict[int, str]:
        """Tag -> value view. Loses the repeating-group ordering; do not send it."""
        return {tag: value for tag, value in self.fix_fields}


def _normalise_side(side: str) -> str:
    if not isinstance(side, str):
        raise TypeError(f"side must be a string, got {type(side).__name__}")
    normalised = side.strip().upper()
    if normalised not in _FIX_SIDE:
        raise ValueError(f"side must be 'BUY' or 'SELL', got {side!r}")
    return normalised


class MOEXApiIntegrationEngine:
    """Pre-dispatch validation and MFIX field construction for MOEX orders.

    The engine is stateless and deterministic: it reads no clock and performs no
    I/O. Every date it needs is passed in, so a report is reproducible from its
    inputs alone.
    """

    def validate_and_serialize_order(
        self,
        config: MOEXSessionConfig,
        order: MOEXOrderRequest,
        instrument: MOEXInstrument,
        as_of: Optional[date] = None,
    ) -> MOEXOrderReport:
        """Run the pre-dispatch checks and build the MFIX NewOrderSingle body.

        ``as_of`` is the date the screening staleness check is evaluated at; it
        is required only when ``config.max_screening_age_days`` is set.

        Returns a report. Malformed inputs -- a non-numeric price, a bad side, a
        fractional lot count -- raise ``ValueError``/``TypeError`` rather than
        being reported as a rejection, because they are caller bugs and not order
        outcomes.
        """
        side = _normalise_side(order.side)
        board = order.board

        def reject(status: str, notes: str, **kwargs) -> MOEXOrderReport:
            logger.warning("%s [%s/%s]: %s", status, order.secid, board, notes)
            return MOEXOrderReport(
                cl_ord_id=order.cl_ord_id, secid=order.secid, board=board,
                side=side, status=status, ready_to_send=False,
                audit_notes=notes, **kwargs
            )

        # 1. Sanctions gate. Fails closed: an absent attestation is not clearance.
        screening = config.sanctions_screening
        if screening is None or not screening.cleared:
            return reject(
                STATUS_SANCTIONS_GATE_NOT_CLEARED,
                "no cleared sanctions screening is attached to the session. Moscow "
                "Exchange and National Clearing Center are designated on the OFAC "
                "SDN list under E.O. 14024; the order path must be screened and "
                "the result attached before any order is built.",
            )
        if screening.is_stale(as_of, config.max_screening_age_days):
            return reject(
                STATUS_SANCTIONS_GATE_NOT_CLEARED,
                f"sanctions screening dated {screening.screened_on} is older than "
                f"the configured {config.max_screening_age_days}-day limit at "
                f"{as_of}; re-screen before sending.",
            )

        # 2. Board resolution and instrument/board agreement.
        if board not in MOEX_BOARDS:
            return reject(
                STATUS_UNKNOWN_BOARD,
                f"board {board!r} is not in the known board registry "
                f"({', '.join(sorted(MOEX_BOARDS))}). Resolve it against ISS "
                f"/iss/index.json before routing.",
            )
        board_meta = MOEX_BOARDS[board]
        if instrument.board != board:
            return reject(
                STATUS_INSTRUMENT_BOARD_MISMATCH,
                f"reference data is for board {instrument.board!r} but the order "
                f"targets {board!r}. Lot size and price step are per Symbol+Board, "
                f"so the wrong row silently mis-sizes the order.",
            )
        if instrument.secid != order.secid:
            return reject(
                STATUS_INSTRUMENT_BOARD_MISMATCH,
                f"reference data is for {instrument.secid!r} but the order is for "
                f"{order.secid!r}.",
            )
        if not board_meta.asts_mfix:
            return reject(
                STATUS_BOARD_NOT_ON_ASTS_MFIX,
                f"board {board} is served by the {board_meta.trading_system} "
                f"trading system. The MOEX public FIX 4.4 interface specification "
                f"covers the FX and Securities markets only; this board needs its "
                f"own interface (TWIME SPECTRA / Plaza II) and its own message "
                f"layout, which this module does not build.",
            )

        # 3. Quantity, in lots.
        if (order.quantity_lots is None) == (order.quantity_units is None):
            raise ValueError("supply exactly one of quantity_lots or quantity_units")
        if order.quantity_units is not None:
            lots = instrument.units_to_lots(order.quantity_units)
        else:
            lots = order.quantity_lots
            if not isinstance(lots, int) or isinstance(lots, bool):
                raise TypeError("quantity_lots must be an int")
        if lots <= 0:
            return reject(
                STATUS_INVALID_LOT_QUANTITY,
                f"quantity resolves to {lots} lots; MOEX Tag 38 requires a "
                f"positive lot count.",
            )
        units = lots * instrument.lot_size
        if len(str(lots)) > MAX_ORDER_QTY_LEN:
            return reject(
                STATUS_FIELD_LENGTH_BREACH,
                f"OrderQty {lots} exceeds Tag 38 Qty({MAX_ORDER_QTY_LEN}).",
                quantity_lots=lots, quantity_units=units,
            )

        # 4. Price: type, step alignment, and limits.
        ord_type = order.ord_type.strip().upper()
        if ord_type not in _FIX_ORD_TYPE:
            raise ValueError(
                f"ord_type must be one of {sorted(_FIX_ORD_TYPE)}, "
                f"got {order.ord_type!r}"
            )
        price: Optional[Decimal] = None
        price_control = "NOT_APPLICABLE"

        if ord_type == "MARKET":
            if order.price is not None:
                raise ValueError(
                    "a MARKET order must not carry a price; MOEX requires Tag 44 "
                    "to be zero for market orders and this module emits that"
                )
        else:
            if order.price is None:
                raise ValueError("a LIMIT order requires a price")
            price = to_decimal(order.price, "price")
            if price <= 0:
                return reject(
                    STATUS_PRICE_STEP_BREACH,
                    f"price {price} must be > 0. A negative price is an exact "
                    f"multiple of the price step, so positivity is checked "
                    f"separately from alignment.",
                    quantity_lots=lots, quantity_units=units, price=price,
                )
            if not instrument.is_on_step(price):
                return reject(
                    STATUS_PRICE_STEP_BREACH,
                    f"price {price} is not a multiple of the {instrument.secid} "
                    f"price step {instrument.min_step}; MOEX rejects orders whose "
                    f"price does not fit the minimal price step levels. Use "
                    f"align_price_to_step() to snap it away from the market.",
                    quantity_lots=lots, quantity_units=units, price=price,
                )

            control = self._check_price_limits(instrument, order, price)
            if control is None:
                return reject(
                    STATUS_NO_PRICE_CONTROL,
                    f"no price control is available for {instrument.secid}: the "
                    f"instrument carries no Exchange-published low/high limit and "
                    f"the order declares no reference_price + max_price_deviation "
                    f"policy band. Refusing to build a limit order with no price "
                    f"sanity check.",
                    quantity_lots=lots, quantity_units=units, price=price,
                )
            breach_status, breach_notes, price_control = control
            if breach_status is not None:
                return reject(
                    breach_status, breach_notes, quantity_lots=lots,
                    quantity_units=units, price=price, price_control=price_control,
                )

        # 5. Field widths, then the MFIX body.
        length_error = self._check_field_widths(config, order, instrument, price)
        if length_error is not None:
            return reject(
                STATUS_FIELD_LENGTH_BREACH, length_error,
                quantity_lots=lots, quantity_units=units, price=price,
                price_control=price_control,
            )

        fix_fields = self._build_fix_fields(
            config, order, instrument, side, ord_type, lots, price
        )
        priced = (f"at {instrument.format_price(price)} {instrument.currency}"
                  if price is not None else "as a market order")
        notes = (
            f"{order.secid} {side} {lots} lot(s) = {units} unit(s) on {board} "
            f"({board_meta.trading_system}) {priced}; price control: "
            f"{price_control}. Client-side checks only -- MOEX has not seen this "
            f"order."
        )
        logger.info("%s: %s", STATUS_VALIDATED, notes)
        return MOEXOrderReport(
            cl_ord_id=order.cl_ord_id, secid=order.secid, board=board, side=side,
            status=STATUS_VALIDATED, ready_to_send=True, audit_notes=notes,
            quantity_lots=lots, quantity_units=units, price=price,
            price_control=price_control, fix_fields=fix_fields,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _check_price_limits(
        self,
        instrument: MOEXInstrument,
        order: MOEXOrderRequest,
        price: Decimal,
    ) -> Optional[Tuple[Optional[str], str, str]]:
        """Apply the Exchange-published limits, else the caller's policy band.

        Returns ``None`` when neither control is available, otherwise
        ``(breach_status_or_None, notes, price_control)``.
        """
        if instrument.has_exchange_price_limits:
            low, high = instrument.low_limit, instrument.high_limit
            if price < low or price > high:
                return (
                    STATUS_PRICE_LIMIT_BREACH,
                    f"price {price} is outside the Exchange-published limits "
                    f"[{low}, {high}] for {instrument.secid} (source: "
                    f"{instrument.source or 'unspecified'}, as of "
                    f"{instrument.as_of or 'unspecified'}). These bounds are "
                    f"absolute and per instrument -- they are not a fixed "
                    f"percentage of any reference price.",
                    "EXCHANGE_LIMITS",
                )
            return (None, "", "EXCHANGE_LIMITS")

        if order.reference_price is None or order.max_price_deviation is None:
            return None

        reference = to_decimal(order.reference_price, "reference_price")
        deviation_limit = to_decimal(order.max_price_deviation, "max_price_deviation")
        if reference <= 0:
            raise ValueError(
                f"reference_price must be > 0 to form a policy band, got {reference}"
            )
        if deviation_limit <= 0:
            raise ValueError(f"max_price_deviation must be > 0, got {deviation_limit}")
        deviation = abs(price - reference) / reference
        if deviation > deviation_limit:
            return (
                STATUS_PRICE_POLICY_BREACH,
                f"price {price} deviates {deviation:.4%} from the caller's "
                f"reference {reference}, exceeding the declared client-side policy "
                f"band of {deviation_limit:.4%}. This band is the caller's own risk "
                f"limit, not a MOEX rule.",
                "CLIENT_POLICY",
            )
        return (None, "", "CLIENT_POLICY")

    def _check_field_widths(
        self,
        config: MOEXSessionConfig,
        order: MOEXOrderRequest,
        instrument: MOEXInstrument,
        price: Optional[Decimal],
    ) -> Optional[str]:
        """Enforce the String(n) widths the MOEX FIX specification documents."""
        for label, value in (
            ("ClOrdID", order.cl_ord_id),
            ("Account", config.account),
            ("client code", config.client_code),
            ("Symbol", instrument.secid),
            ("TradingSessionID", order.board),
            ("TransactTime", order.transact_time),
        ):
            if value is None:
                continue
            for delimiter in _FIX_DELIMITERS:
                if delimiter in value:
                    return (
                        f"{label} {value!r} contains a FIX delimiter "
                        f"({delimiter!r}); it would split the message into fields "
                        f"that were never written."
                    )
        if not order.cl_ord_id:
            return "cl_ord_id is required; it is the order's idempotency key."
        if len(order.cl_ord_id) > MAX_CL_ORD_ID_LEN:
            return (
                f"ClOrdID {order.cl_ord_id!r} is {len(order.cl_ord_id)} characters; "
                f"Tag 11 is String({MAX_CL_ORD_ID_LEN})."
            )
        if order.cl_ord_id.startswith("#"):
            return (
                "ClOrdID must not start with '#': MOEX rejects Order Cancel and "
                "Order Cancel/Replace requests whose ClOrdID does, which would "
                "leave this order uncancellable by client order ID."
            )
        if not config.account or len(config.account) > MAX_ACCOUNT_LEN:
            return (
                f"Account {config.account!r} must be 1..{MAX_ACCOUNT_LEN} "
                f"characters (Tag 1 String({MAX_ACCOUNT_LEN}))."
            )
        if (config.client_code is not None
                and len(config.client_code) > MAX_CLIENT_CODE_LEN):
            return (
                f"client code {config.client_code!r} exceeds Tag 448 "
                f"String({MAX_CLIENT_CODE_LEN})."
            )
        if len(instrument.secid) > MAX_SYMBOL_LEN:
            return (
                f"Symbol {instrument.secid!r} exceeds Tag 55 "
                f"String({MAX_SYMBOL_LEN})."
            )
        if len(order.board) > MAX_TRADING_SESSION_ID_LEN:
            return (
                f"TradingSessionID {order.board!r} exceeds Tag 336 "
                f"String({MAX_TRADING_SESSION_ID_LEN})."
            )
        if price is not None:
            rendered = instrument.format_price(price)
            if len(rendered) > MAX_PRICE_LEN:
                return (
                    f"price renders as {rendered!r} ({len(rendered)} characters); "
                    f"MOEX caps Tag 44 at {MAX_PRICE_LEN} characters including the "
                    f"decimal point."
                )
        if (order.transact_time is not None
                and not _TRANSACT_TIME_RE.match(order.transact_time)):
            return (
                f"transact_time {order.transact_time!r} is not a FIX UTCTimestamp "
                f"(YYYYMMDD-HH:MM:SS[.sss])."
            )
        if (order.time_in_force is not None
                and order.time_in_force.strip().upper() not in _FIX_TIME_IN_FORCE):
            return (
                f"time_in_force must be one of {sorted(_FIX_TIME_IN_FORCE)}, "
                f"got {order.time_in_force!r}"
            )
        return None

    def _build_fix_fields(
        self,
        config: MOEXSessionConfig,
        order: MOEXOrderRequest,
        instrument: MOEXInstrument,
        side: str,
        ord_type: str,
        lots: int,
        price: Optional[Decimal],
    ) -> List[Tuple[int, str]]:
        """Build the NewOrderSingle (35=D) **body** fields, in specification order.

        Ordering matters: MOEX states that "tags 386 and 336 compose a group and
        should be placed exactly in the order 386, then 336, and not separated by
        other tags". The returned list preserves that; the ``fix_field_map``
        convenience view does not, and must not be used to build a wire message.

        The standard header and trailer -- BeginString, BodyLength, MsgType,
        SenderCompID, TargetCompID, MsgSeqNum, CheckSum -- belong to the FIX
        session layer and are deliberately absent.
        """
        fields: List[Tuple[int, str]] = [(11, order.cl_ord_id)]

        if config.client_code:
            # <Parties>: PartyIDSource 'D' (proprietary), PartyRole '3' (client).
            fields.extend([
                (453, "1"),
                (448, config.client_code),
                (447, "D"),
                (452, "3"),
            ])

        fields.append((1, config.account))
        # NoTradingSessions carries exactly one element, and 386 must be
        # immediately followed by 336 with nothing between them.
        fields.append((386, "1"))
        fields.append((336, order.board))
        fields.append((55, instrument.secid))
        fields.append((54, _FIX_SIDE[side]))
        if order.transact_time is not None:
            fields.append((60, order.transact_time))
        fields.append((38, str(lots)))
        fields.append((40, _FIX_ORD_TYPE[ord_type]))
        # MOEX requires Tag 44 to be zero for market orders rather than absent.
        fields.append((44, instrument.format_price(
            price if price is not None else Decimal(0))))
        if order.time_in_force is not None:
            fields.append(
                (59, _FIX_TIME_IN_FORCE[order.time_in_force.strip().upper()])
            )
        return fields

"""HKEX securities-market pre-trade order validation: stock code, spread table, board lot.

This module validates an order *before* it is handed to an HKEX Orion Central Gateway
- Securities Market (OCG-C) session. It is a rulebook validator, not a gateway: it
opens no sockets and speaks neither the OCG-C binary nor the OCG-C FIX protocol.

Scope and sourcing (verified 2026-08-25 against HKEX primary sources):

* **Spread tables - SEHK Rules of the Exchange, Second Schedule.** The Second Schedule
  is not one table. Part A covers all securities except those in Parts B-E; Part B
  covers debt securities and Exchange-authorised securities (a flat 0.050); Part C
  defers Exchange Traded Options to the Operational Trading Procedures; Part D covers
  Exchange Traded Funds; Part E covers Structured Products (DW, CBBC, Inline Warrants).
  Applying Part A to an ETF or a CBBC produces a wrong tick, so the caller must say
  which Part applies - see ``SpreadTable`` and ``SpreadTable.from_omd_c_code``.

* **Band boundaries are upper-INCLUSIVE.** The Schedule reads "From 0.01 to 0.25",
  "Over 0.25 to 10.00", ... A price of exactly 500.00 sits in the "Over 200.00 to
  500.00" band and ticks at 0.200, *not* at the 0.500 of the band above it. Every
  band edge is a boundary an upper-exclusive comparison gets wrong.

* **Part A reflects the Reduction of Minimum Spreads, Phases 1 and 2.** Phase 1
  (launched 2025-08-04) cut 10.00-20.00 from 0.020 to 0.010 and split the old
  20.00-100.00 band into 20.00-50.00 at 0.020 and 50.00-100.00 at 0.050. Phase 2
  (launched 2026-08-03) merged 0.50-10.00 into the 0.25-10.00 band at 0.005. Both
  phases apply to "Applicable Securities" only - Structured Products were carved out
  of code 01 into the new spread table code 06, whose bands are the pre-Phase-1
  Part A bands and are reproduced here as ``SpreadTable.PART_E``.

* **The security's spread table is reference data, not an inference.** OMD-C and
  OMD-C MMDH publish a "Spread Table Code" per security in the Security Definition
  (11) message. HKEX documents code 01 (Part A) and code 06 (Part E, introduced at
  Phase 1); codes 03, 04 and 05 exist for debt securities, ETOs and ETPs but HKEX's
  published FAQ does not state which numeric code is which, so this module refuses to
  guess - map them from the OMD-C interface specification you are actually running.

* **Board lot is issuer-set, and non-multiples do not auto-match.** HKEX board lot
  sizes are set by the issuer and range from 10 to 100,000 shares; there is no
  market-wide 100. A quantity below one board lot is an *odd lot*; a quantity above
  one board lot that is not an integral multiple is a *special lot*. Neither is
  accepted by the auto-matching book - both go to the semi-automatic odd/special lot
  facility - so both are rejected here rather than silently routed.

* **Maximum order size is 3,000 board lots** for automatch stocks, in all trading
  sessions.

Deliberately NOT implemented (see SKILL.md "When NOT to Use"): the 24-spreads opening
quotation rule and the 9-times-nominal-price rule. Both need the security's nominal /
previous closing price, which is market data this module does not take.

Sources:
  SEHK Rules of the Exchange, Second Schedule (Spread Table)
    https://www.hkex.com.hk/-/media/HKEX-Market/Services/Rules-and-Forms-and-Fees/Rules/SEHK/Securities/Rules/Sch_2_eng.pdf
  Reduction of Minimum Spreads FAQ - Phase 1 (spread table codes 01/03/04/05/06)
    https://www.hkex.com.hk/-/media/HKEX-Market/Services/Trading/Securities/Overview/Trading-Mechanism/Reduction-of-Minimum-Spreads/Reduction-of-Minimum-Spreads-FAQ_E.pdf
  Reduction of Minimum Spreads FAQ - Phase 2
    https://www.hkex.com.hk/-/media/HKEX-Market/Services/Trading/Securities/Overview/Trading-Mechanism/Reduction-of-Minimum-Spreads/Reduction-of-Minimum-Spreads-FAQP2_E.pdf
  HKEX Trading Mechanism (order types, max order size, odd/special lots)
    https://www.hkex.com.hk/Services/Trading/Securities/Overview/Trading-Mechanism?sc_lang=en
  HKD-RMB Dual Counter Model FAQ (0XXXX / 8XXXX counter codes)
    https://www.hkex.com.hk/-/media/HKEX-Market/Services/Trading/Securities/Overview/Trading-Mechanism/HKDRMB-Dual-Counter/Dual-Counter-Model-FAQ_E.pdf
  HKEX Orion Central Gateway - Securities Market (OCG-C), order entry
    https://www.hkex.com.hk/Services/Trading/Securities/Infrastructure/OCGC?sc_lang=en
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import List, Mapping, Tuple, Union

logger = logging.getLogger(__name__)

#: Any numeric form accepted for a price. ``str``, ``int`` and ``Decimal`` are exact;
#: a ``float`` is read through its shortest round-tripping repr, so ``0.005`` reads as
#: ``Decimal("0.005")`` and not as the binary value actually stored.
PriceInput = Union[Decimal, float, int, str]

STATUS_VALIDATED = "ORDER_VALIDATED"
STATUS_INVALID_TICK = "INVALID_TICK_SIZE"
STATUS_INVALID_BOARD_LOT = "INVALID_BOARD_LOT"
STATUS_INVALID_ORDER_SIZE = "INVALID_ORDER_SIZE"

#: HKEX auto-matching cap on a single order, in board lots, for all trading sessions.
MAX_ORDER_SIZE_BOARD_LOTS = 3_000

#: Sanity bounds on the issuer-set board lot size, not an HKEX rule. Board lots
#: observed on HKEX span 10 to 100,000 shares; the floor is kept at 1 rather than 10 so
#: a legitimately small lot is not rejected on an unverified assumption. There is no
#: market-wide default, which is why ``HkexOrderPayload.board_lot_size`` is required.
MIN_BOARD_LOT_SIZE = 1
MAX_BOARD_LOT_SIZE = 100_000

_D = Decimal


class HkexOrderError(ValueError):
    """Raised for an order that cannot be evaluated against HKEX rules at all."""


class InvalidStockCodeError(HkexOrderError):
    """Raised when a stock code cannot be a valid HKEX 5-digit security code."""


class PriceOutOfRangeError(HkexOrderError):
    """Raised when a price falls outside its spread table's published price bands.

    This is not the same as an off-tick price. A price below the table's floor or
    above its ceiling has *no* applicable minimum spread at all, so there is no tick
    to validate against and no safe value to assume.
    """


class SpreadTableUnavailableError(HkexOrderError):
    """Raised for a Second Schedule Part whose scale is not published as a table."""


class SpreadTable(str, Enum):
    """Which Part of the SEHK Second Schedule governs a security's minimum spread.

    Determine this from the security's OMD-C **Spread Table Code** (Security
    Definition (11) message), not from the ticker or the price.
    """

    #: Part A - all securities other than those in Parts B-E. OMD-C spread table code 01.
    PART_A = "PART_A"
    #: Part B - Exchange-authorised securities and all debt securities. Flat 0.050.
    PART_B = "PART_B"
    #: Part C - Exchange Traded Options. Scale lives in the Operational Trading
    #: Procedures, not the Second Schedule; not reproduced here.
    PART_C = "PART_C"
    #: Part D - Exchange Traded Funds other than those covered under Part B.
    PART_D = "PART_D"
    #: Part E - Structured Products (DW, CBBC, Inline Warrants). OMD-C code 06.
    PART_E = "PART_E"

    @classmethod
    def from_omd_c_code(cls, spread_table_code: str) -> "SpreadTable":
        """Map a published OMD-C Spread Table Code to a Second Schedule Part.

        Only codes HKEX has published a mapping for are accepted. Codes 03, 04 and 05
        are in use for debt securities, Exchange Traded Options and Exchange Traded
        Products, but HKEX's Reduction of Minimum Spreads FAQ does not state which
        numeric code is which - so this raises rather than guessing a tick table.
        """
        code = str(spread_table_code).strip().zfill(2)
        try:
            return _OMD_C_CODE_TO_TABLE[code]
        except KeyError:
            raise SpreadTableUnavailableError(
                f"OMD-C spread table code '{spread_table_code}' has no HKEX-published "
                f"mapping to a Second Schedule Part. Published: "
                f"{sorted(_OMD_C_CODE_TO_TABLE)}. Resolve codes 03/04/05 from the "
                f"OMD-C interface specification for your feed and pass the "
                f"SpreadTable explicitly."
            ) from None


class LotClassification(str, Enum):
    """How HKEX treats the order quantity relative to one board lot."""

    #: An integral multiple of the board lot - eligible for the auto-matching book.
    BOARD_LOT = "BOARD_LOT"
    #: Fewer shares than one board lot - odd lot, semi-automatic facility only.
    ODD_LOT = "ODD_LOT"
    #: More than one board lot but not an integral multiple - special lot.
    SPECIAL_LOT = "SPECIAL_LOT"


class CounterType(str, Enum):
    """Which Dual Counter leg a 5-digit code addresses."""

    HKD_COUNTER = "HKD_COUNTER"
    RMB_COUNTER = "RMB_COUNTER"
    #: A valid 5-digit code outside the 0XXXX / 8XXXX Dual Counter convention.
    OTHER = "OTHER"


#: Second Schedule spread bands, as ``(upper_bound_inclusive, minimum_spread)`` in
#: ascending order, paired with the inclusive floor of the first band. Boundaries are
#: upper-inclusive: the Schedule reads "Over 200.00 to 500.00", so 500.00 is a Part A
#: 0.200 price, not a 0.500 one.
_SPREAD_BANDS: Mapping[SpreadTable, Tuple[Decimal, Tuple[Tuple[Decimal, Decimal], ...]]] = {
    # Part A, current text (post Phase 1 2025-08-04 and Phase 2 2026-08-03).
    SpreadTable.PART_A: (
        _D("0.01"),
        (
            (_D("0.25"), _D("0.001")),
            (_D("10.00"), _D("0.005")),
            (_D("20.00"), _D("0.010")),
            (_D("50.00"), _D("0.020")),
            (_D("100.00"), _D("0.050")),
            (_D("200.00"), _D("0.100")),
            (_D("500.00"), _D("0.200")),
            (_D("1000.00"), _D("0.500")),
            (_D("2000.00"), _D("1.000")),
            (_D("5000.00"), _D("2.000")),
            (_D("9995.00"), _D("5.000")),
        ),
    ),
    # Part B - "From 0.50 to 9,999.95 ... 0.050".
    SpreadTable.PART_B: (
        _D("0.50"),
        ((_D("9999.95"), _D("0.050")),),
    ),
    # Part D - Exchange Traded Funds other than those covered under Part B.
    SpreadTable.PART_D: (
        _D("0.01"),
        (
            (_D("1.00"), _D("0.001")),
            (_D("5.00"), _D("0.002")),
            (_D("10.00"), _D("0.005")),
            (_D("20.00"), _D("0.010")),
            (_D("100.00"), _D("0.020")),
            (_D("200.00"), _D("0.050")),
            (_D("500.00"), _D("0.100")),
            (_D("1000.00"), _D("0.200")),
            (_D("2000.00"), _D("0.500")),
            (_D("9999.00"), _D("1.000")),
        ),
    ),
    # Part E - Structured Products. Identical to OMD-C spread table code 06.
    SpreadTable.PART_E: (
        _D("0.01"),
        (
            (_D("0.25"), _D("0.001")),
            (_D("0.50"), _D("0.005")),
            (_D("10.00"), _D("0.010")),
            (_D("20.00"), _D("0.020")),
            (_D("100.00"), _D("0.050")),
            (_D("200.00"), _D("0.100")),
            (_D("500.00"), _D("0.200")),
            (_D("1000.00"), _D("0.500")),
            (_D("2000.00"), _D("1.000")),
            (_D("5000.00"), _D("2.000")),
            (_D("9995.00"), _D("5.000")),
        ),
    ),
}

_OMD_C_CODE_TO_TABLE: Mapping[str, SpreadTable] = {
    "01": SpreadTable.PART_A,
    "06": SpreadTable.PART_E,
}

_VALID_SIDES = frozenset({"BUY", "SELL"})
#: HKEX continuous-session and auction order types. Not exhaustive of every OCG-C
#: enumeration - it is the set this validator recognises by name.
_VALID_ORDER_TYPES = frozenset(
    {"LIMIT", "ENHANCED_LIMIT", "SPECIAL_LIMIT", "AT_AUCTION", "AT_AUCTION_LIMIT"}
)
_VALID_CURRENCIES = frozenset({"HKD", "RMB", "CNY", "USD"})


def _to_decimal(value: PriceInput, label: str) -> Decimal:
    """Convert a price-like input to Decimal exactly, rejecting NaN and infinity.

    ``float`` goes through ``str`` deliberately: ``Decimal(0.005)`` is
    ``0.005000000000000000104083408558...`` and would never be an exact tick multiple.
    """
    try:
        result = _D(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError, TypeError):
        raise HkexOrderError(f"{label} '{value!r}' is not a valid decimal number.") from None
    if not result.is_finite():
        raise HkexOrderError(f"{label} '{value!r}' is not finite.")
    return result


@dataclass
class HkexOrderPayload:
    """An order as the caller holds it, before HKEX rulebook validation."""

    raw_stock_code: str                          # e.g. '700', '00700' or '80700'
    side: str                                    # 'BUY' or 'SELL'
    order_type: str                              # 'LIMIT', 'ENHANCED_LIMIT', ...
    price: PriceInput                            # e.g. '300.20' or Decimal('300.20')
    quantity: int                                # in shares, not board lots
    board_lot_size: int                          # issuer-set; 00700 is 100
    currency: str = "HKD"                        # 'HKD' or 'RMB'
    #: Second Schedule Part governing this security. Derive it from the OMD-C Spread
    #: Table Code via ``SpreadTable.from_omd_c_code`` - do not infer it from the price.
    spread_table: SpreadTable = SpreadTable.PART_A


@dataclass
class HkexOrionOrderReport:
    """The outcome of validating one order against the HKEX rulebook."""

    formatted_stock_code: str                    # 5-digit zero-padded, e.g. '00700'
    counter_type: CounterType
    currency: str
    price: Decimal
    spread_table: SpreadTable
    applicable_tick_size: Decimal                # e.g. Decimal('0.200') for 300.20
    is_price_tick_valid: bool
    quantity: int
    board_lot_size: int
    lot_classification: LotClassification
    is_board_lot_multiple: bool
    order_size_board_lots: Decimal               # quantity / board_lot_size
    is_order_size_valid: bool                    # <= 3,000 board lots
    status: str
    #: Every rule violated, in the precedence order used to pick ``status``. Empty on
    #: a validated order. Read this rather than ``status`` when reporting to a human:
    #: an order can breach the spread table *and* the board lot rule at once.
    violations: Tuple[str, ...] = field(default_factory=tuple)
    audit_notes: str = ""


class HkexOrionApiEngine:
    """Validates orders against the HKEX securities-market rulebook before OCG-C entry.

    Stateless and side-effect free apart from logging: one instance is safe to share
    across threads and strategies.
    """

    def format_hkex_stock_code(self, raw_code: str) -> str:
        """Zero-pad a numeric stock code to the HKEX 5-digit form ('700' -> '00700').

        Rejects a code that cannot be a valid HKEX security code: non-numeric, longer
        than five digits, or all zeros. ``zfill`` alone silently passes '123456'
        straight through to the gateway.
        """
        clean_code = str(raw_code).strip()
        if not clean_code or not clean_code.isascii() or not clean_code.isdigit():
            raise InvalidStockCodeError(
                f"Invalid HKEX stock code '{raw_code}'. Code must be ASCII digits only."
            )
        if len(clean_code) > 5:
            raise InvalidStockCodeError(
                f"Invalid HKEX stock code '{raw_code}'. HKEX security codes are at most "
                f"5 digits; got {len(clean_code)}."
            )
        padded = clean_code.zfill(5)
        if padded == "00000":
            raise InvalidStockCodeError("Invalid HKEX stock code '00000'.")
        return padded

    def classify_counter(self, formatted_code: str) -> CounterType:
        """Classify a 5-digit code as the HKD or RMB leg of the Dual Counter Model.

        Under the HKD-RMB Dual Counter Model an equity's HKD counter is ``0XXXX`` and
        its RMB counter is ``8XXXX``, normally sharing the last four digits. Codes
        outside that convention (warrants, CBBCs, debt) classify as ``OTHER`` - this
        is a labelling aid, not a substitute for the Dual Counter Securities list.
        """
        if formatted_code.startswith("0"):
            return CounterType.HKD_COUNTER
        if formatted_code.startswith("8"):
            return CounterType.RMB_COUNTER
        return CounterType.OTHER

    def get_hkex_spread_table_tick_size(
        self,
        price: PriceInput,
        spread_table: SpreadTable = SpreadTable.PART_A,
    ) -> Decimal:
        """Return the minimum spread for ``price`` under a Second Schedule Part.

        Band boundaries are **upper-inclusive**, matching the Schedule's "Over X to Y"
        wording: 500.00 under Part A is a 0.200 price, and 500.01 is a 0.500 price.

        Raises ``PriceOutOfRangeError`` outside the table's published bands - Part A
        stops at 9,995.00 and there is no minimum spread beyond it to fall back on.
        """
        if spread_table is SpreadTable.PART_C:
            raise SpreadTableUnavailableError(
                "Second Schedule Part C defers Exchange Traded Options to the "
                "Operational Trading Procedures; no spread scale is published in the "
                "Second Schedule and none is assumed here."
            )
        try:
            floor, bands = _SPREAD_BANDS[spread_table]
        except KeyError:
            raise SpreadTableUnavailableError(
                f"No spread band table for {spread_table!r}."
            ) from None

        decimal_price = _to_decimal(price, "Price")
        ceiling = bands[-1][0]
        if decimal_price < floor or decimal_price > ceiling:
            raise PriceOutOfRangeError(
                f"Price {decimal_price} is outside the {spread_table.value} price bands "
                f"({floor} to {ceiling}); no minimum spread applies."
            )

        for upper_inclusive, tick in bands:
            if decimal_price <= upper_inclusive:
                return tick
        # Unreachable: the ceiling check above already bounds decimal_price.
        raise PriceOutOfRangeError(f"Price {decimal_price} matched no spread band.")

    def classify_lot(self, quantity: int, board_lot_size: int) -> LotClassification:
        """Classify a share quantity against the security's board lot size.

        Guards its own inputs because it is part of the public surface: a zero lot size
        would raise ``ZeroDivisionError`` and a negative one makes ``quantity % lot``
        zero for every quantity, so a direct caller would be told an order is a clean
        board lot when it is not.
        """
        if quantity <= 0:
            raise HkexOrderError(f"Quantity {quantity} must be positive.")
        if board_lot_size <= 0:
            raise HkexOrderError(f"Board lot size {board_lot_size} must be positive.")
        if quantity % board_lot_size == 0:
            return LotClassification.BOARD_LOT
        if quantity < board_lot_size:
            return LotClassification.ODD_LOT
        return LotClassification.SPECIAL_LOT

    def validate_and_prepare_order(self, payload: HkexOrderPayload) -> HkexOrionOrderReport:
        """Validate one order against stock code, spread table, board lot and size rules.

        Unrecoverable inputs raise ``HkexOrderError`` - a malformed stock code or a
        price with no applicable spread cannot be expressed as a rule violation,
        because there is no rule to test it against. Rule *breaches* are returned in
        the report so the caller can log and route them.
        """
        formatted_code = self.format_hkex_stock_code(payload.raw_stock_code)
        counter_type = self.classify_counter(formatted_code)

        side = str(payload.side).strip().upper()
        if side not in _VALID_SIDES:
            raise HkexOrderError(
                f"Invalid side '{payload.side}'. Expected one of {sorted(_VALID_SIDES)}."
            )
        order_type = str(payload.order_type).strip().upper()
        if order_type not in _VALID_ORDER_TYPES:
            raise HkexOrderError(
                f"Invalid order type '{payload.order_type}'. Expected one of "
                f"{sorted(_VALID_ORDER_TYPES)}."
            )
        currency = str(payload.currency).strip().upper()
        if currency not in _VALID_CURRENCIES:
            raise HkexOrderError(
                f"Invalid currency '{payload.currency}'. Expected one of "
                f"{sorted(_VALID_CURRENCIES)}."
            )

        if not isinstance(payload.quantity, int) or isinstance(payload.quantity, bool):
            raise HkexOrderError(
                f"Quantity {payload.quantity!r} must be an int number of shares."
            )
        if payload.quantity <= 0:
            raise HkexOrderError(f"Quantity {payload.quantity} must be positive.")
        if not isinstance(payload.board_lot_size, int) or isinstance(payload.board_lot_size, bool):
            raise HkexOrderError(f"Board lot size {payload.board_lot_size!r} must be an int.")
        if not MIN_BOARD_LOT_SIZE <= payload.board_lot_size <= MAX_BOARD_LOT_SIZE:
            # A zero board lot size would raise ZeroDivisionError below; a negative one
            # would make every quantity look like a clean multiple.
            raise HkexOrderError(
                f"Board lot size {payload.board_lot_size} is outside the plausible HKEX "
                f"range {MIN_BOARD_LOT_SIZE}-{MAX_BOARD_LOT_SIZE}. It is issuer-set "
                f"reference data - read it from the security master, do not default it."
            )

        decimal_price = _to_decimal(payload.price, "Price")
        tick_size = self.get_hkex_spread_table_tick_size(decimal_price, payload.spread_table)

        # Exact Decimal remainder: no tolerance, and therefore no near-miss price
        # slipping through as "close enough" to a legal tick.
        is_tick_valid = (decimal_price % tick_size) == 0

        lot_classification = self.classify_lot(payload.quantity, payload.board_lot_size)
        is_board_lot = lot_classification is LotClassification.BOARD_LOT

        order_size_lots = _D(payload.quantity) / _D(payload.board_lot_size)
        is_order_size_valid = order_size_lots <= MAX_ORDER_SIZE_BOARD_LOTS

        violations: List[str] = []
        reasons: List[str] = []
        if not is_tick_valid:
            violations.append(STATUS_INVALID_TICK)
            reasons.append(
                f"price {decimal_price} is not a multiple of the "
                f"{payload.spread_table.value} minimum spread {tick_size}"
            )
        if not is_board_lot:
            violations.append(STATUS_INVALID_BOARD_LOT)
            reasons.append(
                f"quantity {payload.quantity} is a {lot_classification.value} against a "
                f"board lot of {payload.board_lot_size} and will not auto-match"
            )
        if not is_order_size_valid:
            violations.append(STATUS_INVALID_ORDER_SIZE)
            reasons.append(
                f"order size {order_size_lots} board lots exceeds the "
                f"{MAX_ORDER_SIZE_BOARD_LOTS} board lot automatch maximum"
            )

        if violations:
            status = violations[0]
            notes = f"HKEX ORDER REJECTED [{formatted_code}]: " + "; ".join(reasons) + "."
            logger.warning(notes)
        else:
            status = STATUS_VALIDATED
            notes = (
                f"HKEX ORDER VALIDATED [{formatted_code} - {currency} {counter_type.value}]: "
                f"{side} {order_type} {payload.quantity} shares @ {decimal_price} "
                f"(spread table {payload.spread_table.value}, minimum spread {tick_size}, "
                f"board lot {payload.board_lot_size}, {order_size_lots} lots)."
            )
            logger.info(notes)

        return HkexOrionOrderReport(
            formatted_stock_code=formatted_code,
            counter_type=counter_type,
            currency=currency,
            price=decimal_price,
            spread_table=payload.spread_table,
            applicable_tick_size=tick_size,
            is_price_tick_valid=is_tick_valid,
            quantity=payload.quantity,
            board_lot_size=payload.board_lot_size,
            lot_classification=lot_classification,
            is_board_lot_multiple=is_board_lot,
            order_size_board_lots=order_size_lots,
            is_order_size_valid=is_order_size_valid,
            status=status,
            violations=tuple(violations),
            audit_notes=notes,
        )

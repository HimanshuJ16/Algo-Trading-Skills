"""
Cboe Options Exchange Complex Order Engine.

Constructs, normalizes, validates, and serializes Complex Multi-Leg Orders
(FIX MsgType=AB New Order Multileg) for the Cboe Complex Order Book (COB) and
Complex Order Auction (COA) on Cboe Titanium U.S. Options exchanges
(C1, C2, BZX Options, EDGX Options), and parses the resulting Execution Reports.

Field semantics implemented here follow the Cboe Titanium U.S. Options FIX
Specification, "New Order Multileg Message Fields" and "Execution Report Message
Fields" (see references/standards.md for the exact citations).

Scope note: only the *long form* request is generated -- the legs are described
explicitly in the NoLegs (555) repeating group. The *short form* request (a
package priced against an already-listed COB strategy symbol, carrying Symbol
(55) and Side (54) instead of legs) is deliberately not generated, because its
net-price sign convention is side-dependent and inverts for Sell orders. See
``build_fix_message`` for details.
"""

from __future__ import annotations

import datetime
import logging
import math
import re
import string
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# --- Cboe complex order limits (Cboe Titanium U.S. Options FIX Specification) ---
MIN_COMPLEX_LEGS = 2
# NoLegs (555): "Minimum of 2, maximum of 16 total legs, including 1 equity leg."
MAX_COMPLEX_LEGS = 16
# "A minimum of 2, maximum of 100 total legs ... on non-FLEX 'Floor-Routed'
# orders (C1 only)." The same 100-leg ceiling applies to FLEX.
MAX_FLOOR_ROUTED_LEGS = 100
MAX_EQUITY_LEGS = 1
# OrderQty (38): "Number of contracts for order, 1 to 999,999"
MAX_ORDER_QTY = 999_999
# LegRatioQty (623): "Accepted values will be 1 - 999,999"
MAX_LEG_RATIO = 999_999
# LegRatioQty (623), C2 and EDGX only: "when reduced, the ratio between the
# smallest and largest leg must be no more than 1:3".
MAX_SMALLEST_TO_LARGEST_RATIO_SPREAD = 3
# ClOrdId (11): "20 characters or less"; Account (1): "Up to 16 characters";
# LegRefID (654): "Five alphanumeric or space characters or less".
MAX_CL_ORD_ID_LEN = 20
MAX_ACCOUNT_LEN = 16
MAX_LEG_REF_ID_LEN = 5
# Cboe Rule 5.33 conforming stock-option ratio: 8 option contracts (measured on
# the SMALLEST option leg) to 100 shares of stock.
MAX_STOCK_OPTION_RATIO = 8.0
# LegStrikePrice (612): "0 - 999999.999"
MAX_LEG_STRIKE_PRICE = Decimal("999999.999")
# Price (44): "-$999,999,999.90 to $999,999,999.90"
MAX_ABS_NET_PRICE = Decimal("999999999.90")

_PENNY = Decimal("0.01")
_ASCII_33_126 = frozenset(chr(c) for c in range(33, 127))
_CL_ORD_ID_FORBIDDEN = frozenset(",;|")
_LEG_REF_ID_ALLOWED = frozenset(string.ascii_letters + string.digits + " ")
_OSI_ROOT_RE = re.compile(r"^[A-Z0-9]{1,6}$")


class CboeExchange(str, Enum):
    """Cboe U.S. options exchange on which the order will be entered."""
    C1 = "C1"
    C2 = "C2"
    BZX = "BZX"
    EDGX = "EDGX"


class LegSide(str, Enum):
    """FIX Tag 624 LegSide. Values 5 and 6 are valid on the stock leg only."""
    BUY = "1"
    SELL = "2"
    SELL_SHORT = "5"
    SELL_SHORT_EXEMPT = "6"


class LegPositionEffect(str, Enum):
    """FIX Tag 564 LegPositionEffect (OCC open/close designation)."""
    OPEN = "O"
    CLOSE = "C"
    NONE = "N"


class LegCFICode(str, Enum):
    """
    FIX Tag 608 LegCFICode. Required when LegSymbol (600) carries an OSI root.
    'E' identifies the equity leg of a stock-option order (C1 and EDGX only).
    """
    OPTION_CALL = "OC"
    OPTION_PUT = "OP"
    EQUITY = "E"


class OrdType(str, Enum):
    """FIX Tag 40 OrdType."""
    MARKET = "1"
    LIMIT = "2"


class TimeInForce(str, Enum):
    """
    FIX Tag 59 TimeInForce values accepted on Cboe New Order Multileg.
    Note: FOK is NOT among the values Cboe documents for this message.
    """
    DAY = "0"
    GTC = "1"
    AT_THE_OPEN = "2"
    IOC = "3"
    GTD = "6"


class OrderCapacity(str, Enum):
    """FIX Tag 47 Rule80A / OrderCapacity. Required on New Order Multileg."""
    CUSTOMER = "C"
    FIRM = "F"
    MARKET_MAKER = "M"
    PROFESSIONAL_CUSTOMER = "U"
    AWAY_MARKET_MAKER = "N"
    BROKER_DEALER = "B"
    JOINT_BACK_OFFICE = "J"
    NON_TPH_AFFILIATE = "L"          # C1 and C2 only
    NON_TPH_BROKER_DEALER = "D"      # FLEX only, C1 only


class RoutingBookInst(str, Enum):
    """First character of RoutingInst (9303) -- book interaction instruction."""
    BOOK_ONLY = "B"          # Default; may interact with single-leg and complex orders
    POST_ONLY = "P"
    COMPLEX_BOOK_ONLY = "D"  # Complex orders only; requires DAY/IOC and OrderCapacity=M


class RoutingCoaInst(str, Enum):
    """
    Second character of RoutingInst (9303) -- Complex Order Auction exposure.
    Cboe defaults this to 'S' for non-IOC orders and 'L' for IOC orders.
    """
    NO_COA = "L"    # Do not expose order via COA
    EXPOSE_COA = "S"


class MultilegReportingType(str, Enum):
    """FIX Tag 442 MultilegReportingType on Cboe Execution Reports."""
    SINGLE_LEG_INSTRUMENT = "1"
    INDIVIDUAL_LEG_OF_MULTILEG = "2"   # per-leg fill report of a complex trade
    MULTILEG_INSTRUMENT = "3"          # complex package fill report


class SecurityType(str, Enum):
    """FIX Tag 167 SecurityType."""
    MULTILEG = "MLEG"
    OPTION = "OPT"
    EQUITY = "EQ"


# Tags that belong to the NoLegs (555) repeating group on New Order Multileg
# and on Execution Reports. Used to detect where the group ends.
_LEG_GROUP_TAGS = ("654", "600", "608", "611", "612", "623", "624", "566", "564", "22024")
_LEG_GROUP_START_TAG = "654"


class CboeValidationError(ValueError):
    """Raised when an order violates a documented Cboe or regulatory constraint."""


def _require_ascii_printable(value: str, field_name: str, max_len: int,
                             forbidden: Iterable[str] = ()) -> None:
    if not isinstance(value, str) or not value:
        raise CboeValidationError(f"{field_name} must be a non-empty string.")
    if len(value) > max_len:
        raise CboeValidationError(
            f"{field_name} must be {max_len} characters or less (got {len(value)})."
        )
    bad = set(value) - _ASCII_33_126
    if bad:
        raise CboeValidationError(
            f"{field_name} must use ASCII 33-126 only; found {sorted(bad)!r}."
        )
    forbidden_found = set(value) & set(forbidden)
    if forbidden_found:
        raise CboeValidationError(
            f"{field_name} must not contain {sorted(forbidden_found)!r}."
        )


def _to_decimal(value: float | int | str | Decimal, field_name: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CboeValidationError(f"{field_name} is not a valid decimal: {value!r}") from exc


def _format_decimal(value: Decimal) -> str:
    """Render a Decimal without exponent notation or trailing-zero noise."""
    normalized = value.normalize()
    if normalized == 0:
        return "0"
    text = format(normalized, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


@dataclass
class OptionLeg:
    """
    One leg of a Cboe complex order (one entry of the NoLegs (555) group).

    Attributes:
        symbol: LegSymbol (600). Either an OSI root (upper case, e.g. 'SPY') or a
            Cboe format symbol. When an OSI root is used, Cboe requires
            LegCFICode (608), LegMaturityDate (611) and LegStrikePrice (612).
        ratio: LegRatioQty (623). Positive integer, 1..999,999, reduced to lowest
            terms across the package before submission.
        side: LegSide (624). '5'/'6' are accepted on the stock leg only.
        position_effect: LegPositionEffect (564). 'O', 'C' or 'N'. Required unless
            OrderCapacity (47) is 'M' or 'N'.
        cfi_code: LegCFICode (608). 'OC'/'OP' for option legs, 'E' for the equity
            leg of a stock-option order (C1 and EDGX only).
        maturity_date: LegMaturityDate (611), 'YYYYMMDD'. Option legs with an OSI
            root only.
        strike_price: LegStrikePrice (612), 0..999999.999. Option legs with an OSI
            root only.
        leg_ref_id: LegRefID (654). Required by Cboe and must be the first field of
            each repeated group; auto-assigned by the engine when omitted. Five
            alphanumeric or space characters or less.
        multiplier: Contract multiplier used *locally* for the Rule 5.33
            stock-option ratio test. It is not a FIX field.
    """
    symbol: str
    ratio: int
    side: LegSide | str
    position_effect: Optional[LegPositionEffect | str] = LegPositionEffect.OPEN
    cfi_code: Optional[LegCFICode | str] = None
    maturity_date: Optional[str] = None
    strike_price: Optional[float | int | str | Decimal] = None
    leg_ref_id: Optional[str] = None
    multiplier: int = 100

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise CboeValidationError("LegSymbol (600) must be a non-empty string.")
        if isinstance(self.ratio, bool) or not isinstance(self.ratio, int):
            raise CboeValidationError(f"LegRatioQty (623) must be an integer, got {self.ratio!r}")
        if not 1 <= self.ratio <= MAX_LEG_RATIO:
            raise CboeValidationError(
                f"LegRatioQty (623) must be between 1 and {MAX_LEG_RATIO}, got {self.ratio}."
            )

        self.side = _coerce_enum(self.side, LegSide, "LegSide (624)")
        if self.cfi_code is not None:
            self.cfi_code = _coerce_enum(self.cfi_code, LegCFICode, "LegCFICode (608)")
        if self.position_effect is not None and self.position_effect != "":
            self.position_effect = _coerce_enum(
                self.position_effect, LegPositionEffect, "LegPositionEffect (564)"
            )
        else:
            self.position_effect = None

        if self.leg_ref_id is not None:
            self._validate_leg_ref_id(self.leg_ref_id)

        if self.is_equity_leg and self.multiplier == 100:
            # An equity leg is quoted in shares; the option multiplier does not apply.
            self.multiplier = 1
        if isinstance(self.multiplier, bool) or not isinstance(self.multiplier, int) \
                or self.multiplier <= 0:
            raise CboeValidationError(
                f"multiplier must be a positive integer, got {self.multiplier!r}"
            )

        if self.strike_price is not None:
            strike = _to_decimal(self.strike_price, "LegStrikePrice (612)")
            if not Decimal("0") <= strike <= MAX_LEG_STRIKE_PRICE:
                raise CboeValidationError(
                    f"LegStrikePrice (612) must be between 0 and {MAX_LEG_STRIKE_PRICE}, "
                    f"got {strike}."
                )
            self.strike_price = strike

        if self.maturity_date is not None:
            self._validate_maturity_date(self.maturity_date)

        if self.side in (LegSide.SELL_SHORT.value, LegSide.SELL_SHORT_EXEMPT.value) \
                and not self.is_equity_leg:
            raise CboeValidationError(
                "LegSide (624) values '5' (Sell Short) and '6' (Sell Short Exempt) are valid "
                "on the stock leg only; set cfi_code='E' for the equity leg."
            )

        if self.symbol_is_osi_root:
            self._validate_osi_root_requirements()

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _validate_leg_ref_id(leg_ref_id: str) -> None:
        if not isinstance(leg_ref_id, str) or not leg_ref_id:
            raise CboeValidationError("LegRefID (654) must be a non-empty string.")
        if len(leg_ref_id) > MAX_LEG_REF_ID_LEN:
            raise CboeValidationError(
                f"LegRefID (654) must be {MAX_LEG_REF_ID_LEN} characters or less "
                f"(got {len(leg_ref_id)}: {leg_ref_id!r})."
            )
        bad = set(leg_ref_id) - _LEG_REF_ID_ALLOWED
        if bad:
            raise CboeValidationError(
                "LegRefID (654) allows alphanumeric and space characters only; "
                f"found {sorted(bad)!r}."
            )

    @staticmethod
    def _validate_maturity_date(value: str) -> None:
        if not isinstance(value, str) or len(value) != 8 or not value.isdigit():
            raise CboeValidationError(
                f"LegMaturityDate (611) must be 'YYYYMMDD', got {value!r}."
            )
        try:
            datetime.date(int(value[:4]), int(value[4:6]), int(value[6:]))
        except ValueError as exc:
            raise CboeValidationError(
                f"LegMaturityDate (611) is not a real date: {value!r}"
            ) from exc

    def _validate_osi_root_requirements(self) -> None:
        if self.cfi_code is None:
            raise CboeValidationError(
                f"LegCFICode (608) is required when LegSymbol (600) is an OSI root "
                f"({self.symbol!r})."
            )
        if self.is_equity_leg:
            return
        missing = [
            name for name, value in (
                ("LegMaturityDate (611)", self.maturity_date),
                ("LegStrikePrice (612)", self.strike_price),
            ) if value is None
        ]
        if missing:
            raise CboeValidationError(
                f"{' and '.join(missing)} required when LegSymbol (600) is an OSI root "
                f"({self.symbol!r})."
            )

    # -- derived properties -------------------------------------------------
    @property
    def is_equity_leg(self) -> bool:
        """True when this leg is the stock leg of a stock-option order."""
        return self.cfi_code == LegCFICode.EQUITY.value

    @property
    def symbol_is_osi_root(self) -> bool:
        """
        True when LegSymbol (600) looks like an OSI root (up to six upper-case
        alphanumerics) rather than a fully qualified Cboe format symbol.
        """
        return bool(_OSI_ROOT_RE.match(self.symbol))

    @property
    def underlying_units(self) -> int:
        """Units of the underlying represented by this leg (ratio x multiplier)."""
        return self.ratio * self.multiplier


def _coerce_enum(value, enum_cls, field_name: str) -> str:
    """Normalize an enum member or raw string to the enum's wire value."""
    if isinstance(value, enum_cls):
        return value.value
    if isinstance(value, str):
        allowed = {member.value for member in enum_cls}
        if value in allowed:
            return value
        raise CboeValidationError(
            f"Invalid {field_name} value {value!r}. Allowed: {sorted(allowed)}."
        )
    raise CboeValidationError(f"Unsupported type for {field_name}: {type(value)}")


@dataclass
class EchoedLeg:
    """A leg echoed back inside the NoLegs (555) group of an Execution Report."""
    leg_ref_id: Optional[str] = None
    leg_symbol: Optional[str] = None
    leg_cfi_code: Optional[str] = None
    leg_maturity_date: Optional[str] = None
    leg_strike_price: Optional[Decimal] = None
    leg_ratio_qty: Optional[int] = None
    leg_side: Optional[str] = None
    leg_position_effect: Optional[str] = None


@dataclass
class CboeExecutionReport:
    """
    Parsed Cboe FIX Execution Report (MsgType=8).

    Cboe does not report per-leg fill prices inside the complex package report.
    A complex execution produces a package report (MultilegReportingType (442) = 3,
    SecurityType (167) = MLEG) plus one report per leg (442 = 2, 167 = OPT or EQ)
    that carries LastPx (31), LastShares (32) and LegRefID (654) at the top level.
    LegLastPx (637) and LegLastQty (638) are NOT part of the Cboe message set.
    """
    cl_ord_id: str = ""
    order_id: str = ""
    exec_id: str = ""
    exec_type: str = ""
    ord_status: str = ""
    symbol: str = ""
    security_type: str = ""
    multileg_reporting_type: str = ""
    leg_ref_id: Optional[str] = None
    last_px: Optional[Decimal] = None
    last_shares: Optional[int] = None
    cum_qty: Optional[int] = None
    leaves_qty: Optional[int] = None
    avg_px: Optional[Decimal] = None
    legs: List[EchoedLeg] = field(default_factory=list)
    raw_tags: Dict[str, str] = field(default_factory=dict)

    @property
    def is_package_report(self) -> bool:
        """True for the complex package fill report (442 = 3)."""
        return self.multileg_reporting_type == MultilegReportingType.MULTILEG_INSTRUMENT.value

    @property
    def is_leg_report(self) -> bool:
        """True for a per-leg fill report of a complex trade (442 = 2)."""
        return self.multileg_reporting_type == MultilegReportingType.INDIVIDUAL_LEG_OF_MULTILEG.value


class CboeComplexOrderEngine:
    """
    Builds and validates a Cboe Titanium FIX New Order Multileg (MsgType=AB)
    long-form request, and parses the Execution Reports it produces.
    """

    def __init__(
        self,
        cl_ord_id: str,
        total_quantity: int,
        order_capacity: OrderCapacity | str,
        net_price: Optional[float | int | str | Decimal] = None,
        ord_type: OrdType | str = OrdType.LIMIT,
        time_in_force: TimeInForce | str = TimeInForce.DAY,
        underlying_symbol: Optional[str] = None,
        account: Optional[str] = None,
        routing_book_inst: Optional[RoutingBookInst | str] = None,
        routing_coa_inst: Optional[RoutingCoaInst | str] = None,
        exchange: CboeExchange | str = CboeExchange.C1,
        expire_time: Optional[datetime.datetime] = None,
        transact_time: Optional[datetime.datetime] = None,
        max_legs: int = MAX_COMPLEX_LEGS,
    ) -> None:
        """
        Args:
            cl_ord_id: ClOrdId (11). <= 20 chars, ASCII 33-126, no ',', ';', '|',
                no leading '~' (reserved by Cboe for system-generated IDs).
            total_quantity: Desired number of complex packages *before* GCD scaling.
            order_capacity: Rule80A / OrderCapacity (47). Required by Cboe.
            net_price: Price (44). Long-form convention: positive = net debit,
                negative = net credit, 0 = even. Option-only spreads must price in
                whole pennies.
            underlying_symbol: Informational only. Not serialized -- Symbol (55) is
                a short-form field carrying the COB strategy symbol, not the root.
            routing_book_inst / routing_coa_inst: the two characters of
                RoutingInst (9303). Leave unset to accept the Cboe port defaults.
            exchange: drives venue-specific validation (C2/EDGX ratio spread cap,
                equity legs on C1/EDGX only).
            max_legs: raise above 16 only for C1 non-FLEX floor-routed or FLEX
                orders, which Cboe allows up to 100 legs.
        """
        _require_ascii_printable(cl_ord_id, "ClOrdId (11)", MAX_CL_ORD_ID_LEN,
                                 forbidden=_CL_ORD_ID_FORBIDDEN)
        if cl_ord_id.startswith("~"):
            raise CboeValidationError(
                "ClOrdId (11) must not begin with '~'; leading tildes are reserved by Cboe "
                "for system-generated identifiers and will be rejected."
            )
        if isinstance(total_quantity, bool) or not isinstance(total_quantity, int) \
                or total_quantity <= 0:
            raise CboeValidationError(
                f"total_quantity must be a positive integer, got {total_quantity!r}"
            )
        if account is not None:
            _require_ascii_printable(account, "Account (1)", MAX_ACCOUNT_LEN)
        if isinstance(max_legs, bool) or not isinstance(max_legs, int) \
                or not MIN_COMPLEX_LEGS <= max_legs <= MAX_FLOOR_ROUTED_LEGS:
            raise CboeValidationError(
                f"max_legs must be between {MIN_COMPLEX_LEGS} and {MAX_FLOOR_ROUTED_LEGS}, "
                f"got {max_legs!r}"
            )

        self.cl_ord_id = cl_ord_id
        self.total_quantity = total_quantity
        self.underlying_symbol = underlying_symbol
        self.account = account
        self.max_legs = max_legs

        self.order_capacity = _coerce_enum(order_capacity, OrderCapacity, "OrderCapacity (47)")
        self.ord_type = _coerce_enum(ord_type, OrdType, "OrdType (40)")
        self.time_in_force = _coerce_enum(time_in_force, TimeInForce, "TimeInForce (59)")
        self.exchange = _coerce_enum(exchange, CboeExchange, "exchange")
        self.routing_book_inst = (
            _coerce_enum(routing_book_inst, RoutingBookInst, "RoutingInst (9303) 1st character")
            if routing_book_inst is not None else None
        )
        self.routing_coa_inst = (
            _coerce_enum(routing_coa_inst, RoutingCoaInst, "RoutingInst (9303) 2nd character")
            if routing_coa_inst is not None else None
        )

        self.net_price: Optional[Decimal] = (
            _to_decimal(net_price, "Price (44)") if net_price is not None else None
        )
        self.expire_time = expire_time
        self.transact_time = transact_time or datetime.datetime.now(datetime.timezone.utc)

        self.legs: List[OptionLeg] = []

    # -- construction -------------------------------------------------------
    def add_leg(self, leg: OptionLeg) -> None:
        """Append a leg, enforcing the venue leg-count and equity-leg limits."""
        if not isinstance(leg, OptionLeg):
            raise TypeError(f"Expected OptionLeg instance, got {type(leg)}")
        if len(self.legs) >= self.max_legs:
            raise CboeValidationError(
                f"NoLegs (555) limit is {self.max_legs} legs for this order; cannot add another."
            )
        if leg.is_equity_leg:
            if sum(1 for existing in self.legs if existing.is_equity_leg) >= MAX_EQUITY_LEGS:
                raise CboeValidationError(
                    f"A Cboe complex order may carry at most {MAX_EQUITY_LEGS} equity leg."
                )
            if self.exchange not in (CboeExchange.C1.value, CboeExchange.EDGX.value):
                raise CboeValidationError(
                    "Equity legs on complex orders are supported on C1 and EDGX only "
                    f"(exchange={self.exchange})."
                )
        self.legs.append(leg)

    # -- validation ---------------------------------------------------------
    def validate(self) -> None:
        """
        Validate the package against the documented Cboe constraints.

        Raises:
            CboeValidationError: on any violation. No partial message is produced --
                callers must treat this as a hard, non-retryable reject.
        """
        self._validate_leg_count()
        self._validate_leg_ref_ids()
        self._validate_ratios()
        self._validate_position_effects()
        self._validate_routing()
        self._validate_price()
        self._validate_stock_option_ratio()
        self._validate_quantity()

    def _validate_leg_count(self) -> None:
        if len(self.legs) < MIN_COMPLEX_LEGS:
            raise CboeValidationError(
                f"Complex orders require at least {MIN_COMPLEX_LEGS} legs (got {len(self.legs)}). "
                "Single-leg orders must be sent as MsgType=D (New Order Single)."
            )
        if len(self.legs) > self.max_legs:
            raise CboeValidationError(
                f"NoLegs (555) is {len(self.legs)}; this order permits at most {self.max_legs}."
            )

    def _validate_leg_ref_ids(self) -> None:
        """
        LegRefID (654) is the only key that ties a 442=2 leg fill report back to a
        leg, so duplicates would silently merge two legs' fills during
        reconciliation. Reject them before the order leaves the process.
        """
        _, normalized = self._normalize_ratios()
        seen: Dict[str, int] = {}
        for leg in normalized:
            seen[leg.leg_ref_id] = seen.get(leg.leg_ref_id, 0) + 1
        duplicates = sorted(ref for ref, count in seen.items() if count > 1)
        if duplicates:
            raise CboeValidationError(
                f"LegRefID (654) must be unique within the order; duplicated {duplicates}. "
                "Leg fill reports could not be attributed to a single leg."
            )

    def _validate_ratios(self) -> None:
        _, normalized = self._normalize_ratios()
        ratios = [leg.ratio for leg in normalized]
        if self.exchange in (CboeExchange.C2.value, CboeExchange.EDGX.value):
            if max(ratios) > MAX_SMALLEST_TO_LARGEST_RATIO_SPREAD * min(ratios):
                raise CboeValidationError(
                    f"On {self.exchange}, the reduced ratio between the smallest and largest leg "
                    f"must be no more than 1:{MAX_SMALLEST_TO_LARGEST_RATIO_SPREAD}; "
                    f"reduced ratios are {ratios}."
                )

    def _validate_position_effects(self) -> None:
        # LegPositionEffect (564) is required unless OrderCapacity (47) is 'M' or 'N'.
        if self.order_capacity in (OrderCapacity.MARKET_MAKER.value,
                                   OrderCapacity.AWAY_MARKET_MAKER.value):
            return
        missing = [leg.leg_ref_id or leg.symbol for leg in self.legs if leg.position_effect is None]
        if missing:
            raise CboeValidationError(
                "LegPositionEffect (564) is required on every leg unless OrderCapacity (47) is "
                f"'M' or 'N'; missing on {missing}."
            )

    def _validate_routing(self) -> None:
        if self.routing_book_inst == RoutingBookInst.POST_ONLY.value and \
                self.routing_coa_inst == RoutingCoaInst.EXPOSE_COA.value:
            raise CboeValidationError(
                "RoutingInst (9303) = 'PS' is not supported: Post Only orders cannot be COA eligible."
            )
        if self.routing_book_inst == RoutingBookInst.COMPLEX_BOOK_ONLY.value:
            if self.time_in_force not in (TimeInForce.DAY.value, TimeInForce.IOC.value):
                raise CboeValidationError(
                    "RoutingInst (9303) = 'D' (Complex Book Only) requires TimeInForce (59) "
                    "of 0 (DAY) or 3 (IOC)."
                )
            if self.order_capacity != OrderCapacity.MARKET_MAKER.value:
                raise CboeValidationError(
                    "RoutingInst (9303) = 'D' (Complex Book Only) requires OrderCapacity (47) = 'M'."
                )
        if self.time_in_force == TimeInForce.GTD.value and self.expire_time is None:
            raise CboeValidationError("TimeInForce (59) = 6 (GTD) requires ExpireTime (126).")

    def _validate_price(self) -> None:
        if self.ord_type == OrdType.LIMIT.value:
            if self.net_price is None:
                raise CboeValidationError("Limit complex orders (OrdType=2) require a net_price.")
            if abs(self.net_price) > MAX_ABS_NET_PRICE:
                raise CboeValidationError(
                    f"Price (44) must be within +/-{MAX_ABS_NET_PRICE}, got {self.net_price}."
                )
            if not self.has_equity_leg and self.net_price % _PENNY != 0:
                raise CboeValidationError(
                    f"Price (44) must be in whole pennies for option-only spreads, "
                    f"got {self.net_price}. Only spreads with a stock leg (and FLEX instruments) "
                    "accept up to 4 decimal places."
                )
        elif self.net_price is not None:
            raise CboeValidationError(
                "Market complex orders (OrdType=1) must not carry a net_price."
            )

    def _validate_stock_option_ratio(self) -> None:
        """
        Cboe Rule 5.33 conforming stock-option ratio: the ratio of underlying units
        in the option leg(s) to units in the stock leg must be <= 8:1, measured on
        the SMALLEST option leg -- Cboe's electronic ratio check compares the
        smallest option leg, not the aggregate of all option legs.
        """
        stock_units = sum(leg.underlying_units for leg in self.legs if leg.is_equity_leg)
        option_legs = [leg for leg in self.legs if not leg.is_equity_leg]
        if stock_units == 0 or not option_legs:
            return
        smallest_option_units = min(leg.underlying_units for leg in option_legs)
        ratio = smallest_option_units / stock_units
        if ratio > MAX_STOCK_OPTION_RATIO:
            raise CboeValidationError(
                f"Non-conforming stock-option order: smallest option leg represents "
                f"{smallest_option_units} underlying units against {stock_units} stock units "
                f"({ratio:.2f}:1), exceeding the Cboe Rule 5.33 conforming ratio of "
                f"{MAX_STOCK_OPTION_RATIO}:1. Non-conforming orders receive different priority "
                "and auction handling."
            )

    def _validate_quantity(self) -> None:
        normalized_qty, _ = self._normalize_ratios()
        if normalized_qty > MAX_ORDER_QTY:
            raise CboeValidationError(
                f"OrderQty (38) after GCD scaling is {normalized_qty}, exceeding the Cboe maximum "
                f"of {MAX_ORDER_QTY}. Reduce total_quantity or split the package."
            )

    @property
    def has_equity_leg(self) -> bool:
        """True when the package includes the stock leg of a stock-option order."""
        return any(leg.is_equity_leg for leg in self.legs)

    # -- normalization ------------------------------------------------------
    def _normalize_ratios(self) -> Tuple[int, List[OptionLeg]]:
        """
        Reduce leg ratios to lowest terms via GCD and scale OrderQty by the same
        GCD so total contract exposure is preserved exactly.

        Cboe rejects unreduced ratios: "All legs must be reduced (i.e., 2:2 must be
        sent as 1:1) in order to be accepted by the system when using this message
        type."

        Example:
            Buy 10 x leg A, Sell 20 x leg B, 100 packages requested.
            GCD(10, 20) = 10 -> reduced ratios 1:2, OrderQty (38) = 100 * 10 = 1000.
        """
        if not self.legs:
            return self.total_quantity, []

        current_gcd = 0
        for leg in self.legs:
            current_gcd = math.gcd(current_gcd, leg.ratio)

        normalized_qty = self.total_quantity * current_gcd
        normalized_legs: List[OptionLeg] = []
        for index, leg in enumerate(self.legs):
            normalized_legs.append(
                OptionLeg(
                    symbol=leg.symbol,
                    ratio=leg.ratio // current_gcd,
                    side=leg.side,
                    position_effect=leg.position_effect,
                    cfi_code=leg.cfi_code,
                    maturity_date=leg.maturity_date,
                    strike_price=leg.strike_price,
                    leg_ref_id=leg.leg_ref_id or f"L{index + 1}",
                    multiplier=leg.multiplier,
                )
            )
        return normalized_qty, normalized_legs

    def reduced_leg_ratios(self) -> Dict[str, int]:
        """Reduced LegRatioQty (623) keyed by LegRefID (654), for fill reconciliation."""
        _, normalized = self._normalize_ratios()
        return {leg.leg_ref_id: leg.ratio for leg in normalized if leg.leg_ref_id}

    # -- serialization ------------------------------------------------------
    def build_fix_message(self, delimiter: str = "|") -> str:
        """
        Build the long-form New Order Multileg (MsgType=AB) body.

        Long-form price convention (Cboe Price (44)): positive value = net debit,
        negative value = net credit, zero = even. This differs from the *short*
        form, where the sign is read against the order's Side (54): on a short-form
        Sell order a positive Price is a CREDIT. The engine only emits the long
        form, so the debit-positive reading always holds for its output.

        Symbol (55) and Side (54) are deliberately omitted: Cboe documents both as
        "required only for short form request", where Symbol (55) is the Cboe
        Complex Order Book strategy symbol rather than the underlying root, and the
        package direction is carried per leg by LegSide (624).

        Args:
            delimiter: field separator. Use chr(1) (SOH) on the wire; '|' is for
                logs and tests.

        Returns:
            The message body, delimiter-terminated. BeginString (8), BodyLength (9)
            and CheckSum (10) are added by the FIX engine, not here.

        Raises:
            CboeValidationError: if the package violates a documented constraint.
        """
        if not isinstance(delimiter, str) or not delimiter:
            raise CboeValidationError("delimiter must be a non-empty string.")
        self.validate()
        norm_qty, norm_legs = self._normalize_ratios()

        parts: List[str] = ["35=AB"]
        if self.account:
            parts.append(f"1={self.account}")
        parts.append(f"11={self.cl_ord_id}")
        parts.append(f"60={self._format_fix_utc(self.transact_time)}")
        parts.append(f"167={SecurityType.MULTILEG.value}")

        # NoLegs (555) repeating group. LegRefID (654) is the required first field
        # of every repeated group.
        parts.append(f"555={len(norm_legs)}")
        for leg in norm_legs:
            parts.append(f"654={leg.leg_ref_id}")
            parts.append(f"600={leg.symbol}")
            if leg.cfi_code:
                parts.append(f"608={leg.cfi_code}")
            if leg.maturity_date:
                parts.append(f"611={leg.maturity_date}")
            if leg.strike_price is not None:
                parts.append(f"612={_format_decimal(leg.strike_price)}")
            parts.append(f"623={leg.ratio}")
            parts.append(f"624={leg.side}")
            if leg.position_effect:
                parts.append(f"564={leg.position_effect}")

        parts.append(f"38={norm_qty}")
        parts.append(f"40={self.ord_type}")
        if self.ord_type == OrdType.LIMIT.value and self.net_price is not None:
            parts.append(f"44={_format_decimal(self.net_price)}")
        if self.routing_inst:
            parts.append(f"9303={self.routing_inst}")
        parts.append(f"47={self.order_capacity}")
        parts.append(f"59={self.time_in_force}")
        if self.time_in_force == TimeInForce.GTD.value and self.expire_time is not None:
            parts.append(f"126={self._format_fix_utc(self.expire_time)}")

        message = delimiter.join(parts) + delimiter
        logger.debug(
            "Built Cboe New Order Multileg ClOrdId=%s legs=%d OrderQty=%d",
            self.cl_ord_id, len(norm_legs), norm_qty,
        )
        return message

    @property
    def routing_inst(self) -> str:
        """
        RoutingInst (9303) as sent on the wire: first character is the book
        instruction, second is COA exposure. Empty when neither is set, in which
        case Cboe applies its defaults ('B', and 'S' for non-IOC / 'L' for IOC).
        """
        if self.routing_book_inst is None and self.routing_coa_inst is None:
            return ""
        book = self.routing_book_inst or RoutingBookInst.BOOK_ONLY.value
        if self.routing_coa_inst is None:
            return book
        return f"{book}{self.routing_coa_inst}"

    @staticmethod
    def _format_fix_utc(value: datetime.datetime) -> str:
        """Format a datetime as the FIX UTCTimestamp 'YYYYMMDD-HH:MM:SS.sss' in UTC."""
        if not isinstance(value, datetime.datetime):
            raise CboeValidationError(f"Expected datetime, got {type(value)}")
        if value.tzinfo is None:
            raise CboeValidationError(
                "Timestamps must be timezone-aware; FIX TransactTime (60) and ExpireTime (126) "
                "are UTC and a naive local timestamp would be sent as though it were UTC."
            )
        as_utc = value.astimezone(datetime.timezone.utc)
        return as_utc.strftime("%Y%m%d-%H:%M:%S.") + f"{as_utc.microsecond // 1000:03d}"

    # -- execution report parsing -------------------------------------------
    @staticmethod
    def parse_fix_execution_report(fix_message: str, delimiter: str = "|") -> CboeExecutionReport:
        """
        Parse one Cboe FIX Execution Report (MsgType=8).

        Cboe reports a complex execution as a package report
        (MultilegReportingType (442) = 3, SecurityType (167) = MLEG) plus one
        report per leg (442 = 2, 167 = OPT or EQ) carrying LastPx (31),
        LastShares (32) and LegRefID (654) at the top level. There are no
        LegLastPx (637) / LegLastQty (638) fields in the Cboe message set, so leg
        fill prices come from the per-leg reports -- see ``reconcile_leg_fills``.

        Unparsable numeric fields are left as None and preserved in ``raw_tags``
        rather than raising, so one malformed field cannot discard an entire fill.
        """
        if not isinstance(fix_message, str) or not fix_message:
            raise CboeValidationError("fix_message must be a non-empty string.")
        if not isinstance(delimiter, str) or not delimiter:
            raise CboeValidationError("delimiter must be a non-empty string.")

        tags: Dict[str, str] = {}
        legs: List[EchoedLeg] = []
        current: Dict[str, str] = {}
        in_group = False

        for token in fix_message.split(delimiter):
            if "=" not in token:
                continue
            tag, value = token.split("=", 1)
            if tag == "555":
                tags[tag] = value
                in_group = True
                continue
            if in_group and tag in _LEG_GROUP_TAGS:
                if tag == _LEG_GROUP_START_TAG and current:
                    legs.append(CboeComplexOrderEngine._build_echoed_leg(current))
                    current = {}
                current[tag] = value
                continue
            # Any tag outside the leg group closes the group, so order-level fields
            # that trail the repeating group are still captured correctly.
            if in_group:
                if current:
                    legs.append(CboeComplexOrderEngine._build_echoed_leg(current))
                    current = {}
                in_group = False
            tags[tag] = value

        if current:
            legs.append(CboeComplexOrderEngine._build_echoed_leg(current))

        return CboeExecutionReport(
            cl_ord_id=tags.get("11", ""),
            order_id=tags.get("37", ""),
            exec_id=tags.get("17", ""),
            exec_type=tags.get("150", ""),
            ord_status=tags.get("39", ""),
            symbol=tags.get("55", ""),
            security_type=tags.get("167", ""),
            multileg_reporting_type=tags.get("442", ""),
            leg_ref_id=tags.get("654"),
            last_px=_safe_decimal(tags.get("31")),
            last_shares=_safe_int(tags.get("32")),
            cum_qty=_safe_int(tags.get("14")),
            leaves_qty=_safe_int(tags.get("151")),
            avg_px=_safe_decimal(tags.get("6")),
            legs=legs,
            raw_tags=tags,
        )

    @staticmethod
    def _build_echoed_leg(fields: Dict[str, str]) -> EchoedLeg:
        return EchoedLeg(
            leg_ref_id=fields.get("654"),
            leg_symbol=fields.get("600"),
            leg_cfi_code=fields.get("608"),
            leg_maturity_date=fields.get("611"),
            leg_strike_price=_safe_decimal(fields.get("612")),
            leg_ratio_qty=_safe_int(fields.get("623")),
            leg_side=fields.get("624"),
            leg_position_effect=fields.get("564"),
        )


def _safe_int(value: Optional[str]) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError:
        logger.warning("Non-integer FIX value %r; leaving field unset.", value)
        return None


def _safe_decimal(value: Optional[str]) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        logger.warning("Non-numeric FIX value %r; leaving field unset.", value)
        return None


def reconcile_leg_fills(
    package_report: CboeExecutionReport,
    leg_reports: Sequence[CboeExecutionReport],
    leg_ratios: Dict[str, int],
) -> Dict[str, int]:
    """
    Verify that the per-leg fill reports of a complex execution match the package.

    Cboe guarantees a complex order executes within its net price and ratio, but
    the fill arrives as several messages. This checks the invariant that must hold
    for an atomic complex trade:

        leg_filled_quantity == package_filled_quantity * reduced_leg_ratio

    Args:
        package_report: the 442=3 package fill report.
        leg_reports: the 442=2 per-leg fill reports for the same execution.
        leg_ratios: reduced LegRatioQty (623) keyed by LegRefID (654), e.g. from
            ``CboeComplexOrderEngine.reduced_leg_ratios()``.

    Returns:
        Filled quantity per LegRefID, as reported.

    Raises:
        CboeValidationError: if the package report is not a package fill, if a leg
            report cannot be attributed to a LegRefID, or if any leg quantity
            breaks the ratio invariant. A breach means the position is not what the
            strategy assumes and must be resolved before trading further.
    """
    if not package_report.is_package_report:
        raise CboeValidationError(
            "package_report must have MultilegReportingType (442) = 3 (complex package fill); "
            f"got {package_report.multileg_reporting_type!r}."
        )
    package_qty = package_report.last_shares
    if package_qty is None:
        raise CboeValidationError("Package report is missing LastShares (32).")

    filled: Dict[str, int] = {}
    for report in leg_reports:
        if not report.is_leg_report:
            raise CboeValidationError(
                "leg_reports must all have MultilegReportingType (442) = 2; "
                f"got {report.multileg_reporting_type!r} for ExecID {report.exec_id!r}."
            )
        ref = report.leg_ref_id
        if not ref:
            raise CboeValidationError(
                f"Leg fill report ExecID {report.exec_id!r} carries no LegRefID (654); "
                "it cannot be attributed to a leg."
            )
        if report.last_shares is None:
            raise CboeValidationError(
                f"Leg fill report ExecID {report.exec_id!r} is missing LastShares (32)."
            )
        filled[ref] = filled.get(ref, 0) + report.last_shares

    missing = sorted(set(leg_ratios) - set(filled))
    if missing:
        raise CboeValidationError(
            f"No leg fill report received for LegRefID(s) {missing}; the package fill of "
            f"{package_qty} is not fully accounted for."
        )
    unexpected = sorted(set(filled) - set(leg_ratios))
    if unexpected:
        raise CboeValidationError(f"Leg fill reports for unknown LegRefID(s) {unexpected}.")

    for ref, ratio in leg_ratios.items():
        expected = package_qty * ratio
        if filled[ref] != expected:
            raise CboeValidationError(
                f"Leg {ref} filled {filled[ref]} against an expected {expected} "
                f"({package_qty} packages x ratio {ratio}). The package did not execute atomically "
                "as assumed; reconcile positions before submitting further orders."
            )
    return filled

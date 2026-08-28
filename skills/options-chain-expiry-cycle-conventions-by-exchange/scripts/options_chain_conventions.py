"""Options chain expiry cycle and settlement convention resolver.

Resolves, for a *known* listed options contract on a *known* exchange:

* the expiry date implied by the venue's monthly-cycle rule
  (Cboe/CME/Eurex third Friday, Deribit last Friday, Cboe VIX 30-day Wednesday),
* the last trading day, which is **not** always the expiration date,
* the settlement type, exercise style and delivery type, and
* signed days to expiration.

Design rule: this module never infers a convention from the shape of a ticker
string. A contract is either present in the sourced registry, or the caller
declares its asset class explicitly. Anything else raises. Guessing that an
unrecognised index symbol is an American-style physically-settled equity option
is the most damaging failure mode in this area, so it is not possible here.

All contract data carries ``source`` and ``source_as_of``. Exchanges change
contract specifications; re-verify against the venue before relying on an entry.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, FrozenSet, Iterable, List, Mapping, Optional, Set, Tuple, Union

logger = logging.getLogger(__name__)
# Library convention: stay silent unless the host application configures
# logging. The same warnings are always returned structurally on
# OptionsChainConventionReport.warnings, which is the programmatic contract.
logger.addHandler(logging.NullHandler())

_FRIDAY = 4
_WEDNESDAY = 2
_SATURDAY = 5

# --- Settlement type vocabulary -------------------------------------------
# AM/PM is a US-centric pair and cannot describe every venue, so two further
# values exist. Eurex determines the final settlement value from an intraday
# Xetra auction; Deribit settles at a fixed UTC wall-clock time.
SETTLEMENT_AM = "AM_SETTLED"
SETTLEMENT_PM = "PM_SETTLED"
SETTLEMENT_AUCTION = "AUCTION_SETTLED"
SETTLEMENT_FIXED_TIME = "FIXED_TIME_SETTLED"

EXERCISE_EUROPEAN = "EUROPEAN"
EXERCISE_AMERICAN = "AMERICAN"

DELIVERY_CASH = "CASH"
DELIVERY_PHYSICAL = "PHYSICAL"
# Exercise of a CME option on futures delivers a position in the underlying
# futures contract -- neither cash nor shares.
DELIVERY_FUTURES = "FUTURES"

# --- Expiry rules ---------------------------------------------------------
RULE_THIRD_FRIDAY = "THIRD_FRIDAY"
RULE_LAST_FRIDAY = "LAST_FRIDAY"
RULE_VIX_30_DAY_WEDNESDAY = "VIX_30_DAY_WEDNESDAY"
RULE_NOT_CALENDAR_DERIVABLE = "NOT_CALENDAR_DERIVABLE"

# --- Last trading day rules -----------------------------------------------
LTD_EXPIRATION_DATE = "EXPIRATION_DATE"
LTD_PRECEDING_BUSINESS_DAY = "PRECEDING_BUSINESS_DAY"

CYCLE_MONTHLY = "MONTHLY"
CYCLE_QUARTERLY = "QUARTERLY"
CYCLE_WEEKLY = "WEEKLY"
CYCLE_LEAPS = "LEAPS"

_STANDARD_QUARTERLY_MONTHS: Tuple[int, ...] = (3, 6, 9, 12)


class OptionsConventionError(ValueError):
    """Base class for every error this module raises."""


class UnknownContractError(OptionsConventionError):
    """The (exchange, symbol) pair is not registered and no asset class was declared."""


class UnsupportedCycleError(OptionsConventionError):
    """The requested cycle cannot be derived from a (year, month) pair for this contract."""


@dataclass(frozen=True)
class ContractConvention:
    """Sourced contract specification for one listed options series."""

    exchange: str
    symbol: str
    description: str
    expiry_rule: str
    settlement_type: str
    settlement_basis: str
    exercise_style: str
    delivery_type: str
    last_trading_day_rule: str
    supported_cycles: FrozenSet[str]
    source: str
    source_as_of: str
    quarterly_months: Tuple[int, ...] = _STANDARD_QUARTERLY_MONTHS
    # False for continuously-traded venues (Deribit trades 24/7/365), where
    # rolling an expiry back off a "holiday" would itself be the error.
    observes_exchange_holidays: bool = True


def _cboe_index(
    symbol: str,
    description: str,
    settlement_type: str,
    settlement_basis: str,
    last_trading_day_rule: str,
    source: str,
    expiry_rule: str = RULE_THIRD_FRIDAY,
    supported_cycles: Iterable[str] = (CYCLE_MONTHLY, CYCLE_QUARTERLY, CYCLE_LEAPS),
) -> ContractConvention:
    """Cboe cash-settled European index option -- the shared half of those entries."""
    return ContractConvention(
        exchange="CBOE",
        symbol=symbol,
        description=description,
        expiry_rule=expiry_rule,
        settlement_type=settlement_type,
        settlement_basis=settlement_basis,
        exercise_style=EXERCISE_EUROPEAN,
        delivery_type=DELIVERY_CASH,
        last_trading_day_rule=last_trading_day_rule,
        supported_cycles=frozenset(supported_cycles),
        source=source,
        source_as_of="2026-08",
    )


_CBOE_SPX_SRC = (
    "Cboe SPX Options product specifications, "
    "cboe.com/tradable_products/sp_500/spx_options/specifications/"
)
_CBOE_RUT_SRC = (
    "Cboe RUT/RUTW Options product specifications, "
    "cboe.com/tradable_products/ftse_russell/russell_2000_index_options/rut_specifications/"
)
_CBOE_NDX_SRC = "Nasdaq NDX & NDXP factsheet, nasdaq.com/nasdaq-100-options-xnd-ndx"
_CBOE_XSP_SRC = (
    "Cboe Mini-SPX (XSP) Index Options factsheet, "
    "cdn.cboe.com/resources/xsp/XSP_Options_Fact_Sheet.pdf"
)
_CBOE_VIX_SRC = (
    "Cboe VIX Options product specifications, "
    "cboe.com/tradable-products/vix/vix-options/specifications/"
)

_NOT_DERIVABLE_BASIS = (
    "Expiries are weekly and/or end-of-month series; the expiry date is not "
    "determined by a (year, month) pair alone."
)

CONTRACT_REGISTRY: Dict[Tuple[str, str], ContractConvention] = {
    # --- Cboe cash-settled index options ---------------------------------
    ("CBOE", "SPX"): _cboe_index(
        "SPX",
        "S&P 500 Index options, standard AM-settled monthly series",
        SETTLEMENT_AM,
        "Special Opening Quotation (SET) derived from the opening sales price of "
        "each S&P 500 component in its primary market on the expiration date.",
        LTD_PRECEDING_BUSINESS_DAY,
        _CBOE_SPX_SRC,
    ),
    ("CBOE", "SPXW"): _cboe_index(
        "SPXW",
        "S&P 500 Index Weeklys and End-of-Month options",
        SETTLEMENT_PM,
        "Closing value of the S&P 500 Index on the expiration date. " + _NOT_DERIVABLE_BASIS,
        LTD_EXPIRATION_DATE,
        _CBOE_SPX_SRC,
        expiry_rule=RULE_NOT_CALENDAR_DERIVABLE,
        supported_cycles=(),
    ),
    ("CBOE", "NDX"): _cboe_index(
        "NDX",
        "Nasdaq-100 Index options, standard AM-settled monthly series",
        SETTLEMENT_AM,
        "Opening settlement value derived from the Nasdaq Official Opening Price "
        "of each Nasdaq-100 component on the expiration date.",
        LTD_PRECEDING_BUSINESS_DAY,
        _CBOE_NDX_SRC,
    ),
    ("CBOE", "NDXP"): _cboe_index(
        "NDXP",
        "Nasdaq-100 Index PM-settled options (daily/weekly/quarterly)",
        SETTLEMENT_PM,
        "Official closing value of the Nasdaq-100 Index on the expiration date. "
        + _NOT_DERIVABLE_BASIS,
        LTD_EXPIRATION_DATE,
        _CBOE_NDX_SRC,
        expiry_rule=RULE_NOT_CALENDAR_DERIVABLE,
        supported_cycles=(),
    ),
    ("CBOE", "RUT"): _cboe_index(
        "RUT",
        "Russell 2000 Index options, standard AM-settled monthly series",
        SETTLEMENT_AM,
        "Special Opening Quotation (RLS) derived from the opening sales price of "
        "each Russell 2000 component in its primary market on the expiration date.",
        LTD_PRECEDING_BUSINESS_DAY,
        _CBOE_RUT_SRC,
    ),
    ("CBOE", "RUTW"): _cboe_index(
        "RUTW",
        "Russell 2000 Index Weeklys and End-of-Month options",
        SETTLEMENT_PM,
        "Closing value of the Russell 2000 Index on the expiration date. "
        + _NOT_DERIVABLE_BASIS,
        LTD_EXPIRATION_DATE,
        _CBOE_RUT_SRC,
        expiry_rule=RULE_NOT_CALENDAR_DERIVABLE,
        supported_cycles=(),
    ),
    # XSP refutes "index options are AM-settled": it is an index option,
    # European and cash-settled, but PM-settled.
    ("CBOE", "XSP"): _cboe_index(
        "XSP",
        "Mini-SPX Index options (1/10th SPX), PM-settled",
        SETTLEMENT_PM,
        "Closing value of the S&P 500 Index on the expiration date.",
        LTD_EXPIRATION_DATE,
        _CBOE_XSP_SRC,
    ),
    # VIX refutes "monthly options expire on the third Friday".
    ("CBOE", "VIX"): _cboe_index(
        "VIX",
        "Cboe Volatility Index options, standard monthly series",
        SETTLEMENT_AM,
        "Special Opening Quotation of VIX calculated from the opening prices of "
        "constituent SPX options on the expiration date.",
        LTD_PRECEDING_BUSINESS_DAY,
        _CBOE_VIX_SRC,
        expiry_rule=RULE_VIX_30_DAY_WEDNESDAY,
        supported_cycles=(CYCLE_MONTHLY,),
    ),
    # --- CME options on futures ------------------------------------------
    # Quarterly ES options are American-style and exercise into the underlying
    # future. CME also lists a separate European-style Third-Friday Monthly
    # series on the same underlying; it is a different product and is not
    # registered here under this symbol.
    ("CME", "ES"): ContractConvention(
        exchange="CME",
        symbol="ES",
        description="E-mini S&P 500 quarterly options on futures",
        expiry_rule=RULE_THIRD_FRIDAY,
        settlement_type=SETTLEMENT_AM,
        settlement_basis=(
            "Special Opening Quotation of the S&P 500 Index from the opening "
            "price of each component on the expiration date; in-the-money "
            "options deliver the underlying future, which settles to the SOQ."
        ),
        exercise_style=EXERCISE_AMERICAN,
        delivery_type=DELIVERY_FUTURES,
        last_trading_day_rule=LTD_EXPIRATION_DATE,
        supported_cycles=frozenset({CYCLE_QUARTERLY}),
        source=(
            "CME Group, Understanding listings and expirations; "
            "FAQ: Weekly & EOM Options on S&P 500 Futures"
        ),
        source_as_of="2026-08",
    ),
    # --- Eurex index options ---------------------------------------------
    ("EUREX", "ODAX"): ContractConvention(
        exchange="EUREX",
        symbol="ODAX",
        description="DAX Index options",
        expiry_rule=RULE_THIRD_FRIDAY,
        settlement_type=SETTLEMENT_AUCTION,
        settlement_basis=(
            "Final settlement price determined from the Xetra intraday auction "
            "prices (from 13:00 CET) of the DAX component shares on the final "
            "settlement day -- neither an opening nor a closing value."
        ),
        exercise_style=EXERCISE_EUROPEAN,
        delivery_type=DELIVERY_CASH,
        last_trading_day_rule=LTD_EXPIRATION_DATE,
        supported_cycles=frozenset({CYCLE_MONTHLY, CYCLE_QUARTERLY, CYCLE_LEAPS}),
        source=(
            "Eurex DAX Options (ODAX) contract specifications, "
            "eurex.com/ex-en/markets/idx/dax/DAX-Options-139884"
        ),
        source_as_of="2026-08",
    ),
    # --- Deribit crypto options ------------------------------------------
    # Monthly expiries are the LAST Friday of the month, not the third.
    ("DERIBIT", "BTC"): ContractConvention(
        exchange="DERIBIT",
        symbol="BTC",
        description="Deribit Bitcoin options (inverse, BTC-margined)",
        expiry_rule=RULE_LAST_FRIDAY,
        settlement_type=SETTLEMENT_FIXED_TIME,
        settlement_basis=(
            "Delivery price computed by Deribit at 08:00 UTC on the expiry date "
            "from the Deribit index; contracts settle in the margin currency."
        ),
        exercise_style=EXERCISE_EUROPEAN,
        delivery_type=DELIVERY_CASH,
        last_trading_day_rule=LTD_EXPIRATION_DATE,
        supported_cycles=frozenset({CYCLE_MONTHLY, CYCLE_QUARTERLY}),
        source="Deribit Support, Settlement; Contract Introduction Policy",
        source_as_of="2026-08",
        observes_exchange_holidays=False,
    ),
    ("DERIBIT", "ETH"): ContractConvention(
        exchange="DERIBIT",
        symbol="ETH",
        description="Deribit Ether options (inverse, ETH-margined)",
        expiry_rule=RULE_LAST_FRIDAY,
        settlement_type=SETTLEMENT_FIXED_TIME,
        settlement_basis=(
            "Delivery price computed by Deribit at 08:00 UTC on the expiry date "
            "from the Deribit index; contracts settle in the margin currency."
        ),
        exercise_style=EXERCISE_EUROPEAN,
        delivery_type=DELIVERY_CASH,
        last_trading_day_rule=LTD_EXPIRATION_DATE,
        supported_cycles=frozenset({CYCLE_MONTHLY, CYCLE_QUARTERLY}),
        source="Deribit Support, Settlement; Contract Introduction Policy",
        source_as_of="2026-08",
        observes_exchange_holidays=False,
    ),
}

# Declared-asset-class fallbacks. Used ONLY when the caller states the asset
# class explicitly -- never inferred from the symbol.
ASSET_CLASS_EQUITY = "EQUITY"
ASSET_CLASS_ETF = "ETF"

_US_EQUITY_SRC = (
    "OCC equity options product specifications, "
    "theocc.com/clearance-and-settlement/clearing/equity-options-product-specifications"
)

_US_EQUITY_CONVENTION = ContractConvention(
    exchange="CBOE",
    symbol="<declared EQUITY/ETF>",
    description="Standard US listed single-name equity or ETF option (OCC cleared)",
    expiry_rule=RULE_THIRD_FRIDAY,
    settlement_type=SETTLEMENT_PM,
    settlement_basis="Closing price of the underlying security on the expiration date.",
    exercise_style=EXERCISE_AMERICAN,
    delivery_type=DELIVERY_PHYSICAL,
    last_trading_day_rule=LTD_EXPIRATION_DATE,
    supported_cycles=frozenset({CYCLE_MONTHLY, CYCLE_QUARTERLY, CYCLE_LEAPS}),
    source=_US_EQUITY_SRC,
    source_as_of="2026-08",
)

ASSET_CLASS_DEFAULTS: Dict[Tuple[str, str], ContractConvention] = {
    ("CBOE", ASSET_CLASS_EQUITY): _US_EQUITY_CONVENTION,
    ("CBOE", ASSET_CLASS_ETF): _US_EQUITY_CONVENTION,
}


@dataclass
class OptionExpiryQuery:
    """A request for the conventions of one contract at one monthly-cycle expiry."""

    exchange: str                        # 'CBOE', 'CME', 'EUREX', 'DERIBIT'
    underlying_symbol: str               # 'SPX', 'XSP', 'VIX', 'ODAX', 'BTC', 'AAPL'
    reference_date_iso: str              # 'YYYY-MM-DD'
    target_year: int
    target_month: int
    cycle_type: str = CYCLE_MONTHLY      # 'MONTHLY', 'QUARTERLY', 'LEAPS'
    # Required only for symbols absent from CONTRACT_REGISTRY. Declaring it is
    # an explicit statement by the caller, not an inference from the ticker.
    asset_class: Optional[str] = None    # 'EQUITY', 'ETF'


@dataclass
class OptionsChainConventionReport:
    """Resolved conventions for one contract at one expiry."""

    exchange: str
    underlying_symbol: str
    expiration_date_iso: str             # 'YYYY-MM-DD'
    dte_days: int                        # Signed calendar days: expiration date - reference date
    settlement_type: str                 # AM_SETTLED / PM_SETTLED / AUCTION_SETTLED / FIXED_TIME_SETTLED
    exercise_style: str                  # EUROPEAN / AMERICAN
    delivery_type: str                   # CASH / PHYSICAL / FUTURES
    cycle_type: str
    audit_notes: str
    reference_date_iso: str = ""
    contract_description: str = ""
    expiry_rule: str = ""
    settlement_basis: str = ""
    # For AM-settled contracts this precedes the expiration date -- the option
    # cannot be traded on the morning its settlement value is struck.
    last_trading_date_iso: str = ""
    dte_to_last_trading_day: int = 0
    is_expired: bool = False
    holiday_calendar_applied: bool = False
    holiday_adjusted: bool = False
    source: str = ""
    source_as_of: str = ""
    warnings: Tuple[str, ...] = field(default_factory=tuple)


def _to_date(value: Union[str, date, datetime], label: str) -> date:
    """Coerce an ISO string, date or datetime to a date, with a clear error."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError as exc:
            raise OptionsConventionError(
                f"{label} {value!r} is not a valid ISO 'YYYY-MM-DD' date"
            ) from exc
    raise OptionsConventionError(
        f"{label} must be an ISO string, date or datetime, got {type(value).__name__}"
    )


class OptionsChainExpiryConventionsEngine:
    """Resolves expiry dates, last trading days and settlement conventions.

    Args:
        holiday_calendar: Exchange non-trading days, as ISO strings or dates.
            Either a flat iterable (applies to whichever exchange is queried --
            only safe for single-venue use) or, for multi-venue use, a mapping
            of exchange code to that exchange's non-trading days. A US calendar
            is not a Eurex calendar, so the mapping form is preferred: an
            exchange absent from the mapping is treated as having no calendar
            rather than borrowing another venue's.

            When a calendar applies, an expiry landing on a non-trading day is
            rolled back to the preceding business day, per the Cboe and Eurex
            "or the immediately preceding business day if the Exchange is not
            open on that day" rule. When none applies, no roll-back happens and
            the report carries an explicit warning that the date is unverified
            -- the module will not invent a calendar.
        registry: Override for the bundled contract registry, for callers
            maintaining their own reference data.
    """

    def __init__(
        self,
        holiday_calendar: Optional[
            Union[
                Iterable[Union[str, date, datetime]],
                Mapping[str, Iterable[Union[str, date, datetime]]],
            ]
        ] = None,
        registry: Optional[Mapping[Tuple[str, str], ContractConvention]] = None,
    ) -> None:
        self._holidays_by_exchange: Optional[Dict[str, Set[date]]] = None
        self._flat_holidays: Optional[Set[date]] = None
        if isinstance(holiday_calendar, Mapping):
            self._holidays_by_exchange = {
                str(exchange).strip().upper(): {
                    _to_date(d, "holiday_calendar entry") for d in days
                }
                for exchange, days in holiday_calendar.items()
            }
        elif holiday_calendar is not None:
            self._flat_holidays = {
                _to_date(d, "holiday_calendar entry") for d in holiday_calendar
            }
        self._registry: Mapping[Tuple[str, str], ContractConvention] = (
            CONTRACT_REGISTRY if registry is None else registry
        )

    def _holidays_for(self, exchange: str) -> Optional[Set[date]]:
        """Non-trading days for one exchange, or None if no calendar covers it."""
        if self._holidays_by_exchange is not None:
            return self._holidays_by_exchange.get(exchange)
        return self._flat_holidays

    # --- calendar arithmetic ---------------------------------------------
    @staticmethod
    def _validate_year_month(year: int, month: int) -> None:
        if isinstance(year, bool) or not isinstance(year, int):
            raise OptionsConventionError(
                f"target_year must be an int, got {type(year).__name__}"
            )
        if isinstance(month, bool) or not isinstance(month, int):
            raise OptionsConventionError(
                f"target_month must be an int, got {type(month).__name__}"
            )
        if not 1 <= month <= 12:
            raise OptionsConventionError(f"target_month must be 1-12, got {month}")
        if not date.min.year <= year <= date.max.year:
            raise OptionsConventionError(f"target_year {year} is outside the supported range")

    @classmethod
    def third_friday(cls, year: int, month: int) -> date:
        """Third Friday of the given month.

        Computed arithmetically rather than via ``calendar.monthcalendar``,
        whose week layout depends on the process-global ``setfirstweekday``.
        """
        cls._validate_year_month(year, month)
        first = date(year, month, 1)
        first_friday = first + timedelta(days=(_FRIDAY - first.weekday()) % 7)
        return first_friday + timedelta(days=14)

    @classmethod
    def get_third_friday(cls, year: int, month: int) -> datetime:
        """Third Friday of the given month, as a midnight ``datetime``."""
        d = cls.third_friday(year, month)
        return datetime(d.year, d.month, d.day)

    @classmethod
    def last_friday(cls, year: int, month: int) -> date:
        """Last Friday of the given month -- the Deribit monthly expiry anchor."""
        cls._validate_year_month(year, month)
        if month == 12:
            first_of_next = date(year + 1, 1, 1)
        else:
            first_of_next = date(year, month + 1, 1)
        last_day = first_of_next - timedelta(days=1)
        return last_day - timedelta(days=(last_day.weekday() - _FRIDAY) % 7)

    @classmethod
    def vix_monthly_expiry(cls, year: int, month: int) -> date:
        """Cboe VIX monthly expiry: the Wednesday 30 days before the third
        Friday of the *following* calendar month.

        30 days before a Friday is always a Wednesday, so this rule never
        produces a Friday expiry -- which is why a third-Friday assumption
        misdates every VIX contract.
        """
        cls._validate_year_month(year, month)
        next_year, next_month = (year + 1, 1) if month == 12 else (year, month + 1)
        expiry = cls.third_friday(next_year, next_month) - timedelta(days=30)
        if expiry.weekday() != _WEDNESDAY:  # pragma: no cover - arithmetic invariant
            raise OptionsConventionError(
                f"VIX expiry arithmetic produced {expiry} "
                f"(weekday {expiry.weekday()}), expected a Wednesday"
            )
        return expiry

    @staticmethod
    def _is_business_day(day: date, holidays: Optional[Set[date]]) -> bool:
        return day.weekday() < _SATURDAY and day not in (holidays or set())

    @classmethod
    def _preceding_business_day(
        cls, day: date, holidays: Optional[Set[date]] = None
    ) -> date:
        """Latest business day strictly before ``day``."""
        candidate = day - timedelta(days=1)
        for _ in range(14):
            if cls._is_business_day(candidate, holidays):
                return candidate
            candidate -= timedelta(days=1)
        raise OptionsConventionError(
            f"no business day found within 14 days before {day.isoformat()}; "
            "check the supplied holiday_calendar"
        )

    # --- convention lookup ------------------------------------------------
    def get_contract_convention(
        self,
        exchange: str,
        symbol: str,
        asset_class: Optional[str] = None,
    ) -> ContractConvention:
        """Look up a contract's conventions without resolving a date.

        Use this for series whose expiries are not derivable from a
        (year, month) pair -- SPXW, NDXP, RUTW.

        Raises:
            UnknownContractError: the pair is not registered and no usable
                asset class was declared. Never falls back to a guess.
        """
        if not isinstance(exchange, str) or not exchange.strip():
            raise OptionsConventionError("exchange must be a non-empty string")
        if not isinstance(symbol, str) or not symbol.strip():
            raise OptionsConventionError("underlying_symbol must be a non-empty string")

        exchange_key = exchange.strip().upper()
        symbol_key = symbol.strip().upper()

        registered = self._registry.get((exchange_key, symbol_key))
        if registered is not None:
            return registered

        if asset_class is None:
            raise UnknownContractError(
                f"({exchange_key}, {symbol_key}) is not in the contract registry. "
                "Register it, or declare asset_class explicitly (one of "
                f"{sorted({ac for _, ac in ASSET_CLASS_DEFAULTS})}). This module "
                "does not infer conventions from a ticker string: index series "
                "such as XSP and NDXP are European and cash-settled but PM-settled, "
                "and VIX does not expire on a Friday."
            )

        if not isinstance(asset_class, str) or not asset_class.strip():
            raise OptionsConventionError("asset_class must be a non-empty string or None")

        class_key = asset_class.strip().upper()
        default = ASSET_CLASS_DEFAULTS.get((exchange_key, class_key))
        if default is None:
            raise UnknownContractError(
                f"no default convention for asset_class {class_key!r} on exchange "
                f"{exchange_key!r}; supported: "
                f"{sorted(ex + '/' + ac for ex, ac in ASSET_CLASS_DEFAULTS)}"
            )
        return default

    # --- expiry resolution ------------------------------------------------
    def _resolve_expiry_anchor(
        self, convention: ContractConvention, year: int, month: int
    ) -> date:
        if convention.expiry_rule == RULE_THIRD_FRIDAY:
            return self.third_friday(year, month)
        if convention.expiry_rule == RULE_LAST_FRIDAY:
            return self.last_friday(year, month)
        if convention.expiry_rule == RULE_VIX_30_DAY_WEDNESDAY:
            return self.vix_monthly_expiry(year, month)
        raise UnsupportedCycleError(
            f"{convention.exchange}/{convention.symbol} uses expiry rule "
            f"{convention.expiry_rule}: {convention.settlement_basis} "
            "Use get_contract_convention() for its settlement and exercise terms, "
            "and take the expiry date from the exchange's listed expiry calendar."
        )

    def _check_cycle(self, convention: ContractConvention, cycle: str, month: int) -> None:
        if cycle not in convention.supported_cycles:
            supported = sorted(convention.supported_cycles) or [
                "<none derivable from year+month>"
            ]
            raise UnsupportedCycleError(
                f"cycle_type {cycle!r} is not derivable for "
                f"{convention.exchange}/{convention.symbol}; supported: {supported}"
            )
        if cycle == CYCLE_QUARTERLY and month not in convention.quarterly_months:
            raise UnsupportedCycleError(
                f"{convention.exchange}/{convention.symbol} lists quarterly expiries in "
                f"months {list(convention.quarterly_months)}; month {month} is not one of them"
            )

    def resolve_conventions(
        self, query: OptionExpiryQuery
    ) -> OptionsChainConventionReport:
        """Resolve expiry date, last trading day, settlement conventions and DTE.

        Raises:
            OptionsConventionError: malformed reference date, year or month.
            UnknownContractError: unregistered contract with no declared asset class.
            UnsupportedCycleError: the cycle is not derivable from (year, month)
                for this contract.
        """
        ref_date = _to_date(query.reference_date_iso, "reference_date_iso")
        self._validate_year_month(query.target_year, query.target_month)

        if not isinstance(query.cycle_type, str) or not query.cycle_type.strip():
            raise OptionsConventionError("cycle_type must be a non-empty string")
        cycle = query.cycle_type.strip().upper()

        convention = self.get_contract_convention(
            query.exchange, query.underlying_symbol, query.asset_class
        )
        self._check_cycle(convention, cycle, query.target_month)

        anchor = self._resolve_expiry_anchor(
            convention, query.target_year, query.target_month
        )

        symbol_upper = query.underlying_symbol.strip().upper()
        exchange_upper = query.exchange.strip().upper()

        warnings: List[str] = []
        holiday_adjusted = False
        expiry = anchor
        holidays = self._holidays_for(exchange_upper)
        calendar_applied = False

        if not convention.observes_exchange_holidays:
            # A continuously-traded venue has no exchange closures to roll off.
            pass
        elif holidays is not None:
            calendar_applied = True
            if not self._is_business_day(anchor, holidays):
                expiry = self._preceding_business_day(anchor, holidays)
                holiday_adjusted = True
        else:
            warnings.append(
                f"No holiday calendar supplied for {exchange_upper}: "
                f"{anchor.isoformat()} has not been checked against that exchange's "
                "trading calendar. If it is a non-trading day the actual expiry is the "
                "preceding business day (Good Friday fell on the third Friday in "
                "April 2022 and April 2025)."
            )

        if convention.last_trading_day_rule == LTD_PRECEDING_BUSINESS_DAY:
            last_trading_date = self._preceding_business_day(expiry, holidays)
            if not calendar_applied:
                warnings.append(
                    "Last trading day derived from weekdays only; without a holiday "
                    f"calendar for {exchange_upper} it is not verified against "
                    "exchange closures."
                )
        else:
            last_trading_date = expiry

        dte_days = (expiry - ref_date).days
        dte_to_ltd = (last_trading_date - ref_date).days

        notes = (
            f"OPTIONS CONVENTION [{exchange_upper}:{symbol_upper}] {convention.description}: "
            f"Expiry = {expiry.isoformat()} ({dte_days} calendar DTE) via {convention.expiry_rule}"
            f"{' [holiday-adjusted]' if holiday_adjusted else ''}, "
            f"Last trading day = {last_trading_date.isoformat()} ({dte_to_ltd} DTE), "
            f"Settlement = {convention.settlement_type}, Style = {convention.exercise_style}, "
            f"Delivery = {convention.delivery_type}."
        )
        logger.info(notes)
        for warning in warnings:
            logger.warning("%s:%s %s", exchange_upper, symbol_upper, warning)

        return OptionsChainConventionReport(
            exchange=exchange_upper,
            underlying_symbol=symbol_upper,
            expiration_date_iso=expiry.isoformat(),
            dte_days=dte_days,
            settlement_type=convention.settlement_type,
            exercise_style=convention.exercise_style,
            delivery_type=convention.delivery_type,
            cycle_type=cycle,
            audit_notes=notes,
            reference_date_iso=ref_date.isoformat(),
            contract_description=convention.description,
            expiry_rule=convention.expiry_rule,
            settlement_basis=convention.settlement_basis,
            last_trading_date_iso=last_trading_date.isoformat(),
            dte_to_last_trading_day=dte_to_ltd,
            is_expired=expiry < ref_date,
            holiday_calendar_applied=calendar_applied,
            holiday_adjusted=holiday_adjusted,
            source=convention.source,
            source_as_of=convention.source_as_of,
            warnings=tuple(warnings),
        )

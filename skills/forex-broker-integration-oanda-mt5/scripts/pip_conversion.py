"""
forex-broker-integration-oanda-mt5: reference helpers for forex broker integration.

Covers the forex-specific concerns SKILL.md calls out:

* pip / pipette sizing driven by **broker instrument metadata** (OANDA's
  ``pipLocation``, MT5's ``symbol_info().digits``) rather than guessed from the
  instrument name;
* lot <-> unit conversion;
* overnight swap / rollover accrual, including the value-date-driven triple-swap
  rollover (Wednesday for T+2 instruments, Thursday for T+1 instruments such as
  USD/CAD);
* MT5 terminal liveness, checked independently of the Python bridge process.

Design rule for this module: **never invent a broker-specific number.** Pip
locations, swap rates and settlement conventions are broker- and instrument-
specific, so they are inputs, not defaults. Where a value cannot be sourced the
call raises rather than returning a plausible-looking figure.

Sources for the conventions encoded here are cited in ``references/standards.md``.
"""
from __future__ import annotations

import datetime as _dt
import logging
import math
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

logger = logging.getLogger(__name__)

# --- Pip conventions ---------------------------------------------------------
# These are *fallback heuristics only*, used when broker instrument metadata is
# unavailable. See InstrumentSpec.from_oanda_instrument / from_mt5_symbol_info
# for the authoritative paths.
JPY_PAIRS_PIP_DECIMALS = 2
DEFAULT_PIP_DECIMALS = 4

LOT_SIZES: Dict[str, float] = {
    "standard": 100_000.0,
    "mini": 10_000.0,
    "micro": 1_000.0,
    "nano": 100.0,
}

# OANDA v20 exposes practice and live as entirely separate host families, with
# separate account IDs and separate API tokens. Keeping both sets of hosts as
# named constants is deliberate: code should select an environment once, at
# construction, from environment-specific configuration -- never toggle a shared
# client between them at runtime.
# Source: https://developer.oanda.com/rest-live-v20/development-guide/
OANDA_HOSTS: Dict[str, Dict[str, str]] = {
    "practice": {
        "rest": "https://api-fxpractice.oanda.com",
        "stream": "https://stream-fxpractice.oanda.com",
    },
    "live": {
        "rest": "https://api-fxtrade.oanda.com",
        "stream": "https://stream-fxtrade.oanda.com",
    },
}

# Spot FX settles T+2 by market convention, with these USD pairs settling T+1.
# The settlement convention -- not the calendar weekday -- is what decides which
# overnight rollover carries the weekend, so it drives triple-swap day below.
# Confirm against your broker's contract specifications before relying on it.
T_PLUS_1_PAIRS = frozenset({"USDCAD", "USDTRY", "USDRUB", "USDPHP"})

_WEDNESDAY = 2
_THURSDAY = 3

_LONG_ALIASES = frozenset({"long", "buy"})
_SHORT_ALIASES = frozenset({"short", "sell"})

# Currency codes for which the retail-FX pip convention below (JPY-quoted pairs
# price the pip at the 2nd decimal, everything else at the 4th) is established.
# Deliberately a whitelist rather than a pattern: precious metals (XAU, XAG,
# XPT, XPD), index and crypto CFDs also have 6-character tickers, but their pip
# definitions vary by broker and must come from instrument metadata.
INFERABLE_CURRENCIES = frozenset(
    {
        "AED", "AUD", "BRL", "CAD", "CHF", "CNH", "CNY", "CZK", "DKK", "EUR",
        "GBP", "HKD", "HUF", "IDR", "ILS", "INR", "JPY", "KRW", "MXN", "MYR",
        "NOK", "NZD", "PHP", "PLN", "RUB", "SAR", "SEK", "SGD", "THB", "TRY",
        "TWD", "USD", "ZAR",
    }
)


class UnknownInstrumentError(ValueError):
    """Raised when an instrument's pip/quote metadata is not available."""


def normalize_pair(pair: str) -> str:
    """Normalise ``EUR/USD`` / ``eur_usd`` / ``EURUSD`` to ``EURUSD``."""
    if not isinstance(pair, str) or not pair.strip():
        raise ValueError("pair must be a non-empty string")
    return pair.upper().replace("/", "").replace("_", "").replace("-", "").strip()


@dataclass(frozen=True)
class InstrumentSpec:
    """
    Broker-supplied pricing metadata for one instrument.

    ``pip_location`` follows OANDA's definition: the pip sits at
    ``10 ** pip_location`` (``-4`` for EUR/USD, ``-2`` for USD/JPY, ``0`` for
    some CFDs).
    Source: https://developer.oanda.com/rest-live-v20/primitives-df/
    """

    name: str
    pip_location: int
    display_precision: Optional[int] = None
    base_currency: Optional[str] = None
    quote_currency: Optional[str] = None

    @property
    def pip_size(self) -> float:
        return 10.0 ** self.pip_location

    @classmethod
    def from_oanda_instrument(cls, instrument: Mapping[str, Any]) -> "InstrumentSpec":
        """
        Build a spec from one entry of OANDA v20
        ``GET /v3/accounts/{accountID}/instruments``.

        Uses the broker's own ``pipLocation`` -- the authoritative pip size for
        that instrument on that account -- instead of inferring it from the name.
        """
        try:
            name = str(instrument["name"])
            pip_location = int(instrument["pipLocation"])
        except (KeyError, TypeError, ValueError) as exc:
            raise UnknownInstrumentError(
                "OANDA instrument payload missing usable 'name'/'pipLocation': "
                f"{instrument!r}"
            ) from exc

        display_precision = instrument.get("displayPrecision")
        base, quote = _split_pair_name(name)
        return cls(
            name=normalize_pair(name),
            pip_location=pip_location,
            display_precision=int(display_precision) if display_precision is not None else None,
            base_currency=base,
            quote_currency=quote,
        )

    @classmethod
    def from_mt5_symbol_info(cls, symbol_info: Any) -> "InstrumentSpec":
        """
        Build a spec from a ``MetaTrader5.symbol_info(symbol)`` result.

        MT5 has no pip concept -- it exposes ``digits``/``point``. The standard
        fractional-pricing convention is that 5- and 3-digit quotes are priced in
        pipettes (1/10 pip), so the pip sits one decimal place left of the last
        digit; 4-, 2- and 1-digit quotes price directly in pips. Verify against
        your broker's contract specification for non-FX symbols.
        """
        digits = getattr(symbol_info, "digits", None)
        name = getattr(symbol_info, "name", None)
        if name is None or digits is None:
            raise UnknownInstrumentError(
                f"MT5 symbol_info missing 'name'/'digits': {symbol_info!r}"
            )
        digits = int(digits)
        pip_location = -(digits - 1) if digits in (3, 5) else -digits
        base, quote = _split_pair_name(str(name))
        return cls(
            name=normalize_pair(str(name)),
            pip_location=pip_location,
            display_precision=digits,
            base_currency=base,
            quote_currency=quote,
        )


def _split_pair_name(name: str) -> Tuple[Optional[str], Optional[str]]:
    """Split a 6-character FX pair into (base, quote); return (None, None) otherwise."""
    clean = normalize_pair(name)
    if len(clean) == 6 and clean.isalpha():
        return clean[:3], clean[3:]
    return None, None


def infer_pip_location(pair: str) -> int:
    """
    Last-resort pip location inferred from the instrument name.

    Only two conventions are safe to infer, and only for pairs whose legs are
    both in ``INFERABLE_CURRENCIES``: JPY-quoted FX pairs price the pip at the
    2nd decimal, everything else at the 4th. Metals, indices, crypto and other
    CFDs are deliberately NOT inferred -- their pip definitions vary by broker
    (OANDA reports ``pipLocation: 0`` for some CFDs) and guessing them silently
    mis-sizes orders. Fetch a real ``InstrumentSpec`` for those.
    """
    clean = normalize_pair(pair)
    base, quote = _split_pair_name(clean)
    if base is None or base not in INFERABLE_CURRENCIES or quote not in INFERABLE_CURRENCIES:
        raise UnknownInstrumentError(
            f"Cannot infer a pip location for {pair!r}: it is not a currency pair "
            "with a known pip convention. Supply an InstrumentSpec built from "
            "broker metadata (InstrumentSpec.from_oanda_instrument / "
            "from_mt5_symbol_info)."
        )
    logger.warning(
        "Inferring pip location for %s from its name; prefer broker instrument "
        "metadata (OANDA pipLocation / MT5 digits).",
        clean,
    )
    return -JPY_PAIRS_PIP_DECIMALS if quote == "JPY" else -DEFAULT_PIP_DECIMALS


class ForexPipEngine:
    """
    Pip/pipette conversions and pip valuation.

    Every entry point accepts an optional ``spec``. When supplied, the broker's
    own metadata decides the pip size; when omitted, ``infer_pip_location`` is
    used and a warning is logged.
    """

    @staticmethod
    def pip_size(pair: str, spec: Optional[InstrumentSpec] = None) -> float:
        if spec is not None:
            return spec.pip_size
        return 10.0 ** infer_pip_location(pair)

    @staticmethod
    def pipette_size(pair: str, spec: Optional[InstrumentSpec] = None) -> float:
        """A pipette (fractional pip) is 1/10th of a pip."""
        return ForexPipEngine.pip_size(pair, spec) / 10.0

    @staticmethod
    def price_diff_to_pips(
        pair: str, price_diff: float, spec: Optional[InstrumentSpec] = None
    ) -> float:
        _require_finite(price_diff, "price_diff")
        return price_diff / ForexPipEngine.pip_size(pair, spec)

    @staticmethod
    def pips_to_price_diff(
        pair: str, pips: float, spec: Optional[InstrumentSpec] = None
    ) -> float:
        _require_finite(pips, "pips")
        return pips * ForexPipEngine.pip_size(pair, spec)

    @staticmethod
    def calculate_pip_value(
        pair: str,
        units: float,
        account_currency: str = "USD",
        quote_to_account_fx_rate: Optional[float] = None,
        spec: Optional[InstrumentSpec] = None,
    ) -> float:
        """
        Monetary value of one pip, in the account's currency, for ``units`` of
        exposure.

            pip value (quote ccy)   = units * pip_size
            pip value (account ccy) = pip value (quote ccy) * quote->account rate

        ``quote_to_account_fx_rate`` is REQUIRED whenever the instrument's quote
        currency differs from ``account_currency``. It is deliberately not
        defaulted to 1.0: for a USD account trading USD/JPY that default
        overstates pip value by the USD/JPY rate (~150x), and the error flows
        straight into position sizing.

        The result is intentionally not rounded -- round at the presentation or
        order-submission layer, where the broker's own precision rules apply.
        """
        _require_finite(units, "units")
        if not isinstance(account_currency, str) or not account_currency.strip():
            raise ValueError("account_currency must be a non-empty string")
        account_ccy = account_currency.upper().strip()

        pip = ForexPipEngine.pip_size(pair, spec)

        quote_ccy = spec.quote_currency if spec is not None else None
        if quote_ccy is None:
            _, quote_ccy = _split_pair_name(pair)

        if quote_to_account_fx_rate is None:
            if quote_ccy is None:
                raise UnknownInstrumentError(
                    f"Cannot determine the quote currency of {pair!r}; supply "
                    "quote_to_account_fx_rate explicitly (or an InstrumentSpec "
                    "carrying quote_currency)."
                )
            if quote_ccy != account_ccy:
                raise ValueError(
                    f"{pair} is quoted in {quote_ccy} but the account is denominated "
                    f"in {account_ccy}; pass quote_to_account_fx_rate "
                    f"({quote_ccy}->{account_ccy}) explicitly."
                )
            rate = 1.0
        else:
            _require_finite(quote_to_account_fx_rate, "quote_to_account_fx_rate")
            if quote_to_account_fx_rate <= 0:
                raise ValueError("quote_to_account_fx_rate must be positive")
            rate = float(quote_to_account_fx_rate)

        return units * pip * rate


# --- Overnight swap / rollover ----------------------------------------------


def triple_swap_weekday(pair: str, settlement_days: Optional[int] = None) -> int:
    """
    Weekday (``date.weekday()``: Mon=0) whose overnight rollover carries the
    weekend and therefore accrues 3x swap.

    Triple swap is a value-date effect, not a fixed calendar day: a T+2
    instrument's value date rolls Friday->Monday on the Wednesday rollover,
    while a T+1 instrument's does so on the Thursday rollover. Pass
    ``settlement_days`` explicitly when your broker's convention differs from
    the market default encoded in ``T_PLUS_1_PAIRS``.
    """
    if settlement_days is None:
        settlement_days = 1 if normalize_pair(pair) in T_PLUS_1_PAIRS else 2
    if settlement_days == 2:
        return _WEDNESDAY
    if settlement_days == 1:
        return _THURSDAY
    raise ValueError(
        f"Unsupported settlement convention T+{settlement_days} for {pair!r}; "
        "supply the triple-swap weekday from the broker's contract specification."
    )


def count_triple_swap_rollovers(
    first_rollover: _dt.date, last_rollover: _dt.date, weekday: int
) -> int:
    """
    Number of ``weekday`` rollovers in the inclusive date range.

    A position held across several weeks crosses one triple-swap rollover per
    week, so this must be counted rather than treated as a boolean.
    """
    if not isinstance(first_rollover, _dt.date) or not isinstance(last_rollover, _dt.date):
        raise TypeError("first_rollover and last_rollover must be datetime.date instances")
    if not 0 <= weekday <= 6:
        raise ValueError("weekday must be in 0..6 (Monday..Sunday)")
    if last_rollover < first_rollover:
        return 0
    span = (last_rollover - first_rollover).days + 1
    offset = (weekday - first_rollover.weekday()) % 7
    if offset >= span:
        return 0
    return (span - offset + 6) // 7


@dataclass
class SwapCalculationResult:
    pair: str
    side: str
    lots: float
    hold_days: int
    daily_swap_rate: float
    total_swap_cost: float
    is_triple_swap_applied: bool
    triple_swap_days: int = 0
    effective_swap_days: int = 0
    rate_units: str = "account_currency_per_lot_per_day"


class SwapRolloverCalculator:
    """
    Overnight financing accrual from broker-published swap rates.

    Swap rates are set per broker, per instrument, per side, and they change;
    there is no meaningful default, so ``swap_rates_by_pair`` is required. Rates
    are interpreted in ``rate_units`` -- by default account currency per lot per
    rollover. Brokers that quote swaps in points must have them converted by the
    caller before being passed in.
    """

    def __init__(
        self,
        swap_rates_by_pair: Mapping[str, Mapping[str, float]],
        rate_units: str = "account_currency_per_lot_per_day",
    ) -> None:
        if not swap_rates_by_pair:
            raise ValueError(
                "swap_rates_by_pair is required: supply the broker's published "
                "swap rates per pair and side, e.g. "
                "{'EURUSD': {'long': -5.2, 'short': 1.5}}."
            )
        self.swap_rates: Dict[str, Dict[str, float]] = {}
        for raw_pair, sides in swap_rates_by_pair.items():
            clean = normalize_pair(raw_pair)
            if clean in self.swap_rates:
                # e.g. both "EUR/USD" and "EURUSD" present: one would silently
                # overwrite the other, leaving the accrual on an unpredictable rate.
                raise ValueError(
                    f"Duplicate swap-rate entry for {clean} after normalisation; "
                    "use one spelling per pair."
                )
            normalised: Dict[str, float] = {}
            for raw_side, rate in sides.items():
                side_key = _normalize_side(raw_side)
                _require_finite(rate, f"swap rate for {clean} {side_key}")
                normalised[side_key] = float(rate)
            self.swap_rates[clean] = normalised
        self.rate_units = rate_units

    def calculate_swap(
        self,
        pair: str,
        side: str,
        lots: float,
        hold_days: int = 1,
        includes_wednesday: Optional[bool] = None,
        triple_swap_days: Optional[int] = None,
    ) -> SwapCalculationResult:
        """
        Total financing over ``hold_days`` rollovers, of which
        ``triple_swap_days`` carry the weekend and accrue 3x.

        ``includes_wednesday`` is retained for backwards compatibility and means
        "exactly one triple-swap rollover"; it cannot express a multi-week hold,
        so prefer ``triple_swap_days`` (see ``count_triple_swap_rollovers``).
        """
        clean_pair = normalize_pair(pair)
        side_key = _normalize_side(side)

        if includes_wednesday is not None and triple_swap_days is not None:
            raise ValueError(
                "Pass either includes_wednesday or triple_swap_days, not both."
            )
        if triple_swap_days is None:
            triple_swap_days = 1 if includes_wednesday else 0

        if not isinstance(hold_days, int) or isinstance(hold_days, bool) or hold_days < 0:
            raise ValueError("hold_days must be a non-negative integer")
        if (
            not isinstance(triple_swap_days, int)
            or isinstance(triple_swap_days, bool)
            or triple_swap_days < 0
        ):
            raise ValueError("triple_swap_days must be a non-negative integer")
        if triple_swap_days > hold_days:
            raise ValueError(
                f"triple_swap_days ({triple_swap_days}) cannot exceed hold_days "
                f"({hold_days}): a triple-swap rollover is one of the rollovers held."
            )
        _require_finite(lots, "lots")

        pair_rates = self.swap_rates.get(clean_pair)
        if pair_rates is None:
            raise UnknownInstrumentError(
                f"No swap rates configured for {clean_pair}; add the broker's "
                "published rate rather than accruing an assumed one."
            )
        if side_key not in pair_rates:
            raise ValueError(
                f"No {side_key} swap rate configured for {clean_pair} "
                f"(have: {sorted(pair_rates)})."
            )
        daily_rate = pair_rates[side_key]

        # A triple-swap rollover replaces one rollover's accrual with three.
        effective_days = hold_days + 2 * triple_swap_days
        total_swap = lots * daily_rate * effective_days

        return SwapCalculationResult(
            pair=clean_pair,
            side=side_key.upper(),
            lots=lots,
            hold_days=hold_days,
            daily_swap_rate=daily_rate,
            total_swap_cost=total_swap,
            is_triple_swap_applied=triple_swap_days > 0,
            triple_swap_days=triple_swap_days,
            effective_swap_days=effective_days,
            rate_units=self.rate_units,
        )


# --- MT5 bridge liveness -----------------------------------------------------


def mt5_terminal_connected_check(mt5_module: Any) -> Callable[[], bool]:
    """
    Build a check function over the ``MetaTrader5`` module.

    ``terminal_info()`` returns ``None`` when no terminal is attached, so the
    naive ``mt5.terminal_info().connected`` raises ``AttributeError`` in exactly
    the failure case it is meant to detect. This treats that as "not connected",
    and additionally requires ``trade_allowed`` -- a terminal that is reachable
    but has algorithmic trading switched off cannot execute.
    """

    def _check() -> bool:
        info = mt5_module.terminal_info()
        if info is None:
            return False
        return bool(getattr(info, "connected", False)) and bool(
            getattr(info, "trade_allowed", False)
        )

    return _check


class MT5BridgeMonitor:
    """
    Liveness of the underlying MT5 terminal, checked independently of the Python
    bridge process's own health.

    ``terminal_connected_check_fn`` is required: a monitor that defaults to
    "healthy" reports success for a terminal it never actually checked. It must
    return a boolean; ``None`` and any raised exception are treated as
    unhealthy. Prefer ``mt5_terminal_connected_check`` over passing
    ``terminal_info()`` itself, which is truthy even when disconnected.
    """

    def __init__(self, terminal_connected_check_fn: Callable[[], Any]) -> None:
        if not callable(terminal_connected_check_fn):
            raise TypeError("terminal_connected_check_fn must be callable")
        self.check_fn = terminal_connected_check_fn

    def is_terminal_connected(self) -> Tuple[bool, str]:
        try:
            connected = self.check_fn()
        except Exception as exc:  # any probe failure means "not known healthy"
            logger.exception("MT5 terminal connection check raised")
            return False, f"MT5 Terminal connection check exception: {exc!r}"
        if connected is None or not bool(connected):
            return False, "MT5 Terminal has lost connection to broker server."
        return True, "MT5 Terminal IPC connection healthy."


# --- OANDA environment isolation --------------------------------------------


def oanda_hosts(environment: str) -> Dict[str, str]:
    """
    REST and streaming hosts for one OANDA environment.

    Deliberately has no default: practice and live are separate account
    universes with separate tokens and account IDs, so the environment must be
    named explicitly and sourced from environment-specific configuration -- not
    flipped on a shared client at runtime.
    """
    if not isinstance(environment, str):
        raise TypeError("environment must be a string")
    key = environment.strip().lower()
    if key not in OANDA_HOSTS:
        raise ValueError(
            f"Unknown OANDA environment {environment!r}; expected one of "
            f"{sorted(OANDA_HOSTS)}."
        )
    return dict(OANDA_HOSTS[key])


# --- Lot / unit conversion ---------------------------------------------------


def lots_to_units(lots: float, lot_type: str = "standard") -> float:
    _require_finite(lots, "lots")
    if lot_type not in LOT_SIZES:
        raise ValueError(f"Unknown lot_type '{lot_type}', expected one of {list(LOT_SIZES)}")
    return lots * LOT_SIZES[lot_type]


def units_to_lots(units: float, lot_type: str = "standard") -> float:
    _require_finite(units, "units")
    if lot_type not in LOT_SIZES:
        raise ValueError(f"Unknown lot_type '{lot_type}', expected one of {list(LOT_SIZES)}")
    return units / LOT_SIZES[lot_type]


# --- Module-level convenience wrappers --------------------------------------


def pip_size(pair: str, spec: Optional[InstrumentSpec] = None) -> float:
    return ForexPipEngine.pip_size(pair, spec)


def price_diff_to_pips(
    pair: str, price_diff: float, spec: Optional[InstrumentSpec] = None
) -> float:
    return ForexPipEngine.price_diff_to_pips(pair, price_diff, spec)


# --- Internals ---------------------------------------------------------------


def _require_finite(value: Any, label: str) -> None:
    if isinstance(value, (str, bytes, bytearray)):
        # float("1.5") succeeds, so without this a numeric string would pass
        # validation and fail later with an unrelated TypeError.
        raise TypeError(f"{label} must be a real number, got {value!r}")
    try:
        as_float = float(value)
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{label} must be a real number, got {value!r}") from exc
    if not math.isfinite(as_float):
        raise ValueError(f"{label} must be finite, got {value!r}")


def _normalize_side(side: str) -> str:
    if not isinstance(side, str):
        raise TypeError(f"side must be a string, got {side!r}")
    key = side.strip().lower()
    if key in _LONG_ALIASES:
        return "long"
    if key in _SHORT_ALIASES:
        return "short"
    raise ValueError(
        f"Unrecognised side {side!r}; expected one of "
        f"{sorted(_LONG_ALIASES | _SHORT_ALIASES)}."
    )

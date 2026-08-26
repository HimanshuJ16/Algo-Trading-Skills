"""
multi-currency-pnl-and-fx-conversion: multi-currency PnL aggregation, point-in-time
FX conversion, and price-vs-FX PnL decomposition.

Rate direction convention -- read this before wiring a provider
--------------------------------------------------------------
Every rate in this module means **units of `to_ccy` per one unit of `from_ccy`**::

    get_rate("USD", "INR", t) -> 83.5   # 1 USD buys 83.5 INR
    get_rate("EUR", "USD", t) ->  1.09  # 1 EUR buys 1.09 USD

This is deliberately *not* the interbank pair convention. `EUR/USD = 1.09` is USD
per EUR (matching `get_rate("EUR", "USD")`), but `USD/JPY = 155` is JPY per USD
(matching `get_rate("USD", "JPY")`, **not** `get_rate("JPY", "USD")`). A provider
written by mapping pair strings straight onto ``(from_ccy, to_ccy)`` is therefore
inverted for roughly half the majors, and an inverted rate produces a plausible
number, not an exception. Normalise the vendor's quote orientation first -- see
`currency-pair-quoting-convention-normalization` -- and unit-test the provider
against a pair quoted each way round.

Design notes
------------
* **No built-in rates.** A bare `PointInTimeFXResolver` has no rate table and
  raises `FXRateUnavailableError` on every pair. Earlier revisions of this module
  shipped hard-coded spot rates (EUR 0.92, INR 83.50, JPY 155.0) as the default
  provider and returned ``1.0`` for any pair outside that table. That default
  converted BTC to USD at parity and silently backfilled a five-year backtest
  with one undated snapshot -- precisely the two failures this skill exists to
  prevent. Rates must now be injected.

* **Unavailable is an exception, never 1.0.** Conversion at parity is
  indistinguishable from a correct conversion in the output, so it can only be
  caught by the exception path.

* **Point-in-time is enforced, not assumed.** `HistoricalFXRateStore` resolves
  as-of: the newest observation at or before the requested timestamp, never a
  later one, with an optional staleness bound. `require_timestamp=True` makes an
  untimestamped lookup an error rather than an implicit "latest".

* **Rounding.** Monetary rounding is half-up on the decimal value (via `Decimal`),
  not Python's `round()`, which is half-to-even on a binary float and turns
  ``round(2.675, 2)`` into ``2.67``. Minor units follow ISO 4217; see
  `references/standards.md`.

* **Decomposition convention.** `calculate_decomposed_pnl` splits base-currency
  PnL into a price effect valued at the *entry* rate and an FX effect on the
  *exit* notional. That is exact, but it places the whole price/FX interaction
  term inside the FX component; the term is reported separately so the split can
  be reconciled against an attribution system that assigns it elsewhere. See the
  method docstring.
"""
from bisect import bisect_right
from dataclasses import dataclass
import datetime
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_UP
import logging
import math
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


class FXConversionError(ValueError):
    """Raised on malformed currency input, an unusable rate, or an unsafe lookup."""


class FXRateUnavailableError(FXConversionError):
    """
    Raised when no rate exists for a pair at the requested time.

    Distinct from `FXConversionError` because `PointInTimeFXResolver` treats it as
    "try the next path" (inverse, then pivot) rather than as a hard failure. A
    provider that cannot serve a pair should raise this, or return ``None``.
    """


#: ISO 4217 minor units for every currency whose exponent is not 2, plus the
#: majors this library is most often pointed at. Maintained by SIX Group on
#: behalf of ISO/SNV. Codes absent from this table fall back to
#: `DEFAULT_MINOR_UNITS` with a logged warning -- correct for the overwhelming
#: majority of ISO 4217 codes, which are 2.
ISO_4217_MINOR_UNITS: Dict[str, int] = {
    # exponent 0
    "BIF": 0, "CLP": 0, "DJF": 0, "GNF": 0, "ISK": 0, "JPY": 0, "KMF": 0,
    "KRW": 0, "PYG": 0, "RWF": 0, "UGX": 0, "UYI": 0, "VND": 0, "VUV": 0,
    "XAF": 0, "XOF": 0, "XPF": 0,
    # exponent 3
    "BHD": 3, "IQD": 3, "JOD": 3, "KWD": 3, "LYD": 3, "OMR": 3, "TND": 3,
    # exponent 4
    "CLF": 4, "UYW": 4,
    # exponent 2 -- listed explicitly so the common majors never hit the warning
    "AUD": 2, "CAD": 2, "CHF": 2, "CNY": 2, "DKK": 2, "EUR": 2, "GBP": 2,
    "HKD": 2, "IDR": 2, "ILS": 2, "INR": 2, "MXN": 2, "MYR": 2, "NOK": 2,
    "NZD": 2, "PHP": 2, "PLN": 2, "SEK": 2, "SGD": 2, "THB": 2, "TRY": 2,
    "TWD": 2, "USD": 2, "ZAR": 2,
}

#: Conventional *exchange display* precision for crypto assets. These are not
#: ISO 4217 codes and there is no single authority for them: the protocol-native
#: smallest units are the satoshi (1e-8 BTC) and the wei (1e-18 ETH), while each
#: venue publishes its own quantity and price increments. Override from the
#: venue's instrument metadata rather than trusting these.
DEFAULT_CRYPTO_MINOR_UNITS: Dict[str, int] = {"BTC": 8, "ETH": 8}

#: Applied to any code in neither table. Correct for most ISO 4217 currencies and
#: wrong for the 3- and 0-decimal ones, hence the warning on use.
DEFAULT_MINOR_UNITS = 2

#: Backwards-compatible alias for the pre-2.0 precision table.
CURRENCY_DECIMALS: Dict[str, int] = dict(ISO_4217_MINOR_UNITS)
CURRENCY_DECIMALS.update(DEFAULT_CRYPTO_MINOR_UNITS)

#: A rate provider takes (from_ccy, to_ccy, timestamp) -- both codes already
#: upper-cased -- and returns `to` units per one `from` unit, or None/raises
#: `FXRateUnavailableError` if it cannot serve that pair at that time.
RateProviderFn = Callable[[str, str, Optional[datetime.datetime]], Optional[float]]

_warned_unknown_currencies: set = set()


def normalize_currency(code: str, field: str = "currency") -> str:
    """Upper-case and validate a currency code. Raises on blank/non-string input."""
    if not isinstance(code, str):
        raise FXConversionError(f"{field} must be a string, got {code!r}.")
    cleaned = code.strip().upper()
    if not cleaned:
        raise FXConversionError(f"{field} must be a non-empty currency code.")
    if not cleaned.isalnum():
        raise FXConversionError(
            f"{field} must be alphanumeric (e.g. 'USD', 'BTC'), got {code!r}.")
    return cleaned


def _require_finite(value: object, field: str) -> float:
    """Coerce to float and reject NaN/Inf, which otherwise propagate silently."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        raise FXConversionError(f"{field} must be a real number, got {value!r}.")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise FXConversionError(
            f"{field} must be finite; NaN/Inf compares False against every risk "
            f"threshold downstream. Got {value!r}.")
    return numeric


def _require_valid_rate(rate: object, from_ccy: str, to_ccy: str) -> float:
    """A conversion rate must be finite and strictly positive."""
    numeric = _require_finite(rate, f"FX rate {from_ccy}->{to_ccy}")
    if numeric <= 0.0:
        raise FXConversionError(
            f"FX rate {from_ccy}->{to_ccy} must be strictly positive, got {numeric!r}. "
            f"A zero or negative rate usually means an inverted or unpopulated quote.")
    return numeric


def minor_units_for(currency: str, table: Optional[Mapping[str, int]] = None) -> int:
    """
    Decimal places for `currency`, warning once per unknown code.

    The warning matters: silently applying the 2-decimal default to KWD (3) or
    KRW (0) misstates every rounded figure in that currency.
    """
    ccy = normalize_currency(currency)
    effective = CURRENCY_DECIMALS if table is None else table
    if ccy in effective:
        return effective[ccy]
    if ccy not in _warned_unknown_currencies:
        _warned_unknown_currencies.add(ccy)
        logger.warning(
            "No registered minor-unit precision for %s; defaulting to %d decimals. "
            "Register the venue's precision explicitly if this is a 0- or "
            "3-decimal currency (e.g. JPY, KRW, KWD) or a crypto asset.",
            ccy, DEFAULT_MINOR_UNITS)
    return DEFAULT_MINOR_UNITS


def round_money(amount: float, decimals: int) -> float:
    """
    Half-up monetary rounding at `decimals` places.

    `round()` is half-to-even on the binary float, so `round(2.675, 2)` is 2.67.
    Going through `Decimal(str(amount))` rounds the decimal literal the user
    actually wrote, half-up, which is the settlement convention.
    """
    numeric = _require_finite(amount, "amount")
    if decimals < 0:
        raise FXConversionError(f"decimals must be >= 0, got {decimals!r}.")
    value = Decimal(str(numeric))
    quantum = Decimal(1).scaleb(-decimals)
    # The default 28-digit context cannot quantise a large magnitude -- 1e30 to two
    # places needs 33 significant digits and raises InvalidOperation. Sizing the
    # context to the value keeps high-magnitude 0-decimal currencies (KRW, IDR,
    # VND) working instead of failing at an arbitrary ceiling.
    needed = len(value.as_tuple().digits) + max(0, value.adjusted()) + decimals + 2
    try:
        with localcontext() as ctx:
            ctx.prec = max(ctx.prec, needed)
            return float(value.quantize(quantum, rounding=ROUND_HALF_UP))
    except InvalidOperation as exc:
        raise FXConversionError(
            f"Cannot round {amount!r} to {decimals} places: {exc}") from exc


@dataclass
class CurrencyAmount:
    """A monetary amount that cannot be added to another without conversion."""

    amount: float
    currency: str
    timestamp: Optional[datetime.datetime] = None


@dataclass
class DecomposedPnL:
    """
    Base-currency PnL split into a price effect and an FX effect.

    Reported components reconcile exactly::

        native_price_pnl + fx_translation_pnl == total_base_pnl

    `fx_on_entry_notional` and `price_fx_interaction` further split the FX
    component and sum to it up to one minor unit of rounding. See
    `MultiCurrencyPnLEngine.calculate_decomposed_pnl` for the convention.
    """

    native_currency: str
    base_currency: str
    native_price_pnl: float
    fx_translation_pnl: float
    total_base_pnl: float
    entry_fx_rate: float
    exit_fx_rate: float
    native_pnl: float = 0.0
    fx_on_entry_notional: float = 0.0
    price_fx_interaction: float = 0.0


class HistoricalFXRateStore:
    """
    As-of point-in-time rate provider: the newest observation at or **before** the
    requested timestamp, never a later one.

    Usable directly as a `RateProviderFn`::

        store = HistoricalFXRateStore(max_staleness=datetime.timedelta(days=1))
        store.add_rate("USD", "INR", t, 83.50)
        resolver = PointInTimeFXResolver(rate_provider_fn=store)

    Three failure modes are made loud rather than silent:

    * **Lookahead.** A request earlier than the first observation raises instead
      of borrowing the first known rate.
    * **Staleness.** With `max_staleness` set, a rate older than that bound raises
      rather than valuing a position on a rate from a halted or unquoted session.
    * **Mixed tz-awareness.** Naive and aware datetimes are not comparable;
      mixing them raises with an explicit message instead of a bare `TypeError`.
    """

    def __init__(self, max_staleness: Optional[datetime.timedelta] = None) -> None:
        if max_staleness is not None:
            if not isinstance(max_staleness, datetime.timedelta):
                raise FXConversionError(
                    f"max_staleness must be a timedelta, got {max_staleness!r}.")
            if max_staleness < datetime.timedelta(0):
                raise FXConversionError("max_staleness must not be negative.")
        self.max_staleness = max_staleness
        # Timestamps and rates are kept as parallel sorted lists so `bisect` can be
        # applied to the key list directly. Rebuilding a key list per call made both
        # insert and lookup O(n), which is the wrong complexity for the module's
        # hot path: one lookup per event over a multi-year minute series.
        self._series: Dict[Tuple[str, str], Tuple[List[datetime.datetime], List[float]]] = {}

    def add_rate(
        self,
        from_ccy: str,
        to_ccy: str,
        timestamp: datetime.datetime,
        rate: float,
    ) -> None:
        """Record `rate` = `to_ccy` units per one `from_ccy` unit, observed at `timestamp`."""
        src = normalize_currency(from_ccy, "from_ccy")
        dst = normalize_currency(to_ccy, "to_ccy")
        if src == dst:
            raise FXConversionError(
                f"Refusing to store a {src}->{src} rate; identity is always 1.0.")
        if not isinstance(timestamp, datetime.datetime):
            raise FXConversionError(
                f"timestamp must be a datetime, got {timestamp!r}.")
        validated = _require_valid_rate(rate, src, dst)

        times, rates = self._series.setdefault((src, dst), ([], []))
        if times and (times[0].tzinfo is None) != (timestamp.tzinfo is None):
            raise FXConversionError(
                f"Mixed naive and timezone-aware timestamps for {src}->{dst}. "
                f"Normalise the whole series to UTC before loading it.")
        position = bisect_right(times, timestamp)
        if position and times[position - 1] == timestamp:
            rates[position - 1] = validated  # last write wins
        else:
            times.insert(position, timestamp)
            rates.insert(position, validated)

    def get_rate(
        self, from_ccy: str, to_ccy: str, timestamp: Optional[datetime.datetime] = None
    ) -> float:
        src = normalize_currency(from_ccy, "from_ccy")
        dst = normalize_currency(to_ccy, "to_ccy")
        if src == dst:
            return 1.0
        if timestamp is None:
            raise FXConversionError(
                f"A timestamp is required to resolve {src}->{dst} from a historical "
                f"store; an implicit 'latest' is exactly the lookahead this store exists "
                f"to prevent.")
        if not isinstance(timestamp, datetime.datetime):
            raise FXConversionError(f"timestamp must be a datetime, got {timestamp!r}.")

        entry = self._series.get((src, dst))
        if not entry or not entry[0]:
            raise FXRateUnavailableError(f"No {src}->{dst} rates loaded.")
        times, rates = entry
        if (times[0].tzinfo is None) != (timestamp.tzinfo is None):
            raise FXConversionError(
                f"Cannot compare a {'naive' if timestamp.tzinfo is None else 'tz-aware'} "
                f"lookup timestamp against the {src}->{dst} series. Normalise both to UTC.")

        position = bisect_right(times, timestamp)
        if position == 0:
            raise FXRateUnavailableError(
                f"No {src}->{dst} rate at or before {timestamp.isoformat()}; the earliest "
                f"observation is {times[0].isoformat()}. Using it would be lookahead.")
        observed_at, rate = times[position - 1], rates[position - 1]
        if self.max_staleness is not None and timestamp - observed_at > self.max_staleness:
            raise FXRateUnavailableError(
                f"Newest {src}->{dst} rate at or before {timestamp.isoformat()} was observed "
                f"{timestamp - observed_at} earlier ({observed_at.isoformat()}), exceeding "
                f"max_staleness={self.max_staleness}.")
        return rate

    __call__ = get_rate


class PointInTimeFXResolver:
    """
    Resolves a point-in-time rate over a caller-supplied provider, trying in order:
    direct, inverse, then triangulation through each pivot currency.

    Has **no** built-in rates. Without `rate_provider_fn` every lookup raises
    `FXRateUnavailableError`, because a resolver that invents a number is more
    dangerous than one that refuses.
    """

    def __init__(
        self,
        rate_provider_fn: Optional[RateProviderFn] = None,
        pivot_currencies: Sequence[str] = ("USD",),
        require_timestamp: bool = False,
    ) -> None:
        if rate_provider_fn is not None and not callable(rate_provider_fn):
            raise FXConversionError("rate_provider_fn must be callable.")
        self.rate_provider_fn: RateProviderFn = rate_provider_fn or self._no_rates_configured
        self.pivot_currencies: Tuple[str, ...] = tuple(
            normalize_currency(c, "pivot currency") for c in pivot_currencies)
        self.require_timestamp = bool(require_timestamp)

    @staticmethod
    def _no_rates_configured(
        from_ccy: str, to_ccy: str, timestamp: Optional[datetime.datetime] = None
    ) -> float:
        raise FXRateUnavailableError(
            f"No FX rate provider configured, so {from_ccy}->{to_ccy} cannot be resolved. "
            f"Pass rate_provider_fn=... (e.g. a HistoricalFXRateStore). This resolver "
            f"deliberately ships no rates: a stale or invented default would convert "
            f"silently and produce a plausible, wrong number.")

    def _direct(
        self, from_ccy: str, to_ccy: str, timestamp: Optional[datetime.datetime]
    ) -> Optional[float]:
        """One provider call. Returns None when the provider cannot serve the pair."""
        try:
            raw = self.rate_provider_fn(from_ccy, to_ccy, timestamp)
        except FXRateUnavailableError:
            return None
        except KeyError:
            return None
        if raw is None:
            return None
        return _require_valid_rate(raw, from_ccy, to_ccy)

    def get_rate(
        self, from_ccy: str, to_ccy: str, timestamp: Optional[datetime.datetime] = None
    ) -> float:
        """
        Return `to_ccy` units per one `from_ccy` unit at `timestamp`.

        Raises `FXRateUnavailableError` if no direct, inverse, or pivot path exists.
        """
        src = normalize_currency(from_ccy, "from_ccy")
        dst = normalize_currency(to_ccy, "to_ccy")
        if src == dst:
            return 1.0
        if timestamp is None and self.require_timestamp:
            raise FXConversionError(
                f"require_timestamp=True: refusing to resolve {src}->{dst} without a "
                f"timestamp. An untimestamped lookup silently means 'latest', which "
                f"backfills history with a rate that did not exist yet.")
        if timestamp is not None and not isinstance(timestamp, datetime.datetime):
            raise FXConversionError(f"timestamp must be a datetime, got {timestamp!r}.")

        direct = self._direct(src, dst, timestamp)
        if direct is not None:
            return direct

        inverse = self._direct(dst, src, timestamp)
        if inverse is not None:
            return 1.0 / inverse

        for pivot in self.pivot_currencies:
            if pivot in (src, dst):
                continue
            src_to_pivot = self._direct(src, pivot, timestamp)
            if src_to_pivot is None:
                pivot_to_src = self._direct(pivot, src, timestamp)
                if pivot_to_src is None:
                    continue
                src_to_pivot = 1.0 / pivot_to_src
            pivot_to_dst = self._direct(pivot, dst, timestamp)
            if pivot_to_dst is None:
                dst_to_pivot = self._direct(dst, pivot, timestamp)
                if dst_to_pivot is None:
                    continue
                pivot_to_dst = 1.0 / dst_to_pivot
            return _require_valid_rate(src_to_pivot * pivot_to_dst, src, dst)

        raise FXRateUnavailableError(
            f"No {src}->{dst} rate at "
            f"{timestamp.isoformat() if timestamp else 'unspecified time'}: no direct or "
            f"inverse quote, and no path through pivots {self.pivot_currencies or '()'}.")


class MultiCurrencyPnLEngine:
    """
    Converts, aggregates, and decomposes PnL across currencies.

    Conversion and aggregation always go through the injected resolver, so an
    unavailable rate raises rather than defaulting to parity.
    """

    def __init__(
        self,
        fx_resolver: Optional[PointInTimeFXResolver] = None,
        minor_units: Optional[Mapping[str, int]] = None,
    ) -> None:
        self.fx_resolver = fx_resolver or PointInTimeFXResolver()
        self.minor_units: Dict[str, int] = dict(CURRENCY_DECIMALS)
        if minor_units:
            for code, places in minor_units.items():
                self.register_currency_precision(code, places)

    def register_currency_precision(self, currency: str, decimals: int) -> None:
        """Register venue-specific precision (crypto assets, exotic currencies)."""
        ccy = normalize_currency(currency)
        if not isinstance(decimals, int) or isinstance(decimals, bool) or decimals < 0:
            raise FXConversionError(
                f"decimals for {ccy} must be a non-negative int, got {decimals!r}.")
        self.minor_units[ccy] = decimals

    def round_amount(self, amount: float, currency: str) -> float:
        """Round `amount` half-up to `currency`'s minor units."""
        return round_money(amount, minor_units_for(currency, self.minor_units))

    def _convert_raw(
        self, amount: CurrencyAmount, target_currency: str,
        timestamp: Optional[datetime.datetime] = None,
    ) -> float:
        """Converted value at full precision. Rounding is the caller's decision."""
        if not isinstance(amount, CurrencyAmount):
            raise FXConversionError(
                f"Expected a CurrencyAmount, got {type(amount).__name__}. An untagged "
                f"number cannot be converted: its currency is unknown.")
        value = _require_finite(amount.amount, "CurrencyAmount.amount")
        src = normalize_currency(amount.currency, "CurrencyAmount.currency")
        dst = normalize_currency(target_currency, "target_currency")
        if src == dst:
            return value
        ts = amount.timestamp if amount.timestamp is not None else timestamp
        rate = self.fx_resolver.get_rate(src, dst, ts)
        return value * _require_valid_rate(rate, src, dst)

    def convert(
        self,
        amount: CurrencyAmount,
        target_currency: str,
        timestamp: Optional[datetime.datetime] = None,
    ) -> CurrencyAmount:
        """
        Convert to `target_currency`, rounded to its minor units.

        Always returns a **new** `CurrencyAmount` with an upper-cased code, including
        on the same-currency path -- returning the caller's own object there made the
        result silently aliased and skipped both rounding and code normalisation.
        """
        dst = normalize_currency(target_currency, "target_currency")
        converted = self._convert_raw(amount, dst, timestamp)
        ts = amount.timestamp if amount.timestamp is not None else timestamp
        return CurrencyAmount(
            amount=self.round_amount(converted, dst), currency=dst, timestamp=ts)

    def aggregate_in_base_currency(
        self,
        amounts: Sequence[CurrencyAmount],
        base_currency: str,
        timestamp: Optional[datetime.datetime] = None,
    ) -> float:
        """
        Sum `amounts` in `base_currency`, rounding **once** at the end.

        Rounding each leg before summing accumulates error: with a 0-decimal base
        such as JPY or KRW every leg drifts up to half a unit, so a 1,000-position
        exposure figure can be off by hundreds of units of pure rounding noise.
        """
        base = normalize_currency(base_currency, "base_currency")
        if isinstance(amounts, (str, bytes, CurrencyAmount)):
            raise FXConversionError("amounts must be a sequence of CurrencyAmount.")
        total = 0.0
        for index, item in enumerate(amounts):
            try:
                total += self._convert_raw(item, base, timestamp)
            except FXConversionError as exc:
                raise FXConversionError(
                    f"Cannot aggregate: leg {index} failed to convert to {base}. "
                    f"Dropping it would understate the aggregate silently. ({exc})") from exc
        return self.round_amount(total, base)

    def calculate_decomposed_pnl(
        self,
        entry_price: float,
        exit_price: float,
        quantity: float,
        native_currency: str,
        base_currency: str,
        entry_timestamp: Optional[datetime.datetime] = None,
        exit_timestamp: Optional[datetime.datetime] = None,
        override_entry_fx: Optional[float] = None,
        override_exit_fx: Optional[float] = None,
    ) -> DecomposedPnL:
        r"""
        Split base-currency PnL into a price effect and an FX effect.

        With $q$ the signed quantity, $P_0, P_1$ the native entry/exit prices and
        $X_0, X_1$ the native-to-base rates at entry and exit, total base PnL is::

            total = q * (P1 * X1 - P0 * X0)

        This method reports the **entry-rate price effect** convention:

        * ``native_price_pnl``    = q * (P1 - P0) * X0        -- price move at the entry rate
        * ``fx_translation_pnl``  = q * P1 * (X1 - X0)        -- FX move on the exit notional

        which sums to `total` exactly. Note where the interaction goes: expanding
        the second line gives ``q*P0*(X1-X0)`` (FX on the *entry* notional) plus
        ``q*(P1-P0)*(X1-X0)`` (the price/FX cross term), so **the whole interaction
        term sits inside the FX component**. Multicurrency attribution frameworks
        differ on where it belongs -- Ankrim & Hensel (1994) and the refined
        Karnosky-Singer model report the cross product explicitly rather than
        folding it into either leg -- so a broker or attribution system splitting it
        differently will disagree with `fx_translation_pnl` while agreeing on
        `total_base_pnl`. Both sub-terms are therefore reported as
        `fx_on_entry_notional` and `price_fx_interaction`.

        Rounding: `native_price_pnl` and `total_base_pnl` are each quantised from
        the raw value and `fx_translation_pnl` is taken as the difference, so the
        two components always sum to the reported total. The sub-terms are
        quantised independently and may differ from `fx_translation_pnl` by one
        minor unit.

        `entry_fx_rate`/`exit_fx_rate` are returned **unrounded** -- they are rates,
        not money, and rounding a JPY rate to 0 decimals would be nonsense.
        """
        native_ccy = normalize_currency(native_currency, "native_currency")
        base_ccy = normalize_currency(base_currency, "base_currency")
        p_entry = _require_finite(entry_price, "entry_price")
        p_exit = _require_finite(exit_price, "exit_price")
        qty = _require_finite(quantity, "quantity")

        if override_entry_fx is not None:
            entry_fx = _require_valid_rate(override_entry_fx, native_ccy, base_ccy)
        else:
            entry_fx = _require_valid_rate(
                self.fx_resolver.get_rate(native_ccy, base_ccy, entry_timestamp),
                native_ccy, base_ccy)
        if override_exit_fx is not None:
            exit_fx = _require_valid_rate(override_exit_fx, native_ccy, base_ccy)
        else:
            exit_fx = _require_valid_rate(
                self.fx_resolver.get_rate(native_ccy, base_ccy, exit_timestamp),
                native_ccy, base_ccy)

        if (entry_timestamp is not None and exit_timestamp is not None
                and exit_timestamp < entry_timestamp):
            logger.warning(
                "exit_timestamp %s precedes entry_timestamp %s for a %s position; "
                "the FX rates have been resolved in that order.",
                exit_timestamp.isoformat(), entry_timestamp.isoformat(), native_ccy)

        native_pnl = (p_exit - p_entry) * qty
        native_price_pnl_base = native_pnl * entry_fx
        fx_delta = exit_fx - entry_fx
        fx_on_entry_notional = (p_entry * qty) * fx_delta
        price_fx_interaction = ((p_exit - p_entry) * qty) * fx_delta
        total_base_pnl = native_price_pnl_base + (p_exit * qty) * fx_delta

        rounded_price = self.round_amount(native_price_pnl_base, base_ccy)
        rounded_total = self.round_amount(total_base_pnl, base_ccy)
        # Difference, not an independent rounding, so the components reconcile to
        # the total an auditor reads off the report.
        rounded_fx = self.round_amount(rounded_total - rounded_price, base_ccy)

        return DecomposedPnL(
            native_currency=native_ccy,
            base_currency=base_ccy,
            native_price_pnl=rounded_price,
            fx_translation_pnl=rounded_fx,
            total_base_pnl=rounded_total,
            entry_fx_rate=entry_fx,
            exit_fx_rate=exit_fx,
            native_pnl=self.round_amount(native_pnl, native_ccy),
            fx_on_entry_notional=self.round_amount(fx_on_entry_notional, base_ccy),
            price_fx_interaction=self.round_amount(price_fx_interaction, base_ccy),
        )


# --------------------------------------------------------------------------
# Pre-2.0 module-level helpers. Retained for callers that still import them;
# they take a rate-lookup callable directly and apply no rounding or validation.
# New code should use MultiCurrencyPnLEngine.
# --------------------------------------------------------------------------
def convert(amount: CurrencyAmount, target_currency: str, rate_lookup_fn) -> CurrencyAmount:
    """Deprecated. Use `MultiCurrencyPnLEngine.convert`."""
    if amount.currency.upper() == target_currency.upper():
        return amount
    ts = getattr(amount, "timestamp", None)
    rate = (rate_lookup_fn(amount.currency, target_currency) if ts is None
            else rate_lookup_fn(amount.currency, target_currency, ts))
    return CurrencyAmount(amount.amount * rate, target_currency)


def aggregate_in_base_currency(amounts, base_currency, rate_lookup_fn):
    """Deprecated. Use `MultiCurrencyPnLEngine.aggregate_in_base_currency`."""
    converted = [convert(a, base_currency, rate_lookup_fn) for a in amounts]
    return sum(c.amount for c in converted)

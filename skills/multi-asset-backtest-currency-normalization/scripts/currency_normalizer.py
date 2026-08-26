"""
multi-asset-backtest-currency-normalization: per-currency cash/position ledger with
point-in-time FX translation into a single reporting currency, and an exact
decomposition of NAV change into local (trading) and FX (translation) effects.

Rate convention (the single most important contract in this module)
-------------------------------------------------------------------
``register_fx_rate(from_currency, to_currency, date, rate)`` stores

    rate = units of ``to_currency`` per ONE unit of ``from_currency``

so ``register_fx_rate("EUR", "USD", d, 1.10)`` means 1 EUR = 1.10 USD, and
``register_fx_rate("USD", "JPY", d, 150.0)`` means 1 USD = 150 JPY. This matches the
market BASE/QUOTE reading of a pair (``EUR/USD = 1.10``, ``USD/JPY = 150``): the first
currency is ``from_currency``, the second is ``to_currency``. Conversion is therefore
always a multiplication:

    amount_in_to = amount_in_from * E(from -> to, T)

Inverting this by mistake is the dominant failure mode in multi-currency accounting,
and it is silent: 10,000 EUR booked at 1/1.10 instead of 1.10 gives 9,091 USD, a
plausible-looking number that is wrong by 18%. The inverse direction does not need to
be registered -- it is derived as ``1/rate`` at lookup time -- but an explicitly
registered direct quote always takes precedence over a derived inverse.

Translation basis
-----------------
Rates are treated as **mid rates used for valuation**, mirroring IAS 21 para 23(a)
("foreign currency monetary items shall be translated using the closing rate") and the
way brokers translate statements: Interactive Brokers converts non-base amounts to the
base currency at the close-of-period rate and reports the resulting difference on a
separate "Cash FX Translation Gain/Loss" line. This module reproduces that separation
via ``MultiCurrencyPortfolioNormalizer.attribute_nav_change``.

Valuation is NOT execution. Translating a balance at the mid rate does not move money;
actually converting cash crosses a bid/ask spread and usually pays a broker fee. Those
costs belong to the execution layer and are deliberately not modelled here -- applying
a spread to a pure valuation would understate NAV every single bar. See ``When NOT to
Use`` in SKILL.md.

Limitations (documented, deliberate)
------------------------------------
- **Direct pairs only.** A rate to the reporting currency must exist (or its inverse)
  for every currency held. No triangulation through a third currency: chaining two
  quotes silently compounds two spreads and two stale timestamps, so the caller is
  required to supply the cross explicitly.
- **Snapshot, not a transaction ledger.** ``set_cash_balance`` replaces a balance;
  there is no trade/cash-flow journal. NAV attribution between two snapshots therefore
  attributes external cash flows (deposits, withdrawals, dividends) to the local
  "trading" effect. Net out external flows before interpreting attribution as P&L.
- **Date granularity.** Rates key on ``datetime.date``. Intraday FX fixes are out of
  scope; ``datetime.datetime`` is rejected rather than silently truncated, because
  ``.date()`` on a tz-aware timestamp depends on the timezone and would map the same
  instant to two different rate keys.
- **ISO 4217 alphabetic codes only** (three letters, e.g. ``USD``, ``JPY``). Crypto and
  stablecoin tickers are outside ISO 4217 and outside the fiat-translation semantics
  above.
- **Float arithmetic.** Balances are IEEE-754 doubles, not ``Decimal``. Adequate for a
  backtest NAV series; not a substitute for a books-and-records ledger, where the
  per-currency minor unit (ISO 4217 defines one per currency) must be rounded
  explicitly.
"""
import bisect
import datetime
import logging
import math
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: ISO 4217 alphabetic currency codes are exactly three letters (ISO 4217, maintained
#: by SIX on behalf of ISO). Enforced so that "usd ", "US" or "EURO" fail loudly
#: instead of opening a second, ghost cash ledger alongside the real one.
_ISO4217_ALPHA_RE = re.compile(r"^[A-Z]{3}$")

#: Two rates for the same directed pair and date are treated as the same quote when
#: they agree to this relative tolerance. Anything larger is a data conflict.
_RATE_CONFLICT_RTOL = 1e-12


class CurrencyMismatchError(ValueError):
    """Raised on missing/invalid FX rates, invalid currency codes, or unit mixing."""
    pass


def normalize_currency_code(currency: str) -> str:
    """
    Normalizes and validates an ISO 4217 alphabetic currency code.

    Strips surrounding whitespace and upper-cases. Raises rather than silently
    accepting a malformed code: an unnormalized ``"USD "`` would otherwise become a
    distinct dictionary key from ``"USD"`` and split one cash balance into two ledgers
    that never reconcile.
    """
    if not isinstance(currency, str):
        raise CurrencyMismatchError(
            f"Currency code must be a string, got {type(currency).__name__}."
        )
    code = currency.strip().upper()
    if not _ISO4217_ALPHA_RE.match(code):
        raise CurrencyMismatchError(
            f"Invalid currency code {currency!r}: expected a three-letter ISO 4217 "
            f"alphabetic code such as 'USD', 'EUR' or 'JPY'."
        )
    return code


def _validate_date(date: datetime.date, label: str = "date") -> datetime.date:
    """
    Requires a plain ``datetime.date``.

    ``datetime.datetime`` is a subclass of ``date``, so it passes an ``isinstance``
    check and every type hint, yet hashes differently: a rate registered under
    ``datetime(2026, 7, 24, 16, 0)`` is invisible to a lookup for
    ``date(2026, 7, 24)``. Rejecting it makes that mismatch loud, and avoids the
    timezone ambiguity of truncating a tz-aware timestamp with ``.date()``.
    """
    if isinstance(date, datetime.datetime):
        raise CurrencyMismatchError(
            f"{label} must be a datetime.date, not a datetime.datetime "
            f"({date!r}). Convert explicitly in the caller's timezone, e.g. "
            f"ts.astimezone(valuation_tz).date()."
        )
    if not isinstance(date, datetime.date):
        raise CurrencyMismatchError(
            f"{label} must be a datetime.date, got {type(date).__name__}."
        )
    return date


def _validate_finite(value: float, label: str) -> float:
    """Rejects NaN/Inf before it can propagate silently into NAV."""
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise CurrencyMismatchError(
            f"{label} must be a real number, got {value!r}."
        ) from exc
    if not math.isfinite(numeric):
        raise CurrencyMismatchError(
            f"{label} must be finite, got {numeric!r}. A non-finite input propagates "
            f"silently through every downstream sum and yields a NaN NAV."
        )
    return numeric


@dataclass
class PositionValuation:
    """
    An open position marked at a price quoted in its own local currency.

    Negative ``quantity`` (a short) and negative ``price_in_local`` (possible for
    spreads and for physically-settled futures, as WTI demonstrated in April 2020) are
    both legitimate and accepted; non-finite values are not.
    """
    symbol: str
    local_currency: str
    quantity: float
    price_in_local: float

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise CurrencyMismatchError(
                "PositionValuation.symbol must be a non-empty string."
            )
        self.symbol = self.symbol.strip()
        self.local_currency = normalize_currency_code(self.local_currency)
        self.quantity = _validate_finite(self.quantity, f"{self.symbol} quantity")
        self.price_in_local = _validate_finite(
            self.price_in_local, f"{self.symbol} price_in_local"
        )

    @property
    def local_value(self) -> float:
        """Mark-to-market value in the position's own local currency."""
        return self.quantity * self.price_in_local


@dataclass
class MultiCurrencyNAV:
    """
    A dated NAV snapshot.

    Every dictionary is explicitly tagged ``_local`` or ``_reporting`` so the two can
    never be summed together by accident. ``*_local_by_currency`` values are in each
    currency's own units and are NOT comparable across keys; only ``*_reporting_*``
    values may be added.
    """
    reporting_currency: str
    date: datetime.date
    total_cash_reporting: float
    total_positions_reporting: float
    total_nav_reporting: float
    cash_local_by_currency: Dict[str, float]
    cash_reporting_by_currency: Dict[str, float]
    positions_local_by_currency: Dict[str, float]
    positions_reporting_by_currency: Dict[str, float]
    positions_reporting_by_symbol: Dict[str, float]
    fx_rates_used: Dict[str, float]
    fx_rate_dates_used: Dict[str, datetime.date] = field(default_factory=dict)

    @property
    def local_value_by_currency(self) -> Dict[str, float]:
        """Cash + positions per currency, in that currency's own units."""
        combined: Dict[str, float] = dict(self.cash_local_by_currency)
        for currency, value in self.positions_local_by_currency.items():
            combined[currency] = combined.get(currency, 0.0) + value
        return combined

    @property
    def stale_fx_currencies(self) -> Dict[str, int]:
        """Currencies translated at a rate older than the snapshot date, by age in days."""
        return {
            currency: (self.date - rate_date).days
            for currency, rate_date in self.fx_rate_dates_used.items()
            if rate_date != self.date
        }


@dataclass
class CurrencyAttribution:
    """Exact three-way decomposition of one currency's reporting-currency change."""
    currency: str
    opening_local: float
    closing_local: float
    opening_rate: float
    closing_rate: float
    local_effect: float
    fx_translation_effect: float
    interaction_effect: float

    @property
    def total_effect(self) -> float:
        return self.local_effect + self.fx_translation_effect + self.interaction_effect


@dataclass
class NAVChangeAttribution:
    """
    Decomposition of the reporting-currency NAV change between two snapshots.

    ``local_effect + fx_translation_effect + interaction_effect`` reconstructs
    ``total_nav_change`` exactly (to floating-point precision) -- the interaction term
    is reported rather than absorbed, because a two-way split does not sum to the total
    and quietly misattributes the difference.
    """
    reporting_currency: str
    start_date: datetime.date
    end_date: datetime.date
    opening_nav_reporting: float
    closing_nav_reporting: float
    total_nav_change: float
    local_effect: float
    fx_translation_effect: float
    interaction_effect: float
    by_currency: Dict[str, CurrencyAttribution]


class MultiCurrencyPortfolioNormalizer:
    """
    Per-currency cash and position ledger with point-in-time FX translation into one
    reporting currency.

    Cash balances stay in their native currency (matching how brokers actually hold
    them -- an IBKR Universal Account keeps a balance per currency and translates only
    for statement presentation) and are converted only at aggregation time.

    Args:
        reporting_currency: ISO 4217 code all aggregates are expressed in.
        max_staleness_days: 0 (default) requires an FX rate registered for exactly the
            valuation date. A positive value permits falling back to the most recent
            rate on or before that date, within this many calendar days. The search is
            strictly backwards -- a rate dated after the valuation date is never used,
            so the fallback cannot introduce look-ahead bias. This exists because
            global portfolios span mismatched calendars: the ECB, for instance,
            publishes euro reference rates only on TARGET working days, so a portfolio
            valued on Good Friday has no same-day euro reference rate at all.
    """

    def __init__(self, reporting_currency: str = "USD", max_staleness_days: int = 0):
        self.reporting_currency = normalize_currency_code(reporting_currency)
        if not isinstance(max_staleness_days, int) or isinstance(max_staleness_days, bool):
            raise CurrencyMismatchError("max_staleness_days must be an int.")
        if max_staleness_days < 0:
            raise CurrencyMismatchError(
                f"max_staleness_days must be >= 0, got {max_staleness_days}."
            )
        self.max_staleness_days = max_staleness_days
        self.cash_balances: Dict[str, float] = {}
        self.positions: List[PositionValuation] = []
        # (from, to) -> {date: rate}, with a parallel sorted date list for as-of lookup.
        self._fx_rates: Dict[Tuple[str, str], Dict[datetime.date, float]] = {}
        self._fx_dates: Dict[Tuple[str, str], List[datetime.date]] = {}

    # ------------------------------------------------------------------ ledger

    def set_cash_balance(self, currency: str, amount: float) -> None:
        """
        Sets (replaces) the cash balance for one currency.

        A negative balance is legitimate and is not an error: buying a foreign-currency
        instrument without pre-funding it leaves a debit in that currency -- IBKR
        creates a margin loan in the traded currency rather than auto-converting the
        base currency.
        """
        code = normalize_currency_code(currency)
        self.cash_balances[code] = _validate_finite(amount, f"{code} cash balance")

    def add_position(self, pos: PositionValuation) -> None:
        """Adds an open position valuation to the ledger."""
        if not isinstance(pos, PositionValuation):
            raise CurrencyMismatchError(
                f"add_position expects a PositionValuation, got {type(pos).__name__}."
            )
        self.positions.append(pos)

    # --------------------------------------------------------------- fx rates

    def register_fx_rate(
        self,
        from_currency: str,
        to_currency: str,
        date: datetime.date,
        rate: float,
        allow_overwrite: bool = False,
    ) -> None:
        """
        Registers a point-in-time rate: ``rate`` units of ``to_currency`` per one unit
        of ``from_currency`` on ``date``.

        Only the direction given is stored. The inverse is derived at lookup time, so
        registering an explicit quote for the opposite direction can no longer
        overwrite this one -- the two are allowed to disagree, as a real bid and offer
        do. Re-registering the *same* direction and date with a materially different
        rate raises unless ``allow_overwrite`` is set, since that indicates two sources
        disagreeing rather than an intentional correction.
        """
        c1 = normalize_currency_code(from_currency)
        c2 = normalize_currency_code(to_currency)
        _validate_date(date)
        numeric_rate = _validate_finite(rate, f"FX rate {c1}->{c2} on {date}")

        if numeric_rate <= 0.0:
            raise CurrencyMismatchError(
                f"FX rate must be strictly positive: got {numeric_rate} for "
                f"{c1}->{c2} on {date}."
            )
        if c1 == c2:
            if not math.isclose(numeric_rate, 1.0, rel_tol=_RATE_CONFLICT_RTOL):
                raise CurrencyMismatchError(
                    f"Self-conversion rate {c1}->{c2} must be 1.0, got {numeric_rate}."
                )
            return

        key = (c1, c2)
        existing = self._fx_rates.get(key, {}).get(date)
        if existing is not None and not allow_overwrite:
            if not math.isclose(existing, numeric_rate, rel_tol=_RATE_CONFLICT_RTOL):
                raise CurrencyMismatchError(
                    f"Conflicting FX rate for {c1}->{c2} on {date}: already registered "
                    f"{existing!r}, received {numeric_rate!r}. Reconcile the sources, "
                    f"or pass allow_overwrite=True to replace it deliberately."
                )
            return

        if key not in self._fx_rates:
            self._fx_rates[key] = {}
            self._fx_dates[key] = []
        if date not in self._fx_rates[key]:
            bisect.insort(self._fx_dates[key], date)
        self._fx_rates[key][date] = numeric_rate

    def _lookup_directed_rate(
        self, key: Tuple[str, str], date: datetime.date, allow_stale: bool
    ) -> Optional[Tuple[float, datetime.date]]:
        """
        Exact-date rate, or -- when ``allow_stale`` and a staleness allowance is
        configured -- the most recent rate at or before ``date``. Never looks forward.
        """
        by_date = self._fx_rates.get(key)
        if not by_date:
            return None
        if date in by_date:
            return by_date[date], date
        if not allow_stale or self.max_staleness_days <= 0:
            return None
        dates = self._fx_dates[key]
        idx = bisect.bisect_right(dates, date)
        if idx == 0:
            return None
        candidate = dates[idx - 1]
        if (date - candidate).days > self.max_staleness_days:
            return None
        return by_date[candidate], candidate

    def resolve_fx_rate(
        self, from_currency: str, to_currency: str, date: datetime.date
    ) -> Tuple[float, datetime.date]:
        """
        Returns ``(rate, effective_date)`` for ``from -> to`` on ``date``.

        Resolution order: identity, then an exact-date quote (direct first, else the
        reciprocal of the opposite direction), and only if neither exists the same two
        in stale form. Freshness outranks direction, so a same-day derived inverse is
        never passed over in favour of a stale direct quote. ``effective_date`` equals
        ``date`` unless a stale rate was accepted under ``max_staleness_days``, which
        makes the staleness auditable instead of invisible.
        """
        c1 = normalize_currency_code(from_currency)
        c2 = normalize_currency_code(to_currency)
        _validate_date(date)

        if c1 == c2:
            return 1.0, date

        for allow_stale in (False, True):
            direct = self._lookup_directed_rate((c1, c2), date, allow_stale)
            if direct is not None:
                return direct
            inverse = self._lookup_directed_rate((c2, c1), date, allow_stale)
            if inverse is not None:
                inverse_rate, effective_date = inverse
                return 1.0 / inverse_rate, effective_date
            if self.max_staleness_days <= 0:
                break

        raise CurrencyMismatchError(
            f"Missing FX rate for conversion '{c1}' to '{c2}' on {date}"
            + (
                f" (no rate within {self.max_staleness_days} day(s) before it either)."
                if self.max_staleness_days > 0
                else "."
            )
        )

    def get_fx_rate(
        self, from_currency: str, to_currency: str, date: datetime.date
    ) -> float:
        """Retrieves the FX conversion rate for ``date``. See ``resolve_fx_rate``."""
        rate, effective_date = self.resolve_fx_rate(from_currency, to_currency, date)
        if effective_date != date:
            logger.warning(
                "Using stale FX rate for %s->%s on %s: effective date %s (%d day(s) old).",
                normalize_currency_code(from_currency),
                normalize_currency_code(to_currency),
                date,
                effective_date,
                (date - effective_date).days,
            )
        return rate

    def convert_amount(
        self, amount: float, from_currency: str, to_currency: str, date: datetime.date
    ) -> float:
        """
        Converts an amount from one currency to another at the ``date`` rate.

        This is a valuation translation at the registered (mid) rate. It does not model
        the bid/ask spread or broker fee incurred by an actual currency conversion.
        """
        numeric_amount = _validate_finite(amount, "amount")
        return numeric_amount * self.get_fx_rate(from_currency, to_currency, date)

    # -------------------------------------------------------------------- nav

    def compute_total_nav(self, date: datetime.date) -> MultiCurrencyNAV:
        """
        Computes NAV in the reporting currency, translating every cash balance and
        position at the ``date`` rate.

        Raises ``CurrencyMismatchError`` if any held currency has no usable rate: a
        partial NAV that silently omits one currency is more dangerous than no NAV,
        because it still looks like a valid number.
        """
        _validate_date(date)

        total_cash_rep = 0.0
        total_pos_rep = 0.0
        cash_local: Dict[str, float] = {}
        cash_rep: Dict[str, float] = {}
        pos_local: Dict[str, float] = {}
        pos_rep: Dict[str, float] = {}
        pos_rep_by_symbol: Dict[str, float] = {}
        rates_used: Dict[str, float] = {}
        rate_dates_used: Dict[str, datetime.date] = {}

        def _rate_for(currency: str) -> float:
            if currency not in rates_used:
                rate, effective_date = self.resolve_fx_rate(
                    currency, self.reporting_currency, date
                )
                rates_used[currency] = rate
                rate_dates_used[currency] = effective_date
                if effective_date != date:
                    logger.warning(
                        "NAV on %s translates %s at a stale rate dated %s (%d day(s) old).",
                        date, currency, effective_date, (date - effective_date).days,
                    )
            return rates_used[currency]

        for currency, amount in self.cash_balances.items():
            converted = amount * _rate_for(currency)
            total_cash_rep += converted
            cash_local[currency] = cash_local.get(currency, 0.0) + amount
            cash_rep[currency] = cash_rep.get(currency, 0.0) + converted

        for pos in self.positions:
            currency = pos.local_currency
            local_value = pos.local_value
            converted = local_value * _rate_for(currency)
            total_pos_rep += converted
            pos_local[currency] = pos_local.get(currency, 0.0) + local_value
            pos_rep[currency] = pos_rep.get(currency, 0.0) + converted
            # Accumulate: two lots of the same symbol are one exposure, not a
            # last-one-wins overwrite that silently drops a leg from the breakdown.
            pos_rep_by_symbol[pos.symbol] = (
                pos_rep_by_symbol.get(pos.symbol, 0.0) + converted
            )

        total_nav = total_cash_rep + total_pos_rep

        logger.info(
            "Multi-currency NAV (%s): cash %.2f, positions %.2f, total %.2f %s",
            date, total_cash_rep, total_pos_rep, total_nav, self.reporting_currency,
        )

        return MultiCurrencyNAV(
            reporting_currency=self.reporting_currency,
            date=date,
            total_cash_reporting=total_cash_rep,
            total_positions_reporting=total_pos_rep,
            total_nav_reporting=total_nav,
            cash_local_by_currency=cash_local,
            cash_reporting_by_currency=cash_rep,
            positions_local_by_currency=pos_local,
            positions_reporting_by_currency=pos_rep,
            positions_reporting_by_symbol=pos_rep_by_symbol,
            fx_rates_used=rates_used,
            fx_rate_dates_used=rate_dates_used,
        )

    # ------------------------------------------------------------ attribution

    def attribute_nav_change(
        self, opening: MultiCurrencyNAV, closing: MultiCurrencyNAV
    ) -> NAVChangeAttribution:
        """
        Splits the reporting-currency NAV change between two snapshots into the part
        driven by local-currency value changes and the part driven purely by FX moves.

        For each currency, with local value V and rate E:

            V1*E1 - V0*E0 = (V1 - V0)*E0        <- local effect
                          + V0*(E1 - E0)        <- FX translation effect
                          + (V1 - V0)*(E1 - E0) <- interaction effect

        The identity is exact, so the three components always reconstruct the total.
        This mirrors IAS 21 para 28 (exchange differences on monetary items recognised
        in profit or loss) and the "Cash FX Translation Gain/Loss" line IBKR reports
        separately from trading results.

        A currency present in only one snapshot has no rate recorded for the other one;
        that rate is looked up from the rate table, and a missing lookup raises rather
        than being defaulted, since assuming an unchanged rate would book a genuine FX
        move as a trading gain.

        Note: with no cash-flow journal (see module docstring), deposits, withdrawals
        and dividends land in ``local_effect``. An inter-currency transfer inside the
        period (converting a EUR balance to USD, say) is likewise indistinguishable
        from a trading result and spreads across all three buckets, since the two
        snapshots cannot show at which rate the conversion actually executed. Net
        external flows and conversions out before reading these components as P&L.
        """
        if not isinstance(opening, MultiCurrencyNAV) or not isinstance(
            closing, MultiCurrencyNAV
        ):
            raise CurrencyMismatchError(
                "attribute_nav_change expects two MultiCurrencyNAV snapshots."
            )
        if opening.reporting_currency != closing.reporting_currency:
            raise CurrencyMismatchError(
                f"Cannot attribute across reporting currencies: "
                f"{opening.reporting_currency} vs {closing.reporting_currency}."
            )
        if opening.date >= closing.date:
            raise CurrencyMismatchError(
                f"Opening snapshot ({opening.date}) must predate the closing snapshot "
                f"({closing.date}); the arguments look reversed."
            )

        opening_local = opening.local_value_by_currency
        closing_local = closing.local_value_by_currency

        def _rate(snapshot: MultiCurrencyNAV, currency: str) -> float:
            if currency in snapshot.fx_rates_used:
                return snapshot.fx_rates_used[currency]
            rate, _ = self.resolve_fx_rate(
                currency, snapshot.reporting_currency, snapshot.date
            )
            return rate

        by_currency: Dict[str, CurrencyAttribution] = {}
        total_local = 0.0
        total_fx = 0.0
        total_interaction = 0.0

        for currency in sorted(set(opening_local) | set(closing_local)):
            v0 = opening_local.get(currency, 0.0)
            v1 = closing_local.get(currency, 0.0)
            e0 = _rate(opening, currency)
            e1 = _rate(closing, currency)

            local_effect = (v1 - v0) * e0
            fx_effect = v0 * (e1 - e0)
            interaction = (v1 - v0) * (e1 - e0)

            total_local += local_effect
            total_fx += fx_effect
            total_interaction += interaction

            by_currency[currency] = CurrencyAttribution(
                currency=currency,
                opening_local=v0,
                closing_local=v1,
                opening_rate=e0,
                closing_rate=e1,
                local_effect=local_effect,
                fx_translation_effect=fx_effect,
                interaction_effect=interaction,
            )

        total_change = closing.total_nav_reporting - opening.total_nav_reporting

        logger.info(
            "NAV attribution %s -> %s (%s): total %.2f = local %.2f + fx %.2f "
            "+ interaction %.2f",
            opening.date, closing.date, opening.reporting_currency,
            total_change, total_local, total_fx, total_interaction,
        )

        return NAVChangeAttribution(
            reporting_currency=opening.reporting_currency,
            start_date=opening.date,
            end_date=closing.date,
            opening_nav_reporting=opening.total_nav_reporting,
            closing_nav_reporting=closing.total_nav_reporting,
            total_nav_change=total_change,
            local_effect=total_local,
            fx_translation_effect=total_fx,
            interaction_effect=total_interaction,
            by_currency=by_currency,
        )

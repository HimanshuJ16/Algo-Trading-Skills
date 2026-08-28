"""
survivorship-bias-free-universe-construction: point-in-time universe registry with
ticker-recycling resolution, terminal delisting settlement, and a survivorship-bias
coverage audit.

Purpose
-------
Answer two questions a backtest cannot answer from a current-membership snapshot:
*which securities were investable on date T*, and *what did a position in a name that
stopped trading actually settle at*. Dropping the second question is the expensive half:
a universe that contains Lehman Brothers but silently deletes the position when the
ticker stops printing has removed the loss, which is the bias in a different costume.

Date convention (read this before loading any vendor data)
----------------------------------------------------------
``delisting_date`` is the **last date the security traded** and membership is
**inclusive** on both ends::

    listing_date <= as_of_date <= delisting_date

This follows CRSP, whose delisting date "is set to the last date of available price
data" (WRDS, CRSP stock database documentation). Index-membership feeds usually do
**not** use this convention: S&P Dow Jones Indices makes constituent changes effective
prior to the open of trading on the effective date, so an index deletion date is
half-open -- the name is already out on that date. ``point-in-time-index-constituent-
tracking`` implements that half-open axis. Feeding an index deletion date into this
registry unconverted keeps every deleted name one session too long; subtract one
trading session first.

The ambiguity is not academic. Twitter has four defensible "delisting dates": the
merger became effective 2022-10-27, the last NYSE trade printed 2022-10-27 (close
$53.70), trading was suspended before the open on 2022-10-28, and the NYSE Form 25-NSE
removed the class from listing at the opening of business on 2022-11-08. Only the
second is this field.

Terminal settlement
-------------------
A delisted security's terminal value is specified one of exactly two ways, never both,
never neither:

- ``delisting_settlement_price`` -- a known absolute per-share cash value. Merger
  consideration ($54.20 for TWTR, which is *not* the $53.70 last close), a liquidation
  distribution, a tender price.
- ``delisting_return`` -- a fractional return from the last traded price to the
  terminal value, for when the terminal value is not known and must be imputed. The
  caller supplies ``last_traded_price`` at settlement time.

Registration **fails** when a delisted security carries neither. That is deliberate:
the previous behaviour defaulted the settlement price to 0.0, so a merger whose
consideration the caller forgot to populate settled at $0 -- a silent, total,
fabricated loss on a name that actually paid cash.

Bankruptcy is not automatically zero
------------------------------------
"Bankruptcy therefore 0.00" is a modelling choice, not a fact, and usually a wrong one.
Lehman Brothers' common stock left the NYSE in September 2008 but went on trading over
the counter as LEHMQ at non-zero prices for years; a position was sellable after the
Chapter 11 filing even though common holders ultimately recovered nothing. CRSP's own
delisting return is computed from the last exchange price to the delisting amount, and
that return is *missing* for most performance-related delistings. Standard research
practice imputes a replacement:

- **-30%** for NYSE/AMEX. Shumway, T. (1997), "The Delisting Bias in CRSP Data",
  *Journal of Finance* 52(1), 327-340, which reports an average delisting return of
  -29.9% over 1962-1993.
- **-55%** for Nasdaq. Shumway, T. and Warther, V.A. (1999), "The Delisting Bias in
  CRSP's Nasdaq Data and Its Implications for the Size Effect", *Journal of Finance*
  54(6).

These are imputations for a *missing* performance-related delisting return. They must
never overwrite an observed one, and they are estimates from US equity samples of that
era -- not constants, and not applicable to other venues or asset classes without
re-estimation.

Ticker recycling
----------------
The registry is keyed by ``security_id``, not by ticker. Tickers are reused: the old
General Motors Corporation traded as ``GM`` until its 2009 bankruptcy (moving to
``GMGMQ``, then ``MTLQQ`` effective 2009-07-15), and the new General Motors Company
took the ``GM`` ticker at its 2010-11-18 IPO. Keyed by ticker, the second registration
overwrites the first and the old issuer vanishes from every pre-2010 universe -- which
reintroduces exactly the survivorship bias this module exists to remove. Supply a
stable identifier (CRSP PERMNO, CUSIP, SEDOL, FIGI). ``security_id`` defaults to the
symbol, which is safe only for a universe with no recycling in it.

Limitations (documented, deliberate)
------------------------------------
- **Not a data source.** A clean audit means the metadata supplied is internally
  consistent. Metadata reverse-engineered from a current-membership table produces a
  survivorship-biased universe that audits clean.
- **Not an index-membership model.** Listing and delisting bound *tradability*, not
  index membership. A name can be listed and not in the index. For membership use
  ``point-in-time-index-constituent-tracking``.
- **Not an announcement-timing auditor.** This resolves when a security traded, not
  when its fate became knowable. See ``backtest-look-ahead-in-universe-selection``.
- **No price adjustment.** Splits and dividends belong to
  ``corporate-action-adjusted-backtesting``. ``last_traded_price`` must be on the same
  basis as the position quantity.
- **Cash values are IEEE-754 floats**, adequate for backtest P&L, not for books and
  records. Use ``decimal.Decimal`` upstream where exactness is required.
"""
import datetime
import logging
import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

#: Imputed delisting return for a **missing** performance-related delisting return on
#: NYSE/AMEX. Shumway (1997), *Journal of Finance* 52(1), reports an average delisting
#: return of -29.9% over 1962-1993; -30% is the figure the literature applies. An
#: estimate from a US equity sample of that era -- not a constant, and never a
#: substitute for an observed delisting return.
SHUMWAY_1997_NYSE_AMEX_DELISTING_RETURN = -0.30

#: Imputed delisting return for a **missing** performance-related delisting return on
#: Nasdaq. Shumway and Warther (1999), *Journal of Finance* 54(6). Same caveats.
SHUMWAY_WARTHER_1999_NASDAQ_DELISTING_RETURN = -0.55


class UniverseError(ValueError):
    """Raised when instrument metadata is missing, ambiguous, or internally invalid."""


class DelistingReason(str, Enum):
    """Why a security stopped trading.

    Deliberately coarser than CRSP's three-digit ``DLSTCD``, whose leading digit
    carries the same distinction: 100 still trading, 200s merger, 300s exchange, 400s
    liquidation, 500s dropped (performance-related). Map a vendor code onto one of
    these before registering; the mapping is the caller's, because vendors disagree.
    """

    ACTIVE = "ACTIVE"
    BANKRUPTCY = "BANKRUPTCY"
    MERGER_ACQUISITION = "MERGER_ACQUISITION"
    VOLUNTARY = "VOLUNTARY"


def _require_plain_date(value: Any, field_name: str) -> datetime.date:
    """Reject ``datetime.datetime`` explicitly rather than failing mid-comparison.

    ``datetime.datetime`` subclasses ``date``, so it passes an ``isinstance`` check and
    then raises ``TypeError`` on the first comparison against a real ``date`` -- deep
    inside a per-bar universe query, where the traceback says nothing useful.
    """
    if isinstance(value, datetime.datetime):
        raise UniverseError(
            f"{field_name} must be a datetime.date, not a datetime.datetime "
            f"({value!r}). Call .date() first -- mixing the two raises TypeError on "
            f"comparison, not at registration."
        )
    if not isinstance(value, datetime.date):
        raise UniverseError(
            f"{field_name} must be a datetime.date, got {type(value).__name__}."
        )
    return value


def _require_finite(value: Any, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise UniverseError(
            f"{field_name} must be a real number, got {type(value).__name__}."
        )
    numeric = float(value)
    if not math.isfinite(numeric):
        raise UniverseError(f"{field_name} must be finite, got {numeric!r}.")
    return numeric


@dataclass
class InstrumentMetadata:
    """Point-in-time listing record for one security.

    ``security_id`` is the registry key and must be stable across ticker changes. It
    defaults to the symbol, which is correct only when no ticker in the universe is
    ever recycled.

    A delisted security must carry **exactly one** of ``delisting_settlement_price``
    (absolute terminal cash per share) or ``delisting_return`` (fraction applied to the
    last traded price). Neither is a registration error, not a zero.
    """

    symbol: str
    name: str
    listing_date: datetime.date
    delisting_date: Optional[datetime.date] = None
    delisting_reason: DelistingReason = DelistingReason.ACTIVE
    delisting_settlement_price: Optional[float] = None
    delisting_return: Optional[float] = None
    security_id: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise UniverseError("symbol must be a non-empty string.")
        self.symbol = self.symbol.strip().upper()

        if self.security_id is None:
            self.security_id = self.symbol
        elif not isinstance(self.security_id, str) or not self.security_id.strip():
            raise UniverseError(
                f"security_id for '{self.symbol}' must be a non-empty string."
            )
        else:
            self.security_id = self.security_id.strip().upper()

        self.listing_date = _require_plain_date(
            self.listing_date, f"listing_date for '{self.symbol}'"
        )

        if not isinstance(self.delisting_reason, DelistingReason):
            raise UniverseError(
                f"delisting_reason for '{self.symbol}' must be a DelistingReason, got "
                f"{type(self.delisting_reason).__name__}."
            )

        is_delisted = self.delisting_reason is not DelistingReason.ACTIVE

        if self.delisting_date is not None:
            self.delisting_date = _require_plain_date(
                self.delisting_date, f"delisting_date for '{self.symbol}'"
            )
            if self.delisting_date < self.listing_date:
                raise UniverseError(
                    f"'{self.symbol}' delisting_date {self.delisting_date} precedes "
                    f"listing_date {self.listing_date}."
                )

        # A reason and a date must agree. Either half alone produces a security that is
        # delisted but never stops trading, or one that stops trading for no recorded
        # reason -- and the second is indistinguishable from missing data.
        if is_delisted and self.delisting_date is None:
            raise UniverseError(
                f"'{self.symbol}' has delisting_reason {self.delisting_reason.value} "
                f"but no delisting_date. It would never leave the universe."
            )
        if not is_delisted and self.delisting_date is not None:
            raise UniverseError(
                f"'{self.symbol}' has delisting_date {self.delisting_date} but reason "
                f"ACTIVE. Set the reason, or clear the date."
            )

        has_price = self.delisting_settlement_price is not None
        has_return = self.delisting_return is not None

        if not is_delisted:
            if has_price or has_return:
                raise UniverseError(
                    f"'{self.symbol}' is ACTIVE but carries terminal settlement data. "
                    f"An active security has no terminal value."
                )
            return

        if has_price and has_return:
            raise UniverseError(
                f"'{self.symbol}' specifies both delisting_settlement_price and "
                f"delisting_return. Supply exactly one -- they disagree by construction."
            )
        if not has_price and not has_return:
            raise UniverseError(
                f"'{self.symbol}' is delisted ({self.delisting_reason.value}) with no "
                f"terminal value. Supply delisting_settlement_price (known cash per "
                f"share) or delisting_return (fraction of the last traded price). "
                f"Defaulting to 0.0 fabricates a total loss -- for a merger that paid "
                f"cash, that is simply wrong."
            )

        if has_price:
            price = _require_finite(
                self.delisting_settlement_price,
                f"delisting_settlement_price for '{self.symbol}'",
            )
            if price < 0.0:
                raise UniverseError(
                    f"delisting_settlement_price for '{self.symbol}' is {price}. A "
                    f"terminal cash value cannot be negative; equity liability is "
                    f"capped at zero."
                )
            self.delisting_settlement_price = price

        if has_return:
            ret = _require_finite(
                self.delisting_return, f"delisting_return for '{self.symbol}'"
            )
            if ret < -1.0:
                raise UniverseError(
                    f"delisting_return for '{self.symbol}' is {ret}. A long equity "
                    f"position cannot lose more than 100%; -1.0 is total loss."
                )
            self.delisting_return = ret


class SurvivorshipFreeUniverseEngine:
    """Point-in-time universe registry, delisting settler, and bias auditor.

    Keyed by ``security_id``. Registering two securities under one id is an error, not
    an update -- see :meth:`add_instrument`.
    """

    def __init__(self) -> None:
        #: security_id -> metadata. Keyed by security, **not** by ticker: keying by
        #: ticker loses one of every recycled pair.
        self.instruments: Dict[str, InstrumentMetadata] = {}
        self._symbol_index: Dict[str, List[str]] = {}

    # ----------------------------------------------------------------- registration

    def add_instrument(self, meta: InstrumentMetadata) -> None:
        """Register one security.

        Raises when ``security_id`` is already registered. The previous implementation
        keyed on ticker and overwrote silently, so registering old GM and new GM left a
        single record and emptied every pre-2010 universe of the old issuer. A
        collision here means either a duplicate load or two securities sharing an id --
        both are data errors, and neither should resolve by deletion.
        """
        if not isinstance(meta, InstrumentMetadata):
            raise UniverseError(
                f"add_instrument expects InstrumentMetadata, got {type(meta).__name__}."
            )

        security_id = meta.security_id
        if security_id is None:  # pragma: no cover - established in __post_init__
            raise UniverseError(f"'{meta.symbol}' has no security_id.")

        if security_id in self.instruments:
            existing = self.instruments[security_id]
            raise UniverseError(
                f"security_id '{security_id}' is already registered to "
                f"'{existing.name}' ({existing.symbol}). Refusing to overwrite with "
                f"'{meta.name}' ({meta.symbol}) -- supply distinct security_ids."
            )

        self.instruments[security_id] = meta
        self._symbol_index.setdefault(meta.symbol, []).append(security_id)
        logger.debug(
            "Registered %s (%s) as security_id=%s, listed %s, delisted %s",
            meta.symbol,
            meta.name,
            security_id,
            meta.listing_date,
            meta.delisting_date,
        )

    # ------------------------------------------------------------ point-in-time read

    def _is_live_on(self, meta: InstrumentMetadata, as_of_date: datetime.date) -> bool:
        """``listing_date <= as_of_date <= delisting_date`` -- both ends inclusive.

        Inclusive because ``delisting_date`` is the last date the security traded (CRSP
        convention). See the module docstring before reconciling an index feed here.
        """
        if meta.listing_date > as_of_date:
            return False
        return meta.delisting_date is None or as_of_date <= meta.delisting_date

    def get_active_securities(
        self, as_of_date: datetime.date
    ) -> List[InstrumentMetadata]:
        """Securities tradable on ``as_of_date``, sorted by ``security_id``."""
        as_of_date = _require_plain_date(as_of_date, "as_of_date")
        live = [m for m in self.instruments.values() if self._is_live_on(m, as_of_date)]
        return sorted(live, key=lambda m: m.security_id or "")

    def get_active_universe(self, as_of_date: datetime.date) -> List[str]:
        """Symbols tradable on ``as_of_date``, sorted and unique.

        Raises when two distinct securities claim the same ticker on the same date.
        That is a genuine metadata defect -- one issuer's listing window overlaps
        another's use of the ticker -- and resolving it by silently picking one is how a
        backtest ends up holding the wrong company's returns.
        """
        live = self.get_active_securities(as_of_date)
        by_symbol: Dict[str, List[InstrumentMetadata]] = {}
        for meta in live:
            by_symbol.setdefault(meta.symbol, []).append(meta)

        collisions = {sym: metas for sym, metas in by_symbol.items() if len(metas) > 1}
        if collisions:
            detail = "; ".join(
                f"{sym}: " + ", ".join(f"{m.security_id} ({m.name})" for m in metas)
                for sym, metas in sorted(collisions.items())
            )
            raise UniverseError(
                f"Ticker collision on {as_of_date}: {detail}. Two securities cannot "
                f"trade under one ticker on one date -- fix the listing windows."
            )

        symbols = sorted(by_symbol)
        logger.debug(
            "Point-in-time universe on %s: %d tradable securities.",
            as_of_date,
            len(symbols),
        )
        return symbols

    def resolve_symbol(
        self, symbol: str, as_of_date: datetime.date
    ) -> Optional[InstrumentMetadata]:
        """Return the security trading under ``symbol`` on ``as_of_date``, if any.

        This is the point of keying by ``security_id``:
        ``resolve_symbol('GM', date(2008, 1, 1))`` is the old General Motors
        Corporation, and ``resolve_symbol('GM', date(2015, 1, 1))`` is the new General
        Motors Company.
        """
        if not isinstance(symbol, str) or not symbol.strip():
            raise UniverseError("symbol must be a non-empty string.")
        as_of_date = _require_plain_date(as_of_date, "as_of_date")

        candidates = [
            self.instruments[sid]
            for sid in self._symbol_index.get(symbol.strip().upper(), [])
            if self._is_live_on(self.instruments[sid], as_of_date)
        ]
        if not candidates:
            return None
        if len(candidates) > 1:
            raise UniverseError(
                f"Ticker '{symbol}' resolves to {len(candidates)} securities on "
                f"{as_of_date}: {', '.join(str(m.security_id) for m in candidates)}."
            )
        return candidates[0]

    def _lookup_for_settlement(self, identifier: str) -> InstrumentMetadata:
        """Resolve a ``security_id`` or an unambiguous ticker to one security.

        Ambiguity is refused in both namespaces. ``security_id`` defaults to the
        symbol, so an id can collide with a ticker that another security also used --
        register the old GM without an explicit id and its id becomes ``GM``, the same
        string the new GM trades under. Resolving the id first would then silently pick
        one issuer, and which one depends on registration order.
        """
        if not isinstance(identifier, str) or not identifier.strip():
            raise UniverseError("identifier must be a non-empty string.")
        key = identifier.strip().upper()

        by_symbol = self._symbol_index.get(key, [])
        # Every security the string could mean, in either namespace.
        candidates = list(dict.fromkeys(([key] if key in self.instruments else []) + by_symbol))

        if not candidates:
            raise UniverseError(f"'{identifier}' not found in universe registry.")
        if len(candidates) > 1:
            described = ", ".join(
                f"{sid} ({self.instruments[sid].name})" for sid in candidates
            )
            raise UniverseError(
                f"'{identifier}' is ambiguous across {len(candidates)} securities: "
                f"{described}. Settle by a security_id that identifies exactly one -- "
                f"settling the wrong issuer's position is silent and unrecoverable."
            )
        return self.instruments[candidates[0]]

    # -------------------------------------------------------------------- settlement

    def process_delisting_settlement(
        self,
        symbol: str,
        position_qty: float,
        last_traded_price: Optional[float] = None,
    ) -> Tuple[float, str]:
        """Terminal cash value of a position in a delisted security.

        ``symbol`` may be a ``security_id`` or an unambiguous ticker; a recycled ticker
        must be settled by id. ``position_qty`` may be negative (a short in a name that
        went to zero pays off). ``last_traded_price`` is required only in
        ``delisting_return`` mode, and is the last price at which the position was
        marked -- on the same split and dividend basis as ``position_qty``.

        Returns ``(settlement_cash_value, message)``.

        Raises for an ACTIVE security. There is no terminal value to compute; the old
        behaviour returned ``qty * 0.0`` with the message "Active instrument.", which
        booked a silent 100% loss on a healthy position.
        """
        meta = self._lookup_for_settlement(symbol)

        if meta.delisting_reason is DelistingReason.ACTIVE:
            raise UniverseError(
                f"'{meta.symbol}' (security_id {meta.security_id}) is ACTIVE and has "
                f"no terminal settlement value. Mark it at market instead."
            )

        qty = _require_finite(position_qty, "position_qty")

        if meta.delisting_settlement_price is not None:
            terminal_price = meta.delisting_settlement_price
            basis = f"settlement price ${terminal_price:,.4f}/share"
        else:
            delisting_return = meta.delisting_return
            if delisting_return is None:  # pragma: no cover - exactly one is set
                raise UniverseError(f"'{meta.symbol}' has no terminal value.")
            if last_traded_price is None:
                raise UniverseError(
                    f"'{meta.symbol}' settles on a delisting_return of "
                    f"{delisting_return:+.2%} and needs last_traded_price to apply it. "
                    f"Passing no price cannot default to zero -- that is the loss this "
                    f"argument exists to measure."
                )
            price = _require_finite(last_traded_price, "last_traded_price")
            if price <= 0.0:
                raise UniverseError(f"last_traded_price must be positive, got {price}.")
            terminal_price = price * (1.0 + delisting_return)
            basis = (
                f"delisting return {delisting_return:+.2%} on last price "
                f"${price:,.4f} -> ${terminal_price:,.4f}/share"
            )

        cash = qty * terminal_price
        msg = (
            f"DELISTING SETTLEMENT [{meta.delisting_reason.value}]: '{meta.symbol}' "
            f"(security_id {meta.security_id}) last traded {meta.delisting_date}, "
            f"settled at {basis}. Qty {qty:,.4f} -> cash ${cash:,.2f}"
        )
        # A modelled settlement is an ordinary backtest event, not an incident. The old
        # code logged bankruptcies at CRITICAL, which pages an operator once per
        # bankrupt constituent of a 30-year backtest.
        logger.info(msg)
        return cash, msg

    # ------------------------------------------------------------------------- audit

    def audit_survivorship_bias(
        self,
        start_date: datetime.date,
        end_date: datetime.date,
        current_static_universe: Optional[Iterable[str]] = None,
        min_expected_attrition_rate: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Measure delisted coverage over ``[start_date, end_date]``.

        The denominator is securities that were tradable at some point **inside the
        window**, not everything ever registered. Counting names that never traded in
        the window inflates the denominator with securities the backtest could not have
        held, and deflates the attrition rate that is the point of the audit.

        ``current_static_universe`` enables the ghost check: how many securities that
        were tradable in the window are absent from today's membership list. A universe
        built from a current snapshot has a ghost count of zero by construction, so a
        non-zero count is direct evidence the registry carries history. Omit it and
        ``ghost_count`` is ``None``, meaning **not audited** -- never read that as zero.

        The ghost check compares **tickers**, because a current-membership list is
        normally a list of tickers. A recycled ticker therefore hides its delisted
        issuer: the old General Motors Corporation is not a ghost, because ``GM`` is in
        today's index for the new General Motors Company. Read ``ghost_count`` as a
        lower bound, and use ``delisted_in_period`` for the count that is not fooled.

        ``min_expected_attrition_rate`` is the caller's threshold, because there is no
        universal one: attrition depends on index, era, and asset class. Omit it and
        ``meets_expected_attrition`` is ``None``. No boolean in this result claims the
        universe is bias-free; the previous ``has_bias_protection`` flag went ``True``
        on a single delisted name in a universe of any size, certifying exactly the
        backtests it existed to reject.
        """
        start_date = _require_plain_date(start_date, "start_date")
        end_date = _require_plain_date(end_date, "end_date")
        if start_date > end_date:
            raise UniverseError(
                f"start_date {start_date} is after end_date {end_date}."
            )

        if min_expected_attrition_rate is not None:
            min_expected_attrition_rate = _require_finite(
                min_expected_attrition_rate, "min_expected_attrition_rate"
            )
            if not 0.0 <= min_expected_attrition_rate <= 1.0:
                raise UniverseError(
                    f"min_expected_attrition_rate must be in [0, 1], got "
                    f"{min_expected_attrition_rate}."
                )

        in_period: List[InstrumentMetadata] = []
        for meta in self.instruments.values():
            if meta.listing_date > end_date:
                continue
            if meta.delisting_date is not None and meta.delisting_date < start_date:
                continue
            in_period.append(meta)

        delisted_in_period = [
            m
            for m in in_period
            if m.delisting_date is not None
            and start_date <= m.delisting_date <= end_date
        ]
        survivors_at_end = [m for m in in_period if self._is_live_on(m, end_date)]

        universe_size = len(in_period)
        attrition_rate = (
            len(delisted_in_period) / universe_size if universe_size else 0.0
        )

        ghost_symbols: Optional[List[str]] = None
        ghost_count: Optional[int] = None
        if current_static_universe is not None:
            if isinstance(current_static_universe, str):
                raise UniverseError(
                    "current_static_universe must be an iterable of symbols, not a "
                    "single string."
                )
            snapshot: Set[str] = {
                s.strip().upper()
                for s in current_static_universe
                if isinstance(s, str) and s.strip()
            }
            ghost_symbols = sorted({m.symbol for m in in_period} - snapshot)
            ghost_count = len(ghost_symbols)

        meets_expected = (
            None
            if min_expected_attrition_rate is None
            else attrition_rate >= min_expected_attrition_rate
        )

        result: Dict[str, Any] = {
            "start_date": start_date,
            "end_date": end_date,
            "registered_instruments": len(self.instruments),
            "universe_in_period": universe_size,
            "never_live_in_period": len(self.instruments) - universe_size,
            "delisted_in_period": len(delisted_in_period),
            "delisted_symbols": sorted(m.symbol for m in delisted_in_period),
            "survivors_at_end": len(survivors_at_end),
            "attrition_rate": attrition_rate,
            "min_expected_attrition_rate": min_expected_attrition_rate,
            "meets_expected_attrition": meets_expected,
            "ghost_count": ghost_count,
            "ghost_symbols": ghost_symbols,
        }
        logger.info(
            "Survivorship audit %s..%s: %d in period (%d registered), %d delisted "
            "(%.1f%% attrition), %d survivors, ghosts=%s",
            start_date,
            end_date,
            universe_size,
            len(self.instruments),
            len(delisted_in_period),
            attrition_rate * 100.0,
            len(survivors_at_end),
            "not audited" if ghost_count is None else ghost_count,
        )
        return result

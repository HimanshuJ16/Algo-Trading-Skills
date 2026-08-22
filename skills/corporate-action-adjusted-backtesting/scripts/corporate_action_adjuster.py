"""
corporate-action-adjusted-backtesting:
Builds a backward-adjusted (CRSP-convention) price series from a raw OHLCV series
and a corporate action log, while keeping the raw series intact for execution and
cash accounting.

The module owns exactly one thing: **the Cumulative Adjustment Factor (CAF) that
maps a raw historical price onto today's share basis**. It does not fetch
corporate action data, does not compute total returns, does not size orders and
does not credit dividend cash. Those belong to the caller.

Four properties distinguish this from ``price * split_ratio``, and each exists
because the naive version silently corrupts a backtest:

  1. **A cash dividend's reference price is the last close BEFORE the ex-date.**
     Not the ex-date close. Yahoo Finance and MATLAB's ``adjustedClosingPrices``
     both document the multiplier as ``1 - dividend / (last close preceding the
     ex-dividend date)``, both citing the CRSP standard. Using the ex-date close
     instead couples the adjustment factor to that day's market move: a $2
     dividend on a stock that fell from $100 to $90 on the ex-date yields 0.9778
     under the wrong convention and 0.98 under the right one, and the error is
     unbounded as the ex-date close approaches zero.

  2. **Price and volume do not share a factor.** Volume is adjusted only by
     *share-count* events -- splits, reverse splits, stock dividends. A cash
     dividend changes the price basis and leaves the share count untouched, so
     folding it into the volume factor inflates historical share volume by the
     dividend yield and biases every ADV liquidity check that reads the adjusted
     series. CRSP models this as two separate fields (factor to adjust price vs.
     factor to adjust shares outstanding); so does this module, via ``caf`` and
     ``volume_caf``.

  3. **An event is applied by DATE, not by matching a bar.** Ex-dates land on
     days with no bar -- exchange holidays, trading halts, a series that starts
     mid-history, a vendor whose calendar disagrees with yours. An implementation
     that only applies an event when ``event.ex_date == bar.dt`` drops such an
     event in silence, leaving a 50%-wide split gap sitting inside a series
     labelled "adjusted". Here the factor for ex-date ``E`` applies to every bar
     with ``dt < E`` regardless of whether a bar exists on ``E``.

  4. **Nothing is skipped quietly.** An unrecognised ``event_type``, a
     non-positive split ratio, a dividend that exceeds its reference price, or a
     zero reference close raises ``CorporateActionError``. Events that genuinely
     cannot affect the sample (ex-date at or before the first bar, ex-date after
     the last bar) are no-ops by construction and are logged, not raised.

Anchoring: CAF is 1.0 on the most recent bar in the series, so the newest
adjusted price equals the newest raw price. Events with an ex-date after the last
bar have not occurred within the sample and are therefore **not** applied --
applying them would break that anchor and pre-empt an event the backtest has not
reached yet.

Point-in-time: pass ``as_of`` to ``compute_caf_series`` / ``adjust_bars`` to
rebuild the series exactly as it would have looked on that date -- bars after
``as_of`` and events with an ex-date after ``as_of`` are both excluded. Without
this, running a signal over a fully-adjusted modern series lets the model see a
dividend or split that had not been announced yet.

What this is not:

  - **Not a total-return series.** Multiplying prices by ``1 - D/P`` removes the
    ex-date price drop; it does not credit the cash. A strategy's dividend PnL
    must be credited separately, from the raw series and the position held on the
    ex-date. Adjusted prices are for signals only.
  - **Not an execution price source.** Order quantities, cash debits/credits,
    commissions and tick-size rounding must all use the raw series. Adjusted
    prices here are rounded for display and carry no venue tick semantics.
  - **Not a corporate action data source.** Event ingestion, vendor parity and
    the declaration/ex/record/pay lifecycle belong to
    ``corporate-action-event-calendar-integration``.
  - **Not a futures continuation builder.** Rolling futures contracts use a
    different (ratio- or difference-based) stitching convention; see
    ``synthetic-continuous-futures-contract-construction``.
  - **Not a spin-off or merger handler.** Only splits, reverse splits and
    ordinary cash dividends are modelled. A spin-off's factor depends on the
    when-issued value of the distributed security and must be supplied out of
    band.
"""
import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional, Sequence

logger = logging.getLogger(__name__)

SPLIT = "SPLIT"
REVERSE_SPLIT = "REVERSE_SPLIT"
DIVIDEND = "DIVIDEND"

#: Events that change the share count. Volume is adjusted by these only.
SHARE_COUNT_EVENTS = frozenset({SPLIT, REVERSE_SPLIT})
VALID_EVENT_TYPES = frozenset({SPLIT, REVERSE_SPLIT, DIVIDEND})


class CorporateActionError(ValueError):
    """Raised when an event or bar cannot yield a well-defined adjustment factor."""


def _require_finite(value: float, label: str) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise CorporateActionError(f"{label} must be numeric, got {value!r}") from exc
    if not math.isfinite(numeric):
        raise CorporateActionError(f"{label} must be finite, got {numeric!r}")
    return numeric


@dataclass
class CorporateActionEvent:
    """
    A single corporate action.

    ``value`` is the **share multiplier** for split events and the **per-share
    cash amount** for dividends:

      - ``SPLIT`` with ``value=2.0``: 2-for-1; one old share becomes two. A 10%
        stock dividend is ``SPLIT`` with ``value=1.1``.
      - ``REVERSE_SPLIT`` with ``value=5.0``: 1-for-5; five old shares become one.
      - ``DIVIDEND`` with ``value=1.50``: $1.50 per share, cash.

    ``event_type`` is normalised to upper case; an unrecognised type raises
    rather than being ignored.
    """
    ex_date: date
    event_type: str
    value: float

    def __post_init__(self) -> None:
        if not isinstance(self.ex_date, date):
            raise CorporateActionError(
                f"ex_date must be a datetime.date, got {type(self.ex_date).__name__}"
            )
        if not isinstance(self.event_type, str):
            raise CorporateActionError(
                f"event_type must be a string, got {type(self.event_type).__name__}"
            )
        self.event_type = self.event_type.strip().upper()
        if self.event_type not in VALID_EVENT_TYPES:
            raise CorporateActionError(
                f"unknown event_type {self.event_type!r}; "
                f"expected one of {sorted(VALID_EVENT_TYPES)}"
            )
        self.value = _require_finite(self.value, f"{self.event_type} value")
        if self.value <= 0.0:
            # A zero split ratio divides by zero; a negative one flips the sign of
            # every historical price. Neither is a recoverable data condition.
            raise CorporateActionError(
                f"{self.event_type} value must be > 0 on {self.ex_date}, "
                f"got {self.value}"
            )


@dataclass
class BarData:
    """One raw, unadjusted OHLCV bar. Prices are as printed by the venue."""
    dt: date
    raw_open: float
    raw_high: float
    raw_low: float
    raw_close: float
    raw_volume: float

    def __post_init__(self) -> None:
        if not isinstance(self.dt, date):
            raise CorporateActionError(
                f"dt must be a datetime.date, got {type(self.dt).__name__}"
            )
        for name in ("raw_open", "raw_high", "raw_low", "raw_close", "raw_volume"):
            value = _require_finite(getattr(self, name), f"{name} on {self.dt}")
            if value < 0.0:
                raise CorporateActionError(
                    f"{name} must be >= 0 on {self.dt}, got {value}"
                )
            setattr(self, name, value)


@dataclass
class AdjustedBarData(BarData):
    """
    A raw bar plus its adjusted counterpart.

    ``caf`` is the price factor, ``volume_caf`` the share-count factor. They
    differ whenever a cash dividend sits between this bar and the end of the
    series, and only ``volume_caf`` is meaningful for liquidity checks.
    """
    caf: float = 1.0
    volume_caf: float = 1.0
    adj_open: float = 0.0
    adj_high: float = 0.0
    adj_low: float = 0.0
    adj_close: float = 0.0
    adj_volume: float = 0.0


class CorporateActionAdjuster:
    """
    Computes Cumulative Adjustment Factors and generates backward-adjusted
    price/volume series for backtesting, preserving raw prices for execution.

    See the module docstring for the conventions this implements and for what
    deliberately lives outside it.
    """

    #: Adjusted prices are rounded for readability only; execution must use raw
    #: prices. Six places keeps deeply-split histories (cumulative factors of
    #: 1e-4 and smaller) from collapsing the way four places would.
    PRICE_DECIMALS = 6
    VOLUME_DECIMALS = 2

    def __init__(self, events: Optional[Sequence[CorporateActionEvent]] = None) -> None:
        self.events: List[CorporateActionEvent] = sorted(
            events or [], key=lambda e: e.ex_date
        )

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _sorted_bars(bars: Iterable[BarData], as_of: Optional[date]) -> List[BarData]:
        selected = [b for b in bars if as_of is None or b.dt <= as_of]
        selected.sort(key=lambda b: b.dt)
        distinct = {b.dt for b in selected}
        if len(distinct) != len(selected):
            # Duplicates no longer double-apply a factor (factors are keyed by
            # date, not by bar), but they still signal a broken feed.
            logger.warning(
                "duplicate bar dates in series (%d bars, %d distinct dates); "
                "adjustment is unaffected but the input should be de-duplicated",
                len(selected), len(distinct),
            )
        return selected

    def _applicable_events(
        self, bars: Sequence[BarData], as_of: Optional[date]
    ) -> List[CorporateActionEvent]:
        """
        Events that can move at least one bar in the sample.

        Dropped, with a reason logged:
          - ex-date after ``as_of`` -- not yet effective at that vantage point,
            so including it would be look-ahead bias.
          - ex-date after the last bar -- has not occurred within the sample.
            Applying it would scale every bar and break the CAF == 1.0 anchor on
            the most recent bar.
          - ex-date at or before the first bar -- no bar precedes it, so its
            factor multiplies nothing.
        """
        if not bars:
            return []
        first_dt, last_dt = bars[0].dt, bars[-1].dt
        applicable: List[CorporateActionEvent] = []
        for event in self.events:
            if as_of is not None and event.ex_date > as_of:
                logger.debug(
                    "event %s on %s ignored: after as_of %s",
                    event.event_type, event.ex_date, as_of,
                )
                continue
            if event.ex_date > last_dt:
                logger.debug(
                    "event %s on %s ignored: after last bar %s (not yet in sample)",
                    event.event_type, event.ex_date, last_dt,
                )
                continue
            if event.ex_date <= first_dt:
                logger.debug(
                    "event %s on %s ignored: no bar precedes it (first bar %s)",
                    event.event_type, event.ex_date, first_dt,
                )
                continue
            applicable.append(event)
        return applicable

    @staticmethod
    def _reference_close(bars: Sequence[BarData], ex_date: date) -> float:
        """
        Last close strictly before ``ex_date`` -- the cum-dividend close.

        ``bars`` must be sorted ascending. Callers only reach this for events
        already known to have at least one preceding bar.
        """
        reference: Optional[BarData] = None
        for bar in bars:
            if bar.dt >= ex_date:
                break
            reference = bar
        if reference is None:  # pragma: no cover - guarded by _applicable_events
            raise CorporateActionError(
                f"no bar precedes ex-date {ex_date}; cannot price the dividend"
            )
        return reference.raw_close

    def _event_factor(
        self, event: CorporateActionEvent, bars: Sequence[BarData]
    ) -> float:
        """Single-event price factor applied to every bar before its ex-date."""
        if event.event_type == SPLIT:
            # 2-for-1: history is multiplied by 0.5.
            return 1.0 / event.value
        if event.event_type == REVERSE_SPLIT:
            # 1-for-5: history is multiplied by 5.0.
            return event.value
        # DIVIDEND: CRSP convention -- the dividend is expressed as a fraction of
        # the last close PRECEDING the ex-date, never the ex-date close itself.
        reference_close = self._reference_close(bars, event.ex_date)
        if reference_close <= 0.0:
            raise CorporateActionError(
                f"dividend on {event.ex_date} has a non-positive reference close "
                f"({reference_close}); cannot compute an adjustment factor"
            )
        if event.value >= reference_close:
            # A cash distribution at or above the cum-dividend close drives the
            # factor to zero or negative, i.e. zero or negative adjusted prices.
            # This is either bad data or a liquidating/special distribution that
            # needs an explicitly supplied factor.
            raise CorporateActionError(
                f"dividend {event.value} on {event.ex_date} is >= its reference "
                f"close {reference_close}; adjusted prices would be non-positive"
            )
        return 1.0 - (event.value / reference_close)

    # --------------------------------------------------------------------- API

    def compute_caf_series(
        self, bars: Sequence[BarData], as_of: Optional[date] = None
    ) -> Dict[date, float]:
        """
        Price CAF per bar date: ``CAF_t = prod(alpha_E for E > t)``.

        Anchored at 1.0 on the most recent bar. Pass ``as_of`` for a
        point-in-time view -- bars after it and events with a later ex-date are
        both excluded, reproducing the series as it stood on that date.
        """
        return self._caf_series(bars, as_of, share_events_only=False)

    def compute_volume_caf_series(
        self, bars: Sequence[BarData], as_of: Optional[date] = None
    ) -> Dict[date, float]:
        """
        Share-count CAF per bar date, built from split events only.

        Volume is adjusted as ``V_raw / volume_caf``: a 2-for-1 split gives
        ``volume_caf = 0.5`` before the ex-date, doubling historical share volume
        onto today's basis. Cash dividends are excluded because they do not
        change the share count.
        """
        return self._caf_series(bars, as_of, share_events_only=True)

    def _caf_series(
        self,
        bars: Sequence[BarData],
        as_of: Optional[date],
        share_events_only: bool,
    ) -> Dict[date, float]:
        sorted_bars = self._sorted_bars(bars, as_of)
        if not sorted_bars:
            return {}

        events = self._applicable_events(sorted_bars, as_of)
        if share_events_only:
            events = [e for e in events if e.event_type in SHARE_COUNT_EVENTS]

        # Factor keyed by ex-date, applied to every bar strictly before it. Keying
        # by date (not by bar) is what makes an ex-date on a non-trading day, or a
        # duplicated bar, harmless.
        factor_by_ex_date: Dict[date, float] = {}
        for event in events:
            factor = self._event_factor(event, sorted_bars)
            factor_by_ex_date[event.ex_date] = (
                factor_by_ex_date.get(event.ex_date, 1.0) * factor
            )

        pending_ex_dates = sorted(factor_by_ex_date, reverse=True)
        caf_map: Dict[date, float] = {}
        cum_factor = 1.0
        cursor = 0
        for bar in reversed(sorted_bars):
            # Fold in every event whose ex-date is strictly after this bar.
            while cursor < len(pending_ex_dates) and pending_ex_dates[cursor] > bar.dt:
                cum_factor *= factor_by_ex_date[pending_ex_dates[cursor]]
                cursor += 1
            # Not rounded: a long history of splits can drive the factor below
            # 1e-7, where rounding to six places is total precision loss.
            caf_map[bar.dt] = cum_factor
        return caf_map

    def adjust_bars(
        self, bars: Sequence[BarData], as_of: Optional[date] = None
    ) -> List[AdjustedBarData]:
        """
        Return each in-scope bar with both raw and adjusted OHLCV fields.

        Output is sorted ascending by date and, when ``as_of`` is given,
        truncated to bars on or before it. Adjusted values are rounded for
        display; ``caf`` and ``volume_caf`` are exact and are what downstream
        arithmetic should use.
        """
        sorted_bars = self._sorted_bars(bars, as_of)
        caf_map = self.compute_caf_series(sorted_bars, as_of)
        volume_caf_map = self.compute_volume_caf_series(sorted_bars, as_of)

        adjusted_list: List[AdjustedBarData] = []
        for bar in sorted_bars:
            caf = caf_map.get(bar.dt, 1.0)
            volume_caf = volume_caf_map.get(bar.dt, 1.0)
            adjusted_list.append(
                AdjustedBarData(
                    dt=bar.dt,
                    raw_open=bar.raw_open,
                    raw_high=bar.raw_high,
                    raw_low=bar.raw_low,
                    raw_close=bar.raw_close,
                    raw_volume=bar.raw_volume,
                    caf=caf,
                    volume_caf=volume_caf,
                    adj_open=round(bar.raw_open * caf, self.PRICE_DECIMALS),
                    adj_high=round(bar.raw_high * caf, self.PRICE_DECIMALS),
                    adj_low=round(bar.raw_low * caf, self.PRICE_DECIMALS),
                    adj_close=round(bar.raw_close * caf, self.PRICE_DECIMALS),
                    adj_volume=round(bar.raw_volume / volume_caf, self.VOLUME_DECIMALS),
                )
            )
        return adjusted_list

"""
synthetic-labels-from-triple-barrier-method: path-dependent supervised labels for
financial machine learning, following the Triple-Barrier Method of Lopez de Prado,
*Advances in Financial Machine Learning* (Wiley, 2018), Ch. 3.

Each event seeded at bar ``t`` is given three barriers, and the label is decided by
whichever is touched **first**:

* an upper profit-taking barrier at path return ``+pt_mult * sigma_t``  -> ``+1``
* a lower stop-loss barrier at path return ``-sl_mult * sigma_t``       -> ``-1``
* a vertical barrier ``vertical_bars`` bars later                       -> ``0``

Design notes
------------
* **Barrier units are returns, not prices.** ``sigma_t`` is the per-bar volatility
  estimate and plays the role of AFML's ``trgt`` -- "the unit width of the
  horizontal barriers" (Snippet 3.2) -- while ``pt_mult``/``sl_mult`` are AFML's
  ``ptSl[0]``/``ptSl[1]``. A multiplier of ``0`` disables that barrier entirely,
  as ``applyPtSlOnT1`` does with ``if ptSl[0] > 0``.

* **Barrier tests are strict inequalities**, matching Snippet 3.2's ``df0[df0 >
  pt]`` / ``df0[df0 < sl]``. This matters: with a non-strict ``>=`` a flat series
  (sigma_t = 0, barriers collapsed onto the entry price) labels every unchanged
  price as a profit-take. Degenerate targets are additionally filtered by
  ``min_target_return``, mirroring ``getEvents``' ``trgt = trgt[trgt > minRet]``.

* **Volatility is causal.** ``sigma_t`` is an EWM standard deviation of log returns
  up to and including bar ``t``, whose last input is ``log(P_t / P_{t-1})`` -- known
  at the close of bar ``t``, which is also the entry price. The forward scan starts
  at ``t+1``. No future information enters a label's own barrier widths. Note the
  unit approximation: sigma is estimated on **log** returns and applied to
  **arithmetic** path returns, a difference of order ``sigma^2 / 2`` (0.005% at
  sigma = 1%). AFML's ``getDailyVol`` (Snippet 3.1, default ``span0=100``)
  estimates on arithmetic returns instead.

* **Events with no usable target are dropped, never defaulted.** An earlier version
  of this module substituted ``0.01`` for a NaN volatility estimate, silently
  giving warm-up bars a fabricated 1% barrier width. AFML drops them
  (``dropna(subset=['trgt'])``); so does this engine, with a WARNING naming the
  count.

* **``side`` turns directional labels into meta-labels.** Path returns are
  multiplied by the side of the bet, exactly as ``applyPtSlOnT1`` does with
  ``(df0 / close[loc] - 1) * events_.at[loc, 'side']``. For a short, the
  profit-taking barrier therefore sits *below* the entry price. When ``side`` is
  supplied, ``meta_label`` carries the AFML ``getBins`` binary target
  (``1`` if the side-adjusted return is positive, else ``0``); when it is not, the
  barrier multipliers should be symmetric, because "when we cannot differentiate
  between a profit-taking barrier and a stop-loss barrier due to lack of side
  knowledge, we must use symmetric horizontal barriers" (Snippet 3.3, which forces
  ``ptSl_ = [ptSl[0], ptSl[0]]``). Asymmetric multipliers without a side are
  allowed here but logged as a WARNING: on a driftless random walk they manufacture
  a class skew that is a property of the barriers, not of the market.

* **Close-only by default, intrabar when given highs and lows.** With closes alone
  a stop breached mid-bar and recovered by the close is invisible, and the event is
  mislabelled ``+1`` or ``0``. Passing ``highs``/``lows`` scans the bar's range
  instead; a bar that spans both barriers is resolved to the **stop-loss** (the
  adverse assumption, since the intrabar path is unknown) and flagged in
  ``intrabar_ambiguous``. In intrabar mode the exit price is the barrier level
  itself -- the fill a resting stop/limit at that level would get, absent a gap.

* **Labels overlap.** Seeding an event on every bar with a horizon of
  ``vertical_bars`` produces labels sharing up to ``vertical_bars - 1`` bars of
  price path each. They are not IID and must be weighted by uniqueness before
  training -- see the repo skill ``sample-weighting-for-overlapping-labels``.

* **Complexity** is O(number of events x vertical_bars). Prices are materialised
  into NumPy arrays once, so the inner scan does not pay pandas' scalar-indexing
  overhead.
"""
import logging
from dataclasses import dataclass, fields
from enum import IntEnum
from typing import Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

#: The only bet directions a label can be conditioned on.
VALID_SIDES: Tuple[int, int] = (-1, 1)


class TripleBarrierError(ValueError):
    """Raised on malformed price data, event selections, or barrier configuration."""


class BarrierTouched(IntEnum):
    """Which barrier was touched first. Values are the directional label."""

    STOP_LOSS = -1
    VERTICAL_TIMEOUT = 0
    TAKE_PROFIT = 1


@dataclass(frozen=True)
class TripleBarrierLabel:
    """
    One labelled event.

    ``realized_return`` is the arithmetic return from ``entry_price`` to
    ``exit_price``, multiplied by ``side``: it is the return *of the bet*, so a
    profitable short is positive. ``meta_label`` is the AFML ``getBins`` binary
    target and is ``None`` unless ``side`` was supplied.
    """

    entry_timestamp: object
    entry_price: float
    exit_timestamp: object
    exit_price: float
    barrier_touched: BarrierTouched
    realized_return: float
    target_volatility: float
    side: int
    holding_bars: int
    meta_label: Optional[int] = None
    intrabar_ambiguous: bool = False


def _require_positive_finite_series(
    name: str,
    series: pd.Series,
    expected_index: Optional[pd.Index] = None,
) -> np.ndarray:
    """Validates a price-like series and returns its values as a float64 array."""
    if not isinstance(series, pd.Series):
        raise TripleBarrierError(f"{name} must be a pandas Series, got {type(series).__name__}.")
    if expected_index is not None and not series.index.equals(expected_index):
        raise TripleBarrierError(
            f"{name} index must be identical to the price series index "
            f"({len(series)} vs {len(expected_index)} bars); align the bars before labelling."
        )
    try:
        values = series.to_numpy(dtype=float)
    except (TypeError, ValueError) as exc:
        raise TripleBarrierError(f"{name} must be numeric: {exc}") from exc
    if not np.isfinite(values).all():
        bad = int((~np.isfinite(values)).sum())
        raise TripleBarrierError(
            f"{name} contains {bad} non-finite value(s) (NaN/inf). A NaN price fails every barrier "
            f"comparison silently and would be recorded as an untouched barrier; clean or drop "
            f"those bars before labelling."
        )
    if (values <= 0.0).any():
        raise TripleBarrierError(
            f"{name} contains non-positive prices; log returns and proportional barriers are "
            f"undefined for them."
        )
    return values


class TripleBarrierLabelerEngine:
    """
    Triple-Barrier labeller (Lopez de Prado, AFML Ch. 3).

    Parameters
    ----------
    pt_mult, sl_mult:
        Multipliers on the per-bar volatility target that set the profit-taking and
        stop-loss barrier widths (AFML ``ptSl``). ``0`` disables that barrier; they
        cannot both be ``0``. Without a ``side`` these should be equal -- see the
        module docstring; the engine warns rather than refusing.
    vertical_bars:
        Holding horizon in bars. Events with fewer than this many bars remaining are
        dropped rather than labelled over a truncated horizon.
    volatility_span:
        EWM span for the volatility estimate. AFML's ``getDailyVol`` defaults to a
        span of 100; the 20 used here is a shorter, more reactive choice, not a
        standard.
    volatility_min_periods:
        Observations required before the EWM standard deviation is defined. Events
        whose target is still NaN are dropped.
    min_target_return:
        Events are kept only while ``sigma_t > min_target_return`` (AFML ``minRet``).
        The default ``0.0`` drops zero-volatility events, whose barriers would sit on
        top of the entry price.
    scale_target_by_horizon:
        When True the target becomes ``sigma_t * sqrt(vertical_bars)``, making the
        barrier width commensurate with the horizon it has to survive. Off by
        default because it changes every label; ``references/standards.md`` records
        the measured effect on class balance.
    """

    def __init__(
        self,
        pt_mult: float = 2.0,
        sl_mult: float = 2.0,
        vertical_bars: int = 10,
        volatility_span: int = 20,
        volatility_min_periods: int = 2,
        min_target_return: float = 0.0,
        scale_target_by_horizon: bool = False,
    ) -> None:
        for label, value in (("pt_mult", pt_mult), ("sl_mult", sl_mult)):
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise TripleBarrierError(f"{label} must be a real number, got {type(value).__name__}.")
            if not np.isfinite(value) or value < 0.0:
                raise TripleBarrierError(f"{label} must be finite and >= 0, got {value!r}.")
        if pt_mult == 0.0 and sl_mult == 0.0:
            raise TripleBarrierError(
                "pt_mult and sl_mult cannot both be 0: every event would time out by construction."
            )
        if not isinstance(vertical_bars, int) or isinstance(vertical_bars, bool) or vertical_bars < 1:
            raise TripleBarrierError(f"vertical_bars must be an integer >= 1, got {vertical_bars!r}.")
        if not isinstance(volatility_span, int) or isinstance(volatility_span, bool) or volatility_span < 1:
            raise TripleBarrierError(f"volatility_span must be an integer >= 1, got {volatility_span!r}.")
        if (
            not isinstance(volatility_min_periods, int)
            or isinstance(volatility_min_periods, bool)
            or volatility_min_periods < 2
        ):
            raise TripleBarrierError(
                f"volatility_min_periods must be an integer >= 2 (a standard deviation needs two "
                f"observations), got {volatility_min_periods!r}."
            )
        if not isinstance(min_target_return, (int, float)) or isinstance(min_target_return, bool):
            raise TripleBarrierError("min_target_return must be a real number.")
        if not np.isfinite(min_target_return) or min_target_return < 0.0:
            raise TripleBarrierError(
                f"min_target_return must be finite and >= 0, got {min_target_return!r}."
            )

        self.pt_mult = float(pt_mult)
        self.sl_mult = float(sl_mult)
        self.vertical_bars = vertical_bars
        self.volatility_span = volatility_span
        self.volatility_min_periods = volatility_min_periods
        self.min_target_return = float(min_target_return)
        self.scale_target_by_horizon = bool(scale_target_by_horizon)

    def compute_volatility(self, prices: pd.Series) -> pd.Series:
        """
        Per-bar volatility target: EWM standard deviation of log returns.

        Causal by construction -- the estimate at bar ``t`` uses returns up to and
        including ``log(P_t / P_{t-1})``. Warm-up bars are left as NaN rather than
        filled; ``generate_labels`` drops the events they would have seeded.
        """
        _require_positive_finite_series("prices", prices)
        returns = np.log(prices / prices.shift(1))
        vol = returns.ewm(span=self.volatility_span, min_periods=self.volatility_min_periods).std()
        if self.scale_target_by_horizon:
            vol = vol * float(np.sqrt(self.vertical_bars))
        return vol

    def _resolve_sides(self, prices: pd.Series, side: Optional[Union[int, pd.Series]]) -> np.ndarray:
        """Broadcasts and validates the bet side into a +1/-1 array aligned with prices."""
        if side is None:
            if self.pt_mult != self.sl_mult:
                logger.warning(
                    "Asymmetric barriers (pt_mult=%s, sl_mult=%s) without a bet side: AFML Snippet 3.3 "
                    "forces symmetric barriers when the side is unknown. The resulting +1/-1 class "
                    "imbalance is a property of the barrier widths, not of the market.",
                    self.pt_mult,
                    self.sl_mult,
                )
            return np.ones(len(prices), dtype=int)

        if isinstance(side, pd.Series):
            if not side.index.equals(prices.index):
                raise TripleBarrierError("side index must be identical to the price series index.")
            values = side.to_numpy()
        elif isinstance(side, (int, np.integer)) and not isinstance(side, bool):
            values = np.full(len(prices), int(side))
        else:
            raise TripleBarrierError(
                f"side must be None, an int in {VALID_SIDES}, or a pandas Series aligned with prices; "
                f"got {type(side).__name__}."
            )

        as_array = np.asarray(values)
        if as_array.dtype == bool:
            # `signal > 0` is a natural mistake; True would coerce to +1 and label a
            # short book long without complaint.
            raise TripleBarrierError(
                f"side must be signed ({VALID_SIDES}), not boolean: map False to -1 explicitly."
            )
        if not np.isin(as_array, VALID_SIDES).all():
            raise TripleBarrierError(f"every side must be one of {VALID_SIDES}; 0 is not a bet.")
        return as_array.astype(int)

    def _resolve_event_positions(self, prices: pd.Series, events: Optional[Sequence]) -> np.ndarray:
        """Maps requested event index labels to integer positions, or seeds every bar."""
        n = len(prices)
        last_seedable = n - self.vertical_bars  # exclusive

        if events is None:
            return np.arange(0, max(last_seedable, 0), dtype=int)

        requested = pd.Index(events)
        if requested.empty:
            raise TripleBarrierError("events was supplied but empty; pass None to seed every bar.")
        if requested.has_duplicates:
            raise TripleBarrierError("events contains duplicate labels; each event must be seeded once.")

        positions = prices.index.get_indexer(requested)
        missing = requested[positions == -1]
        if len(missing) > 0:
            raise TripleBarrierError(
                f"{len(missing)} event label(s) are not in the price index (first: {missing[0]!r}). "
                f"Silently dropping them would return fewer labels than events, with no signal."
            )

        seedable = positions[positions < last_seedable]
        dropped = len(positions) - len(seedable)
        if dropped:
            logger.warning(
                "%d of %d event(s) sit within %d bars of the series end and were dropped: their "
                "vertical barrier extends past the available data.",
                dropped,
                len(positions),
                self.vertical_bars,
            )
        return np.sort(seedable)

    def generate_labels(
        self,
        prices: pd.Series,
        events: Optional[Sequence] = None,
        side: Optional[Union[int, pd.Series]] = None,
        highs: Optional[pd.Series] = None,
        lows: Optional[pd.Series] = None,
    ) -> pd.DataFrame:
        """
        Labels each event by the first barrier its forward path touches.

        Parameters
        ----------
        prices:
            Close prices, strictly positive and finite, on a unique, monotonically
            increasing index. The entry price of an event at bar ``t`` is ``P_t``;
            the scan runs over bars ``t+1 .. t+vertical_bars``.
        events:
            Index **labels** to seed barriers on (e.g. CUSUM filter output). Every
            label must exist in ``prices.index`` or the call raises. ``None`` seeds
            every bar that has a full horizon ahead of it.
        side:
            Bet direction, ``+1`` or ``-1``, as a scalar or as a series aligned with
            ``prices``. Supplying it switches the output to meta-labelling.
        highs, lows:
            Optional bar extremes, aligned with ``prices``, enabling intrabar barrier
            detection. Both must be supplied together and must bracket the close.

        Returns
        -------
        A DataFrame with one row per surviving event, carrying the fields of
        :class:`TripleBarrierLabel`. If every event is filtered out the call raises
        rather than returning an empty frame that a training pipeline would read as
        "no signal".
        """
        close = _require_positive_finite_series("prices", prices)
        n = len(prices)
        if n < 2:
            raise TripleBarrierError("prices must contain at least 2 bars to estimate volatility.")
        if not prices.index.is_unique:
            raise TripleBarrierError(
                "prices index has duplicate labels; barrier scanning and event lookup are ambiguous."
            )
        if not prices.index.is_monotonic_increasing:
            raise TripleBarrierError(
                "prices index is not monotonically increasing; a forward barrier scan over "
                "out-of-order bars labels events against the wrong future."
            )
        if n < self.vertical_bars + 1:
            raise TripleBarrierError(
                f"price series length ({n}) must exceed vertical_bars ({self.vertical_bars}); "
                f"no event has a full horizon."
            )

        if (highs is None) != (lows is None):
            raise TripleBarrierError("highs and lows must be supplied together for intrabar scanning.")
        use_intrabar = highs is not None
        if use_intrabar:
            high_arr = _require_positive_finite_series("highs", highs, prices.index)
            low_arr = _require_positive_finite_series("lows", lows, prices.index)
            if (high_arr < low_arr).any():
                raise TripleBarrierError("highs must be >= lows on every bar; the arguments look swapped.")
            if ((close > high_arr) | (close < low_arr)).any():
                raise TripleBarrierError("every close must lie within its own bar's [low, high] range.")
        else:
            high_arr = low_arr = close

        sides = self._resolve_sides(prices, side)
        volatility = self.compute_volatility(prices).to_numpy(dtype=float)
        positions = self._resolve_event_positions(prices, events)
        timestamps = prices.index

        records = []
        skipped_no_target = 0
        for idx in positions:
            target = volatility[idx]
            if not np.isfinite(target) or target <= self.min_target_return:
                skipped_no_target += 1
                continue

            p0 = close[idx]
            bet = int(sides[idx])
            pt_threshold = self.pt_mult * target
            sl_threshold = -self.sl_mult * target
            vert_idx = idx + self.vertical_bars

            touched = BarrierTouched.VERTICAL_TIMEOUT
            exit_idx = vert_idx
            exit_p = close[vert_idx]
            ambiguous = False

            for sub_idx in range(idx + 1, vert_idx + 1):
                r_high = (high_arr[sub_idx] / p0 - 1.0) * bet
                r_low = (low_arr[sub_idx] / p0 - 1.0) * bet
                best, worst = (r_high, r_low) if r_high >= r_low else (r_low, r_high)

                pt_hit = self.pt_mult > 0.0 and best > pt_threshold
                sl_hit = self.sl_mult > 0.0 and worst < sl_threshold
                if not (pt_hit or sl_hit):
                    continue

                exit_idx = sub_idx
                if pt_hit and sl_hit:
                    # Both barriers inside a single bar: the intrabar path is unknown,
                    # so assume the adverse one.
                    ambiguous = True
                    touched = BarrierTouched.STOP_LOSS
                    exit_p = p0 * (1.0 + bet * sl_threshold)
                elif sl_hit:
                    touched = BarrierTouched.STOP_LOSS
                    exit_p = p0 * (1.0 + bet * sl_threshold) if use_intrabar else close[sub_idx]
                else:
                    touched = BarrierTouched.TAKE_PROFIT
                    exit_p = p0 * (1.0 + bet * pt_threshold) if use_intrabar else close[sub_idx]
                break

            realized_ret = (exit_p / p0 - 1.0) * bet
            records.append(
                TripleBarrierLabel(
                    entry_timestamp=timestamps[idx],
                    entry_price=p0,
                    exit_timestamp=timestamps[exit_idx],
                    exit_price=exit_p,
                    barrier_touched=touched,
                    realized_return=realized_ret,
                    target_volatility=target,
                    side=bet,
                    holding_bars=int(exit_idx - idx),
                    meta_label=int(realized_ret > 0.0) if side is not None else None,
                    intrabar_ambiguous=ambiguous,
                )
            )

        if skipped_no_target:
            logger.warning(
                "%d of %d event(s) dropped: the volatility target was NaN (EWM warm-up) or not above "
                "min_target_return=%s. Zero-width barriers would label an unchanged price as a touch.",
                skipped_no_target,
                len(positions),
                self.min_target_return,
            )
        if not records:
            raise TripleBarrierError(
                "no event survived target filtering; the series is too short for the volatility "
                "warm-up, or its volatility never exceeds min_target_return."
            )

        logger.info(
            "Labelled %d event(s): %d take-profit, %d stop-loss, %d vertical timeout.",
            len(records),
            sum(r.barrier_touched is BarrierTouched.TAKE_PROFIT for r in records),
            sum(r.barrier_touched is BarrierTouched.STOP_LOSS for r in records),
            sum(r.barrier_touched is BarrierTouched.VERTICAL_TIMEOUT for r in records),
        )
        # Columns are taken from TripleBarrierLabel so the emitted frame and the
        # documented schema cannot drift apart.
        frame = pd.DataFrame(
            [record.__dict__ for record in records],
            columns=[field.name for field in fields(TripleBarrierLabel)],
        )
        frame["barrier_touched"] = frame["barrier_touched"].astype(int)
        return frame

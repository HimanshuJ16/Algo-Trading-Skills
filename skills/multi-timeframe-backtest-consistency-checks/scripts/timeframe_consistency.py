"""
multi-timeframe-backtest-consistency-checks: wall-clock-anchored OHLCV bar
resampler and cross-provenance timeframe consistency auditor.

The question this module answers
--------------------------------
"My strategy reads 5-minute bars. My vendor supplies 5-minute bars, but I also
have 1-minute bars. Do the two agree?" A disagreement means the vendor's bars,
my resampling, or my session/boundary assumptions are wrong -- and every
backtest run on the losing series is invalid.

That check requires **two independent provenances of the same timeframe**:
bars resampled here from high-resolution input, and reference bars obtained
independently. Both sides are then compared at the same resolution, with the
same indicator and the same period.

What this module deliberately does NOT do
-----------------------------------------
It does not compare an indicator computed on low-resolution bars against the
same indicator computed with a proportionally scaled period on high-resolution
bars (e.g. SMA(3) on 5-minute bars vs SMA(15) on 1-minute bars). Those are
**different estimators over different observation sets** and they do not agree
on any series that is not flat:

    SMA_P(resampled closes) averages P closes sampled every `factor` bars.
    SMA_{P*factor}(high-res closes) averages all P*factor closes.

On closes 100 + 0.1i with factor 5 and P = 3 the two differ by a constant 0.2
even after perfect timestamp alignment. Because the gap is measured as a
*percentage*, it also scales with price level: the identical, correct
resampling of the identical series shape reads 0.60% at a price near 100 and
5.56% at a price near 10. A tolerance-based verdict built on that comparison
reports a price level, not a resampling defect. Earlier versions of this module
made exactly that comparison; see `references/standards.md`.

Boundary anchoring
------------------
Bars are bucketed by wall-clock time, never by position in the input list.
Position-based chunking cannot see a gap: a missing 09:32 bar silently shifts
every subsequent bucket by one bar, which is the "boundary bar misalignment"
failure this skill exists to catch.

Two anchors are supported, matching the two conventions in common use:

- ``ANCHOR_EPOCH`` -- buckets align to the fixed grid ``ts % bucket_seconds``,
  i.e. the grid running from the Unix epoch. Because the epoch is a UTC
  midnight and days are a whole number of seconds, this is the same grid as
  pandas' default ``origin="start_day"`` **when the timestamps are UTC**.
- ``ANCHOR_SESSION`` -- buckets align to the first bar of the supplied series,
  equivalent to pandas ``origin="start"``.

These two agree only when the session's first bar already sits on the epoch
grid. NSE's capital market segment opens at 09:15 IST (= 03:45 UTC, 13500 s
after UTC midnight): 13500 / 900 = 15 exactly, so 15-minute buckets align under
either anchor, but 13500 / 1800 = 7.5, so 30-minute epoch-anchored buckets open
at 09:00 IST and the first bucket of the day holds only half a session's bars.
Choose the anchor deliberately; do not accept the default without checking it
against the venue's session start.

Conventions
-----------
- ``Bar.timestamp`` is the bar's **opening** time in epoch seconds, and
  resampled bars are labelled with the bucket's opening time. This is
  left-labelling, matching pandas' default ``label="left"``/``closed="left"``
  for tick frequencies. A bucket covers ``[start, start + bucket_seconds)``.
- Resampling is an **exact** arithmetic operation, not an estimate. A correctly
  resampled series must reproduce the reference to floating-point noise, so the
  default tolerance is set at noise level rather than at a percentage that
  would mask a genuine one-bar boundary error.

Limitations (documented, deliberate)
------------------------------------
- **Input must already be clean.** ``Bar`` rejects non-finite values and
  OHLC relation violations at construction. Bad-tick filtering belongs
  upstream; see `backtest-outlier-and-bad-tick-filtering`.
- **A gap cannot be distinguished from a not-yet-finished period.** A trailing
  bucket holding fewer than `factor` source bars may be a completed period with
  missing bars, or a period still forming. Treating a still-forming bar as
  final leaks information from an incomplete period, so it is dropped by
  default; see `lookahead-bias-elimination`.
- **No timezone or session-calendar awareness.** Timestamps are treated as
  epoch seconds. DST transitions and half-days are the caller's problem; see
  `daylight-saving-time-transition-handling` and
  `global-exchange-holiday-calendar-handling`.
- **SMA only.** ``compute_sma`` is the reference indicator for the parity
  check. It is not a general indicator library.
"""
import logging
import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Buckets align to the fixed grid running from the Unix epoch (UTC midnight).
ANCHOR_EPOCH = "epoch"

#: Buckets align to the first bar of the supplied series.
ANCHOR_SESSION = "session"

VALID_ANCHORS = (ANCHOR_EPOCH, ANCHOR_SESSION)

#: Resampling is exact arithmetic, so a correct series matches its reference to
#: floating-point noise. 1e-6 percent is ~1e-8 relative -- six orders of
#: magnitude above double-precision noise, and five orders below the ~0.1%
#: divergence produced by a genuine one-bar boundary error on a price near 100.
DEFAULT_DIVERGENCE_TOLERANCE_PCT = 1e-6

#: Absolute tolerance for comparing aggregated OHLCV fields against a reference.
DEFAULT_ABS_TOLERANCE = 1e-9


class InsufficientDataError(ValueError):
    """Raised when a check cannot be performed for lack of comparable data.

    A consistency checker that silently reports "consistent" after making zero
    comparisons provides false assurance, which is worse than no check at all.
    """


@dataclass(frozen=True)
class Bar:
    """A single OHLCV bar.

    ``timestamp`` is the bar's **opening** time in epoch seconds; the bar covers
    ``[timestamp, timestamp + bar_interval_seconds)``.

    Validation runs at construction: prices must be finite, volume finite and
    non-negative, and the OHLC relations must hold. Prices are *not* required to
    be positive -- settlement prices for spreads and some futures legitimately
    go negative.
    """

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float

    def __post_init__(self) -> None:
        for field_name in ("open", "high", "low", "close", "volume"):
            value = getattr(self, field_name)
            if not math.isfinite(value):
                raise ValueError(
                    f"Bar at {self.timestamp}: {field_name} is not finite ({value!r}). "
                    "Filter bad ticks before resampling."
                )
        if self.volume < 0:
            raise ValueError(f"Bar at {self.timestamp}: negative volume {self.volume!r}.")
        if self.high < self.low:
            raise ValueError(
                f"Bar at {self.timestamp}: high {self.high!r} < low {self.low!r}."
            )
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise ValueError(
                f"Bar at {self.timestamp}: OHLC relations violated "
                f"(o={self.open!r} h={self.high!r} l={self.low!r} c={self.close!r})."
            )


@dataclass(frozen=True)
class ResampleResult:
    """Resampled bars plus the diagnostics needed to trust them.

    ``incomplete_buckets`` counts *interior* buckets built from fewer than
    ``factor`` source bars -- always a data gap, never a still-forming period.
    A non-zero count means the aggregated high/low/volume for those buckets are
    computed over partial data and will not match a reference built from a
    complete series.
    """

    bars: List[Bar]
    source_bars: int
    bucket_seconds: int
    anchor: str
    complete_buckets: int
    incomplete_buckets: int
    dropped_incomplete_final: bool


@dataclass(frozen=True)
class IntegrityReport:
    """Exact OHLCV comparison between self-resampled and reference bars."""

    compared_buckets: int
    mismatched_buckets: int
    missing_in_reference: int
    missing_in_resampled: int
    field_mismatches: Dict[str, int]
    first_mismatch_timestamp: Optional[int]
    is_consistent: bool
    message: str


@dataclass(frozen=True)
class ConsistencyReport:
    """Indicator parity between self-resampled and reference bars.

    ``max_divergence_pct`` is relative to the reference value. Because a
    percentage shrinks as price level rises, ``max_absolute_divergence`` is
    reported alongside it -- on a low-priced instrument the percentage is the
    more sensitive figure, on a high-priced one it is the less sensitive.
    """

    high_res_bars: int
    resampled_bars: int
    reference_bars: int
    matched_signals: int
    max_divergence_pct: float
    avg_divergence_pct: float
    max_absolute_divergence: float
    worst_timestamp: Optional[int]
    is_consistent: bool
    message: str


def _validate_positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}.")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}.")
    return value


def _validate_series(bars: Sequence[Bar], bar_interval_seconds: int, name: str) -> None:
    """Rejects series that cannot be bucketed reliably.

    Timestamps must be strictly increasing (duplicates and out-of-order bars are
    silently mis-bucketed by any positional scheme), and every gap must be a
    whole multiple of ``bar_interval_seconds`` -- a fractional gap means the
    declared interval does not describe the data.
    """
    if not bars:
        raise InsufficientDataError(f"{name} is empty.")
    for previous, current in zip(bars, bars[1:]):
        delta = current.timestamp - previous.timestamp
        if delta <= 0:
            raise ValueError(
                f"{name}: timestamps must be strictly increasing; "
                f"{previous.timestamp} is followed by {current.timestamp}."
            )
        if delta % bar_interval_seconds != 0:
            raise ValueError(
                f"{name}: gap of {delta}s between {previous.timestamp} and "
                f"{current.timestamp} is not a multiple of the declared "
                f"bar interval {bar_interval_seconds}s."
            )


def _bucket_start(timestamp: int, bucket_seconds: int, anchor: str, origin: int) -> int:
    """Wall-clock bucket opening time for ``timestamp``.

    Floor division is used deliberately so pre-epoch (negative) timestamps floor
    toward the earlier bucket rather than toward zero.
    """
    if anchor == ANCHOR_EPOCH:
        return timestamp - (timestamp % bucket_seconds)
    return origin + ((timestamp - origin) // bucket_seconds) * bucket_seconds


class TimeframeConsistencyChecker:
    """Resamples high-resolution bars and audits them against a reference series.

    Args:
        divergence_tolerance_pct: Maximum permitted indicator divergence,
            as a percentage of the reference value. The default is set at
            floating-point noise level because resampling is exact arithmetic.
            Loosen it only when the reference is known to be built differently
            (different tick filtering, different session boundaries), and record
            why.
        min_comparisons: Minimum number of overlapping points a check must find
            before it is allowed to return a verdict. Below this the check
            raises rather than reporting a pass it did not earn.
    """

    def __init__(
        self,
        divergence_tolerance_pct: float = DEFAULT_DIVERGENCE_TOLERANCE_PCT,
        min_comparisons: int = 1,
    ) -> None:
        if not math.isfinite(divergence_tolerance_pct) or divergence_tolerance_pct < 0:
            raise ValueError(
                f"divergence_tolerance_pct must be finite and >= 0, "
                f"got {divergence_tolerance_pct!r}."
            )
        self.tolerance_pct = divergence_tolerance_pct
        self.min_comparisons = _validate_positive_int(min_comparisons, "min_comparisons")

    def resample_bars(
        self,
        bars: Sequence[Bar],
        factor: int,
        bar_interval_seconds: int,
        anchor: str = ANCHOR_EPOCH,
        drop_incomplete_final: bool = True,
    ) -> ResampleResult:
        """Aggregates bars into wall-clock buckets ``factor`` intervals wide.

        Args:
            bars: Strictly time-ordered high-resolution bars.
            factor: Number of source intervals per output bucket (5 turns
                1-minute bars into 5-minute bars).
            bar_interval_seconds: Width of one *source* bar. Required rather
                than inferred: inferring it from the modal gap silently
                misreads a series that opens with a gap.
            anchor: ``ANCHOR_EPOCH`` or ``ANCHOR_SESSION``. See module docstring
                -- the two differ whenever the session start is off the epoch
                grid.
            drop_incomplete_final: Drop a trailing bucket holding fewer than
                ``factor`` source bars. A still-forming bucket presented as a
                finished bar leaks information from an unfinished period into
                the backtest.

        Returns:
            A :class:`ResampleResult` carrying the bars and the bucket
            completeness diagnostics.
        """
        _validate_positive_int(factor, "factor")
        _validate_positive_int(bar_interval_seconds, "bar_interval_seconds")
        if anchor not in VALID_ANCHORS:
            raise ValueError(f"anchor must be one of {VALID_ANCHORS}, got {anchor!r}.")
        _validate_series(bars, bar_interval_seconds, "bars")

        bucket_seconds = bar_interval_seconds * factor
        origin = bars[0].timestamp

        grouped: List[Tuple[int, List[Bar]]] = []
        for bar in bars:
            start = _bucket_start(bar.timestamp, bucket_seconds, anchor, origin)
            if grouped and grouped[-1][0] == start:
                grouped[-1][1].append(bar)
            else:
                grouped.append((start, [bar]))

        dropped_final = False
        if drop_incomplete_final and grouped and len(grouped[-1][1]) < factor:
            grouped.pop()
            dropped_final = True

        resampled: List[Bar] = []
        incomplete = 0
        for start, chunk in grouped:
            if len(chunk) < factor:
                incomplete += 1
            resampled.append(
                Bar(
                    timestamp=start,
                    open=chunk[0].open,
                    high=max(b.high for b in chunk),
                    low=min(b.low for b in chunk),
                    close=chunk[-1].close,
                    volume=math.fsum(b.volume for b in chunk),
                )
            )

        if incomplete:
            logger.warning(
                "Resampling produced %d bucket(s) built from fewer than %d source "
                "bars. These are gaps in the source series; their high/low/volume "
                "are computed over partial data.",
                incomplete,
                factor,
            )

        return ResampleResult(
            bars=resampled,
            source_bars=len(bars),
            bucket_seconds=bucket_seconds,
            anchor=anchor,
            complete_buckets=len(resampled) - incomplete,
            incomplete_buckets=incomplete,
            dropped_incomplete_final=dropped_final,
        )

    def compute_sma(self, bars: Sequence[Bar], period: int) -> List[Tuple[int, float]]:
        """Trailing simple moving average of closes.

        Returns ``(timestamp, sma)`` pairs where ``timestamp`` is the opening
        time of the **last** bar in the window, so the value is labelled with
        the bar that completes it. Results are not rounded: rounding inside a
        comparison primitive injects error of the same order as the tolerance
        the comparison is testing against.
        """
        _validate_positive_int(period, "period")
        results: List[Tuple[int, float]] = []
        for index in range(period - 1, len(bars)):
            window = bars[index - period + 1: index + 1]
            results.append((bars[index].timestamp, math.fsum(b.close for b in window) / period))
        return results

    def check_resampling_integrity(
        self,
        high_res_bars: Sequence[Bar],
        reference_low_res_bars: Sequence[Bar],
        factor: int,
        bar_interval_seconds: int,
        anchor: str = ANCHOR_EPOCH,
        abs_tolerance: float = DEFAULT_ABS_TOLERANCE,
    ) -> IntegrityReport:
        """Compares every aggregated OHLCV field against the reference series.

        This is the check that actually catches boundary misalignment and
        volume double-counting. It needs no percentage tolerance: aggregation is
        exact, so a correct series matches field-for-field.

        A trailing incomplete bucket is always dropped here, because a check
        run for a backtest must not bless a period that had not finished. If the
        reference series does include that bucket the difference surfaces as
        ``missing_in_resampled``, not as a field mismatch.
        """
        resampled = self.resample_bars(
            high_res_bars, factor, bar_interval_seconds, anchor, drop_incomplete_final=True
        )
        _validate_series(
            reference_low_res_bars,
            bar_interval_seconds * factor,
            "reference_low_res_bars",
        )

        ours = {bar.timestamp: bar for bar in resampled.bars}
        theirs = {bar.timestamp: bar for bar in reference_low_res_bars}
        shared = sorted(set(ours) & set(theirs))

        if len(shared) < self.min_comparisons:
            raise InsufficientDataError(
                f"Only {len(shared)} bucket(s) overlap between the resampled and "
                f"reference series (minimum {self.min_comparisons}). Nothing was "
                f"verified. A non-overlap this complete usually means the two "
                f"series use different boundary anchors -- try "
                f"anchor={ANCHOR_SESSION!r}."
            )

        field_mismatches: Dict[str, int] = {}
        mismatched = 0
        first_mismatch: Optional[int] = None
        for timestamp in shared:
            mine, reference = ours[timestamp], theirs[timestamp]
            bad = [
                name
                for name in ("open", "high", "low", "close", "volume")
                if not math.isclose(
                    getattr(mine, name),
                    getattr(reference, name),
                    rel_tol=0.0,
                    abs_tol=abs_tolerance,
                )
            ]
            if bad:
                mismatched += 1
                if first_mismatch is None:
                    first_mismatch = timestamp
                for name in bad:
                    field_mismatches[name] = field_mismatches.get(name, 0) + 1

        missing_in_reference = len(set(ours) - set(theirs))
        missing_in_resampled = len(set(theirs) - set(ours))
        is_ok = mismatched == 0 and missing_in_reference == 0 and missing_in_resampled == 0

        if is_ok:
            message = (
                f"Resampling integrity PASSED: {len(shared)} bucket(s) match the "
                f"reference on every OHLCV field."
            )
        else:
            message = (
                f"RESAMPLING INTEGRITY FAILURE: {mismatched}/{len(shared)} bucket(s) "
                f"differ (fields: {field_mismatches or 'none'}); "
                f"{missing_in_reference} bucket(s) absent from the reference, "
                f"{missing_in_resampled} absent from the resampled series. "
                f"First mismatch at {first_mismatch}."
            )
            logger.warning(message)

        return IntegrityReport(
            compared_buckets=len(shared),
            mismatched_buckets=mismatched,
            missing_in_reference=missing_in_reference,
            missing_in_resampled=missing_in_resampled,
            field_mismatches=field_mismatches,
            first_mismatch_timestamp=first_mismatch,
            is_consistent=is_ok,
            message=message,
        )

    def check_consistency(
        self,
        high_res_bars: Sequence[Bar],
        reference_low_res_bars: Sequence[Bar],
        factor: int,
        sma_period: int,
        bar_interval_seconds: int,
        anchor: str = ANCHOR_EPOCH,
    ) -> ConsistencyReport:
        """Compares the same indicator, at the same period and the same
        timeframe, across two independent provenances.

        ``sma_period`` is expressed in **low-resolution** bars and is applied
        unchanged to both series. It is deliberately not scaled by ``factor``:
        scaling it would compare two different estimators, which disagree on any
        non-flat series regardless of how correct the resampling is.

        Raises:
            InsufficientDataError: if fewer than ``min_comparisons`` points
                overlap. The check reports no verdict it did not earn.
        """
        _validate_positive_int(sma_period, "sma_period")
        resampled = self.resample_bars(
            high_res_bars, factor, bar_interval_seconds, anchor, drop_incomplete_final=True
        )
        _validate_series(
            reference_low_res_bars,
            bar_interval_seconds * factor,
            "reference_low_res_bars",
        )

        sma_ours = dict(self.compute_sma(resampled.bars, sma_period))
        sma_reference = dict(self.compute_sma(reference_low_res_bars, sma_period))

        divergences: List[float] = []
        max_pct = 0.0
        max_abs = 0.0
        worst_timestamp: Optional[int] = None
        for timestamp, reference_value in sorted(sma_reference.items()):
            our_value = sma_ours.get(timestamp)
            if our_value is None:
                continue
            absolute = abs(our_value - reference_value)
            if reference_value == 0.0:
                # Never silently drop the point: a zero reference with a
                # non-zero difference is a total mismatch, not a missing datum.
                percent = 0.0 if absolute == 0.0 else math.inf
            else:
                percent = absolute / abs(reference_value) * 100.0
            divergences.append(percent)
            if percent > max_pct:
                max_pct, worst_timestamp = percent, timestamp
            max_abs = max(max_abs, absolute)

        if len(divergences) < self.min_comparisons:
            raise InsufficientDataError(
                f"Only {len(divergences)} SMA point(s) overlap between the "
                f"resampled and reference series (minimum {self.min_comparisons}). "
                f"Nothing was verified. Supply at least "
                f"{(sma_period + 1) * factor} high-resolution bars for a "
                f"{sma_period}-period SMA, and confirm both series use the same "
                f"boundary anchor."
            )

        finite = [d for d in divergences if math.isfinite(d)]
        average = math.fsum(finite) / len(finite) if len(finite) == len(divergences) else math.inf
        is_ok = max_pct <= self.tolerance_pct

        if is_ok:
            message = (
                f"Timeframe consistency PASSED: {len(divergences)} point(s) compared, "
                f"max divergence {max_pct:.3e}% (<= {self.tolerance_pct:g}%)."
            )
        else:
            message = (
                f"TIMEFRAME INCONSISTENCY: max divergence {max_pct:.3e}% "
                f"(absolute {max_abs:.6g}) at timestamp {worst_timestamp} exceeds "
                f"{self.tolerance_pct:g}% over {len(divergences)} point(s)."
            )
            logger.warning(message)

        return ConsistencyReport(
            high_res_bars=len(high_res_bars),
            resampled_bars=len(resampled.bars),
            reference_bars=len(reference_low_res_bars),
            matched_signals=len(divergences),
            max_divergence_pct=max_pct,
            avg_divergence_pct=average,
            max_absolute_divergence=max_abs,
            worst_timestamp=worst_timestamp,
            is_consistent=is_ok,
            message=message,
        )

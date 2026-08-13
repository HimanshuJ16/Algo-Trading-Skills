"""
backtest-outlier-and-bad-tick-filtering: Rolling MAD outlier detector,
bad tick filter, and data cleanliness report generator.

Detection rule
--------------
The modified Z-score of Iglewicz and Hoaglin, as documented in the NIST/SEMATECH
e-Handbook of Statistical Methods section 1.3.5.17:

    M_i = 0.6745 * (x_i - median) / MAD

A price is flagged when ``M_i`` exceeds ``z_threshold``. The comparison is
implemented in price units with an additive floor, following the outlier rule of
Brownlees and Gallo (2006):

    |p_i - median| > z_threshold * MAD / 0.6745 + min_deviation

``min_deviation`` is that paper's minimum-price-variation term; set it to the
instrument's tick size. It stops the test from flagging one-tick moves in very
quiet windows, where MAD collapses toward zero.

Deviation from the canonical method
-----------------------------------
Brownlees and Gallo evaluate each observation against a neighbourhood of the k
records *closest in time* to it, which is centred and therefore non-causal. This
implementation uses a strictly **trailing** window of already-accepted prices, so
the decision for tick i never reads tick i+1. Two consequences follow, and both
are documented in ``references/standards.md``:

  * the first ``window_size`` accepted ticks get no MAD screening at all, so a bad
    print at the very start of a file can survive and anchor the jump test;
  * the first tick after a genuine level shift always looks like an outlier,
    which is what ``max_consecutive_drops`` exists to recover from.

Filtering is not free: every purged print is a real observation the backtest will
never see. See ``SKILL.md`` "When NOT to Use" before applying this to a series
used for stop-loss or liquidation modelling.
"""
from dataclasses import dataclass, field
import logging
import math
from typing import List, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Consistency constant making MAD an estimator of sigma under normality; it is the
#: 0.75 quantile of the standard normal. Fixed by the Iglewicz-Hoaglin definition.
MAD_CONSISTENCY_CONSTANT = 0.6745

#: Smallest window for which a median and a median absolute deviation carry
#: meaningful scale information.
MIN_WINDOW_SIZE = 3


class OutlierFilterError(ValueError):
    """Raised on invalid filter configuration or a structurally invalid price series.

    Non-finite and non-positive *values* are bad ticks and get purged. A wrong
    *type* in the price array is a schema error and raises instead, because
    silently dropping it would hide a broken ingestion path.
    """


@dataclass
class FilteredTickReport:
    """Outcome of one filtering pass.

    Attributes:
        total_input_ticks: Length of the input series.
        cleaned_ticks_count: Number of prices in the returned clean series.
        purged_bad_ticks_count: Number of prices removed.
        purged_indices: Input positions that were removed, ascending.
        cleanliness_pct: Percentage of input ticks retained, rounded to 2 dp.
        message: Human-readable summary.
        kept_indices: Input positions that survived, ascending. `cleaned[j]` came
            from `prices[kept_indices[j]]`; use this to realign timestamps or any
            other parallel array.
        non_finite_count: Prices purged for being NaN or infinite.
        non_positive_count: Prices purged for being <= 0.
        regime_changes_detected: Times a run of consecutive outliers was accepted
            as a genuine level shift rather than bad prints.
        mad_test_skipped_count: Ticks for which the MAD test was undefined (MAD was
            zero and `min_deviation` was zero) and therefore not applied. A large
            value means most of the series was screened by the jump rule alone.
    """

    total_input_ticks: int
    cleaned_ticks_count: int
    purged_bad_ticks_count: int
    purged_indices: List[int]
    cleanliness_pct: float
    message: str
    kept_indices: List[int] = field(default_factory=list)
    non_finite_count: int = 0
    non_positive_count: int = 0
    regime_changes_detected: int = 0
    mad_test_skipped_count: int = 0


class OutlierBadTickFilter:
    """
    Filters erroneous price prints, fat-finger quotes, and price spikes using
    rolling Median Absolute Deviation (MAD) modified Z-scores.
    """

    def __init__(
        self,
        window_size: int = 21,
        z_threshold: float = 5.0,
        max_single_tick_jump_pct: float = 20.0,
        max_consecutive_drops: int = 3,
        min_deviation: float = 0.0,
        restore_ticks_on_regime_change: bool = True,
    ) -> None:
        """
        Args:
            window_size: Trailing window of accepted prices used for median and MAD.
            z_threshold: Modified Z-score above which a price is an outlier. Iglewicz
                and Hoaglin recommend 3.5 for generic data; the default of 5.0 is
                deliberately looser because genuine intraday returns are fat-tailed
                and a tight threshold deletes real volatility.
            max_single_tick_jump_pct: Percentage move from the last accepted price
                that marks a print as erroneous. The 20.0 default is loose for liquid
                equities — see `references/standards.md` for calibration against
                published venue thresholds.
            max_consecutive_drops: Length of a consecutive-outlier run that is
                reinterpreted as a genuine level shift rather than bad prints.
                Must be at least 2; a value of 1 would accept every outlier.
            min_deviation: Brownlees-Gallo minimum price variation, in price units,
                added to the MAD threshold. Set to the instrument's tick size to give
                the test power in flat windows. At 0.0 the MAD test is skipped
                whenever MAD is zero, which leaves quiet stretches screened by the
                jump rule alone.
            restore_ticks_on_regime_change: When True (default), the outliers purged
                during a run that turns out to be a genuine level shift are put back,
                because the evidence says they were real prices. This makes the
                decision for those ticks depend on up to `max_consecutive_drops - 1`
                later ticks. Set False for a strictly causal pass whose output
                matches what a live streaming filter would produce.

        Raises:
            OutlierFilterError: On any invalid parameter.
        """
        if not isinstance(window_size, int) or isinstance(window_size, bool):
            raise OutlierFilterError(f"window_size must be an int, got {type(window_size).__name__}.")
        if window_size < MIN_WINDOW_SIZE:
            raise OutlierFilterError(
                f"window_size must be >= {MIN_WINDOW_SIZE} for a median and MAD to carry "
                f"scale information, got {window_size}. Note that 0 would silently select "
                f"the entire history, since prices[-0:] is prices[0:]."
            )
        if not math.isfinite(z_threshold) or z_threshold <= 0:
            raise OutlierFilterError(f"z_threshold must be a finite positive number, got {z_threshold}.")
        if not math.isfinite(max_single_tick_jump_pct) or max_single_tick_jump_pct <= 0:
            raise OutlierFilterError(
                f"max_single_tick_jump_pct must be a finite positive percentage, got "
                f"{max_single_tick_jump_pct}."
            )
        if not isinstance(max_consecutive_drops, int) or isinstance(max_consecutive_drops, bool):
            raise OutlierFilterError(
                f"max_consecutive_drops must be an int, got {type(max_consecutive_drops).__name__}."
            )
        if max_consecutive_drops < 2:
            raise OutlierFilterError(
                f"max_consecutive_drops must be >= 2, got {max_consecutive_drops}. A value of 1 "
                f"makes every outlier immediately qualify as a regime change, silently disabling "
                f"all outlier purging."
            )
        if not math.isfinite(min_deviation) or min_deviation < 0:
            raise OutlierFilterError(f"min_deviation must be a finite non-negative number, got {min_deviation}.")

        self.window_size = window_size
        self.z_threshold = z_threshold
        self.max_single_tick_jump_pct = max_single_tick_jump_pct
        self.max_consecutive_drops = max_consecutive_drops
        self.min_deviation = min_deviation
        self.restore_ticks_on_regime_change = restore_ticks_on_regime_change

    @staticmethod
    def _median(arr: List[float]) -> float:
        s = sorted(arr)
        n = len(s)
        if n % 2 == 1:
            return s[n // 2]
        return (s[n // 2 - 1] + s[n // 2]) / 2.0

    def filter_prices(self, prices: Sequence[float]) -> Tuple[List[float], FilteredTickReport]:
        """Removes bad prints from a price series.

        Args:
            prices: Raw price series in chronological order. NaN, infinite and
                non-positive values are treated as bad ticks and purged.

        Returns:
            `(cleaned_prices, report)`. Use `report.kept_indices` to realign
            timestamps or any other array parallel to `prices` — the cleaned series
            alone carries no positional information.

        Raises:
            OutlierFilterError: If any element is not a real number. That is a schema
                error rather than a bad tick, so it fails loudly.
        """
        price_list = list(prices)
        n = len(price_list)
        if n == 0:
            rep = FilteredTickReport(0, 0, 0, [], 100.0, "Empty price series.")
            return [], rep

        for i, p in enumerate(price_list):
            if isinstance(p, bool) or not isinstance(p, (int, float)):
                raise OutlierFilterError(
                    f"prices[{i}] must be a real number, got {type(p).__name__} ({p!r})."
                )

        cleaned_prices: List[float] = []
        kept_indices: List[int] = []
        purged_indices: List[int] = []
        non_finite_count = 0
        non_positive_count = 0
        regime_changes = 0
        mad_skipped = 0

        # Outliers purged during the current consecutive run. Restored if the run
        # turns out to be a genuine level shift.
        pending: List[Tuple[int, float]] = []
        # Position in cleaned_prices where the current price regime starts. After a
        # confirmed level shift the pre-shift prices are no longer a valid reference
        # distribution, so the MAD window restarts from here.
        regime_floor = 0

        for i in range(n):
            p = float(price_list[i])

            # Rule 0: NaN and Inf are never valid prices. Left unchecked, NaN passes
            # every comparison below (all NaN comparisons are False), enters the
            # cleaned series, and then corrupts every subsequent median.
            if not math.isfinite(p):
                purged_indices.append(i)
                non_finite_count += 1
                pending.clear()
                logger.debug("BAD TICK PURGED (non-finite): idx=%d, price=%r", i, p)
                continue

            # Rule 1: Non-positive price check
            if p <= 0:
                purged_indices.append(i)
                non_positive_count += 1
                pending.clear()
                logger.debug("BAD TICK PURGED (non-positive): idx=%d, price=%s", i, p)
                continue

            is_outlier = False
            drop_reason = ""

            # Rule 2: Single tick jump check vs previous valid price
            if cleaned_prices:
                prev_p = cleaned_prices[-1]
                pct_change = abs(p - prev_p) / prev_p * 100.0
                if pct_change > self.max_single_tick_jump_pct:
                    is_outlier = True
                    drop_reason = f"Jump {pct_change:.1f}%"

            # Rule 3: Rolling MAD modified Z-score check
            if not is_outlier and (len(cleaned_prices) - regime_floor) >= self.window_size:
                window = cleaned_prices[-self.window_size:]
                med = self._median(window)
                mad = self._median([abs(x - med) for x in window])

                if mad > 0 or self.min_deviation > 0:
                    # Equivalent to 0.6745 * |p - med| / mad > z_threshold, rearranged
                    # into price units so the min_deviation floor can be added and so
                    # mad == 0 does not divide by zero.
                    allowed = (self.z_threshold * mad / MAD_CONSISTENCY_CONSTANT) + self.min_deviation
                    deviation = abs(p - med)
                    if deviation > allowed:
                        is_outlier = True
                        mod_z = (
                            (MAD_CONSISTENCY_CONSTANT * deviation / mad) if mad > 0 else float("inf")
                        )
                        drop_reason = f"Z={mod_z:.1f}"
                else:
                    # MAD is zero and no floor is configured: the scale estimate has
                    # degenerated and the test is undefined, so it is not applied.
                    mad_skipped += 1

            if is_outlier:
                if len(pending) + 1 >= self.max_consecutive_drops:
                    regime_changes += 1
                    logger.info(
                        "REGIME CHANGE DETECTED: %d consecutive outliers. Accepting price %s at "
                        "idx=%d and resetting.",
                        len(pending) + 1,
                        p,
                        i,
                    )
                    # Restart the MAD window at the new level. Without this the window
                    # stays contaminated by the pre-shift prices for `window_size` more
                    # ticks and keeps re-flagging perfectly good prints.
                    regime_floor = len(cleaned_prices)
                    if self.restore_ticks_on_regime_change:
                        # The run was a genuine level shift, so the prints purged on the
                        # way into it were real prices. Put them back in order.
                        for pending_idx, pending_price in pending:
                            purged_indices.remove(pending_idx)
                            kept_indices.append(pending_idx)
                            cleaned_prices.append(pending_price)
                    pending.clear()
                    # Fall through and accept p.
                else:
                    purged_indices.append(i)
                    pending.append((i, p))
                    logger.debug("BAD TICK PURGED (%s): idx=%d, price=%s", drop_reason, i, p)
                    continue
            else:
                pending.clear()

            kept_indices.append(i)
            cleaned_prices.append(p)

        purged_indices.sort()
        purged_cnt = len(purged_indices)
        clean_pct = round(((n - purged_cnt) / float(n)) * 100.0, 2)
        msg = (
            f"Outlier Filtering Complete: {n} raw ticks -> {len(cleaned_prices)} clean ticks "
            f"({purged_cnt} bad ticks purged, {clean_pct}% clean)."
        )
        logger.info(msg)

        report = FilteredTickReport(
            total_input_ticks=n,
            cleaned_ticks_count=len(cleaned_prices),
            purged_bad_ticks_count=purged_cnt,
            purged_indices=purged_indices,
            cleanliness_pct=clean_pct,
            message=msg,
            kept_indices=kept_indices,
            non_finite_count=non_finite_count,
            non_positive_count=non_positive_count,
            regime_changes_detected=regime_changes,
            mad_test_skipped_count=mad_skipped,
        )

        return cleaned_prices, report

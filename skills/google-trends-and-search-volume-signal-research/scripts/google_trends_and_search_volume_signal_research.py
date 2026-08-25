"""
Google Trends / Search Volume Index (SVI) attention-spike signal engine.

Standardizes an SVI observation against a STRICTLY TRAILING baseline window and
emits an attention-spike research signal, split by contemporaneous price momentum.

Methodological provenance
-------------------------
The trailing-baseline construction follows the published SVI literature, in which
the baseline is always computed over periods *preceding* the observation tested:

  Preis, T., Moat, H. S. & Stanley, H. E. (2013). "Quantifying Trading Behavior in
  Financial Markets Using Google Trends." Scientific Reports 3, 1684.
      "To quantify changes in information gathering behavior, we use the relative
       change in search volume: dn(t, dt) = n(t) - N(t - 1, dt) with
       N(t - 1, dt) = (n(t - 1) + n(t - 2) + ... + n(t - dt))/dt"

  Da, Z., Engelberg, J. & Gao, P. (2011). "In Search of Attention."
  Journal of Finance 66(5), 1461-1499.
      "abnormal SVI (ASVI), which is defined as the (log) SVI during the current
       week minus the (log) median SVI during the previous eight weeks"

Including the tested observation in its own baseline inflates the baseline standard
deviation and drags the baseline mean toward the observation. The distortion is
largest precisely when a spike occurs, which is when the statistic matters, so this
module excludes it.

Scope
-----
This is a research feature generator, not a trading strategy. It carries no return
model, no holding period, no position sizing and no calibration. The default
thresholds are illustrative starting points, NOT validated constants.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import logging
import math
import statistics
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

SIGNAL_BULLISH = "BULLISH_ATTENTION_SURGE"
SIGNAL_BEARISH = "BEARISH_PANIC_SPIKE"
SIGNAL_NEUTRAL = "NEUTRAL_ATTENTION"
SIGNAL_INSUFFICIENT = "INSUFFICIENT_DATA"


def _parse_iso_timestamp(value: str, context: str) -> datetime:
    """
    Parses an ISO-8601 timestamp and rejects timezone-naive values.

    Google Trends time bases differ by query window: pulls of 30 days or longer are
    stamped in UTC, while pulls of 7 days or less are stamped in the *requester's*
    local timezone (Google Trends FAQ, "Time zones"). A naive timestamp therefore
    carries no reliable time base, and silently mixing the two against market data
    is a look-ahead hazard. Naive input is rejected rather than assumed to be UTC.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: timestamp must be a non-empty ISO-8601 string")
    raw = value.strip()
    # datetime.fromisoformat does not accept a trailing 'Z' before Python 3.11.
    normalised = raw[:-1] + "+00:00" if raw.endswith(("Z", "z")) else raw
    try:
        parsed = datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ValueError(f"{context}: '{raw}' is not a valid ISO-8601 timestamp ({exc})") from exc
    if parsed.tzinfo is None or parsed.tzinfo.utcoffset(parsed) is None:
        raise ValueError(
            f"{context}: '{raw}' is timezone-naive. Google Trends stamps windows of 30 days "
            "or more in UTC and windows of 7 days or less in the requester's local timezone; "
            "supply an explicit offset so the series can be aligned to market time."
        )
    return parsed


@dataclass
class SviDataPoint:
    """
    One Search Volume Index observation for one keyword.

    Attributes:
        timestamp_iso: ISO-8601 timestamp of the period the SVI value *describes*
            (the Trends bucket start), not the time it was downloaded. Must carry
            an explicit UTC offset.
        keyword: The exact query string submitted to Google Trends.
        svi_score: The index value. The Trends web UI and pytrends return a 0-100
            index rescaled per request; the official Trends API (alpha) returns
            consistently scaled values that are explicitly not 0-100.
        publication_lag_hours: Hours after `timestamp_iso` at which this observation
            actually became retrievable, used to filter the series to what was
            knowable at a given `as_of` instant. The default of 24 is a placeholder,
            NOT a documented Google guarantee - measure the real lag of your own
            collection pipeline and set it explicitly.
    """

    timestamp_iso: str
    keyword: str
    svi_score: float
    publication_lag_hours: float = 24.0


@dataclass
class GoogleTrendsSignalReport:
    """
    Attention-spike signal for one keyword as of one instant.

    Attributes:
        ticker_or_keyword: Keyword the signal was computed for.
        latest_svi_score: The observation that was standardized.
        rolling_mean_svi: Mean of the strictly trailing baseline window; excludes
            `latest_svi_score`.
        rolling_std_dev_svi: Sample standard deviation (ddof=1) of that same
            trailing window. Reported as observed - never floored or substituted.
        svi_z_score: (latest - rolling_mean_svi) / rolling_std_dev_svi, or NaN when
            the baseline is degenerate.
        asset_price_momentum_pct: Caller-supplied contemporaneous price momentum,
            used only to split a spike into an up-move and a down-move case.
        is_attention_spike: True when svi_z_score >= z_score_threshold.
        signal_type: BULLISH_ATTENTION_SURGE, BEARISH_PANIC_SPIKE,
            NEUTRAL_ATTENTION or INSUFFICIENT_DATA.
        audit_notes: Human-readable explanation of the emitted signal.
        baseline_periods: Number of observations in the trailing baseline.
        as_of_timestamp: Instant the series was truncated to. None means no
            point-in-time filter was applied - acceptable in live use, where the
            caller already controls arrival, and unsafe in a backtest.
        observation_timestamp: Timestamp of the standardized observation.
        observable_at: Instant that observation became retrievable
            (observation_timestamp + publication_lag_hours).
        dropped_unobservable_points: Observations excluded because they had not yet
            published as of `as_of_timestamp`.
    """

    ticker_or_keyword: str
    latest_svi_score: float
    rolling_mean_svi: float
    rolling_std_dev_svi: float
    svi_z_score: float
    asset_price_momentum_pct: float
    is_attention_spike: bool
    signal_type: str
    audit_notes: str
    baseline_periods: int = 0
    as_of_timestamp: Optional[datetime] = None
    observation_timestamp: Optional[datetime] = None
    observable_at: Optional[datetime] = None
    dropped_unobservable_points: int = 0


class GoogleTrendsSignalEngine:
    """
    Standardizes SVI observations against a strictly trailing baseline and flags
    attention spikes.

    Thresholds are constructor parameters, not constants. `lookback_window=30` and
    `z_score_threshold=2.0` are illustrative defaults with no published empirical
    backing for this construction; re-estimate them out-of-sample on your own
    keyword universe before trading anything.
    """

    def __init__(
        self,
        lookback_window: int = 30,
        z_score_threshold: float = 2.0,
        *,
        min_baseline_std: float = 0.0,
        svi_scale_max: Optional[float] = 100.0,
    ) -> None:
        """
        Args:
            lookback_window: Number of observations in the trailing baseline,
                excluding the observation being standardized. A series therefore
                needs `lookback_window + 1` points.
            z_score_threshold: Z at or above which an observation counts as a spike.
            min_baseline_std: Baselines whose standard deviation is at or below this
                value are treated as degenerate and yield INSUFFICIENT_DATA. The
                default of 0.0 rejects only a perfectly flat baseline. Raise it to
                gate out near-flat baselines; it is a GATE, never a substitute value
                - the reported standard deviation is always the observed one.
            svi_scale_max: Upper bound used to validate SVI inputs, matching the
                0-100 index returned by the Trends UI and pytrends. Set to None for
                consistently-scaled sources such as the official Trends API (alpha),
                which does not scale to 0-100.
        """
        if not isinstance(lookback_window, int) or isinstance(lookback_window, bool):
            raise TypeError("lookback_window must be an int")
        if lookback_window < 2:
            raise ValueError(
                "lookback_window must be >= 2 to admit a sample standard deviation, "
                f"got {lookback_window}"
            )
        if isinstance(z_score_threshold, bool) or not isinstance(z_score_threshold, (int, float)):
            raise TypeError("z_score_threshold must be a number")
        if not math.isfinite(z_score_threshold):
            raise ValueError("z_score_threshold must be finite")
        if isinstance(min_baseline_std, bool) or not isinstance(min_baseline_std, (int, float)):
            raise TypeError("min_baseline_std must be a number")
        if not math.isfinite(min_baseline_std) or min_baseline_std < 0.0:
            raise ValueError("min_baseline_std must be finite and non-negative")
        if svi_scale_max is not None:
            if isinstance(svi_scale_max, bool) or not isinstance(svi_scale_max, (int, float)):
                raise TypeError("svi_scale_max must be a number or None")
            if not math.isfinite(svi_scale_max) or svi_scale_max <= 0.0:
                raise ValueError("svi_scale_max must be finite and positive, or None")

        self.lookback_window = lookback_window
        self.z_score_threshold = float(z_score_threshold)
        self.min_baseline_std = float(min_baseline_std)
        self.svi_scale_max = None if svi_scale_max is None else float(svi_scale_max)

    def _validate_svi_score(self, value: float, context: str) -> float:
        """Rejects non-numeric, non-finite, negative and out-of-scale SVI values."""
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{context}: svi_score must be a number, got {type(value).__name__}")
        score = float(value)
        if not math.isfinite(score):
            raise ValueError(f"{context}: svi_score must be finite, got {value!r}")
        if score < 0.0:
            raise ValueError(f"{context}: svi_score must be non-negative, got {score}")
        if self.svi_scale_max is not None and score > self.svi_scale_max:
            raise ValueError(
                f"{context}: svi_score {score} exceeds svi_scale_max {self.svi_scale_max}. "
                "The Trends UI and pytrends return a 0-100 index; pass svi_scale_max=None "
                "for consistently-scaled sources such as the official Trends API."
            )
        return score

    def calculate_svi_z_score(self, svi_history: Sequence[float]) -> Tuple[float, float, float]:
        """
        Standardizes the final observation against the `lookback_window`
        observations that precede it.

        The baseline EXCLUDES the observation being tested, following Preis et al.
        (2013) and Da et al. (2011). `svi_history` must therefore hold at least
        `lookback_window + 1` values, ordered oldest to newest.

        Returns:
            (z_score, baseline_mean, baseline_std). `z_score` is NaN when the
            baseline standard deviation is at or below `min_baseline_std`; the
            returned standard deviation is always the observed value.

        All three values are rounded to 4 decimal places, and the degeneracy gate
        is applied to the rounded standard deviation, so a returned report can
        never pair a finite Z-score with a reported standard deviation of 0.0.
        Spike classification likewise compares the *returned* Z against the
        threshold, so `is_attention_spike` always agrees with the Z-score written
        into the audit record.

        Raises:
            ValueError: if the history is too short, or holds a non-finite,
                negative or out-of-scale value.
        """
        required = self.lookback_window + 1
        if len(svi_history) < required:
            raise ValueError(
                f"SVI history length ({len(svi_history)}) must be >= lookback_window + 1 "
                f"({required}): the baseline excludes the observation being standardized."
            )

        latest_val = self._validate_svi_score(svi_history[-1], "observation")
        baseline = [
            self._validate_svi_score(v, f"baseline[{i}]")
            for i, v in enumerate(svi_history[-required:-1])
        ]

        mean_val = round(statistics.mean(baseline), 4)
        std_val = round(statistics.stdev(baseline), 4)

        if std_val <= self.min_baseline_std:
            logger.warning(
                "Degenerate SVI baseline: std dev %.4f <= min_baseline_std %.4f over %d periods; "
                "Z-score is undefined.",
                std_val, self.min_baseline_std, len(baseline),
            )
            return float("nan"), mean_val, std_val

        z_score = (latest_val - mean_val) / std_val
        return round(z_score, 4), mean_val, std_val

    def _prepare_series(
        self,
        svi_series: Sequence[SviDataPoint],
        as_of: Optional[datetime],
    ) -> Tuple[List[SviDataPoint], int]:
        """
        Validates timestamps, orders the series oldest to newest, and drops
        observations that had not yet published as of `as_of`.

        Returns the observable series and the number of points dropped.
        """
        stamped: List[Tuple[datetime, SviDataPoint]] = []
        seen = set()
        for idx, point in enumerate(svi_series):
            if not isinstance(point, SviDataPoint):
                raise TypeError(
                    f"svi_series[{idx}] must be an SviDataPoint, got {type(point).__name__}"
                )
            ts = _parse_iso_timestamp(point.timestamp_iso, f"svi_series[{idx}]")
            lag = point.publication_lag_hours
            if isinstance(lag, bool) or not isinstance(lag, (int, float)) or not math.isfinite(lag):
                raise ValueError(f"svi_series[{idx}]: publication_lag_hours must be a finite number")
            if lag < 0:
                raise ValueError(
                    f"svi_series[{idx}]: publication_lag_hours must be non-negative, got {lag}"
                )
            # Compare instants, not wall-clock strings, so the same bucket expressed
            # in two different offsets is still caught as a duplicate.
            key = ts.timestamp()
            if key in seen:
                raise ValueError(
                    f"svi_series[{idx}]: duplicate timestamp {point.timestamp_iso}. Deduplicate "
                    "before scoring; a repeated bucket silently double-weights the baseline."
                )
            seen.add(key)
            stamped.append((ts, point))

        # Sort explicitly rather than trusting caller order: the series is consumed
        # positionally, so an out-of-order feed would otherwise standardize the wrong
        # observation against the wrong baseline.
        stamped.sort(key=lambda pair: pair[0])

        if as_of is None:
            return [p for _, p in stamped], 0

        observable: List[SviDataPoint] = []
        dropped = 0
        for ts, point in stamped:
            if ts + timedelta(hours=float(point.publication_lag_hours)) <= as_of:
                observable.append(point)
            else:
                dropped += 1
        return observable, dropped

    def generate_trends_signal(
        self,
        keyword: str,
        svi_series: Sequence[SviDataPoint],
        asset_price_momentum_pct: float,
        as_of: Optional[datetime] = None,
    ) -> GoogleTrendsSignalReport:
        """
        Emits an attention-spike signal for `keyword`.

        Point-in-time handling: when `as_of` is supplied, every observation whose
        `timestamp + publication_lag_hours` falls after `as_of` is dropped before
        anything is computed, so the signal only ever sees data that had actually
        published. `as_of` is optional for live use, where arrival order is already
        controlled by the caller, but a backtest MUST supply it - otherwise the
        publication lag is merely recorded, not enforced, which is the exact
        look-ahead this module exists to prevent.

        Signal precedence, highest first:
          1. INSUFFICIENT_DATA - fewer than `lookback_window + 1` observable points,
             or a degenerate (flat) baseline.
          2. BULLISH_ATTENTION_SURGE - spike with strictly positive momentum.
          3. BEARISH_PANIC_SPIKE - spike with strictly negative momentum.
          4. NEUTRAL_ATTENTION - no spike, or a spike with exactly zero momentum
             (attention moved but gave no direction; `is_attention_spike` stays True).

        Raises:
            TypeError: on a non-SviDataPoint element, non-numeric momentum, or a
                non-datetime `as_of`.
            ValueError: on a blank keyword, non-finite momentum, an empty series, a
                timezone-naive or duplicate timestamp, or an out-of-range SVI value.
        """
        if not isinstance(keyword, str) or not keyword.strip():
            raise ValueError("keyword must be a non-empty string")
        keyword = keyword.strip()

        if isinstance(asset_price_momentum_pct, bool) or not isinstance(
            asset_price_momentum_pct, (int, float)
        ):
            raise TypeError("asset_price_momentum_pct must be a number")
        momentum = float(asset_price_momentum_pct)
        if not math.isfinite(momentum):
            raise ValueError("asset_price_momentum_pct must be finite")

        if as_of is not None:
            if not isinstance(as_of, datetime):
                raise TypeError("as_of must be a datetime")
            if as_of.tzinfo is None or as_of.tzinfo.utcoffset(as_of) is None:
                raise ValueError("as_of must be timezone-aware")

        if not svi_series:
            raise ValueError("SVI series cannot be empty.")

        observable, dropped = self._prepare_series(svi_series, as_of)

        if as_of is None:
            logger.warning(
                "No as_of supplied for keyword '%s': the publication lag on each point is "
                "recorded but NOT enforced. Supply as_of in any backtest.", keyword,
            )

        required = self.lookback_window + 1
        if len(observable) < required:
            notes = (
                f"GOOGLE TRENDS INSUFFICIENT DATA [{keyword}]: {len(observable)} observable "
                f"point(s) ({dropped} dropped as not-yet-published as of {as_of}) but {required} "
                f"are required (lookback_window={self.lookback_window} plus the observation "
                "itself). No signal."
            )
            logger.warning(notes)
            return GoogleTrendsSignalReport(
                ticker_or_keyword=keyword,
                latest_svi_score=float("nan"),
                rolling_mean_svi=float("nan"),
                rolling_std_dev_svi=float("nan"),
                svi_z_score=float("nan"),
                asset_price_momentum_pct=momentum,
                is_attention_spike=False,
                signal_type=SIGNAL_INSUFFICIENT,
                audit_notes=notes,
                baseline_periods=max(0, len(observable) - 1),
                as_of_timestamp=as_of,
                dropped_unobservable_points=dropped,
            )

        window = observable[-required:]
        latest_point = window[-1]
        observation_ts = _parse_iso_timestamp(latest_point.timestamp_iso, "observation")
        observable_at = observation_ts + timedelta(hours=float(latest_point.publication_lag_hours))

        z_score, mean_val, std_val = self.calculate_svi_z_score([p.svi_score for p in window])
        latest_svi = float(latest_point.svi_score)

        report = GoogleTrendsSignalReport(
            ticker_or_keyword=keyword,
            latest_svi_score=latest_svi,
            rolling_mean_svi=mean_val,
            rolling_std_dev_svi=std_val,
            svi_z_score=z_score,
            asset_price_momentum_pct=momentum,
            is_attention_spike=False,
            signal_type=SIGNAL_INSUFFICIENT,
            audit_notes="",
            baseline_periods=self.lookback_window,
            as_of_timestamp=as_of,
            observation_timestamp=observation_ts,
            observable_at=observable_at,
            dropped_unobservable_points=dropped,
        )

        if math.isnan(z_score):
            report.audit_notes = (
                f"GOOGLE TRENDS INSUFFICIENT DATA [{keyword}]: baseline std dev {std_val} over "
                f"{self.lookback_window} periods is at or below min_baseline_std "
                f"{self.min_baseline_std}; the Z-score is undefined. A flat baseline is typical "
                "of a low-volume keyword that Google Trends reports as 0. No signal."
            )
            logger.warning(report.audit_notes)
            return report

        is_spike = z_score >= self.z_score_threshold
        report.is_attention_spike = is_spike
        stats_fragment = (
            f"(Latest SVI = {latest_svi}, trailing mean = {mean_val}, trailing std = {std_val} "
            f"over {self.lookback_window} periods)"
        )

        if is_spike and momentum > 0:
            report.signal_type = SIGNAL_BULLISH
            report.audit_notes = (
                f"GOOGLE TRENDS BULLISH SURGE [{keyword}]: SVI Z-score = {z_score:+.2f} >= "
                f"{self.z_score_threshold:.2f} {stats_fragment}. Price momentum = "
                f"{momentum:+.2f}%. Attention rose alongside price."
            )
            logger.info(report.audit_notes)
        elif is_spike and momentum < 0:
            report.signal_type = SIGNAL_BEARISH
            report.audit_notes = (
                f"GOOGLE TRENDS BEARISH PANIC [{keyword}]: SVI Z-score = {z_score:+.2f} >= "
                f"{self.z_score_threshold:.2f} {stats_fragment}. Price momentum = "
                f"{momentum:+.2f}%. Attention rose while price fell."
            )
            logger.warning(report.audit_notes)
        elif is_spike:
            report.signal_type = SIGNAL_NEUTRAL
            report.audit_notes = (
                f"GOOGLE TRENDS UNDIRECTED SPIKE [{keyword}]: SVI Z-score = {z_score:+.2f} >= "
                f"{self.z_score_threshold:.2f} {stats_fragment}, but price momentum is exactly "
                "0.00%, so the spike carries no direction. is_attention_spike is True while "
                "signal_type is NEUTRAL_ATTENTION."
            )
            logger.info(report.audit_notes)
        else:
            report.signal_type = SIGNAL_NEUTRAL
            report.audit_notes = (
                f"GOOGLE TRENDS NEUTRAL [{keyword}]: SVI Z-score = {z_score:+.2f} is below the "
                f"{self.z_score_threshold:.2f} spike threshold {stats_fragment}."
            )
            logger.info(report.audit_notes)

        return report

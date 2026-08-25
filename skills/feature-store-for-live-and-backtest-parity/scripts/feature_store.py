"""
feature-store-for-live-and-backtest-parity: single-point-of-truth feature engine with an
offline batch pipeline, an online streaming pipeline, and a parity validator.

Both pipelines call one pure function, ``compute_features_from_window``, over an
identically-constructed lookback window. That is the mechanism: parity is a structural
property of sharing the calculation, not something the validator retrofits afterwards
(Zinkevich, *Rules of Machine Learning*, Rule #32 -- re-use code between the training and
serving pipelines).

Features produced (all computed from closes only):

    return_1d          (c[-1] - c[-2]) / c[-2]
    rsi                Cutler's RSI: 100 - 100 / (1 + avg_gain / avg_loss), where the
                       averages are *simple* means of the last RSI_PERIOD gains/losses.
    bollinger_b        (c[-1] - lower) / (upper - lower), bands = mean +/- 2 sigma
    volatility_zscore  (|r_t| - mean(|r|)) / stdev(|r|) over the window

Conventions that are load-bearing for parity -- change these and a re-implemented batch
pipeline will silently diverge:

- **Cutler's RSI, not Wilder's.** Wilder's RSI (New Concepts in Technical Trading
  Systems, 1978) smooths gains and losses recursively, so its value at bar t depends on
  where the calculation history was started. That path dependence is exactly what a
  window-based online pipeline cannot reproduce from a bounded ring buffer, and it is the
  documented reason Cutler substituted a simple moving average. A recursive RSI can still
  be served live, but only by carrying the smoothed state across the batch/online boundary
  as an explicit checkpoint -- it is not window-derivable.
- **Standard deviation ddof.** ``stddev_ddof`` defaults to 0 (population), matching
  TA-Lib -- TA_VAR computes ``sum_sq/n - mean^2`` -- and pandas_ta's ``bbands`` default.
  ``pandas.DataFrame.rolling().std()`` defaults to ``ddof=1``. A batch pipeline written in
  pandas therefore disagrees with a TA-Lib-shaped live path on every bar unless the ddof
  is matched explicitly. This is the most common concrete cause of the skew this skill
  exists to prevent.
- **No rounding.** Feature values are returned at full float precision. Rounding inside
  the shared core would mask real divergence from the parity check: quantising to 4
  decimals makes any tolerance below roughly 5e-5 unenforceable.
- **Flat-series RSI is neutral (50.0).** With zero gains *and* zero losses, RSI is 0/0.
  Returning 100.0 (maximum overbought) for a flat or non-trading instrument is wrong under
  every convention. Note that TA-Lib returns 0.0 in this case, so a consumer diffing
  against TA-Lib should expect a difference on flat windows, and only on flat windows.

Limitations (documented, deliberate):

- **Replay parity is code parity, not data parity.** ``validate_parity`` streams the
  *same* bars through both pipelines, so it proves the two code paths agree on identical
  input. It cannot detect skew caused by the input itself differing in production: vendor
  bar revisions, late or dropped ticks, a different consolidation rule, or a
  corporate-action adjustment applied to history but not to the live feed. Rules of ML
  #29 is the complement -- log the feature vectors actually served and reconcile them
  against the batch recomputation for the same timestamps.
- **Features include the current bar's close.** The ``FeatureVector`` at timestamp t uses
  bar t's close. It is therefore only actionable at or after bar t's close; using it to
  trade bar t's open is look-ahead bias introduced by the caller, not by this engine.
- **Warm-up rows are not features.** Below ``MIN_BARS_FOR_FEATURES`` bars the engine emits
  a documented neutral placeholder, and any window shorter than ``lookback_period`` yields
  a *different* estimator (shorter RSI and Bollinger periods). Both cases are flagged
  ``is_warm=False`` and must be dropped or masked before training and before inference.
- **Absolute parity tolerance.** ``tolerance`` is compared against absolute differences.
  RSI spans 0-100 while ``return_1d`` is of order 1e-2, so a single absolute threshold is
  far stricter in relative terms for RSI than for returns. Set it from the least-scaled
  feature.
- **Closes only.** ``high``, ``low``, ``open`` and ``volume`` are carried on ``Bar`` and
  validated, but no feature currently consumes them.
"""
from collections import deque
from dataclasses import dataclass
import logging
import math
import statistics
from typing import Deque, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Lookback for the RSI gain/loss averages (Wilder's canonical 14).
RSI_PERIOD = 14

#: Lookback for the Bollinger moving average and standard deviation.
BOLLINGER_PERIOD = 20

#: Band width in standard deviations (Bollinger's canonical 2).
BOLLINGER_NUM_STDDEV = 2.0

#: Population standard deviation (divisor n), matching TA-Lib and pandas_ta `bbands`.
DEFAULT_STDDEV_DDOF = 0

#: RSI emitted when a window has neither gains nor losses (0/0 is undefined).
NEUTRAL_RSI = 50.0

#: %B emitted when the band has zero width (flat window): price sits on the middle band.
NEUTRAL_BOLLINGER_B = 0.5

#: Shortest window that produces a defined value for every feature.
MIN_BARS_FOR_FEATURES = 5

#: Default absolute parity tolerance. With the shared core below the two pipelines are
#: bit-identical (observed difference exactly 0.0); the headroom exists for callers who
#: substitute a vectorised batch implementation whose float accumulation order differs.
DEFAULT_PARITY_TOLERANCE = 1e-6

#: Feature fields compared by `validate_parity`.
COMPARED_FEATURES = ("rsi", "bollinger_b", "volatility_zscore", "return_1d")


class FeatureParityMismatchError(ValueError):
    """Raised when batch backtest and online live streaming feature outputs diverge."""


class BarSequenceError(ValueError):
    """Raised on a malformed bar, or a bar duplicating / predating the last one ingested."""


@dataclass(frozen=True)
class Bar:
    """
    One OHLCV bar. Frozen: a bar already inside the ring buffer must not be mutated, or the
    online window stops matching the batch window built from the same data.
    """

    timestamp: float
    open: float
    high: float
    low: float
    close: float
    volume: float

    def validate(self) -> None:
        """
        Rejects bars that would corrupt features rather than fail.

        A non-finite or non-positive close previously either raised ZeroDivisionError deep
        inside the return calculation or reached `statistics.stdev` as a NaN, which raises
        an unrelated AttributeError. Neither is actionable in a live feed handler.
        """
        for name in ("timestamp", "open", "high", "low", "close", "volume"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise BarSequenceError(f"Bar.{name} must be numeric, got {value!r}.")
            if not math.isfinite(float(value)):
                raise BarSequenceError(f"Bar.{name} must be finite, got {value!r}.")
        for name in ("open", "high", "low", "close"):
            if getattr(self, name) <= 0.0:
                raise BarSequenceError(
                    f"Bar.{name} must be positive (returns are computed as ratios), got "
                    f"{getattr(self, name)!r} at ts {self.timestamp}."
                )
        if self.volume < 0.0:
            raise BarSequenceError(f"Bar.volume must be non-negative, got {self.volume!r}.")
        if self.low > self.high:
            raise BarSequenceError(
                f"Bar.low ({self.low}) exceeds Bar.high ({self.high}) at ts {self.timestamp}."
            )


@dataclass(frozen=True)
class FeatureVector:
    """
    One row of the feature matrix.

    ``is_warm`` is False while the window is shorter than the engine's ``lookback_period``.
    Those rows come from a shorter estimator (or, below ``MIN_BARS_FOR_FEATURES``, are a
    fixed neutral placeholder) and are not comparable to warm rows. Drop or mask them
    before training and before inference.
    """

    timestamp: float
    rsi: float
    bollinger_b: float
    volatility_zscore: float
    return_1d: float
    is_warm: bool = True
    bars_in_window: int = 0

    def to_dict(self) -> Dict[str, float]:
        return {
            "timestamp": self.timestamp,
            "rsi": self.rsi,
            "bollinger_b": self.bollinger_b,
            "volatility_zscore": self.volatility_zscore,
            "return_1d": self.return_1d,
        }


def _stddev(values: Sequence[float], ddof: int) -> float:
    """Standard deviation with an explicit ddof; 0.0 when the sample is too small."""
    if len(values) - ddof < 1:
        return 0.0
    return statistics.pstdev(values) if ddof == 0 else statistics.stdev(values)


def _validate_bar_sequence(bars: Sequence[Bar]) -> None:
    """Rejects empty, malformed, duplicated, or non-chronological bar series."""
    if not bars:
        raise BarSequenceError("Bar series is empty; no features can be computed.")
    previous: Optional[float] = None
    for i, bar in enumerate(bars):
        if not isinstance(bar, Bar):
            raise BarSequenceError(f"bars[{i}] is {type(bar).__name__}, expected Bar.")
        bar.validate()
        if previous is not None and bar.timestamp <= previous:
            raise BarSequenceError(
                f"bars[{i}] timestamp {bar.timestamp} is not strictly after bars[{i - 1}] "
                f"({previous}). Batch windows assume chronological, de-duplicated bars; an "
                "unsorted series yields a feature matrix no live stream can reproduce."
            )
        previous = bar.timestamp


class ParityFeatureStoreEngine:
    """
    Single-point-of-truth feature engine shared by the offline batch backtest and the
    online streaming runtime.

    The engine holds mutable online state (the ring buffer and the last-seen timestamp).
    ``compute_batch_features`` and ``validate_parity`` neither read nor write that state,
    so the parity self-check is safe to run against a live engine.
    """

    def __init__(
        self,
        lookback_period: int = 20,
        stddev_ddof: int = DEFAULT_STDDEV_DDOF,
    ) -> None:
        if isinstance(lookback_period, bool) or not isinstance(lookback_period, int):
            raise ValueError(f"lookback_period must be an int, got {lookback_period!r}.")
        min_lookback = max(BOLLINGER_PERIOD, RSI_PERIOD + 1, MIN_BARS_FOR_FEATURES)
        if lookback_period < min_lookback:
            # Below this the window silently truncates the RSI and Bollinger periods
            # (`min(RSI_PERIOD, n - 1)`), so the engine would emit something labelled
            # "RSI(14)" that is not RSI(14). Fail at construction instead.
            raise ValueError(
                f"lookback_period must be >= {min_lookback} to compute RSI({RSI_PERIOD}) and "
                f"Bollinger({BOLLINGER_PERIOD}) at full period, got {lookback_period}."
            )
        if stddev_ddof not in (0, 1):
            raise ValueError(
                f"stddev_ddof must be 0 (population, TA-Lib) or 1 (sample, pandas default), "
                f"got {stddev_ddof!r}."
            )

        self.lookback_period = lookback_period
        self.stddev_ddof = stddev_ddof
        self.online_ring_buffer: Deque[Bar] = deque(maxlen=lookback_period)
        self._last_online_timestamp: Optional[float] = None

    # ------------------------------------------------------------------ shared core

    @staticmethod
    def compute_features_from_window(
        closes: Sequence[float],
        stddev_ddof: int = DEFAULT_STDDEV_DDOF,
    ) -> Tuple[float, float, float, float]:
        """
        Pure feature calculation over one lookback window of closing prices.

        This is the single point of truth: the batch and online pipelines differ only in
        how they assemble ``closes``, never in what is computed from it. Returns
        ``(rsi, bollinger_b, volatility_zscore, return_1d)`` at full float precision.

        Windows shorter than ``MIN_BARS_FOR_FEATURES`` return a fixed neutral placeholder;
        callers must use ``FeatureVector.is_warm`` to tell it apart from a real reading.
        """
        closes = list(closes)  # A deque window is not sliceable; normalise once.
        n = len(closes)
        if n < MIN_BARS_FOR_FEATURES:
            return NEUTRAL_RSI, NEUTRAL_BOLLINGER_B, 0.0, 0.0

        for i, close in enumerate(closes):
            if not math.isfinite(close) or close <= 0.0:
                raise ValueError(
                    f"closes[{i}] must be finite and positive, got {close!r}. A NaN or "
                    "non-positive close otherwise propagates into every feature."
                )

        # 1. One-bar simple return.
        ret_1d = (closes[-1] - closes[-2]) / closes[-2]

        # 2. Cutler's RSI -- simple means of gains and losses (see module docstring).
        period = min(RSI_PERIOD, n - 1)
        avg_gain = sum(max(closes[i] - closes[i - 1], 0.0) for i in range(n - period, n)) / period
        avg_loss = sum(max(closes[i - 1] - closes[i], 0.0) for i in range(n - period, n)) / period
        if avg_gain == 0.0 and avg_loss == 0.0:
            rsi = NEUTRAL_RSI          # 0/0: flat window. Undefined momentum, not overbought.
        elif avg_loss == 0.0:
            rsi = 100.0                # Only up-moves in the window.
        else:
            rsi = 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

        # 3. Bollinger Band %B.
        recent_closes = list(closes[-min(BOLLINGER_PERIOD, n):])
        std_c = _stddev(recent_closes, stddev_ddof)
        if std_c == 0.0:
            # Zero-width band: %B is 0/0. The price is on the middle band by construction.
            bollinger_b = NEUTRAL_BOLLINGER_B
        else:
            mean_c = statistics.fmean(recent_closes)
            lower_band = mean_c - BOLLINGER_NUM_STDDEV * std_c
            band_width = 2.0 * BOLLINGER_NUM_STDDEV * std_c
            bollinger_b = (closes[-1] - lower_band) / band_width

        # 4. Volatility z-score: how surprising this bar's *magnitude* is against the
        #    window's typical magnitude. Both terms must be absolute -- comparing |r_t|
        #    against the mean of *signed* returns is not a z-score of any quantity.
        abs_returns = [abs((closes[i] - closes[i - 1]) / closes[i - 1]) for i in range(1, n)]
        std_abs = _stddev(abs_returns, stddev_ddof)
        if std_abs == 0.0:
            vol_zscore = 0.0
        else:
            vol_zscore = (abs(ret_1d) - statistics.fmean(abs_returns)) / std_abs

        return rsi, bollinger_b, vol_zscore, ret_1d

    def _features_for_window(self, window: Sequence[Bar]) -> FeatureVector:
        """Builds one FeatureVector from a window. Used by both pipelines -- do not inline."""
        closes = [b.close for b in window]
        rsi, bb_b, vol_z, ret_1d = self.compute_features_from_window(closes, self.stddev_ddof)
        return FeatureVector(
            timestamp=window[-1].timestamp,
            rsi=rsi,
            bollinger_b=bb_b,
            volatility_zscore=vol_z,
            return_1d=ret_1d,
            is_warm=len(window) >= self.lookback_period,
            bars_in_window=len(window),
        )

    # ------------------------------------------------------------------ batch pipeline

    def compute_batch_features(self, bars: Sequence[Bar]) -> List[FeatureVector]:
        """
        Offline batch pipeline. Emits one FeatureVector per input bar.

        The window at index ``i`` is ``bars[max(0, i - lookback + 1) : i + 1]`` -- closed on
        the right at the bar being labelled, so no future bar can enter it.
        """
        _validate_bar_sequence(bars)
        feature_matrix = [
            self._features_for_window(bars[max(0, idx - self.lookback_period + 1): idx + 1])
            for idx in range(len(bars))
        ]
        warm = sum(1 for fv in feature_matrix if fv.is_warm)
        logger.info(
            "Batch features computed for %d bars (%d warm, %d warm-up rows to mask).",
            len(feature_matrix), warm, len(feature_matrix) - warm,
        )
        return feature_matrix

    # ------------------------------------------------------------------ online pipeline

    @property
    def is_warm(self) -> bool:
        """True once the ring buffer holds a full lookback window."""
        return len(self.online_ring_buffer) >= self.lookback_period

    def reset(self) -> None:
        """Clears online state. Required before replaying a different bar series."""
        self.online_ring_buffer.clear()
        self._last_online_timestamp = None

    def warm_up(self, historical_bars: Sequence[Bar]) -> int:
        """
        Seeds the ring buffer from history before going live; returns the buffer depth.

        Starting a live session cold means the first ``lookback_period`` inferences run on
        short-window estimators the model never saw in training. Feed the same bars the
        batch pipeline would have held immediately before the live session's first bar.
        """
        for bar in historical_bars:
            self.compute_online_feature(bar)
        if not self.is_warm:
            logger.warning(
                "Warm-up incomplete: %d/%d bars buffered; features stay is_warm=False.",
                len(self.online_ring_buffer), self.lookback_period,
            )
        return len(self.online_ring_buffer)

    def compute_online_feature(self, incoming_bar: Bar) -> FeatureVector:
        """
        Online streaming pipeline. Appends one bar to the ring buffer and returns its
        FeatureVector.

        Duplicate and out-of-order bars are rejected. A websocket replaying the last bar
        after a reconnect would otherwise be appended as a distinct observation: it yields
        ``return_1d = 0.0`` (fabricated), and because the buffer is bounded it evicts a real
        bar, permanently shortening the effective history behind every later feature.
        """
        incoming_bar.validate()
        last_ts = self._last_online_timestamp
        if last_ts is not None and incoming_bar.timestamp <= last_ts:
            raise BarSequenceError(
                f"Bar timestamp {incoming_bar.timestamp} is not newer than the last ingested "
                f"bar ({last_ts}). Duplicate or out-of-order bars corrupt the ring buffer; "
                "de-duplicate upstream, or call reset() to replay a different series."
            )

        self.online_ring_buffer.append(incoming_bar)
        self._last_online_timestamp = incoming_bar.timestamp
        return self._features_for_window(list(self.online_ring_buffer))

    # ------------------------------------------------------------------ parity validator

    def validate_parity(
        self,
        bars: Sequence[Bar],
        tolerance: float = DEFAULT_PARITY_TOLERANCE,
    ) -> bool:
        """
        Replays ``bars`` through both pipelines and asserts the feature matrices agree
        within ``tolerance``. Raises FeatureParityMismatchError on the first divergence.

        The online replay runs on a throwaway engine constructed as
        ``type(self)(lookback_period, stddev_ddof)``, so calling this on a live engine
        cannot disturb its ring buffer; a subclass must keep that constructor signature
        callable. It proves *code* parity only -- see the module docstring on why replay
        cannot detect input-side skew.
        """
        if not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0.0:
            raise ValueError(f"tolerance must be finite and non-negative, got {tolerance!r}.")
        _validate_bar_sequence(bars)

        batch_features = self.compute_batch_features(bars)

        replay = type(self)(self.lookback_period, self.stddev_ddof)
        online_features = [replay.compute_online_feature(b) for b in bars]

        for i, (b_fv, o_fv) in enumerate(zip(batch_features, online_features)):
            if b_fv.is_warm != o_fv.is_warm:
                raise FeatureParityMismatchError(
                    f"Warm-up state diverged at index {i} (ts {bars[i].timestamp}): batch "
                    f"is_warm={b_fv.is_warm}, online is_warm={o_fv.is_warm}."
                )
            for feature in COMPARED_FEATURES:
                diff = abs(getattr(b_fv, feature) - getattr(o_fv, feature))
                if diff > tolerance:
                    msg = (
                        f"FEATURE PARITY MISMATCH on '{feature}' at index {i} "
                        f"(ts {bars[i].timestamp}): batch={getattr(b_fv, feature)!r} vs "
                        f"online={getattr(o_fv, feature)!r}, |diff|={diff!r} > {tolerance!r}. "
                        f"Batch row: {b_fv.to_dict()}; online row: {o_fv.to_dict()}."
                    )
                    logger.critical(msg)
                    raise FeatureParityMismatchError(msg)

        logger.info("Feature parity verified for %d bars (tolerance %r).", len(bars), tolerance)
        return True

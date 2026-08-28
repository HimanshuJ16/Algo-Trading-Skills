"""
regime-detection-for-strategy-switching: live market-regime classifier, Wilder
ADX/DMI and ATR engine, hysteresis transition filter, and strategy-variant router.

Purpose
-------
Answer one question on every closed bar: *which strategy variant should be armed
right now, and has that answer actually changed?* Running a trend-following model
through a range, or a mean-reversion model into a volatility shock, is a common
route to an unplanned drawdown. This engine buckets the current market into one
of four regimes and refuses to act on the first bar that disagrees with the
regime already in force.

Indicators (Wilder, 1978)
-------------------------
``_compute_adx`` implements the Average Directional Index as published in
J. Welles Wilder Jr., *New Concepts in Technical Trading Systems* (1978), in the
form documented by StockCharts ChartSchool:

    TR_i    = max(high_i - low_i, |high_i - close_{i-1}|, |low_i - close_{i-1}|)
    +DM_i   = high_i - high_{i-1}   if that exceeds (low_{i-1} - low_i) and > 0, else 0
    -DM_i   = low_{i-1} - low_i     if that exceeds (high_i - high_{i-1}) and > 0, else 0

    Wilder smoothing (accumulation form), period p:
        S_p     = sum of the first p values
        S_t     = S_{t-1} - S_{t-1}/p + x_t

    +DI = 100 * smoothed(+DM) / smoothed(TR)
    -DI = 100 * smoothed(-DM) / smoothed(TR)
    DX  = 100 * |+DI - -DI| / (+DI + -DI)
    ADX = p-period mean of the first p DX values, then ((prior_ADX * (p-1)) + DX_t) / p

**ADX is the smoothed average of DX, not DX itself.** DX is a single-bar reading
that reaches 100 whenever one side of the DI pair is zero, which happens on any
quiet one-directional drift. Comparing DX against Wilder's ADX >= 25 threshold
therefore classifies large numbers of non-trending bars as trends. A prior
revision of this module returned DX under the name ``adx``; that is fixed here
and is the reason for the 2.0.0 major version.

Bar 0 carries no prior close and no prior high/low, so TR/+DM/-DM are undefined
there and the series start at bar 1. One ADX value therefore needs ``2 * period``
bars (p true-range values to seed the Wilder smoothing, then p DX values to seed
the ADX average); ``min_bars_required`` enforces that and nothing is fabricated
below it. Wilder's smoothing has a long memory, so the first ADX values still
carry a visible seed dependence -- roughly 150 bars are needed before ADX is
stable enough to compare across instruments or against a fixed threshold.

Volatility z-score
------------------
The volatility leg scores the latest ATR against the ATR readings that preceded
it in the supplied history:

    z = (ATR_latest - mean(ATR_history)) / stdev(ATR_history)

The latest observation is **excluded** from the mean and the standard deviation.
Including it bounds the achievable z at (n-1)/sqrt(n) -- with the 15 ATR readings
available at the 28-bar minimum that ceiling is ~3.6 and the score stops rising
with the size of the shock, which is the opposite of what a shock detector needs.

Two properties of this leg must be understood before the threshold is trusted:

* ATR is Wilder-smoothed, so a single violent bar moves it by only ~1/p of the
  excess range. The z-score reacts to a volatility *regime*, not to one bar.
* The score is direction-agnostic. ``HIGH_VOLATILITY_CRASH`` fires on a violent
  rally as readily as on a sell-off; it means "volatility regime break", and the
  routed variant is the risk-off one either way.

Look-ahead
----------
Every reading is computed from the bars supplied, with the latest bar treated as
**closed**. Feeding the currently-forming bar leaks its unfinished high, low and
close into the regime that routes the same bar's orders. Pass completed bars only.

Thresholds
----------
The ADX 25 / 20 defaults are Wilder's published interpretation levels; the
hysteresis and z-score defaults are this engine's own choices, not values
mandated by any regulator, exchange or published study. See
``references/standards.md``.

This engine is a *router*, not a risk control. The hysteresis filter that
suppresses whipsaws also delays the risk-off regime by ``hysteresis_bars``.
Capital protection belongs in an independent circuit breaker -- see
``kill-switch-and-drawdown-circuit-breakers``.
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
import numbers
import statistics
from typing import Dict, Hashable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Reported in place of an infinite z-score when the ATR history has zero
#: dispersion (synthetic or perfectly flat data). Finite so downstream reports
#: and serialisation stay well-defined; far above any usable threshold.
MAX_VOLATILITY_ZSCORE: float = 1000.0

#: Prior ATR observations required before a volatility z-score is meaningful.
MIN_VOLATILITY_HISTORY: int = 10


class RegimeDetectorError(ValueError):
    """Raised when bar data or configuration is invalid, or history is too short."""
    pass


class MarketRegime(str, Enum):
    BULL_TRENDING = "BULL_TRENDING"
    BEAR_TRENDING = "BEAR_TRENDING"
    MEAN_REVERTING_RANGING = "MEAN_REVERTING_RANGING"
    HIGH_VOLATILITY_CRASH = "HIGH_VOLATILITY_CRASH"


TREND_REGIMES = (MarketRegime.BULL_TRENDING, MarketRegime.BEAR_TRENDING)

#: Default regime -> strategy-variant labels. These are *labels* the caller maps
#: to its own modules; this engine never imports or instantiates a strategy.
DEFAULT_STRATEGY_VARIANTS: Dict[MarketRegime, str] = {
    MarketRegime.BULL_TRENDING: "TrendFollowingLongStrategy",
    MarketRegime.BEAR_TRENDING: "TrendFollowingShortStrategy",
    MarketRegime.MEAN_REVERTING_RANGING: "MeanReversionBollingerStrategy",
    MarketRegime.HIGH_VOLATILITY_CRASH: "RiskOffHaltStrategy",
}


@dataclass
class RegimeAnalysis:
    """One bar's regime reading.

    ``confirmed_regime`` is the only field that should drive order routing;
    ``raw_candidate_regime`` is this bar's unfiltered classification and will
    lead the confirmed regime by up to ``hysteresis_bars`` bars.
    """

    confirmed_regime: MarketRegime
    raw_candidate_regime: MarketRegime
    adx_value: float
    volatility_zscore: float
    consecutive_candidate_bars: int
    active_strategy_variant: str
    plus_di: float = 0.0
    minus_di: float = 0.0
    atr_value: float = 0.0
    regime_changed: bool = False


class MarketRegimeDetector:
    """
    Classifies the current market regime (bull trend, bear trend, range,
    high-volatility break) from closed OHLC bars and applies a consecutive-bar
    hysteresis filter before the confirmed regime is allowed to change.

    The instance is **stateful**: it holds the confirmed regime and the pending
    candidate count across calls, and expects exactly one ``detect_regime`` call
    per newly closed bar. Pass ``bar_key`` to make redundant calls for the same
    bar idempotent; see ``detect_regime``.
    """

    def __init__(
        self,
        adx_trend_threshold: float = 25.0,
        adx_ranging_threshold: float = 20.0,
        volatility_z_threshold: float = 2.0,
        hysteresis_bars: int = 3,
        indicator_period: int = 14,
        strategy_variants: Optional[Mapping[MarketRegime, str]] = None,
        initial_regime: MarketRegime = MarketRegime.MEAN_REVERTING_RANGING,
    ) -> None:
        """
        Args:
            adx_trend_threshold: ADX at or above which a directional regime may
                be *entered*. Wilder's published strong-trend level is 25.
            adx_ranging_threshold: ADX below which an in-force trend regime is
                *exited*. Between the two levels (Wilder's 20-25 grey zone) an
                already-confirmed trend is held provided the DI pair still
                agrees with its direction. Must not exceed the trend threshold.
            volatility_z_threshold: ATR z-score at or above which the regime is
                HIGH_VOLATILITY_CRASH, evaluated before the trend legs.
            hysteresis_bars: Consecutive bars a new candidate regime must hold
                before the confirmed regime switches. Must be >= 1; 1 disables
                the filter.
            indicator_period: Wilder period for ATR, DI and ADX (Wilder's 14).
            strategy_variants: Optional override of the regime -> label map.
                Must cover every ``MarketRegime`` member.
            initial_regime: Regime assumed in force before the first bar. The
                default assumes the flattest of the four; a caller restarting
                mid-session should pass the regime that was last confirmed,
                otherwise the filter re-confirms it from scratch.

        Raises:
            RegimeDetectorError: on any invalid configuration value.
        """
        self.adx_trend_threshold = _validate_finite_number(
            adx_trend_threshold, "adx_trend_threshold", minimum=0.0, maximum=100.0
        )
        self.adx_ranging_threshold = _validate_finite_number(
            adx_ranging_threshold, "adx_ranging_threshold", minimum=0.0, maximum=100.0
        )
        if self.adx_ranging_threshold > self.adx_trend_threshold:
            raise RegimeDetectorError(
                f"adx_ranging_threshold ({self.adx_ranging_threshold}) must not exceed "
                f"adx_trend_threshold ({self.adx_trend_threshold}); the trend-exit level "
                f"cannot sit above the trend-entry level."
            )
        self.volatility_z_threshold = _validate_finite_number(
            volatility_z_threshold, "volatility_z_threshold"
        )

        if not isinstance(hysteresis_bars, int) or isinstance(hysteresis_bars, bool):
            raise RegimeDetectorError("hysteresis_bars must be an int.")
        if hysteresis_bars < 1:
            raise RegimeDetectorError(
                f"hysteresis_bars must be >= 1 (got {hysteresis_bars}); a value below 1 "
                f"would switch the confirmed regime on every disagreeing bar and remove "
                f"the whipsaw protection this engine exists to provide."
            )
        self.hysteresis_bars = hysteresis_bars

        if not isinstance(indicator_period, int) or isinstance(indicator_period, bool):
            raise RegimeDetectorError("indicator_period must be an int.")
        if indicator_period < 2:
            raise RegimeDetectorError(f"indicator_period must be >= 2 (got {indicator_period}).")
        self.indicator_period = indicator_period

        if strategy_variants is None:
            self.strategy_variants: Dict[MarketRegime, str] = dict(DEFAULT_STRATEGY_VARIANTS)
        else:
            missing = [r.value for r in MarketRegime if r not in strategy_variants]
            if missing:
                raise RegimeDetectorError(
                    f"strategy_variants is missing a label for: {', '.join(sorted(missing))}."
                )
            self.strategy_variants = {r: str(strategy_variants[r]) for r in MarketRegime}

        if not isinstance(initial_regime, MarketRegime):
            raise RegimeDetectorError("initial_regime must be a MarketRegime member.")

        self.confirmed_regime: MarketRegime = initial_regime
        self.candidate_regime: Optional[MarketRegime] = None
        self.candidate_count: int = 0
        self._last_bar_key: Optional[Hashable] = None
        self._last_analysis: Optional[RegimeAnalysis] = None

    # ------------------------------------------------------------------ #
    # Minimum history
    # ------------------------------------------------------------------ #

    @property
    def min_bars_required(self) -> int:
        """Bars needed before both indicator legs produce a real value.

        ``2 * period`` for the first ADX (bar 0 seeds nothing, ``period`` TR
        values seed the Wilder smoothing, ``period`` DX values seed the ADX
        average), and ``period + MIN_VOLATILITY_HISTORY + 1`` for a z-score with
        enough prior ATR observations to be meaningful.
        """
        return max(2 * self.indicator_period, self.indicator_period + MIN_VOLATILITY_HISTORY + 1)

    # ------------------------------------------------------------------ #
    # Indicators
    # ------------------------------------------------------------------ #

    @staticmethod
    def _wilder_smooth(values: Sequence[float], period: int) -> List[float]:
        """Wilder's accumulation smoothing.

        First output is the sum of the first ``period`` inputs; each subsequent
        output is ``prior - prior / period + current``. Returns
        ``len(values) - period + 1`` values, or ``[]`` if the input is shorter
        than ``period``. These are accumulated sums, not averages -- divide by
        ``period`` for ATR. A ratio of two such sums (as in +DI and -DI) is
        unaffected by the missing division.
        """
        if len(values) < period:
            return []
        smoothed: List[float] = [math.fsum(values[:period])]
        for value in values[period:]:
            prior = smoothed[-1]
            smoothed.append(prior - prior / period + value)
        return smoothed

    @staticmethod
    def _directional_series(
        highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
    ) -> Tuple[List[float], List[float], List[float]]:
        """True Range, +DM and -DM for bars 1..n-1 (bar 0 has no prior bar)."""
        tr: List[float] = []
        plus_dm: List[float] = []
        minus_dm: List[float] = []
        for i in range(1, len(closes)):
            tr.append(
                max(
                    highs[i] - lows[i],
                    abs(highs[i] - closes[i - 1]),
                    abs(lows[i] - closes[i - 1]),
                )
            )
            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]
            plus_dm.append(up_move if (up_move > down_move and up_move > 0.0) else 0.0)
            minus_dm.append(down_move if (down_move > up_move and down_move > 0.0) else 0.0)
        return tr, plus_dm, minus_dm

    def _compute_atr_series(
        self, highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
    ) -> List[float]:
        """Wilder ATR series, one value per bar from index ``period`` onward."""
        period = self.indicator_period
        tr, _, _ = self._directional_series(highs, lows, closes)
        return [s / period for s in self._wilder_smooth(tr, period)]

    def _compute_adx(
        self, highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]
    ) -> Tuple[float, float, float]:
        """Latest Wilder ADX, +DI and -DI.

        Raises:
            RegimeDetectorError: if fewer than ``2 * period`` bars are supplied.
                No neutral placeholder is returned -- a fabricated ADX is
                indistinguishable from a measured one downstream.
        """
        period = self.indicator_period
        if len(closes) < 2 * period:
            raise RegimeDetectorError(
                f"ADX needs at least {2 * period} bars ({len(closes)} supplied): "
                f"{period} true-range values seed the Wilder smoothing and a further "
                f"{period} DX values seed the ADX average."
            )

        tr, plus_dm, minus_dm = self._directional_series(highs, lows, closes)
        smooth_tr = self._wilder_smooth(tr, period)
        smooth_plus = self._wilder_smooth(plus_dm, period)
        smooth_minus = self._wilder_smooth(minus_dm, period)

        dx_series: List[float] = []
        plus_di = minus_di = 0.0
        for total_tr, total_plus, total_minus in zip(smooth_tr, smooth_plus, smooth_minus):
            if total_tr <= 0.0:
                # A flat window: no range, therefore no directional information.
                plus_di = minus_di = 0.0
            else:
                plus_di = 100.0 * total_plus / total_tr
                minus_di = 100.0 * total_minus / total_tr
            di_sum = plus_di + minus_di
            dx_series.append(0.0 if di_sum <= 0.0 else 100.0 * abs(plus_di - minus_di) / di_sum)

        adx = math.fsum(dx_series[:period]) / period
        for dx in dx_series[period:]:
            adx = ((adx * (period - 1)) + dx) / period
        return adx, plus_di, minus_di

    def _volatility_zscore(self, atr_series: Sequence[float]) -> float:
        """Z-score of the latest ATR against the ATR readings that preceded it."""
        history = atr_series[:-1]
        latest = atr_series[-1]
        if len(history) < MIN_VOLATILITY_HISTORY:
            raise RegimeDetectorError(
                f"Volatility z-score needs at least {MIN_VOLATILITY_HISTORY} prior ATR "
                f"observations ({len(history)} available)."
            )

        mean = statistics.fmean(history)
        stdev = statistics.stdev(history)
        if stdev <= 0.0:
            # Degenerate history (synthetic or perfectly flat data): the score is
            # undefined rather than zero. Report a capped extreme in the
            # direction of the move so the threshold comparison stays honest.
            if latest == mean:
                return 0.0
            return math.copysign(MAX_VOLATILITY_ZSCORE, latest - mean)
        return max(-MAX_VOLATILITY_ZSCORE, min(MAX_VOLATILITY_ZSCORE, (latest - mean) / stdev))

    # ------------------------------------------------------------------ #
    # Classification
    # ------------------------------------------------------------------ #

    @staticmethod
    def _direction_agrees(regime: MarketRegime, plus_di: float, minus_di: float) -> bool:
        if regime is MarketRegime.BULL_TRENDING:
            return plus_di > minus_di
        if regime is MarketRegime.BEAR_TRENDING:
            return minus_di > plus_di
        return False

    def _classify_raw(
        self, adx: float, plus_di: float, minus_di: float, vol_zscore: float
    ) -> MarketRegime:
        """Unfiltered regime for this bar, before the hysteresis filter.

        The volatility leg is evaluated first: a volatility-regime break
        outranks whatever the trend leg reports, because the trend variants are
        the ones that size worst into a shock.
        """
        if vol_zscore >= self.volatility_z_threshold:
            return MarketRegime.HIGH_VOLATILITY_CRASH
        if adx >= self.adx_trend_threshold:
            if plus_di > minus_di:
                return MarketRegime.BULL_TRENDING
            if minus_di > plus_di:
                return MarketRegime.BEAR_TRENDING
            # Strength without a direction is not a tradeable trend.
            return MarketRegime.MEAN_REVERTING_RANGING
        if (
            self.confirmed_regime in TREND_REGIMES
            and adx >= self.adx_ranging_threshold
            and self._direction_agrees(self.confirmed_regime, plus_di, minus_di)
        ):
            # Wilder's 20-25 grey zone: the trend is weakening but not dead, and
            # the DI pair still points the way the confirmed regime does.
            return self.confirmed_regime
        return MarketRegime.MEAN_REVERTING_RANGING

    def detect_regime(
        self,
        highs: Sequence[float],
        lows: Sequence[float],
        closes: Sequence[float],
        bar_key: Optional[Hashable] = None,
    ) -> RegimeAnalysis:
        """Classify the latest closed bar and advance the hysteresis filter.

        Args:
            highs, lows, closes: Equal-length OHLC series of **closed** bars,
                oldest first, at least ``min_bars_required`` long. Passing the
                currently-forming bar leaks its unfinished values into the
                regime that routes its own orders.
            bar_key: Identity of the latest bar (its close timestamp, or a
                monotonically increasing bar index). When supplied and equal to
                the previous call's key, the cached analysis is returned and the
                hysteresis counter is **not** advanced. Without it, a retry, a
                duplicated tick, or a second call on the same history counts as
                another confirming bar and can switch the regime early. The key
                must change whenever the latest bar changes -- a constant key
                freezes the detector on its first reading.

        Raises:
            RegimeDetectorError: on mismatched, too-short, non-finite or
                inverted (high < low) bar data.
        """
        _validate_bars(highs, lows, closes, self.min_bars_required)

        if bar_key is not None and self._last_analysis is not None and bar_key == self._last_bar_key:
            logger.debug("Repeat call for bar_key %r; returning cached analysis.", bar_key)
            return self._last_analysis

        atr_series = self._compute_atr_series(highs, lows, closes)
        vol_zscore = self._volatility_zscore(atr_series)
        adx, plus_di, minus_di = self._compute_adx(highs, lows, closes)

        raw_regime = self._classify_raw(adx, plus_di, minus_di, vol_zscore)

        previous_regime = self.confirmed_regime
        if raw_regime == self.confirmed_regime:
            self.candidate_regime = None
            self.candidate_count = 0
        else:
            if raw_regime == self.candidate_regime:
                self.candidate_count += 1
            else:
                self.candidate_regime = raw_regime
                self.candidate_count = 1

            if self.candidate_count >= self.hysteresis_bars:
                logger.info(
                    "REGIME SHIFT CONFIRMED: %s -> %s after %d consecutive bars "
                    "(ADX=%.2f, +DI=%.2f, -DI=%.2f, vol_z=%.2f).",
                    self.confirmed_regime.value,
                    raw_regime.value,
                    self.candidate_count,
                    adx,
                    plus_di,
                    minus_di,
                    vol_zscore,
                )
                self.confirmed_regime = raw_regime
                self.candidate_regime = None
                self.candidate_count = 0

        analysis = RegimeAnalysis(
            confirmed_regime=self.confirmed_regime,
            raw_candidate_regime=raw_regime,
            adx_value=adx,
            volatility_zscore=vol_zscore,
            consecutive_candidate_bars=self.candidate_count,
            active_strategy_variant=self.route_strategy_variant(self.confirmed_regime),
            plus_di=plus_di,
            minus_di=minus_di,
            atr_value=atr_series[-1],
            regime_changed=self.confirmed_regime != previous_regime,
        )
        self._last_bar_key = bar_key
        self._last_analysis = analysis
        return analysis

    def route_strategy_variant(self, regime: MarketRegime) -> str:
        """Return the strategy-variant label mapped to ``regime``.

        The return value is a label, not a module: the caller owns the mapping
        from label to executable strategy.
        """
        try:
            return self.strategy_variants[regime]
        except KeyError:
            raise RegimeDetectorError(f"No strategy variant mapped for regime {regime!r}.") from None


# ---------------------------------------------------------------------- #
# Validation helpers
# ---------------------------------------------------------------------- #


def _validate_finite_number(
    value: float,
    name: str,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise RegimeDetectorError(f"{name} must be a real number (got {value!r}).")
    numeric = float(value)
    if not math.isfinite(numeric):
        raise RegimeDetectorError(f"{name} must be finite (got {value!r}).")
    if minimum is not None and numeric < minimum:
        raise RegimeDetectorError(f"{name} must be >= {minimum} (got {numeric}).")
    if maximum is not None and numeric > maximum:
        raise RegimeDetectorError(f"{name} must be <= {maximum} (got {numeric}).")
    return numeric


def _validate_bars(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], min_bars: int
) -> None:
    """Reject bar data that would silently produce a meaningless regime.

    Non-finite values are the dangerous case: every comparison against a NaN is
    False, so an unvalidated NaN falls through the classification chain and is
    reported as MEAN_REVERTING_RANGING -- a normal-looking regime produced by
    corrupt data.
    """
    if not (len(highs) == len(lows) == len(closes)):
        raise RegimeDetectorError(
            f"highs, lows and closes must be the same length "
            f"(got {len(highs)}, {len(lows)}, {len(closes)})."
        )
    if len(closes) < min_bars:
        raise RegimeDetectorError(
            f"Insufficient bar data ({len(closes)} bars). Min {min_bars} required."
        )
    for i, (high, low, close) in enumerate(zip(highs, lows, closes)):
        for label, value in (("high", high), ("low", low), ("close", close)):
            if isinstance(value, bool) or not isinstance(value, numbers.Real):
                raise RegimeDetectorError(
                    f"Bar {i}: {label} must be a real number (got {value!r})."
                )
            if not math.isfinite(float(value)):
                raise RegimeDetectorError(f"Bar {i}: {label} is not finite (got {value!r}).")
        if high < low:
            raise RegimeDetectorError(f"Bar {i}: high ({high}) is below low ({low}).")

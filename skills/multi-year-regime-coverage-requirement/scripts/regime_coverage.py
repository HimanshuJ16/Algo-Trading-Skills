"""
multi-year-regime-coverage-requirement: retrospective market-regime classifier,
multi-year coverage auditor, and de-averaged per-regime performance breakdown.

Purpose
-------
Answer one question before a strategy is promoted to live capital: *was this
backtest actually exposed to more than one kind of market, and did it survive
each one separately?* An aggregate multi-year Sharpe hides the regime that would
have ended the strategy. This engine buckets every bar into a regime, refuses to
count a regime that barely appears, and reports drawdown, return and Sharpe per
bucket so the worst bucket is visible rather than averaged away.

Classification (heuristic, engine-internal)
-------------------------------------------
For each bar ``i`` with a full trailing window of ``window_size`` returns:

    ann_vol   = stdev(window returns) * sqrt(bars_per_year)
    change    = (price[i] - price[i - window_size]) / price[i - window_size]

    ann_vol > high_vol_annualized_threshold      -> HIGH_VOLATILITY_CRASH
    change  > trend_threshold_pct                -> BULL_TREND
    change  < -trend_threshold_pct               -> BEAR_MARKET
    otherwise                                    -> LOW_VOLATILITY_RANGE

Bars without a full trailing window are ``UNCLASSIFIED`` and are excluded from
coverage counting and from every per-regime metric. They are counted and
reported so the exclusion is visible.

**These four labels are engine-internal buckets, not conventional market
definitions.** The SEC's investor education material defines a bear market as a
broad index falling 20% or more over at least a two-month period
(investor.gov, "Bear Market"); a 20-bar move of -3% is not that. Do not report a
``BEAR_MARKET`` bar count as "the strategy was tested through a bear market" in
an external document. The thresholds are defaults chosen for this engine, not
values derived from a regulator, an exchange, or a published study — tune them
per asset class and record what you used.

Look-ahead
----------
Bar ``i``'s label uses a window *ending at bar i*, including bar i's own price.
That is correct and intended for retrospective attribution: the return earned on
a bar that crashed belongs to the crash bucket. It makes the labels **unusable
as a live trading signal** — at the open of bar i, price[i] is not known. For
live regime routing use ``regime-detection-for-strategy-switching``, which
applies a hysteresis filter to confirmed, already-closed bars.

Sharpe ratio
------------
Per-regime Sharpe uses the ex-post definition of Sharpe (1994), "The Sharpe
Ratio", Journal of Portfolio Management 21(1): mean differential return divided
by its standard deviation, where the standard deviation uses the **population
divisor** (Sharpe's endnote: "We use the formula for the standard deviation of a
population, taking the observations as a sample"). ``strategy_returns`` are
treated as *differential* (excess) returns; with a non-zero risk-free rate the
caller must subtract it before calling, otherwise the figure is a
return-to-variability ratio, not a Sharpe ratio.

The ``sqrt(bars_per_year)`` annualization is reported as a **comparative
indicator only**. Sharpe (1994) states the sqrt(T) rule holds "under simple
conditions with zero serial correlation" and flags compounding and serial
correlation as complications; Lo (2002), "The Statistics of Sharpe Ratios",
Financial Analysts Journal 58(4), shows serial correlation can overstate an
annualized Sharpe by as much as 65%. A regime bucket is a set of non-contiguous
bars selected *conditional on the price path* — close to the worst case for the
IID assumption. Use these numbers to rank regimes against each other, not as
defensible annualized statistics.

Limitations (documented, deliberate)
------------------------------------
- **Bar frequency cannot be inferred** from a list of floats. ``bars_per_year``
  drives both the duration gate and the annualization and must be set by the
  caller (252 for daily bars). Leaving it at 252 while feeding 1-minute bars
  reports a one-year backtest as 390 years of coverage.
- **Calendar gaps are invisible.** Duration is bar count / ``bars_per_year``, not
  a timestamp span. A series with a six-month hole reports the span it would
  have had if the bars were consecutive. Pass a gap-free series.
- **Drawdown is per-regime, not portfolio-level.** The veto here asks "did any
  single regime hurt badly", not "what was the worst drawdown of the backtest".
  For the account-level control see ``kill-switch-and-drawdown-circuit-breakers``
  and ``portfolio-level-stop-loss-independent-of-strategy-stops``.
- **No regime-transition analysis.** Bars are bucketed, not sequenced; the cost of
  switching between regimes is not measured.
- **Passing this audit is a necessary, not sufficient, condition** for promotion.
  It says nothing about look-ahead bias, overfitting, or capacity.
"""
import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Trading days per year for daily bars. Any other bar frequency must override
#: ``bars_per_year`` explicitly -- it cannot be inferred from the data.
DAILY_BARS_PER_YEAR = 252

#: Default trailing window (bars) used to classify each bar.
DEFAULT_WINDOW_SIZE = 20

#: Default annualized-volatility level above which a window is bucketed as
#: HIGH_VOLATILITY_CRASH. A heuristic default for daily equity-index bars, not a
#: published or regulatory threshold.
DEFAULT_HIGH_VOL_ANNUALIZED_THRESHOLD = 0.35

#: Default absolute window price change separating trend from range. Heuristic.
DEFAULT_TREND_THRESHOLD_PCT = 0.03

#: Default minimum bars a regime needs before it counts toward coverage and
#: before its Sharpe ratio is reported. 21 bars ~ one trading month of daily
#: data. A regime seen for three bars is an artifact, not coverage.
DEFAULT_MIN_BARS_PER_REGIME = 21

#: Dispersion below this fraction of the largest absolute return in a bucket is
#: floating-point noise rather than signal, so the Sharpe ratio is undefined.
#: Guards the failure where a constant return series yields a ~1e-19 standard
#: deviation and therefore an astronomically large "Sharpe ratio".
DISPERSION_RELATIVE_EPSILON = 1e-9


class MarketRegime(Enum):
    BULL_TREND = "BULL_TREND"
    BEAR_MARKET = "BEAR_MARKET"
    HIGH_VOLATILITY_CRASH = "HIGH_VOLATILITY_CRASH"
    LOW_VOLATILITY_RANGE = "LOW_VOLATILITY_RANGE"
    #: Warm-up bars without a full trailing window. Never counts toward coverage
    #: and never carries performance metrics -- previously these bars were
    #: silently labelled LOW_VOLATILITY_RANGE, fabricating a fourth regime.
    UNCLASSIFIED = "UNCLASSIFIED"


#: The regimes that can satisfy a coverage requirement (UNCLASSIFIED cannot).
CLASSIFIABLE_REGIMES: Tuple[MarketRegime, ...] = (
    MarketRegime.BULL_TREND,
    MarketRegime.BEAR_MARKET,
    MarketRegime.HIGH_VOLATILITY_CRASH,
    MarketRegime.LOW_VOLATILITY_RANGE,
)


@dataclass
class RegimePerformanceMetrics:
    regime: MarketRegime
    bars_count: int
    total_return_pct: float
    #: Annualized ex-post Sharpe ratio, or ``None`` when undefined: fewer than
    #: ``min_bars_per_regime`` observations, or dispersion indistinguishable from
    #: zero. ``None`` means "not measurable", never 0.0 and never a huge number
    #: produced by an epsilon denominator.
    sharpe_ratio: Optional[float]
    #: Worst peak-to-trough decline *within a single contiguous episode* of this
    #: regime -- the decline actually experienced. This is the veto metric.
    max_drawdown_pct: float
    win_rate_pct: float
    #: Number of contiguous runs of bars in this regime.
    episode_count: int = 0
    #: Drawdown of a synthetic equity curve built by concatenating every bar of
    #: this regime, skipping the bars in between. Useful for spotting death by a
    #: thousand cuts across many separate episodes, but it is a decline no
    #: account ever experienced -- do not report it as a realized drawdown.
    concatenated_drawdown_pct: float = 0.0
    #: False when the regime appears but with fewer than ``min_bars_per_regime``
    #: bars: observed, reported, but not counted as coverage.
    counts_toward_coverage: bool = True


@dataclass
class RegimeCoverageAuditReport:
    #: Bar count / bars_per_year, unrounded. Compared against the minimum
    #: *before* any rounding, so 755 daily bars no longer passes a 3-year gate.
    total_years: float
    #: Regimes with at least ``min_bars_per_regime`` bars. These satisfy the
    #: coverage requirement.
    unique_regimes_covered: List[MarketRegime]
    regime_metrics: Dict[str, RegimePerformanceMetrics]
    is_coverage_sufficient: bool
    is_promotable: bool
    message: str
    #: Every regime with at least one bar, including those too thin to count.
    regimes_observed: List[MarketRegime] = field(default_factory=list)
    #: Regimes whose within-episode drawdown breached the limit. Empty means no
    #: drawdown veto fired -- the audit message must not claim one did.
    vetoed_regimes: List[MarketRegime] = field(default_factory=list)
    bars_analyzed: int = 0
    #: Warm-up bars excluded from every metric above.
    unclassified_bars: int = 0
    bars_per_year: int = DAILY_BARS_PER_YEAR


def _validate_prices(prices: Sequence[float]) -> List[float]:
    """
    Rejects price series that make classification meaningless or unsafe.

    A zero price raised an unhandled ZeroDivisionError inside the return
    calculation; a NaN price propagated silently, because every comparison
    against NaN is False, so a corrupt window fell through to
    LOW_VOLATILITY_RANGE.
    """
    if not isinstance(prices, (list, tuple)):
        prices = list(prices)
    if len(prices) < 2:
        raise ValueError(f"prices must contain at least 2 observations, got {len(prices)}.")
    out: List[float] = []
    for i, p in enumerate(prices):
        value = float(p)
        if not math.isfinite(value):
            raise ValueError(f"prices[{i}] is not finite ({p!r}); reject corrupt data before auditing.")
        if value <= 0.0:
            raise ValueError(
                f"prices[{i}] is {value}; prices must be strictly positive. "
                "Back-adjusted futures series can go non-positive -- use a ratio-adjusted "
                "or unadjusted series for regime classification."
            )
        out.append(value)
    return out


def _validate_returns(strategy_returns: Sequence[float], expected_len: int) -> List[float]:
    """
    Rejects return series that would silently disable the drawdown veto.

    A single NaN made ``equity`` NaN, after which ``dd > max_dd`` is False
    forever, so ``max_drawdown_pct`` stayed 0.0 and no veto fired: corrupt data
    produced an automatic pass. A length mismatch was silently truncated to the
    shorter series, which misaligns every regime label by the n-vs-n-1
    off-by-one that return series routinely introduce.
    """
    if not isinstance(strategy_returns, (list, tuple)):
        strategy_returns = list(strategy_returns)
    if len(strategy_returns) != expected_len:
        raise ValueError(
            f"strategy_returns has {len(strategy_returns)} observations but prices has "
            f"{expected_len}. They must align one-to-one: strategy_returns[i] is the return "
            "realized over the bar ending at prices[i]. If your return series is one shorter, "
            "pad the first element explicitly rather than letting the audit truncate."
        )
    out: List[float] = []
    for i, r in enumerate(strategy_returns):
        value = float(r)
        if not math.isfinite(value):
            raise ValueError(
                f"strategy_returns[{i}] is not finite ({r!r}). A non-finite return silently "
                "disables the drawdown veto -- clean the series before auditing."
            )
        if value <= -1.0:
            raise ValueError(
                f"strategy_returns[{i}] is {value}, which wipes out or inverts the equity "
                "curve; every compounded figure after it is meaningless. Handle total loss "
                "upstream."
            )
        out.append(value)
    return out


def _population_stdev(values: Sequence[float], mean: float) -> float:
    """Population standard deviation, matching Sharpe (1994)'s ex-post definition."""
    variance = math.fsum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(max(0.0, variance))


def _max_drawdown(returns: Sequence[float]) -> float:
    """Peak-to-trough decline of the compounded equity curve, as a fraction."""
    peak = 1.0
    equity = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= (1.0 + r)
        if equity > peak:
            peak = equity
        dd = (peak - equity) / peak
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _contiguous_runs(indices: Sequence[int]) -> List[List[int]]:
    """Splits a sorted index list into runs of consecutive integers."""
    runs: List[List[int]] = []
    for idx in indices:
        if runs and idx == runs[-1][-1] + 1:
            runs[-1].append(idx)
        else:
            runs.append([idx])
    return runs


class MarketRegimeCoverageEngine:
    """
    Buckets a price series into market regimes, audits multi-year regime coverage,
    and reports de-averaged per-regime performance.

    All thresholds are constructor parameters with heuristic defaults. Record the
    values used alongside the audit report -- an audit is only reproducible if the
    thresholds that produced it are known.
    """

    def __init__(
        self,
        min_required_years: float = 3.0,
        min_required_regimes: int = 3,
        max_allowed_regime_drawdown_pct: float = 25.0,
        bars_per_year: int = DAILY_BARS_PER_YEAR,
        min_bars_per_regime: int = DEFAULT_MIN_BARS_PER_REGIME,
        window_size: int = DEFAULT_WINDOW_SIZE,
        high_vol_annualized_threshold: float = DEFAULT_HIGH_VOL_ANNUALIZED_THRESHOLD,
        trend_threshold_pct: float = DEFAULT_TREND_THRESHOLD_PCT,
    ):
        if not math.isfinite(min_required_years) or min_required_years <= 0.0:
            raise ValueError(f"min_required_years must be positive and finite, got {min_required_years}.")
        if not 1 <= min_required_regimes <= len(CLASSIFIABLE_REGIMES):
            raise ValueError(
                f"min_required_regimes must be between 1 and {len(CLASSIFIABLE_REGIMES)}, "
                f"got {min_required_regimes}: only {len(CLASSIFIABLE_REGIMES)} regimes exist."
            )
        if not 0.0 < max_allowed_regime_drawdown_pct <= 100.0:
            raise ValueError(
                f"max_allowed_regime_drawdown_pct must be in (0, 100], got "
                f"{max_allowed_regime_drawdown_pct}. It is a percent, not a fraction."
            )
        if bars_per_year < 1:
            raise ValueError(f"bars_per_year must be at least 1, got {bars_per_year}.")
        if min_bars_per_regime < 1:
            raise ValueError(f"min_bars_per_regime must be at least 1, got {min_bars_per_regime}.")
        if window_size < 2:
            raise ValueError(f"window_size must be at least 2, got {window_size}.")
        if not math.isfinite(high_vol_annualized_threshold) or high_vol_annualized_threshold <= 0.0:
            raise ValueError(
                f"high_vol_annualized_threshold must be positive and finite, got "
                f"{high_vol_annualized_threshold}."
            )
        if not math.isfinite(trend_threshold_pct) or trend_threshold_pct <= 0.0:
            raise ValueError(
                f"trend_threshold_pct must be positive and finite, got {trend_threshold_pct}. "
                "It is a fraction (0.03 = 3%), applied symmetrically."
            )

        self.min_required_years = float(min_required_years)
        self.min_required_regimes = int(min_required_regimes)
        self.max_allowed_regime_drawdown_pct = float(max_allowed_regime_drawdown_pct)
        self.bars_per_year = int(bars_per_year)
        self.min_bars_per_regime = int(min_bars_per_regime)
        self.window_size = int(window_size)
        self.high_vol_annualized_threshold = float(high_vol_annualized_threshold)
        self.trend_threshold_pct = float(trend_threshold_pct)

        if self.bars_per_year == DAILY_BARS_PER_YEAR:
            logger.info(
                "bars_per_year is the daily default (%d). Intraday bars must override it: "
                "otherwise one year of 1-minute bars audits as ~390 years of coverage.",
                DAILY_BARS_PER_YEAR,
            )

    def classify_regimes(
        self,
        prices: Sequence[float],
        window_size: Optional[int] = None,
    ) -> List[MarketRegime]:
        """
        Labels every bar with a MarketRegime from its trailing window.

        Bars 0..window_size-1 lack a full window and are ``UNCLASSIFIED``.
        ``window_size`` defaults to the engine's configured value -- previously
        ``audit_coverage`` always used the hard-coded 20 and silently ignored any
        configured window.

        The label for bar i includes bar i's own price and is therefore
        retrospective only. Do not use it as a live signal.
        """
        window = self.window_size if window_size is None else int(window_size)
        if window < 2:
            raise ValueError(f"window_size must be at least 2, got {window}.")
        clean = _validate_prices(prices)
        if window >= len(clean):
            # Warned here rather than in _validate_prices, which runs twice per
            # audit and would otherwise emit the same line twice.
            logger.warning(
                "window_size %d >= series length %d: every bar will be UNCLASSIFIED.",
                window, len(clean),
            )
        n = len(clean)

        ann_factor = math.sqrt(self.bars_per_year)
        regimes: List[MarketRegime] = [MarketRegime.UNCLASSIFIED] * n

        for i in range(window, n):
            sub = clean[i - window : i + 1]
            rets = [(sub[j] - sub[j - 1]) / sub[j - 1] for j in range(1, len(sub))]
            mean_r = math.fsum(rets) / len(rets)
            ann_vol = _population_stdev(rets, mean_r) * ann_factor

            price_change_pct = (sub[-1] - sub[0]) / sub[0]

            if ann_vol > self.high_vol_annualized_threshold:
                regimes[i] = MarketRegime.HIGH_VOLATILITY_CRASH
            elif price_change_pct > self.trend_threshold_pct:
                regimes[i] = MarketRegime.BULL_TREND
            elif price_change_pct < -self.trend_threshold_pct:
                regimes[i] = MarketRegime.BEAR_MARKET
            else:
                regimes[i] = MarketRegime.LOW_VOLATILITY_RANGE

        return regimes

    def _regime_metrics(
        self,
        regime: MarketRegime,
        indices: Sequence[int],
        strategy_returns: Sequence[float],
    ) -> Tuple[RegimePerformanceMetrics, float]:
        """
        Computes de-averaged metrics for one regime bucket.

        Returns the metrics alongside the *unrounded* within-episode drawdown
        fraction. The veto compares against that value: rounding the percentage
        to 2 dp first lets a 25.004% decline present as 25.00% and slip under a
        25% limit, the same class of bug as rounding the duration before the
        coverage comparison.
        """
        rets = [strategy_returns[i] for i in indices]
        bars_cnt = len(rets)

        # Compounded, consistent with the equity curve the drawdown walks. The
        # previous arithmetic sum of simple returns disagreed with it.
        equity = 1.0
        for r in rets:
            equity *= (1.0 + r)
        total_return = equity - 1.0

        wins = sum(1 for r in rets if r > 0)
        win_rate = (wins / bars_cnt) * 100.0

        mean_r = math.fsum(rets) / bars_cnt
        std_r = _population_stdev(rets, mean_r)
        scale = max(abs(r) for r in rets)
        dispersion_is_real = std_r > DISPERSION_RELATIVE_EPSILON * max(scale, 1e-30)

        sharpe: Optional[float] = None
        if bars_cnt < self.min_bars_per_regime:
            logger.info(
                "Sharpe for '%s' not reported: %d bars < min_bars_per_regime %d.",
                regime.value, bars_cnt, self.min_bars_per_regime,
            )
        elif not dispersion_is_real:
            logger.warning(
                "Sharpe for '%s' undefined: return dispersion (%.3e) is floating-point noise "
                "relative to the return scale (%.3e). A constant return series has no "
                "risk-adjusted interpretation.", regime.value, std_r, scale,
            )
        else:
            sharpe = round((mean_r / std_r) * math.sqrt(self.bars_per_year), 2)

        # Experienced drawdown: worst decline inside one contiguous episode.
        episodes = _contiguous_runs(list(indices))
        max_dd = 0.0
        for run in episodes:
            run_dd = _max_drawdown([strategy_returns[i] for i in run])
            if run_dd > max_dd:
                max_dd = run_dd

        return (
            RegimePerformanceMetrics(
                regime=regime,
                bars_count=bars_cnt,
                total_return_pct=round(total_return * 100.0, 2),
                sharpe_ratio=sharpe,
                max_drawdown_pct=round(max_dd * 100.0, 2),
                win_rate_pct=round(win_rate, 2),
                episode_count=len(episodes),
                concatenated_drawdown_pct=round(_max_drawdown(rets) * 100.0, 2),
                counts_toward_coverage=bars_cnt >= self.min_bars_per_regime,
            ),
            max_dd,
        )

    def audit_coverage(
        self,
        prices: Sequence[float],
        strategy_returns: Sequence[float],
    ) -> RegimeCoverageAuditReport:
        """
        Audits multi-year regime coverage and computes de-averaged per-regime metrics.

        ``strategy_returns[i]`` must be the return realized over the bar ending at
        ``prices[i]``; the two series must be the same length. Raises ValueError on
        non-finite or non-positive prices, non-finite returns, returns at or below
        -100%, and length mismatch.
        """
        clean_prices = _validate_prices(prices)
        clean_returns = _validate_returns(strategy_returns, len(clean_prices))
        n = len(clean_prices)

        total_years = n / float(self.bars_per_year)
        regimes = self.classify_regimes(clean_prices)

        indices_by_regime: Dict[MarketRegime, List[int]] = {m: [] for m in CLASSIFIABLE_REGIMES}
        unclassified_bars = 0
        for i, reg in enumerate(regimes):
            if reg is MarketRegime.UNCLASSIFIED:
                unclassified_bars += 1
            else:
                indices_by_regime[reg].append(i)

        metrics_map: Dict[str, RegimePerformanceMetrics] = {}
        regimes_observed: List[MarketRegime] = []
        unique_covered: List[MarketRegime] = []
        vetoed_regimes: List[MarketRegime] = []
        thin_regimes: List[MarketRegime] = []

        for reg in CLASSIFIABLE_REGIMES:
            indices = indices_by_regime[reg]
            if not indices:
                continue

            regimes_observed.append(reg)
            metrics, raw_max_dd = self._regime_metrics(reg, indices, clean_returns)
            metrics_map[reg.value] = metrics

            if metrics.counts_toward_coverage:
                unique_covered.append(reg)
            else:
                thin_regimes.append(reg)
                logger.info(
                    "Regime '%s' observed with only %d bars (< min_bars_per_regime %d): "
                    "reported but not counted toward coverage.",
                    reg.value, metrics.bars_count, self.min_bars_per_regime,
                )

            # Drawdown is a path fact, so it vetoes regardless of sample size --
            # a 40% loss over five crash bars is still a 40% loss.
            if raw_max_dd * 100.0 > self.max_allowed_regime_drawdown_pct:
                vetoed_regimes.append(reg)
                logger.warning(
                    "REGIME VETO: within-episode max drawdown %.4f%% in '%s' exceeds limit %.2f%%.",
                    raw_max_dd * 100.0, reg.value, self.max_allowed_regime_drawdown_pct,
                )

        duration_ok = total_years >= self.min_required_years
        regimes_ok = len(unique_covered) >= self.min_required_regimes
        is_coverage_ok = duration_ok and regimes_ok
        is_promotable = is_coverage_ok and not vetoed_regimes

        # Each failure gets its own reason. Previously a pure coverage failure
        # still appended "REGIME VETO: Exceeded max drawdown threshold", claiming
        # a drawdown breach on strategies that had none.
        reasons: List[str] = []
        if not duration_ok:
            reasons.append(
                f"Insufficient duration ({total_years:.4f} yrs < {self.min_required_years:.4f} "
                f"required, {n} bars at {self.bars_per_year} bars/yr)."
            )
        if not regimes_ok:
            thin_note = ""
            if thin_regimes:
                thin_note = (
                    f" {len(thin_regimes)} further regime(s) appeared with fewer than "
                    f"{self.min_bars_per_regime} bars and were not counted: "
                    f"{', '.join(r.value for r in thin_regimes)}."
                )
            reasons.append(
                f"Insufficient regimes ({len(unique_covered)} covered < "
                f"{self.min_required_regimes} required).{thin_note}"
            )
        if vetoed_regimes:
            reasons.append(
                "REGIME VETO: within-episode drawdown exceeded "
                f"{self.max_allowed_regime_drawdown_pct:.2f}% in "
                f"{', '.join(r.value for r in vetoed_regimes)}."
            )

        if reasons:
            msg = " | ".join(reasons)
            logger.warning("Backtest NOT promotable: %s", msg)
        else:
            msg = (
                f"Regime coverage verified across {total_years:.2f} years and "
                f"{len(unique_covered)} regimes. No regime breached the "
                f"{self.max_allowed_regime_drawdown_pct:.2f}% drawdown limit. Strategy promotable."
            )
            logger.info(msg)

        return RegimeCoverageAuditReport(
            total_years=total_years,
            unique_regimes_covered=unique_covered,
            regime_metrics=metrics_map,
            is_coverage_sufficient=is_coverage_ok,
            is_promotable=is_promotable,
            message=msg,
            regimes_observed=regimes_observed,
            vetoed_regimes=vetoed_regimes,
            bars_analyzed=n,
            unclassified_bars=unclassified_bars,
            bars_per_year=self.bars_per_year,
        )

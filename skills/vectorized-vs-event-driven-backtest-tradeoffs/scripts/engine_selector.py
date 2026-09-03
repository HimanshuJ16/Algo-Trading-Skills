"""
vectorized-vs-event-driven-backtest-tradeoffs: a matched pair of backtest engines
built to be compared against each other, plus an engine-selection advisor.

What this module is for
-----------------------
Measuring how much of a backtest's reported performance is an artefact of the
*fill assumption* rather than of the strategy. It runs one signal series through
two engines that differ in exactly one respect at a time, and reports the gap.

It is a diagnostic harness, not a production backtester. There is no order book, no
partial fill, no queue position, no bid/ask, no market impact, no borrow cost, no
corporate action and no margin model. It takes a close series and a target-exposure
series and nothing else.

The three things that decide whether the comparison means anything
------------------------------------------------------------------
**1. Both engines must express positions in the same units.** ``signals[t]`` is a
*target exposure as a fraction of current equity* -- 1.0 fully invested long, -1.0
fully short, 0.0 flat. It is not a share count. an earlier event-driven engine
held ``signals[t]`` **shares** against a fixed $100,000 of capital, so on an
identical long-only series the vectorized engine reported 49.00% and the
event-driven engine 0.06%. That 48.94-point "execution drag" was a unit mismatch,
not a fill effect.

**2. Both engines must compound the same way.** Summing arithmetic per-bar returns
and compounding an equity curve give different answers on the same returns, and the
gap grows with the length and volatility of the series. Both engines here compound;
neither sums.

**3. The two engines must differ in one variable at a time.** ``compare_engines``
therefore runs three curves:

  * ``frictionless_metrics`` -- instant fill at the signal bar's close, zero cost.
    The number a naive vectorized backtest reports.
  * ``vectorized_metrics``   -- instant fill at the signal bar's close, costs charged.
  * ``event_driven_metrics`` -- costs charged **and** the order fills
    ``execution_lag_bars`` bars later, at a slipped price.

so ``cost_drag_pct`` (frictionless - vectorized) and ``return_drag_pct``
(vectorized - event-driven) are separately attributable. A single combined number
cannot distinguish a cost problem from a latency problem, and the two have
different remedies.

Timing convention (no look-ahead)
---------------------------------
``signals[t]`` is the target exposure decided **at the close of bar t** and must be
computable from data up to and including bar t. The caller owns that guarantee;
this module cannot check it. See ``lookahead-bias-elimination``.

The idealised (vectorized) engine fills that decision at the close of bar t, so the
exposure earns bar t+1's return. The event-driven engine fills it at the close of
bar ``t + execution_lag_bars``. The default of 1 bar matches the documented default
of the mainstream Python event-driven engines -- ``backtesting.py`` fills market
orders on the next bar unless ``trade_on_close=True`` is set explicitly.

Weight drift
------------
The vectorized product ``w * r`` silently assumes the position is rebalanced to the
target weight every bar. A fully-invested long position satisfies that for free; a
short or a leveraged position does not, and drifts away from its target weight as
the price moves. ``rebalance_every_bar`` makes the event-driven engine hold the
weight constant (matching the vectorized formulation, at the cost of churn a real
book would pay for). It defaults to ``False`` -- the realistic behaviour -- so a
short signal diverges from the vectorized curve even at zero cost and zero latency.
That divergence is a modelling difference, not a defect.

Speed
-----
The vectorized engine is NumPy array arithmetic; the event-driven engine is a
per-bar Python loop. The ratio between them is reported as ``speedup_factor``,
**measured**, and is ``None`` below ``MIN_BARS_FOR_TIMING`` bars because timing a
sub-millisecond workload measures the clock rather than the engines. There is no
universal speedup figure: the ratio scales with how many events the loop must
process per unit of vectorizable work, so a monthly rebalance and a per-bar signal
are not comparable. Published third-party benchmarks on realistic strategies land
in single- to low-double-digit multiples (~6-8x Moonshot vs Zipline on a
1,000-name monthly factor rebalance; ~20x VectorBT vs Backtrader on a 500-name
monthly momentum rotation), not the 1,000x an earlier documentation asserted.
Measure it on your own workload; do not quote a constant.
"""
from dataclasses import dataclass, field
import logging
import math
import time
from enum import Enum
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

logger = logging.getLogger(__name__)

#: Bars per year used to annualize the Sharpe ratio. 252 is the US equity regular
#: session convention; pass ``periods_per_year`` explicitly for any other bar size
#: (e.g. 252 * 390 for 1-minute US equity bars, 365 for daily crypto).
TRADING_DAYS_PER_YEAR = 252.0

#: Below this many bars a wall-clock ratio between the two engines is timer noise,
#: so ``speedup_factor`` is reported as None rather than as a number.
MIN_BARS_FOR_TIMING = 5_000

#: Return dispersion below which a Sharpe ratio is undefined rather than enormous.
#: A constant return series has a sample standard deviation of ~1e-17 (float noise),
#: not exactly 0.0, so an ``or``-style zero guard does not catch it.
_MIN_RETURN_STD = 1e-12

#: Default ceiling on |target exposure|. 1.0 = no leverage. Raise it deliberately.
DEFAULT_MAX_ABS_EXPOSURE = 1.0

#: Estimated annual friction above which the idealised fill assumption is a
#: first-order driver of the result rather than a detail, so the event-driven
#: engine is required. A fraction of equity per year.
DEFAULT_MAX_TOLERABLE_ANNUAL_COST_DRAG = 0.02


class RecommendedEngine(Enum):
    VECTORIZED = "VECTORIZED"
    EVENT_DRIVEN = "EVENT_DRIVEN"


@dataclass
class EngineRecommendation:
    """Which engine to use, and the arithmetic behind the answer."""

    engine: RecommendedEngine
    complexity_score: float
    reason: str
    #: Characteristics that make a vectorized backtest structurally invalid, not
    #: merely imprecise. Non-empty implies EVENT_DRIVEN regardless of the score.
    blocking_reasons: List[str] = field(default_factory=list)
    #: turnover x cost rate, annualized, as a percentage of equity.
    estimated_annual_cost_drag_pct: float = 0.0


@dataclass
class BacktestEngineMetrics:
    total_return_pct: float
    sharpe_ratio: float
    trades_count: int
    execution_time_sec: float
    #: Sum of |change in target exposure| over the run, in units of equity. Cost
    #: drag is this times the per-unit cost rate, which is why a drag figure
    #: reported without a turnover figure cannot be interpreted.
    total_turnover: float = 0.0
    #: Equity marked at each bar's close, length n+1, with the opening equity at
    #: index 0 so ``equity_curve[t + 1]`` is the mark at the close of bar t.
    #: Exposed so the timing convention can be audited: two runs whose inputs agree
    #: up to bar k must have identical equity through index k+1, whatever happens
    #: after it. A run that fails that check is reading the future.
    equity_curve: Optional[np.ndarray] = field(default=None, repr=False)


@dataclass
class DualEngineAuditReport:
    vectorized_metrics: BacktestEngineMetrics
    event_driven_metrics: BacktestEngineMetrics
    sharpe_divergence: float
    #: vectorized - event_driven, in percentage points. Attributable to fill
    #: latency and slippage alone: both engines charge the same commission on the
    #: same turnover.
    return_drag_pct: float
    #: None when the series is too short for the wall-clock ratio to mean anything.
    speedup_factor: Optional[float]
    summary: str
    #: Instant fill at the signal bar's close with zero costs -- the curve a naive
    #: vectorized backtest reports.
    frictionless_metrics: Optional[BacktestEngineMetrics] = None
    #: frictionless - vectorized, in percentage points. Transaction costs alone.
    cost_drag_pct: float = 0.0
    #: frictionless - event_driven, in percentage points. Everything the idealised
    #: curve loses once costs and latency are both applied.
    total_drag_pct: float = 0.0


def _validate_series(
    prices: Sequence[float],
    signals: Sequence[float],
    max_abs_exposure: float,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Rejects series that cannot produce a meaningful backtest.

    Each of these was previously either a silent all-zero result that looked like a
    successful backtest (length mismatch, or fewer than two bars), an uncaught
    ``ZeroDivisionError`` (a zero price), or a NaN that propagated into every
    reported metric without ever raising.
    """
    p = np.asarray(prices, dtype=float)
    s = np.asarray(signals, dtype=float)

    if p.ndim != 1 or s.ndim != 1:
        raise ValueError("prices and signals must each be one-dimensional")
    if p.size != s.size:
        raise ValueError(
            f"prices and signals must be the same length (got {p.size} and {s.size}); "
            "a length mismatch means the signal is not aligned to the bar it was decided on"
        )
    if p.size < 2:
        raise ValueError(f"need at least 2 bars to compute a return, got {p.size}")

    if not np.all(np.isfinite(p)):
        bad = int(np.argmax(~np.isfinite(p)))
        raise ValueError(f"prices contains a non-finite value at index {bad}")
    if not np.all(np.isfinite(s)):
        bad = int(np.argmax(~np.isfinite(s)))
        raise ValueError(f"signals contains a non-finite value at index {bad}")
    if np.any(p <= 0.0):
        bad = int(np.argmax(p <= 0.0))
        raise ValueError(f"prices must be strictly positive; index {bad} is {p[bad]}")

    if max_abs_exposure <= 0.0:
        raise ValueError(f"max_abs_exposure must be positive, got {max_abs_exposure}")
    if np.any(np.abs(s) > max_abs_exposure):
        bad = int(np.argmax(np.abs(s) > max_abs_exposure))
        raise ValueError(
            f"signals[{bad}] = {s[bad]} exceeds max_abs_exposure={max_abs_exposure}. "
            "signals are target exposure as a fraction of equity, not share counts; "
            "raise max_abs_exposure only if that leverage is intended"
        )
    return p, s


def annualized_sharpe(
    returns: Sequence[float],
    periods_per_year: float = TRADING_DAYS_PER_YEAR,
    risk_free_rate: float = 0.0,
) -> float:
    """
    Annualized Sharpe ratio of a per-period simple return series:
    ``mean(r - rf_per_period) / stdev(r - rf_per_period) * sqrt(periods_per_year)``,
    using the *sample* standard deviation (ddof=1).

    Returns NaN -- not a large number -- when fewer than two observations exist or
    when dispersion is below ``_MIN_RETURN_STD``. A constant return series has no
    risk to adjust for, so its Sharpe ratio is undefined. an earlier guard
    (``std_r or 0.0001``) only caught a standard deviation of exactly 0.0, so a
    constant +1%/bar series -- whose sample standard deviation is ~1e-17 of float
    noise, not zero -- reported a Sharpe ratio of 1.6e15.

    The sqrt(periods_per_year) rule assumes i.i.d. returns. Lagged execution induces
    serial correlation, under which the rule is biased: Lo (2002) reports annual
    Sharpe ratios overstated by as much as 65% on serially correlated monthly
    returns. Read the return drag, which needs no distributional assumption,
    alongside any cross-engine Sharpe comparison.
    """
    if periods_per_year <= 0.0:
        raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
    r = np.asarray(returns, dtype=float)
    if r.size < 2:
        return float("nan")

    excess = r - (risk_free_rate / periods_per_year)
    mean = float(np.mean(excess))
    sd = float(np.std(excess, ddof=1))
    if not math.isfinite(sd) or not math.isfinite(mean) or sd < _MIN_RETURN_STD:
        return float("nan")
    return (mean / sd) * math.sqrt(periods_per_year)


class DualBacktestEngineSelector:
    """
    Recommends an engine architecture, and runs a matched vectorized / event-driven
    pair so the gap between them can be attributed to cost and to fill latency
    separately.

    Args:
        commission_bps: Commission per unit of traded notional, in basis points.
        slippage_bps: One-way slippage in basis points, applied to the fill price
            against the direction of the trade.
        periods_per_year: Bars per year, for Sharpe annualization. The default of
            252 is daily US equity bars; minute bars are not daily bars.
        risk_free_rate: Annual risk-free rate, deducted per bar before the ratio.
        initial_capital: Starting equity. Returns are reported as percentages so
            this does not change them -- but it must be identical across engines,
            which is exactly what a naive implementation got wrong.
        max_abs_exposure: Rejects any |signal| above this. See ``_validate_series``.
    """

    def __init__(
        self,
        commission_bps: float = 5.0,
        slippage_bps: float = 5.0,
        periods_per_year: float = TRADING_DAYS_PER_YEAR,
        risk_free_rate: float = 0.0,
        initial_capital: float = 100_000.0,
        max_abs_exposure: float = DEFAULT_MAX_ABS_EXPOSURE,
    ) -> None:
        if commission_bps < 0.0 or slippage_bps < 0.0:
            raise ValueError("commission_bps and slippage_bps must be non-negative")
        if initial_capital <= 0.0:
            raise ValueError(f"initial_capital must be positive, got {initial_capital}")
        if periods_per_year <= 0.0:
            raise ValueError(f"periods_per_year must be positive, got {periods_per_year}")
        if max_abs_exposure <= 0.0:
            raise ValueError(f"max_abs_exposure must be positive, got {max_abs_exposure}")

        self.commission_rate = commission_bps / 10_000.0
        self.slippage_rate = slippage_bps / 10_000.0
        self.periods_per_year = float(periods_per_year)
        self.risk_free_rate = float(risk_free_rate)
        self.initial_capital = float(initial_capital)
        self.max_abs_exposure = float(max_abs_exposure)

    # ------------------------------------------------------------------ advisor

    def recommend_engine(
        self,
        trades_per_day: float,
        uses_limit_orders: bool = False,
        uses_path_dependent_stops: bool = False,
        avg_exposure_change_per_trade: float = 1.0,
        max_tolerable_annual_cost_drag: float = DEFAULT_MAX_TOLERABLE_ANNUAL_COST_DRAG,
    ) -> EngineRecommendation:
        """
        Recommends an engine architecture.

        Two of the three inputs are *blocking*, not weighted. A weighted score that
        can be outvoted is the wrong shape for them, because they do not make a
        vectorized backtest less accurate -- they make it structurally unable to
        represent the strategy at all:

        * **Path-dependent stops.** Whether the position is still open at bar t
          depends on the realized path since entry, so the exposure series is not
          knowable before the run. There is nothing to multiply the return vector
          by. Engines that appear to vectorize this (VectorBT and similar)
          JIT-compile the loop rather than removing it: a faster event loop, not
          array algebra.
        * **Limit or other passive orders.** A limit order may not fill at all. A
          vectorized engine has no representation of an unfilled order; it applies
          the exposure unconditionally, which is the assumption that every order
          filled, at the best available price.

        an earlier rule scored these 3.0 each against a threshold of 4.0, so a
        strategy whose *only* complex feature was path-dependent stops was routed to
        the vectorized engine -- the one case where the vectorized answer is not
        merely optimistic but undefined. It also contradicted this skill's own
        documented workflow, which sends limit-order strategies to the event engine.

        Turnover genuinely is a matter of degree, so it is treated as one: the
        estimated annual friction (``trades/day x bars/year x exposure change per
        trade x cost rate``) is computed and compared against
        ``max_tolerable_annual_cost_drag``. That replaces a bare "more than 10
        trades a day" threshold with arithmetic the caller can see and overrule.
        """
        if trades_per_day < 0.0:
            raise ValueError(f"trades_per_day must be non-negative, got {trades_per_day}")
        if avg_exposure_change_per_trade <= 0.0:
            raise ValueError("avg_exposure_change_per_trade must be positive")
        if max_tolerable_annual_cost_drag < 0.0:
            raise ValueError("max_tolerable_annual_cost_drag must be non-negative")

        cost_rate = self.commission_rate + self.slippage_rate
        annual_drag = (
            trades_per_day
            * self.periods_per_year
            * avg_exposure_change_per_trade
            * cost_rate
        )

        blocking: List[str] = []
        if uses_path_dependent_stops:
            blocking.append(
                "path-dependent stops: the exposure series depends on the realized "
                "path since entry and cannot be formed before the run"
            )
        if uses_limit_orders:
            blocking.append(
                "limit/passive orders: fills are conditional on the price trading "
                "through the limit, which a vectorized engine cannot represent"
            )

        score = 0.0
        if uses_path_dependent_stops:
            score += 3.0
        if uses_limit_orders:
            score += 3.0
        if annual_drag > max_tolerable_annual_cost_drag:
            score += 4.0

        if blocking:
            engine = RecommendedEngine.EVENT_DRIVEN
            reason = (
                f"Vectorized backtest is structurally invalid for this strategy "
                f"({'; '.join(blocking)}). Estimated annual friction "
                f"{annual_drag * 100.0:.2f}% of equity."
            )
        elif annual_drag > max_tolerable_annual_cost_drag:
            engine = RecommendedEngine.EVENT_DRIVEN
            reason = (
                f"Estimated annual friction {annual_drag * 100.0:.2f}% of equity exceeds the "
                f"{max_tolerable_annual_cost_drag * 100.0:.2f}% tolerance, so the fill "
                f"assumption drives the result rather than refining it."
            )
        else:
            engine = RecommendedEngine.VECTORIZED
            reason = (
                f"No blocking execution feature, and estimated annual friction "
                f"{annual_drag * 100.0:.2f}% of equity is within the "
                f"{max_tolerable_annual_cost_drag * 100.0:.2f}% tolerance. Use the vectorized "
                f"engine for parameter search, then confirm the survivors event-driven."
            )

        logger.info("Engine recommendation: %s | %s", engine.value, reason)
        return EngineRecommendation(
            engine=engine,
            complexity_score=score,
            reason=reason,
            blocking_reasons=blocking,
            estimated_annual_cost_drag_pct=round(annual_drag * 100.0, 4),
        )

    # ------------------------------------------------------------------- engines

    def run_vectorized_backtest(
        self,
        prices: Sequence[float],
        signals: Sequence[float],
        apply_costs: bool = True,
    ) -> BacktestEngineMetrics:
        """
        Idealised engine: NumPy array arithmetic, instant fill at the signal bar's
        close, no latency.

        Exposure over bar ``t+1`` is ``signals[t]``, with a starting exposure of 0.
        Costs are charged as a multiplicative haircut on equity at the moment of the
        trade, proportional to ``|signals[t] - signals[t-1]|``. an earlier
        version charged a flat one-way cost on any change, so a -1 -> +1 reversal
        (two units of turnover) cost the same as a 0 -> +1 entry, understating the
        cost of every reversal by half.

        Equity compounds; it is not a sum of per-bar returns.

        Set ``apply_costs=False`` for the frictionless baseline.
        """
        p, s = _validate_series(prices, signals, self.max_abs_exposure)
        t_start = time.perf_counter()

        # One entry per bar, so the array mirrors the event loop's bar-by-bar
        # "earn the bar, then trade at its close" order exactly. bar_returns[0] is 0
        # because there is no bar before the first one; held_weights[t] is the
        # exposure decided at bar t-1, and starts flat.
        bar_returns = np.concatenate(([0.0], p[1:] / p[:-1] - 1.0))
        held_weights = np.concatenate(([0.0], s[:-1]))
        turnover = np.abs(s - held_weights)

        cost_rate = (self.commission_rate + self.slippage_rate) if apply_costs else 0.0
        # The cost is a haircut on equity at the trade; the market return then applies
        # to what is left. Charging it multiplicatively rather than subtracting it
        # from the bar's return is what lets this engine agree with the event-driven
        # engine bar for bar once latency is set to zero.
        growth = (1.0 + held_weights * bar_returns) * (1.0 - turnover * cost_rate)
        equity = self.initial_capital * np.cumprod(growth)
        t_end = time.perf_counter()

        equity_curve = np.concatenate(([self.initial_capital], equity))
        self._warn_on_ruin(equity_curve, "Vectorized")
        period_returns = equity_curve[1:] / equity_curve[:-1] - 1.0
        period_returns = period_returns[np.isfinite(period_returns)]

        return BacktestEngineMetrics(
            total_return_pct=round(float(equity_curve[-1] / self.initial_capital - 1.0) * 100.0, 6),
            sharpe_ratio=round(
                annualized_sharpe(period_returns, self.periods_per_year, self.risk_free_rate), 4
            ),
            trades_count=int(np.count_nonzero(turnover)),
            execution_time_sec=t_end - t_start,
            total_turnover=round(float(np.sum(turnover)), 6),
            equity_curve=equity_curve,
        )

    def run_event_driven_backtest(
        self,
        prices: Sequence[float],
        signals: Sequence[float],
        execution_lag_bars: int = 1,
        rebalance_every_bar: bool = False,
        apply_costs: bool = True,
    ) -> BacktestEngineMetrics:
        """
        Event-driven engine: a per-bar Python loop with an explicit pending-order
        queue, a signed cash ledger, and a slipped fill price.

        A target decided at the close of bar ``t`` is submitted there and fills at
        the close of bar ``t + execution_lag_bars``. ``execution_lag_bars=0``
        reproduces the vectorized engine's instant-fill assumption, which is how the
        two are held to bar-for-bar agreement in the tests; the default of 1 matches
        ``backtesting.py``'s documented next-bar default. An order still pending when
        the series ends never fills, which is the honest outcome rather than a
        retroactive fill.

        Cash is debited on a buy and **credited** on a sell. an earlier loop
        debited both (``cash -= cost + comm`` regardless of direction), so every exit
        and every reversal destroyed equity: a profitable long/short sample series
        reported -0.42%.

        Positions are sized to ``target_weight * current_equity`` in fractional
        units. an earlier loop held ``signals[t]`` *shares* -- one share against
        $100,000 -- so its returns were three orders of magnitude smaller than the
        vectorized engine's, for reasons that had nothing to do with execution.

        ``rebalance_every_bar=False`` (the default) lets the weight drift with price
        between signal changes, which is what a real book does. The vectorized
        ``w * r`` product instead assumes continuous rebalancing to the target
        weight. The two coincide for fully-invested long/flat signals and diverge for
        shorts and leverage; set ``rebalance_every_bar=True`` to match the vectorized
        formulation exactly.
        """
        if execution_lag_bars < 0:
            raise ValueError(f"execution_lag_bars must be non-negative, got {execution_lag_bars}")
        p, s = _validate_series(prices, signals, self.max_abs_exposure)

        commission = self.commission_rate if apply_costs else 0.0
        slippage = self.slippage_rate if apply_costs else 0.0
        n = p.size

        t_start = time.perf_counter()
        cash = self.initial_capital
        units = 0.0
        active_target = 0.0
        submitted_target = 0.0
        trade_count = 0
        total_turnover = 0.0
        pending: Dict[int, float] = {}
        equity_curve = np.empty(n, dtype=float)

        for t in range(n):
            px = float(p[t])

            # 1. Decide at this bar's close, using only data up to and including bar t.
            target = float(s[t])
            if target != submitted_target:
                submitted_target = target
                pending[t + execution_lag_bars] = target

            # 2. Fill whatever was scheduled to execute at this bar.
            due = pending.pop(t, None)
            if due is not None:
                active_target = due
            if due is not None or rebalance_every_bar:
                equity_now = cash + units * px
                # Size on the price actually paid, not on the mark. Sizing on the
                # mark deploys target * equity at a slipped price, which buys
                # target * (1 + slippage) of exposure -- the engine would miss its
                # own target weight by the slippage rate and quietly run levered.
                provisional_units = active_target * equity_now / px
                direction = (
                    1.0 if provisional_units > units
                    else (-1.0 if provisional_units < units else 0.0)
                )
                if direction != 0.0:
                    fill_px = px * (1.0 + direction * slippage)
                    target_units = active_target * equity_now / fill_px
                    delta = target_units - units
                    # The slippage adjustment can be larger than the gap it is
                    # correcting, which would mean crossing the spread to trade in
                    # the opposite direction from the one priced. Hold instead.
                    if delta * direction > 0.0:
                        cash -= delta * fill_px                   # signed: a sell credits cash
                        cash -= abs(delta) * fill_px * commission
                        if equity_now != 0.0:
                            total_turnover += abs(delta) * px / equity_now
                        units = target_units
                        trade_count += 1

            # 3. Mark to market at this bar's close.
            equity_curve[t] = cash + units * px

        t_end = time.perf_counter()

        if pending:
            logger.debug(
                "%d order(s) were still pending when the series ended and never filled "
                "(execution_lag_bars=%d).", len(pending), execution_lag_bars,
            )
        # Prefixed with the opening equity so bar 0 -- which can already carry an
        # entry cost -- contributes a return, matching the vectorized engine's curve.
        equity_curve = np.concatenate(([self.initial_capital], equity_curve))
        self._warn_on_ruin(equity_curve, "Event-driven")

        period_returns = equity_curve[1:] / equity_curve[:-1] - 1.0
        period_returns = period_returns[np.isfinite(period_returns)]

        return BacktestEngineMetrics(
            total_return_pct=round(float(equity_curve[-1] / self.initial_capital - 1.0) * 100.0, 6),
            sharpe_ratio=round(
                annualized_sharpe(period_returns, self.periods_per_year, self.risk_free_rate), 4
            ),
            trades_count=trade_count,
            execution_time_sec=t_end - t_start,
            total_turnover=round(total_turnover, 6),
            equity_curve=equity_curve,
        )

    # -------------------------------------------------------------------- audit

    def compare_engines(
        self,
        prices: Sequence[float],
        signals: Sequence[float],
        execution_lag_bars: int = 1,
        rebalance_every_bar: bool = False,
    ) -> DualEngineAuditReport:
        """
        Runs the frictionless, vectorized and event-driven curves on one signal
        series and attributes the gap between them.

        ``cost_drag_pct`` is frictionless minus vectorized: transaction costs alone,
        with the fill assumption held constant. ``return_drag_pct`` is vectorized
        minus event-driven: fill latency and slippage alone, with costs held
        constant. Both are in percentage points of total return, not ratios.

        ``speedup_factor`` is a measurement, and is ``None`` below
        ``MIN_BARS_FOR_TIMING`` bars because timing a sub-millisecond workload
        measures the clock, not the engines. Even above it, one run on one machine
        is indicative rather than a benchmark.
        """
        frictionless = self.run_vectorized_backtest(prices, signals, apply_costs=False)
        vectorized = self.run_vectorized_backtest(prices, signals, apply_costs=True)
        event_driven = self.run_event_driven_backtest(
            prices,
            signals,
            execution_lag_bars=execution_lag_bars,
            rebalance_every_bar=rebalance_every_bar,
        )

        n_bars = len(prices)
        speedup: Optional[float] = None
        if n_bars >= MIN_BARS_FOR_TIMING and vectorized.execution_time_sec > 0.0:
            speedup = round(event_driven.execution_time_sec / vectorized.execution_time_sec, 2)

        sharpe_divergence = round(vectorized.sharpe_ratio - event_driven.sharpe_ratio, 4)
        return_drag = round(vectorized.total_return_pct - event_driven.total_return_pct, 6)
        cost_drag = round(frictionless.total_return_pct - vectorized.total_return_pct, 6)
        total_drag = round(frictionless.total_return_pct - event_driven.total_return_pct, 6)

        speed_text = (
            f"{speedup:.1f}x measured"
            if speedup is not None
            else f"not measured (<{MIN_BARS_FOR_TIMING} bars)"
        )
        summary = (
            f"Dual Engine Audit over {n_bars} bars, turnover {vectorized.total_turnover:.2f}: "
            f"frictionless {frictionless.total_return_pct:+.2f}%, "
            f"vectorized {vectorized.total_return_pct:+.2f}%, "
            f"event-driven ({execution_lag_bars}-bar lag) {event_driven.total_return_pct:+.2f}%. "
            f"Cost drag {cost_drag:+.2f}pp, latency drag {return_drag:+.2f}pp, "
            f"total {total_drag:+.2f}pp. Vector speedup: {speed_text}."
        )
        logger.info(summary)

        return DualEngineAuditReport(
            vectorized_metrics=vectorized,
            event_driven_metrics=event_driven,
            sharpe_divergence=sharpe_divergence,
            return_drag_pct=return_drag,
            speedup_factor=speedup,
            summary=summary,
            frictionless_metrics=frictionless,
            cost_drag_pct=cost_drag,
            total_drag_pct=total_drag,
        )

    # ------------------------------------------------------------------ internal

    @staticmethod
    def _warn_on_ruin(equity_curve: np.ndarray, engine_name: str) -> None:
        """
        Flags an equity curve that reached zero or went negative. Past that bar the
        account would have been liquidated, so percentage returns computed off it are
        arithmetic rather than results.
        """
        ruined = equity_curve <= 0.0
        if bool(np.any(ruined)):
            first = int(np.argmax(ruined))
            logger.warning(
                "%s equity reached %.2f at bar %d; the account would have been liquidated. "
                "Metrics from that bar onward are not meaningful.",
                engine_name, float(equity_curve[first]), first,
            )

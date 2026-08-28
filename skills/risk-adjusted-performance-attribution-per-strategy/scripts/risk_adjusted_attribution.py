"""
risk-adjusted-performance-attribution-per-strategy: per-strategy risk-adjusted
performance metrics (Sharpe, Sortino, Calmar, max drawdown) plus a covariance-based
Euler decomposition of portfolio risk across strategies.

Metric definitions (each verified against the primary source cited):

- **Sharpe ratio** (Sharpe, "The Sharpe Ratio", Journal of Portfolio Management,
  Fall 1994): mean differential return divided by the standard deviation of the
  differential return. Because this engine takes a *constant* annual risk-free rate,
  subtracting it does not change the standard deviation, so the volatility of raw
  returns is used directly.

- **Sortino ratio** (Sortino & Price 1994; see CFA Institute, Kidd, "The Sortino
  Ratio: Is Downside Risk the Only Risk that Matters?", 2012): (mean return - MAR)
  divided by downside deviation, where the squared shortfalls below the MAR are
  averaged over **all** observations, not only the observations below the MAR. That
  convention is deliberate and is what makes a strategy that rarely breaches the MAR
  score well; dividing by the count of losing periods only is a common miscalculation.

- **Calmar ratio** (Young, "Calmar Ratio: A Smoother Tool", Futures, October 1991):
  compound annualized return divided by the absolute maximum drawdown. Young's
  convention is a trailing **36-month** window evaluated monthly; this engine applies
  the formula to whatever window it is given, so a Calmar computed here over a short
  window is NOT comparable to a published 36-month Calmar.

- **Risk contribution** (Euler decomposition; Zivot, *Introduction to Computational
  Finance and Financial Econometrics*, Eq. 14.8-14.9): portfolio volatility is
  homogeneous of degree one in the weights, so

      MCR_i = (Sigma w)_i / sigma_p          (marginal contribution)
      CR_i  = w_i * MCR_i                    (component contribution)
      sum_i CR_i = sigma_p                   (Euler's theorem)

  `risk_contribution_pct` reports CR_i / sigma_p, which sums to 100%.

Why the Euler decomposition rather than a share of weighted volatilities: the naive
w_i * sigma_i / sum_j(w_j * sigma_j) ignores correlation and is only correct when every
pairwise correlation is +1. For a portfolio containing a hedge it is not merely
imprecise, it is directionally wrong -- it assigns a large positive risk contribution to
a strategy that is *removing* risk. A perfectly hedged pair has zero portfolio
volatility, and the naive formula still reports 50%/50%. The naive number is retained
under the honest name `standalone_volatility_share_pct` because it is a useful gross
measure of standalone scale, but it is not a risk attribution.

Limitations (documented, deliberate):

- **Backward-looking and sample-dependent.** Every figure describes the window
  supplied. Annualizing a short window produces an arithmetically correct but
  statistically meaningless number; see `insufficient_history_warning`.
- **Downside deviation is annualized from discrete historical returns.** Sortino &
  Forsey (1996) note that the discrete method understates downside risk when most
  returns are positive, and that annualizing discrete data overstates risk. The
  Sortino ratio here is therefore an estimate from realized history, not a forecast.
- **Volatility is annualized by sqrt(252).** Sharpe (1994) notes this requires zero
  serial correlation in the differential returns; compounding makes the exact
  relationship more complicated. Returns are annualized geometrically, volatility by
  the square-root-of-time rule -- the standard convention, and an approximation.
- **No timestamps.** Return series are aligned by position only, so all series must
  be the same length and represent the same periods in the same order. The engine
  cannot detect a misalignment that is merely wrong rather than ragged.
- **Risk contributions may be negative.** A diversifying strategy legitimately
  contributes negative risk. The percentages sum to 100% but are not all positive.
"""
import logging
import math
import statistics
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Trading days per year used for annualization of daily return series.
TRADING_DAYS_PER_YEAR = 252

#: Below this many observations the engine flags the report rather than silently
#: presenting annualized statistics from a small sample. One trading year is used as
#: the reference point; GIPS 2020 requires the 3-year annualized ex-post standard
#: deviation for firms claiming compliance, and requires disclosure when 36 monthly
#: returns are unavailable. That standard governs firms presenting composites to
#: prospective clients, not internal attribution, so this is a warning threshold and
#: not a compliance gate.
DEFAULT_MIN_RECOMMENDED_OBSERVATIONS = TRADING_DAYS_PER_YEAR

#: Portfolio variance at or below this is treated as numerically indistinguishable
#: from zero, making the Euler decomposition (which divides by sigma_p) undefined.
_VARIANCE_EPSILON = 1e-18


@dataclass
class StrategyReturns:
    strategy_id: str
    daily_returns: List[float]                        # simple periodic returns, e.g. [0.005, -0.002]
    risk_free_rate_annual: Optional[float] = None     # None => use the engine's rate


@dataclass
class RiskAdjustedMetrics:
    strategy_id: str
    total_return: float                          # compounded: prod(1 + r) - 1
    annualized_return: float
    annualized_volatility: float
    max_drawdown: float                          # positive fraction of peak equity
    observations: int
    # Ratios are None when mathematically undefined rather than 0.0, because 0.0 is a
    # legitimate (mediocre) score and would rank an undefined-but-excellent strategy
    # below a merely average one.
    sharpe_ratio: Optional[float] = None
    sortino_ratio: Optional[float] = None
    calmar_ratio: Optional[float] = None
    undefined_ratios: Tuple[str, ...] = ()       # e.g. ('calmar: no drawdown observed',)
    # Euler component contribution CR_i / sigma_p, in percent. Sums to 100% across
    # strategies. MAY BE NEGATIVE for a diversifying strategy. None when portfolio
    # volatility is zero, which makes the decomposition undefined.
    risk_contribution_pct: Optional[float] = None
    # Naive w_i * sigma_i share of summed weighted volatility. Correlation-blind; a
    # measure of standalone scale, NOT a risk attribution. Always non-negative.
    standalone_volatility_share_pct: float = 0.0


@dataclass
class PortfolioAttributionReport:
    total_portfolio_return: float
    total_portfolio_volatility: float
    portfolio_sharpe: Optional[float]
    strategy_attributions: List[RiskAdjustedMetrics]
    status: str                                  # 'ATTRIBUTION_COMPLETE'
    audit_notes: str
    observations: int = 0
    insufficient_history_warning: bool = False   # sample too short for annualized stats
    risk_decomposition_available: bool = True    # False when portfolio volatility is zero


class RiskAdjustedPerformanceAttributionEngine:
    """
    Computes per-strategy risk-adjusted metrics (Sharpe, Sortino, Calmar, max drawdown)
    and decomposes portfolio volatility across strategies via the Euler/marginal
    risk-contribution identity.

    All inputs are simple periodic returns aligned by position. See the module
    docstring for metric definitions, sources, and limitations.
    """

    TRADING_DAYS = TRADING_DAYS_PER_YEAR

    def __init__(
        self,
        risk_free_rate_annual: float = 0.05,
        min_recommended_observations: int = DEFAULT_MIN_RECOMMENDED_OBSERVATIONS,
    ) -> None:
        if not math.isfinite(risk_free_rate_annual):
            raise ValueError("risk_free_rate_annual must be finite.")
        if risk_free_rate_annual <= -1.0:
            raise ValueError("risk_free_rate_annual must be greater than -1.0 (-100%).")
        if min_recommended_observations < 2:
            raise ValueError("min_recommended_observations must be at least 2.")
        self.risk_free_rate_annual = risk_free_rate_annual
        self.min_recommended_observations = min_recommended_observations

    # ---------------------------------------------------------------- validation

    @staticmethod
    def _validate_returns(strategy_id: str, daily_returns: Sequence[float]) -> None:
        """
        Rejects return series the metrics cannot describe honestly.

        A return of exactly -1.0 wipes the equity to zero; below -1.0 drives it
        negative, at which point compounding to an annualized figure raises a negative
        base to a fractional power and yields a complex number. Both are rejected at
        the boundary rather than allowed to produce a nonsense drawdown above 100%.
        """
        if len(daily_returns) < 2:
            raise ValueError(
                f"Strategy '{strategy_id}': at least 2 return observations are required "
                f"to estimate volatility, got {len(daily_returns)}."
            )
        for idx, r in enumerate(daily_returns):
            if isinstance(r, bool) or not isinstance(r, (int, float)):
                raise TypeError(
                    f"Strategy '{strategy_id}': return at index {idx} is not numeric ({r!r})."
                )
            if not math.isfinite(r):
                raise ValueError(
                    f"Strategy '{strategy_id}': non-finite return at index {idx} ({r!r}). "
                    "NaN/Inf must be resolved upstream, not silently propagated into metrics."
                )
            if r <= -1.0:
                raise ValueError(
                    f"Strategy '{strategy_id}': return at index {idx} is {r!r}, which implies "
                    "total or negative equity. Simple periodic returns must be > -1.0."
                )

    @staticmethod
    def _validate_weights(weights: Sequence[float], n: int) -> None:
        if len(weights) != n:
            raise ValueError(
                f"weights has length {len(weights)} but {n} strategies were supplied."
            )
        for idx, w in enumerate(weights):
            if not math.isfinite(w):
                raise ValueError(f"weights[{idx}] is not finite ({w!r}).")
        total = sum(weights)
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            raise ValueError(
                f"weights must sum to 1.0 (got {total!r}). Normalize before calling; the "
                "engine will not rescale silently, because a mis-scaled weight vector "
                "changes both the portfolio return and every risk contribution."
            )

    # ------------------------------------------------------------------ metrics

    def _risk_free_for(self, strategy: StrategyReturns) -> float:
        rf = (
            strategy.risk_free_rate_annual
            if strategy.risk_free_rate_annual is not None
            else self.risk_free_rate_annual
        )
        if not math.isfinite(rf) or rf <= -1.0:
            raise ValueError(
                f"Strategy '{strategy.strategy_id}': risk_free_rate_annual must be finite "
                f"and greater than -1.0 (got {rf!r})."
            )
        return rf

    def _daily_risk_free(self, annual_rate: float) -> float:
        """
        De-annualizes geometrically: (1 + rf)^(1/252) - 1.

        Returns compound, so the risk-free rate must be de-annualized the same way.
        rf/252 is the arithmetic shortcut and overstates the daily rate (at 5%:
        0.00019841 vs 0.00019363, ~2.5% too high).
        """
        return (1.0 + annual_rate) ** (1.0 / self.TRADING_DAYS) - 1.0

    def _annualized_return(self, daily_returns: Sequence[float]) -> float:
        """Geometric (compound) annualized return. Callers must pre-validate r > -1."""
        n_days = len(daily_returns)
        if n_days == 0:
            return 0.0
        cumulative = 1.0
        for r in daily_returns:
            cumulative *= (1.0 + r)
        return cumulative ** (self.TRADING_DAYS / n_days) - 1.0

    @staticmethod
    def _total_return(daily_returns: Sequence[float]) -> float:
        """
        Compounded total return, prod(1 + r) - 1.

        Not sum(r): the arithmetic sum is not a return the portfolio ever earned and is
        inconsistent with the geometric annualized figure. For [-0.5, +1.0] the sum says
        +50% while the investor is exactly flat.
        """
        cumulative = 1.0
        for r in daily_returns:
            cumulative *= (1.0 + r)
        return cumulative - 1.0

    def _annualized_volatility(self, daily_returns: Sequence[float]) -> float:
        if len(daily_returns) < 2:
            return 0.0
        return statistics.stdev(daily_returns) * math.sqrt(self.TRADING_DAYS)

    @staticmethod
    def _sharpe_ratio(ann_return: float, ann_vol: float, rf_annual: float) -> Optional[float]:
        """None when volatility is zero: the ratio diverges, it is not 0.0."""
        if ann_vol <= 0.0:
            return None
        return (ann_return - rf_annual) / ann_vol

    def _downside_deviation(self, daily_returns: Sequence[float], rf_daily: float) -> float:
        """
        Annualized downside deviation about the MAR (here, the risk-free rate).

        Squared shortfalls are averaged over ALL observations (CFA Institute / Kidd
        2012), not only the periods below the MAR.
        """
        if not daily_returns:
            return 0.0
        shortfalls = [min(r - rf_daily, 0.0) ** 2 for r in daily_returns]
        return math.sqrt(sum(shortfalls) / len(shortfalls)) * math.sqrt(self.TRADING_DAYS)

    @staticmethod
    def _sortino_ratio(
        downside_dev: float, ann_return: float, rf_annual: float
    ) -> Optional[float]:
        """None when no observation fell below the MAR: undefined, not 0.0."""
        if downside_dev <= 0.0:
            return None
        return (ann_return - rf_annual) / downside_dev

    @staticmethod
    def _max_drawdown(daily_returns: Sequence[float]) -> float:
        cumulative = 1.0
        peak = 1.0
        max_dd = 0.0
        for r in daily_returns:
            cumulative *= (1.0 + r)
            peak = max(peak, cumulative)
            max_dd = max(max_dd, (peak - cumulative) / peak)
        return max_dd

    @staticmethod
    def _calmar_ratio(ann_return: float, max_dd: float) -> Optional[float]:
        """
        None when no drawdown was observed: the ratio diverges, it is not 0.0.

        Returning 0.0 here was a ranking inversion -- a strategy that never drew down
        scored below one that did.
        """
        if max_dd <= 0.0:
            return None
        return ann_return / max_dd

    # ------------------------------------------------------- risk decomposition

    @staticmethod
    def _covariance_matrix(series: Sequence[Sequence[float]]) -> List[List[float]]:
        """Sample covariance matrix (n-1 denominator) of equal-length return series."""
        n = len(series)
        means = [statistics.fmean(s) for s in series]
        length = len(series[0])
        cov = [[0.0] * n for _ in range(n)]
        for i in range(n):
            for j in range(i, n):
                acc = sum(
                    (series[i][k] - means[i]) * (series[j][k] - means[j])
                    for k in range(length)
                )
                value = acc / (length - 1)
                cov[i][j] = value
                cov[j][i] = value
        return cov

    # --------------------------------------------------------------- public API

    def compute_strategy_attribution(
        self,
        strategies: List[StrategyReturns],
        weights: Optional[List[float]] = None,
    ) -> PortfolioAttributionReport:
        """
        Computes risk-adjusted metrics per strategy and the Euler decomposition of
        portfolio volatility across strategies.

        Args:
            strategies: strategies with equal-length, position-aligned simple return
                series. Series must cover the same periods in the same order; the
                engine has no timestamps and cannot verify this beyond length.
            weights: portfolio weights summing to 1.0. Defaults to equal weight.

        Returns:
            A `PortfolioAttributionReport`. Ratios that are mathematically undefined
            are `None` with the reason recorded in `undefined_ratios`, never 0.0.

        Raises:
            ValueError: on an empty strategy list, ragged series, fewer than 2
                observations, non-finite or <= -1.0 returns, or weights that do not
                match the strategy count or do not sum to 1.0.
            TypeError: on a non-numeric return value.
        """
        n = len(strategies)
        if n == 0:
            raise ValueError("At least 1 strategy is required for performance attribution.")

        for strat in strategies:
            self._validate_returns(strat.strategy_id, strat.daily_returns)

        lengths = {len(s.daily_returns) for s in strategies}
        if len(lengths) > 1:
            detail = ", ".join(
                f"{s.strategy_id}={len(s.daily_returns)}" for s in strategies
            )
            raise ValueError(
                "All strategies must supply the same number of return observations "
                f"(got {detail}). Returns are aligned by position, so truncating to the "
                "shortest series would compare strategies over different periods and "
                "silently discard the most recent observations of the longer ones. "
                "Align the series on dates upstream."
            )
        observations = lengths.pop()

        if weights is None:
            weights = [1.0 / n] * n
        self._validate_weights(weights, n)

        attributions: List[RiskAdjustedMetrics] = []

        for strat in strategies:
            rf_annual = self._risk_free_for(strat)
            returns = strat.daily_returns

            ann_ret = self._annualized_return(returns)
            ann_vol = self._annualized_volatility(returns)
            max_dd = self._max_drawdown(returns)
            downside_dev = self._downside_deviation(returns, self._daily_risk_free(rf_annual))

            sharpe = self._sharpe_ratio(ann_ret, ann_vol, rf_annual)
            sortino = self._sortino_ratio(downside_dev, ann_ret, rf_annual)
            calmar = self._calmar_ratio(ann_ret, max_dd)

            undefined: List[str] = []
            if sharpe is None:
                undefined.append("sharpe: zero volatility")
            if sortino is None:
                undefined.append("sortino: no observation below the risk-free MAR")
            if calmar is None:
                undefined.append("calmar: no drawdown observed")

            attributions.append(RiskAdjustedMetrics(
                strategy_id=strat.strategy_id,
                total_return=round(self._total_return(returns), 6),
                annualized_return=round(ann_ret, 6),
                annualized_volatility=round(ann_vol, 6),
                max_drawdown=round(max_dd, 6),
                observations=observations,
                sharpe_ratio=None if sharpe is None else round(sharpe, 4),
                sortino_ratio=None if sortino is None else round(sortino, 4),
                calmar_ratio=None if calmar is None else round(calmar, 4),
                undefined_ratios=tuple(undefined),
            ))

        # Portfolio-level blended return series (positional alignment, equal lengths).
        portfolio_returns = [
            sum(weights[i] * strategies[i].daily_returns[k] for i in range(n))
            for k in range(observations)
        ]
        port_ann_ret = self._annualized_return(portfolio_returns)
        port_ann_vol = self._annualized_volatility(portfolio_returns)
        port_sharpe = self._sharpe_ratio(port_ann_ret, port_ann_vol, self.risk_free_rate_annual)

        # Euler risk decomposition: CR_i / sigma_p = w_i * (Sigma w)_i / sigma_p^2.
        cov = self._covariance_matrix([s.daily_returns for s in strategies])
        port_variance_daily = sum(
            weights[i] * weights[j] * cov[i][j] for i in range(n) for j in range(n)
        )
        risk_decomposition_available = port_variance_daily > _VARIANCE_EPSILON

        total_weighted_vol = sum(
            abs(weights[i]) * attributions[i].annualized_volatility for i in range(n)
        )
        for i, attr in enumerate(attributions):
            if total_weighted_vol > 0.0:
                weighted_vol = abs(weights[i]) * attr.annualized_volatility
                attr.standalone_volatility_share_pct = round(
                    (weighted_vol / total_weighted_vol) * 100.0, 2
                )
            if risk_decomposition_available:
                marginal = sum(cov[i][j] * weights[j] for j in range(n))
                attr.risk_contribution_pct = round(
                    (weights[i] * marginal / port_variance_daily) * 100.0, 2
                )

        insufficient_history = observations < self.min_recommended_observations

        notes = (
            f"RISK-ADJUSTED ATTRIBUTION [ATTRIBUTION_COMPLETE]: "
            f"Strategies = {n}, Observations = {observations}, "
            f"Portfolio Sharpe = {port_sharpe}, Portfolio Vol = {port_ann_vol:.4f}."
        )
        if not risk_decomposition_available:
            notes += (
                " Portfolio volatility is zero (fully offsetting strategies); "
                "Euler risk contributions are undefined and reported as None."
            )
            logger.warning(
                "Portfolio volatility is zero; risk decomposition undefined for %d strategies.", n
            )
        if insufficient_history:
            notes += (
                f" WARNING: {observations} observations is below the recommended "
                f"{self.min_recommended_observations}; annualized statistics from this "
                "sample carry wide error bars and should not be compared to full-year figures."
            )
            logger.warning(
                "Attribution computed from %d observations, below the recommended %d.",
                observations, self.min_recommended_observations,
            )
        logger.info(notes)

        return PortfolioAttributionReport(
            total_portfolio_return=round(port_ann_ret, 6),
            total_portfolio_volatility=round(port_ann_vol, 6),
            portfolio_sharpe=None if port_sharpe is None else round(port_sharpe, 4),
            strategy_attributions=attributions,
            status="ATTRIBUTION_COMPLETE",
            audit_notes=notes,
            observations=observations,
            insufficient_history_warning=insufficient_history,
            risk_decomposition_available=risk_decomposition_available,
        )

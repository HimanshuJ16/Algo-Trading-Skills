"""
multi-strategy-reporting-consolidation-for-stakeholders: consolidates sub-strategy
capital, PnL, and daily return series into a single portfolio-level report for fund
managers, risk committees, and LP investors.

The point of the engine is that portfolio-level risk metrics are *not* obtainable by
summing or averaging sub-strategy metrics. Volatility, Sharpe, and drawdown are all
recomputed from the synthesized joint return series:

    R_p,t   = sum_k w_k * R_k,t          with w_k = C_k / C_total
    sigma_p = stdev(R_p,t) * sqrt(F)     (sample stdev, n-1)
    SR_p    = (mean(R_p,t) * F - R_f) / sigma_p
    DR      = (sum_k w_k * sigma_k) / sigma_p

Definitions and sources:

- Sharpe ratio: Sharpe, W.F. (1994), "The Sharpe Ratio", Journal of Portfolio
  Management 21(1), 49-58 -- the ex post ratio is the mean *differential* return
  divided by the standard deviation of that differential return. With a constant
  risk-free rate the daily differential series has the same standard deviation as the
  raw series, so the annualized form used above is algebraically identical to
  annualizing the daily excess-return Sharpe by sqrt(F). Annualization is arithmetic
  (mean * F), not geometric, so SR_p is not derivable from a CAGR.
- Diversification ratio: Choueifaty, Y. and Coignard, Y. (2008), "Toward Maximum
  Diversification", Journal of Portfolio Management 35(1), 40-51, Eq. (1) -- the ratio
  of the weighted average of asset volatilities to the portfolio volatility. The paper
  defines it over long-only weights; DR >= 1 is guaranteed only when every weight is
  non-negative, which is why negative allocated capital is rejected here.

Limitations (documented, deliberate):

- **Gross unless the caller's inputs are already net.** The engine consolidates the
  PnL and returns it is given and applies no fee, carry, or transaction-cost model.
  If those figures are gross, the output is gross performance. SEC rule
  17 CFR 275.206(4)-1(d)(1) requires any presentation of gross performance in an
  advertisement to be accompanied by net performance with equal prominence, and GIPS
  2020 for Firms Provision 2.A.13 requires returns to be net of transaction costs.
- **sqrt(F) annualization assumes serially uncorrelated returns.** Lo, A.W. (2002),
  "The Statistics of Sharpe Ratios", Financial Analysts Journal 58(4), 36-52, shows
  the square-root-of-time rule holds only for IID returns and that a hedge fund's
  annual Sharpe ratio can be overstated by as much as 65% when monthly returns are
  serially correlated. Smoothed or illiquid marks -- common in the very sleeves this
  engine consolidates -- are exactly that case.
- **Annualizing a sub-year window is an extrapolation.** GIPS 2020 for Firms
  Provision 2.A.12: "Returns for periods of less than one year must not be
  annualized." A warning is emitted whenever the supplied window is shorter than
  ``trading_days_per_year``; the annualized figures are still computed, but they must
  not be presented as GIPS-compliant returns.
- **Returns must already be aligned by date.** ``daily_returns`` carries no
  timestamps, so the engine cannot align series itself. Unequal lengths are rejected
  rather than truncated, because truncating to the shortest series pairs a late-launch
  sleeve with the *oldest* observations of the others and silently corrupts every
  co-movement-dependent figure.
- **Weights are allocated-capital weights**, held fixed across the window. They are
  not drifting market-value weights, so the joint series is a rebalanced-to-allocation
  series, not a buy-and-hold one.
- **Undefined is reported as NaN, never as a number.** A zero-volatility window leaves
  the Sharpe ratio and the diversification ratio undefined, not large. Read
  ``warnings`` before quoting any figure.
"""
import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

logger = logging.getLogger(__name__)

#: Status emitted on a successful consolidation. Present regardless of whether
#: ``warnings`` is populated -- warnings qualify the numbers, they do not fail the run.
STATUS_CONSOLIDATED = "REPORT_CONSOLIDATED_SUCCESS"


class ConsolidationError(ValueError):
    """
    Raised when the supplied telemetry cannot produce a defensible report.

    Subclasses ``ValueError`` so callers already catching ``ValueError`` keep working.
    """


@dataclass
class StrategyTelemetry:
    strategy_id: str
    strategy_name: str
    allocated_capital_usd: float
    realized_pnl_usd: float
    unrealized_pnl_usd: float
    daily_returns: List[float]            # Daily decimal returns (e.g. 0.001 = 0.1%)
    max_drawdown_pct: float               # Sub-strategy max drawdown as reported upstream


@dataclass
class ReportingConfig:
    portfolio_name: str = "ALPHA_MULTI_STRAT_FUND"
    risk_free_rate_ann: float = 0.04     # 4% annual risk free rate
    trading_days_per_year: int = 252


@dataclass
class StrategyContribution:
    strategy_id: str
    strategy_name: str
    allocated_capital_usd: float
    weight_pct: float
    net_pnl_usd: float
    pnl_contribution_pct: float          # NaN when total portfolio PnL is exactly zero
    annualized_volatility_pct: float
    sharpe_ratio: float                  # NaN when the sleeve's window volatility is zero
    window_max_drawdown_pct: float = 0.0      # Recomputed from daily_returns over this window
    reported_max_drawdown_pct: float = 0.0    # Passed through from telemetry, window unknown


@dataclass
class ConsolidatedStakeholderReport:
    portfolio_name: str
    total_allocated_capital_usd: float
    total_net_pnl_usd: float
    portfolio_return_pct: float          # PnL / allocated capital, period return, NOT annualized
    portfolio_annualized_volatility_pct: float
    portfolio_sharpe_ratio: float        # NaN when portfolio window volatility is zero
    weighted_sum_volatility_pct: float
    diversification_ratio: float         # NaN when portfolio window volatility is zero
    strategy_contributions: List[StrategyContribution]
    status: str                          # 'REPORT_CONSOLIDATED_SUCCESS'
    audit_notes: str
    observations: int = 0                       # Length of the aligned return window
    series_implied_return_pct: float = 0.0      # Compounded joint series, reconcile against PnL
    portfolio_max_drawdown_pct: float = 0.0     # From the joint series, NOT a max/sum of sleeves
    max_strategy_max_drawdown_pct: float = 0.0  # Worst single sleeve over the same window
    warnings: List[str] = field(default_factory=list)


def _max_drawdown_pct(returns: Sequence[float]) -> float:
    """
    Peak-to-trough drawdown of the compounded equity path, as a positive percentage.

    Computed from the return series itself. A portfolio drawdown cannot be recovered
    from sub-strategy drawdowns, which trough on different dates.
    """
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= 1.0 + r
        if equity > peak:
            peak = equity
        drawdown = (peak - equity) / peak
        if drawdown > max_dd:
            max_dd = drawdown
    return max_dd * 100.0


def _compounded_return_pct(returns: Sequence[float]) -> float:
    """Geometric (compounded) return of the series, as a percentage."""
    equity = 1.0
    for r in returns:
        equity *= 1.0 + r
    return (equity - 1.0) * 100.0


class MultiStrategyReportingConsolidatorEngine:
    """
    Multi-strategy reporting consolidation engine aggregating sub-strategy PnL,
    computing portfolio-level Sharpe ratios, diversification ratios, and stakeholder
    attribution.

    Every risk figure is recomputed from the synthesized joint return series. No
    portfolio metric is derived by summing or averaging sub-strategy metrics.
    """

    def __init__(self, config: Optional[ReportingConfig] = None) -> None:
        self.config = config or ReportingConfig()
        if self.config.trading_days_per_year <= 0:
            raise ConsolidationError("trading_days_per_year must be a positive integer.")
        if not math.isfinite(self.config.risk_free_rate_ann):
            raise ConsolidationError("risk_free_rate_ann must be a finite number.")

    def _validate(self, strategies: List[StrategyTelemetry]) -> int:
        """
        Validates telemetry and returns the common return-series length.

        :raises ConsolidationError: on empty input, duplicate strategy ids, non-finite
            or negative capital, non-finite PnL, misaligned or too-short return series,
            or a daily return below -100%.
        """
        if not strategies:
            raise ConsolidationError("Strategy telemetry list cannot be empty.")

        seen_ids = set()
        for s in strategies:
            if s.strategy_id in seen_ids:
                raise ConsolidationError(
                    f"Duplicate strategy_id '{s.strategy_id}': the same sleeve would be "
                    "counted twice in capital, PnL, and weights."
                )
            seen_ids.add(s.strategy_id)

            if not math.isfinite(s.allocated_capital_usd) or s.allocated_capital_usd < 0.0:
                raise ConsolidationError(
                    f"[{s.strategy_id}] allocated_capital_usd must be finite and non-negative "
                    f"(got {s.allocated_capital_usd!r}). Negative capital produces a negative "
                    "weight, under which the diversification ratio is no longer bounded below "
                    "by 1.0 (Choueifaty & Coignard 2008 define it over long-only weights)."
                )

            for label, value in (
                ("realized_pnl_usd", s.realized_pnl_usd),
                ("unrealized_pnl_usd", s.unrealized_pnl_usd),
            ):
                if not math.isfinite(value):
                    raise ConsolidationError(
                        f"[{s.strategy_id}] {label} must be a finite number (got {value!r})."
                    )

            for i, r in enumerate(s.daily_returns):
                if not math.isfinite(r):
                    raise ConsolidationError(
                        f"[{s.strategy_id}] daily_returns[{i}] is not finite ({r!r}). "
                        "Non-finite returns propagate silently into every headline figure."
                    )
                if r < -1.0:
                    raise ConsolidationError(
                        f"[{s.strategy_id}] daily_returns[{i}] = {r!r} is below -100%. "
                        "The compounded equity path turns negative and drawdown becomes "
                        "meaningless; check whether the series holds decimals, not percent."
                    )

        lengths = {len(s.daily_returns) for s in strategies}
        if len(lengths) > 1:
            detail = ", ".join(f"{s.strategy_id}={len(s.daily_returns)}" for s in strategies)
            raise ConsolidationError(
                "Return series must be aligned by date and of equal length "
                f"({detail}). daily_returns carries no timestamps, so the engine cannot "
                "align them; truncating to the shortest series would pair a late-launch "
                "sleeve with the oldest observations of the others."
            )

        n = lengths.pop()
        if n < 2:
            raise ConsolidationError(
                "At least 2 return observations are required to compute a sample "
                f"standard deviation (got {n})."
            )

        if sum(s.allocated_capital_usd for s in strategies) <= 0:
            raise ConsolidationError("Total allocated capital must be positive.")

        return n

    def consolidate_reports(
        self, strategies: List[StrategyTelemetry]
    ) -> ConsolidatedStakeholderReport:
        """
        Synthesizes the daily joint portfolio return series and computes portfolio
        annualized volatility, Sharpe ratio, max drawdown, and diversification ratio.

        Metrics that are undefined for the supplied data are returned as ``float('nan')``
        with an entry in ``report.warnings``; they are never substituted with a
        placeholder volatility or a zero. Read ``warnings`` before quoting any figure.

        :raises ConsolidationError: see :meth:`_validate`.
        """
        observations = self._validate(strategies)
        warnings_out: List[str] = []

        total_capital = sum(s.allocated_capital_usd for s in strategies)
        total_pnl = sum(s.realized_pnl_usd + s.unrealized_pnl_usd for s in strategies)
        portfolio_return = (total_pnl / total_capital) * 100.0

        days_ann = self.config.trading_days_per_year
        if observations < days_ann:
            warnings_out.append(
                f"Window is {observations} observations, shorter than one year "
                f"({days_ann}). Annualized volatility and Sharpe are extrapolations; GIPS "
                "2020 for Firms Provision 2.A.12 prohibits annualizing returns for periods "
                "of less than one year in a GIPS report."
            )

        # 1. Synthesize the portfolio daily weighted return series.
        weights = [s.allocated_capital_usd / total_capital for s in strategies]
        portfolio_daily_returns: List[float] = [
            sum(weights[k] * strategies[k].daily_returns[t] for k in range(len(strategies)))
            for t in range(observations)
        ]

        # 2. Portfolio annualized metrics. A zero-volatility window leaves the Sharpe
        #    ratio and the diversification ratio undefined, not large.
        mean_p_daily = statistics.mean(portfolio_daily_returns)
        std_p_daily = statistics.stdev(portfolio_daily_returns)
        port_vol_ann = std_p_daily * math.sqrt(days_ann) * 100.0

        if std_p_daily == 0.0:
            port_sharpe = float("nan")
            warnings_out.append(
                "Portfolio volatility over this window is exactly zero (the weighted "
                "sleeves offset perfectly). The Sharpe ratio and the diversification ratio "
                "are undefined, not large, and are reported as NaN."
            )
        else:
            port_sharpe = (
                (mean_p_daily * days_ann) - self.config.risk_free_rate_ann
            ) / (std_p_daily * math.sqrt(days_ann))

        portfolio_max_dd = _max_drawdown_pct(portfolio_daily_returns)
        series_implied_return = _compounded_return_pct(portfolio_daily_returns)

        # 3. Individual sleeve metrics and the weighted sum of volatilities.
        contributions: List[StrategyContribution] = []
        sum_weighted_vols = 0.0
        max_strategy_dd = 0.0

        for k, s in enumerate(strategies):
            w = weights[k]
            net_pnl = s.realized_pnl_usd + s.unrealized_pnl_usd
            pnl_contrib = float("nan") if total_pnl == 0.0 else net_pnl / total_pnl * 100.0

            std_k_daily = statistics.stdev(s.daily_returns)
            strat_vol_ann = std_k_daily * math.sqrt(days_ann) * 100.0
            mean_k_daily = statistics.mean(s.daily_returns)

            if std_k_daily == 0.0:
                strat_sharpe = float("nan")
                warnings_out.append(
                    f"[{s.strategy_id}] volatility over this window is exactly zero; its "
                    "Sharpe ratio is undefined and reported as NaN."
                )
            else:
                strat_sharpe = (
                    (mean_k_daily * days_ann) - self.config.risk_free_rate_ann
                ) / (std_k_daily * math.sqrt(days_ann))

            sum_weighted_vols += w * strat_vol_ann
            window_dd = _max_drawdown_pct(s.daily_returns)
            max_strategy_dd = max(max_strategy_dd, window_dd)

            contributions.append(StrategyContribution(
                strategy_id=s.strategy_id,
                strategy_name=s.strategy_name,
                allocated_capital_usd=s.allocated_capital_usd,
                weight_pct=round(w * 100.0, 2),
                net_pnl_usd=round(net_pnl, 2),
                pnl_contribution_pct=round(pnl_contrib, 2),
                annualized_volatility_pct=round(strat_vol_ann, 2),
                sharpe_ratio=round(strat_sharpe, 2),
                window_max_drawdown_pct=round(window_dd, 2),
                reported_max_drawdown_pct=s.max_drawdown_pct,
            ))

        if total_pnl == 0.0:
            warnings_out.append(
                "Total portfolio PnL is exactly zero; per-sleeve PnL contribution shares "
                "are undefined and reported as NaN."
            )
        elif total_pnl < 0.0:
            warnings_out.append(
                "Total portfolio PnL is negative, so pnl_contribution_pct inverts sign: a "
                "profitable sleeve shows a negative share and a loss-making sleeve a "
                "positive one. Shares still sum to 100%."
            )

        # 4. Diversification ratio (Choueifaty & Coignard 2008, Eq. 1).
        diversification_ratio = (
            sum_weighted_vols / port_vol_ann if port_vol_ann > 0.0 else float("nan")
        )

        notes = (
            f"REPORT CONSOLIDATED SUCCESS [{self.config.portfolio_name}]: Total Capital = ${total_capital:,.2f}, "
            f"Total PnL = ${total_pnl:,.2f} ({portfolio_return:.2f}%). Portfolio Vol = {port_vol_ann:.2f}%, "
            f"Portfolio Sharpe = {port_sharpe:.2f}, Diversification Ratio = {diversification_ratio:.2f}x, "
            f"Portfolio Max DD = {portfolio_max_dd:.2f}% over {observations} observations, "
            f"{len(warnings_out)} warning(s)."
        )
        logger.info(notes)
        for msg in warnings_out:
            logger.warning("[%s] %s", self.config.portfolio_name, msg)

        return ConsolidatedStakeholderReport(
            portfolio_name=self.config.portfolio_name,
            total_allocated_capital_usd=round(total_capital, 2),
            total_net_pnl_usd=round(total_pnl, 2),
            portfolio_return_pct=round(portfolio_return, 2),
            portfolio_annualized_volatility_pct=round(port_vol_ann, 2),
            portfolio_sharpe_ratio=round(port_sharpe, 2),
            weighted_sum_volatility_pct=round(sum_weighted_vols, 2),
            diversification_ratio=round(diversification_ratio, 2),
            strategy_contributions=contributions,
            status=STATUS_CONSOLIDATED,
            audit_notes=notes,
            observations=observations,
            series_implied_return_pct=round(series_implied_return, 2),
            portfolio_max_drawdown_pct=round(portfolio_max_dd, 2),
            max_strategy_max_drawdown_pct=round(max_strategy_dd, 2),
            warnings=warnings_out,
        )

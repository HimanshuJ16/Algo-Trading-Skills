"""
backtest-reporting-standardized-tearsheet: standardized backtest performance tearsheet.

Conventions, stated explicitly because "standardized" is the whole point of the skill.
Two shops computing "Sharpe" or "Calmar" from the same returns will disagree unless
these choices match. See `references/standards.md` for sourcing.

  * Sharpe        arithmetic annualized excess return / annualized sample standard
                  deviation (ddof=1). Subtracting a constant per-period risk-free rate
                  does not change the standard deviation, so mean(R)*ppy - rf is exactly
                  the annualized mean of the per-period excess returns.
  * Sortino       same numerator, divided by annualized *target downside deviation*:
                  sqrt( mean_over_ALL_periods( min(0, R_t - MAR)^2 ) ), with MAR =
                  rf / ppy. The divisor is the total period count, not the count of
                  below-target periods. Dividing by only the losing periods is a common
                  error and inflates the ratio.
  * Calmar        CAGR / |max drawdown| by default, the more common convention. An
                  excess-return variant, (CAGR - rf) / |max drawdown|, also circulates;
                  select it with `calmar_uses_excess_return=True`. The report states
                  which one produced the number.
  * Drawdown      computed on an equity curve seeded at 1.0, so a decline that starts on
                  the first period counts. Reported as a negative fraction.
  * Degenerate    a zero denominator yields +/-inf or nan, never 0.0. Reporting 0.0 for
    denominators  an infinite Sharpe would present the best possible outcome as one of
                  the worst.

This module computes descriptive statistics from a return series. It does not judge a
strategy, correct for selection bias across many backtests, or make a backtest suitable
for distribution to investors -- see `SKILL.md` "When NOT to Use".
"""
import logging
import math
from typing import Any, Dict, Optional, Sequence, Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

#: Fewest observations for which a sample standard deviation (ddof=1) exists.
MIN_PERIODS = 2


class TearsheetError(ValueError):
    """Raised when the return series or the generator configuration is invalid."""


def _safe_ratio(numerator: float, denominator: float) -> float:
    """Divides, mapping a zero denominator to +/-inf or nan rather than 0.0.

    A zero denominator means the metric is degenerate, not bad: a strategy with zero
    volatility and a positive return has an unbounded Sharpe. Collapsing that to 0.0
    would rank a flawless equity curve alongside a worthless one.
    """
    if denominator == 0.0:
        if numerator == 0.0:
            return float("nan")
        return math.inf if numerator > 0 else -math.inf
    return numerator / denominator


class StandardizedTearsheetGenerator:
    def __init__(
        self,
        risk_free_rate: float = 0.0,
        periods_per_year: int = 252,
        calmar_uses_excess_return: bool = False,
    ) -> None:
        """
        Args:
            risk_free_rate: Annual risk-free rate as a decimal (0.04 for 4%), expressed
                on the same annualization basis as `periods_per_year`.
            periods_per_year: Observations per year in the return series. 252 for daily
                trading-day returns, 12 for monthly, 52 for weekly. Using 365 for a
                trading-day series overstates annualized volatility by about 20%.
            calmar_uses_excess_return: Selects the (CAGR - rf) / |maxDD| variant instead
                of the default CAGR / |maxDD|. Both conventions are in circulation; they
                coincide when the risk-free rate is zero.

        Raises:
            TearsheetError: On any invalid configuration.
        """
        if not isinstance(risk_free_rate, (int, float)) or isinstance(risk_free_rate, bool):
            raise TearsheetError(
                f"risk_free_rate must be a real number, got {type(risk_free_rate).__name__}."
            )
        if not math.isfinite(risk_free_rate):
            raise TearsheetError(f"risk_free_rate must be finite, got {risk_free_rate}.")
        if not isinstance(periods_per_year, (int, np.integer)) or isinstance(periods_per_year, bool):
            raise TearsheetError(
                f"periods_per_year must be an int, got {type(periods_per_year).__name__}."
            )
        if periods_per_year <= 0:
            raise TearsheetError(f"periods_per_year must be positive, got {periods_per_year}.")

        self.rf = float(risk_free_rate)
        self.ppy = int(periods_per_year)
        self.calmar_uses_excess_return = calmar_uses_excess_return

    def generate(self, returns: Union[np.ndarray, Sequence[float]]) -> Dict[str, Any]:
        """Computes the standardized tearsheet for a series of per-period returns.

        Args:
            returns: Simple (not log) per-period returns as decimals, in chronological
                order. 0.01 is +1%.

        Returns:
            A metric dictionary, or `{}` for an empty input. Every value is a float
            except `Max Drawdown Recovery Periods` (int or None), `Calmar Convention`
            (str) and `Annualization Extrapolated` (bool).

        Raises:
            TearsheetError: If the series is not one-dimensional, contains non-finite
                values, contains a return below -100%, or is too short for a sample
                standard deviation to exist.
        """
        r = self._validated_returns(returns)
        if r.size == 0:
            return {}

        n = r.size

        # Equity curve seeded at 1.0 so that a decline beginning on the first period is
        # measured from starting capital rather than from the first period's close.
        equity = np.concatenate((np.ones(1), np.cumprod(1.0 + r)))
        total_return = float(equity[-1] - 1.0)

        # Geometric annualized return (CAGR).
        ann_return = float(equity[-1] ** (self.ppy / n) - 1.0)

        # Arithmetic Annualized Return (for Sharpe/Sortino numerators)
        arithmetic_ann_return = float(np.mean(r) * self.ppy)
        excess_ann_return = arithmetic_ann_return - self.rf

        # Volatility: sample standard deviation, annualized by sqrt of period count.
        # A literally constant series has zero variance analytically, but np.std returns
        # accumulated rounding error of order 1e-18 for it, which turns an unbounded
        # Sharpe into a finite but absurd ~1e16. Test constancy exactly on the input
        # rather than comparing the derived statistic against an invented tolerance.
        is_constant = bool(np.all(r == r[0]))
        vol = 0.0 if is_constant else float(np.std(r, ddof=1) * np.sqrt(self.ppy))

        sharpe = _safe_ratio(excess_ann_return, vol)

        max_dd, dd_duration, dd_recovery = self._drawdown_stats(equity)

        # Sortino: target downside deviation against MAR = rf per period. The mean is
        # taken over all n periods, not only the below-target ones.
        per_period_mar = self.rf / self.ppy
        downside_diff = np.clip(r - per_period_mar, a_min=None, a_max=0.0)
        downside_vol = float(np.sqrt(np.mean(np.square(downside_diff))) * np.sqrt(self.ppy))
        sortino = _safe_ratio(excess_ann_return, downside_vol)

        calmar_numerator = (ann_return - self.rf) if self.calmar_uses_excess_return else ann_return
        calmar = _safe_ratio(calmar_numerator, abs(max_dd))

        # Per-period hit rate. This is NOT a per-trade win rate: a flat period counts in
        # the denominator but is not a win.
        wins_mask = r > 0
        losses_mask = r < 0
        wins = int(np.count_nonzero(wins_mask))
        hit_rate = wins / n

        gross_profits = float(np.sum(r[wins_mask]))
        gross_losses = float(np.abs(np.sum(r[losses_mask])))
        profit_factor = _safe_ratio(gross_profits, gross_losses)

        avg_win = float(np.mean(r[wins_mask])) if wins > 0 else 0.0
        avg_loss = float(np.mean(r[losses_mask])) if np.any(losses_mask) else 0.0

        extrapolated = n < self.ppy
        if extrapolated:
            logger.warning(
                "Annualized figures extrapolated from %d period(s), less than one year "
                "of %d. Annualized Return and Volatility are projections, not observed "
                "annual results.",
                n,
                self.ppy,
            )

        return {
            "Total Return": total_return,
            "Annualized Return": ann_return,
            "Annualized Volatility": vol,
            "Sharpe Ratio": sharpe,
            "Sortino Ratio": sortino,
            "Calmar Ratio": calmar,
            "Calmar Convention": (
                "(CAGR - rf) / |maxDD|" if self.calmar_uses_excess_return else "CAGR / |maxDD|"
            ),
            "Max Drawdown": max_dd,
            "Max Drawdown Duration": dd_duration,
            "Max Drawdown Recovery Periods": dd_recovery,
            "Hit Rate": hit_rate,
            "Profit Factor": profit_factor,
            "Average Win": avg_win,
            "Average Loss": avg_loss,
            "Periods": n,
            "Annualization Extrapolated": extrapolated,
        }

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _validated_returns(returns: Union[np.ndarray, Sequence[float]]) -> np.ndarray:
        """Coerces to a 1-D float array, rejecting anything that would silently corrupt.

        NaN is the dangerous case: it propagates through mean, std and min while leaving
        the count-based statistics (hit rate, profit factor) looking perfectly normal, so
        an unguarded series produces a report that is half plausible and half nan.
        """
        try:
            r = np.asarray(returns, dtype=float)
        except (TypeError, ValueError) as exc:
            raise TearsheetError(f"returns must be convertible to a float array: {exc}") from exc

        if r.ndim == 0:
            raise TearsheetError("returns must be a sequence, not a scalar.")
        if r.ndim != 1:
            raise TearsheetError(f"returns must be one-dimensional, got shape {r.shape}.")
        if r.size == 0:
            return r

        if not np.all(np.isfinite(r)):
            bad = int(np.count_nonzero(~np.isfinite(r)))
            raise TearsheetError(
                f"returns contains {bad} non-finite value(s). A NaN propagates through the "
                f"risk statistics while the count-based statistics stay plausible, producing "
                f"a report that looks partly valid. Resolve the gaps at source."
            )
        if np.any(r < -1.0):
            raise TearsheetError(
                "returns contains a value below -1.0 (worse than -100%). That drives the "
                "equity curve negative, for which compound annual return and drawdown are "
                "undefined. A return of exactly -1.0 is accepted and zeroes the curve."
            )
        if r.size < MIN_PERIODS:
            raise TearsheetError(
                f"returns has {r.size} observation(s); at least {MIN_PERIODS} are required for "
                f"a sample standard deviation. Annualizing a single observation yields a "
                f"meaningless figure (one +5% day compounds to over 20,000,000% per year)."
            )
        return r

    @staticmethod
    def _drawdown_stats(equity: np.ndarray) -> Tuple[float, int, Optional[int]]:
        """Returns (max drawdown, periods peak->trough, periods trough->recovery or None).

        `equity` must start at 1.0, so the running maximum is never below 1.0 and the
        drawdown denominator can be neither zero nor negative.
        """
        running_max = np.maximum.accumulate(equity)
        drawdowns = equity / running_max - 1.0
        trough_idx = int(np.argmin(drawdowns))
        max_dd = float(drawdowns[trough_idx])

        if max_dd == 0.0:
            return 0.0, 0, 0

        # Peak is the most recent point at or before the trough where equity was at its
        # running maximum, i.e. the last zero-drawdown point.
        peak_idx = int(np.max(np.flatnonzero(drawdowns[: trough_idx + 1] == 0.0)))
        duration = trough_idx - peak_idx

        peak_value = equity[peak_idx]
        after = np.flatnonzero(equity[trough_idx:] >= peak_value)
        recovery: Optional[int] = int(after[0]) if after.size > 0 else None

        return max_dd, duration, recovery

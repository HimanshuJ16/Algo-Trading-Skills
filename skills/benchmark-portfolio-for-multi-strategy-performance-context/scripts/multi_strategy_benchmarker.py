"""
benchmark-portfolio-for-multi-strategy-performance-context: Decomposes multi-strategy
portfolio returns against a policy benchmark into Beta (market exposure), Jensen's
Alpha (skill), Tracking Error, and the Information Ratio.

Conventions used throughout:
  * Returns are simple (arithmetic) per-period returns, not log returns.
  * Annualization is arithmetic: mean_daily * periods_per_year. This matches the
    linear CAPM/Jensen decomposition; it is NOT a compounded (geometric) return and
    will differ from a CAGR figure.
  * Tracking Error is annualized as std_daily * sqrt(periods_per_year), which assumes
    serially uncorrelated active returns. Autocorrelated active returns (common for
    illiquid or smoothed marks) make this understate true annual tracking error.
"""
import logging
import math
from typing import Any, Dict, List, Sequence, Union

import numpy as np

logger = logging.getLogger(__name__)

ArrayLike = Union[Sequence[float], np.ndarray]

# Numerical-noise floor on a *daily* standard deviation, used only to detect a series
# that is constant to within floating-point resolution. Deliberately far below any
# real return series: a daily sigma of 1e-12 is ~1e-10 basis points per day. A genuine
# low-volatility benchmark (e.g. a cash or short-duration index at ~1bp daily sigma,
# variance ~1e-8) is therefore NOT treated as degenerate.
_DEGENERATE_STD_TOL = 1e-12


class BenchmarkingError(ValueError):
    """Raised when the supplied return series cannot support a valid benchmark decomposition.

    Subclasses ValueError so that callers catching ValueError keep working.
    """


class MultiStrategyBenchmarker:
    """
    Evaluates a multi-strategy portfolio against a benchmark.
    Isolates Alpha (skill) from Beta (market exposure) and computes Tracking Error and Information Ratio.
    """

    def __init__(self, risk_free_rate_annual: float = 0.04, periods_per_year: int = 252) -> None:
        if not math.isfinite(risk_free_rate_annual):
            raise BenchmarkingError("risk_free_rate_annual must be a finite number.")
        try:
            ppy = int(periods_per_year)
        except (TypeError, ValueError, OverflowError) as exc:
            raise BenchmarkingError(
                f"periods_per_year must be a positive integer, got {periods_per_year!r}."
            ) from exc
        # Reject non-integral floats rather than silently truncating: 252.9 -> 252 would
        # quietly mis-annualize every metric.
        if ppy != periods_per_year or ppy <= 0:
            raise BenchmarkingError(
                f"periods_per_year must be a positive integer, got {periods_per_year!r}."
            )
        self.rf_annual = float(risk_free_rate_annual)
        self.ppy = ppy
        # Per-period risk-free rate. Exposed for callers that need the daily-frequency
        # form of the CAPM equation; the annualized alpha below is algebraically identical
        # to ppy * [(mean_p - rf_daily) - beta * (mean_b - rf_daily)].
        self.rf_daily = self.rf_annual / self.ppy

    def _validate_series(self, values: ArrayLike, label: str) -> np.ndarray:
        """Coerces to a 1D float array and rejects non-numeric, non-1D or non-finite input."""
        try:
            arr = np.asarray(values, dtype=float)
        except (TypeError, ValueError) as exc:
            raise BenchmarkingError(f"{label} must be numeric: {exc}") from exc

        if arr.ndim != 1:
            raise BenchmarkingError(
                f"{label} must be a 1D series of per-period returns, got {arr.ndim}D "
                f"with shape {arr.shape}."
            )
        if not np.all(np.isfinite(arr)):
            bad = int(np.count_nonzero(~np.isfinite(arr)))
            raise BenchmarkingError(
                f"{label} contains {bad} NaN/Inf value(s). Align and clean both series "
                "before benchmarking -- silently propagating NaN produces metrics that "
                "look numeric but are meaningless."
            )
        return arr

    def evaluate_performance(
        self, portfolio_returns: ArrayLike, benchmark_returns: ArrayLike
    ) -> Dict[str, Any]:
        """
        :param portfolio_returns: 1D array of daily portfolio returns.
        :param benchmark_returns: 1D array of daily benchmark returns over the exact same period.
        :return: Dictionary of benchmarking statistics. Metrics that are mathematically
                 undefined for the supplied data are returned as ``float('nan')`` and the
                 reason is appended to the ``"Warnings"`` list -- they are never reported
                 as ``0.0``, which would be indistinguishable from a genuine zero.
        :raises BenchmarkingError: on length mismatch, non-1D input, non-finite values, or
                 fewer than 2 observations.
        """
        port = self._validate_series(portfolio_returns, "portfolio_returns")
        bench = self._validate_series(benchmark_returns, "benchmark_returns")

        if port.size != bench.size:
            raise BenchmarkingError(
                f"Portfolio and benchmark return arrays must be the same length "
                f"({port.size} vs {bench.size}). Align both series by date first."
            )

        n = int(port.size)
        if n < 2:
            raise BenchmarkingError(
                f"At least 2 observations are required to compute a sample variance, got {n}."
            )

        warnings_out: List[str] = []
        years = n / self.ppy
        if years < 1.0:
            warnings_out.append(
                f"Sample covers only {years:.2f} year(s) ({n} periods). Annualized figures "
                "are extrapolations and Beta/Alpha are unlikely to be statistically significant."
            )

        port_std = float(np.std(port, ddof=1))
        bench_std = float(np.std(bench, ddof=1))

        # 1. Beta Calculation
        bench_var = bench_std ** 2
        if bench_std > _DEGENERATE_STD_TOL:
            beta = float(np.cov(port, bench)[0, 1] / bench_var)
        else:
            # A constant benchmark (e.g. a cash / absolute-return policy benchmark) has no
            # variation to load on, so Beta is not identified by the regression. We report
            # 0.0 by convention -- correct for a riskless benchmark, and it keeps Alpha
            # equal to the portfolio's excess return over the risk-free rate.
            beta = 0.0
            warnings_out.append(
                "Benchmark return series is constant, so Beta is not identified by the "
                "data; reported as 0.0 by convention (riskless-benchmark interpretation)."
            )

        # 2. Correlation
        if port_std > _DEGENERATE_STD_TOL and bench_std > _DEGENERATE_STD_TOL:
            corr = float(np.corrcoef(port, bench)[0, 1])
        else:
            corr = float("nan")
            warnings_out.append(
                "Correlation is undefined: at least one return series is constant."
            )

        # 3. Annualized Alpha (Jensen's Alpha)
        # alpha = (Rp - Rf) - Beta * (Rb - Rf), with arithmetic annualization.
        port_ann_return = float(np.mean(port)) * self.ppy
        bench_ann_return = float(np.mean(bench)) * self.ppy

        alpha_annual = (port_ann_return - self.rf_annual) - beta * (bench_ann_return - self.rf_annual)

        # 4. Tracking Error
        active_returns = port - bench
        active_std = float(np.std(active_returns, ddof=1))
        tracking_error_ann = active_std * math.sqrt(self.ppy)

        # 5. Information Ratio
        # IR = annualized active return / annualized tracking error
        active_return_ann = port_ann_return - bench_ann_return
        if active_std > _DEGENERATE_STD_TOL:
            ir = active_return_ann / tracking_error_ann
            # t-statistic of the mean active return under an i.i.d. assumption:
            # t = mean/(sd/sqrt(n)) = IR_annualized * sqrt(years).
            t_stat = ir * math.sqrt(years)
        else:
            ir = float("nan")
            t_stat = float("nan")
            if abs(active_return_ann) <= _DEGENERATE_STD_TOL * self.ppy:
                warnings_out.append(
                    "Information Ratio is undefined (0/0): the portfolio replicates the "
                    "benchmark exactly, so there is no active return to evaluate."
                )
            else:
                warnings_out.append(
                    f"Information Ratio is undefined: tracking error is zero with an annualized "
                    f"active return of {active_return_ann:+.4f}. A constant active return means "
                    "deterministic out/under-performance, not an absence of skill."
                )

        result: Dict[str, Any] = {
            "Beta": _round(beta),
            "Correlation": _round(corr),
            "Annualized Alpha": _round(alpha_annual),
            "Tracking Error (Ann)": _round(tracking_error_ann),
            "Information Ratio": _round(ir),
            "Active Return (Ann)": _round(active_return_ann),
            "Active Return t-stat": _round(t_stat),
            "Observations": n,
            "Years": _round(years),
            "Warnings": warnings_out,
        }

        logger.info(
            "Benchmark decomposition over %d periods (%.2f yr): Beta=%s, Alpha=%s, "
            "TE=%s, IR=%s, t=%s, warnings=%d",
            n, years, result["Beta"], result["Annualized Alpha"],
            result["Tracking Error (Ann)"], result["Information Ratio"],
            result["Active Return t-stat"], len(warnings_out),
        )
        for msg in warnings_out:
            logger.warning("Benchmark decomposition caveat: %s", msg)

        return result


def _round(value: float) -> float:
    """Rounds to 4dp for reporting, preserving NaN."""
    return float(value) if math.isnan(value) else float(round(value, 4))

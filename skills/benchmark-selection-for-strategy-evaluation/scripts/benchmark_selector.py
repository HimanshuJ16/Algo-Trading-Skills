"""
benchmark-selection-for-strategy-evaluation: statistical screening of candidate
benchmarks for a strategy return series.

What this measures
------------------
For each candidate benchmark it reports the *active return* statistics — active
return is `strategy - benchmark`, with no beta adjustment. Tracking error is the
annualised standard deviation of active returns; the information ratio is mean
annualised active return divided by tracking error.

Active return is **not** alpha. Alpha requires adjusting for the strategy's beta
exposure to the benchmark; `beta` and `r_squared` are reported so a mismatch is
visible, but this module does not compute a risk-adjusted alpha.

Ranking
-------
Candidates are ranked by ascending tracking error — the benchmark whose return
stream most closely replicates the strategy's. Ranking by correlation instead is
unsafe: correlation is scale-invariant, so a 3x-levered version of the strategy
scores a perfect 1.00 while being a terrible benchmark. See `references/standards.md`
for the Bailey (1992) benchmark-quality criterion this implements.

Statistical fit is a screen, not a decision. A benchmark must also be investable,
unambiguous, measurable, and specified in advance — properties this module cannot
observe. See `SKILL.md`.
"""
from dataclasses import dataclass
import logging
import math
from typing import Dict, List, Optional, Sequence

import numpy as np

logger = logging.getLogger(__name__)

# Annualised tracking error below this is numerical noise, not real active risk.
_TE_ZERO_TOL = 1e-12

# Correlation and a sample standard deviation both need at least two observations.
_MIN_OBSERVATIONS = 2


@dataclass
class BenchmarkMetrics:
    """
    Active-return statistics for one candidate benchmark.

    :param correlation: Pearson correlation of strategy and benchmark returns.
        `nan` when either series has zero variance (correlation is undefined,
        not zero — a flat risk-free series is the common case).
    :param tracking_error: Annualised standard deviation of active returns.
    :param information_ratio: Annualised mean active return / tracking error.
        `+/-inf` when tracking error is zero but active return is not (the
        strategy beats or trails the benchmark by a constant, with no active
        risk); `0.0` when both are zero.
    :param beta: Slope of strategy on benchmark, cov(s, b) / var(b). A benchmark
        that tracks the strategy's *shape* but not its scale shows beta far from
        1.0 — the failure correlation alone cannot detect. `nan` if var(b) == 0.
    :param r_squared: Fraction of strategy variance explained by the benchmark
        (correlation squared). `nan` when correlation is undefined.
    :param annualized_active_return: Mean active return x periods_per_year.
    """

    name: str
    correlation: float
    tracking_error: float
    information_ratio: float
    beta: float = float("nan")
    r_squared: float = float("nan")
    annualized_active_return: float = float("nan")


class BenchmarkSelector:
    """Screens candidate benchmarks by how closely they replicate a strategy."""

    def __init__(
        self,
        benchmark_returns: Dict[str, Sequence[float]],
        periods_per_year: int = 252,
        ddof: int = 1,
    ):
        """
        :param benchmark_returns: Mapping of benchmark name to its periodic return
            series. Every series must be the same length as the strategy series
            passed to `evaluate_benchmarks`, and aligned to the same dates.
        :param periods_per_year: Annualisation factor. 252 for daily equity data,
            12 for monthly, 52 for weekly, 365 for 24/7 crypto. Getting this wrong
            scales tracking error by sqrt(ratio) and the information ratio by
            sqrt(ratio) in the opposite direction.
        :param ddof: Delta degrees of freedom for the standard deviation. Default 1
            (sample), matching the performance-measurement convention of treating
            active risk as the sample standard deviation of active returns. Pass 0
            for the population convention. At n=10 the two differ by ~5%; the
            choice is a reporting convention, so state which one you used.
        """
        if periods_per_year <= 0:
            raise ValueError(f"periods_per_year must be positive, got {periods_per_year!r}.")
        if ddof not in (0, 1):
            raise ValueError(f"ddof must be 0 (population) or 1 (sample), got {ddof!r}.")

        self.benchmark_returns = benchmark_returns
        self.periods_per_year = periods_per_year
        self.ddof = ddof

    @staticmethod
    def _validate_series(values: Sequence[float], label: str, min_length: int) -> np.ndarray:
        """Coerce to a finite 1-D float array of at least `min_length`, or raise."""
        try:
            array = np.asarray(values, dtype=float)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be numeric array-like: {exc}") from exc
        if array.ndim != 1:
            raise ValueError(f"{label} must be 1-dimensional, got shape {array.shape}.")
        if array.size < min_length:
            raise ValueError(
                f"{label} needs at least {min_length} observations to compute "
                f"correlation and a sample standard deviation, got {array.size}."
            )
        if not np.all(np.isfinite(array)):
            bad = int(np.count_nonzero(~np.isfinite(array)))
            raise ValueError(
                f"{label} contains {bad} NaN/Inf value(s). Drop or impute them and "
                "re-align the series — a NaN here silently propagates into every metric."
            )
        return array

    def _information_ratio(self, annualized_active: float, tracking_error: float) -> float:
        """IR with explicit handling of the zero-active-risk case."""
        if tracking_error > _TE_ZERO_TOL:
            return annualized_active / tracking_error
        # Active risk is (numerically) zero: the strategy differs from the
        # benchmark by a constant. Returning 0.0 here would report a strategy
        # that beats its benchmark risk-free as merely average.
        if math.isclose(annualized_active, 0.0, abs_tol=_TE_ZERO_TOL):
            return 0.0
        return math.inf if annualized_active > 0 else -math.inf

    def evaluate_benchmarks(self, strategy_returns: Sequence[float]) -> List[BenchmarkMetrics]:
        """
        Compute active-return statistics for every candidate benchmark.

        :param strategy_returns: Periodic strategy returns, date-aligned with every
            benchmark series.
        :return: Metrics sorted by ascending tracking error — closest replicating
            benchmark first. Ties keep the insertion order of `benchmark_returns`.
        :raises ValueError: on non-finite values, fewer than 2 observations, a
            length mismatch, or non-numeric input.
        """
        s_ret = self._validate_series(strategy_returns, "strategy_returns", _MIN_OBSERVATIONS)

        results: List[BenchmarkMetrics] = []
        for name, b_ret_list in self.benchmark_returns.items():
            b_ret = self._validate_series(b_ret_list, f"benchmark {name!r}", _MIN_OBSERVATIONS)

            if s_ret.size != b_ret.size:
                raise ValueError(
                    f"Length mismatch for benchmark {name!r}: strategy has "
                    f"{s_ret.size} observations, benchmark has {b_ret.size}."
                )

            s_var = float(np.var(s_ret, ddof=self.ddof))
            b_var = float(np.var(b_ret, ddof=self.ddof))

            # Correlation and beta are undefined against a zero-variance series
            # (e.g. a flat risk-free rate). Report nan rather than inventing 0.0.
            if s_var > 0.0 and b_var > 0.0:
                correlation = float(np.corrcoef(s_ret, b_ret)[0, 1])
                r_squared = correlation ** 2
            else:
                correlation = float("nan")
                r_squared = float("nan")

            if b_var > 0.0:
                covariance = float(np.cov(s_ret, b_ret, ddof=self.ddof)[0, 1])
                beta = covariance / b_var
            else:
                beta = float("nan")

            active_returns = s_ret - b_ret
            tracking_error = float(
                np.std(active_returns, ddof=self.ddof) * math.sqrt(self.periods_per_year)
            )
            annualized_active = float(np.mean(active_returns) * self.periods_per_year)

            results.append(
                BenchmarkMetrics(
                    name=name,
                    correlation=correlation,
                    tracking_error=tracking_error,
                    information_ratio=self._information_ratio(annualized_active, tracking_error),
                    beta=beta,
                    r_squared=r_squared,
                    annualized_active_return=annualized_active,
                )
            )

        # Ascending tracking error: the benchmark that most closely replicates the
        # strategy ranks first. Tracking error is never nan after validation, so
        # this sort is total and deterministic.
        results.sort(key=lambda m: m.tracking_error)

        if results:
            best = results[0]
            logger.info(
                "Benchmark screen over %d candidate(s): best fit %r "
                "(TE=%.4f, beta=%.3f, corr=%.3f, IR=%.3f)",
                len(results), best.name, best.tracking_error,
                best.beta, best.correlation, best.information_ratio,
            )
        return results

    def recommend_benchmark(self, strategy_returns: Sequence[float]) -> Optional[str]:
        """
        Name of the closest-replicating candidate, or None if no candidates were given.

        This is a statistical screen only. Confirm the result is investable,
        unambiguous, measurable, and was specified in advance before reporting
        performance against it.
        """
        metrics = self.evaluate_benchmarks(strategy_returns)
        if not metrics:
            return None
        return metrics[0].name

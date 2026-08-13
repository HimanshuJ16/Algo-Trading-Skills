"""
benchmark-relative-performance-attribution: single-period CAPM alpha/beta attribution,
tracking error / information ratio, and Brinson-Fachler sector attribution.

Conventions this module commits to
----------------------------------
**Alpha is annualised arithmetically** as `daily_alpha * periods_per_year`, the
convention used when the intercept is reported as an annual rate. `pyfolio` /
`empyrical` instead compound it geometrically, `(1 + daily_alpha) ** periods - 1`,
so numbers from this module and from empyrical will not match exactly — they
diverge as alpha grows (5.00% arithmetic is 5.13% geometric at 252 periods).
State which convention a report uses.

**Tracking error and the information ratio use the sample standard deviation**
(`ddof=1`) of active returns, annualised by `sqrt(periods_per_year)`, and the
information ratio annualises the numerator too:
`IR = mean(active) * N / (stdev(active) * sqrt(N)) = IR_per_period * sqrt(N)`.
That sqrt-of-time scaling assumes active returns are serially independent; with
autocorrelated active returns it overstates the annualised IR.

**Brinson attribution here is single-period only.** Single-period allocation,
selection, and interaction effects are additive across sectors but *not* across
time — returns compound multiplicatively, effects add arithmetically, so summing
twelve monthly allocation effects does not give the annual allocation effect. Use
a linking method (Cariño, Menchero, Frongello, GRAP) to chain periods. This module
deliberately does not implement linking rather than implementing it implicitly and
wrongly. See `references/standards.md`.

**Brinson weights are start-of-period weights.** Using end-of-period weights
double-counts the return that produced them.
"""
from dataclasses import dataclass
import logging
import math
import statistics
from typing import Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

# statistics.variance / statistics.stdev need >= 2 points; 5 is the module's hard
# floor. It is a *numerical* floor, not a statistical one -- see
# _THIN_SAMPLE_OBSERVATIONS.
_MIN_OBSERVATIONS = 5

# Below this many observations a beta/alpha point estimate carries essentially no
# information. 30 is a conventional small-sample threshold, not a regulatory one;
# it only controls a warning, never behaviour.
_THIN_SAMPLE_OBSERVATIONS = 30

# Benchmark variance at or below this is treated as zero: beta is undefined, not 1.0.
_ZERO_VARIANCE_TOL = 1e-12

# Annualised tracking error at or below this is numerical noise, not active risk.
_TE_ZERO_TOL = 1e-12

# Portfolio and benchmark weight vectors must each sum to 1 within this tolerance,
# otherwise the Brinson effects do not reconcile to active return.
_WEIGHT_SUM_TOL = 1e-6

# Tolerance for the caller-supplied total benchmark return vs the one implied by
# the benchmark weights and sector returns.
_BENCHMARK_RETURN_TOL = 1e-9


class AttributionError(ValueError):
    """Raised on malformed attribution input or on a mathematically undefined metric."""
    pass


@dataclass
class AttributionSummary:
    """
    Benchmark-relative statistics for one return series pair.

    :param alpha_annualized: CAPM intercept, annualised arithmetically. See the
        module docstring -- this is not the empyrical/pyfolio convention.
    :param beta: cov(Rp, Rb) / var(Rb), sample (ddof=1). Never a fallback value:
        a zero-variance benchmark raises instead.
    :param tracking_error_annualized: Sample stdev of active returns * sqrt(N).
    :param information_ratio: Annualised active return / annualised tracking
        error. `+/-inf` when active risk is zero but active return is not (the
        portfolio beats or trails the benchmark by a constant every period);
        `0.0` when both are zero.
    :param active_return_annualized: mean(Rp - Rb) * N. Active return, *not* alpha
        -- it carries the beta mismatch that alpha strips out.
    :param is_alpha_positive: Sign test on the alpha point estimate only. It is
        not a significance test; read `information_ratio_t_stat` alongside it.
    :param observations: Number of periods the statistics were computed over.
    :param information_ratio_t_stat: t-statistic of the mean active return,
        which equals `information_ratio * sqrt(observations / periods_per_year)`
        -- i.e. IR times the square root of the sample length in years. |t| >= 1.96
        is the usual 95% two-sided threshold, so an IR of 0.5 needs roughly 15
        years of data to be distinguishable from zero. Assumes i.i.d. active
        returns; serial correlation inflates it.
    """

    alpha_annualized: float
    beta: float
    tracking_error_annualized: float
    information_ratio: float
    active_return_annualized: float
    is_alpha_positive: bool
    observations: int = 0
    information_ratio_t_stat: float = float("nan")


@dataclass
class BrinsonSectorResult:
    """
    Single-period Brinson-Fachler effects for one sector.

    `total_effect` is the sector's contribution to active return. Summing
    `total_effect` across all sectors reproduces (portfolio return - benchmark
    return) exactly, which `compute_brinson_attribution` asserts before returning.
    """

    sector: str
    allocation_effect: float
    selection_effect: float
    interaction_effect: float
    total_effect: float


class PerformanceAttributionEngine:
    """
    Decomposes single-period portfolio performance relative to a benchmark into
    alpha, beta, tracking error, information ratio, and Brinson-Fachler sector
    effects.

    Not for: multi-period Brinson linking, multi-factor (Fama-French) attribution
    -- see `strategy-performance-attribution-vs-market-beta` -- or benchmarks with
    no return variance.
    """

    def __init__(self, risk_free_rate: float = 0.0, annualization_factor: int = 252):
        """
        :param risk_free_rate: Annual risk-free rate as a decimal (0.02 = 2%).
            Converted to a per-period rate by simple division by
            `annualization_factor`, matching the arithmetic alpha convention.
        :param annualization_factor: Periods per year in the return series. 252 for
            daily equity data, 12 for monthly, 52 for weekly, 365 for 24/7 crypto.
            Getting this wrong scales tracking error by sqrt(ratio) and alpha
            linearly.
        :raises AttributionError: if the factor is not a positive integer or the
            risk-free rate is not finite.
        """
        if not isinstance(annualization_factor, int) or annualization_factor <= 0:
            raise AttributionError(
                f"annualization_factor must be a positive integer, got {annualization_factor!r}."
            )
        if not math.isfinite(risk_free_rate):
            raise AttributionError(f"risk_free_rate must be finite, got {risk_free_rate!r}.")

        self.risk_free_rate = risk_free_rate
        self.annualization_factor = annualization_factor

    @staticmethod
    def _validate_returns(values: Sequence[float], label: str) -> List[float]:
        """Coerce a return series to a finite list of floats, or raise."""
        try:
            series = [float(v) for v in values]
        except (TypeError, ValueError) as exc:
            raise AttributionError(f"{label} must contain only numbers: {exc}") from exc

        non_finite = sum(1 for v in series if not math.isfinite(v))
        if non_finite:
            raise AttributionError(
                f"{label} contains {non_finite} NaN/Inf value(s). Drop or impute them and "
                "re-align the series -- a NaN here propagates silently into beta, alpha, "
                "and the sign-off flags."
            )
        return series

    def evaluate_alpha_beta(
        self, portfolio_returns: Sequence[float], benchmark_returns: Sequence[float]
    ) -> AttributionSummary:
        """
        Compute beta, annualised CAPM alpha, tracking error, and information ratio.

        The two series must be date-aligned by the caller. Equal length is enforced;
        a one-period shift is not detectable here and silently contaminates every
        statistic.

        :param portfolio_returns: Periodic portfolio returns as decimals.
        :param benchmark_returns: Periodic benchmark returns, same length and dates.
        :return: An `AttributionSummary`.
        :raises AttributionError: on length mismatch, fewer than 5 observations,
            non-numeric or non-finite values, or a zero-variance benchmark (beta
            and therefore alpha are undefined against a constant benchmark -- use
            the active-return statistics from
            `benchmark-selection-for-strategy-evaluation` instead).
        """
        if len(portfolio_returns) != len(benchmark_returns):
            raise AttributionError(
                f"Length mismatch: portfolio returns ({len(portfolio_returns)}) vs "
                f"benchmark returns ({len(benchmark_returns)})."
            )

        p_ret = self._validate_returns(portfolio_returns, "portfolio_returns")
        b_ret = self._validate_returns(benchmark_returns, "benchmark_returns")

        n = len(p_ret)
        if n < _MIN_OBSERVATIONS:
            raise AttributionError(
                f"Insufficient return samples: {n}. Min {_MIN_OBSERVATIONS} required."
            )
        if n < _THIN_SAMPLE_OBSERVATIONS:
            logger.warning(
                "Attribution computed over only %d observations; beta and alpha point "
                "estimates from a sample this short are not statistically meaningful. "
                "Read information_ratio_t_stat before acting on them.",
                n,
            )

        mean_p = statistics.mean(p_ret)
        mean_b = statistics.mean(b_ret)

        var_b = statistics.variance(b_ret)
        if var_b <= _ZERO_VARIANCE_TOL:
            # Regressing on a constant leaves beta unidentified. Returning a
            # plausible-looking 1.0 would push an invented number straight into a
            # sign-off gate.
            raise AttributionError(
                "Benchmark return series has zero variance, so beta (and therefore "
                "CAPM alpha) is undefined. A flat benchmark -- a constant risk-free "
                "series is the usual case -- needs active-return statistics, not a "
                "beta-adjusted decomposition."
            )

        cov_pb = sum(
            (p_ret[i] - mean_p) * (b_ret[i] - mean_b) for i in range(n)
        ) / (n - 1)
        beta = cov_pb / var_b

        # CAPM: Rp - Rf = alpha + beta * (Rb - Rf). Arithmetic annualisation.
        rf_periodic = self.risk_free_rate / self.annualization_factor
        alpha_periodic = (mean_p - rf_periodic) - beta * (mean_b - rf_periodic)
        alpha_annualized = alpha_periodic * self.annualization_factor

        active_returns = [p_ret[i] - b_ret[i] for i in range(n)]
        mean_active = statistics.mean(active_returns)
        active_annualized = mean_active * self.annualization_factor

        te_annualized = statistics.stdev(active_returns) * math.sqrt(self.annualization_factor)

        information_ratio = self._information_ratio(active_annualized, te_annualized)
        t_stat = self._information_ratio_t_stat(information_ratio, n)

        logger.info(
            "Attribution over %d observations: alpha %.2f%%, beta %.2f, TE %.2f%%, IR %.2f (t=%.2f)",
            n,
            alpha_annualized * 100.0,
            beta,
            te_annualized * 100.0,
            information_ratio,
            t_stat,
        )

        return AttributionSummary(
            alpha_annualized=alpha_annualized,
            beta=beta,
            tracking_error_annualized=te_annualized,
            information_ratio=information_ratio,
            active_return_annualized=active_annualized,
            is_alpha_positive=alpha_annualized > 0.0,
            observations=n,
            information_ratio_t_stat=t_stat,
        )

    @staticmethod
    def _information_ratio(active_annualized: float, tracking_error: float) -> float:
        """IR with explicit handling of the zero-active-risk case."""
        if tracking_error > _TE_ZERO_TOL:
            return active_annualized / tracking_error
        # Zero active risk. Reporting 0.0 here would score a portfolio that beats
        # its benchmark by a constant every period as merely average.
        if math.isclose(active_annualized, 0.0, abs_tol=_TE_ZERO_TOL):
            return 0.0
        return math.inf if active_annualized > 0.0 else -math.inf

    def _information_ratio_t_stat(self, information_ratio: float, observations: int) -> float:
        """
        t-statistic of the mean active return: IR * sqrt(sample length in years).

        Derivation: t = sqrt(n) * mean(d) / stdev(d) and
        IR = sqrt(N) * mean(d) / stdev(d), so t = IR * sqrt(n / N).
        """
        if not math.isfinite(information_ratio):
            return information_ratio
        return information_ratio * math.sqrt(observations / self.annualization_factor)

    def compute_brinson_attribution(
        self,
        portfolio_weights: Dict[str, float],
        benchmark_weights: Dict[str, float],
        portfolio_sector_returns: Dict[str, float],
        benchmark_sector_returns: Dict[str, float],
        total_benchmark_return: Optional[float] = None,
    ) -> List[BrinsonSectorResult]:
        """
        Compute single-period Brinson-Fachler allocation, selection, and interaction
        effects per sector.

        Brinson-Fachler measures allocation against the *benchmark's own total
        return*, `(wp - wb) * (rb_sector - Rb)`, so overweighting a sector only
        scores positively when that sector beat the benchmark overall. The
        Brinson-Hood-Beebower variant uses `(wp - wb) * rb_sector` instead and can
        reward overweighting a sector that merely rose less than the benchmark.
        Selection and interaction are identical in both variants.

        Weights must be **start-of-period** weights, and the sector partition must
        be mutually exclusive and exhaustive -- both weight vectors are required to
        sum to 1.0. Without that, the effects do not reconcile to active return and
        the decomposition is meaningless; this method enforces it rather than
        returning numbers that silently fail to add up.

        Sectors held off-benchmark (`wb == 0`) are assigned a benchmark sector
        return of 0.0. That choice does not affect reconciliation (a zero benchmark
        weight cancels the term), but it does move value between the allocation and
        interaction effects, so state it when reporting.

        :param portfolio_weights: Start-of-period portfolio weight per sector.
        :param benchmark_weights: Start-of-period benchmark weight per sector.
        :param portfolio_sector_returns: Portfolio return within each sector held.
        :param benchmark_sector_returns: Benchmark return for each sector.
        :param total_benchmark_return: Optional override for the total benchmark
            return. Leave as `None` to derive it from the benchmark weights and
            sector returns, which is what Brinson-Fachler requires. If supplied it
            is validated against the derived value and a mismatch raises -- passing
            a compounded multi-period benchmark return here is a common way to get
            plausible-looking allocation effects that do not reconcile.
        :return: One `BrinsonSectorResult` per sector, sorted by sector name.
        :raises AttributionError: on non-finite inputs, weights that do not sum to
            1.0, a missing return for a sector carrying weight, or a supplied
            `total_benchmark_return` inconsistent with the benchmark inputs.
        """
        # Normalising to plain floats keeps every downstream computation in float64.
        # Passing numpy float32 weights straight through would leave the
        # reconciliation identity holding only to ~1e-7, tripping the assertion below.
        portfolio_weights = self._validate_weights(portfolio_weights, "portfolio_weights")
        benchmark_weights = self._validate_weights(benchmark_weights, "benchmark_weights")
        portfolio_sector_returns = self._validate_sector_returns(
            portfolio_sector_returns, "portfolio_sector_returns"
        )
        benchmark_sector_returns = self._validate_sector_returns(
            benchmark_sector_returns, "benchmark_sector_returns"
        )

        self._require_returns_for_weighted_sectors(
            portfolio_weights, portfolio_sector_returns, "portfolio"
        )
        self._require_returns_for_weighted_sectors(
            benchmark_weights, benchmark_sector_returns, "benchmark"
        )

        derived_benchmark_return = sum(
            w * benchmark_sector_returns.get(sector, 0.0)
            for sector, w in benchmark_weights.items()
        )
        if total_benchmark_return is None:
            total_benchmark_return = derived_benchmark_return
        else:
            total_benchmark_return = self._as_finite_float(
                total_benchmark_return, "total_benchmark_return"
            )
            if abs(total_benchmark_return - derived_benchmark_return) > _BENCHMARK_RETURN_TOL:
                raise AttributionError(
                    f"total_benchmark_return ({total_benchmark_return!r}) does not match the "
                    f"value implied by benchmark_weights and benchmark_sector_returns "
                    f"({derived_benchmark_return!r}). Brinson-Fachler allocation effects are "
                    "measured against the benchmark's own single-period return; supplying a "
                    "different figure breaks the reconciliation to active return."
                )

        results: List[BrinsonSectorResult] = []
        for sector in sorted(set(portfolio_weights) | set(benchmark_weights)):
            wp = portfolio_weights.get(sector, 0.0)
            wb = benchmark_weights.get(sector, 0.0)
            rp = portfolio_sector_returns.get(sector, 0.0)
            rb = benchmark_sector_returns.get(sector, 0.0)

            allocation = (wp - wb) * (rb - total_benchmark_return)
            selection = wb * (rp - rb)
            interaction = (wp - wb) * (rp - rb)

            results.append(
                BrinsonSectorResult(
                    sector=sector,
                    allocation_effect=allocation,
                    selection_effect=selection,
                    interaction_effect=interaction,
                    total_effect=allocation + selection + interaction,
                )
            )

        self._assert_reconciles(
            results, portfolio_weights, portfolio_sector_returns, total_benchmark_return
        )
        return results

    @staticmethod
    def _as_finite_float(value: object, label: str) -> float:
        """
        Coerce to a finite float or raise.

        Duck-typed rather than `isinstance(value, float)` so numpy scalars
        (`float32` is not a `float` subclass) are accepted. `bool` is rejected
        explicitly -- `float(True) == 1.0` would turn a boolean typo into a weight.
        """
        if isinstance(value, bool):
            raise AttributionError(f"{label} must be a finite number, got {value!r}.")
        try:
            number = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise AttributionError(f"{label} must be a finite number, got {value!r}.") from exc
        if not math.isfinite(number):
            raise AttributionError(f"{label} must be a finite number, got {value!r}.")
        return number

    @classmethod
    def _validate_weights(cls, weights: Dict[str, float], label: str) -> Dict[str, float]:
        """Weights must be finite, non-empty, and sum to 1.0. Returns them as floats."""
        if not weights:
            raise AttributionError(f"{label} is empty; Brinson attribution needs at least one sector.")

        normalized = {
            sector: cls._as_finite_float(w, f"{label}[{sector!r}]")
            for sector, w in weights.items()
        }
        total = sum(normalized.values())
        if abs(total - 1.0) > _WEIGHT_SUM_TOL:
            raise AttributionError(
                f"{label} sums to {total!r}, not 1.0. Brinson effects only reconcile to active "
                "return when the sector partition is exhaustive and both weight vectors sum to "
                "1.0. Include a cash/other bucket rather than attributing a partial portfolio."
            )
        return normalized

    @classmethod
    def _validate_sector_returns(cls, returns: Dict[str, float], label: str) -> Dict[str, float]:
        """Sector returns must be finite numbers. Returns them as floats."""
        return {
            sector: cls._as_finite_float(r, f"{label}[{sector!r}]")
            for sector, r in returns.items()
        }

    @staticmethod
    def _require_returns_for_weighted_sectors(
        weights: Dict[str, float], returns: Dict[str, float], side: str
    ) -> None:
        """
        Every sector carrying weight needs an explicit return.

        Defaulting a missing return to 0.0 turns a mistyped sector key into a large
        phantom selection effect with no error.
        """
        missing = sorted(
            sector for sector, w in weights.items() if w != 0.0 and sector not in returns
        )
        if missing:
            raise AttributionError(
                f"Missing {side} sector return(s) for weighted sector(s): {missing}. "
                "Supply an explicit return for every sector carrying weight -- a silently "
                "defaulted 0.0 return produces a large phantom effect."
            )

    @staticmethod
    def _assert_reconciles(
        results: List[BrinsonSectorResult],
        portfolio_weights: Dict[str, float],
        portfolio_sector_returns: Dict[str, float],
        total_benchmark_return: float,
    ) -> None:
        """
        Verify the effects sum to active return before handing them to a caller.

        This is a guard against a future change silently breaking the identity, not
        a check on user input -- the input validation above is what makes it hold.
        """
        total_portfolio_return = sum(
            w * portfolio_sector_returns.get(sector, 0.0)
            for sector, w in portfolio_weights.items()
        )
        active_return = total_portfolio_return - total_benchmark_return
        effects_total = sum(r.total_effect for r in results)
        if not math.isclose(effects_total, active_return, rel_tol=1e-9, abs_tol=1e-12):
            raise AttributionError(
                f"Brinson effects sum to {effects_total!r} but active return is "
                f"{active_return!r}. The decomposition does not reconcile."
            )

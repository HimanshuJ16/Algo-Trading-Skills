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

**Undefined is not zero.** A zero-variance benchmark leaves beta unidentified and
raises; a constant return series leaves correlation undefined and reports `nan`;
zero active risk against a non-zero active return leaves the information ratio
unbounded and reports `+/-inf`. Reporting `0.0` for any of these would make a
perfectly consistent outperformer indistinguishable from a manager with no skill.
Degeneracy tolerances sit at the floating-point noise floor (`1e-12`), never at a
plausible financial magnitude: a `variance > 1e-8` guard misclassifies a genuine
cash or short-duration benchmark (daily sigma ~1bp, variance ~1e-8) as constant.

**Every caveat is surfaced, not just logged.** `AttributionSummary.warnings`
carries the sub-one-year sample, thin-sample, insignificant-t and undefined-metric
notes so a report generator cannot quote a headline number without them.

**Multi-strategy comparison.** `compare_strategies` runs the same decomposition for
several strategies against one benchmark over one window, so the rows are actually
comparable. It is not a multi-factor model: each row is still single-factor CAPM,
and a strategy that is really a small-cap or value tilt will show that tilt as
alpha in every row alike.
"""
from dataclasses import dataclass, field
import logging
import math
import statistics
from typing import Dict, List, Mapping, Optional, Sequence

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

# A per-period standard deviation at or below this is constant to within
# floating-point resolution. Deliberately far below any real return series: a
# genuine cash or short-duration benchmark runs a daily sigma of ~1bp (1e-4), and
# a degeneracy guard placed at a plausible financial magnitude would misclassify
# it as constant and report a correlation of nan alongside a beta of 1.
_ZERO_STD_TOL = 1e-12

# Two-sided 95% critical value for the active-return t-statistic. A heuristic
# reporting threshold, not a standard.
_SIGNIFICANCE_T = 1.96

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
    :param correlation_to_benchmark: Pearson correlation of the two series.
        `nan` -- never `0.0` -- when either series is constant, because
        correlation is then 0/0 and undefined rather than absent.
    :param warnings: Every caveat that applies to these numbers: a sub-one-year
        sample, a thin sample, an insignificant t-statistic, an undefined
        correlation, an unbounded information ratio. Read this list before
        quoting any figure from the summary; an empty list means no caveats.
    """

    alpha_annualized: float
    beta: float
    tracking_error_annualized: float
    information_ratio: float
    active_return_annualized: float
    is_alpha_positive: bool
    observations: int = 0
    information_ratio_t_stat: float = float("nan")
    correlation_to_benchmark: float = float("nan")
    warnings: List[str] = field(default_factory=list)


@dataclass
class StrategyComparisonRow:
    """
    One strategy's row in a multi-strategy comparison against a shared benchmark.

    Rows in a single `compare_strategies` result are directly comparable: same
    benchmark, same window, same annualization factor and same risk-free rate.
    Rows from separate calls are not, unless all four match.
    """

    strategy: str
    alpha_annualized: float
    beta: float
    correlation_to_benchmark: float
    tracking_error_annualized: float
    information_ratio: float
    information_ratio_t_stat: float
    active_return_annualized: float
    observations: int
    warnings: List[str] = field(default_factory=list)


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

        warnings: List[str] = []
        years = n / self.annualization_factor
        if n < _THIN_SAMPLE_OBSERVATIONS:
            message = (
                f"Attribution computed over only {n} observations; beta and alpha point "
                "estimates from a sample this short are not statistically meaningful. "
                "Read information_ratio_t_stat before acting on them."
            )
            warnings.append(message)
            logger.warning("%s", message)
        if years < 1.0:
            warnings.append(
                f"Sample covers only {years:.2f} year(s) ({n} periods). Annualized alpha "
                "and tracking error are extrapolations from a sub-annual window."
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

        # Correlation is 0/0 -- undefined, not zero -- against a constant series.
        # var_b > _ZERO_VARIANCE_TOL is already guaranteed above; the portfolio
        # series can still be flat (a cash sleeve, or a strategy that never traded).
        std_p = math.sqrt(statistics.variance(p_ret))
        std_b = math.sqrt(var_b)
        if std_p > _ZERO_STD_TOL and std_b > _ZERO_STD_TOL:
            correlation = cov_pb / (std_p * std_b)
            # Clip float error rather than emitting |rho| > 1.
            correlation = max(-1.0, min(1.0, correlation))
        else:
            correlation = float("nan")
            warnings.append(
                "Correlation to the benchmark is undefined (0/0): the portfolio return "
                "series is constant. Reported as nan, not 0.0."
            )

        if not math.isfinite(information_ratio):
            warnings.append(
                f"Information ratio is unbounded ({information_ratio}): active risk is zero "
                f"with an annualized active return of {active_annualized:+.4f}. A constant "
                "active return means deterministic out/under-performance, not an absence "
                "of skill -- reporting 0.0 here would score the most consistent manager "
                "possible as merely average."
            )
        elif math.isfinite(t_stat) and abs(t_stat) < _SIGNIFICANCE_T:
            warnings.append(
                f"Active return t-statistic is {t_stat:+.2f}, inside +/-{_SIGNIFICANCE_T}: "
                f"the information ratio of {information_ratio:.2f} over {years:.2f} year(s) "
                "is not distinguishable from zero at the 95% level."
            )

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
            correlation_to_benchmark=correlation,
            warnings=warnings,
        )

    def compare_strategies(
        self,
        strategy_returns: Mapping[str, Sequence[float]],
        benchmark_returns: Sequence[float],
        *,
        sort_by_information_ratio: bool = True,
    ) -> List[StrategyComparisonRow]:
        """
        Run the same decomposition for several strategies against one benchmark.

        This is the multi-strategy view: it answers "which of these sleeves is
        actually earning its benchmark-relative keep", and — pointed at the *simple
        alternative* the book is meant to beat (a static 60/40 blend, an
        equal-weight sleeve, or cash) — whether the added complexity earns a
        positive alpha at a defensible information ratio at all.

        Every row uses the same benchmark, window, `annualization_factor` and
        `risk_free_rate`, which is what makes the rows comparable. Rows from
        separate calls are not comparable unless all four match.

        This remains **single-factor CAPM per row**. A sleeve that is really a
        small-cap or value tilt shows that tilt as alpha here exactly as it would
        in a single-strategy run; use `strategy-performance-attribution-vs-market-beta`
        when the question is which factors the return came from.

        :param strategy_returns: Mapping of strategy name to its periodic return
            series. Every series must be date-aligned with `benchmark_returns` and
            of equal length; alignment itself is the caller's responsibility.
        :param benchmark_returns: The shared benchmark's periodic returns.
        :param sort_by_information_ratio: Sort rows by information ratio descending
            (undefined and `-inf` ratios last), ties broken by strategy name. Set
            `False` to preserve the mapping's insertion order.
        :return: One `StrategyComparisonRow` per strategy.
        :raises AttributionError: if the mapping is empty, a key is not a non-empty
            string, or any strategy fails the single-strategy validation. A failing
            strategy raises rather than being dropped -- a comparison table with a
            silently missing row is worse than no table.
        """
        if not strategy_returns:
            raise AttributionError(
                "strategy_returns is empty; a comparison needs at least one strategy."
            )

        rows: List[StrategyComparisonRow] = []
        for name, series in strategy_returns.items():
            if not isinstance(name, str) or not name.strip():
                raise AttributionError(
                    f"Strategy names must be non-empty strings, got {name!r}."
                )
            try:
                summary = self.evaluate_alpha_beta(series, benchmark_returns)
            except AttributionError as exc:
                raise AttributionError(f"Strategy {name!r}: {exc}") from exc

            rows.append(
                StrategyComparisonRow(
                    strategy=name,
                    alpha_annualized=summary.alpha_annualized,
                    beta=summary.beta,
                    correlation_to_benchmark=summary.correlation_to_benchmark,
                    tracking_error_annualized=summary.tracking_error_annualized,
                    information_ratio=summary.information_ratio,
                    information_ratio_t_stat=summary.information_ratio_t_stat,
                    active_return_annualized=summary.active_return_annualized,
                    observations=summary.observations,
                    warnings=list(summary.warnings),
                )
            )

        if sort_by_information_ratio:
            rows.sort(key=self._comparison_sort_key)

        logger.info(
            "Compared %d strategies against a shared benchmark over %d observations.",
            len(rows), rows[0].observations,
        )
        return rows

    @staticmethod
    def _comparison_sort_key(row: "StrategyComparisonRow"):
        """
        Information ratio descending, with undefined ratios last and name as the
        tie-break so the ordering is deterministic.
        """
        ir = row.information_ratio
        rank = -math.inf if math.isnan(ir) else ir
        return (-rank, row.strategy)

    @staticmethod
    def render_comparison_table(rows: Sequence["StrategyComparisonRow"]) -> str:
        """
        Render comparison rows as a fixed-width text table.

        The caveat count is a column, not a footnote: a row whose information ratio
        rests on four months of data must not read the same as one built on ten
        years. Read `StrategyComparisonRow.warnings` for the caveats themselves.
        """
        header = (
            f"{'Strategy':<24}{'Alpha':>10}{'Beta':>8}{'Corr':>8}"
            f"{'TE':>10}{'IR':>8}{'t':>8}{'Obs':>7}{'Caveats':>9}"
        )
        lines = [header, "-" * len(header)]
        for row in rows:
            # Snap display-invisible magnitudes to zero so a 1e-18 alpha does not
            # render as "-0.00%", which reads as a loss.
            alpha_pct = row.alpha_annualized * 100.0
            if abs(alpha_pct) < 5e-3:
                alpha_pct = 0.0
            lines.append(
                f"{row.strategy[:23]:<24}"
                f"{alpha_pct:>9.2f}%"
                f"{row.beta:>8.2f}"
                f"{row.correlation_to_benchmark:>8.2f}"
                f"{row.tracking_error_annualized * 100.0:>9.2f}%"
                f"{row.information_ratio:>8.2f}"
                f"{row.information_ratio_t_stat:>8.2f}"
                f"{row.observations:>7d}"
                f"{len(row.warnings):>9d}"
            )
        return "\n".join(lines)

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

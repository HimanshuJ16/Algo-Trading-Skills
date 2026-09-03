"""
walk-forward-hyperparameter-search-budget: Hyperparameter search space bounder,
grid pruner, and walk-forward overfitting risk auditor.

What this does
--------------
Given a parameter grid and the length of the in-sample window it will be searched
on, it (a) computes an evaluation budget, (b) draws a bounded, deterministic,
non-aliasing subset of the grid when the grid exceeds that budget, and (c) audits
the *cumulative* trial count across every walk-forward window, which is the count
that actually governs selection bias.

Two independent numbers are reported, and they answer different questions:

1. `allowed_budget_max` - the repo's house heuristic, `years * max_trials_per_year`
   clamped to [10, 500]. It is an operational guardrail with **no published basis**.
   See `references/standards.md`.
2. `min_backtest_length_years` - the Minimum Backtest Length of Bailey, Borwein,
   Lopez de Prado and Zhu: the data span required before a given trial count stops
   guaranteeing a spurious in-sample Sharpe. This one is derived from the literature
   and is far stricter than the heuristic.

Sampling
--------
When the grid must be pruned, the subset is drawn by **seeded random selection over
the flat index space**, not by a constant stride. A constant stride aliases against
the grid's own mixed-radix layout: with `itertools.product` ordering the last
parameter varies fastest, so a stride sharing a factor with the last parameter's
cardinality silently freezes that parameter at a single value. A 20x20x10 grid pruned
to 100 points by stride 40 explores exactly one value of the third parameter and five
of the twenty values of the second. The pruned search then reports a bounded, "budget
OK" result for a sweep that never moved two of the three parameters.

Selection is done on indices, so the full Cartesian product is never materialised.
A grid of 50,000 combinations - the case `SKILL.md` exists to prevent - costs the
same memory as one of 50.

What this does NOT do
---------------------
It does not compute the Probability of Backtest Overfitting (PBO), deflate a Sharpe
ratio, or measure out-of-sample decay. It bounds and counts trials; it does not
evaluate them. Nothing here makes an over-searched strategy safe - it makes the
search size explicit and auditable. See `SKILL.md` "When NOT to Use".
"""
from dataclasses import dataclass
import itertools
import logging
import math
import random
from statistics import NormalDist
from typing import Any, Dict, List, Mapping, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Euler-Mascheroni constant, used in the Gumbel approximation to the expected
#: maximum of N i.i.d. standard normals.
EULER_MASCHERONI = 0.5772156649015329

#: Trading days per year used to convert an in-sample window length into years.
TRADING_DAYS_PER_YEAR = 252.0

_STANDARD_NORMAL = NormalDist()


class SearchBudgetError(ValueError):
    """Raised on invalid budgeter configuration or a malformed parameter grid."""


def expected_max_sharpe(n_trials: int, years: float = 1.0) -> float:
    """Expected maximum annualised Sharpe ratio over `n_trials` independent trials
    of strategies whose true Sharpe ratio is zero, given `years` of data.

    This is the Gumbel approximation of Bailey, Borwein, Lopez de Prado and Zhu:

        E[max_N] = ((1 - g) * Z^-1[1 - 1/N] + g * Z^-1[1 - 1/(N e)]) / sqrt(years)

    where `g` is the Euler-Mascheroni constant and `Z^-1` the inverse standard normal
    CDF. The numerator is the expected maximum of N i.i.d. standard normal estimates;
    dividing by sqrt(years) annualises it, because the standard error of a Sharpe
    estimate shrinks with the square root of the sample length.

    The approximation is asymptotic in N. At N = 1 the exact value is 0.0 (the mean of
    a single standard normal) and that is returned directly; the formula is not applied
    below N = 2, where Z^-1[1 - 1/N] = Z^-1[0] is undefined.

    Raises:
        SearchBudgetError: if `n_trials` < 1 or `years` <= 0.
    """
    if n_trials < 1:
        raise SearchBudgetError(f"n_trials must be >= 1, got {n_trials}")
    if years <= 0:
        raise SearchBudgetError(f"years must be > 0, got {years}")
    if n_trials == 1:
        return 0.0
    n = float(n_trials)
    standardised = (
        (1.0 - EULER_MASCHERONI) * _STANDARD_NORMAL.inv_cdf(1.0 - 1.0 / n)
        + EULER_MASCHERONI * _STANDARD_NORMAL.inv_cdf(1.0 - 1.0 / (n * math.e))
    )
    return standardised / math.sqrt(years)


def minimum_backtest_length_years(n_trials: int, target_sharpe: float = 1.0) -> float:
    """Minimum Backtest Length (MinBTL), in years, for `n_trials` independent trials.

    Below this data span, selecting the best of `n_trials` zero-skill strategies is
    expected to yield an in-sample annualised Sharpe of at least `target_sharpe`
    purely by chance, with an expected out-of-sample Sharpe of zero. Inverting
    `expected_max_sharpe`:

        MinBTL = (E[max_N] / target_sharpe)^2

    Verified against the source's own worked example: N = 45 returns 5.00 years at
    `target_sharpe` = 1.0, matching the published statement that five years of data
    support no more than forty-five independent model configurations.

    The paper also gives the looser closed-form upper bound MinBTL < 2*ln(N) /
    target_sharpe^2. That bound is not used here because it materially overstates the
    requirement at the trial counts this skill deals with (7.61 vs 5.00 years at
    N = 45); the Gumbel form above is the sharper estimate.

    IMPORTANT - independence: `n_trials` means *independent* trials. Neighbouring grid
    points are highly correlated, so a raw grid-point count overstates the effective
    number of independent trials and this function therefore returns a conservative
    (long) requirement. Treat it as an order-of-magnitude signal, not a threshold to
    optimise against. Estimating the effective independent trial count is out of scope
    for this skill.

    Raises:
        SearchBudgetError: if `n_trials` < 1 or `target_sharpe` <= 0.
    """
    if target_sharpe <= 0:
        raise SearchBudgetError(f"target_sharpe must be > 0, got {target_sharpe}")
    return (expected_max_sharpe(n_trials) / target_sharpe) ** 2


@dataclass
class SearchBudgetReport:
    """Outcome of auditing one in-sample window's parameter grid.

    `overfitting_risk_level` grades the grid *as designed* (`raw_combinations` against
    `allowed_budget_max`), not the pruned search that was actually run. A HIGH grade on
    a pruned search is a statement that the search space was mis-scaled for the data,
    which remains true after pruning: the sampled subset covers proportionally less of
    a space that was already too large to characterise from this much data.
    """

    raw_combinations: int
    allowed_budget_max: int
    sampled_evaluations: int
    is_budget_exceeded: bool
    pruning_applied: bool
    overfitting_risk_level: str  # LOW, MODERATE, HIGH
    message: str


@dataclass
class WalkForwardBudgetAudit:
    """Cumulative trial audit across every window of a walk-forward campaign.

    Per-window budgets are individually satisfied by construction - `audit_and_prune`
    enforces them - so summing them against a per-window limit proves nothing. What
    governs selection bias in the final strategy choice is the total number of
    configurations evaluated over the whole campaign, measured against the total
    distinct data span it ran on.
    """

    windows: int
    total_evaluations: int
    total_span_days: int
    cumulative_budget_max: int
    is_cumulative_budget_exceeded: bool
    min_backtest_length_years: float
    available_years: float
    is_data_span_sufficient: bool
    overfitting_risk_level: str  # LOW, MODERATE, HIGH
    message: str


class HyperparameterSearchBudgeter:
    """
    Bounds hyperparameter search space in walk-forward validation to limit
    the Probability of Backtest Overfitting (PBO).

    Args:
        max_trials_per_year: House heuristic for evaluations permitted per year of
            in-sample data. Must be >= 1.
        seed: Seed for the deterministic grid subsampler. Two runs with the same seed,
            grid and window length select the same combinations, so a pruned search is
            reproducible; change it only to deliberately redraw the subset.
        target_sharpe: Annualised in-sample Sharpe ratio that the MinBTL diagnostic
            treats as "too good to have arisen by chance". Must be > 0.
    """

    #: `raw_combinations` above this multiple of the allowed budget is graded HIGH
    #: rather than MODERATE. A house convention with no published basis; it marks the
    #: point at which pruning discards the large majority of the designed space.
    HIGH_RISK_OVERRUN_MULTIPLE = 5

    #: Floor and ceiling applied to the heuristic budget, in evaluations.
    MIN_BUDGET = 10
    MAX_BUDGET = 500

    #: At or below this many total combinations the sampler indexes the flat space
    #: directly; above it, it draws indices by rejection so that no range object of
    #: that size is ever constructed.
    _DENSE_SAMPLE_LIMIT = 1_000_000

    def __init__(
        self,
        max_trials_per_year: int = 100,
        seed: int = 12345,
        target_sharpe: float = 1.0,
    ):
        if max_trials_per_year < 1:
            raise SearchBudgetError(
                f"max_trials_per_year must be >= 1, got {max_trials_per_year}"
            )
        if target_sharpe <= 0:
            raise SearchBudgetError(f"target_sharpe must be > 0, got {target_sharpe}")
        self.max_trials_per_year = max_trials_per_year
        self.seed = seed
        self.target_sharpe = target_sharpe

    def compute_max_budget(self, in_sample_days: int) -> int:
        """Maximum allowed parameter evaluation budget for an in-sample window.

            budget = clamp(floor(in_sample_days / 252 * max_trials_per_year), 10, 500)

        The division truncates, so a 250-day window yields 99, not 100; a full
        252-day year yields exactly 100.

        Raises:
            SearchBudgetError: if `in_sample_days` <= 0. A window of zero or negative
                length previously returned the floor of 10 silently, which let a caller
                with an empty or misconfigured window believe a search had been
                budgeted for it.
        """
        if in_sample_days <= 0:
            raise SearchBudgetError(f"in_sample_days must be > 0, got {in_sample_days}")
        years = in_sample_days / TRADING_DAYS_PER_YEAR
        max_budget = math.floor(years * self.max_trials_per_year)
        return max(self.MIN_BUDGET, min(self.MAX_BUDGET, max_budget))

    def audit_and_prune(
        self,
        parameter_grid: Mapping[str, Sequence[Any]],
        in_sample_days: int,
    ) -> Tuple[List[Dict[str, Any]], SearchBudgetReport]:
        """Audit grid size, compute the allowed budget, and subsample if required.

        Returns the combinations to evaluate - in ascending flat-index order, so the
        result is deterministic and independent of the RNG's emission order - together
        with the accompanying report.

        Raises:
            SearchBudgetError: if the grid is empty, a parameter has no candidate
                values, or `in_sample_days` <= 0.
        """
        keys, values = self._validate_grid(parameter_grid)

        raw_combinations = 1
        for vals in values:
            raw_combinations *= len(vals)

        max_allowed = self.compute_max_budget(in_sample_days)
        is_exceeded = raw_combinations > max_allowed

        if is_exceeded:
            risk = (
                "HIGH"
                if raw_combinations > max_allowed * self.HIGH_RISK_OVERRUN_MULTIPLE
                else "MODERATE"
            )
            msg = (
                f"SEARCH BUDGET OVERRUN: {raw_combinations} combinations > max allowed "
                f"{max_allowed} for {in_sample_days} days. Pruning applied."
            )
            logger.warning(msg)
            pruned = True
            sampled_combinations = self._sample_grid(
                keys, values, raw_combinations, max_allowed
            )
        else:
            risk = "LOW"
            msg = (
                f"Search budget OK: {raw_combinations} combinations <= max allowed "
                f"{max_allowed}."
            )
            logger.info(msg)
            pruned = False
            sampled_combinations = [
                dict(zip(keys, combo)) for combo in itertools.product(*values)
            ]

        report = SearchBudgetReport(
            raw_combinations=raw_combinations,
            allowed_budget_max=max_allowed,
            sampled_evaluations=len(sampled_combinations),
            is_budget_exceeded=is_exceeded,
            pruning_applied=pruned,
            overfitting_risk_level=risk,
            message=msg,
        )

        return sampled_combinations, report

    def audit_walk_forward(
        self,
        reports: Sequence[SearchBudgetReport],
        total_span_days: int,
    ) -> WalkForwardBudgetAudit:
        """Audit cumulative evaluations across a whole walk-forward campaign.

        Per-window compliance does not imply campaign compliance: ten windows each
        capped at 100 evaluations is a 1,000-trial selection process, and the final
        parameter choice inherits the selection bias of all 1,000. This compares the
        campaign total against the budget implied by the *total distinct data span*,
        and reports MinBTL alongside so the house heuristic can be read against the
        literature's stricter requirement.

        Args:
            reports: One `SearchBudgetReport` per walk-forward window.
            total_span_days: Total distinct trading days spanned by the campaign
                (first in-sample day to last out-of-sample day), NOT the sum of the
                in-sample window lengths - overlapping walk-forward windows reuse the
                same data and must not be counted twice.

        Raises:
            SearchBudgetError: if `reports` is empty or `total_span_days` <= 0.
        """
        if not reports:
            raise SearchBudgetError("reports must contain at least one window report")

        cumulative_budget = self.compute_max_budget(total_span_days)
        total_evaluations = sum(r.sampled_evaluations for r in reports)
        is_exceeded = total_evaluations > cumulative_budget

        available_years = total_span_days / TRADING_DAYS_PER_YEAR
        required_years = minimum_backtest_length_years(
            max(1, total_evaluations), self.target_sharpe
        )
        span_sufficient = available_years >= required_years

        if is_exceeded:
            risk = (
                "HIGH"
                if total_evaluations
                > cumulative_budget * self.HIGH_RISK_OVERRUN_MULTIPLE
                else "MODERATE"
            )
            msg = (
                f"CUMULATIVE BUDGET OVERRUN: {total_evaluations} evaluations across "
                f"{len(reports)} windows > max allowed {cumulative_budget} for a "
                f"{total_span_days}-day span."
            )
            logger.warning(msg)
        else:
            risk = "LOW"
            msg = (
                f"Cumulative budget OK: {total_evaluations} evaluations across "
                f"{len(reports)} windows <= max allowed {cumulative_budget}."
            )
            logger.info(msg)

        if not span_sufficient:
            shortfall = (
                f"MinBTL SHORTFALL: {total_evaluations} trials require "
                f"{required_years:.2f} years at Sharpe {self.target_sharpe:g}; "
                f"{available_years:.2f} years available."
            )
            logger.warning(shortfall)
            msg = f"{msg} {shortfall}"

        return WalkForwardBudgetAudit(
            windows=len(reports),
            total_evaluations=total_evaluations,
            total_span_days=total_span_days,
            cumulative_budget_max=cumulative_budget,
            is_cumulative_budget_exceeded=is_exceeded,
            min_backtest_length_years=required_years,
            available_years=available_years,
            is_data_span_sufficient=span_sufficient,
            overfitting_risk_level=risk,
            message=msg,
        )

    def _validate_grid(
        self, grid: Mapping[str, Sequence[Any]]
    ) -> Tuple[List[str], List[Sequence[Any]]]:
        """Reject malformed grids before any budget arithmetic runs.

        An empty candidate list previously made the Cartesian product zero, which
        compared as "within budget" and produced a LOW-risk report for a search that
        evaluated nothing at all.
        """
        if not grid:
            raise SearchBudgetError("parameter_grid must contain at least one parameter")
        keys = list(grid.keys())
        values: List[Sequence[Any]] = []
        for key in keys:
            vals = grid[key]
            if isinstance(vals, (str, bytes)):
                raise SearchBudgetError(
                    f"parameter '{key}' must map to a sequence of candidate values, "
                    f"not a bare string"
                )
            try:
                length = len(vals)
            except TypeError as exc:
                raise SearchBudgetError(
                    f"parameter '{key}' must map to a sized sequence of candidate "
                    f"values, got {type(vals).__name__}"
                ) from exc
            if length == 0:
                raise SearchBudgetError(
                    f"parameter '{key}' has no candidate values; an empty grid axis "
                    f"makes the whole search space empty"
                )
            values.append(vals)
        return keys, values

    def _sample_grid(
        self,
        keys: Sequence[str],
        values: Sequence[Sequence[Any]],
        raw_combinations: int,
        budget: int,
    ) -> List[Dict[str, Any]]:
        """Draw `budget` distinct combinations without materialising the full product."""
        strides = self._mixed_radix_strides(values)
        indices = self._sample_indices(raw_combinations, budget)
        return [self._decode_index(i, keys, values, strides) for i in indices]

    @staticmethod
    def _mixed_radix_strides(values: Sequence[Sequence[Any]]) -> List[int]:
        """Place value of each parameter under `itertools.product` ordering.

        The last parameter varies fastest, so its stride is 1 and each earlier
        parameter's stride is the product of all cardinalities to its right.
        """
        strides = [1] * len(values)
        for j in range(len(values) - 2, -1, -1):
            strides[j] = strides[j + 1] * len(values[j + 1])
        return strides

    @staticmethod
    def _decode_index(
        index: int,
        keys: Sequence[str],
        values: Sequence[Sequence[Any]],
        strides: Sequence[int],
    ) -> Dict[str, Any]:
        """Map a flat index to the combination `itertools.product` yields at that spot."""
        return {
            key: values[j][(index // strides[j]) % len(values[j])]
            for j, key in enumerate(keys)
        }

    def _sample_indices(self, n_total: int, k: int) -> List[int]:
        """Deterministically choose `k` distinct indices from [0, n_total).

        Returned sorted, so the caller's evaluation order does not depend on the RNG's
        emission order and two runs with the same seed produce identical output.
        """
        rng = random.Random(self.seed)
        if n_total <= self._DENSE_SAMPLE_LIMIT:
            chosen = rng.sample(range(n_total), k)
        else:
            # k is bounded by MAX_BUDGET and n_total exceeds a million in this branch,
            # so collisions are rare and rejection sampling terminates quickly. It
            # avoids building a range object over an astronomically large space: a
            # 10-parameter, 20-value grid has 20^10 combinations.
            seen = set()
            while len(seen) < k:
                seen.add(rng.randrange(n_total))
            chosen = list(seen)
        return sorted(chosen)

"""
backtest-parameter-sensitivity-analysis: Grid sweep, Sharpe gradient computation,
and overfitting fragility detector.

What this measures
------------------
For a one-dimensional parameter grid, it locates the best-performing point and asks
whether its *immediate neighbours in parameter space* hold up. A peak that collapses
the moment the parameter moves one grid step is an overfit artefact; a peak whose
neighbours perform comparably is a plateau.

The score is a relative degradation, not a derivative:

    degradation = (best_sharpe - worst_adjacent_sharpe) / best_sharpe

A raw gradient (dSharpe/dParam) is deliberately *not* thresholded here, because its
units depend on the parameter: 0.2 Sharpe per lookback-day and 0.2 Sharpe per
entry-threshold-unit are not comparable quantities, so no single cut-off is portable
across parameters. The relative form is dimensionless and comparable.

What this does NOT do
---------------------
It does not correct `best_sharpe` for selection bias. The maximum over an N-point grid
is upward-biased even when true skill is zero, simply because it is a maximum of N
noisy estimates. Deflating it requires the number of (effectively independent) trials —
see Bailey and Lopez de Prado (2014) on the Deflated Sharpe Ratio, Bailey, Borwein,
Lopez de Prado and Zhu on the Probability of Backtest Overfitting, and the sibling
skill `factor-research-multiple-testing-correction`. `total_grid_points` is reported so
that correction can be applied downstream.

It is also strictly one-dimensional: it cannot see interaction effects between
parameters. See `SKILL.md` "When NOT to Use".
"""
from dataclasses import dataclass
from enum import Enum
import logging
import math
from typing import Callable, Dict, List, Sequence, Tuple

logger = logging.getLogger(__name__)

#: A plateau verdict requires the optimum plus a neighbour on each side. This is a
#: structural minimum, not a tuning parameter.
MIN_GRID_POINTS_FOR_PLATEAU = 3


class SensitivityError(ValueError):
    """Raised on invalid analyzer configuration or a malformed parameter grid."""


class RobustnessVerdict(str, Enum):
    """Classification of the best grid point.

    Values double as the message prefix, so callers may switch on the enum rather
    than substring-matching the rendered text.
    """

    ROBUST_PLATEAU = "ROBUST PLATEAU"
    FRAGILE_PEAK = "FRAGILE PEAK"
    EDGE_OPTIMUM = "EDGE OPTIMUM"
    NOT_VIABLE = "NOT VIABLE"
    INSUFFICIENT_GRID = "INSUFFICIENT GRID"


@dataclass
class GridPoint:
    params: Dict[str, float]
    sharpe_ratio: float


@dataclass
class SensitivityReport:
    """Outcome of one sensitivity analysis.

    Attributes:
        total_grid_points: Number of points swept. This is the trial count needed to
            deflate `best_sharpe` for selection bias.
        best_sharpe: Highest Sharpe on the grid. **Upward-biased**: it is the maximum
            of `total_grid_points` noisy estimates, so it overstates expected
            out-of-sample performance even for a strategy with no real edge.
        best_params: Parameters at the best point.
        avg_sharpe: Mean Sharpe across the grid.
        sharpe_std: Population standard deviation of Sharpe across the grid
            (divisor N, not N-1). Zero for a single point.
        max_gradient: Retained for backward compatibility. Despite the name this is
            **not** a gradient — it holds the same dimensionless relative degradation
            as `neighborhood_degradation_pct`. Prefer that field.
        is_robust: True only for `RobustnessVerdict.ROBUST_PLATEAU`.
        message: Human-readable verdict, prefixed with the verdict value.
        verdict: Machine-readable classification.
        neighborhood_degradation_pct: Relative Sharpe drop to the worst adjacent grid
            point, as a fraction of `best_sharpe`. 0.0 when not computable.
        best_at_grid_edge: True when the optimum sits at the first or last grid point,
            so only one side of its neighbourhood was observed and the true optimum
            may lie outside the swept range.
    """

    total_grid_points: int
    best_sharpe: float
    best_params: Dict[str, float]
    avg_sharpe: float
    sharpe_std: float
    max_gradient: float
    is_robust: bool
    message: str
    verdict: RobustnessVerdict = RobustnessVerdict.INSUFFICIENT_GRID
    neighborhood_degradation_pct: float = 0.0
    best_at_grid_edge: bool = False


class ParameterSensitivityAnalyzer:
    """
    Sweeps parameter grid, computes Sharpe sensitivity gradient,
    and classifies parameter robustness vs overfitting fragility.
    """

    def __init__(
        self,
        max_neighborhood_degradation_pct: float = 0.15,
        min_viable_sharpe: float = 0.0,
        min_grid_points: int = MIN_GRID_POINTS_FOR_PLATEAU,
    ) -> None:
        """
        Args:
            max_neighborhood_degradation_pct: Relative Sharpe drop to an adjacent grid
                point that still counts as a plateau. The 0.15 default is an
                implementation default with no published basis; calibrate it against
                the dispersion your own backtest produces on re-runs.
            min_viable_sharpe: The best Sharpe must strictly exceed this for any
                robustness verdict. The 0.0 default only rules out strategies that
                lose money at every grid point; raise it to your actual deployment
                hurdle, because a flat grid at Sharpe 0.01 is stable and worthless.
            min_grid_points: Fewest points that can support a plateau verdict. Must be
                at least 3 — the optimum plus one neighbour on each side.

        Raises:
            SensitivityError: On any invalid parameter.
        """
        if not math.isfinite(max_neighborhood_degradation_pct) or max_neighborhood_degradation_pct < 0:
            raise SensitivityError(
                f"max_neighborhood_degradation_pct must be a finite non-negative fraction, "
                f"got {max_neighborhood_degradation_pct}."
            )
        if not math.isfinite(min_viable_sharpe) or min_viable_sharpe < 0:
            # Must be non-negative: the degradation ratio divides by the best Sharpe, so
            # it is undefined for a non-positive optimum. Clearing the viability gate
            # therefore has to guarantee a strictly positive best Sharpe.
            raise SensitivityError(
                f"min_viable_sharpe must be finite and non-negative, got {min_viable_sharpe}."
            )
        if not isinstance(min_grid_points, int) or isinstance(min_grid_points, bool):
            raise SensitivityError(
                f"min_grid_points must be an int, got {type(min_grid_points).__name__}."
            )
        if min_grid_points < MIN_GRID_POINTS_FOR_PLATEAU:
            raise SensitivityError(
                f"min_grid_points must be >= {MIN_GRID_POINTS_FOR_PLATEAU}; a plateau cannot be "
                f"observed without a neighbour on each side. Got {min_grid_points}."
            )

        self.max_neighborhood_degradation_pct = max_neighborhood_degradation_pct
        self.min_viable_sharpe = min_viable_sharpe
        self.min_grid_points = min_grid_points

    def run_grid_sweep(
        self,
        param_name: str,
        param_values: Sequence[float],
        backtest_fn: Callable[[float], float],
    ) -> List[GridPoint]:
        """Evaluates `backtest_fn` at each parameter value.

        Args:
            param_name: Name of the parameter being swept.
            param_values: Values to test. Need not be sorted; `analyze_sensitivity`
                orders by parameter value before looking at neighbours. Duplicates are
                rejected, since two Sharpes for one parameter value make the
                neighbourhood undefined.
            backtest_fn: Maps a parameter value to a Sharpe ratio.

        Raises:
            SensitivityError: On an invalid grid or a non-finite Sharpe from
                `backtest_fn`. A NaN Sharpe left unchecked would silently produce a
                "robust" verdict, because every comparison against NaN is False.
        """
        if not isinstance(param_name, str) or not param_name.strip():
            raise SensitivityError("param_name must be a non-empty string.")
        if not callable(backtest_fn):
            raise SensitivityError("backtest_fn must be callable.")

        values = list(param_values)
        for position, value in enumerate(values):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise SensitivityError(
                    f"param_values[{position}] must be a real number, got "
                    f"{type(value).__name__} ({value!r})."
                )
            if not math.isfinite(value):
                raise SensitivityError(f"param_values[{position}] must be finite, got {value}.")
        if len(set(values)) != len(values):
            raise SensitivityError(
                "param_values contains duplicates; the neighbourhood of a repeated "
                "parameter value is undefined."
            )

        results: List[GridPoint] = []
        for val in values:
            sharpe = backtest_fn(val)
            if isinstance(sharpe, bool) or not isinstance(sharpe, (int, float)):
                raise SensitivityError(
                    f"backtest_fn({val}) must return a real number, got "
                    f"{type(sharpe).__name__} ({sharpe!r})."
                )
            if not math.isfinite(sharpe):
                raise SensitivityError(
                    f"backtest_fn({val}) returned a non-finite Sharpe ({sharpe}). A NaN or "
                    f"infinite Sharpe usually means zero return volatility or an empty trade "
                    f"log; resolve it rather than letting it into the grid."
                )
            results.append(GridPoint(params={param_name: float(val)}, sharpe_ratio=float(sharpe)))
        return results

    def analyze_sensitivity(
        self,
        grid_results: List[GridPoint],
        param_name: str,
    ) -> SensitivityReport:
        """Classifies the best grid point as a plateau or an overfit peak.

        Grid points are ordered by their `param_name` value before neighbours are
        taken. Passing an unordered list is safe; relying on list order was previously
        enough to flip the verdict on an identical set of results.

        Raises:
            SensitivityError: If a grid point lacks `param_name`, carries a non-finite
                value, or duplicates another point's parameter value.
        """
        if not grid_results:
            return SensitivityReport(
                total_grid_points=0,
                best_sharpe=0.0,
                best_params={},
                avg_sharpe=0.0,
                sharpe_std=0.0,
                max_gradient=0.0,
                is_robust=False,
                message=f"{RobustnessVerdict.INSUFFICIENT_GRID.value}: No grid points.",
                verdict=RobustnessVerdict.INSUFFICIENT_GRID,
            )

        ordered = self._validated_and_sorted(grid_results, param_name)

        sharpes = [g.sharpe_ratio for g in ordered]
        n = len(sharpes)
        best_idx = self._best_index(sharpes)
        best = ordered[best_idx]
        avg_s = sum(sharpes) / n
        # Population standard deviation (divisor N); the grid is the whole population
        # of tested configurations, not a sample drawn from one.
        std_s = math.sqrt(sum((s - avg_s) ** 2 for s in sharpes) / n) if n > 1 else 0.0
        at_edge = best_idx == 0 or best_idx == n - 1

        # Plateau Score Algorithm: Evaluate the stability of the neighborhood around the BEST parameter.
        # If the immediate neighbors suffer a massive drop in Sharpe, the optimal point is a fragile overfit peak.
        neighbors: List[float] = []
        if best_idx > 0:
            neighbors.append(sharpes[best_idx - 1])
        if best_idx < n - 1:
            neighbors.append(sharpes[best_idx + 1])

        degradation = 0.0
        if neighbors and best.sharpe_ratio > 0:
            worst_neighbor = min(neighbors)
            degradation = (best.sharpe_ratio - worst_neighbor) / best.sharpe_ratio

        verdict, message = self._classify(
            param_name=param_name,
            best_sharpe=best.sharpe_ratio,
            degradation=degradation,
            n=n,
            at_edge=at_edge,
        )

        if verdict is not RobustnessVerdict.ROBUST_PLATEAU:
            logger.warning(message)

        return SensitivityReport(
            total_grid_points=n,
            best_sharpe=round(best.sharpe_ratio, 4),
            best_params=best.params,
            avg_sharpe=round(avg_s, 4),
            sharpe_std=round(std_s, 4),
            max_gradient=round(degradation, 4),  # Backward-compatible alias; see docstring.
            is_robust=verdict is RobustnessVerdict.ROBUST_PLATEAU,
            message=message,
            verdict=verdict,
            neighborhood_degradation_pct=round(degradation, 4),
            best_at_grid_edge=at_edge,
        )

    # ------------------------------------------------------------------ internals

    @staticmethod
    def _best_index(sharpes: List[float]) -> int:
        """Index of the best Sharpe, breaking ties toward the most interior point.

        A perfectly flat grid is the ideal plateau, yet every point ties for the
        maximum. Taking the first index would land on the boundary and report an edge
        optimum, so ties resolve to the point furthest from either end: if any optimal
        point is bracketed, the optimum is bracketed. Remaining ties take the lowest
        index, keeping the result deterministic.
        """
        n = len(sharpes)
        best = max(sharpes)
        candidates = [i for i, s in enumerate(sharpes) if s == best]
        return max(candidates, key=lambda i: (min(i, n - 1 - i), -i))

    @staticmethod
    def _validated_and_sorted(
        grid_results: List[GridPoint], param_name: str
    ) -> List[GridPoint]:
        """Orders grid points by parameter value, rejecting malformed grids.

        Neighbour selection is only meaningful in parameter order. The previous
        implementation indexed into the caller's list order, so the same set of
        results could be classified either way depending on how it was assembled.
        """
        seen = set()
        for position, point in enumerate(grid_results):
            if not isinstance(point, GridPoint):
                raise SensitivityError(
                    f"grid_results[{position}] must be a GridPoint, got {type(point).__name__}."
                )
            if param_name not in point.params:
                raise SensitivityError(
                    f"grid_results[{position}] has no '{param_name}' entry in params "
                    f"(found {sorted(point.params)}); cannot order the grid."
                )
            value = point.params[param_name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise SensitivityError(
                    f"grid_results[{position}]['{param_name}'] must be a finite real number, "
                    f"got {value!r}."
                )
            if isinstance(point.sharpe_ratio, bool) or not isinstance(point.sharpe_ratio, (int, float)):
                raise SensitivityError(
                    f"grid_results[{position}].sharpe_ratio must be a real number, got "
                    f"{type(point.sharpe_ratio).__name__}."
                )
            if not math.isfinite(point.sharpe_ratio):
                raise SensitivityError(
                    f"grid_results[{position}].sharpe_ratio is non-finite ({point.sharpe_ratio}). "
                    f"Every comparison against NaN is False, so leaving it in would silently "
                    f"produce a robust verdict."
                )
            if value in seen:
                raise SensitivityError(
                    f"grid_results contains duplicate '{param_name}' value {value}; the "
                    f"neighbourhood of a repeated parameter value is undefined."
                )
            seen.add(value)

        return sorted(grid_results, key=lambda g: g.params[param_name])

    def _classify(
        self,
        param_name: str,
        best_sharpe: float,
        degradation: float,
        n: int,
        at_edge: bool,
    ) -> Tuple[RobustnessVerdict, str]:
        """Applies the verdict ladder. Order matters: viability first, then coverage."""
        trials_note = (
            f" Best Sharpe {best_sharpe:.4f} is the maximum over {n} trials and is "
            f"upward-biased by selection; deflate it before treating it as an expectation."
        )

        if best_sharpe <= self.min_viable_sharpe:
            return (
                RobustnessVerdict.NOT_VIABLE,
                f"{RobustnessVerdict.NOT_VIABLE.value}: Best Sharpe for '{param_name}' is "
                f"{best_sharpe:.4f}, at or below the minimum viable {self.min_viable_sharpe:.4f}. "
                f"A flat grid of unprofitable results is stable, not robust.",
            )

        if n < self.min_grid_points:
            return (
                RobustnessVerdict.INSUFFICIENT_GRID,
                f"{RobustnessVerdict.INSUFFICIENT_GRID.value}: {n} grid point(s) for "
                f"'{param_name}' cannot establish a plateau; at least {self.min_grid_points} "
                f"are needed to observe a neighbour on each side of the optimum.",
            )

        if at_edge:
            return (
                RobustnessVerdict.EDGE_OPTIMUM,
                f"{RobustnessVerdict.EDGE_OPTIMUM.value}: Best value of '{param_name}' sits at "
                f"the boundary of the swept range, so only one side of its neighbourhood was "
                f"observed and the true optimum may lie outside the grid. Widen the range before "
                f"judging robustness.{trials_note}",
            )

        if degradation <= self.max_neighborhood_degradation_pct:
            return (
                RobustnessVerdict.ROBUST_PLATEAU,
                f"{RobustnessVerdict.ROBUST_PLATEAU.value}: Parameter '{param_name}' "
                f"neighborhood degradation is {degradation * 100:.1f}%.{trials_note}",
            )

        return (
            RobustnessVerdict.FRAGILE_PEAK,
            f"{RobustnessVerdict.FRAGILE_PEAK.value}: Parameter '{param_name}' suffers a "
            f"{degradation * 100:.1f}% degradation in its immediate neighborhood. High risk of "
            f"overfitting.",
        )

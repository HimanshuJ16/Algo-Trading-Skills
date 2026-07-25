"""
backtest-parameter-sensitivity-analysis: Grid sweep, Sharpe gradient computation,
and overfitting fragility detector.
"""
from dataclasses import dataclass
import logging
import math
from typing import Callable, Dict, List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class GridPoint:
    params: Dict[str, float]
    sharpe_ratio: float


@dataclass
class SensitivityReport:
    total_grid_points: int
    best_sharpe: float
    best_params: Dict[str, float]
    avg_sharpe: float
    sharpe_std: float
    max_gradient: float
    is_robust: bool
    message: str


class ParameterSensitivityAnalyzer:
    """
    Sweeps parameter grid, computes Sharpe sensitivity gradient,
    and classifies parameter robustness vs overfitting fragility.
    """

    def __init__(self, max_neighborhood_degradation_pct: float = 0.15):
        self.max_neighborhood_degradation_pct = max_neighborhood_degradation_pct

    def run_grid_sweep(
        self,
        param_name: str,
        param_values: List[float],
        backtest_fn: Callable[[float], float],
    ) -> List[GridPoint]:
        results = []
        for val in param_values:
            sharpe = backtest_fn(val)
            results.append(GridPoint(params={param_name: val}, sharpe_ratio=sharpe))
        return results

    def analyze_sensitivity(
        self,
        grid_results: List[GridPoint],
        param_name: str,
    ) -> SensitivityReport:
        if not grid_results:
            return SensitivityReport(0, 0, {}, 0, 0, 0, False, "No grid points.")

        sharpes = [g.sharpe_ratio for g in grid_results]
        best_idx = max(range(len(sharpes)), key=lambda i: sharpes[i])
        best = grid_results[best_idx]
        avg_s = sum(sharpes) / len(sharpes)
        std_s = math.sqrt(sum((s - avg_s)**2 for s in sharpes) / len(sharpes)) if len(sharpes) > 1 else 0.0

        # Plateau Score Algorithm: Evaluate the stability of the neighborhood around the BEST parameter.
        # If the immediate neighbors suffer a massive drop in Sharpe, the optimal point is a fragile overfit peak.
        neighbors = []
        if best_idx > 0:
            neighbors.append(sharpes[best_idx - 1])
        if best_idx < len(sharpes) - 1:
            neighbors.append(sharpes[best_idx + 1])
            
        max_degradation = 0.0
        if neighbors and best.sharpe_ratio > 0:
            worst_neighbor = min(neighbors)
            max_degradation = (best.sharpe_ratio - worst_neighbor) / best.sharpe_ratio

        # A parameter choice is robust if it sits on a plateau (degradation < threshold)
        is_robust = max_degradation <= self.max_neighborhood_degradation_pct

        if is_robust:
            msg = f"ROBUST PLATEAU: Parameter '{param_name}' neighborhood degradation is {max_degradation*100:.1f}%. Safe to deploy."
        else:
            msg = f"FRAGILE PEAK: Parameter '{param_name}' suffers a {max_degradation*100:.1f}% degradation in its immediate neighborhood. High risk of overfitting."
            logger.warning(msg)

        return SensitivityReport(
            total_grid_points=len(grid_results),
            best_sharpe=round(best.sharpe_ratio, 4),
            best_params=best.params,
            avg_sharpe=round(avg_s, 4),
            sharpe_std=round(std_s, 4),
            max_gradient=round(max_degradation, 4),  # Reusing this field to store degradation score
            is_robust=is_robust,
            message=msg,
        )

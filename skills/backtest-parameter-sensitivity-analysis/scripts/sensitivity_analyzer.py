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

    def __init__(self, max_gradient_threshold: float = 2.0):
        self.max_gradient_threshold = max_gradient_threshold

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

        # Compute max absolute gradient between adjacent grid points
        max_grad = 0.0
        for i in range(1, len(grid_results)):
            p1 = grid_results[i-1].params[param_name]
            p2 = grid_results[i].params[param_name]
            dp = abs(p2 - p1)
            if dp > 0:
                grad = abs(sharpes[i] - sharpes[i-1]) / dp
                max_grad = max(max_grad, grad)

        is_robust = (max_grad <= self.max_gradient_threshold) and (std_s < avg_s * 0.5 if avg_s > 0 else True)

        if is_robust:
            msg = f"Parameter '{param_name}' is ROBUST: max gradient {max_grad:.2f}, Sharpe std {std_s:.2f}."
        else:
            msg = f"FRAGILE PARAMETER '{param_name}': max gradient {max_grad:.2f} exceeds threshold. Likely overfit."
            logger.warning(msg)

        return SensitivityReport(
            total_grid_points=len(grid_results),
            best_sharpe=round(best.sharpe_ratio, 4),
            best_params=best.params,
            avg_sharpe=round(avg_s, 4),
            sharpe_std=round(std_s, 4),
            max_gradient=round(max_grad, 4),
            is_robust=is_robust,
            message=msg,
        )

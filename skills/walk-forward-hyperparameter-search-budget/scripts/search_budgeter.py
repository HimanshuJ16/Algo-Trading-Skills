"""
walk-forward-hyperparameter-search-budget: Hyperparameter search space bounder,
grid pruner, and walk-forward overfitting risk auditor.
"""
from dataclasses import dataclass
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class SearchBudgetReport:
    raw_combinations: int
    allowed_budget_max: int
    sampled_evaluations: int
    is_budget_exceeded: bool
    pruning_applied: bool
    overfitting_risk_level: str  # LOW, MODERATE, HIGH
    message: str


class HyperparameterSearchBudgeter:
    """
    Bounds hyperparameter search space in walk-forward validation to limit
    the Probability of Backtest Overfitting (PBO).
    """

    def __init__(self, max_trials_per_year: int = 100):
        self.max_trials_per_year = max_trials_per_year

    def compute_max_budget(self, in_sample_days: int) -> int:
        """
        Calculates maximum allowed parameter evaluation budget based on in-sample dataset length.
        """
        years = max(0.1, in_sample_days / 252.0)
        max_budget = int(years * self.max_trials_per_year)
        return max(10, min(500, max_budget))

    def audit_and_prune(
        self,
        parameter_grid: Dict[str, List[Any]],
        in_sample_days: int,
    ) -> Tuple[List[Dict[str, Any]], SearchBudgetReport]:
        """
        Audits total grid size, computes allowed budget, and prunes/samples grid if required.
        """
        # Calculate Cartesian product size
        raw_combinations = 1
        for vals in parameter_grid.values():
            raw_combinations *= len(vals)

        max_allowed = self.compute_max_budget(in_sample_days)
        is_exceeded = raw_combinations > max_allowed

        if is_exceeded:
            risk = "HIGH" if raw_combinations > max_allowed * 5 else "MODERATE"
            msg = f"SEARCH BUDGET OVERRUN: {raw_combinations} combinations > max allowed {max_allowed} for {in_sample_days} days. Pruning applied."
            logger.warning(msg)
            pruned = True
            # Systematic step sampling to bring under max_allowed
            sample_step = math.ceil(raw_combinations / max_allowed)
            # Generate deterministic subset
            combinations = self._generate_flat_grid(parameter_grid)
            sampled_combinations = combinations[::sample_step][:max_allowed]
        else:
            risk = "LOW"
            msg = f"Search budget OK: {raw_combinations} combinations <= max allowed {max_allowed}."
            logger.info(msg)
            pruned = False
            sampled_combinations = self._generate_flat_grid(parameter_grid)

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

    def _generate_flat_grid(self, grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
        import itertools
        keys = list(grid.keys())
        values = list(grid.values())
        flat = []
        for combo in itertools.product(*values):
            flat.append(dict(zip(keys, combo)))
        return flat

"""
monte-carlo-strategy-robustness-testing: Production-grade Monte Carlo trade sequence shuffler,
bootstrap resampler, execution noise perturbator, and Risk of Ruin evaluator.
"""
from dataclasses import dataclass
import logging
import math
import random
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MonteCarloError(ValueError):
    """Raised when trade log is insufficient for Monte Carlo simulation."""
    pass


@dataclass
class MonteCarloResult:
    num_simulations: int
    median_final_equity: float
    p95_max_drawdown: float
    p99_max_drawdown: float
    risk_of_ruin_pct: float
    is_robust: bool  # True if risk_of_ruin_pct <= 1.0% and p95_max_drawdown <= limit


class MonteCarloRobustnessEngine:
    """
    Evaluates trading strategy robustness against trade sequence permutation,
    bootstrap sampling with replacement, and price execution noise.
    """

    def __init__(
        self,
        initial_capital: float = 100000.0,
        max_drawdown_limit: float = 0.25,
        num_simulations: int = 1000,
        seed: Optional[int] = 42,
    ):
        self.initial_capital = initial_capital
        self.max_drawdown_limit = max_drawdown_limit
        self.num_simulations = num_simulations
        if seed is not None:
            random.seed(seed)

    def _compute_equity_curve_metrics(self, trade_returns: List[float]) -> Tuple[float, float]:
        """
        Computes final equity and max drawdown for a single trade sequence.
        trade_returns expressed as fractional returns (e.g. 0.02 for +2%, -0.01 for -1%).
        """
        equity = self.initial_capital
        peak = equity
        max_dd = 0.0

        for r in trade_returns:
            equity *= (1.0 + r)
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak
            if dd > max_dd:
                max_dd = dd

        return equity, max_dd

    def run_sequence_shuffling(self, trade_returns: List[float]) -> MonteCarloResult:
        """
        Runs Monte Carlo trade sequence permutations (shuffling without replacement).
        """
        if len(trade_returns) < 5:
            raise MonteCarloError(f"Insufficient trade history ({len(trade_returns)} trades). Min 5 required.")

        final_equities: List[float] = []
        max_drawdowns: List[float] = []
        ruin_count = 0

        for _ in range(self.num_simulations):
            shuffled_returns = list(trade_returns)
            random.shuffle(shuffled_returns)

            eq, max_dd = self._compute_equity_curve_metrics(shuffled_returns)
            final_equities.append(eq)
            max_drawdowns.append(max_dd)

            if max_dd >= self.max_drawdown_limit:
                ruin_count += 1

        final_equities.sort()
        max_drawdowns.sort()

        median_eq = final_equities[int(0.50 * self.num_simulations)]
        p95_dd = max_drawdowns[int(0.95 * self.num_simulations)]
        p99_dd = max_drawdowns[int(0.99 * self.num_simulations)]
        ruin_pct = (ruin_count / self.num_simulations) * 100.0

        is_robust = (ruin_pct <= 1.0) and (p95_dd <= self.max_drawdown_limit)

        logger.info(
            f"Monte Carlo Sequence Shuffling ({self.num_simulations} runs): "
            f"Median Equity: ${median_eq:,.2f}, 95th DD: {p95_dd:.1%}, Ruin Pct: {ruin_pct:.2f}% (Robust: {is_robust})"
        )

        return MonteCarloResult(
            num_simulations=self.num_simulations,
            median_final_equity=median_eq,
            p95_max_drawdown=p95_dd,
            p99_max_drawdown=p99_dd,
            risk_of_ruin_pct=ruin_pct,
            is_robust=is_robust,
        )

    def run_bootstrap_resampling(self, trade_returns: List[float]) -> MonteCarloResult:
        """
        Runs Monte Carlo bootstrap resampling (sampling with replacement).
        """
        if len(trade_returns) < 5:
            raise MonteCarloError("Insufficient trade history for bootstrap resampling.")

        n_trades = len(trade_returns)
        final_equities: List[float] = []
        max_drawdowns: List[float] = []
        ruin_count = 0

        for _ in range(self.num_simulations):
            bootstrapped = [random.choice(trade_returns) for _ in range(n_trades)]
            eq, max_dd = self._compute_equity_curve_metrics(bootstrapped)
            final_equities.append(eq)
            max_drawdowns.append(max_dd)

            if max_dd >= self.max_drawdown_limit:
                ruin_count += 1

        final_equities.sort()
        max_drawdowns.sort()

        median_eq = final_equities[int(0.50 * self.num_simulations)]
        p95_dd = max_drawdowns[int(0.95 * self.num_simulations)]
        p99_dd = max_drawdowns[int(0.99 * self.num_simulations)]
        ruin_pct = (ruin_count / self.num_simulations) * 100.0

        is_robust = (ruin_pct <= 1.0) and (p95_dd <= self.max_drawdown_limit)

        logger.info(
            f"Monte Carlo Bootstrap ({self.num_simulations} runs): "
            f"Median Equity: ${median_eq:,.2f}, 95th DD: {p95_dd:.1%}, Ruin Pct: {ruin_pct:.2f}% (Robust: {is_robust})"
        )

        return MonteCarloResult(
            num_simulations=self.num_simulations,
            median_final_equity=median_eq,
            p95_max_drawdown=p95_dd,
            p99_max_drawdown=p99_dd,
            risk_of_ruin_pct=ruin_pct,
            is_robust=is_robust,
        )

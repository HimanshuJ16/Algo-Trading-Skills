"""
benchmark-relative-performance-attribution: Production-grade Alpha/Beta performance attribution,
Tracking Error & Information Ratio calculator, and Brinson-Fachler sector attribution engine.
"""
from dataclasses import dataclass
import logging
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class AttributionError(ValueError):
    """Raised when return series length mismatch or empty data occurs."""
    pass


@dataclass
class AttributionSummary:
    alpha_annualized: float
    beta: float
    tracking_error_annualized: float
    information_ratio: float
    active_return_annualized: float
    is_alpha_positive: bool


@dataclass
class BrinsonSectorResult:
    sector: str
    allocation_effect: float
    selection_effect: float
    interaction_effect: float
    total_effect: float


class PerformanceAttributionEngine:
    """
    Decomposes total portfolio performance relative to a benchmark index into
    Alpha, Beta, Tracking Error, Information Ratio, and Brinson-Fachler sector effects.
    """

    def __init__(self, risk_free_rate: float = 0.0, annualization_factor: int = 252):
        self.risk_free_rate = risk_free_rate
        self.annualization_factor = annualization_factor

    def evaluate_alpha_beta(
        self, portfolio_returns: List[float], benchmark_returns: List[float]
    ) -> AttributionSummary:
        """
        Calculates Beta, Annualized Alpha, Tracking Error, and Information Ratio.
        """
        if len(portfolio_returns) != len(benchmark_returns):
            raise AttributionError(
                f"Length mismatch: portfolio returns ({len(portfolio_returns)}) vs benchmark returns ({len(benchmark_returns)})."
            )

        if len(portfolio_returns) < 5:
            raise AttributionError("Insufficient return samples. Min 5 required.")

        n = len(portfolio_returns)
        mean_p = statistics.mean(portfolio_returns)
        mean_b = statistics.mean(benchmark_returns)

        # Variance of benchmark
        var_b = statistics.variance(benchmark_returns)
        if var_b <= 1e-12:
            beta = 1.0
        else:
            # Covariance
            cov_pb = sum((portfolio_returns[i] - mean_p) * (benchmark_returns[i] - mean_b) for i in range(n)) / (n - 1)
            beta = cov_pb / var_b

        # Annualized Alpha (CAPM model: R_p - R_f = alpha + beta*(R_b - R_f))
        rf_daily = self.risk_free_rate / self.annualization_factor
        alpha_daily = (mean_p - rf_daily) - beta * (mean_b - rf_daily)
        alpha_annualized = alpha_daily * self.annualization_factor

        # Active Return & Tracking Error
        active_returns = [portfolio_returns[i] - benchmark_returns[i] for i in range(n)]
        mean_active = statistics.mean(active_returns)
        active_annualized = mean_active * self.annualization_factor

        te_daily = statistics.stdev(active_returns) if n > 1 else 0.0
        te_annualized = te_daily * math.sqrt(self.annualization_factor)

        if te_annualized <= 1e-6:
            information_ratio = 0.0
        else:
            information_ratio = active_annualized / te_annualized

        is_alpha_pos = alpha_annualized > 0.0

        logger.info(
            f"Attribution Summary: Alpha: {alpha_annualized:.2%}, Beta: {beta:.2f}, TE: {te_annualized:.2%}, IR: {information_ratio:.2f}"
        )

        return AttributionSummary(
            alpha_annualized=alpha_annualized,
            beta=beta,
            tracking_error_annualized=te_annualized,
            information_ratio=information_ratio,
            active_return_annualized=active_annualized,
            is_alpha_positive=is_alpha_pos,
        )

    def compute_brinson_attribution(
        self,
        portfolio_weights: Dict[str, float],
        benchmark_weights: Dict[str, float],
        portfolio_sector_returns: Dict[str, float],
        benchmark_sector_returns: Dict[str, float],
        total_benchmark_return: float,
    ) -> List[BrinsonSectorResult]:
        """
        Computes Brinson-Fachler sector allocation, selection, and interaction effects.
        """
        all_sectors = set(portfolio_weights.keys()).union(set(benchmark_weights.keys()))
        results: List[BrinsonSectorResult] = []

        for sector in sorted(all_sectors):
            wp = portfolio_weights.get(sector, 0.0)
            wb = benchmark_weights.get(sector, 0.0)
            rp = portfolio_sector_returns.get(sector, 0.0)
            rb = benchmark_sector_returns.get(sector, 0.0)

            # Brinson-Fachler Allocation Effect: (wp - wb) * (rb - R_b_total)
            alloc = (wp - wb) * (rb - total_benchmark_return)

            # Selection Effect: wb * (rp - rb)
            select = wb * (rp - rb)

            # Interaction Effect: (wp - wb) * (rp - rb)
            inter = (wp - wb) * (rp - rb)

            total_eff = alloc + select + inter

            results.append(
                BrinsonSectorResult(
                    sector=sector,
                    allocation_effect=alloc,
                    selection_effect=select,
                    interaction_effect=inter,
                    total_effect=total_eff,
                )
            )

        return results

"""
correlation-aware-exposure-limits: Production-grade rolling correlation matrix calculator,
hierarchical correlation cluster engine, and cluster exposure guard.
"""
from dataclasses import dataclass, field
import logging
import math
import statistics
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class CorrelationLimitBreachError(ValueError):
    """Raised when an order breaches maximum allowed correlation cluster exposure."""
    pass


@dataclass
class ClusterInfo:
    cluster_id: str
    symbols: List[str]
    current_exposure_usd: float
    current_exposure_pct: float


@dataclass
class OrderValidationResult:
    approved: bool
    symbol: str
    proposed_value_usd: float
    cluster_id: Optional[str]
    projected_cluster_exposure_pct: float
    rejection_reason: Optional[str]


class CorrelationExposureManager:
    """
    Computes rolling correlation matrices, forms asset correlation clusters,
    and enforces maximum cluster exposure limits against total portfolio NAV.
    """

    def __init__(
        self,
        correlation_threshold: float = 0.70,
        max_cluster_exposure_pct: float = 0.30,
    ):
        self.correlation_threshold = correlation_threshold
        self.max_cluster_exposure_pct = max_cluster_exposure_pct

    @staticmethod
    def _compute_pearson_corr(r1: List[float], r2: List[float]) -> float:
        """Computes Pearson correlation coefficient between two return vectors."""
        n = len(r1)
        if n != len(r2) or n < 3:
            return 0.0

        m1 = statistics.mean(r1)
        m2 = statistics.mean(r2)
        v1 = statistics.variance(r1)
        v2 = statistics.variance(r2)

        if v1 <= 1e-12 or v2 <= 1e-12:
            return 0.0

        cov = sum((r1[i] - m1) * (r2[i] - m2) for i in range(n)) / (n - 1)
        corr = cov / (math.sqrt(v1) * math.sqrt(v2))
        return max(min(corr, 1.0), -1.0)

    def compute_correlation_matrix(
        self, returns_dict: Dict[str, List[float]]
    ) -> Dict[Tuple[str, str], float]:
        """Computes pairwise correlation matrix across all symbols."""
        symbols = sorted(returns_dict.keys())
        corr_matrix: Dict[Tuple[str, str], float] = {}

        for i in range(len(symbols)):
            s1 = symbols[i]
            corr_matrix[(s1, s1)] = 1.0
            for j in range(i + 1, len(symbols)):
                s2 = symbols[j]
                corr = self._compute_pearson_corr(returns_dict[s1], returns_dict[s2])
                corr_matrix[(s1, s2)] = corr
                corr_matrix[(s2, s1)] = corr
        return corr_matrix

    def form_clusters(self, returns_dict: Dict[str, List[float]]) -> List[List[str]]:
        """
        Groups symbols into connected correlation clusters where pairwise correlation >= threshold.
        """
        symbols = sorted(returns_dict.keys())
        corr_matrix = self.compute_correlation_matrix(returns_dict)
        visited: Set[str] = set()
        clusters: List[List[str]] = []

        for s in symbols:
            if s not in visited:
                cluster: List[str] = []
                queue = [s]
                visited.add(s)

                while queue:
                    curr = queue.pop(0)
                    cluster.append(curr)
                    for neighbor in symbols:
                        if neighbor not in visited:
                            if corr_matrix.get((curr, neighbor), 0.0) >= self.correlation_threshold:
                                visited.add(neighbor)
                                queue.append(neighbor)
                clusters.append(sorted(cluster))
        return clusters

    def validate_order_exposure(
        self,
        symbol: str,
        proposed_value_usd: float,
        current_positions: Dict[str, float],  # symbol -> position quantity
        current_prices: Dict[str, float],     # symbol -> price USD
        returns_dict: Dict[str, List[float]],
        portfolio_nav: float,
    ) -> OrderValidationResult:
        """
        Validates proposed order value against correlation cluster exposure limits.
        """
        if portfolio_nav <= 0:
            raise CorrelationLimitBreachError(f"Invalid portfolio NAV: ${portfolio_nav}")

        # 1. Form clusters
        all_symbols = set(returns_dict.keys()).union(set(current_positions.keys())).union({symbol})
        # Fill missing returns with 0s if necessary
        clean_returns = {s: returns_dict.get(s, [0.0] * 10) for s in all_symbols}
        clusters = self.form_clusters(clean_returns)

        # 2. Find cluster for target symbol
        target_cluster: List[str] = [symbol]
        target_cluster_id = None
        for idx, cl in enumerate(clusters):
            if symbol in cl:
                target_cluster = cl
                target_cluster_id = f"Cluster_{idx+1}"
                break

        # 3. Calculate current cluster dollar exposure
        current_cluster_usd = 0.0
        for s in target_cluster:
            qty = current_positions.get(s, 0.0)
            price = current_prices.get(s, 0.0)
            current_cluster_usd += abs(qty * price)

        # 4. Compute projected cluster exposure
        projected_cluster_usd = current_cluster_usd + abs(proposed_value_usd)
        projected_pct = projected_cluster_usd / portfolio_nav

        if projected_pct > self.max_cluster_exposure_pct:
            reason = (
                f"CORRELATION CLUSTER BREACH: Projected exposure {projected_pct:.1%} for {target_cluster_id} "
                f"({', '.join(target_cluster)}) exceeds max limit {self.max_cluster_exposure_pct:.1%} NAV."
            )
            logger.warning(reason)
            return OrderValidationResult(
                approved=False,
                symbol=symbol,
                proposed_value_usd=proposed_value_usd,
                cluster_id=target_cluster_id,
                projected_cluster_exposure_pct=projected_pct,
                rejection_reason=reason,
            )

        logger.info(
            f"Order Approved [{symbol}]: Projected cluster exposure {projected_pct:.1%} <= max {self.max_cluster_exposure_pct:.1%} NAV."
        )

        return OrderValidationResult(
            approved=True,
            symbol=symbol,
            proposed_value_usd=proposed_value_usd,
            cluster_id=target_cluster_id,
            projected_cluster_exposure_pct=projected_pct,
            rejection_reason=None,
        )

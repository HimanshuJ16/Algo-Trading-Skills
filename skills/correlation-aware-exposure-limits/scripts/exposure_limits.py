"""
correlation-aware-exposure-limits: Production-grade cluster exposure management,
rolling correlation matrix calculation, options delta factor aggregation, staleness validation,
and position size scaling.
"""
from dataclasses import dataclass, field
import datetime
import math
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RiskCheckResult:
    approved: bool
    proposed_notional: float
    allowed_notional: float
    reason: str
    cluster_id: Optional[str] = None
    current_cluster_exposure: float = 0.0
    max_cluster_limit: float = 0.0


@dataclass
class PositionAuditLog:
    timestamp: str
    symbol: str
    proposed_notional: float
    approved_notional: float
    approved: bool
    cluster_symbols: List[str]
    reason: str


class CorrelationExposureManager:
    """
    Manages correlation-aware risk checks, cluster caps, and options factor delta aggregation.
    """

    def __init__(
        self,
        correlation_threshold: float = 0.7,
        max_cluster_notional: float = 1_000_000.0,
        max_portfolio_notional: float = 3_000_000.0,
        sector_mapping: Optional[Dict[str, str]] = None,
        max_matrix_age_days: float = 7.0,
    ):
        self.correlation_threshold = correlation_threshold
        self.max_cluster_notional = max_cluster_notional
        self.max_portfolio_notional = max_portfolio_notional
        self.sector_mapping = sector_mapping or {}
        self.max_matrix_age_days = max_matrix_age_days

        self.corr_matrix: Dict[Tuple[str, str], float] = {}
        self.matrix_timestamp: Optional[datetime.datetime] = None
        self.clusters: List[Set[str]] = []
        self.audit_trail: List[PositionAuditLog] = []

    def update_correlation_matrix(
        self,
        price_history: Dict[str, List[float]],
        timestamp: Optional[datetime.datetime] = None,
    ):
        """
        Computes rolling Pearson correlation matrix from price history dictionary {symbol: [prices]}.
        """
        symbols = list(price_history.keys())
        returns: Dict[str, List[float]] = {}

        for sym, prices in price_history.items():
            if len(prices) < 2:
                continue
            returns[sym] = [
                (prices[i] - prices[i - 1]) / prices[i - 1]
                for i in range(1, len(prices))
                if prices[i - 1] != 0
            ]

        self.corr_matrix = {}
        valid_symbols = list(returns.keys())

        for i in range(len(valid_symbols)):
            s1 = valid_symbols[i]
            r1 = returns[s1]
            mean1 = sum(r1) / len(r1) if r1 else 0
            var1 = sum((x - mean1) ** 2 for x in r1)

            for j in range(i, len(valid_symbols)):
                s2 = valid_symbols[j]
                if s1 == s2:
                    self.corr_matrix[(s1, s2)] = 1.0
                    continue

                r2 = returns[s2]
                min_len = min(len(r1), len(r2))
                if min_len < 2:
                    self.corr_matrix[(s1, s2)] = 0.0
                    self.corr_matrix[(s2, s1)] = 0.0
                    continue

                m1 = sum(r1[:min_len]) / min_len
                m2 = sum(r2[:min_len]) / min_len

                cov = sum((r1[k] - m1) * (r2[k] - m2) for k in range(min_len))
                v1 = sum((r1[k] - m1) ** 2 for k in range(min_len))
                v2 = sum((r2[k] - m2) ** 2 for k in range(min_len))

                denom = math.sqrt(v1 * v2)
                corr = cov / denom if denom > 0 else 0.0
                self.corr_matrix[(s1, s2)] = corr
                self.corr_matrix[(s2, s1)] = corr

        self.matrix_timestamp = timestamp or datetime.datetime.now(datetime.timezone.utc)
        self._rebuild_clusters(valid_symbols)

    def _rebuild_clusters(self, symbols: List[str]):
        """Builds connected component clusters for correlation >= threshold."""
        visited = set()
        self.clusters = []

        for s in symbols:
            if s in visited:
                continue

            cluster = {s}
            queue = [s]
            visited.add(s)

            while queue:
                curr = queue.pop(0)
                for other in symbols:
                    if other not in visited:
                        corr = self.corr_matrix.get((curr, other), 0.0)
                        same_sector = (
                            self.sector_mapping.get(curr) == self.sector_mapping.get(other)
                            if (curr in self.sector_mapping and other in self.sector_mapping)
                            else False
                        )
                        if corr >= self.correlation_threshold or same_sector:
                            visited.add(other)
                            cluster.add(other)
                            queue.append(other)

            self.clusters.append(cluster)

    def check_staleness(self) -> bool:
        """Returns True if the correlation matrix is stale or missing."""
        if not self.matrix_timestamp:
            return True
        now = datetime.datetime.now(datetime.timezone.utc)
        age_days = (now - self.matrix_timestamp).total_seconds() / 86400.0
        return age_days > self.max_matrix_age_days

    def evaluate_proposed_position(
        self,
        symbol: str,
        proposed_notional: float,
        current_positions: Dict[str, float],
        underlying_delta_weights: Optional[Dict[str, float]] = None,
    ) -> RiskCheckResult:
        """
        Evaluates a proposed position against cluster caps and options delta factors.
        Returns a RiskCheckResult with approved status and scaled allowed notional.
        """
        if self.check_staleness():
            logger.warning("Correlation matrix is stale or uninitialized!")

        # 1. Total Portfolio Check
        current_total_notional = sum(abs(v) for v in current_positions.values())
        if current_total_notional + abs(proposed_notional) > self.max_portfolio_notional:
            max_allowed = max(0.0, self.max_portfolio_notional - current_total_notional)
            res = RiskCheckResult(
                approved=False,
                proposed_notional=proposed_notional,
                allowed_notional=max_allowed,
                reason=f"Breaches max portfolio cap ({self.max_portfolio_notional})",
            )
            self._log_audit(symbol, proposed_notional, res)
            return res

        # 2. Cluster Exposure Check
        target_cluster = set()
        for cl in self.clusters:
            if symbol in cl:
                target_cluster = cl
                break
        if not target_cluster:
            target_cluster = {symbol}

        cluster_notional = sum(
            abs(v) for k, v in current_positions.items() if k in target_cluster
        )

        # Options Delta Exposure adjustment if provided
        effective_proposed = abs(proposed_notional)
        if underlying_delta_weights and symbol in underlying_delta_weights:
            effective_proposed *= abs(underlying_delta_weights[symbol])

        if cluster_notional + effective_proposed > self.max_cluster_notional:
            remaining_cluster_cap = max(0.0, self.max_cluster_notional - cluster_notional)
            approved = effective_proposed <= remaining_cluster_cap
            res = RiskCheckResult(
                approved=approved,
                proposed_notional=proposed_notional,
                allowed_notional=remaining_cluster_cap,
                reason=f"Cluster exposure ({cluster_notional + effective_proposed:.2f}) exceeds max cluster limit ({self.max_cluster_notional:.2f})",
                cluster_id=",".join(sorted(target_cluster)),
                current_cluster_exposure=cluster_notional,
                max_cluster_limit=self.max_cluster_notional,
            )
            self._log_audit(symbol, proposed_notional, res)
            return res

        res = RiskCheckResult(
            approved=True,
            proposed_notional=proposed_notional,
            allowed_notional=proposed_notional,
            reason="Approved within correlation cluster limits",
            cluster_id=",".join(sorted(target_cluster)),
            current_cluster_exposure=cluster_notional,
            max_cluster_limit=self.max_cluster_notional,
        )
        self._log_audit(symbol, proposed_notional, res)
        return res

    def _log_audit(self, symbol: str, proposed: float, res: RiskCheckResult):
        entry = PositionAuditLog(
            timestamp=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            symbol=symbol,
            proposed_notional=proposed,
            approved_notional=res.allowed_notional,
            approved=res.approved,
            cluster_symbols=res.cluster_id.split(",") if res.cluster_id else [symbol],
            reason=res.reason,
        )
        self.audit_trail.append(entry)


# Backward compatibility functions
def cluster_by_correlation(corr_matrix: dict, threshold=0.7):
    """corr_matrix: {(a, b): correlation}. Returns list of clusters (sets)."""
    symbols = set()
    for a, b in corr_matrix:
        symbols.add(a)
        symbols.add(b)
    clusters = []
    assigned = set()
    for s in symbols:
        if s in assigned:
            continue
        cluster = {s}
        for other in symbols:
            if other != s and corr_matrix.get((s, other), corr_matrix.get((other, s), 0)) >= threshold:
                cluster.add(other)
        clusters.append(cluster)
        assigned |= cluster
    return clusters


def cluster_exposure(positions: dict, cluster: set):
    return sum(abs(v) for k, v in positions.items() if k in cluster)

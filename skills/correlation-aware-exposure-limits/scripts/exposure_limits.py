"""
correlation-aware-exposure-limits: cluster exposure management,
rolling correlation matrix calculation, options delta factor aggregation,
staleness validation, and position size scaling.

Fail-closed design: an order is only evaluated against cluster caps when a
usable correlation matrix exists. A missing matrix always raises; a stale
matrix raises or warns depending on ``stale_matrix_policy``.
"""
from dataclasses import dataclass
import datetime
import logging
import math
import threading
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class CorrelationMatrixUnavailableError(RuntimeError):
    """Raised when no correlation matrix has been built, or when it is stale
    under the ``stale_matrix_policy="block"`` setting. Cluster limits cannot
    be evaluated, so the risk check fails closed instead of silently
    treating every symbol as uncorrelated."""


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
    Manages correlation-aware risk checks, cluster caps, and options factor
    delta aggregation.

    Exposure convention: cluster and portfolio exposure are GROSS (sum of
    absolute notionals), deliberately. Netting a long against a short inside
    a correlated cluster assumes the correlation hedge holds exactly when it
    matters — correlations converge toward 1 in stress, so gross is the
    conservative basis for concentration limits. Delta weights (when
    supplied) convert options notionals to underlying-equivalent exposure on
    both existing positions and the proposed increment.

    Thread safety: matrix updates and evaluations are serialized internally
    (a check never reads a half-rebuilt cluster list). The caller's own
    order book / position state is NOT protected — serialize
    check-then-trade sequences at the caller if orders can arrive
    concurrently.
    """

    def __init__(
        self,
        correlation_threshold: float = 0.7,
        max_cluster_notional: float = 1_000_000.0,
        max_portfolio_notional: float = 3_000_000.0,
        sector_mapping: Optional[Dict[str, str]] = None,
        max_matrix_age_days: float = 7.0,
        stale_matrix_policy: str = "warn",
    ):
        if not isinstance(correlation_threshold, (int, float)) or not (
            -1.0 <= correlation_threshold <= 1.0
        ):
            raise ValueError(
                f"correlation_threshold must be in [-1, 1], got {correlation_threshold!r}"
            )
        for name, value in (
            ("max_cluster_notional", max_cluster_notional),
            ("max_portfolio_notional", max_portfolio_notional),
        ):
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number, got {value!r}")
        if not isinstance(max_matrix_age_days, (int, float)) or max_matrix_age_days <= 0:
            raise ValueError(
                f"max_matrix_age_days must be a positive number, got {max_matrix_age_days!r}"
            )
        if stale_matrix_policy not in ("warn", "block"):
            raise ValueError(
                f"stale_matrix_policy must be 'warn' or 'block', got {stale_matrix_policy!r}"
            )
        self.correlation_threshold = correlation_threshold
        self.max_cluster_notional = max_cluster_notional
        self.max_portfolio_notional = max_portfolio_notional
        self.sector_mapping = sector_mapping or {}
        self.max_matrix_age_days = max_matrix_age_days
        self.stale_matrix_policy = stale_matrix_policy

        self.corr_matrix: Dict[Tuple[str, str], float] = {}
        self.matrix_timestamp: Optional[datetime.datetime] = None
        self.clusters: List[Set[str]] = []
        self.audit_trail: List[PositionAuditLog] = []
        # Guards matrix/clusters/audit_trail so a pre-trade check on one
        # thread never reads a half-rebuilt cluster list during an update on
        # another. Order book state itself is the caller's responsibility.
        self._lock = threading.Lock()

    def update_correlation_matrix(
        self,
        price_history: Dict[str, List[float]],
        timestamp: Optional[datetime.datetime] = None,
    ):
        """
        Computes rolling Pearson correlation matrix from price history
        dictionary {symbol: [prices]}.

        Contract: price series must be chronological (oldest first), date
        aligned at their most recent point, and every price a positive finite
        number. Series of different lengths are correlated over their most
        recent overlapping returns (e.g. a recently listed symbol with 30
        days of history is correlated against the last 30 returns of the
        60-day names), with a warning logged — aligning at the oldest end
        would compare returns from different dates.
        """
        if not price_history:
            raise ValueError("price_history must contain at least one symbol")
        if timestamp is not None and (
            not isinstance(timestamp, datetime.datetime)
            or timestamp.tzinfo is None
        ):
            raise ValueError("timestamp must be a timezone-aware datetime.datetime")

        lengths = {sym: len(prices) for sym, prices in price_history.items()}
        for sym, prices in price_history.items():
            if not isinstance(prices, (list, tuple)) or len(prices) < 2:
                raise ValueError(
                    f"price history for {sym} must contain at least 2 prices, "
                    f"got {len(prices) if isinstance(prices, (list, tuple)) else type(prices).__name__}"
                )
            for p in prices:
                if not isinstance(p, (int, float)) or not math.isfinite(p) or p <= 0:
                    raise ValueError(
                        f"price history for {sym} contains a non-positive or "
                        f"non-finite price ({p!r}); refusing to correlate on bad data"
                    )
        if len(set(lengths.values())) > 1:
            logger.warning(
                "Price history lengths differ (%s); pairwise correlations use the "
                "most recent overlapping returns of each pair.", lengths
            )

        returns: Dict[str, List[float]] = {
            sym: [(prices[i] - prices[i - 1]) / prices[i - 1]
                  for i in range(1, len(prices))]
            for sym, prices in price_history.items()
        }

        corr_values: Dict[Tuple[str, str], float] = {}
        valid_symbols = sorted(returns.keys())

        for i, s1 in enumerate(valid_symbols):
            for j in range(i, len(valid_symbols)):
                s2 = valid_symbols[j]
                if s1 == s2:
                    corr_values[(s1, s2)] = 1.0
                    continue

                r1, r2 = returns[s1], returns[s2]
                min_len = min(len(r1), len(r2))
                a, b = r1[-min_len:], r2[-min_len:]

                m1 = sum(a) / min_len
                m2 = sum(b) / min_len
                cov = sum((a[k] - m1) * (b[k] - m2) for k in range(min_len))
                v1 = sum((a[k] - m1) ** 2 for k in range(min_len))
                v2 = sum((b[k] - m2) ** 2 for k in range(min_len))

                denom = math.sqrt(v1 * v2)
                if denom == 0:
                    logger.warning(
                        "Correlation between %s and %s is undefined over the "
                        "overlap (zero variance in at least one series, e.g. a "
                        "constant or pegged price); treating as uncorrelated (0.0).",
                        s1, s2,
                    )
                    corr = 0.0
                else:
                    corr = cov / denom
                    corr = max(-1.0, min(1.0, corr))
                corr_values[(s1, s2)] = corr
                corr_values[(s2, s1)] = corr

        with self._lock:
            self.corr_matrix = corr_values
            self.matrix_timestamp = timestamp or datetime.datetime.now(
                datetime.timezone.utc
            )
            self._rebuild_clusters(valid_symbols)

    def _rebuild_clusters(self, symbols: List[str]):
        """Builds connected-component clusters: two symbols share a cluster
        when directly correlated >= threshold or both carry the same non-None
        sector label (sector co-membership is treated as one risk pocket
        regardless of measured correlation)."""
        visited: Set[str] = set()
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
                        sector_a = self.sector_mapping.get(curr)
                        sector_b = self.sector_mapping.get(other)
                        same_sector = (
                            sector_a is not None
                            and sector_a == sector_b
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

    def _require_usable_matrix(self) -> None:
        if self.matrix_timestamp is None:
            raise CorrelationMatrixUnavailableError(
                "No correlation matrix has been built yet - call "
                "update_correlation_matrix() before evaluating positions. "
                "Without it, cluster limits cannot be enforced."
            )
        if self.check_staleness():
            message = (
                f"Correlation matrix is stale (older than "
                f"{self.max_matrix_age_days} days)."
            )
            if self.stale_matrix_policy == "block":
                raise CorrelationMatrixUnavailableError(
                    message + " stale_matrix_policy='block' rejects risk checks "
                    "on stale data."
                )
            logger.warning(
                "%s Proceeding under stale_matrix_policy='warn'; production "
                "deployments should use 'block'.", message,
            )

    def evaluate_proposed_position(
        self,
        symbol: str,
        proposed_notional: float,
        current_positions: Dict[str, float],
        underlying_delta_weights: Optional[Dict[str, float]] = None,
    ) -> RiskCheckResult:
        """
        Evaluates a proposed position change against the portfolio cap and
        cluster caps.

        Contract:
        - ``current_positions`` maps symbol -> signed notional of the current
          book. It MAY include ``symbol`` itself; if it does, the check uses
          the exact post-trade exposure ``|existing + delta|`` so that
          risk-REDUCING orders are not spuriously vetoed. If ``symbol`` is
          absent, the proposal is treated as a new position of ``|delta|``.
        - ``proposed_notional`` is the signed increment (positive = buy).
        - ``underlying_delta_weights`` maps symbol -> absolute delta in [0, 1]
          (e.g. 0.5 for an ATM call). Weights apply to existing positions and
          the proposed increment alike, converting option notional to
          underlying-equivalent exposure.

        Returns a RiskCheckResult; ``allowed_notional`` on a veto is the
        indicative raw increment that would fit under the cluster cap
        (remaining cap / delta weight).

        Veto rule is never allowed to block de-risking: when a cluster or
        the portfolio is ALREADY over its cap, an order that strictly
        reduces that exposure is approved (flagged in ``reason`` for
        remediation); only exposure-increasing or neutral orders are vetoed.
        """
        if not isinstance(symbol, str) or not symbol:
            raise ValueError("symbol must be a non-empty string")
        if (
            not isinstance(proposed_notional, (int, float))
            or isinstance(proposed_notional, bool)
            or not math.isfinite(proposed_notional)
        ):
            raise ValueError(
                f"proposed_notional must be a finite number, got {proposed_notional!r}"
            )
        if not isinstance(current_positions, dict):
            raise TypeError("current_positions must be a dict of symbol -> signed notional")
        for pos_sym, v in current_positions.items():
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v):
                raise ValueError(
                    f"current position for {pos_sym} must be a finite number, got {v!r}"
                )
        weights = underlying_delta_weights or {}
        for w_sym, w in weights.items():
            if not isinstance(w, (int, float)) or not math.isfinite(w) or not (0.0 <= abs(w) <= 1.0):
                raise ValueError(
                    f"underlying delta weight for {w_sym} must be a finite number "
                    f"in [0, 1], got {w!r}"
                )

        with self._lock:
            self._require_usable_matrix()
            clusters_snapshot: List[Set[str]] = [set(c) for c in self.clusters]

        def weight_of(sym: str) -> float:
            return abs(weights.get(sym, 1.0))

        # 1. Total Portfolio Check (raw gross notional, no delta adjustment)
        pre_trade_total = sum(abs(v) for v in current_positions.values())
        post_trade_total = sum(
            abs(v + proposed_notional) if k == symbol else abs(v)
            for k, v in current_positions.items()
        )
        if post_trade_total > self.max_portfolio_notional and post_trade_total >= pre_trade_total:
            res = RiskCheckResult(
                approved=False,
                proposed_notional=proposed_notional,
                allowed_notional=max(
                    0.0, self.max_portfolio_notional - pre_trade_total
                ),
                reason=f"Breaches max portfolio cap ({self.max_portfolio_notional})",
            )
            self._log_audit(symbol, proposed_notional, res)
            return res
        if post_trade_total > self.max_portfolio_notional:
            logger.warning(
                "Order approved as risk-reducing, but portfolio gross notional "
                "(%.2f) remains over the cap (%.2f) - remediation required.",
                post_trade_total, self.max_portfolio_notional,
            )

        # 2. Cluster Exposure Check
        target_cluster: Set[str] = set()
        for cl in clusters_snapshot:
            if symbol in cl:
                target_cluster = cl
                break
        if not target_cluster:
            logger.warning(
                "Symbol %s not present in the correlation matrix; treating it "
                "as its own single-symbol cluster.", symbol,
            )
            target_cluster = {symbol}

        # Post-trade, delta-adjusted gross exposure of the cluster. If the
        # symbol already has a position in current_positions, the increment
        # nets against it (a reduction lowers exposure instead of adding).
        existing = current_positions.get(symbol, 0.0)
        others_cluster = sum(
            abs(v * weight_of(k))
            for k, v in current_positions.items()
            if k in target_cluster and k != symbol
        )
        pre_trade_cluster = others_cluster + abs(existing * weight_of(symbol))
        post_trade_cluster = others_cluster + abs(
            (existing + proposed_notional) * weight_of(symbol)
        )

        if post_trade_cluster > self.max_cluster_notional and post_trade_cluster < pre_trade_cluster:
            res = RiskCheckResult(
                approved=True,
                proposed_notional=proposed_notional,
                allowed_notional=proposed_notional,
                reason=(
                    f"Approved as risk-reducing; cluster exposure "
                    f"({post_trade_cluster:.2f}) remains over max cluster limit "
                    f"({self.max_cluster_notional:.2f}) - remediation required"
                ),
                cluster_id=",".join(sorted(target_cluster)),
                current_cluster_exposure=pre_trade_cluster,
                max_cluster_limit=self.max_cluster_notional,
            )
            self._log_audit(symbol, proposed_notional, res)
            return res

        if post_trade_cluster > self.max_cluster_notional:
            remaining_cluster_cap = max(
                0.0, self.max_cluster_notional - pre_trade_cluster
            )
            w = weight_of(symbol) or 1.0
            res = RiskCheckResult(
                approved=False,
                proposed_notional=proposed_notional,
                allowed_notional=remaining_cluster_cap / w,
                reason=(
                    f"Cluster exposure ({post_trade_cluster:.2f}) exceeds max "
                    f"cluster limit ({self.max_cluster_notional:.2f})"
                ),
                cluster_id=",".join(sorted(target_cluster)),
                current_cluster_exposure=pre_trade_cluster,
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
            current_cluster_exposure=pre_trade_cluster,
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
        with self._lock:
            self.audit_trail.append(entry)


# Backward compatibility functions
def cluster_by_correlation(corr_matrix: dict, threshold=0.7):
    """corr_matrix: {(a, b): correlation}. Returns connected-component
    clusters (sets) using the same transitive semantics as
    CorrelationExposureManager: a-b at/above threshold and b-c at/above
    threshold places a, b, c in one cluster even if a-c is below threshold."""
    symbols = set()
    for a, b in corr_matrix:
        symbols.add(a)
        symbols.add(b)

    def corr(a: str, b: str) -> float:
        return corr_matrix.get((a, b), corr_matrix.get((b, a), 0.0))

    clusters = []
    visited = set()
    for s in sorted(symbols):
        if s in visited:
            continue
        cluster = {s}
        queue = [s]
        visited.add(s)
        while queue:
            curr = queue.pop(0)
            for other in symbols:
                if other not in visited and corr(curr, other) >= threshold:
                    visited.add(other)
                    cluster.add(other)
                    queue.append(other)
        clusters.append(cluster)
    return clusters


def cluster_exposure(positions: dict, cluster: set):
    """Gross (sum of absolute) notional exposure of a cluster."""
    return sum(abs(v) for k, v in positions.items() if k in cluster)

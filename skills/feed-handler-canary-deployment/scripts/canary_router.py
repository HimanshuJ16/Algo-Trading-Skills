"""
feed-handler-canary-deployment: Symbol canary routing engine, shadow comparative price auditor,
and auto-rollback circuit breaker module.
"""
from dataclasses import dataclass, field
import hashlib
import logging
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class CanaryStatus(Enum):
    HEALTHY = "HEALTHY"
    AUDIT_FAIL = "AUDIT_FAIL"
    ROLLED_BACK = "ROLLED_BACK"


@dataclass
class CanaryRoutingDecision:
    symbol: str
    is_canary: bool
    version_tag: str


@dataclass
class CanaryAuditSummary:
    total_evaluated_ticks: int
    canary_symbols_count: int
    baseline_symbols_count: int
    price_mismatch_count: int
    error_count: int
    status: CanaryStatus
    message: str


class FeedHandlerCanaryRouter:
    """
    Routes ticker symbols between baseline (V_stable) and canary (V_canary) feed handlers,
    auditing price output diffs and managing automated rollbacks.
    """

    def __init__(
        self,
        canary_percentage: float = 10.0,
        canary_symbols: Optional[List[str]] = None,
        max_allowed_error_rate: float = 0.01,
    ):
        self.canary_percentage = canary_percentage
        self.canary_symbols_whitelist = set([s.upper() for s in (canary_symbols or [])])
        self.max_allowed_error_rate = max_allowed_error_rate

        self.is_rolled_back = False
        self.total_ticks_processed = 0
        self.price_mismatches = 0
        self.canary_errors = 0

    def route_symbol(self, symbol: str) -> CanaryRoutingDecision:
        """Determines whether a symbol is routed to V_canary or V_stable."""
        sym = symbol.upper()

        if self.is_rolled_back:
            return CanaryRoutingDecision(sym, False, "V_stable")

        # Explicit whitelist takes precedence
        if sym in self.canary_symbols_whitelist:
            return CanaryRoutingDecision(sym, True, "V_canary")

        # Hash-based percentage allocation
        hash_val = int(hashlib.md5(sym.encode("utf-8")).hexdigest(), 16)
        bucket = hash_val % 100

        is_canary = bucket < self.canary_percentage
        version = "V_canary" if is_canary else "V_stable"
        return CanaryRoutingDecision(sym, is_canary, version)

    def audit_tick_pair(
        self,
        symbol: str,
        price_stable: float,
        price_canary: float,
    ) -> bool:
        """
        Audits tick price output from V_canary against baseline V_stable.
        """
        self.total_ticks_processed += 1

        if price_stable <= 0:
            rel_diff = 0.0
        else:
            rel_diff = abs(price_canary - price_stable) / price_stable

        if rel_diff > 0.001:  # 0.1% price tolerance breach
            self.price_mismatches += 1
            logger.warning(
                f"Canary Price Audit Breach on {symbol}: Stable=${price_stable}, Canary=${price_canary} "
                f"(Diff: {rel_diff*100:.3f}%)."
            )

        # Check if error rate breaches rollback threshold
        error_rate = (self.price_mismatches + self.canary_errors) / max(1, self.total_ticks_processed)
        if error_rate > self.max_allowed_error_rate and self.total_ticks_processed >= 10:
            self._trigger_auto_rollback(f"Error rate {error_rate*100:.2f}% breached threshold {self.max_allowed_error_rate*100:.2f}%.")
            return False

        return True

    def record_canary_exception(self, symbol: str, err_msg: str) -> None:
        """Records an unhandled exception inside V_canary feed handler."""
        self.canary_errors += 1
        logger.error(f"Canary Exception on '{symbol}': {err_msg}")

        error_rate = (self.price_mismatches + self.canary_errors) / max(1, self.total_ticks_processed or 1)
        if error_rate > self.max_allowed_error_rate:
            self._trigger_auto_rollback(f"Canary exception logged: {err_msg}")

    def _trigger_auto_rollback(self, reason: str) -> None:
        self.is_rolled_back = True
        logger.error(f"AUTO-ROLLBACK TRIGGERED for Canary Feed Handler! Reason: {reason}. All symbols reverted to V_stable.")

    def get_audit_summary(self, universe_symbols: List[str]) -> CanaryAuditSummary:
        canary_cnt = 0
        baseline_cnt = 0
        for s in universe_symbols:
            d = self.route_symbol(s)
            if d.is_canary:
                canary_cnt += 1
            else:
                baseline_cnt += 1

        if self.is_rolled_back:
            status = CanaryStatus.ROLLED_BACK
            msg = "Canary deployment rolled back to V_stable."
        elif self.price_mismatches > 0 or self.canary_errors > 0:
            status = CanaryStatus.AUDIT_FAIL
            msg = f"Audit issues detected: {self.price_mismatches} mismatches, {self.canary_errors} errors."
        else:
            status = CanaryStatus.HEALTHY
            msg = f"Canary deployment healthy across {canary_cnt} canary symbols."

        return CanaryAuditSummary(
            total_evaluated_ticks=self.total_ticks_processed,
            canary_symbols_count=canary_cnt,
            baseline_symbols_count=baseline_cnt,
            price_mismatch_count=self.price_mismatches,
            error_count=self.canary_errors,
            status=status,
            message=msg,
        )

"""
multi-strategy-capital-allocation-limits: Allocates and caps capital across
multiple concurrently-running strategies sharing one brokerage account.
"""
from dataclasses import dataclass, field
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AllocationError(ValueError):
    """Raised when capital allocation configuration is invalid."""
    pass


@dataclass
class StrategyAllocation:
    strategy_name: str
    max_allocation_pct: float  # e.g. 0.40 = 40% of NAV
    current_exposure_usd: float = 0.0


@dataclass
class AllocationCheckResult:
    approved: bool
    strategy_name: str
    requested_usd: float
    current_exposure_usd: float
    max_allowed_usd: float
    remaining_capacity_usd: float
    rejection_reason: Optional[str] = None


class MultiStrategyCapitalAllocator:
    """
    Enforces per-strategy capital allocation limits across multiple strategies
    sharing a single brokerage account.
    """

    def __init__(self, cash_reserve_pct: float = 0.10):
        """
        Args:
            cash_reserve_pct: Minimum cash reserve as fraction of NAV (default 10%).
        """
        self.cash_reserve_pct = cash_reserve_pct
        self.strategies: Dict[str, StrategyAllocation] = {}

    def register_strategy(self, strategy_name: str, max_allocation_pct: float) -> None:
        """Register a strategy with its maximum capital allocation percentage."""
        if max_allocation_pct <= 0 or max_allocation_pct > 1.0:
            raise AllocationError(
                f"Invalid allocation {max_allocation_pct:.2%} for '{strategy_name}'. Must be in (0, 1.0]."
            )
        self.strategies[strategy_name] = StrategyAllocation(
            strategy_name=strategy_name,
            max_allocation_pct=max_allocation_pct,
        )

    def validate_allocations(self) -> None:
        """Validate that total allocations + cash reserve don't exceed 100%."""
        total = sum(s.max_allocation_pct for s in self.strategies.values())
        max_investable = 1.0 - self.cash_reserve_pct
        if total > max_investable + 1e-9:
            raise AllocationError(
                f"Total strategy allocations ({total:.2%}) exceed investable capital "
                f"({max_investable:.2%} after {self.cash_reserve_pct:.2%} cash reserve)."
            )

    def update_exposure(self, strategy_name: str, current_exposure_usd: float) -> None:
        """Update current mark-to-market exposure for a strategy."""
        if strategy_name not in self.strategies:
            raise AllocationError(f"Unknown strategy: '{strategy_name}'")
        self.strategies[strategy_name].current_exposure_usd = current_exposure_usd

    def check_order(
        self,
        strategy_name: str,
        order_value_usd: float,
        portfolio_nav: float,
    ) -> AllocationCheckResult:
        """
        Pre-trade check: verify the order won't exceed the strategy's capital allocation.
        """
        if strategy_name not in self.strategies:
            return AllocationCheckResult(
                approved=False,
                strategy_name=strategy_name,
                requested_usd=order_value_usd,
                current_exposure_usd=0.0,
                max_allowed_usd=0.0,
                remaining_capacity_usd=0.0,
                rejection_reason=f"Unregistered strategy: '{strategy_name}'",
            )

        alloc = self.strategies[strategy_name]
        max_allowed = alloc.max_allocation_pct * portfolio_nav
        projected = alloc.current_exposure_usd + order_value_usd
        remaining = max(max_allowed - alloc.current_exposure_usd, 0.0)

        if projected > max_allowed + 1e-2:
            reason = (
                f"ALLOCATION BREACH: Strategy '{strategy_name}' projected exposure "
                f"${projected:,.2f} exceeds cap ${max_allowed:,.2f} "
                f"({alloc.max_allocation_pct:.0%} of ${portfolio_nav:,.2f} NAV). Order blocked."
            )
            logger.warning(reason)
            return AllocationCheckResult(
                approved=False,
                strategy_name=strategy_name,
                requested_usd=order_value_usd,
                current_exposure_usd=alloc.current_exposure_usd,
                max_allowed_usd=max_allowed,
                remaining_capacity_usd=remaining,
                rejection_reason=reason,
            )

        return AllocationCheckResult(
            approved=True,
            strategy_name=strategy_name,
            requested_usd=order_value_usd,
            current_exposure_usd=alloc.current_exposure_usd,
            max_allowed_usd=max_allowed,
            remaining_capacity_usd=remaining,
        )

    def get_utilization_report(self, portfolio_nav: float) -> List[Dict]:
        """Returns utilization report for all registered strategies."""
        report = []
        for name, alloc in self.strategies.items():
            max_usd = alloc.max_allocation_pct * portfolio_nav
            util_pct = (alloc.current_exposure_usd / max_usd) if max_usd > 0 else 0.0
            report.append({
                "strategy": name,
                "allocation_pct": alloc.max_allocation_pct,
                "max_usd": max_usd,
                "current_exposure_usd": alloc.current_exposure_usd,
                "utilization_pct": util_pct,
                "remaining_usd": max(max_usd - alloc.current_exposure_usd, 0.0),
            })
        return report

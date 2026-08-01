import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class TimeHorizonBucket:
    horizon_label: str                   # e.g. 'INTRADAY', 'SHORT_TERM', 'MEDIUM_TERM', 'LONG_TERM'
    holding_period_days: int             # e.g. 1, 5, 20, 60
    allocated_risk_pct: float            # % of total portfolio risk budget allocated
    annualized_vol_target: float         # target vol for this horizon
    max_drawdown_limit_pct: float        # max drawdown limit as % of equity

@dataclass
class HorizonAllocation:
    horizon_label: str
    risk_budget_pct: float
    vol_target: float
    position_size_scalar: float          # multiplier for base position sizing
    is_within_limits: bool

@dataclass
class RiskBudgetAllocationReport:
    total_risk_budget_pct: float
    horizon_allocations: List[HorizonAllocation]
    total_horizons: int
    over_allocated: bool
    status: str                          # 'RISK_BUDGET_VALID', 'RISK_BUDGET_OVER_ALLOCATED'
    audit_notes: str

class RiskBudgetAllocationEngine:
    """
    Risk budget allocation engine distributing total portfolio risk across multiple
    time horizon buckets (intraday, short-term, medium-term, long-term) with per-horizon
    volatility targets and position size scalars.
    """
    def __init__(self, total_portfolio_vol_target: float = 0.15):
        self.total_portfolio_vol_target = total_portfolio_vol_target

    def allocate_risk_budget(
        self,
        buckets: List[TimeHorizonBucket]
    ) -> RiskBudgetAllocationReport:
        """
        Allocates risk budget across time horizon buckets. Validates total allocation <= 100%
        and computes position size scalars based on vol targets.
        """
        allocations: List[HorizonAllocation] = []
        total_pct = 0.0

        for bucket in buckets:
            total_pct += bucket.allocated_risk_pct

            # Position size scalar: ratio of horizon vol target to total portfolio vol target
            if self.total_portfolio_vol_target > 0:
                scalar = round(bucket.annualized_vol_target / self.total_portfolio_vol_target, 4)
            else:
                scalar = 1.0

            is_within = bucket.allocated_risk_pct > 0 and bucket.allocated_risk_pct <= 100.0

            allocations.append(HorizonAllocation(
                horizon_label=bucket.horizon_label,
                risk_budget_pct=bucket.allocated_risk_pct,
                vol_target=bucket.annualized_vol_target,
                position_size_scalar=scalar,
                is_within_limits=is_within
            ))

        over_allocated = total_pct > 100.0
        status = "RISK_BUDGET_OVER_ALLOCATED" if over_allocated else "RISK_BUDGET_VALID"

        notes = (
            f"RISK BUDGET ALLOCATION [{status}]: "
            f"Horizons = {len(buckets)}, Total Allocated = {total_pct:.1f}%, "
            f"Portfolio Vol Target = {self.total_portfolio_vol_target:.2%}."
        )

        if over_allocated:
            logger.warning(notes)
        else:
            logger.info(notes)

        return RiskBudgetAllocationReport(
            total_risk_budget_pct=round(total_pct, 2),
            horizon_allocations=allocations,
            total_horizons=len(buckets),
            over_allocated=over_allocated,
            status=status,
            audit_notes=notes
        )

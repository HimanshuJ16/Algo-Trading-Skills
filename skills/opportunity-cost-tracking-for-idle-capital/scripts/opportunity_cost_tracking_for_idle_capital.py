import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class PortfolioCapitalState:
    total_capital: float
    allocated_capital: float
    unallocated_cash: float
    benchmark_rate_pct: float = 5.25     # SOFR / Risk-Free Yield rate e.g. 5.25%
    holding_period_days: float = 30.0    # Capital idle duration in days

@dataclass
class SweepConfig:
    min_sweep_threshold_usd: float = 100000.0 # Min cash to trigger sweep
    sweep_transaction_cost_usd: float = 50.0  # Execution fee per sweep
    target_idle_ratio_max: float = 0.05       # Max 5% idle capital ratio threshold

@dataclass
class OpportunityCostReport:
    total_capital_usd: float
    unallocated_cash_usd: float
    idle_capital_ratio_pct: float
    gross_opportunity_cost_usd: float
    drag_basis_points: float
    net_yield_gain_usd: float
    recommendation: str                  # 'SWEEP_TO_YIELD_BENCHMARK', 'MAINTAIN_IDLE_CASH'
    status: str                          # 'IDLE_RATIO_HEALTHY', 'IDLE_CAPITAL_RATIO_EXCEEDED'
    audit_notes: str

class OpportunityCostTrackerEngine:
    """
    Opportunity cost tracking engine calculating idle capital return drag against SOFR benchmark rates,
    evaluating net yield gains after sweep transaction costs, and auditing cash sweep thresholds.
    """
    def __init__(self, config: Optional[SweepConfig] = None):
        self.config = config or SweepConfig()

    def analyze_opportunity_cost(
        self, state: PortfolioCapitalState
    ) -> OpportunityCostReport:
        """
        Calculates idle capital ratio, gross USD return drag against SOFR, net yield gain after sweep costs, and recommendations.
        """
        if state.total_capital <= 0:
            raise ValueError("Total portfolio capital must be greater than zero.")

        idle_ratio = state.unallocated_cash / state.total_capital
        idle_ratio_pct = idle_ratio * 100.0

        # Gross Opportunity Cost Drag (USD) = Cash * (BenchmarkRate / 100) * (Days / 365)
        annual_yield_frac = state.benchmark_rate_pct / 100.0
        period_yield_frac = annual_yield_frac * (state.holding_period_days / 365.0)
        gross_drag_usd = state.unallocated_cash * period_yield_frac

        # Drag in basis points relative to total portfolio capital
        drag_bps = (gross_drag_usd / state.total_capital) * 10000.0

        # Net yield gain after transaction sweep cost
        net_yield_gain_usd = gross_drag_usd - self.config.sweep_transaction_cost_usd

        # Check if cash sweep is recommended
        is_threshold_met = state.unallocated_cash >= self.config.min_sweep_threshold_usd
        is_profitable = net_yield_gain_usd > 0

        if is_threshold_met and is_profitable:
            recommendation = "SWEEP_TO_YIELD_BENCHMARK"
        else:
            recommendation = "MAINTAIN_IDLE_CASH"

        is_ratio_exceeded = idle_ratio > self.config.target_idle_ratio_max
        status = "IDLE_CAPITAL_RATIO_EXCEEDED" if is_ratio_exceeded else "IDLE_RATIO_HEALTHY"

        notes = (
            f"OPPORTUNITY COST AUDIT [{status}]: Unallocated Cash = ${state.unallocated_cash:,.2f} "
            f"({idle_ratio_pct:.2f}% of ${state.total_capital:,.2f} total). "
            f"Gross Drag @ {state.benchmark_rate_pct:.2f}% SOFR ({state.holding_period_days:.0f} days) = ${gross_drag_usd:,.2f} "
            f"({drag_bps:.2f} bps). Net Yield Gain after ${self.config.sweep_transaction_cost_usd:.2f} fee = ${net_yield_gain_usd:,.2f}. "
            f"Recommendation: '{recommendation}'."
        )

        if is_ratio_exceeded:
            logger.warning(notes)
        else:
            logger.info(notes)

        return OpportunityCostReport(
            total_capital_usd=round(state.total_capital, 2),
            unallocated_cash_usd=round(state.unallocated_cash, 2),
            idle_capital_ratio_pct=round(idle_ratio_pct, 2),
            gross_opportunity_cost_usd=round(gross_drag_usd, 2),
            drag_basis_points=round(drag_bps, 2),
            net_yield_gain_usd=round(net_yield_gain_usd, 2),
            recommendation=recommendation,
            status=status,
            audit_notes=notes
        )

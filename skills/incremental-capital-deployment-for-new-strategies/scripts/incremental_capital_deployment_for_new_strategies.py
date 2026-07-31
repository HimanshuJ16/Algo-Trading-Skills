import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Deployment Tier Definitions:
# Tier 0: Sandbox / Paper (0% Capital)
# Tier 1: Seed Pilot (10% Capital)
# Tier 2: Intermediate Scale (50% Capital)
# Tier 3: Full Production (100% Capital)

@dataclass
class StrategyDeploymentState:
    strategy_id: str
    current_tier: int                    # 0, 1, 2, 3
    days_in_tier: int
    realized_sharpe: float
    realized_max_drawdown_pct: float     # e.g. 4.5%
    slippage_vs_backtest_ratio: float    # e.g. 1.1x (actual vs backtest slippage)
    target_full_capital_usd: float       # e.g. $1,000,000

@dataclass
class IncrementalDeploymentReport:
    strategy_id: str
    previous_tier: int
    new_tier: int
    tier_name: str                      # 'TIER_0_SANDBOX', 'TIER_1_SEED', 'TIER_2_SCALE', 'TIER_3_FULL', 'EMERGENCY_DEACTIVATED'
    capital_allocation_pct: float       # 0.0, 0.10, 0.50, 1.00
    allocated_capital_usd: float
    promotion_status: str               # 'PROMOTED', 'RETAINED_CURRENT_TIER', 'DEMOTED_DRAWDOWN_BREACH'
    audit_notes: str

class IncrementalCapitalDeploymentEngine:
    """
    Portfolio risk management engine implementing 4-tier stage-gated capital ramp-up
    (Paper -> 10% Seed -> 50% Scale -> 100% Full) with realized Sharpe and drawdown promotion gates.
    """
    def __init__(
        self,
        emergency_max_drawdown_limit_pct: float = 12.0 # > 12% triggers demotion to Tier 0
    ):
        self.emergency_max_drawdown_limit_pct = emergency_max_drawdown_limit_pct

        self.tier_allocation_pcts = {
            0: 0.0,    # Sandbox
            1: 0.10,   # Seed 10%
            2: 0.50,   # Scale 50%
            3: 1.00    # Full 100%
        }
        self.tier_names = {
            0: "TIER_0_SANDBOX",
            1: "TIER_1_SEED",
            2: "TIER_2_SCALE",
            3: "TIER_3_FULL"
        }

    def evaluate_strategy_deployment(
        self,
        state: StrategyDeploymentState
    ) -> IncrementalDeploymentReport:
        """
        Evaluates strategy stage-gated promotion, retention, or demotion logic.
        """
        curr = state.current_tier

        # 1. Audit Emergency Drawdown Breach (Demote to Tier 0 & Deactivate)
        if state.realized_max_drawdown_pct >= self.emergency_max_drawdown_limit_pct:
            notes = (
                f"EMERGENCY DEMOTION [{state.strategy_id}]: Realized Max Drawdown {state.realized_max_drawdown_pct:.1f}% "
                f"exceeds limit ({self.emergency_max_drawdown_limit_pct:.1f}%). Demoting from Tier {curr} to Tier 0 ($0 allocation)."
            )
            logger.critical(notes)
            return IncrementalDeploymentReport(
                strategy_id=state.strategy_id,
                previous_tier=curr,
                new_tier=0,
                tier_name="EMERGENCY_DEACTIVATED",
                capital_allocation_pct=0.0,
                allocated_capital_usd=0.0,
                promotion_status="DEMOTED_DRAWDOWN_BREACH",
                audit_notes=notes
            )

        # 2. Stage-Gated Promotion Logic
        new_tier = curr
        status = "RETAINED_CURRENT_TIER"

        if curr == 0:
            # Tier 0 -> Tier 1 (Requires >= 14 paper trading days)
            if state.days_in_tier >= 14:
                new_tier = 1
                status = "PROMOTED"
        elif curr == 1:
            # Tier 1 -> Tier 2 (Requires >= 30 days, Sharpe >= 1.0, Max DD <= 5.0%, Slippage <= 1.5x)
            if (state.days_in_tier >= 30 and
                state.realized_sharpe >= 1.0 and
                state.realized_max_drawdown_pct <= 5.0 and
                state.slippage_vs_backtest_ratio <= 1.5):
                new_tier = 2
                status = "PROMOTED"
        elif curr == 2:
            # Tier 2 -> Tier 3 (Requires >= 60 days, Sharpe >= 1.2, Max DD <= 8.0%)
            if (state.days_in_tier >= 60 and
                state.realized_sharpe >= 1.2 and
                state.realized_max_drawdown_pct <= 8.0):
                new_tier = 3
                status = "PROMOTED"

        alloc_pct = self.tier_allocation_pcts[new_tier]
        alloc_usd = round(state.target_full_capital_usd * alloc_pct, 2)
        tier_name = self.tier_names[new_tier]

        if status == "PROMOTED":
            notes = (
                f"STAGE-GATED PROMOTION [{state.strategy_id}]: Promoted from Tier {curr} to Tier {new_tier} ({tier_name}). "
                f"Capital allocation increased to {alloc_pct*100:.0f}% (${alloc_usd:,.2f} USD). "
                f"Realized Sharpe = {state.realized_sharpe:.2f}, Max DD = {state.realized_max_drawdown_pct:.1f}%."
            )
            logger.info(notes)
        else:
            notes = (
                f"STAGE-GATED RETAINED [{state.strategy_id}]: Retained at Tier {curr} ({tier_name}). "
                f"Capital allocation = {alloc_pct*100:.0f}% (${alloc_usd:,.2f} USD). Days in Tier = {state.days_in_tier}."
            )
            logger.info(notes)

        return IncrementalDeploymentReport(
            strategy_id=state.strategy_id,
            previous_tier=curr,
            new_tier=new_tier,
            tier_name=tier_name,
            capital_allocation_pct=alloc_pct,
            allocated_capital_usd=alloc_usd,
            promotion_status=status,
            audit_notes=notes
        )

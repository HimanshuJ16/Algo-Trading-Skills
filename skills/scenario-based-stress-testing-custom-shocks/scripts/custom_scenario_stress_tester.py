import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class ScenarioResult:
    """Legacy ScenarioResult container for backward compatibility."""
    scenario_name: str
    pnl: float

class StressScenarioCategory(str, Enum):
    HISTORICAL_CRISIS = "HISTORICAL_CRISIS"
    CUSTOM_HYPOTHETICAL = "CUSTOM_HYPOTHETICAL"
    REVERSE_STRESS = "REVERSE_STRESS"

@dataclass
class FactorShock:
    factor_name: str                      # e.g., 'EQUITY_SPOT', 'IMPLIED_VOL', 'INTEREST_RATE_BPS', 'CRUDE_OIL'
    percentage_shock: float               # e.g., -0.35 for -35% shock, or +2.0 for +200bps
    is_absolute_change: bool = False

@dataclass
class StressScenarioDefinition:
    scenario_id: str
    scenario_name: str
    category: StressScenarioCategory
    shocks: List[FactorShock]
    description: str = ""

PREDEFINED_HISTORICAL_SCENARIOS = [
    StressScenarioDefinition(
        scenario_id="SCEN_2008_LEHMAN",
        scenario_name="2008 Lehman Financial Crisis",
        category=StressScenarioCategory.HISTORICAL_CRISIS,
        shocks=[
            FactorShock("EQUITY_SPOT", -0.35),
            FactorShock("IMPLIED_VOL", 1.50),     # +150% VIX spike
            FactorShock("CREDIT_SPREAD", 3.00),   # +300bps credit spread
        ],
        description="Global banking liquidity freeze and equity market sell-off."
    ),
    StressScenarioDefinition(
        scenario_id="SCEN_2020_COVID",
        scenario_name="March 2020 Covid Liquidity Shock",
        category=StressScenarioCategory.HISTORICAL_CRISIS,
        shocks=[
            FactorShock("EQUITY_SPOT", -0.30),
            FactorShock("CRUDE_OIL", -0.60),      # -60% oil crash
            FactorShock("IMPLIED_VOL", 1.20),
        ],
        description="Rapid global market shutdown and commodity collapse."
    ),
    StressScenarioDefinition(
        scenario_id="SCEN_2022_RATES",
        scenario_name="2022 Inflationary Rate Hike",
        category=StressScenarioCategory.HISTORICAL_CRISIS,
        shocks=[
            FactorShock("INTEREST_RATE_BPS", 200.0, is_absolute_change=True), # +200bps
            FactorShock("TECH_GROWTH_SPOT", -0.25),
        ],
        description="Aggressive central bank rate hiking cycle."
    ),
]

@dataclass
class AssetPosition:
    asset_id: str
    factor_name: str                     # Matched to factor shock name
    current_value_usd: float
    beta_to_factor: float = 1.0          # Sensitivity multiplier

@dataclass
class DetailedScenarioResult:
    scenario_id: str
    scenario_name: str
    category: StressScenarioCategory
    starting_portfolio_value_usd: float
    simulated_pnl_usd: float
    ending_portfolio_value_usd: float
    percentage_loss_pct: float
    is_drawdown_limit_breached: bool
    audit_notes: str

@dataclass
class StressTestReport:
    total_scenarios_evaluated: int
    worst_case_scenario: str
    max_loss_usd: float
    max_percentage_loss_pct: float
    results: List[DetailedScenarioResult]
    audit_notes: str

class CustomScenarioStressTester:
    """
    Production-grade scenario-based stress testing engine running historical crisis replays,
    custom multi-factor shocks, and reverse stress tests across multi-asset portfolios.
    """
    def __init__(
        self,
        scenarios: Optional[Dict[str, float]] = None,
        max_allowed_drawdown_pct: float = 20.0
    ):
        # Legacy dict support: scenario_name -> shock multiplier (e.g. {"Crash": -0.2})
        self.scenarios = scenarios or {"Crash": -0.2, "Rally": 0.1}
        self.max_allowed_drawdown_pct = max_allowed_drawdown_pct

    def run_stress_test(self, current_value: float) -> List[ScenarioResult]:
        """Legacy run_stress_test method retained for 100% backward compatibility."""
        results = []
        for name, shock in self.scenarios.items():
            results.append(ScenarioResult(name, current_value * shock))
        return results

    def run_multi_factor_stress_test(
        self,
        positions: List[AssetPosition],
        custom_scenarios: Optional[List[StressScenarioDefinition]] = None
    ) -> StressTestReport:
        """
        Executes multi-factor scenario stress tests across portfolio positions.
        """
        scenarios_to_run = list(PREDEFINED_HISTORICAL_SCENARIOS)
        if custom_scenarios:
            scenarios_to_run.extend(custom_scenarios)

        total_portfolio_value = sum(p.current_value_usd for p in positions)
        if total_portfolio_value <= 0:
            raise ValueError(f"Portfolio value must be > 0, got {total_portfolio_value}")

        detailed_results: List[DetailedScenarioResult] = []
        worst_loss_usd = 0.0
        worst_scenario_name = ""

        for scen in scenarios_to_run:
            simulated_pnl = 0.0
            shock_map = {s.factor_name: s for s in scen.shocks}

            for pos in positions:
                if pos.factor_name in shock_map:
                    shk = shock_map[pos.factor_name]
                    if shk.is_absolute_change:
                        # e.g., Interest rate bps change: -Duration * Delta_r
                        pos_pnl = pos.current_value_usd * (shk.percentage_shock / 10000.0) * pos.beta_to_factor
                    else:
                        # Percentage shock (e.g. -0.35)
                        pos_pnl = pos.current_value_usd * shk.percentage_shock * pos.beta_to_factor
                    simulated_pnl += pos_pnl

            simulated_pnl = round(simulated_pnl, 2)
            pct_loss = round((simulated_pnl / total_portfolio_value) * 100.0, 2)
            ending_val = round(total_portfolio_value + simulated_pnl, 2)
            is_breached = abs(pct_loss) > self.max_allowed_drawdown_pct and pct_loss < 0

            notes = (
                f"STRESS SCENARIO [{scen.scenario_name}]: PnL = ${simulated_pnl:,.2f} ({pct_loss}%), "
                f"Breached Limit ({self.max_allowed_drawdown_pct}%) = {is_breached}."
            )

            detailed_results.append(DetailedScenarioResult(
                scenario_id=scen.scenario_id,
                scenario_name=scen.scenario_name,
                category=scen.category,
                starting_portfolio_value_usd=total_portfolio_value,
                simulated_pnl_usd=simulated_pnl,
                ending_portfolio_value_usd=ending_val,
                percentage_loss_pct=pct_loss,
                is_drawdown_limit_breached=is_breached,
                audit_notes=notes
            ))

            if simulated_pnl < worst_loss_usd:
                worst_loss_usd = simulated_pnl
                worst_scenario_name = scen.scenario_name

        max_pct_loss = round((worst_loss_usd / total_portfolio_value) * 100.0, 2)
        summary_notes = (
            f"STRESS TEST SUMMARY: Total Scenarios = {len(scenarios_to_run)}, "
            f"Worst Scenario = '{worst_scenario_name}' (${worst_loss_usd:,.2f} / {max_pct_loss}%)."
        )

        logger.info(summary_notes)

        return StressTestReport(
            total_scenarios_evaluated=len(scenarios_to_run),
            worst_case_scenario=worst_scenario_name,
            max_loss_usd=worst_loss_usd,
            max_percentage_loss_pct=max_pct_loss,
            results=detailed_results,
            audit_notes=summary_notes
        )

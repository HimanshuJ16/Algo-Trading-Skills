"""
stress-testing-against-historical-crash-scenarios: Replay current portfolio positions
through historical crash return distributions to estimate tail-risk P&L impact.
"""
from dataclasses import dataclass, field
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class CrashScenario:
    """Defines a historical crash scenario with per-asset return shocks."""
    name: str
    description: str
    asset_returns: Dict[str, float]  # symbol -> cumulative return during crash (e.g. -0.34 = -34%)


@dataclass
class ScenarioResult:
    scenario_name: str
    stressed_pnl_usd: float
    stressed_pnl_pct: float
    per_asset_impact: Dict[str, float]  # symbol -> dollar impact


@dataclass
class StressTestReport:
    results: List[ScenarioResult]
    worst_scenario: str
    worst_loss_pct: float
    worst_loss_usd: float
    threshold_breached: bool
    breach_reason: Optional[str]


# Built-in historical crash scenarios
BUILTIN_SCENARIOS: List[CrashScenario] = [
    CrashScenario(
        name="2020_COVID_CRASH",
        description="Feb-Mar 2020 COVID-19 pandemic sell-off (peak-to-trough)",
        asset_returns={
            "SPY": -0.3393, "QQQ": -0.2833, "IWM": -0.4131,
            "EEM": -0.3120, "TLT": 0.1560, "GLD": -0.0380,
            "AAPL": -0.3100, "MSFT": -0.2600, "AMZN": -0.2000,
            "TSLA": -0.6000, "NVDA": -0.3500, "META": -0.3400,
            "DEFAULT": -0.30,  # fallback for unlisted assets
        },
    ),
    CrashScenario(
        name="2008_GFC",
        description="Sep-Nov 2008 Global Financial Crisis (peak-to-trough)",
        asset_returns={
            "SPY": -0.5190, "QQQ": -0.4950, "IWM": -0.5530,
            "EEM": -0.6240, "TLT": 0.2830, "GLD": -0.0720,
            "AAPL": -0.5700, "MSFT": -0.4800, "AMZN": -0.6200,
            "TSLA": -0.8000, "NVDA": -0.7500, "META": -0.5000,
            "DEFAULT": -0.50,
        },
    ),
    CrashScenario(
        name="2015_FLASH_CRASH",
        description="Aug 24, 2015 China-driven flash crash (single-day)",
        asset_returns={
            "SPY": -0.0530, "QQQ": -0.0650, "IWM": -0.0780,
            "EEM": -0.0710, "TLT": 0.0200, "GLD": 0.0150,
            "AAPL": -0.0600, "MSFT": -0.0500, "AMZN": -0.0450,
            "TSLA": -0.0800, "NVDA": -0.0900, "META": -0.0550,
            "DEFAULT": -0.06,
        },
    ),
]


class HistoricalStressTester:
    """
    Replays current portfolio positions through historical crash scenarios and
    enforces a stressed-loss threshold gate.
    """

    def __init__(
        self,
        max_stressed_loss_pct: float = 0.15,
        scenarios: Optional[List[CrashScenario]] = None,
    ):
        self.max_stressed_loss_pct = max_stressed_loss_pct
        self.scenarios = scenarios or BUILTIN_SCENARIOS

    def _get_scenario_return(self, scenario: CrashScenario, symbol: str) -> float:
        """Returns the scenario return for a symbol, falling back to DEFAULT."""
        return scenario.asset_returns.get(symbol, scenario.asset_returns.get("DEFAULT", -0.30))

    def run_stress_test(
        self,
        positions: Dict[str, float],  # symbol -> quantity
        prices: Dict[str, float],     # symbol -> current price
        portfolio_nav: float,
    ) -> StressTestReport:
        """
        Replays current positions through all crash scenarios and returns a full report.
        """
        if portfolio_nav <= 0:
            raise ValueError(f"Invalid portfolio NAV: {portfolio_nav}")

        results: List[ScenarioResult] = []

        for scenario in self.scenarios:
            per_asset_impact: Dict[str, float] = {}
            total_pnl = 0.0

            for symbol, qty in positions.items():
                if abs(qty) < 1e-9 or symbol not in prices:
                    continue
                position_value = qty * prices[symbol]
                shock = self._get_scenario_return(scenario, symbol)
                impact = position_value * shock
                per_asset_impact[symbol] = impact
                total_pnl += impact

            stressed_pct = total_pnl / portfolio_nav if portfolio_nav > 0 else 0.0

            results.append(ScenarioResult(
                scenario_name=scenario.name,
                stressed_pnl_usd=total_pnl,
                stressed_pnl_pct=stressed_pct,
                per_asset_impact=per_asset_impact,
            ))

        # Find worst-case scenario
        worst = min(results, key=lambda r: r.stressed_pnl_pct) if results else None

        if worst is None:
            return StressTestReport(
                results=[], worst_scenario="NONE", worst_loss_pct=0.0,
                worst_loss_usd=0.0, threshold_breached=False, breach_reason=None,
            )

        worst_loss_pct = abs(worst.stressed_pnl_pct)
        breached = worst_loss_pct >= self.max_stressed_loss_pct

        breach_reason = None
        if breached:
            breach_reason = (
                f"STRESS TEST BREACH: Worst-case scenario '{worst.scenario_name}' produces "
                f"{worst_loss_pct:.2%} loss (${abs(worst.stressed_pnl_usd):,.2f}), exceeding "
                f"threshold {self.max_stressed_loss_pct:.2%}. Blocking new entries."
            )
            logger.warning(breach_reason)

        return StressTestReport(
            results=results,
            worst_scenario=worst.scenario_name,
            worst_loss_pct=worst_loss_pct,
            worst_loss_usd=abs(worst.stressed_pnl_usd),
            threshold_breached=breached,
            breach_reason=breach_reason,
        )

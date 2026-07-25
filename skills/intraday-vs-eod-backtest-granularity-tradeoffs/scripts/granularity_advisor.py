"""
intraday-vs-eod-backtest-granularity-tradeoffs: Recommends optimal data resolution
based on strategy holding period, decision frequency, and compute budget.
"""
from dataclasses import dataclass
import logging
from enum import Enum
from typing import List

logger = logging.getLogger(__name__)


class DataGranularity(Enum):
    TICK = "TICK"
    ONE_MINUTE = "1MIN"
    FIVE_MINUTE = "5MIN"
    HOURLY = "1H"
    EOD = "EOD"


@dataclass
class GranularityOption:
    resolution: DataGranularity
    accuracy_score: float   # 0-1
    cost_multiplier: float  # relative to EOD=1.0
    bars_per_day: int


@dataclass
class GranularityRecommendation:
    strategy_type: str
    holding_period_days: float
    recommended: DataGranularity
    accuracy_score: float
    cost_multiplier: float
    message: str


GRANULARITY_OPTIONS = [
    GranularityOption(DataGranularity.TICK, 1.0, 500.0, 50000),
    GranularityOption(DataGranularity.ONE_MINUTE, 0.95, 50.0, 390),
    GranularityOption(DataGranularity.FIVE_MINUTE, 0.85, 10.0, 78),
    GranularityOption(DataGranularity.HOURLY, 0.65, 3.0, 7),
    GranularityOption(DataGranularity.EOD, 0.40, 1.0, 1),
]


class GranularityAdvisor:
    """Recommends optimal backtest data resolution based on strategy profile."""

    def recommend(self, holding_period_days: float, signals_per_day: float) -> GranularityRecommendation:
        if holding_period_days < 0.01:  # Sub-minute scalping
            strat_type = "HFT/Scalping"
            best = GRANULARITY_OPTIONS[0]  # TICK
        elif holding_period_days < 0.5:  # Intraday
            strat_type = "Intraday"
            best = GRANULARITY_OPTIONS[1] if signals_per_day > 10 else GRANULARITY_OPTIONS[2]
        elif holding_period_days < 5:  # Swing
            strat_type = "Swing"
            best = GRANULARITY_OPTIONS[2] if signals_per_day > 2 else GRANULARITY_OPTIONS[3]
        else:  # Position/Long-term
            strat_type = "Position"
            best = GRANULARITY_OPTIONS[4]  # EOD

        msg = (f"Strategy type '{strat_type}' (hold={holding_period_days}d, "
               f"signals={signals_per_day}/day) -> Recommended: {best.resolution.value} "
               f"(accuracy={best.accuracy_score}, cost={best.cost_multiplier}x EOD).")
        logger.info(msg)

        return GranularityRecommendation(
            strategy_type=strat_type,
            holding_period_days=holding_period_days,
            recommended=best.resolution,
            accuracy_score=best.accuracy_score,
            cost_multiplier=best.cost_multiplier,
            message=msg,
        )

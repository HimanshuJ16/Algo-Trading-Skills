import logging
import math
import datetime
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class WeatherIndexType(Enum):
    HDD = "HDD"                                 # Heating Degree Days (65F - T_mean)
    CDD = "CDD"                                 # Cooling Degree Days (T_mean - 65F)
    CAT = "CAT"                                 # Cumulative Average Temperature (Celsius)


class InstrumentType(Enum):
    FUTURES = "FUTURES"
    CALL_OPTION = "CALL_OPTION"
    PUT_OPTION = "PUT_OPTION"
    CAPPED_SWAP = "CAPPED_SWAP"


class WeatherDerivativeError(Exception):
    """Base exception for Weather Derivatives Engine errors."""
    pass


@dataclass
class WeatherDerivativeContract:
    contract_id: str
    symbol: str
    location: str
    index_type: WeatherIndexType
    instrument_type: InstrumentType
    strike_index: float = 0.0                   # Strike index value (HDD/CDD points)
    tick_multiplier_usd: float = 20.0           # CME Weather standard multiplier = $20 per index point
    max_payout_usd: float = 0.0                 # OTC Payout Cap (0.0 = uncapped)
    start_date: datetime.date = field(default_factory=datetime.date.today)
    end_date: datetime.date = field(default_factory=datetime.date.today)


@dataclass
class BurnAnalysisResult:
    contract_id: str
    historical_seasons_analyzed: int
    mean_index_value: float
    std_dev_index: float
    expected_payoff_usd: float
    std_dev_payoff_usd: float
    max_historical_payoff_usd: float


@dataclass
class SettlementPayoff:
    contract_id: str
    accumulated_index: float
    total_payoff_usd: float
    is_capped: bool
    rationale: str


class WeatherDerivativesEngine:
    """
    Institutional Weather Derivatives & Niche Instrument Engine (CME Weather Derivatives / OTC Swaps).
    
    Prices CME HDD/CDD Futures & Options and OTC Weather Swaps using Burn Analysis (historical simulation),
    models accumulated degree day indexes, calculates $20/point tick multiplier payoffs, and enforces payout caps.
    """

    def __init__(self):
        logger.info("Initialized Weather Derivatives & Niche Instrument Engine")

    def calculate_monthly_index(
        self, daily_min_max_temps: List[Tuple[float, float]], index_type: WeatherIndexType, base_temp_f: float = 65.0
    ) -> float:
        """
        Calculates accumulated monthly index value (HDD, CDD, or CAT) from daily (T_min, T_max) tuples.
        """
        if not daily_min_max_temps:
            raise WeatherDerivativeError("Temperature list cannot be empty.")

        total_index = 0.0

        for t_min, t_max in daily_min_max_temps:
            t_mean = (t_max + t_min) / 2.0

            if index_type == WeatherIndexType.HDD:
                daily_val = max(0.0, base_temp_f - t_mean)
            elif index_type == WeatherIndexType.CDD:
                daily_val = max(0.0, t_mean - base_temp_f)
            elif index_type == WeatherIndexType.CAT:
                daily_val = t_mean  # Cumulative Average Temp

            total_index += daily_val

        logger.info(f"Accumulated Monthly {index_type.value} Index = {total_index:.2f} points over {len(daily_min_max_temps)} days")
        return round(total_index, 2)

    def calculate_settlement_payoff(
        self, contract: WeatherDerivativeContract, accumulated_index: float
    ) -> SettlementPayoff:
        """
        Calculates settlement payoff for Futures, Options, and Swaps based on accumulated index value.
        """
        if accumulated_index < 0:
            raise WeatherDerivativeError("Accumulated index value cannot be negative.")

        mult = contract.tick_multiplier_usd
        strike = contract.strike_index
        uncapped_payoff = 0.0

        if contract.instrument_type == InstrumentType.FUTURES:
            uncapped_payoff = accumulated_index * mult

        elif contract.instrument_type == InstrumentType.CALL_OPTION:
            uncapped_payoff = max(0.0, accumulated_index - strike) * mult

        elif contract.instrument_type == InstrumentType.PUT_OPTION:
            uncapped_payoff = max(0.0, strike - accumulated_index) * mult

        elif contract.instrument_type == InstrumentType.CAPPED_SWAP:
            # Swap payoff relative to strike index
            uncapped_payoff = (accumulated_index - strike) * mult

        # Apply Max Payout Cap if specified (> 0)
        is_capped = False
        final_payoff = uncapped_payoff

        if contract.max_payout_usd > 0.0:
            if uncapped_payoff > contract.max_payout_usd:
                final_payoff = contract.max_payout_usd
                is_capped = True
            elif uncapped_payoff < -contract.max_payout_usd:
                final_payoff = -contract.max_payout_usd
                is_capped = True

        rationale = (
            f"Settlement Payoff [{contract.symbol}]: Index={accumulated_index:.2f}, "
            f"Strike={strike:.2f}, Payoff=${final_payoff:,.2f} "
            f"({'CAPPED at $' + str(contract.max_payout_usd) if is_capped else 'Uncapped'})"
        )
        logger.info(rationale)

        return SettlementPayoff(
            contract_id=contract.contract_id,
            accumulated_index=round(accumulated_index, 2),
            total_payoff_usd=round(final_payoff, 2),
            is_capped=is_capped,
            rationale=rationale,
        )

    def run_burn_analysis(
        self, contract: WeatherDerivativeContract, historical_season_indexes: List[float]
    ) -> BurnAnalysisResult:
        """
        Executes Burn Analysis (Historical Simulation) across historical weather seasons to derive fair value.
        """
        if not historical_season_indexes:
            raise WeatherDerivativeError("Historical season indexes list cannot be empty.")

        n = len(historical_season_indexes)
        mean_index = sum(historical_season_indexes) / n

        # Calculate standard deviation of index
        var_index = sum((x - mean_index) ** 2 for x in historical_season_indexes) / max(1, n - 1)
        std_index = math.sqrt(var_index)

        # Calculate payoffs for each historical season
        payoffs = []
        for idx in historical_season_indexes:
            res = self.calculate_settlement_payoff(contract, idx)
            payoffs.append(res.total_payoff_usd)

        mean_payoff = sum(payoffs) / n
        var_payoff = sum((p - mean_payoff) ** 2 for p in payoffs) / max(1, n - 1)
        std_payoff = math.sqrt(var_payoff)
        max_payoff = max(payoffs)

        logger.info(
            f"Burn Analysis [{contract.symbol}]: {n} Seasons Analyzed. MeanIndex={mean_index:.1f}, "
            f"ExpectedPayoff=${mean_payoff:,.2f}, StdDevPayoff=${std_payoff:,.2f}, MaxHistorical=${max_payoff:,.2f}"
        )

        return BurnAnalysisResult(
            contract_id=contract.contract_id,
            historical_seasons_analyzed=n,
            mean_index_value=round(mean_index, 2),
            std_dev_index=round(std_index, 2),
            expected_payoff_usd=round(mean_payoff, 2),
            std_dev_payoff_usd=round(std_payoff, 2),
            max_historical_payoff_usd=round(max_payoff, 2),
        )


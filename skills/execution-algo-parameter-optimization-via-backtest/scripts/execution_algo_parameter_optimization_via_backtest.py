import math
import statistics
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class AlgoParameterConfig:
    max_participation_rate: float       # e.g. 0.10 (10% ADV)
    risk_aversion_lambda: float         # e.g. 1e-5
    peg_offset_ticks: int               # e.g. 0 (midpoint), 1 (join), 2 (cross)

@dataclass
class HistoricalTradeSample:
    trade_id: str
    symbol: str
    order_qty: int
    arrival_price: float
    market_adv_shares: float
    volatility_daily_pct: float
    historical_execution_prices: List[float]

@dataclass
class OptimizationCandidateResult:
    config: AlgoParameterConfig
    mean_implementation_shortfall_bps: float
    std_implementation_shortfall_bps: float
    avg_fill_completion_rate: float
    utility_score: float

@dataclass
class AlgoOptimizationAuditReport:
    algo_name: str
    symbol: str
    total_trade_samples_tested: int
    total_grid_candidates_evaluated: int
    optimal_config: AlgoParameterConfig
    optimal_utility_score: float
    optimal_mean_is_bps: float
    optimal_std_is_bps: float
    all_candidate_results: List[OptimizationCandidateResult]
    audit_notes: str

class ExecutionAlgoOptimizerEngine:
    """
    Quantitative execution research engine for optimizing Almgren-Chriss & VWAP/TWAP algorithm parameters
    (participation rate ceilings, risk aversion lambda, peg offsets) via historical backtest grid search.
    """
    def __init__(self, is_volatility_penalty_weight: float = 0.5, incomplete_fill_penalty_weight: float = 100.0):
        self.is_volatility_penalty_weight = is_volatility_penalty_weight
        self.incomplete_fill_penalty_weight = incomplete_fill_penalty_weight

    def simulate_single_execution(
        self,
        config: AlgoParameterConfig,
        sample: HistoricalTradeSample
    ) -> Tuple[float, float]:
        """
        Simulates execution trajectory for a single trade sample under given algo config.
        Returns: (Implementation Shortfall in bps, Fill Rate 0.0-1.0)
        """
        # Impact model: Impact = gamma * (part_rate)^0.5 + risk_aversion_penalty
        impact_bps = 5.0 * (config.max_participation_rate ** 0.5) * (1.0 + (config.peg_offset_ticks * 0.2))
        risk_bps = (sample.volatility_daily_pct * 100.0) * (config.risk_aversion_lambda * 1e4)

        # Baseline shortfall
        simulated_is_bps = round(impact_bps + risk_bps, 2)

        # Fill rate depends on participation rate cap vs order size
        fill_rate = min(1.0, round(config.max_participation_rate * (sample.market_adv_shares / float(sample.order_qty)), 2))

        return simulated_is_bps, fill_rate

    def optimize_algo_parameters(
        self,
        algo_name: str,
        symbol: str,
        grid_search_configs: List[AlgoParameterConfig],
        trade_samples: List[HistoricalTradeSample]
    ) -> AlgoOptimizationAuditReport:
        """
        Evaluates grid search parameter configurations across trade samples and selects optimal config.
        """
        if not grid_search_configs or not trade_samples:
            raise ValueError("Grid search configs and trade samples cannot be empty.")

        results: List[OptimizationCandidateResult] = []

        for config in grid_search_configs:
            shortfalls: List[float] = []
            fills: List[float] = []

            for sample in trade_samples:
                is_bps, fill_rate = self.simulate_single_execution(config, sample)
                shortfalls.append(is_bps)
                fills.append(fill_rate)

            mean_is = round(statistics.mean(shortfalls), 2)
            std_is = round(statistics.stdev(shortfalls), 2) if len(shortfalls) > 1 else 0.0
            avg_fill = round(statistics.mean(fills), 2)

            # Score = Mean_IS + gamma * Std_IS + (1 - FillRate) * Penalty
            score = round(mean_is + (self.is_volatility_penalty_weight * std_is) + ((1.0 - avg_fill) * self.incomplete_fill_penalty_weight), 2)

            results.append(OptimizationCandidateResult(
                config=config,
                mean_implementation_shortfall_bps=mean_is,
                std_implementation_shortfall_bps=std_is,
                avg_fill_completion_rate=avg_fill,
                utility_score=score
            ))

        # Sort by lowest utility score
        sorted_results = sorted(results, key=lambda r: r.utility_score)
        optimal = sorted_results[0]

        notes = (
            f"ALGO PARAMETER OPTIMIZATION COMPLETE [{algo_name} - {symbol}]: Evaluated {len(grid_search_configs)} candidates across {len(trade_samples)} samples. "
            f"Optimal Config: PartRate={optimal.config.max_participation_rate:.2f}, Lambda={optimal.config.risk_aversion_lambda:.1e}, Offset={optimal.config.peg_offset_ticks} -> "
            f"Utility Score={optimal.utility_score:.2f} (Mean IS={optimal.mean_implementation_shortfall_bps:.1f}bps, Fill Rate={optimal.avg_fill_completion_rate*100:.0f}%)."
        )
        logger.info(notes)

        return AlgoOptimizationAuditReport(
            algo_name=algo_name,
            symbol=symbol,
            total_trade_samples_tested=len(trade_samples),
            total_grid_candidates_evaluated=len(grid_search_configs),
            optimal_config=optimal.config,
            optimal_utility_score=optimal.utility_score,
            optimal_mean_is_bps=optimal.mean_implementation_shortfall_bps,
            optimal_std_is_bps=optimal.std_implementation_shortfall_bps,
            all_candidate_results=sorted_results,
            audit_notes=notes
        )

"""
vectorized-vs-event-driven-backtest-tradeoffs: Dual backtest engine selector,
fast vectorized matrix engine, event-driven fidelity engine, and execution drag auditor.
"""
from dataclasses import dataclass, field
import logging
import math
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class RecommendedEngine(Enum):
    VECTORIZED = "VECTORIZED"
    EVENT_DRIVEN = "EVENT_DRIVEN"


@dataclass
class EngineRecommendation:
    engine: RecommendedEngine
    complexity_score: float
    reason: str


@dataclass
class BacktestEngineMetrics:
    total_return_pct: float
    sharpe_ratio: float
    trades_count: int
    execution_time_sec: float


@dataclass
class DualEngineAuditReport:
    vectorized_metrics: BacktestEngineMetrics
    event_driven_metrics: BacktestEngineMetrics
    sharpe_divergence: float
    return_drag_pct: float
    speedup_factor: float
    summary: str


class DualBacktestEngineSelector:
    """
    Recommends and executes Vectorized or Event-Driven backtest engines based on strategy
    complexity, quantifying execution leakage drag between fast and realistic engines.
    """

    def __init__(self, commission_bps: float = 5.0, slippage_bps: float = 5.0):
        self.commission_rate = commission_bps / 10000.0
        self.slippage_rate = slippage_bps / 10000.0

    def recommend_engine(
        self,
        trades_per_day: float,
        uses_limit_orders: bool = False,
        uses_path_dependent_stops: bool = False,
    ) -> EngineRecommendation:
        """
        Evaluates strategy characteristics and recommends optimal engine architecture.
        """
        score = 0.0
        if trades_per_day > 10.0:
            score += 4.0
        if uses_limit_orders:
            score += 3.0
        if uses_path_dependent_stops:
            score += 3.0

        if score >= 4.0:
            rec = RecommendedEngine.EVENT_DRIVEN
            reason = f"High execution complexity (Score {score:.1f}/10.0). Requires event-driven fill mechanics."
        else:
            rec = RecommendedEngine.VECTORIZED
            reason = f"Low execution complexity (Score {score:.1f}/10.0). Vectorized engine recommended for speed."

        logger.info(f"Engine Recommendation: {rec.value} | Reason: {reason}")
        return EngineRecommendation(engine=rec, complexity_score=score, reason=reason)

    def run_vectorized_backtest(self, prices: List[float], signals: List[int]) -> BacktestEngineMetrics:
        """
        Executes fast $O(N)$ vectorized matrix backtest.
        """
        t_start = time.perf_counter()
        n = len(prices)
        if n < 2 or len(signals) != n:
            return BacktestEngineMetrics(0.0, 0.0, 0, 0.0)

        returns = []
        trade_count = 0

        for i in range(1, n):
            pos_prev = signals[i - 1]
            p_prev = prices[i - 1]
            p_curr = prices[i]

            raw_ret = (p_curr - p_prev) / p_prev
            strat_ret = pos_prev * raw_ret

            # Deduct friction if position changed
            if signals[i] != signals[i - 1]:
                trade_count += 1
                strat_ret -= (self.commission_rate + self.slippage_rate)

            returns.append(strat_ret)

        t_end = time.perf_counter()
        cum_ret = sum(returns)
        mean_r = cum_ret / len(returns) if returns else 0.0
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in returns) / len(returns)) if returns else 0.0001
        sharpe = (mean_r / (std_r or 0.0001)) * math.sqrt(252)

        return BacktestEngineMetrics(
            total_return_pct=round(cum_ret * 100.0, 2),
            sharpe_ratio=round(sharpe, 2),
            trades_count=trade_count,
            execution_time_sec=round(t_end - t_start, 6),
        )

    def run_event_driven_backtest(self, prices: List[float], signals: List[int]) -> BacktestEngineMetrics:
        """
        Executes event-driven bar-by-bar backtest simulating order state machine.
        """
        t_start = time.perf_counter()
        n = len(prices)
        if n < 2 or len(signals) != n:
            return BacktestEngineMetrics(0.0, 0.0, 0, 0.0)

        portfolio_value = 100000.0
        position = 0
        cash = portfolio_value
        trade_count = 0
        pnl_history = [portfolio_value]

        for i in range(1, n):
            desired_pos = signals[i - 1]
            p_curr = prices[i]

            # Simulate discrete event order execution with latency delay
            if desired_pos != position:
                trade_count += 1
                fill_price = p_curr * (1.0 + (self.slippage_rate if desired_pos > position else -self.slippage_rate))
                trade_size = abs(desired_pos - position)

                cost = trade_size * fill_price
                comm = cost * self.commission_rate
                cash -= (cost + comm)
                position = desired_pos

            current_val = cash + (position * p_curr)
            pnl_history.append(current_val)

        t_end = time.perf_counter()
        cum_ret = (pnl_history[-1] - pnl_history[0]) / pnl_history[0]

        daily_rets = [(pnl_history[j] - pnl_history[j-1]) / pnl_history[j-1] for j in range(1, len(pnl_history))]
        mean_r = sum(daily_rets) / len(daily_rets) if daily_rets else 0.0
        std_r = math.sqrt(sum((r - mean_r) ** 2 for r in daily_rets) / len(daily_rets)) if daily_rets else 0.0001
        sharpe = (mean_r / (std_r or 0.0001)) * math.sqrt(252)

        return BacktestEngineMetrics(
            total_return_pct=round(cum_ret * 100.0, 2),
            sharpe_ratio=round(sharpe, 2),
            trades_count=trade_count,
            execution_time_sec=round(t_end - t_start, 6),
        )

    def compare_engines(self, prices: List[float], signals: List[int]) -> DualEngineAuditReport:
        v_m = self.run_vectorized_backtest(prices, signals)
        e_m = self.run_event_driven_backtest(prices, signals)

        sharpe_diff = round(v_m.sharpe_ratio - e_m.sharpe_ratio, 2)
        drag_pct = round(v_m.total_return_pct - e_m.total_return_pct, 2)
        speedup = round(e_m.execution_time_sec / max(0.000001, v_m.execution_time_sec), 2)

        summary = (
            f"Dual Engine Audit: Vectorized Sharpe={v_m.sharpe_ratio:.2f} vs Event-Driven Sharpe={e_m.sharpe_ratio:.2f} "
            f"(Drag: {drag_pct:+.2f}%, Vector Speedup: {speedup:.1f}x)."
        )
        logger.info(summary)

        return DualEngineAuditReport(
            vectorized_metrics=v_m,
            event_driven_metrics=e_m,
            sharpe_divergence=sharpe_diff,
            return_drag_pct=drag_pct,
            speedup_factor=speedup,
            summary=summary,
        )

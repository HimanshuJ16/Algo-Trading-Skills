"""
demo-account-realism-gap-assessment: Execution realism score calculator and backtest
Sharpe ratio haircut estimator comparing demo/paper trade logs with live executions.
"""
from dataclasses import dataclass, field
import logging
import math
import statistics
from typing import List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExecutionLog:
    environment: str  # "DEMO" or "LIVE"
    symbol: str
    arrival_price: float
    fill_price: float
    requested_qty: float
    filled_qty: float
    submission_time: float
    fill_time: float

    @property
    def latency_ms(self) -> float:
        return (self.fill_time - self.submission_time) * 1000.0

    @property
    def slippage_bps(self) -> float:
        if self.arrival_price <= 0:
            return 0.0
        return (abs(self.fill_price - self.arrival_price) / self.arrival_price) * 10000.0

    @property
    def fill_rate(self) -> float:
        if self.requested_qty <= 0:
            return 1.0
        return min(1.0, self.filled_qty / self.requested_qty)


@dataclass
class RealismAssessmentResult:
    mean_demo_latency_ms: float
    mean_live_latency_ms: float
    mean_demo_slippage_bps: float
    mean_live_slippage_bps: float
    demo_fill_rate: float
    live_fill_rate: float
    realism_score: float  # 0.0 (unrealistic) to 1.0 (perfect parity)
    adjusted_sharpe_ratio: float
    summary: str


class DemoRealismAssessor:
    """
    Compares demo vs live execution metrics to compute a Realism Score R in [0, 1]
    and apply Sharpe ratio haircut factors.
    """

    def __init__(self, demo_logs: List[ExecutionLog], live_logs: List[ExecutionLog]):
        if not demo_logs or not live_logs:
            raise ValueError("Both demo_logs and live_logs must contain execution entries.")
        self.demo_logs = demo_logs
        self.live_logs = live_logs

    def assess_realism(self, unadjusted_demo_sharpe: float = 2.0) -> RealismAssessmentResult:
        """Calculates latency gap, slippage discrepancy, realism score, and adjusted Sharpe."""
        demo_lat = statistics.mean([l.latency_ms for l in self.demo_logs])
        live_lat = statistics.mean([l.latency_ms for l in self.live_logs])

        demo_slip = statistics.mean([l.slippage_bps for l in self.demo_logs])
        live_slip = statistics.mean([l.slippage_bps for l in self.live_logs])

        demo_fr = statistics.mean([l.fill_rate for l in self.demo_logs])
        live_fr = statistics.mean([l.fill_rate for l in self.live_logs])

        # 1. Latency Score (0 to 1)
        lat_ratio = demo_lat / live_lat if live_lat > 0 else 1.0
        lat_score = min(1.0, max(0.0, lat_ratio))

        # 2. Slippage Score (0 to 1)
        slip_diff_bps = max(0.0, live_slip - demo_slip)
        slip_score = math.exp(-slip_diff_bps / 10.0)  # Exponential decay penalty for unaccounted slippage

        # 3. Fill Rate Score (0 to 1)
        fr_score = live_fr / demo_fr if demo_fr > 0 else 1.0
        fr_score = min(1.0, max(0.0, fr_score))

        # Composite Realism Score: 30% Latency + 40% Slippage + 30% Fill Rate
        realism_score = max(0.0, min(1.0, 0.30 * lat_score + 0.40 * slip_score + 0.30 * fr_score))

        adjusted_sharpe = unadjusted_demo_sharpe * realism_score

        summary = (
            f"Execution Realism Score: {realism_score:.2f}/1.00 | "
            f"Live Latency: {live_lat:.1f}ms (Demo: {demo_lat:.1f}ms) | "
            f"Live Slippage: {live_slip:.1f}bps (Demo: {demo_slip:.1f}bps) | "
            f"Adjusted Sharpe: {adjusted_sharpe:.2f} (Demo: {unadjusted_demo_sharpe:.2f})"
        )
        logger.info(summary)

        return RealismAssessmentResult(
            mean_demo_latency_ms=demo_lat,
            mean_live_latency_ms=live_lat,
            mean_demo_slippage_bps=demo_slip,
            mean_live_slippage_bps=live_slip,
            demo_fill_rate=demo_fr,
            live_fill_rate=live_fr,
            realism_score=realism_score,
            adjusted_sharpe_ratio=adjusted_sharpe,
            summary=summary,
        )

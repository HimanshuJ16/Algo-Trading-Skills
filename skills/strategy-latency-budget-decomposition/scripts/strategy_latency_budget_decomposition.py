import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Legacy Config container for backward compatibility."""
    threshold: float = 1.0

class Engine:
    """Legacy Engine class for backward compatibility."""
    def __init__(self, config: Config):
        self.config = config

    def process(self, value: float) -> bool:
        return value > self.config.threshold

class LatencyPipelineStage(str, Enum):
    INGRESS_NETWORK = "INGRESS_NETWORK"           # Network NIC to application buffer
    MARKET_DATA_DECODE = "MARKET_DATA_DECODE"     # ITCH / SBE binary parser
    SIGNAL_COMPUTATION = "SIGNAL_COMPUTATION"     # Quantitative model execution
    PRE_TRADE_RISK = "PRE_TRADE_RISK"             # Risk limits & compliance check
    EGRESS_ORDER_ENCODE = "EGRESS_ORDER_ENCODE"   # FIX / OUCH encoding & wire transit

@dataclass
class StageLatencyMeasurement:
    stage: LatencyPipelineStage
    latency_us: float                            # Measured latency in microseconds
    sla_budget_us: float                         # Maximum SLA microsecond budget

@dataclass
class LatencyDecompositionReport:
    trade_id: str
    total_tick_to_trade_latency_us: float
    total_sla_budget_us: float
    is_within_budget: bool
    stage_breakdown: List[StageLatencyMeasurement]
    primary_bottleneck_stage: Optional[LatencyPipelineStage]
    breached_stages: List[LatencyPipelineStage]
    p99_jitter_us: float
    audit_notes: str

class StrategyLatencyBudgetDecompositionEngine:
    """
    Production-grade Strategy Latency Budget Decomposition Engine measuring microsecond tick-to-trade
    pipeline stages, profiling SLA budget breaches, and isolating execution bottlenecks.
    """
    DEFAULT_SLAS_US = {
        LatencyPipelineStage.INGRESS_NETWORK: 2.0,
        LatencyPipelineStage.MARKET_DATA_DECODE: 3.0,
        LatencyPipelineStage.SIGNAL_COMPUTATION: 10.0,
        LatencyPipelineStage.PRE_TRADE_RISK: 5.0,
        LatencyPipelineStage.EGRESS_ORDER_ENCODE: 5.0,
    }

    def __init__(self, stage_sla_budgets: Optional[Dict[LatencyPipelineStage, float]] = None):
        self.sla_budgets = stage_sla_budgets or self.DEFAULT_SLAS_US

    def decompose_tick_to_trade(
        self,
        trade_id: str,
        stage_latencies: Dict[LatencyPipelineStage, float]
    ) -> LatencyDecompositionReport:
        """
        Decomposes end-to-end tick-to-trade latency into pipeline stage measurements,
        compares against SLA budgets, and identifies bottlenecks.
        """
        total_latency = 0.0
        total_budget = 0.0
        stage_measurements: List[StageLatencyMeasurement] = []
        breached_stages: List[LatencyPipelineStage] = []
        max_overage = -1.0
        bottleneck: Optional[LatencyPipelineStage] = None

        all_latencies: List[float] = []

        for stage in LatencyPipelineStage:
            lat = stage_latencies.get(stage, 0.0)
            budget = self.sla_budgets.get(stage, 10.0)
            total_latency += lat
            total_budget += budget
            all_latencies.append(lat)

            stage_measurements.append(StageLatencyMeasurement(
                stage=stage,
                latency_us=lat,
                sla_budget_us=budget
            ))

            overage = lat - budget
            if overage > 0:
                breached_stages.append(stage)
                if overage > max_overage:
                    max_overage = overage
                    bottleneck = stage

        # Determine primary bottleneck if no single breach
        if not bottleneck and stage_latencies:
            bottleneck = max(stage_latencies.items(), key=lambda x: x[1])[0]

        is_within_budget = total_latency <= total_budget

        # P99 Jitter estimate (standard deviation scaled)
        mean_lat = total_latency / len(stage_latencies) if stage_latencies else 0.0
        variance = sum((l - mean_lat) ** 2 for l in all_latencies) / len(all_latencies) if all_latencies else 0.0
        p99_jitter = round(math.sqrt(variance) * 2.326, 2)

        status_str = "SLO_COMPLIANT" if is_within_budget else "SLA_BUDGET_BREACH"
        notes = (
            f"LATENCY DECOMPOSITION [{status_str}] ({trade_id}): Total Latency = {total_latency:.1f}us "
            f"(Budget {total_budget:.1f}us), Primary Bottleneck = {bottleneck.value if bottleneck else 'None'}, "
            f"Breached Stages = {[b.value for b in breached_stages]}."
        )

        if not is_within_budget:
            logger.warning(notes)
        else:
            logger.info(notes)

        return LatencyDecompositionReport(
            trade_id=trade_id,
            total_tick_to_trade_latency_us=round(total_latency, 2),
            total_sla_budget_us=round(total_budget, 2),
            is_within_budget=is_within_budget,
            stage_breakdown=stage_measurements,
            primary_bottleneck_stage=bottleneck,
            breached_stages=breached_stages,
            p99_jitter_us=p99_jitter,
            audit_notes=notes
        )

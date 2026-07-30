import math
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class EngineLoadMetrics:
    venue_id: str
    baseline_latency_us: float          # Base processing latency (e.g. 20.0 microseconds)
    engine_capacity_msgs_per_sec: float # Maximum service rate (e.g. 50,000 msgs/sec)
    arrival_rate_msgs_per_sec: float    # Current message arrival rate (lambda)

@dataclass
class MatchingEngineLoadAuditReport:
    venue_id: str
    arrival_rate_msgs_per_sec: float
    engine_capacity_msgs_per_sec: float
    utilization_factor_rho: float       # lambda / C (0.0 to 1.0)
    baseline_latency_us: float
    effective_latency_us: float         # Latency under load
    queuing_delay_penalty_us: float     # Effective Latency - Baseline
    latency_multiplier: float           # Effective / Baseline
    adverse_selection_risk_level: str   # 'LOW', 'MODERATE', 'HIGH_SNIPING_RISK'
    strategy_adaptation_directive: str  # 'NORMAL_OPERATIONS', 'WIDEN_PASSIVE_SPREADS', 'PAUSE_PASSIVE_QUOTING'
    audit_notes: str

class ExchangeMatchingEngineLoadSimulator:
    """
    Quantitative market microstructure engine for modeling exchange matching engine queuing delay under high message rate bursts,
    evaluating adverse selection risk, and adapting strategy quoting directives.
    """
    def __init__(self, high_congestion_threshold: float = 0.85, moderate_congestion_threshold: float = 0.50):
        self.high_congestion_threshold = high_congestion_threshold
        self.moderate_congestion_threshold = moderate_congestion_threshold

    def simulate_matching_engine_load(self, metrics: EngineLoadMetrics) -> MatchingEngineLoadAuditReport:
        """
        Simulates M/M/1 queuing delay non-linear latency spikes and evaluates strategy adaptation directives.
        """
        if metrics.engine_capacity_msgs_per_sec <= 0:
            raise ValueError("Engine capacity must be > 0.")

        rho = round(metrics.arrival_rate_msgs_per_sec / float(metrics.engine_capacity_msgs_per_sec), 4)

        # M/M/1 Queuing Theory Model: Effective Latency = Base / (1 - min(0.99, rho))
        clamped_rho = min(0.99, max(0.0, rho))
        effective_latency = round(metrics.baseline_latency_us / (1.0 - clamped_rho), 2)
        queuing_penalty = round(effective_latency - metrics.baseline_latency_us, 2)
        multiplier = round(effective_latency / float(metrics.baseline_latency_us), 2)

        # Strategy Adaptation Directives
        if rho >= self.high_congestion_threshold:
            risk_level = "HIGH_SNIPING_RISK"
            directive = "PAUSE_PASSIVE_QUOTING"
            notes = (
                f"HIGH MATCHING ENGINE CONGESTION [{metrics.venue_id}]: Utilization rho={rho:.2f} >= {self.high_congestion_threshold:.2f}. "
                f"Latency spiked {multiplier}x to {effective_latency:.1f}us (+{queuing_penalty:.1f}us delay). Directing PAUSE_PASSIVE_QUOTING to prevent stale quote sniping."
            )
            logger.critical(notes)
        elif rho >= self.moderate_congestion_threshold:
            risk_level = "MODERATE"
            directive = "WIDEN_PASSIVE_SPREADS"
            notes = (
                f"MODERATE MATCHING ENGINE CONGESTION [{metrics.venue_id}]: Utilization rho={rho:.2f}. "
                f"Latency increased {multiplier}x to {effective_latency:.1f}us. Directing WIDEN_PASSIVE_SPREADS to absorb queue drift."
            )
            logger.warning(notes)
        else:
            risk_level = "LOW"
            directive = "NORMAL_OPERATIONS"
            notes = f"MATCHING ENGINE HEALTHY [{metrics.venue_id}]: Utilization rho={rho:.2f}. Latency = {effective_latency:.1f}us."
            logger.info(notes)

        return MatchingEngineLoadAuditReport(
            venue_id=metrics.venue_id,
            arrival_rate_msgs_per_sec=metrics.arrival_rate_msgs_per_sec,
            engine_capacity_msgs_per_sec=metrics.engine_capacity_msgs_per_sec,
            utilization_factor_rho=rho,
            baseline_latency_us=metrics.baseline_latency_us,
            effective_latency_us=effective_latency,
            queuing_delay_penalty_us=queuing_penalty,
            latency_multiplier=multiplier,
            adverse_selection_risk_level=risk_level,
            strategy_adaptation_directive=directive,
            audit_notes=notes
        )

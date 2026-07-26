import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class StrategyProcessInfo:
    strategy_id: str
    priority_level: str                 # 'HIGH_HFT', 'MEDIUM_ARB', 'LOW_BATCH'
    pinned_cpu_core: int
    current_cpu_utilization_pct: float
    current_msg_rate_per_sec: float
    status: str                         # 'RUNNING', 'THROTTLED', 'PAUSED'

@dataclass
class ContentionMitigationReport:
    contention_state: str               # 'NORMAL', 'ELEVATED', 'CRITICAL_CONTENTION'
    overall_cpu_pct: float
    overall_ram_pct: float
    total_fix_msg_rate_sec: float
    throttled_strategies: List[str]
    paused_strategies: List[str]
    mitigation_directives: List[str]

class SharedInfrastructureContentionManager:
    """
    Real-time infrastructure management engine for auditing resource contention across co-located
    strategies, isolating CPU affinity, rate-limiting FIX gateways, and preempting low-priority tasks.
    """
    def __init__(
        self,
        max_fix_gateway_rate_sec: float = 1000.0,
        elevated_threshold_pct: float = 75.0,
        critical_threshold_pct: float = 85.0
    ):
        self.max_fix_gateway_rate_sec = max_fix_gateway_rate_sec
        self.elevated_threshold_pct = elevated_threshold_pct
        self.critical_threshold_pct = critical_threshold_pct
        self.processes: Dict[str, StrategyProcessInfo] = {}

    def register_process(self, process: StrategyProcessInfo):
        self.processes[process.strategy_id] = process

    def evaluate_contention_state(
        self,
        overall_cpu_pct: float,
        overall_ram_pct: float,
        current_fix_msg_rate_sec: float
    ) -> ContentionMitigationReport:
        """
        Audits shared infrastructure resource utilization and issues preemption / throttling directives.
        """
        fix_utilization_pct = (current_fix_msg_rate_sec / self.max_fix_gateway_rate_sec) * 100.0
        max_utilization = max(overall_cpu_pct, overall_ram_pct, fix_utilization_pct)

        throttled = []
        paused = []
        directives = []

        if max_utilization >= self.critical_threshold_pct:
            state = "CRITICAL_CONTENTION"
            logger.critical(
                f"CRITICAL RESOURCE CONTENTION: Max utilization {max_utilization:.1f}% >= {self.critical_threshold_pct}%. Preempting low-priority tasks!"
            )
            
            # Preemption Logic:
            # 1. PAUSE all 'LOW_BATCH' processes to free CPU & memory bandwidth
            # 2. THROTTLE 'MEDIUM_ARB' processes by 50%
            # 3. Reserve 100% resources for 'HIGH_HFT'
            for sid, proc in self.processes.items():
                if proc.priority_level == "LOW_BATCH":
                    proc.status = "PAUSED"
                    paused.append(sid)
                    directives.append(f"PAUSED process {sid} (LOW_BATCH) to relieve CPU/Memory contention.")
                elif proc.priority_level == "MEDIUM_ARB":
                    proc.status = "THROTTLED"
                    throttled.append(sid)
                    directives.append(f"THROTTLED process {sid} (MEDIUM_ARB) to 50% max message rate.")
                elif proc.priority_level == "HIGH_HFT":
                    proc.status = "RUNNING"
                    directives.append(f"PROTECTED process {sid} (HIGH_HFT) on pinned CPU Core {proc.pinned_cpu_core}.")

        elif max_utilization >= self.elevated_threshold_pct:
            state = "ELEVATED"
            logger.warning(f"ELEVATED RESOURCE UTILIZATION: {max_utilization:.1f}%. Monitoring closely.")
            directives.append("Elevated system usage. Restricting new background batch job spawns.")
            for sid, proc in self.processes.items():
                proc.status = "RUNNING"
        else:
            state = "NORMAL"
            directives.append("Resource usage normal. All processes running at 100% quota.")
            for sid, proc in self.processes.items():
                proc.status = "RUNNING"

        return ContentionMitigationReport(
            contention_state=state,
            overall_cpu_pct=round(overall_cpu_pct, 2),
            overall_ram_pct=round(overall_ram_pct, 2),
            total_fix_msg_rate_sec=round(current_fix_msg_rate_sec, 2),
            throttled_strategies=throttled,
            paused_strategies=paused,
            mitigation_directives=directives
        )

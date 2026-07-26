import numpy as np
import logging
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class CostRecord:
    timestamp: str
    service_name: str                  # e.g. 'EC2', 'EgressBandwidth', 'RDS'
    category: str                      # 'COMPUTE', 'NETWORK_EGRESS', 'STORAGE', 'DATABASE'
    environment: str                   # 'PROD', 'STAGING', 'DEV'
    cost_usd: float
    units_consumed: float              # e.g. GB transferred, hours run, or trade volume

@dataclass
class CostAnomalyReport:
    service_name: str
    current_cost_usd: float
    baseline_mean_usd: float
    baseline_std_usd: float
    z_score: float
    percentage_change_pct: float
    severity: str                      # 'NORMAL', 'WARNING', 'CRITICAL'
    unit_cost_usd: float
    recommendation: str

class CloudCostAnomalyDetector:
    """
    Quantitative FinOps engine for evaluating cloud infrastructure expenditure,
    detecting cost spikes using rolling Z-scores, and auditing unit economics.
    """
    def __init__(self, z_warning_threshold: float = 2.0, z_critical_threshold: float = 3.0):
        self.z_warning_threshold = z_warning_threshold
        self.z_critical_threshold = z_critical_threshold

    def compute_z_score(self, current_cost: float, historical_costs: List[float]) -> Tuple[float, float, float]:
        """
        Calculates Z-score, baseline mean, and baseline std dev.
        """
        if not historical_costs:
            return 0.0, current_cost, 0.0
            
        arr = np.array(historical_costs)
        mean = float(np.mean(arr))
        std = float(np.std(arr))
        
        if std < 1e-4:
            # Avoid divide-by-zero if std is near 0
            z_score = 0.0 if abs(current_cost - mean) < 1e-4 else (current_cost - mean) / 1.0
        else:
            z_score = (current_cost - mean) / std

        return round(float(z_score), 2), round(mean, 2), round(std, 2)

    def analyze_service_cost(
        self,
        current_record: CostRecord,
        historical_records: List[CostRecord],
        trading_volume: float = 1.0
    ) -> CostAnomalyReport:
        """
        Analyzes a current service cost record against historical baseline records.
        """
        hist_costs = [r.cost_usd for r in historical_records if r.service_name == current_record.service_name]
        z_score, mean, std = self.compute_z_score(current_record.cost_usd, hist_costs)

        pct_change = ((current_record.cost_usd - mean) / mean * 100.0) if mean > 0 else 0.0
        unit_cost = current_record.cost_usd / (trading_volume if trading_volume > 0 else 1.0)

        # Severity Classification
        if z_score >= self.z_critical_threshold and pct_change > 30.0:
            severity = "CRITICAL"
            rec = f"CRITICAL COST SPIKE: {current_record.service_name} spend (${current_record.cost_usd}) is {z_score} std dev above baseline. Inspect runaway workers or cross-AZ egress."
            logger.critical(rec)
        elif z_score >= self.z_warning_threshold:
            severity = "WARNING"
            rec = f"WARNING COST SPIKE: {current_record.service_name} spend (${current_record.cost_usd}) elevated ({pct_change:.1f}% above mean)."
            logger.warning(rec)
        else:
            severity = "NORMAL"
            rec = f"Service {current_record.service_name} spend is within expected baseline bounds."

        return CostAnomalyReport(
            service_name=current_record.service_name,
            current_cost_usd=round(current_record.cost_usd, 2),
            baseline_mean_usd=mean,
            baseline_std_usd=std,
            z_score=z_score,
            percentage_change_pct=round(pct_change, 2),
            severity=severity,
            unit_cost_usd=round(unit_cost, 6),
            recommendation=rec
        )

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class LatencyProfile:
    p50_latency_ns: float
    p99_latency_ns: float
    max_latency_ns: float
    std_dev_jitter_ns: float

@dataclass
class StrategyAlphaMetrics:
    daily_trade_frequency: int
    alpha_half_life_ns: float          # e.g. 5,000 ns
    estimated_annual_alpha_gain_usd: float

@dataclass
class FpgaHardwareCosts:
    smartnic_hardware_usd: float       # e.g. $15,000
    ip_core_licensing_usd: float       # e.g. $25,000
    annual_engineering_maintenance_usd: float # e.g. $20,000

@dataclass
class FpgaEvaluationReport:
    software_cpu_profile: LatencyProfile
    fpga_hardware_profile: LatencyProfile
    median_latency_reduction_ns: float
    tail_jitter_reduction_ratio: float
    total_cost_of_ownership_usd: float
    projected_annual_alpha_gain_usd: float
    net_annual_roi_usd: float
    recommendation: str                 # 'FPGA_RECOMMENDED', 'SOFTWARE_CPU_SUFFICIENT'
    audit_notes: str

class FpgaMarketDataEvaluationEngine:
    """
    Quantitative evaluation engine for comparing software CPU market data parsing vs hardware FPGA SmartNIC acceleration,
    auditing nanosecond tick-to-trade latency, and evaluating ROI.
    """
    def __init__(
        self,
        min_latency_reduction_threshold_ns: float = 1000.0, # Must save at least 1,000 ns (1 us)
        min_roi_net_benefit_usd: float = 25000.0
    ):
        self.min_latency_reduction_threshold_ns = min_latency_reduction_threshold_ns
        self.min_roi_net_benefit_usd = min_roi_net_benefit_usd

    def evaluate_fpga_acceleration(
        self,
        cpu_profile: LatencyProfile,
        fpga_profile: LatencyProfile,
        strategy_metrics: StrategyAlphaMetrics,
        costs: FpgaHardwareCosts
    ) -> FpgaEvaluationReport:
        """
        Audits software CPU vs hardware FPGA latency and financial ROI to generate hardware deployment recommendations.
        """
        # 1. Calculate Latency & Jitter Metrics
        delta_p50_ns = round(cpu_profile.p50_latency_ns - fpga_profile.p50_latency_ns, 2)
        
        cpu_jitter = cpu_profile.p99_latency_ns - cpu_profile.p50_latency_ns
        fpga_jitter = fpga_profile.p99_latency_ns - fpga_profile.p50_latency_ns
        jitter_ratio = round(cpu_jitter / max(1.0, fpga_jitter), 2)

        # 2. Compute TCO and Net Financial ROI
        tco_usd = round(costs.smartnic_hardware_usd + costs.ip_core_licensing_usd + costs.annual_engineering_maintenance_usd, 2)
        net_roi_usd = round(strategy_metrics.estimated_annual_alpha_gain_usd - tco_usd, 2)

        # 3. Decision Rules
        is_latency_justified = (delta_p50_ns >= self.min_latency_reduction_threshold_ns)
        is_financial_justified = (net_roi_usd >= self.min_roi_net_benefit_usd)

        if is_latency_justified and is_financial_justified:
            rec = "FPGA_RECOMMENDED"
            notes = (
                f"FPGA ACCELERATION RECOMMENDED: Cuts median tick-to-trade latency by {delta_p50_ns:,.0f}ns "
                f"({cpu_profile.p50_latency_ns:,.0f}ns -> {fpga_profile.p50_latency_ns:,.0f}ns) with {jitter_ratio}x jitter reduction. "
                f"Projected Annual Net ROI = ${net_roi_usd:,.2f} (Gain = ${strategy_metrics.estimated_annual_alpha_gain_usd:,.2f} vs TCO = ${tco_usd:,.2f})."
            )
            logger.info(notes)
        else:
            rec = "SOFTWARE_CPU_SUFFICIENT"
            reasons = []
            if not is_latency_justified:
                reasons.append(f"Latency savings {delta_p50_ns:.0f}ns < required {self.min_latency_reduction_threshold_ns:.0f}ns")
            if not is_financial_justified:
                reasons.append(f"Net ROI ${net_roi_usd:,.2f} < required threshold ${self.min_roi_net_benefit_usd:,.2f}")
            notes = f"SOFTWARE CPU SUFFICIENT: FPGA hardware not justified ({'; '.join(reasons)}). Recommend DPDK/EF_VI software optimization."
            logger.warning(notes)

        return FpgaEvaluationReport(
            software_cpu_profile=cpu_profile,
            fpga_hardware_profile=fpga_profile,
            median_latency_reduction_ns=delta_p50_ns,
            tail_jitter_reduction_ratio=jitter_ratio,
            total_cost_of_ownership_usd=tco_usd,
            projected_annual_alpha_gain_usd=strategy_metrics.estimated_annual_alpha_gain_usd,
            net_annual_roi_usd=net_roi_usd,
            recommendation=rec,
            audit_notes=notes
        )

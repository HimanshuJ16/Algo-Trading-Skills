import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class UniverseScaleSpec:
    universe_name: str                  # e.g. 'Russell 3000 Expansion'
    current_universe_size: int          # e.g. 50 symbols
    target_universe_size: int           # e.g. 3,000 symbols
    avg_ticks_sec_per_symbol: float = 10.0 # Baseline ticks/sec per instrument
    peak_volatility_multiplier: float = 5.0 # Market open / news event spike multiplier
    bytes_per_tick: int = 320           # Tick message payload size in bytes
    memory_mb_per_orderbook: float = 20.0 # RAM required per L2 order book structure (MB)
    db_write_fraction: float = 0.50     # Percentage of ticks written to persistent database

@dataclass
class HardwareCapacitySpec:
    available_ram_gb: float             # Total available RAM (GB)
    max_network_mbps: float             # Max NIC network bandwidth (Mbps)
    max_db_iops: float                  # Max database write IOPS limit
    available_cpu_cores: int = 16

@dataclass
class LoadTestReport:
    universe_name: str
    target_universe_size: int
    projected_peak_msg_rate_per_sec: float
    projected_ram_required_gb: float
    projected_network_mbps: float
    projected_db_iops: float
    ram_utilization_pct: float
    network_utilization_pct: float
    db_iops_utilization_pct: float
    is_ram_capacity_ok: bool
    is_network_capacity_ok: bool
    is_db_iops_capacity_ok: bool
    status: str                         # 'LOAD_TEST_PASSED_READY_TO_SCALE', 'LOAD_TEST_FAILED_MEMORY_EXCEEDED', 'LOAD_TEST_FAILED_NETWORK_EXCEEDED', 'LOAD_TEST_FAILED_IOPS_EXCEEDED'
    audit_notes: str

class InfrastructureLoadTesterEngine:
    """
    Pre-scaling infrastructure capacity planning and load testing engine,
    projecting tick message throughput, L2 order book memory footprints, and network bandwidth utilization before expanding instrument universes.
    """
    def __init__(self, max_safe_utilization_pct: float = 80.0):
        self.max_safe_utilization_pct = max_safe_utilization_pct

    def audit_universe_scaling_load(
        self,
        scale_spec: UniverseScaleSpec,
        hardware_spec: HardwareCapacitySpec
    ) -> LoadTestReport:
        """
        Projects peak message throughput, RAM, Network, and DB IOPS utilization for scaled universe size and audits against hardware.
        """
        n_target = scale_spec.target_universe_size
        if n_target <= 0:
            raise ValueError("Target universe size must be strictly positive.")

        # 1. Peak Message Throughput (msg/sec)
        peak_msg_rate = n_target * scale_spec.avg_ticks_sec_per_symbol * scale_spec.peak_volatility_multiplier

        # 2. RAM Footprint (GB) with 25% safety buffer
        raw_ram_mb = n_target * scale_spec.memory_mb_per_orderbook * 1.25
        proj_ram_gb = round(raw_ram_mb / 1024.0, 2)

        # 3. Peak Network Bandwidth (Mbps)
        # Mbps = (msg/sec * bytes_per_msg * 8) / 1,000,000
        proj_net_mbps = round((peak_msg_rate * scale_spec.bytes_per_tick * 8.0) / 1_000_000.0, 2)

        # 4. Database Write IOPS
        proj_db_iops = round(peak_msg_rate * scale_spec.db_write_fraction, 2)

        # Utilization Percentages
        ram_util = round((proj_ram_gb / hardware_spec.available_ram_gb) * 100.0, 1)
        net_util = round((proj_net_mbps / hardware_spec.max_network_mbps) * 100.0, 1)
        iops_util = round((proj_db_iops / hardware_spec.max_db_iops) * 100.0, 1)

        is_ram_ok = (ram_util <= self.max_safe_utilization_pct)
        is_net_ok = (net_util <= self.max_safe_utilization_pct)
        is_iops_ok = (iops_util <= self.max_safe_utilization_pct)

        if not is_ram_ok:
            status = "LOAD_TEST_FAILED_MEMORY_EXCEEDED"
            notes = (
                f"LOAD TEST FAILED [{scale_spec.universe_name}]: Projected RAM {proj_ram_gb:.1f} GB "
                f"({ram_util:.1f}% util) exceeds safe limit ({self.max_safe_utilization_pct:.0f}% of {hardware_spec.available_ram_gb:.0f} GB)."
            )
            logger.error(notes)
        elif not is_net_ok:
            status = "LOAD_TEST_FAILED_NETWORK_EXCEEDED"
            notes = (
                f"LOAD TEST FAILED [{scale_spec.universe_name}]: Projected Network Bandwidth {proj_net_mbps:.1f} Mbps "
                f"({net_util:.1f}% util) exceeds safe limit ({self.max_safe_utilization_pct:.0f}% of {hardware_spec.max_network_mbps:.0f} Mbps)."
            )
            logger.error(notes)
        elif not is_iops_ok:
            status = "LOAD_TEST_FAILED_IOPS_EXCEEDED"
            notes = (
                f"LOAD TEST FAILED [{scale_spec.universe_name}]: Projected DB IOPS {proj_db_iops:,.0f} "
                f"({iops_util:.1f}% util) exceeds safe limit ({self.max_safe_utilization_pct:.0f}% of {hardware_spec.max_db_iops:,.0f} IOPS)."
            )
            logger.error(notes)
        else:
            status = "LOAD_TEST_PASSED_READY_TO_SCALE"
            notes = (
                f"LOAD TEST PASSED [{scale_spec.universe_name}]: Scaled to {n_target:,} symbols. "
                f"Peak Msg Rate = {peak_msg_rate:,.0f} msg/sec. RAM = {proj_ram_gb:.1f} GB ({ram_util:.1f}%), "
                f"Network = {proj_net_mbps:.1f} Mbps ({net_util:.1f}%), IOPS = {proj_db_iops:,.0f} ({iops_util:.1f}%). System ready to scale."
            )
            logger.info(notes)

        return LoadTestReport(
            universe_name=scale_spec.universe_name,
            target_universe_size=n_target,
            projected_peak_msg_rate_per_sec=peak_msg_rate,
            projected_ram_required_gb=proj_ram_gb,
            projected_network_mbps=proj_net_mbps,
            projected_db_iops=proj_db_iops,
            ram_utilization_pct=ram_util,
            network_utilization_pct=net_util,
            db_iops_utilization_pct=iops_util,
            is_ram_capacity_ok=is_ram_ok,
            is_network_capacity_ok=is_net_ok,
            is_db_iops_capacity_ok=is_iops_ok,
            status=status,
            audit_notes=notes
        )

import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

@dataclass
class HardwareSpec:
    nic_bandwidth_mbps: float       # E.g., 10000 for 10GbE
    cpu_msgs_per_sec_per_core: int  # How many messages a single core can process per second
    available_ram_gb: float

@dataclass
class UniverseSpec:
    num_symbols: int
    peak_ticks_per_sec_per_symbol: int
    bytes_per_msg: int              # Include TCP/IP overhead (e.g., payload + 60 bytes)
    state_memory_mb_per_symbol: float # Memory needed to store order book/indicators per symbol

@dataclass
class CapacityReport:
    total_peak_msgs_per_sec: int
    required_network_mbps: float
    required_cpu_cores: int
    required_ram_gb: float
    network_bottleneck: bool
    cpu_bottleneck: bool
    ram_bottleneck: bool
    is_viable: bool

class CapacityPlanner:
    """
    Models hardware requirements for algorithmic trading systems 
    as the symbol universe grows.
    """
    def __init__(self, hardware: HardwareSpec):
        self.hardware = hardware
        # We enforce a maximum 60% network utilization rule to prevent TCP bufferbloat
        self.max_safe_network_mbps = hardware.nic_bandwidth_mbps * 0.60

    def evaluate(self, universe: UniverseSpec) -> CapacityReport:
        # 1. Total Throughput
        total_msgs = universe.num_symbols * universe.peak_ticks_per_sec_per_symbol
        
        # 2. Network Bandwidth (Mbps)
        # Total bytes per second * 8 bits/byte / 1,000,000
        bytes_per_sec = total_msgs * universe.bytes_per_msg
        required_mbps = (bytes_per_sec * 8) / 1_000_000.0
        
        # 3. CPU Cores
        required_cores = math.ceil(total_msgs / self.hardware.cpu_msgs_per_sec_per_core)
        
        # 4. RAM
        required_ram_mb = universe.num_symbols * universe.state_memory_mb_per_symbol
        required_ram_gb = required_ram_mb / 1024.0
        
        # Bottleneck checks
        net_bottleneck = required_mbps > self.max_safe_network_mbps
        cpu_bottleneck = required_cores > 64 # Assume a standard max of 64 dedicated cores per server
        ram_bottleneck = required_ram_gb > self.hardware.available_ram_gb
        
        is_viable = not (net_bottleneck or cpu_bottleneck or ram_bottleneck)
        
        logger.info(f"Capacity Report for {universe.num_symbols} symbols: Viable={is_viable}")
        logger.info(f"Required: {required_mbps:.2f} Mbps, {required_cores} Cores, {required_ram_gb:.2f} GB RAM")
        
        return CapacityReport(
            total_peak_msgs_per_sec=total_msgs,
            required_network_mbps=required_mbps,
            required_cpu_cores=required_cores,
            required_ram_gb=required_ram_gb,
            network_bottleneck=net_bottleneck,
            cpu_bottleneck=cpu_bottleneck,
            ram_bottleneck=ram_bottleneck,
            is_viable=is_viable
        )

"""
capacity-planning-for-symbol-universe-growth: forecasts the network, CPU and memory
required to consume a market data feed as the symbol universe grows.

Unit conventions (deliberately mixed, matching how each domain is quoted commercially):
  - Network is decimal: 1 Mbps = 1e6 bits/sec, so a "10 GbE" NIC is 10_000 Mbps.
  - Memory is binary:   1 GB = 1024 MB (i.e. GiB/MiB), matching how RAM is sold and
    how operating systems report it.
"""
import logging
import math
from dataclasses import dataclass

logger = logging.getLogger(__name__)

BITS_PER_BYTE = 8
BITS_PER_MEGABIT = 1_000_000

# Wire overhead per packet, in bytes, for common market data transports. These are the
# full L1/L2 costs: preamble+SFD (8) + Ethernet header (14) + FCS (4) + interframe gap
# (12) = 38, plus the L3/L4 headers.
ETHERNET_L1_L2_OVERHEAD_BYTES = 38
IPV4_HEADER_BYTES = 20
UDP_HEADER_BYTES = 8
TCP_HEADER_BYTES = 20  # minimum, without options
UDP_IPV4_ETHERNET_OVERHEAD_BYTES = (
    ETHERNET_L1_L2_OVERHEAD_BYTES + IPV4_HEADER_BYTES + UDP_HEADER_BYTES)  # 66
TCP_IPV4_ETHERNET_OVERHEAD_BYTES = (
    ETHERNET_L1_L2_OVERHEAD_BYTES + IPV4_HEADER_BYTES + TCP_HEADER_BYTES)  # 78

# MoldUDP64 (Nasdaq ITCH transport): 20-byte packet header (10-byte session, 8-byte
# sequence number, 2-byte message count) plus a 2-byte length field per message block.
MOLDUDP64_HEADER_BYTES = 20
MOLDUDP64_PER_MESSAGE_BYTES = 2
MOLDUDP64_MAX_PACKET_BYTES = 1472  # Max MoldUDP64 downstream packet, per the spec

# Standard Ethernet MTU payload. Used only to sanity-check an implausible batching factor.
STANDARD_MTU_PAYLOAD_BYTES = 1500


def _require_finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a real number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    return value


def _require_positive(name: str, value: float) -> float:
    _require_finite(name, value)
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value!r}")
    return value


def _require_non_negative(name: str, value: float) -> float:
    _require_finite(name, value)
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}")
    return value


def _require_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {value!r}")
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value!r}")
    return value


@dataclass
class HardwareSpec:
    """
    The server the feed handler will run on.

    available_cpu_cores is the number of cores that can actually be dedicated to feed
    processing - not the machine's total core count, since the OS and other processes
    need their own. It defaults to 64, which was this model's previous hard-coded
    assumption; set it to your real figure.
    """
    nic_bandwidth_mbps: float       # E.g., 10000 for 10GbE (decimal megabits)
    cpu_msgs_per_sec_per_core: int  # How many messages a single core can process per second
    available_ram_gb: float
    available_cpu_cores: int = 64

    def __post_init__(self) -> None:
        _require_positive("nic_bandwidth_mbps", self.nic_bandwidth_mbps)
        _require_positive_int("cpu_msgs_per_sec_per_core", self.cpu_msgs_per_sec_per_core)
        _require_positive("available_ram_gb", self.available_ram_gb)
        _require_positive_int("available_cpu_cores", self.available_cpu_cores)


@dataclass
class UniverseSpec:
    """
    The target symbol universe and the wire characteristics of its feed.

    bytes_per_msg is the per-message byte cost. There are two consistent ways to fill it
    in; do not mix them:
      1. Unbatched / simple model: put payload + per-packet framing overhead in
         bytes_per_msg and leave msgs_per_packet=1, packet_overhead_bytes=0.
      2. Batched model (correct for ITCH over MoldUDP64 and similar): put only the
         payload in bytes_per_msg, set packet_overhead_bytes to the framing cost per
         packet (see UDP_IPV4_ETHERNET_OVERHEAD_BYTES) and msgs_per_packet to the
         observed batching factor. Charging full framing to every message overstates
         bandwidth several-fold on a batched feed.

    redundant_feeds models A/B line redundancy: taking both sides of a redundant
    multicast feed doubles the bandwidth requirement.

    retransmission_overhead_fraction inflates bandwidth to cover gap-fill/retransmission
    traffic, expressed as a fraction (0.10 = +10%).
    """
    num_symbols: int
    peak_ticks_per_sec_per_symbol: int
    bytes_per_msg: int              # See note above on batching
    state_memory_mb_per_symbol: float  # Memory to store order book/indicators per symbol
    msgs_per_packet: int = 1
    packet_overhead_bytes: int = 0
    redundant_feeds: int = 1
    retransmission_overhead_fraction: float = 0.0

    def __post_init__(self) -> None:
        _require_positive_int("num_symbols", self.num_symbols)
        _require_positive_int("peak_ticks_per_sec_per_symbol", self.peak_ticks_per_sec_per_symbol)
        _require_positive_int("bytes_per_msg", self.bytes_per_msg)
        _require_positive("state_memory_mb_per_symbol", self.state_memory_mb_per_symbol)
        _require_positive_int("msgs_per_packet", self.msgs_per_packet)
        _require_non_negative("packet_overhead_bytes", self.packet_overhead_bytes)
        _require_positive_int("redundant_feeds", self.redundant_feeds)
        _require_non_negative("retransmission_overhead_fraction", self.retransmission_overhead_fraction)


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
    # A single symbol's stream cannot be split across cores when partitioning by symbol,
    # so this design is infeasible at any core count when True.
    single_symbol_exceeds_core: bool = False
    max_safe_network_mbps: float = 0.0


def peak_rate_per_sec_from_burst(messages: int, window_ms: float) -> float:
    """
    Convert a burst observation into the per-second rate to plan against.

    Feeds are specified and sized on sub-second windows precisely because a one-second
    average hides microbursts: OPRA publishes its capacity projections in messages per
    100ms and per 10ms intervals, noting that the 10ms interval "reflects system
    utilization during bursts of traffic". Measuring your peak over a one-second window
    and planning against it will under-provision.

    Example: 40,000 messages observed in a 10ms window -> 4,000,000 msgs/sec.
    """
    _require_positive_int("messages", messages)
    _require_positive("window_ms", window_ms)
    return messages * (1000.0 / window_ms)


class CapacityPlanner:
    """
    Models hardware requirements for algorithmic trading systems as the symbol universe
    grows.

    max_network_utilization caps how much of the NIC the design may consume. The default
    of 0.60 leaves headroom for queueing delay and microbursts: in a simple M/M/1 model
    mean queueing delay scales with 1/(1 - rho), so latency degrades sharply well before
    the link is "full", and a link at 60% of line rate averaged over a second can still
    be at 100% for a 10ms burst.

    safety_margin multiplies the *message rate* to cover forecast uncertainty (the
    referenced engineering practice is 1.5x-2.0x). It defaults to 1.0 - no margin - so
    the model reports what you asked for; set it deliberately. It does not scale RAM,
    which is driven by symbol count rather than tick rate.
    """

    def __init__(
        self,
        hardware: HardwareSpec,
        max_network_utilization: float = 0.60,
        safety_margin: float = 1.0,
    ) -> None:
        _require_positive("max_network_utilization", max_network_utilization)
        if max_network_utilization > 1.0:
            raise ValueError(
                f"max_network_utilization must be <= 1.0, got {max_network_utilization!r}")
        if _require_finite("safety_margin", safety_margin) < 1.0:
            raise ValueError(f"safety_margin must be >= 1.0, got {safety_margin!r}")

        self.hardware = hardware
        self.max_network_utilization = max_network_utilization
        self.safety_margin = safety_margin
        self.max_safe_network_mbps = hardware.nic_bandwidth_mbps * max_network_utilization

    def evaluate(self, universe: UniverseSpec) -> CapacityReport:
        """Compute the capacity required for `universe` on this planner's hardware."""
        # 1. Total throughput, inflated by the forecast safety margin.
        raw_msgs = universe.num_symbols * universe.peak_ticks_per_sec_per_symbol
        total_msgs = math.ceil(raw_msgs * self.safety_margin)

        # 2. Network bandwidth. Payload is charged per message; framing is charged per
        #    packet, so a batched transport does not pay full framing for every message.
        batched_payload_bytes = universe.msgs_per_packet * universe.bytes_per_msg
        if batched_payload_bytes > STANDARD_MTU_PAYLOAD_BYTES:
            # Over-stating the batching factor under-states bandwidth - the dangerous
            # direction for a capacity forecast - so say so loudly.
            logger.warning(
                f"msgs_per_packet={universe.msgs_per_packet} x bytes_per_msg="
                f"{universe.bytes_per_msg} = {batched_payload_bytes} bytes exceeds a "
                f"standard {STANDARD_MTU_PAYLOAD_BYTES}-byte MTU; this batching factor is "
                "not achievable without jumbo frames and will under-state bandwidth.")

        packets_per_sec = math.ceil(total_msgs / universe.msgs_per_packet)
        payload_bytes_per_sec = total_msgs * universe.bytes_per_msg
        framing_bytes_per_sec = packets_per_sec * universe.packet_overhead_bytes
        wire_bytes_per_sec = (payload_bytes_per_sec + framing_bytes_per_sec)
        wire_bytes_per_sec *= (1.0 + universe.retransmission_overhead_fraction)
        wire_bytes_per_sec *= universe.redundant_feeds
        required_mbps = (wire_bytes_per_sec * BITS_PER_BYTE) / float(BITS_PER_MEGABIT)

        # 3. CPU cores.
        required_cores = math.ceil(total_msgs / self.hardware.cpu_msgs_per_sec_per_core)

        # A single symbol's message stream cannot be partitioned across cores when
        # sharding by symbol, so a symbol hotter than one core makes the design
        # infeasible no matter how many cores are added.
        per_symbol_msgs = universe.peak_ticks_per_sec_per_symbol * self.safety_margin
        single_symbol_exceeds_core = per_symbol_msgs > self.hardware.cpu_msgs_per_sec_per_core

        # 4. RAM. Binary units: MB -> GB divides by 1024.
        required_ram_mb = universe.num_symbols * universe.state_memory_mb_per_symbol
        required_ram_gb = required_ram_mb / 1024.0

        # Bottleneck checks.
        net_bottleneck = required_mbps > self.max_safe_network_mbps
        cpu_bottleneck = (
            required_cores > self.hardware.available_cpu_cores or single_symbol_exceeds_core)
        ram_bottleneck = required_ram_gb > self.hardware.available_ram_gb

        is_viable = not (net_bottleneck or cpu_bottleneck or ram_bottleneck)

        logger.info(f"Capacity Report for {universe.num_symbols} symbols: Viable={is_viable}")
        logger.info(
            f"Required: {required_mbps:.2f} Mbps, {required_cores} Cores, "
            f"{required_ram_gb:.2f} GB RAM")
        if single_symbol_exceeds_core:
            logger.warning(
                f"Single symbol peak {per_symbol_msgs:,.0f} msgs/sec exceeds one core's "
                f"{self.hardware.cpu_msgs_per_sec_per_core:,} msgs/sec; symbol-partitioned "
                "processing cannot scale this design by adding cores.")

        return CapacityReport(
            total_peak_msgs_per_sec=total_msgs,
            required_network_mbps=required_mbps,
            required_cpu_cores=required_cores,
            required_ram_gb=required_ram_gb,
            network_bottleneck=net_bottleneck,
            cpu_bottleneck=cpu_bottleneck,
            ram_bottleneck=ram_bottleneck,
            is_viable=is_viable,
            single_symbol_exceeds_core=single_symbol_exceeds_core,
            max_safe_network_mbps=self.max_safe_network_mbps,
        )

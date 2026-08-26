"""
load-testing-before-scaling-to-new-instrument-universe: projects the tick throughput,
order book memory, network bandwidth and database write load that a larger instrument
universe will impose, and gates the scale-up on a utilization ceiling.

This is a first-order *projection* from per-symbol figures you have measured. It is not
a load test: it tells you whether a load test is worth running and what rate to run it
at. Nothing here observes a real system.

Unit conventions (deliberately mixed, matching how each domain is quoted commercially):
  - Network is decimal: 1 Mbps = 1e6 bits/sec, so a "10 GbE" NIC is 10_000 Mbps.
  - Memory is binary:   1 GB = 1024 MB (i.e. GiB/MiB), matching how RAM is sold and
    how operating systems report it.

Threshold comparisons are made on unrounded values, and the reported figures are
unrounded too, so a caller can recompute any utilization percentage from the report.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import List

logger = logging.getLogger(__name__)

BITS_PER_BYTE = 8
BITS_PER_MEGABIT = 1_000_000
MB_PER_GB = 1024.0

# Report status values. Exported as constants so callers compare against a symbol
# rather than retyping the string.
STATUS_PASSED = "LOAD_TEST_PASSED_READY_TO_SCALE"
STATUS_FAILED_MEMORY = "LOAD_TEST_FAILED_MEMORY_EXCEEDED"
STATUS_FAILED_NETWORK = "LOAD_TEST_FAILED_NETWORK_EXCEEDED"
STATUS_FAILED_IOPS = "LOAD_TEST_FAILED_IOPS_EXCEEDED"


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


def _require_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an int, got {value!r}")
    if value <= 0:
        raise ValueError(f"{name} must be > 0, got {value!r}")
    return value


def _require_at_least(name: str, value: float, minimum: float) -> float:
    _require_finite(name, value)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value!r}")
    return value


def _require_fraction(name: str, value: float) -> float:
    _require_finite(name, value)
    if not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1 inclusive, got {value!r}")
    return value


@dataclass
class UniverseScaleSpec:
    """
    The universe you are scaling to, and the per-symbol load it is expected to generate.

    Every per-symbol default below is an ILLUSTRATIVE PLACEHOLDER, not a measured
    constant, and each one scales the whole projection linearly. Measure them on your
    own stack before trusting a verdict:

      - avg_ticks_sec_per_symbol is an *average*; the peak is applied separately via
        peak_volatility_multiplier. Message rates are heavy-tailed across symbols, so a
        single uniform average understates the hot names that actually break a feed
        handler. Model liquidity tiers separately and audit each tier if that matters.
      - bytes_per_tick at 320 bytes is representative of a verbose JSON WebSocket
        payload. A binary exchange feed (e.g. ITCH) is an order of magnitude smaller.
      - memory_mb_per_orderbook at 20 MB is a placeholder for a Python-object-heavy
        full-depth book plus its per-symbol buffers. A compact fixed-depth array book in
        a compiled language is kilobytes. Measure with an RSS delta or tracemalloc.

    peak_volatility_multiplier multiplies the *average* rate. Do not confuse it with the
    EU stress-test floor in Article 10 of RTS 6, which is twice the highest volume
    actually *observed* over the previous six months - a different and usually larger
    quantity. See references/standards.md.

    memory_allocation_buffer is an allocator/fragmentation headroom heuristic, not a
    published standard; 1.25 (+25%) is this skill's default. Set it deliberately.

    wire_overhead_factor inflates the payload-only bandwidth figure to cover per-packet
    framing, A/B feed redundancy and retransmissions. It defaults to 1.0, meaning the
    projection charges payload bytes only and therefore UNDER-states wire bandwidth -
    the dangerous direction for a capacity gate. Derive the real ratio with
    capacity-planning-for-symbol-universe-growth, which models packet framing and
    batching properly, and pass it in here.

    ticks_per_write_io converts persisted ticks into storage write operations. It
    defaults to 1.0 (one IO per persisted tick), which over-states IOPS for any batching
    writer - group-commit WAL, LSM-tree flush, columnar batch insert - and so errs in
    the conservative direction. Set it to your measured batch factor.
    """
    universe_name: str                       # e.g. Russell 3000 Expansion
    current_universe_size: int               # Symbols in the universe today
    target_universe_size: int                # Symbols after the scale-up
    avg_ticks_sec_per_symbol: float = 10.0   # Baseline (average) ticks/sec per instrument
    peak_volatility_multiplier: float = 5.0  # Market open / news event spike multiplier
    bytes_per_tick: int = 320                # Tick message payload size in bytes
    memory_mb_per_orderbook: float = 20.0    # RAM per L2 order book structure (MB)
    db_write_fraction: float = 0.50          # Fraction of ticks persisted to the database
    memory_allocation_buffer: float = 1.25   # Allocator headroom multiplier on order book RAM
    wire_overhead_factor: float = 1.0        # Framing / redundancy / retransmit multiplier
    ticks_per_write_io: float = 1.0          # Persisted ticks coalesced into one write IO

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Reject inputs that would silently produce a meaningless projection."""
        if not isinstance(self.universe_name, str) or not self.universe_name.strip():
            raise ValueError(
                f"universe_name must be a non-empty string, got {self.universe_name!r}")
        _require_positive_int("current_universe_size", self.current_universe_size)
        _require_positive_int("target_universe_size", self.target_universe_size)
        _require_positive("avg_ticks_sec_per_symbol", self.avg_ticks_sec_per_symbol)
        _require_at_least("peak_volatility_multiplier", self.peak_volatility_multiplier, 1.0)
        _require_positive_int("bytes_per_tick", self.bytes_per_tick)
        _require_positive("memory_mb_per_orderbook", self.memory_mb_per_orderbook)
        _require_fraction("db_write_fraction", self.db_write_fraction)
        _require_at_least("memory_allocation_buffer", self.memory_allocation_buffer, 1.0)
        _require_at_least("wire_overhead_factor", self.wire_overhead_factor, 1.0)
        _require_at_least("ticks_per_write_io", self.ticks_per_write_io, 1.0)


@dataclass
class HardwareCapacitySpec:
    """
    The hardware the scaled universe has to fit on.

    available_cpu_cores is carried for documentation but is not modelled here: this
    skill gates on memory, network and storage IO. For CPU core sizing and wire-accurate
    bandwidth, use capacity-planning-for-symbol-universe-growth.
    """
    available_ram_gb: float             # RAM available to the feed handler (GB, binary)
    max_network_mbps: float             # NIC line rate (Mbps, decimal)
    max_db_iops: float                  # Sustained write IOPS the storage layer absorbs
    available_cpu_cores: int = 16

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        """Reject capacities that would divide by zero or invert a utilization ratio."""
        _require_positive("available_ram_gb", self.available_ram_gb)
        _require_positive("max_network_mbps", self.max_network_mbps)
        _require_positive("max_db_iops", self.max_db_iops)
        _require_positive_int("available_cpu_cores", self.available_cpu_cores)


@dataclass
class LoadTestReport:
    """
    Projected load for the target universe and the pass/fail verdict against hardware.

    status names the highest-priority breach only (memory, then network, then IOPS).
    breached_resources lists *every* resource over the ceiling - read that, not just
    status, or you will fix one bottleneck and rediscover the next on the re-run.
    """
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
    status: str                         # One of the STATUS_* constants in this module
    audit_notes: str
    universe_scale_factor: float = 1.0  # target_universe_size / current_universe_size
    breached_resources: List[str] = field(default_factory=list)


class InfrastructureLoadTesterEngine:
    """
    Pre-scaling infrastructure capacity projection and scale-up gate.

    Projects peak tick message throughput, L2 order book memory footprint, network
    bandwidth and database write IOPS for a target instrument universe, then compares
    each against a utilization ceiling.

    max_safe_utilization_pct defaults to 80.0. That ceiling is an operating heuristic,
    not a published standard: in an M/M/1 queue mean response time scales with
    1/(1 - rho), so latency degrades sharply well before a resource is "full", and a
    link averaging 80% over a second can be saturated for a 10 ms microburst. Lower it
    when the projection is built from second-averaged rates rather than burst rates.
    """

    def __init__(self, max_safe_utilization_pct: float = 80.0) -> None:
        _require_positive("max_safe_utilization_pct", max_safe_utilization_pct)
        if max_safe_utilization_pct > 100.0:
            raise ValueError(
                f"max_safe_utilization_pct must be <= 100.0, "
                f"got {max_safe_utilization_pct!r}")
        self.max_safe_utilization_pct = max_safe_utilization_pct

    def audit_universe_scaling_load(
        self,
        scale_spec: UniverseScaleSpec,
        hardware_spec: HardwareCapacitySpec,
    ) -> LoadTestReport:
        """
        Project peak throughput, RAM, network and DB IOPS for the target universe size
        and audit each against the hardware ceiling.

        Raises ValueError if either spec is invalid. Both specs are re-validated here
        rather than trusted from construction time: dataclasses are mutable, and a
        negative capacity silently produces a negative utilization that would otherwise
        sail through the ceiling check as a PASS.
        """
        scale_spec.validate()
        hardware_spec.validate()

        n_target = scale_spec.target_universe_size
        n_current = scale_spec.current_universe_size
        scale_factor = n_target / float(n_current)

        if n_target < n_current:
            logger.warning(
                f"[{scale_spec.universe_name}] target universe ({n_target:,}) is smaller "
                f"than the current universe ({n_current:,}); this skill gates universe "
                "*growth*, and a shrink needs no capacity headroom check.")

        # 1. Peak message throughput (msg/sec): average rate lifted by the peak multiplier.
        peak_msg_rate = (
            n_target
            * scale_spec.avg_ticks_sec_per_symbol
            * scale_spec.peak_volatility_multiplier)

        # 2. L2 order book RAM footprint (GB, binary) plus allocator headroom.
        raw_ram_mb = (
            n_target
            * scale_spec.memory_mb_per_orderbook
            * scale_spec.memory_allocation_buffer)
        proj_ram_gb = raw_ram_mb / MB_PER_GB

        # 3. Peak network bandwidth (Mbps, decimal). Payload bytes only unless the caller
        #    supplies a wire_overhead_factor covering framing, redundancy and retransmits.
        payload_bytes_per_sec = peak_msg_rate * scale_spec.bytes_per_tick
        wire_bytes_per_sec = payload_bytes_per_sec * scale_spec.wire_overhead_factor
        proj_net_mbps = (wire_bytes_per_sec * BITS_PER_BYTE) / float(BITS_PER_MEGABIT)

        # 4. Database write IOPS: persisted ticks, coalesced by the writer's batch factor.
        proj_db_iops = (
            peak_msg_rate * scale_spec.db_write_fraction) / scale_spec.ticks_per_write_io

        # Utilization percentages, compared unrounded: rounding an 80.04% projection to
        # 80.0% would let it pass an 80.0% ceiling it actually breaches.
        ram_util = (proj_ram_gb / hardware_spec.available_ram_gb) * 100.0
        net_util = (proj_net_mbps / hardware_spec.max_network_mbps) * 100.0
        iops_util = (proj_db_iops / hardware_spec.max_db_iops) * 100.0

        ceiling = self.max_safe_utilization_pct
        is_ram_ok = ram_util <= ceiling
        is_net_ok = net_util <= ceiling
        is_iops_ok = iops_util <= ceiling

        breaches: List[str] = []
        if not is_ram_ok:
            breaches.append("ram")
        if not is_net_ok:
            breaches.append("network")
        if not is_iops_ok:
            breaches.append("db_iops")

        if breaches:
            breach_detail = {
                "ram": (
                    f"RAM {proj_ram_gb:,.1f} GB ({ram_util:.2f}% of "
                    f"{hardware_spec.available_ram_gb:,.0f} GB)"),
                "network": (
                    f"Network {proj_net_mbps:,.1f} Mbps ({net_util:.2f}% of "
                    f"{hardware_spec.max_network_mbps:,.0f} Mbps)"),
                "db_iops": (
                    f"DB IOPS {proj_db_iops:,.0f} ({iops_util:.2f}% of "
                    f"{hardware_spec.max_db_iops:,.0f} IOPS)"),
            }
            # Priority order fixes which single status is reported; the full breach list
            # travels on the report so a caller can size every bottleneck in one pass.
            status = {
                "ram": STATUS_FAILED_MEMORY,
                "network": STATUS_FAILED_NETWORK,
                "db_iops": STATUS_FAILED_IOPS,
            }[breaches[0]]
            notes = (
                f"LOAD TEST FAILED [{scale_spec.universe_name}]: scaling {n_current:,} -> "
                f"{n_target:,} symbols ({scale_factor:,.1f}x) breaches the {ceiling:.1f}% "
                f"utilization ceiling on {len(breaches)} resource(s): "
                + "; ".join(breach_detail[r] for r in breaches) + "."
            )
            logger.error(notes)
        else:
            status = STATUS_PASSED
            notes = (
                f"LOAD TEST PASSED [{scale_spec.universe_name}]: scaling {n_current:,} -> "
                f"{n_target:,} symbols ({scale_factor:,.1f}x). Peak msg rate = "
                f"{peak_msg_rate:,.0f} msg/sec. RAM = {proj_ram_gb:,.1f} GB "
                f"({ram_util:.2f}%), Network = {proj_net_mbps:,.1f} Mbps "
                f"({net_util:.2f}%), DB IOPS = {proj_db_iops:,.0f} ({iops_util:.2f}%). "
                f"Projection only - confirm with a replay at {peak_msg_rate:,.0f} "
                "msg/sec before scaling."
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
            audit_notes=notes,
            universe_scale_factor=scale_factor,
            breached_resources=breaches,
        )

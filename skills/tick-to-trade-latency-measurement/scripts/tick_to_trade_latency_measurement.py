import logging
import math
from dataclasses import dataclass, field
from typing import Dict, Optional, List, Tuple
from enum import Enum

logger = logging.getLogger(__name__)


class LatencyStage(Enum):
    NIC_INGRESS = "NIC_INGRESS"            # Network HW packet timestamp to socket read (t0 -> t1)
    DECODER_PARSING = "DECODER_PARSING"    # Protocol decoding & quote book update (t1 -> t2)
    STRATEGY_EVALUATION = "STRATEGY_EVALUATION" # Signal calculation & risk check (t2 -> t3)
    ORDER_SERIALIZATION = "ORDER_SERIALIZATION" # Order message encoding (FIX/OUCH/BOE) (t3 -> t4)
    NIC_EGRESS = "NIC_EGRESS"              # Socket write to physical wire transmission (t4 -> t5)


class LatencyError(Exception):
    """Base exception for latency measurement errors."""
    pass


@dataclass
class LatencySample:
    sample_id: str
    symbol: str
    ingress_ns: int       # t0: Packet arrival at NIC
    decoded_ns: int       # t1: Feed handler decoded message
    strategy_ns: int      # t2: Strategy signal computed
    serialized_ns: int    # t3: Order message formatted
    egress_ns: int        # t4: Order packet transmitted to NIC wire

    def validate(self) -> None:
        """Validates strictly monotonic timestamp progression."""
        if not (self.ingress_ns <= self.decoded_ns <= self.strategy_ns <= self.serialized_ns <= self.egress_ns):
            raise LatencyError(
                f"Non-monotonic timestamps in sample {self.sample_id}: "
                f"Ingress={self.ingress_ns}, Decoded={self.decoded_ns}, "
                f"Strategy={self.strategy_ns}, Serialized={self.serialized_ns}, Egress={self.egress_ns}"
            )

    @property
    def total_t2t_ns(self) -> int:
        """Total Tick-to-Trade latency in nanoseconds (t4 - t0)."""
        return self.egress_ns - self.ingress_ns

    @property
    def total_t2t_us(self) -> float:
        """Total Tick-to-Trade latency in microseconds."""
        return self.total_t2t_ns / 1_000.0


@dataclass
class StageBreakdown:
    stage: LatencyStage
    avg_us: float
    p50_us: float
    p90_us: float
    p99_us: float
    p999_us: float
    max_us: float
    std_dev_us: float
    percentage_of_total: float


@dataclass
class SLAConfig:
    max_p50_us: float = 5.0      # SLA target for Median latency (5.0 µs)
    max_p99_us: float = 15.0     # SLA target for 99th percentile (15.0 µs)
    max_p999_us: float = 50.0    # SLA target for 99.9th percentile (50.0 µs)
    max_tail_us: float = 100.0   # SLA target for Max tail spike (100.0 µs)


@dataclass
class LatencySummary:
    total_samples: int
    t2t_avg_us: float
    t2t_p50_us: float
    t2t_p90_us: float
    t2t_p99_us: float
    t2t_p999_us: float
    t2t_max_us: float
    t2t_std_dev_us: float
    stage_breakdowns: Dict[LatencyStage, StageBreakdown]
    sla_breaches: List[str] = field(default_factory=list)


class TickToTradeLatencyEngine:
    """
    Institutional Tick-to-Trade (T2T) Latency Profiling Engine.
    
    Measures microsecond/nanosecond latency across each pipeline stage of an HFT system:
    1. NIC Ingress (Hardware -> Driver)
    2. Protocol Decoding (Feed Handler)
    3. Strategy & Alpha Engine Evaluation
    4. Order Message Serialization
    5. NIC Egress (Wire Transmission)
    
    Calculates statistical percentiles (P50, P90, P99, P99.9, Max), detects SLA breaches,
    and pinpoints latency bottlenecks.
    """

    def __init__(self):
        self.samples: List[LatencySample] = []
        logger.info("Initialized Tick-to-Trade Latency Profiling Engine")

    def record_sample(self, sample: LatencySample) -> None:
        """Records a timestamped latency sample for analysis."""
        sample.validate()
        self.samples.append(sample)

    @staticmethod
    def calculate_percentile(values: List[float], p: float) -> float:
        """Calculates exact percentile p (0-100) using linear interpolation."""
        if not values:
            return 0.0
        sorted_vals = sorted(values)
        k = (len(sorted_vals) - 1) * (p / 100.0)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return sorted_vals[int(k)]
        d0 = sorted_vals[int(f)] * (c - k)
        d1 = sorted_vals[int(c)] * (k - f)
        return d0 + d1

    @staticmethod
    def calculate_std_dev(values: List[float], mean: float) -> float:
        """Calculates standard deviation (jitter) of values."""
        if len(values) < 2:
            return 0.0
        variance = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
        return math.sqrt(variance)

    def evaluate_latency_distribution(
        self, sla_config: Optional[SLAConfig] = None
    ) -> LatencySummary:
        """
        Computes statistical summary and per-stage latency breakdown across all recorded samples.
        """
        if not self.samples:
            raise LatencyError("Cannot evaluate latency distribution on empty sample set.")

        n = len(self.samples)
        total_t2t_us = [s.total_t2t_us for s in self.samples]

        t2t_avg = sum(total_t2t_us) / n
        t2t_p50 = self.calculate_percentile(total_t2t_us, 50.0)
        t2t_p90 = self.calculate_percentile(total_t2t_us, 90.0)
        t2t_p99 = self.calculate_percentile(total_t2t_us, 99.0)
        t2t_p999 = self.calculate_percentile(total_t2t_us, 99.9)
        t2t_max = max(total_t2t_us)
        t2t_std = self.calculate_std_dev(total_t2t_us, t2t_avg)

        # Compute per-stage deltas in µs
        stage_deltas: Dict[LatencyStage, List[float]] = {
            LatencyStage.NIC_INGRESS: [(s.decoded_ns - s.ingress_ns) / 1_000.0 for s in self.samples],
            LatencyStage.DECODER_PARSING: [(s.strategy_ns - s.decoded_ns) / 1_000.0 for s in self.samples],
            LatencyStage.STRATEGY_EVALUATION: [(s.serialized_ns - s.strategy_ns) / 1_000.0 for s in self.samples],
            LatencyStage.ORDER_SERIALIZATION: [(s.egress_ns - s.serialized_ns) / 1_000.0 for s in self.samples],
        }

        stage_breakdowns = {}
        for stage, deltas in stage_deltas.items():
            stg_avg = sum(deltas) / n
            stg_p50 = self.calculate_percentile(deltas, 50.0)
            stg_p90 = self.calculate_percentile(deltas, 90.0)
            stg_p99 = self.calculate_percentile(deltas, 99.0)
            stg_p999 = self.calculate_percentile(deltas, 99.9)
            stg_max = max(deltas)
            stg_std = self.calculate_std_dev(deltas, stg_avg)
            pct_total = (stg_avg / t2t_avg * 100.0) if t2t_avg > 0 else 0.0

            stage_breakdowns[stage] = StageBreakdown(
                stage=stage,
                avg_us=stg_avg,
                p50_us=stg_p50,
                p90_us=stg_p90,
                p99_us=stg_p99,
                p999_us=stg_p999,
                max_us=stg_max,
                std_dev_us=stg_std,
                percentage_of_total=pct_total,
            )

        # SLA breach detection
        sla_breaches = []
        if sla_config:
            if t2t_p50 > sla_config.max_p50_us:
                sla_breaches.append(f"P50 SLA Breach: {t2t_p50:.2f} µs > limit {sla_config.max_p50_us:.2f} µs")
            if t2t_p99 > sla_config.max_p99_us:
                sla_breaches.append(f"P99 SLA Breach: {t2t_p99:.2f} µs > limit {sla_config.max_p99_us:.2f} µs")
            if t2t_p999 > sla_config.max_p999_us:
                sla_breaches.append(f"P99.9 SLA Breach: {t2t_p999:.2f} µs > limit {sla_config.max_p999_us:.2f} µs")
            if t2t_max > sla_config.max_tail_us:
                sla_breaches.append(f"Max Tail SLA Breach: {t2t_max:.2f} µs > limit {sla_config.max_tail_us:.2f} µs")

        logger.info(
            f"Latency Evaluation completed ({n} samples): P50={t2t_p50:.2f}µs, P99={t2t_p99:.2f}µs, Max={t2t_max:.2f}µs"
        )

        return LatencySummary(
            total_samples=n,
            t2t_avg_us=t2t_avg,
            t2t_p50_us=t2t_p50,
            t2t_p90_us=t2t_p90,
            t2t_p99_us=t2t_p99,
            t2t_p999_us=t2t_p999,
            t2t_max_us=t2t_max,
            t2t_std_dev_us=t2t_std,
            stage_breakdowns=stage_breakdowns,
            sla_breaches=sla_breaches,
        )


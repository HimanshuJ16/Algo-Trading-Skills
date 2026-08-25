"""
Hardware (NIC MAC/PHY) vs software (kernel / user-space) timestamp accuracy analysis
with a MiFID II RTS 25 business-clock audit.

Two quantities are deliberately kept separate throughout this module, because
conflating them is the defect this engine exists to prevent:

  * **Clock divergence from UTC** - how far a clock reads from the traceable UTC
    time of the same instant. This is the quantity Commission Delegated
    Regulation (EU) 2017/574 (RTS 25) Arts. 2-3 and the Annex tables bound.
  * **Capture-path delay** - the elapsed time between the packet hitting the MAC
    and a later layer observing it. This is a real latency, not a clock error.

A user-space timestamp taken 120 us after the packet arrived is not "120 us of
clock drift"; the clock may be perfectly disciplined. It matters only because
whichever layer's value is *recorded* as the reportable event time carries both
errors, which is why RTS 25 Art. 4 requires the point at which a timestamp is
applied to be documented. The engine therefore asks the caller to declare that
point (``recorded_timestamp_source``) and audits the recorded value against the
applicable Annex limit, while still reporting each layer separately.

Figures in ``RTS25_ACCURACY_REQUIREMENTS`` are transcribed from the RTS 25 Annex
(see ``references/standards.md`` for sources). RTS 25 is technology-neutral: it
names no protocol and nowhere requires nanosecond granularity.
"""
import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

NS_PER_MICROSECOND = 1_000
NS_PER_MILLISECOND = 1_000_000
NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True)
class Rts25AccuracyRequirement:
    """One row of the RTS 25 Annex: a divergence bound and a granularity bound."""

    activity: str
    max_divergence_nanos: int
    granularity_nanos: int
    legal_basis: str


# RTS 25 Annex Table 2 (Art. 3 - members/participants of a trading venue) and
# Table 1 (Art. 2 - trading venue operators, keyed by gateway-to-gateway
# latency). Divergence and granularity are CO-EQUAL obligations: a clock held
# within 100 us of UTC still fails if the recorded timestamp only resolves
# milliseconds. 100 us is NOT a universal figure - it applies to the
# high-frequency algorithmic trading technique and to venues whose
# gateway-to-gateway latency is 1 ms or below; everything else is coarser.
RTS25_ACCURACY_REQUIREMENTS: Dict[str, Rts25AccuracyRequirement] = {
    "HIGH_FREQUENCY_ALGORITHMIC_TRADING": Rts25AccuracyRequirement(
        "HIGH_FREQUENCY_ALGORITHMIC_TRADING",
        100 * NS_PER_MICROSECOND,
        1 * NS_PER_MICROSECOND,
        "Reg. (EU) 2017/574 Art. 3 + Annex Table 2",
    ),
    "OTHER_TRADING_ACTIVITY": Rts25AccuracyRequirement(
        "OTHER_TRADING_ACTIVITY",
        1 * NS_PER_MILLISECOND,
        1 * NS_PER_MILLISECOND,
        "Reg. (EU) 2017/574 Art. 3 + Annex Table 2",
    ),
    "VOICE_TRADING": Rts25AccuracyRequirement(
        "VOICE_TRADING",
        1 * NS_PER_SECOND,
        1 * NS_PER_SECOND,
        "Reg. (EU) 2017/574 Art. 3 + Annex Table 2",
    ),
    "RFQ_WITH_HUMAN_INTERVENTION": Rts25AccuracyRequirement(
        "RFQ_WITH_HUMAN_INTERVENTION",
        1 * NS_PER_SECOND,
        1 * NS_PER_SECOND,
        "Reg. (EU) 2017/574 Art. 3 + Annex Table 2",
    ),
    "NEGOTIATED_TRANSACTION": Rts25AccuracyRequirement(
        "NEGOTIATED_TRANSACTION",
        1 * NS_PER_SECOND,
        1 * NS_PER_SECOND,
        "Reg. (EU) 2017/574 Art. 3 + Annex Table 2",
    ),
    "VENUE_GATEWAY_LATENCY_1MS_OR_BELOW": Rts25AccuracyRequirement(
        "VENUE_GATEWAY_LATENCY_1MS_OR_BELOW",
        100 * NS_PER_MICROSECOND,
        1 * NS_PER_MICROSECOND,
        "Reg. (EU) 2017/574 Art. 2 + Annex Table 1",
    ),
    "VENUE_GATEWAY_LATENCY_ABOVE_1MS": Rts25AccuracyRequirement(
        "VENUE_GATEWAY_LATENCY_ABOVE_1MS",
        1 * NS_PER_MILLISECOND,
        1 * NS_PER_MILLISECOND,
        "Reg. (EU) 2017/574 Art. 2 + Annex Table 1",
    ),
}

# The layer whose timestamp is written into the reportable record. RTS 25 Art. 4
# requires this point to be documented, so the engine requires it to be declared.
TIMESTAMPING_POINTS: Tuple[str, ...] = ("HARDWARE_MAC", "KERNEL_STACK", "APPLICATION")

RTS25_COMPLIANT = "COMPLIANT"
RTS25_NON_COMPLIANT_DIVERGENCE = "NON_COMPLIANT_DIVERGENCE"
RTS25_NON_COMPLIANT_GRANULARITY = "NON_COMPLIANT_GRANULARITY"
RTS25_NON_COMPLIANT_DIVERGENCE_AND_GRANULARITY = "NON_COMPLIANT_DIVERGENCE_AND_GRANULARITY"


def _validate_nanos(field_name: str, value: int) -> int:
    """
    Reject anything that is not a plain ``int``.

    ``bool`` is excluded explicitly because it subclasses ``int``, and ``float``
    because IEEE 754 binary64 carries a 53-bit significand: at an epoch magnitude
    of ~1.7e18 ns the representable spacing is 256 ns, so a float nanosecond
    timestamp has already lost more precision than the measurement is trying to
    resolve.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{field_name} must be an int of nanoseconds, got {type(value).__name__}. "
            "float cannot represent a nanosecond epoch exactly."
        )
    if value < 0:
        raise ValueError(f"{field_name} must be non-negative, got {value}")
    return value


def percentile_nearest_rank(sorted_values: Sequence[int], percentile: float) -> int:
    """
    Nearest-rank percentile over an already-sorted sequence.

    Nearest rank is used rather than an interpolating definition so that every
    reported figure is a value that was actually observed - an audit artifact
    should not contain a latency no packet ever exhibited.
    """
    if not sorted_values:
        raise ValueError("percentile of an empty sequence is undefined")
    if not 0 < percentile <= 100:
        raise ValueError(f"percentile must be in (0, 100], got {percentile}")
    rank = math.ceil(percentile / 100.0 * len(sorted_values))
    return sorted_values[min(max(rank, 1), len(sorted_values)) - 1]


@dataclass(frozen=True)
class PacketTimestampSample:
    """
    One packet observed at three timestamping points plus a traceable UTC reference.

    All four values must describe the SAME packet arrival and must be expressed
    on a COMMON timebase. That is not automatic on Linux: the kernel does not
    convert NIC hardware timestamps to system time, and the PTP hardware clock
    (PHC) on the adapter is an independent clock. Unless ``phc2sys``/``sfptpd``
    is disciplining the system clock to the PHC (or vice versa), subtracting a
    kernel timestamp from a hardware timestamp measures the offset between two
    clocks, not the kernel stack delay. The engine rejects samples where a later
    layer precedes an earlier one, because that is the observable signature of
    exactly this misconfiguration.

    ``utc_reference_nanos`` is the traceable UTC time of the packet arrival at
    the capture point - e.g. from a GNSS-disciplined capture appliance on a tap,
    or the hardware timestamp corrected by the PHC-to-UTC offset the PTP daemon
    reports. It is NOT a second reading of the host's own clock; comparing a
    clock against itself measures nothing.

    ``timestamp_granularity_nanos`` is the smallest unit the RECORDED timestamp
    actually resolves (1_000 for a microsecond field). RTS 25 bounds this
    independently of divergence.
    """

    packet_id: str
    hardware_mac_nanos: int             # MAC/PHY capture on the NIC (PHC timebase)
    kernel_stack_nanos: int             # SO_TIMESTAMPING software receive timestamp
    application_layer_nanos: int        # user-space clock_gettime() on the read path
    utc_reference_nanos: int            # traceable UTC time of the same arrival
    timestamp_granularity_nanos: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.packet_id, str) or not self.packet_id.strip():
            raise ValueError("packet_id must be a non-empty string")
        _validate_nanos("hardware_mac_nanos", self.hardware_mac_nanos)
        _validate_nanos("kernel_stack_nanos", self.kernel_stack_nanos)
        _validate_nanos("application_layer_nanos", self.application_layer_nanos)
        _validate_nanos("utc_reference_nanos", self.utc_reference_nanos)
        _validate_nanos("timestamp_granularity_nanos", self.timestamp_granularity_nanos)
        if self.timestamp_granularity_nanos < 1:
            raise ValueError("timestamp_granularity_nanos must be >= 1")


@dataclass
class TimestampAccuracyAuditReport:
    """Per-packet decomposition and RTS 25 verdict. All figures in nanoseconds."""

    packet_id: str
    # Capture-path delays (always >= 0 on a common timebase).
    kernel_capture_delay_nanos: int
    application_capture_delay_nanos: int
    software_capture_delay_nanos: int
    # Clock/timestamp errors vs traceable UTC. SIGNED: a positive value means the
    # timestamp reads LATER than true UTC, a negative value means it reads EARLIER
    # (a clock ahead of UTC stamps events before they happened). abs() would hide
    # the direction, which is the part that identifies the faulty component.
    hardware_clock_divergence_nanos: int
    kernel_timestamp_error_nanos: int
    application_timestamp_error_nanos: int
    # The layer actually written into the reportable record, and its error.
    recorded_timestamp_source: str
    recorded_timestamp_error_nanos: int
    # Applicable RTS 25 row.
    trading_activity: str
    max_divergence_nanos: int
    required_granularity_nanos: int
    timestamp_granularity_nanos: int
    # Verdicts.
    hardware_within_divergence_limit: bool
    application_within_divergence_limit: bool
    recorded_within_divergence_limit: bool
    granularity_sufficient: bool
    rts25_verdict: str
    status: str                         # hardware-vs-application comparison
    audit_notes: str


@dataclass
class TimestampBenchmarkSummary:
    """
    Distributional summary over a batch.

    Single-sample figures cannot establish compliance or characterise jitter:
    divergence and capture delay are distributions, and one packet says nothing
    about the tail. ``software_capture_delay_jitter_peak_to_peak_nanos`` is
    defined here as ``max - min`` of the capture-delay series; the p50/p99 fields
    are reported alongside it because peak-to-peak is dominated by single outliers.
    """

    sample_count: int
    trading_activity: str
    recorded_timestamp_source: str
    max_divergence_nanos: int
    required_granularity_nanos: int
    hardware_divergence_abs_p50_nanos: int
    hardware_divergence_abs_p99_nanos: int
    hardware_divergence_abs_max_nanos: int
    recorded_error_abs_p50_nanos: int
    recorded_error_abs_p99_nanos: int
    recorded_error_abs_max_nanos: int
    kernel_capture_delay_p50_nanos: int
    kernel_capture_delay_p99_nanos: int
    software_capture_delay_p50_nanos: int
    software_capture_delay_p99_nanos: int
    software_capture_delay_min_nanos: int
    software_capture_delay_max_nanos: int
    software_capture_delay_jitter_peak_to_peak_nanos: int
    divergence_breach_count: int
    granularity_breach_count: int
    rts25_compliant_sample_count: int
    worst_recorded_error_packet_id: str
    reports: List[TimestampAccuracyAuditReport] = field(default_factory=list)


class TimestampAccuracyAnalyzerEngine:
    """
    Decomposes hardware/kernel/application timestamps for the same packet and
    audits the recorded timestamp against the applicable MiFID II RTS 25 Annex row.

    The engine measures timestamps. It does not by itself demonstrate RTS 25
    compliance: Art. 4 requires a documented traceability chain to UTC, a stated
    timestamping point, and an annual review. A passing verdict here is evidence
    for that file, not a substitute for it.
    """

    def __init__(
        self,
        trading_activity: str = "HIGH_FREQUENCY_ALGORITHMIC_TRADING",
        recorded_timestamp_source: str = "HARDWARE_MAC",
        max_divergence_nanos: Optional[int] = None,
        required_granularity_nanos: Optional[int] = None,
    ) -> None:
        """
        :param trading_activity: key into ``RTS25_ACCURACY_REQUIREMENTS`` selecting
            the Annex row that applies to the entity and activity being audited.
        :param recorded_timestamp_source: which layer's value is written into the
            reportable record (``HARDWARE_MAC``, ``KERNEL_STACK``, ``APPLICATION``).
        :param max_divergence_nanos: optional override of the Annex divergence
            bound - accepted only if STRICTER than the obligation, so an internal
            target can be tightened but a regulatory limit can never be relaxed.
        :param required_granularity_nanos: optional override of the Annex
            granularity bound, same one-way constraint.
        """
        if trading_activity not in RTS25_ACCURACY_REQUIREMENTS:
            raise ValueError(
                f"unknown trading_activity {trading_activity!r}; "
                f"expected one of {sorted(RTS25_ACCURACY_REQUIREMENTS)}"
            )
        if recorded_timestamp_source not in TIMESTAMPING_POINTS:
            raise ValueError(
                f"unknown recorded_timestamp_source {recorded_timestamp_source!r}; "
                f"expected one of {list(TIMESTAMPING_POINTS)}"
            )

        requirement = RTS25_ACCURACY_REQUIREMENTS[trading_activity]
        self.trading_activity = trading_activity
        self.requirement = requirement
        self.recorded_timestamp_source = recorded_timestamp_source

        if max_divergence_nanos is None:
            self.max_divergence_nanos = requirement.max_divergence_nanos
        else:
            _validate_nanos("max_divergence_nanos", max_divergence_nanos)
            if max_divergence_nanos < 1:
                raise ValueError("max_divergence_nanos must be >= 1")
            if max_divergence_nanos > requirement.max_divergence_nanos:
                raise ValueError(
                    f"max_divergence_nanos {max_divergence_nanos} is looser than the "
                    f"{requirement.max_divergence_nanos} ns bound of "
                    f"{requirement.legal_basis}; an override may only tighten it"
                )
            self.max_divergence_nanos = max_divergence_nanos

        if required_granularity_nanos is None:
            self.required_granularity_nanos = requirement.granularity_nanos
        else:
            _validate_nanos("required_granularity_nanos", required_granularity_nanos)
            if required_granularity_nanos < 1:
                raise ValueError("required_granularity_nanos must be >= 1")
            if required_granularity_nanos > requirement.granularity_nanos:
                raise ValueError(
                    f"required_granularity_nanos {required_granularity_nanos} is coarser "
                    f"than the {requirement.granularity_nanos} ns bound of "
                    f"{requirement.legal_basis}; an override may only tighten it"
                )
            self.required_granularity_nanos = required_granularity_nanos

    def analyze_sample(
        self, sample: PacketTimestampSample, log_result: bool = True
    ) -> TimestampAccuracyAuditReport:
        """
        Decompose one packet's timestamps and audit the recorded layer.

        :param log_result: emit the per-packet audit line. ``analyze_batch``
            disables it and logs one summary instead - a clock failure across a
            large capture would otherwise emit one CRITICAL line per packet.

        :raises ValueError: if a later capture point precedes an earlier one,
            which means the layers are not on a common timebase (typically an
            undisciplined NIC PHC) and no delay computed from them is meaningful.
        """
        if not isinstance(sample, PacketTimestampSample):
            raise TypeError(f"expected PacketTimestampSample, got {type(sample).__name__}")

        kernel_delay = sample.kernel_stack_nanos - sample.hardware_mac_nanos
        app_delay = sample.application_layer_nanos - sample.kernel_stack_nanos
        software_delay = sample.application_layer_nanos - sample.hardware_mac_nanos

        if kernel_delay < 0 or app_delay < 0:
            raise ValueError(
                f"[{sample.packet_id}] capture points are out of order "
                f"(kernel-hardware={kernel_delay} ns, application-kernel={app_delay} ns). "
                "A packet cannot be seen by a later layer before it reached an earlier one. "
                "The usual cause is that the NIC PTP hardware clock and the system clock "
                "are not disciplined to a common timebase (the kernel does not convert "
                "hardware timestamps to system time) - check phc2sys/sfptpd before "
                "interpreting any latency computed from these values."
            )

        hardware_divergence = sample.hardware_mac_nanos - sample.utc_reference_nanos
        kernel_error = sample.kernel_stack_nanos - sample.utc_reference_nanos
        application_error = sample.application_layer_nanos - sample.utc_reference_nanos

        errors_by_layer = {
            "HARDWARE_MAC": hardware_divergence,
            "KERNEL_STACK": kernel_error,
            "APPLICATION": application_error,
        }
        recorded_error = errors_by_layer[self.recorded_timestamp_source]

        hardware_ok = abs(hardware_divergence) <= self.max_divergence_nanos
        application_ok = abs(application_error) <= self.max_divergence_nanos
        recorded_ok = abs(recorded_error) <= self.max_divergence_nanos
        granularity_ok = sample.timestamp_granularity_nanos <= self.required_granularity_nanos

        if recorded_ok and granularity_ok:
            verdict = RTS25_COMPLIANT
        elif not recorded_ok and not granularity_ok:
            verdict = RTS25_NON_COMPLIANT_DIVERGENCE_AND_GRANULARITY
        elif not recorded_ok:
            verdict = RTS25_NON_COMPLIANT_DIVERGENCE
        else:
            verdict = RTS25_NON_COMPLIANT_GRANULARITY

        # Four distinct states: the hardware clock can fail while the application
        # timestamp happens to land inside the limit (a clock running behind UTC
        # plus the capture delay can cancel), so the two verdicts are independent.
        if hardware_ok and application_ok:
            status = "BOTH_WITHIN_DIVERGENCE_LIMIT"
        elif hardware_ok and not application_ok:
            status = "HARDWARE_WITHIN_LIMIT_APPLICATION_EXCEEDS"
        elif not hardware_ok and application_ok:
            status = "HARDWARE_EXCEEDS_APPLICATION_WITHIN_LIMIT"
        else:
            status = "BOTH_EXCEED_DIVERGENCE_LIMIT"

        notes = (
            f"TIMESTAMP AUDIT [{sample.packet_id}] activity={self.trading_activity} "
            f"recorded_at={self.recorded_timestamp_source} verdict={verdict}. "
            f"Hardware clock divergence from UTC = {hardware_divergence / 1000.0:+.3f} us "
            f"(limit +/-{self.max_divergence_nanos / 1000.0:.3f} us). "
            f"Capture-path delay: kernel {kernel_delay / 1000.0:.3f} us, "
            f"application {app_delay / 1000.0:.3f} us, total {software_delay / 1000.0:.3f} us "
            f"(elapsed time, not clock error). "
            f"Recorded timestamp error = {recorded_error / 1000.0:+.3f} us. "
            f"Granularity {sample.timestamp_granularity_nanos} ns vs required "
            f"{self.required_granularity_nanos} ns."
        )

        if log_result:
            if verdict == RTS25_COMPLIANT:
                logger.info(notes)
            elif not hardware_ok:
                # The clock itself is out of tolerance - no choice of timestamping
                # point rescues this, so it is the more serious of the two failures.
                logger.critical(notes)
            else:
                logger.warning(notes)

        return TimestampAccuracyAuditReport(
            packet_id=sample.packet_id,
            kernel_capture_delay_nanos=kernel_delay,
            application_capture_delay_nanos=app_delay,
            software_capture_delay_nanos=software_delay,
            hardware_clock_divergence_nanos=hardware_divergence,
            kernel_timestamp_error_nanos=kernel_error,
            application_timestamp_error_nanos=application_error,
            recorded_timestamp_source=self.recorded_timestamp_source,
            recorded_timestamp_error_nanos=recorded_error,
            trading_activity=self.trading_activity,
            max_divergence_nanos=self.max_divergence_nanos,
            required_granularity_nanos=self.required_granularity_nanos,
            timestamp_granularity_nanos=sample.timestamp_granularity_nanos,
            hardware_within_divergence_limit=hardware_ok,
            application_within_divergence_limit=application_ok,
            recorded_within_divergence_limit=recorded_ok,
            granularity_sufficient=granularity_ok,
            rts25_verdict=verdict,
            status=status,
            audit_notes=notes,
        )

    def analyze_batch(
        self, samples: Sequence[PacketTimestampSample]
    ) -> TimestampBenchmarkSummary:
        """
        Analyze a packet capture and summarise the distributions.

        Compliance and jitter are properties of a distribution, not of one packet:
        a median well inside the limit says nothing about the p99, and RTS 25
        bounds the divergence at the instant EVERY timestamp is applied.
        """
        samples = list(samples)
        if not samples:
            raise ValueError("analyze_batch requires at least one sample")

        reports = [self.analyze_sample(s, log_result=False) for s in samples]

        divergence_abs = sorted(abs(r.hardware_clock_divergence_nanos) for r in reports)
        recorded_abs = sorted(abs(r.recorded_timestamp_error_nanos) for r in reports)
        kernel_delays = sorted(r.kernel_capture_delay_nanos for r in reports)
        software_delays = sorted(r.software_capture_delay_nanos for r in reports)

        worst = max(reports, key=lambda r: abs(r.recorded_timestamp_error_nanos))

        summary = TimestampBenchmarkSummary(
            sample_count=len(reports),
            trading_activity=self.trading_activity,
            recorded_timestamp_source=self.recorded_timestamp_source,
            max_divergence_nanos=self.max_divergence_nanos,
            required_granularity_nanos=self.required_granularity_nanos,
            hardware_divergence_abs_p50_nanos=percentile_nearest_rank(divergence_abs, 50),
            hardware_divergence_abs_p99_nanos=percentile_nearest_rank(divergence_abs, 99),
            hardware_divergence_abs_max_nanos=divergence_abs[-1],
            recorded_error_abs_p50_nanos=percentile_nearest_rank(recorded_abs, 50),
            recorded_error_abs_p99_nanos=percentile_nearest_rank(recorded_abs, 99),
            recorded_error_abs_max_nanos=recorded_abs[-1],
            kernel_capture_delay_p50_nanos=percentile_nearest_rank(kernel_delays, 50),
            kernel_capture_delay_p99_nanos=percentile_nearest_rank(kernel_delays, 99),
            software_capture_delay_p50_nanos=percentile_nearest_rank(software_delays, 50),
            software_capture_delay_p99_nanos=percentile_nearest_rank(software_delays, 99),
            software_capture_delay_min_nanos=software_delays[0],
            software_capture_delay_max_nanos=software_delays[-1],
            software_capture_delay_jitter_peak_to_peak_nanos=(
                software_delays[-1] - software_delays[0]
            ),
            divergence_breach_count=sum(
                1 for r in reports if not r.recorded_within_divergence_limit
            ),
            granularity_breach_count=sum(1 for r in reports if not r.granularity_sufficient),
            rts25_compliant_sample_count=sum(
                1 for r in reports if r.rts25_verdict == RTS25_COMPLIANT
            ),
            worst_recorded_error_packet_id=worst.packet_id,
            reports=reports,
        )

        if summary.rts25_compliant_sample_count < summary.sample_count:
            logger.warning(
                "TIMESTAMP BENCHMARK: %d/%d samples fail RTS 25 (%s). "
                "Worst recorded timestamp error %+d ns on packet %s.",
                summary.sample_count - summary.rts25_compliant_sample_count,
                summary.sample_count,
                self.requirement.legal_basis,
                worst.recorded_timestamp_error_nanos,
                worst.packet_id,
            )
        return summary

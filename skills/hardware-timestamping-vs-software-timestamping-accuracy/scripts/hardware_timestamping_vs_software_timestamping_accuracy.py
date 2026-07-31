import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

@dataclass
class PacketTimestampSample:
    packet_id: str
    hardware_mac_nanos: int             # MAC layer hardware timestamp
    kernel_stack_nanos: int             # Linux SO_TIMESTAMPING kernel timestamp
    application_layer_nanos: int        # User-space clock_gettime timestamp
    utc_reference_nanos: int            # PTP Grandmaster / GPS UTC reference

@dataclass
class TimestampAccuracyAuditReport:
    packet_id: str
    kernel_stack_latency_nanos: int
    application_jitter_nanos: int
    total_software_latency_distortion_nanos: int
    hardware_utc_drift_nanos: int
    software_utc_drift_nanos: int
    is_hardware_mifid_rts25_compliant: bool # <= 100,000 ns (100 us)
    is_software_mifid_rts25_compliant: bool
    status: str                         # 'HARDWARE_COMPLIANT_SOFTWARE_NON_COMPLIANT', 'BOTH_COMPLIANT', 'BOTH_NON_COMPLIANT'
    audit_notes: str

class TimestampAccuracyAnalyzerEngine:
    """
    Low-latency benchmarking engine for measuring Hardware NIC (Solarflare/Exablaze) vs Software OS Kernel
    timestamping jitter and auditing MiFID II RTS 25 compliance (< 100us UTC drift).
    """
    def __init__(
        self,
        mifid_rts25_max_drift_nanos: int = 100_000 # 100 microseconds max UTC divergence
    ):
        self.mifid_rts25_max_drift_nanos = mifid_rts25_max_drift_nanos

    def analyze_sample(
        self,
        sample: PacketTimestampSample
    ) -> TimestampAccuracyAuditReport:
        """
        Decomposes 3-layer timestamp latency and audits MiFID II RTS 25 compliance.
        """
        kernel_latency = sample.kernel_stack_nanos - sample.hardware_mac_nanos
        app_jitter = sample.application_layer_nanos - sample.kernel_stack_nanos
        total_sw_distortion = sample.application_layer_nanos - sample.hardware_mac_nanos

        hw_utc_drift = abs(sample.hardware_mac_nanos - sample.utc_reference_nanos)
        sw_utc_drift = abs(sample.application_layer_nanos - sample.utc_reference_nanos)

        hw_compliant = (hw_utc_drift <= self.mifid_rts25_max_drift_nanos)
        sw_compliant = (sw_utc_drift <= self.mifid_rts25_max_drift_nanos)

        if hw_compliant and not sw_compliant:
            status = "HARDWARE_COMPLIANT_SOFTWARE_NON_COMPLIANT"
            notes = (
                f"TIMESTAMP AUDIT [{sample.packet_id}]: Hardware NIC Timestamp is MiFID II RTS 25 COMPLIANT "
                f"(Drift = {hw_utc_drift/1000.0:.2f} µs <= 100 µs). Software App Timestamp FAILED compliance "
                f"(Drift = {sw_utc_drift/1000.0:.2f} µs > 100 µs due to {total_sw_distortion/1000.0:.2f} µs OS kernel jitter)."
            )
            logger.warning(notes)
        elif hw_compliant and sw_compliant:
            status = "BOTH_COMPLIANT"
            notes = f"TIMESTAMP AUDIT [{sample.packet_id}]: Both Hardware and Software timestamps meet RTS 25 standards."
            logger.info(notes)
        else:
            status = "BOTH_NON_COMPLIANT"
            notes = f"TIMESTAMP AUDIT [{sample.packet_id}]: PTP Clock Drift failure! Both hardware and software exceed 100 µs limit."
            logger.critical(notes)

        return TimestampAccuracyAuditReport(
            packet_id=sample.packet_id,
            kernel_stack_latency_nanos=kernel_latency,
            application_jitter_nanos=app_jitter,
            total_software_latency_distortion_nanos=total_sw_distortion,
            hardware_utc_drift_nanos=hw_utc_drift,
            software_utc_drift_nanos=sw_utc_drift,
            is_hardware_mifid_rts25_compliant=hw_compliant,
            is_software_mifid_rts25_compliant=sw_compliant,
            status=status,
            audit_notes=notes
        )

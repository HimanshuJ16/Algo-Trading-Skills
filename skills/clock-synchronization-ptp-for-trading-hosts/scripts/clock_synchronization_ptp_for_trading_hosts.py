import re
import logging
from dataclasses import dataclass
from typing import Optional, Dict

logger = logging.getLogger(__name__)

@dataclass
class PtpTelemetrySample:
    daemon: str          # 'ptp4l' or 'phc2sys'
    offset_ns: float     # Offset from master in nanoseconds
    freq_adj_ppb: float  # Frequency adjustment in parts-per-billion
    path_delay_ns: Optional[float] = None
    ptp_state: Optional[str] = None # e.g. 's0', 's1', 's2' (locked), 'SLAVE'

class PtpClockSyncManager:
    """
    Parses linuxptp (ptp4l & phc2sys) log output lines, tracks synchronization state,
    and checks against HFT and MiFID II compliance limits.
    """
    def __init__(self, max_allowed_offset_ns: float = 100000.0, target_hft_offset_ns: float = 1000.0):
        self.max_allowed_offset_ns = max_allowed_offset_ns  # 100us MiFID II RTS 25
        self.target_hft_offset_ns = target_hft_offset_ns      # 1us HFT target
        
        self.ptp4l_state = "UNKNOWN"
        self.phc2sys_state = "UNKNOWN"
        self.latest_ptp4l_offset: Optional[float] = None
        self.latest_phc2sys_offset: Optional[float] = None
        
        # Regex patterns for linuxptp log parsing
        self.ptp4l_pattern = re.compile(
            r"ptp4l\[\d+\.\d+\]:\s+master\s+offset\s+([+-]?\d+)\s+(s\d+|[A-Z_]+)\s+freq\s+([+-]?\d+)\s+path\s+delay\s+(\d+)"
        )
        
        self.phc2sys_pattern = re.compile(
            r"phc2sys\[\d+\.\d+\]:\s+[\w_]+\s+(?:phc\s+)?offset\s+([+-]?\d+)\s+(s\d+|[A-Z_]+)\s+freq\s+([+-]?\d+)"
        )

    def parse_log_line(self, line: str) -> Optional[PtpTelemetrySample]:
        """
        Parses a single log line from ptp4l or phc2sys.
        Updates internal status and returns a structured telemetry sample.
        """
        ptp4l_match = self.ptp4l_pattern.search(line)
        if ptp4l_match:
            offset = float(ptp4l_match.group(1))
            state = ptp4l_match.group(2)
            freq = float(ptp4l_match.group(3))
            delay = float(ptp4l_match.group(4))
            
            self.ptp4l_state = state
            self.latest_ptp4l_offset = offset
            
            return PtpTelemetrySample(
                daemon="ptp4l",
                offset_ns=offset,
                freq_adj_ppb=freq,
                path_delay_ns=delay,
                ptp_state=state
            )
            
        phc2sys_match = self.phc2sys_pattern.search(line)
        if phc2sys_match:
            offset = float(phc2sys_match.group(1))
            state = phc2sys_match.group(2)
            freq = float(phc2sys_match.group(3))
            
            self.phc2sys_state = state
            self.latest_phc2sys_offset = offset
            
            return PtpTelemetrySample(
                daemon="phc2sys",
                offset_ns=offset,
                freq_adj_ppb=freq,
                ptp_state=state
            )
            
        return None

    def evaluate_compliance(self) -> Dict[str, any]:
        """
        Evaluates current sync state against compliance and HFT thresholds.
        """
        is_synced = (self.ptp4l_state in ("s2", "SLAVE")) and (self.phc2sys_state in ("s2", "SLAVE"))
        
        max_offset = max(
            abs(self.latest_ptp4l_offset or 0.0),
            abs(self.latest_phc2sys_offset or 0.0)
        )
        
        mifid_compliant = is_synced and (max_offset <= self.max_allowed_offset_ns)
        hft_ready = is_synced and (max_offset <= self.target_hft_offset_ns)
        
        return {
            "is_synced": is_synced,
            "max_offset_ns": max_offset,
            "mifid_compliant": mifid_compliant,
            "hft_ready": hft_ready,
            "ptp4l_state": self.ptp4l_state,
            "phc2sys_state": self.phc2sys_state
        }

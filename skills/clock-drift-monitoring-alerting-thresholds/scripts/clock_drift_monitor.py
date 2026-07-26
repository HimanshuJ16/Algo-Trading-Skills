import logging
from enum import Enum
from typing import Callable

logger = logging.getLogger(__name__)

class PtpState(Enum):
    LOCKED = "LOCKED"
    HOLDOVER = "HOLDOVER"
    UNLOCKED = "UNLOCKED"

class MonitorStatus(Enum):
    HEALTHY = "HEALTHY"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"

class ClockDriftMonitor:
    """
    Monitors PTP offset and state, enforcing MiFID II RTS 25 HFT thresholds.
    Triggers automated callbacks (kill switches) on regulatory breach.
    """
    def __init__(self, kill_switch_callback: Callable, warning_threshold_us: int = 50, critical_threshold_us: int = 100):
        self.warning_threshold_us = warning_threshold_us
        self.critical_threshold_us = critical_threshold_us
        self.kill_switch_callback = kill_switch_callback
        
        self.current_status = MonitorStatus.HEALTHY
        self.trading_halted = False

    def process_telemetry(self, offset_us: float, ptp_state: PtpState) -> MonitorStatus:
        """
        Evaluates the current offset and state.
        offset_us: The clock drift in microseconds.
        Returns the resulting MonitorStatus.
        """
        if self.trading_halted:
            logger.warning("Telemetry ignored; trading is currently halted due to previous breach.")
            return MonitorStatus.CRITICAL
            
        abs_offset = abs(offset_us)
        
        # 1. State Evaluation
        if ptp_state == PtpState.UNLOCKED:
            logger.critical("PTP STATE UNLOCKED. Clock synchronization lost entirely.")
            self._trigger_kill_switch("PTP_UNLOCKED")
            return MonitorStatus.CRITICAL

        # 2. Threshold Evaluation
        if abs_offset >= self.critical_threshold_us:
            logger.critical(f"REGULATORY BREACH: Drift {abs_offset}µs exceeds limit of {self.critical_threshold_us}µs.")
            self._trigger_kill_switch(f"DRIFT_BREACH_{abs_offset}us")
            return MonitorStatus.CRITICAL
            
        elif abs_offset >= self.warning_threshold_us:
            if self.current_status != MonitorStatus.WARNING:
                logger.warning(f"Drift {abs_offset}µs exceeds warning threshold of {self.warning_threshold_us}µs.")
            self.current_status = MonitorStatus.WARNING
            return MonitorStatus.WARNING
            
        else:
            self.current_status = MonitorStatus.HEALTHY
            return MonitorStatus.HEALTHY

    def _trigger_kill_switch(self, reason: str):
        self.current_status = MonitorStatus.CRITICAL
        self.trading_halted = True
        logger.critical(f"Executing kill switch callback. Reason: {reason}")
        self.kill_switch_callback(reason)

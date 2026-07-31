import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class OutboundMessageRecord:
    session_id: str
    message_type: str                    # 'NEW_ORDER', 'CANCEL', 'MODIFY'
    timestamp_epoch: float               # Microsecond/second timestamp
    sequence_id: int

@dataclass
class InboundMessageRecord:
    session_id: str
    message_type: str                    # 'EXEC_REPORT', 'CANCEL_ACK', 'REJECT'
    sequence_id: int
    timestamp_epoch: float

@dataclass
class SequenceGapDetail:
    session_id: str
    expected_seq_id: int
    received_seq_id: int
    missing_seq_start: int
    missing_seq_end: int

@dataclass
class MatchingEngineAuditReport:
    session_id: str
    outbound_rate_per_sec: float
    max_allowed_mps: float
    is_throttled: bool
    has_sequence_gap: bool
    sequence_gap_details: Optional[SequenceGapDetail]
    status: str                         # 'MATCHING_ENGINE_NORMAL', 'THROTTLE_WARNING_SLOW_DOWN', 'EXCHANGE_RATE_LIMIT_THROTTLED', 'MESSAGE_SEQUENCE_GAP_DETECTED'
    audit_notes: str

class MatchingEngineMonitorEngine:
    """
    Exchange matching engine monitoring engine detecting outbound order rate throttling (CME iLink 3 / NASDAQ OUCH)
    and inbound sequence gapping to trigger automatic resend recovery.
    """
    def __init__(self, max_allowed_mps: float = 500.0, warning_threshold_pct: float = 80.0):
        self.max_allowed_mps = max_allowed_mps
        self.warning_threshold_pct = warning_threshold_pct
        self.session_expected_seq: Dict[str, int] = {}

    def audit_matching_engine_session(
        self,
        session_id: str,
        outbound_messages: List[OutboundMessageRecord],
        inbound_messages: List[InboundMessageRecord],
        window_seconds: float = 1.0
    ) -> MatchingEngineAuditReport:
        """
        Audits outbound message rate velocity and inbound sequence continuity for a matching engine session.
        """
        if not session_id:
            raise ValueError("Session ID cannot be empty.")

        # 1. Outbound Rate Limit Audit (sliding window velocity)
        now = time.time()
        active_outbound = [
            m for m in outbound_messages
            if (now - m.timestamp_epoch) <= window_seconds
        ]
        outbound_mps = len(active_outbound) / window_seconds

        warning_limit = (self.warning_threshold_pct / 100.0) * self.max_allowed_mps

        is_throttled = False
        if outbound_mps >= self.max_allowed_mps:
            is_throttled = True
            throttle_status = "EXCHANGE_RATE_LIMIT_THROTTLED"
            notes = (
                f"EXCHANGE THROTTLE BLOCK [{session_id}]: Message rate {outbound_mps:.0f} msgs/sec "
                f"reaches/exceeds max limit ({self.max_allowed_mps:.0f} msgs/sec)! Outbound orders blocked."
            )
            logger.critical(notes)
        elif outbound_mps >= warning_limit:
            throttle_status = "THROTTLE_WARNING_SLOW_DOWN"
            notes = (
                f"THROTTLE WARNING [{session_id}]: Message rate {outbound_mps:.0f} msgs/sec "
                f"nears limit ({self.warning_threshold_pct:.0f}% of {self.max_allowed_mps:.0f} msgs/sec)."
            )
            logger.warning(notes)
        else:
            throttle_status = "MATCHING_ENGINE_NORMAL"
            notes = f"MATCHING ENGINE OK [{session_id}]: Outbound rate = {outbound_mps:.0f} msgs/sec."

        # 2. Inbound Sequence Gap Audit
        gap_detail: Optional[SequenceGapDetail] = None
        has_gap = False

        if session_id not in self.session_expected_seq:
            self.session_expected_seq[session_id] = 1

        sorted_inbound = sorted(inbound_messages, key=lambda m: m.sequence_id)

        for m in sorted_inbound:
            expected = self.session_expected_seq[session_id]
            if m.sequence_id > expected:
                has_gap = True
                gap_detail = SequenceGapDetail(
                    session_id=session_id,
                    expected_seq_id=expected,
                    received_seq_id=m.sequence_id,
                    missing_seq_start=expected,
                    missing_seq_end=m.sequence_id - 1
                )
                self.session_expected_seq[session_id] = m.sequence_id + 1
                gap_notes = (
                    f"SEQUENCE GAP DETECTED [{session_id}]: Expected Seq {expected}, got {m.sequence_id}. "
                    f"Missing range [{expected}, {m.sequence_id - 1}]. Requesting Resend!"
                )
                logger.error(gap_notes)
                break
            elif m.sequence_id == expected:
                self.session_expected_seq[session_id] = expected + 1

        final_status = "MESSAGE_SEQUENCE_GAP_DETECTED" if has_gap else throttle_status
        if has_gap:
            final_notes = f"{notes} | SEQUENCE GAP DETECTED: Missing [{gap_detail.missing_seq_start}, {gap_detail.missing_seq_end}]."
        else:
            final_notes = notes

        return MatchingEngineAuditReport(
            session_id=session_id,
            outbound_rate_per_sec=outbound_mps,
            max_allowed_mps=self.max_allowed_mps,
            is_throttled=is_throttled,
            has_sequence_gap=has_gap,
            sequence_gap_details=gap_detail,
            status=final_status,
            audit_notes=final_notes
        )

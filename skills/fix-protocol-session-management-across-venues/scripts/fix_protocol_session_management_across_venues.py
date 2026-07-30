import time
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class FixMessage:
    msg_type: str                       # Tag 35 (A=Logon, 0=Heartbeat, 1=TestRequest, 2=ResendRequest, 4=SeqReset, 5=Logout, D=NewOrderSingle)
    msg_seq_num: int                    # Tag 34
    sender_comp_id: str                 # Tag 49
    target_comp_id: str                 # Tag 56
    sending_time_iso: str               # Tag 52
    body_fields: Dict[int, str] = field(default_factory=dict)
    poss_dup_flag: bool = False         # Tag 43

@dataclass
class FixSessionConfig:
    session_id: str
    sender_comp_id: str
    target_comp_id: str
    heartbeat_interval_sec: int = 30
    auto_resend_on_gap: bool = True

@dataclass
class FixSessionAuditReport:
    session_id: str
    state: str                          # 'DISCONNECTED', 'LOGON_SENT', 'LOGGED_IN', 'RESEND_REQUEST_SENT', 'LOGOUT_SENT'
    out_seq_num: int
    expected_in_seq_num: int
    gap_detected: bool
    last_sent_msg_type: Optional[str]
    last_recv_msg_type: Optional[str]
    status: str                         # 'SESSION_ACTIVE', 'RESEND_REQUEST_ISSUED', 'SESSION_TERMINATED'
    audit_notes: str

class FixProtocolSessionManagerEngine:
    """
    Quantitative execution engine for managing multi-venue FIX protocol session state machines,
    sequence number resynchronization, gap fills, heartbeats, and graceful failovers.
    """
    def __init__(self, config: FixSessionConfig):
        self.config = config
        self.state = "DISCONNECTED"
        self.out_seq_num = 1
        self.expected_in_seq_num = 1
        self.last_sent_time = 0.0
        self.last_recv_time = 0.0
        self.sent_messages: Dict[int, FixMessage] = {}
        self.last_sent_type: Optional[str] = None
        self.last_recv_type: Optional[str] = None

    def create_outbound_msg(self, msg_type: str, body: Optional[Dict[int, str]] = None) -> FixMessage:
        msg = FixMessage(
            msg_type=msg_type,
            msg_seq_num=self.out_seq_num,
            sender_comp_id=self.config.sender_comp_id,
            target_comp_id=self.config.target_comp_id,
            sending_time_iso=time.strftime("%Y%m%dT%H:%M:%SZ", time.gmtime()),
            body_fields=body or {}
        )
        self.sent_messages[self.out_seq_num] = msg
        self.out_seq_num += 1
        self.last_sent_type = msg_type
        self.last_sent_time = time.time()
        return msg

    def initiate_logon(self) -> FixMessage:
        """
        Sends FIX Logon (Tag 35=A, Tag 108=HeartBtInt) and transitions state to LOGON_SENT.
        """
        body = {108: str(self.config.heartbeat_interval_sec), 98: "0"} # Tag 98 EncryptMethod=0
        logon_msg = self.create_outbound_msg("A", body)
        self.state = "LOGON_SENT"
        logger.info(f"FIX LOGON SENT [{self.config.session_id}]: SeqNum={logon_msg.msg_seq_num}")
        return logon_msg

    def process_inbound_msg(self, msg: FixMessage) -> Tuple[FixSessionAuditReport, Optional[FixMessage]]:
        """
        Processes incoming FIX message, verifying sequence numbers and updating session state machine.
        Returns: (audit_report, optional_response_msg)
        """
        self.last_recv_type = msg.msg_type
        self.last_recv_time = time.time()

        gap_detected = False
        resp_msg: Optional[FixMessage] = None

        # 1. State Machine: Process Logon Response
        if self.state == "LOGON_SENT" and msg.msg_type == "A":
            self.state = "LOGGED_IN"
            self.expected_in_seq_num = msg.msg_seq_num + 1
            notes = f"FIX SESSION LOGGED IN [{self.config.session_id}]: Logon accepted by venue."
            logger.info(notes)
            rep = FixSessionAuditReport(
                session_id=self.config.session_id, state=self.state,
                out_seq_num=self.out_seq_num, expected_in_seq_num=self.expected_in_seq_num,
                gap_detected=False, last_sent_msg_type=self.last_sent_type, last_recv_msg_type="A",
                status="SESSION_ACTIVE", audit_notes=notes
            )
            return rep, None

        # 2. Sequence Gap Detection: Incoming MsgSeqNum > Expected in_seq_num
        if msg.msg_seq_num > self.expected_in_seq_num and not msg.poss_dup_flag:
            gap_detected = True
            begin_seq = self.expected_in_seq_num
            end_seq = msg.msg_seq_num - 1
            notes = f"FIX SEQUENCE GAP DETECTED [{self.config.session_id}]: Recv MsgSeqNum={msg.msg_seq_num} > Expected={self.expected_in_seq_num}. Issuing ResendRequest({begin_seq} to {end_seq})."
            logger.warning(notes)

            self.state = "RESEND_REQUEST_SENT"
            resp_msg = self.create_outbound_msg("2", {7: str(begin_seq), 16: "0"}) # Tag 7 BeginSeq, Tag 16 EndSeq=0 (all missing)
            rep = FixSessionAuditReport(
                session_id=self.config.session_id, state=self.state,
                out_seq_num=self.out_seq_num, expected_in_seq_num=self.expected_in_seq_num,
                gap_detected=True, last_sent_msg_type="2", last_recv_msg_type=msg.msg_type,
                status="RESEND_REQUEST_ISSUED", audit_notes=notes
            )
            return rep, resp_msg

        # 3. Handle Sequence Reset / Gap Fill (Tag 35=4)
        if msg.msg_type == "4":
            new_seq = int(msg.body_fields.get(36, str(msg.msg_seq_num + 1)))
            self.expected_in_seq_num = new_seq
            self.state = "LOGGED_IN"
            notes = f"FIX GAP FILL RECEIVED [{self.config.session_id}]: Resynchronized in_seq_num to {new_seq}."
            logger.info(notes)
            rep = FixSessionAuditReport(
                session_id=self.config.session_id, state=self.state,
                out_seq_num=self.out_seq_num, expected_in_seq_num=self.expected_in_seq_num,
                gap_detected=False, last_sent_msg_type=self.last_sent_type, last_recv_msg_type="4",
                status="SESSION_ACTIVE", audit_notes=notes
            )
            return rep, None

        # 4. Handle Out-of-Sequence Message Drop (< Expected)
        if msg.msg_seq_num < self.expected_in_seq_num and not msg.poss_dup_flag:
            notes = f"FIX OUT-OF-SEQUENCE REJECT [{self.config.session_id}]: Recv MsgSeqNum={msg.msg_seq_num} < Expected={self.expected_in_seq_num} without PossDupFlag."
            logger.error(notes)
            rep = FixSessionAuditReport(
                session_id=self.config.session_id, state="SESSION_ERROR",
                out_seq_num=self.out_seq_num, expected_in_seq_num=self.expected_in_seq_num,
                gap_detected=True, last_sent_msg_type=self.last_sent_type, last_recv_msg_type=msg.msg_type,
                status="SESSION_TERMINATED", audit_notes=notes
            )
            return rep, None

        # Normal message processing
        self.expected_in_seq_num = msg.msg_seq_num + 1
        notes = f"FIX MSG PROCESSED [{self.config.session_id}]: MsgType={msg.msg_type}, SeqNum={msg.msg_seq_num}."
        rep = FixSessionAuditReport(
            session_id=self.config.session_id, state=self.state,
            out_seq_num=self.out_seq_num, expected_in_seq_num=self.expected_in_seq_num,
            gap_detected=False, last_sent_msg_type=self.last_sent_type, last_recv_msg_type=msg.msg_type,
            status="SESSION_ACTIVE", audit_notes=notes
        )
        return rep, None

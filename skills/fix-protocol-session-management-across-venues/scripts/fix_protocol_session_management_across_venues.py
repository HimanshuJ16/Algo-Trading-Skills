"""FIX session-layer state machine for multi-venue order-entry connectivity.

Implements the FIX 4.2/4.4/5.0 *session* layer only: logon negotiation, inbound
and outbound ``MsgSeqNum`` (Tag 34) discipline, gap detection and recovery,
``SequenceReset`` handling, heartbeat/``TestRequest`` liveness, and graceful
logout. It performs no I/O -- the caller owns the socket, the wire encoding and
the transmission of everything this engine returns.

The sequence-number rules implemented here are the ones that decide whether an
``ExecutionReport`` is applied once, twice, or never. They follow the FIX
session specification rather than convenience:

* A message whose ``MsgSeqNum`` is lower than expected **with** ``PossDupFlag``
  is dropped and the expected sequence number is left untouched.
* A message whose ``MsgSeqNum`` is lower than expected **without**
  ``PossDupFlag`` is a fatal session error: Logout and terminate.
* ``SequenceReset`` may only ever *increase* the expected sequence number.
* ``SequenceReset``-Reset (``GapFillFlag`` absent or ``N``) is applied
  regardless of its own ``MsgSeqNum`` and never triggers a ``ResendRequest``.
* ``SequenceReset``-GapFill (``GapFillFlag=Y``) is subject to normal sequencing.
* A ``Logon`` carrying a gap is processed first, then recovered with a
  ``ResendRequest``.

See ``references/standards.md`` for the clause backing each of these.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# --- MsgType (Tag 35) -------------------------------------------------------
MSG_HEARTBEAT = "0"
MSG_TEST_REQUEST = "1"
MSG_RESEND_REQUEST = "2"
MSG_REJECT = "3"
MSG_SEQUENCE_RESET = "4"
MSG_LOGOUT = "5"
MSG_LOGON = "A"

#: Session-layer message types. These are regenerated on demand rather than
#: replayed from the resend buffer, per the FIX message-recovery procedure.
ADMIN_MSG_TYPES = frozenset(
    {MSG_HEARTBEAT, MSG_TEST_REQUEST, MSG_RESEND_REQUEST, MSG_REJECT,
     MSG_SEQUENCE_RESET, MSG_LOGOUT, MSG_LOGON}
)

# --- Tags -------------------------------------------------------------------
TAG_BEGIN_SEQ_NO = 7
TAG_END_SEQ_NO = 16
TAG_NEW_SEQ_NO = 36
TAG_TEXT = 58
TAG_ENCRYPT_METHOD = 98
TAG_HEART_BT_INT = 108
TAG_TEST_REQ_ID = 112
TAG_ORIG_SENDING_TIME = 122
TAG_GAP_FILL_FLAG = 123
TAG_RESET_SEQ_NUM_FLAG = 141

# --- Session states ---------------------------------------------------------
STATE_DISCONNECTED = "DISCONNECTED"
STATE_LOGON_SENT = "LOGON_SENT"
STATE_LOGGED_IN = "LOGGED_IN"
STATE_RESEND_REQUEST_SENT = "RESEND_REQUEST_SENT"
STATE_LOGOUT_SENT = "LOGOUT_SENT"

#: States in which application traffic may be exchanged.
ESTABLISHED_STATES = frozenset({STATE_LOGGED_IN, STATE_RESEND_REQUEST_SENT})

# --- Audit statuses ---------------------------------------------------------
STATUS_SESSION_ACTIVE = "SESSION_ACTIVE"
STATUS_RESEND_REQUEST_ISSUED = "RESEND_REQUEST_ISSUED"
STATUS_SESSION_TERMINATED = "SESSION_TERMINATED"
STATUS_MESSAGE_DISCARDED = "MESSAGE_DISCARDED"
STATUS_MESSAGE_REJECTED = "MESSAGE_REJECTED"

# --- Liveness defaults ------------------------------------------------------
# The FIX specification does NOT define a numeric heartbeat timeout. It says a
# TestRequest should be sent after "HeartBtInt + some reasonable transmission
# time" and the connection considered lost after the same interval again, and
# leaves "reasonable transmission time" deliberately undefined. These defaults
# reproduce QuickFIX/J's behaviour, which is a widely deployed convention and
# not a standard: TestRequest at (1 + 0.5) x HeartBtInt, disconnect at
# (1 + 1.4) x HeartBtInt. Venues frequently mandate their own multiplier
# (CME iLink 3, for example, uses 2 x KeepAliveInterval) -- override these from
# the venue specification rather than assuming the default is compliant.
DEFAULT_TEST_REQUEST_MULTIPLIER = 1.5
DEFAULT_DISCONNECT_MULTIPLIER = 2.4

#: Tag 16 (EndSeqNo) = 0 means infinity. FIX "strongly recommends" this
#: open-ended form for out-of-sequence recovery because it survives race
#: conditions a closed range does not.
END_SEQ_NO_INFINITY = "0"


def fix_utc_timestamp(epoch_seconds: float) -> str:
    """Format a UTC epoch as a FIX ``UTCTimestamp`` (Tag 52 / Tag 122).

    FIX requires ``YYYYMMDD-HH:MM:SS.sss`` -- a hyphen between date and time,
    colons in the time, and no timezone suffix. ISO-8601 forms such as
    ``20260824T15:34:51Z`` are not valid FIX and are rejected by venue parsers.
    """
    whole = int(epoch_seconds)
    millis = int(round((epoch_seconds - whole) * 1000))
    if millis >= 1000:  # rounding carried into the next second
        whole += 1
        millis = 0
    return f"{time.strftime('%Y%m%d-%H:%M:%S', time.gmtime(whole))}.{millis:03d}"


def _parse_int_tag(body: Dict[int, str], tag: int) -> Optional[int]:
    """Parse an integer FIX tag from untrusted wire input; ``None`` if invalid."""
    raw = body.get(tag)
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


@dataclass
class FixMessage:
    """A decoded FIX message. Field names mirror the FIX tag they carry."""

    msg_type: str                       # Tag 35
    msg_seq_num: int                    # Tag 34
    sender_comp_id: str                 # Tag 49
    target_comp_id: str                 # Tag 56
    sending_time_iso: str               # Tag 52, FIX UTCTimestamp (see fix_utc_timestamp)
    body_fields: Dict[int, str] = field(default_factory=dict)
    poss_dup_flag: bool = False         # Tag 43


@dataclass
class FixSessionConfig:
    """Static session configuration, normally taken from the venue's spec."""

    session_id: str
    sender_comp_id: str
    target_comp_id: str
    heartbeat_interval_sec: int = 30
    auto_resend_on_gap: bool = True
    #: Multiples of HeartBtInt. Not standards -- see the note above their
    #: module-level defaults, and set them from the venue specification.
    test_request_multiplier: float = DEFAULT_TEST_REQUEST_MULTIPLIER
    disconnect_multiplier: float = DEFAULT_DISCONNECT_MULTIPLIER
    #: Outbound messages retained for replay on an inbound ResendRequest.
    #: Bounded so a long-lived session cannot exhaust memory.
    resend_buffer_size: int = 10_000
    #: Reject inbound messages whose CompIDs do not mirror this session's.
    validate_comp_ids: bool = True

    def __post_init__(self) -> None:
        if self.heartbeat_interval_sec < 0:
            raise ValueError("heartbeat_interval_sec must be >= 0 (0 disables heartbeats)")
        if self.test_request_multiplier <= 1.0:
            raise ValueError("test_request_multiplier must exceed 1.0")
        if self.disconnect_multiplier <= self.test_request_multiplier:
            raise ValueError("disconnect_multiplier must exceed test_request_multiplier")
        if self.resend_buffer_size < 1:
            raise ValueError("resend_buffer_size must be >= 1")


@dataclass
class FixSessionAuditReport:
    """Outcome of processing one inbound message.

    ``responses`` is authoritative: transmit every message in it, in order.
    The second element of :meth:`FixProtocolSessionManagerEngine.process_inbound_msg`'s
    return tuple is ``responses[0]`` (or ``None``) and is complete for every
    case except an inbound ``ResendRequest``, which can require several.
    """

    session_id: str
    state: str
    out_seq_num: int
    expected_in_seq_num: int
    gap_detected: bool
    last_sent_msg_type: Optional[str]
    last_recv_msg_type: Optional[str]
    status: str
    audit_notes: str
    responses: List[FixMessage] = field(default_factory=list)


class FixProtocolSessionManagerEngine:
    """FIX session state machine for one venue connection.

    One instance models one session -- one ``SenderCompID``/``TargetCompID``
    pair. Sequence numbers belong to a session, never to a firm, so a
    multi-venue deployment holds one engine per venue and never shares
    sequence state between them.

    Thread safety: heartbeat timers and the receive loop normally run on
    different threads, so every state mutation is guarded by a re-entrant lock.
    """

    def __init__(
        self,
        config: FixSessionConfig,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._clock = clock
        self._lock = threading.RLock()
        self.state = STATE_DISCONNECTED
        self.out_seq_num = 1
        self.expected_in_seq_num = 1
        now = self._clock()
        self.last_sent_time = now
        self.last_recv_time = now
        self.sent_messages: Dict[int, FixMessage] = {}
        self.last_sent_type: Optional[str] = None
        self.last_recv_type: Optional[str] = None
        self.outstanding_test_req_id: Optional[str] = None
        self._test_request_counter = 0

    # ------------------------------------------------------------------ utils
    def _audit(
        self,
        status: str,
        notes: str,
        *,
        gap_detected: bool = False,
        recv_type: Optional[str] = None,
        responses: Optional[List[FixMessage]] = None,
    ) -> FixSessionAuditReport:
        return FixSessionAuditReport(
            session_id=self.config.session_id,
            state=self.state,
            out_seq_num=self.out_seq_num,
            expected_in_seq_num=self.expected_in_seq_num,
            gap_detected=gap_detected,
            last_sent_msg_type=self.last_sent_type,
            last_recv_msg_type=recv_type,
            status=status,
            audit_notes=notes,
            responses=list(responses or []),
        )

    def _trim_resend_buffer(self) -> None:
        excess = len(self.sent_messages) - self.config.resend_buffer_size
        if excess > 0:
            for seq in sorted(self.sent_messages)[:excess]:
                del self.sent_messages[seq]

    def create_outbound_msg(
        self, msg_type: str, body: Optional[Dict[int, str]] = None
    ) -> FixMessage:
        """Build the next outbound message and consume one outbound sequence number.

        The sequence number is consumed here, not on transmission. If the caller
        fails to put the returned message on the wire, the counterparty will see
        a gap and issue a ``ResendRequest`` for it -- which the resend buffer can
        answer. Never re-number or silently drop a message this method returned.
        """
        with self._lock:
            msg = FixMessage(
                msg_type=msg_type,
                msg_seq_num=self.out_seq_num,
                sender_comp_id=self.config.sender_comp_id,
                target_comp_id=self.config.target_comp_id,
                sending_time_iso=fix_utc_timestamp(self._clock()),
                body_fields=dict(body or {}),
            )
            self.sent_messages[self.out_seq_num] = msg
            self._trim_resend_buffer()
            self.out_seq_num += 1
            self.last_sent_type = msg_type
            self.last_sent_time = self._clock()
            return msg

    # ----------------------------------------------------------------- logon
    def initiate_logon(self, reset_seq_num: bool = False) -> FixMessage:
        """Send ``Logon`` (35=A) and transition to ``LOGON_SENT``.

        ``reset_seq_num`` sets ``ResetSeqNumFlag(141)=Y`` and resets both
        directions to 1. A sequence reset must be bilaterally agreed with the
        venue beforehand -- an unagreed reset is itself a session failure, and
        during live trading it discards the recovery path for any unreconciled
        execution report.
        """
        with self._lock:
            body = {
                TAG_HEART_BT_INT: str(self.config.heartbeat_interval_sec),
                TAG_ENCRYPT_METHOD: "0",
            }
            if reset_seq_num:
                body[TAG_RESET_SEQ_NUM_FLAG] = "Y"
                self.out_seq_num = 1
                self.expected_in_seq_num = 1
                self.sent_messages.clear()
            logon_msg = self.create_outbound_msg(MSG_LOGON, body)
            self.state = STATE_LOGON_SENT
            logger.info(
                "FIX LOGON SENT [%s]: SeqNum=%d HeartBtInt=%ds ResetSeqNumFlag=%s",
                self.config.session_id, logon_msg.msg_seq_num,
                self.config.heartbeat_interval_sec, "Y" if reset_seq_num else "N",
            )
            return logon_msg

    def initiate_logout(self, reason: str = "Normal logout") -> FixMessage:
        """Send ``Logout`` (35=5) and transition to ``LOGOUT_SENT``.

        The session is not closed until the counterparty's ``Logout`` response
        arrives (or the caller's own timeout expires). Tearing the socket down
        immediately forfeits the confirmation that both sides agree on the final
        sequence numbers.
        """
        with self._lock:
            logout = self.create_outbound_msg(MSG_LOGOUT, {TAG_TEXT: reason})
            self.state = STATE_LOGOUT_SENT
            logger.info("FIX LOGOUT SENT [%s]: %s", self.config.session_id, reason)
            return logout

    # -------------------------------------------------------------- liveness
    def check_liveness(self, now: Optional[float] = None) -> List[FixMessage]:
        """Return the liveness messages due at ``now``; transmit them in order.

        Emits a ``Heartbeat`` when the outbound link has been idle for
        ``HeartBtInt``, and a ``TestRequest`` when nothing has been *received*
        for ``test_request_multiplier x HeartBtInt``. Only one ``TestRequest``
        is outstanding at a time -- re-issuing it on every poll is the spin-lock
        failure mode, and it also resets nothing.

        ``HeartBtInt = 0`` disables heartbeat generation, per the specification.
        """
        with self._lock:
            if self.state not in ESTABLISHED_STATES:
                return []
            interval = self.config.heartbeat_interval_sec
            if interval <= 0:
                return []
            now = self._clock() if now is None else now
            due: List[FixMessage] = []
            if now - self.last_sent_time >= interval:
                due.append(self.create_outbound_msg(MSG_HEARTBEAT))
            if (
                self.outstanding_test_req_id is None
                and now - self.last_recv_time >= interval * self.config.test_request_multiplier
            ):
                self._test_request_counter += 1
                test_req_id = f"TR-{self.config.session_id}-{self._test_request_counter}"
                self.outstanding_test_req_id = test_req_id
                logger.warning(
                    "FIX LIVENESS PROBE [%s]: no inbound message for %.1fs "
                    "(>= %.1f x HeartBtInt). Issuing TestRequest %s.",
                    self.config.session_id, now - self.last_recv_time,
                    self.config.test_request_multiplier, test_req_id,
                )
                due.append(
                    self.create_outbound_msg(MSG_TEST_REQUEST, {TAG_TEST_REQ_ID: test_req_id})
                )
            return due

    def is_timed_out(self, now: Optional[float] = None) -> bool:
        """True once the inbound link has been silent past the disconnect threshold.

        A ``True`` here means the session is presumed dead -- but a silent
        session is not a cancelled order book. Reconcile working orders against
        the venue before assuming anything about what it still holds.
        """
        with self._lock:
            if self.state not in ESTABLISHED_STATES:
                return False
            interval = self.config.heartbeat_interval_sec
            if interval <= 0:
                return False
            now = self._clock() if now is None else now
            return now - self.last_recv_time >= interval * self.config.disconnect_multiplier

    # ---------------------------------------------------------------- resend
    def build_resend_response(self, begin_seq_no: int, end_seq_no: int) -> List[FixMessage]:
        """Answer an inbound ``ResendRequest`` for ``[begin_seq_no, end_seq_no]``.

        Stored application messages are replayed under their **original**
        ``MsgSeqNum`` with ``PossDupFlag(43)=Y`` and ``OrigSendingTime(122)``,
        as the specification requires -- retransmission must not consume new
        outbound sequence numbers. Administrative messages are not replayed;
        contiguous runs of them collapse into a ``SequenceReset``-GapFill.

        A range that has aged out of the resend buffer can only be gap-filled.
        That is spec-legal but it means real application messages were skipped,
        so it is logged at ``ERROR`` and the caller must reconcile the affected
        orders with the venue rather than assume the gap was administrative.
        """
        with self._lock:
            if end_seq_no <= 0 or end_seq_no >= self.out_seq_num:
                end_seq_no = self.out_seq_num - 1  # EndSeqNo=0 means infinity
            if begin_seq_no < 1 or begin_seq_no > end_seq_no:
                return []

            responses: List[FixMessage] = []
            gap_start: Optional[int] = None
            lost: List[int] = []

            def flush_gap(next_expected: int) -> None:
                if gap_start is None:
                    return
                responses.append(
                    FixMessage(
                        msg_type=MSG_SEQUENCE_RESET,
                        msg_seq_num=gap_start,
                        sender_comp_id=self.config.sender_comp_id,
                        target_comp_id=self.config.target_comp_id,
                        sending_time_iso=fix_utc_timestamp(self._clock()),
                        body_fields={
                            TAG_GAP_FILL_FLAG: "Y",
                            TAG_NEW_SEQ_NO: str(next_expected),
                        },
                        poss_dup_flag=True,
                    )
                )

            for seq in range(begin_seq_no, end_seq_no + 1):
                original = self.sent_messages.get(seq)
                if original is None:
                    lost.append(seq)
                if original is None or original.msg_type in ADMIN_MSG_TYPES:
                    gap_start = seq if gap_start is None else gap_start
                    continue
                flush_gap(seq)
                gap_start = None
                replay = FixMessage(
                    msg_type=original.msg_type,
                    msg_seq_num=original.msg_seq_num,
                    sender_comp_id=original.sender_comp_id,
                    target_comp_id=original.target_comp_id,
                    sending_time_iso=fix_utc_timestamp(self._clock()),
                    body_fields=dict(original.body_fields),
                    poss_dup_flag=True,
                )
                replay.body_fields[TAG_ORIG_SENDING_TIME] = original.sending_time_iso
                responses.append(replay)
            flush_gap(end_seq_no + 1)

            if lost:
                logger.error(
                    "FIX RESEND BUFFER MISS [%s]: sequence numbers %d-%d are no longer "
                    "retained and were gap-filled. Any application message in that range "
                    "is unrecoverable from this engine -- reconcile affected orders with "
                    "the venue.",
                    self.config.session_id, lost[0], lost[-1],
                )
            return responses

    # --------------------------------------------------------------- inbound
    def process_inbound_msg(
        self, msg: FixMessage
    ) -> Tuple[FixSessionAuditReport, Optional[FixMessage]]:
        """Apply one inbound message to the session state machine.

        Returns ``(audit_report, first_response)``. ``audit_report.responses``
        holds the full ordered response list and is authoritative; the tuple's
        second element is a convenience for the single-response cases.
        """
        with self._lock:
            report = self._process_locked(msg)
            return report, (report.responses[0] if report.responses else None)

    def _process_locked(self, msg: FixMessage) -> FixSessionAuditReport:
        # --- Guard: nothing but Logon may be processed before the session is up.
        if self.state == STATE_DISCONNECTED and msg.msg_type != MSG_LOGON:
            notes = (
                f"FIX PRE-LOGON MESSAGE [{self.config.session_id}]: MsgType={msg.msg_type} "
                f"received while DISCONNECTED. Dropping connection without processing; "
                f"session state and sequence numbers are unchanged."
            )
            logger.error(notes)
            return self._audit(STATUS_SESSION_TERMINATED, notes, recv_type=msg.msg_type)

        # --- Guard: the message must belong to this session.
        if self.config.validate_comp_ids and (
            msg.sender_comp_id != self.config.target_comp_id
            or msg.target_comp_id != self.config.sender_comp_id
        ):
            notes = (
                f"FIX COMPID MISMATCH [{self.config.session_id}]: expected "
                f"{self.config.target_comp_id}->{self.config.sender_comp_id}, received "
                f"{msg.sender_comp_id}->{msg.target_comp_id}. Terminating session; "
                f"sequence numbers unchanged."
            )
            logger.error(notes)
            logout = self.create_outbound_msg(MSG_LOGOUT, {TAG_TEXT: "CompID mismatch"})
            self.state = STATE_LOGOUT_SENT
            return self._audit(
                STATUS_SESSION_TERMINATED, notes,
                recv_type=msg.msg_type, responses=[logout],
            )

        # Only now is the message attributable to this session.
        self.last_recv_type = msg.msg_type
        self.last_recv_time = self._clock()

        if msg.msg_type == MSG_LOGON:
            if self.state in (STATE_DISCONNECTED, STATE_LOGON_SENT):
                return self._handle_logon(msg)
            # A Logon on an already-established session is a session-level
            # error, not application traffic. Accepting it silently would let a
            # misrouted or replayed Logon renegotiate a live session.
            notes = (
                f"FIX UNEXPECTED LOGON [{self.config.session_id}]: Logon received while "
                f"state={self.state}. Terminating session; sequence numbers unchanged."
            )
            logger.error(notes)
            logout = self.create_outbound_msg(
                MSG_LOGOUT, {TAG_TEXT: "Unexpected Logon on established session"}
            )
            self.state = STATE_LOGOUT_SENT
            return self._audit(
                STATUS_SESSION_TERMINATED, notes,
                recv_type=MSG_LOGON, responses=[logout],
            )

        # SequenceReset-Reset is evaluated before gap detection: its own
        # MsgSeqNum is ignored and it must never provoke a ResendRequest.
        if msg.msg_type == MSG_SEQUENCE_RESET and not self._is_gap_fill(msg):
            return self._handle_sequence_reset_mode(msg)

        if msg.poss_dup_flag and msg.msg_seq_num < self.expected_in_seq_num:
            notes = (
                f"FIX DUPLICATE DISCARDED [{self.config.session_id}]: MsgType={msg.msg_type} "
                f"MsgSeqNum={msg.msg_seq_num} < Expected={self.expected_in_seq_num} with "
                f"PossDupFlag=Y. Already processed; expected sequence left unchanged."
            )
            logger.info(notes)
            return self._audit(STATUS_MESSAGE_DISCARDED, notes, recv_type=msg.msg_type)

        if msg.msg_seq_num > self.expected_in_seq_num:
            return self._handle_gap(msg)

        if msg.msg_seq_num < self.expected_in_seq_num:
            return self._handle_seq_too_low(msg)

        return self._handle_in_sequence(msg)

    # ------------------------------------------------------------- handlers
    @staticmethod
    def _is_gap_fill(msg: FixMessage) -> bool:
        return str(msg.body_fields.get(TAG_GAP_FILL_FLAG, "N")).upper() == "Y"

    def _handle_logon(self, msg: FixMessage) -> FixSessionAuditReport:
        if str(msg.body_fields.get(TAG_RESET_SEQ_NUM_FLAG, "N")).upper() == "Y":
            self.expected_in_seq_num = 1
        if msg.msg_seq_num < self.expected_in_seq_num and not msg.poss_dup_flag:
            return self._handle_seq_too_low(msg)

        self.state = STATE_LOGGED_IN
        self.outstanding_test_req_id = None

        if msg.msg_seq_num > self.expected_in_seq_num:
            # The Logon is accepted first, then the gap is recovered. Advancing
            # the expected sequence to the Logon's would silently discard every
            # message the counterparty sent in between.
            begin = self.expected_in_seq_num
            if not self.config.auto_resend_on_gap:
                notes = (
                    f"FIX LOGON ACCEPTED WITH GAP [{self.config.session_id}]: Logon "
                    f"MsgSeqNum={msg.msg_seq_num} > Expected={begin} and "
                    f"auto_resend_on_gap is disabled. Session is LOGGED_IN and the "
                    f"expected sequence is held at {begin}; the caller must drive "
                    f"recovery before resuming application traffic."
                )
                logger.warning(notes)
                return self._audit(
                    STATUS_SESSION_ACTIVE, notes,
                    gap_detected=True, recv_type=MSG_LOGON,
                )
            notes = (
                f"FIX LOGON ACCEPTED WITH GAP [{self.config.session_id}]: Logon "
                f"MsgSeqNum={msg.msg_seq_num} > Expected={begin}. Session is LOGGED_IN; "
                f"issuing ResendRequest(BeginSeqNo={begin}, EndSeqNo=0) before any "
                f"application traffic."
            )
            logger.warning(notes)
            resend = self.create_outbound_msg(
                MSG_RESEND_REQUEST,
                {TAG_BEGIN_SEQ_NO: str(begin), TAG_END_SEQ_NO: END_SEQ_NO_INFINITY},
            )
            self.state = STATE_RESEND_REQUEST_SENT
            return self._audit(
                STATUS_RESEND_REQUEST_ISSUED, notes,
                gap_detected=True, recv_type=MSG_LOGON, responses=[resend],
            )

        self.expected_in_seq_num = msg.msg_seq_num + 1
        notes = f"FIX SESSION LOGGED IN [{self.config.session_id}]: Logon accepted by venue."
        logger.info(notes)
        return self._audit(STATUS_SESSION_ACTIVE, notes, recv_type=MSG_LOGON)

    def _handle_sequence_reset_mode(self, msg: FixMessage) -> FixSessionAuditReport:
        """``SequenceReset``-Reset (GapFillFlag absent or N)."""
        new_seq = _parse_int_tag(msg.body_fields, TAG_NEW_SEQ_NO)
        if new_seq is None:
            notes = (
                f"FIX SEQUENCE RESET REJECTED [{self.config.session_id}]: NewSeqNo(36) "
                f"missing or non-numeric ({msg.body_fields.get(TAG_NEW_SEQ_NO)!r}). "
                f"Expected sequence left at {self.expected_in_seq_num}."
            )
            logger.error(notes)
            return self._audit(STATUS_MESSAGE_REJECTED, notes, recv_type=MSG_SEQUENCE_RESET)

        if new_seq <= self.expected_in_seq_num:
            # A SequenceReset may only increase the sequence number. Honouring a
            # decrease would replay execution reports that have already been
            # applied to positions.
            notes = (
                f"FIX SEQUENCE RESET REJECTED [{self.config.session_id}]: NewSeqNo={new_seq} "
                f"<= Expected={self.expected_in_seq_num}. A SequenceReset may only increase "
                f"the expected sequence number; refusing to rewind."
            )
            logger.error(notes)
            reject = self.create_outbound_msg(
                MSG_REJECT,
                {TAG_TEXT: f"NewSeqNo {new_seq} is not greater than expected "
                           f"{self.expected_in_seq_num}"},
            )
            return self._audit(
                STATUS_MESSAGE_REJECTED, notes,
                recv_type=MSG_SEQUENCE_RESET, responses=[reject],
            )

        self.expected_in_seq_num = new_seq
        if self.state == STATE_RESEND_REQUEST_SENT:
            self.state = STATE_LOGGED_IN
        notes = (
            f"FIX SEQUENCE RESET (RESET MODE) [{self.config.session_id}]: own MsgSeqNum="
            f"{msg.msg_seq_num} ignored per specification; expected sequence advanced to "
            f"{new_seq}. Any application message skipped by this reset is unrecoverable -- "
            f"reconcile open orders with the venue."
        )
        logger.warning(notes)
        return self._audit(STATUS_SESSION_ACTIVE, notes, recv_type=MSG_SEQUENCE_RESET)

    def _handle_gap(self, msg: FixMessage) -> FixSessionAuditReport:
        begin = self.expected_in_seq_num
        if self.state == STATE_RESEND_REQUEST_SENT or not self.config.auto_resend_on_gap:
            # A ResendRequest is already outstanding (or auto-resend is off).
            # Issuing another for every message that arrives ahead of the gap is
            # the resend-storm failure mode; the open-ended EndSeqNo=0 request
            # already covers this message.
            notes = (
                f"FIX GAP MESSAGE HELD [{self.config.session_id}]: MsgType={msg.msg_type} "
                f"MsgSeqNum={msg.msg_seq_num} > Expected={begin}; ResendRequest already "
                f"outstanding (auto_resend_on_gap={self.config.auto_resend_on_gap}). "
                f"No further ResendRequest issued."
            )
            logger.warning(notes)
            return self._audit(
                STATUS_RESEND_REQUEST_ISSUED if self.config.auto_resend_on_gap
                else STATUS_MESSAGE_DISCARDED,
                notes, gap_detected=True, recv_type=msg.msg_type,
            )

        notes = (
            f"FIX SEQUENCE GAP DETECTED [{self.config.session_id}]: MsgType={msg.msg_type} "
            f"MsgSeqNum={msg.msg_seq_num} > Expected={begin}. Issuing ResendRequest"
            f"(BeginSeqNo={begin}, EndSeqNo=0 [infinity]). Message held pending resend; "
            f"expected sequence unchanged."
        )
        logger.warning(notes)
        self.state = STATE_RESEND_REQUEST_SENT
        resend = self.create_outbound_msg(
            MSG_RESEND_REQUEST,
            {TAG_BEGIN_SEQ_NO: str(begin), TAG_END_SEQ_NO: END_SEQ_NO_INFINITY},
        )
        return self._audit(
            STATUS_RESEND_REQUEST_ISSUED, notes,
            gap_detected=True, recv_type=msg.msg_type, responses=[resend],
        )

    def _handle_seq_too_low(self, msg: FixMessage) -> FixSessionAuditReport:
        notes = (
            f"FIX MSGSEQNUM TOO LOW [{self.config.session_id}]: MsgType={msg.msg_type} "
            f"MsgSeqNum={msg.msg_seq_num} < Expected={self.expected_in_seq_num} without "
            f"PossDupFlag. Unrecoverable session error; sending Logout and terminating."
        )
        logger.error(notes)
        logout = self.create_outbound_msg(
            MSG_LOGOUT,
            {TAG_TEXT: f"MsgSeqNum too low, expecting {self.expected_in_seq_num} "
                       f"but received {msg.msg_seq_num}"},
        )
        self.state = STATE_LOGOUT_SENT
        return self._audit(
            STATUS_SESSION_TERMINATED, notes,
            gap_detected=True, recv_type=msg.msg_type, responses=[logout],
        )

    def _handle_in_sequence(self, msg: FixMessage) -> FixSessionAuditReport:
        """``msg.msg_seq_num == self.expected_in_seq_num``."""
        if msg.msg_type == MSG_SEQUENCE_RESET:  # GapFillFlag=Y, normally sequenced
            new_seq = _parse_int_tag(msg.body_fields, TAG_NEW_SEQ_NO)
            if new_seq is None or new_seq <= self.expected_in_seq_num:
                notes = (
                    f"FIX GAP FILL REJECTED [{self.config.session_id}]: NewSeqNo="
                    f"{msg.body_fields.get(TAG_NEW_SEQ_NO)!r} is missing, non-numeric, or "
                    f"not greater than Expected={self.expected_in_seq_num}. Refusing to "
                    f"rewind the expected sequence number."
                )
                logger.error(notes)
                reject = self.create_outbound_msg(
                    MSG_REJECT, {TAG_TEXT: "Invalid NewSeqNo in SequenceReset-GapFill"}
                )
                return self._audit(
                    STATUS_MESSAGE_REJECTED, notes,
                    recv_type=MSG_SEQUENCE_RESET, responses=[reject],
                )
            self.expected_in_seq_num = new_seq
            if self.state == STATE_RESEND_REQUEST_SENT:
                self.state = STATE_LOGGED_IN
            notes = (
                f"FIX GAP FILL RECEIVED [{self.config.session_id}]: Resynchronized "
                f"in_seq_num to {new_seq}."
            )
            logger.info(notes)
            return self._audit(STATUS_SESSION_ACTIVE, notes, recv_type=MSG_SEQUENCE_RESET)

        self.expected_in_seq_num = msg.msg_seq_num + 1

        if msg.msg_type == MSG_TEST_REQUEST:
            # Failing to echo TestReqID(112) is read by the counterparty as a
            # dead session and gets the connection dropped.
            test_req_id = str(msg.body_fields.get(TAG_TEST_REQ_ID, ""))
            heartbeat = self.create_outbound_msg(
                MSG_HEARTBEAT, {TAG_TEST_REQ_ID: test_req_id} if test_req_id else None
            )
            notes = (
                f"FIX TEST REQUEST ANSWERED [{self.config.session_id}]: replying with "
                f"Heartbeat carrying TestReqID={test_req_id!r}."
            )
            logger.info(notes)
            return self._audit(
                STATUS_SESSION_ACTIVE, notes,
                recv_type=MSG_TEST_REQUEST, responses=[heartbeat],
            )

        if msg.msg_type == MSG_HEARTBEAT:
            echoed = str(msg.body_fields.get(TAG_TEST_REQ_ID, ""))
            if self.outstanding_test_req_id and echoed == self.outstanding_test_req_id:
                self.outstanding_test_req_id = None
                notes = (
                    f"FIX LIVENESS CONFIRMED [{self.config.session_id}]: Heartbeat echoed "
                    f"TestReqID={echoed}."
                )
            else:
                notes = f"FIX HEARTBEAT [{self.config.session_id}]: SeqNum={msg.msg_seq_num}."
            logger.debug(notes)
            return self._audit(STATUS_SESSION_ACTIVE, notes, recv_type=MSG_HEARTBEAT)

        if msg.msg_type == MSG_RESEND_REQUEST:
            begin = _parse_int_tag(msg.body_fields, TAG_BEGIN_SEQ_NO)
            end = _parse_int_tag(msg.body_fields, TAG_END_SEQ_NO)
            if begin is None or end is None:
                notes = (
                    f"FIX RESEND REQUEST REJECTED [{self.config.session_id}]: BeginSeqNo="
                    f"{msg.body_fields.get(TAG_BEGIN_SEQ_NO)!r} EndSeqNo="
                    f"{msg.body_fields.get(TAG_END_SEQ_NO)!r} missing or non-numeric."
                )
                logger.error(notes)
                reject = self.create_outbound_msg(
                    MSG_REJECT, {TAG_TEXT: "Invalid BeginSeqNo/EndSeqNo in ResendRequest"}
                )
                return self._audit(
                    STATUS_MESSAGE_REJECTED, notes,
                    recv_type=MSG_RESEND_REQUEST, responses=[reject],
                )
            retransmission = self.build_resend_response(begin, end)
            notes = (
                f"FIX RESEND REQUEST SERVED [{self.config.session_id}]: BeginSeqNo={begin} "
                f"EndSeqNo={end}; {len(retransmission)} message(s) queued for retransmission."
            )
            logger.warning(notes)
            return self._audit(
                STATUS_SESSION_ACTIVE, notes,
                recv_type=MSG_RESEND_REQUEST, responses=retransmission,
            )

        if msg.msg_type == MSG_LOGOUT:
            acknowledgement: List[FixMessage] = []
            if self.state != STATE_LOGOUT_SENT:
                acknowledgement.append(
                    self.create_outbound_msg(MSG_LOGOUT, {TAG_TEXT: "Logout acknowledged"})
                )
            self.state = STATE_DISCONNECTED
            notes = (
                f"FIX SESSION TERMINATED [{self.config.session_id}]: Logout exchanged. "
                f"Reason={msg.body_fields.get(TAG_TEXT, 'not supplied')!r}. Final expected "
                f"in_seq_num={self.expected_in_seq_num}, out_seq_num={self.out_seq_num}."
            )
            logger.info(notes)
            return self._audit(
                STATUS_SESSION_TERMINATED, notes,
                recv_type=MSG_LOGOUT, responses=acknowledgement,
            )

        notes = (
            f"FIX MSG PROCESSED [{self.config.session_id}]: MsgType={msg.msg_type}, "
            f"SeqNum={msg.msg_seq_num}."
        )
        logger.debug(notes)
        return self._audit(STATUS_SESSION_ACTIVE, notes, recv_type=msg.msg_type)

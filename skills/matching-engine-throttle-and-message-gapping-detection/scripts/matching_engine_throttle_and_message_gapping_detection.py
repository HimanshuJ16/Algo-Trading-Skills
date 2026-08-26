"""
Session-layer monitor for exchange order-entry links: outbound message-rate throttle
detection and inbound sequence-gap detection.

Two independent controls share one audit record:

1. **Outbound throttle.** Venues count the messages a *session* submits over a
   venue-defined interval and shed load by **rejecting**, then **disconnecting** -- they
   do not queue your overflow. CME Globex counts iLink messages over a pre-defined time
   interval that begins with the first message processed; breaching the reject threshold
   rejects subsequent messages, and breaching the larger terminate threshold ends the
   session. This module answers "did I put more than ``max_allowed_mps`` messages into
   *any* window of ``window_seconds`` in the log I was given" and, if so, directs the
   caller to stop submitting.

2. **Inbound sequence gap.** FIX (``MsgSeqNum(34)``), FIXP/iLink 3 and MoldUDP64 all carry
   a per-session sequence number, so a received number *higher* than expected means
   messages were lost and must be retransmit-requested. A number *lower* than expected
   without a possible-duplicate marker is a protocol violation the FIX session layer
   requires the receiver to resolve by logging out and terminating the session -- it is
   never something to silently drop.

Scope and limits (read before trusting a verdict out of this module):

* ``max_allowed_mps = 500.0`` is a **placeholder**, not a venue-published figure. CME does
  not publish a single per-iLink-session application TPS number; its published figures are
  the *administrative* message controls (100 admin MPS averaged over three seconds ->
  reject; 200 admin MPS over three seconds, or 5 invalid Negotiate/Establish in 60
  seconds -> automatic port closure). Nasdaq's INET Nordic order-entry port limit was
  reduced from 20,000 msg/s in 2020 (announced as 5,000, revised to 10,000 before it took
  effect). Set ``max_allowed_mps`` and ``window_seconds`` from your own session's
  contracted limit and the venue's own counting interval; see ``references/standards.md``.
* Venue counters are **per message class**. CME's administrative counter is separate from
  application messaging. This module counts every record it is given, so pre-filter the
  log per class and call once per class rather than mixing them.
* The throttle verdict is computed on the **worst** window in the supplied log, not the
  most recent one. Pass only the messages you consider in scope.
* Sequence numbers are **per session**. Records whose ``session_id`` does not match the
  audited session are excluded and counted, never folded into the session's counters.
* A sequence **reset** (FIX Logon with ``ResetSeqNumFlag=Y``, a new FIXP UUID, a new
  SoupBinTCP session) is signalled by the session layer and cannot be inferred from a low
  sequence number. Call ``reset_session_sequence`` explicitly; this module will never
  guess, because guessing turns a genuine regression into a silent state divergence.
* This module *detects and directs*. It does not send Retransmit Requests, Resend
  Requests, Logouts or MoldUDP64 Request Packets, and it does not gate the order path --
  wire its ``directives`` into whatever does.
"""
import logging
import math
import time
from dataclasses import dataclass
from typing import Dict, Final, List, Optional, Sequence, Set, Tuple

logger = logging.getLogger(__name__)

# --- Throttle statuses ----------------------------------------------------------------
STATUS_NORMAL: Final[str] = "MATCHING_ENGINE_NORMAL"
STATUS_THROTTLE_WARNING: Final[str] = "THROTTLE_WARNING_SLOW_DOWN"
STATUS_THROTTLED: Final[str] = "EXCHANGE_RATE_LIMIT_THROTTLED"

# --- Sequence statuses ----------------------------------------------------------------
SEQUENCE_CONTIGUOUS: Final[str] = "SEQUENCE_CONTIGUOUS"
STATUS_SEQUENCE_GAP: Final[str] = "MESSAGE_SEQUENCE_GAP_DETECTED"
STATUS_SEQUENCE_REGRESSION: Final[str] = "SEQUENCE_REGRESSION_SESSION_UNRECOVERABLE"

# Precedence used to collapse the two independent verdicts into the single ``status``
# field. Rationale, weakest first:
#   WARNING     -- advisory; keep trading, slow down.
#   GAP         -- recoverable inside the session by retransmission.
#   THROTTLED   -- the venue will reject and then terminate if you keep submitting;
#                  stopping is more urgent than recovering, because a terminated session
#                  loses the very link the retransmission would arrive on.
#   REGRESSION  -- the FIX session layer requires logout + termination; the session is not
#                  recoverable in place and local order state may already be wrong.
# ``status`` is a convenience only. The booleans beside it are INDEPENDENT and a caller
# must honour every one of them -- never branch on ``status`` alone.
_STATUS_SEVERITY: Final[Dict[str, int]] = {
    STATUS_NORMAL: 0,
    SEQUENCE_CONTIGUOUS: 0,
    STATUS_THROTTLE_WARNING: 1,
    STATUS_SEQUENCE_GAP: 2,
    STATUS_THROTTLED: 3,
    STATUS_SEQUENCE_REGRESSION: 4,
}

# --- Directives -----------------------------------------------------------------------
DIRECTIVE_REDUCE_RATE: Final[str] = "REDUCE_OUTBOUND_MESSAGE_RATE"
DIRECTIVE_BLOCK_OUTBOUND: Final[str] = "BLOCK_OUTBOUND_ORDER_SUBMISSION"
DIRECTIVE_REQUEST_RETRANSMIT: Final[str] = "REQUEST_RETRANSMIT"
DIRECTIVE_TERMINATE_SESSION: Final[str] = "TERMINATE_SESSION_AND_RESYNC"
DIRECTIVE_SESSION_RESYNC: Final[str] = "SESSION_RESYNC_REQUIRED"

# CME iLink 3 / Drop Copy 4.0 cap the Count of a single Retransmit (Resend) Request at
# 2500 messages; a larger request is rejected, so a gap above this must be recovered with
# several sequential requests. CME's AutoCert+ suite tests specifically that a client does
# NOT enter an infinite resend loop on a gap larger than the cap. Confirm the cap for your
# own venue -- it is not universal.
DEFAULT_MAX_RETRANSMIT_REQUEST_COUNT: Final[int] = 2500

# Ceiling on how many out-of-order sequence numbers may be held while a gap stays open.
# Reaching it means retransmission is not converging; the correct response is a
# session-level resynchronisation, not more gap-fill.
DEFAULT_MAX_BUFFERED_AHEAD: Final[int] = 10_000


def _require_finite(value: float, name: str) -> float:
    """Reject NaN/Inf before it can propagate into a 'matching engine normal' verdict."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(
            f"{name} must be a finite number, got {value!r}. A non-finite timestamp or "
            f"limit must fail loudly -- silently dropping it reports a burst as 0 msgs/sec."
        )
    return numeric


def _require_int_at_least(value: int, name: str, minimum: int) -> int:
    """Sequence numbers and counts are integers; a float here is almost always a swapped field."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{name} must be an int, got {type(value).__name__} ({value!r}). "
            f"A float here usually means a timestamp was passed into a sequence field."
        )
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}.")
    return value


def _require_non_empty_str(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string, got {value!r}.")
    return value


@dataclass(frozen=True)
class OutboundMessageRecord:
    """
    One message this client submitted on the session.

    Args:
        session_id: Order-entry session the message was submitted on.
        message_type: Venue message type, e.g. ``'NEW_ORDER'``, ``'CANCEL'``, ``'MODIFY'``.
            Audit context only -- **this module does not separate message classes**, so
            pre-filter per class when the venue counts them separately.
        timestamp_epoch: Submission time as seconds since the Unix epoch; the fractional
            part carries sub-second resolution.
        sequence_id: Outbound sequence number. Recorded for audit only -- outbound gap
            detection is the venue's job (FIXP signals it with ``NotApplied``) and is not
            modelled here, so ``0`` is accepted to mean "not tracked".

    Raises:
        ValueError: on an empty ``session_id``/``message_type``, a non-finite timestamp,
            or a negative ``sequence_id``.
        TypeError: if ``sequence_id`` is not an int.
    """

    session_id: str
    message_type: str
    timestamp_epoch: float
    sequence_id: int

    def __post_init__(self) -> None:
        _require_non_empty_str(self.session_id, "session_id")
        _require_non_empty_str(self.message_type, "message_type")
        _require_finite(self.timestamp_epoch, "timestamp_epoch")
        _require_int_at_least(self.sequence_id, "sequence_id", minimum=0)


@dataclass(frozen=True)
class InboundMessageRecord:
    """
    One message received from the venue on the session.

    Note the field order differs from :class:`OutboundMessageRecord` (``sequence_id``
    precedes ``timestamp_epoch`` here). The order is preserved for backwards
    compatibility; the validation below turns a swapped pair into a loud error rather
    than a plausible-looking wrong answer.

    Args:
        session_id: Session the message arrived on. Sequence numbers are per session.
        message_type: Venue message type, e.g. ``'EXEC_REPORT'``, ``'CANCEL_ACK'``,
            ``'REJECT'``.
        sequence_id: Session-layer sequence number -- FIX ``MsgSeqNum(34)``, the FIXP /
            iLink 3 sequence number, or a MoldUDP64 **per-message** sequence number. For
            MoldUDP64 expand each packet into ``MessageCount`` records starting at the
            packet header's sequence number: the header numbers the *first* message in
            the packet, so advancing one per packet invents phantom gaps. The first
            message of a session is 1.
        timestamp_epoch: Receipt time as seconds since the Unix epoch.
        poss_dup: True when the venue marked this message a possible duplicate -- FIX
            ``PossDupFlag(43)=Y``, or a message delivered in answer to a retransmit
            request. A low sequence number carrying this flag is an expected duplicate;
            without it, it is a protocol violation.

    Raises:
        ValueError: on an empty ``session_id``/``message_type``, a non-finite timestamp,
            or ``sequence_id < 1``.
        TypeError: if ``sequence_id`` is not an int or ``poss_dup`` is not a bool.
    """

    session_id: str
    message_type: str
    sequence_id: int
    timestamp_epoch: float
    poss_dup: bool = False

    def __post_init__(self) -> None:
        _require_non_empty_str(self.session_id, "session_id")
        _require_non_empty_str(self.message_type, "message_type")
        _require_int_at_least(self.sequence_id, "sequence_id", minimum=1)
        _require_finite(self.timestamp_epoch, "timestamp_epoch")
        if not isinstance(self.poss_dup, bool):
            raise TypeError(f"poss_dup must be a bool, got {type(self.poss_dup).__name__}.")


@dataclass(frozen=True)
class SequenceGapDetail:
    """
    One contiguous run of sequence numbers still missing at the end of the audit.

    ``missing_seq_start``/``missing_seq_end`` are **inclusive** and are exactly the range
    to put in a Retransmit/Resend Request. ``expected_seq_id`` equals
    ``missing_seq_start``; ``received_seq_id`` is the first number received above the run.
    """

    session_id: str
    expected_seq_id: int
    received_seq_id: int
    missing_seq_start: int
    missing_seq_end: int
    max_retransmit_request_count: int = DEFAULT_MAX_RETRANSMIT_REQUEST_COUNT

    @property
    def gap_size(self) -> int:
        """Number of messages missing in this run, inclusive of both endpoints."""
        return self.missing_seq_end - self.missing_seq_start + 1

    @property
    def retransmit_requests_required(self) -> int:
        """
        Sequential retransmit requests needed to cover the run.

        Above the venue's per-request cap the gap must be recovered in several requests;
        re-issuing one oversized request instead is the infinite-resend-loop failure CME's
        certification suite exists to catch.
        """
        return -(-self.gap_size // self.max_retransmit_request_count)

    @property
    def exceeds_single_retransmit_limit(self) -> bool:
        """True when one request cannot cover this run."""
        return self.gap_size > self.max_retransmit_request_count


@dataclass(frozen=True)
class SequenceRegressionDetail:
    """
    A sequence number *below* the expected one, arriving without a duplicate marker.

    The FIX session layer requires the receiver to send ``Logout(35=5)`` with
    ``SessionStatus(1409)=9`` ("received MsgSeqNum too low") and terminate the transport
    connection -- the only exception being ``SequenceReset(35=4)`` with
    ``GapFillFlag(123)=N``. Continuing to trade over a regressed session risks acting on a
    stale execution report twice.
    """

    session_id: str
    expected_seq_id: int
    received_seq_id: int
    message_type: str
    poss_dup: bool = False


@dataclass(frozen=True)
class MatchingEngineAuditReport:
    """
    Result of one session audit.

    ``status`` collapses the throttle and sequence verdicts by severity for logging. It is
    lossy on purpose: ``is_throttled``, ``has_sequence_gap`` and
    ``has_sequence_regression`` are independent and **all** must be honoured. Branching on
    ``status`` alone will miss a throttle block that coincides with a sequence gap.

    ``outbound_rate_per_sec`` is the trailing-window rate ending at ``as_of_epoch``, for
    display. ``peak_window_rate_per_sec`` is the worst window anywhere in the supplied log
    and is what ``is_throttled`` is decided on.
    """

    session_id: str
    outbound_rate_per_sec: float
    max_allowed_mps: float
    is_throttled: bool
    has_sequence_gap: bool
    sequence_gap_details: Optional[SequenceGapDetail]
    status: str
    audit_notes: str
    # --- appended fields (all defaulted, so positional construction stays compatible) --
    peak_window_rate_per_sec: float = 0.0
    throttle_status: str = STATUS_NORMAL
    sequence_status: str = SEQUENCE_CONTIGUOUS
    sequence_gaps: Tuple[SequenceGapDetail, ...] = ()
    has_sequence_regression: bool = False
    sequence_regressions: Tuple[SequenceRegressionDetail, ...] = ()
    next_expected_seq_id: int = 1
    window_seconds: float = 1.0
    as_of_epoch: float = 0.0
    outbound_message_count: int = 0
    newest_outbound_age_sec: Optional[float] = None
    records_excluded_other_session: int = 0
    future_dated_outbound_count: int = 0
    duplicate_inbound_count: int = 0
    out_of_order_ahead_count: int = 0
    buffered_ahead_overflow: bool = False
    directives: Tuple[str, ...] = ()


class MatchingEngineMonitorEngine:
    """
    Detects outbound message-rate throttle breaches and inbound sequence discontinuities
    on an exchange order-entry session.

    The engine is **stateful across calls**: it carries the next expected inbound sequence
    number and any still-unfilled sequence numbers per session, so successive audits of
    consecutive message batches compose. It is *not* thread-safe -- serialise calls for a
    given session, or hold one engine per session.
    """

    def __init__(
        self,
        max_allowed_mps: float = 500.0,
        warning_threshold_pct: float = 80.0,
        max_retransmit_request_count: int = DEFAULT_MAX_RETRANSMIT_REQUEST_COUNT,
        max_buffered_ahead: int = DEFAULT_MAX_BUFFERED_AHEAD,
    ) -> None:
        """
        Args:
            max_allowed_mps: Messages per second this session is permitted. **Placeholder
                default** -- 500.0 is not a venue-published figure. Take the real number
                from your session's contracted limit.
            warning_threshold_pct: Percentage of ``max_allowed_mps`` at which to warn.
                Must be in ``(0, 100]``.
            max_retransmit_request_count: Venue cap on the message Count of one
                retransmit/resend request. 2500 is CME iLink 3 / Drop Copy 4.0; confirm
                yours.
            max_buffered_ahead: Ceiling on out-of-order sequence numbers held while a gap
                is open, before the audit escalates to a session resync.

        Raises:
            ValueError: on a non-finite or non-positive ``max_allowed_mps``, a
                ``warning_threshold_pct`` outside ``(0, 100]``, or a
                ``max_retransmit_request_count`` / ``max_buffered_ahead`` below 1.
            TypeError: if the integer parameters are not ints.
        """
        max_mps = _require_finite(max_allowed_mps, "max_allowed_mps")
        if max_mps <= 0.0:
            raise ValueError(
                f"max_allowed_mps must be > 0, got {max_mps}. A non-positive limit makes "
                f"every audit report a throttle block."
            )
        warn_pct = _require_finite(warning_threshold_pct, "warning_threshold_pct")
        if not 0.0 < warn_pct <= 100.0:
            raise ValueError(
                f"warning_threshold_pct must be in (0, 100], got {warn_pct}. Above 100 the "
                f"warning band is unreachable and the first signal is the hard block."
            )
        _require_int_at_least(
            max_retransmit_request_count, "max_retransmit_request_count", minimum=1)
        _require_int_at_least(max_buffered_ahead, "max_buffered_ahead", minimum=1)

        self.max_allowed_mps: float = max_mps
        self.warning_threshold_pct: float = warn_pct
        self.max_retransmit_request_count: int = max_retransmit_request_count
        self.max_buffered_ahead: int = max_buffered_ahead

        # Next inbound sequence number expected per session. FIX, FIXP and SoupBinTCP all
        # number the first message of a session 1.
        self.session_expected_seq: Dict[str, int] = {}
        # Sequence numbers received while a gap was open, held until the retransmission
        # fills the hole in front of them.
        self._session_buffered_ahead: Dict[str, Set[int]] = {}

    # --- session lifecycle ------------------------------------------------------------

    def reset_session_sequence(self, session_id: str, next_expected_seq_id: int = 1) -> None:
        """
        Re-anchor a session's inbound sequence counter after a venue-signalled reset.

        Call this **only** on an explicit session-layer signal -- a FIX ``Logon(35=A)``
        with ``ResetSeqNumFlag(141)=Y``, a FIXP ``Establish``/``Negotiate`` under a new
        UUID, or a new SoupBinTCP session id. A low sequence number on its own is a
        regression, not a reset, and inferring a reset from one would silently discard the
        evidence that local order state has diverged.

        Any still-unfilled gap for the session is discarded, because it belongs to the
        previous sequence stream and can no longer be retransmit-requested.
        """
        _require_non_empty_str(session_id, "session_id")
        _require_int_at_least(next_expected_seq_id, "next_expected_seq_id", minimum=1)
        dropped = len(self._session_buffered_ahead.get(session_id, ()))
        self.session_expected_seq[session_id] = next_expected_seq_id
        self._session_buffered_ahead[session_id] = set()
        logger.warning(
            "SEQUENCE RESET [%s]: next expected sequence re-anchored to %d; %d buffered "
            "out-of-order message(s) from the previous stream discarded.",
            session_id, next_expected_seq_id, dropped,
        )

    def forget_session(self, session_id: str) -> None:
        """
        Drop all retained state for a session.

        Per-session state is retained indefinitely so consecutive batches compose. In a
        long-running monitor whose session identifier changes per connection -- a FIXP
        UUID, say, rather than a stable session name -- that grows without bound. Call
        this when a session is torn down for good. Unknown ids are a no-op.
        """
        _require_non_empty_str(session_id, "session_id")
        self.session_expected_seq.pop(session_id, None)
        self._session_buffered_ahead.pop(session_id, None)

    # --- audit ------------------------------------------------------------------------

    def audit_matching_engine_session(
        self,
        session_id: str,
        outbound_messages: Sequence[OutboundMessageRecord],
        inbound_messages: Sequence[InboundMessageRecord],
        window_seconds: float = 1.0,
        as_of_epoch: Optional[float] = None,
    ) -> MatchingEngineAuditReport:
        """
        Audit outbound message rate and inbound sequence continuity for one session.

        Args:
            session_id: Session to audit. Records in either list whose ``session_id``
                differs are excluded and counted in ``records_excluded_other_session``.
            outbound_messages: Messages submitted on the session. Order is irrelevant --
                the throttle verdict is computed from the timestamps.
            inbound_messages: Messages received on the session, **in arrival order**. This
                list is deliberately *not* sorted by sequence number: sorting would let a
                later arrival conceal the gap that existed when the earlier one landed,
                which is exactly the moment a retransmit request was owed.
            window_seconds: Length of the counting window, in seconds. Match the venue's
                own counting interval -- a burst that breaches a 100 ms counter can sit
                well inside a 1 s average.
            as_of_epoch: Evaluation time for the *reported* trailing rate and staleness
                age. Defaults to ``time.time()``. The throttle **verdict** does not depend
                on it: it is taken from the worst window in the supplied log, so a
                replayed or clock-stepped log still reports the burst it contains.

        Returns:
            A :class:`MatchingEngineAuditReport`. Honour ``is_throttled``,
            ``has_sequence_gap`` and ``has_sequence_regression`` independently.

        Raises:
            ValueError: on an empty ``session_id``, a non-finite or non-positive
                ``window_seconds``, or a non-finite ``as_of_epoch``.
        """
        _require_non_empty_str(session_id, "session_id")
        window = _require_finite(window_seconds, "window_seconds")
        if window <= 0.0:
            raise ValueError(
                f"window_seconds must be > 0, got {window}. Zero divides by zero and a "
                f"negative window reports a burst as a negative rate."
            )
        as_of = time.time() if as_of_epoch is None else _require_finite(as_of_epoch, "as_of_epoch")

        excluded = 0

        own_outbound: List[OutboundMessageRecord] = []
        for out_msg in outbound_messages:
            if out_msg.session_id == session_id:
                own_outbound.append(out_msg)
            else:
                excluded += 1

        own_inbound: List[InboundMessageRecord] = []
        for in_msg in inbound_messages:
            if in_msg.session_id == session_id:
                own_inbound.append(in_msg)
            else:
                excluded += 1

        if excluded:
            logger.warning(
                "SESSION MISMATCH [%s]: excluded %d record(s) belonging to another "
                "session. Rate limits and sequence numbers are per session and must not "
                "be pooled.",
                session_id, excluded,
            )

        throttle = self._audit_outbound_rate(session_id, own_outbound, window, as_of)
        sequence = self._audit_inbound_sequence(session_id, own_inbound)

        # Collapse by severity. Both-benign resolves to STATUS_NORMAL explicitly rather
        # than relying on max()'s tie-break, so SEQUENCE_CONTIGUOUS -- an inbound-only
        # label -- can never surface as the report's top-level status.
        if _STATUS_SEVERITY[sequence.status] > _STATUS_SEVERITY[throttle.status]:
            status = sequence.status
        elif _STATUS_SEVERITY[throttle.status] > 0:
            status = throttle.status
        else:
            status = STATUS_NORMAL

        directives: List[str] = []
        if throttle.status == STATUS_THROTTLED:
            directives.append(DIRECTIVE_BLOCK_OUTBOUND)
        elif throttle.status == STATUS_THROTTLE_WARNING:
            directives.append(DIRECTIVE_REDUCE_RATE)
        if sequence.gaps:
            directives.append(DIRECTIVE_REQUEST_RETRANSMIT)
        if sequence.buffer_overflow:
            directives.append(DIRECTIVE_SESSION_RESYNC)
        if sequence.regressions:
            directives.append(DIRECTIVE_TERMINATE_SESSION)

        notes = throttle.notes
        if sequence.notes:
            notes = f"{notes} | {sequence.notes}"

        return MatchingEngineAuditReport(
            session_id=session_id,
            outbound_rate_per_sec=throttle.trailing_rate,
            max_allowed_mps=self.max_allowed_mps,
            is_throttled=throttle.status == STATUS_THROTTLED,
            has_sequence_gap=bool(sequence.gaps),
            sequence_gap_details=sequence.gaps[0] if sequence.gaps else None,
            status=status,
            audit_notes=notes,
            peak_window_rate_per_sec=throttle.peak_rate,
            throttle_status=throttle.status,
            sequence_status=sequence.status,
            sequence_gaps=tuple(sequence.gaps),
            has_sequence_regression=bool(sequence.regressions),
            sequence_regressions=tuple(sequence.regressions),
            next_expected_seq_id=sequence.next_expected,
            window_seconds=window,
            as_of_epoch=as_of,
            outbound_message_count=len(own_outbound),
            newest_outbound_age_sec=throttle.newest_age,
            records_excluded_other_session=excluded,
            future_dated_outbound_count=throttle.future_dated,
            duplicate_inbound_count=sequence.duplicates,
            out_of_order_ahead_count=sequence.out_of_order_ahead,
            buffered_ahead_overflow=sequence.buffer_overflow,
            directives=tuple(directives),
        )

    # --- outbound ---------------------------------------------------------------------

    def _audit_outbound_rate(
        self,
        session_id: str,
        messages: Sequence[OutboundMessageRecord],
        window: float,
        as_of: float,
    ) -> "_ThrottleVerdict":
        """
        Rate the session against ``max_allowed_mps``.

        Two rates are produced. ``peak_rate`` is the maximum count over *any* window of
        ``window`` seconds in the supplied log -- this is the breach test, and it is a
        pure function of the data. ``trailing_rate`` is the count in the single window
        ending at ``as_of``, reported for live dashboards. The verdict uses the peak:
        under-reporting a breach costs the session, over-reporting costs one slowed batch.
        """
        timestamps = sorted(msg.timestamp_epoch for msg in messages)
        future_dated = sum(1 for stamp in timestamps if stamp > as_of)
        newest_age: Optional[float] = (as_of - timestamps[-1]) if timestamps else None

        if future_dated:
            logger.warning(
                "CLOCK SKEW [%s]: %d outbound message(s) timestamped after the evaluation "
                "time. They still count toward the peak window; check the clock source.",
                session_id, future_dated,
            )
        if newest_age is not None and newest_age > window:
            logger.warning(
                "STALE LOG [%s]: newest outbound message is %.3fs old against a %.3fs "
                "window. The trailing rate reflects an empty window; the verdict is taken "
                "from the peak window in the log.",
                session_id, newest_age, window,
            )

        peak_rate = _peak_window_count(timestamps, window) / window
        trailing_rate = sum(
            1 for stamp in timestamps if (as_of - window) < stamp <= as_of) / window

        warning_limit = (self.warning_threshold_pct / 100.0) * self.max_allowed_mps

        if peak_rate >= self.max_allowed_mps:
            status = STATUS_THROTTLED
            notes = (
                f"EXCHANGE THROTTLE BLOCK [{session_id}]: peak rate {peak_rate:.0f} msgs/sec "
                f"over a {window:g}s window reaches/exceeds max limit "
                f"({self.max_allowed_mps:.0f} msgs/sec)! Outbound orders blocked."
            )
            logger.critical(notes)
        elif peak_rate >= warning_limit:
            status = STATUS_THROTTLE_WARNING
            notes = (
                f"THROTTLE WARNING [{session_id}]: peak rate {peak_rate:.0f} msgs/sec over a "
                f"{window:g}s window nears limit ({self.warning_threshold_pct:.0f}% of "
                f"{self.max_allowed_mps:.0f} msgs/sec)."
            )
            logger.warning(notes)
        else:
            status = STATUS_NORMAL
            notes = (
                f"MATCHING ENGINE OK [{session_id}]: peak rate = {peak_rate:.0f} msgs/sec "
                f"over a {window:g}s window."
            )

        return _ThrottleVerdict(
            status=status,
            notes=notes,
            peak_rate=peak_rate,
            trailing_rate=trailing_rate,
            newest_age=newest_age,
            future_dated=future_dated,
        )

    # --- inbound ----------------------------------------------------------------------

    def _audit_inbound_sequence(
        self,
        session_id: str,
        messages: Sequence[InboundMessageRecord],
    ) -> "_SequenceVerdict":
        """
        Walk inbound messages **in arrival order** and maintain the session's sequence
        state.

        On a number above the expected one the expected counter is *not* advanced: the
        missing messages are still owed by the venue and the retransmission will deliver
        them under their original numbers. Advancing past a gap would report it once and
        then declare the stream healthy while the messages never arrived.
        """
        expected = self.session_expected_seq.setdefault(session_id, 1)
        buffered = self._session_buffered_ahead.setdefault(session_id, set())

        duplicates = 0
        out_of_order_ahead = 0
        overflow = False
        regressions: List[SequenceRegressionDetail] = []

        for msg in messages:
            seq = msg.sequence_id
            if seq == expected:
                expected += 1
                while expected in buffered:
                    buffered.discard(expected)
                    expected += 1
            elif seq > expected:
                if seq in buffered:
                    duplicates += 1
                    logger.warning(
                        "DUPLICATE AHEAD [%s]: sequence %d already buffered while the gap "
                        "from %d is open.", session_id, seq, expected,
                    )
                    continue
                if len(buffered) >= self.max_buffered_ahead:
                    overflow = True
                    logger.critical(
                        "GAP BUFFER OVERFLOW [%s]: %d out-of-order message(s) held while "
                        "the gap from %d is still open. Retransmission is not converging "
                        "-- resynchronise the session instead of requesting more.",
                        session_id, len(buffered), expected,
                    )
                    continue
                buffered.add(seq)
                out_of_order_ahead += 1
                logger.error(
                    "SEQUENCE GAP DETECTED [%s]: expected sequence %d, got %d. Missing "
                    "range [%d, %d]. Retransmit request owed.",
                    session_id, expected, seq, expected, seq - 1,
                )
            else:  # seq < expected
                if msg.poss_dup:
                    duplicates += 1
                    logger.info(
                        "POSSIBLE DUPLICATE [%s]: sequence %d below expected %d with "
                        "PossDup set; already applied, discarding.",
                        session_id, seq, expected,
                    )
                else:
                    regressions.append(SequenceRegressionDetail(
                        session_id=session_id,
                        expected_seq_id=expected,
                        received_seq_id=seq,
                        message_type=msg.message_type,
                        poss_dup=False,
                    ))
                    logger.critical(
                        "SEQUENCE REGRESSION [%s]: received sequence %d below expected %d "
                        "without a possible-duplicate marker. The FIX session layer "
                        "requires Logout with SessionStatus=9 and transport termination; "
                        "do not treat this as a sequence reset.",
                        session_id, seq, expected,
                    )

        self.session_expected_seq[session_id] = expected

        gaps = [
            SequenceGapDetail(
                session_id=session_id,
                expected_seq_id=start,
                received_seq_id=received,
                missing_seq_start=start,
                missing_seq_end=end,
                max_retransmit_request_count=self.max_retransmit_request_count,
            )
            for start, end, received in _missing_runs(expected, buffered)
        ]

        if regressions:
            status = STATUS_SEQUENCE_REGRESSION
        elif gaps:
            status = STATUS_SEQUENCE_GAP
        else:
            status = SEQUENCE_CONTIGUOUS

        note_parts: List[str] = []
        if gaps:
            ranges = ", ".join(f"[{gap.missing_seq_start}, {gap.missing_seq_end}]" for gap in gaps)
            requests = sum(gap.retransmit_requests_required for gap in gaps)
            note_parts.append(
                f"SEQUENCE GAP DETECTED: {len(gaps)} open run(s) missing {ranges}; "
                f"{requests} retransmit request(s) required at a cap of "
                f"{self.max_retransmit_request_count} messages each."
            )
        if regressions:
            note_parts.append(
                f"SEQUENCE REGRESSION: {len(regressions)} message(s) below expected "
                f"sequence {expected} without PossDup -- terminate and resynchronise."
            )
        if overflow:
            note_parts.append(
                f"GAP BUFFER OVERFLOW at {self.max_buffered_ahead} held messages -- "
                f"session resync required."
            )

        return _SequenceVerdict(
            status=status,
            notes=" | ".join(note_parts),
            gaps=gaps,
            regressions=regressions,
            duplicates=duplicates,
            out_of_order_ahead=out_of_order_ahead,
            next_expected=expected,
            buffer_overflow=overflow,
        )


@dataclass(frozen=True)
class _ThrottleVerdict:
    """Internal result of the outbound half of an audit."""

    status: str
    notes: str
    peak_rate: float
    trailing_rate: float
    newest_age: Optional[float]
    future_dated: int


@dataclass(frozen=True)
class _SequenceVerdict:
    """Internal result of the inbound half of an audit."""

    status: str
    notes: str
    gaps: List[SequenceGapDetail]
    regressions: List[SequenceRegressionDetail]
    duplicates: int
    out_of_order_ahead: int
    next_expected: int
    buffer_overflow: bool


def _peak_window_count(sorted_timestamps: Sequence[float], window: float) -> int:
    """
    Largest number of messages falling in any half-open window ``(t - window, t]``.

    Two-pointer sweep, O(n) over already-sorted timestamps. Half-open so a message exactly
    ``window`` seconds before the window end sits outside it, matching "in the last
    ``window`` seconds". Only windows ending on an actual message need testing -- sliding
    a window further right can only drop messages, never add them.

    Timestamps are epoch seconds in float64, so around 1.8e9 the representable spacing is
    roughly 0.24 microseconds. Messages sitting closer than that to a window edge may fall
    on either side. This is immaterial for millisecond-and-coarser windows; for
    nanosecond-resolution work, pass timestamps rebased to the start of the log.
    """
    peak = 0
    left = 0
    for right, end in enumerate(sorted_timestamps):
        while sorted_timestamps[left] <= end - window:
            left += 1
        peak = max(peak, right - left + 1)
    return peak


def _missing_runs(expected: int, buffered: Set[int]) -> List[Tuple[int, int, int]]:
    """
    Contiguous runs of sequence numbers still missing, given the expected counter and the
    numbers received ahead of it.

    Returns ``(start, end, first_received_above_run)`` triples, inclusive of both
    endpoints. Iterates the buffered numbers rather than the integer range, so a gap of
    ten million costs one step, not ten million.
    """
    runs: List[Tuple[int, int, int]] = []
    cursor = expected
    for seq in sorted(buffered):
        if seq > cursor:
            runs.append((cursor, seq - 1, seq))
        cursor = seq + 1
    return runs

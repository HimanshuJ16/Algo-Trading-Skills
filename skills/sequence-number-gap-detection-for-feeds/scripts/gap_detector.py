"""
sequence-number-gap-detection-for-feeds: multi-stream sequence-continuity tracking,
out-of-order buffering, and consumer gating for market data feeds.

One :class:`SequenceGapDetector` instance tracks **many independent sequence spaces**
at once -- one per WebSocket stream, per vendor channel, per exchange channel -- and
answers a single question for each of them: *is the state I built from this stream
still guaranteed to match the publisher's?* When the answer is no, it says so loudly
and refuses to hand out frames until the stream is repaired.

Two sequencing models are supported, because feeds do not agree on one:

* **Point sequencing** -- each frame carries one sequence number that advances by one.
  Nasdaq MoldUDP64 numbers *messages* this way (the packet header carries the sequence
  of the first message plus a Message Count, and following messages are implicitly
  numbered sequentially); CME MDP 3.0 numbers *packets* per channel.
* **Range sequencing** -- each frame covers an inclusive span of update IDs. Binance
  diff-depth events carry ``U`` (first update ID) and ``u`` (final update ID), and the
  documented continuity rule is that each event's ``pu`` equals the previous event's
  ``u``. Pass ``last_sequence_id`` and the detector advances to ``u + 1``.

The recovery model is the caller's, and it is not the same everywhere. A venue with a
retransmission service (MoldUDP64 Re-request Server, CME TCP replay) can backfill the
missing frames -- feed them to :meth:`SequenceGapDetector.reconcile_missing_frames`.
A venue without one (Binance: "otherwise initialize the process from step 3") can only
re-snapshot -- call :meth:`SequenceGapDetector.resynchronize`.

Scope boundary: this module owns no sockets, no timers and no transport. Redundant
A/B multicast line arbitration, the arbitration window that separates a *delayed*
datagram from a *lost* one, and escalation through a venue's recovery tiers belong to
``exchange-multicast-feed-handling``, which holds one packet sequence space per
instance. Use that for a co-located multicast handler; use this for the general
multi-stream case, and for the range-sequenced streams it cannot express.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "FeedSyncState",
    "FrameDisposition",
    "FeedFrame",
    "GapDetectionResult",
    "ReconciliationResult",
    "StreamStats",
    "FeedResetRequiredError",
    "SequenceGapDetector",
]

#: A backward jump of at least this many sequence numbers is reported as a suspected
#: stream restart rather than swallowed as a duplicate. Not a venue constant: CME MDP
#: 3.0 resets MsgSeqNum weekly and on a Channel Reset, and a MoldUDP64 restart opens a
#: new Session with its own numbering, but neither publishes a "safe" threshold.
DEFAULT_SEQUENCE_RESET_THRESHOLD = 1_000_000


class FeedSyncState(Enum):
    """Whether state built from a stream can still be trusted."""

    #: Every sequence up to ``expected - 1`` has been delivered in order.
    SYNCED = "SYNCED"
    #: A gap is open and no backfill has been applied to it yet. Frames beyond the
    #: gap are held, not applied.
    DIRTY_SYNC_PENDING = "DIRTY_SYNC_PENDING"
    #: A backfill round has been applied through :meth:`reconcile_missing_frames` and
    #: the gap is still not closed -- recovery is under way and incomplete. Distinct
    #: from ``DIRTY_SYNC_PENDING`` so a monitor can tell "loss detected, nothing done
    #: about it" from "loss detected, recovery in flight".
    RECOVERING = "RECOVERING"
    #: Latched. The stream can no longer be repaired by backfill -- the out-of-order
    #: buffer overflowed, or the publisher appears to have restarted its numbering.
    #: Cleared only by :meth:`SequenceGapDetector.resynchronize` against a snapshot.
    RESET_REQUIRED = "RESET_REQUIRED"


#: States in which downstream consumers may act on the reconstructed state. Kept as a
#: single definition so the trading gate cannot drift from the state machine.
_TRADING_AUTHORIZED_STATES = frozenset({FeedSyncState.SYNCED})


class FrameDisposition(Enum):
    """What the detector did with one ingested frame."""

    #: Applied in order and returned in ``processed_frames``.
    PROCESSED = "PROCESSED"
    #: A range frame straddling the expected sequence: part already seen, part new.
    #: Applied. This is the shape of Binance's first post-snapshot depth event.
    PARTIAL_OVERLAP = "PARTIAL_OVERLAP"
    #: Ahead of the expected sequence; held for re-sequencing behind an open gap.
    BUFFERED = "BUFFERED"
    #: Entirely at or below sequences already delivered -- a retransmission echo, a
    #: duplicated datagram, or a frame already sitting in the buffer.
    DUPLICATE = "DUPLICATE"
    #: Ahead of the expected sequence but the buffer is full. Dropped, and the stream
    #: is latched ``RESET_REQUIRED``.
    DROPPED_BUFFER_FULL = "DROPPED_BUFFER_FULL"
    #: Far enough below the expected sequence to look like a restart. Not applied.
    RESET_SUSPECTED = "RESET_SUSPECTED"
    #: The stream is latched ``RESET_REQUIRED``; nothing is applied until it is
    #: resynchronized against a snapshot.
    DROPPED_RESET_REQUIRED = "DROPPED_RESET_REQUIRED"


@dataclass(frozen=True)
class FeedFrame:
    """One sequenced unit of a feed, with an opaque payload this module never reads.

    Args:
        stream: Identifier of the **sequence space**, compared exactly. On MoldUDP64
            and CME MDP 3.0 this is the channel or session, *not* the instrument:
            those feeds number one sequence per channel, so keying per symbol makes
            every message look like a gap. On a per-symbol WebSocket feed it is that
            symbol's stream. Normalize case before constructing; this module does not.
        sequence_id: First sequence number the frame carries.
        payload: Opaque body. Never inspected.
        last_sequence_id: Final sequence number the frame carries, inclusive, for
            range-sequenced feeds (Binance ``u``). ``None`` means a single sequence.
    """

    stream: str
    sequence_id: int
    payload: Any
    last_sequence_id: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.stream, str) or not self.stream:
            raise ValueError("stream must be a non-empty string")
        _validate_sequence(self.sequence_id, "sequence_id")
        if self.last_sequence_id is not None:
            _validate_sequence(self.last_sequence_id, "last_sequence_id")
            if self.last_sequence_id < self.sequence_id:
                raise ValueError(
                    f"last_sequence_id ({self.last_sequence_id}) must be >= "
                    f"sequence_id ({self.sequence_id})"
                )

    @property
    def final_sequence_id(self) -> int:
        """Highest sequence number this frame carries, inclusive."""
        return self.sequence_id if self.last_sequence_id is None else self.last_sequence_id

    @property
    def sequence_span(self) -> int:
        """How many sequence numbers this frame consumes."""
        return self.final_sequence_id - self.sequence_id + 1


@dataclass(frozen=True)
class GapDetectionResult:
    """Outcome of one :meth:`SequenceGapDetector.ingest_frame` or heartbeat call."""

    stream: str
    state: FeedSyncState
    disposition: FrameDisposition
    is_gap_detected: bool
    #: The sequence runs that are actually missing, inclusive, already-buffered spans
    #: excluded. Empty when nothing is outstanding. Request *these*, not the whole
    #: span: venue recovery capacity is capped, and re-requesting frames already held
    #: spends it for nothing.
    missing_ranges: Tuple[Tuple[int, int], ...]
    #: Frames released in sequence order, safe to apply to downstream state.
    processed_frames: Tuple[FeedFrame, ...]
    message: str

    @property
    def is_trading_authorized(self) -> bool:
        """Whether state built from this stream may be traded on right now."""
        return self.state in _TRADING_AUTHORIZED_STATES

    @property
    def missing_sequence_count(self) -> int:
        """Total sequence numbers outstanding across :attr:`missing_ranges`."""
        return sum(end - start + 1 for start, end in self.missing_ranges)


@dataclass(frozen=True)
class ReconciliationResult:
    """Outcome of applying backfill frames to an open gap.

    ``is_synced`` is the field that matters. Partial recovery is the *normal* case,
    not an exception: MoldUDP64 returns only the messages that completely fit one UDP
    packet and states further requests are needed for the remainder, and CME caps a
    TCP replay request. A caller that assumes one backfill round closes the gap will
    resume trading on a stream that is still broken.
    """

    stream: str
    state: FeedSyncState
    processed_frames: Tuple[FeedFrame, ...]
    applied_count: int
    duplicate_count: int
    remaining_ranges: Tuple[Tuple[int, int], ...]
    is_synced: bool
    message: str

    @property
    def remaining_sequence_count(self) -> int:
        """Total sequence numbers still missing after this backfill round."""
        return sum(end - start + 1 for start, end in self.remaining_ranges)


@dataclass(frozen=True)
class StreamStats:
    """Monitoring snapshot for one stream.

    ``outstanding_missing_count`` is the metric
    ``graduated-response-to-data-quality-degradation`` consumes as
    ``missing_sequence_count``.
    """

    stream: str
    state: FeedSyncState
    expected_sequence: int
    buffered_frames: int
    outstanding_missing_count: int
    frames_processed: int
    gaps_detected: int
    duplicates_suppressed: int
    frames_dropped_buffer_full: int
    resets_suspected: int

    @property
    def is_trading_authorized(self) -> bool:
        """Whether state built from this stream may be traded on right now."""
        return self.state in _TRADING_AUTHORIZED_STATES


class FeedResetRequiredError(RuntimeError):
    """Raised when backfill is attempted on a stream that only a snapshot can repair."""


def _validate_sequence(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} must be non-negative, got {value}")


@dataclass
class _StreamState:
    """Mutable per-stream tracking. Internal; read it through :class:`StreamStats`."""

    expected: int
    state: FeedSyncState = FeedSyncState.SYNCED
    #: Out-of-order frames held behind an open gap, keyed by first sequence number.
    buffer: Dict[int, FeedFrame] = field(default_factory=dict)
    #: Highest ``next expected`` the publisher has claimed, from a frame or a
    #: heartbeat. Loss at the tail of a stream is outstanding even with an empty
    #: buffer, so the state machine cannot rely on the buffer alone.
    high_water: int = 0
    frames_processed: int = 0
    gaps_detected: int = 0
    duplicates_suppressed: int = 0
    frames_dropped_buffer_full: int = 0
    resets_suspected: int = 0


class SequenceGapDetector:
    """
    Tracks sequence continuity across many independent feed streams, buffers
    out-of-order frames behind an open gap, and withholds every frame whose
    predecessors have not arrived.

    The design rule throughout is that a stream is guilty until proven contiguous.
    Nothing is applied speculatively, nothing missing is silently absorbed, and the
    two conditions backfill cannot repair -- a buffer overflow and a publisher restart
    -- latch :attr:`FeedSyncState.RESET_REQUIRED` rather than degrading quietly.

    Not thread-safe. One instance may hold many streams, but concurrent ingestion of
    the *same* stream from multiple threads must be serialized by the caller.
    """

    def __init__(
        self,
        max_buffer_size: int = 1000,
        *,
        sequence_reset_threshold: int = DEFAULT_SEQUENCE_RESET_THRESHOLD,
    ) -> None:
        """
        Args:
            max_buffer_size: Cap on out-of-order frames held per stream. An
                engineering guard against a prolonged outage exhausting memory, not a
                venue constant -- size it from the stream's message rate times the
                longest recovery you intend to survive. Overflow drops the frame and
                latches ``RESET_REQUIRED``, because a stream whose future is being
                discarded can no longer be repaired by backfilling its past.
            sequence_reset_threshold: A frame this far *below* the expected sequence
                is reported ``RESET_SUSPECTED`` instead of being discarded as a
                duplicate. From sequence numbers alone a restart and a stale
                retransmission echo are indistinguishable, so the detector refuses the
                frame and leaves the decision to the caller, who can see the venue's
                in-band restart signal. Filing every low sequence under "duplicate" is
                how a handler goes permanently deaf after a weekly reset.
        """
        _validate_sequence(max_buffer_size, "max_buffer_size")
        if max_buffer_size < 1:
            raise ValueError("max_buffer_size must be >= 1")
        _validate_sequence(sequence_reset_threshold, "sequence_reset_threshold")
        if sequence_reset_threshold < 1:
            raise ValueError("sequence_reset_threshold must be >= 1")

        self.max_buffer_size = max_buffer_size
        self.sequence_reset_threshold = sequence_reset_threshold
        self._streams: Dict[str, _StreamState] = {}

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest_frame(self, frame: FeedFrame) -> GapDetectionResult:
        """Classify one frame and release whatever is now contiguous.

        Frames are released only when every sequence before them has been delivered,
        so ``processed_frames`` is always safe to apply in the order given.
        """
        if not isinstance(frame, FeedFrame):
            raise TypeError(f"frame must be a FeedFrame, got {type(frame).__name__}")

        stream = frame.stream
        first = frame.sequence_id
        last = frame.final_sequence_id
        st = self._streams.get(stream)

        # First sight of a stream: adopt it as the baseline. Anything the publisher
        # sent before this frame is neither recoverable nor detectable -- seed the
        # expected sequence with resynchronize() first when it is known (a restarting
        # MoldUDP64 client is configured with a session and next expected sequence).
        if st is None:
            st = _StreamState(expected=last + 1, high_water=last + 1)
            st.frames_processed = 1
            self._streams[stream] = st
            logger.info(
                "Feed baseline established for stream %s at sequence %d (next expected %d).",
                stream, first, st.expected,
            )
            return self._result(
                stream, st, FrameDisposition.PROCESSED, (frame,),
                f"Baseline established for {stream} at sequence {first}.",
            )

        # A latched reset is not repairable by anything arriving on the wire.
        if st.state is FeedSyncState.RESET_REQUIRED:
            return self._result(
                stream, st, FrameDisposition.DROPPED_RESET_REQUIRED, (),
                f"Stream {stream} requires snapshot resynchronization; frame {first} "
                f"not applied.",
            )

        expected = st.expected
        # Captured before the high-water mark moves, so one gap is counted once
        # however many out-of-order frames arrive behind it.
        was_clean = not self._outstanding_ranges(st)
        st.high_water = max(st.high_water, last + 1)

        # Case 1: contiguous, or a range frame straddling the boundary. The straddle
        # is Binance's documented first post-snapshot event: U <= lastUpdateId <= u.
        if first <= expected <= last:
            disposition = (
                FrameDisposition.PROCESSED if first == expected
                else FrameDisposition.PARTIAL_OVERLAP
            )
            processed: List[FeedFrame] = [frame]
            st.expected = last + 1
            processed.extend(self._drain(st))
            st.frames_processed += len(processed)
            st.state = self._settle_state(st)
            return self._result(
                stream, st, disposition, tuple(processed),
                f"Released {len(processed)} contiguous frame(s) for {stream}; "
                f"next expected {st.expected}.",
            )

        # Case 2: entirely behind the stream -- duplicate, retransmission echo, or a
        # publisher that restarted its numbering.
        if last < expected:
            if expected - last >= self.sequence_reset_threshold:
                st.resets_suspected += 1
                st.state = FeedSyncState.RESET_REQUIRED
                logger.error(
                    "Suspected sequence restart on stream %s: frame [%d..%d] is %d "
                    "below expected %d. Confirm the venue's restart signal, then "
                    "resynchronize.",
                    stream, first, last, expected - last, expected,
                )
                return self._result(
                    stream, st, FrameDisposition.RESET_SUSPECTED, (),
                    f"Suspected restart on {stream}: frame [{first}..{last}] is "
                    f"{expected - last} below expected {expected}.",
                )
            st.duplicates_suppressed += 1
            logger.debug(
                "Duplicate/stale frame suppressed on stream %s: [%d..%d] < expected %d.",
                stream, first, last, expected,
            )
            return self._result(
                stream, st, FrameDisposition.DUPLICATE, (),
                f"Stale frame [{first}..{last}] ignored for {stream}.",
            )

        # Case 3: ahead of the stream -- a gap.
        buffered = st.buffer.get(first)
        if buffered is not None and buffered.final_sequence_id >= last:
            # Only a true duplicate if the held copy covers at least as much. A wider
            # range arriving over a narrower one carries sequences not yet held, so it
            # replaces it rather than being discarded.
            st.duplicates_suppressed += 1
            return self._result(
                stream, st, FrameDisposition.DUPLICATE, (),
                f"Frame {first} already buffered for {stream}.",
            )

        if buffered is None and len(st.buffer) >= self.max_buffer_size:
            st.frames_dropped_buffer_full += 1
            st.state = FeedSyncState.RESET_REQUIRED
            logger.error(
                "Out-of-order buffer full (%d frames) on stream %s; dropping frame %d "
                "and latching RESET_REQUIRED. Backfill can no longer repair this "
                "stream -- resynchronize from a snapshot.",
                self.max_buffer_size, stream, first,
            )
            return self._result(
                stream, st, FrameDisposition.DROPPED_BUFFER_FULL, (),
                f"Buffer full for {stream}; frame {first} dropped and stream latched "
                f"RESET_REQUIRED.",
            )

        st.buffer[first] = frame
        st.state = self._settle_state(st)
        if was_clean:
            st.gaps_detected += 1
        missing = self._missing_ranges(st, first - 1)
        logger.warning(
            "SEQUENCE GAP on stream %s: expected %d, received [%d..%d]. Missing %s.",
            stream, expected, first, last, self._format_ranges(missing),
        )
        return self._result(
            stream, st, FrameDisposition.BUFFERED, (),
            f"Gap on {stream}: expected {expected}, received {first}. Missing "
            f"{self._format_ranges(missing)}.",
            missing_ranges=missing,
        )

    def observe_heartbeat(
        self, stream: str, next_expected_sequence: int
    ) -> GapDetectionResult:
        """Detect loss at the tail of a stream from a publisher heartbeat.

        Loss of the *last* frames before a quiet period is invisible to
        :meth:`ingest_frame` -- there is no later frame to expose it. MoldUDP64 exists
        partly for this: heartbeats are sent "so receivers can sense packet loss even
        during times of low traffic" and carry the next expected sequence number.

        Args:
            stream: Sequence space the heartbeat belongs to.
            next_expected_sequence: The publisher's next sequence number. Seeds the
                baseline on an unknown stream, exactly as a restarting MoldUDP64
                client is configured with a session and next expected sequence.
        """
        if not isinstance(stream, str) or not stream:
            raise ValueError("stream must be a non-empty string")
        _validate_sequence(next_expected_sequence, "next_expected_sequence")

        st = self._streams.get(stream)
        if st is None:
            st = _StreamState(
                expected=next_expected_sequence, high_water=next_expected_sequence
            )
            self._streams[stream] = st
            logger.info(
                "Feed baseline seeded from heartbeat for stream %s at next expected %d.",
                stream, next_expected_sequence,
            )
            return self._result(
                stream, st, FrameDisposition.PROCESSED, (),
                f"Baseline seeded for {stream} at next expected {next_expected_sequence}.",
            )

        if st.state is FeedSyncState.RESET_REQUIRED:
            return self._result(
                stream, st, FrameDisposition.DROPPED_RESET_REQUIRED, (),
                f"Stream {stream} requires snapshot resynchronization.",
            )

        if next_expected_sequence == st.expected:
            return self._result(
                stream, st, FrameDisposition.PROCESSED, (),
                f"Heartbeat confirms {stream} contiguous at {st.expected}.",
            )

        if next_expected_sequence < st.expected:
            # The publisher is behind us. Within the threshold this is a benign race
            # with an in-flight heartbeat; beyond it, the publisher restarted.
            if st.expected - next_expected_sequence >= self.sequence_reset_threshold:
                st.resets_suspected += 1
                st.state = FeedSyncState.RESET_REQUIRED
                logger.error(
                    "Heartbeat on stream %s reports next expected %d, %d below local "
                    "expected %d. Suspected publisher restart.",
                    stream, next_expected_sequence,
                    st.expected - next_expected_sequence, st.expected,
                )
                return self._result(
                    stream, st, FrameDisposition.RESET_SUSPECTED, (),
                    f"Heartbeat on {stream} reports next expected "
                    f"{next_expected_sequence}, far below local expected {st.expected}.",
                )
            logger.debug(
                "Stale heartbeat on stream %s: next expected %d < local expected %d.",
                stream, next_expected_sequence, st.expected,
            )
            return self._result(
                stream, st, FrameDisposition.DUPLICATE, (),
                f"Stale heartbeat ignored for {stream}.",
            )

        was_clean = not self._outstanding_ranges(st)
        st.high_water = max(st.high_water, next_expected_sequence)
        missing = self._missing_ranges(st, next_expected_sequence - 1)
        if missing:
            if was_clean:
                st.gaps_detected += 1
            st.state = self._settle_state(st)
            logger.warning(
                "SEQUENCE GAP on stream %s revealed by heartbeat: expected %d, "
                "publisher next expected %d. Missing %s.",
                stream, st.expected, next_expected_sequence,
                self._format_ranges(missing),
            )
            return self._result(
                stream, st, FrameDisposition.BUFFERED, (),
                f"Heartbeat on {stream} reveals missing {self._format_ranges(missing)}.",
                missing_ranges=missing,
            )
        return self._result(
            stream, st, FrameDisposition.PROCESSED, (),
            f"Heartbeat on {stream} covered entirely by buffered frames.",
        )

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def reconcile_missing_frames(
        self, stream: str, missing_frames: Sequence[FeedFrame]
    ) -> ReconciliationResult:
        """Apply backfill frames fetched from a retransmission or replay service.

        Frames are applied in sequence order, and any buffered frames the backfill
        makes contiguous are drained behind them. The gap is **not** assumed closed:
        check :attr:`ReconciliationResult.is_synced` before resuming, because venue
        recovery services routinely return less than was asked for.

        Raises:
            TypeError: ``missing_frames`` contains something other than a FeedFrame.
            ValueError: The stream is unknown, or a frame belongs to another stream.
                Applying another stream's frames here would corrupt both sequence
                spaces silently.
            FeedResetRequiredError: The stream is latched ``RESET_REQUIRED``. Backfill
                cannot repair a stream whose future frames were dropped or whose
                publisher restarted; call :meth:`resynchronize` against a snapshot.
        """
        if not isinstance(stream, str) or not stream:
            raise ValueError("stream must be a non-empty string")
        st = self._streams.get(stream)
        if st is None:
            raise ValueError(f"unknown stream {stream!r}: nothing to reconcile")
        if st.state is FeedSyncState.RESET_REQUIRED:
            raise FeedResetRequiredError(
                f"stream {stream!r} is latched RESET_REQUIRED; backfill cannot repair "
                f"it -- resynchronize from a snapshot instead"
            )

        frames = list(missing_frames)
        for mf in frames:
            if not isinstance(mf, FeedFrame):
                raise TypeError(
                    f"missing_frames must contain FeedFrame, got {type(mf).__name__}"
                )
            if mf.stream != stream:
                raise ValueError(
                    f"frame for stream {mf.stream!r} passed to "
                    f"reconcile_missing_frames({stream!r})"
                )

        st.state = FeedSyncState.RECOVERING
        processed: List[FeedFrame] = []
        applied = 0
        duplicates = 0
        for mf in sorted(frames, key=lambda f: (f.sequence_id, f.final_sequence_id)):
            res = self.ingest_frame(mf)
            processed.extend(res.processed_frames)
            if res.disposition in (
                FrameDisposition.PROCESSED,
                FrameDisposition.PARTIAL_OVERLAP,
                FrameDisposition.BUFFERED,
            ):
                applied += 1
            elif res.disposition is FrameDisposition.DUPLICATE:
                duplicates += 1
            if st.state is FeedSyncState.RESET_REQUIRED:
                # A backfill batch large enough to overflow the buffer, or one that
                # tripped restart detection: stop rather than half-apply it.
                break

        remaining = self._outstanding_ranges(st)
        if st.state is FeedSyncState.RESET_REQUIRED:
            return ReconciliationResult(
                stream=stream,
                state=st.state,
                processed_frames=tuple(processed),
                applied_count=applied,
                duplicate_count=duplicates,
                remaining_ranges=remaining,
                is_synced=False,
                message=(
                    f"Reconciliation of {stream} aborted: stream latched "
                    f"RESET_REQUIRED. Resynchronize from a snapshot."
                ),
            )

        st.state = self._settle_state(st)
        is_synced = st.state is FeedSyncState.SYNCED
        if is_synced:
            logger.info(
                "Reconciled %d backfill frame(s) for stream %s; contiguous at %d.",
                applied, stream, st.expected,
            )
        else:
            logger.warning(
                "Partial recovery on stream %s: %d frame(s) applied, still missing %s. "
                "Request the remainder before resuming.",
                stream, applied, self._format_ranges(remaining),
            )
        return ReconciliationResult(
            stream=stream,
            state=st.state,
            processed_frames=tuple(processed),
            applied_count=applied,
            duplicate_count=duplicates,
            remaining_ranges=remaining,
            is_synced=is_synced,
            message=(
                f"Reconciled {applied} frame(s) for {stream}; next expected "
                f"{st.expected}."
                if is_synced else
                f"Partial recovery for {stream}: still missing "
                f"{self._format_ranges(remaining)}."
            ),
        )

    def resynchronize(self, stream: str, next_sequence_id: int) -> None:
        """Reset a stream to a known-good point taken from a snapshot.

        This is the only way out of ``RESET_REQUIRED``, and the only recovery path on
        a feed with no retransmission service -- Binance's documented response to a
        continuity break is to re-fetch the depth snapshot and restart. It is also how
        a client seeds a stream it has not yet seen a frame on.

        Everything buffered is discarded: those frames are relative to the old
        sequence space and downstream state is about to be rebuilt from the snapshot.

        Args:
            stream: Sequence space to reset.
            next_sequence_id: First sequence number to expect after the snapshot. For
                a Binance depth snapshot this is ``lastUpdateId + 1``.
        """
        if not isinstance(stream, str) or not stream:
            raise ValueError("stream must be a non-empty string")
        _validate_sequence(next_sequence_id, "next_sequence_id")

        st = self._streams.get(stream)
        if st is None:
            self._streams[stream] = _StreamState(
                expected=next_sequence_id, high_water=next_sequence_id
            )
            discarded = 0
        else:
            discarded = len(st.buffer)
            st.buffer.clear()
            st.expected = next_sequence_id
            # The snapshot supersedes anything previously known to be outstanding;
            # a stale high-water mark would keep the stream permanently un-synced.
            st.high_water = next_sequence_id
            st.state = FeedSyncState.SYNCED
        logger.info(
            "Stream %s resynchronized to next expected %d (%d buffered frame(s) "
            "discarded).",
            stream, next_sequence_id, discarded,
        )

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_state(self, stream: str) -> Optional[FeedSyncState]:
        """Current sync state, or ``None`` if the stream has never been seen."""
        st = self._streams.get(stream)
        return st.state if st is not None else None

    def is_trading_authorized(self, stream: str) -> bool:
        """Whether downstream state from this stream may be traded on.

        An unknown stream is **not** authorized: no frame has established that the
        local state corresponds to anything the publisher sent.
        """
        st = self._streams.get(stream)
        return st is not None and st.state in _TRADING_AUTHORIZED_STATES

    def stats(self, stream: str) -> StreamStats:
        """Monitoring snapshot for one stream.

        Raises:
            KeyError: The stream has never been seen.
        """
        st = self._streams[stream]
        missing = self._outstanding_ranges(st)
        return StreamStats(
            stream=stream,
            state=st.state,
            expected_sequence=st.expected,
            buffered_frames=len(st.buffer),
            outstanding_missing_count=sum(end - start + 1 for start, end in missing),
            frames_processed=st.frames_processed,
            gaps_detected=st.gaps_detected,
            duplicates_suppressed=st.duplicates_suppressed,
            frames_dropped_buffer_full=st.frames_dropped_buffer_full,
            resets_suspected=st.resets_suspected,
        )

    def tracked_streams(self) -> Tuple[str, ...]:
        """Every stream this detector currently tracks."""
        return tuple(self._streams)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _drain(self, st: _StreamState) -> List[FeedFrame]:
        """Release buffered frames the new expected sequence has made contiguous.

        Frames wholly behind the expected sequence are purged as they are found: a
        backfill range can overtake a buffered frame, and leaving it in place would
        consume the buffer bound forever. The straddle scan is linear in the buffer,
        which is bounded by ``max_buffer_size``.
        """
        released: List[FeedFrame] = []
        while st.buffer:
            frame = st.buffer.pop(st.expected, None)
            if frame is None:
                frame = self._pop_straddling(st)
                if frame is None:
                    break
            released.append(frame)
            st.expected = frame.final_sequence_id + 1
        self._purge_stale(st)
        return released

    @staticmethod
    def _pop_straddling(st: _StreamState) -> Optional[FeedFrame]:
        """Pop a buffered range frame covering the expected sequence, if any."""
        for key, frame in st.buffer.items():
            if frame.sequence_id <= st.expected <= frame.final_sequence_id:
                del st.buffer[key]
                return frame
        return None

    @staticmethod
    def _purge_stale(st: _StreamState) -> None:
        stale = [k for k, f in st.buffer.items() if f.final_sequence_id < st.expected]
        for key in stale:
            del st.buffer[key]
            st.duplicates_suppressed += 1

    @staticmethod
    def _missing_ranges(
        st: _StreamState, upto_inclusive: int
    ) -> Tuple[Tuple[int, int], ...]:
        """Sequence runs in ``[expected .. upto_inclusive]`` that are neither delivered
        nor sitting in the out-of-order buffer."""
        if upto_inclusive < st.expected:
            return ()
        gaps: List[Tuple[int, int]] = []
        cursor = st.expected
        for frame in sorted(st.buffer.values(), key=lambda f: f.sequence_id):
            if frame.final_sequence_id < cursor:
                continue
            if frame.sequence_id > upto_inclusive:
                break
            if frame.sequence_id > cursor:
                gaps.append((cursor, frame.sequence_id - 1))
            cursor = frame.final_sequence_id + 1
            if cursor > upto_inclusive:
                return tuple(gaps)
        gaps.append((cursor, upto_inclusive))
        return tuple(gaps)

    @classmethod
    def _outstanding_ranges(cls, st: _StreamState) -> Tuple[Tuple[int, int], ...]:
        """Every sequence run currently known to be missing on this stream.

        Bounded by the highest sequence the publisher has claimed -- from a buffered
        frame *or* from a heartbeat -- so loss at the tail of a stream stays
        outstanding even though the buffer is empty.
        """
        return cls._missing_ranges(st, max(cls._highest_buffered(st), st.high_water - 1))

    @classmethod
    def _settle_state(cls, st: _StreamState) -> FeedSyncState:
        """Recompute a stream's state after its expected sequence or buffer moved.

        ``RECOVERING`` is preserved while a gap is still open so a monitor can tell a
        gap nobody has acted on from one with backfill already in flight.
        """
        if not cls._outstanding_ranges(st):
            return FeedSyncState.SYNCED
        if st.state is FeedSyncState.RECOVERING:
            return FeedSyncState.RECOVERING
        return FeedSyncState.DIRTY_SYNC_PENDING

    @staticmethod
    def _highest_buffered(st: _StreamState) -> int:
        """Highest sequence held in the buffer, or ``expected - 1`` when it is empty."""
        if not st.buffer:
            return st.expected - 1
        return max(f.final_sequence_id for f in st.buffer.values())

    @staticmethod
    def _format_ranges(ranges: Tuple[Tuple[int, int], ...]) -> str:
        if not ranges:
            return "nothing"
        return ", ".join(f"[{start}..{end}]" for start, end in ranges)

    @staticmethod
    def _result(
        stream: str,
        st: _StreamState,
        disposition: FrameDisposition,
        processed: Tuple[FeedFrame, ...],
        message: str,
        missing_ranges: Tuple[Tuple[int, int], ...] = (),
    ) -> GapDetectionResult:
        return GapDetectionResult(
            stream=stream,
            state=st.state,
            disposition=disposition,
            is_gap_detected=bool(missing_ranges),
            missing_ranges=missing_ranges,
            processed_frames=processed,
            message=message,
        )

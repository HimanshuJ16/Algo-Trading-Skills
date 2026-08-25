"""
exchange-multicast-feed-handling: transport-agnostic A/B line arbitration,
out-of-order re-sequencing, and recovery escalation for exchange UDP multicast feeds.

This module owns the *sequencing* layer of a feed handler: it decides whether an
arriving datagram is new, a duplicate of the twin line, or evidence of a gap, and
when a gap has aged long enough to stop being "delayed" and start being "lost".
It deliberately owns no sockets and decodes no payloads -- see SKILL.md
"When NOT to Use".

Sequence-space note: CME MDP 3.0 and Eurex T7 number one sequence per *packet*
(message_count == 1). Nasdaq MoldUDP64 numbers *messages*: the packet header
carries the sequence number of the first message plus a Message Count, and the
following messages are implicitly numbered sequentially. Pass ``message_count``
so the handler advances the sequence space correctly on those feeds.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, replace
from enum import Enum
from typing import Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "MulticastChannel",
    "PacketDisposition",
    "GapState",
    "MulticastPacket",
    "SequenceGap",
    "RecoveryRequest",
    "MulticastHandlerResult",
    "RecoveryResult",
    "ExchangeMulticastFeedHandler",
]


class MulticastChannel(Enum):
    """Which line a datagram arrived on.

    ``RECOVERY`` covers every second-tier recovery transport, because the
    transport is venue-specific: CME MDP 3.0 uses a TCP replay session, Nasdaq
    MoldUDP64 uses a UDP *unicast* Request Packet to a Re-request Server, and
    Eurex T7 has no retransmission service at all (its fallback is the snapshot
    channel).
    """

    CHANNEL_A = "CHANNEL_A"
    CHANNEL_B = "CHANNEL_B"
    RECOVERY = "RECOVERY"


class PacketDisposition(Enum):
    """What the handler did with a datagram."""

    PROCESSED = "PROCESSED"
    #: Every sequence the packet carries is already past; the twin line or a
    #: network-level duplicate delivered it first.
    DUPLICATE = "DUPLICATE"
    #: MoldUDP64 recovery only: the packet straddles the expected sequence, so
    #: its leading messages are already processed. See ``first_new_message_index``.
    PARTIAL_OVERLAP = "PARTIAL_OVERLAP"
    #: Ahead of the expected sequence and held for re-sequencing.
    BUFFERED = "BUFFERED"
    #: Ahead of the expected sequence but the buffer is full; the feed can no
    #: longer be repaired by arbitration and needs a snapshot resynchronization.
    DROPPED_BUFFER_FULL = "DROPPED_BUFFER_FULL"
    #: Far enough behind the expected sequence to look like a stream restart
    #: rather than a duplicate. Not applied -- see ``ExchangeMulticastFeedHandler``.
    RESET_SUSPECTED = "RESET_SUSPECTED"


class GapState(Enum):
    """Lifecycle of the single outstanding sequence gap.

    The absence of a gap is expressed by ``pending_gap is None``, not by a member here.
    """

    #: Inside the arbitration window: the missing packets may still arrive on
    #: the other line, so no recovery has been requested yet.
    ARBITRATING = "ARBITRATING"
    #: The window elapsed, the twin never arrived, and a recovery request has
    #: been handed to the caller and is outstanding.
    RECOVERY_REQUESTED = "RECOVERY_REQUESTED"


@dataclass(frozen=True)
class MulticastPacket:
    """One datagram's sequencing metadata plus its opaque payload."""

    channel: MulticastChannel
    sequence_id: int
    payload: bytes
    timestamp: float
    message_count: int = 1

    @property
    def last_sequence_id(self) -> int:
        """Highest sequence number this packet carries (inclusive)."""
        return self.sequence_id + self.message_count - 1


@dataclass(frozen=True)
class SequenceGap:
    """A contiguous run of sequence numbers that has not arrived on either line."""

    start: int
    end: int
    #: Monotonic clock reading when the gap was first observed. Never reset while
    #: the gap stays open, so a burst of later packets cannot postpone recovery.
    detected_at: float
    state: GapState

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    def as_range(self) -> Tuple[int, int]:
        return (self.start, self.end)


@dataclass(frozen=True)
class RecoveryRequest:
    """Instruction to the caller to fetch a range over the venue's recovery transport."""

    start: int
    end: int
    #: Seconds the gap spent inside the arbitration window before being declared lost.
    arbitrated_for: float

    @property
    def length(self) -> int:
        return self.end - self.start + 1


@dataclass(frozen=True)
class MulticastHandlerResult:
    """Outcome of a single ``ingest_packet`` call."""

    processed_packets: List[MulticastPacket]
    disposition: PacketDisposition
    is_gap_detected: bool
    missing_range: Optional[Tuple[int, int]]
    source_channel: MulticastChannel
    message: str
    #: Set on the one call that crosses the arbitration window. ``None`` otherwise
    #: -- an open gap yields a request exactly once, never once per packet.
    recovery_request: Optional[RecoveryRequest] = None
    #: For ``PARTIAL_OVERLAP``: index of the first message in the payload the
    #: caller has not already processed. Always 0 for other dispositions.
    first_new_message_index: int = 0
    #: True once arbitration can no longer repair the stream and only a snapshot can.
    requires_resynchronization: bool = False


@dataclass(frozen=True)
class RecoveryResult:
    """Outcome of applying recovery packets to an outstanding gap."""

    processed_packets: List[MulticastPacket]
    is_gap_closed: bool
    outstanding_gap: Optional[Tuple[int, int]]
    message: str


class ExchangeMulticastFeedHandler:
    """Arbitrates redundant A/B multicast lines and escalates unrecoverable gaps.

    Recovery is a three-tier hierarchy and this class implements the first tier
    and the decision to escalate to the other two:

    1. **A/B line arbitration.** Both lines carry identical packets; the first
       copy to arrive is processed and the second discarded. CME strongly
       recommends processing both A and B incremental feeds, and Eurex advises
       joining both services.
    2. **Retransmission**, where the venue offers one. CME's TCP replay is
       capped (2000 packets per request, 24-hour window, one request per login/
       logout cycle) and CME states it is for small-scale recovery only.
       MoldUDP64's Re-request Server answers over UDP unicast and returns only
       as many messages as fit one datagram. Eurex T7 offers nothing here.
    3. **Snapshot resynchronization** -- CME Market Recovery (which CME
       recommends, with Natural Refresh, as the *primary* recovery option) or
       the Eurex depth-snapshot channel. Call :meth:`resynchronize`.

    A gap does not trigger recovery the instant it is seen. Multicast reorders
    packets routinely and the twin copy is normally microseconds behind on the
    other line, so every lost packet starts life as a delayed one. The handler
    holds the gap for ``arbitration_window_s`` first, and while that gap stays
    open later out-of-order packets extend its range without restarting the
    timer or issuing a second request.

    The class is not thread-safe. Run one instance per channel, on the thread
    that reads that channel's sockets; sequence spaces are per channel and
    sharing an instance across channels interleaves two unrelated sequences.
    """

    def __init__(
        self,
        initial_sequence: int = 1,
        *,
        arbitration_window_s: float,
        max_buffered_packets: int = 4096,
        sequence_reset_threshold: int = 1_000_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            initial_sequence: First sequence number expected. Must be >= 0.
            arbitration_window_s: How long to hold an out-of-order gap before
                declaring the packets lost. **Required, because there is no safe
                default**: the tolerable delay is a property of the venue and of
                the A/B path asymmetry in your own network. Eurex publishes the
                maximum expected recovery interval as ``MDRecoveryTimeInterval``
                (tag 2565) in the T7 RDI Product snapshot; for other venues,
                measure the A-versus-B arrival skew on your own cross-connects.
                Too short manufactures recovery requests out of ordinary
                reordering; too long leaves the book stale.
            max_buffered_packets: Cap on the re-sequencing buffer. An engineering
                guard, not a venue constant -- size it from your line rate times
                the arbitration window. Overflowing it sets
                :attr:`requires_resynchronization`.
            sequence_reset_threshold: A packet this far *below* the expected
                sequence is reported as ``RESET_SUSPECTED`` instead of being
                silently discarded as a duplicate. CME MDP 3.0 resets MsgSeqNum
                weekly and restarts it on a Channel Reset; MoldUDP64 starts a new
                Session. A handler that treats every low sequence as a duplicate
                goes permanently deaf after such a restart.
            clock: Monotonic time source, injectable for tests. Must not go
                backwards -- ``time.time()`` can step under NTP correction and
                would corrupt the arbitration window.
        """
        if not isinstance(initial_sequence, int) or isinstance(initial_sequence, bool):
            raise TypeError("initial_sequence must be an int")
        if initial_sequence < 0:
            raise ValueError("initial_sequence must be non-negative")
        if arbitration_window_s < 0:
            raise ValueError("arbitration_window_s must be non-negative")
        if max_buffered_packets < 1:
            raise ValueError("max_buffered_packets must be >= 1")
        if sequence_reset_threshold < 1:
            raise ValueError("sequence_reset_threshold must be >= 1")

        self.expected_sequence: int = initial_sequence
        self.arbitration_window_s: float = float(arbitration_window_s)
        self.max_buffered_packets: int = max_buffered_packets
        self.sequence_reset_threshold: int = sequence_reset_threshold
        self._clock = clock

        self.out_of_order_buffer: Dict[int, MulticastPacket] = {}
        self.pending_gap: Optional[SequenceGap] = None
        #: Every recovery range handed out, in order. One entry per gap, not one
        #: per out-of-order packet.
        self.recovery_requests: List[RecoveryRequest] = []
        #: Latched when arbitration can no longer repair the stream. Cleared only
        #: by :meth:`resynchronize`. While set, downstream book state is untrusted
        #: and quoting must stop.
        self.requires_resynchronization: bool = False

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest_packet(
        self,
        channel: MulticastChannel,
        sequence_id: int,
        payload: bytes,
        message_count: int = 1,
    ) -> MulticastHandlerResult:
        """Classify one datagram and re-sequence the stream.

        Args:
            channel: Line the datagram arrived on.
            sequence_id: Sequence number of the packet, or on MoldUDP64 of the
                first message in the packet.
            payload: Opaque packet body; this layer never decodes it.
            message_count: How many sequence numbers the packet consumes. 1 for
                CME MDP 3.0 and Eurex T7; the MoldUDP64 header's Message Count
                for Nasdaq-style feeds.
        """
        if not isinstance(channel, MulticastChannel):
            raise TypeError("channel must be a MulticastChannel")
        if not isinstance(sequence_id, int) or isinstance(sequence_id, bool):
            raise TypeError("sequence_id must be an int")
        if sequence_id < 0:
            raise ValueError("sequence_id must be non-negative")
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("payload must be bytes")
        if not isinstance(message_count, int) or isinstance(message_count, bool):
            raise TypeError("message_count must be an int")
        if message_count < 1:
            raise ValueError("message_count must be >= 1")

        now = self._clock()
        packet = MulticastPacket(
            channel=channel,
            sequence_id=sequence_id,
            payload=bytes(payload),
            timestamp=now,
            message_count=message_count,
        )
        last_id = packet.last_sequence_id

        # 1. Far below expected: a stream restart, not a duplicate. Never applied
        #    automatically -- accepting a large backward jump would let a stale
        #    replay rewind a live book. The caller confirms the restart from the
        #    venue's in-band signal (CME Channel Reset 35=X/269=J, or a new
        #    MoldUDP64 Session id) and calls reset_sequence().
        if last_id < self.expected_sequence - self.sequence_reset_threshold:
            logger.error(
                "SEQUENCE RESET SUSPECTED on %s: seq %d is %d below expected %d; "
                "confirm against the venue reset signal and call reset_sequence()",
                channel.value,
                sequence_id,
                self.expected_sequence - last_id,
                self.expected_sequence,
            )
            return self._result(
                packet,
                PacketDisposition.RESET_SUSPECTED,
                message=(
                    f"Sequence {sequence_id} far below expected "
                    f"{self.expected_sequence}; suspected stream restart, not applied."
                ),
            )

        # 2. Fully behind: the twin line or a network duplicate got here first.
        if last_id < self.expected_sequence:
            logger.debug(
                "Duplicate ignored: seq %d..%d on %s < expected %d",
                sequence_id,
                last_id,
                channel.value,
                self.expected_sequence,
            )
            return self._result(
                packet,
                PacketDisposition.DUPLICATE,
                message=f"Duplicate packet seq {sequence_id} ignored.",
            )

        # 3. In order, or straddling the expected sequence. A recovery packet can
        #    straddle because MoldUDP64 re-request responses are whole packets
        #    that need not align with the requested range.
        if sequence_id <= self.expected_sequence:
            first_new = self.expected_sequence - sequence_id
            disposition = (
                PacketDisposition.PARTIAL_OVERLAP if first_new > 0
                else PacketDisposition.PROCESSED
            )
            processed = [packet]
            self.expected_sequence = last_id + 1
            self._purge_below_expected(sequence_id)
            processed.extend(self._drain_buffer())
            self._refresh_gap(now)
            recovery = self._evaluate_gap(now)
            if first_new > 0:
                logger.info(
                    "Partial overlap on %s: seq %d..%d, first %d message(s) already processed",
                    channel.value,
                    sequence_id,
                    last_id,
                    first_new,
                )
            return self._result(
                packet,
                disposition,
                processed_packets=processed,
                message=f"Processed {len(processed)} in-order packet(s).",
                recovery_request=recovery,
                first_new_message_index=first_new,
            )

        # 4. Ahead of expected. Duplicate of something already held?
        if sequence_id in self.out_of_order_buffer:
            logger.debug(
                "Duplicate of buffered packet: seq %d on %s already held",
                sequence_id,
                channel.value,
            )
            recovery = self._evaluate_gap(now)
            return self._result(
                packet,
                PacketDisposition.DUPLICATE,
                is_gap_detected=self.pending_gap is not None,
                missing_range=self._pending_range(),
                message=f"Duplicate of buffered packet seq {sequence_id} ignored.",
                recovery_request=recovery,
            )

        # 5. Ahead of expected and new: buffer it and open or extend the gap.
        if len(self.out_of_order_buffer) >= self.max_buffered_packets:
            self.requires_resynchronization = True
            logger.error(
                "Re-sequencing buffer full (%d packets) on %s at seq %d; "
                "arbitration cannot repair this stream -- resynchronize from snapshot",
                self.max_buffered_packets,
                channel.value,
                sequence_id,
            )
            return self._result(
                packet,
                PacketDisposition.DROPPED_BUFFER_FULL,
                is_gap_detected=self.pending_gap is not None,
                missing_range=self._pending_range(),
                message=(
                    f"Buffer full at {self.max_buffered_packets} packets; seq "
                    f"{sequence_id} dropped. Snapshot resynchronization required."
                ),
            )

        self.out_of_order_buffer[sequence_id] = packet
        self._open_or_extend_gap(sequence_id - 1, now, channel)
        recovery = self._evaluate_gap(now)
        return self._result(
            packet,
            PacketDisposition.BUFFERED,
            is_gap_detected=True,
            missing_range=self._pending_range(),
            message=(
                f"Gap open {self._pending_range()}; seq {sequence_id} buffered "
                f"pending arbitration."
            ),
            recovery_request=recovery,
        )

    def poll_recovery(self, now: Optional[float] = None) -> Optional[RecoveryRequest]:
        """Age the outstanding gap without an arriving packet.

        Call this on a timer. After a loss the feed may go quiet -- especially at
        the end of a session, or if the loss was the last packet of a burst -- and
        a handler that only evaluates gaps on ingest would then never escalate.
        """
        return self._evaluate_gap(self._clock() if now is None else now)

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    def apply_recovery_packets(self, packets: List[MulticastPacket]) -> RecoveryResult:
        """Apply packets fetched over the venue's recovery transport.

        Recovered data is applied ahead of the queued real-time packets already
        held in the re-sequencing buffer, which is what CME instructs client
        systems to do. A recovery response that only partially fills the gap
        leaves the gap open and re-armed rather than closing it silently.
        """
        if not isinstance(packets, list):
            raise TypeError("packets must be a list of MulticastPacket")
        for pkt in packets:
            if not isinstance(pkt, MulticastPacket):
                raise TypeError("packets must contain MulticastPacket instances")

        processed_all: List[MulticastPacket] = []
        for pkt in sorted(packets, key=lambda p: p.sequence_id):
            res = self.ingest_packet(
                MulticastChannel.RECOVERY,
                pkt.sequence_id,
                pkt.payload,
                pkt.message_count,
            )
            processed_all.extend(res.processed_packets)

        # Re-arm a gap the recovery response only partially filled, so the
        # remainder can be requested again instead of sitting outstanding
        # forever behind a request that has already been answered.
        if self.pending_gap is not None and self.pending_gap.state is GapState.RECOVERY_REQUESTED:
            self.pending_gap = replace(
                self.pending_gap,
                state=GapState.ARBITRATING,
                detected_at=self._clock(),
            )

        outstanding = self._pending_range()
        is_closed = outstanding is None
        if is_closed:
            logger.info("Recovery closed the gap; %d packet(s) released.", len(processed_all))
            message = f"Gap closed; released {len(processed_all)} packet(s)."
        else:
            logger.warning(
                "Recovery incomplete: %d packet(s) released, gap %s still open.",
                len(processed_all),
                outstanding,
            )
            message = (
                f"Recovery incomplete; released {len(processed_all)} packet(s), "
                f"gap {outstanding} still open."
            )
        return RecoveryResult(
            processed_packets=processed_all,
            is_gap_closed=is_closed,
            outstanding_gap=outstanding,
            message=message,
        )

    def resynchronize(self, next_sequence: int) -> None:
        """Reset state from a snapshot, abandoning the outstanding gap.

        This is the third recovery tier: CME Market Recovery / Natural Refresh,
        or the Eurex depth-snapshot channel. Everything held for re-sequencing is
        discarded because the snapshot already reflects it.
        """
        if not isinstance(next_sequence, int) or isinstance(next_sequence, bool):
            raise TypeError("next_sequence must be an int")
        if next_sequence < 0:
            raise ValueError("next_sequence must be non-negative")

        logger.warning(
            "Resynchronizing from snapshot: expected sequence %d -> %d, discarding "
            "%d buffered packet(s), abandoning gap %s",
            self.expected_sequence,
            next_sequence,
            len(self.out_of_order_buffer),
            self._pending_range(),
        )
        self.expected_sequence = next_sequence
        self.out_of_order_buffer.clear()
        self.pending_gap = None
        self.requires_resynchronization = False

    def reset_sequence(self, next_sequence: int) -> None:
        """Restart the sequence space after a confirmed venue-side reset.

        Call only once the restart is confirmed from the venue's own signal --
        a CME Channel Reset (35=X, 269=J, 1180-ApplID), a weekly MsgSeqNum reset,
        or a new MoldUDP64 Session identifier. Confirming matters because from
        the sequence numbers alone a restart and a stale replayed packet look
        alike, and applying the wrong one rewinds a live book.
        """
        self.resynchronize(next_sequence)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _drain_buffer(self) -> List[MulticastPacket]:
        """Release buffered packets that are now contiguous with the stream."""
        released: List[MulticastPacket] = []
        while self.expected_sequence in self.out_of_order_buffer:
            pkt = self.out_of_order_buffer.pop(self.expected_sequence)
            released.append(pkt)
            start = self.expected_sequence
            self.expected_sequence = pkt.last_sequence_id + 1
            self._purge_below_expected(start)
        return released

    def _purge_below_expected(self, from_sequence: int) -> None:
        """Drop buffered packets the stream has advanced past.

        Only reachable when ``message_count`` > 1 lets one packet cover several
        buffered start sequences. The work is bounded by the smaller of the span
        and the buffer, so a malformed message count cannot turn this into an
        arbitrarily long loop on a per-packet path.
        """
        span = self.expected_sequence - from_sequence
        if span > len(self.out_of_order_buffer):
            for seq in [
                seq for seq in self.out_of_order_buffer if seq < self.expected_sequence
            ]:
                del self.out_of_order_buffer[seq]
            return
        for seq in range(from_sequence, self.expected_sequence):
            self.out_of_order_buffer.pop(seq, None)

    def _open_or_extend_gap(
        self, end: int, now: float, channel: MulticastChannel
    ) -> None:
        if self.pending_gap is None:
            self.pending_gap = SequenceGap(
                start=self.expected_sequence,
                end=end,
                detected_at=now,
                state=GapState.ARBITRATING,
            )
            logger.warning(
                "Sequence gap opened on %s: expected %d, missing [%d..%d]; "
                "arbitrating for %.6fs before declaring loss",
                channel.value,
                self.expected_sequence,
                self.pending_gap.start,
                end,
                self.arbitration_window_s,
            )
            return

        # Extend the existing gap. detected_at is deliberately preserved: Eurex
        # instructs that recovery already pending for a stream must not have its
        # timer reset by further out-of-sequence packets.
        if end > self.pending_gap.end:
            self.pending_gap = replace(self.pending_gap, end=end)

    def _refresh_gap(self, now: float) -> None:
        """Close the gap if the stream has passed it, else re-anchor its start.

        A partial fill narrows the gap without closing it. ``detected_at`` is
        left alone so the arbitration window keeps running from the original
        detection, not from the last packet to arrive.
        """
        gap = self.pending_gap
        if gap is None:
            return
        if self.expected_sequence > gap.end:
            logger.info(
                "Gap [%d..%d] closed after %.6fs in state %s",
                gap.start,
                gap.end,
                now - gap.detected_at,
                gap.state.value,
            )
            self.pending_gap = None
        elif self.expected_sequence > gap.start:
            self.pending_gap = replace(gap, start=self.expected_sequence)

    def _evaluate_gap(self, now: float) -> Optional[RecoveryRequest]:
        """Promote an arbitrating gap to a recovery request once, and only once."""
        gap = self.pending_gap
        if gap is None or gap.state is GapState.RECOVERY_REQUESTED:
            return None

        elapsed = now - gap.detected_at
        if elapsed < self.arbitration_window_s:
            return None

        request = RecoveryRequest(start=gap.start, end=gap.end, arbitrated_for=elapsed)
        self.pending_gap = replace(gap, state=GapState.RECOVERY_REQUESTED)
        self.recovery_requests.append(request)
        logger.warning(
            "Arbitration window elapsed (%.6fs): requesting recovery for [%d..%d] "
            "(%d sequence(s))",
            elapsed,
            request.start,
            request.end,
            request.length,
        )
        return request

    def _pending_range(self) -> Optional[Tuple[int, int]]:
        return None if self.pending_gap is None else self.pending_gap.as_range()

    def _result(
        self,
        packet: MulticastPacket,
        disposition: PacketDisposition,
        *,
        processed_packets: Optional[List[MulticastPacket]] = None,
        is_gap_detected: bool = False,
        missing_range: Optional[Tuple[int, int]] = None,
        message: str = "",
        recovery_request: Optional[RecoveryRequest] = None,
        first_new_message_index: int = 0,
    ) -> MulticastHandlerResult:
        if processed_packets is None:
            processed_packets = []
        if disposition in (PacketDisposition.PROCESSED, PacketDisposition.PARTIAL_OVERLAP):
            is_gap_detected = self.pending_gap is not None
            missing_range = self._pending_range()
        return MulticastHandlerResult(
            processed_packets=processed_packets,
            disposition=disposition,
            is_gap_detected=is_gap_detected,
            missing_range=missing_range,
            source_channel=packet.channel,
            message=message,
            recovery_request=recovery_request,
            first_new_message_index=first_new_message_index,
            requires_resynchronization=self.requires_resynchronization,
        )

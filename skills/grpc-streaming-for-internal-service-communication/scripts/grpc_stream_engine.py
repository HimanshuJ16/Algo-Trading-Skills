"""
grpc-streaming-for-internal-service-communication: gRPC HTTP/2 channel state machine,
binary tick frame codec, and protobuf-vs-JSON payload overhead benchmark.

Scope note: this module models the *channel lifecycle and payload economics* of a
gRPC bi-directional stream without depending on the `grpc` runtime or a compiled
`.proto`. `ProtobufTickFrame` uses fixed-width `struct` packing, which is NOT the
protobuf wire format; `proto3_wire_size()` computes the real proto3 encoded size
so the size claims in `SKILL.md` can be checked rather than assumed.
"""
from collections import deque
from dataclasses import dataclass
import json
import logging
import math
import struct
import time
from enum import Enum
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger(__name__)

# Reference reconnect backoff parameters from grpc/grpc `doc/connection-backoff.md`.
# gRPC additionally applies +/- JITTER (0.2) to each delay; the helper below returns
# the deterministic base delay so behaviour is reproducible in tests and backtests.
DEFAULT_INITIAL_BACKOFF_S = 1.0
DEFAULT_BACKOFF_MULTIPLIER = 1.6
DEFAULT_MAX_BACKOFF_S = 120.0
BACKOFF_JITTER_FRACTION = 0.2

# Fixed-width simulation layout: >QQIddd == 8+8+4+8+8+8 bytes (no alignment padding
# because of the '>' byte-order prefix).
TICK_FRAME_STRUCT = struct.Struct(">QQIddd")
TICK_FRAME_BYTES = TICK_FRAME_STRUCT.size

UINT32_MAX = 2 ** 32 - 1
UINT64_MAX = 2 ** 64 - 1

# proto3 field numbers for the equivalent TickFrame message. All are in 1..15, so
# every field key encodes as a single byte (protobuf.dev encoding guide).
_VARINT_FIELD_NUMBERS = (1, 2, 3)   # sequence_id, timestamp_ns, symbol_id
_FIXED64_FIELD_NUMBERS = (4, 5, 6)  # bid_price, ask_price, volume


class GRPCChannelState(Enum):
    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    READY = "READY"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    SHUTDOWN = "SHUTDOWN"


# Legal channel transitions per grpc/grpc `doc/connectivity-semantics-and-api.md`.
# SHUTDOWN is terminal: channels that enter it never leave it.
LEGAL_CHANNEL_TRANSITIONS: Dict[GRPCChannelState, frozenset] = {
    GRPCChannelState.IDLE: frozenset(
        {GRPCChannelState.CONNECTING, GRPCChannelState.SHUTDOWN}
    ),
    GRPCChannelState.CONNECTING: frozenset(
        {GRPCChannelState.READY, GRPCChannelState.TRANSIENT_FAILURE, GRPCChannelState.SHUTDOWN}
    ),
    GRPCChannelState.READY: frozenset(
        {GRPCChannelState.IDLE, GRPCChannelState.TRANSIENT_FAILURE, GRPCChannelState.SHUTDOWN}
    ),
    GRPCChannelState.TRANSIENT_FAILURE: frozenset(
        {GRPCChannelState.CONNECTING, GRPCChannelState.SHUTDOWN}
    ),
    GRPCChannelState.SHUTDOWN: frozenset(),
}


def _varint_len(value: int) -> int:
    """Byte length of `value` under protobuf base-128 varint encoding."""
    if value < 0:
        raise ValueError(f"varint encoding requires a non-negative value, got {value}")
    length = 1
    while value >= 0x80:
        value >>= 7
        length += 1
    return length


@dataclass
class ProtobufTickFrame:
    """
    Fixed-width binary tick frame used as a stand-in for a compiled protobuf message.

    The 44-byte size is a property of this `struct` layout, not of protobuf: proto3
    encodes uint32/uint64 as varints, omits fields holding the default value, and
    prefixes each field with a key byte. Use `proto3_wire_size()` for the real
    on-the-wire size, which is value-dependent (0 bytes for an all-default frame,
    55 bytes at maximum field values for this schema).
    """
    sequence_id: int      # proto3 field 1, uint64
    timestamp_ns: int     # proto3 field 2, uint64
    symbol_id: int        # proto3 field 3, uint32
    bid_price: float      # proto3 field 4, double
    ask_price: float      # proto3 field 5, double
    volume: float         # proto3 field 6, double

    def __post_init__(self) -> None:
        for name, limit in (
            ("sequence_id", UINT64_MAX),
            ("timestamp_ns", UINT64_MAX),
            ("symbol_id", UINT32_MAX),
        ):
            value = getattr(self, name)
            if not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}")
            if not 0 <= value <= limit:
                raise ValueError(f"{name}={value} outside encodable range [0, {limit}]")

        for name in ("bid_price", "ask_price", "volume"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)):
                raise TypeError(f"{name} must be numeric, got {type(value).__name__}")
            value = float(value)
            # NaN/Inf pack into a double without error and then propagate silently
            # into downstream risk state, so they are rejected at the boundary.
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite, got {value!r}")
            setattr(self, name, value)

        # Negative prices are permitted (settled negative futures prices are real) and
        # crossed bid/ask is permitted (legitimate across venues); only quantities are
        # sign-constrained.
        if self.volume < 0.0:
            raise ValueError(f"volume must be non-negative, got {self.volume}")

    def serialize_binary(self) -> bytes:
        """Serializes the frame into its fixed-width binary representation (44 bytes)."""
        return TICK_FRAME_STRUCT.pack(
            self.sequence_id,
            self.timestamp_ns,
            self.symbol_id,
            self.bid_price,
            self.ask_price,
            self.volume,
        )

    @classmethod
    def deserialize_binary(cls, raw_bytes: bytes) -> "ProtobufTickFrame":
        """
        Decodes a fixed-width binary frame.

        Raises ValueError on a truncated or over-long buffer rather than surfacing an
        opaque `struct.error`: a short read on a stream is a framing bug, and the
        caller needs the expected/actual byte counts to diagnose it.
        """
        if len(raw_bytes) != TICK_FRAME_BYTES:
            raise ValueError(
                f"tick frame must be exactly {TICK_FRAME_BYTES} bytes, got {len(raw_bytes)}"
            )
        seq, ts, sym_id, bid, ask, vol = TICK_FRAME_STRUCT.unpack(raw_bytes)
        return cls(
            sequence_id=seq,
            timestamp_ns=ts,
            symbol_id=sym_id,
            bid_price=bid,
            ask_price=ask,
            volume=vol,
        )

    def proto3_wire_size(self) -> int:
        """
        Exact proto3 encoded size of this frame, in bytes.

        Per the protobuf encoding guide: uint32/uint64 use base-128 varints, doubles
        use the fixed 8-byte I64 wire type, field keys for numbers 1-15 cost one byte,
        and fields holding the default value are omitted entirely.
        """
        size = 0
        for value in (self.sequence_id, self.timestamp_ns, self.symbol_id):
            if value != 0:
                size += 1 + _varint_len(value)
        for value in (self.bid_price, self.ask_price, self.volume):
            if value != 0.0:
                size += 1 + 8
        return size

    def to_json_dict(self) -> Dict[str, Any]:
        """Converts frame to the equivalent JSON dictionary representation for comparison."""
        return {
            "sequence_id": self.sequence_id,
            "timestamp_ns": self.timestamp_ns,
            "symbol_id": self.symbol_id,
            "bid_price": self.bid_price,
            "ask_price": self.ask_price,
            "volume": self.volume,
        }


class GRPCStreamingMarketDataEngine:
    """
    Models gRPC bi-directional streaming channel management over HTTP/2 and benchmarks
    binary frame vs JSON serialization overhead.

    The push path is a loopback: `stream_push_frame` encodes and immediately decodes
    the frame in-process. That verifies the codec and the producer's sequence
    discipline; it does NOT model network loss. Real receive-side gap recovery
    (snapshot re-sync, out-of-order buffering) belongs to
    `sequence-number-gap-detection-for-feeds`.
    """

    def __init__(
        self,
        target_service_name: str = "risk-engine-gateway",
        max_retained_frames: int = 1000,
    ):
        if max_retained_frames < 1:
            raise ValueError(f"max_retained_frames must be >= 1, got {max_retained_frames}")
        self.target_service_name = target_service_name
        self.max_retained_frames = max_retained_frames
        self.state = GRPCChannelState.IDLE
        # Bounded: an unbounded list grows without limit on a long-lived stream.
        self.received_frames: Deque[ProtobufTickFrame] = deque(maxlen=max_retained_frames)
        self.frames_sent = 0
        self.frames_received = 0
        self.sequence_gap_events = 0
        self.missing_frame_count = 0
        self.out_of_order_frames = 0
        self.reconnect_attempts = 0
        self._last_sequence_id: Optional[int] = None

    # ------------------------------------------------------------------
    # Channel lifecycle
    # ------------------------------------------------------------------
    def _transition(self, new_state: GRPCChannelState) -> None:
        """Applies a channel state transition, rejecting transitions gRPC does not allow."""
        if new_state not in LEGAL_CHANNEL_TRANSITIONS[self.state]:
            raise RuntimeError(
                f"illegal gRPC channel transition {self.state.value} -> {new_state.value}"
            )
        logger.info(
            f"gRPC channel '{self.target_service_name}': "
            f"{self.state.value} -> {new_state.value}"
        )
        self.state = new_state

    def connect_channel(self, connect_succeeds: bool = True) -> bool:
        """
        Drives the channel through IDLE/TRANSIENT_FAILURE -> CONNECTING -> READY.

        `connect_succeeds=False` routes to TRANSIENT_FAILURE instead, which is the
        state a real channel enters on a TCP/TLS/HTTP-2 handshake failure and from
        which it retries with exponential backoff.
        """
        if self.state is GRPCChannelState.READY:
            return True
        self._transition(GRPCChannelState.CONNECTING)
        if not connect_succeeds:
            self._transition(GRPCChannelState.TRANSIENT_FAILURE)
            self.reconnect_attempts += 1
            logger.warning(
                f"gRPC channel to '{self.target_service_name}' failed; retry in "
                f"{self.next_backoff_delay_s():.1f}s (attempt {self.reconnect_attempts})"
            )
            return False
        self._transition(GRPCChannelState.READY)
        # gRPC resets connection backoff once the server's SETTINGS frame is received.
        self.reconnect_attempts = 0
        return True

    def next_backoff_delay_s(self) -> float:
        """
        Deterministic base reconnect delay for the current attempt count.

        Follows the gRPC reference schedule (INITIAL_BACKOFF 1s, MULTIPLIER 1.6,
        MAX_BACKOFF 120s); gRPC additionally randomises each delay by
        +/- BACKOFF_JITTER_FRACTION, which is deliberately not applied here so the
        schedule is reproducible.
        """
        if self.reconnect_attempts <= 0:
            return 0.0
        delay = DEFAULT_INITIAL_BACKOFF_S * (
            DEFAULT_BACKOFF_MULTIPLIER ** (self.reconnect_attempts - 1)
        )
        return min(delay, DEFAULT_MAX_BACKOFF_S)

    def mark_transient_failure(self, reason: str) -> None:
        """Records a mid-stream failure on an established channel (READY -> TRANSIENT_FAILURE)."""
        self._transition(GRPCChannelState.TRANSIENT_FAILURE)
        self.reconnect_attempts += 1
        logger.warning(
            f"gRPC stream to '{self.target_service_name}' dropped: {reason}; "
            f"retry in {self.next_backoff_delay_s():.1f}s"
        )

    def on_goaway_received(self, has_pending_rpcs: bool = False) -> GRPCChannelState:
        """
        Handles an HTTP/2 GOAWAY from the peer.

        With no pending RPCs the channel drops to IDLE; with RPCs still in flight it
        stays READY until they drain. Either way the drop is silent - no error is
        raised on the stream - so a caller that assumes it is still connected will
        fail on its next push.
        """
        if has_pending_rpcs:
            logger.warning(
                f"GOAWAY from '{self.target_service_name}' with RPCs in flight; "
                f"draining before the channel goes IDLE"
            )
            return self.state
        if self.state is not GRPCChannelState.READY:
            # A duplicate or late GOAWAY on an already-dropped channel is not an error.
            return self.state
        self._transition(GRPCChannelState.IDLE)
        return self.state

    def shutdown(self) -> None:
        """Terminally shuts the channel down. SHUTDOWN cannot be left."""
        if self.state is GRPCChannelState.SHUTDOWN:
            return
        self._transition(GRPCChannelState.SHUTDOWN)

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------
    def stream_push_frame(self, frame: ProtobufTickFrame) -> bytes:
        """
        Pushes a binary tick frame over the open gRPC stream and decodes it loopback.

        Raises RuntimeError unless the channel is READY: pushing on an IDLE or
        TRANSIENT_FAILURE channel must reconnect first, never silently drop the frame.
        """
        if self.state is not GRPCChannelState.READY:
            raise RuntimeError(
                f"gRPC channel not READY (current state: {self.state.value}); "
                f"reconnect before streaming"
            )

        binary_payload = frame.serialize_binary()
        self.frames_sent += 1

        deserialized = ProtobufTickFrame.deserialize_binary(binary_payload)
        self._record_sequence(deserialized.sequence_id)
        self.received_frames.append(deserialized)
        self.frames_received += 1
        return binary_payload

    def _record_sequence(self, sequence_id: int) -> None:
        """Tracks producer sequence continuity across the stream."""
        last = self._last_sequence_id
        if last is not None:
            if sequence_id <= last:
                self.out_of_order_frames += 1
                logger.warning(
                    f"non-monotonic sequence on '{self.target_service_name}': "
                    f"received {sequence_id} after {last}"
                )
                return
            if sequence_id > last + 1:
                missing = sequence_id - last - 1
                self.sequence_gap_events += 1
                self.missing_frame_count += missing
                logger.warning(
                    f"sequence gap on '{self.target_service_name}': "
                    f"{missing} frame(s) missing between {last} and {sequence_id}"
                )
        self._last_sequence_id = sequence_id

    def stream_integrity_report(self) -> Dict[str, Any]:
        """Summarises stream continuity so 'zero frame drops' can be asserted, not assumed."""
        return {
            "frames_sent": self.frames_sent,
            "frames_received": self.frames_received,
            "sequence_gap_events": self.sequence_gap_events,
            "missing_frame_count": self.missing_frame_count,
            "out_of_order_frames": self.out_of_order_frames,
            "retained_frames": len(self.received_frames),
            "is_contiguous": (
                self.frames_sent == self.frames_received
                and self.sequence_gap_events == 0
                and self.out_of_order_frames == 0
            ),
        }

    # ------------------------------------------------------------------
    # Benchmarking
    # ------------------------------------------------------------------
    def benchmark_protobuf_vs_json(
        self, sample_frame: ProtobufTickFrame, iterations: int = 1000
    ) -> Dict[str, Any]:
        """
        Benchmarks payload size and serialization time of binary frames vs JSON text.

        JSON is encoded with compact separators, because `json.dumps` defaults to
        ", " and ": " and those cosmetic spaces would inflate the JSON baseline by
        ~8% and overstate the binary advantage. Timing is averaged over `iterations`
        calls: a single `perf_counter` pair around a sub-microsecond operation
        measures clock resolution, not serialization cost.
        """
        if iterations < 1:
            raise ValueError(f"iterations must be >= 1, got {iterations}")

        json_payload = sample_frame.to_json_dict()

        t0 = time.perf_counter()
        for _ in range(iterations):
            binary_bytes = sample_frame.serialize_binary()
        t1 = time.perf_counter()
        pb_time_us = (t1 - t0) * 1e6 / iterations

        t2 = time.perf_counter()
        for _ in range(iterations):
            json_bytes = json.dumps(json_payload, separators=(",", ":")).encode("utf-8")
        t3 = time.perf_counter()
        json_time_us = (t3 - t2) * 1e6 / iterations

        pb_size = len(binary_bytes)
        proto3_size = sample_frame.proto3_wire_size()
        json_size = len(json_bytes)

        summary = {
            "protobuf_bytes": pb_size,
            "proto3_wire_bytes": proto3_size,
            "json_bytes": json_size,
            "size_reduction_percent": round((json_size - pb_size) / json_size * 100.0, 2),
            "proto3_size_reduction_percent": round(
                (json_size - proto3_size) / json_size * 100.0, 2
            ),
            "protobuf_serialize_us": round(pb_time_us, 3),
            "json_serialize_us": round(json_time_us, 3),
            "iterations": iterations,
        }
        logger.info(
            f"gRPC Benchmark ({iterations} iters): binary {pb_size} bytes / proto3 "
            f"{proto3_size} bytes ({pb_time_us:.3f}us) vs compact JSON {json_size} bytes "
            f"({json_time_us:.3f}us) -> {summary['size_reduction_percent']:.1f}% size reduction."
        )
        return summary

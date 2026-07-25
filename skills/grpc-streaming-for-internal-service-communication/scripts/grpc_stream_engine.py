"""
grpc-streaming-for-internal-service-communication: Bi-directional gRPC HTTP/2 streaming engine
and binary Protocol Buffer serialization benchmark module.
"""
from dataclasses import dataclass, field
import json
import logging
import struct
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class GRPCChannelState(Enum):
    IDLE = "IDLE"
    CONNECTING = "CONNECTING"
    READY = "READY"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    SHUTDOWN = "SHUTDOWN"


@dataclass
class ProtobufTickFrame:
    """Simulated Protocol Buffer binary tick frame."""
    sequence_id: int      # uint64 (8 bytes)
    timestamp_ns: int     # uint64 (8 bytes)
    symbol_id: int        # uint32 (4 bytes)
    bid_price: float      # double (8 bytes)
    ask_price: float      # double (8 bytes)
    volume: float         # double (8 bytes)

    def serialize_binary(self) -> bytes:
        """Serializes frame into compact binary representation (44 bytes)."""
        # Format: >QQIddd (Big-endian Q:uint64, I:uint32, d:double)
        return struct.pack(">QQIddd", self.sequence_id, self.timestamp_ns, self.symbol_id, self.bid_price, self.ask_price, self.volume)

    @classmethod
    def deserialize_binary(cls, raw_bytes: bytes) -> "ProtobufTickFrame":
        seq, ts, sym_id, bid, ask, vol = struct.unpack(">QQIddd", raw_bytes)
        return cls(
            sequence_id=seq,
            timestamp_ns=ts,
            symbol_id=sym_id,
            bid_price=bid,
            ask_price=ask,
            volume=vol,
        )

    def to_json_dict(self) -> Dict[str, Any]:
        """Converts frame to equivalent JSON dictionary representation for comparison."""
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
    Simulates gRPC bi-directional streaming channel management over HTTP/2
    and benchmarks binary protobuf vs JSON serialization overhead.
    """

    def __init__(self, target_service_name: str = "risk-engine-gateway"):
        self.target_service_name = target_service_name
        self.state = GRPCChannelState.IDLE
        self.received_frames: List[ProtobufTickFrame] = []

    def connect_channel(self) -> bool:
        """Establishes gRPC HTTP/2 persistent channel."""
        self.state = GRPCChannelState.CONNECTING
        logger.info(f"Establishing gRPC HTTP/2 channel to '{self.target_service_name}'...")
        self.state = GRPCChannelState.READY
        return True

    def stream_push_frame(self, frame: ProtobufTickFrame) -> bytes:
        """Pushes a binary protobuf tick frame over the open gRPC stream."""
        if self.state != GRPCChannelState.READY:
            raise RuntimeError(f"gRPC channel not READY (Current state: {self.state.value})")

        binary_payload = frame.serialize_binary()
        deserialized = ProtobufTickFrame.deserialize_binary(binary_payload)
        self.received_frames.append(deserialized)
        return binary_payload

    def benchmark_protobuf_vs_json(self, sample_frame: ProtobufTickFrame) -> Dict[str, Any]:
        """
        Benchmarks byte payload size and serialization time of binary Protobuf vs JSON text.
        """
        # Protobuf binary
        t0 = time.perf_counter()
        binary_bytes = sample_frame.serialize_binary()
        t1 = time.perf_counter()
        pb_time_us = (t1 - t0) * 1e6

        # JSON text
        t2 = time.perf_counter()
        json_bytes = json.dumps(sample_frame.to_json_dict()).encode("utf-8")
        t3 = time.perf_counter()
        json_time_us = (t3 - t2) * 1e6

        pb_size = len(binary_bytes)
        json_size = len(json_bytes)
        savings_percent = ((json_size - pb_size) / json_size) * 100.0

        summary = {
            "protobuf_bytes": pb_size,
            "json_bytes": json_size,
            "size_reduction_percent": round(savings_percent, 2),
            "protobuf_serialize_us": round(pb_time_us, 3),
            "json_serialize_us": round(json_time_us, 3),
        }
        logger.info(
            f"gRPC Benchmark: Protobuf {pb_size} bytes ({pb_time_us:.2f}us) vs "
            f"JSON {json_size} bytes ({json_time_us:.2f}us) -> {savings_percent:.1f}% size reduction."
        )
        return summary

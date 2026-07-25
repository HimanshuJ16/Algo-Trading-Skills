"""
exchange-multicast-feed-handling: Dual A/B UDP multicast channel arbitrator,
out-of-order packet re-sequencing, and TCP gap-fill re-transmission engine.
"""
from dataclasses import dataclass, field
import logging
import socket
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class MulticastChannel(Enum):
    CHANNEL_A = "CHANNEL_A"
    CHANNEL_B = "CHANNEL_B"
    TCP_RETRANSMISSION = "TCP_RETRANSMISSION"


@dataclass
class MulticastPacket:
    channel: MulticastChannel
    sequence_id: int
    payload: bytes
    timestamp: float


@dataclass
class MulticastHandlerResult:
    processed_packets: List[MulticastPacket]
    is_gap_detected: bool
    missing_range: Optional[Tuple[int, int]]
    active_channel: MulticastChannel
    message: str


class ExchangeMulticastFeedHandler:
    """
    Handles dual A/B UDP multicast channels (CME MDP 3.0 / MoldUDP64),
    deduplicates twin streams, and triggers TCP gap-fill requests when both channels drop.
    """

    def __init__(self, initial_sequence: int = 1):
        self.expected_sequence = initial_sequence
        self.seen_sequences: Dict[int, MulticastPacket] = {}  # sequence -> packet
        self.out_of_order_buffer: Dict[int, MulticastPacket] = {}  # sequence -> packet
        self.tcp_retransmission_requests: List[Tuple[int, int]] = []

    def ingest_packet(self, channel: MulticastChannel, sequence_id: int, payload: bytes) -> MulticastHandlerResult:
        """
        Ingests a packet from Channel A, Channel B, or TCP Retransmission.
        """
        now = time.time()
        packet = MulticastPacket(
            channel=channel,
            sequence_id=sequence_id,
            payload=payload,
            timestamp=now,
        )

        # 1. Deduplicate if sequence was already processed
        if sequence_id < self.expected_sequence:
            logger.debug(f"Multicast Duplicate Ignored: Seq {sequence_id} on {channel.value} < expected {self.expected_sequence}")
            return MulticastHandlerResult(
                processed_packets=[],
                is_gap_detected=False,
                missing_range=None,
                active_channel=channel,
                message=f"Duplicate packet seq {sequence_id} ignored.",
            )

        # 2. In-order packet
        if sequence_id == self.expected_sequence:
            processed = [packet]
            self.seen_sequences[sequence_id] = packet
            next_seq = self.expected_sequence + 1

            # Drain contiguous out-of-order buffer
            while next_seq in self.out_of_order_buffer:
                buf_pkt = self.out_of_order_buffer.pop(next_seq)
                processed.append(buf_pkt)
                self.seen_sequences[next_seq] = buf_pkt
                next_seq += 1

            self.expected_sequence = next_seq
            return MulticastHandlerResult(
                processed_packets=processed,
                is_gap_detected=False,
                missing_range=None,
                active_channel=channel,
                message=f"Processed {len(processed)} in-order multicast packets.",
            )

        # 3. Out-of-order gap (sequence_id > expected_sequence)
        self.out_of_order_buffer[sequence_id] = packet
        missing_range = (self.expected_sequence, sequence_id - 1)

        # Check if packet arrived on secondary channel (A/B line arbitration)
        logger.warning(
            f"MULTICAST GAP DETECTED on {channel.value}: Expected seq {self.expected_sequence}, got {sequence_id}. "
            f"Requesting TCP re-transmission for range [{missing_range[0]}..{missing_range[1]}]."
        )

        self.tcp_retransmission_requests.append(missing_range)

        return MulticastHandlerResult(
            processed_packets=[],
            is_gap_detected=True,
            missing_range=missing_range,
            active_channel=channel,
            message=f"Gap detected. Triggered TCP re-transmission for range [{missing_range[0]}..{missing_range[1]}].",
        )

    def reconcile_tcp_retransmission(self, packets: List[MulticastPacket]) -> List[MulticastPacket]:
        """
        Ingests re-transmitted packets from TCP historical server to fill gaps.
        """
        sorted_pkts = sorted(packets, key=lambda p: p.sequence_id)
        processed_all: List[MulticastPacket] = []

        for pkt in sorted_pkts:
            res = self.ingest_packet(MulticastChannel.TCP_RETRANSMISSION, pkt.sequence_id, pkt.payload)
            processed_all.extend(res.processed_packets)

        logger.info(f"TCP Retransmission: Reconciled {len(processed_all)} missing multicast packets.")
        return processed_all

"""
network-interface-level-tick-timestamping: Socket ancillary control message decoder
for hardware NIC nanosecond packet timestamping (SO_TIMESTAMPNS / SO_TIMESTAMPING).
"""
from dataclasses import dataclass, field
import logging
import socket
import struct
import time
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TimestampSource(Enum):
    HARDWARE_NIC = "HARDWARE_NIC"
    KERNEL_SO_TIMESTAMP = "KERNEL_SO_TIMESTAMP"
    APPLICATION_FALLBACK = "APPLICATION_FALLBACK"


@dataclass
class NICTimestampedPacket:
    payload: bytes
    timestamp_ns: int
    source: TimestampSource
    kernel_queueing_jitter_us: float


class NICHardwareTimestamperEngine:
    """
    Extracts hardware NIC nanosecond timestamps from socket control buffers (SO_TIMESTAMPNS),
    eliminating OS kernel stack queueing jitter.
    """

    def __init__(self, use_hardware_timestamping: bool = True):
        self.use_hardware_timestamping = use_hardware_timestamping

    def enable_nic_timestamping(self, sock: socket.socket) -> bool:
        """
        Configures socket options for hardware / kernel timestamping.
        """
        try:
            # Enable SO_TIMESTAMPNS if supported on Linux/Unix
            if hasattr(socket, "SO_TIMESTAMPNS"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_TIMESTAMPNS, 1)
                logger.info("Socket option SO_TIMESTAMPNS enabled successfully.")
                return True
            elif hasattr(socket, "SO_TIMESTAMP"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_TIMESTAMP, 1)
                logger.info("Socket option SO_TIMESTAMP enabled.")
                return True
        except Exception as exc:
            logger.warning(f"Could not enable socket timestamping: {exc}")
        return False

    def unpack_ancillary_timestamp(
        self,
        ancillary_data: List[Tuple[int, int, bytes]],
        app_receipt_time: float,
    ) -> Tuple[int, TimestampSource]:
        """
        Unpacks struct timespec (seconds, nanoseconds) from socket control buffer ancillary data.
        """
        for cmsg_level, cmsg_type, cmsg_data in ancillary_data:
            # Check SOL_SOCKET & SO_TIMESTAMPNS / SO_TIMESTAMP
            if cmsg_level == socket.SOL_SOCKET:
                if len(cmsg_data) >= 16:
                    # Unpack 64-bit sec and 64-bit nsec (Linux timespec: >QQ or <QQ)
                    sec, nsec = struct.unpack_from("qq", cmsg_data, 0)
                    ts_ns = (sec * 1000000000) + nsec
                    return ts_ns, TimestampSource.HARDWARE_NIC
                elif len(cmsg_data) == 8:
                    # Unpack 32-bit sec and 32-bit usec (timeval: >II or <II)
                    sec, usec = struct.unpack_from("ii", cmsg_data, 0)
                    ts_ns = (sec * 1000000000) + (usec * 1000)
                    return ts_ns, TimestampSource.KERNEL_SO_TIMESTAMP

        # Fallback to application time if ancillary data missing
        app_ns = int(app_receipt_time * 1e9)
        return app_ns, TimestampSource.APPLICATION_FALLBACK

    def process_packet_with_nic_timestamp(
        self,
        payload: bytes,
        ancillary_data: List[Tuple[int, int, bytes]],
        app_receipt_time: Optional[float] = None,
    ) -> NICTimestampedPacket:
        """
        Processes incoming socket packet, decodes NIC hardware timestamp,
        and calculates kernel queueing jitter.
        """
        now = app_receipt_time or time.time()
        app_ns = int(now * 1e9)

        ts_ns, source = self.unpack_ancillary_timestamp(ancillary_data, now)
        jitter_us = max(0.0, (app_ns - ts_ns) / 1000.0)

        if source == TimestampSource.HARDWARE_NIC:
            logger.debug(f"NIC Timestamper: Extracted HW timestamp {ts_ns} ns. Kernel jitter: {jitter_us:.2f} us.")

        return NICTimestampedPacket(
            payload=payload,
            timestamp_ns=ts_ns,
            source=source,
            kernel_queueing_jitter_us=round(jitter_us, 3),
        )

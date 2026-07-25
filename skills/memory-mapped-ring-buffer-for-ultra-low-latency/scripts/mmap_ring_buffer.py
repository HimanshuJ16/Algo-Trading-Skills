"""
memory-mapped-ring-buffer-for-ultra-low-latency: Zero-copy memory-mapped file (mmap)
ring buffer engine for low-latency IPC tick pipelines.
"""
from dataclasses import dataclass, field
import logging
import mmap
import os
import struct
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class MMAPTickSlot:
    sequence_id: int   # uint64 (8 bytes)
    timestamp_ns: int  # uint64 (8 bytes)
    bid: float         # double (8 bytes)
    ask: float         # double (8 bytes)
    volume: float      # double (8 bytes)


class MemoryMappedRingBufferEngine:
    """
    Fixed-size memory-mapped (mmap) ring buffer supporting sub-microsecond
    lock-free IPC reads and writes.
    """

    HEADER_FORMAT = ">QQQ"  # capacity (8B), write_head (8B), read_tail (8B) = 24 bytes
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
    SLOT_FORMAT = ">QQddd"  # seq (8B), ts (8B), bid (8B), ask (8B), vol (8B) = 40 bytes
    SLOT_SIZE = struct.calcsize(SLOT_FORMAT)

    def __init__(self, capacity: int = 1000, backing_filepath: Optional[str] = None):
        self.capacity = capacity
        self.total_buffer_size = self.HEADER_SIZE + (self.capacity * self.SLOT_SIZE)

        if not backing_filepath:
            temp_dir = tempfile.gettempdir()
            backing_filepath = os.path.join(temp_dir, f"mmap_ring_buf_{os.getpid()}_{time.time_ns()}.bin")

        self.filepath = backing_filepath
        self._init_file()

    def _init_file(self) -> None:
        """Creates backing binary file and memory-maps it."""
        with open(self.filepath, "wb") as f:
            f.write(b"\x00" * self.total_buffer_size)

        self._file_obj = open(self.filepath, "r+b")
        self.mm = mmap.mmap(self._file_obj.fileno(), self.total_buffer_size)

        # Write initial header
        struct.pack_into(self.HEADER_FORMAT, self.mm, 0, self.capacity, 0, 0)
        logger.info(f"Initialized mmap ring buffer at '{self.filepath}' ({self.capacity} slots, {self.total_buffer_size} bytes).")

    def _get_header(self) -> Tuple[int, int, int]:
        return struct.unpack_from(self.HEADER_FORMAT, self.mm, 0)

    def _update_header(self, capacity: int, write_head: int, read_tail: int) -> None:
        struct.pack_into(self.HEADER_FORMAT, self.mm, 0, capacity, write_head, read_tail)

    def push(self, seq: int, ts_ns: int, bid: float, ask: float, vol: float) -> bool:
        """
        Pushes a tick slot into the ring buffer. Returns False if buffer is full.
        """
        cap, head, tail = self._get_header()

        # Check overflow (full condition)
        if head - tail >= cap:
            logger.warning(f"mmap ring buffer FULL (capacity={cap}, head={head}, tail={tail}). Push dropped.")
            return False

        slot_idx = head % cap
        offset = self.HEADER_SIZE + (slot_idx * self.SLOT_SIZE)

        struct.pack_into(self.SLOT_FORMAT, self.mm, offset, seq, ts_ns, bid, ask, vol)
        self._update_header(cap, head + 1, tail)
        return True

    def pop(self) -> Optional[MMAPTickSlot]:
        """
        Pops the oldest tick slot from the ring buffer. Returns None if empty.
        """
        cap, head, tail = self._get_header()

        if tail >= head:
            return None  # Empty

        slot_idx = tail % cap
        offset = self.HEADER_SIZE + (slot_idx * self.SLOT_SIZE)

        seq, ts_ns, bid, ask, vol = struct.unpack_from(self.SLOT_FORMAT, self.mm, offset)
        self._update_header(cap, head, tail + 1)

        return MMAPTickSlot(
            sequence_id=seq,
            timestamp_ns=ts_ns,
            bid=bid,
            ask=ask,
            volume=vol,
        )

    def close(self) -> None:
        """Closes memory map and removes temp backing file."""
        if hasattr(self, "mm") and self.mm:
            self.mm.close()
        if hasattr(self, "_file_obj") and self._file_obj:
            self._file_obj.close()
        if os.path.exists(self.filepath):
            try:
                os.remove(self.filepath)
            except Exception:
                pass

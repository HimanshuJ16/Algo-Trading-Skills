"""
memory-mapped-ring-buffer-for-ultra-low-latency: shared-memory ring buffer over a
memory-mapped file, for handing ticks between a producer and a consumer without a
language-level queue.

Design contract (read before reusing this in production):

* **Single producer, single consumer.** The producer writes ``write_head`` and
  nothing else; the consumer writes ``read_tail`` and nothing else. This is the
  Lamport (1977) bounded-buffer arrangement: correct without locks *provided*
  each index is written by exactly one party and index reads/writes are atomic
  and sequentially consistent. Two producers, or two consumers, will corrupt the
  ring -- there is no compare-and-swap here to arbitrate them.
* **CPython guarantees neither atomicity nor ordering.** ``struct.pack_into``
  fills the mapped page through byte-level writes (``_PyLong_AsByteArray`` for
  format ``Q``), so a reader in another process can in principle observe a torn
  index. The GIL serializes bytecode *within* one interpreter; it says nothing
  across processes. Treat the cross-process path here as a reference design to
  port to C++/Rust with real acquire/release semantics, or guard it with an
  OS-level lock and accept the latency. Within a single process (producer
  thread, consumer thread) the GIL makes each ``push``/``pop`` effectively
  atomic.
* **No msync/flush is needed for the peer to see a write.** A shared file
  mapping is coherent by definition (POSIX ``mmap``: "If MAP_SHARED is
  specified, write references shall change the underlying object"). ``msync``
  controls *durability to disk*, not inter-process visibility. Calling it on
  the hot path buys nothing and costs a blocking syscall.
* **This is a transport, not a validator.** Payload floats round-trip exactly,
  including NaN and infinity. The consumer is responsible for rejecting
  nonsensical quotes.
"""
from dataclasses import dataclass
import logging
import mmap
import os
import struct
import tempfile
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_UINT64_MAX = 2**64 - 1


class RingBufferError(ValueError):
    """
    Raised when a ring buffer cannot be created, attached to, or written.

    Subclasses ``ValueError`` so callers written against a plain
    ``except ValueError`` contract keep working, and so a caller never has to
    catch a raw ``struct.error`` leaking out of the pack path.
    """


@dataclass(slots=True)
class MMAPTickSlot:
    """
    One decoded tick slot.

    Uses ``__slots__`` for a smaller footprint and faster attribute access.
    Constructing this object nonetheless allocates -- see the note in
    ``references/standards.md`` on what "zero-copy" does and does not mean in
    CPython.
    """

    sequence_id: int   # uint64 (8 bytes)
    timestamp_ns: int  # uint64 (8 bytes)
    bid: float         # double (8 bytes)
    ask: float         # double (8 bytes)
    volume: float      # double (8 bytes)


class MemoryMappedRingBufferEngine:
    """
    Fixed-slot ring buffer backed by a memory-mapped file.

    Create the buffer in the producer::

        ring = MemoryMappedRingBufferEngine(capacity=8192, backing_filepath=path)

    and join it from the consumer, which must NOT re-run the constructor --
    that would zero the live buffer::

        ring = MemoryMappedRingBufferEngine.attach(path)

    ``write_head`` and ``read_tail`` are free-running monotonic uint64 counters,
    never taken modulo capacity. Only the *slot index* is reduced mod capacity.
    Wrapping the counters themselves would break both the full test
    (``head - tail >= capacity``) and the empty test (``tail >= head``): after a
    wrap ``head - tail`` goes negative, so the ring reports itself permanently
    empty while the producer silently overwrites unread slots. At 10 million
    ticks/second a uint64 counter takes roughly 58,000 years to exhaust, so
    monotonic counters need no wrap handling; the range is asserted anyway so
    exhaustion would raise rather than corrupt.
    """

    MAGIC = b"MMRB"
    FORMAT_VERSION = 1

    # magic(4) version(4) capacity(8) write_head(8) read_tail(8) = 32 bytes.
    # Explicit '>' prefix: no native alignment padding, and an identical layout
    # regardless of the architecture of the process that attaches.
    HEADER_FORMAT = ">4sIQQQ"
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
    SLOT_FORMAT = ">QQddd"  # seq (8B), ts (8B), bid (8B), ask (8B), vol (8B) = 40 bytes
    SLOT_SIZE = struct.calcsize(SLOT_FORMAT)

    # Byte offsets of the two mutable indices. The producer writes only
    # _HEAD_OFFSET, the consumer only _TAIL_OFFSET. Rewriting the whole header
    # from either side makes each writer clobber the other's index with its own
    # stale snapshot, silently losing already-committed ticks.
    _HEAD_OFFSET = 16
    _TAIL_OFFSET = 24

    # Precompiled once at class creation so no format string is re-parsed on
    # the hot path.
    _HEADER_STRUCT = struct.Struct(HEADER_FORMAT)
    _SLOT_STRUCT = struct.Struct(SLOT_FORMAT)
    _INDEX_STRUCT = struct.Struct(">Q")

    def __init__(
        self,
        capacity: int = 1000,
        backing_filepath: Optional[str] = None,
        unlink_on_close: Optional[bool] = None,
    ):
        """
        Create (or re-create) a ring buffer and its backing file.

        This constructor is the *producer* entry point: it truncates and zeroes
        ``backing_filepath``. To join an existing buffer, use :meth:`attach`.

        :param capacity: number of tick slots; must be a positive int.
        :param backing_filepath: backing file; a temp file is used if omitted.
        :param unlink_on_close: delete the backing file in :meth:`close`.
            Defaults to True only for the generated temp file. A caller-supplied
            path is never deleted by default -- a consumer calling ``close()``
            must not be able to destroy the producer's buffer.
        :raises RingBufferError: if ``capacity`` is not a positive int.
        """
        self.capacity = self._validate_capacity(capacity)
        self.total_buffer_size = self.HEADER_SIZE + (self.capacity * self.SLOT_SIZE)
        self.mm: Optional[mmap.mmap] = None
        self._file_obj = None

        created_temp_file = not backing_filepath
        if created_temp_file:
            backing_filepath = os.path.join(
                tempfile.gettempdir(),
                f"mmap_ring_buf_{os.getpid()}_{time.time_ns()}.bin",
            )

        self.filepath = backing_filepath
        self._unlink_on_close = (
            created_temp_file if unlink_on_close is None else bool(unlink_on_close)
        )
        self._init_file()

    @staticmethod
    def _validate_capacity(capacity: int) -> int:
        """Rejects capacities that mmap or the modulo arithmetic cannot honour."""
        if isinstance(capacity, bool) or not isinstance(capacity, int):
            raise RingBufferError(
                f"capacity must be an int, got {type(capacity).__name__}")
        if capacity < 1:
            # capacity == 0 is the dangerous one: it maps cleanly, then reports
            # full on every push (0 - 0 >= 0) and silently drops 100% of ticks.
            raise RingBufferError(f"capacity must be >= 1, got {capacity}")
        return capacity

    @classmethod
    def attach(cls, backing_filepath: str) -> "MemoryMappedRingBufferEngine":
        """
        Join an existing ring buffer without disturbing its contents.

        Validates the magic, format version, declared capacity and file length
        before mapping, so attaching to a stale, truncated or unrelated file
        fails loudly instead of indexing off the end of the mapping.

        :raises RingBufferError: if the file is missing or is not a compatible
            ring buffer.
        """
        if not os.path.isfile(backing_filepath):
            raise RingBufferError(f"no ring buffer file at '{backing_filepath}'")

        file_size = os.path.getsize(backing_filepath)
        if file_size < cls.HEADER_SIZE:
            raise RingBufferError(
                f"'{backing_filepath}' is {file_size} bytes, too small for a "
                f"{cls.HEADER_SIZE}-byte header")

        with open(backing_filepath, "rb") as probe:
            magic, version, capacity, _head, _tail = cls._HEADER_STRUCT.unpack(
                probe.read(cls.HEADER_SIZE))

        if magic != cls.MAGIC:
            raise RingBufferError(
                f"'{backing_filepath}' is not a ring buffer "
                f"(magic {magic!r}, expected {cls.MAGIC!r})")
        if version != cls.FORMAT_VERSION:
            raise RingBufferError(
                f"'{backing_filepath}' uses format version {version}, "
                f"this build speaks version {cls.FORMAT_VERSION}")

        capacity = cls._validate_capacity(capacity)
        expected_size = cls.HEADER_SIZE + capacity * cls.SLOT_SIZE
        if file_size != expected_size:
            raise RingBufferError(
                f"'{backing_filepath}' is {file_size} bytes but its header "
                f"declares capacity {capacity} ({expected_size} bytes expected)")

        instance = cls.__new__(cls)
        instance.capacity = capacity
        instance.total_buffer_size = expected_size
        instance.filepath = backing_filepath
        # An attaching consumer never owns the file's lifetime.
        instance._unlink_on_close = False
        instance.mm = None
        instance._file_obj = None
        instance._map_existing_file()
        logger.info(
            "Attached to mmap ring buffer at '%s' (%d slots, %d bytes).",
            backing_filepath, capacity, expected_size)
        return instance

    def _init_file(self) -> None:
        """Creates and zeroes the backing binary file, then memory-maps it."""
        with open(self.filepath, "wb") as f:
            f.write(b"\x00" * self.total_buffer_size)

        self._map_existing_file()
        self._HEADER_STRUCT.pack_into(
            self.mm, 0, self.MAGIC, self.FORMAT_VERSION, self.capacity, 0, 0)
        logger.info(
            "Initialized mmap ring buffer at '%s' (%d slots, %d bytes).",
            self.filepath, self.capacity, self.total_buffer_size)

    def _map_existing_file(self) -> None:
        """
        Maps the backing file without truncating it.

        ``access=mmap.ACCESS_WRITE`` is passed explicitly rather than relying on
        the platform default: it is the write-through / shared mapping on both
        POSIX and Windows, which is what makes writes visible to the peer
        process. ``ACCESS_COPY`` would map copy-on-write, and every write would
        be invisible to the peer -- a failure mode that raises nothing at all,
        it just yields a consumer that never sees a tick.
        """
        self._file_obj = open(self.filepath, "r+b")
        try:
            self.mm = mmap.mmap(
                self._file_obj.fileno(),
                self.total_buffer_size,
                access=mmap.ACCESS_WRITE,
            )
        except (OSError, ValueError):
            self._file_obj.close()
            self._file_obj = None
            raise

    def _read_head(self) -> int:
        return self._INDEX_STRUCT.unpack_from(self.mm, self._HEAD_OFFSET)[0]

    def _read_tail(self) -> int:
        return self._INDEX_STRUCT.unpack_from(self.mm, self._TAIL_OFFSET)[0]

    def _get_header(self) -> Tuple[int, int, int]:
        """Returns ``(capacity, write_head, read_tail)``."""
        return (self.capacity, self._read_head(), self._read_tail())

    def __len__(self) -> int:
        """
        Number of unread ticks -- the pipeline's backlog metric.

        Read from the consumer side this can lag by whatever the producer has
        committed since; it is a monitoring signal, not a synchronisation point.

        A negative difference means the indices are inconsistent (a torn read,
        or a second writer violating the single-producer/single-consumer
        contract). That is clamped to 0 and logged rather than raised: this is
        called from monitoring code, and ``__len__`` rejecting a negative return
        would surface as an unrelated-looking ``ValueError`` from ``len()``.
        """
        backlog = self._read_head() - self._read_tail()
        if backlog < 0:
            logger.error(
                "mmap ring buffer indices inconsistent (head=%d < tail=%d) at '%s'; "
                "backlog reported as 0. Check for a second writer or a torn read.",
                self._read_head(), self._read_tail(), self.filepath)
            return 0
        return backlog

    def push(self, seq: int, ts_ns: int, bid: float, ask: float, vol: float) -> bool:
        """
        Publish one tick. Returns False if the ring is full and the tick was dropped.

        Producer-only. The slot payload is written *before* ``write_head`` is
        advanced, so a consumer that observes the new head has, on any
        store-ordered architecture, a fully written slot behind it. See the
        module docstring for why CPython cannot turn that into a guarantee.

        :raises RingBufferError: if ``seq`` or ``ts_ns`` falls outside uint64,
            or if the payload cannot be packed.
        """
        if not 0 <= seq <= _UINT64_MAX:
            raise RingBufferError(f"seq {seq} outside uint64 range")
        if not 0 <= ts_ns <= _UINT64_MAX:
            raise RingBufferError(f"ts_ns {ts_ns} outside uint64 range")

        head = self._read_head()
        tail = self._read_tail()

        if head - tail >= self.capacity:
            # Lazy %-formatting: on a latency-sensitive path the message must
            # not be built when the log level is disabled.
            logger.warning(
                "mmap ring buffer FULL (capacity=%d, head=%d, tail=%d). Push dropped.",
                self.capacity, head, tail)
            return False

        offset = self.HEADER_SIZE + ((head % self.capacity) * self.SLOT_SIZE)
        try:
            self._SLOT_STRUCT.pack_into(self.mm, offset, seq, ts_ns, bid, ask, vol)
        except struct.error as exc:
            # head is deliberately not advanced: the half-written slot stays
            # beyond the head and is overwritten by the next successful push.
            raise RingBufferError(f"could not pack tick payload: {exc}") from exc

        self._INDEX_STRUCT.pack_into(self.mm, self._HEAD_OFFSET, head + 1)
        return True

    def pop(self) -> Optional[MMAPTickSlot]:
        """
        Consume the oldest unread tick, or return None if the ring is empty.

        Consumer-only. The slot is unpacked *before* ``read_tail`` advances:
        advancing first would release the slot back to the producer while it is
        still being read, yielding a torn tick rather than a dropped one.
        """
        tail = self._read_tail()
        head = self._read_head()

        if tail >= head:
            return None

        offset = self.HEADER_SIZE + ((tail % self.capacity) * self.SLOT_SIZE)
        seq, ts_ns, bid, ask, vol = self._SLOT_STRUCT.unpack_from(self.mm, offset)
        self._INDEX_STRUCT.pack_into(self.mm, self._TAIL_OFFSET, tail + 1)

        return MMAPTickSlot(
            sequence_id=seq,
            timestamp_ns=ts_ns,
            bid=bid,
            ask=ask,
            volume=vol,
        )

    def close(self) -> None:
        """
        Unmap the buffer and release the file handle. Idempotent.

        The backing file is removed only when this instance owns it (see
        ``unlink_on_close``). A consumer created by :meth:`attach` never deletes
        the file it was handed.

        ``self.mm`` deliberately keeps pointing at the *closed* mmap rather than
        being set to None: a subsequent ``push``/``pop`` then fails with
        ``ValueError: mmap closed or invalid``, which names the actual problem,
        instead of an opaque ``TypeError`` about NoneType. Both ``mmap.close()``
        and ``file.close()`` are idempotent, so this method is too, and no
        liveness check is needed on the hot path.
        """
        if self.mm is not None:
            self.mm.close()
        if self._file_obj is not None:
            self._file_obj.close()

        if self._unlink_on_close and os.path.exists(self.filepath):
            try:
                os.remove(self.filepath)
            except OSError as exc:
                logger.warning(
                    "Could not remove ring buffer file '%s': %s", self.filepath, exc)

    def __enter__(self) -> "MemoryMappedRingBufferEngine":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

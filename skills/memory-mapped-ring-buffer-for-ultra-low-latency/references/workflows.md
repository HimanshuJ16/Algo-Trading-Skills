# Deep Workflow Reference — memory-mapped-ring-buffer-for-ultra-low-latency

This file holds the full technical procedure referenced by `SKILL.md`.

## Full Procedure

### 1. Producer: create the buffer

- Validate `capacity` as a positive integer *before* touching the filesystem.
  `capacity = 0` maps cleanly and then reports full on every push
  (`0 - 0 >= 0`), silently dropping every tick; a negative capacity surfaces as
  an opaque `OverflowError` from inside `mmap`.
- Allocate a backing file of `HeaderSize + capacity × SlotSize` bytes and map it
  with `access=mmap.ACCESS_WRITE` (write-through/shared on both POSIX and
  Windows). Do not rely on the platform default — `ACCESS_COPY` maps
  copy-on-write and every write silently becomes invisible to the peer.
- Write the header: magic, format version, capacity, `write_head = 0`,
  `read_tail = 0`.

### 2. Consumer: attach, never re-create

The creating constructor truncates and zeroes the file. Running it in the
consumer wipes the live buffer — on Linux silently, on Windows as
`OSError: [Errno 22]` because the file is already mapped. Attach instead, and
validate before mapping:

- magic matches, so a foreign or garbage file is rejected rather than read as a
  header;
- format version matches this build;
- declared capacity is a positive integer;
- file length equals `HeaderSize + capacity × SlotSize`.

Without the length check, a stale file from a run with a different capacity maps
at the wrong size and slot offsets index past the end of the mapping.

### 3. Push (producer only)

```
validate seq, ts_ns within uint64
head = read(write_head); tail = read(read_tail)
if head - tail >= capacity:  drop, count, return False
offset = HeaderSize + (head mod capacity) * SlotSize
pack slot payload at offset          <-- payload first
write(write_head, head + 1)          <-- publish second
```

Order matters. Publishing the head before the payload exposes a slot the
consumer may read half-formed. On a store-ordered architecture the shown order
is what a consumer needs; on a weakly-ordered one a native port must place a
release fence between the two, which Python cannot express.

Only `write_head` is written. Rewriting the whole header also stamps `read_tail`
from the producer's stale snapshot, reverting a tail the consumer already
committed.

### 4. Pop (consumer only)

```
tail = read(read_tail); head = read(write_head)
if tail >= head:  return None
offset = HeaderSize + (tail mod capacity) * SlotSize
unpack slot payload at offset        <-- read first
write(read_tail, tail + 1)           <-- release second
```

Releasing the slot before reading it hands it back to the producer mid-read,
producing a torn tick rather than a dropped one. Only `read_tail` is written.

### 5. Index arithmetic

`write_head` and `read_tail` are free-running monotonic uint64 counters. Only
the slot index is reduced `mod capacity`.

Taking the counters themselves modulo anything breaks the ring. After a wrap,
`head - tail` is negative, so `head - tail >= capacity` never fires and the
producer overwrites unread slots forever, while `tail >= head` reports the ring
permanently empty. At 10 million ticks/second a uint64 counter takes roughly
58,000 years to exhaust, so no wrap handling is warranted — but the range is
asserted anyway so exhaustion would raise rather than corrupt.

The full test is `head - tail >= capacity` rather than the usual
`(head + 1) mod capacity == tail`, so all `capacity` slots are usable; there is
no sacrificed slot.

### 6. Failure and backpressure

- A full ring drops the newest tick and returns `False`. It does not block and
  does not overwrite. Count the drops — a drop counter that only appears in log
  lines is not a metric.
- Deciding *what* to do about a full ring (drop oldest, degrade, widen quotes,
  halt) is out of scope here; see `backpressure-drop-degrade-policy`.
- Payload values are transported verbatim. NaN and infinity round-trip intact.
  The ring is a transport, not a validator; the consumer must reject
  nonsensical quotes.

### 7. Resource cleanup

- Close the mapping, then the file handle. Both must be idempotent so a
  `finally` block or a context manager can run twice without raising.
- Unlink the backing file **only from the process that created it**. A
  consumer's `close()` removing the file destroys the producer's buffer — and
  on POSIX the producer keeps writing into an unlinked inode that no new
  consumer can ever attach to, which is a particularly quiet way to lose a feed.

## Production Implementation Reference

- Reference code: `scripts/mmap_ring_buffer.py`
  (`MemoryMappedRingBufferEngine`, `MMAPTickSlot`, `RingBufferError`).
- Automated unit tests: `scripts/test_mmap_ring_buffer.py`, including two
  interleaving regression tests that fail against a whole-header write and pass
  against split index ownership.
- Concurrency, visibility, atomicity and measured latency: `references/standards.md`.

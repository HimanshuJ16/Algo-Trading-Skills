# Real-Time Architecture Standards — memory-mapped-ring-buffer-for-ultra-low-latency

| Parameter | Default Value | Description |
|---|---|---|
| Header Size | 24 bytes | `capacity` (8B), `write_head` (8B), `read_tail` (8B) |
| Slot Size | 40 bytes | Packed binary tick payload (`>QQddd`) |
| Access Speed | $< 0.5 \mu\text{s}$ | Sub-microsecond zero-copy mmap write/read |
| Overflow Behavior | Drop & Warn | Prevents head overwriting unread tail slots |

## Category

`real-time-architecture` — see top-level `mappings/` directory.

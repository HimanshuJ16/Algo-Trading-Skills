# Deep Workflow Reference — network-interface-level-tick-timestamping

This file holds the full technical procedure referenced by `SKILL.md`. Layout and
constant facts cited here are sourced in `references/standards.md`.

## Full procedure

### 1. Confirm the adapter timestamps in hardware

```
ethtool -T eth0
```

Look for `hardware-receive` under *Capabilities* and a non-empty *HW Receive Filter
Modes* list. If they are absent, the driver cannot produce a `ts[2]` value; a
`SCM_TIMESTAMPING` control message will still be delivered, with the hardware slot
zeroed. Decide here whether to fix the platform or to record kernel software
timestamps under their real name (`use_hardware_timestamping=False`).

### 2. Enable `SO_TIMESTAMPING`

```python
engine = NICHardwareTimestamperEngine(use_hardware_timestamping=True)
if not engine.enable_nic_timestamping(sock):
    raise RuntimeError("hardware timestamping unavailable")
```

The engine issues:

```python
sock.setsockopt(socket.SOL_SOCKET, 37,
                SOF_TIMESTAMPING_RX_HARDWARE | SOF_TIMESTAMPING_RAW_HARDWARE)
```

Two constraints are structural, not stylistic:

- The option number must be passed numerically. CPython exports no
  `SO_TIMESTAMPING` constant, so `hasattr(socket, ...)` guards never fire. On sparc
  and parisc the number differs from 37 — pass `so_timestamping=` and the matching
  `scm_timestamping_types=`.
- `SO_TIMESTAMP` and `SO_TIMESTAMPNS` must not also be set on this socket. With
  `SOF_TIMESTAMPING_SOFTWARE` requested, the kernel writes a fabricated software
  timestamp into `ts[0]` during `recvmsg()` whenever a real one is missing.

### 3. Receive with a correctly sized ancillary buffer

```python
payload, ancdata, msg_flags, addr = sock.recvmsg(65535, socket.CMSG_SPACE(48))
app_ns = time.time_ns()
```

`struct scm_timestamping` is 48 bytes on LP64 (three 16-byte `timespec` slots). An
undersized `ancbufsize` truncates the control message from the tail, which is
precisely where `ts[2]` lives; check `socket.MSG_CTRUNC` in `msg_flags` if in doubt.

Read `time.time_ns()`, not `time.time()`. A nanosecond epoch of ~1.78e18 exceeds the
53-bit binary64 significand, giving 256 ns of representable spacing.

### 4. Decode by `cmsg_type`

```python
pkt = engine.process_packet_with_nic_timestamp(payload, ancdata, app_ns)
```

`decode_ancillary` scans **every** `SOL_SOCKET` control message before selecting one —
`recvmsg` does not guarantee the hardware message comes first — and dispatches on
`cmsg_type`:

| `cmsg_type` | Decoded as | Recorded into |
|---|---|---|
| `SCM_TIMESTAMPING` (37 / 65) | `timespec[3]`; `ts[0]` → software, `ts[2]` → hardware | `hardware_ns`, `software_ns` |
| `SCM_TIMESTAMPNS` (35 / 64) | single `timespec` | `software_ns` |
| `SCM_TIMESTAMP` (29 / 63) | single `timeval`, `tv_usec × 1000` | `software_coarse_ns` |

Slot size is derived from the payload length (16 B on LP64 and for all `_NEW`
variants, 8 B for `_OLD` on a 32-bit host), never the capture point. A slot with
`tv_sec == 0 and tv_nsec == 0` is unfilled and decodes to `None`; `tv_nsec` outside
`[0, 1e9)` or `tv_usec` outside `[0, 1e6)` means the buffer is not what its
`cmsg_type` claims and is rejected rather than multiplied out.

Selection precedence: `hardware_ns` → `software_ns` → `software_coarse_ns` →
the supplied application timestamp.

### 5. Act on the result fields

| Field | Use |
|---|---|
| `timestamp_ns` | The authoritative tick timestamp. Attach this to the market data object. |
| `source` | `HARDWARE_NIC` / `KERNEL_SOFTWARE` / `KERNEL_SO_TIMESTAMP` / `APPLICATION_FALLBACK`. Persist it — a tick's accuracy claim is only as good as its capture point. |
| `degraded` | Hardware was required and did not arrive. Alert; do not keep writing ticks that claim wire accuracy. |
| `capture_delay_ns` | `app_timestamp_ns − timestamp_ns`, **signed**. |
| `cross_timebase_comparison` | `True` when `capture_delay_ns` mixes the PHC and `CLOCK_REALTIME`. |
| `kernel_queueing_jitter_us` | `capture_delay_ns` in microseconds, signed. Retained for compatibility with the v1 field name. |

A negative `capture_delay_ns` with `cross_timebase_comparison == True` is an
undisciplined PHC, not a fast packet. Fix `phc2sys`/`sfptpd` before drawing any
latency conclusion from that batch — see `clock-synchronization-ptp-for-trading-hosts`.

## Behaviour changes from v1.0.0

| v1.0.0 | v2.0.0 | Why |
|---|---|---|
| Classified by control-buffer length; any ≥16-byte `SOL_SOCKET` payload became `HARDWARE_NIC` | Classifies by `cmsg_type` | On LP64 a 16-byte payload is `SCM_TIMESTAMPNS` or `SCM_TIMESTAMP` — both kernel software |
| Decoded `scm_timestamping` from offset 0 (`ts[0]`) | Reads `ts[2]` for hardware, `ts[0]` for software | `ts[0]` is the software timestamp |
| Decoded a 16-byte `timeval` as a `timespec` | Decodes `timeval` as microseconds | `tv_usec` read as nanoseconds is 1000× small |
| `enable_nic_timestamping` gated on `hasattr(socket, "SO_TIMESTAMPNS")` | Uses the numeric option, Linux-guarded | CPython defines neither name, so the function never enabled anything |
| `max(0.0, T_app − T_hw)` | Signed `capture_delay_ns` | Clamping hides an undisciplined PHC |
| `app_receipt_time: float` seconds | `app_receipt_ns: int` nanoseconds, floats rejected | binary64 spacing at a ns epoch is 256 ns |
| Returned on the first matching control message | Scans all, then applies precedence | `recvmsg` does not order control messages |

## Production implementation reference

- Reference code: `scripts/nic_timestamping.py` (`NICHardwareTimestamperEngine`,
  `TimestampSource`, `NICTimestampedPacket`, `AncillaryTimestamps`,
  `decode_scm_timestamping`).
- Automated unit tests: `scripts/test_nic_timestamping.py`.

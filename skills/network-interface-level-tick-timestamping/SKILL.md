---
name: network-interface-level-tick-timestamping
description: >-
  Use when a Linux feed handler must stamp ticks at wire arrival rather than in the
  application. Decodes SO_TIMESTAMPING ancillary messages and reads the NIC hardware
  stamp without mistaking a kernel software timestamp for it.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: real-time-architecture
  tags: real-time-architecture, nic-timestamping, so-timestamping, scm-timestamping, ptp-hardware-clock, solarflare, low-latency
  brokers_frameworks: "Linux SO_TIMESTAMPING (SCM_TIMESTAMPING); PTP hardware clock (ptp4l / phc2sys); Solarflare/AMD OpenOnload & sfptpd; Python socket.recvmsg"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a co-located feed handler records tick arrival times that must
reflect when the packet hit the wire, not when Python got around to reading it.
An application-level `time.time()` reading includes NIC-to-kernel DMA, softirq
scheduling, socket-buffer queueing and the interpreter's own context switches.
Linux exposes earlier capture points through `SO_TIMESTAMPING`, and this skill
covers decoding them correctly.

The one thing this skill exists to get right is **which capture point you actually
received**. Three different `SOL_SOCKET` control messages carry a timestamp, two of
them are byte-identical in size on a 64-bit host, and only one of them can ever
contain a hardware timestamp:

| `cmsg_type` | Enabled by | Payload | Size (LP64 / ILP32) | Capture point |
|---|---|---|---|---|
| `SCM_TIMESTAMP` (29 / 63) | `SO_TIMESTAMP` | `struct __kernel_old_timeval` | 16 / 8 | Kernel receive path, **microseconds** |
| `SCM_TIMESTAMPNS` (35 / 64) | `SO_TIMESTAMPNS` | `struct timespec` | 16 / 8 | Kernel receive path, nanoseconds |
| `SCM_TIMESTAMPING` (37 / 65) | `SO_TIMESTAMPING` | `struct scm_timestamping` (`timespec[3]`) | 48 / 24 | `ts[0]` software, `ts[1]` deprecated, **`ts[2]` hardware** |

Classifying these by buffer length instead of `cmsg_type` is the classic failure:
a 16-byte payload is a software timestamp either way, and a 48-byte payload decoded
from offset 0 yields `ts[0]` — the kernel software timestamp — which then gets
recorded and reported as a wire timestamp.

## When NOT to Use

- **On any non-Linux host.** `SO_TIMESTAMPING` is a Linux socket option. There is no
  portable equivalent, and the engine returns `False` from `enable_nic_timestamping`
  rather than pretending otherwise.
- **To subtract a hardware timestamp from a system-clock reading, without a
  disciplined PHC.** The kernel does not convert hardware timestamps to system time;
  `ts[2]` is read from the adapter's PTP hardware clock, an independent clock. Until
  `phc2sys`/`sfptpd` ties it to `CLOCK_REALTIME`, `T_app − T_hw` is a clock offset
  plus a capture delay, and routinely goes negative. See
  `clock-synchronization-ptp-for-trading-hosts`.
- **As evidence of MiFID II RTS 25 clock compliance.** Decoding a timestamp is not
  auditing one. Use `hardware-timestamping-vs-software-timestamping-accuracy` for the
  divergence/granularity audit and the documented timestamping point.
- **As a substitute for a latency budget.** Wire-to-decision decomposition across the
  whole path is `tick-to-trade-latency-measurement` and
  `strategy-latency-budget-decomposition`.
- **With a kernel-bypass stack that never touches a Linux socket.** Onload's
  `onload_timestamping` and equivalent bypass APIs deliver timestamps through the
  vendor library, not through `recvmsg` ancillary data; the layout facts here still
  apply, the socket plumbing does not.

## Prerequisites

- Linux host with an adapter whose driver supports receive hardware timestamping.
  **Verify before trusting any `HARDWARE_NIC` label**: `ethtool -T <iface>` must list
  `hardware-receive` / `SOF_TIMESTAMPING_RX_HARDWARE` in its capabilities. A driver
  without it still returns a populated `SCM_TIMESTAMPING` message — the hardware slot
  is simply zero.
- `ptp4l` disciplining the PHC to a grandmaster, and `phc2sys` (or `sfptpd`) relating
  the PHC to the system clock, if any cross-layer subtraction is to mean anything.
- `socket.recvmsg` with an ancillary buffer sized via
  `socket.CMSG_SPACE(48)` — `struct scm_timestamping` is 48 bytes on LP64, and an
  undersized `ancbufsize` truncates the control message rather than failing loudly.
- CPython's `socket` module exports none of `SO_TIMESTAMPING`, `SCM_TIMESTAMPING` or
  the `SOF_TIMESTAMPING_*` flags. The numeric constants must be supplied by the
  caller; `hasattr(socket, "SO_TIMESTAMPNS")` is `False` on every platform and must
  never be used to gate activation.

## Workflow

1. **Confirm the adapter can do it before enabling anything.** Run
   `ethtool -T <iface>`. Decision point: if `hardware-receive` is absent, do not ship
   a config that claims hardware timestamps — either fix the driver/NIC or set
   `use_hardware_timestamping=False` and record kernel software timestamps under
   their real name.

2. **Enable `SO_TIMESTAMPING`, not `SO_TIMESTAMPNS`.**
   ```python
   engine = NICHardwareTimestamperEngine(use_hardware_timestamping=True)
   if not engine.enable_nic_timestamping(sock):
       raise RuntimeError("hardware timestamping unavailable — do not start the feed")
   ```
   The engine sets `SOF_TIMESTAMPING_RX_HARDWARE | SOF_TIMESTAMPING_RAW_HARDWARE`
   (`0x44`). Decision point: never additionally enable `SO_TIMESTAMP` or
   `SO_TIMESTAMPNS` on the same socket — with `SOF_TIMESTAMPING_SOFTWARE` set, the
   kernel fabricates a substitute software timestamp into `ts[0]` when a real one is
   missing, and it is indistinguishable from a genuine capture.

3. **Receive with an adequately sized control buffer.**
   ```python
   payload, ancdata, flags, addr = sock.recvmsg(65535, socket.CMSG_SPACE(48))
   ```

4. **Decode by `cmsg_type`, and take the hardware timestamp from `ts[2]`.**
   ```python
   pkt = engine.process_packet_with_nic_timestamp(payload, ancdata, time.time_ns())
   ```
   Precedence is hardware (`ts[2]`) → kernel nanosecond → kernel microsecond →
   application clock. All control messages are scanned before one is chosen;
   `recvmsg` does not guarantee the hardware message arrives first. An all-zero
   `ts[2]` means the adapter did not stamp this packet, not that it stamped it at
   the epoch.

5. **Treat degradation as an incident, not a log line.** Decision point: if
   `pkt.degraded` is set, hardware timestamping was required and did not arrive —
   the tick stream's stated accuracy is no longer true. Alert; do not silently
   continue writing ticks that claim wire accuracy.

6. **Read `capture_delay_ns` as signed, and know which timebase it crosses.** When
   `pkt.cross_timebase_comparison` is `True` the figure mixes the PHC and
   `CLOCK_REALTIME`. A negative value is not a fast packet — it is an undisciplined
   PHC. Fix the clock before reading anything else from that batch.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Classifying the timestamp by control-buffer length.** On LP64 both
  `SCM_TIMESTAMPNS` (`timespec`) and `SCM_TIMESTAMP` (`timeval`) are exactly 16
  bytes. Decoding a `timeval` as a `timespec` reads `tv_usec` as nanoseconds and
  under-reports the sub-second component by a factor of 1000 — half a second becomes
  half a millisecond — while a size test also labels both as "hardware".
- **Reading `ts[0]` out of `struct scm_timestamping` and calling it hardware.**
  `ts[0]` is the software timestamp; the hardware one is `ts[2]`. `ts[1]` held
  hardware-converted-to-system-time and is deprecated — do not resurrect it.
- **Gating on `hasattr(socket, "SO_TIMESTAMPNS")`.** CPython never defines it, so the
  guard is always false and timestamping is silently never enabled. The failure looks
  exactly like a working system that simply never sees hardware timestamps.
- **Enabling `SO_TIMESTAMP`/`SO_TIMESTAMPNS` alongside `SO_TIMESTAMPING`.** With
  `SOF_TIMESTAMPING_SOFTWARE` requested, the kernel generates a false software
  timestamp in `ts[0]` during `recvmsg()` when a real one is missing.
- **Clamping the capture delay at zero.** `max(0.0, T_app − T_hw)` turns the single
  clearest symptom of an undisciplined PHC into a healthy-looking `0.0` and hides the
  PTP fault the rest of this skill is trying to surface.
- **Reading only the negative case as a clock fault.** A PHC that was never set counts
  from zero at boot, so `T_app − T_hw` comes back as a *positive* delay of roughly the
  current epoch — tens of years — rather than a negative one. Both directions are the
  same fault. Sanity-check the magnitude of `capture_delay_ns` whenever
  `cross_timebase_comparison` is `True`, using a bound derived from your own measured
  capture path rather than a number copied from a datasheet.
- **Carrying a nanosecond epoch in a float.** `int(time.time() * 1e9)` at a ~1.78e18
  ns epoch has 256 ns of binary64 spacing — coarser than the effect being measured.
  Use `time.time_ns()` and integers end to end.
- **Trusting a populated `SCM_TIMESTAMPING` message as proof of hardware capture.**
  The message is delivered whether or not the adapter stamped the packet. Confirm
  with `ethtool -T` and check that `ts[2]` is non-zero.
- **Sizing `ancbufsize` for a 16-byte timespec.** `struct scm_timestamping` needs
  `CMSG_SPACE(48)` on LP64; a smaller buffer truncates the control message and the
  hardware slot is the part that gets cut.
- **Assuming the constants are portable across architectures.** `SO_TIMESTAMPING_OLD`
  is 37 under `asm-generic` (x86-64, arm64, mips, alpha) but `0x0023` on sparc and
  `0x4020` on parisc. Pass the overrides rather than hard-coding.

## Verification

- Build a `struct scm_timestamping` with `ts[0]` at +900,000 ns and `ts[2]` at
  +400,000 ns and assert the selected `timestamp_ns` is the **+400,000** value with
  `source == HARDWARE_NIC`. A decoder reading offset 0 returns +900,000 here.
- Feed a 16-byte `SCM_TIMESTAMPNS` payload and assert `source == KERNEL_SOFTWARE`,
  not `HARDWARE_NIC`.
- Feed a 16-byte `SCM_TIMESTAMP` payload with `tv_usec = 500_000` and assert the
  decoded value is `base + 500_000_000` ns, not `base + 500_000` ns.
- Present `SCM_TIMESTAMPNS` ahead of `SCM_TIMESTAMPING` in the ancillary list and
  assert the hardware timestamp still wins.
- Set `ts[2]` 250 µs ahead of the application timestamp and assert
  `capture_delay_ns == -250_000` and `cross_timebase_comparison is True` — the value
  must be signed, not clamped.
- Assert an all-zero `ts[2]` yields `KERNEL_SOFTWARE` with `degraded is True`, and a
  32-byte (truncated) `SCM_TIMESTAMPING` payload yields `APPLICATION_FALLBACK`
  rather than a misdecoded number.
- Assert `tv_nsec == 1_000_000_000` is rejected, a float epoch raises `TypeError`,
  and a negative epoch raises `ValueError`.
- Assert `timestamping_flags()` is `68` (`1<<2 | 1<<6`) when hardware is required and
  `92` when software fallback is allowed, and that the default option number is 37,
  not 35.
- Run `python -m unittest discover -s skills/network-interface-level-tick-timestamping/scripts`.

## Related Skills

- `clock-synchronization-ptp-for-trading-hosts`
- `hardware-timestamping-vs-software-timestamping-accuracy`
- `clock-skew-correction-for-tick-timestamps`
- `tick-to-trade-latency-measurement`
- `binary-protocol-parsing-for-low-latency-feeds`
- `feed-handler-cpu-pinning-and-numa-awareness`

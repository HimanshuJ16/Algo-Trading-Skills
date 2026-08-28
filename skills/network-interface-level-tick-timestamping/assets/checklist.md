# Pre-Flight / Sign-off Checklist — network-interface-level-tick-timestamping

Use this before a feed handler is allowed to claim wire-accurate tick timestamps.

## Platform

- [ ] **Adapter capability:** `ethtool -T <iface>` lists `hardware-receive`. A populated
      `SCM_TIMESTAMPING` message is not evidence of hardware capture.
- [ ] **PHC discipline:** `ptp4l` is locked to a grandmaster and `phc2sys`/`sfptpd` is
      relating the PHC to `CLOCK_REALTIME`. Without it, no cross-layer subtraction is
      meaningful.
- [ ] **Architecture constants:** the `SO_*`/`SCM_*` numbers in use match this host's
      architecture (37 under `asm-generic`; `0x0023` sparc, `0x4020` parisc).

## Socket configuration

- [ ] **`SO_TIMESTAMPING` is the option being set** — not `SO_TIMESTAMPNS`, which cannot
      deliver a hardware timestamp.
- [ ] **Option number passed numerically**, not gated on `hasattr(socket, ...)`; CPython
      exports none of these constants and such a guard never fires.
- [ ] **`SOF_TIMESTAMPING_RX_HARDWARE | SOF_TIMESTAMPING_RAW_HARDWARE` requested**
      (`0x44`) when hardware timestamps are required.
- [ ] **`SO_TIMESTAMP`/`SO_TIMESTAMPNS` are NOT also enabled** on the same socket — the
      kernel fabricates a substitute software timestamp into `ts[0]` when they are.
- [ ] **A `False` return from `enable_nic_timestamping` aborts startup**, rather than
      being logged and ignored.

## Decoding

- [ ] **Classification is by `cmsg_type`, not payload length.** On LP64 a `timespec` and
      a `timeval` are both 16 bytes.
- [ ] **Hardware timestamps are read from `ts[2]`**, software from `ts[0]`; `ts[1]` is
      deprecated and ignored.
- [ ] **`SCM_TIMESTAMP` is converted from microseconds** (`tv_usec × 1000`), not read as
      nanoseconds.
- [ ] **All control messages are scanned** before a capture point is selected; `recvmsg`
      does not guarantee ordering.
- [ ] **`ancbufsize` is `CMSG_SPACE(48)`** on LP64, and `MSG_CTRUNC` is checked.
- [ ] **All-zero slots decode to "absent"**, not to an epoch timestamp.

## Reporting

- [ ] **Timestamps are integer nanoseconds end to end**; no float epoch anywhere in the
      path (`time.time_ns()`, not `time.time()`).
- [ ] **`capture_delay_ns` is signed and never clamped**, and
      `cross_timebase_comparison` is surfaced alongside it.
- [ ] **`source` is persisted with every tick**, so a stored timestamp's accuracy claim
      remains auditable.
- [ ] **`degraded` raises an operational alert**, not just a log line.

## Testing

- [ ] **Automated tests pass:**
      `python -m unittest discover -s skills/network-interface-level-tick-timestamping/scripts`
- [ ] **Repository validation passes:** `python tools/validate_skills.py`

## Sign-off

- Reviewed by: ___________________________
- Date: ___________________________

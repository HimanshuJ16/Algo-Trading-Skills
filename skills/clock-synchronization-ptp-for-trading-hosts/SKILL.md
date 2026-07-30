---
name: clock-synchronization-ptp-for-trading-hosts
description: Quantitative infrastructure management module for configuring, parsing,
  and validating PTP (IEEE 1588v2) hardware clock synchronization across trading host
  NICs and system CLOCK_REALTIME.
domain: Infrastructure
subdomain: Network & Hardware Architecture
tags:
- ptp
- ieee-1588
- ptp4l
- phc2sys
- hardware-timestamping
- hft
- mifid-ii
brokers_frameworks:
- linuxptp
- Generic Infrastructure
version: 1.0.0
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when deploying or auditing Linux trading hosts requiring sub-microsecond timestamping accuracy for high-frequency trading (HFT) or regulatory compliance (e.g. MiFID II RTS 25). PTP uses hardware timestamping on PTP-capable Network Interface Cards (NICs) to bypass operating system network stack jitter, driving `ptp4l` (NIC-to-Grandmaster sync) and `phc2sys` (NIC PHC to `CLOCK_REALTIME` sync).

## Prerequisites

- PTP-capable NIC (Network Interface Card with hardware timestamping support, e.g. Intel I210/E810, Solarflare/AMD).
- `linuxptp` package (`ptp4l` and `phc2sys` binaries) installed on host.
- PTP Grandmaster clock active on the local PTP network segment.

## Workflow

1. **Hardware Verification**: Verify NIC hardware timestamping capabilities using `ethtool -T <iface>`.
2. **Daemon Orchestration (`ptp4l`)**: Launch `ptp4l` in slave mode using Layer-2 transport (`-2`) and hardware timestamping (`-H`).
3. **System Clock Sync (`phc2sys`)**: Launch `phc2sys` to discipline `CLOCK_REALTIME` from the PTP Hardware Clock (PHC).
4. **Telemetry & Log Parsing**: Use `PtpClockSyncManager` to parse telemetry output from `ptp4l` and `phc2sys` in real time.
5. **Sync State Enforcement**: Extract current offset (ns/us), state (`SLAVE`, `PASSIVE`, `FAULTY`, `LISTENING`), path delay, and grandmaster ID. If offset exceeds safety thresholds (e.g., > 1µs for HFT or > 50µs for MiFID II), trip an alert or kill-switch.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Using NTP Instead of PTP for HFT**: Relying on software NTP over UDP, which experiences 1-10ms jitter due to OS scheduler interrupts.
- **Forgetting `phc2sys`**: Running `ptp4l` successfully (synchronizing the NIC's PHC) but failing to run `phc2sys`, leaving the Linux kernel `CLOCK_REALTIME` un-synchronized and drifting.
- **Software Timestamping Mode (`-S`)**: Accidentally invoking `ptp4l -S` instead of `-H`, which degrades precision from nanoseconds to tens of microseconds.

## Verification

- Feed raw `ptp4l` and `phc2sys` stdout/log streams into `PtpClockSyncManager`. Verify accurate extraction of offset, frequency adjustment (ppb), state, and threshold violation detection.
- Run `python scripts/test_ptp_clock_sync.py`.

## Related Skills

- `clock-drift-monitoring-alerting-thresholds`
- `clock-skew-correction-for-tick-timestamps`

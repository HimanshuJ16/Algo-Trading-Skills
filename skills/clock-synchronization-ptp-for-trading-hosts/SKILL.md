---
name: clock-synchronization-ptp-for-trading-hosts
description: Use when a Linux trading host must be disciplined to a PTP grandmaster
  and prove it — configuring ptp4l and phc2sys against the grandmaster's actual PTP
  profile, parsing linuxptp telemetry as it is really emitted (message tags, negative
  path delay, summary lines), and evaluating the grandmaster-to-PHC and PHC-to-CLOCK_REALTIME
  offsets as the serial error pair they are rather than picking the larger one.
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
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when standing up or auditing the **time-synchronization stack itself** on a
Linux trading host: NIC hardware timestamping, `ptp4l` (grandmaster → PTP Hardware Clock),
`phc2sys` (PHC → `CLOCK_REALTIME`), and the telemetry that evidences all three are working.

PTP (IEEE 1588) exists here because software NTP over UDP carries millisecond-scale OS
scheduler jitter and cannot evidence a microsecond-scale bound. Hardware timestamping on a
PTP-capable NIC takes the timestamp at the wire, outside the kernel network stack.

`PtpClockSyncManager` in `scripts/` parses `ptp4l`/`phc2sys` output and returns a
fail-closed sync verdict. It is a **telemetry reader**, not a controller.

## When NOT to Use

- **As the enforcement point.** This module reports; it does not halt trading. Threshold
  escalation, holdover grace and the latched kill-switch belong to
  `clock-drift-monitoring-alerting-thresholds`. Do not build a second, divergent set of
  thresholds here.
- **As proof that timestamps are correct.** A perfectly synchronized clock on the wrong
  timescale reads as green — see the TAI/UTC pitfall below. Offset telemetry measures
  agreement with the source, never correctness of the source or the timescale.
- **To repair already-written timestamps.** That is
  `clock-skew-correction-for-tick-timestamps`.
- **As a substitute for the RTS 25 Article 4 traceability system.** Article 4 requires a
  documented, annually reviewed traceability system — design, functioning, specifications,
  and the exact point at which each timestamp is applied. This is evidence produced inside
  that system, not the system.
- **On a host without a PTP-capable NIC.** Software timestamping (`ptp4l -S`) degrades
  accuracy by orders of magnitude and cannot support a 100 µs claim. If the NIC has no PHC,
  the answer is different hardware, not different flags.
- **Under concurrency, unguarded.** `PtpClockSyncManager` is not thread-safe. Drive it from
  one reader loop or wrap it in your own lock.

## Prerequisites

- A PTP-capable NIC. Confirm with `ethtool -T <iface>` — you need `SOF_TIMESTAMPING_TX_HARDWARE`,
  `SOF_TIMESTAMPING_RX_HARDWARE`, `SOF_TIMESTAMPING_RAW_HARDWARE` and a non-`none` PTP
  Hardware Clock index. This is a hardware property; no flag substitutes for it.
- `linuxptp` (`ptp4l`, `phc2sys`) installed, and **the grandmaster's PTP profile documented**:
  transport, domain number, delay mechanism and message rates. Every one of these must match
  or the port never leaves `LISTENING`.
- Physical path to the grandmaster whose asymmetry you can characterise. PTP assumes a
  symmetric path; every nanosecond of asymmetry becomes half a nanosecond of fixed offset
  error that the servo cannot see and will never correct.
- An out-of-band UTC reference for periodic timescale verification (see Pitfalls).
- The divergence ceiling that binds **your** activity and jurisdiction — not a default.
  See `references/standards.md`.

## Workflow

1. **Verify the hardware before writing any config.** `ethtool -T <iface>`. If the PTP
   Hardware Clock index is `none`, stop: everything downstream is software timestamping
   wearing a PTP costume.

2. **Take the transport and domain from the grandmaster, not from a tutorial.** `linuxptp`
   defaults to `network_transport UDPv4`; `-2` selects IEEE 802.3 (L2). Neither is
   universally right — the Enterprise Profile (RFC 9760) *mandates* UDP over IPv4/IPv6,
   while the ITU-T telecom profile G.8275.1 uses L2 multicast. Choosing `-2` because a
   document said so, against a UDP grandmaster, produces a host that never synchronizes.
   `domainNumber` must likewise match; a mismatched domain looks identical to "no
   grandmaster on the segment".

3. **Run `ptp4l` client-only with hardware timestamping.** `-H` is already the linuxptp
   default; state it anyway so a config change cannot silently demote you. `-s` enables
   `clientOnly` (the old `slaveOnly` spelling is deprecated) and is what stops a trading
   host from ever advertising itself as a time source to its own venue segment.

4. **Run `phc2sys` with `-w`, and understand why.** `ptp4l` keeps the PHC on the **PTP
   timescale (TAI)**, which has no leap seconds. Per `phc2sys(8)`, `-w` (absent `-O`) keeps
   the sink-to-source offset updated from the `currentUtcOffset` obtained from `ptp4l`.
   Omit both and `CLOCK_REALTIME` is disciplined onto TAI — a whole number of seconds away
   from UTC — while every offset reads in the single nanosecond digits.

5. **Confirm the port state transition, not just the offset.** A healthy client walks
   `INITIALIZING → LISTENING → UNCALIBRATED → SLAVE`, and the servo walks `s0` (unlocked) →
   `s1` (clock step) → `s2` (locked). An offset printed in `s0` is a measurement, not a
   synchronization; treat it as unlocked no matter how small the number.

6. **Feed both daemons' output to `PtpClockSyncManager`, and set `max_sample_age_s`.**
   The manager's state is sticky by construction: with staleness detection off, a daemon
   that dies leaves the last `s2` and the last good offset latched forever and the host
   keeps reporting compliant with no time synchronization at all. Set the age to your log
   interval times a tolerance.

7. **Judge on `combined_offset_ns`, not `max_offset_ns`.** The grandmaster→PHC offset and
   the PHC→`CLOCK_REALTIME` offset are **serial** error terms: an event stamped from
   `CLOCK_REALTIME` carries both. 60 µs on each leg is 120 µs on the record while the
   maximum reads a comfortable 60 µs.

8. **Verify the timescale out of band, on a schedule.** Compare `CLOCK_REALTIME` against an
   independent UTC source. This is the only check that catches a correct-looking sync to a
   wrong or spoofed grandmaster, and it is the check the offset telemetry structurally
   cannot perform.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **`phc2sys` without `-w` or `-O`: a 37-second error that reports as zero.** The PHC is on
  TAI. Without the UTC offset, `CLOCK_REALTIME` is disciplined to TAI and every timestamp
  the firm records is a whole number of seconds from UTC — a gross RTS 25 failure — while
  `ptp4l`, `phc2sys` and this module all report nanosecond agreement. This is the single
  most dangerous way to pass every green check and be entirely non-compliant.
- **Taking the maximum of the two offsets.** They are serial, not alternatives. The
  maximum is not a bound on the error of the recorded timestamp; the sum of magnitudes is.
- **Treating a silent daemon as a healthy one.** The common failure is not a drifting
  clock, it is a dead `ptp4l`. A parser with no staleness deadline never notices, because
  nothing arrives to trigger it.
- **A parser that drops the lines that matter.** `path delay` **can be negative** in real
  logs, and a negative path delay is a symptom of bad hardware timestamps. A regex that
  requires unsigned digits rejects the whole line, so the manager keeps serving the last
  good sample and the fault is invisible. The same applies to `message_tag`, which most
  orchestrated deployments set — it inserts text between the daemon timestamp and the
  keyword and silently blinds a rigid pattern.
- **Reading `rms` from a summary line as if it were an offset.** `summary_interval` and
  `phc2sys -u` emit `rms N max M` lines with **no servo state**. RMS is an average
  magnitude; the divergence ceiling is a bound on the worst case, so compare `max`.
- **Running `ptp4l` without `-s`/`clientOnly`.** A trading host that wins a BMCA election
  starts serving time to the segment from its own free-running oscillator.
- **Forgetting `phc2sys` entirely.** `ptp4l` alone disciplines the NIC's PHC. If the
  application stamps from `CLOCK_REALTIME`, that clock is still free-running.
- **Assuming `-2` because the document said so.** Transport, domain and delay mechanism are
  properties of the grandmaster's profile. Guessing produces a port stuck in `LISTENING`,
  which is at least loud — guessing the *domain* on a shared segment can instead sync you
  to the wrong grandmaster, which is not.
- **Ignoring path asymmetry.** PTP cannot observe it. A different fibre length or an
  asymmetric switch path in each direction is a fixed, uncorrectable offset that no amount
  of servo tuning removes, and it does not appear in the offset telemetry.
- **Assuming 100 µs is "the clock rule".** It is one row of one EU table. Non-HFT EU algo
  flow is bound to 1 ms; a US CAT reporter is bound by FINRA Rule 6820 to 50 ms — 500×
  looser. Enforcing 100 µs on flow it does not bind manufactures outages, not compliance.

## Verification

- Parse `ptp4l[600.1]: [ens1f0] master offset 88 s2 freq -25937 path delay -2391`; confirm
  the sample parses, with `path_delay_ns == -2391`. A rigid parser drops this line.
- Feed 61,000 ns from `ptp4l` and 45,000 ns from `phc2sys`, both `s2`; confirm
  `max_offset_ns == 61000`, `combined_offset_ns == 106000` and `mifid_compliant is False`.
- Feed only `phc2sys` telemetry; confirm `is_synced is False` and reason
  `no ptp4l telemetry` — never a passing verdict on half a stack.
- With `max_sample_age_s=5.0`, feed a locked pair, advance the injected clock past the
  deadline, and confirm the verdict flips to non-compliant with `telemetry_stale`.
- Feed `port 1: SLAVE to FAULTY on FAULT_DETECTED` after a locked pair; confirm the verdict
  fails on the port state even though the last offsets were small.
- Run `python -m unittest discover -s skills/clock-synchronization-ptp-for-trading-hosts/scripts`.

## Related Skills

- `clock-drift-monitoring-alerting-thresholds`
- `clock-skew-correction-for-tick-timestamps`
- `cross-datacenter-clock-sync-validation`
- `hardware-timestamping-vs-software-timestamping-accuracy`
- `network-interface-level-tick-timestamping`
- `mifid-ii-algo-trading-compliance-eu`

---
name: clock-drift-monitoring-alerting-thresholds
description: >-
  Use when a host stamping reportable events must prove its clock stays inside the MiFID
  II RTS 25 divergence limit for that activity, evaluating PTP offset and daemon state
  and latching a halt on breach. Disciplining the clock is a separate skill.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: market-microstructure-latency
  tags: mifid-ii, rts-25, clock-drift, ptp, hft, compliance, kill-switch
  brokers_frameworks: "Linux PTP; Generic Infrastructure"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when a host that stamps **reportable events** must demonstrate traceability
to UTC inside a stated tolerance, and you need that tolerance enforced automatically
rather than reviewed after the fact.

Pick the threshold from the activity, not from the headline number. RTS 25
(Commission Delegated Regulation (EU) 2017/574) Annex Table 2 binds members and
participants of EU trading venues by *type of trading activity*:

| Activity | Max divergence from UTC | Granularity |
|---|---|---|
| High frequency algorithmic trading technique | **100 µs** | 1 µs or better |
| Any other trading activity | **1 ms** | 1 ms or better |
| Voice / RFQ with human intervention / negotiated transactions | 1 s | 1 s or better |

The 100 µs default this skill ships is the HFT row of one EU table. It is **not** a
universal clock rule: a US CAT reporter is bound by FINRA Rule 6820 to 50 **milliseconds**
against NIST — 500× looser — and non-HFT EU algo flow is bound to 1 ms. Configure
`critical_threshold_us` from the row that applies to you. Enforcing 100 µs on a book that
is not running an HFT technique into an EU venue buys no compliance and manufactures
outages.

## When NOT to Use

- **As a substitute for a time-sync stack.** This measures and reacts; it does not
  discipline anything. Configuring `ptp4l`/`phc2sys`, NIC hardware timestamping and
  grandmaster selection belongs to `clock-synchronization-ptp-for-trading-hosts`.
- **To repair timestamps already written.** A halt stops the bleeding. Correcting event
  times recorded during the breach window is `clock-skew-correction-for-tick-timestamps`.
- **For US-only trading, at these defaults.** See the table above; run it at
  `CAT_MAX_DIVERGENCE_US`, and note that FINRA also requires a daily pre-open sync check,
  which is a procedure this monitor does not perform.
- **As the sole justification of RTS 25 compliance.** Article 4 requires a documented,
  annually reviewed traceability system — system design, functioning, specifications, and
  the exact point at which each timestamp is applied. A monitor is evidence within that
  system, not the system.
- **Under concurrency, unguarded.** `ClockDriftMonitor` is not thread-safe. Drive it from
  one polling loop or wrap it in your own lock.

## Prerequisites

- A PTP daemon on the host (`ptp4l`, plus `phc2sys` if events are stamped from
  `CLOCK_REALTIME` rather than the PHC) emitting parseable offset telemetry.
- A mapping from your daemon's states to `PtpState`. `ptp4l` has no `HOLDOVER` state of
  its own — it reports IEEE 1588 port states (`SLAVE`, `UNCALIBRATED`, `FAULTY`, …) and
  servo states (`s0` unlocked, `s1` clock step, `s2` locked). `references/workflows.md`
  gives the mapping this skill assumes.
- A **measured** holdover drift rate for the host oscillator, in µs/s. Without it,
  `holdover_grace_s` must stay at its fail-closed default of `0.0`.
- A kill-switch entry point the monitor can call synchronously, and an out-of-band alert
  path that does not depend on the trading engine still being up.

## Workflow

1. **Convert units at the boundary.** `ptp4l` logs `master offset` in **nanoseconds**;
   this module takes microseconds. Call `offset_us_from_ptp4l_ns()` rather than passing
   the raw figure — see Pitfalls for what happens if you don't.

2. **Poll, and validate before you compare.** `process_telemetry(offset_us, ptp_state)`
   rejects a non-finite offset with `ClockTelemetryError` instead of classifying it.
   Catch that exception in the polling loop and treat it as a fault, not as a skipped
   reading: an offset you could not parse is a clock state you do not know.

3. **Evaluate state before offset.** An `UNLOCKED` servo halts regardless of the number
   it reported, because an unlocked servo's offset is meaningless. `HOLDOVER` starts a
   grace timer on a **monotonic** clock — never the wall clock, which is the thing under
   suspicion — and returns `WARNING` while inside grace so a lost grandmaster is visible
   to operations immediately rather than at expiry.

4. **Evaluate the offset.** `|offset| ≥ critical` latches the halt and fires the callback
   once; `|offset| ≥ warning` alerts without blocking, and re-logs only on transition into
   the warning state, so a host sitting at 60 µs does not generate an alarm per poll.

5. **Check liveness on every tick, including empty ones.** Call `check_liveness()` even
   when no telemetry was read. `process_telemetry` is reactive and cannot observe absence;
   a crashed `ptp4l` emits no offsets at all, and with `max_telemetry_age_s` unset that
   silence reads as continued health.

6. **Resume deliberately.** The halt latches. `reset(operator, reason)` requires both
   arguments and logs them at CRITICAL, because resuming order origination after a clock
   breach is a compliance decision that has to be attributable at the annual Article 4
   review.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Feeding raw `ptp4l` nanoseconds into a microsecond threshold.** A real 120 µs breach
  arrives as `120` and reads HEALTHY — the monitor runs green forever while the firm is
  continuously non-compliant. This is the single most likely way to deploy this skill and
  get nothing from it.
- **Letting a NaN offset mean "fine".** `abs(nan) >= 100` is `False` and so is
  `abs(nan) >= 50`, so an unvalidated bad parse falls through every threshold and returns
  HEALTHY. Absence of a breach signal is not evidence of a healthy clock.
- **Treating silence as health.** The common failure is not a drifting clock, it is a
  dead daemon. A monitor with no staleness deadline never fires, because nothing calls it.
- **Ignoring HOLDOVER because the offset still looks small.** On entering holdover the
  reported offset is against a grandmaster that is no longer there. It looks excellent
  right up until the local oscillator walks past the limit, and how long that takes is a
  hardware property you must measure, not assume.
- **Setting the halt threshold exactly at the regulatory limit.** By the time drift
  reaches 100 µs, non-compliant timestamps have already been written. The regulatory
  number is the ceiling, not the alarm point — leave headroom for detection and halt
  latency.
- **Alert fatigue from a too-tight warning level.** A 5 µs warning on a network with
  ordinary jitter trains operations to ignore the channel, so the real breach lands in a
  muted room.
- **Monitoring software clocks only.** NTP over UDP carries millisecond-scale OS jitter;
  it cannot evidence a 100 µs bound. Hardware timestamping at the NIC is the prerequisite,
  not an optimization.
- **Assuming the kill switch worked.** If the callback raises, the monitor stays latched
  and re-raises — but the *engine* may still be live. Treat a failed callback as a manual
  escalation, not a logged warning.

## Verification

- Feed a 120 µs offset with `PtpState.LOCKED`; confirm `CRITICAL`, one callback
  invocation, and that a subsequent 10 µs reading still returns `CRITICAL` until `reset`.
- Feed `PtpState.HOLDOVER` at a 1 µs offset with `holdover_grace_s=30`; confirm `WARNING`
  before expiry and `CRITICAL` after, using an injected monotonic clock rather than sleeps.
- Feed `float("nan")`; confirm `ClockTelemetryError` and that the monitor did **not** halt
  and did **not** report HEALTHY.
- Stop feeding telemetry with `max_telemetry_age_s` set; confirm `check_liveness()` halts.
- Run `python -m unittest discover -s skills/clock-drift-monitoring-alerting-thresholds/scripts`.

## Related Skills

- `clock-synchronization-ptp-for-trading-hosts`
- `clock-skew-correction-for-tick-timestamps`
- `cross-datacenter-clock-sync-validation`
- `execution-algorithm-kill-switch-integration`
- `mifid-ii-algo-trading-compliance-eu`

# Business Clock Accuracy: Sources and Scope

Every figure below is scoped to a jurisdiction and a rule. Clock-accuracy limits are
jurisdiction-specific and differ by three orders of magnitude between regimes, so none of
them generalizes. Verify against the current text before relying on any of it.

## 1. EU — MiFID II RTS 25

**Commission Delegated Regulation (EU) 2017/574** of 7 June 2016, supplementing Directive
2014/65/EU with regard to regulatory technical standards for the level of accuracy of
business clocks.
<https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:32017R0574>

**Article 3 + Annex Table 2 — members or participants of a trading venue.** Business
clocks used to record the time of reportable events must adhere to:

| Type of trading activity | Maximum divergence from UTC | Granularity of the timestamp |
|---|---|---|
| High frequency algorithmic trading technique | 100 microseconds | 1 microsecond or better |
| Voice trading systems | 1 second | 1 second or better |
| Request for quote systems where the response requires human intervention or where the system does not permit algorithmic trading | 1 second | 1 second or better |
| Concluding negotiated transactions | 1 second | 1 second or better |
| Any other trading activity | 1 millisecond | 1 millisecond or better |

A firm engaging in several of these activities must meet the level of accuracy applicable
to **each**, per system.

**Article 2 + Annex Table 1 — operators of trading venues.** Scoped by gateway-to-gateway
latency of the trading system, not by the member's activity: latency > 1 ms → 1 ms
divergence / 1 ms granularity; latency ≤ 1 ms → 100 µs divergence / 1 µs granularity.
This table binds venues, not members, and is included only so the two are not confused.

**Article 4 — compliance and traceability.** Entities must establish a system of
traceability to UTC and be able to demonstrate it by documenting the system's design,
functioning and specifications; identify the exact point at which a timestamp is applied;
and **review compliance of the traceability system at least once a year**. Note the shape
of the obligation: the monitor in this skill is evidence produced *inside* that system,
not a substitute for documenting it.

**Granularity is a separate requirement from divergence.** Meeting 100 µs divergence
while recording timestamps at millisecond granularity still fails Table 2. This module
enforces divergence only; granularity is a property of the recording path.

## 2. US — CAT / FINRA

**FINRA Rule 6820 (Clock Synchronization)** — Industry Members must synchronize Business
Clocks used to record CAT reportable events to within a **50 millisecond** tolerance of
the NIST atomic clock; clocks used solely for Manual Order Events, within 1 second.
<https://www.finra.org/rules-guidance/rulebooks/finra-rules/6820>

**FINRA Rule 4590** covers member business clocks where Rule 6820 does not; the same
50 ms / 1 s split applies, with clocks to be synchronized each business day before market
open and re-checked through the day.
<https://www.finra.org/rules-guidance/rulebooks/finra-rules/4590>

No microsecond-level tolerance appears in either rule. **The US requirement is 500× looser
than the EU HFT requirement.** Any claim that CAT imposes "similar" microsecond accuracy
is wrong, and configuring a US-only stack at 100 µs produces spurious halts with no
compliance benefit.

## 3. Linux PTP telemetry — units and states

From the `linuxptp` documentation as reproduced in the Red Hat and SUSE system tuning
guides:
<https://doc.opensuse.org/documentation/leap/tuning/html/book-tuning/cha-tuning-ptp.html>

- `master offset` is reported in **nanoseconds**. `path delay` likewise; `freq` is in
  parts per billion.
- Servo states in the log line: `s0` unlocked, `s1` clock step, `s2` locked.
- IEEE 1588 **port** states include `INITIALIZING`, `LISTENING`, `UNCALIBRATED`, `SLAVE`
  (also `PRE_MASTER`, `MASTER`, `PASSIVE`, `FAULTY`, `DISABLED`). The transition
  `UNCALIBRATED` → `SLAVE` marks successful synchronization to a master.
- **There is no `HOLDOVER` port state in `ptp4l`.** Holdover is a grandmaster / boundary
  clock concept (see ITU-T G.8273.2 for telecom profile holdover specifications). The
  `PtpState.HOLDOVER` value in this skill is a normalized abstraction that the integrator
  maps onto — typically loss of the selected grandmaster with the servo no longer
  receiving Sync messages.

## 4. Figures this skill does *not* source

The following are **engineering targets, not standards**. No regulator publishes them, and
they must be set from measurement of your own stack:

- **Detection-to-halt latency.** How fast the monitor must reach the engine is a function
  of your order rate and the headroom between your alarm point and the regulatory ceiling
  — not a published number. Measure it, budget it, and set the alarm point below the
  ceiling by at least the drift accumulated over that latency.
- **Holdover grace period.** Derive as `critical_threshold_us / drift_us_per_second`,
  where the drift rate comes from the oscillator's datasheet *validated by measurement*
  under your rack's thermal conditions. There is no safe generic default, which is why
  `holdover_grace_s` fails closed at `0.0`.
- **Warning threshold.** A trade-off between early notice and alert fatigue, set from the
  observed offset distribution of the specific network segment.

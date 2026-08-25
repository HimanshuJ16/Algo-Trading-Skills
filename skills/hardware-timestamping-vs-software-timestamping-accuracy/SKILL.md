---
name: hardware-timestamping-vs-software-timestamping-accuracy
description: >-
  Benchmarking and audit engine that separates NIC hardware (MAC/PHY) clock divergence from UTC from
  kernel and user-space capture-path delay, reports capture jitter distributionally, and audits the
  recorded timestamp against the applicable MiFID II RTS 25 Annex divergence AND granularity bounds.
domain: Market Microstructure & Latency
subdomain: Hardware NIC Timestamping & MiFID II Compliance
tags: ["hardware-timestamping", "solarflare", "so-timestamping", "ptp-hardware-clock", "mifid-ii", "rts-25", "capture-jitter", "clock-divergence"]
brokers_frameworks: ["Solarflare/AMD OpenOnload & sfptpd", "Linux SO_TIMESTAMPING", "PTP IEEE 1588 / phc2sys", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a co-located gateway or tick-capture host records event timestamps that must
withstand a MiFID II RTS 25 clock-traceability review, and you need to know **which layer's
timestamp** is worth recording. The engine takes one packet observed at three capture points —
NIC MAC/PHY, kernel `SO_TIMESTAMPING`, user-space `clock_gettime()` — plus a traceable UTC
reference for the same arrival, and separates two quantities that are routinely conflated:

- **Clock divergence from UTC** — how far a clock *reads* from true UTC at the instant a timestamp
  is applied. This is the quantity RTS 25 Arts. 2–3 and the Annex tables bound.
- **Capture-path delay** — the elapsed time between the packet hitting the MAC and a later layer
  observing it. This is real latency, not clock error.

A user-space timestamp taken 120 µs after arrival is not "120 µs of clock drift" — the clock may be
perfectly disciplined. It matters because whichever layer you *record* carries both errors, which is
precisely why RTS 25 Art. 4 requires the point at which a timestamp is applied to be documented.
The engine makes that point an explicit input and audits the recorded value against both the
divergence bound and the co-equal granularity bound of the applicable Annex row.

## When NOT to Use

- **As proof of RTS 25 compliance.** Art. 4 requires a documented traceability chain to UTC, a
  documented system design, a stated timestamping point, and an annual review. A passing verdict
  here is evidence for that file, not a substitute for it.
- **Without an independent UTC reference.** `utc_reference_nanos` must come from a source
  independent of the host being audited — a GNSS-disciplined capture appliance on a tap, or the
  hardware timestamp corrected by the PHC-to-UTC offset the PTP daemon reports. Feeding the host's
  own `CLOCK_REALTIME` back in compares a clock against itself and always reports near-zero error.
- **As a clock-synchronization monitor.** Continuous PHC/system-clock offset telemetry is a PTP
  daemon's job — see `clock-drift-monitoring-alerting-thresholds` and
  `clock-synchronization-ptp-for-trading-hosts`. This engine audits captured samples.
- **To compare timestamps taken on different hosts or feeds.** The layers here are three
  observations of one packet on one host. Cross-vendor or cross-venue timestamp comparison is
  `cross-vendor-timestamp-precision-reconciliation`.
- **Outside the EU regime, unmodified.** The Annex figures encoded here are MiFID II. US CAT and
  FINRA Rule 4590 use different tolerances; do not reuse the 100 µs row for them.

## Prerequisites

- Three same-packet timestamps plus a traceable UTC reference, all as **integer nanoseconds on a
  common timebase**. On Linux this is not automatic: the kernel does not convert NIC hardware
  timestamps to system time, and the adapter's PTP hardware clock (PHC) is an independent clock.
  Run `phc2sys`/`sfptpd` and confirm it before trusting any cross-layer subtraction.
- The **timestamping point** actually written into the reportable record
  (`HARDWARE_MAC`, `KERNEL_STACK`, or `APPLICATION`).
- The **granularity** the recorded timestamp field actually resolves (e.g. `1_000` ns for a
  microsecond column) — RTS 25 bounds this separately from divergence.
- The **Annex row** that applies to the entity and activity being audited (see
  `references/standards.md`); 100 µs is not a universal figure.

## Workflow

1. **Select the applicable RTS 25 row** — instantiate `TimestampAccuracyAnalyzerEngine` with a
   `trading_activity` key. Decision point: the 100 µs / 1 µs pair applies to members using a
   high-frequency algorithmic trading technique (Art. 3, Table 2) and to venue operators whose
   gateway-to-gateway latency is 1 ms or below (Art. 2, Table 1). Any other trading activity is
   1 ms / 1 ms; voice, human-intervention RFQ and negotiated transactions are 1 s / 1 s. Overrides
   are accepted only if **stricter** than the Annex row — an internal target may be tightened, a
   regulatory bound may never be relaxed.
2. **Declare the timestamping point** — `recorded_timestamp_source` names the layer whose value
   enters the record. The same packet can be compliant recorded at the MAC and non-compliant
   recorded in user space; the verdict follows this declaration, not a default.
3. **Ingest and validate the sample** — `PacketTimestampSample` rejects non-`int` values (a float
   cannot hold a nanosecond epoch: at ~1.7e18 ns the float64 spacing is 256 ns), negative epochs,
   and blank ids.
4. **Timebase guard** — if a later capture point precedes an earlier one
   ($\Delta t_{\text{kernel}} < 0$ or $\Delta t_{\text{app}} < 0$), `analyze_sample` raises rather
   than reporting a negative latency. Decision point: this is the observable signature of an
   undisciplined PHC, not a fast packet — fix the clock configuration before reading any figure
   from this batch.
5. **Decompose** — capture-path delays $\Delta t_{\text{kernel}} = T_{\text{kernel}} - T_{\text{hw}}$,
   $\Delta t_{\text{app}} = T_{\text{app}} - T_{\text{kernel}}$, and their sum; then **signed**
   errors versus UTC for each layer, $T_{\text{layer}} - T_{\text{UTC\_ref}}$. The sign is retained:
   a clock *ahead* of UTC stamps events before they happened, which is a different fault from one
   running behind.
6. **Audit** — the recorded layer's error is compared on magnitude against the divergence bound, and
   the field granularity against the granularity bound. `rts25_verdict` distinguishes
   `COMPLIANT`, `NON_COMPLIANT_DIVERGENCE`, `NON_COMPLIANT_GRANULARITY`, and
   `NON_COMPLIANT_DIVERGENCE_AND_GRANULARITY`. A separate four-state `status` compares the hardware
   and application layers, including the case where hardware fails while the application timestamp
   happens to land inside the limit.
7. **Benchmark the distribution** — `analyze_batch` returns p50/p99/max of |divergence| and of the
   recorded error, p50/p99 of the capture delays, and peak-to-peak capture jitter, plus breach
   counts and the worst packet. Decision point: RTS 25 bounds the divergence at the instant *every*
   timestamp is applied, so a healthy median proves nothing — read the p99 and the breach count.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Calling capture-path delay "clock drift"**: `abs(T_app - T_utc_ref)` is clock divergence *plus*
  elapsed processing time. Reporting it as UTC drift blames the PTP grandmaster for a scheduler
  stall, and sends an engineer to re-tune a clock that was never wrong.
- **Subtracting across two timebases**: the kernel does not convert NIC hardware timestamps to
  system time. Without `phc2sys`/`sfptpd`, $T_{\text{kernel}} - T_{\text{hw}}$ measures the offset
  between the PHC and `CLOCK_REALTIME`, not the kernel stack delay — and the result can be negative,
  which no packet ever is.
- **Taking `abs()` of the divergence**: it hides whether the clock is ahead of or behind UTC, which
  is the part that identifies the fault.
- **Treating 100 µs as the MiFID II number**: it is one row of one table. Applying it to activity
  the Annex puts at 1 ms manufactures failures; applying a 1 ms tolerance to HFT-technique activity
  hides real ones.
- **Auditing divergence and ignoring granularity**: a clock held within 100 µs of UTC still breaches
  RTS 25 if the recorded field only resolves milliseconds. The two bounds are co-equal.
- **Assuming RTS 25 mandates PTP or nanoseconds**: it is technology-neutral and names no protocol;
  its finest granularity row is 1 µs. PTP/GNSS is how the 100 µs tier is usually reached in
  practice, not what the text requires.
- **Reading `SO_TIMESTAMPING` output as hardware timestamps**: if the adapter or driver does not
  support hardware timestamping the software path still returns a value, and the kernel can generate
  a substitute software timestamp. Confirm the reported capabilities (`ethtool -T`) rather than
  inferring hardware capture from a populated field.
- **Certifying on one packet, or on a median**: divergence and capture delay are distributions. One
  sample cannot characterise jitter, and a p50 well inside the limit routinely hides a p99 outside it.
- **Feeding the host's own clock in as the UTC reference**: the comparison then measures nothing and
  always passes.

## Verification

- Analyze a packet with the hardware clock 10 µs ahead of UTC, a 10 µs kernel delay and a 115 µs
  application delay: assert `hardware_clock_divergence_nanos == 10_000`,
  `software_capture_delay_nanos == 125_000`, and `application_timestamp_error_nanos == 135_000` —
  the 125 µs is elapsed time, not clock error.
- Regression: hardware clock 150 µs **behind** UTC with 120 µs of capture delay. Assert
  `status == "HARDWARE_EXCEEDS_APPLICATION_WITHIN_LIMIT"` and
  `application_timestamp_error_nanos == -30_000`. The previous three-state classifier returned
  `BOTH_NON_COMPLIANT` here and asserted in the audit note that both layers exceeded 100 µs.
- Run the same sample with `recorded_timestamp_source="HARDWARE_MAC"` and `"APPLICATION"` and assert
  the verdict flips from `COMPLIANT` to `NON_COMPLIANT_DIVERGENCE`.
- Assert a divergence of exactly 100,000 ns is within tolerance and 100,001 ns is not, on both signs.
- Submit a sample with a kernel timestamp preceding the hardware timestamp and assert `ValueError`.
- Submit a compliant clock with `timestamp_granularity_nanos=1_000_000` and assert
  `NON_COMPLIANT_GRANULARITY`; add a divergence breach and assert
  `NON_COMPLIANT_DIVERGENCE_AND_GRANULARITY`.
- Assert 500 µs of divergence fails `HIGH_FREQUENCY_ALGORITHMIC_TRADING` and passes
  `OTHER_TRADING_ACTIVITY`; assert `max_divergence_nanos=200_000` raises.
- Batch five packets with capture delays 10/20/30/40/500 µs and assert nearest-rank
  `software_capture_delay_p50_nanos == 30_000`, `p99 == 500_000`, and
  `software_capture_delay_jitter_peak_to_peak_nanos == 490_000`.
- Run `python -m unittest discover -s skills/hardware-timestamping-vs-software-timestamping-accuracy/scripts`.

## Related Skills

- `clock-synchronization-ptp-for-trading-hosts`
- `network-interface-level-tick-timestamping`
- `clock-drift-monitoring-alerting-thresholds`
- `cross-vendor-timestamp-precision-reconciliation`
- `tick-to-trade-latency-measurement`
- `mifid-ii-algo-trading-compliance-eu`

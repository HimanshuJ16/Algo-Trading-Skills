---
name: market-data-latency-monitoring-per-vendor
description: >-
  Per-vendor market data latency auditing that decomposes an exchange-to-application tick path into vendor-transport, network-wire and app-queue segments, attributes the tail to the segment that actually caused it, and refuses to publish percentiles for a window whose four clock domains disagree.
domain: Market Data & Vendor Infrastructure
subdomain: Latency Decomposition & Vendor SLA Auditing
tags: ["market-data", "latency-monitoring", "vendor-sla", "clock-domain", "tail-attribution", "hardware-timestamping", "percentiles", "jitter"]
brokers_frameworks: ["Nasdaq TotalView-ITCH 5.0", "CME MDP 3.0", "HdrHistogram", "Linux PTP Hardware Clock", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when a market data feed is suspected of being slow and the question is
**which party owns the delay**. A tick is stamped at four points — exchange, vendor
gateway, local NIC, application thread — and the three intervals between them are the
only place a "the feed got slow" complaint can be pinned on someone. This module pools
samples per vendor, decomposes each tick into `VENDOR_TRANSPORT`, `NETWORK_WIRE` and
`APP_QUEUE`, computes nearest-rank percentiles and jitter over the end-to-end total,
audits a configurable percentile against a budget, and names the segment responsible for
the tail.

The subtraction is trivial. The reason this skill exists is that **the four timestamps
are not on one clock** — they are on four, owned by three organisations, and nothing in
the arithmetic notices when they disagree:

1. **The venue's epoch is a venue convention.** Nasdaq TotalView-ITCH 5.0 carries
   "nanoseconds since midnight" with no date and no timezone in the field; CME MDP 3.0
   carries a sending time in nanoseconds since the Unix epoch. Subtract one from the
   other unconverted and you get a delta of decades.
2. **The NIC is on its own clock.** A raw hardware receive timestamp comes from the
   NIC's PTP hardware clock, not the system clock. Linux deprecated the kernel-side
   translation between them and expects userspace to convert or discipline.
3. **A negative segment is therefore normal, and it is fatal.** It is not a small
   latency; it is proof that the two clocks bracketing that segment disagree — which
   means the *positive* deltas from the same window are wrong by the same unknown,
   unsigned amount. Version 1 of this module clamped negatives to zero. A vendor gateway
   clock running 2 ms ahead then reported a wire segment of a flawless 0 µs, a healthy
   80 µs end-to-end verdict, and a decomposition summing to 2,020 µs — three mutually
   contradictory numbers and no warning. This version rejects that window.

## When NOT to Use

- **As a latency collector.** This module reads no clock and instruments nothing. It
  audits timestamps captured elsewhere and already normalised to a common epoch. Every
  guarantee it offers is about arithmetic over those numbers.
- **On un-normalised venue timestamps.** Nothing here can recover the session date and
  timezone an ITCH stamp does not carry. Normalise first; the module rejects magnitudes
  that betray an unconverted epoch, but it cannot detect a conversion wrong by a whole
  number of hours.
- **With the shipped budget unchanged.** `max_allowed_p99_latency_us = 500` is an
  engineering placeholder. **No regulator, exchange or vendor publishes a microsecond
  feed latency SLA** — see `references/standards.md`. ESMA's survey of data contributors
  found reported SLAs ranging from 1 ms to 1 s; the shipped default is twice as tight as
  the tightest of those. Calibrate it or the verdict is decorative.
- **As a real-time circuit breaker.** This is a windowed audit over a completed sample
  set, not a live control. Halting trading on a stale feed belongs in a dedicated risk
  control — see `kill-switch-and-drawdown-circuit-breakers`.
- **To compare percentiles across vendors as though they were commensurable.** Each
  vendor's `VENDOR_TRANSPORT` segment is measured against a *different* gateway clock
  with no regulated accuracy guarantee. Rank vendors on their own trend, not on a
  microsecond difference between them.
- **For generic percentile-SLA work with no vendor decomposition.** Use
  `latency-monitoring-percentile-based-slas`, which owns coordinated-omission correction
  and the general percentile-SLA vocabulary this skill reuses.

## Prerequisites

- Per-tick timestamps (`t_exchange_us`, `t_vendor_us`, `t_local_nic_us`, `t_app_us`)
  **already normalised to microseconds on one common epoch**.
- The NIC's PTP hardware clock disciplined to the system clock (`phc2sys`), or hardware
  timestamps converted before use.
- A calibrated budget (`max_allowed_p99_latency_us`) and the percentile your obligation
  actually attaches to (`audited_percentile`).
- Optional: `clock_uncertainty_us`, the combined uncertainty of the two clocks bracketing
  the measurement, to enable the noise-floor annotation.
- Python 3.9+ (`math.nextafter`, `math.ulp`).

## Workflow

1. **Normalise, then validate.** The engine rejects rather than repairs: empty input,
   NaN/Inf, `bool` (which would read as 1 µs), non-numeric stamps, blank vendor IDs, and
   timestamps beyond `1e17` µs — the signature of a nanosecond stamp in a microsecond
   field. It also reports `timestamp_quantum_us`, the float64 spacing at this magnitude:
   microseconds-since-Unix-epoch quantise at **0.25 µs**, so a sub-microsecond figure
   there is not evidenced however precisely the NIC measured it.
2. **Check clock-domain integrity before believing any percentile.** Any negative
   segment delta returns `VENDOR_CLOCK_DOMAIN_ERROR` for that vendor and **no
   percentiles are published for it** — printing them beside the warning invites exactly
   the reading the warning exists to prevent. The segment that went negative names the
   pair of clocks to diagnose: `VENDOR_TRANSPORT` → venue vs vendor epoch conversion;
   `NETWORK_WIRE` → vendor gateway clock vs your NIC; `APP_QUEUE` → PHC vs system clock.
   Verdicts are per vendor, so one broken feed does not invalidate the others.
3. **Compute percentiles by nearest rank.** `ceil(p/100 × N)` with HdrHistogram's
   one-ULP rank guard, so every reported figure is a latency actually observed and P99.9
   resolves at exactly 1,000 samples. `PERCENTILE_LINEAR` stays available for NumPy/Excel
   parity, but on a feed that is either 10 µs or 900 µs it reports a median of 455 µs —
   a latency the feed never produced.
4. **Audit the budget, and mind the asymmetry.** A breach is reported at **any** sample
   count: an over-budget latency was genuinely observed. A healthy verdict is not — if
   nothing breached but the sample count cannot resolve the audited percentile, the
   verdict is `INSUFFICIENT_SAMPLES_FOR_SLA`. *No breach observed* is not *compliant*.
   Comparisons run unrounded; rounding applies to report fields only.
5. **Attribute the tail to a segment — from the tail subset, not from segment
   percentiles.** Percentiles are not additive, so the segment with the highest
   standalone P99 need not be the one that was slow during the ticks that breached.
   Attribution runs over the samples at or above the audited percentile and reports each
   segment's mean contribution across just those ticks. `dominant_tail_segment` is the
   party to raise the ticket with.
6. **Pool, never average.** Samples sharing a normalised `vendor_id` are pooled across
   symbols and hosts into one distribution before ranking. Per-symbol P99s cannot be
   averaged into a feed P99.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Clamping a negative segment latency to zero.** This is the defect that motivated
  v2.0.0. The clamp turns proof that two clocks disagree into a perfect 0 µs hop, and it
  breaks the identity that the three segments sum to the end-to-end total — leaving a
  report whose decomposition and whose headline figure contradict each other by 25×
  while the status reads healthy.
- **Subtracting timestamps that use different epochs.** An ITCH "nanoseconds since
  midnight" value and a CME Unix-epoch value are both perfectly valid and are not
  comparable. Nothing in the feed tells you which you have; the venue specification does.
- **Missing the session rollover.** A midnight-relative venue stamp wraps to zero. Every
  tick across that boundary produces a large negative transport segment that has nothing
  to do with the vendor.
- **Treating a raw NIC hardware timestamp as system time.** It comes from the NIC's PTP
  hardware clock. Undisciplined, that clock drifts against the system clock and produces
  a steadily growing — eventually negative — app-queue segment that is pure clock drift.
- **Blaming the segment with the largest mean.** A hop that is steadily 200 µs looks
  worse on average than one that is 10 µs on 985 ticks and 5,000 µs on 15 — but the
  second hop is the one that blew the budget. Means describe the body; the tail subset
  identifies the culprit.
- **Averaging the three segment P99s and expecting the total P99.** Percentiles are not
  additive. The segment P99s can come from entirely disjoint samples.
- **Reading "no breach" as compliance on a short window.** A window can prove a breach
  but cannot prove its absence. Below 100 samples P99 is arithmetically the maximum, and
  below 1,000 so is P99.9.
- **Rounding before comparing to the budget.** A P99 of 500.004 µs displayed as 500.00
  and recorded as a pass.
- **Quoting a budget tighter than the clocks can resolve.** MiFID II RTS 25 permits an
  HFT firm's business clocks 100 µs of divergence from UTC, and two such clocks bracketing
  one measurement can contribute 200 µs of error. The vendor's gateway clock is not even
  a party to that regulation and carries no accuracy guarantee at all.
- **Quoting sub-microsecond latencies off Unix-epoch microsecond floats.** At 1.8e15 the
  float64 spacing is 0.25 µs. The digits below that are quantisation noise.
- **Reading σ as the only jitter metric.** One stall moves σ by orders of magnitude and
  leaves the IQR untouched. Report both.

## Verification

- Percentile arithmetic against hand-derived values: over the samples 1..100, nearest
  rank returns P25 = 25, P50 = 50, P99 = 99 $\implies$ every figure is an observed sample.
- Estimator divergence: 500 samples at 10 µs plus 500 at 900 µs $\implies$ nearest rank
  reports a median of 10 µs and `PERCENTILE_LINEAR` reports 455 µs, a value never observed.
- ULP guard: `rank_for_percentile(1000, 99.9) == 999`, `is_percentile_resolvable(1000,
  99.9)` is `True` and `is_percentile_resolvable(999, 99.9)` is `False`;
  `min_samples_for_percentile(99.9) == 1000` and `min_samples_for_percentile(99.0) == 100`.
- **Clock-skew regression (the v1 defect):** 200 samples whose vendor gateway clock runs
  2,000 µs ahead, so the tick reaches the NIC 1,940 µs "before" the vendor stamped it
  $\implies$ `VENDOR_CLOCK_DOMAIN_ERROR`, all percentiles withheld at 0.0, report status
  `VENDOR_LATENCY_UNMEASURABLE`. v1 returned `VENDOR_LATENCY_HEALTHY` on this input.
- Segment identity: the three segments sum to the end-to-end total sample by sample,
  which the clamp broke.
- Resolution gate: 99 healthy samples $\implies$ `INSUFFICIENT_SAMPLES_FOR_SLA`; the same
  series at 100 $\implies$ `VENDOR_LATENCY_HEALTHY`. Ten samples with one 4,980 µs
  outlier $\implies$ `VENDOR_LATENCY_SLA_BREACH_ALERT` despite P99 being unresolvable.
- Rounding: 1,000 samples at 500.004 µs against a 500 µs budget $\implies$ breach, with
  `audited_percentile_us` displayed as 500.0.
- Tail attribution: 985 ticks at (transport 200, wire 10, app 10) plus 15 at
  (transport 200, wire 5000, app 10) $\implies$ mean wire is 84.85 µs against a mean
  transport of 200 µs, yet `dominant_tail_segment == NETWORK_WIRE` at a 95.97% tail share.
- Timestamp resolution: microseconds-since-Unix-epoch (1.787e15, in binade
  $[2^{50}, 2^{51})$) $\implies$ `timestamp_quantum_us == 0.25` and no warning; 1e16
  (binade $[2^{53}, 2^{54})$) $\implies$ quantum 2.0 µs and a quantisation warning.
- Jitter over totals 1..100: mean = 50.5, population $\sigma = \sqrt{(100^2-1)/12} =
  \sqrt{833.25} = 28.87$, IQR = 50.
- Input rejection: NaN, Inf, `bool`, non-numeric, blank vendor ID, nanosecond-scale
  timestamps and empty input each raise `LatencySampleError` (a `ValueError` subclass).
- Run `python -m unittest discover -s skills/market-data-latency-monitoring-per-vendor/scripts`.

## Related Skills

- `latency-monitoring-percentile-based-slas`
- `tick-to-trade-latency-measurement`
- `cross-vendor-timestamp-precision-reconciliation`
- `clock-synchronization-ptp-for-trading-hosts`
- `clock-drift-monitoring-alerting-thresholds`
- `hardware-timestamping-vs-software-timestamping-accuracy`
- `market-data-feed-arbitration-across-vendors`
- `vendor-outage-fallback-data-source-hierarchy`

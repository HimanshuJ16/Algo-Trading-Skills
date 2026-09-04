---
name: tick-to-trade-latency-measurement
description: >-
  Use when measuring how long your own box takes to turn a tick into an order on the
  wire, decomposing six capture points into five labelled stages with nearest-rank
  percentiles. Never run it on the hot path.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: market-microstructure-latency
  tags: latency, tick-to-trade, hft, low-latency, ptp-clock-sync, hardware-timestamping, percentile-sla, tail-attribution
  brokers_frameworks: "HdrHistogram (nearest-rank semantics); Linux SO_TIMESTAMPING / PTP hardware clock; CLOCK_MONOTONIC_RAW; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when you need to know **how long your own box takes to turn a tick into an order on the wire**, and which part of it is responsible.

A `LatencySample` is six capture points on one tick that produced one order — $T_0$ NIC hardware RX, $T_1$ user-space read, $T_2$ decode complete, $T_3$ signal and pre-trade risk complete, $T_4$ order encoded and handed to the socket, $T_5$ NIC hardware TX. The engine turns a window of those into an aggregate distribution, a per-stage share of the mean, an attribution of the *tail*, and an SLA verdict.

Three things make a tick-to-trade number wrong in ways nothing complains about, and this module exists to keep them visible:

1. **The stage labels can be off by one.** Five stages need six capture points. Revision 1.1.0 of this module carried five, had no $T_1$, computed only four deltas, and gave each one the *following* stage's name — so a report blaming `STRATEGY_EVALUATION` was in fact measuring order serialization, and `NIC_EGRESS` never appeared at all. See **Migrating from 1.1.0** below.
2. **Per-stage percentiles do not add up.** Each stage's P99 is that stage ranked on its own. Their sum is not the T2T P99, and the error is not conservative in either direction: in this repo's own test fixture, two stalls in different stages of different samples give summed stage P99s of **2.5 µs** against a measured total P99 of **21.5 µs**. Attribute a tail by conditioning on the slow samples (`tail_attribution`), never by summing.
3. **The sample count may not resolve the percentile.** Below 1,000 samples the P99.9 nearest rank *is* the maximum, arithmetically. A P99.9 off a 100-sample window is the worst of 100 wearing a label it did not earn.

## When NOT to Use

- **On the hot path.** This module allocates, sorts and computes percentiles. Allocation on the critical path *is* the jitter this skill exists to find. Capture into a pre-allocated ring buffer (`memory-mapped-ring-buffer-for-ultra-low-latency`), drain off-path, then call this.
- **As the instrumentation.** It reads no clock. Obtaining $T_0/T_5$ and converting the NIC clock domain belongs to `hardware-timestamping-vs-software-timestamping-accuracy` and `network-interface-level-tick-timestamping`.
- **When every stage has its own budget.** A per-phase SLA audit with a named over-budget bottleneck, in integer nanoseconds, is `colocation-latency-budget-accounting`. This module needs no per-stage budget — it attributes shares of the *measured* total. Deriving the budgets in the first place is `strategy-latency-budget-decomposition`.
- **For coordinated-omission correction or cross-node pooling.** `latency-monitoring-percentile-based-slas` handles a single flat sample series with a fixed sampler cadence. T2T samples are event-driven and have no expected interval to correct against.
- **With the shipped budgets unchanged.** `max_p50_us = 5.0`, `max_p99_us = 15.0`, `max_p999_us = 50.0`, `max_tail_us = 100.0` are engineering starting points. **No regulator, exchange or standards body publishes a tick-to-trade latency SLA** — see `references/standards.md`. Calibrate against your venue, colocation and strategy or the verdict is meaningless.
- **As a live circuit breaker.** This is a windowed audit over a completed sample set. Halting trading on a latency excursion belongs in `risk-control-latency-budget` and `kill-switch-and-drawdown-circuit-breakers`.
- **As a regulatory timestamping record.** MiFID II RTS 25 governs *business clock* accuracy against UTC for reportable events. `CLOCK_MONOTONIC_RAW` is not UTC-traceable and these relative deltas are not a substitute for a compliant record.

## Prerequisites

- Python 3.10+ (`math.nextafter` is used for HdrHistogram's rank nudge). No third-party dependencies.
- Hardware timestamping on the NIC for $T_0$ and $T_5$: request `SOF_TIMESTAMPING_RX_HARDWARE` / `SOF_TIMESTAMPING_TX_HARDWARE` via `SO_TIMESTAMPING`, and **confirm adapter support with `ethtool -T`** — the software path returns a populated field regardless.
- An in-host counter for $T_1 \dots T_4$: `clock_gettime(CLOCK_MONOTONIC_RAW)`, or `rdtsc` converted with a calibrated invariant-TSC frequency (invariant TSC is advertised by `CPUID.80000007H:EDX[8]`).
- **A documented conversion between the NIC PHC domain and the in-host timebase.** The Linux kernel does not convert hardware timestamps to system time; it exposes the NIC clock as a PTP clock source so userspace can. See `clock-synchronization-ptp-for-trading-hosts`.
- Enough samples for the percentile you intend to audit: 100 for P99, 1,000 for P99.9. The engine will tell you when the window cannot resolve one.

## Units

Input timestamps are **integer nanoseconds**. Every reported latency is a **float in microseconds (µs)**. `colocation-latency-budget-accounting` reports nanoseconds throughout — a value moved between the two without conversion is wrong by 1,000× and neither module can catch it.

Timestamps must be `int`. A `float` is rejected rather than rounded: IEEE-754 binary64 has a 53-bit significand, so at an epoch magnitude of ~1.7e18 ns the representable spacing is 256 ns — coarser than most of the stages being measured. `bool` is rejected too, since it is a subclass of `int` and `True` would otherwise be accepted as the timestamp `1`.

## Workflow

1. **Normalise to one timebase before building a sample.** $T_0$ and $T_5$ are taken in the NIC's PTP hardware clock domain; $T_1 \dots T_4$ come from an in-host counter. Subtracting across the two yields an offset, not a duration. Monotonicity validation catches only the case where the offset runs the timestamps *backwards*; a constant, small inter-domain offset produces positive, plausible and entirely wrong `NIC_INGRESS` and `NIC_EGRESS` deltas, and **no check in this module can detect it**.
2. **Construct `LatencySample` off the hot path.** Validation runs on construction and rejects non-`int`, negative, implausible and non-monotonic timestamps, and blank `sample_id` / `symbol`. A stage of exactly 0 ns is *accepted* — it means the timer could not resolve the stage, not that the stage was free. A negative stage is rejected outright: it proves the two capture points came from clocks that disagree, which makes the positive stages in the same sample wrong by an unknown amount too. Catch the error **per sample**, count the quarantine, and continue; a silently dropped quarantine biases the tail downwards, because instrumentation defects correlate with the slow path.
3. **Record into the engine.** `TickToTradeLatencyEngine(percentile_method=..., max_samples=...)`. Exceeding `max_samples` **raises**; the engine never evicts. A ring buffer over the accumulated window would discard observations from the very distribution being measured, and the ones a cap drops are the ones that mattered.
4. **Choose the percentile estimator deliberately.** `PERCENTILE_NEAREST_RANK` (default) follows HdrHistogram, so every reported figure is a latency that was actually observed. `PERCENTILE_LINEAR` exists for parity with NumPy/Excel tooling, but interpolation blends neighbours: on a stage that is either 10 µs or 900 µs and nothing between, it reports a median of **455 µs** — a latency the system never produced.
5. **Evaluate.** `evaluate_latency_distribution(sla_config, tail_percentile=99.0)` returns the T2T distribution, the five stage breakdowns, the tail attribution, the resolution warnings and the verdict. It raises on an empty sample set rather than returning zeros, which would read as a perfectly fast pipeline.
6. **Read `percentage_of_total` as a mean decomposition, and nothing more.** Means are additive, so the five shares sum to exactly 100% of the *average* T2T. That is the right view for "where does the budget go on a normal tick" and says nothing about the tail.
7. **Attribute the tail with `tail_attribution`.** The window is split by **rank** — the samples at or above the nearest rank for `tail_percentile` form the tail, the rest the body — and each stage's mean is differenced across the two. Because the total is the sum of the stages and the mean is linear, the stage excesses sum *exactly* to `total_excess_us`. `dominant_stage` names the stage owning the largest excess, and is `None` when the tail is no slower than the body. Splitting on `total >= threshold` instead would sweep every sample into the tail whenever the threshold value repeats — which it routinely does on a pipeline with a flat body.
8. **Read `sla_status`, not `sla_breaches == []`.** A breach is reported at **any** sample count: an over-budget latency was genuinely observed, and one sample is enough to observe one. An approval is not. If nothing breached but the window cannot resolve an audited percentile the status is `T2T_INSUFFICIENT_SAMPLES_FOR_SLA`. *No breach observed* is not *compliant*. Calling `evaluate_latency_distribution()` with no `SLAConfig` returns `T2T_NOT_AUDITED` — a report that was never audited must not read as one that passed.
9. **Check `below_noise_floor` before optimising a stage.** Set `SLAConfig.timestamp_uncertainty_us` to the combined uncertainty of the two clocks bracketing a stage. Any stage whose P50 falls below it is not measurable by those clocks, whatever the report prints — and the two clock-domain-spanning stages, `NIC_INGRESS` and `NIC_EGRESS`, are exactly the ones most likely to trip it.

> Full procedure: see `references/workflows.md`.
> Standards, sources and engineering rules: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Migrating from 1.1.0

`LatencySample` gained a required `socket_read_ns` ($T_1$) in position 4, so a 1.1.0 seven-argument construction now raises `TypeError` rather than silently misassigning — the break is loud by design. Two things must be re-read, not just recompiled:

| 1.1.0 delta | Label it was given | Stage it actually measured |
|---|---|---|
| `decoded_ns - ingress_ns` | `NIC_INGRESS` | NIC ingress **and** decode combined |
| `strategy_ns - decoded_ns` | `DECODER_PARSING` | strategy evaluation |
| `serialized_ns - strategy_ns` | `STRATEGY_EVALUATION` | order serialization |
| `egress_ns - serialized_ns` | `ORDER_SERIALIZATION` | NIC egress |
| — | `NIC_EGRESS` | never computed |

Any optimisation decision taken from a 1.1.0 stage report was aimed one stage too early. `calculate_percentile` also now defaults to nearest rank and raises on an empty sequence instead of returning `0.0`; pass `PERCENTILE_LINEAR` explicitly to keep the old numbers.

## Common Pitfalls

- **Subtracting a NIC hardware timestamp from a `CLOCK_MONOTONIC` one.** They are different clocks. The kernel does not convert hardware timestamps to system time — it exposes the NIC clock as a PTP clock source "to allow time conversion in userspace". A large inter-domain offset shows up as a negative stage and raises; a *small* one shows up as a plausible NIC_INGRESS figure that is entirely fictitious, and nothing catches it.
- **Summing per-stage P99s to get the T2T P99.** Two stalls in different stages of different samples are each 1-in-100 within their own stage, so neither stage's own P99 resolves them, while both land in the top 2 of the totals. The sum then reads 2.5 µs against a measured 21.5 µs and approves a pipeline that is already over budget. Use `tail_attribution`.
- **Diagnosing a tail spike from `percentage_of_total`.** That is the share of the *mean*. A stage can be 40% of the average T2T and 99% of the tail, or 3% of the average and the entire cause of every stall. In the module's own smoke fixture the strategy stage is 43.5% of the mean and 98.7% of the tail excess.
- **Reporting a P99.9 the window cannot support.** Below 1,000 samples the P99.9 nearest rank *is* the maximum. The number renders, the dashboard is green, and it describes the worst of a few hundred ticks. This is the failure mode most likely to survive code review, because nothing about the output looks wrong.
- **Reading "no breach" as compliance.** A short window can prove a breach but cannot prove its absence. A five-minute sample of a quiet market becomes evidence of an SLA the system has never met under load.
- **Treating a 0 ns stage as free.** It means the timer could not resolve the stage. Set `timestamp_uncertainty_us` and read `below_noise_floor` rather than concluding a stage costs nothing.
- **Quarantining non-monotonic samples without counting them.** Instrumentation defects correlate with the slow path, so silently dropping them biases the tail downwards — the one direction that turns a real problem into a passing report.
- **Assuming `rdtsc` needs C-states and Turbo disabled to keep a constant rate.** On any processor advertising invariant TSC (`CPUID.80000007H:EDX[8]`) the counter "will run at a constant rate in all ACPI P-, C-. and T-states" (Intel SDM). Disabling C-states is still worth doing — for *exit-latency* jitter — but it is not what makes the TSC usable as a timebase; calibrating its frequency and confirming the invariant bit is.
- **Measuring only the samples that produced an order.** A pipeline that *drops* ticks under load omits its own worst observations and this module cannot see that. Count drops in the feed handler; see `tick-buffering-burst-handling`.
- **Profiling inside the trading thread.** Sorting and percentile computation allocate. Run this module out of band on a drained buffer.

## Verification

- Stage labelling: a sample with stage durations 300 / 500 / 1,000 / 400 / 300 ns $\implies$ `NIC_INGRESS = 300`, `DECODER_PARSING = 500`, `STRATEGY_EVALUATION = 1000`, `ORDER_SERIALIZATION = 400`, `NIC_EGRESS = 300`, summing to the 2,500 ns total. Every duration is distinct, so a one-position shift cannot pass.
- Mean shares: 100 samples of that shape $\implies$ mean T2T 2.5 µs and shares of exactly 12 / 20 / 40 / 16 / 12 %, summing to 100.
- Nearest rank over the samples 1..100 $\implies$ P50 = 50, P90 = 90, P99 = 99, P99.9 = 100 (the maximum). Linear over 1..10 $\implies$ P50 = 5.5 and P90 = 9.1; nearest rank over the same $\implies$ P50 = 5.0. Over 500 samples at 10 µs plus 500 at 900 µs, linear reports a median of 455 µs and nearest rank reports 10 µs.
- Resolution: `min_samples_for_percentile(99.0) == 100`, `min_samples_for_percentile(99.9) == 1000`, `is_percentile_resolvable(999, 99.9)` is `False` and `is_percentile_resolvable(1000, 99.9)` is `True`.
- Tail attribution: 99 samples at 2.5 µs plus one whose strategy stage alone stalls to 20 µs $\implies$ tail of 2 samples, body of 98, `tail_mean_total_us = 12.0`, `body_mean_total_us = 2.5`, `total_excess_us = 9.5`, `dominant_stage = STRATEGY_EVALUATION` with 100% of the excess, and the five stage excesses summing exactly to 9.5.
- Non-additivity: 98 fast samples, one with a 20 µs strategy stall and one with a 20 µs decode stall $\implies$ total P99 = 21.5 µs while the summed stage P99s are 2.5 µs.
- SLA asymmetry: one 50.3 µs sample against a 5 µs P50 budget $\implies$ `T2T_SLA_BREACH` from a single observation. 100 healthy samples $\implies$ `T2T_INSUFFICIENT_SAMPLES_FOR_SLA` (P99.9 unresolvable); the same shape at 1,000 $\implies$ `T2T_SLA_COMPLIANCE_APPROVED`. Evaluating with no `SLAConfig` $\implies$ `T2T_NOT_AUDITED`, never `T2T_SLA_COMPLIANCE_APPROVED`.
- Input rejection: non-monotonic, `float`, `bool`, negative, implausibly large timestamps and blank identity fields each raise `LatencyError` (a `ValueError` subclass); an empty sample set and an empty percentile sequence raise rather than returning zero; non-monotonic SLA budgets (`max_p50_us > max_p99_us`) and non-finite budgets raise.
- Run `python -m unittest discover -s skills/tick-to-trade-latency-measurement/scripts`.

## Related Skills

- `colocation-latency-budget-accounting`
- `strategy-latency-budget-decomposition`
- `latency-monitoring-percentile-based-slas`
- `hardware-timestamping-vs-software-timestamping-accuracy`
- `clock-synchronization-ptp-for-trading-hosts`
- `network-interface-level-tick-timestamping`
- `binary-protocol-parsing-for-low-latency-feeds`
- `memory-mapped-ring-buffer-for-ultra-low-latency`
- `feed-handler-cpu-pinning-and-numa-awareness`
- `risk-control-latency-budget`

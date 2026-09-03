---
name: latency-monitoring-percentile-based-slas
description: >-
  Use when auditing captured latency samples against a percentile budget rather than an
  average, with nearest-rank P50 to P99.9 and a sample-count gate that refuses to
  certify on too few observations.
license: Apache-2.0
metadata:
  domain: algorithmic-trading
  subdomain: market-microstructure-latency
  tags: latency-monitoring, percentiles, p99, p999, sla-breach, tick-to-trade, jitter, coordinated-omission
  brokers_frameworks: "HdrHistogram; CLOCK_MONOTONIC_RAW; Python Dataclasses"
  version: "2.0.0"
  author: algo-trading-skills-contributors
---

## When to Use

Use this skill when auditing the latency of low-latency trading infrastructure — order gateways, tick-to-trade pipelines, risk-check hot paths — against a percentile budget rather than an average. Mean latency is a vanity metric: a pipeline that is 30 µs on 999 ticks and 40 ms on the thousandth averages out to something reassuring while losing the one trade that mattered. Percentiles are the only summary that keeps the stall visible.

Getting a percentile *number* is easy. This skill exists because three things routinely make that number wrong in ways nothing complains about:

1. **The sample count may not be able to resolve the percentile at all.** A "P99.9" over 200 samples is not a 1-in-1000 event — it is the worst of 200 samples wearing a label it did not earn. Below 1,000 samples the P99.9 rank *is* the maximum, arithmetically.
2. **The sampler may have stopped sampling during the stall.** If the measuring loop blocks while the system is slow, the slow period contributes one observation instead of the hundreds it should — Gil Tene's coordinated omission. The tail is then under-reported by the exact events the SLA is meant to catch.
3. **The clocks may not be able to resolve the budget.** A 200 µs P99 measured between two hosts each permitted 100 µs of divergence from UTC has an error bar the size of the thing being measured.

The engine reports HdrHistogram-compatible nearest-rank percentiles (P25 through P99.9), jitter as both standard deviation and IQR, and a status that distinguishes *breached*, *compliant*, and *not measurable*.

## When NOT to Use

- **As a latency collector.** This module reads no clock and instruments nothing. It audits a sample series you already captured; every guarantee it offers is about arithmetic on those samples, not about how they were obtained.
- **With the shipped budgets unchanged.** `sla_p50_target_us = 50`, `sla_p99_target_us = 200` and `sla_p999_target_us = 1000` are engineering starting points. **No regulator, exchange or standards body publishes a tick-to-trade latency SLA** — see `references/standards.md`. Calibrate against your own venue, colocation and strategy, or the verdict is meaningless.
- **On fewer samples than the tightest audited percentile needs.** The engine will tell you (`INSUFFICIENT_SAMPLES_FOR_SLA`) rather than guess, but the fix is a longer measurement window, not a looser reading of the report.
- **To compare two stages measured against different clocks.** Latency differences smaller than the combined timestamp uncertainty of the two clocks are noise. Set `clock_uncertainty_us` and the engine will say so.
- **As a real-time circuit breaker.** This is a windowed, offline-style audit over a completed sample series. Tripping live trading on a latency excursion belongs in a dedicated risk control — see `risk-control-latency-budget` and `kill-switch-and-drawdown-circuit-breakers`.

## Prerequisites

- A latency sample series (`pipeline_stage`, `samples_microseconds`) in microseconds. Samples must be finite and non-negative; the engine rejects the series otherwise rather than reporting percentiles over corrupted data.
- **Samples measured with a monotonic clock.** An interval computed from two `CLOCK_REALTIME` readings is "affected by discontinuous jumps in the system time" and can come out negative when NTP steps the clock mid-measurement. `CLOCK_MONOTONIC_RAW` "is not subject to frequency adjustments" and is the safe source for a duration. Python's `time.perf_counter_ns()` / `time.monotonic_ns()` are monotonic; `time.time()` is not.
- SLA budgets (`sla_p50_target_us`, `sla_p99_target_us`, `sla_p999_target_us`), calibrated — not inherited from this skill's defaults.
- Optional: the sampler's fixed cadence (`expected_sample_interval_us`) to enable coordinated-omission correction, and the combined two-clock timestamp uncertainty (`clock_uncertainty_us`) to enable the noise-floor check.

## Workflow

1. **Validate before computing anything.** The engine rejects three input classes outright, because each produces a confidently wrong report rather than an error:
   - **NaN/Inf** — a NaN compares `False` against every bound, so `sorted()` silently leaves the list unordered *and* `NaN <= budget` is `False` for every budget. Left unchecked, a corrupted series reads as a passing audit.
   - **Negative** — a duration cannot be negative. It means the two timestamps came from clocks that disagree, so the positive samples in the same window are wrong by an unknown amount too. Reject the window; do not filter the negatives and keep the rest.
   - **Empty** — nothing to audit.
2. **Correct for coordinated omission, if and only if the sampler had a fixed cadence.** Set `expected_sample_interval_us` to the sampler's *intended* interval. Following HdrHistogram, each recorded value larger than that interval generates "an additional series of decreasingly-smaller ... value records". Apply it once — correcting an already-corrected series double-counts the stall. Leave it off if the sampler is event-driven, where there is no expected interval to compare against.
3. **Compute percentiles by nearest rank.** `ceil(p/100 × N)` into the ascending-sorted series, matching HdrHistogram's `getValueAtPercentile`. Every reported figure is a latency that was actually observed. `PERCENTILE_LINEAR` is available for parity with NumPy/Excel-based tooling, but interpolation blends neighbouring observations: on a stage that is either 10 µs or 900 µs and nothing between, it reports a median of **455 µs** — a latency the system never produced.
4. **Check that each audited percentile is resolvable.** `is_percentile_resolvable(n, p)` is true only when the nearest rank falls *strictly below* N; when it lands on N the "percentile" is just the maximum. P99 needs 100 samples, P99.9 needs 1,000.
5. **Audit the budgets, and mind the asymmetry between proving a breach and proving compliance**:
   - A breach is reported at **any** sample count — an over-budget latency was genuinely observed, and ten samples are enough to observe one.
   - Approval requires resolution. If nothing breached but the sample count cannot resolve an audited percentile, the verdict is `INSUFFICIENT_SAMPLES_FOR_SLA`. *No breach observed* is not *compliant*.
   - Precedence: `SLA_BREACH_P999_CRITICAL` > `SLA_BREACH_P99_WARNING` > `SLA_BREACH_P50_WARNING` > `INSUFFICIENT_SAMPLES_FOR_SLA` > `SLA_COMPLIANCE_APPROVED`. A P50 breach with healthy tails is a whole-distribution shift, not a tail spike, and is a distinct finding — never an approval.
   - Comparisons run on unrounded values; rounding is applied to the report fields only. A P99 of 200.004 µs against a 200 µs budget is a breach, not a 200.00 µs pass.
6. **Flag verdicts inside the measurement noise floor.** With `clock_uncertainty_us` set, any percentile landing within that distance of its budget is reported as undecidable. This never changes the status — it annotates it, so a marginal pass is not mistaken for a comfortable one.
7. **Aggregate across nodes by pooling raw samples, never by averaging percentiles.** `pool_latency_samples()` concatenates the underlying observations. Two gateways at a uniform 10 µs and 900 µs have a mean-of-P99 of 455 µs and a true fleet P99 of **900 µs**.

> Full procedure: see `references/workflows.md`.
> Standards reference: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Reporting a P99.9 the sample count cannot support.** Below 1,000 samples the P99.9 nearest rank *is* the maximum. The number renders, the dashboard is green, and it describes the worst of a few hundred samples rather than a 1-in-1000 event. This is the failure mode most likely to survive code review, because nothing is obviously wrong with the output.
- **Measuring latency with a sampler that stops during stalls.** A loop that issues a request, waits, and records the round trip stops issuing requests while the system is stalled. A 50 ms freeze at a 1 ms cadence should contribute ~50 degrading observations; it contributes one. In this repo's own test fixture, a series with one such stall reports a P99.9 of **20 µs and passes**; corrected, the same data reports **49,000 µs and breaches critically**.
- **Averaging percentiles across nodes.** The mean of per-node P99s is not the fleet P99 — a percentile is a quantile of a distribution, not an additive quantity. Pool the observations and re-rank. Prometheus documents the same rule for histograms: aggregate the bucket counters, then take the quantile, never the reverse.
- **Treating "no breach observed" as compliance.** A short window can prove a breach but cannot prove its absence. Distinguish the two verdicts, or a five-minute sample of a quiet market becomes evidence of an SLA the system has never actually met under load.
- **Letting a NaN into the series.** NaN does not raise, does not sort, and does not compare — it produces an unordered list, arbitrary percentiles, and a *passing* verdict, because `NaN <= budget` is `False` for every budget and a naive audit reads "no breach".
- **Filtering out negative samples and auditing the rest.** Negative durations mean the two clocks disagree. The positive samples from the same window are drawn from the same broken measurement and are wrong by an unknown, unsigned amount. The window is unusable, not partially usable.
- **Rounding percentiles before comparing them to the budget.** Round for the report, compare on the raw value, or a 200.004 µs P99 is displayed as 200.00 and recorded as a pass.
- **Quoting a latency SLA tighter than the clocks can resolve.** MiFID II RTS 25 permits an HFT firm's business clocks to diverge from UTC by up to 100 µs. Two such clocks bracketing one measurement can contribute up to 200 µs of error — the whole of a 200 µs P99 budget.
- **Reading standard deviation as the only jitter metric.** One 100 ms stall moves σ by orders of magnitude while the IQR does not move at all. Report both: σ is sensitive to the tail, IQR describes the body.

## Verification

- Percentile arithmetic against hand-derived values: over the samples 1..100, nearest rank returns P25 = 25, P50 = 50, P99 = 99 $\implies$ every figure is an observed sample.
- Resolution boundary: `is_percentile_resolvable(1000, 99.9)` is `True` and `is_percentile_resolvable(999, 99.9)` is `False`; `min_samples_for_percentile(99.9) == 1000` and `min_samples_for_percentile(99.0) == 100`. A healthy series of 999 samples $\implies$ `INSUFFICIENT_SAMPLES_FOR_SLA`; the same series at 1,000 $\implies$ `SLA_COMPLIANCE_APPROVED`.
- Estimator divergence: over 500 samples at 10 µs plus 500 at 900 µs, nearest rank reports a median of 10 µs and `PERCENTILE_LINEAR` reports 455 µs $\implies$ confirm interpolation returns an unobserved value.
- Median-breach regression: 1,000 samples at 100 µs against `sla_p50_target_us=50` $\implies$ `SLA_BREACH_P50_WARNING`, never `SLA_COMPLIANCE_APPROVED`.
- Coordinated omission: 999 samples at 20 µs plus one 50,000 µs stall $\implies$ uncorrected P99.9 = 20 µs and `SLA_COMPLIANCE_APPROVED`; with `expected_sample_interval_us=1000` $\implies$ 1,049 samples, P99.9 = 49,000 µs and `SLA_BREACH_P999_CRITICAL`. A single 50,000 µs value at a 1,000 µs interval expands to exactly 50 records.
- Input rejection: NaN, Inf, negative, boolean, non-numeric and empty series each raise `LatencySampleError` (a `ValueError` subclass) rather than producing a report.
- Rounding: 1,000 samples at 200.004 µs against a 200 µs P99 budget $\implies$ `SLA_BREACH_P99_WARNING` with `p99_latency_us` displayed as 200.0.
- Fleet aggregation: nodes at a uniform 10 µs and 900 µs $\implies$ mean-of-P99 = 455 µs, pooled P99 = 900 µs.
- Jitter: over 1..100, mean = 50.5, population σ = √833.25 = 28.87, IQR = 50.
- Run `python -m unittest discover -s skills/latency-monitoring-percentile-based-slas/scripts`.

## Related Skills

- `tick-to-trade-latency-measurement`
- `colocation-latency-budget-accounting`
- `market-data-latency-monitoring-per-vendor`
- `clock-synchronization-ptp-for-trading-hosts`
- `clock-drift-monitoring-alerting-thresholds`
- `strategy-latency-budget-decomposition`
- `network-jitter-impact-on-strategy-performance`
- `risk-control-latency-budget`

---
name: network-jitter-impact-on-strategy-performance
description: >-
  Auditing whether a link's one-way delay variation is small enough for a latency-sensitive strategy to keep running: HdrHistogram-compatible nearest-rank P50/P95/P99 delays, jitter reported as sigma, IQR and RFC 5481 packet delay variation, a sample-count gate that refuses to approve a tail it cannot resolve, and a Sharpe degradation model that says out loud that its coefficient is operator-fitted rather than published.
domain: Market Microstructure & Latency
subdomain: Latency Percentiles & Strategy Degradation Audit
tags: ["network-jitter", "packet-delay-variation", "rfc-5481", "latency-percentiles", "p99-latency", "adverse-selection", "nearest-rank", "low-latency"]
brokers_frameworks: ["HdrHistogram (nearest-rank semantics)", "IETF RFC 5481 / RFC 3550", "Python Dataclasses"]
version: "2.0.0"
author: algo-trading-skills-contributors
license: Apache-2.0
---

## When to Use

Use this skill when you have a captured series of one-way packet delays for a link a latency-sensitive strategy depends on — a market data feed, an order gateway path, a cross-datacentre hop — and you need a defensible answer to *is the delay variation on this link small enough that the strategy is still worth running?*

Variation matters more than the baseline because a constant delay can be modelled and priced, while a variable one cannot. A quote that is reliably 2 ms stale is a quote you can widen against; a quote that is 2 ms stale most of the time and 40 ms stale once a minute is a quote you get picked off on. Aquilina, Budish and O'Neill's message-level study of FTSE 100 order books found the modal latency-arbitrage race is won by **5–10 microseconds**, that races account for roughly **31% of price impact and 33% of the effective spread**, and that the resulting "latency arbitrage tax" is about **0.42 bp of trading volume** against a value-weighted effective spread of just over 3 bp. Those are the units the damage is denominated in.

That last point is also the sharpest calibration warning this skill can give: **if your jitter is measured in milliseconds, you are already three orders of magnitude outside the margin that decides a race.** The engine's shipped defaults are in milliseconds because most links being audited are, but a strategy genuinely competing in latency races needs budgets in microseconds and this module's default configuration will approve links it should not.

The engine reports nearest-rank P50/P95/P99 one-way delays, delay variation as sigma, IQR and RFC 5481 packet delay variation, and a modelled Sharpe under a **user-calibrated** linear degradation coefficient.

## When NOT to Use

- **As a latency collector.** This module reads no clock and instruments nothing. It audits a series you captured elsewhere; every guarantee it makes is about arithmetic over those samples, not about how they were obtained.
- **With `jitter_penalty_coeff` (gamma) left at its default.** `base_sharpe = 2.5`, `jitter_penalty_coeff = 0.5` and `target_sharpe_min = 1.0` are placeholders so the module runs out of the box. **No regulator, exchange, vendor or paper publishes a Sharpe-lost-per-millisecond-of-jitter coefficient** — see `references/standards.md`. Gamma must be regressed from your own strategy's realized Sharpe against measured jitter, or the modelled Sharpe is a number with a decimal point and nothing behind it.
- **Outside the range gamma was fitted on.** The linear form is a local approximation. Moallemi and Saglam's closed-form cost of latency is asymptotically proportional to `sigma_price * sqrt(dt) / spread` — *concave* in the delay, with the marginal benefit of latency reduction increasing as delay falls. Extrapolating a straight line to a jitter regime you never observed will misprice the tail in whichever direction the curve bends.
- **On a window shorter than the tail you want to audit.** P99 needs 100 packets before it means anything more than "the worst one we saw". The engine returns `JITTER_INSUFFICIENT_SAMPLES` rather than guessing; the fix is a longer capture, not a looser reading of the report.
- **As a live circuit breaker.** This is a windowed, after-the-fact audit. Halting trading on a latency excursion belongs in a dedicated risk control — see `risk-control-latency-budget` and `kill-switch-and-drawdown-circuit-breakers`.
- **For general percentile/SLA machinery.** If the question is "does this pipeline meet its latency SLA", `latency-monitoring-percentile-based-slas` is the skill: it adds coordinated-omission correction, P99.9, clock-noise-floor annotation and fleet pooling. This skill is specifically about translating delay *variation* into a strategy-level verdict.

## Prerequisites

- A one-way packet delay series (`packet_id`, `send_timestamp_ns`, `receive_timestamp_ns`), at least 100 packets for a P99 verdict.
- **Two clocks synchronised to each other.** A one-way delay spans two hosts, so a monotonic clock cannot measure it — you need synchronised real-time clocks, and the achievable resolution is bounded by their combined divergence from UTC. Under MiFID II RTS 25 an HFT firm's business clocks may each diverge from UTC by up to 100 µs; two such clocks bracketing one measurement can contribute up to 200 µs of error. A jitter budget tighter than that is not measurable by those clocks.
- A **fitted** `jitter_penalty_coeff` (gamma, units: Sharpe per millisecond of delay sigma), `base_sharpe`, and `target_sharpe_min`.
- Optional: `max_acceptable_jitter_ms` (absolute sigma ceiling) and `max_p99_latency_ms` (absolute tail budget, off by default).

## Workflow

1. **Validate before computing anything.** `analyze_jitter_impact` rejects three input classes outright rather than repairing them, because each otherwise produces a confidently wrong report instead of an error:
   - **NaN / Inf timestamps** — a NaN compares `False` against every bound, so `sorted()` silently leaves the series unordered *and* every budget comparison reads as a pass. A corrupted capture becomes a passing audit.
   - **Negative delay** (receive before send) — this proves the two clocks disagree. The positive delays in the same window came from the same disagreeing pair and are wrong by an unknown amount, so the **whole window** is rejected. Do not filter the negatives and audit the remainder.
   - **Implausibly large delay** — almost always a unit error, typically a raw nanosecond duration passed where a nanosecond *timestamp* was expected.
   A single-packet window is also rejected: its standard deviation is zero by construction, which would read as a perfectly jitter-free link.
2. **Compute percentiles by nearest rank.** `ceil(p/100 × N)` into the ascending-sorted series, matching HdrHistogram's `getValueAtPercentile`, so every reported delay was actually observed. This is not cosmetic: the v1.0.0 index rule `int(n × p)` was one rank too high for every percentile, and over 100 packets its "P99" was arithmetically the observed maximum.
3. **Report all three variation metrics, and read them against each other.**
   - **sigma** (`jitter_std_ms`, Bessel-corrected) is what the Sharpe model consumes. It is tail-sensitive: one stall moves it by orders of magnitude.
   - **IQR** (`jitter_iqr_ms`, P75 − P25) describes the body and does not move for a single stall. When sigma is large and the IQR is near zero, you have a stall problem, not a jitter problem — and the remediation is different (find the stalling component, don't re-tune the link).
   - **PDV** (`pdv_p99_ms`) is RFC 5481 packet delay variation at the tail, `P99 − min`: how far behind the link's own demonstrated best case a tail packet falls. This is the figure that maps most directly onto "how stale can my quote be", because the minimum is proof of what the path can do.
4. **Audit the budgets, on unrounded values.** Three independent checks, each contributing a named finding to `report.breaches`:
   - `SHARPE_BELOW_FLOOR` — modelled Sharpe `base_sharpe − gamma × sigma` below `target_sharpe_min`. Tested **before** the presentational clamp at zero, so a negative floor cannot be satisfied by a clamped 0.0.
   - `JITTER_STD_OVER_CEILING` — sigma over `max_acceptable_jitter_ms`, independent of the Sharpe model. A generous gamma must not be able to approve a link you have separately decided is too variable.
   - `P99_LATENCY_OVER_BUDGET` — nearest-rank P99 over `max_p99_latency_ms`, when that budget is set.
   Comparisons run on raw values and rounding is applied to the report fields only, so a P99 of 5.0004 ms against a 5 ms budget is a breach and not a 5.00 ms pass.
5. **Mind the asymmetry between proving a breach and proving a link is fine.**
   - A **breach** is reported at any packet count. An over-budget delay was genuinely observed, and ten packets are enough to observe one.
   - An **approval** requires that P99 be resolvable — at least 100 packets. Otherwise the verdict is `JITTER_INSUFFICIENT_SAMPLES`. *No breach observed* is not *within budget*.
6. **Read the Sharpe figure as a model output, never as a measurement.** `report.sharpe_model` records which model produced it (`LINEAR_LOCAL_FIT`). Re-fit gamma whenever the strategy, venue, instrument or network path changes; a gamma inherited from a different context is a guess wearing a number.

> Full procedure: see `references/workflows.md`.
> Standards, sources, and what is *not* published by anyone: see `references/standards.md`.
> Printable pre-flight checklist: see `assets/checklist.md`.

## Common Pitfalls

- **Truncating the index instead of taking the nearest rank.** `sorted[int(n × p)]` is the (n·p + 1)-th smallest, one rank too high everywhere. Over 50 packets at 1 ms and 50 at 9 ms it reports a median of **9 ms** where the median is 1 ms, and over exactly 100 packets its P99 is the observed maximum. Nothing about the output looks wrong, which is why this survives review.
- **Approving a tail the window cannot resolve.** Below 100 packets the P99 nearest rank *is* the maximum. The number renders and the dashboard is green while describing the worst of a few dozen packets rather than a 1-in-100 excursion.
- **Treating "no breach observed" as a healthy link.** A short capture can prove a breach but cannot prove its absence. A quiet five-minute window is not evidence about a link under load.
- **Letting a NaN into the series.** NaN does not raise, does not sort, and does not compare — it yields an unordered list, arbitrary percentiles, and a *passing* verdict, because `NaN <= budget` is `False` for every budget and a naive audit reads "no breach".
- **Filtering out negative delays and auditing the rest.** A receive timestamp before its send timestamp means the bracketing clocks disagree. Every other delay in that window shares the same unknown, unsigned error. The window is unusable, not partially usable.
- **Reading sigma as "the jitter".** Sigma of the one-way delay, RFC 5481 PDV (`D(i) − D(min)`), and RFC 3550 interarrival jitter (a smoothed average of `|D(i-1,i)|` over *consecutive* packets) are three different quantities that do not agree numerically. Quoting one against a budget written for another silently changes what is being enforced.
- **Inheriting gamma from somewhere else.** `jitter_penalty_coeff` is dimensioned (Sharpe per ms) and strategy-specific. Nobody publishes it. A borrowed value produces a modelled Sharpe with no relationship to the strategy being audited — and because the number is plausible-looking, it will be quoted downstream as if it were measured.
- **Extrapolating the linear model past its fitted range.** The published cost-of-latency result is concave in the delay, so a straight line fitted at 1 ms jitter will misstate the impact at 20 ms.
- **Setting millisecond budgets for a microsecond-margin strategy.** Latency-arbitrage races are decided on 5–10 µs. A 3 ms jitter ceiling is not a tight budget for such a strategy; it is no budget at all.
- **Auditing average latency and calling it done.** Mean delay hides exactly the excursions that cause stale-quote fills, and OS kernel queueing, hypervisor context switching and NIC interrupt coalescing all produce tail-only damage that the mean absorbs.
- **Concluding a link is fine because P99 is clean.** A stall rarer than 1 in 100 sits above P99 entirely. When sigma is far larger than `pdv_p99_ms`, something is stalling above the audited percentile — sigma sees it and P99 does not. Audit a rarer percentile with `latency-monitoring-percentile-based-slas` rather than reading the clean P99 as an all-clear.

## Verification

- Percentile arithmetic against hand-derived ranks: over the delays 1..100 ms, nearest rank returns P25 = 25, P50 = 50, P95 = 95, P99 = 99 $\implies$ every figure is an observed delay.
- Regression against the v1.0.0 index bug: 50 packets at 1 ms plus 50 at 9 ms $\implies$ `p50_latency_ms == 1.0` (v1.0.0 reported 9.0); 99 packets at 1 ms plus one at 50 ms $\implies$ `p99_latency_ms == 1.0` (v1.0.0 reported the 50 ms maximum); delays 1..200 ms $\implies$ `p95_latency_ms == 190.0` (v1.0.0 reported 191.0).
- Jitter metrics over 1..100 ms: mean = 50.5, Bessel-corrected sigma = $\sqrt{100 \cdot 101 / 12}$ = 29.011, IQR = 75 − 25 = 50.
- Metric divergence: 999 packets at 1 ms plus one 5,000 ms stall $\implies$ sigma > 100 ms while the IQR is exactly 0 $\implies$ confirm sigma and IQR separate on a stall.
- PDV against the minimum: 95 packets at 2 ms plus 5 at 12 ms $\implies$ `p99_latency_ms == 12.0`, `min_latency_ms == 2.0`, `pdv_p99_ms == 10.0`.
- Sharpe model, hand-computed: 100 packets alternating 2.1 / 1.9 ms $\implies$ sigma = $\sqrt{100 \cdot 0.01 / 99}$ = 0.1005, modelled Sharpe = 2.5 − 0.5 × 0.1005 = **2.45**, status `JITTER_HEALTHY`. Alternating 1 / 9 ms $\implies$ sigma = 4.0202, modelled Sharpe = **0.49** $\implies$ `JITTER_HIGH_RISK_WARNING` with both `SHARPE_BELOW_FLOOR` and `JITTER_STD_OVER_CEILING` raised.
- Clamp ordering: `target_sharpe_min = -1.0` with a modelled Sharpe of −1.52 $\implies$ reported Sharpe 0.0 but status `JITTER_HIGH_RISK_WARNING` $\implies$ confirm the floor is tested before the presentational clamp.
- Dead-config regression: `max_acceptable_jitter_ms = 1.0` with a gamma generous enough to approve (tolerance 150 ms) $\implies$ `JITTER_STD_OVER_CEILING` still raised. In v1.0.0 this field was never read.
- Unrounded comparison: 95 packets at 5.0 ms plus 5 at 5.0004 ms against `max_p99_latency_ms = 5.0` $\implies$ `P99_LATENCY_OVER_BUDGET`, with `p99_latency_ms` displayed as 5.0.
- Sample sufficiency: 100 identical packets $\implies$ `is_p99_resolvable` `True` and `JITTER_HEALTHY`; 99 $\implies$ `False` and `JITTER_INSUFFICIENT_SAMPLES`; a 10-packet window with sigma over budget $\implies$ `JITTER_HIGH_RISK_WARNING` (a breach needs no resolution guarantee); a single packet $\implies$ `JitterSampleError`.
- Input rejection: empty, NaN, Inf, negative, boolean and non-numeric timestamps each raise `JitterSampleError` (a `ValueError` subclass) rather than producing a report; duplicate `packet_id`s log a warning without rejecting.
- Config rejection: `jitter_penalty_coeff <= 0`, `target_sharpe_min > base_sharpe`, `max_acceptable_jitter_ms <= 0`, and a non-finite or non-positive `max_p99_latency_ms` each raise `JitterConfigError`.
- Run `python -m unittest discover -s skills/network-jitter-impact-on-strategy-performance/scripts`.

## Related Skills

- `latency-monitoring-percentile-based-slas`
- `network-interface-level-tick-timestamping`
- `tick-to-trade-latency-measurement`
- `colocation-latency-budget-accounting`
- `market-data-latency-monitoring-per-vendor`
- `clock-synchronization-ptp-for-trading-hosts`
- `adverse-selection-measurement-for-passive-orders`
- `latency-arbitrage-defensive-order-sizing`
- `risk-control-latency-budget`

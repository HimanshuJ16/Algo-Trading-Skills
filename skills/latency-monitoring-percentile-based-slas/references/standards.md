# Standards & Sources for Percentile-Based Latency SLAs

## There is no published latency SLA to comply with

**No regulator, exchange or standards body publishes a mandatory tick-to-trade latency
SLA.** The `200 µs` P99 and `1,000 µs` P99.9 figures shipped as defaults in this module
are engineering starting points, not requirements, and presenting them in a "MUST" table
would be wrong. Latency budgets are set by each firm against its own venue, colocation,
strategy and hardware.

What *is* regulated is the **clock** the measurement depends on — and that constrains
how tight an SLA you can credibly claim to have measured at all.

| Area | Documented requirement | Source |
|---|---|---|
| EU — HFT clock accuracy | For members/participants using a high frequency algorithmic trading technique, maximum divergence from UTC is **100 µs** and timestamp granularity **1 µs or better**. Other trading activity: 1 ms / 1 ms. Voice, RFQ with human intervention, and negotiated transactions: 1 s / 1 s. | Commission Delegated Regulation (EU) 2017/574 (RTS 25), Annex Table 2 |
| EU — venue clock accuracy | Trading venues with a gateway-to-gateway latency **≤ 1 ms** must hold 100 µs divergence and 1 µs granularity; **> 1 ms**, 1 ms and 1 ms. | Commission Delegated Regulation (EU) 2017/574 (RTS 25), Annex Table 1 |
| US — CAT clock sync | Industry Member Business Clocks, other than those used solely for Manual Order Events or the time of allocation on Allocation Reports, must be synchronised "at a minimum to within a fifty (50) millisecond tolerance of the time maintained by the atomic clock of the National Institute of Standards and Technology". Clocks used solely for Manual Order Events or allocation time: "within a one second tolerance". | [FINRA Rule 6820](https://www.finra.org/rules-guidance/rulebooks/finra-rules/6820) |

**Consequence for this module.** RTS 25's 100 µs is the tightest clock accuracy any of
these regimes demands, and it is *half* this module's default 200 µs P99 budget. Two
clocks each sitting at the edge of that tolerance can contribute up to 200 µs of error
to a single interval — the entire budget. A latency SLA tighter than roughly the
combined uncertainty of the two clocks bracketing the measurement is not measurable by
those clocks, whatever the dashboard says. That is what `clock_uncertainty_us` exists to
surface. Note also that the CAT 50 ms tolerance is a *recordkeeping* requirement and is
four orders of magnitude looser than a microsecond latency SLA: clocks that satisfy
FINRA Rule 6820 are nowhere near sufficient to measure one.

## Percentile semantics

| Area | Documented behaviour | Source |
|---|---|---|
| Nearest-rank percentile | `getValueAtPercentile` "Returns the largest value that (100% - percentile) [+/- 1 ulp] of the overall recorded value entries in the histogram are either larger than or equivalent to." Implemented as `countAtPercentile = ceil(requestedPercentile * totalCount / 100)`, where `requestedPercentile` is `Math.nextAfter(percentile, Double.NEGATIVE_INFINITY)`. | [HdrHistogram `AbstractHistogram` JavaDoc](https://hdrhistogram.github.io/HdrHistogram/JavaDoc/org/HdrHistogram/AbstractHistogram.html) and [source](https://github.com/HdrHistogram/HdrHistogram/blob/master/src/main/java/org/HdrHistogram/AbstractHistogram.java) |
| Coordinated-omission correction | "To compensate for the loss of sampled values when a recorded value is larger than the expected interval between value samples, Histogram will auto-generate an additional series of decreasingly-smaller (down to the expectedIntervalBetweenValueSamples) value records." The two correction entry points "are mutually exclusive" — only one may be applied to a given data set. | [HdrHistogram `AbstractHistogram` JavaDoc](https://hdrhistogram.github.io/HdrHistogram/JavaDoc/org/HdrHistogram/AbstractHistogram.html) |
| Correction loop | `recordSingleValue(value)`, then `for (missingValue = value - interval; missingValue >= interval; missingValue -= interval) recordSingleValue(missingValue);` — a no-op when `interval <= 0`. | [HdrHistogram `AbstractHistogram.java`](https://github.com/HdrHistogram/HdrHistogram/blob/master/src/main/java/org/HdrHistogram/AbstractHistogram.java) |
| Quantile aggregation | For classic histograms `histogram_quantile()` "assumes a uniform distribution of observations within the bucket (also called *linear interpolation*)". To aggregate across series you "use the `sum()` aggregator around the `rate()` function" preserving the `le` label — i.e. aggregate the buckets, then take the quantile, not the reverse. | [Prometheus querying functions](https://prometheus.io/docs/prometheus/latest/querying/functions/) |

The one-ULP nudge is not a detail. `99.9 / 100.0` evaluates to `0.9990000000000001` in
IEEE-754 double precision, so `ceil(0.999... × 1000)` is `1000`, not `999` — which pins
P99.9 to the observed maximum at exactly the sample count that should first resolve it.
This module reproduces HdrHistogram's `nextAfter` guard for that reason.

## Clock sources for interval measurement

| Clock | Documented behaviour | Suitability for a duration |
|---|---|---|
| `CLOCK_REALTIME` | "affected by discontinuous jumps in the system time (e.g., if the system administrator manually changes the clock), and by frequency adjustments performed by NTP and similar applications" | **Unsafe.** A mid-measurement step can produce a negative or inflated interval. |
| `CLOCK_MONOTONIC` | "not affected by discontinuous jumps in the system time ... but is affected by frequency adjustments" | Safe for ordering and durations; the rate may be slewed by NTP. |
| `CLOCK_MONOTONIC_RAW` | "provides access to a raw hardware-based time that is not subject to frequency adjustments" | Preferred for interval measurement on one host. |

Source: [`clock_gettime(2)`, man7.org](https://man7.org/linux/man-pages/man2/clock_gettime.2.html).
A monotonic clock measures an interval *on one host* and cannot be compared across
hosts; a two-host tick-to-trade measurement needs synchronised real-time clocks, which
is where the RTS 25 tolerances above become the binding constraint.

## This skill's engineering rules

Everything below is an engineering choice made by this skill. **None of it is published
by a regulator, an exchange, or a standards body.**

| Rule | Requirement | Why |
|---|---|---|
| Estimator | Percentiles MUST default to nearest rank, matching HdrHistogram. | Every reported figure is then a latency actually observed, and reconciles with HdrHistogram-based collectors. Interpolation blends neighbours and can report a value the system never produced. |
| Resolution gate | A percentile whose nearest rank equals N MUST NOT support an approval. | That "percentile" is the observed maximum; the window contains no rarer event to measure. |
| Breach/approval asymmetry | A breach MUST be reported at any sample count; an approval MUST NOT. | Observing one over-budget latency proves a breach. Observing none over a short window proves nothing. |
| P50 breach | A median over budget MUST produce its own status, never an approval. | Healthy tails with a shifted median is a real regression — a whole-distribution shift rather than a tail spike. |
| Non-finite samples | NaN/Inf MUST be rejected, not filtered. | NaN breaks `sorted()` silently and compares `False` against every budget, so a corrupted series reads as passing. |
| Negative samples | A negative duration MUST reject the whole window. | It proves the bracketing clocks disagree; the positive samples share that error by an unknown amount. |
| Comparison precision | SLA comparisons MUST use unrounded values. | Rounding to 2 dp before comparison turns a 200.004 µs P99 into a 200.00 µs pass. |
| Coordinated omission | Correction MUST be opt-in and applied at most once. | It requires a known fixed sampler cadence; a second pass double-counts the stall. |
| Fleet aggregation | Cross-node percentiles MUST be computed over pooled raw samples. | The mean of per-node percentiles is not the fleet percentile. |
| Clock uncertainty | A verdict within the timestamp uncertainty MUST be annotated, never silently widened or narrowed. | The budget is a policy choice; the noise floor is a measurement fact. Conflating them hides one or the other. |
| Jitter | Both σ and IQR MUST be reported. | σ is tail-sensitive, IQR describes the body; one stall separates them by orders of magnitude. |

## Tunable defaults (calibrate, do not inherit)

| Parameter | Default | Status |
|---|---|---|
| `sla_p50_target_us` | `50.0` | Engineering starting point. Not published by anyone. |
| `sla_p99_target_us` | `200.0` | Engineering starting point. Not published by anyone. |
| `sla_p999_target_us` | `1000.0` | Engineering starting point. Not published by anyone. |
| `percentile_method` | `NEAREST_RANK` | HdrHistogram-compatible. `LINEAR_INTERPOLATION` available for NumPy/Excel parity. |
| `expected_sample_interval_us` | `None` (off) | Opt-in; requires a known fixed sampler cadence. |
| `clock_uncertainty_us` | `0.0` (off) | Set to the combined uncertainty of the two clocks bracketing the measurement. RTS 25's 100 µs per clock is a defensible ceiling for an HFT firm's own hosts. |

## Scope boundary

This module reads no clock and instruments nothing. It audits a sample series that was
captured elsewhere, and every guarantee it offers concerns arithmetic over those
samples. It is not a compliance artifact, asserts no regulatory requirement, and its
SLA budgets carry no authority beyond the operator who sets them.

Requires Python 3.10+ for `math.nextafter`.
